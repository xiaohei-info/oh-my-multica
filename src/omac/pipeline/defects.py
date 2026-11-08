"""Upstream-defect feedback between two ticks of ``dag run|tick`` (设计 §7.5).

A blocked reporter carries a defect report on its platform item (``defect_pending`` on the node). ``advance`` reads those
reports, lets ``decide_defects`` route each defect, and turns the accepted ones into one *system amendment*: fix nodes plus
``blocked_by`` edges from the reporters, applied through the ordinary amendment ledger. It only runs in the process that
already holds the manifest write lock, never while a tick is in progress, and always in the same order:

    intent (``_dag.defects.applying``, saved) → amendment file → apply_amendment → reload → ``fixing`` (saved)

so a crash at any point leaves either nothing to redo or one idempotent step. Fingerprint states only move forward.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core import logsetup
from ..core.amendment import _node_dict, build_system_amendment
from ..core.config import resolve_defects
from ..core.graph import SATISFIED
from ..core.defects import (
    INBOX_MAX, LEDGER_MAX_ENTRIES, UPSTREAM_DEFECT, build_fix_node, build_system_proposal, defect_errors, fingerprint, anchor_ids,
    new_ledger, reporter_left, routing_problem,
)
from ..core.manifest import Manifest, adopt_disk
from ..errors import NeedsDecision, ValidationError
from ..i18n import ui
from .amendment import _acceptance_for_manifest, _write_yaml_atomic, apply_amendment, load_amendment_file
from .apply import ManifestState, Ports, apply
from .decide_defects import DefectFacts, Route, STOPS, decide_defect, decide_report, render_release, stop_summary
from .decide_effects import SaveState, SetDagFact, SetNode, UpdateMetadata

log = logsetup.get_logger(__name__)
_CODE_STOPS = {Route.NEEDS_OWNER, Route.UNROUTABLE, Route.GENERATION_CAP}


def system_amendment_path(manifest_path: str) -> str:
    path = Path(manifest_path)
    return str(path.with_name(f"{path.stem}.system-amendment.yaml"))


def ledger_of(manifest: Manifest) -> dict:
    """A private copy of the defect ledger (``_dag.defects``); mutate it, then ``_save`` it."""
    return {**new_ledger(), **copy.deepcopy(manifest.dag_state.get("defects") or {})}


def _save(manifest: Manifest, path: str, ledger: dict, flags: dict[str, bool] | None = None, *,
          store: Any = None, decisions: list[tuple[str, dict]] | None = None) -> None:
    """Make ``ledger`` (and the reporters' ``defect_pending`` flags) durable; platform decisions are written first."""
    if len(ledger["entries"]) > LEDGER_MAX_ENTRIES:
        raise NeedsDecision(ui(
            f"The defect ledger holds more than {LEDGER_MAX_ENTRIES} fingerprints; stop auto-fixing and review it with "
            f"`omac defect list {path}`.", f"缺陷账本超过 {LEDGER_MAX_ENTRIES} 条;请用 `omac defect list {path}` 检查并转人工。"),
            report={"reason_code": "defect-ledger-full"})
    effects: list[Any] = [UpdateMetadata(item_id, {"decision_required": decision}) for item_id, decision in decisions or []]
    effects += [SetDagFact("defects", ledger)]
    effects += [SetNode(key, {"defect_pending": value}) for key, value in (flags or {}).items() if key in manifest.nodes]
    apply(effects + [SaveState()], Ports(store=store), ManifestState(manifest, path))


@dataclass
class _Report:
    reporter: str
    defects: list
    source: str                        # "worker" | "review" | "inbox"
    item_id: str | None = None
    decision: dict | None = None
    fps: list = field(default_factory=list)


@dataclass
class _Pass:
    """Mutable bookkeeping of one ``advance`` pass; small enough to snapshot per report."""
    groups: dict = field(default_factory=dict)      # suspected → {"fps", "defects", "reporters"}
    attach: dict = field(default_factory=dict)      # reporter → [fix node ids]
    charges: dict = field(default_factory=dict)     # reporter → fixes charged in this pass
    retried: dict = field(default_factory=dict)     # fp → defect, re-run once against a fix that was merged meanwhile


def _inbox_id(reporter: str, defects: list) -> str:
    return hashlib.sha256(json.dumps([reporter, defects], sort_keys=True).encode()).hexdigest()[:12]


def _human_amendment_pending(manifest: Manifest) -> bool:
    marker = manifest.dag_state.get("pending_human_amendment")
    if not marker:
        return False
    try:
        amendment = load_amendment_file(marker.get("file") or "")
    except ValidationError:
        return False
    return (amendment.get("human_confirmation") in {"pending", "accepted"}
            and amendment.get("amendment_id") != manifest.meta.get("last_amendment_id"))


def _pending_reports(store, manifest: Manifest, ledger: dict) -> tuple[list[_Report], dict[str, bool]]:
    """Reports waiting for a route, and the stale ``defect_pending`` flags to clear."""
    reports, stale = [], {}
    for key, node in manifest.nodes.items():
        if not node.defect_pending:
            continue
        item = store.get_work_item(node.work_item_id) if node.status == "blocked" and node.work_item_id else None
        decision = item.decision_required if item is not None and isinstance(item.decision_required, dict) else {}
        blocker = decision.get("blocker") if isinstance(decision.get("blocker"), dict) else {}
        if blocker.get("reason_code") != UPSTREAM_DEFECT:
            stale[key] = False
            continue
        source = "review" if decision.get("gate") == "review" else "worker"
        reports.append(_Report(key, blocker.get("defects"), source, item.id, decision))
    for entry in ledger["inbox"]:
        if entry.get("stopped"):
            continue                    # already stopped (and logged) once: `omac defect grant` or a new report re-queues it
        node = manifest.nodes.get(entry["reporter"])
        item = store.get_work_item(node.work_item_id) if node and node.status == "blocked" and node.work_item_id else None
        held = item.decision_required if item is not None and isinstance(item.decision_required, dict) else None
        reports.append(_Report(entry["reporter"], entry["defects"], "inbox", item.id if held else None, held))
    return reports, stale


def _facts(ctx: "_Ctx", ledger: dict, report: _Report, defect: dict, fp: str, pass_: _Pass) -> DefectFacts:
    manifest, cfg = ctx.manifest, ctx.cfg
    entry = ledger["entries"].get(fp) or {}
    state = entry.get("state")
    if state == "fixed" and report.reporter not in entry.get("reporters", []):
        state = "fixing"        # a reporter that never waited for this fix attaches to it instead of "recurring"
    suspected = defect["suspected_node"]
    others = [e for other, e in ledger["entries"].items() if other != fp and e.get("suspected") == suspected]
    anchors = ctx.anchors | (set(a for d in report.defects for a in d["anchors"]) if report.source == "review" else set())
    group = pass_.groups.get(suspected)       # joining a fix that is already planned in this pass costs nothing new
    reporter_ok = (group is not None and report.reporter in group["reporters"]) or (
        reporter_left(ledger, report.reporter, cfg["per_reporter"]) - pass_.charges.get(report.reporter, 0) > 0)
    reporter_node = manifest.nodes.get(report.reporter)
    return DefectFacts(
        auto_fix=cfg["auto_fix"],
        unroutable=routing_problem(defect, report.reporter, manifest, anchors),
        defect_class=defect["class"],
        reporter_generation=1 if reporter_node and reporter_node.contract and reporter_node.contract.defect_refs else 0,
        state=state,
        human_amendment_pending=ctx.human_pending,
        inflight_other_fix=any(e["state"] in ("applying", "fixing") for e in others),
        done_other_fix=(not entry.get("retried") and any(e["state"] == "fixed" for e in others)),
        budget_left=reporter_ok and (group is not None or ledger["used_global"] + len(pass_.groups) < cfg["max_auto_fixes"]))


@dataclass
class _Ctx:
    engine: Any
    manifest: Manifest
    path: str
    config: dict
    cfg: dict
    acceptance: Any
    anchors: set
    human_pending: bool
    apply_system: Callable[..., Any]


def _route_report(ctx: _Ctx, ledger: dict, report: _Report, pass_: _Pass) -> tuple[Route, str | None]:
    """Route every defect of one report; mutates ``pass_`` only when the whole report is carried out."""
    errors = defect_errors(report.defects, repro_required=report.source == "worker")
    if errors:
        return Route.UNROUTABLE, "; ".join(errors[:3])
    snapshot = copy.deepcopy(pass_)
    routes, detail = [], None
    for defect in report.defects:
        fp = fingerprint(defect["suspected_node"], defect["anchors"])
        report.fps.append(fp)
        facts = _facts(ctx, ledger, report, defect, fp, pass_)
        route = decide_defect(facts)
        routes.append(route)
        detail = detail or facts.unroutable
        if route is Route.AUTO_FIX:
            group = pass_.groups.setdefault(defect["suspected_node"], {"fps": [], "defects": [], "reporters": []})
            if fp not in group["fps"]:
                group["fps"].append(fp)
                group["defects"].append(defect)
            if report.reporter not in group["reporters"]:
                group["reporters"].append(report.reporter)
                pass_.charges[report.reporter] = pass_.charges.get(report.reporter, 0) + 1
        elif route in (Route.ATTACH, Route.RETRY_AFTER_FIX):
            pass_.attach.setdefault(report.reporter, []).append(_fix_for(ledger, route, fp, defect["suspected_node"]))
            if route is Route.RETRY_AFTER_FIX:
                pass_.retried[fp] = defect
    verdict = decide_report(routes)
    if verdict in STOPS or verdict is Route.QUEUED:
        pass_.groups, pass_.attach, pass_.charges, pass_.retried = (
            snapshot.groups, snapshot.attach, snapshot.charges, snapshot.retried)
    return verdict, detail


def _fix_for(ledger: dict, route: Route, fp: str, suspected: str) -> str:
    """The fix node a reporter waits for: this fingerprint's own, or (retry) one already merged on the same node."""
    if route is Route.ATTACH:
        return ledger["entries"][fp]["fix_node"]
    return next(e["fix_node"] for other, e in ledger["entries"].items()
                if other != fp and e.get("suspected") == suspected and e["state"] == "fixed")


def _record(ledger: dict, report: _Report, state: str, ctx: _Ctx) -> None:
    if not report.fps:
        return
    for defect, fp in zip(report.defects, report.fps):
        entry = ledger["entries"].setdefault(fp, {
            "suspected": defect["suspected_node"], "anchors": sorted(set(defect["anchors"])), "reporters": [], "state": state})
        if report.reporter not in entry["reporters"]:
            entry["reporters"].append(report.reporter)
        entry["state"] = state if entry["state"] in ("queued", "needs-owner", state) else entry["state"]


def _stop(ctx: _Ctx, ledger: dict, report: _Report, route: Route, detail: str | None, decisions: list, flags: dict) -> None:
    actions = render_release(route, ctx.path, report.reporter)
    reason = (detail or stop_summary(route))[:300]
    if route is Route.RECURRED:
        for fp in report.fps:
            if ledger["entries"].get(fp, {}).get("state") in ("fixed", "recurred"):
                ledger["entries"][fp]["state"] = "recurred"
    elif route in _CODE_STOPS:
        _record(ledger, report, "needs-owner", ctx)
    for fp in report.fps:
        if fp in ledger["entries"]:
            ledger["entries"][fp]["stop"] = {"reason_code": route.value, "detail": reason}
    if report.item_id is not None:
        decisions.append((report.item_id, {
            **report.decision, "reason_code": route.value, "next_action": actions[0], "alternatives": actions[1:],
            "detail": reason}))
        flags[report.reporter] = False
    if report.source == "inbox":
        inbox_id = _inbox_id(report.reporter, report.defects)
        ledger["inbox"] = [{**e, "stopped": route.value} if e["id"] == inbox_id else e for e in ledger["inbox"]
                           if route is Route.BUDGET_EXHAUSTED or e["id"] != inbox_id]
    log.info(logsetup.EVT_DEFECT_ROUTED, kind="dag", reporter=report.reporter, route=route.value, fps=report.fps)


def _fix_nodes(ctx: _Ctx, pass_: _Pass) -> dict[str, dict]:
    """One fix node per suspected node; every reporter of the group waits for it."""
    flow_ids = set(getattr(ctx.acceptance, "flow_ids", None) or [])
    nodes = {}
    for suspected, group in sorted(pass_.groups.items()):
        nodes[suspected] = build_fix_node(
            ctx.manifest, suspected, group["fps"], group["defects"], group["reporters"], flow_ids)
        for reporter in group["reporters"]:
            pass_.attach.setdefault(reporter, []).append(nodes[suspected]["id"])
    return nodes


def _carry_out(ctx: _Ctx, ledger: dict, proposal: dict, *, reporters: list, new_entries: dict, retried: dict,
               charges: dict, inbox_ids: list, acceptance: Any) -> None:
    """Intent → amendment file → apply → reload → ``fixing``; every step is repeatable after a crash."""
    store = ctx.engine.store
    amendment = build_system_amendment(
        ctx.manifest, proposal, store, defect_fps=sorted(set(new_entries) | set(retried)), reporters=reporters,
        agent_pool=set(store.list_members(store.config.workspace_id)), acceptance=acceptance)
    file = system_amendment_path(ctx.path)
    ledger["entries"].update(new_entries)
    for fp, defect in retried.items():
        entry = ledger["entries"].setdefault(fp, {
            "suspected": defect["suspected_node"], "anchors": sorted(set(defect["anchors"])), "reporters": [], "state": "queued"})
        entry["retried"] = True
    ledger["applying"] = {"amendment_id": amendment["amendment_id"], "file": file, "fps": sorted(new_entries),
                          "retried": sorted(retried), "reporters": sorted(reporters), "charges": charges,
                          "global": len({e["fix_node"] for e in new_entries.values()}) if charges else 0,
                          "inbox_ids": inbox_ids}
    _save(ctx.manifest, ctx.path, ledger, store=store)
    _write_yaml_atomic(file, amendment)
    _apply_and_finish(ctx, ledger)


def _apply_and_finish(ctx: _Ctx, ledger: dict) -> None:
    applying = ledger["applying"]
    store = ctx.engine.store
    ctx.apply_system(
        ctx.path, load_amendment_file(applying["file"]), store, set(store.list_members(store.config.workspace_id)),
        amendment_file=applying["file"], acceptance=ctx.acceptance, runtime=ctx.engine.runtime)
    adopt_disk(ctx.manifest, ctx.path)
    ledger = ledger_of(ctx.manifest)
    for fp in applying["fps"]:
        ledger["entries"][fp]["state"] = "fixing"
    for reporter, count in applying["charges"].items():
        ledger["used_by_reporter"][reporter] = ledger["used_by_reporter"].get(reporter, 0) + count
    ledger["used_global"] += applying["global"]
    ledger["inbox"] = [e for e in ledger["inbox"] if e["id"] not in applying["inbox_ids"]]
    del ledger["applying"]
    _save(ctx.manifest, ctx.path, ledger, {reporter: False for reporter in applying["reporters"]}, store=store)


def _void_applying(ctx: _Ctx, ledger: dict) -> None:
    """The amendment was never written: nothing outside the ledger happened, so the intent is withdrawn."""
    applying = ledger.pop("applying")
    for fp in applying["fps"]:
        ledger["entries"].pop(fp, None)
    for fp in applying["retried"]:
        ledger["entries"].get(fp, {}).pop("retried", None)
    _save(ctx.manifest, ctx.path, ledger, store=ctx.engine.store)


def _resume(ctx: _Ctx, ledger: dict) -> bool:
    """Finish or withdraw an intent left by a crashed pass. Returns whether anything was done."""
    applying = ledger.get("applying")
    if not applying:
        return False
    try:
        written = load_amendment_file(applying["file"]).get("amendment_id") == applying["amendment_id"]
    except ValidationError:
        written = False
    (_apply_and_finish if written else _void_applying)(ctx, ledger)
    return True


def _context(engine, manifest: Manifest, path: str, config: dict, apply_system) -> _Ctx:
    acceptance = _acceptance_for_manifest(manifest, path)
    return _Ctx(engine, manifest, path, config, resolve_defects(config, len(manifest.nodes)), acceptance,
                anchor_ids(manifest, acceptance), _human_amendment_pending(manifest), apply_system or apply_amendment)


def resume_system_amendment(engine, manifest: Manifest, path: str, config: dict, *, apply_system=None) -> bool:
    """``dag run|tick`` start: finish a system amendment a previous process left half applied."""
    ctx = _context(engine, manifest, path, config, apply_system)
    return _resume(ctx, ledger_of(manifest))


def advance(engine, manifest: Manifest, path: str, config: dict, *, apply_system=None) -> bool:
    """Route every pending defect report once. Returns True when the manifest or a platform decision changed."""
    ledger = ledger_of(manifest)
    if not (ledger.get("applying") or ledger["inbox"] or any(n.defect_pending for n in manifest.nodes.values())):
        return False                    # nothing reported: a tick costs no extra reads
    ctx = _context(engine, manifest, path, config, apply_system)
    changed = _resume(ctx, ledger)
    if changed:
        ledger = ledger_of(manifest)
    for entry in ledger["entries"].values():       # a merged (or abandoned) fix makes its defect `fixed`
        fix = manifest.nodes.get(entry.get("fix_node"))
        if entry["state"] == "fixing" and fix is not None and fix.status in SATISFIED:
            entry["state"] = "fixed"
    reports, flags = _pending_reports(engine.store, manifest, ledger)
    before, decisions, pass_ = copy.deepcopy(ledger), [], _Pass()
    consumed: list[_Report] = []
    for report in reports:
        if report.source == "inbox" and manifest.nodes.get(report.reporter) is None:
            continue
        verdict, detail = _route_report(ctx, ledger, report, pass_)
        if verdict in STOPS:
            _stop(ctx, ledger, report, verdict, detail, decisions, flags)
        elif verdict is Route.QUEUED:
            _record(ledger, report, "queued", ctx)
        else:
            consumed.append(report)
        if verdict not in STOPS:        # a stop is logged once, by `_stop`
            log.info(logsetup.EVT_DEFECT_ROUTED, kind="dag", reporter=report.reporter, route=verdict.value, fps=report.fps)
    if ledger != before or decisions or flags:
        _save(manifest, path, ledger, flags, store=engine.store, decisions=decisions)
        changed = True
    if consumed:
        return _commit(ctx, ledger_of(manifest), pass_, consumed) or changed
    return changed


def _commit(ctx: _Ctx, ledger: dict, pass_: _Pass, consumed: list[_Report]) -> bool:
    fix_nodes = _fix_nodes(ctx, pass_)
    new_entries = {}
    for suspected, group in pass_.groups.items():
        for defect, fp in zip(group["defects"], group["fps"]):
            new_entries[fp] = {"suspected": suspected, "anchors": sorted(set(defect["anchors"])),
                               "reporters": sorted(group["reporters"]), "state": "applying",
                               "fix_node": fix_nodes[suspected]["id"]}
    reason = "Route upstream defect(s): " + ", ".join(sorted({fp for r in consumed for fp in r.fps}))
    proposal = build_system_proposal(ctx.manifest, list(fix_nodes.values()), pass_.attach, reason)
    if not proposal["operations"]:        # the reporters already wait for those fixes: just consume the reports
        _save(ctx.manifest, ctx.path, ledger, {r.reporter: False for r in consumed}, store=ctx.engine.store)
        return True
    inbox_ids = [_inbox_id(r.reporter, r.defects) for r in consumed if r.source == "inbox"]
    try:
        _carry_out(ctx, ledger, proposal, reporters=sorted(pass_.attach), new_entries=new_entries, retried=pass_.retried,
                   charges=pass_.charges, inbox_ids=inbox_ids, acceptance=ctx.acceptance)
    except ValidationError as exc:
        _stop_all(ctx, consumed, str(exc))
    return True


def _stop_all(ctx: _Ctx, consumed: list[_Report], detail: str) -> None:
    """The system amendment failed its checks: a deterministic result, so every carried report stops as unroutable."""
    ledger, decisions, flags = ledger_of(ctx.manifest), [], {}
    ledger.pop("applying", None)
    for report in consumed:
        _stop(ctx, ledger, report, Route.UNROUTABLE, detail.replace("\n", " "), decisions, flags)
    _save(ctx.manifest, ctx.path, ledger, flags, store=ctx.engine.store, decisions=decisions)


# --- final acceptance (PLAN G2) ----------------------------------------------------------------------------------------

def recurred_flows(engine, manifest: Manifest, path: str, failed_flows: list[str]) -> list[str]:
    """Flows that failed again after an increment was already merged for them (marked ``recurred``: the caller stops)."""
    ledger = ledger_of(manifest)
    again = [flow for flow in failed_flows if fingerprint("", [flow]) in ledger["entries"]]
    for flow in again:
        ledger["entries"][fingerprint("", [flow])]["state"] = "recurred"
    if again:
        _save(manifest, path, ledger, store=engine.store)
    return again


def merge_acceptance_increment(engine, manifest: Manifest, path: str, increment: Manifest, failed_flows: list[str],
                               config: dict, *, apply_system=None) -> None:
    """Merge a failed round's increment through a system amendment (add-only), recorded per flow in the defect ledger.

    The increment was linted against the manifest alone, so no acceptance document is bound. Only
    ``acceptance.max_rounds`` bounds these rounds; they are not charged to the defect budgets.
    """
    ctx = _context(engine, manifest, path, config, apply_system)
    ctx.acceptance = None
    fps = {flow: fingerprint("", [flow]) for flow in failed_flows}
    nodes = []
    for node in increment.nodes.values():
        value = _node_dict(node, include_runtime=False)
        value["contract"] = {**(value.get("contract") or {}), "defect_refs": sorted(fps.values())}
        nodes.append(value)
    proposal = build_system_proposal(manifest, nodes, {}, "Final acceptance failed: " + ", ".join(failed_flows))
    entries = {fp: {"suspected": "", "anchors": [flow], "reporters": [], "state": "applying",
                    "fix_node": sorted(n["id"] for n in nodes)[0]} for flow, fp in fps.items()}
    try:
        _carry_out(ctx, ledger_of(manifest), proposal, reporters=[], new_entries=entries, retried={}, charges={},
                   inbox_ids=[], acceptance=None)
    except ValidationError as exc:
        raise NeedsDecision(ui(
            f"The incremental decomposition cannot be merged:\n  - {exc}",
            f"增量拆解无法并入 DAG:\n  - {exc}"), report={"reason_code": "defect-unroutable", "errors": str(exc)}) from exc


# --- operator commands (omac defect ...) ---------------------------------------------------------------------------------

def report_defects(manifest: Manifest, path: str, before: str, defects: list) -> dict:
    """Queue a person's defect report: ``before`` waits for the fix. Validated now, routed on the next ``dag run``."""
    acceptance = _acceptance_for_manifest(manifest, path)
    errors = defect_errors(defects, repro_required=False)
    node = manifest.nodes.get(before)
    if node is None or node.status not in ("todo", "blocked"):
        errors.append(f"--before {before!r} must be a todo or blocked node of this manifest")
    anchors = anchor_ids(manifest, acceptance)
    if not errors:
        errors += [p for d in defects for p in [routing_problem(d, before, manifest, anchors)] if p]
    ledger = ledger_of(manifest)
    if not errors and len(ledger["inbox"]) >= INBOX_MAX:
        errors.append(f"{INBOX_MAX} reports are already waiting; run `omac dag run {path}` first")
    if errors:
        raise ValidationError(ui(
            "Defect report refused:\n  - " + "\n  - ".join(errors),
            "缺陷报告被拒绝:\n  - " + "\n  - ".join(errors)))
    entry = {"id": _inbox_id(before, defects), "reporter": before, "defects": defects}
    ledger["inbox"] = [e for e in ledger["inbox"] if e["id"] != entry["id"]] + [entry]
    apply([SetDagFact("defects", ledger), SaveState()], Ports(), ManifestState(manifest, path))
    return {"queued": [fingerprint(d["suspected_node"], d["anchors"]) for d in defects], "reporter": before,
            "next_action": f"omac dag run {path}"}


def grant_budget(manifest: Manifest, path: str, reporter: str, count: int) -> dict:
    """Add ``count`` automatic fixes to a reporter and re-queue its stopped report."""
    node = manifest.nodes.get(reporter)
    if node is None:
        raise ValidationError(ui(f"Unknown node {reporter!r}", f"未知节点 {reporter!r}"))
    ledger = ledger_of(manifest)
    ledger["granted_by_reporter"][reporter] = ledger["granted_by_reporter"].get(reporter, 0) + count
    ledger["inbox"] = [{k: v for k, v in e.items() if k != "stopped"} if e["reporter"] == reporter else e for e in ledger["inbox"]]
    effects: list[Any] = [SetDagFact("defects", ledger)]
    if node.status == "blocked" and node.work_item_id:
        effects.append(SetNode(reporter, {"defect_pending": True}))
    apply(effects + [SaveState()], Ports(), ManifestState(manifest, path))
    return {"reporter": reporter, "granted": ledger["granted_by_reporter"][reporter], "next_action": f"omac dag run {path}"}


def describe(manifest: Manifest) -> dict:
    """Read-only view of the ledger for ``omac defect list``."""
    ledger = ledger_of(manifest)
    return {**{key: ledger[key] for key in ("used_global", "used_by_reporter", "granted_by_reporter", "entries", "inbox")},
            "applying": ledger.get("applying"), "pending_reporters": sorted(k for k, n in manifest.nodes.items() if n.defect_pending),
            "pending_human_amendment": manifest.dag_state.get("pending_human_amendment")}

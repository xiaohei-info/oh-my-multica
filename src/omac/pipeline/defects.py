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
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from datetime import datetime

from ..core import logsetup
from ..core.amendment import _node_dict, build_system_amendment
from ..core.config import resolve_defects
from ..core.graph import SATISFIED
from ..core.defects import (
    INBOX_MAX, LEDGER_MAX_ENTRIES, UPSTREAM_DEFECT, build_fix_node, build_system_proposal, defect_errors, fingerprint, anchor_ids,
    global_limit, new_ledger, routing_problem,
)
from ..core.manifest import Manifest, adopt_disk
from ..core.pathrefs import persist_path, resolve_path
from ..errors import NeedsDecision, PlatformError, ValidationError
from ..i18n import ui
from .amendment import _acceptance_for_manifest, _write_yaml_atomic, apply_amendment, load_amendment_file
from .apply import ManifestState, Ports, apply
from .decide_defects import (
    DefectFacts, Route, STOPS, budget_detail, budget_summary, decide_defect, decide_report, render_release, stop_summary,
)
from .decide_effects import SaveState, SetDagFact, SetNode, UpdateMetadata
from .decide_runs import parse_platform_time

log = logsetup.get_logger(__name__)
_WAIT_CAUSES_MAX = 8
_CODE_STOPS = {Route.NEEDS_OWNER, Route.UNROUTABLE, Route.GENERATION_CAP, Route.NODE_FIX_CAP}


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
    budget: dict | None = None         # which budget layers stopped it (`decide_defects.budget_detail`), set when one did


@dataclass
class _Pass:
    """Mutable bookkeeping of one ``advance`` pass; small enough to snapshot per report."""
    groups: dict = field(default_factory=dict)      # suspected → {"fps", "defects", "reporters"}
    attach: dict = field(default_factory=dict)      # reporter → [fix node ids]
    charges: dict = field(default_factory=dict)     # reporter → fixes charged in this pass
    retried: dict = field(default_factory=dict)     # fp → defect, re-run once against a fix that was merged meanwhile


def _inbox_id(reporter: str, defects: list) -> str:
    return hashlib.sha256(json.dumps([reporter, defects], sort_keys=True).encode()).hexdigest()[:12]


def _wait_causes(manifest: Manifest, ledger: dict, suspected: str, fp: str) -> list[str]:
    """What a ``queued`` defect waits for, named from the manifest alone: the person's amendment and the other fixes of the
    same suspected node still in flight. Bounded, sorted, comparable: the wait ends exactly when this list changes."""
    causes = []
    if _human_amendment_pending(manifest):
        causes.append("amendment:" + str((manifest.dag_state.get("pending_human_amendment") or {}).get("amendment_id")))
    causes += [f"fix:{e.get('fix_node') or other}" for other, e in ledger["entries"].items()
               if other != fp and e.get("suspected") == suspected and e.get("state") in ("applying", "fixing")]
    return sorted(set(causes))[:_WAIT_CAUSES_MAX]


def _wait_ended(manifest: Manifest, ledger: dict) -> bool:
    """Is any recorded ``queued`` wait over or different? A wait recorded by an older version has no causes and is skipped:
    the process-local signature covers it until its next pass records the causes."""
    if not (ledger["inbox"] or any(n.defect_pending for n in manifest.nodes.values())):
        return False
    return any(e.get("state") == "queued" and "waits_on" in e
               and e["waits_on"] != _wait_causes(manifest, ledger, e.get("suspected", ""), fp)
               for fp, e in ledger["entries"].items())


def _human_amendment_pending(manifest: Manifest) -> bool:
    marker = manifest.dag_state.get("pending_human_amendment")
    if not marker:
        return False
    try:
        amendment = load_amendment_file(resolve_path(marker.get("file") or "", manifest.source_path))
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
    reporter_used = ledger["used_by_reporter"].get(report.reporter, 0) + pass_.charges.get(report.reporter, 0)
    reporter_limit = cfg["per_reporter"] + ledger["granted_by_reporter"].get(report.reporter, 0)
    global_used, global_cap = ledger["used_global"] + len(pass_.groups), global_limit(ledger, cfg["max_auto_fixes"])
    reporter_ok = (group is not None and report.reporter in group["reporters"]) or reporter_used < reporter_limit
    reporter_node = manifest.nodes.get(report.reporter)
    fixed_others = [e for e in others if e["state"] == "fixed" and e.get("fix_node")]      # a fix node the reporter can wait for
    done = not entry.get("retried") and bool(fixed_others)
    return DefectFacts(
        auto_fix=cfg["auto_fix"],
        unroutable=routing_problem(defect, report.reporter, manifest, anchors),
        defect_class=defect["class"],
        reporter_generation=1 if reporter_node and reporter_node.contract and reporter_node.contract.defect_refs else 0,
        state=state,
        human_amendment_pending=ctx.human_pending,
        inflight_other_fix=any(e["state"] in ("applying", "fixing") for e in others),
        done_other_fix=done,
        other_fix_after_run=_other_fix_after_run(ctx, report.reporter, fixed_others) if done else None,
        fixed_on_suspected=len({e.get("fix_node") or other for other, e in ledger["entries"].items()
                                if other != fp and e.get("suspected") == suspected and e["state"] == "fixed"}),
        suspected_fix_cap=cfg["per_suspected"],
        reporter_budget_left=reporter_ok,
        global_budget_left=group is not None or global_used < global_cap,
        reporter_used=reporter_used, reporter_limit=reporter_limit, global_used=global_used, global_limit=global_cap)


def _newest_run_start(ctx: "_Ctx", reporter: str) -> datetime | None:
    """When the reporter's newest Run started (the Run its report rests on); None when it cannot be read."""
    if reporter in ctx.run_started:
        return ctx.run_started[reporter]
    node, started = ctx.manifest.nodes.get(reporter), None
    runtime = ctx.engine.runtime
    if node is not None and node.work_item_id and runtime is not None:
        try:
            times = [parse_platform_time(run.created_at) for run in runtime.list_runs(node.work_item_id)]
        except PlatformError as exc:    # a read that fails must not change the route: unknown keeps the old behaviour
            log.info(logsetup.EVT_DEFECT_ROUTED, kind="dag", reporter=reporter, route="run-time-unreadable", error=str(exc)[:200])
            times = []
        started = max((t for t in times if t is not None and t.tzinfo is not None), default=None)
    ctx.run_started[reporter] = started
    return started


def _other_fix_after_run(ctx: "_Ctx", reporter: str, fixed: list[dict]) -> bool | None:
    """Was a merged fix of the same node merged after the reporter's newest Run began? None when a time is unknown.

    shortcut: time comparison only; neither the fix's merge commit nor the Run's base commit is recorded, so ancestry
    cannot be asked. Upgrade when the code host port can return a PR's merge commit.
    """
    started = _newest_run_start(ctx, reporter)
    merged = [parse_platform_time(getattr(ctx.manifest.nodes.get(e["fix_node"]), "merged_at", None)) for e in fixed]
    merged = [m if m is None or m.tzinfo is not None else None for m in merged]
    if started is None or all(m is None for m in merged):
        reason = "the reporter's Run start" if started is None else "the fix's merge time"
        log.info(logsetup.EVT_DEFECT_ROUTED, kind="dag", reporter=reporter, route="retry-after-fix-unverified",
                 reason=f"{reason} is unknown; keeping the retry")
        return None
    if any(m is not None and m > started for m in merged):
        return True
    if any(m is None for m in merged):
        return None
    return False


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
    run_started: dict = field(default_factory=dict)     # reporter → start of its newest Run (None: unknown), read once per pass


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
        if route is Route.BUDGET_EXHAUSTED and report.budget is None:
            report.budget = budget_detail(facts)
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
                if other != fp and e.get("suspected") == suspected and e["state"] == "fixed" and e.get("fix_node"))


def _record(ledger: dict, report: _Report, state: str, ctx: _Ctx) -> None:
    if not report.fps:
        return
    for defect, fp in zip(report.defects, report.fps):
        entry = ledger["entries"].setdefault(fp, {
            "suspected": defect["suspected_node"], "anchors": sorted(set(defect["anchors"])), "reporters": [], "state": state})
        if report.reporter not in entry["reporters"]:
            entry["reporters"].append(report.reporter)
        entry["state"] = state if entry["state"] in ("queued", "needs-owner", state) else entry["state"]
        if entry["state"] == "queued":      # the wait is part of the ledger: a restart, `defect list` and the next pass all see it
            entry["waits_on"] = _wait_causes(ctx.manifest, ledger, defect["suspected_node"], fp)
        else:
            entry.pop("waits_on", None)


def _stop(ctx: _Ctx, ledger: dict, report: _Report, route: Route, detail: str | None, decisions: list, flags: dict) -> None:
    budget = report.budget if route is Route.BUDGET_EXHAUSTED else None
    actions = render_release(route, ctx.path, report.reporter, tuple(budget["layers"]) if budget else ("reporter",))
    reason = (detail or (budget_summary(budget) if budget else stop_summary(route)))[:300]
    if route is Route.RECURRED:
        for fp in report.fps:
            if ledger["entries"].get(fp, {}).get("state") in ("fixed", "recurred"):
                ledger["entries"][fp]["state"] = "recurred"
    elif route in _CODE_STOPS:
        _record(ledger, report, "needs-owner", ctx)
    elif route is Route.BUDGET_EXHAUSTED:
        _record(ledger, report, "queued", ctx)      # remembered with its stop, so a `defect grant --global` finds who to release
    for fp in report.fps:
        if fp in ledger["entries"]:
            ledger["entries"][fp]["stop"] = {"reason_code": route.value, "detail": reason}
            ledger["entries"][fp].pop("waits_on", None)
    if report.item_id is not None:
        decisions.append((report.item_id, {
            **report.decision, "reason_code": route.value, "next_action": actions[0], "alternatives": actions[1:],
            "detail": reason, **({"budget": budget} if budget else {})}))
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
        entry.pop("waits_on", None)
    ledger["applying"] = {"amendment_id": amendment["amendment_id"], "file": persist_path(file, ctx.path), "fps": sorted(new_entries),
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
        ctx.path, load_amendment_file(resolve_path(applying["file"], ctx.path)), store, set(store.list_members(store.config.workspace_id)),
        amendment_file=resolve_path(applying["file"], ctx.path), acceptance=ctx.acceptance, runtime=ctx.engine.runtime)
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
        written = load_amendment_file(resolve_path(applying["file"], ctx.path)).get("amendment_id") == applying["amendment_id"]
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


def _promote_fixed(manifest: Manifest, ledger: dict) -> bool:
    """A merged (or abandoned) fix makes its defect `fixed`. Returns whether any entry changed."""
    promoted = False
    for entry in ledger["entries"].values():
        fix = manifest.nodes.get(entry.get("fix_node"))
        if entry["state"] == "fixing" and fix is not None and fix.status in SATISFIED:
            entry["state"] = "fixed"
            promoted = True
    return promoted


# manifest path → what the last pass saw when every pending report only waited (``queued``). Process-local on purpose:
# losing it (a restart) costs one more routing pass, never a wrong route.
_QUEUED_SEEN: dict[str, str] = {}


def _wait_signature(manifest: Manifest, ledger: dict, cfg: dict) -> str:
    """Everything that can end a ``queued`` wait, read from the manifest alone (no platform read).

    A wait ends when a human amendment settles, a fix of the same node changes state or finishes, a report appears or
    goes, or the defect config (budgets) changes.
    """
    entries = {fp: (e.get("state"), e.get("fix_node"), e.get("retried"), e.get("stop")) for fp, e in ledger["entries"].items()}
    fixes = {e["fix_node"]: getattr(manifest.nodes.get(e["fix_node"]), "status", None)
             for e in ledger["entries"].values() if e.get("fix_node")}
    pending = {k: (n.status, n.work_item_id) for k, n in manifest.nodes.items() if n.defect_pending}
    return json.dumps([entries, fixes, pending, ledger["inbox"], ledger.get("applying"), _human_amendment_pending(manifest),
                       cfg, ledger.get("used_global"), ledger.get("granted_global"), ledger["granted_by_reporter"],
                       ledger["used_by_reporter"]], sort_keys=True, default=str)


def advance(engine, manifest: Manifest, path: str, config: dict, *, apply_system=None) -> bool:
    """Route every pending defect report once. Returns True when the manifest or a platform decision changed.

    Reports that only wait (``queued``) are not routed again, and cost no platform read, until what they wait for changes.
    """
    ledger = ledger_of(manifest)
    promoted = _promote_fixed(manifest, ledger)
    signature = _wait_signature(manifest, ledger, resolve_defects(config, len(manifest.nodes)))
    if _QUEUED_SEEN.get(path) == signature and not promoted and not _wait_ended(manifest, ledger):
        return False
    _QUEUED_SEEN.pop(path, None)
    if not (ledger.get("applying") or ledger["inbox"] or any(n.defect_pending for n in manifest.nodes.values())):
        if promoted:                    # nothing reported, but a fix finished: make `fixed` durable
            _save(manifest, path, ledger, store=engine.store)
        return promoted                 # nothing reported: a tick costs no extra reads
    ctx = _context(engine, manifest, path, config, apply_system)
    changed = _resume(ctx, ledger)
    if changed:
        ledger = ledger_of(manifest)
    _promote_fixed(manifest, ledger)
    reports, flags = _pending_reports(engine.store, manifest, ledger)
    before, decisions, pass_ = ledger_of(manifest), [], _Pass()      # the stored ledger: a promotion is saved too
    consumed: list[_Report] = []
    waiting = bool(reports)
    for report in reports:
        if report.source == "inbox" and manifest.nodes.get(report.reporter) is None:
            continue
        verdict, detail = _route_report(ctx, ledger, report, pass_)
        waiting = waiting and verdict is Route.QUEUED
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
        changed = not waiting or bool(decisions)        # a pass that only recorded `queued` is not a change to observe again
    if waiting and not consumed and not changed and not decisions:
        _QUEUED_SEEN[path] = _wait_signature(manifest, ledger_of(manifest), ctx.cfg)    # after the save: what we now wait on
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


def grant_budget(manifest: Manifest, path: str, reporter: str | None, count: int, *, global_scope: bool = False) -> dict:
    """Add ``count`` automatic fixes to a reporter (or, with ``global_scope``, to the DAG-wide allowance) and re-queue the
    reports that a spent budget stopped. The stop names the spent layer; the grant must be of that layer."""
    if global_scope == (reporter is not None):
        raise ValidationError(ui("Name exactly one of --reporter <node> or --global", "--reporter <节点> 与 --global 必须二选一"))
    node = manifest.nodes.get(reporter) if reporter else None
    if reporter and node is None:
        raise ValidationError(ui(f"Unknown node {reporter!r}", f"未知节点 {reporter!r}"))
    ledger = ledger_of(manifest)
    if global_scope:
        ledger["granted_global"] = ledger.get("granted_global", 0) + count
        stopped = {r for e in ledger["entries"].values() if e.get("stop", {}).get("reason_code") == Route.BUDGET_EXHAUSTED.value
                   for r in e["reporters"]} | {e["reporter"] for e in ledger["inbox"] if e.get("stopped") == Route.BUDGET_EXHAUSTED.value}
        requeue = sorted(r for r in stopped if r in manifest.nodes)
    else:
        ledger["granted_by_reporter"][reporter] = ledger["granted_by_reporter"].get(reporter, 0) + count
        requeue = [reporter]
    ledger["inbox"] = [{k: v for k, v in e.items() if k != "stopped"} if e["reporter"] in requeue else e for e in ledger["inbox"]]
    effects: list[Any] = [SetDagFact("defects", ledger)]
    effects += [SetNode(r, {"defect_pending": True}) for r in requeue if manifest.nodes[r].status == "blocked" and manifest.nodes[r].work_item_id]
    apply(effects + [SaveState()], Ports(), ManifestState(manifest, path))
    granted = ledger["granted_global"] if global_scope else ledger["granted_by_reporter"][reporter]
    return {"scope": "global" if global_scope else "reporter", "reporter": reporter, "granted": granted, "requeued": requeue,
            "next_action": f"omac dag run {path}"}


def resolve_defect(manifest: Manifest, path: str, fp: str, pr_url: str) -> dict:
    """Record a fix that was merged outside OMAC: the fingerprint becomes ``fixed`` (it has no fix node), the PR url is kept."""
    ledger = ledger_of(manifest)
    entry = ledger["entries"].get(fp)
    if entry is None:
        raise ValidationError(ui(
            f"Unknown defect fingerprint {fp!r}; list them with `omac defect list {path}`",
            f"未知的缺陷指纹 {fp!r};用 `omac defect list {path}` 查看"))
    if entry["state"] in ("applying", "fixing"):
        raise ValidationError(ui(
            f"{fp} is already being fixed by {entry.get('fix_node')!r}; wait for that fix node instead",
            f"{fp} 已由修复节点 {entry.get('fix_node')!r} 在修复中;请等待该节点"))
    entry["state"], entry["external_fix"] = "fixed", pr_url
    entry.pop("stop", None)
    # a queued report of exactly this fingerprint has nothing left to route
    ledger["inbox"] = [e for e in ledger["inbox"] if {fingerprint(d["suspected_node"], d["anchors"]) for d in e["defects"]} != {fp}]
    apply([SetDagFact("defects", ledger), SaveState()], Ports(), ManifestState(manifest, path))
    blocked = [r for r in entry["reporters"] if r in manifest.nodes and manifest.nodes[r].status == "blocked"]
    return {"fingerprint": fp, "state": "fixed", "external_fix": pr_url, "reporters": entry["reporters"],
            "next_actions": [f"omac node retry {shlex.quote(path)} {shlex.quote(r)}" for r in blocked] or [f"omac dag run {shlex.quote(path)}"]}


def describe(manifest: Manifest) -> dict:
    """Read-only view of the ledger for ``omac defect list``."""
    ledger = ledger_of(manifest)
    return {**{key: ledger[key] for key in ("used_global", "granted_global", "used_by_reporter", "granted_by_reporter", "entries", "inbox")},
            "applying": ledger.get("applying"), "pending_reporters": sorted(k for k, n in manifest.nodes.items() if n.defect_pending),
            "pending_human_amendment": manifest.dag_state.get("pending_human_amendment")}

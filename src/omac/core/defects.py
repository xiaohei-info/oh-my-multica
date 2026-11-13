"""Upstream-defect feedback (3.0): the report schema, fingerprint, routability, fix-node template, system proposal and
ledger helpers. Everything here is a pure function of a manifest and plain data (设计 §7.5).

A reporter (a Worker, a Reviewer whose blockers belong to finished upstream work, or an operator) names the finished node
it suspects and the stable anchors (flow / action / provides ids) the defect violates. Code then fingerprints the defect,
checks that it can be routed, and turns it into an add-only amendment: a fix node plus a ``blocked_by`` edge from the
reporter to it. Nothing in a report is a command; every field is checked here before it can change the DAG.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .acceptance_responsibility import dependency_closure
from .manifest import Manifest, _dump_contract

SYSTEM_POLICY = "defects"
UPSTREAM_DEFECT = "upstream-defect"          # the worker-blocker reason code that carries a defect report
CLASSES = ("design-gap", "contract-gap", "tradeoff")
MAX_DEFECTS = 6
FP_PREFIX = "dfx-"
FIX_PREFIX = "fix."
LEDGER_SCHEMA = "omac.defect-ledger/v1"
LEDGER_MAX_ENTRIES = 200
INBOX_MAX = 5
# Defect states only move forward; ``queued`` waits for a condition, the others are decisions already taken.
STATES = ("queued", "applying", "fixing", "fixed", "recurred", "needs-owner")
_FIELDS = {"class", "suspected_node", "anchors", "design_ref", "expected", "observed", "locations", "repro"}
_TEXT_LIMIT = 1000


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= _TEXT_LIMIT


def _list_of_text(value: Any, limit: int) -> bool:
    return isinstance(value, list) and len(value) <= limit and all(_text(item) for item in value)


def defect_errors(defects: Any, *, repro_required: bool = True) -> list[str]:
    """Shape errors of a defect list (empty = well formed). Review-origin defects carry no repro command."""
    if not isinstance(defects, list) or not 1 <= len(defects) <= MAX_DEFECTS:
        return [f"defects must be a list of 1-{MAX_DEFECTS} objects"]
    errors: list[str] = []
    for index, defect in enumerate(defects):
        prefix = f"defects[{index}]"
        if not isinstance(defect, dict):
            errors.append(f"{prefix} must be an object")
            continue
        optional = set() if repro_required else {"repro"}
        if set(defect) - _FIELDS or (_FIELDS - optional) - set(defect):
            errors.append(f"{prefix} must have exactly the fields {sorted(_FIELDS - optional)}")
            continue
        if defect["class"] not in CLASSES:
            errors.append(f"{prefix}.class must be one of {', '.join(CLASSES)}")
        for name in ("suspected_node", "design_ref", "expected", "observed"):
            if not _text(defect[name]):
                errors.append(f"{prefix}.{name} must be a non-empty string")
        if not _list_of_text(defect["anchors"], 8) or not defect["anchors"]:
            errors.append(f"{prefix}.anchors must list 1-8 flow / action / provides ids")
        if not _list_of_text(defect["locations"], 6):
            errors.append(f"{prefix}.locations must be a list of at most 6 strings")
        repro = defect.get("repro")
        if repro is not None or repro_required:
            if not (isinstance(repro, dict) and set(repro) == {"command", "exit_code"} and _text(repro["command"])
                    and type(repro["exit_code"]) is int and repro["exit_code"] != 0):
                errors.append(f"{prefix}.repro must be {{command: <failing command>, exit_code: <non-zero int>}}")
    if not errors and len({fingerprint(d["suspected_node"], d["anchors"]) for d in defects}) != len(defects):
        errors.append("defects contains the same suspected_node + anchors twice; merge them into one entry")
    return errors


def fingerprint(suspected: str, anchors: list[str]) -> str:
    """Stable identity of a defect: the suspected node and the sorted anchor ids (prose never enters it)."""
    payload = json.dumps([suspected, sorted(set(anchors))], separators=(",", ":"))
    return FP_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def anchor_ids(manifest: Manifest, acceptance: Any = None) -> set[str]:
    """Every id a defect may anchor to: acceptance flows (``flow`` and ``flow/action``), requirements, provides."""
    anchors: set[str] = set()
    for flow in getattr(acceptance, "flows", None) or []:
        anchors.add(flow.id)
        anchors.update(f"{flow.id}/{action.id}" for action in flow.actions)
    anchors.update(requirement.id for requirement in getattr(acceptance, "requirements", None) or [])
    for node in manifest.nodes.values():
        for artifact in getattr(node.contract, "produces", None) or []:
            anchors.update(item.id for item in getattr(artifact, "provides", None) or [])
    return anchors


def _source_paths(node: Any) -> set[str]:
    return {str(ref).split("#")[0].strip() for ref in getattr(node.contract, "source_of_truth", None) or []}


def routing_problem(defect: dict, reporter: str, manifest: Manifest, anchors: set[str]) -> str | None:
    """Why this defect cannot be routed to a fix node (None = routable). Taught, not guessed: the text names the repair."""
    suspected = defect["suspected_node"]
    node = manifest.nodes.get(suspected)
    if node is None:
        return f"suspected_node {suspected!r} is not a node of this manifest"
    if node.status != "done":
        return (f"suspected node {suspected!r} is {node.status}, not done: an unfinished node is fixed in place "
                "(review rework or `omac node retry`), not through a fix node")
    if reporter in manifest.nodes and reporter in dependency_closure(manifest, suspected):
        return f"suspected node {suspected!r} runs after the reporter {reporter!r}; a defect can only be routed upstream"
    unknown = sorted(a for a in defect["anchors"] if anchors and a not in anchors)
    if unknown:
        return f"anchors {unknown} are not in the acceptance document or any provides; use ids that exist"
    path = defect["design_ref"].split("#")[0].strip()
    sources = _source_paths(node) | (_source_paths(manifest.nodes[reporter]) if reporter in manifest.nodes else set())
    if path not in sources:
        return (f"design_ref {defect['design_ref']!r} is not under the source_of_truth of {suspected!r} or {reporter!r} "
                f"({sorted(sources)})")
    return None


def fix_node_id(suspected: str, fps: list[str]) -> str:
    return f"{FIX_PREFIX}{suspected}.{min(fps)[len(FP_PREFIX):][:6]}"


def _flow_refs(source_contract: Any, anchors: list[str], flow_ids: set[str]) -> list[str]:
    refs = sorted({a.split("/")[0] for a in anchors if a.split("/")[0] in flow_ids})
    inherited = list(source_contract.acceptance_refs or source_contract.acceptance_claims or source_contract.acceptance or [])
    return refs or inherited or sorted(anchors)


def _fix_description(reporters: list[str], defects: list[dict]) -> str:
    lines = [f"Automatic fix for upstream defect(s) reported by {', '.join(sorted(reporters))}.", ""]
    for defect in defects:
        lines += [f"- [{defect['class']}] anchors {', '.join(defect['anchors'])} ({defect['design_ref']})",
                  f"  expected: {defect['expected']}", f"  observed: {defect['observed']}"]
        if defect.get("locations"):
            lines.append(f"  where: {'; '.join(defect['locations'])}")
        if defect.get("repro"):
            lines.append(f"  repro (the reporter re-runs it after your fix merges; it is not your gate): "
                         f"`{defect['repro']['command']}` (exit {defect['repro']['exit_code']})")
    return "\n".join(lines)


def build_fix_node(manifest: Manifest, suspected: str, fps: list[str], defects: list[dict], reporters: list[str],
                   flow_ids: set[str]) -> dict:
    """The ``add`` value of a fix node: the suspected node's worker, reviewer, scope and gates, a defect-only objective.

    ``blocked_by`` copies the suspected node's (all satisfied) so its typed ``consumes`` stay legal; the fix node never
    claims acceptance responsibility, never produces or registers anything, and carries ``defect_refs``.
    """
    source = manifest.nodes[suspected]
    contract = _dump_contract(source.contract)
    for name in ("acceptance", "acceptance_claims", "acceptance_contributions", "produces", "registers", "requirement_refs"):
        contract.pop(name, None)
    # The reporter's repro command is NOT a gate of the fix node: it usually runs tests that exist only on the reporter's
    # unmerged branch. It stays in the description; the reporter re-runs it after the fix merges (a repeat is `defect-recurred`).
    contract.update(
        objective=f"Fix the upstream defect(s) {', '.join(sorted(fps))} in {suspected}: "
                  + "; ".join(d["expected"] for d in defects)[:600],
        acceptance_refs=_flow_refs(source.contract, [a for d in defects for a in d["anchors"]], flow_ids),
        defect_refs=sorted(fps))
    node = {"id": fix_node_id(suspected, fps), "worker": source.worker, "blocked_by": list(source.blocked_by),
            "title": f"Fix {suspected}: {', '.join(sorted(fps))}"[:120],
            "description": _fix_description(reporters, defects), "contract": contract}
    for name in ("reviewer", "risk", "gate"):
        if getattr(source, name) is not None:
            node[name] = getattr(source, name)
    return node


def build_system_proposal(manifest: Manifest, fix_nodes: list[dict], attach: dict[str, list[str]], reason: str) -> dict:
    """An amendment proposal of the only shapes the system identity allows: ``add`` fix nodes, ``update`` blocked_by, ``resume``."""
    operations: list[dict] = [{"op": "add", "value": node} for node in fix_nodes]
    for reporter, fixes in sorted(attach.items()):
        current = list(manifest.nodes[reporter].blocked_by)
        merged = current + [fix for fix in dict.fromkeys(fixes) if fix not in current]
        # Already waiting for that fix (it was merged meanwhile): the reporter only needs to run its authoring again.
        operations.append({"op": "update", "node": reporter, "set": {"blocked_by": merged}} if merged != current
                          else {"op": "resume", "node": reporter, "stage": "authoring"})
    return {"schema": "omac.dag-amendment/v1", "reason": reason, "operations": operations}


# --- ledger (``_dag.yaml`` → ``defects``) -------------------------------------------------------------------------------

def new_ledger() -> dict:
    return {"schema": LEDGER_SCHEMA, "used_global": 0, "used_by_reporter": {}, "granted_by_reporter": {},
            "entries": {}, "inbox": []}


def reporter_left(ledger: dict, reporter: str, per_reporter: int) -> int:
    allowed = per_reporter + ledger["granted_by_reporter"].get(reporter, 0)
    return allowed - ledger["used_by_reporter"].get(reporter, 0)


def review_origin_defects(blockers: list, reporter: str, manifest: Manifest) -> list[dict] | None:
    """Defects for a rejecting review whose every open blocker names a finished upstream node as its ``owner``.

    None when any blocker has no owner, or an owner that is not a done node upstream of the reporter: that review is
    ordinary rework. The blocker's own words become expected / observed; its obligation id is the anchor.
    """
    upstream = dependency_closure(manifest, reporter)
    defects: dict[str, dict] = {}
    for blocker in blockers:
        owner = blocker.get("owner") if isinstance(blocker, dict) else None
        if owner not in upstream or manifest.nodes[owner].status != "done":
            return None
        defect = defects.setdefault(owner, {
            "class": "design-gap", "suspected_node": owner, "anchors": [], "expected": [], "observed": [], "locations": []})
        defect["anchors"].append(str(blocker.get("obligation_id")))
        defect["expected"].append(str(blocker.get("required_fix")))
        defect["observed"].append(str(blocker.get("summary")))
        defect["locations"].append(str(blocker.get("evidence")))
    if len(defects) > MAX_DEFECTS:
        return None
    reporter_node = manifest.nodes[reporter]
    design_ref = (reporter_node.contract.source_of_truth or [""])[0] if reporter_node.contract else ""
    # The decision lives in 8 KiB platform metadata next to the report: keep the quoted review prose short.
    return [{**d, "anchors": sorted(set(d["anchors"]))[:8], "design_ref": design_ref,
             "expected": "; ".join(d["expected"])[:250], "observed": "; ".join(d["observed"])[:250],
             "locations": [loc[:150] for loc in d["locations"][:2]]} for d in defects.values()] or None

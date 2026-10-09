"""A Planner's terminal scope/source gap is a decision, never a proposal."""
from copy import deepcopy
from dataclasses import asdict
import json

from ..core.owner_amendment import digest, plain

from ..core.taskmeta import TaskKind, TaskPhase, review_context_binding, DECISION_REQUIRED_SCHEMA
from ..engines.models import WorkItemStatus
from ..errors import NeedsDecision, PlatformError, ValidationError

SCHEMA = "omac.amendment-scope-gap/v1"


def scope_gap_template(item):
    ref = next((r for r in item.source_refs if r.get("label") == "owner-source"), {})
    return {"schema": SCHEMA, "reason_code": "scope-gap", "issue_id": item.id,
            "worker": item.worker, "run_id": "<current-direct-planner-run-id>",
            "review_context_binding": review_context_binding(item),
            "owner_resolution": item.dag_key.removeprefix("amend-owner-") if item.dag_key.startswith("amend-owner-") else None,
            "owner_source_sha256": ref.get("content_sha256"),
            "summary": "<observed source or authorized assessment boundary gap>",
            "decision_needed": "<exact missing input or assessment authority>",
            "evidence": []}

def _control_sha(item):
    value = plain(asdict(item))
    # These are the two owned effects and their platform acknowledgement time.
    for key in ("decision_required", "status", "updated_at"):
        value.pop(key)
    return digest(value)


def _runs_sha(runs, run_id, *, decided):
    if len({r.id for r in runs}) != len(runs):
        raise ValidationError("Ambiguous native Run binding; read work show again")
    matches = [r for r in runs if r.id == run_id]
    if len(matches) != 1 or matches[0].status not in ({"running", "completed"} if decided else {"running"}):
        raise ValidationError("Original Planner Run binding changed; read work show again")
    values = []
    for run in runs:
        value = plain(asdict(run))
        if run.id == run_id:
            # The original Run may finish after recording its once-only decision.
            value["status"] = "running"
            value["updated_at"] = None
        values.append(value)
    return digest(sorted(values, key=lambda v: v["id"]))


def _observe_gap_binding(store, runtime, issue_id, decision, status):
    item = store.get_work_item(issue_id)
    binding = decision["scope_gap_binding"]
    if (digest(item.decision_required) != digest(decision)
            or item.status.value != status
            or _control_sha(item) != binding["control_sha256"]
            or store.resolve_agent_id(item.worker) != binding["agent_id"]
            or _runs_sha(runtime.list_runs(issue_id), decision["blocker"]["run_id"], decided=True)
            != binding["runs_sha256"]):
        raise ValidationError("Scope-gap control/Run binding changed; read work show without replay")
    if digest(plain(asdict(store.get_work_item(issue_id)))) != digest(plain(asdict(item))):
        raise ValidationError("Scope-gap control changed during native Run/agent observation; read work show")
    return item


def report_scope_gap(store, runtime, issue_id, report):
    if not isinstance(report, dict) or runtime is None:
        raise ValidationError("Use the amendment scope-gap template from omac work show")
    with store.worker_control_lock(issue_id):
        item = store.get_work_item(issue_id)
        template = scope_gap_template(item)
        fixed = {"schema", "issue_id", "worker", "review_context_binding", "owner_resolution", "owner_source_sha256"}
        if (set(report) != set(template) or any(report[k] != template[k] for k in fixed)
                or item.kind != TaskKind.AMENDMENT or item.phase != TaskPhase.AUTHORING
                or item.status not in {WorkItemStatus.IN_PROGRESS, WorkItemStatus.BLOCKED}
                or item.deliverable or item.review_verdict
                or report["reason_code"] not in {"scope-gap", "source-gap"}
                or any(not isinstance(report[k], str) or not report[k].strip()
                       for k in ("run_id", "summary", "decision_needed"))
                or not isinstance(report["evidence"], list)
                or len(json.dumps(report, ensure_ascii=False).encode()) > 2048):
            raise ValidationError("Stale or invalid amendment scope-gap; copy the current omac work show template")
        for evidence in report["evidence"]:
            if (not isinstance(evidence, dict) or set(evidence) != {"ref", "observation"}
                    or any(not isinstance(v, str) or not v.strip() for v in evidence.values())):
                raise ValidationError("Scope-gap evidence must retain exact references and observations")
        decision = {"schema": DECISION_REQUIRED_SCHEMA, "reason_code": "amendment-scope-gap",
                    "kind": item.kind.value, "phase": item.phase.value, "gate": "authoring-scope",
                    "resume_issue_id": item.id, "review_context_binding": review_context_binding(item),
                    "blocker": deepcopy(report), "next_action": "Caller must supply a fresh exact source/assessment decision; do not retry or dispatch automatically"}
        if item.decision_required:
            binding = item.decision_required.get("scope_gap_binding")
            if not isinstance(binding, dict):
                raise ValidationError("Existing scope-gap lacks complete control/Run binding; read work show")
            decision["scope_gap_binding"] = deepcopy(binding)
            if digest(item.decision_required) != digest(decision):
                raise NeedsDecision("An existing decision must be resolved first", report=item.decision_required)
        else:
            runs = runtime.list_runs(issue_id)
            active = [r for r in runs if r.active]
            agent = store.resolve_agent_id(item.worker)
            if (len(active) != 1 or active[0].id != report["run_id"]
                    or active[0].kind != "direct" or not active[0].formal
                    or active[0].status != "running" or active[0].agent_id != agent):
                raise ValidationError("Scope-gap requires the uniquely observed current running direct Planner Run")
            decision["scope_gap_binding"] = {
                "control_sha256": _control_sha(item), "agent_id": agent,
                "runs_sha256": _runs_sha(runs, report["run_id"], decided=False),
                "initial_status": item.status.value,
            }
            if (digest(plain(asdict(store.get_work_item(issue_id)))) != digest(plain(asdict(item)))
                    or digest(plain([asdict(r) for r in runtime.list_runs(issue_id)]))
                    != digest(plain([asdict(r) for r in runs]))
                    or store.resolve_agent_id(item.worker) != agent
                    or digest(plain(asdict(store.get_work_item(issue_id)))) != digest(plain(asdict(item)))):
                raise ValidationError("Amendment control/Run binding changed before scope-gap effect; read work show again")
            try:
                store.update_work_item_metadata(issue_id, decision_required=decision)
            except PlatformError:
                _observe_gap_binding(store, runtime, issue_id, decision, item.status.value)
            _observe_gap_binding(store, runtime, issue_id, decision, item.status.value)
        # Revalidate the entire original control and native Run before EACH effect.
        status = store.get_work_item(issue_id).status.value
        if status not in {decision["scope_gap_binding"]["initial_status"], WorkItemStatus.BLOCKED.value}:
            raise ValidationError("Scope-gap status/control binding changed; read work show")
        _observe_gap_binding(store, runtime, issue_id, decision, status)
        if status != WorkItemStatus.BLOCKED.value:
            try:
                store.update_status(issue_id, WorkItemStatus.BLOCKED)
            except PlatformError:
                _observe_gap_binding(store, runtime, issue_id, decision, WorkItemStatus.BLOCKED.value)
        _observe_gap_binding(store, runtime, issue_id, decision, WorkItemStatus.BLOCKED.value)
        return {"ok": False, "exit_code": 20, "terminal": True,
                "decision_required": decision, "next_action": "stop"}

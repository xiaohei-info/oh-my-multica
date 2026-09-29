"""Typed Worker requests for an operator decision, separate from delivery."""

from copy import deepcopy
import json

from ..core.taskmeta import (
    DECISION_REQUIRED_SCHEMA,
    TaskKind,
    TaskPhase,
    review_context_binding,
)
from ..engines.models import WorkItemStatus, WorkItemPayload
from ..errors import NeedsDecision, PlatformError, ValidationError

SCHEMA = "omac.worker-blocker/v2"
REASONS = {"quality-gate-failed", "owner-decision-required"}
FIELDS = {
    "schema",
    "reason_code",
    "issue_id",
    "review_context_binding",
    "handoff_generation",
    "worker",
    "run_id",
    "summary",
    "decision_needed",
    "contract_ref",
    "evidence",
}


def blocker_template(item):
    intent = item.worker_handoff
    if intent is None or not intent.is_causally_bound():
        return None
    return {
        "schema": SCHEMA,
        "reason_code": "owner-decision-required",
        "issue_id": item.id,
        "review_context_binding": review_context_binding(item),
        "handoff_generation": intent.generation,
        "worker": intent.target_worker,
        "run_id": intent.target_run_id or "<current-direct-worker-run-id>",
        "summary": "<observed blocking condition>",
        "decision_needed": "<specific owner decision required>",
        "contract_ref": "<existing top-level context.contract field requiring an owner decision>",
        "evidence": [
            {
                "ref": "<retained evidence location>",
                "observation": "<observed facts and limitations>",
            }
        ],
    }


def _text(value, limit=4096):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value.encode("utf-8")) <= limit
    )


def _validate(report, issue_id):
    def invalid():
        raise ValidationError(
            f"Invalid worker decision report (maximum 2048 UTF-8 JSON bytes, 1-4 evidence entries); read `omac work show {issue_id} --output json` and copy control.blocker_report_template, then run `omac work block {issue_id} --report-file <blocker.yaml>`"
        )

    if (
        set(report) != FIELDS
        or not isinstance(report.get("reason_code"), str)
        or report["reason_code"] not in REASONS
    ):
        invalid()
    try:
        size = len(json.dumps(report, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        invalid()
    if size > 2048:
        invalid()
    if any(
        not _text(report.get(key))
        for key in FIELDS - {"schema", "review_context_binding", "evidence"}
    ):
        invalid()
    evidence = report.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
        invalid()
    for fact in evidence:
        if not isinstance(fact, dict) or set(fact) - {
            "ref",
            "observation",
            "command",
            "exit_code",
        }:
            invalid()
        if not _text(fact.get("ref")) or not _text(fact.get("observation")):
            invalid()
        if ("command" in fact) != ("exit_code" in fact):
            invalid()
        if "command" in fact and (
            not _text(fact["command"]) or type(fact["exit_code"]) is not int
        ):
            invalid()
    if report["reason_code"] == "quality-gate-failed" and not any(
        "command" in fact and fact["exit_code"] != 0 for fact in evidence
    ):
        invalid()


def _read_context(store, issue_id):
    projection = store.observe_work_item_control(issue_id)
    control = projection.work_item
    if control.kind != TaskKind.DEVELOP or control.phase != TaskPhase.AUTHORING:
        raise ValidationError(
            f"Worker decisions require develop authoring; run `omac work show {issue_id} --output json`"
        )
    plan = frozenset({WorkItemPayload.CONTRACT})
    item = store.hydrate_work_item_evidence(projection, plan)
    from .dispatch import _require_submit_payloads_materialized

    _require_submit_payloads_materialized(projection, plan, item)
    return item


def report_decision(store, runtime, issue_id, report):
    _validate(report, issue_id)
    if runtime is None:
        raise ValidationError(
            "Worker decision reporting requires a Runtime with direct Run observation"
        )
    with store.worker_control_lock(issue_id):
        item = _read_context(store, issue_id)
        intent = item.worker_handoff
        binding = review_context_binding(item)
        if (
            item.kind != TaskKind.DEVELOP
            or item.phase != TaskPhase.AUTHORING
            or item.status not in {WorkItemStatus.IN_PROGRESS, WorkItemStatus.BLOCKED}
            or intent is None
            or not intent.is_causally_bound()
            or intent.review_context_binding != binding
            or report["issue_id"] != item.id
            or report["review_context_binding"] != binding
            or report["handoff_generation"] != intent.generation
            or report["worker"] != intent.target_worker
            or item.worker != intent.target_worker
        ):
            raise ValidationError(
                f"Stale worker decision binding; run `omac work show {issue_id} --output json` before reporting"
            )
        from ..core.manifest import _dump_contract

        contract = (
            item.contract
            if isinstance(item.contract, dict)
            else _dump_contract(item.contract)
        )
        if report["contract_ref"] not in (contract or {}):
            raise ValidationError(
                "contract_ref must name an existing top-level field in context.contract"
            )
        decision = {
            "schema": DECISION_REQUIRED_SCHEMA,
            "reason_code": "worker-decision-required",
            "kind": item.kind.value,
            "phase": TaskPhase.AUTHORING.value,
            "gate": "worker",
            "resume_issue_id": item.id,
            "review_context_binding": binding,
            "blocker": deepcopy(report),
            "next_action": f"Resolve the reported decision, then explicitly run omac node retry <manifest> {item.dag_key}",
        }
        # An exact persisted request remains idempotent after its Run finishes.
        if item.decision_required not in (None, {}, decision):
            raise NeedsDecision(
                "An existing decision must be resolved first",
                report=item.decision_required,
            )
        if item.decision_required != decision:
            from .loop import _observe_direct_run_attempt

            runs = runtime.list_runs(issue_id)
            observed = _observe_direct_run_attempt(
                runs,
                intent.target_agent_id,
                baseline_direct_run_ids=intent.baseline_direct_run_ids,
                cutoff_created_at=intent.baseline_cutoff_created_at,
                target_run_id=intent.target_run_id,
            )
            if any(
                run.kind == "direct"
                and run.active
                and run.agent_id != intent.target_agent_id
                for run in runs
            ):
                raise ValidationError(
                    "Another direct Agent Run is active; do not report for an ambiguous execution"
                )
            target = next((run for run in runs if run.id == report["run_id"]), None)
            if (
                observed.state != "active"
                or observed.target_run_id != report["run_id"]
                or target is None
                or target.kind != "direct"
                or not target.formal
                or target.status != "running"
            ):
                raise ValidationError(
                    f"Worker decision requires the current running direct Worker Run; run `omac work show {issue_id} --output json`"
                )
            # Re-read after the remote Runtime call; a new generation invalidates this request.
            current = _read_context(store, issue_id)
            if (
                current.worker_handoff != intent
                or review_context_binding(current) != binding
                or current.phase != item.phase
                or current.status != item.status
                or current.worker != item.worker
                or current.decision_required != item.decision_required
            ):
                raise ValidationError(
                    "Worker control changed while reporting; re-read work show before retrying"
                )
            store.update_work_item_metadata(issue_id, decision_required=decision)
        store.update_status(issue_id, WorkItemStatus.BLOCKED)
        current = _read_context(store, issue_id)
        if (
            current.decision_required != decision
            or current.status != WorkItemStatus.BLOCKED
        ):
            raise PlatformError(
                "Worker decision was not confirmed; retry the identical work block report"
            )
        return {
            "ok": False,
            "exit_code": 20,
            "terminal": True,
            "decision_required": decision,
            "next_action": "stop",
        }

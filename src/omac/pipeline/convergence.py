"""Single review-convergence resolution and persistence boundary."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Type

from ..core.review_convergence import (
    LegacyReviewLedgerUnverifiable,
    REVIEW_CONVERGENCE_DECISION_SCHEMA,
    REVIEW_CONVERGENCE_EARLIEST_CYCLE,
    bounded_decision_required,
    build_review_convergence_decision,
    review_convergence_decision,
    review_state as summarize_review_state,
    validate_review_ledger,
)
from ..core.taskmeta import TaskPhase, current_review_ledger
from ..engines.models import WorkItemStatus


class ResolutionState(str, Enum):
    VALID = "valid"
    NEEDS_DECISION = "needs-decision"
    INVALID = "invalid"


@dataclass(frozen=True)
class ConvergenceResolution:
    state: ResolutionState
    ledger: dict | None = None
    decision: dict | None = None
    error: str | None = None

    @property
    def convergence(self) -> dict | None:
        return self.decision.get("convergence") if self.decision else None

    @property
    def reason(self) -> str:
        return self.error or self.decision["reason_code"]

    def raise_if_invalid(self, error_type: Type[Exception], item_id: str) -> None:
        if self.state is ResolutionState.INVALID:
            raise error_type(f"Invalid review ledger for work item {item_id}: {self.error}")

    def cli_fields(self) -> dict:
        if self.state is not ResolutionState.NEEDS_DECISION:
            return {}
        return {
            "ok": False,
            "exit_code": 20,
            "reason_code": self.decision["reason_code"],
            "decision_required": self.decision,
            "next_action": self.decision["next_action"],
            "terminal": True,
        }

    def review_state(self, ledger: Any) -> dict:
        if self.state is not ResolutionState.NEEDS_DECISION:
            return summarize_review_state(ledger)
        return {
            "mode": "convergence-audit",
            "reason": self.decision["reason_code"],
            "cycle_count": self.decision["convergence"]["cycle_count"],
            "decision": self.decision["convergence"],
        }

    def apply_to_show(self, output: dict, context: dict) -> None:
        if self.state is ResolutionState.NEEDS_DECISION:
            context["decision_required"] = self.decision
            output.update(self.cli_fields())
            output["submit"] = None


def _next_action(node_id: str | None) -> str:
    command = "omac dag amend propose <manifest> --report-file <report> --docs <docs>"
    if node_id:
        command += f" --blocked-node {node_id}"
    return command + " --output json"


def resolve_convergence(
    item: Any,
    *,
    expected_round: int | None = None,
    for_next_cycle: bool = False,
    kind: str | None = None,
    node_id: str | None = None,
    recommended_action: str = "dag-amendment",
) -> ConvergenceResolution:
    ledger = current_review_ledger(item)
    cycles = ledger.get("cycles") if isinstance(ledger, dict) else None
    effective_cycles = max(
        len(cycles) + (1 if for_next_cycle and isinstance(cycles, list) else 0),
        expected_round or 0,
    ) if isinstance(cycles, list) else (expected_round or 0)
    if effective_cycles < REVIEW_CONVERGENCE_EARLIEST_CYCLE:
        return ConvergenceResolution(ResolutionState.VALID, ledger=ledger)
    try:
        validated = validate_review_ledger(ledger, expected_round=expected_round)
        convergence = review_convergence_decision(validated)
    except LegacyReviewLedgerUnverifiable as exc:
        convergence = {
            "schema": REVIEW_CONVERGENCE_DECISION_SCHEMA,
            "mode": "unverifiable-legacy-ledger",
            "reason_code": "review-convergence-ledger-unverifiable",
            "cycle_count": len(ledger["cycles"]),
        }
    except ValueError as exc:
        return ConvergenceResolution(ResolutionState.INVALID, error=str(exc))
    if convergence is None:
        return ConvergenceResolution(ResolutionState.VALID, ledger=validated)
    resolved_kind = kind or getattr(getattr(item, "kind", None), "value", "develop")
    decision = build_review_convergence_decision(
        item, convergence, kind=resolved_kind, node_id=node_id,
        recommended_action=recommended_action)
    decision["next_action"] = _next_action(node_id)
    return ConvergenceResolution(
        ResolutionState.NEEDS_DECISION, ledger=ledger, decision=decision)


def persist_decision(store, item: Any, resolution: ConvergenceResolution) -> dict:
    decision = bounded_decision_required(resolution.decision)
    if item.decision_required != decision or item.phase != TaskPhase.REVIEW:
        store.update_work_item_metadata(
            item.id, decision_required=decision, phase=TaskPhase.REVIEW)
    if item.status != WorkItemStatus.BLOCKED:
        store.update_status(item.id, WorkItemStatus.BLOCKED)
    return decision

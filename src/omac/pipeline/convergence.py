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
from ..core.taskmeta import TaskKind, TaskPhase, current_review_ledger
from ..core.workitem import WorkItemStatus
from ..errors import PlatformError
from .apply import Ports, apply
from .decide_task import plan_persist_decision


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
            raise error_type(
                f"Invalid review ledger for work item {item_id}: {self.error}. "
                "Do not renumber historical rounds. For a fresh delivery run "
                "omac node retry <manifest> <node> --stage authoring; "
                "retain the original ledger as audit evidence.")

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
    apply(plan_persist_decision(
        item.id, decision, decision_current=item.decision_required == decision and item.phase == TaskPhase.REVIEW,
        already_blocked=item.status == WorkItemStatus.BLOCKED), Ports(store=store))
    return decision


def handoff_needs_ledger(handoff_intent) -> bool:
    return max(
        handoff_intent.source_review_round or 0,
        handoff_intent.target_review_bounce or 0,
    ) >= REVIEW_CONVERGENCE_EARLIEST_CYCLE


def check_handoff_ledger_ref(item, handoff_intent) -> None:
    """A handoff that depends on the ledger must still point at the ledger it was written against."""
    source_ref = (handoff_intent.source_review_feedback or {}).get("ledger_ref")
    if handoff_needs_ledger(handoff_intent) and source_ref and item.review_ledger_ref != source_ref:
        raise PlatformError(f"Worker handoff ledger reference changed for {item.id}; inspect `omac work show {item.id} --output json`")


def handoff_ledger_round_reset(item, handoff_intent) -> bool:
    """Recovery 重置 review ledger 后，round 不一致是否为重置产物。

    恢复保留绝对 bounce 审计事实（retry 预算用），但 review ledger 经
    generation tombstone 从 round 1 重启；ledger 的 round 是位置编号
    （_validate_common_cycles 强制 round == index+1），而 handoff 的
    source_review_round 携带绝对 round。两者只在 ledger 从未重置时相等。
    当 ledger 最新 cycle 就是 handoff 记录的同一评审事件（subject digest
    相同，subject 内已烙入绝对 round 与交付身份）且位置 round 落后于
    绝对 round 时，这是重置产物而非漂移；ledger 超前于 handoff 仍是真漂移。
    """
    ledger = current_review_ledger(item)
    cycles = ledger.get("cycles") if isinstance(ledger, dict) else None
    if not isinstance(cycles, list) or not cycles:
        return False
    latest = cycles[-1]
    if not isinstance(latest, dict):
        return False
    source_round = handoff_intent.source_review_round
    latest_round = latest.get("round")
    if (
        isinstance(source_round, bool) or not isinstance(source_round, int)
        or isinstance(latest_round, bool) or not isinstance(latest_round, int)
        or latest_round >= source_round
    ):
        return False
    if latest.get("subject_digest") != handoff_intent.source_review_subject_digest:
        return False
    expected_verdict = handoff_intent.source_review_verdict
    if expected_verdict and latest.get("verdict") != expected_verdict:
        return False
    try:
        validate_review_ledger(ledger)
    except (ValueError, LegacyReviewLedgerUnverifiable):
        return False
    return True


def resolve_handoff_convergence(item, handoff_intent, node_id=None) -> ConvergenceResolution:
    """Convergence of the ledger a Worker handoff relies on; an invalid ledger raises, a reset ledger is tolerated."""
    resolution = resolve_convergence(
        item, expected_round=handoff_intent.source_review_round,
        kind=TaskKind.DEVELOP.value, node_id=node_id)
    if resolution.state is ResolutionState.INVALID and handoff_ledger_round_reset(item, handoff_intent):
        # The subject digest proves the handoff is the ledger's latest review event; the round gap is a reset artifact.
        return ConvergenceResolution(ResolutionState.VALID, ledger=current_review_ledger(item))
    resolution.raise_if_invalid(PlatformError, item.id)
    return resolution

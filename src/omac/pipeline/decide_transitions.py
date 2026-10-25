"""Named issue-level transitions: the small plans that submit, stop and rework commands and authoring tasks share.

No I/O and no platform access (enforced by tests/test_architecture.py). Each function returns the ordered effects of one
transition on one issue; callers apply them with ``pipeline.apply.apply``. The order is part of the contract: a decision
is written before the status that makes it observable, so an interrupted status write still prevents redispatch.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..core.taskmeta import TaskPhase
from ..core.workitem import WorkItemStatus
from .decide_effects import Effect, ResetReview, UpdateMetadata, UpdateStatus

# What a submitted delivery clears so the next review starts from a clean slate.
_NEW_REVIEW_SUBJECT: Mapping[str, Any] = {
    "review_verdict": "", "review_comment": "", "machine_feedback": {}, "review_subject_digest": "",
    "decision_required": {}, "review_nits_acceptance": {}, "phase": TaskPhase.REVIEW,
}


def block_with_decision(item_id: str, decision: Mapping[str, Any]) -> tuple[Effect, ...]:
    """Decision first: an interrupted status write still prevents redispatch."""
    return (UpdateMetadata(item_id, {"decision_required": decision}), UpdateStatus(item_id, WorkItemStatus.BLOCKED))


def block_in_phase(item_id: str, decision: Mapping[str, Any], phase: TaskPhase) -> tuple[Effect, ...]:
    return (UpdateMetadata(item_id, {"decision_required": decision, "phase": phase}),
            UpdateStatus(item_id, WorkItemStatus.BLOCKED))


def block_if_needed(item_id: str, current: WorkItemStatus) -> tuple[Effect, ...]:
    """Stop an issue that is not blocked yet (idempotent on restart)."""
    return () if current == WorkItemStatus.BLOCKED else (UpdateStatus(item_id, WorkItemStatus.BLOCKED),)


def advance_to_review(item_id: str, delivery: Mapping[str, Any]) -> tuple[Effect, ...]:
    """A delivery lands in review: old verdicts and decisions are cleared, the delivery fields are written."""
    return (UpdateMetadata(item_id, {**_NEW_REVIEW_SUBJECT, **delivery}),
            UpdateStatus(item_id, WorkItemStatus.IN_REVIEW))


def finish_with_delivery(item_id: str, delivery: Mapping[str, Any]) -> tuple[Effect, ...]:
    """Terminal submit (develop evidence, final acceptance results): fields, then done."""
    return (UpdateMetadata(item_id, dict(delivery)), UpdateStatus(item_id, WorkItemStatus.DONE))


def finish_clearing_decision(item_id: str) -> tuple[Effect, ...]:
    """An operator accepted: the standing decision goes first, then done."""
    return (UpdateMetadata(item_id, {"decision_required": {}}), UpdateStatus(item_id, WorkItemStatus.DONE))


def rework_to_producer(item_id: str) -> tuple[Effect, ...]:
    """Clear the current verdict and return the issue to its producer."""
    return (ResetReview(item_id), UpdateStatus(item_id, WorkItemStatus.TODO))

"""Pure decisions of the Worker stage in result collection: facts in, a named action out.

No I/O and no platform access (enforced by tests/test_architecture.py). ``loop._collect_in_progress``
and ``loop._worker_finished_without_submit`` observe, call these, then apply the action.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .decide_effects import Observation


class NoSubmit(Enum):
    REQUIRE_RETRY_INTENT = "require-retry-intent"  # no causal handoff: one explicit `node retry` is required
    BUDGET_EXHAUSTED = "budget-exhausted"          # try a late delivery, else block
    RETRY = "retry"                                # bounded return to the Worker


@dataclass(frozen=True)
class NoSubmitObservation(Observation):
    has_handoff: bool
    limit: int
    consumed: int


def decide_worker_no_submit(obs: NoSubmitObservation) -> NoSubmit:
    if not obs.has_handoff:
        return NoSubmit.REQUIRE_RETRY_INTENT
    if obs.limit == 0 or obs.consumed >= obs.limit:
        return NoSubmit.BUDGET_EXHAUSTED
    return NoSubmit.RETRY


class AfterCi(Enum):
    BOUNCE = "bounce"                  # already returned to the Worker; do not advance this tick
    BLOCK = "block"                    # CI retry budget exhausted
    MERGE_PASSED_REVIEW = "merge-passed-review"   # current delivery already has a passing verdict
    QUEUE_REVIEW = "queue-review"
    CONFIRM_MERGE = "confirm-merge"    # no reviewer configured


@dataclass(frozen=True)
class AfterCiObservation(Observation):
    ci_action: str
    review_passed: bool
    has_reviewer: bool


def decide_after_ci(obs: AfterCiObservation) -> AfterCi:
    if obs.ci_action == "bounce":
        return AfterCi.BOUNCE
    if obs.ci_action == "blocked":
        return AfterCi.BLOCK
    if obs.review_passed:
        return AfterCi.MERGE_PASSED_REVIEW
    return AfterCi.QUEUE_REVIEW if obs.has_reviewer else AfterCi.CONFIRM_MERGE

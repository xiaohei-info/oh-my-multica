"""Pure decisions of the Worker stage in result collection: facts in, a named action out.

No I/O and no platform access (enforced by tests/test_architecture.py). ``loop._collect_in_progress``
and ``loop._worker_finished_without_submit`` observe, call these, then apply the action.
"""
from __future__ import annotations

from enum import Enum


class NoSubmit(Enum):
    REQUIRE_RETRY_INTENT = "require-retry-intent"  # no causal handoff: one explicit `node retry` is required
    BUDGET_EXHAUSTED = "budget-exhausted"          # try a late delivery, else block
    RETRY = "retry"                                # bounded return to the Worker


def decide_worker_no_submit(*, has_handoff: bool, limit: int, consumed: int) -> NoSubmit:
    if not has_handoff:
        return NoSubmit.REQUIRE_RETRY_INTENT
    if limit == 0 or consumed >= limit:
        return NoSubmit.BUDGET_EXHAUSTED
    return NoSubmit.RETRY


class AfterCi(Enum):
    BOUNCE = "bounce"                  # already returned to the Worker; do not advance this tick
    BLOCK = "block"                    # CI retry budget exhausted
    MERGE_PASSED_REVIEW = "merge-passed-review"   # current delivery already has a passing verdict
    QUEUE_REVIEW = "queue-review"
    CONFIRM_MERGE = "confirm-merge"    # no reviewer configured


def decide_after_ci(*, ci_action: str, review_passed: bool, has_reviewer: bool) -> AfterCi:
    if ci_action == "bounce":
        return AfterCi.BOUNCE
    if ci_action == "blocked":
        return AfterCi.BLOCK
    if review_passed:
        return AfterCi.MERGE_PASSED_REVIEW
    return AfterCi.QUEUE_REVIEW if has_reviewer else AfterCi.CONFIRM_MERGE

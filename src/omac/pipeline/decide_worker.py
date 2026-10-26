"""Pure decisions of the Worker stage in result collection: facts in, a named action out.

No I/O and no platform access (enforced by tests/test_architecture.py). ``loop._collect_in_progress``
and ``loop._worker_finished_without_submit`` observe, call these, then apply the action.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from ..core.taskmeta import DeliveryIdentity, TaskPhase, WorkerHandoffIntent
from ..core.workitem import WorkItemStatus
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


def handoff_has_new_delivery(item, intent: WorkerHandoffIntent) -> bool:
    """Return whether control facts prove a new Worker submission exists."""
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    attachment_id = str(
        verification_ref.get("attachment_id") or "").strip()
    status_can_hold_delivery = item.status == WorkItemStatus.DONE or (
        item.status == WorkItemStatus.BLOCKED
        and item.phase == TaskPhase.AUTHORING
        and item.worker_handoff is not None
        and not item.requires_decision
        and item.worker_handoff.generation == intent.generation
    )
    if not (
        status_can_hold_delivery
        and artifacts
        and attachment_id
        and attachment_id != intent.baseline_verification_attachment_id
    ):
        return False

    baseline_head = intent.baseline_pr_head_sha
    current_head = str(artifacts.get("head_sha") or "").strip()
    if not current_head:
        return False
    if (
        intent.gate == "explicit-dispatch"
        and intent.source_review_verdict is None
        and intent.source_review_feedback is None
    ):
        # An explicit authoring dispatch has no rejected code obligation. Its
        # attachment baseline prevents reusing the old submission, not the code
        # head. Run/attachment provenance and remote HEAD are still checked by
        # _seal_worker_delivery before any candidate becomes a sealed delivery.
        return True
    if not baseline_head:
        # Legacy explicit dispatches had no rejected head to compare. A plain
        # operator retry is different: absent a captured baseline it cannot
        # prove that the new submit changed the rejected delivery. Keep the
        # pass-with-nits compatibility path, but fail closed for unknown or
        # rejected operator-retry provenance.
        if intent.gate == "operator-retry":
            return intent.source_review_verdict == "pass-with-nits"
        if (
            intent.gate == "explicit-dispatch"
            and intent.baseline_verification_attachment_id
        ):
            return intent.source_review_verdict == "pass-with-nits"
        return intent.source_review_verdict != "reject"
    if current_head != baseline_head:
        return True
    # pass-with-nits is an explicit non-blocking review path: a new verification
    # attachment may legitimately reuse the same code head.
    return intent.source_review_verdict == "pass-with-nits"


def control_matches_handoff_candidate(
    item, intent: WorkerHandoffIntent, identity: DeliveryIdentity,
) -> bool:
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    current_intent = item.worker_handoff
    same_causal_intent = bool(
        current_intent is not None
        and replace(current_intent, terminal_observed_at=None)
        == replace(intent, terminal_observed_at=None)
    )
    return bool(
        same_causal_intent
        and handoff_has_new_delivery(item, intent)
        and identity.pr_url == (artifacts.get("pr_url") or artifacts.get("pr"))
        and identity.pr_head_sha == artifacts.get("head_sha")
        and identity.verification_attachment_id
        == verification_ref.get("attachment_id")
        and identity.verification_comment_id == verification_ref.get("comment_id")
        and identity.verification_sha256 == verification_ref.get("sha256")
    )

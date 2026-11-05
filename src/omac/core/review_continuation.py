"""Explicit operator decisions that authorize one additional review round."""
from __future__ import annotations

from typing import Any

from .taskmeta import DECISION_REQUIRED_SCHEMA, TaskPhase
from .workitem import WorkItemStatus


REVIEW_CONTINUATION_SCHEMA = "omac.review-continuation/v1"
REVIEW_CONTINUATION_MODES = {"producer-rework", "review-only"}
# Before 2.0.4 a stop at review entry wrote "review" even for a delivery awaiting re-review, so both gates can name one.
REVIEW_BUDGET_GATES = frozenset({"review", "review-nits"})


def review_budget_gate(item: Any) -> str:
    """Gate of a review-budget stop: a reject awaits rework, any other stop leaves a delivery awaiting review."""
    return "review" if item.review_verdict == "reject" else "review-nits"


def review_continuation_mode(item: Any) -> str | None:
    """What `plan continue-review` can authorize for an item at its review limit; None if it stopped for another reason."""
    if item.review_verdict == "reject":
        return "producer-rework"
    if item.review_verdict or item.phase != TaskPhase.REVIEW or not item.deliverable:
        return None
    if item.status == WorkItemStatus.IN_REVIEW:
        return "review-only"
    decision = item.decision_required if isinstance(item.decision_required, dict) else {}
    stopped_on_review_budget = (
        item.status == WorkItemStatus.BLOCKED
        and decision.get("schema") == DECISION_REQUIRED_SCHEMA
        and decision.get("gate") in REVIEW_BUDGET_GATES
        and decision.get("rounds") == max(0, item.bounces.review)
    )
    return "review-only" if stopped_on_review_budget else None


def valid_review_continuation(value: Any, *, stage: str | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    limit = value.get("authorized_through_round")
    count = value.get("decision_count")
    return (
        value.get("schema") == REVIEW_CONTINUATION_SCHEMA
        and isinstance(value.get("stage"), str)
        and (stage is None or value.get("stage") == stage)
        and value.get("mode") in REVIEW_CONTINUATION_MODES
        and isinstance(limit, int)
        and not isinstance(limit, bool)
        and limit >= 0
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 1
        and isinstance(value.get("reason"), str)
        and bool(value.get("reason").strip())
    )


def authorized_review_limit(item: Any, configured_limit: int) -> int:
    """Return the monotonic absolute review limit for one work item."""
    configured = max(0, int(configured_limit))
    continuation = getattr(item, "review_continuation", None)
    stage = getattr(getattr(item, "kind", None), "value", None)
    if not valid_review_continuation(continuation, stage=stage):
        return configured
    return max(configured, int(continuation["authorized_through_round"]))


def build_review_continuation(
    item: Any,
    configured_limit: int,
    *,
    mode: str,
    reason: str,
) -> dict[str, Any]:
    if mode not in REVIEW_CONTINUATION_MODES:
        raise ValueError(f"unsupported review continuation mode: {mode}")
    existing = getattr(item, "review_continuation", None)
    stage = getattr(getattr(item, "kind", None), "value", "")
    existing_count = (
        int(existing["decision_count"])
        if valid_review_continuation(existing, stage=stage)
        else 0
    )
    current_round = max(0, int(getattr(item.bounces, "review", 0)))
    current_limit = authorized_review_limit(item, configured_limit)
    return {
        "schema": REVIEW_CONTINUATION_SCHEMA,
        "stage": stage,
        "mode": mode,
        "authorized_through_round": max(current_round, current_limit) + 1,
        "decision_count": existing_count + 1,
        "reason": reason.strip(),
    }

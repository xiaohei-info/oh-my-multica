"""Explicit operator decisions that authorize one additional review round."""
from __future__ import annotations

from typing import Any


REVIEW_CONTINUATION_SCHEMA = "omac.review-continuation/v1"
REVIEW_CONTINUATION_MODES = {"producer-rework", "review-only"}


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


def continuation_record(*, stage: str, existing: Any, mode: str, through: int, reason: str) -> dict[str, Any]:
    """The marker of one explicit authorization: the absolute round the stage may now review through."""
    if mode not in REVIEW_CONTINUATION_MODES:
        raise ValueError(f"unsupported review continuation mode: {mode}")
    count = int(existing["decision_count"]) if valid_review_continuation(existing, stage=stage) else 0
    return {
        "schema": REVIEW_CONTINUATION_SCHEMA,
        "stage": stage,
        "mode": mode,
        "authorized_through_round": through,
        "decision_count": count + 1,
        "reason": reason.strip(),
    }

"""Amendment recovery budgets over cumulative bounce audit counters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .review_continuation import authorized_review_limit


_SUPPORTED_STAGES = {"worker", "review", "merge"}


@dataclass(frozen=True)
class ReviewReworkBudget:
    """One absolute review boundary over cumulative audit counters."""

    current_round: int
    consumed: int
    authorized_through_round: int

    @property
    def allows_rework(self) -> bool:
        return self.current_round < self.authorized_through_round

    @property
    def next_round(self) -> int:
        return self.current_round + 1


def amendment_bounce_baseline(item: Any) -> dict[str, int]:
    """Capture cumulative counters without resetting their audit history."""
    return {
        stage: max(0, int(getattr(item.bounces, stage, 0)))
        for stage in sorted(_SUPPORTED_STAGES)
    }


def consumed_bounces(
    manifest: Any,
    node_id: str,
    item: Any,
    stage: str,
    *,
    absolute_count: int | None = None,
) -> int:
    """Return retries consumed since the latest amendment recovery.

    Bounce fields remain monotonic absolute audit counters. A reviewed and
    accepted amendment records a baseline in its restart-safe apply ledger;
    only budget comparison becomes relative to that baseline. Old manifests
    without a baseline retain the historical absolute semantics.
    """
    if stage not in _SUPPORTED_STAGES:
        raise ValueError(f"unsupported bounce stage: {stage}")
    current = (
        max(0, int(absolute_count))
        if absolute_count is not None
        else max(0, int(getattr(item.bounces, stage, 0)))
    )
    meta = getattr(manifest, "meta", None)
    ledger = meta.get("amendment_apply") if isinstance(meta, dict) else None
    entries = ledger.get("nodes") if isinstance(ledger, dict) else None
    entry = entries.get(node_id) if isinstance(entries, dict) else None
    baseline = entry.get("bounce_baseline") if isinstance(entry, dict) else None
    value = baseline.get(stage) if isinstance(baseline, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return current
    # A regressed counter violates cumulative audit semantics. Preserve the
    # legacy absolute behavior instead of accidentally granting free rounds.
    return current - value if current >= value else current


def review_rework_budget(
    manifest: Any,
    node_id: str,
    item: Any,
    configured_limit: int,
) -> ReviewReworkBudget:
    """Resolve amendment-relative config and absolute continuation as one limit.

    Bounce counters are absolute audit facts, while retry configuration is
    relative to the latest amendment baseline.  An operator continuation is
    already an absolute round authorization.  Converting the configured
    budget to the same absolute coordinate makes every rework verdict use one
    comparison without resetting or duplicating counters.
    """
    current = max(0, int(getattr(item.bounces, "review", 0)))
    consumed = consumed_bounces(
        manifest, node_id, item, "review", absolute_count=current)
    baseline = max(0, current - consumed)
    configured_through = baseline + max(0, int(configured_limit))
    authorized_through = authorized_review_limit(item, configured_through)
    return ReviewReworkBudget(
        current_round=current,
        consumed=consumed,
        authorized_through_round=authorized_through,
    )

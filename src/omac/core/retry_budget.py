"""Amendment recovery budgets over cumulative bounce audit counters."""
from __future__ import annotations

from typing import Any


_SUPPORTED_STAGES = {"worker", "review", "merge"}


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

"""Amendment recovery budgets over cumulative bounce audit counters."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .review_continuation import authorized_review_limit
from .taskmeta import review_context_binding


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


def projected_bounce_baseline(item: Any) -> dict[str, int] | None:
    """Return a valid WorkItem projection of the latest amendment baseline."""
    baseline = getattr(item, "bounce_baseline", None)
    if not isinstance(baseline, dict):
        return None
    projected = {}
    for stage in sorted(_SUPPORTED_STAGES):
        value = baseline.get(stage)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        projected[stage] = value
    return projected


def projected_consumed_bounces(
    item: Any,
    stage: str,
    *,
    absolute_count: int | None = None,
) -> int:
    """Calculate current-generation consumption from the WorkItem projection."""
    if stage not in _SUPPORTED_STAGES:
        raise ValueError(f"unsupported bounce stage: {stage}")
    current = (
        max(0, int(absolute_count))
        if absolute_count is not None
        else max(0, int(getattr(item.bounces, stage, 0)))
    )
    baseline = projected_bounce_baseline(item)
    if baseline is None or current < baseline[stage]:
        return current
    return current - baseline[stage]


def bounce_budget_projection(item: Any) -> dict[str, Any] | None:
    """Describe absolute audit counters and relative amendment consumption."""
    baseline = projected_bounce_baseline(item)
    if baseline is None:
        return None
    absolute = amendment_bounce_baseline(item)
    return {
        "counter_semantics": "absolute-audit",
        "absolute": absolute,
        "current_generation": {
            "baseline": baseline,
            "consumed": {
                stage: projected_consumed_bounces(item, stage)
                for stage in sorted(_SUPPORTED_STAGES)
            },
        },
    }


def bounce_log_fields(
    item: Any,
    stage: str,
    *,
    absolute_count: int,
    limit: int,
    manifest: Any = None,
    node_id: str | None = None,
) -> dict[str, int]:
    """Return explicit absolute and current-generation retry log fields."""
    return {
        "absolute_audit_round": max(0, int(absolute_count)),
        "current_generation_consumed": (
            consumed_bounces(manifest, node_id, item, stage, absolute_count=absolute_count)
            if manifest is not None and node_id is not None
            else projected_consumed_bounces(item, stage, absolute_count=absolute_count)
        ),
        "current_generation_limit": max(0, int(limit)),
    }



def _retained_bounce_baseline(manifest: Any, node_id: str, record: Any) -> dict | None:
    """Read a completed amendment authorization, never a Store projection."""
    node = getattr(manifest, "nodes", {}).get(node_id)
    if not isinstance(record, dict) or node is None or not node.work_item_id:
        return None
    if (
        not isinstance(record.get("amendment_id"), str) or not record["amendment_id"]
        or record.get("work_item_id") != node.work_item_id
        or record.get("contract_sha256") != review_context_binding(node)["contract_sha256"]
    ):
        return None
    baseline = record.get("bounce_baseline")
    if not isinstance(baseline, dict) or any(
        type(baseline.get(stage)) is not int or baseline[stage] < 0
        for stage in _SUPPORTED_STAGES
    ):
        return None
    return baseline


def carry_forward_bounce_baselines(manifest: Any) -> dict:
    """Keep per-node budget authority separate from the next apply work queue."""
    meta = getattr(manifest, "meta", {})
    ledger = meta.get("amendment_apply")
    if not isinstance(ledger, dict) or (
        ledger.get("schema") != "omac.amendment-apply/v1"
        or not isinstance(ledger.get("amendment_id"), str)
        or not ledger["amendment_id"]
        or ledger["amendment_id"] != meta.get("last_amendment_id")
    ):
        return {}
    previous = ledger.get("retained_bounce_baselines", {})
    retained = {
        key: deepcopy(record) for key, record in previous.items()
        if _retained_bounce_baseline(manifest, key, record) is not None
    } if isinstance(previous, dict) else {}
    for key, entry in ledger.get("nodes", {}).items():
        # A newer recovery supersedes earlier authority even if it is incomplete.
        retained.pop(key, None)
        node = manifest.nodes.get(key)
        if not isinstance(entry, dict) or node is None or (
            entry.get("state") not in {"synced", "observed_progress"}
            or entry.get("stage") not in {"authoring", "review", "merging"}
        ):
            continue
        record = {
            "amendment_id": ledger["amendment_id"],
            "work_item_id": entry.get("work_item_id", node.work_item_id),
            "contract_sha256": entry.get("expected_contract_sha256"),
            "bounce_baseline": entry.get("bounce_baseline"),
        }
        if _retained_bounce_baseline(manifest, key, record) is not None:
            retained[key] = deepcopy(record)
    return retained

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
    only budget comparison becomes relative to that baseline. Completed per-node
    authorizations are retained outside later amendments' active apply queue.
    Old manifests without either baseline retain absolute semantics.
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
    if isinstance(entry, dict) and entry.get("work_item_id", getattr(item, "id", None)) != getattr(item, "id", None):
        return current
    if entry is None and isinstance(ledger, dict) and (
        ledger.get("schema") == "omac.amendment-apply/v1"
        and isinstance(ledger.get("amendment_id"), str)
        and bool(ledger["amendment_id"])
        and ledger["amendment_id"] == meta.get("last_amendment_id")
    ):
        retained = ledger.get("retained_bounce_baselines", {})
        record = retained.get(node_id) if isinstance(retained, dict) else None
        if isinstance(record, dict) and record.get("work_item_id") == getattr(item, "id", None):
            baseline = _retained_bounce_baseline(manifest, node_id, record)
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

"""Persisted scheduling for periodic full reconcile audits."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..core.manifest import Manifest


META_KEY = "reconcile_audit"
SCHEMA = "omac.reconcile-audit/v1"


@dataclass(frozen=True)
class AuditState:
    last_full_scan_at: datetime
    completed_interval_ticks: int


def should_full_scan(
    manifest: Manifest,
    config: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether this runner tick must reconcile every WorkItem.

    Missing or malformed state is deliberately treated as unknown and forces a
    full scan. This makes old manifests safe and prevents an uncertain state
    write from silently delaying an audit.
    """
    current = _utc_now(now)
    state = _load_state(manifest.meta.get(META_KEY))
    if state is None or state.last_full_scan_at > current:
        return True
    if state.completed_interval_ticks >= config["full_scan_interval_ticks"]:
        return True
    age = (current - state.last_full_scan_at).total_seconds()
    return age >= config["full_scan_max_age_seconds"]


def record_successful_tick(
    manifest: Manifest,
    *,
    full_scan: bool,
    now: datetime | None = None,
) -> None:
    """Record a completed runner tick after every tick phase has succeeded."""
    current = _utc_now(now)
    if full_scan:
        manifest.meta[META_KEY] = {
            "schema": SCHEMA,
            "last_full_scan_at": _format_utc(current),
            "completed_interval_ticks": 0,
        }
        return

    state = _load_state(manifest.meta.get(META_KEY))
    if state is None:
        # The caller should have selected a full scan. Retain the bad state so
        # a restart fails closed and schedules one instead of inventing facts.
        return
    manifest.meta[META_KEY] = {
        "schema": SCHEMA,
        "last_full_scan_at": _format_utc(state.last_full_scan_at),
        "completed_interval_ticks": state.completed_interval_ticks + 1,
    }


def _load_state(raw: Any) -> AuditState | None:
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    timestamp = raw.get("last_full_scan_at")
    completed = raw.get("completed_interval_ticks")
    if (
        not isinstance(timestamp, str)
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed < 0
    ):
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return AuditState(
        last_full_scan_at=parsed.astimezone(timezone.utc),
        completed_interval_ticks=completed,
    )


def _utc_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("reconcile audit clock must be timezone-aware")
    return current.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")

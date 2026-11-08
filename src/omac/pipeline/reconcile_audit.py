"""Scheduling for periodic full reconcile audits.

The schedule (last full scan, ticks since) is volatile operational data: it lives in a host-local runtime file,
never in git, so a tick that changes nothing else commits nothing. A lost file only forces one early full scan.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..core.manifest import read_local_runtime, write_local_runtime


RUNTIME_NAME = "reconcile-audit"
SCHEMA = "omac.reconcile-audit/v1"


@dataclass(frozen=True)
class AuditState:
    last_full_scan_at: datetime
    completed_interval_ticks: int


def should_full_scan(
    manifest_path: str,
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
    state = _load_state(read_local_runtime(manifest_path, RUNTIME_NAME))
    if state is None or state.last_full_scan_at > current:
        return True
    if state.completed_interval_ticks >= config["full_scan_interval_ticks"]:
        return True
    age = (current - state.last_full_scan_at).total_seconds()
    return age >= config["full_scan_max_age_seconds"]


def record_successful_tick(
    manifest_path: str,
    *,
    full_scan: bool,
    now: datetime | None = None,
) -> None:
    """Record a completed runner tick after every tick phase has succeeded."""
    current = _utc_now(now)
    if full_scan:
        _record(manifest_path, _format_utc(current), 0)
        return

    state = _load_state(read_local_runtime(manifest_path, RUNTIME_NAME))
    if state is None:
        # The caller should have selected a full scan. Retain the bad state so
        # a restart fails closed and schedules one instead of inventing facts.
        return
    _record(manifest_path, _format_utc(state.last_full_scan_at), state.completed_interval_ticks + 1)


def _record(manifest_path: str, last_full_scan_at: str, completed_interval_ticks: int) -> None:
    fact = {"schema": SCHEMA, "last_full_scan_at": last_full_scan_at, "completed_interval_ticks": completed_interval_ticks}
    write_local_runtime(manifest_path, RUNTIME_NAME, fact)


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

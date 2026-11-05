"""Per-item handling of an optimistic-read conflict (`ObservationConflict`).

A conflict means a Worker/Reviewer wrote the work item while the Loop was reading it. The item is skipped for this
tick and observed again on the next; the rest of the tick is unaffected. Streaks live in memory on the manifest object
(shared by every tick of one `dag run`), so a conflict that never clears is logged at warn level once rather than
silently repeating. Stopping the node would need a persisted state and a release command; the log is the only escalation.
"""
from __future__ import annotations

from ..core import logsetup
from ..errors import ObservationConflict

log = logsetup.get_logger(__name__)

# shortcut: fixed warn threshold, no config key; make it configurable if operators need to tune it.
CONFLICT_STREAK_WARN = 5


class ConflictStreaks:
    """Consecutive ticks in which each node was skipped because of an observation conflict."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self._seen: set[str] = set()

    def skip(self, key: str, item_id: str | None, where: str, exc: ObservationConflict) -> None:
        if key not in self._seen:
            self._seen.add(key)
            self.counts[key] = self.counts.get(key, 0) + 1
        streak = self.counts[key]
        emit = log.warning if streak == CONFLICT_STREAK_WARN else log.info
        emit(logsetup.EVT_OBSERVATION_CONFLICT, kind="develop", node=key, id=item_id, where=where,
             streak=streak, reason=str(exc))

    def end_tick(self) -> None:
        """Forget nodes that did not conflict this tick."""
        self.counts = {k: v for k, v in self.counts.items() if k in self._seen}
        self._seen.clear()


def conflict_streaks(manifest) -> ConflictStreaks:
    streaks = getattr(manifest, "_conflict_streaks", None)
    if streaks is None:
        streaks = ConflictStreaks()
        manifest._conflict_streaks = streaks
    return streaks

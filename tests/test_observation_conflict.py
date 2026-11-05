"""A conflict that never clears is escalated once, in the log; a clean tick ends the streak."""
import json

from omac.core import logsetup
from omac.errors import ObservationConflict
from omac.pipeline.observation_conflict import CONFLICT_STREAK_WARN, ConflictStreaks


def _events(capsys):
    return [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]


def _tick(streaks, key="n1"):
    streaks.skip(key, "item-1", "collect", ObservationConflict("changed"))
    streaks.end_tick()


def test_streak_warns_once_at_the_threshold(capsys):
    logsetup.configure_logging("json")
    streaks = ConflictStreaks()

    for _ in range(CONFLICT_STREAK_WARN + 2):
        _tick(streaks)

    events = _events(capsys)
    assert [e["level"] for e in events].count("warning") == 1
    warned = next(e for e in events if e["level"] == "warning")
    assert warned["streak"] == CONFLICT_STREAK_WARN and warned["id"] == "item-1"


def test_clean_tick_resets_the_streak(capsys):
    logsetup.configure_logging("json")
    streaks = ConflictStreaks()

    for _ in range(CONFLICT_STREAK_WARN - 1):
        _tick(streaks)
    streaks.end_tick()                      # a tick in which the node did not conflict
    for _ in range(CONFLICT_STREAK_WARN - 1):
        _tick(streaks)

    assert "warning" not in [e["level"] for e in _events(capsys)]


def test_same_node_counts_once_per_tick(capsys):
    streaks = ConflictStreaks()
    streaks.skip("n1", "item-1", "restore", ObservationConflict("changed"))
    streaks.skip("n1", "item-1", "review-dispatch", ObservationConflict("changed"))
    assert streaks.counts == {"n1": 1}

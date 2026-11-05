"""The one decision table of node hold / release."""
import pytest

from omac.pipeline.decide_effects import SaveState, SetNode
from omac.pipeline.decide_hold import HoldRefused, plan_hold, plan_release


def test_holding_a_todo_node_sets_the_flag_and_reason_then_saves():
    plan = plan_hold("a", "todo", "needs the test account")

    assert plan.effects == (SetNode("a", {"held": True, "hold_reason": "needs the test account"}), SaveState())


def test_a_hold_without_a_reason_records_none():
    assert plan_hold("a", "todo", "").effects[0] == SetNode("a", {"held": True, "hold_reason": None})


@pytest.mark.parametrize("status", ["in_progress", "ci_check", "in_review", "merging"])
def test_a_node_in_progress_cannot_be_held(status):
    with pytest.raises(HoldRefused, match="in progress cannot be held"):
        plan_hold("a", status, None)


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_a_node_waiting_for_a_decision_is_sent_to_node_retry(status):
    with pytest.raises(HoldRefused, match="omac node retry"):
        plan_hold("a", status, None)


@pytest.mark.parametrize("status", ["done", "abandoned"])
def test_a_closed_node_has_nothing_to_hold(status):
    with pytest.raises(HoldRefused, match="nothing left to hold"):
        plan_hold("a", status, None)


def test_release_clears_the_flag_and_the_reason_then_saves():
    assert plan_release("a", True).effects == (SetNode("a", {"held": False, "hold_reason": None}), SaveState())


def test_releasing_a_node_that_is_not_held_is_refused():
    with pytest.raises(HoldRefused, match="not held"):
        plan_release("a", False)

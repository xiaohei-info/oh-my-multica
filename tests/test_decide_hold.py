"""The one decision table of node hold / release."""
import pytest

from omac.pipeline.decide_effects import SaveState, SetNode
from omac.core.manifest import Contract, EvidenceMode, Node
from omac.pipeline.decide_effects import SetDagFact
from omac.pipeline.decide_hold import AUTO_HELD_KEY, HoldRefused, plan_auto_hold_live, plan_hold, plan_release


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


def nodes():
    return {
        "live": Node(id="live", worker="alice", contract=Contract(evidence_mode=EvidenceMode.LIVE)),
        "fixture": Node(id="fixture", worker="alice", contract=Contract(evidence_mode=EvidenceMode.FIXTURE)),
        "plain": Node(id="plain", worker="alice"),
    }


def test_s17_a_ready_live_node_is_held_by_the_code_and_remembered():
    plan = plan_auto_hold_live(["plain", "live", "fixture"], nodes(), [])

    node_effects = [effect for effect in plan.effects if isinstance(effect, SetNode)]
    assert [(e.node_key, e.fields["held"]) for e in node_effects] == [("live", True)]
    assert "live" in node_effects[0].fields["hold_reason"] and "dispatch.hold_live" in node_effects[0].fields["hold_reason"]
    assert plan.effects[-2:] == (SetDagFact(AUTO_HELD_KEY, ["live"]), SaveState())


def test_s17_a_live_node_that_was_released_is_not_held_again_and_nothing_to_hold_is_no_plan():
    assert plan_auto_hold_live(["live"], nodes(), ["live"]) is None
    assert plan_auto_hold_live(["plain", "fixture"], nodes(), []) is None
    assert plan_auto_hold_live([], nodes(), []) is None

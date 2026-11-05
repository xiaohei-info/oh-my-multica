"""What a review-budget stop records and what `plan continue-review` reads from it: pure, no platform.

The full round on a real Multica (stop, continue-review, plan resume dispatches the Reviewer) is in tests/it/test_it_tasks_review.py.
"""
from dataclasses import replace

import pytest

from _support.workitems import make_item
from omac.core.review_continuation import review_budget_gate, review_continuation_mode
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.pipeline.tasks import _budget_exhausted_decision


def at_limit(**fields):
    item = make_item(TaskKind.ACCEPTANCE, TaskPhase.REVIEW, dag_key="acceptance-p-1", with_deliverable=True,
                     bounces=Bounces(review=3), status=WorkItemStatus.IN_REVIEW)
    return replace(item, **fields)


def stopped(item, gate=None):
    decision = _budget_exhausted_decision(TaskKind.ACCEPTANCE, item, gate=gate or review_budget_gate(item), rounds=3)
    return replace(item, status=WorkItemStatus.BLOCKED, decision_required=decision)


@pytest.mark.parametrize("verdict,gate,mode", [
    ("reject", "review", "producer-rework"),
    (None, "review-nits", "review-only"),
    ("", "review-nits", "review-only"),
])
def test_the_command_a_review_budget_stop_names_accepts_that_stop(verdict, gate, mode):
    blocked = stopped(at_limit(review_verdict=verdict))
    assert blocked.decision_required["gate"] == gate
    assert blocked.decision_required["next_action"] == "omac plan continue-review --dag-key acceptance-p-1"
    assert review_continuation_mode(blocked) == mode


def test_a_final_delivery_an_older_version_recorded_under_the_review_gate_is_still_released():
    assert review_continuation_mode(stopped(at_limit(review_verdict=""), gate="review")) == "review-only"


def test_a_machine_gate_stop_is_not_released_and_does_not_name_continue_review():
    blocked = stopped(at_limit(review_verdict=""), gate="guard")
    assert "next_action" not in blocked.decision_required
    assert review_continuation_mode(blocked) is None


@pytest.mark.parametrize("change", [
    {"decision_required": None},
    {"decision_required": {"schema": "omac.decision-required/v1", "gate": "review-convergence", "rounds": 3}},
    {"decision_required": {"schema": "omac.decision-required/v1", "gate": "review-nits", "rounds": 2}},
    {"decision_required": {"schema": "other/v1", "gate": "review-nits", "rounds": 3}},
    {"status": WorkItemStatus.TODO},
    {"phase": TaskPhase.AUTHORING},
    {"deliverable": None},
    {"review_verdict": "pass-with-nits"},
])
def test_any_other_stop_is_refused(change):
    assert review_continuation_mode(replace(stopped(at_limit(review_verdict="")), **change)) is None


def test_a_delivery_still_in_review_needs_no_recorded_stop():
    assert review_continuation_mode(at_limit(review_verdict="")) == "review-only"

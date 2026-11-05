"""Releasing a review stop with `plan continue-review` / `plan accept-nits`: pure decisions over facts, no platform.

The full rounds on a real Multica (stop, release, plan resume dispatches the Reviewer) are in tests/it.
"""
from dataclasses import replace

import pytest

from omac.core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskPhase
from omac.core.workitem import WorkItemStatus as S
from omac.pipeline.decide_effects import UpdateMetadata, UpdateStatus
from omac.pipeline.decide_stop import Refusal, Release, ReleaseFacts, decide_release


def decision(reason, gate="review", rounds=3):
    return {"schema": DECISION_REQUIRED_SCHEMA, "reason_code": reason, "gate": gate, "rounds": rounds}


def at_limit(**fields):
    base = ReleaseFacts(item_id="i", status=S.IN_REVIEW, phase=TaskPhase.REVIEW, verdict="", has_deliverable=True,
                        decision=None, bounce=3, limit=3, continuation=None)
    return replace(base, **fields)


def release(facts, which=Release.CONTINUE_REVIEW):
    return decide_release(which, facts, stage="acceptance", reason="because")


def continuation_of(plan):
    return next(e.fields["review_continuation"] for e in plan.effects
                if isinstance(e, UpdateMetadata) and "review_continuation" in e.fields)


@pytest.mark.parametrize("facts,mode,through", [
    (at_limit(verdict="reject"), "producer-rework", 4),
    (at_limit(status=S.BLOCKED, verdict="reject", decision=decision("review-budget-exhausted")), "producer-rework", 4),
    (at_limit(verdict=""), "review-only", 4),
    # a stop an older version recorded for a delivery that never got its verdict, under either gate
    (at_limit(status=S.BLOCKED, decision=decision("review-nits-budget-exhausted", "review-nits")), "review-only", 4),
    (at_limit(status=S.BLOCKED, decision=decision("review-budget-exhausted", "review")), "review-only", 4),
])
def test_a_review_budget_stop_authorizes_one_more_round_in_the_mode_its_state_asks_for(facts, mode, through):
    plan = release(facts)

    assert plan.outcome == mode and continuation_of(plan)["authorized_through_round"] == through
    assert continuation_of(plan)["mode"] == mode and continuation_of(plan)["decision_count"] == 1


def test_a_review_only_release_of_a_blocked_stop_clears_the_decision_and_returns_to_review():
    plan = release(at_limit(status=S.BLOCKED, decision=decision("review-nits-budget-exhausted", "review-nits")))

    assert plan.effects[-2:] == (UpdateMetadata("i", {"decision_required": {}}), UpdateStatus("i", S.IN_REVIEW))


def test_a_convergence_stop_is_released_by_a_round_the_next_convergence_check_cannot_veto():
    stop = at_limit(status=S.BLOCKED, verdict="reject", bounce=2, decision=decision("review-convergence-stalled", "review-convergence", 0))

    assert continuation_of(release(stop))["authorized_through_round"] == 4 and release(stop).outcome == "producer-rework"
    assert continuation_of(release(replace(stop, bounce=0, limit=8)))["authorized_through_round"] == 1


@pytest.mark.parametrize("bounce,limit,counted_through", [(0, 3, None), (2, 3, 4)])
def test_declining_the_nits_of_a_stop_is_a_counted_return_to_the_author(bounce, limit, counted_through):
    stop = at_limit(status=S.BLOCKED, verdict="pass-with-nits", bounce=bounce, limit=limit,
                    decision=decision("review-nits-acceptance-required", "review-nits", bounce + 1))

    plan = release(stop)

    assert UpdateMetadata("i", {"review_bounce": bounce + 1}) in plan.effects and plan.outcome == "producer-rework"
    assert (continuation_of(plan)["authorized_through_round"] if counted_through else None) == counted_through
    assert plan.effects[-1] == UpdateStatus("i", S.TODO)


def test_a_second_decision_is_not_stacked_on_a_round_that_is_still_authorized():
    refusal = release(at_limit(verdict="reject", bounce=2, limit=3))

    assert isinstance(refusal, Refusal) and "plan resume" in refusal.message


def test_the_second_authorization_counts_on_the_first():
    first = {"schema": "omac.review-continuation/v1", "stage": "acceptance", "mode": "review-only",
             "authorized_through_round": 4, "decision_count": 1, "reason": "x"}

    assert continuation_of(release(at_limit(verdict="reject", bounce=4, limit=4, continuation=first)))["decision_count"] == 2


@pytest.mark.parametrize("change", [
    {"verdict": "pass"},
    {"verdict": "pass-with-nits"},
    {"status": S.TODO, "phase": TaskPhase.AUTHORING},
    {"has_deliverable": False},
    {"status": S.BLOCKED, "decision": decision("review-nits-budget-exhausted", "review-nits", 2)},
    {"status": S.BLOCKED, "decision": decision("guard-budget-exhausted", "guard")},
    {"status": S.BLOCKED, "decision": None},
])
def test_any_other_state_is_refused(change):
    assert isinstance(release(at_limit(**change)), Refusal)


def test_a_release_never_acts_while_an_agent_is_working_on_the_issue():
    assert isinstance(release(at_limit(verdict="reject", active_runs=("run-1",))), Refusal)


@pytest.mark.parametrize("change", [{}, {"status": S.BLOCKED}])
def test_nits_are_accepted_only_for_a_verdict_bound_to_the_current_delivery(change):
    facts = at_limit(verdict="pass-with-nits", delivery_bound=True, **change)

    assert release(facts, Release.ACCEPT_NITS).outcome == "accept-nits"
    assert isinstance(release(replace(facts, delivery_bound=False), Release.ACCEPT_NITS), Refusal)
    assert isinstance(release(replace(facts, verdict=""), Release.ACCEPT_NITS), Refusal)

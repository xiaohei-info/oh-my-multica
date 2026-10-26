"""Plans of the CI gate and the merge state machine: facts in, ordered effects out. No platform, no manifest."""
import pytest

from omac.core.workitem import PullRequestState as PR, WorkItemStatus as S
from omac.pipeline.decide_ci import plan_ci_result, plan_ci_start
from omac.pipeline.decide_effects import (
    AddComment, Assign, ResetReview, SaveState, SetNode, UpdateMetadata, UpdateStatus, Wake, validate_plan,
)
from omac.pipeline.decide_merge import (
    MergeStep, block_no_pull_request, block_unproven, plan_bounce_pending, plan_observed_step,
    plan_finish_bounce, plan_request_intent, plan_request_sent, plan_resume_bounce, plan_settle,
)


def kinds(plan):
    return [type(e).__name__ for e in plan.effects]


def test_ci_start_without_pr_blocks_with_a_teaching_comment():
    plan = plan_ci_start(node_key="a", item_id="i", pr_url="")
    assert plan.outcome == "blocked" and kinds(plan) == ["AddComment", "SetNode", "UpdateStatus"]
    assert "omac work submit" in plan.effects[0].text and plan.effects[2].status is S.BLOCKED


def test_ci_start_with_pr_enters_ci_check_and_keeps_platform_in_progress():
    plan = plan_ci_start(node_key="a", item_id="i", pr_url="u")
    assert plan.outcome is None
    assert plan.effects == (SetNode("a", {"status": "ci_check"}), UpdateStatus("i", S.IN_PROGRESS))


CI = dict(node_key="a", item_id="i", worker="w", label="CI check failed (exit code 1)", summary="boom")


@pytest.mark.parametrize("passed,bounces,limit,outcome,kind_list", [
    (True, 0, 3, "pass", ["SetNode", "UpdateStatus"]),
    (False, 0, 3, "bounce", ["AddComment", "UpdateMetadata", "SetNode", "UpdateStatus"]),
    (False, 1, 3, "bounce", ["AddComment", "UpdateMetadata", "SetNode", "UpdateStatus"]),
    (False, 2, 3, "blocked", ["AddComment", "UpdateMetadata", "SetNode", "UpdateStatus"]),
    (False, 0, 0, "blocked", ["AddComment", "UpdateMetadata", "SetNode", "UpdateStatus"]),
])
def test_ci_result(passed, bounces, limit, outcome, kind_list):
    plan = plan_ci_result(passed=passed, bounces=bounces, limit=limit, **CI)
    assert plan.outcome == outcome and kinds(plan) == kind_list
    if not passed:
        assert plan.effects[1] == UpdateMetadata("i", {"ci_bounce": bounces + 1})
        assert "boom" in plan.effects[0].text


def test_merge_request_intent_is_durable_before_the_request():
    plan = plan_request_intent("a", "i")
    assert plan.effects == (
        SetNode("a", {"status": "merging", "merge_request_state": "intent"}), SaveState(),
        UpdateStatus("i", S.IN_REVIEW))
    from omac.pipeline.decide_effects import RequestMerge
    validate_plan(plan.effects + (RequestMerge("u", "c", 1),))


@pytest.mark.parametrize("timed_out,expected", [
    (False, (SetNode("a", {"merge_request_state": "requested"}), SaveState())),
    (True, ()),  # outcome unknown: the intent marker stays, the next tick only observes
])
def test_request_sent(timed_out, expected):
    assert plan_request_sent("a", timed_out=timed_out).effects == expected


@pytest.mark.parametrize("step,outcome,first", [
    (MergeStep.RECORD_PENDING, "pending", SetNode("a", {"status": "merging", "merge_request_state": "requested"})),
    (MergeStep.STAY_PENDING, "pending", SetNode("a", {"status": "merging"})),
    (MergeStep.BLOCK_PRIOR_INTENT, "blocked", SetNode("a", {"status": "blocked"})),
])
def test_observed_steps(step, outcome, first):
    plan = plan_observed_step(step, node_key="a", item_id="i", manifest_path="m.yaml")
    assert plan.outcome == outcome and plan.effects[0] == first and isinstance(plan.effects[1], SaveState)


def test_request_step_has_no_standalone_plan():
    with pytest.raises(ValueError):
        plan_observed_step(MergeStep.REQUEST, node_key="a", item_id="i", manifest_path=None)


@pytest.mark.parametrize("state,merged_at,outcome,closed,effects_kinds", [
    (PR.MERGED, "2026-01-01", "pass", False, ["SetNode"]),
    (PR.MERGED, None, "pending", False, ["SetNode", "SaveState", "UpdateStatus"]),  # no timestamp: not proof
    (PR.PENDING, None, "pending", False, ["SetNode"]),
    (PR.CLOSED_UNMERGED, None, None, True, ["SetNode"]),
    (PR.UNKNOWN, None, "pending", False, ["SetNode", "SaveState", "UpdateStatus"]),
    (PR.OPEN, None, "pending", False, ["SetNode", "SaveState", "UpdateStatus"]),
])
def test_settle(state, merged_at, outcome, closed, effects_kinds):
    plan, bounce = plan_settle(state, merged_at=merged_at, node_key="a", item_id="i")
    assert (plan.outcome, bounce, kinds(plan)) == (outcome, closed, effects_kinds)


def test_merged_settle_records_the_merge_and_clears_the_marker():
    plan, _ = plan_settle(PR.MERGED, merged_at="t", node_key="a", item_id="i")
    assert plan.effects[0].fields == {"merged": True, "merged_at": "t", "merge_request_state": None}


def test_block_helpers_save_before_remote_writes():
    for plan in (block_no_pull_request("a", "i"), block_unproven("a", "i", "m.yaml", "why")):
        assert kinds(plan) == ["SetNode", "SaveState", "AddComment", "UpdateStatus"] and plan.outcome == "blocked"
    assert "omac node retry m.yaml a" in block_unproven("a", "i", "m.yaml", "why").effects[2].text
    assert kinds(block_unproven("a", "i", None, "why", save=False)) == ["SetNode", "AddComment", "UpdateStatus"]


def test_bounce_pending_marker_carries_the_absolute_attempt():
    plan = plan_bounce_pending("a", 3)
    assert plan.effects[0].fields["merge_request_state"] == "bounce_pending:3" and isinstance(plan.effects[1], SaveState)


RESUME = dict(node_key="a", item_id="i", worker="w", label="merge failed", output="out", merge_limit=3)


@pytest.mark.parametrize("attempt,current,consumed,limit,outcome,kind_list", [
    (1, 0, 1, 3, "bounce", ["AddComment", "UpdateMetadata", "UpdateStatus", "ResetReview"]),
    (1, 1, 1, 3, "bounce", ["UpdateStatus", "ResetReview"]),  # comment already done
    (3, 2, 3, 3, "blocked", ["AddComment", "UpdateMetadata", "UpdateStatus", "SetNode", "SaveState"]),
    (1, 0, 0, 0, "blocked", ["AddComment", "UpdateMetadata", "UpdateStatus", "SetNode", "SaveState"]),
    (1, 2, 1, 3, "blocked", ["SetNode", "SaveState", "AddComment", "UpdateStatus"]),  # platform is ahead: refuse
])
def test_resume_bounce(attempt, current, consumed, limit, outcome, kind_list):
    plan = plan_resume_bounce(attempt=attempt, current=current, consumed_attempt=consumed,
                              **{**RESUME, "merge_limit": limit})
    assert plan.outcome == outcome and kinds(plan) == kind_list


def test_a_bounce_keeps_its_durable_marker_until_the_worker_handoff_is_persisted():
    plan = plan_resume_bounce(attempt=1, current=1, consumed_attempt=1, **RESUME)
    assert plan.effects == (UpdateStatus("i", S.IN_PROGRESS), ResetReview("i"))      # the marker is untouched
    assert plan_finish_bounce("a").effects == (SetNode("a", {"status": "in_progress", "merge_request_state": None}), SaveState())

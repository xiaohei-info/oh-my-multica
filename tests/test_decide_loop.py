"""Plans of result collection and reconcile: observed facts in, ordered effects out."""
import pytest

from omac.core.taskmeta import TaskPhase
from omac.core.workitem import PullRequestState as PR, WorkItemStatus as S
from omac.pipeline.decide_effects import AddComment, ResetReview, SaveState, SetNode, UpdateMetadata, UpdateStatus
from omac.pipeline.decide_loop import (
    DoneDevelopObservation, decide_done_develop, decide_tick_state, plan_block, plan_done_non_develop, plan_evidence_gate_failed,
    plan_return_to_worker_failed, plan_worker_failed,
)

BLOCKED = SetNode("a", {"status": "blocked"})
DECISION = {"reason_code": "x"}


def names(plan):
    return [type(e).__name__ for e in plan.effects]


@pytest.mark.parametrize("kwargs,expected", [
    ({}, [UpdateStatus("i", S.BLOCKED), BLOCKED]),
    ({"write_status": False}, [BLOCKED]),
    ({"decision": DECISION}, [UpdateMetadata("i", {"decision_required": DECISION}), UpdateStatus("i", S.BLOCKED), BLOCKED]),
    ({"decision": DECISION, "write_decision": False}, [UpdateStatus("i", S.BLOCKED), BLOCKED]),
    ({"decision": DECISION, "phase": TaskPhase.REVIEW},
     [UpdateMetadata("i", {"decision_required": DECISION, "phase": TaskPhase.REVIEW}), UpdateStatus("i", S.BLOCKED), BLOCKED]),
    ({"comment": "c"}, [UpdateStatus("i", S.BLOCKED), AddComment("i", "c"), BLOCKED]),
    ({"comment": "c", "comment_before_status": True}, [AddComment("i", "c"), UpdateStatus("i", S.BLOCKED), BLOCKED]),
    ({"mark": True}, [SetNode("a", {"recovery_marker": True}), UpdateStatus("i", S.BLOCKED), BLOCKED]),
    ({"mark": True, "persist_marker": True},
     [SetNode("a", {"recovery_marker": True}), SaveState(), UpdateStatus("i", S.BLOCKED), BLOCKED]),
    ({"blocked_first": True, "mark": True, "write_status": False}, [BLOCKED, SetNode("a", {"recovery_marker": True})]),
])
def test_plan_block_orders_marker_decision_status_comment_and_node(kwargs, expected):
    plan = plan_block("a", "i", **kwargs)
    assert plan.effects == tuple(expected) and plan.outcome == "blocked"


def test_named_block_plans():
    assert plan_worker_failed("a", "i").effects[1] == AddComment("i", "Worker execution failed")
    assert "Evidence gate failed: why" in plan_evidence_gate_failed("a", "i", "why").effects[1].text
    rolled = plan_return_to_worker_failed("a", "i", "w", 2, "boom")
    assert rolled.effects[0] == UpdateMetadata("i", {"worker_bounce": 2}) and rolled.effects[-1] == BLOCKED
    assert "boom" in rolled.effects[2].text


def done(**kw):
    base = dict(node_key="a", item_id="i", item_status=S.DONE, has_pull_request=True, request_state=None,
                request_state_valid=True, merge_closed=False, pr_state=PR.OPEN)
    base.update(kw)
    return DoneDevelopObservation(**base)


@pytest.mark.parametrize("obs,outcome,kind_list", [
    (done(has_pull_request=False), "changed", ["UpdateStatus", "AddComment", "SetNode"]),
    (done(request_state="junk", request_state_valid=False), "changed", ["SetNode", "AddComment", "UpdateStatus"]),
    (done(merge_closed=True), "unchanged", []),
    (done(merge_closed=True, item_status=S.IN_REVIEW), "unchanged", ["UpdateStatus"]),
    (done(pr_state=PR.MERGED, merged_at="t"), "changed", ["SetNode"]),
    (done(pr_state=PR.MERGED, merged_at="t", item_status=S.IN_REVIEW), "changed", ["SetNode", "UpdateStatus"]),
    (done(pr_state=PR.MERGED, merged_at=None), "changed", ["UpdateStatus", "AddComment", "SetNode"]),  # no proof
    (done(pr_state=PR.OPEN), "changed", ["UpdateStatus", "SetNode"]),
    (done(pr_state=PR.PENDING), "changed", ["UpdateStatus", "SetNode"]),
    (done(pr_state=PR.UNKNOWN), "changed", ["UpdateStatus", "SetNode"]),
    (done(pr_state=PR.CLOSED_UNMERGED, detail="why"), "changed", ["UpdateStatus", "AddComment", "SetNode"]),
])
def test_historical_done_develop_must_still_prove_the_merge(obs, outcome, kind_list):
    plan = decide_done_develop(obs, "m.yaml")
    assert (plan.outcome, names(plan)) == (outcome, kind_list)


def test_pending_remote_merge_records_the_request_marker():
    plan = decide_done_develop(done(pr_state=PR.PENDING), "m.yaml")
    assert plan.effects[1] == SetNode("a", {"status": "merging", "merge_request_state": "requested"})
    assert decide_done_develop(done(pr_state=PR.OPEN), "m.yaml").effects[1] == SetNode("a", {"status": "merging"})


def test_closed_unmerged_comment_carries_the_observed_detail():
    plan = decide_done_develop(done(pr_state=PR.CLOSED_UNMERGED, detail="closed by owner"), "m.yaml")
    assert "closed by owner" in plan.effects[1].text and plan.effects[0].status is S.BLOCKED


@pytest.mark.parametrize("status,verdict,expected", [
    (S.DONE, None, []),
    (S.IN_REVIEW, None, ["UpdateStatus"]),
    (S.DONE, "pass-with-nits", ["ResetReview"]),
    (S.IN_REVIEW, "pass-with-nits", ["ResetReview", "UpdateStatus"]),
])
def test_done_non_develop_repairs_projection(status, verdict, expected):
    assert names(plan_done_non_develop("i", status, verdict)) == expected


@pytest.mark.parametrize(("running", "failed", "full_scan", "audit_complete", "state"), [
    pytest.param(True, True, False, True, "running", id="running-work-wins-over-a-failure"),
    pytest.param(False, True, False, True, "needs_decision", id="a-failure-needs-a-decision"),
    pytest.param(False, False, False, True, "converged", id="nothing-left"),
    pytest.param(False, False, True, True, "converged", id="complete-audit-converges"),
    pytest.param(False, False, True, False, "running", id="incomplete-audit-cannot-report-converged"),
    pytest.param(False, True, True, False, "running", id="incomplete-audit-does-not-hide-behind-a-failure"),
    pytest.param(False, False, False, False, "converged", id="incomplete-audit-only-matters-for-a-full-scan"),
])
def test_the_tick_state_never_reports_convergence_from_an_unfinished_audit(running, failed, full_scan, audit_complete, state):
    assert decide_tick_state(running=running, failed=failed, full_scan=full_scan, audit_complete=audit_complete) == state


@pytest.mark.parametrize("remote", [PR.MERGED, PR.OPEN, PR.PENDING])
def test_an_unknown_merge_marker_blocks_a_done_node_whatever_the_remote_says(remote):
    plan = decide_done_develop(done(request_state="invalid", request_state_valid=False, pr_state=remote,
                                    merged_at="t" if remote == PR.MERGED else None), "m.yaml")

    assert [type(e).__name__ for e in plan.effects] == ["SetNode", "AddComment", "UpdateStatus"]


MERGE_CLOSED = [("SetNode", {"merged": True, "merged_at": "t", "merge_request_state": None})]


@pytest.mark.parametrize("marker,remote,expected", [
    ("intent", PR.MERGED, MERGE_CLOSED),
    ("intent", PR.OPEN, ["UpdateStatus", ("SetNode", {"status": "merging"})]),                       # the marker is left as it was
    ("intent", PR.PENDING, ["UpdateStatus", ("SetNode", {"status": "merging", "merge_request_state": "requested"})]),
    ("requested", PR.MERGED, MERGE_CLOSED),
    ("requested", PR.OPEN, ["UpdateStatus", ("SetNode", {"status": "merging"})]),
    ("requested", PR.PENDING, ["UpdateStatus", ("SetNode", {"status": "merging", "merge_request_state": "requested"})]),
])
def test_a_done_node_keeps_the_meaning_of_a_valid_merge_marker(marker, remote, expected):
    plan = decide_done_develop(done(request_state=marker, pr_state=remote, merged_at="t" if remote == PR.MERGED else None), "m.yaml")

    assert [(type(e).__name__, e.fields) if isinstance(e, SetNode) else type(e).__name__ for e in plan.effects] == expected

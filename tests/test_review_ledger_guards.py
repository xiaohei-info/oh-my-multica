"""The review ledger a Worker handoff relies on: which ones converge, which stall, which are refused. No platform."""
from dataclasses import replace

import pytest

from _support import ledgers
from omac.core.manifest import Node
from omac.core.review_convergence import advance_review_ledger, review_convergence_decision
from omac.core.taskmeta import TaskPhase, WorkerHandoffIntent
from omac.core.workitem import WorkItem, WorkItemControlProjection, WorkItemPayload, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.convergence import ResolutionState, check_handoff_ledger_ref, resolve_handoff_convergence

REF = {"attachment_id": "review-ledger-attachment", "sha256": "a" * 64}


def intent(round_index=3, *, subject="subject-3", verdict="reject"):
    return WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="alice", gate="review",
        source_review_subject_digest=subject, source_review_round=round_index, target_review_bounce=round_index,
        source_review_verdict=verdict, generation="handoff-1", target_agent_id="agent-alice")


def item_with(ledger, **fields):
    return WorkItem(id="item-1", workspace_id="ws", title="t", description="", status=WorkItemStatus.IN_PROGRESS, dag_key="a",
                    phase=TaskPhase.AUTHORING, review_ledger=ledger, **fields)


def test_a_stalled_ledger_asks_for_a_decision_before_another_handoff():
    ledger = ledgers.stalled(3)
    assert review_convergence_decision(ledger)["reason_code"] == "review-convergence-stalled"

    resolution = resolve_handoff_convergence(item_with(ledger), intent(3), "a")

    assert resolution.state is ResolutionState.NEEDS_DECISION
    assert resolution.decision["reason_code"] == "review-convergence-stalled"
    assert resolution.decision["recommended_action"] == "dag-amendment"
    assert resolution.decision["convergence"]["cycle_count"] == 3


def test_a_ledger_that_keeps_going_deeper_is_still_converging():
    resolution = resolve_handoff_convergence(item_with(ledgers.stalled(3, classification="deeper")), intent(3), "a")

    assert resolution.state is ResolutionState.VALID


def test_two_failed_reworks_are_not_yet_a_stall():
    assert resolve_handoff_convergence(item_with(ledgers.stalled(2)), intent(2), "a").state is ResolutionState.VALID


@pytest.mark.parametrize("name", ledgers.FORGERIES)
def test_a_forged_blocker_projection_is_refused_before_it_can_steer_a_handoff(name):
    round_index, ledger = ledgers.forged(name)

    with pytest.raises(PlatformError, match="review ledger|seen_count"):
        resolve_handoff_convergence(item_with(ledger), intent(round_index), "a")


@pytest.mark.parametrize("name", ledgers.UNTRUSTED)
def test_a_parseable_but_untrusted_ledger_is_refused(name):
    with pytest.raises(PlatformError, match="review ledger"):
        resolve_handoff_convergence(item_with(ledgers.copy_of(ledgers.UNTRUSTED[name])), intent(3), "a")


# ---- a recovery that restarted the ledger at round 1 --------------------------------------------------------------

def reject_report(root="recovery-reset-root"):
    return {
        "obligation_results": [{"obligation_id": "dimension:structure", "status": "fail", "evidence": "the same invariant still fails"}],
        "prior_blocker_results": [],
        "blockers": [{"root_cause_key": root, "obligation_id": "dimension:structure", "summary": "post-recovery finding",
                      "evidence": "the same invariant still fails", "required_fix": "close the root", "classification": "new"}],
    }


def test_a_ledger_restarted_by_recovery_does_not_invalidate_the_handoff_of_the_same_review():
    """The absolute round (4) lives in the handoff; the restarted ledger counts from 1. Same review event: fine."""
    ledger = advance_review_ledger(None, reject_report(), verdict="reject", subject_digest="subject-3", round_index=4)
    assert [cycle["round"] for cycle in ledger["cycles"]] == [1]

    resolution = resolve_handoff_convergence(item_with(ledger), intent(4), "a")

    assert resolution.state is ResolutionState.VALID


def test_a_restarted_ledger_whose_latest_review_is_another_subject_is_refused():
    ledger = advance_review_ledger(None, reject_report(), verdict="reject", subject_digest="drifted-subject", round_index=4)

    with pytest.raises(PlatformError):
        resolve_handoff_convergence(item_with(ledger), intent(4), "a")


def test_a_ledger_that_advanced_past_the_handoff_is_real_drift():
    ledger, blocker_id = None, None
    for index in range(1, 5):
        report = reject_report("older-root")
        report["blockers"][0]["evidence"] = f"round {index} invariant still fails"
        if blocker_id is not None:
            report["prior_blocker_results"] = [{"blocker_id": blocker_id, "status": "unchanged", "evidence": "the same invariant still fails"}]
            report["blockers"][0]["classification"] = "unchanged"
        ledger = advance_review_ledger(ledger, report, verdict="reject", subject_digest="subject-3" if index == 4 else "older-subject",
                                       round_index=index)
        blocker_id = ledger["blockers"][0]["blocker_id"]
    assert [cycle["round"] for cycle in ledger["cycles"]] == [1, 2, 3, 4]

    with pytest.raises(PlatformError):
        resolve_handoff_convergence(item_with(ledger), intent(3), "a")


# ---- which evidence the Loop must fetch before it decides -------------------------------------------------------------

@pytest.mark.parametrize(("round_index", "requires_ledger"), [(1, False), (3, True)])
def test_a_review_handoff_fetches_the_authoritative_ledger_from_the_third_review_on(round_index, requires_ledger):
    node = Node(id="a", worker="alice", reviewer="bob", title="a", description="")
    current = item_with(None, review_ledger_ref=REF, worker_handoff=intent(round_index))
    projection = WorkItemControlProjection(current, deferred_payloads=frozenset({WorkItemPayload.REVIEW_LEDGER}))

    plan = loop._build_work_item_hydration_plan(replace(node, status="in_progress"), projection)

    assert (WorkItemPayload.REVIEW_LEDGER in plan) is requires_ledger


# ---- the ledger a handoff was written against ----------------------------------------------------------------------------

def feedback_with_ledger_ref(ref):
    return {"verdict": "reject", "ledger_ref": ref}


@pytest.mark.parametrize(("round_index", "item_ref", "recorded_ref", "refused"), [
    pytest.param(3, REF, REF, False, id="same-ledger"),
    pytest.param(3, {**REF, "sha256": "b" * 64}, REF, True, id="ledger-replaced-after-the-handoff"),
    pytest.param(3, None, REF, True, id="ledger-gone"),
    pytest.param(3, REF, None, False, id="handoff-recorded-no-ledger"),
    pytest.param(2, {**REF, "sha256": "b" * 64}, REF, False, id="before-the-third-review-the-ledger-is-not-consulted"),
])
def test_a_handoff_must_still_point_at_the_ledger_it_was_written_against(round_index, item_ref, recorded_ref, refused):
    handoff = replace(intent(round_index), source_review_feedback=feedback_with_ledger_ref(recorded_ref) if recorded_ref else None)
    item = item_with(None, review_ledger_ref=item_ref)

    if refused:
        with pytest.raises(PlatformError, match="ledger reference changed"):
            check_handoff_ledger_ref(item, handoff)
    else:
        check_handoff_ledger_ref(item, handoff)

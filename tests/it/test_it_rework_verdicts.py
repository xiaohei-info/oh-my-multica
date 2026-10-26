"""Verdicts and intents the Loop must not trust: unproven handoffs, stale passes, nits that need an explicit acceptance.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
from dataclasses import replace

import pytest

from _support.loopkit import HANG, contract
from _support.rework import CONTINUATION, causal_intent, nits_review, rejected, workers
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop


@pytest.mark.parametrize("kind", ["malformed", "another-worker-and-subject", "stale-after-a-bounce"])
def test_a_handoff_intent_without_a_causal_identity_fails_closed_and_is_kept(kit, kind):
    manifest, path, item = rejected(kit, *HANG)
    current = kit.store.get_work_item(item.id)
    intent = {
        "malformed": {"schema": "omac.worker-handoff/v1", "state": "pending"},
        "another-worker-and-subject": causal_intent(current, target_worker="charlie", source_review_subject_digest="wrong-subject"),
        "stale-after-a-bounce": causal_intent(current),
    }[kind]
    kit.store.update_work_item_metadata(item.id, worker_handoff=intent)
    if kind == "stale-after-a-bounce":
        kit.store.update_work_item_metadata(item.id, review_bounce=1, artifacts={"pr_url": current.artifacts["pr_url"] + "-stale"})
    assigns = kit.record(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(PlatformError, match="causal|identity|predates"):
        kit.tick(manifest, path)

    assert kit.store.get_work_item(item.id).worker_handoff is not None and not assigns


def test_a_review_handoff_whose_bounce_is_zero_changes_nothing(kit):
    manifest, path, item = rejected(kit, *HANG)
    current = kit.store.get_work_item(item.id)
    baseline = tuple(sorted(run.id for run in kit.runtime.list_runs(item.id) if run.kind == "direct"))
    kit.store.update_work_item_metadata(item.id, worker_handoff=causal_intent(
        current, target_review_bounce=0, generation="handoff-zero-bounce", target_agent_id=kit.store.resolve_agent_id("alice"),
        baseline_direct_run_ids=list(baseline), baseline_verification_attachment_id=current.verification_ref["attachment_id"],
        target_worker_bounce=current.bounces.worker))
    kit.forbid(kit.store, "reset_review", "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(PlatformError, match="causal identity"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")


@pytest.mark.parametrize("legacy", [True, False], ids=["no-handoff-no-seal", "handoff-can-seal"])
def test_a_pass_left_over_from_an_older_subject_never_merges_a_new_delivery(kit, legacy):
    """With a handoff the new delivery is sealed and reviewed afresh; without one (a delivery from before handoffs
    existed) the Reviewer cannot be bound to a Run and the node asks for a decision. Either way nothing merges."""
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")]])
    node = kit.node("a", reviewer="bob", contract=contract())
    manifest = kit.manifest([node])
    path = kit.save(manifest)
    kit.tick(manifest, path)
    item = kit.store.get_work_item(node.work_item_id)
    kit.wait_runs_ended(item.id)
    kit.store.update_work_item_metadata(item.id, review_verdict="pass", delivery_identity={}, **({"worker_handoff": {}} if legacy else {}),
                                        review_report={"review_goals": ["a pass for an older delivery"], "diff_reviewed": True,
                                                       "tests_rerun": True, "coverage_checked": True, "full_review_completed": True,
                                                       "blockers": [], "nits": []})

    result = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert recovered.review_verdict is None and not kit.merges()
    if legacy:
        assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and recovered.status is WorkItemStatus.BLOCKED
        assert recovered.decision_required["reason_code"] == "reviewer-run-baseline-unavailable"
    else:
        assert result.state == "running" and manifest.nodes["a"].status == "in_review" and recovered.phase is TaskPhase.REVIEW


def test_a_reviewer_already_running_on_the_same_subject_is_not_assigned_or_woken_again(kit):
    manifest, path, item = kit.in_review()
    subject = kit.store.get_work_item(item.id).review_subject_digest
    manifest.nodes["a"].status = "in_progress"   # the manifest is behind the platform
    kit.save(manifest)
    kit.fault(kit.store, "assign_work_item", when="before", error=AssertionError("must not assign"))
    kit.fault(kit.runtime, "wake", when="before", error=AssertionError("must not wake"))
    kit.fault(kit.store, "update_work_item_metadata", when="before", error=AssertionError("must not refresh the issue body"),
              only=lambda _id, **m: "description" in m)

    result = kit.tick(manifest, path)

    assert result.state == "running" and manifest.nodes["a"].status == "in_review"
    assert kit.store.get_work_item(item.id).review_subject_digest == subject and len(kit.reviewer_runs(item.id)) == 1


@pytest.mark.parametrize(("bounces", "limit", "continuation"), [
    pytest.param(0, None, None, id="first-review"),
    pytest.param(1, 2, None, id="below-the-review-limit"),
    pytest.param(9, 9, None, id="at-the-review-limit"),
    pytest.param(9, 9, CONTINUATION, id="a-continuation-does-not-turn-nits-into-rework"),
])
def test_pass_with_nits_waits_for_an_explicit_acceptance_and_never_bounces_the_worker(kit, bounces, limit, continuation):
    manifest, path, item = nits_review(kit, bounces=bounces, continuation=continuation)
    reviewed = kit.store.get_work_item(item.id)
    runs_before, assigns = kit.run_ids(item.id), kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path, **({"retry_limits": {"review": limit}} if limit else {}))

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.bounces.review == bounces and blocked.review_verdict == "pass-with-nits" and blocked.review_report == reviewed.review_report
    assert blocked.worker_handoff is None and blocked.review_nits_acceptance is None
    decision = blocked.decision_required
    assert decision["schema"] == "omac.decision-required/v1" and decision["reason_code"] == "review-nits-acceptance-required"
    assert (decision["kind"], decision["phase"], decision["gate"]) == ("develop", "review", "review-nits")
    assert decision["resume_issue_id"] == item.id and decision["node_id"] == "a" and decision["verdict"] == "pass-with-nits"
    assert decision["review_subject_digest"] == blocked.review_subject_digest and decision["review_report_ref"] == blocked.review_report_ref
    assert f"omac node accept-nits {path} a" in result.report["failed_nodes"][0]["reason"]
    again = kit.tick(manifest, path, **({"retry_limits": {"review": limit}} if limit else {}))   # the decision is persisted by now
    assert f"omac node accept-nits {path} a" in again.report["next_actions"]
    assert not workers(assigns) and kit.run_ids(item.id) == runs_before


def test_a_legacy_partial_nits_report_cannot_skip_the_obligation_evidence_gate(kit):
    manifest, path, item = rejected(kit, *HANG)
    kit.store.update_work_item_metadata(item.id, review_verdict="pass-with-nits", review_report={
        "review_goals": ["partial legacy report"], "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "full_review_completed": True, "acceptance_mapping": [{"acceptance": "works", "status": "pass"}], "blockers": [],
        "nits": ["looks fine"]})

    result = kit.tick(manifest, path)

    got = kit.store.get_work_item(item.id)
    assert result.state == "running" and got.status is WorkItemStatus.IN_PROGRESS and got.review_verdict is None and got.bounces.review == 1


@pytest.mark.parametrize("break_ref", [
    pytest.param(lambda ref: None, id="legacy-inline-only"),
    pytest.param(lambda ref: {**ref, "attachment_id": ""}, id="wrong-attachment"),
    pytest.param(lambda ref: {**ref, "sha256": "not-a-sha"}, id="wrong-sha"),
])
def test_a_nits_handoff_without_the_exact_report_reference_changes_nothing(kit, break_ref):
    manifest, path, item = nits_review(kit)
    before = kit.store.get_work_item(item.id)
    shown = kit.distort(kit.store, "observe_work_item_control", lambda projection: replace(
        projection, work_item=replace(projection.work_item, review_report_ref=break_ref(before.review_report_ref))), times=100)
    kit.forbid(kit.store, "update_work_item_metadata", "reset_review", "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake", "list_runs")

    with pytest.raises(PlatformError, match="review report ref|feedback|declared SHA-256"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=1, gate="review-nits")

    shown["armed"] = False   # reads are real again
    after = kit.store.get_work_item(item.id)
    assert (after.status, after.phase, after.bounces, after.review_verdict, after.worker_handoff) == (
        before.status, before.phase, before.bounces, before.review_verdict, before.worker_handoff)

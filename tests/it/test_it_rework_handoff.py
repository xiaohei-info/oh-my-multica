"""A rejected delivery goes back to the Worker: the handoff, its budgets, and every crash on the way.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
import pytest

from _support.loopkit import HANG
from _support.rework import (
    CHECKPOINTS,
    CRASH,
    IDLE,
    delivered_again,
    rejected,
    reviewers,
    worker_assignment_sees_authoring,
    workers,
)
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase, review_context_binding
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline.dispatch import _previous_review_context


def test_a_rejection_hands_the_worker_back_with_the_rejected_head_and_feedback(kit):
    manifest, path, item = rejected(kit, *HANG)
    reviewed = kit.store.get_work_item(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path, retry_limits={"review": 3})

    current = kit.store.get_work_item(item.id)
    handoff = current.worker_handoff
    assert result.state == "running" and manifest.nodes["a"].status == "in_progress"
    assert current.status is WorkItemStatus.IN_PROGRESS and current.phase is TaskPhase.AUTHORING
    assert current.review_verdict is None and current.review_report_ref is None and current.review_subject_digest is None
    assert current.bounces.review == 1
    assert handoff.gate == "review" and handoff.source_review_verdict == "reject"
    assert handoff.baseline_pr_head_sha == reviewed.artifacts["head_sha"]
    assert handoff.review_context_binding == review_context_binding(reviewed) and handoff.is_causally_bound()
    previous = _previous_review_context(current)
    assert previous["report_ref"] == reviewed.review_report_ref and previous["blockers"][0]["required_fix"]
    assert len(workers(assigns)) == 1 and not reviewers(assigns)


@pytest.mark.parametrize("ending", ["completed", "cancelled"])
def test_a_rework_run_that_ends_without_a_submit_costs_one_bounded_retry(kit, ending):
    manifest, path, item = rejected(kit, IDLE if ending == "completed" else HANG[0], *HANG)
    kit.tick(manifest, path, retry_limits={"worker": 1})
    first = kit.store.get_work_item(item.id).worker_handoff
    kit.wait_for(lambda: kit.runtime.list_runs(item.id)[0].agent_id == first.target_agent_id, what="the rework Run")
    if ending == "cancelled":
        kit.runtime.cancel(item.id)
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    kit.tick_until(manifest, path, lambda: kit.store.get_work_item(item.id).bounces.worker == 1, retry_limits={"worker": 1},
                   what="the bounded retry")
    for _ in range(3):
        kit.tick(manifest, path, retry_limits={"worker": 1})

    recovered = kit.store.get_work_item(item.id)
    assert manifest.nodes["a"].status == "in_progress" and recovered.bounces.worker == 1
    assert recovered.worker_handoff.target_worker_bounce == 1 and recovered.worker_handoff.target_run_id != first.target_run_id
    assert len(workers(assigns)) == 1


def test_a_rework_run_that_ends_without_a_submit_blocks_when_the_worker_budget_is_spent(kit):
    manifest, path, item = rejected(kit, IDLE, *HANG)
    kit.tick(manifest, path, retry_limits={"worker": 0})
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "blocked", retry_limits={"worker": 0},
                   what="the node to block")

    blocked = kit.store.get_work_item(item.id)
    assert blocked.status is WorkItemStatus.BLOCKED and blocked.worker_handoff is None and not workers(assigns)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_every_crash_on_the_way_back_to_the_worker_resumes_with_exactly_one_worker_run(kit, checkpoint):
    manifest, path, item = rejected(kit, *HANG)
    runs_before = len(kit.runtime.list_runs(item.id))
    assigns = kit.record(kit.store, "assign_work_item")
    worker_assignment_sees_authoring(kit, item.id)
    crash = CHECKPOINTS[checkpoint](kit)

    with pytest.raises(RuntimeError, match="crash"):
        kit.tick(manifest, path)

    assert crash["fired"] and load_manifest(path).nodes["a"].status == "in_review"
    interrupted = kit.store.get_work_item(item.id)
    assert interrupted.worker_handoff is not None and interrupted.worker_handoff.source_review_feedback["verdict"] == "reject"
    assert interrupted.worker_handoff.source_review_feedback["blockers"]
    crash["armed"] = False
    persisted = load_manifest(path)

    first, second = kit.tick(persisted, path), kit.tick(persisted, path)

    recovered = kit.store.get_work_item(item.id)
    assert first.state == second.state == "running" and persisted.nodes["a"].status == "in_progress"
    assert recovered.phase is TaskPhase.AUTHORING and recovered.status is WorkItemStatus.IN_PROGRESS
    assert recovered.review_verdict is None and recovered.review_report is None and recovered.review_subject_digest is None
    assert not recovered.decision_required and recovered.bounces.review == 1
    assert recovered.worker_handoff.source_review_feedback["verdict"] == "reject" and recovered.worker_handoff.source_review_feedback["blockers"]
    assert len(kit.runtime.list_runs(item.id)) == runs_before + 1
    assert workers(assigns)[-1] is assigns[-1] and not reviewers(assigns)


def test_a_crash_between_the_retry_intent_and_its_bounce_resumes_in_the_same_generation(kit):
    manifest, path, item = rejected(kit, IDLE, *HANG)
    kit.tick(manifest, path, retry_limits={"worker": 1})
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")
    crash = kit.fault(kit.store, "update_work_item_metadata", error=CRASH,
                      only=lambda _id, **m: getattr(m.get("worker_handoff"), "target_worker_bounce", None) == 1)

    with pytest.raises(RuntimeError, match="crash"):
        for _ in range(5):
            kit.tick(manifest, path, retry_limits={"worker": 1})

    interrupted = kit.store.get_work_item(item.id)
    generation = interrupted.worker_handoff.generation
    assert interrupted.worker_handoff.target_worker_bounce == 1 and interrupted.bounces.worker == 0 and not workers(assigns)
    crash["armed"] = False
    persisted = load_manifest(path)
    for _ in range(3):
        kit.tick(persisted, path, retry_limits={"worker": 1})

    recovered = kit.store.get_work_item(item.id)
    assert recovered.worker_handoff.generation == generation and recovered.bounces.worker == 1 and len(workers(assigns)) == 1


def test_an_assignment_whose_response_is_unknown_keeps_the_handoff_and_never_starts_a_second_run(kit):
    """On Multica the assignment itself starts the Worker Run, so that is where a lost response hides a started Run."""
    manifest, path, item = rejected(kit, *HANG)
    runs_before = len(kit.runtime.list_runs(item.id))
    unknown = kit.fault(kit.store, "assign_work_item", when="after", error=PlatformError("assignment response unknown"),
                        only=lambda _id, _who, role, **k: role == "worker")

    kit.tick(manifest, path, retry_limits={"review": 3})

    interrupted = kit.store.get_work_item(item.id)
    assert unknown["fired"] and interrupted.worker_handoff is not None and interrupted.bounces.review == 1
    assert interrupted.status is WorkItemStatus.IN_PROGRESS and len(kit.runtime.list_runs(item.id)) == runs_before + 1
    unknown["armed"] = False
    kit.forbid(kit.runtime, "wake")
    persisted = load_manifest(path)

    first, second = kit.tick(persisted, path), kit.tick(persisted, path)

    recovered = kit.store.get_work_item(item.id)
    assert first.state == second.state == "running" and persisted.nodes["a"].status == "in_progress"
    assert recovered.worker_handoff is not None and recovered.bounces.review == 1
    assert len(kit.runtime.list_runs(item.id)) == runs_before + 1


def test_a_new_delivery_wins_over_a_handoff_intent_left_behind_by_a_crash(kit):
    manifest, path, item = delivered_again(kit)
    source_subject = kit.store.get_work_item(item.id).review_subject_digest
    runs_before, assigns = len(kit.runtime.list_runs(item.id)), kit.record(kit.store, "assign_work_item")
    crash = kit.fault(kit.store, "update_work_item_metadata", when="before", error=CRASH,
                      only=lambda _id, **m: m.get("worker_handoff") == {})

    with pytest.raises(RuntimeError, match="crash"):
        kit.tick(manifest, path)

    crash["armed"] = False
    persisted = load_manifest(path)
    first, second = kit.tick(persisted, path), kit.tick(persisted, path)

    recovered = kit.store.get_work_item(item.id)
    assert first.state == second.state == "running" and persisted.nodes["a"].status == "in_review"
    assert recovered.worker_handoff is None and recovered.phase is TaskPhase.REVIEW and recovered.status is WorkItemStatus.IN_REVIEW
    assert recovered.review_subject_digest != source_subject
    assert not workers(assigns) and len(reviewers(assigns)) == 1 and len(kit.runtime.list_runs(item.id)) == runs_before + 1


def test_a_crash_after_the_delivery_is_sealed_and_before_the_handoff_retires_is_restart_safe(kit):
    manifest, path, item = delivered_again(kit)
    assigns = kit.record(kit.store, "assign_work_item")
    crash = kit.fault(kit.store, "update_work_item_metadata", error=CRASH, only=lambda _id, **m: bool(m.get("delivery_identity")))

    with pytest.raises(RuntimeError, match="crash"):
        kit.tick(manifest, path)

    persisted_item = kit.store.get_work_item(item.id)
    assert persisted_item.delivery_identity is not None and persisted_item.worker_handoff is not None
    crash["armed"] = False
    persisted = load_manifest(path)
    kit.tick(persisted, path)
    kit.tick(persisted, path)

    recovered = kit.store.get_work_item(item.id)
    assert recovered.worker_handoff is None and len(reviewers(assigns)) == 1 and not workers(assigns)


def test_an_assignment_with_an_unknown_response_still_finds_the_completed_causal_submit(kit):
    manifest, path, item = rejected(kit)

    def worker_finishes_and_the_response_is_lost(_result, _id, _assignee, role, **_kw):
        if role == "worker":
            kit.wait_runs_ended(item.id)
            raise PlatformError("assignment response unknown")

    kit.after(kit.store, "assign_work_item", worker_finishes_and_the_response_is_lost, only=lambda _id, _who, role, **k: role == "worker")
    wakes = kit.record(kit.runtime, "wake")

    first = kit.tick(manifest, path)

    assert first.state == "running" and not [w for w in wakes if w[1][2] == "worker"]
    assert kit.store.get_work_item(item.id).worker_handoff is None


def test_a_rework_that_submits_goes_to_review_without_a_second_worker_run(kit):
    manifest, path, item = rejected(kit)
    assigns = kit.record(kit.store, "assign_work_item")
    wakes = kit.record(kit.runtime, "wake")

    first = kit.tick(manifest, path)
    assert first.state == "running" and kit.store.get_work_item(item.id).worker_handoff is not None
    kit.wait_runs_ended(item.id)
    second = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert second.state == "running" and manifest.nodes["a"].status == "in_review"
    assert recovered.worker_handoff is None and recovered.phase is TaskPhase.REVIEW and recovered.status is WorkItemStatus.IN_REVIEW
    assert len(workers(assigns)) == 1 and len(reviewers(assigns)) == 1 and not [w for w in wakes if w[1][2] == "worker"]


def test_a_rejection_is_final_when_the_review_budget_is_zero(kit):
    manifest, path, item = rejected(kit, *HANG)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path, retry_limits={"review": 0})

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert blocked.bounces.review == 0 and not workers(assigns)
    assert any("retry limit" in body for body in kit.comments(item.id))


def test_the_second_rejection_exhausts_a_review_budget_of_one(kit):
    reject = [{"hang": 3}, kit.stack.review("reject")]
    manifest, path, item = rejected(kit, reviews=(reject, reject))
    kit.tick(manifest, path, retry_limits={"review": 1})
    assert manifest.nodes["a"].status == "in_progress" and kit.store.get_work_item(item.id).bounces.review == 1
    assert kit.store.get_work_item(item.id).review_verdict is None   # the verdict is cleared; the next review starts afresh

    kit.wait_runs_ended(item.id)
    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_review", retry_limits={"review": 1}, what="the second review")
    kit.wait_for(lambda: kit.store.get_work_item(item.id).review_verdict == "reject", what="the second rejection")
    kit.wait_runs_ended(item.id)
    result = kit.tick(manifest, path, retry_limits={"review": 1})

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and blocked.bounces.review == 1


def test_a_rejection_then_a_pass_converges_with_the_whole_review_history(kit):
    manifest, path, item = rejected(kit, reviews=([{"hang": 3}, kit.stack.review("reject")], [kit.stack.review("pass")]))

    result = kit.settle(manifest, path, timeout=120, retry_limits={"review": 3})

    done = kit.store.get_work_item(item.id)
    assert result.state == "converged" and manifest.nodes["a"].status == "done" and manifest.nodes["a"].merged is True
    assert done.status is WorkItemStatus.DONE and done.bounces.review == 1
    assert [cycle["verdict"] for cycle in done.review_ledger["cycles"]] == ["reject", "pass"]
    assert len({cycle["subject_digest"] for cycle in done.review_ledger["cycles"]}) == 2
    assert len(kit.merges()) == 1

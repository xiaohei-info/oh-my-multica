"""A rejected delivery goes back to the Worker: the handoff, its budgets, and every crash on the way.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from _support import ledgers
from _support.loopkit import HANG, contract
from omac.core.manifest import load_manifest
from omac.core.review_convergence import review_subject_digest
from omac.core.taskmeta import TaskPhase, review_context_binding
from omac.core.workitem import WorkItemControlProjection, WorkItemPayload, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.dispatch import _previous_review_context

IDLE = []   # a Run that ends at once without submitting anything


def rejected(kit, *rework, reviews=None):
    """``reviews``: the Reviewer's attempts (default: reject the first delivery, hang on the next)."""
    manifest, path, item = kit.in_review(*(reviews or ([{"hang": 3}, kit.stack.review("reject")], *HANG)), rework=rework)
    kit.wait_for(lambda: kit.store.get_work_item(item.id).review_verdict == "reject", what="the rejection")
    kit.wait_runs_ended(item.id)
    return manifest, path, item


def workers(assigns):
    return [a for a in assigns if a[1][2] == "worker"]


def reviewers(assigns):
    return [a for a in assigns if a[1][2] == "reviewer"]


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


# ---- crashes on the way back to the Worker -------------------------------------------------------------------------

CRASH = RuntimeError("crash")
CHECKPOINTS = {
    "intent": lambda kit: kit.fault(kit.store, "update_work_item_metadata", error=CRASH,
                                    only=lambda _id, **m: m.get("worker_handoff") is not None),
    "bounce": lambda kit: kit.fault(kit.store, "update_work_item_metadata", error=CRASH,
                                    only=lambda _id, **m: m.get("review_bounce") == 1),
    "reset_review": lambda kit: kit.fault(kit.store, "reset_review", error=CRASH),
    "status": lambda kit: kit.fault(kit.store, "update_status", error=CRASH,
                                    only=lambda _id, status: status is WorkItemStatus.IN_PROGRESS),
    "assignment": lambda kit: kit.fault(kit.store, "assign_work_item", error=CRASH, only=lambda _id, _who, role, **k: role == "worker"),
}


def worker_assignment_sees_authoring(kit, item_id):
    """The Worker may only be assigned once the item is back in authoring, in progress, with the review cleared."""
    def check(_id, _assignee, role, **_kw):
        current = kit.store.get_work_item(item_id)
        if role == "worker":
            assert current.phase is TaskPhase.AUTHORING and current.status is WorkItemStatus.IN_PROGRESS
            assert current.review_verdict is None and current.review_report is None and current.review_subject_digest is None

    kit.before(kit.store, "assign_work_item", check)


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


def delivered_again(kit):
    """The rework Run submitted a new delivery and ended; the Loop has not looked yet."""
    manifest, path, item = rejected(kit)
    kit.tick(manifest, path)
    assert kit.store.get_work_item(item.id).worker_handoff is not None
    kit.wait_runs_ended(item.id)
    return manifest, path, item


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


# ---- what counts as the Worker's new delivery ---------------------------------------------------------------------------

def pushes(kit, item):
    """Move the PR head as a push would."""
    prs = json.loads((kit.stack.gh_state / "prs.json").read_text())
    url = item.artifacts["pr_url"]
    kit.pr(url, pushes=prs.get(url, {}).get("pushes", 0) + 1)


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


def test_a_delivery_that_a_member_submits_cannot_complete_the_workers_handoff(kit):
    from _support.deliveries import verification_for
    from _support.loopkit import contract
    import yaml
    from omac.pipeline.dispatch import submit as submit_work

    manifest, path, item = rejected(kit, IDLE, *HANG)
    kit.tick(manifest, path, retry_limits={"worker": 5})
    kit.wait_runs_ended(item.id)
    current = kit.store.get_work_item(item.id)
    document = kit.tmp_path / "verification.yaml"
    document.write_text(yaml.safe_dump({**verification_for(contract(), "a"), "revision": "by a member"}))
    pushes(kit, current)
    submit_work(kit.store, item.id, code_host=kit.code_host, pr_url=current.artifacts["pr_url"], verification_file=str(document))
    assigns = kit.record(kit.store, "assign_work_item")

    with pytest.raises(PlatformError, match="causal|identity|handoff"):
        kit.tick(manifest, path, retry_limits={"worker": 5})

    assert kit.store.get_work_item(item.id).worker_handoff is not None and not reviewers(assigns)


def test_a_matching_submit_waits_until_the_workers_run_is_over(kit):
    manifest, path, item = rejected(kit, [kit.stack.tool("worker-submit"), {"hang": 600}])
    kit.tick(manifest, path)
    baseline = kit.store.get_work_item(item.id).worker_handoff.baseline_verification_attachment_id
    kit.wait_for(lambda: kit.store.get_work_item(item.id).verification_ref.get("attachment_id") != baseline, what="the new submit")
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path)

    assert result.state == "running" and manifest.nodes["a"].status == "in_progress" and not reviewers(assigns)
    assert kit.store.get_work_item(item.id).worker_handoff is not None


def test_a_revision_on_the_rejected_head_returns_to_the_worker_not_to_the_reviewer(kit):
    manifest, path, item = rejected(kit, [kit.stack.tool("worker-submit", "--no-push")], *HANG)
    kit.tick(manifest, path)
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path)

    assert result.state == "running" and manifest.nodes["a"].status == "in_progress"
    assert not reviewers(assigns) and kit.store.get_work_item(item.id).worker_handoff is not None


def test_a_pr_head_that_moves_after_the_submit_is_caught_before_the_reviewer(kit):
    manifest, path, item = delivered_again(kit)
    pushes(kit, kit.store.get_work_item(item.id))
    assigns = kit.record(kit.store, "assign_work_item")

    with pytest.raises(PlatformError, match="head|HEAD"):
        kit.tick(manifest, path)

    assert not reviewers(assigns)


def test_a_pr_head_that_moves_after_the_seal_blocks_the_reviewer_dispatch(kit):
    manifest, path, item = delivered_again(kit)
    kit.after(kit.store, "update_work_item_metadata", lambda *_, **__: pushes(kit, kit.store.get_work_item(item.id)),
              only=lambda _id, **m: bool(m.get("delivery_identity")))
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path)

    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and not reviewers(assigns)
    assert any("head" in failed["reason"].lower() for failed in result.report["failed_nodes"])


def test_the_ordinary_ci_gate_still_applies_to_a_reworked_delivery(kit):
    manifest, path, item = delivered_again(kit)
    assigns = kit.record(kit.store, "assign_work_item")
    checks = kit.record(kit.code_host, "check_pull_request")

    result = kit.tick(manifest, path, retry_limits={"ci": 0}, config={"ci": {"check_command": "gh pr checks {pr_url}"}})

    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert len(checks) == 1 and not reviewers(assigns)


def test_a_delivery_without_commands_is_stopped_by_the_ordinary_evidence_gate(kit):
    manifest, path, item = rejected(kit, [kit.stack.tool("worker-submit", "--empty-commands")], *HANG)
    kit.tick(manifest, path)
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path)

    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and not reviewers(assigns)


# ---- reads that lag or disagree --------------------------------------------------------------------------------------

def stale_status(item):
    return replace(item, status=WorkItemStatus.TODO)


def stale_projection(projection):
    return replace(projection, work_item=stale_status(projection.work_item))


def test_a_stale_status_readback_after_the_assignment_does_not_restart_the_handoff(kit):
    manifest, path, item = rejected(kit, *HANG)
    stale = []
    kit.after(kit.store, "assign_work_item", lambda *_, **__: stale.extend([
        kit.distort(kit.store, "get_work_item", stale_status), kit.distort(kit.store, "observe_work_item_control", stale_projection)]),
        only=lambda _id, _who, role, **k: role == "worker")
    assigns, wakes = kit.record(kit.store, "assign_work_item"), kit.record(kit.runtime, "wake")

    result = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert stale and result.state == "running" and manifest.nodes["a"].status == "in_progress"
    assert recovered.worker_handoff is not None and recovered.phase is TaskPhase.AUTHORING and recovered.status is WorkItemStatus.IN_PROGRESS
    assert len(workers(assigns)) == 1 and not [w for w in wakes if w[1][2] == "worker"] and not reviewers(assigns)


def test_two_stale_reads_after_a_lost_assignment_response_are_followed_by_the_real_delivery(kit):
    manifest, path, item = rejected(kit)
    stale_projection_before_the_delivery = kit.store.observe_work_item_control(item.id)
    lag = []

    def worker_finishes_and_the_response_is_lost(_result, _id, _assignee, role, **_kw):
        kit.wait_runs_ended(item.id)   # the delivery exists, but the next two reads still show the item as it was
        lag.append(kit.distort(kit.store, "observe_work_item_control", lambda _fresh: stale_projection_before_the_delivery, times=2))
        raise PlatformError("assignment response unknown")

    kit.after(kit.store, "assign_work_item", worker_finishes_and_the_response_is_lost, only=lambda _id, _who, role, **k: role == "worker")
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path)
    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_review", what="the delivery to be sealed")

    assert lag[0]["calls"] == 2 and result.state == "running"
    assert len(workers(assigns)) == 1 and len(reviewers(assigns)) == 1
    assert kit.store.get_work_item(item.id).worker_handoff is None


def test_a_delivery_that_changes_while_it_is_being_sealed_defers_one_tick_and_is_sealed_the_next(kit):
    manifest, path, item = delivered_again(kit)
    changed = kit.store.get_work_item(item.id)
    other_pr = replace(changed, artifacts={**changed.artifacts, "pr_url": changed.artifacts["pr_url"] + "-other"})
    # the read right before the seal is committed shows another pull request
    window = kit.distort(kit.store, "observe_work_item_control", lambda projection: replace(projection, work_item=other_pr),
                         skip=2, times=1)
    assigns = kit.record(kit.store, "assign_work_item")

    first = kit.tick(manifest, path)

    deferred = kit.store.get_work_item(item.id)
    assert window["calls"] == 1 and first.state == "running" and manifest.nodes["a"].status == "in_progress"
    assert deferred.worker_handoff is not None and deferred.delivery_identity is None and not reviewers(assigns)

    second = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert second.state == "running" and manifest.nodes["a"].status == "in_review"
    assert recovered.worker_handoff is None and recovered.delivery_identity is not None and len(reviewers(assigns)) == 1


def test_a_verification_projection_that_disagrees_with_the_attachment_is_never_sealed(kit):
    manifest, path, item = delivered_again(kit)

    def tampered(projection):
        shown = replace(projection.work_item, verification={"commands": []})
        return replace(projection, work_item=shown, deferred_payloads=projection.deferred_payloads - {WorkItemPayload.VERIFICATION})

    kit.distort(kit.store, "observe_work_item_control", tampered, times=50)
    assigns = kit.record(kit.store, "assign_work_item")

    with pytest.raises(PlatformError, match="projection|attachment"):
        kit.tick(manifest, path)

    assert not reviewers(assigns)


# ---- intents and verdicts that cannot be trusted ----------------------------------------------------------------------

def causal_intent(item, **fields):
    return {"schema": "omac.worker-handoff/v1", "state": "pending", "target_worker": "alice", "gate": "review",
            "source_review_subject_digest": item.review_subject_digest, "source_review_round": 1, "target_review_bounce": 1, **fields}


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


# ---- a submit that becomes visible after the Run ended ----------------------------------------------------------------

def slow_submit(kit):
    return [{"hang": 3}, kit.stack.tool("worker-submit")]


def test_a_submit_that_shows_up_after_the_budget_is_spent_still_beats_the_no_submit_guard(kit):
    kit.proxy   # the Loop's reads go through a recording proxy so that they can lag
    manifest, path, item = rejected(kit, slow_submit(kit))
    limit = 20
    kit.store.update_work_item_metadata(item.id, worker_bounce=limit)
    kit.tick(manifest, path, retry_limits={"worker": limit})
    kit.lag_reads_of(item.id)         # what the item showed before the submit
    kit.wait_runs_ended(item.id)      # the Run is over and its submit exists, but reads still show the old item ...
    kit.after(kit.runtime, "list_runs", lambda *_: kit.catch_up(), times=1)   # ... until the Loop has looked at the Run
    assigns = kit.record(kit.store, "assign_work_item")

    first = kit.tick(manifest, path, retry_limits={"worker": limit})
    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_review", retry_limits={"worker": limit},
                   what="the late delivery to be reviewed")

    recovered = kit.store.get_work_item(item.id)
    assert first.failed == [] and recovered.status is WorkItemStatus.IN_REVIEW
    assert recovered.worker_handoff is None and recovered.delivery_identity is not None
    assert recovered.bounces.worker == limit and not workers(assigns)


def test_a_submit_that_arrives_within_the_observation_window_is_collected(kit):
    kit.monkeypatch.setattr(loop, "_HANDOFF_TERMINAL_GRACE_SECONDS", 120)
    kit.proxy
    manifest, path, item = rejected(kit, slow_submit(kit))
    kit.tick(manifest, path)
    kit.lag_reads_of(item.id)
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    kit.tick(manifest, path)
    assert kit.store.get_work_item(item.id).worker_handoff is not None   # reads lag: the delivery is not visible yet
    kit.catch_up()
    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_review", what="the delivery to be collected")

    recovered = kit.store.get_work_item(item.id)
    assert recovered.worker_handoff is None and recovered.bounces.worker == 0 and not workers(assigns)


def test_a_node_blocked_by_an_old_projection_is_collected_when_its_handoff_delivered(kit):
    manifest, path, item = delivered_again(kit)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest.nodes["a"].status, manifest.nodes["a"].recovery_marker = "blocked", True
    kit.save(manifest)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert result.state == "running" and result.failed == [] and manifest.nodes["a"].status == "in_review"
    assert recovered.status is WorkItemStatus.IN_REVIEW and recovered.worker_handoff is None and recovered.delivery_identity is not None
    assert len(reviewers(assigns)) == 1


# ---- a review that does not converge ------------------------------------------------------------------------------------

def with_handoff_round(kit, item, round_index, **fields):
    """The pending handoff as if it answered the ``round_index``-th review."""
    intent = kit.store.get_work_item(item.id).worker_handoff
    feedback = {key: value for key, value in (intent.source_review_feedback or {}).items() if key != "ledger_ref"}   # the ledger is replaced
    kit.store.update_work_item_metadata(item.id, worker_handoff=replace(
        intent, source_review_round=round_index, target_review_bounce=round_index, source_review_feedback=feedback or None, **fields))
    return kit.store.get_work_item(item.id).worker_handoff


def test_a_review_that_stalls_blocks_before_another_worker_handoff(kit):
    """The third review repeats the blocker of the first two: no fourth attempt."""
    manifest, path, item = kit.in_review([{"hang": 4}, kit.stack.tool("reviewer-submit", "--repeat", "reject")], *HANG)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(2), review_bounce=2)   # two reworks happened
    third = kit.store.get_work_item(item.id)
    subject = review_subject_digest(third, 3)
    kit.store.update_work_item_metadata(item.id, review_subject_digest=subject,
                                        reviewer_run_baseline=replace(third.reviewer_run_baseline, subject_digest=subject))
    kit.wait_for(lambda: kit.store.get_work_item(item.id).review_verdict == "reject", what="the third rejection")
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path, retry_limits={"review": 20})

    blocked = kit.store.get_work_item(item.id)
    assert len(blocked.review_ledger["cycles"]) == 3
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.bounces.review == 2 and not workers(assigns)
    assert blocked.decision_required["reason_code"] == "review-convergence-stalled"
    assert blocked.decision_required["recommended_action"] == "dag-amendment" and blocked.decision_required["convergence"]["cycle_count"] == 3


@pytest.mark.parametrize("terminal_observed", [False, True])
def test_a_restart_decides_the_stall_before_it_looks_at_an_existing_handoff(kit, terminal_observed):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    intent = with_handoff_round(kit, item, 3, **({"terminal_observed_at": "2026-08-03T00:00:00+00:00"} if terminal_observed else {}))
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(3))
    runs_before = kit.run_ids(item.id)
    assigns = kit.record(kit.store, "assign_work_item")
    decisions = kit.record(kit.store, "update_work_item_metadata")
    statuses = kit.record(kit.store, "update_status")

    first = kit.tick(manifest, path)
    second = kit.tick(load_manifest(path), path)

    blocked = kit.store.get_work_item(item.id)
    assert first.state == second.state == "needs_decision" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required["reason_code"] == "review-convergence-stalled" and blocked.worker_handoff == intent
    assert len([d for d in decisions if (d[2].get("decision_required") or {}).get("reason_code") == "review-convergence-stalled"]) == 1
    assert len([s for s in statuses if s[1][1] is WorkItemStatus.BLOCKED]) == 1
    assert not workers(assigns) and kit.run_ids(item.id) == runs_before


def test_a_ledger_that_keeps_going_deeper_does_not_stop_the_handoff(kit):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    with_handoff_round(kit, item, 3)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(3, classification="deeper"))

    result = kit.tick(manifest, path)

    assert result.state == "running" and manifest.nodes["a"].status == "in_progress"


@pytest.mark.parametrize("name", [*ledgers.FORGERIES, *ledgers.UNTRUSTED])
def test_an_untrustworthy_ledger_fails_the_tick_before_any_write(kit, name):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    round_index, ledger = ledgers.forged(name) if name in ledgers.FORGERIES else (3, ledgers.copy_of(ledgers.UNTRUSTED[name]))
    with_handoff_round(kit, item, round_index)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledger)
    before = Path(path).read_bytes()
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(PlatformError, match="review ledger|seen_count"):
        kit.tick(manifest, path)

    assert not writes and manifest.nodes["a"].status == "in_progress" and Path(path).read_bytes() == before


def test_an_active_worker_is_not_restored_before_a_stalled_review_has_its_decision(kit):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    with_handoff_round(kit, item, 3)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(3))
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)    # the graph's blocked projection of the node
    manifest.nodes["a"].status = "blocked"
    kit.save(manifest)
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status")
    saved = []
    original_save = loop.save_manifest
    kit.monkeypatch.setattr(loop, "save_manifest", lambda current, target: (saved.append(current.nodes["a"].status), original_save(current, target))[1])
    kit.fault(kit.store, "assign_work_item", when="before", error=AssertionError("must not assign"))
    kit.fault(kit.runtime, "wake", when="before", error=AssertionError("must not wake"))

    result = kit.tick(manifest, path)

    blocked = kit.store.get_work_item(item.id)
    assert "in_progress" not in saved and writes[0][2].get("decision_required", {}).get("reason_code") == "review-convergence-stalled"
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert blocked.decision_required["reason_code"] == "review-convergence-stalled"


# ---- how many times a rejection may go back to the Worker ---------------------------------------------------------------

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


# ---- nits ---------------------------------------------------------------------------------------------------------------

def nits_review(kit, *, bounces=0, continuation=None):
    """Node `a` reviewed with pass-with-nits as its ``bounces + 1``-th review; the Loop has not collected the verdict."""
    manifest, path, item = kit.in_review([{"hang": 4}, kit.stack.review("pass-with-nits")], *HANG)
    kit.wait_for(lambda: kit.store.get_work_item(item.id).review_verdict == "pass-with-nits", what="the verdict")
    kit.wait_runs_ended(item.id)
    if bounces:
        current = kit.store.get_work_item(item.id)
        kit.store.update_work_item_metadata(item.id, review_bounce=bounces, **({"review_continuation": continuation} if continuation else {}))
        subject = review_subject_digest(kit.store.get_work_item(item.id), bounces + 1)
        kit.store.update_work_item_metadata(item.id, review_subject_digest=subject,
                                            reviewer_run_baseline=replace(current.reviewer_run_baseline, subject_digest=subject))
    return manifest, path, item


CONTINUATION = {"schema": "omac.review-continuation/v1", "stage": "develop", "mode": "producer-rework", "authorized_through_round": 10,
                "decision_count": 1, "reason": "operator approved one additional review round"}


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


def test_a_nits_handoff_may_be_answered_on_the_same_head_and_goes_to_review(kit):
    manifest, path, item = nits_review(kit)
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")], [kit.stack.tool("worker-submit", "--no-push")]])
    loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=1, gate="review-nits")
    handoff = kit.store.get_work_item(item.id).worker_handoff
    assert handoff.gate == "review-nits" and handoff.source_review_verdict == "pass-with-nits"
    manifest.nodes["a"].status = "in_progress"
    kit.save(manifest)
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_review", what="the answer to be reviewed")

    recovered = kit.store.get_work_item(item.id)
    assert recovered.worker_handoff is None and len(reviewers(assigns)) == 1 and not workers(assigns)


# ---- states the platform and the manifest can disagree about ---------------------------------------------------------------

def test_a_done_node_whose_issue_was_moved_back_to_review_is_corrected_to_done(kit):
    manifest, path, item = kit.in_review()
    url = item.artifacts["pr_url"]
    kit.pr(url, pushes=json.loads((kit.stack.gh_state / "prs.json").read_text())[url]["pushes"], merged=True)
    manifest.nodes["a"].status, manifest.nodes["a"].merged, manifest.nodes["a"].merged_at = "done", True, "2026-07-26T08:00:00Z"
    kit.save(manifest)
    kit.store.update_work_item_metadata(item.id, review_verdict="pass-with-nits", worker_handoff={}, reviewer_run_baseline={}, decision_required={})
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    result = kit.tick(manifest, path)

    assert result.state == "converged" and manifest.nodes["a"].status == "done"
    assert kit.store.get_work_item(item.id).status is WorkItemStatus.DONE


def test_a_done_node_with_a_reject_verdict_goes_back_to_the_worker(kit):
    """An older version could mark a legitimate rejection done; resuming must recognise it."""
    manifest, path, item = rejected(kit, *HANG)
    manifest.nodes["a"].status = "done"
    kit.save(manifest)
    kit.store.update_status(item.id, WorkItemStatus.DONE)

    result = kit.tick(manifest, path)

    got = kit.store.get_work_item(item.id)
    assert result.state == "running" and manifest.nodes["a"].status == "in_progress"
    assert got.status is WorkItemStatus.IN_PROGRESS and got.review_verdict is None and got.bounces.review == 1


@pytest.mark.parametrize("stale", ["todo", "blocked", "done"])
def test_a_reworked_delivery_is_reviewed_even_when_the_manifest_still_shows_an_old_state(kit, stale):
    manifest, path, item = delivered_again(kit)
    manifest.nodes["a"].status = stale
    kit.save(manifest)

    result = kit.tick(manifest, path)

    got = kit.store.get_work_item(item.id)
    assert result.state == "running" and manifest.nodes["a"].status == "in_review"
    assert got.status is WorkItemStatus.IN_REVIEW and got.phase is TaskPhase.REVIEW


def test_a_rejection_is_collected_while_the_manifest_still_shows_the_node_in_progress(kit):
    manifest, path, item = rejected(kit, *HANG)
    manifest.nodes["a"].status = "in_progress"
    kit.save(manifest)
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    result = kit.tick(manifest, path)

    got = kit.store.get_work_item(item.id)
    assert result.state == "running" and manifest.nodes["a"].status == "in_progress"
    assert got.status is WorkItemStatus.IN_PROGRESS and got.worker_handoff is not None and got.bounces.review == 1


# ---- preparing the handoff -------------------------------------------------------------------------------------------------

class AssignmentReached(Exception):
    pass


def unassigned_handoff(kit):
    """Node `a` with a Worker handoff that has not started a Run yet (everything before the assignment is done)."""
    kit.monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)
    manifest, path, item = kit.dispatched(wait=False)
    current = kit.store.get_work_item(item.id)
    baseline = tuple(sorted(run.id for run in kit.runtime.list_runs(item.id) if run.kind == "direct"))
    kit.store.update_work_item_metadata(item.id, worker_handoff=replace(
        current.worker_handoff, target_run_id=None, baseline_direct_run_ids=baseline))
    return manifest, path, item


def residual_reviewer_baseline(kit, item):
    from omac.core.taskmeta import ReviewerRunBaseline

    return ReviewerRunBaseline(
        schema="omac.reviewer-run-baseline/v1", subject_digest="old-subject", target_reviewer="bob",
        target_agent_id=kit.store.resolve_agent_id("bob"), cutoff_created_at="2026-08-01T00:00:00Z", generation="review-old", attempt=1)


def test_the_handoff_clears_a_residual_reviewer_baseline_before_it_assigns_the_worker(kit):
    manifest, path, item = unassigned_handoff(kit)
    kit.store.update_work_item_metadata(item.id, reviewer_run_baseline=residual_reviewer_baseline(kit, item))
    resets = kit.record(kit.store, "reset_review")

    def baseline_is_gone(*_args, **_kw):
        assert kit.store.get_work_item(item.id).reviewer_run_baseline is None
        raise AssignmentReached

    kit.before(kit.store, "assign_work_item", baseline_is_gone)

    with pytest.raises(AssignmentReached):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")

    assert len(resets) == 1


def test_a_reviewer_baseline_that_appears_during_preparation_stops_the_assignment(kit):
    manifest, path, item = unassigned_handoff(kit)
    drift = kit.after(kit.store, "update_work_item_metadata", lambda *_, **__: kit.store.update_work_item_metadata(
        item.id, reviewer_run_baseline=residual_reviewer_baseline(kit, item)), only=lambda _id, **m: "description" in m)
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    result = loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")

    assert drift["calls"] == 1 and result.state == "pending-preparation"
    assert kit.store.get_work_item(item.id).reviewer_run_baseline is not None


@pytest.mark.parametrize("field", ["machine_feedback_ref", "review_report_ref"])
@pytest.mark.parametrize("raw", [[], ["fact"]], ids=["empty-list", "list"])
def test_a_review_reference_that_turns_invalid_during_preparation_stops_the_assignment(kit, field, raw):
    manifest, path, item = unassigned_handoff(kit)
    shown = []

    def arm(*_args, **_kw):
        for name, transform in (
            ("get_work_item", lambda current: replace(current, **{field: raw})),
            ("observe_work_item_control", lambda projection: replace(projection, work_item=replace(projection.work_item, **{field: raw}))),
        ):
            shown.append(kit.distort(kit.store, name, transform, times=100))

    kit.after(kit.store, "update_work_item_metadata", arm, only=lambda _id, **m: "description" in m)
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    result = loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")

    assert shown and result.state == "pending-preparation"


@pytest.mark.parametrize("payload", [WorkItemPayload.VERIFICATION, WorkItemPayload.DELIVERABLE, WorkItemPayload.PROJECT_RULES])
@pytest.mark.parametrize("source", ["current", "head-drift", "unreadable"])
def test_a_new_review_handoff_reads_the_fresh_subject_before_it_writes_anything(kit, payload, source):
    manifest, path, item = rejected(kit, *HANG)
    kit.store.update_work_item_metadata(item.id, deliverable="exact delivery", project_rules="exact rules")
    current = kit.store.get_work_item(item.id)
    kit.store.update_work_item_metadata(item.id, review_subject_digest=review_subject_digest(current, current.bounces.review + 1))
    current = kit.store.get_work_item(item.id)
    stale_head = replace(current, artifacts={**current.artifacts, "head_sha": "c" * 40}) if source == "head-drift" else current
    deferred = WorkItemControlProjection(replace(stale_head, **{payload.value: None}), deferred_payloads=frozenset({payload}))
    plans = []

    def hydrate(shown, plan):
        plans.append(plan)
        return replace(shown.work_item, **{payload.value: None if source == "unreadable" else getattr(current, payload.value)})

    kit.monkeypatch.setattr(kit.store, "hydrate_work_item_evidence", hydrate)
    kit.distort(kit.store, "observe_work_item_control", lambda _fresh: deferred, times=1)
    writes = []

    def stop_before_write(_id, **metadata):
        writes.append(metadata)
        raise RuntimeError("source accepted before the first write")

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", stop_before_write)
    kit.forbid(kit.store, "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    error, message = (RuntimeError, "source accepted") if source == "current" else (PlatformError, "source is stale")
    with pytest.raises(error, match=message):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=current.bounces.review + 1,
                                      gate="review", projection=WorkItemControlProjection(current))

    assert plans == [frozenset({payload})] and bool(writes) is (source == "current")


def test_a_handoff_intent_that_cannot_be_written_never_retires_the_delivery(kit):
    manifest, path, item = rejected(kit, *HANG)
    before = kit.store.get_work_item(item.id)
    assert before.delivery_identity is not None
    kit.fault(kit.store, "update_work_item_metadata", when="before", error=PlatformError("metadata exceeds the 8KB size limit"),
              only=lambda _id, **m: bool(m.get("worker_handoff")))

    with pytest.raises(PlatformError, match="8KB"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=before.bounces.review + 1, gate="review")

    current = kit.store.get_work_item(item.id)
    assert current.delivery_identity == before.delivery_identity and current.worker_handoff is None
    assert current.bounces == before.bounces and current.review_verdict == before.review_verdict


@pytest.mark.parametrize("drift", [False, True])
def test_the_intent_is_durable_before_the_delivery_is_retired_and_resumes_strictly(kit, drift):
    manifest, path, item = rejected(kit, *HANG)
    before = kit.store.get_work_item(item.id)
    kit.monkeypatch.setattr(kit.store, "is_transient_transport_error", lambda exc: "connection reset by peer" in str(exc))
    state = {"lost": False, "crashed": False}
    original = kit.store.update_work_item_metadata

    def interrupt(item_id, **metadata):
        if metadata.get("worker_handoff") and not state["lost"]:
            state["lost"] = True
            original(item_id, **metadata)
            raise PlatformError("connection reset by peer")     # written, but the answer is lost
        if metadata.get("delivery_identity") == {} and not state["crashed"]:
            assert kit.store.get_work_item(item_id).worker_handoff is not None, "the intent must be durable first"
            state["crashed"] = True
            raise RuntimeError("crash before the source delivery is retired")
        return original(item_id, **metadata)

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", interrupt)
    with pytest.raises(RuntimeError, match="before the source delivery is retired"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=before.bounces.review + 1, gate="review")
    staged = kit.store.get_work_item(item.id)
    generation = staged.worker_handoff.generation
    assert staged.delivery_identity == before.delivery_identity and staged.bounces == before.bounces
    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", original)

    if drift:
        kit.distort(kit.store, "observe_work_item_control", lambda projection: replace(projection, work_item=replace(
            projection.work_item, artifacts={**projection.work_item.artifacts, "head_sha": "foreign-head"})), times=100)
        with pytest.raises(PlatformError, match="source.*changed|source.*stale"):
            loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")
    else:
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")
        current = kit.store.get_work_item(item.id)
        assert current.delivery_identity is None and current.worker_handoff.generation == generation
        assert current.bounces.worker == before.bounces.worker and current.bounces.review == before.bounces.review + 1


def test_a_source_that_stays_stale_fails_the_handoff_before_any_write(kit):
    manifest, path, item = rejected(kit, *HANG)
    current = kit.store.get_work_item(item.id)
    stale = WorkItemControlProjection(replace(current, bounces=replace(current.bounces, review=current.bounces.review + 1)))
    kit.distort(kit.store, "observe_work_item_control", lambda _fresh: stale, times=100)
    kit.forbid(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(PlatformError, match="source is stale"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=current.bounces.review + 1,
                                      gate="review", projection=stale)


def test_a_rejection_then_a_pass_converges_with_the_whole_review_history(kit):
    manifest, path, item = rejected(kit, reviews=([{"hang": 3}, kit.stack.review("reject")], [kit.stack.review("pass")]))

    result = kit.settle(manifest, path, timeout=120, retry_limits={"review": 3})

    done = kit.store.get_work_item(item.id)
    assert result.state == "converged" and manifest.nodes["a"].status == "done" and manifest.nodes["a"].merged is True
    assert done.status is WorkItemStatus.DONE and done.bounces.review == 1
    assert [cycle["verdict"] for cycle in done.review_ledger["cycles"]] == ["reject", "pass"]
    assert len({cycle["subject_digest"] for cycle in done.review_ledger["cycles"]}) == 2
    assert len(kit.merges()) == 1


@pytest.mark.parametrize("changed", ["artifacts", "verification"])
def test_a_delivery_that_changes_without_a_causal_run_does_not_replace_the_target_worker(kit, changed):
    manifest, path, item = rejected(kit, *HANG)
    ledger_before = kit.store.get_work_item(item.id).review_ledger
    crash = CHECKPOINTS["bounce"](kit)
    with pytest.raises(RuntimeError, match="crash"):
        kit.tick(manifest, path)
    crash["armed"] = False
    if changed == "artifacts":
        kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/changed"})
    else:
        document = {"commands": [{"cmd": "pytest changed", "exit_code": 0}], "pr_base": "main", "coverage": 91}
        kit.store.update_work_item_metadata(item.id, verification=document, verification_source=json.dumps(document))
    assigns = kit.record(kit.store, "assign_work_item")
    persisted = load_manifest(path)

    result = kit.tick(persisted, path)

    recovered = kit.store.get_work_item(item.id)
    assert result.state == "running" and persisted.nodes["a"].status == "in_progress" and recovered.worker_handoff is not None
    assert recovered.review_ledger == ledger_before and len(workers(assigns)) == 1 and not reviewers(assigns)


def test_an_authoring_recovery_collects_a_submit_on_the_same_head_without_a_worker_retry(kit):
    from omac.core.stage_recovery import prepare_stage_recovery

    manifest, path, item = rejected(kit, [kit.stack.tool("worker-submit", "--no-push")], *HANG)
    prepare_stage_recovery(manifest.nodes["a"], kit.store, "authoring")
    kit.store.update_work_item_metadata(item.id, worker_bounce=15)
    manifest.nodes["a"].status = "todo"
    kit.save(manifest)
    head = kit.store.get_work_item(item.id).artifacts["head_sha"]
    kit.tick(manifest, path, retry_limits={"worker": 15})
    intent = kit.store.get_work_item(item.id).worker_handoff
    assert intent.gate == "explicit-dispatch" and intent.source_review_verdict is None and intent.baseline_verification_attachment_id
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_review", retry_limits={"worker": 15}, what="the submit to be reviewed")

    recovered = kit.store.get_work_item(item.id)
    assert recovered.worker_handoff is None and recovered.delivery_identity.pr_head_sha == head
    assert recovered.verification_ref["attachment_id"] != intent.baseline_verification_attachment_id
    assert recovered.bounces.worker == 15 and len(reviewers(assigns)) == 1 and not workers(assigns)

"""A rejected delivery goes back to the Worker: the handoff, its budgets, and every crash on the way.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
import json
from dataclasses import replace

import pytest

from _support.loopkit import HANG, contract
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase, review_context_binding
from omac.core.workitem import WorkItemPayload, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.dispatch import _previous_review_context

IDLE = []   # a Run that ends at once without submitting anything


def rejected(kit, *rework):
    manifest, path, item = kit.in_review([{"hang": 3}, kit.stack.review("reject")], *HANG, rework=rework)
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

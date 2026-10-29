"""What counts as the Worker's new delivery after a rejection: who submitted it, which head, and reads that lag.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
import json
from dataclasses import replace

import pytest

from _support.loopkit import HANG
from _support.rework import (
    CHECKPOINTS,
    IDLE,
    delivered_again,
    nits_review,
    pushes,
    rejected,
    reviewers,
    slow_submit,
    stale_projection,
    stale_status,
    workers,
)
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemPayload, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop


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
    kit.stack.gh_behavior(checks="fail")          # the reworked delivery fails its checks
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
    from omac.pipeline.stage_recovery import prepare_stage_recovery

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


def test_a_run_of_another_agent_during_the_handoff_breaks_the_causal_chain(kit):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    kit.store.assign_work_item(item.id, "bob", "reviewer")   # the platform starts a Run of an agent the handoff did not name
    kit.wait_for(lambda: len(kit.reviewer_runs(item.id)) >= 2, what="the stranger's Run")
    assigns = kit.record(kit.store, "assign_work_item")

    with pytest.raises(PlatformError, match="non-causal"):
        kit.tick(manifest, path)

    assert not assigns and kit.store.get_work_item(item.id).worker_handoff is not None

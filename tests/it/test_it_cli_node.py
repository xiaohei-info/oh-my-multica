"""`omac node show|retry|accept-nits|accept` on a real Multica.

What a retry carries over from a review (facts to handoff) is a pure table: tests/test_decide_retry.py and
tests/test_decide_recovery.py; the argument handling that needs no platform: tests/test_cli_node.py. Here the platform
proves the state a decision leaves behind, the order of its writes and what a crash in between leaves.
"""
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

import omac.cli.commands.node as node_cmd
from _support.loopkit import HANG
from _support.rework import causal_intent, delayed_verdict, nits_review, rejected
from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import Contract, load_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import dispatch
from omac.pipeline.decide_recovery import reviewer_recovery_decision


def test_accept_nits_waits_for_a_quiet_reviewer_keeps_its_facts_and_a_retry_clears_the_acceptance(kit, node):
    manifest, path, item = nits_review(kit)
    assert kit.tick(manifest, path).state == "needs_decision"
    reviewed = kit.store.get_work_item(item.id)
    kit.store.assign_work_item(item.id, "bob", "reviewer")                  # a Run of the Reviewer starts and stays active
    kit.wait_for(lambda: any(run.active for run in kit.runtime.list_runs(item.id)), what="the active Run")
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")

    code, _, _ = node("accept-nits", path, "a")

    blocked = kit.store.get_work_item(item.id)
    assert code == exit_codes.VALIDATION and writes == []
    assert blocked.status is WorkItemStatus.BLOCKED and blocked.decision_required == reviewed.decision_required
    assert blocked.review_nits_acceptance is None and load_manifest(path).nodes["a"].status == "blocked"

    kit.runtime.cancel(item.id)
    kit.wait_runs_ended(item.id)
    code, payload, _ = node("accept-nits", path, "a")

    accepted = kit.store.get_work_item(item.id)
    assert code == exit_codes.OK and payload["status"] == "in_review"
    assert accepted.status is WorkItemStatus.IN_REVIEW and accepted.phase.value == "review"
    assert (accepted.review_verdict, accepted.review_report, accepted.review_report_ref, accepted.review_subject_digest) == (
        "pass-with-nits", reviewed.review_report, reviewed.review_report_ref, reviewed.review_subject_digest)
    assert not accepted.decision_required
    assert accepted.review_nits_acceptance == {
        "schema": "omac.review-nits-acceptance/v1", "review_subject_digest": reviewed.review_subject_digest,
        "review_report_ref": reviewed.review_report_ref, "verdict": "pass-with-nits"}
    assert load_manifest(path).nodes["a"].status == "in_review"

    writes.clear()
    code, _, _ = node("accept-nits", path, "a")                              # the same decision twice changes nothing
    assert code == exit_codes.OK and [w for w in writes if w[0] == "assign_work_item"] == []
    assert kit.store.get_work_item(item.id).review_nits_acceptance == accepted.review_nits_acceptance

    code, _, _ = node("retry", path, "a")

    retried = kit.store.get_work_item(item.id)
    assert code == exit_codes.OK and retried.review_nits_acceptance is None
    assert (retried.review_verdict, retried.review_report) == (None, None)
    assert retried.phase.value == "authoring" and retried.status is WorkItemStatus.TODO
    assert load_manifest(path).nodes["a"].status == "todo"


# ---- decisions on a node that has an issue (no agent involved) --------------------------------------------------------------

def saved(kit, *nodes, name="decide.yaml"):
    return kit.save(kit.manifest(list(nodes)), name)


def blocked_node(kit, item=None, key="b", **fields):
    node = kit.node(key, worker="bob", **fields)
    node.status, node.work_item_id = "blocked", item.id if item else None
    return node


def test_show_prints_the_evidence_of_the_platform_and_degrades_when_the_issue_cannot_be_read(kit, node):
    pr = f"https://example.test/pr/{uuid.uuid4().hex[:8]}"
    item = kit.item("shown", worker="bob", artifacts={"pr_url": pr}, review_verdict="pass", status=WorkItemStatus.IN_REVIEW)
    path = saved(kit, blocked_node(kit, item))

    code, payload, _ = node("show", path, "b", "--output", "json")

    assert code == exit_codes.OK and payload["evidence"]["work_item_id"] == item.id
    assert payload["evidence"]["artifacts"]["pr_url"] == pr and payload["evidence"]["review_verdict"] == "pass"
    assert payload["evidence"]["platform_status"] == "in_review"

    kit.fault(kit.store, "get_work_item", when="before")
    code, payload, err = node("show", path, "b", "--output", "json")

    assert code == exit_codes.OK and payload["evidence"] is None and "Could not read work-item evidence" in err


def test_retry_keeps_the_id_of_an_issue_that_is_gone_for_the_next_reconcile(kit, node):
    gone = str(uuid.uuid4())
    path = saved(kit, blocked_node(kit, type("Gone", (), {"id": gone})()))

    code, payload, _ = node("retry", path, "b")

    retried = load_manifest(path).nodes["b"]
    assert code == exit_codes.OK and payload["work_item_id"] == gone
    assert (retried.status, retried.work_item_id, retried.recovery_marker) == ("todo", gone, False)


def test_a_replacement_worker_must_be_in_the_agent_pool_of_the_workspace_even_without_a_config_file_pool(kit, node):
    (kit.project.work / ".omac" / "config.yaml").write_text(yaml.safe_dump({
        "engine": "multica", "workspace": kit.workspace, "project": kit.stack.project_id}))
    path = saved(kit, blocked_node(kit))

    code, _, err = node("retry", path, "b", "--worker", "ghost")

    unchanged = load_manifest(path).nodes["b"]
    assert code == exit_codes.VALIDATION and "ghost" in err and (unchanged.worker, unchanged.status) == ("bob", "blocked")

    code, _, _ = node("retry", path, "b", "--worker", "charlie")

    replaced = load_manifest(path).nodes["b"]
    assert code == exit_codes.OK and (replaced.worker, replaced.status) == ("charlie", "todo")


def test_accept_marks_a_planning_node_done_and_a_develop_node_only_after_a_confirmed_merge(kit, node):
    plan = kit.store.create_work_item(kit.workspace, "plan", "Task plan", dag_key="plan", worker="bob", kind=TaskKind.PLAN)
    kit.store.update_work_item_metadata(plan.id, review_verdict="pass-with-nits", decision_required={"verdict": "pass-with-nits"})
    kit.store.update_status(plan.id, WorkItemStatus.BLOCKED)
    follower = kit.node("c", worker="carol", blocked_by=["b"])
    path = saved(kit, blocked_node(kit, plan), follower)

    code, payload, _ = node("accept", path, "b")

    assert code == exit_codes.OK and payload["status"] == "done"
    assert load_manifest(path).nodes["b"].status == "done"
    assert kit.store.get_work_item(plan.id).status is WorkItemStatus.DONE

    open_pr = f"https://example.test/pr/{uuid.uuid4().hex[:8]}"
    merged_pr = f"https://example.test/pr/{uuid.uuid4().hex[:8]}"
    kit.pr(open_pr)
    kit.pr(merged_pr, merged=True, merged_at="2026-07-26T08:00:00Z")
    no_pr = kit.item("develop-without-pr", status=WorkItemStatus.BLOCKED)
    unmerged = kit.item("develop-open-pr", artifacts={"pr_url": open_pr}, status=WorkItemStatus.BLOCKED)
    merged = kit.item("develop-merged-pr", artifacts={"pr_url": merged_pr}, status=WorkItemStatus.BLOCKED)
    claims_merge = blocked_node(kit, unmerged, key="claims")
    claims_merge.merged = True                                    # a merge flag without the authoritative timestamp
    path = saved(kit, blocked_node(kit, no_pr, key="nopr"), blocked_node(kit, unmerged, key="open"), claims_merge,
                 blocked_node(kit, merged, key="merged"), blocked_node(kit, None, key="noissue"), name="develop.yaml")

    refused = {key: node("accept", path, key)[0] for key in ("nopr", "open", "claims", "noissue")}

    assert set(refused.values()) == {exit_codes.VALIDATION}
    on_disk = load_manifest(path).nodes
    assert {key: on_disk[key].status for key in refused} == {key: "blocked" for key in refused}
    assert kit.store.get_work_item(unmerged.id).status is WorkItemStatus.BLOCKED

    code, payload, _ = node("accept", path, "merged")

    accepted = load_manifest(path).nodes["merged"]
    assert code == exit_codes.OK and (accepted.status, accepted.merged, accepted.merged_at) == ("done", True, "2026-07-26T08:00:00Z")
    assert kit.store.get_work_item(merged.id).status is WorkItemStatus.DONE


def test_a_review_retry_without_a_sealed_delivery_is_refused_before_anything_is_written(kit, node):
    item = kit.item("unsealed", worker="bob", reviewer="alice", status=WorkItemStatus.BLOCKED)
    path = saved(kit, blocked_node(kit, item, reviewer="alice"))
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item", "reset_review")

    code, _, err = node("retry", path, "b", "--stage", "review")

    assert code == exit_codes.VALIDATION and "controller-sealed delivery identity" in err and writes == []


# ---- a retry after a rejected review -----------------------------------------------------------------------------------------

def blocked_after_rejection(kit):
    """Node `a` delivered by alice and rejected by bob, then blocked as a Loop that gave up leaves it."""
    manifest, path, item = rejected(kit, *HANG)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest.nodes["a"].status = "blocked"
    kit.save(manifest)
    return manifest, path, kit.store.get_work_item(item.id)


def test_a_retry_after_a_rejection_hands_the_rejected_delivery_to_the_new_worker_and_reviews_its_answer(kit, node):
    manifest, path, item = blocked_after_rejection(kit)
    kit.stack.set_scenario("charlie", [[kit.stack.tool("worker-submit")]])
    kit.project.configure(roles={"workers": ["alice", "bob", "charlie"]})

    code, payload, err = node("retry", path, "a", "--worker", "charlie")
    assert code == exit_codes.OK, err

    retried = kit.store.get_work_item(item.id)
    handoff = retried.worker_handoff
    assert code == exit_codes.OK and (payload["status"], payload["worker"]) == ("todo", "charlie")
    assert (retried.status, retried.phase.value, retried.review_verdict) == (WorkItemStatus.TODO, "authoring", None)
    assert (handoff.gate, handoff.source_review_verdict, handoff.target_worker) == ("operator-retry", "reject", "charlie")
    assert handoff.baseline_pr_head_sha == item.delivery_identity.pr_head_sha
    assert handoff.source_review_feedback["verdict"] == "reject" and handoff.source_review_feedback["report_ref"]
    assert load_manifest(path).nodes["a"].status == "todo"

    manifest = load_manifest(path)
    assigns = kit.record(kit.store, "assign_work_item")
    first = kit.tick(manifest, path, max_parallel=1)

    dispatched = kit.store.get_work_item(item.id)
    assert first.dispatched == ["a"] and dispatched.worker == "charlie" and dispatched.worker_handoff.generation == handoff.generation
    assert [call[1][2] for call in assigns] == ["worker"]

    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_review", what="the answer to be reviewed")

    answered = kit.store.get_work_item(item.id)
    assert answered.worker_handoff is None and answered.delivery_identity.worker == "charlie"
    assert answered.delivery_identity.pr_head_sha != item.delivery_identity.pr_head_sha
    assert [call[1][2] for call in assigns] == ["worker", "reviewer"]


# ---- `retry --stage review` binds the Reviewer Run that became visible late ----------------------------------------------------

def hold_for_the_operator(kit, manifest, path, item, **metadata):
    """The node as an operator finds it: blocked behind the recovery decision, the Run not yet bound."""
    kit.store.update_work_item_metadata(item.id, **metadata)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest.nodes["a"].status, manifest.nodes["a"].recovery_marker = "blocked", False
    kit.save(manifest)


def forbid_dispatch(kit):
    """Binding an existing Run assigns nobody and starts nothing."""
    return (kit.fault(kit.store, "assign_work_item", when="before", error=AssertionError("must not assign")),
            kit.fault(kit.runtime, "wake", when="before", error=AssertionError("must not wake")))


CRASHES = {
    "baseline": lambda kit: kit.fault(kit.store, "update_work_item_metadata", error=RuntimeError("crash at baseline"),
                                      only=lambda _id, **meta: "reviewer_run_baseline" in meta),
    "status": lambda kit: kit.fault(kit.store, "update_status", error=RuntimeError("crash at status"),
                                    only=lambda _id, status: status is WorkItemStatus.IN_REVIEW),
    "manifest": lambda kit: kit.fault(node_cmd, "save_manifest", nth=2, error=RuntimeError("crash at manifest")),   # the first save is the marker
}


def test_a_review_retry_binds_the_submitted_review_to_its_late_run_and_a_crash_at_any_write_is_completed_by_the_next_retry(kit, node):
    manifest, path, item, run = delayed_verdict(kit, "reject")
    reviewed = kit.store.get_work_item(item.id)
    guards = forbid_dispatch(kit)
    unbound = dict(reviewer_run_baseline=replace(reviewed.reviewer_run_baseline, target_run_id=None),
                   decision_required=reviewed.decision_required)

    for checkpoint in (*CRASHES, None):
        hold_for_the_operator(kit, manifest, path, item, **unbound)
        crash = CRASHES[checkpoint](kit) if checkpoint else None
        if crash:
            with pytest.raises(RuntimeError, match=f"crash at {checkpoint}"):
                main(["node", "retry", path, "a", "--stage", "review"])
            crash["armed"] = False
        for _again in range(2 if crash else 1):
            code, _, err = node("retry", path, "a", "--stage", "review")
            assert code == exit_codes.OK, (checkpoint, err)

        bound = kit.store.get_work_item(item.id)
        assert load_manifest(path).nodes["a"].status == "in_review", checkpoint
        assert (bound.status, bound.phase.value, bound.review_subject_digest) == (WorkItemStatus.IN_REVIEW, "review", reviewed.review_subject_digest)
        assert (bound.review_verdict, bound.review_report, bound.review_report_ref) == (
            "reject", reviewed.review_report, reviewed.review_report_ref), checkpoint
        assert bound.decision_required["reason_code"] == "reviewer-run-baseline-unavailable"      # kept until the Loop consumes the verdict
        assert bound.reviewer_run_baseline.target_run_id == run.id, checkpoint

    manifest = load_manifest(path)                               # a fresh controller consumes the reject instead of dispatching a Reviewer
    for guard in guards:
        guard["armed"] = False
    assigns = kit.record(kit.store, "assign_work_item")
    kit.tick(manifest, path, max_parallel=1)

    consumed = kit.store.get_work_item(item.id)
    assert manifest.nodes["a"].status == "in_progress" and consumed.bounces.review == 1 and consumed.phase.value == "authoring"
    assert not [call for call in assigns if call[1][2] == "reviewer"]


def test_a_review_retry_adopts_a_continuation_run_it_can_see_and_redispatches_one_it_cannot(kit, node):
    manifest, path, item = kit.in_review(*HANG)
    run = kit.reviewer_runs(item.id)[0]
    review = kit.store.get_work_item(item.id)
    forbid_dispatch(kit)
    unresolved = dict(
        reviewer_run_baseline=replace(review.reviewer_run_baseline, target_run_id=None, attempt=2),
        decision_required=reviewer_recovery_decision(path, "a", item, "reviewer-run-dispatch-unresolved"))
    hold_for_the_operator(kit, manifest, path, item, **unresolved)

    code, _, err = node("retry", path, "a", "--stage", "review")

    adopted = kit.store.get_work_item(item.id)
    assert code == exit_codes.OK, err
    assert (adopted.status, adopted.phase.value, adopted.review_subject_digest) == (WorkItemStatus.IN_REVIEW, "review", review.review_subject_digest)
    assert adopted.reviewer_run_baseline.target_run_id == run.id and adopted.reviewer_run_baseline.attempt == 2
    assert adopted.decision_required["reason_code"] == "reviewer-run-dispatch-unresolved" and adopted.review_verdict is None

    hold_for_the_operator(kit, manifest, path, item, **unresolved)
    kit.distort(kit.runtime, "list_runs", lambda _runs: [], times=10)                 # the Run is still not visible

    code, _, err = node("retry", path, "a", "--stage", "review")

    reset = kit.store.get_work_item(item.id)
    assert code == exit_codes.OK, err
    assert (reset.status, reset.phase.value, reset.review_verdict, reset.reviewer_run_baseline) == (WorkItemStatus.IN_REVIEW, "review", None, None)
    assert not reset.decision_required and reset.review_subject_digest != review.review_subject_digest
    assert reset.delivery_identity == review.delivery_identity and reset.artifacts == review.artifacts      # the sealed delivery is kept


# ---- a plain retry of a node stuck behind old review facts -------------------------------------------------------------------

def stuck_in_review(kit, key="b"):
    """A rejected review with a stale handoff, a residual Reviewer baseline and a decision, as the operator finds a blocked node."""
    stale = causal_intent(SimpleNamespace(review_subject_digest="old-review-subject"), target_worker="bob")
    item = kit.item("stuck", worker="bob", status=WorkItemStatus.BLOCKED, phase=TaskPhase.REVIEW, review_verdict="reject",
                    review_subject_digest="old-review-subject", worker_handoff=stale, decision_required={"reason_code": "operator-retry"})
    return item, saved(kit, blocked_node(kit, item, key=key))


@pytest.mark.parametrize("replacement", [None, "charlie"])
@pytest.mark.parametrize("checkpoint", ["before_switch", "after_switch"])
def test_a_crash_around_the_switch_to_a_new_authoring_generation_commits_no_half_retry(kit, node, replacement, checkpoint):
    kit.project.configure(roles={"workers": ["alice", "bob", "charlie"]})
    item, path = stuck_in_review(kit)
    command = ["retry", path, "b", *(["--worker", replacement] if replacement else [])]
    crash = kit.fault(kit.store, "restore_authoring_generation", when="before" if checkpoint == "before_switch" else "after",
                      error=RuntimeError("crash at the authoring generation switch"))

    with pytest.raises(RuntimeError, match="authoring generation switch"):
        main(["node", *command])

    interrupted, held = kit.store.get_work_item(item.id), load_manifest(path).nodes["b"]
    assert (held.status, held.worker) == ("blocked", "bob")                      # the platform comes first, the manifest after
    assert (interrupted.worker_handoff is None) is (checkpoint == "after_switch")
    crash["armed"] = False

    assert node(*command)[0] == exit_codes.OK and node(*command)[0] == exit_codes.OK

    recovered, node_after = kit.store.get_work_item(item.id), load_manifest(path).nodes["b"]
    assert (node_after.status, node_after.worker, node_after.recovery_marker) == ("todo", replacement or "bob", False)
    assert recovered.worker_handoff is None and recovered.reviewer_run_baseline is None and recovered.decision_required is None
    assert (recovered.phase.value, recovered.status) == ("authoring", WorkItemStatus.TODO)


@pytest.mark.parametrize("failing", ["restore_authoring_generation", "get_work_item"])
def test_a_retry_that_cannot_reach_the_platform_keeps_the_recovery_marker_and_changes_no_business_field(kit, node, failing):
    kit.project.configure(roles={"workers": ["alice", "bob", "charlie"]})
    item, path = stuck_in_review(kit)
    kit.fault(kit.store, failing, when="before", error=PlatformError("offline"), nth=1)

    code, _, _ = node("retry", path, "b", "--worker", "charlie")

    held = load_manifest(path).nodes["b"]
    assert code == exit_codes.PLATFORM
    assert (held.worker, held.status, held.work_item_id, held.recovery_marker) == ("bob", "blocked", item.id, True)


def test_a_retry_recovers_the_rejection_from_the_review_comments_when_the_issue_no_longer_shows_it(kit, node):
    manifest, path, item = blocked_after_rejection(kit)
    rejected_item = kit.store.get_work_item(item.id)
    kit.store.reset_review(item.id)                                              # the ordinary reject-to-authoring reset clears the live report
    kit.store.update_work_item_metadata(item.id, review_bounce=1)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    cleared = kit.store.get_work_item(item.id)
    assert not cleared.review_report and cleared.review_ledger

    code, _, err = node("retry", path, "a")

    handoff = kit.store.get_work_item(item.id).worker_handoff
    assert code == exit_codes.OK, err
    assert (handoff.gate, handoff.source_review_verdict) == ("operator-retry", "reject")
    assert handoff.source_review_subject_digest == rejected_item.review_subject_digest
    assert handoff.source_review_feedback["report_ref"] == rejected_item.review_report_ref
    assert handoff.source_review_feedback["blockers"] and handoff.baseline_pr_head_sha == rejected_item.delivery_identity.pr_head_sha


def test_a_retry_of_a_delivery_from_before_handoffs_existed_binds_the_old_evidence_to_a_new_worker_generation(kit, node, tmp_path):
    legacy_contract = Contract(objective="fix the rejected delivery", acceptance=["works"], non_goals=["no scope creep"],
                               verification_commands=["pytest -q"], pr_base="main", coverage_gate=0)
    old_pr = f"https://example.test/pr/{uuid.uuid4().hex[:8]}"
    kit.pr(old_pr)
    old_verification = {"commands": [{"cmd": "pytest -q", "exit_code": 0, "business_tests": [
        {"acceptance": "works", "test": "tests/test_delivery.py::test_old"}]}], "integration_gates": [{"name": "smoke", "commands": []}], "pr_base": "main", "coverage": 100}
    blockers = [{"summary": "restore the legacy rework context", "required_fix": "make the new change explicit"}]
    item = kit.item(
        "legacy", worker="alice", reviewer="bob", contract=legacy_contract, status=WorkItemStatus.BLOCKED, artifacts={"pr_url": old_pr},
        verification=old_verification, verification_source=yaml.safe_dump(old_verification), review_report={"blockers": blockers},
        review_report_source=yaml.safe_dump({"blockers": blockers}), phase=TaskPhase.AUTHORING, review_bounce=1,
        review_ledger={"schema": "omac.review-ledger/v1", "cycles": [{"round": 1, "subject_digest": "rejected-subject", "verdict": "reject"}],
                       "blockers": []},
        decision_required={"schema": "omac.decision-required/v1", "reason_code": "legacy-delivery-retry-required"})
    old_attachment = kit.store.get_work_item(item.id).verification_ref["attachment_id"]
    legacy = kit.node("a", reviewer="bob", contract=legacy_contract)
    legacy.status, legacy.work_item_id = "blocked", item.id
    manifest = kit.manifest([legacy])
    path = kit.save(manifest)
    runs_before, assigns = len(kit.runtime.list_runs(item.id)), kit.record(kit.store, "assign_work_item")
    crash = kit.fault(node_cmd, "save_manifest", nth=2, when="before", error=RuntimeError("crash before the retry is saved"))   # the first save is the marker

    with pytest.raises(RuntimeError, match="crash before the retry is saved"):
        main(["node", "retry", path, "a"])

    interrupted = kit.store.get_work_item(item.id)
    assert load_manifest(path).nodes["a"].status == "blocked" and len(kit.runtime.list_runs(item.id)) == runs_before and not assigns
    crash["armed"] = False

    code, _, err = node("retry", path, "a")

    retried = kit.store.get_work_item(item.id)
    handoff = retried.worker_handoff
    assert code == exit_codes.OK, err
    assert (retried.status, retried.phase.value, retried.decision_required) == (WorkItemStatus.TODO, "authoring", None)
    assert handoff.generation == interrupted.worker_handoff.generation and handoff.is_causally_bound()      # the crash left the same intent
    assert handoff.target_worker_bounce == retried.bounces.worker and handoff.baseline_verification_attachment_id == old_attachment
    assert retried.artifacts["pr_url"] == old_pr and retried.verification == old_verification

    manifest, checks = load_manifest(path), len(kit.gh_calls("checks"))
    first = kit.tick(manifest, path, max_parallel=1)

    dispatched = kit.store.get_work_item(item.id).worker_handoff
    assert first.dispatched == ["a"] and (dispatched.generation, dispatched.gate) == (handoff.generation, "operator-retry")
    assert [call[1][2] for call in assigns] == ["worker"]

    fresh = dict(old_verification, commands=[{**old_verification["commands"][0], "business_tests": [
        {"acceptance": "works", "test": "tests/test_delivery.py::test_fresh"}]}])
    verification_file = tmp_path / "verification.yaml"
    verification_file.write_text(yaml.safe_dump(fresh))
    dispatch.submit(kit.store, item.id, code_host=kit.code_host, pr_url=old_pr, verification_file=str(verification_file))   # not made by the Run of the generation

    waiting = kit.tick(manifest, path, max_parallel=1, config={"ci": {"check_command": "gh pr checks {pr_url}"}})

    current = kit.store.get_work_item(item.id)
    assert waiting.state == "running" and manifest.nodes["a"].status == "in_progress" and current.phase is TaskPhase.AUTHORING
    assert current.delivery_identity is None and current.worker_handoff.baseline_pr_head_sha is None
    assert current.worker_handoff.baseline_verification_attachment_id == old_attachment
    assert [call[1][2] for call in assigns] == ["worker"] and len(kit.gh_calls("checks")) == checks


def test_a_retry_that_finds_a_rejection_without_any_context_to_carry_asks_for_a_decision_and_changes_nothing(kit, node):
    item = kit.item("no-context", worker="bob", reviewer="alice", status=WorkItemStatus.BLOCKED,
                    artifacts={"pr_url": f"https://example.test/pr/{uuid.uuid4().hex[:8]}"}, review_verdict="reject")
    path = saved(kit, blocked_node(kit, item, reviewer="alice"))
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item", "reset_review")

    code, _, err = node("retry", path, "b")

    assert code == exit_codes.NEEDS_DECISION and "prior review rework context is unavailable" in err
    assert [call for call in writes if call[0] != "update_work_item_metadata"] == []
    assert kit.store.get_work_item(item.id).status is WorkItemStatus.BLOCKED and load_manifest(path).nodes["b"].status == "blocked"

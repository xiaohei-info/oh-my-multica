"""States the platform and the manifest can disagree about after a rejection, a boundary conflict, and nobody being assigned.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
import json

import pytest

from _support.loopkit import HANG
from _support.rework import boundary_setup, delivered_again, rejected, workers
from omac.core.review_convergence import review_subject_digest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus


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


def test_a_rejection_that_requires_a_downstream_artifact_asks_for_a_decision_instead_of_a_rework(kit):
    manifest, path, item = boundary_setup(
        kit, "Generate production-bundle before tooling can pass.", required_evidence_mode="live",
        required_inputs=[{"artifact_id": "production-bundle", "producer": "assembly", "evidence_mode": "live"}])
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path)

    got = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and got.status is WorkItemStatus.BLOCKED
    assert got.bounces.review == 0 and got.review_verdict == "reject" and not workers(assigns)
    decision = dict(got.decision_required)
    assert decision.pop("review_report_ref") == got.review_report_ref   # the platform pins the exact report that was judged
    assert decision == {
        "schema": "omac.decision-required/v1", "reason_code": "contract-boundary-conflict", "kind": "develop", "phase": "review",
        "gate": "review-boundary", "resume_issue_id": item.id, "node_id": "a",
        "conflict_codes": ["fixture-requires-live-evidence", "review-requires-non-upstream-artifact"],
        "artifact_ids": ["production-bundle"], "producer_nodes": ["assembly"]}


def test_review_prose_that_mentions_a_downstream_artifact_is_an_ordinary_rework(kit):
    manifest, path, item = boundary_setup(kit, "Do not generate production-bundle; only fix the local fixture.")

    result = kit.tick(manifest, path)

    got = kit.store.get_work_item(item.id)
    assert result.state == "running" and manifest.nodes["a"].status == "in_progress" and got.status is WorkItemStatus.IN_PROGRESS
    assert got.review_verdict is None and not got.decision_required and got.bounces.review == 1


def test_a_new_delivery_with_nobody_assigned_prepares_the_review_before_it_assigns_the_reviewer(kit):
    manifest, path, item = delivered_again(kit)
    kit.store.clear_assignment(item.id)
    manifest.nodes["a"].status = "in_progress"
    kit.save(manifest)
    ledger = kit.store.get_work_item(item.id).review_ledger
    events = []

    def reviewer_sees_a_prepared_review(_id, _assignee, role, **_kw):
        if role == "reviewer":
            current = kit.store.get_work_item(item.id)
            assert current.phase is TaskPhase.REVIEW and current.status is WorkItemStatus.IN_REVIEW
            assert current.review_report is None and current.review_ledger == ledger
            events.append("assign")

    kit.before(kit.store, "prepare_review_cycle", lambda *_a, **_k: events.append("prepare"))
    kit.before(kit.store, "assign_work_item", reviewer_sees_a_prepared_review)

    result = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert result.state == "running" and events == ["prepare", "assign"] and manifest.nodes["a"].status == "in_review"
    assert recovered.phase is TaskPhase.REVIEW and recovered.review_verdict is None and recovered.review_report is None
    assert recovered.review_subject_digest == review_subject_digest(recovered, recovered.bounces.review + 1)


def test_a_rejection_with_nobody_assigned_prepares_authoring_before_it_assigns_the_worker(kit):
    manifest, path, item = rejected(kit, *HANG)
    kit.store.clear_assignment(item.id)
    events = []

    def worker_sees_authoring(_id, _assignee, role, **_kw):
        if role == "worker":
            current = kit.store.get_work_item(item.id)
            assert current.phase is TaskPhase.AUTHORING and current.status is WorkItemStatus.IN_PROGRESS and current.review_verdict is None
            events.append("assign")

    kit.before(kit.store, "assign_work_item", worker_sees_authoring)

    result = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert result.state == "running" and events == ["assign"] and manifest.nodes["a"].status == "in_progress"
    assert recovered.review_verdict is None and recovered.bounces.review == 1

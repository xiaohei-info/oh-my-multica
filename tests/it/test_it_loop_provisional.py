"""A node blocked behind a provisional Reviewer decision, or behind another node's decision, while its Run is alive.

The Loop restores the stage only for the one causally bound formal Run; every other shape stays blocked. What counts
as a bound Run is a pure table (tests/test_decide_recovery.py); here the real platform proves the wiring and the crash safety.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from _support.loopkit import HANG
from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemPayload, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.dispatch import build_show_output


def guard(kit):
    """The recovery must bind the existing Run: no new assignment, no wake."""
    return (kit.fault(kit.store, "assign_work_item", when="before", error=AssertionError("must not assign")),
            kit.fault(kit.runtime, "wake", when="before", error=AssertionError("must not wake")))


def provisional(kit, *scenario):
    """Node `a` in review with a live Reviewer Run, blocked behind the provisional baseline-unavailable decision."""
    manifest, path, item = kit.in_review(*(scenario or HANG))
    run = kit.reviewer_runs(item.id)[0]
    current = kit.store.get_work_item(item.id)
    decision = {
        "schema": "omac.decision-required/v1", "reason_code": "reviewer-run-baseline-unavailable", "kind": "develop",
        "phase": "review", "gate": "reviewer", "resume_issue_id": item.id, "node_id": "a",
        "failure_class": "unproven-reviewer-run-causality", "next_action": f"omac node retry {path} a --stage review"}
    kit.store.update_work_item_metadata(
        item.id, reviewer_run_baseline=replace(current.reviewer_run_baseline, target_run_id=None), decision_required=decision)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest.nodes["a"].status = "blocked"
    save_manifest(manifest, path)
    return manifest, path, item, run


def test_a_unique_formal_run_clears_the_provisional_decision_without_a_new_dispatch(kit):
    manifest, path, item, run = provisional(kit)
    hydrated = kit.record(kit.store, "hydrate_work_item_evidence")
    guard(kit)

    result = kit.tick(manifest, path)

    recovered = kit.store.get_work_item(item.id)
    assert result.state == "running" and result.running == ["a"] and result.failed == []
    assert manifest.nodes["a"].status == "in_review" and recovered.status is WorkItemStatus.IN_REVIEW
    assert not recovered.decision_required and recovered.reviewer_run_baseline.target_run_id == run.id
    assert any({WorkItemPayload.VERIFICATION, WorkItemPayload.CONTRACT} <= plan for _n, (_projection, plan), _k in hydrated)

    restarted = load_manifest(path)
    second = kit.tick(restarted, path)

    assert second.state == "running" and restarted.nodes["a"].status == "in_review"
    assert not kit.store.get_work_item(item.id).decision_required


def test_a_tampered_provisional_decision_is_never_cleared_by_a_live_run(kit):
    manifest, path, item, _run = provisional(kit)
    kit.store.update_work_item_metadata(
        item.id, decision_required={**kit.store.get_work_item(item.id).decision_required, "next_action": "wait for product owner"})
    tampered = kit.store.get_work_item(item.id).decision_required
    guard(kit)
    metadata = kit.record(kit.store, "update_work_item_metadata")

    result = kit.tick(manifest, path)

    assert result.state == "needs_decision" and result.failed == ["a"] and result.running == []
    assert manifest.nodes["a"].status == "blocked"
    assert kit.store.get_work_item(item.id).decision_required == tampered
    assert not [m for m in metadata if m[2].get("decision_required") == {}]


def test_a_failed_download_of_the_deferred_evidence_writes_nothing(kit):
    manifest, path, item, _run = provisional(kit)
    decision = kit.store.get_work_item(item.id).decision_required
    before = Path(path).read_bytes()
    metadata = kit.record(kit.store, "update_work_item_metadata")
    unreachable, hydrate = [True], kit.store.hydrate_work_item_evidence

    def download(projection, plan):
        if unreachable[0]:
            raise PlatformError("attachment download failed")
        return hydrate(projection, plan)

    kit.monkeypatch.setattr(kit.store, "hydrate_work_item_evidence", download)
    guard(kit)

    with pytest.raises(PlatformError, match="attachment download failed"):
        kit.tick(manifest, path)

    unreachable[0] = False
    assert manifest.nodes["a"].status == "blocked" and Path(path).read_bytes() == before
    assert kit.store.get_work_item(item.id).decision_required == decision
    assert not [m for m in metadata if m[2].get("decision_required") == {}]


@pytest.mark.parametrize("crash", ["clear-response-unknown", "status-response-unknown", "manifest-save"])
def test_clearing_the_provisional_decision_is_restart_safe(kit, crash):
    manifest, path, item, run = provisional(kit)
    inject = {
        "clear-response-unknown": lambda: kit.fault(
            kit.store, "update_work_item_metadata", when="after", error=PlatformError("decision clear response unknown"),
            only=lambda _id, **kw: kw.get("decision_required") == {}),
        "status-response-unknown": lambda: kit.fault(
            kit.store, "update_status", when="after", error=PlatformError("review status response unknown"),
            only=lambda *a, **kw: WorkItemStatus.IN_REVIEW in (*a, *kw.values())),
        "manifest-save": lambda: kit.fault(
            loop, "save_manifest", when="before", error=RuntimeError("crash before manifest save")),
    }[crash]()
    guard(kit)

    with pytest.raises(PlatformError if crash != "manifest-save" else RuntimeError):
        kit.tick(manifest, path)

    assert inject["fired"] and not kit.store.get_work_item(item.id).decision_required
    persisted = load_manifest(path)
    assert persisted.nodes["a"].status == "blocked"

    inject["armed"] = False
    assigns = kit.record(kit.store, "assign_work_item")
    recovered = kit.tick(persisted, path)

    current = kit.store.get_work_item(item.id)
    assert recovered.state == "running" and persisted.nodes["a"].status == "in_review"
    assert current.reviewer_run_baseline.target_run_id == run.id and not assigns


def test_a_verdict_submitted_after_the_decision_was_cleared_is_collected(kit):
    manifest, path, item, run = provisional(kit, [{"hang": 8}, kit.stack.review("reject")])
    no_assign, no_wake = guard(kit)

    assert kit.tick(manifest, path).state == "running"
    assert not kit.store.get_work_item(item.id).decision_required
    no_assign["armed"] = no_wake["armed"] = False   # the rejection legitimately sends the Worker back to work
    kit.wait_runs_ended(item.id)
    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_progress", what="the rejection to be collected")

    recovered = kit.store.get_work_item(item.id)
    assert recovered.phase is TaskPhase.AUTHORING and recovered.bounces.review == 1 and not recovered.decision_required
    context = build_show_output(recovered, "worker:alice")["context"]
    assert context["previous_review"]["verdict"] == "reject" and context["previous_review"]["blockers"]
    assert context["required_closures"]


# ---- a node whose Run is alive next to a node that needs a decision -----------------------------------------------------

def beside_a_decision(kit, role, *, downstream=False):
    """Node `a` has a live Run for ``role`` but its projection is blocked; node `d` really needs a decision."""
    if role == "worker":
        manifest, path, item = kit.dispatched(wait=False)
    else:
        manifest, path, item = kit.in_review()
    other = kit.item("d", status=WorkItemStatus.BLOCKED)
    node = kit.node("d", blocked_by=["a"] if downstream else None)
    node.status, node.work_item_id = "blocked", other.id
    manifest.nodes["d"] = node
    manifest.nodes["a"].status = "blocked"
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    save_manifest(manifest, path)
    return manifest, path, item


@pytest.mark.parametrize(("role", "downstream", "running"), [
    ("worker", False, "in_progress"), ("reviewer", False, "in_review"), ("worker", True, "in_progress")])
def test_a_formal_active_run_keeps_the_runner_alive_beside_a_blocked_node(kit, role, downstream, running):
    manifest, path, item = beside_a_decision(kit, role, downstream=downstream)
    guard(kit)
    runs = kit.run_ids(item.id)
    before = kit.store.get_work_item(item.id)

    result = kit.tick(manifest, path)

    assert result.state == "running" and result.running == ["a"] and result.failed == ["d"], (
        result, before.reviewer_run_baseline, [(r.id, r.status, r.trigger_kind, r.created_at) for r in kit.runtime.list_runs(item.id)])
    assert manifest.nodes["a"].status == running and manifest.nodes["d"].status == "blocked"
    assert kit.run_ids(item.id) == runs

    restarted = load_manifest(path)
    second = kit.tick(restarted, path)

    assert second.state == "running" and second.running == ["a"] and second.failed == ["d"]
    assert kit.run_ids(item.id) == runs


def test_a_recovered_active_reviewer_that_rejects_hands_the_worker_its_rework_context(kit):
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")]])
    manifest, path, item = kit.in_review([{"hang": 8}, kit.stack.review("reject")])
    other = kit.item("d", status=WorkItemStatus.BLOCKED)
    node = kit.node("d")
    node.status, node.work_item_id = "blocked", other.id
    manifest.nodes["d"], manifest.nodes["a"].status = node, "blocked"
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    save_manifest(manifest, path)
    assigns = kit.record(kit.store, "assign_work_item")

    first = kit.tick(manifest, path)

    assert first.state == "running" and first.running == ["a"] and first.failed == ["d"]
    assert manifest.nodes["a"].status == "in_review" and not [a for a in assigns if a[1][2] == "reviewer"]
    kit.wait_runs_ended(item.id)
    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "in_progress", what="the rejection to be collected")

    recovered = kit.store.get_work_item(item.id)
    assert recovered.phase is TaskPhase.AUTHORING and recovered.bounces.review == 1
    context = build_show_output(recovered, "worker:alice")["context"]
    assert context["previous_review"]["verdict"] == "reject" and context["previous_review"]["blockers"]
    assert context["required_closures"][0]["blocker_id"] == recovered.review_ledger["blockers"][0]["blocker_id"]


def test_restoring_a_worker_stage_validates_the_deferred_review_ledger_before_any_write(kit):
    from test_decide_recovery import INVALID_LEDGERS

    manifest, path, item = beside_a_decision(kit, "worker")
    intent = kit.store.get_work_item(item.id).worker_handoff
    kit.store.update_work_item_metadata(
        item.id, worker_handoff=replace(intent, gate="review", source_review_round=3, target_review_bounce=3))
    before = Path(path).read_bytes()
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")
    guard(kit)

    for name, ledger in INVALID_LEDGERS.items():
        kit.store.update_work_item_metadata(item.id, review_ledger=ledger)
        writes.clear()

        with pytest.raises(PlatformError, match="review ledger"):
            kit.tick(manifest, path)

        assert manifest.nodes["a"].status == "blocked" and Path(path).read_bytes() == before, name
        assert not writes, f"{name}: an invalid ledger must not be followed by any write"

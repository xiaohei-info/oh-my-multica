"""Node lifecycle through the Loop on a real Multica: failure isolation, restarts, the reviewer handoff, reconcile and the evidence gate."""
import uuid

import pytest
import yaml

from _support.deliveries import verification_for
from _support.loopkit import contract, fails
from omac.core.manifest import load_manifest, set_node
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.pipeline import loop

BUSINESS_FAILURE = "business validation failed: the acceptance case is not met"


def submits(kit, *workers):
    for worker in workers:
        kit.stack.set_scenario(worker, [[kit.stack.tool("worker-submit")]])


def failing_dag(kit):
    """a fails for a reason retrying cannot fix; b and c depend on it; d is independent."""
    kit.agents([fails(BUSINESS_FAILURE)], names=("alice",))
    submits(kit, "carol")
    nodes = [kit.node("a", contract=contract()), kit.node("b", worker="bob", blocked_by=["a"], contract=contract()),
             kit.node("c", worker="bob", blocked_by=["b"], contract=contract()), kit.node("d", worker="carol", contract=contract())]
    manifest = kit.manifest(nodes)
    return manifest, kit.save(manifest)


def test_a_failed_node_blocks_its_dependents_but_not_independent_work(kit):
    manifest, path = failing_dag(kit)

    result = kit.settle(manifest, path)

    assert result.state == "needs_decision"
    assert sorted(result.failed) == ["a", "b", "c"] and result.done == ["d"]
    assert [n["key"] for n in result.report["failed_nodes"]] == sorted(result.failed)
    assert result.report["blocked_downstream"]
    failed_a = next(n for n in result.report["failed_nodes"] if n["key"] == "a")
    assert failed_a["decision_required"]["reason_code"] == "nonretryable-runtime-failure"


def test_blocked_nodes_stay_blocked_and_are_never_redispatched(kit):
    manifest, path = failing_dag(kit)
    assert kit.settle(manifest, path).state == "needs_decision"
    kit.forbid(kit.runtime, "wake")
    assigns = kit.record(kit.store, "assign_work_item")

    for _ in range(3):
        again = kit.tick(manifest, path)
        assert sorted(again.failed) == ["a", "b", "c"] and again.state == "needs_decision"
        assert again.dispatched == []

    assert not assigns


def test_a_converged_dag_survives_a_restart_and_a_lost_link_to_a_merged_node(kit):
    submits(kit, "alice", "bob")
    manifest = kit.manifest([kit.node("a", contract=contract()), kit.node("b", worker="bob", blocked_by=["a"], contract=contract())])
    path = kit.save(manifest)
    assert kit.settle(manifest, path).state == "converged"

    restarted = load_manifest(path)
    again = kit.tick(restarted, path)
    assert again.state == "converged" and sorted(again.done) == ["a", "b"]

    set_node(restarted, "a", work_item_id=None)   # the link to a merged node is lost
    lost = kit.tick(restarted, path)
    assert "a" in lost.done and "a" not in lost.dispatched
    assert restarted.nodes["a"].status == "done" and restarted.nodes["a"].work_item_id is None


def test_the_reviewer_handoff_applies_only_to_nodes_with_a_reviewer(kit):
    submits(kit, "alice", "carol")
    kit.stack.set_scenario("bob", [[kit.stack.review("pass")]])
    manifest = kit.manifest([kit.node("plain", contract=contract()),
                             kit.node("reviewed", worker="carol", reviewer="bob", contract=contract())])
    path = kit.save(manifest)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.settle(manifest, path)

    assert result.state == "converged" and sorted(result.done) == ["plain", "reviewed"]
    plain, reviewed = (kit.store.get_work_item(manifest.nodes[key].work_item_id) for key in ("plain", "reviewed"))
    assert plain.phase is TaskPhase.AUTHORING and not plain.review_obligations and plain.review_verdict is None
    assert [a[1][:3] for a in assigns if a[1][2] == "reviewer"] == [(reviewed.id, "bob", "reviewer")]
    assert reviewed.review_obligations and reviewed.review_ledger["cycles"][0]["verdict"] == "pass"


# ---- reconcile ---------------------------------------------------------------------------------------------------

def test_a_running_node_whose_issue_is_done_without_a_pull_request_is_stopped_by_the_evidence_gate(kit):
    item = kit.item("a", contract=contract(), status=WorkItemStatus.DONE, artifacts={})
    node = kit.node("a", contract=contract())
    node.work_item_id, node.status = item.id, "in_progress"
    manifest = kit.manifest([node])
    path = kit.save(manifest)

    result = kit.tick(manifest, path)

    reason = next(n["reason"] for n in result.report["failed_nodes"] if n["key"] == "a")
    assert result.state == "needs_decision" and "a" in result.failed and "pr_url" in reason
    assert any("Evidence gate" in body for body in kit.comments(item.id))


def test_a_coverage_below_the_gate_stops_the_evidence_gate(kit):
    delivery = {**verification_for(contract(), "a"), "coverage": 50}
    item = kit.item("a", contract=contract(), status=WorkItemStatus.DONE, artifacts={"pr_url": "https://example.test/pr/1"},
                    verification=delivery, verification_source=yaml.safe_dump(delivery))
    node = kit.node("a", contract=contract())
    node.work_item_id, node.status = item.id, "in_progress"
    manifest = kit.manifest([node])
    path = kit.save(manifest)

    result = kit.tick(manifest, path)

    reason = next(n["reason"] for n in result.report["failed_nodes"] if n["key"] == "a").lower()
    assert result.state == "needs_decision" and ("coverage" in reason or "below gate" in reason)


def test_a_platform_status_change_of_an_idle_node_is_synced(kit):
    item = kit.item("a", status=WorkItemStatus.DONE)
    node = kit.node("a")
    node.work_item_id, node.status = item.id, "todo"
    manifest = kit.manifest([node])
    path = kit.save(manifest)

    result = kit.tick(manifest, path)

    assert "a" in result.done and result.state == "converged"


@pytest.mark.parametrize(("status", "full_scan"), [("in_progress", False), ("blocked", True)])
def test_a_node_whose_issue_no_longer_exists_is_cleared_and_dispatched_again(kit, status, full_scan):
    """A running link is checked on every tick; a blocked one is found by an explicit full audit."""
    node = kit.node("a")
    gone = str(uuid.uuid4())
    node.work_item_id, node.status = gone, status
    manifest = kit.manifest([node])
    path = kit.save(manifest)

    result = kit.tick(manifest, path, full_scan=full_scan)

    assert "a" in result.dispatched and result.state == "running"
    assert manifest.nodes["a"].work_item_id not in {None, gone}


def test_reconcile_does_not_swallow_programming_errors(kit):
    item = kit.item("a", status=WorkItemStatus.DONE)
    node = kit.node("a")
    node.work_item_id, node.status = item.id, "done"
    manifest = kit.manifest([node])
    path = kit.save(manifest)
    boom = ValueError("bug")
    kit.fault(kit.store, "get_work_item", when="before", error=boom)
    kit.fault(kit.store, "observe_work_item_control", when="before", error=boom)

    with pytest.raises(ValueError, match="bug"):
        loop.reconcile(kit.store, kit.code_host, manifest, path)

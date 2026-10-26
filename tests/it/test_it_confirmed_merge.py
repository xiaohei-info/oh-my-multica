"""A node whose merge is confirmed while its issue is still open, and a delivery that lands during a tick, on a real Multica."""
import pytest

from _support.loopkit import contract
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop

PR = "https://github.com/acme/repo/pull/1"


def review_item(kit, **metadata):
    """Node `a`'s issue: in review, assigned to its Reviewer, as it is when a merge is confirmed before the Loop closes it."""
    item = kit.item("node-a", worker="alice", reviewer="bob", phase=TaskPhase.REVIEW, **metadata)
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    kit.store.assign_work_item(item.id, "bob", "reviewer")
    return kit.store.get_work_item(item.id)


def merged_node(kit, item):
    node = kit.node("node-a", reviewer="bob")
    node.work_item_id, node.status, node.merged, node.merged_at = item.id, "in_progress", True, "2026-07-30T00:00:00Z"
    manifest = kit.manifest([node])
    return manifest, kit.save(manifest)


def test_a_confirmed_merge_closes_the_issue_once_and_starts_no_run(kit):
    item = review_item(kit, artifacts={"pr_url": PR}, verification={"commands": [{"cmd": "pytest -q", "exit_code": 0}]})
    manifest, path = merged_node(kit, item)
    runs_before = kit.run_ids(item.id)
    normalizations = kit.record(kit.store, "normalize_confirmed_merge")

    assert loop.reconcile(kit.store, kit.code_host, manifest, path) is True

    recovered = kit.store.get_work_item(item.id)
    assert manifest.nodes["node-a"].status == "done" and recovered.status is WorkItemStatus.DONE
    assert recovered.platform_assignee_id is None and kit.run_ids(item.id) == runs_before
    assert [call[1][0] for call in normalizations] == [item.id]

    assert loop.reconcile(kit.store, kit.code_host, manifest, path) is False   # nothing left to do
    assert len(normalizations) == 1 and kit.run_ids(item.id) == runs_before


@pytest.mark.parametrize("outcome", ["failed", "unknown-after-commit"])
def test_a_failing_normalization_never_leaves_a_half_closed_issue_or_a_changed_manifest(kit, outcome):
    item = review_item(kit)
    manifest, path = merged_node(kit, item)
    before_file, runs_before = open(path, "rb").read(), kit.run_ids(item.id)
    kit.fault(kit.store, "normalize_confirmed_merge", when="before" if outcome == "failed" else "after",
              error=PlatformError("confirmed merge normalization outcome unknown"))

    with pytest.raises(PlatformError, match="normalization outcome unknown"):
        loop.reconcile(kit.store, kit.code_host, manifest, path)

    current = kit.store.get_work_item(item.id)
    bob = kit.store.resolve_agent_id("bob")
    assert (current.status, current.platform_assignee_id) in {(WorkItemStatus.IN_REVIEW, bob), (WorkItemStatus.DONE, None)}
    assert manifest.nodes["node-a"].status == "in_progress" and open(path, "rb").read() == before_file
    assert kit.run_ids(item.id) == runs_before


def test_a_tick_collects_a_delivery_that_was_persisted_after_its_reconcile_snapshot(kit, tmp_path):
    import sys

    gate = tmp_path / "gate"
    wait_for_gate = {"run": [sys.executable, "-c", f"import os, time\nwhile not os.path.exists({str(gate)!r}): time.sleep(0.05)"]}
    kit.stack.set_scenario("alice", [[wait_for_gate, kit.stack.tool("worker-submit")]])
    kit.agents("hang", names=("bob",))
    manifest = kit.manifest([kit.node("node-a", reviewer="bob", contract=contract())])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)   # dispatches the Worker, whose Run waits at the gate
    item_id = manifest.nodes["node-a"].work_item_id

    def worker_submits(_projection, observed_id):   # the Worker's submit lands right after the Loop looked at the issue
        gate.write_text("open")
        kit.wait_for(lambda: kit.store.get_work_item(observed_id).artifacts, what="the Worker's submit")
    kit.after(kit.store, "observe_work_item_control", worker_submits)

    result = kit.tick(manifest, path, max_parallel=1)

    current = kit.store.get_work_item(item_id)
    assert result.state == "running" and manifest.nodes["node-a"].status == "in_review"
    assert current.phase is TaskPhase.REVIEW and current.status is WorkItemStatus.IN_REVIEW

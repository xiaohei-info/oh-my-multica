"""A failed node blocks what depends on it, except what has already merged."""
from omac.core.manifest import Manifest, Node, save_manifest
from omac.pipeline import loop


def test_failure_cascade_preserves_merged_descendant_and_blocks_unfinished_peer(
    tmp_path,
):
    manifest = Manifest(meta={}, nodes={
        "failed-upstream": Node(
            id="failed-upstream", worker="alice", status="blocked"),
        "merged-descendant": Node(
            id="merged-descendant", worker="bob",
            blocked_by=["failed-upstream"], status="todo",
            merged=True, merged_at="2026-07-27T08:00:00Z"),
        "unfinished-descendant": Node(
            id="unfinished-descendant", worker="charlie",
            blocked_by=["failed-upstream"], status="todo"),
    })
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)

    newly_blocked = loop._mark_downstream_blocked(
        manifest, path, {"failed-upstream"})

    assert manifest.nodes["merged-descendant"].status == "done"
    assert manifest.nodes["merged-descendant"].merged is True
    assert manifest.nodes["unfinished-descendant"].status == "blocked"
    assert newly_blocked == {"unfinished-descendant"}


def test_failure_cascade_leaves_a_branch_that_does_not_depend_on_the_failure_alone(tmp_path):
    manifest = Manifest(meta={}, nodes={
        "failed-upstream": Node(id="failed-upstream", worker="alice", status="blocked"),
        "dependent": Node(id="dependent", worker="bob", blocked_by=["failed-upstream"], status="todo"),
        "other-root": Node(id="other-root", worker="bob", status="todo"),
        "other-child": Node(id="other-child", worker="bob", blocked_by=["other-root"], status="todo"),
    })
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)

    newly_blocked = loop._mark_downstream_blocked(manifest, path, {"failed-upstream"})

    assert newly_blocked == {"dependent"}
    assert manifest.nodes["other-root"].status == manifest.nodes["other-child"].status == "todo"


def _unblock_world(tmp_path, platform_status, decision=None):
    from types import SimpleNamespace
    from omac.core.workitem import WorkItem, WorkItemStatus

    manifest = Manifest(meta={}, nodes={
        "fixed-upstream": Node(id="fixed-upstream", worker="alice", status="done"),
        "waiting": Node(id="waiting", worker="bob", blocked_by=["fixed-upstream"], status="blocked", work_item_id="w-1"),
    })
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    item = WorkItem(id="w-1", workspace_id="ws", title="t", description="", status=WorkItemStatus[platform_status],
                    dag_key="waiting")
    item.decision_required = decision

    class Store:
        def observe_work_item_control(self, item_id):
            return SimpleNamespace(work_item=item)

    return manifest, path, Store()


def test_blocked_node_with_a_work_item_that_was_only_isolated_returns_to_todo_once_upstream_is_satisfied(tmp_path):
    manifest, path, store = _unblock_world(tmp_path, "TODO")

    assert loop._maybe_unblock(store, manifest, path) is True
    assert manifest.nodes["waiting"].status == "todo" and manifest.nodes["waiting"].work_item_id == "w-1"


def test_blocked_node_that_failed_itself_stays_blocked_for_an_explicit_retry(tmp_path):
    for platform_status, decision in (("BLOCKED", None), ("TODO", {"schema": "omac.decision-required/v1", "reason_code": "x"})):
        manifest, path, store = _unblock_world(tmp_path, platform_status, decision)

        assert loop._maybe_unblock(store, manifest, path) is False
        assert manifest.nodes["waiting"].status == "blocked"


def test_upstream_in_bounded_rework_only_keeps_its_downstream_waiting(tmp_path):
    manifest = Manifest(meta={}, nodes={
        "reworked-upstream": Node(id="reworked-upstream", worker="alice", status="in_progress"),
        "dependent": Node(id="dependent", worker="bob", blocked_by=["reworked-upstream"], status="todo"),
    })
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)

    newly_blocked = loop._mark_downstream_blocked(manifest, path, {"reworked-upstream"})

    assert newly_blocked == set()
    assert manifest.nodes["dependent"].status == "todo"


def test_upstream_that_became_blocked_after_rework_budget_cascades(tmp_path):
    manifest = Manifest(meta={}, nodes={
        "spent-upstream": Node(id="spent-upstream", worker="alice", status="blocked"),
        "dependent": Node(id="dependent", worker="bob", blocked_by=["spent-upstream"], status="todo"),
    })
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)

    assert loop._mark_downstream_blocked(manifest, path, {"spent-upstream"}) == {"dependent"}

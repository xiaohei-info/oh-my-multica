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

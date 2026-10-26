"""2.0 state layout: DAG definition file plus one small, hash-guarded state file per node."""
import os

import pytest
import yaml

from omac.core.manifest import (
    Manifest, Node, STATE_FILE_MAX, load_manifest, manifest_files, save_manifest, state_dir,
)
from omac.errors import ValidationError


def _dag(count, name="dag"):
    return Manifest(meta={"name": name}, nodes={
        f"n{i}": Node(id=f"n{i}", worker="alice", blocked_by=[f"n{i - 1}"] if i else [])
        for i in range(count)})


def _inodes(directory):
    return {p.name: p.stat().st_ino for p in directory.rglob("*") if p.is_file()}


def test_first_save_writes_definition_and_one_state_file_per_node(tmp_path):
    path = tmp_path / "dag.yaml"
    save_manifest(_dag(3), str(path))
    assert sorted(p.name for p in (tmp_path / "dag.state").iterdir()) == ["n0.yaml", "n1.yaml", "n2.yaml"]
    definition = yaml.safe_load(path.read_text())
    assert all(set(n) <= {"id", "worker", "blocked_by"} for n in definition["nodes"])


def test_runtime_state_round_trips_through_state_files(tmp_path):
    path = str(tmp_path / "dag.yaml")
    manifest = _dag(2)
    node = manifest.nodes["n1"]
    node.work_item_id, node.status, node.merged, node.merged_at = "42", "done", True, "2026-01-01"
    node.merge_request_state, node.recovery_marker = "requested", True
    save_manifest(manifest, path)
    loaded = load_manifest(path).nodes["n1"]
    assert (loaded.work_item_id, loaded.status, loaded.merged, loaded.merged_at) == ("42", "done", True, "2026-01-01")
    assert (loaded.merge_request_state, loaded.recovery_marker) == ("requested", True)


def test_a_save_rewrites_only_the_files_that_changed(tmp_path):
    path = str(tmp_path / "dag.yaml")
    save_manifest(_dag(5), path)
    manifest = load_manifest(path)
    before = _inodes(tmp_path)
    manifest.nodes["n3"].status = "in_progress"
    save_manifest(manifest, path)
    after = _inodes(tmp_path)
    assert {name for name in after if before.get(name) != after[name]} == {"n3.yaml"}


def test_tick_on_a_500_node_dag_writes_kilobytes_not_the_whole_state(tmp_path):
    path = str(tmp_path / "dag.yaml")
    save_manifest(_dag(500), path)
    manifest = load_manifest(path)
    before = _inodes(tmp_path)
    for key in ("n10", "n11", "n12"):
        manifest.nodes[key].status = "in_progress"
        manifest.nodes[key].work_item_id = key
        save_manifest(manifest, path)
    rewritten = [p for p in tmp_path.rglob("*") if p.is_file() and before.get(p.name) != p.stat().st_ino]
    assert {p.name for p in rewritten} == {"n10.yaml", "n11.yaml", "n12.yaml"}
    assert sum(p.stat().st_size for p in rewritten) < 100 * 1024


def test_a_500_node_dag_with_every_runtime_fact_set_averages_under_4kb_per_node_state_file(tmp_path):
    path = str(tmp_path / "dag.yaml")
    manifest = _dag(500)
    for node in manifest.nodes.values():     # the largest a node's state gets: every runtime fact present
        node.work_item_id, node.status = "01a17891-a4e5-761b-a249-d1e381a4ccf8", "in_review"
        node.merged, node.merged_at, node.merge_request_state, node.recovery_marker = True, "2026-10-26T14:16:15.005519Z", "requested", True
    save_manifest(manifest, path)

    sizes = [p.stat().st_size for p in (tmp_path / "dag.state").iterdir()]

    assert len(sizes) == 500 and sum(sizes) / len(sizes) <= 4 * 1024 and max(sizes) < STATE_FILE_MAX


def test_killed_save_leaves_every_state_file_old_or_new_but_complete(tmp_path, monkeypatch):
    path = str(tmp_path / "dag.yaml")
    save_manifest(_dag(3), path)
    manifest = load_manifest(path)
    for node in manifest.nodes.values():
        node.status = "done"
    real_replace, calls = os.replace, []

    def die_on_second_file(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise KeyboardInterrupt
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", die_on_second_file)
    with pytest.raises(KeyboardInterrupt):
        save_manifest(manifest, path)
    monkeypatch.undo()
    assert list(tmp_path.rglob("*.tmp")) == []
    statuses = sorted(n.status for n in load_manifest(path).nodes.values())
    assert statuses == ["done", "todo", "todo"]


def test_hand_edited_or_truncated_state_file_is_refused_with_a_restore_hint(tmp_path):
    path = str(tmp_path / "dag.yaml")
    save_manifest(_dag(2), path)
    victim = tmp_path / "dag.state" / "n0.yaml"
    victim.write_text(victim.read_text().replace("todo", "done"))
    with pytest.raises(ValidationError, match=r"git checkout -- .*n0\.yaml"):
        load_manifest(path)


def test_state_file_of_a_removed_node_is_deleted(tmp_path):
    path = str(tmp_path / "dag.yaml")
    save_manifest(_dag(3), path)
    manifest = load_manifest(path)
    del manifest.nodes["n2"]
    save_manifest(manifest, path)
    assert not (tmp_path / "dag.state" / "n2.yaml").exists()


def test_definition_with_inline_status_is_accepted_and_moved_into_state_files(tmp_path):
    path = tmp_path / "dag.yaml"
    path.write_text("meta: {}\nnodes:\n  - {id: a, worker: alice, status: blocked}\n")
    manifest = load_manifest(str(path))
    assert manifest.nodes["a"].status == "blocked"
    save_manifest(manifest, str(path))
    assert "status" not in path.read_text()
    assert load_manifest(str(path)).nodes["a"].status == "blocked"


@pytest.mark.parametrize("text", [
    "meta: {}\nnodes:\n  - {id: a, worker: alice, work_item_id: '7', status: done}\n",
    "schema: omac.full-state-transport/v1\nencoding: gzip+base64\ndecoded_sha256: " + "0" * 64 + "\npayload: eA==\n",
])
def test_one_x_single_file_state_is_refused_not_read(tmp_path, text):
    path = tmp_path / "dag.yaml"
    path.write_text(text)
    with pytest.raises(ValidationError, match=r"1\.x.*omac plan create"):
        load_manifest(str(path))
    assert path.read_text() == text


def test_node_id_that_cannot_be_a_file_name_is_refused(tmp_path):
    bad = Manifest(meta={}, nodes={"../x": Node(id="../x", worker="alice")})
    with pytest.raises(ValidationError, match="state file name"):
        save_manifest(bad, str(tmp_path / "dag.yaml"))


def test_manifest_files_lists_the_state_directory_once_it_exists(tmp_path):
    path = str(tmp_path / "dag.yaml")
    assert manifest_files(path) == [path]
    save_manifest(_dag(1), path)
    assert manifest_files(path) == [path, state_dir(path)]


def test_an_oversized_node_state_is_refused_and_the_definition_is_not_written(tmp_path):
    manifest = _dag(2)
    manifest.nodes["n1"].merge_request_state = "x" * (64 * 1024)
    with pytest.raises(ValidationError, match="platform attachments"):
        save_manifest(manifest, str(tmp_path / "dag.yaml"))
    assert not (tmp_path / "dag.yaml").exists()

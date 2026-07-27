"""reconcile 未知结果必须保持 manifest 业务事实原子不变。"""

from types import SimpleNamespace

import pytest

from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import AuthError, PlatformError
from omac.pipeline import loop


def _store() -> MockStore:
    return MockStore(EngineConfig(
        engine_type="mock",
        workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false"},
    ))


def _create_item(store: MockStore, key: str, status: WorkItemStatus) -> str:
    item = store.create_work_item(
        "ws", key, f"work for {key}", dag_key=key, worker="worker")
    store.update_status(item.id, status)
    store.update_work_item_metadata(
        item.id, artifacts={"pr_url": f"https://example.test/{key}"})
    return item.id


@pytest.mark.parametrize("error", [
    PlatformError("platform timeout"),
    AuthError("authentication expired"),
])
@pytest.mark.parametrize("status", [
    "done", "in_review", "todo", "blocked",
])
def test_unknown_work_item_read_preserves_every_manifest_business_state(
    tmp_path, error, status,
):
    store = _store()
    item_id = _create_item(store, "a", WorkItemStatus.DONE)
    manifest = Manifest(meta={}, nodes={
        "a": Node(
            id="a",
            worker="worker",
            work_item_id=item_id,
            status=status,
            merged=status == "done",
            merged_at="2026-07-27T00:00:00Z" if status == "done" else None,
        ),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    before = (tmp_path / "manifest.yaml").read_bytes()
    store.get_work_item = lambda _item_id: (_ for _ in ()).throw(error)

    with pytest.raises(type(error), match=str(error)):
        loop.reconcile(store, manifest, path)

    assert manifest.nodes["a"].status == status
    assert manifest.nodes["a"].work_item_id == item_id
    assert manifest.nodes["a"].merged is (status == "done")
    assert (tmp_path / "manifest.yaml").read_bytes() == before


def test_nth_work_item_read_failure_discards_partial_reconcile_results(tmp_path):
    store = _store()
    ids = {
        "done": _create_item(store, "done", WorkItemStatus.DONE),
        "review": _create_item(store, "review", WorkItemStatus.IN_REVIEW),
        "todo": _create_item(store, "todo", WorkItemStatus.IN_PROGRESS),
        "blocked": _create_item(store, "blocked", WorkItemStatus.BLOCKED),
    }
    manifest = Manifest(meta={"name": "atomic"}, nodes={
        "done": Node(
            id="done", worker="worker", work_item_id=ids["done"],
            status="done", merged=True, merged_at="2026-07-27T00:00:00Z"),
        "review": Node(
            id="review", worker="worker", work_item_id=ids["review"],
            status="in_review"),
        "todo": Node(
            id="todo", worker="worker", work_item_id=ids["todo"],
            status="todo"),
        "blocked": Node(
            id="blocked", worker="worker", work_item_id=ids["blocked"],
            status="blocked"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    before = (tmp_path / "manifest.yaml").read_bytes()
    original_get = store.get_work_item

    def fail_on_fourth(item_id):
        if item_id == ids["blocked"]:
            raise PlatformError("fourth read timed out")
        return original_get(item_id)

    store.get_work_item = fail_on_fourth

    with pytest.raises(PlatformError, match="fourth read timed out"):
        loop.reconcile(store, manifest, path)

    assert [node.status for node in manifest.nodes.values()] == [
        "done", "in_review", "todo", "blocked",
    ]
    assert load_manifest(path).nodes["todo"].status == "todo"
    assert (tmp_path / "manifest.yaml").read_bytes() == before


def test_later_read_failure_prevents_earlier_platform_writes(tmp_path):
    store = _store()
    first_id = _create_item(store, "first", WorkItemStatus.DONE)
    second_id = _create_item(store, "second", WorkItemStatus.IN_PROGRESS)
    store.update_work_item_metadata(first_id, artifacts={})
    manifest = Manifest(meta={}, nodes={
        "first": Node(
            id="first", worker="worker", work_item_id=first_id,
            status="done"),
        "second": Node(
            id="second", worker="worker", work_item_id=second_id,
            status="todo"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    original_get = store.get_work_item

    def fail_after_first_node(item_id):
        if item_id == second_id:
            raise PlatformError("second read timed out")
        return original_get(item_id)

    store.get_work_item = fail_after_first_node

    with pytest.raises(PlatformError, match="second read timed out"):
        loop.reconcile(store, manifest, path)

    first = original_get(first_id)
    assert first.status is WorkItemStatus.DONE
    assert store.get_comments(first_id) == []
    assert manifest.nodes["first"].status == "done"
    assert load_manifest(path).nodes["first"].status == "done"


@pytest.mark.parametrize("error", [
    PlatformError("pull request observation timed out"),
    AuthError("pull request authentication expired"),
])
def test_unknown_pull_request_observation_preserves_manifest(tmp_path, error):
    store = _store()
    item_id = _create_item(store, "a", WorkItemStatus.DONE)
    manifest = Manifest(meta={}, nodes={
        "a": Node(
            id="a", worker="worker", work_item_id=item_id, status="done",
            merge_request_state="requested"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    before = (tmp_path / "manifest.yaml").read_bytes()
    store.observe_pull_request = lambda _url: (_ for _ in ()).throw(error)

    with pytest.raises(type(error), match=str(error)):
        loop.reconcile(store, manifest, path)

    assert manifest.nodes["a"].status == "done"
    assert manifest.nodes["a"].merge_request_state == "requested"
    assert store.get_work_item(item_id).status is WorkItemStatus.DONE
    assert (tmp_path / "manifest.yaml").read_bytes() == before


def test_work_item_not_found_keeps_existing_recovery_semantics(tmp_path):
    store = _store()
    manifest = Manifest(meta={}, nodes={
        "a": Node(
            id="a", worker="worker", work_item_id="missing",
            status="in_progress"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)

    assert loop.reconcile(store, manifest, path) is True

    assert manifest.nodes["a"].status == "todo"
    assert manifest.nodes["a"].work_item_id is None
    persisted = load_manifest(path).nodes["a"]
    assert persisted.status == "todo"
    assert persisted.work_item_id is None


@pytest.mark.parametrize(("error", "expected_code"), [
    (PlatformError("platform timeout"), exit_codes.PLATFORM),
    (AuthError("authentication expired"), exit_codes.AUTH),
])
@pytest.mark.parametrize("action", ["tick", "status"])
def test_online_dag_commands_map_reconcile_error_without_manifest_sync_commit(
    tmp_path, monkeypatch, error, expected_code, action,
):
    store = _store()
    item_id = _create_item(store, "a", WorkItemStatus.IN_PROGRESS)
    manifest = Manifest(meta={}, nodes={
        "a": Node(
            id="a", worker="worker", work_item_id=item_id, status="todo"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    before = (tmp_path / "manifest.yaml").read_bytes()
    store.get_work_item = lambda _item_id: (_ for _ in ()).throw(error)
    engine = SimpleNamespace(store=store, runtime=MockRuntime(store))

    from omac.cli.commands import dag

    monkeypatch.setattr(dag, "_assemble_engine", lambda _args: (engine, store.config))
    monkeypatch.setattr(dag, "ensure_config_synced", lambda *args, **kwargs: None)
    monkeypatch.setattr(dag, "load_config", lambda _path: {})
    monkeypatch.setattr(dag, "_load_config_for_manifest", lambda _path: {})
    sync_commits = []
    monkeypatch.setattr(
        loop, "commit_manifest", lambda *args, **kwargs: sync_commits.append(args))

    code = main(["dag", action, path, "--output", "json"])

    assert code == expected_code
    assert sync_commits == []
    assert (tmp_path / "manifest.yaml").read_bytes() == before
    assert manifest.nodes["a"].status == "todo"

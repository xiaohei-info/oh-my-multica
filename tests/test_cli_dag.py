"""P1.5 dag status:reconcile + 快照,json schema 固定。

测试三层:
1. report schema 字段名/嵌套结构锁定(单一 schema 模块,P5 web 与 agent 共消费)
2. build_status_report 内容断言:mixed 状态节点、reconcile 同步、needs_decision
3. CLI dag status:退出码恒 0、table/json 输出、--output json 可解析
"""
import json
import os

import pytest

from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.engines.mock import MockStore, MockRuntime
from omac.core.manifest import load_manifest, save_manifest, Node, Manifest
from omac.pipeline.loop import reconcile
from omac.pipeline.report import (
    build_status_report,
    render_table,
    STATUS_REPORT_KEYS,
    PROGRESS_KEYS,
    NODE_KEYS,
    NEEDS_DECISION_KEYS,
)


# ==================== fixtures ====================

def _engine_config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="ws", extra=base)


def _mock_store():
    return MockStore(_engine_config())


def _manifest_yaml(tmp_path, nodes):
    """Write a manifest YAML from a list of node dicts."""
    import yaml
    data = {"meta": {"name": "test-dag"}, "nodes": nodes}
    path = tmp_path / "dag.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return str(path)


def _mixed_manifest(tmp_path):
    """Manifest with nodes in various states; work_item_ids use 1,2,3 (mock order)."""
    return _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "done", "work_item_id": "1"},
        {"id": "b", "worker": "bob", "status": "in_progress", "work_item_id": "2", "blocked_by": ["a"]},
        {"id": "c", "worker": "charlie", "status": "todo"},
        {"id": "d", "worker": "alice", "status": "blocked", "work_item_id": "3", "blocked_by": ["b"]},
        {"id": "e", "worker": "bob", "status": "done"},
        {"id": "f", "worker": "charlie", "status": "abandoned"},
    ])


def _populate_store(store):
    """Populate mock store with work items for dag a/b/d (IDs 1/2/3)."""
    # item 1 — done with pr_url
    store.create_work_item("ws", "A", "d", dag_key="a", worker="alice")
    store.update_status("1", WorkItemStatus.DONE)
    store.update_work_item_metadata("1", artifacts={"pr_url": "https://pr/1"})

    # item 2 — in_progress
    store.create_work_item("ws", "B", "d", dag_key="b", worker="bob")
    store.update_status("2", WorkItemStatus.IN_PROGRESS)

    # item 3 — blockd (failed on platform)
    store.create_work_item("ws", "D", "d", dag_key="d", worker="alice")
    store.update_status("3", WorkItemStatus.BLOCKED)
    store.update_work_item_metadata(
        "3", review_verdict="reject", review_comment="tests missing")


class TestSchemaLock:
    """字段名/嵌套结构锁定 — schema 变更即测试失败。"""

    def test_top_level_keys(self):
        assert STATUS_REPORT_KEYS == ("manifest", "progress", "nodes", "needs_decision")

    def test_progress_keys(self):
        assert PROGRESS_KEYS == (
            "total", "done", "running", "todo", "blocked",
            "failed", "abandoned", "converged")

    def test_node_keys(self):
        assert NODE_KEYS == (
            "key", "status", "worker", "reviewer", "work_item_id",
            "pr_url", "blocked_by")

    def test_needs_decision_keys(self):
        assert NEEDS_DECISION_KEYS == (
            "failed_nodes", "blocked_downstream", "next_actions")

    def test_report_has_exactly_top_level_keys(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)
        assert set(report.keys()) == set(STATUS_REPORT_KEYS)

    def test_progress_has_exactly_locked_keys(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)
        assert set(report["progress"].keys()) == set(PROGRESS_KEYS)

    def test_node_has_exactly_locked_keys(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)
        for node in report["nodes"]:
            assert set(node.keys()) == set(NODE_KEYS)

    def test_needs_decision_has_exactly_locked_keys(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)
        nd = report["needs_decision"]
        assert nd is not None
        assert set(nd.keys()) == set(NEEDS_DECISION_KEYS)


# ==================== build_status_report content tests ====================

class TestBuildStatusReport:
    """reconcile + 快照内容断言。"""

    def test_reconcile_syncs_platform_status_to_manifest(self, tmp_path):
        """manifest says todo, platform says done → manifest synced to done."""
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "todo", "work_item_id": "1"},
        ])
        manifest = load_manifest(path)
        store = _mock_store()
        store.create_work_item("ws", "A", "d", dag_key="a", worker="alice")
        store.update_status("1", WorkItemStatus.DONE)
        store.update_work_item_metadata("1", artifacts={"pr_url": "https://pr/1"})

        build_status_report(manifest, store, path)

        reloaded = load_manifest(path)
        assert reloaded.nodes["a"].status == "done"

    def test_reconcile_clears_missing_work_item_id(self, tmp_path):
        """work_item_id points to nonexistent item → cleared, status → todo."""
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "in_progress", "work_item_id": "999"},
        ])
        manifest = load_manifest(path)
        store = _mock_store()

        build_status_report(manifest, store, path)

        reloaded = load_manifest(path)
        assert reloaded.nodes["a"].work_item_id is None
        assert reloaded.nodes["a"].status == "todo"

    def test_progress_counts_mixed_states(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)

        p = report["progress"]
        assert p["total"] == 6
        assert p["done"] == 2      # a, e
        assert p["running"] == 1   # b (in_progress)
        assert p["todo"] == 1      # c
        assert p["blocked"] == 1   # d
        assert p["abandoned"] == 1 # f
        assert p["failed"] == 0
        assert p["converged"] is False

    def test_converged_when_all_done(self, tmp_path):
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "done"},
            {"id": "b", "worker": "bob", "status": "done", "blocked_by": ["a"]},
        ])
        manifest = load_manifest(path)
        store = _mock_store()
        report = build_status_report(manifest, store, path)
        assert report["progress"]["converged"] is True
        assert report["needs_decision"] is None

    def test_node_table_fields(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)

        nodes = {n["key"]: n for n in report["nodes"]}
        # done node with pr_url from platform
        assert nodes["a"]["status"] == "done"
        assert nodes["a"]["work_item_id"] == "1"
        assert nodes["a"]["pr_url"] == "https://pr/1"
        assert nodes["a"]["worker"] == "alice"
        # todo node without work_item_id
        assert nodes["c"]["status"] == "todo"
        assert nodes["c"]["work_item_id"] is None
        assert nodes["c"]["pr_url"] is None
        # blocked_by preserved
        assert nodes["d"]["blocked_by"] == ["b"]

    def test_needs_decision_failed_nodes(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)

        nd = report["needs_decision"]
        assert nd is not None
        failed_keys = {fn["key"] for fn in nd["failed_nodes"]}
        assert "d" in failed_keys
        # blocked downstream of d
        # d has no downstream in this manifest, but the set should exist
        assert isinstance(nd["blocked_downstream"], list)
        # next actions contain executable commands
        assert len(nd["next_actions"]) > 0
        for action in nd["next_actions"]:
            assert action.startswith("omac node ")

    def test_needs_decision_blocked_downstream(self, tmp_path):
        """A failed node's downstream appears in blocked_downstream."""
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "failed", "work_item_id": "1"},
            {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]},
            {"id": "c", "worker": "charlie", "status": "todo", "blocked_by": ["b"]},
        ])
        manifest = load_manifest(path)
        store = _mock_store()
        store.create_work_item("ws", "A", "d", dag_key="a", worker="alice")
        store.update_status("1", WorkItemStatus.FAILED)

        report = build_status_report(manifest, store, path)
        nd = report["needs_decision"]
        assert nd is not None
        assert "b" in nd["blocked_downstream"]
        assert "c" in nd["blocked_downstream"]

    def test_needs_decision_null_when_no_failures(self, tmp_path):
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "done"},
            {"id": "b", "worker": "bob", "status": "in_progress", "work_item_id": "1"},
        ])
        manifest = load_manifest(path)
        store = _mock_store()
        store.create_work_item("ws", "B", "d", dag_key="b", worker="bob")
        store.update_status("1", WorkItemStatus.IN_PROGRESS)

        report = build_status_report(manifest, store, path)
        assert report["needs_decision"] is None


# ==================== table rendering tests ====================

class TestRenderTable:
    def test_table_contains_headers_and_keys(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)
        table = render_table(report)

        for key in ("KEY", "STATUS", "WORKER"):
            assert key in table
        for nkey in ("a", "b", "c", "d", "e", "f"):
            assert nkey in table

    def test_table_includes_progress_line(self, tmp_path):
        path = _mixed_manifest(tmp_path)
        manifest = load_manifest(path)
        store = _mock_store()
        _populate_store(store)
        report = build_status_report(manifest, store, path)
        table = render_table(report)
        assert "done" in table.lower() or "/" in table  # progress x/y


# ==================== CLI tests ====================

class TestDagStatusCLI:
    """dag status 命令:退出码恒 0、输出格式。"""

    def _write_config(self, tmp_path):
        import yaml
        cfg_dir = tmp_path / ".omac"
        cfg_dir.mkdir(exist_ok=True)
        with open(cfg_dir / "config.yaml", "w") as f:
            yaml.dump({"engine": "mock", "workspace": "ws"}, f)

    def test_exit_code_always_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._write_config(tmp_path)
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "done"},
            {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]},
        ])
        assert main(["dag", "status", path]) == exit_codes.OK

    def test_json_output_parseable(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._write_config(tmp_path)
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "done"},
            {"id": "b", "worker": "bob", "status": "blocked"},
        ])
        assert main(["dag", "status", path, "--output", "json"]) == exit_codes.OK
        out = capsys.readouterr().out
        data = json.loads(out)
        assert set(data.keys()) == set(STATUS_REPORT_KEYS)
        assert data["progress"]["total"] == 2
        assert data["progress"]["done"] == 1
        assert data["progress"]["blocked"] == 1
        assert data["needs_decision"] is not None

    def test_table_output_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._write_config(tmp_path)
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "done"},
        ])
        assert main(["dag", "status", path]) == exit_codes.OK
        out = capsys.readouterr().out
        # table (not JSON)
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)
        assert "a" in out

    def test_status_with_engine_workspace_flags(self, tmp_path, monkeypatch, capsys):
        """--engine/--workspace flags override config."""
        monkeypatch.chdir(tmp_path)
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "done"},
        ])
        code = main(["dag", "status", path, "--engine", "mock",
                      "--workspace", "ws", "--output", "json"])
        assert code == exit_codes.OK
        data = json.loads(capsys.readouterr().out)
        assert data["progress"]["done"] == 1

    def test_status_manifest_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._write_config(tmp_path)
        code = main(["dag", "status", str(tmp_path / "nope.yaml")])
        assert code == exit_codes.VALIDATION

"""P1.5 cross-check: tick() exit-20 report shares /status needs_decision schema.

Reviewer request: assert tick(...).report keys == NEEDS_DECISION_KEYS and is
structurally isomorphic with build_status_report(...)["needs_decision"]
—— /status and exit-20 draw from a single schema module.
"""
import os

import yaml

from omac.engines.models import WorkItemStatus
from omac.pipeline.loop import tick


class TestNeedsDecisionContract:
    """tick() exit-20 report must be isomorphic with /status needs_decision schema."""

    def test_tick_report_keys_match_needs_decision_keys(self, tmp_path):
        """tick() 进入 needs_decision 时,report 键集 == NEEDS_DECISION_KEYS。"""
        path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "in_progress", "work_item_id": "1"},
            {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]},
        ])
        manifest = load_manifest(path)
        store = _mock_store()
        store.create_work_item("ws", "A", "d", dag_key="a", worker="alice")
        store.update_status("1", WorkItemStatus.FAILED)

        result = tick(store, MockRuntime(store), manifest, path)

        assert result.state == "needs_decision"
        assert set(result.report.keys()) == set(NEEDS_DECISION_KEYS)
        failed_keys = {n["key"] for n in result.report["failed_nodes"]}
        assert "a" in failed_keys
        # blocked downstream of a (b) present
        assert "b" in result.report["blocked_downstream"]

    def test_tick_report_isomorphic_with_status_report(self, tmp_path):
        """tick() exit-20 报告与 /status needs_decision 同构(同一 schema 模块)。

        build_status_report 是"观测"(只 reconcile,不推进),而 tick 会先把失败节点
        的下游标为 failed 再落盘。因此真正的同构要在 tick 落盘后的 manifest 上再跑
        一次 build_status_report——两者此时面对同一 failed set,输出的 needs_decision
        必须完全相等,从而证明共用单一 schema 模块(字段与结构一致)。
        """
        spec = [
            {"id": "a", "worker": "alice", "status": "in_progress", "work_item_id": "1"},
            {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]},
            {"id": "c", "worker": "charlie", "status": "todo", "blocked_by": ["b"]},
        ]

        path = _manifest_yaml(tmp_path, spec)
        manifest = load_manifest(path)

        # 路径 A:tick() 推演 —— a 在工作台 FAILED,collect_results 把 a 转 blocked,
        # 下游 b,c 标 failed,落盘后 manifest 含完整失败拓扑。
        store = _mock_store()
        store.create_work_item("ws", "A", "d", dag_key="a", worker="alice")
        store.update_status("1", WorkItemStatus.FAILED)
        result = tick(store, MockRuntime(store), manifest, path)
        assert result.state == "needs_decision"
        failed_keys = sorted(n["key"] for n in result.report["failed_nodes"])

        # 路径 B:在 tick 落盘后的同一 manifest 上观测 build_status_report
        manifest_after = load_manifest(path)
        store_obs = _mock_store()
        store_obs.create_work_item("ws", "A", "d", dag_key="a", worker="alice")
        store_obs.update_status("1", WorkItemStatus.FAILED)
        report_b = build_status_report(manifest_after, store_obs, path)
        nd_b = report_b["needs_decision"]
        assert nd_b is not None

        # 同构:顶层键集 == NEEDS_DECISION_KEYS (单一 schema 模块)
        assert set(result.report.keys()) == set(NEEDS_DECISION_KEYS)
        assert set(nd_b.keys()) == set(NEEDS_DECISION_KEYS)

        # failed_nodes 键集 + blocked_downstream + next_actions 完全一致
        status_failed = sorted(n["key"] for n in nd_b["failed_nodes"])
        assert failed_keys == status_failed
        assert result.report["blocked_downstream"] == nd_b["blocked_downstream"]
        assert result.report["next_actions"] == nd_b["next_actions"]
        # 每个 failed_node 字段集相同(对象结构一致)
        by_key_tick = {n["key"]: n for n in result.report["failed_nodes"]}
        by_key_status = {n["key"]: n for n in nd_b["failed_nodes"]}
        for key in by_key_status:
            assert set(by_key_tick[key].keys()) == set(by_key_status[key].keys())
        # next_actions 都是可执行的 omac node 命令
        assert all(a.startswith("omac node ") for a in nd_b["next_actions"])
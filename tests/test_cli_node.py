"""cli.node: show / retry / abandon —— exit 20 后的显式决策工具(§7.5)。"""
import os

import pytest
import yaml

from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import Contract, load_manifest, save_manifest, Manifest, Node


def _write_manifest(tmp_path, nodes_yaml):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump({"meta": {}, "nodes": nodes_yaml}, allow_unicode=True))
    return str(p)


def _basic_nodes():
    return [
        {"id": "a", "worker": "alice", "status": "done",
         "work_item_id": "1"},
        {"id": "b", "worker": "bob", "blocked_by": ["a"], "status": "blocked",
         "work_item_id": "2",
         "contract": {"objective": "do b", "acceptance": ["b works"],
                      "verification_commands": ["pytest -q"],
                      "pr_base": "main", "coverage_gate": 90}},
        {"id": "c", "worker": "charlie", "blocked_by": ["b"], "status": "todo"},
    ]


# ---------------- show ----------------

def test_show_missing_manifest_is_validation(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main(["node", "show", "nope.yaml", "a"])
    assert code == exit_codes.VALIDATION
    assert "manifest 文件不存在" in capsys.readouterr().err


def test_show_unknown_node_is_validation(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    code = main(["node", "show", path, "ghost"])
    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "ghost" in err and "a" in err  # 报错即教学:列出可用节点


def test_show_json_contains_contract_and_evidence_fields(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 无引擎配置 → 降级 contract-only,evidence=null
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "show", path, "b", "--output", "json"]) == exit_codes.OK
    out = capsys.readouterr().out
    import json
    payload = json.loads(out)
    assert payload["node_key"] == "b"
    assert payload["status"] == "blocked"
    assert payload["contract"]["objective"] == "do b"
    assert payload["contract"]["acceptance"] == ["b works"]
    assert payload["contract"]["verification_commands"] == ["pytest -q"]
    assert payload["contract"]["coverage_gate"] == 90
    assert "evidence" in payload
    assert payload["rollback_count"] == 0


def test_show_reads_evidence_from_mock_engine(tmp_path, capsys, monkeypatch):
    """有 work_item_id 且引擎可解析时,show 从 store.get_work_item 取证据链。

    mock 引擎是内存态:注入同一个 store 实例,验证 show 的读证据逻辑。
    """
    import json
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    engine = create_engine("mock", EngineConfig("mock", "ws-1",
                                                extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    item.artifacts = {"pr_url": "https://mock.example.com/pr/x"}
    item.verification = {"commands": [{"cmd": "pytest -q", "exit_code": 0}]}
    item.review_verdict = "pass"

    # 让 node show 用同一个内存 store(模拟 multica 持久化)
    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)

    nodes = [{"id": "b", "worker": "bob", "status": "in_review",
              "work_item_id": item.id}]
    path = _write_manifest(tmp_path, nodes)

    assert main(["node", "show", path, "b", "--output", "json"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence"] is not None
    assert payload["evidence"]["work_item_id"] == item.id
    assert payload["evidence"]["artifacts"]["pr_url"] == "https://mock.example.com/pr/x"
    assert payload["evidence"]["review_verdict"] == "pass"


def test_show_degrades_when_engine_unresolvable(tmp_path, capsys, monkeypatch):
    """无引擎配置 + 无 env:show 降级为 contract-only,evidence=null,不报错。"""
    import json
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMAC_ENGINE", raising=False)
    monkeypatch.delenv("OMAC_WORKSPACE_ID", raising=False)
    path = _write_manifest(tmp_path, [
        {"id": "b", "worker": "bob", "status": "blocked", "work_item_id": "9",
         "contract": {"objective": "x", "acceptance": [], "verification_commands": []}}])
    assert main(["node", "show", path, "b", "--output", "json"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence"] is None
    assert payload["contract"]["objective"] == "x"


# ---------------- retry ----------------

def test_retry_resets_to_todo_and_keeps_work_item_id(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    m = load_manifest(path)
    assert m.nodes["b"].status == "todo"
    assert m.nodes["b"].work_item_id == "2"   # 保留
    assert m.nodes["b"].worker == "bob"        # 未改派


def test_retry_reassign_worker_validated_against_config(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # config.roles.workers 提供 agent 池
    main(["config", "set", "roles.workers", '["alice", "bob", "dave"]'])
    capsys.readouterr()
    path = _write_manifest(tmp_path, _basic_nodes())

    # 非法 worker → exit 5
    assert main(["node", "retry", path, "b", "--worker", "ghost"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "ghost" in err

    # 合法 worker → 生效
    assert main(["node", "retry", path, "b", "--worker", "dave"]) == exit_codes.OK
    capsys.readouterr()
    m = load_manifest(path)
    assert m.nodes["b"].worker == "dave"
    assert m.nodes["b"].status == "todo"


def test_retry_hints_rerun(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    main(["node", "retry", path, "b"])
    assert "dag run" in capsys.readouterr().err


# ---------------- abandon ----------------

def test_abandon_marks_abandoned_and_unlocks_downstream(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "abandon", path, "b"]) == exit_codes.OK
    m = load_manifest(path)
    assert m.nodes["b"].status == "abandoned"


def test_abandon_downstream_becomes_ready(tmp_path, monkeypatch):
    """abandon 后下游在下轮 tick 进入就绪集(graph 层语义)。"""
    from omac.core.graph import ready_nodes
    issues = {
        "a": {"status": "done", "blocked_by": []},
        "b": {"status": "abandoned", "blocked_by": ["a"]},
        "c": {"status": "todo", "blocked_by": ["b"]},
    }
    assert ready_nodes(issues) == ["c"]


def test_abandon_reports_affected_downstream(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    main(["node", "abandon", path, "a"])
    import json
    payload = json.loads(capsys.readouterr().out)
    # a 的传递下游:b、c
    assert "b" in payload["affected_downstream"]
    assert "c" in payload["affected_downstream"]

"""cli.node: argument handling and the decisions that need no platform (§7.5).

With no engine configured `show` degrades to the contract and `retry`/`abandon` only rewrite the manifest. What the
commands leave on a real platform (evidence, handoffs, crash safety, acceptance): tests/it/test_it_cli_node.py; what a
retry carries from a review: tests/test_decide_retry.py and tests/test_decide_recovery.py.
"""
import json

import pytest
from _support.state import save_literal

from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.graph import ready_nodes
from omac.core.manifest import load_manifest, manifest_write_lock


@pytest.fixture(autouse=True)
def no_engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("OMAC_ENGINE", "OMAC_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)


def _write_manifest(tmp_path, nodes_yaml):
    return save_literal(tmp_path / "m.yaml", {"meta": {}, "nodes": nodes_yaml})


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

def test_show_missing_manifest_is_validation(capsys):
    code = main(["node", "show", "nope.yaml", "a"])
    assert code == exit_codes.VALIDATION
    assert "Manifest file not found" in capsys.readouterr().err


def test_show_unknown_node_is_validation(tmp_path, capsys):
    path = _write_manifest(tmp_path, _basic_nodes())
    code = main(["node", "show", path, "ghost"])
    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "ghost" in err and "a" in err  # 报错即教学:列出可用节点


def test_show_json_contains_contract_and_evidence_fields(tmp_path, capsys):
    # 无引擎配置 → 降级 contract-only,evidence=null
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "show", path, "b", "--output", "json"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["node_key"] == "b"
    assert payload["status"] == "blocked"
    assert payload["contract"]["objective"] == "do b"
    assert payload["contract"]["acceptance"] == ["b works"]
    assert payload["contract"]["verification_commands"] == ["pytest -q"]
    assert payload["contract"]["coverage_gate"] == 90
    assert payload["evidence"] is None
    assert payload["rollback_count"] == 0


# ---------------- retry ----------------

def test_retry_resets_to_todo_and_keeps_work_item_id(tmp_path):
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    m = load_manifest(path)
    assert m.nodes["b"].status == "todo"
    assert m.nodes["b"].work_item_id == "2"   # 保留
    assert m.nodes["b"].worker == "bob"        # 未改派


def test_retry_explicitly_clears_confirmed_merge_closure(tmp_path):
    path = _write_manifest(tmp_path, [{
        "id": "merged-node",
        "worker": "bob",
        "status": "done",
        "work_item_id": "issue-merged",
        "merged": True,
        "merged_at": "2026-07-30T00:00:00Z",
    }])

    assert main(["node", "retry", path, "merged-node"]) == exit_codes.OK

    node = load_manifest(path).nodes["merged-node"]
    assert node.status == "todo"
    assert node.merged is False
    assert node.merged_at is None
    assert node.merge_request_state is None


def test_retry_reassign_worker_validated_against_config(tmp_path, capsys):
    # config.roles.workers 提供 agent 池
    main(["config", "set", "roles.workers", '["alice", "bob", "dave"]'])
    capsys.readouterr()
    path = _write_manifest(tmp_path, _basic_nodes())

    # 非法 worker → exit 5
    assert main(["node", "retry", path, "b", "--worker", "ghost"]) == exit_codes.VALIDATION
    assert "ghost" in capsys.readouterr().err

    # 合法 worker → 生效
    assert main(["node", "retry", path, "b", "--worker", "dave"]) == exit_codes.OK
    capsys.readouterr()
    m = load_manifest(path)
    assert m.nodes["b"].worker == "dave"
    assert m.nodes["b"].status == "todo"


def test_retry_note_reaches_the_workers_issue_body_and_a_retry_without_one_clears_it(tmp_path, capsys):
    from omac.core.taskmeta import TaskKind
    from omac.pipeline.dispatch import render_issue_body

    path = _write_manifest(tmp_path, _basic_nodes())

    assert main(["node", "retry", path, "b", "--note", "  the registry is on the VPN, use mirror.example.test  "]) == exit_codes.OK
    node = load_manifest(path).nodes["b"]
    body = render_issue_body(node, node.contract, TaskKind.DEVELOP, "wi-2")
    assert node.retry_note == "the registry is on the VPN, use mirror.example.test"
    assert "the registry is on the VPN, use mirror.example.test" in body

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    cleared = load_manifest(path).nodes["b"]
    assert cleared.retry_note is None
    assert "mirror.example.test" not in render_issue_body(cleared, cleared.contract, TaskKind.DEVELOP, "wi-2")


def test_retry_note_is_bounded_and_not_for_a_review_retry(tmp_path, capsys):
    path = _write_manifest(tmp_path, _basic_nodes())

    assert main(["node", "retry", path, "b", "--note", "x" * 2001]) == exit_codes.VALIDATION
    assert "2000" in capsys.readouterr().err
    assert main(["node", "retry", path, "b", "--stage", "review", "--note", "look again"]) == exit_codes.VALIDATION
    assert load_manifest(path).nodes["b"].status == "blocked"


def test_retry_hints_rerun(tmp_path, capsys):
    path = _write_manifest(tmp_path, _basic_nodes())
    main(["node", "retry", path, "b"])
    assert "dag run" in capsys.readouterr().err


# ---------------- accept ----------------

def test_accept_rejects_node_without_work_item(tmp_path):
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "status": "blocked",
    }])

    assert main(["node", "accept", path, "b"]) == exit_codes.VALIDATION
    assert load_manifest(path).nodes["b"].status == "blocked"


# ---------------- abandon ----------------

def test_abandon_marks_abandoned_and_unlocks_downstream(tmp_path, capsys):
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "abandon", path, "b"]) == exit_codes.OK
    m = load_manifest(path)
    assert m.nodes["b"].status == "abandoned"


def test_abandon_downstream_becomes_ready():
    """abandon 后下游在下轮 tick 进入就绪集(graph 层语义)。"""
    issues = {
        "a": {"status": "done", "blocked_by": []},
        "b": {"status": "abandoned", "blocked_by": ["a"]},
        "c": {"status": "todo", "blocked_by": ["b"]},
    }
    assert ready_nodes(issues) == ["c"]


def test_abandon_reports_affected_downstream(tmp_path, capsys):
    path = _write_manifest(tmp_path, _basic_nodes())
    main(["node", "abandon", path, "a"])
    payload = json.loads(capsys.readouterr().out)
    # a 的传递下游:b、c
    assert "b" in payload["affected_downstream"]
    assert "c" in payload["affected_downstream"]


# ---------------- hold / release ----------------

def _hold_nodes():
    return [
        {"id": "root", "worker": "alice", "status": "done", "work_item_id": "1"},
        {"id": "held", "worker": "bob", "blocked_by": ["root"], "status": "todo"},
        {"id": "after", "worker": "bob", "blocked_by": ["held"], "status": "todo"},
        {"id": "other", "worker": "bob", "blocked_by": ["root"], "status": "todo"},
    ]


def _ready(path):
    manifest = load_manifest(path)
    return ready_nodes({k: {"status": n.status, "blocked_by": n.blocked_by, "held": n.held}
                        for k, n in manifest.nodes.items()})


def test_hold_persists_in_the_node_state_and_leaves_the_downstream_and_other_branches_to_the_graph(tmp_path, capsys):
    path = _write_manifest(tmp_path, _hold_nodes())

    assert main(["node", "hold", path, "held", "--reason", "needs the test account"]) == exit_codes.OK

    payload = json.loads(capsys.readouterr().out)
    assert (payload["held"], payload["hold_reason"]) == (True, "needs the test account")
    held = load_manifest(path).nodes["held"]
    assert (held.held, held.hold_reason, held.status) == (True, "needs the test account", "todo")
    assert _ready(path) == ["other"]


def test_release_makes_the_held_node_ready_again(tmp_path):
    path = _write_manifest(tmp_path, _hold_nodes())
    main(["node", "hold", path, "held", "--reason", "x"])

    assert main(["node", "release", path, "held"]) == exit_codes.OK

    node = load_manifest(path).nodes["held"]
    assert (node.held, node.hold_reason) == (False, None)
    assert _ready(path) == ["held", "other"]


@pytest.mark.parametrize("status", ["in_progress", "in_review", "blocked", "done"])
def test_hold_refuses_a_node_that_is_not_todo_and_changes_nothing(tmp_path, status, capsys):
    nodes = _hold_nodes()
    nodes[1]["status"] = status
    path = _write_manifest(tmp_path, nodes)

    assert main(["node", "hold", path, "held"]) == exit_codes.VALIDATION

    assert load_manifest(path).nodes["held"].held is False
    assert status in capsys.readouterr().err


def test_release_refuses_a_node_that_is_not_held(tmp_path):
    path = _write_manifest(tmp_path, _hold_nodes())

    assert main(["node", "release", path, "held"]) == exit_codes.VALIDATION


def test_hold_refuses_an_unknown_node(tmp_path):
    path = _write_manifest(tmp_path, _hold_nodes())

    assert main(["node", "hold", path, "nope"]) == exit_codes.VALIDATION


def test_hold_with_a_dag_run_alive_is_queued_not_lost_or_applied_behind_its_back(tmp_path, capsys):
    """The Loop is the single writer of node state: with a `dag run` active the hold is queued for its tick boundary."""
    path = _write_manifest(tmp_path, _hold_nodes())

    with manifest_write_lock(path):
        assert main(["node", "hold", path, "held", "--no-wait"]) == exit_codes.OK

    assert json.loads(capsys.readouterr().out)["queued"] is True
    assert load_manifest(path).nodes["held"].held is False


def test_show_reports_the_hold(tmp_path, capsys):
    path = _write_manifest(tmp_path, _hold_nodes())
    main(["node", "hold", path, "held", "--reason", "needs the test account"])
    capsys.readouterr()

    main(["node", "show", path, "held"])

    payload = json.loads(capsys.readouterr().out)
    assert (payload["held"], payload["hold_reason"]) == (True, "needs the test account")

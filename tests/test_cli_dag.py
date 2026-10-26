"""dag status/snapshot pure rules: the locked report schema, rendering, the needs-decision report built from given observations.

Reports built from a real platform, `omac dag status` and the tick report: tests/it/test_it_cli_dag.py.
"""
import json
from types import SimpleNamespace

import pytest

from _support.state import save_literal
from omac.cli import exit_codes
from omac.cli.commands.dag import _assemble_engine
from omac.cli.main import main
from omac.core.manifest import load_manifest
from omac.core.workitem import WorkItem, WorkItemControlProjection, WorkItemPayload, WorkItemStatus
from omac.pipeline.report import (
    NEEDS_DECISION_KEYS, NODE_KEYS, PROGRESS_KEYS, STATUS_REPORT_KEYS, _next_actions, build_needs_decision, render_table,
)


def _manifest_yaml(tmp_path, nodes):
    return save_literal(tmp_path / "dag.yaml", {"meta": {"name": "test-dag"}, "nodes": nodes})


def test_dag_engine_maps_config_project_to_multica_batch_capability(
        tmp_path):
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(
        "engine: multica\nworkspace: ws\nproject: project-1\n")
    manifest = omac_dir / "dag.yaml"
    manifest.write_text("meta: {}\nnodes: []\n")

    engine, _ = _assemble_engine(SimpleNamespace(manifest=str(manifest)))

    assert engine.store.config.project_id == "project-1"
    assert engine.store.control_batch_observation_supported() is True


def test_needs_decision_report_prioritizes_convergence_amendment_action():
    actions = _next_actions([{
        "key": "resource-runs",
        "decision_required": {
            "reason_code": "review-convergence-scope-expanding",
            "next_action": (
                "omac dag amend propose .omac/dag.yaml --report-file report.md "
                "--docs docs --blocked-node resource-runs --output json"
            ),
        },
    }], ".omac/dag.yaml")

    assert actions == [
        "omac dag amend propose .omac/dag.yaml --report-file report.md "
        "--docs docs --blocked-node resource-runs --output json"
    ]


def test_the_report_schema_is_locked():
    assert STATUS_REPORT_KEYS == ("manifest", "progress", "nodes", "needs_decision")
    assert PROGRESS_KEYS == ("total", "done", "running", "todo", "blocked", "failed", "abandoned", "converged")
    assert NODE_KEYS == ("key", "status", "worker", "reviewer", "work_item_id", "pr_url", "blocked_by")
    assert NEEDS_DECISION_KEYS == ("failed_nodes", "blocked_downstream", "next_actions")


def test_the_table_lists_the_nodes_and_the_progress():
    report = {"manifest": "m.yaml", "progress": {"total": 2, "done": 1, "running": 0, "todo": 1, "blocked": 0, "failed": 0, "abandoned": 0,
                                                   "converged": False},
              "nodes": [{"key": "a", "status": "done", "worker": "alice", "reviewer": None, "work_item_id": "1", "pr_url": "https://pr/1",
                         "blocked_by": []},
                        {"key": "b", "status": "todo", "worker": "bob", "reviewer": None, "work_item_id": None, "pr_url": None, "blocked_by": ["a"]}],
              "needs_decision": None}

    table = render_table(report)

    assert all(header in table for header in ("KEY", "STATUS", "WORKER")) and "alice" in table and "bob" in table and "done" in table.lower()


def test_a_missing_manifest_is_a_validation_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert main(["dag", "status", str(tmp_path / "nope.yaml")]) == exit_codes.VALIDATION


def test_the_engine_assembly_keeps_the_workspace_slug_of_the_config(tmp_path):
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text("engine: multica\nworkspace: ws\nproject: project-1\nworkspace_slug: acme-team\n")
    path = _manifest_yaml(omac_dir, [{"id": "a", "worker": "alice", "status": "todo"}])

    engine, _ = _assemble_engine(SimpleNamespace(manifest=path, engine=None, workspace=None, project=None))

    assert engine.store.config.extra["workspace_slug"] == "acme-team"



def test_snapshot_needs_no_engine_and_preserves_manifest(tmp_path, monkeypatch, capsys):
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "done"},
        {"id": "b", "worker": "bob", "status": "blocked",
         "blocked_by": ["a"]},
    ])
    before = open(path, "rb").read()

    import omac.cli.commands.dag as dag_mod
    monkeypatch.setattr(
        dag_mod, "_assemble_engine",
        lambda _args: pytest.fail("dag snapshot must not assemble an engine"),
    )

    assert main(["dag", "snapshot", path, "--output", "json"]) == exit_codes.OK

    data = json.loads(capsys.readouterr().out)
    assert set(data) == set(STATUS_REPORT_KEYS)
    assert data["progress"] == {
        "total": 2, "done": 1, "running": 0, "todo": 0,
        "blocked": 1, "failed": 0, "abandoned": 0,
        "converged": False,
    }
    assert [node["status"] for node in data["nodes"]] == ["done", "blocked"]
    assert data["needs_decision"]["failed_nodes"][0]["reason"] == "blocked"
    assert open(path, "rb").read() == before


def test_build_needs_decision_consumes_observations_only(tmp_path):
    """报告仅由传入的观察快照构建:字段映射与 deferred payload 语义与
    build_status_report 一致(同一 _build_needs_decision_from_items)。"""
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "blocked", "work_item_id": "1"},
        {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]},
    ])
    manifest = load_manifest(path)
    item = WorkItem(
        id="1", workspace_id="ws", title="A", description="d",
        status=WorkItemStatus.BLOCKED,
        dag_key="a",
        artifacts={"pr_url": "https://pr/1"},
        review_verdict="reject",
        review_comment="tests missing",
    )
    projection = WorkItemControlProjection(
        item, frozenset({WorkItemPayload.VERIFICATION}))

    report = build_needs_decision(
        manifest, path, {"a"}, {"a": projection, "b": None},
        evidence={"a": "ci failed"})

    assert set(report.keys()) == set(NEEDS_DECISION_KEYS)
    assert len(report["failed_nodes"]) == 1
    node = report["failed_nodes"][0]
    assert node["key"] == "a"
    assert node["status"] == "blocked"
    assert node["reason"] == "ci failed"
    assert node["work_item_id"] == "1"
    assert node["pr_url"] == "https://pr/1"
    # deferred payload 按「引用已声明」计存在,与 build_status_report 相同
    assert node["evidence_summary"] == {
        "review_verdict": "reject",
        "review_comment": "tests missing",
        "has_verification": True,
        "has_review": False,
    }
    assert report["blocked_downstream"] == ["b"]
    assert report["next_actions"] == [
        f"omac node retry {path} a",
        f"omac node abandon {path} a",
    ]


def test_build_needs_decision_none_observation_equals_legacy_tolerance(tmp_path):
    """None 观察(≙ 旧 _fetch_items 查找失败 / _MISSING_WORK_ITEM)→ None item。"""
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "failed", "work_item_id": "1"},
    ])
    manifest = load_manifest(path)

    report = build_needs_decision(manifest, path, {"a"}, {"a": None})

    assert set(report.keys()) == set(NEEDS_DECISION_KEYS)
    node = report["failed_nodes"][0]
    assert node["pr_url"] is None
    assert node["evidence_summary"] is None
    # 无精确原因且无 item 时回退 node.status,与旧 _fetch_items 宽容路径一致
    assert node["reason"] == "failed"

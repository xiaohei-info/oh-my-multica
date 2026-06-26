"""Structured harvest gates for worker verification and reviewer reports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import load_manifest, save_manifest, set_node
from engines import create_engine_from_config, WorkItemStatus
from run_dag import _harvest
import run_dag as rd


def _make_mock_engine():
    env = {
        "ENGINE_TYPE": "mock",
        "MOCK_WORKSPACE_ID": "ws",
        "MOCK_AUTO_COMPLETE": "false",
        "POLLING_INTERVAL": "1",
    }
    engine = create_engine_from_config("mock", "ws", **env)
    engine.config.polling_interval = 0.001
    engine._members["sq"] = ["alice", "bob"]
    return engine


def _write_manifest(path, *, reviewer=None, coverage_gate=90):
    reviewer_line = f"    reviewer: {reviewer}\n" if reviewer else ""
    path.write_text(
        "meta:\n"
        "  name: harvest-gates\n"
        "  squad: sq\n"
        "nodes:\n"
        "  - id: A\n"
        "    worker: alice\n"
        "    title: Task A\n"
        "    description: 'Test task'\n"
        f"{reviewer_line}"
        "    contract:\n"
        "      objective: Implement A\n"
        "      acceptance:\n"
        "        - A returns success\n"
        "      non_goals:\n"
        "        - Do not modify B\n"
        "      verification_commands:\n"
        "        - pytest tests/a\n"
        "      pr_base: feature/v1\n"
        f"      coverage_gate: {coverage_gate}\n"
    )


def _make_done_item(engine, *, verification=None):
    item = engine.create_work_item(
        workspace_id="sq",
        title="Task A",
        description="Test",
        dag_key="A",
        worker="alice",
    )
    engine.update_status(item.id, WorkItemStatus.DONE)
    item.artifacts = {"pr_url": "https://mock.example.com/pr/1"}
    item.verification = verification
    return item


def _load_in_progress(path, item_id):
    m = load_manifest(str(path))
    set_node(m, "A", work_item_id=item_id, status="in_progress")
    save_manifest(m, str(path))
    return m


def _load_in_review(path, item_id):
    m = load_manifest(str(path))
    set_node(m, "A", work_item_id=item_id, status="in_review")
    save_manifest(m, str(path))
    return m


def valid_verification(*, coverage=92):
    return {
        "commands": [
            {"cmd": "pytest tests/a", "exit_code": 0, "summary": "3 passed"},
        ],
        "pr_base": "feature/v1",
        "ci_status": "passed",
        "coverage": coverage,
    }


def valid_review_report():
    return {
        "diff_reviewed": True,
        "tests_rerun": True,
        "coverage_checked": True,
        "acceptance_mapping": [
            {
                "acceptance": "A returns success",
                "evidence": "tests/test_a.py::test_success",
                "status": "pass",
            }
        ],
        "blockers": [],
        "nits": ["Rename temporary variable"],
    }


def test_harvest_worker_done_without_verification_blocks(tmp_path, monkeypatch):
    manifest_path = tmp_path / "dag.yaml"
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)
    item = _make_done_item(engine, verification=None)
    m = _load_in_progress(manifest_path, item.id)

    completed, failed = set(), set()
    _harvest(engine, m, str(manifest_path), completed, failed)

    assert m.nodes["A"].status == "blocked"
    assert "A" in failed
    assert "A" not in completed
    assert engine._work_items[item.id].status == WorkItemStatus.BLOCKED


def test_harvest_worker_done_missing_declared_command_blocks(tmp_path, monkeypatch):
    manifest_path = tmp_path / "dag.yaml"
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)
    item = _make_done_item(
        engine,
        verification={
            "commands": [{"cmd": "pytest other", "exit_code": 0}],
            "pr_base": "feature/v1",
            "coverage": 92,
        },
    )
    m = _load_in_progress(manifest_path, item.id)

    completed, failed = set(), set()
    _harvest(engine, m, str(manifest_path), completed, failed)

    assert m.nodes["A"].status == "blocked"
    assert "A" in failed


def test_harvest_worker_done_low_coverage_blocks(tmp_path, monkeypatch):
    manifest_path = tmp_path / "dag.yaml"
    _write_manifest(manifest_path, coverage_gate=95)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)
    item = _make_done_item(engine, verification=valid_verification(coverage=94))
    m = _load_in_progress(manifest_path, item.id)

    completed, failed = set(), set()
    _harvest(engine, m, str(manifest_path), completed, failed)

    assert m.nodes["A"].status == "blocked"
    assert "A" in failed


def test_harvest_worker_done_valid_verification_enters_review(tmp_path, monkeypatch):
    manifest_path = tmp_path / "dag.yaml"
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)
    item = _make_done_item(engine, verification=valid_verification())
    m = _load_in_progress(manifest_path, item.id)

    completed, failed = set(), set()
    _harvest(engine, m, str(manifest_path), completed, failed)

    assert m.nodes["A"].status == "in_review"
    assert "A" not in failed
    assert engine._work_items[item.id].status == WorkItemStatus.IN_REVIEW
    assert engine._work_items[item.id].reviewer == "bob"


def test_harvest_reviewer_pass_without_report_blocks(tmp_path, monkeypatch):
    manifest_path = tmp_path / "dag.yaml"
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)
    item = engine.create_work_item(
        workspace_id="sq",
        title="Task A",
        description="Test",
        dag_key="A",
        worker="alice",
        reviewer="bob",
    )
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    item.review_verdict = "pass"
    item.review_report = None
    m = _load_in_review(manifest_path, item.id)

    completed, failed = set(), set()
    _harvest(engine, m, str(manifest_path), completed, failed)

    assert m.nodes["A"].status == "blocked"
    assert "A" in failed
    assert "A" not in completed
    assert engine._work_items[item.id].status == WorkItemStatus.BLOCKED


def test_harvest_reviewer_pass_with_blockers_blocks(tmp_path, monkeypatch):
    manifest_path = tmp_path / "dag.yaml"
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)
    item = engine.create_work_item(
        workspace_id="sq",
        title="Task A",
        description="Test",
        dag_key="A",
        worker="alice",
        reviewer="bob",
    )
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    item.review_verdict = "pass"
    report = valid_review_report()
    report["blockers"] = [{"type": "contract_violation", "reason": "Wrong DTO"}]
    item.review_report = report
    m = _load_in_review(manifest_path, item.id)

    completed, failed = set(), set()
    _harvest(engine, m, str(manifest_path), completed, failed)

    assert m.nodes["A"].status == "blocked"
    assert "A" in failed


def test_harvest_reviewer_pass_with_nits_and_valid_report_done(tmp_path, monkeypatch):
    manifest_path = tmp_path / "dag.yaml"
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)
    item = engine.create_work_item(
        workspace_id="sq",
        title="Task A",
        description="Test",
        dag_key="A",
        worker="alice",
        reviewer="bob",
    )
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    item.review_verdict = "pass-with-nits"
    item.review_report = valid_review_report()
    m = _load_in_review(manifest_path, item.id)

    completed, failed = set(), set()
    _harvest(engine, m, str(manifest_path), completed, failed)

    assert m.nodes["A"].status == "done"
    assert "A" in completed
    assert "A" not in failed
    assert engine._work_items[item.id].status == WorkItemStatus.DONE

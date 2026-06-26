"""
MockEngine 覆盖率补测 - 覆盖 mock.py 未测分支，目标分支覆盖率 >=90%。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines import create_engine_from_config, WorkItemStatus


def _make_engine(auto_complete="true", delay="0"):
    env = {
        "ENGINE_TYPE": "mock",
        "MOCK_WORKSPACE_ID": "ws",
        "MOCK_AUTO_COMPLETE": auto_complete,
        "MOCK_AUTO_COMPLETE_DELAY": delay,
        "POLLING_INTERVAL": "1",
    }
    engine = create_engine_from_config("mock", "ws", **env)
    engine.config.polling_interval = 0.001
    engine._members["ws"] = ["alice", "bob", "carol"]
    return engine


def test_auto_complete_disabled():
    """auto_complete=False 时 get_work_item 不自动完成。"""
    engine = _make_engine(auto_complete="false")
    item = engine.create_work_item(
        workspace_id="ws", title="T", description="D",
        dag_key="k", worker="alice")
    engine.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    engine.assign_work_item(item.id, "alice", "worker")
    time.sleep(0.05)
    result = engine.get_work_item(item.id)
    assert result.status == WorkItemStatus.IN_PROGRESS


def test_auto_complete_check_unknown_item():
    """_auto_complete_check 对不存在的 item_id 不 crash。"""
    engine = _make_engine()
    engine._auto_complete_check("nonexistent-id")


def test_auto_complete_review_auto_pass():
    """IN_REVIEW auto_complete -> verdict=pass。"""
    engine = _make_engine(delay="0")
    item = engine.create_work_item(
        workspace_id="ws", title="T", description="D",
        dag_key="k", worker="alice", reviewer="bob")
    engine.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    engine.assign_work_item(item.id, "alice", "worker")
    time.sleep(0.05)
    engine.get_work_item(item.id)
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    engine.assign_work_item(item.id, "bob", "reviewer")
    time.sleep(0.05)
    result = engine.get_work_item(item.id)
    assert result.review_verdict == "pass"
    assert result.review_comment == "Mock: LGTM"


def test_get_required_env_vars():
    from engines.mock import MockEngine
    vars_ = MockEngine.get_required_env_vars()
    assert len(vars_) == 3
    names = [v["name"] for v in vars_]
    assert "MOCK_WORKSPACE_ID" in names
    assert "MOCK_AUTO_COMPLETE" in names
    assert "MOCK_AUTO_COMPLETE_DELAY" in names


def test_get_recommended_polling_interval():
    from engines.mock import MockEngine
    assert MockEngine.get_recommended_polling_interval() == 1


def test_update_metadata_all_fields():
    """update_work_item_metadata 覆盖 reviewer/blocked_by/artifacts/verification/review 分支。"""
    engine = _make_engine()
    item = engine.create_work_item(
        workspace_id="ws", title="T", description="D",
        dag_key="k", worker="alice")
    result = engine.update_work_item_metadata(
        item.id, reviewer="bob", blocked_by=["A"],
        artifacts={"pr_url": "https://example.com/pr/1"},
        verification={"commands": [{"cmd": "pytest", "exit_code": 0}], "coverage": 91},
        review_verdict="pass", review_comment="LGTM",
        review_report={"diff_reviewed": True, "blockers": []})
    assert result.reviewer == "bob"
    assert result.blocked_by == ["A"]
    assert result.artifacts == {"pr_url": "https://example.com/pr/1"}
    assert result.verification == {"commands": [{"cmd": "pytest", "exit_code": 0}], "coverage": 91}
    assert result.review_verdict == "pass"
    assert result.review_comment == "LGTM"
    assert result.review_report == {"diff_reviewed": True, "blockers": []}


def test_list_work_items_basic():
    engine = _make_engine()
    engine.create_work_item(workspace_id="ws", title="T1", description="D", dag_key="k1", worker="alice")
    engine.create_work_item(workspace_id="ws", title="T2", description="D", dag_key="k2", worker="bob")
    items = engine.list_work_items("ws")
    assert len(items) == 2


def test_list_work_items_status_filter():
    engine = _make_engine()
    item1 = engine.create_work_item(workspace_id="ws", title="T1", description="D", dag_key="k1", worker="alice")
    engine.create_work_item(workspace_id="ws", title="T2", description="D", dag_key="k2", worker="bob")
    engine.update_status(item1.id, WorkItemStatus.DONE)
    done_items = engine.list_work_items("ws", status=WorkItemStatus.DONE)
    assert len(done_items) == 1
    assert all(i.status == WorkItemStatus.DONE for i in done_items)


def test_add_comment():
    engine = _make_engine()
    item = engine.create_work_item(workspace_id="ws", title="T", description="D", dag_key="k", worker="alice")
    engine.add_comment(item.id, "test comment")


def test_assign_work_item_unknown_role():
    """assign_work_item role 非 worker/reviewer -> fallthrough 不改 metadata。"""
    engine = _make_engine()
    item = engine.create_work_item(workspace_id="ws", title="T", description="D", dag_key="k", worker="alice")
    before_worker = item.worker
    before_reviewer = item.reviewer
    engine.assign_work_item(item.id, "carol", "observer")
    assert item.worker == before_worker
    assert item.reviewer == before_reviewer
    assert len(engine.assign_log) > 0

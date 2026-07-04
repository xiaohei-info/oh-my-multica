"""P3.1:run_task 确定性原语 —— 派任务→等终态→取交付→有界修订循环。

验收标准:
- mock:一次过 / reject 2 次后过 / 耗尽 NeedsDecision 三条路径单测
- 全程同一 issue id(不新建评审 issue)
- issue body 取自 dispatch.render_issue_body(三段式 §7.4 模板)
"""
from __future__ import annotations

import pytest

from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import NeedsDecision
from omac.pipeline.tasks import run_task


def _engine(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return create_engine("mock", EngineConfig(engine_type="mock", workspace_id="ws", extra=base))


def _payload(**over):
    base = {
        "title": "feature-x",
        "contract": Contract(
            objective="实现 feature-x",
            acceptance=["端到端可走通"],
            non_goals=["不碰其他模块"],
        ),
    }
    base.update(over)
    return base


def test_one_pass_no_reviewers():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan_file": "plan.md"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice")
    assert res["item_id"]
    assert res["delivery"] == {"plan_file": "plan.md"}
    assert res["rounds"] == 0
    assert res["verdict"] == "pass"
    assert res["kind"] == "plan"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE
    # 全程只建了一条 issue
    assert len(eng.store.list_work_items("ws")) == 1
    # body 取自 dispatch 三段式模板(含 work show/submit 命令 + issue id)
    assert f"omac work show {res['item_id']}" in item.description
    assert f"omac work submit {res['item_id']}" in item.description


def test_reject_twice_then_pass():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan_file": "plan.md"})
    MockStore.set_review_rejects(2)
    res = run_task(
        eng, TaskKind.PLAN, _payload(), "alice",
        reviewers=["bob"], max_revisions=3)
    assert res["delivery"] == {"plan_file": "plan.md"}
    assert res["rounds"] == 3  # 2 次 reject + 1 次 pass
    assert res["verdict"] == "pass"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE
    # 全程同一 issue id,未新建评审 issue
    assert len(eng.store.list_work_items("ws")) == 1
    assert item.id == res["item_id"]
    # 两次 reject 意见都落在同一 issue 上
    comments = eng.store.get_comments(res["item_id"])
    assert len(comments) == 2


def test_exhausted_needs_decision():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan_file": "plan.md"})
    MockStore.set_review_rejects(99)  # 永远 reject
    with pytest.raises(NeedsDecision) as exc:
        run_task(eng, TaskKind.PLAN, _payload(), "alice",
                 reviewers=["bob"], max_revisions=3)
    report = exc.value.report
    assert report["rounds"] == 3
    assert report["last_opinion"]
    assert report["item_id"]
    assert report["kind"] == "plan"
    # 全程同一 issue id
    assert len(eng.store.list_work_items("ws")) == 1
    assert report["item_id"] == eng.store.list_work_items("ws")[0].id


def test_reviewer_rotation_avoids_producer():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan_file": "plan.md"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["alice", "bob", "charlie"])
    item = eng.store.get_work_item(res["item_id"])
    # reviewer ≠ producer (alice)
    assert item.reviewer in ("bob", "charlie")


def test_failure_in_production_short_circuits():
    eng = _engine()
    # dag_key == kind == "plan",注入失败
    MockStore.set_fail_keys({"plan"})
    with pytest.raises(NeedsDecision) as exc:
        run_task(eng, TaskKind.PLAN, _payload(), "alice")
    assert exc.value.report["rounds"] == 0
    assert "producer failed" in exc.value.report["last_opinion"]

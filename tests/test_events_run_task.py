"""run_task 生命周期事件:dispatch / verdict / revision / node_done /
human_gate_wait / needs_decision。用 structlog.testing.capture_logs 断言,
不依赖渲染格式。"""
from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from omac.core import logsetup
from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig
from omac.errors import NeedsDecision
from omac.pipeline.tasks import run_task


def _engine(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return create_engine("mock", EngineConfig(
        engine_type="mock", workspace_id="ws", extra=base))


def _payload():
    return {"title": "feature-x", "contract": Contract(
        objective="实现 feature-x", acceptance=["走通"], non_goals=["不越界"])}


def _poll():
    pass


def _names(cap):
    return [e["event"] for e in cap]


def test_dispatch_and_done_emitted():
    MockStore.set_review_rejects(0)
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    with capture_logs() as cap:
        run_task(_engine(), TaskKind.PLAN, _payload(), "alice", poll=_poll)
    assert logsetup.EVT_DISPATCH in _names(cap)
    assert logsetup.EVT_NODE_DONE in _names(cap)
    disp = next(e for e in cap if e["event"] == logsetup.EVT_DISPATCH)
    assert disp["worker"] == "alice"  # 派单事件带 worker


def test_reject_then_pass_emits_verdict_and_review_revision():
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    MockStore.set_review_rejects(2)
    with capture_logs() as cap:
        run_task(_engine(), TaskKind.PLAN, _payload(), "alice",
                 reviewers=["bob"], max_revisions=3, poll=_poll)
    names = _names(cap)
    assert names.count(logsetup.EVT_VERDICT) >= 3  # 2 reject + 1 pass
    # 回退事件带 gate=review 判别
    assert any(e["event"] == logsetup.EVT_REVISION and e.get("gate") == "review"
               for e in cap)
    assert logsetup.EVT_NODE_DONE in names


def test_exhausted_emits_needs_decision():
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    MockStore.set_review_rejects(99)
    with capture_logs() as cap:
        with pytest.raises(NeedsDecision):
            run_task(_engine(), TaskKind.PLAN, _payload(), "alice",
                     reviewers=["bob"], max_revisions=3, poll=_poll)
    assert logsetup.EVT_NEEDS_DECISION in _names(cap)


def test_human_gate_wait_emitted_when_confirm():
    MockStore.set_review_rejects(0)
    MockStore.set_auto_confirm(True)
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    with capture_logs() as cap:
        run_task(_engine(), TaskKind.PLAN, _payload(), "alice",
                 reviewers=["bob"], confirm=True, poll=_poll)
    MockStore.set_auto_confirm(False)
    assert logsetup.EVT_HUMAN_GATE_WAIT in _names(cap)


def test_confirmation_resume_does_not_emit_dispatch_or_create_run(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = eng.store.create_work_item(
        "ws", "amendment", "desc", "amend-confirmation", "alice",
        kind=TaskKind.AMENDMENT,
    )
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="reviewed amendment",
        review_verdict="pass",
        review_report={
            "review_goals": ["review"],
            "diff_reviewed": True,
            "tests_rerun": True,
            "coverage_checked": True,
            "full_review_completed": True,
            "acceptance_mapping": [{
                "acceptance": "走通",
                "evidence": "reviewed",
                "status": "pass",
            }],
            "blockers": [],
        },
        phase=TaskPhase.CONFIRMATION,
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = __import__(
        "omac.pipeline.tasks", fromlist=["_review_subject_digest"]
    )._review_subject_digest(TaskKind.AMENDMENT, current, 1)
    eng.store.mark_in_review(item.id)
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("confirmation resume must not wake an agent"),
    )

    with capture_logs() as cap:
        result = run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(),
            "alice",
            reviewers=["bob"],
            confirm=True,
            pause_at_confirmation=True,
            poll=lambda: pytest.fail("confirmation resume must not poll"),
            resume_item_id=item.id,
        )

    assert result["pending_confirmation"] is True
    assert logsetup.EVT_DISPATCH not in _names(cap)
    assert logsetup.EVT_HUMAN_GATE_WAIT in _names(cap)

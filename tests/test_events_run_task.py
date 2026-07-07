"""run_task 生命周期事件:dispatch / verdict / revision / node_done /
human_gate_wait / needs_decision。用 structlog.testing.capture_logs 断言,
不依赖渲染格式。"""
from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from omac.core import logsetup
from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind
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

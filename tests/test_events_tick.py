"""tick(dag run)生命周期事件:dispatch / node_done / converged /
node_failed / cascade_blocked / needs_decision。capture_logs 断言,不看渲染。"""
from __future__ import annotations

import os
import tempfile

from structlog.testing import capture_logs

from omac.core import logsetup
from omac.core.manifest import Manifest, Node, save_manifest
from omac.engines import create_engine
from omac.engines.models import EngineConfig
from omac.pipeline.loop import tick


def _engine():
    return create_engine("mock", EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}))


def _node(key, blocked_by=None):
    return Node(id=key, worker="alice", blocked_by=blocked_by or [],
                title=key, description=f"Task {key}")


def _manifest(nodes):
    return Manifest(meta={"workspace_id": "ws"}, nodes={n.id: n for n in nodes})


def _path(manifest):
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="omac_evt_")
    os.close(fd)
    save_manifest(manifest, path)
    return path


def _settle(store, runtime, manifest, path):
    for _ in range(50):
        r = tick(store, runtime, manifest, path)
        if r.state != "running":
            return r
    raise AssertionError("did not settle")


def _names(cap):
    return [e["event"] for e in cap]


def test_happy_dag_emits_dispatch_done_converged():
    manifest = _manifest([_node("a"), _node("b", blocked_by=["a"])])
    path = _path(manifest)
    eng = _engine()
    with capture_logs() as cap:
        result = _settle(eng.store, eng.runtime, manifest, path)
    assert result.state == "converged"
    names = _names(cap)
    assert names.count(logsetup.EVT_DISPATCH) == 2  # 两个节点各派一次
    assert names.count(logsetup.EVT_NODE_DONE) == 2
    assert logsetup.EVT_CONVERGED in names
    conv = next(e for e in cap if e["event"] == logsetup.EVT_CONVERGED)
    assert conv["total"] == 2 and conv["done"] == 2
    # dispatch 事件带 node key 与 worker
    disp = next(e for e in cap if e["event"] == logsetup.EVT_DISPATCH)
    assert disp["worker"] == "alice" and disp["node"] in {"a", "b"}


def test_failure_emits_node_failed_cascade_needs_decision():
    manifest = _manifest([_node("a"), _node("b", blocked_by=["a"]),
                          _node("c", blocked_by=["b"])])
    path = _path(manifest)
    eng = _engine()
    eng.store.set_fail_keys({"a"})
    with capture_logs() as cap:
        result = _settle(eng.store, eng.runtime, manifest, path)
    assert result.state == "needs_decision"
    names = _names(cap)
    assert logsetup.EVT_NODE_FAILED in names          # a 失败
    assert logsetup.EVT_CASCADE_BLOCKED in names      # b/c 连坐
    assert logsetup.EVT_NEEDS_DECISION in names
    casc = next(e for e in cap if e["event"] == logsetup.EVT_CASCADE_BLOCKED)
    assert set(casc["ids"]) & {"b", "c"}

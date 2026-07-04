"""P1 e2e:omac dag run 确定性编排循环(mock 引擎下 4 场景)。

验收标准(issue AITEAM-352):
  1. 单节点 manifest 在 mock 引擎下 dag run -> exit 0,done 复用
  2. 失败注入 -> exit 20(NeedsDecision),报告含四段(failed_nodes/blocked_downstream/next_actions)
  3. node retry(清注入) -> dag run 续跑 exit 0
  4. abandon -> 下游解锁,exit 0
  5. 中断续跑(--max-rounds 多次) -> issue 不重复创建
"""
from __future__ import annotations

import os
import tempfile

import pytest

from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import Contract, Manifest, Node, load_manifest, save_manifest
from omac.engines import create_engine
from omac.engines.models import EngineConfig
from omac.errors import NeedsDecision
from omac.pipeline.loop import tick


# ==================== helpers ====================

def _config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="ws", extra=base)


def _engine(**extra):
    return create_engine("mock", _config(**extra))


def _contract():
    return Contract(
        objective="do it",
        acceptance=["works"],
        non_goals=["no creep"],
        verification_commands=["pytest -q"],
        integration_gates=[{
            "name": "gate-1", "layer": "L1", "delivery_goal": "delivers",
            "source_of_truth": ["docs/d.md"], "covers": ["route"],
            "acceptance_refs": ["works"], "commands": ["pytest tests/int"],
            "required_metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
        }],
        pr_base="feature/v1",
        coverage_gate=90,
    )


def _node(key, worker="alice", blocked_by=None, reviewer=None, contract=None):
    return Node(
        id=key, worker=worker, blocked_by=blocked_by or [],
        reviewer=reviewer, contract=contract,
        title=key, description=f"Task {key}",
    )


def _manifest(nodes, meta=None):
    return Manifest(meta=meta or {"workspace_id": "ws"},
                    nodes={n.id: n for n in nodes})


def _tmp_manifest(manifest):
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="omac_e2e_")
    os.close(fd)
    save_manifest(manifest, path)
    return path


def _loop(store, runtime, manifest, path, max_rounds=50, max_parallel=4):
    for _ in range(max_rounds):
        result = tick(store, runtime, manifest, path, max_parallel=max_parallel)
        if result.state != "running":
            return result
    raise AssertionError("loop did not settle")


# ==================== 1. 单节点 happy path:exit 0 + done 复用 ====================

class TestSingleNode:
    def test_dag_run_single_node_converges(self):
        manifest = _manifest([_node("a", contract=_contract())])
        path = _tmp_manifest(manifest)
        eng = _engine()

        result = _loop(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert result.done == ["a"]
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].work_item_id is not None

    def test_done_reused_on_rerun(self):
        manifest = _manifest([_node("a")])
        path = _tmp_manifest(manifest)
        eng = _engine()

        r1 = _loop(eng.store, eng.runtime, manifest, path)
        assert r1.state == "converged"
        first_wi = manifest.nodes["a"].work_item_id
        assert first_wi is not None

        # 续跑:done 节点复用,work_item_id 不变、不重复建 issue
        r2 = tick(eng.store, eng.runtime, manifest, path)
        assert r2.state == "converged"
        assert manifest.nodes["a"].work_item_id == first_wi
        assert len(eng.store.list_work_items("ws")) == 1


# ==================== 2. 失败注入:NeedsDecision + 四段报告 ====================

class TestFailureInjection:
    def test_failure_injection_needs_decision(self):
        manifest = _manifest([
            _node("a"),
            _node("b", blocked_by=["a"]),
        ])
        path = _tmp_manifest(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        report = result.report
        assert report is not None
        # 四段 schema(NEDS_DECISION_KEYS)
        assert "failed_nodes" in report
        assert "blocked_downstream" in report
        assert "next_actions" in report
        failed_keys = [fn["key"] for fn in report["failed_nodes"]]
        assert "a" in failed_keys
        assert "b" in report["blocked_downstream"]
        assert any("node retry" in s and "a" in s for s in report["next_actions"])

    def test_failure_does_not_auto_retry(self):
        """失败节点不自活(§2.4 红线):多轮 tick 仍 needs_decision。"""
        manifest = _manifest([_node("a")])
        path = _tmp_manifest(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop(eng.store, eng.runtime, manifest, path)
        assert result.state == "needs_decision"
        for _ in range(5):
            r = tick(eng.store, eng.runtime, manifest, path)
            assert r.state == "needs_decision"
            assert "a" in r.failed


# ==================== 3. node retry(清注入) -> 续跑 exit 0 ====================

class TestNodeRetry:
    def test_retry_clears_failure_and_rerun_converges(self):
        manifest = _manifest([
            _node("a"),
            _node("b", blocked_by=["a"]),
        ])
        path = _tmp_manifest(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop(eng.store, eng.runtime, manifest, path)
        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert manifest.nodes["b"].status == "blocked"

        # 清注入 + 显式 retry a(重置为 todo,保留 work_item_id)——模拟 omac node retry
        eng.store.set_fail_keys(set())
        manifest.nodes["a"].status = "todo"
        save_manifest(manifest, path)

        result2 = _loop(eng.store, eng.runtime, manifest, path)
        assert result2.state == "converged"
        assert sorted(result2.done) == ["a", "b"]


# ==================== 4. abandon -> 下游解锁,exit 0 ====================

class TestAbandon:
    def test_abandon_unlocks_downstream(self):
        manifest = _manifest([
            _node("a"),
            _node("b", blocked_by=["a"]),
            _node("c", blocked_by=["b"]),
        ])
        path = _tmp_manifest(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop(eng.store, eng.runtime, manifest, path)
        assert result.state == "needs_decision"
        assert manifest.nodes["b"].status == "blocked"
        assert manifest.nodes["c"].status == "blocked"

        # abandon a —— 模拟 omac node abandon
        manifest.nodes["a"].status = "abandoned"
        save_manifest(manifest, path)

        # 续跑:下游应解锁并收敛(a 保持 abandoned)
        result2 = _loop(eng.store, eng.runtime, manifest, path)
        assert result2.state == "converged"
        assert manifest.nodes["a"].status == "abandoned"
        assert manifest.nodes["b"].status == "done"
        assert manifest.nodes["c"].status == "done"


# ==================== 5. 中断续跑:--max-rounds 幂等续跑不重复创建 ====================

class TestBoundedResume:
    def test_max_rounds_resume_no_duplicate_issues(self):
        manifest = _manifest([
            _node("a"),
            _node("b", blocked_by=["a"]),
        ])
        path = _tmp_manifest(manifest)
        eng = _engine()

        # 派发 a(running)
        r1 = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert "a" in r1.dispatched
        a_wi = manifest.nodes["a"].work_item_id
        assert a_wi is not None

        # a 完成,b 派发
        r2 = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert "a" in r2.done
        assert "b" in r2.dispatched
        assert manifest.nodes["a"].work_item_id == a_wi  # 未重复创建

        # 收敛
        r3 = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert r3.state == "converged"
        assert sorted(r3.done) == ["a", "b"]
        assert manifest.nodes["a"].work_item_id == a_wi
        # a 全程只建了一个 issue
        assert sum(1 for i in eng.store.list_work_items("ws")
                   if i.dag_key == "a") == 1


# ==================== CLI 契约:dag run / tick / status 退出码 ====================

class TestCliContract:
    def _env(self, monkeypatch, tmp, fail_keys=None):
        monkeypatch.chdir(tmp)
        monkeypatch.setenv("OMAC_ENGINE", "mock")
        monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws")
        if fail_keys is not None:
            monkeypatch.setenv("OMAC_MOCK_FAIL_KEYS", fail_keys)

    def test_dag_run_cli_exit_0(self, tmp_path, monkeypatch):
        self._env(monkeypatch, tmp_path)
        m = _manifest([_node("a")])
        path = _tmp_manifest(m)
        assert main(["dag", "run", path]) == exit_codes.OK

    def test_dag_run_cli_failure_exit_20(self, tmp_path, monkeypatch):
        """dag run 需决策时 main() 捕获 NeedsDecision,exit 20 + 报告打 stdout。"""
        self._env(monkeypatch, tmp_path, fail_keys="a")
        m = _manifest([_node("a")])
        path = _tmp_manifest(m)
        rc = main(["dag", "run", path, "--output", "json"])
        assert rc == exit_codes.NEEDS_DECISION

    def test_dag_tick_cli_exit_10_in_progress(self, tmp_path, monkeypatch):
        self._env(monkeypatch, tmp_path)
        m = _manifest([_node("a"), _node("b", blocked_by=["a"])])
        path = _tmp_manifest(m)
        rc = main(["dag", "tick", path])
        # 单轮 tick 派发 a -> running -> exit 10
        assert rc == exit_codes.IN_PROGRESS

    def test_dag_status_cli_exit_0(self, tmp_path, monkeypatch):
        self._env(monkeypatch, tmp_path)
        m = _manifest([_node("a"), _node("b", blocked_by=["a"])])
        path = _tmp_manifest(m)
        rc = main(["dag", "status", path])
        assert rc == exit_codes.OK

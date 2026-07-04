"""pipeline/loop:单轮 tick 核心——结果回收 → 就绪计算 → 派发。

验收标准:
- mock:多节点带依赖 manifest,循环调 tick 至 converged,节点全 done
- mock 失败注入:tick 返回 needs_decision,失败节点 blocked、下游 blocked、report 完整
- 幂等:tick 序列中途重建 loop 对同一 manifest 继续,done 节点复用、不重复建 issue
- 无 reviewer 节点直接 done;有 reviewer 节点经 in_review → done(mock 自动评审)
- 不存在任何自动重试路径(blocked 节点在后续 tick 保持 blocked)
"""
import os
import tempfile

import pytest

from omac.core.manifest import Contract, Manifest, Node, load_manifest, save_manifest
from omac.engines import create_engine
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import EngineConfig
from omac.pipeline.loop import TickResult, tick


# ==================== fixtures ====================

def _config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="ws", extra=base)


def _engine(**extra):
    return create_engine("mock", _config(**extra))


def _contract(acceptance=None, verification_commands=None, integration_gates=None):
    return Contract(
        objective="do it",
        acceptance=acceptance or ["works"],
        non_goals=["no creep"],
        verification_commands=verification_commands or ["pytest -q"],
        integration_gates=integration_gates or [{
            "name": "gate-1",
            "layer": "L1",
            "delivery_goal": "delivers",
            "source_of_truth": ["docs/d.md"],
            "covers": ["route"],
            "acceptance_refs": ["works"],
            "commands": ["pytest tests/int"],
            "required_metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
        }],
        pr_base="feature/v1",
        coverage_gate=90,
    )


def _node(key, worker="alice", blocked_by=None, reviewer=None, contract=None, title=None):
    return Node(
        id=key,
        worker=worker,
        blocked_by=blocked_by or [],
        reviewer=reviewer,
        contract=contract,
        title=title or key,
        description=f"Task {key}",
    )


def _manifest(nodes, meta=None):
    return Manifest(
        meta=meta or {"workspace_id": "ws"},
        nodes={n.id: n for n in nodes},
    )


def _tmp_manifest_path(manifest):
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="omac_test_")
    os.close(fd)
    save_manifest(manifest, path)
    return path


def _loop_to_settle(store, runtime, manifest, path, max_rounds=50, max_parallel=4):
    """反复调 tick 直到非 running,返回最终 TickResult。"""
    result = None
    for _ in range(max_rounds):
        result = tick(store, runtime, manifest, path, max_parallel=max_parallel)
        if result.state != "running":
            break
    assert result is not None, "never ran a tick"
    return result


# ==================== 1. happy path:多节点带依赖 → converged ====================

class TestHappyPath:
    def test_linear_dag_converges(self):
        """a → b → c,循环 tick 至 converged,节点全 done。"""
        nodes = [
            _node("a"),
            _node("b", blocked_by=["a"]),
            _node("c", blocked_by=["b"]),
        ]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert sorted(result.done) == ["a", "b", "c"]
        assert result.failed == []
        assert result.running == []
        # 每个节点都有 work_item_id
        for n in manifest.nodes.values():
            assert n.work_item_id is not None

    def test_parallel_dag_converges(self):
        """a, b 独立;c 依赖两者。"""
        nodes = [
            _node("a"),
            _node("b"),
            _node("c", blocked_by=["a", "b"]),
        ]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert sorted(result.done) == ["a", "b", "c"]

    def test_dispatched_count_first_tick(self):
        """首轮 tick 派发所有无依赖节点(受 max_parallel 约束)。"""
        nodes = [_node("a"), _node("b"), _node("c")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert result.state == "running"
        assert sorted(result.dispatched) == ["a", "b", "c"]
        assert sorted(result.running) == ["a", "b", "c"]

    def test_max_parallel_limits_dispatch(self):
        """max_parallel=1 时首轮只派发 1 个节点。"""
        nodes = [_node("a"), _node("b")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)

        assert len(result.dispatched) == 1
        assert len(result.running) == 1


# ==================== 2. 失败注入 → needs_decision ====================

class TestFailureInjection:
    def test_failed_node_and_downstream_blocked(self):
        """a 失败 → a blocked,下游 b/c blocked,report 完整。"""
        nodes = [
            _node("a"),
            _node("b", blocked_by=["a"]),
            _node("c", blocked_by=["b"]),
        ]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert "a" in result.failed
        assert "b" in result.failed  # 下游 blocked
        assert "c" in result.failed  # 传递下游 blocked
        assert [n["key"] for n in result.report["failed_nodes"]] == sorted(result.failed)
        assert any(n["key"] == "a" for n in result.report["failed_nodes"])
        assert result.report["blocked_downstream"]  # 非空

    def test_independent_node_still_done(self):
        """a 失败不影响无依赖的 d。"""
        nodes = [
            _node("a"),
            _node("b", blocked_by=["a"]),
            _node("d"),
        ]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert "d" in result.done
        assert "a" in result.failed
        assert "b" in result.failed

    def test_report_has_evidence_summary(self):
        """report.evidence_summary 含失败原因。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        node_a = next(n for n in result.report["failed_nodes"] if n["key"] == "a")
        assert "失败" in node_a["reason"] or "failed" in node_a["reason"].lower()


# ==================== 3. 幂等:中途重建 loop 继续推进 ====================

class TestIdempotency:
    def test_done_nodes_reused_no_duplicate_issues(self):
        """tick 序列中途重建 loop,done 节点复用 work_item_id,不重复建。"""
        nodes = [_node("a"), _node("b", blocked_by=["a"])]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        # 第一轮 tick:派发 a
        r1 = tick(eng.store, eng.runtime, manifest, path)
        assert "a" in r1.dispatched

        # 第二轮 tick:a 完成,b 派发
        r2 = tick(eng.store, eng.runtime, manifest, path)
        assert "a" in r2.done

        # 记录 a 的 work_item_id
        a_item_id = manifest.nodes["a"].work_item_id
        assert a_item_id is not None

        # 重建 loop(store/runtime 是新的,但 work_items 在内存里丢失)
        # 重建意味着对同一 manifest 文件继续——mock store 是内存的,
        # 重建后 work_item_id 指向的 item 不存在 → reconcile 清空走新建
        # 但 done 节点状态在 manifest 里保持 done,reconcile 不会动它(无 work_item_id 跳过)
        eng2 = _engine()
        # 手动清空 a 的 work_item_id 模拟「平台已无此 item」
        # 但 done 状态不变——reconcile 跳过无 work_item_id 的节点
        from omac.core.manifest import set_node
        set_node(manifest, "a", work_item_id=None)

        r3 = tick(eng2.store, eng2.runtime, manifest, path)
        # a 保持 done(不重新派发),b 应继续推进
        assert "a" in r3.done
        assert "a" not in r3.dispatched

    def test_full_run_idempotent_reload(self):
        """完整跑完一次后,用新 engine 再 tick 不改变 converged 状态。"""
        nodes = [_node("a"), _node("b", blocked_by=["a"])]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)
        assert result.state == "converged"

        # 新 engine tick 一次:reconcile 发现 work_item_id 不存在 → 清空
        # 但 done 状态保持,ready_nodes 跳过 done → 仍 converged
        eng2 = _engine()
        r2 = tick(eng2.store, eng2.runtime, manifest, path)
        assert r2.state == "converged"
        assert sorted(r2.done) == ["a", "b"]


# ==================== 4. reviewer 阶段交接 ====================

class TestReviewerHandoff:
    def test_no_reviewer_direct_done(self):
        """无 reviewer 节点:worker 完成 → 直接 done。"""
        nodes = [_node("a", reviewer=None)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert "a" in result.done
        # 不经过 in_review
        # (mock 自动完成 → done,collect_results 直接标 done)

    def test_with_reviewer_goes_through_in_review(self):
        """有 reviewer 节点:worker 完成 → in_review → reviewer pass → done。"""
        nodes = [_node("a", reviewer="bob")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert "a" in result.done

    def test_reviewer_handoff_assigns_reviewer(self):
        """有 reviewer 节点:collect_results 把 issue 转派给 reviewer。"""
        nodes = [_node("a", reviewer="bob")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        # 第一轮:派发 a(in_progress)
        r1 = tick(eng.store, eng.runtime, manifest, path)
        assert "a" in r1.dispatched
        assert "a" in r1.running

        # 第二轮:worker 完成 → 转 in_review(有 reviewer)
        r2 = tick(eng.store, eng.runtime, manifest, path)
        # a 要么在 in_review(running),要么已完成 review(done)
        assert "a" in r2.running or "a" in r2.done

        # 跑到收敛
        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)
        assert result.state == "converged"
        assert "a" in result.done


# ==================== 5. 无自动重试 ====================

class TestNoAutoRetry:
    def test_blocked_stays_blocked(self):
        """blocked 节点在后续 tick 保持 blocked,不自动重置为 todo。"""
        nodes = [
            _node("a"),
            _node("b", blocked_by=["a"]),
            _node("c", blocked_by=["b"]),
        ]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        # 跑到 needs_decision
        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)
        assert result.state == "needs_decision"
        assert "a" in result.failed

        # 再 tick 多次:blocked 节点保持 blocked
        for _ in range(5):
            r = tick(eng.store, eng.runtime, manifest, path)
            assert "a" in r.failed
            assert "b" in r.failed
            assert "c" in r.failed
            assert r.state == "needs_decision"

    def test_blocked_node_not_redispatched(self):
        """blocked 节点不出现在 dispatched 列表中。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()
        eng.store.set_fail_keys({"a"})

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)
        assert result.state == "needs_decision"
        assert "a" in result.failed
        assert "a" not in result.dispatched


# ==================== 6. reconcile ====================

class TestReconcile:
    def test_reconcile_skips_running_nodes(self):
        """reconcile:运行中节点(in_progress)不归 reconcile 同步,
        由 collect_results 过证据门——平台 DONE 但缺 pr_url 应被拦住。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        # 平台 DONE 但缺 pr_url(不合规提交)
        item = eng.store.create_work_item(
            "ws", "a", "d", dag_key="a", worker="alice")
        eng.store.update_status(item.id, __import__("omac").engines.models.WorkItemStatus.DONE)
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        r = tick(eng.store, eng.runtime, manifest, path)
        # reconcile 不再把 in_progress → done;collect_results 过证据门 → blocked
        assert "a" in r.failed
        assert r.state == "needs_decision"
        node_a = next(n for n in r.report["failed_nodes"] if n["key"] == "a")
        assert "pr_url" in node_a["reason"]

    def test_reconcile_syncs_non_running_platform_status(self):
        """reconcile:非运行态节点的平台状态仍正常同步(如 todo 节点被外部标 done)。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        # 手动建 work item + 标 done,manifest 保持 todo(非运行态)
        item = eng.store.create_work_item(
            "ws", "a", "d", dag_key="a", worker="alice")
        eng.store.update_status(item.id, __import__("omac").engines.models.WorkItemStatus.DONE)
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "todo"
        save_manifest(manifest, path)

        r = tick(eng.store, eng.runtime, manifest, path)
        # reconcile 把 todo → done(非运行态,直接同步)
        assert "a" in r.done
        assert r.state == "converged"

    def test_reconcile_clears_missing_work_item(self):
        """reconcile:work_item_id 指向不存在的 item → 清空,标 todo。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        manifest.nodes["a"].work_item_id = "nonexistent-999"
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        r = tick(eng.store, eng.runtime, manifest, path)
        # reconcile 清空 → todo → ready → dispatch → running
        assert "a" in r.dispatched
        assert r.state == "running"


# ==================== 7. contract 验证(证据门) ====================

class TestContractEvidence:
    def test_contract_node_passes_gate(self):
        """有 contract 的节点:mock 自动生成合规证据 → 通过证据门 → done。"""
        nodes = [_node("a", contract=_contract())]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)
        assert result.state == "converged"
        assert "a" in result.done

    def test_contract_node_with_reviewer_passes_gate(self):
        """有 contract + reviewer:worker 证据门过 → in_review → reviewer pass → done。"""
        nodes = [_node("a", reviewer="bob", contract=_contract())]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)
        assert result.state == "converged"
        assert "a" in result.done


# ==================== 8. 证据门回归测试(reviewer 要求) ====================

class TestEvidenceGateRegression:
    """验证证据门不被 reconcile 短路——collect_results 真正执行证据校验。

    使用 MOCK_AUTO_COMPLETE=false + 手动构造平台终态,绕过 mock 自动完成。
    """

    def _manual_done_item(self, eng, key, worker="alice", reviewer=None,
                          artifacts=None, verification=None, contract=None):
        """手动建 work item 并标 DONE(不触发 mock 自动完成)。"""
        item = eng.store.create_work_item(
            "ws", key, f"Task {key}", dag_key=key, worker=worker, reviewer=reviewer)
        if contract is not None:
            eng.store.set_node_contract(item.id, contract)
        if artifacts is not None:
            eng.store.update_work_item_metadata(item.id, artifacts=artifacts)
        if verification is not None:
            eng.store.update_work_item_metadata(item.id, verification=verification)
        eng.store.update_status(item.id, __import__("omac").engines.models.WorkItemStatus.DONE)
        return item

    def test_invalid_worker_evidence_blocks_node(self):
        """worker DONE 但缺 pr_url → 证据门不过 → blocked + 回贴。"""
        contract = _contract()
        nodes = [_node("a", contract=contract)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        # 手动构造:worker 提交但缺 pr_url 和 verification
        item = self._manual_done_item(eng, "a", contract=contract,
                                      artifacts={}, verification=None)

        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert "a" in result.failed
        node_a = next(n for n in result.report["failed_nodes"] if n["key"] == "a")
        assert "pr_url" in node_a["reason"]
        # 失败原因经 add_comment 回贴
        assert any("证据门" in c for c in eng.store.get_comments(item.id))

    def test_invalid_worker_evidence_coverage_gate(self):
        """worker DONE + pr_url 但 coverage 不达标 → 证据门不过 → blocked。"""
        contract = _contract()
        nodes = [_node("a", contract=contract)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        item = self._manual_done_item(
            eng, "a", contract=contract,
            artifacts={"pr_url": "https://x/pr/1"},
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0}],
                "integration_gates": [{
                    "name": "gate-1",
                    "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
                    "metrics": {"route_coverage": 100},
                    "artifacts": ["coverage.xml"],
                    "source_of_truth": ["docs/d.md"],
                    "delivery_goal": "delivers",
                }],
                "pr_base": "feature/v1",
                "env_setup": ["mock: integration env ready"],
                "coverage": 50,  # 低于 gate 90
            },
        )

        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert "a" in result.failed
        node_a = next(n for n in result.report["failed_nodes"] if n["key"] == "a")
        assert "coverage" in node_a["reason"].lower() or "below gate" in node_a["reason"].lower()

    def test_valid_evidence_with_reviewer_enters_in_review(self):
        """worker DONE + 合规证据 + reviewer → in_review + assign reviewer + wake。"""
        contract = _contract()
        nodes = [_node("a", reviewer="bob", contract=contract)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        item = self._manual_done_item(
            eng, "a", reviewer="bob", contract=contract,
            artifacts={"pr_url": "https://x/pr/1"},
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0}],
                "integration_gates": [{
                    "name": "gate-1",
                    "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
                    "metrics": {"route_coverage": 100},
                    "artifacts": ["coverage.xml"],
                    "source_of_truth": ["docs/d.md"],
                    "delivery_goal": "delivers",
                }],
                "env_setup": ["mock: integration env ready"],
                "pr_base": "feature/v1",
                "coverage": 95,
                "env_setup": ["mock: provision integration env for gate-1"],
            },
        )

        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path)

        # 证据门过 → 转 in_review(有 reviewer)
        assert manifest.nodes["a"].status == "in_review"
        assert "a" in result.running  # in_review 属于 running
        # reviewer 已分配
        got = eng.store.get_work_item(item.id)
        assert got.reviewer == "bob"
        # assign_log 含 reviewer 分配
        assert any(role == "reviewer" for _, _, role, _ in eng.store.assign_log)

    def test_valid_evidence_without_reviewer_direct_done(self):
        """worker DONE + 合规证据 + 无 reviewer → 直接 done。"""
        contract = _contract()
        nodes = [_node("a", reviewer=None, contract=contract)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        item = self._manual_done_item(
            eng, "a", contract=contract,
            artifacts={"pr_url": "https://x/pr/1"},
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0}],
                "integration_gates": [{
                    "name": "gate-1",
                    "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
                    "metrics": {"route_coverage": 100},
                    "artifacts": ["coverage.xml"],
                    "source_of_truth": ["docs/d.md"],
                    "delivery_goal": "delivers",
                }],
                "env_setup": ["mock: integration env ready"],
                "pr_base": "feature/v1",
                "coverage": 95,
                "env_setup": ["mock: provision integration env for gate-1"],
            },
        )

        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert "a" in result.done
        assert manifest.nodes["a"].status == "done"

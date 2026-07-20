"""pipeline/loop:单轮 tick 核心——结果回收 → 就绪计算 → 派发。

验收标准:
- mock:多节点带依赖 manifest,循环调 tick 至 converged,节点全 done
- mock 失败注入:tick 返回 needs_decision,失败节点 blocked、下游 blocked、report 完整
- 幂等:tick 序列中途重建 loop 对同一 manifest 继续,done 节点复用、不重复建 issue
- develop 节点必须有独立 reviewer，并经 in_review → merge → done(mock 自动评审)
- 不存在任何自动重试路径(blocked 节点在后续 tick 保持 blocked)
"""
import os
import tempfile

import pytest

from omac.core.manifest import Contract, Manifest, Node, load_manifest, save_manifest
from omac.engines import create_engine
from omac.engines.mock import MockRuntime, MockStore
from omac.core.taskmeta import TaskPhase
from omac.engines.models import (
    DeliveryAction,
    DeliveryBlockReason,
    DeliveryResult,
    EngineConfig,
    WorkItemStatus,
)
from omac.errors import AuthError, PlatformError
from omac.pipeline.loop import TickResult, collect_results, tick


# ==================== fixtures ====================

@pytest.fixture(autouse=True)
def _default_gh_merge_succeeds_in_loop_tests(monkeypatch):
    """loop 单测不依赖外部 GitHub;默认 gh merge 在这里视为成功。

    显式 merge 命令的 subprocess 行为由 tests/test_delivery_merge.py 覆盖。
    """
    import subprocess

    real_run = subprocess.run

    def fake_run(command, *args, **kwargs):
        if isinstance(command, str) and command.startswith("gh pr merge "):
            class Proc:
                returncode = 0
                stdout = "merged"
                stderr = ""

            return Proc()
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr("omac.engines.mock.subprocess.run", fake_run)


def _config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="ws", extra=base)


def _engine(**extra):
    return create_engine("mock", _config(**extra))


def _quality(command="pytest tests/int"):
    return {
        "required_outcomes": [{
            "id": "outcome-works", "source_ref": "acceptance#works.action",
        }],
        "business_tests": [{
            "id": "business-works", "outcome_refs": ["outcome-works"],
            "command": command, "level": "integration",
            "real_dependencies": ["none"], "must_fail_on_base": True,
        }],
        "runtime_data_policy": "real-or-error",
    }


def _contract(
    acceptance=None,
    verification_commands=None,
    integration_gates=None,
    pr_base="feature/v1",
    coverage_gate=90,
):
    gates = integration_gates or [{
        "name": "gate-1",
        "layer": "L1",
        "delivery_goal": "delivers",
        "source_of_truth": ["docs/d.md"],
        "covers": ["route"],
        "acceptance_refs": ["works"],
        "commands": ["pytest tests/int"],
        "required_metrics": {"route_coverage": 100},
        "artifacts": ["coverage.xml"],
    }]
    business_command = gates[0]["commands"][0]
    return Contract(
        objective="do it",
        source_of_truth=["docs/d.md#feature"],
        acceptance=acceptance or ["works"],
        non_goals=["no creep"],
        verification_commands=verification_commands or ["pytest -q"],
        integration_gates=gates,
        quality=_quality(business_command),
        pr_base=pr_base,
        coverage_gate=coverage_gate,
    )


def _verification(
    *, command="pytest -q", gate_command="pytest tests/int",
    gate_name="gate-1", pr_base="feature/v1", coverage=95,
    delivered_revision="head-sha",
):
    return {
        "commands": [{"cmd": command, "exit_code": 0}],
        "integration_gates": [{
            "name": gate_name,
            "commands": [{"cmd": gate_command, "exit_code": 0}],
            "metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"],
            "delivery_goal": "delivers",
        }],
        "env_setup": ["mock: integration env ready"],
        "pr_base": pr_base,
        "coverage": coverage,
        "quality": {
            "delivered_revision": delivered_revision,
            "outcome_mapping": [{
                "outcome": "outcome-works",
                "implementation": ["src/feature.py"],
                "tests": ["tests/int/test_feature.py"],
            }],
            "regression_proof": [{
                "test_id": "business-works",
                "base_ref": "base-sha", "base_exit_code": 1,
                "head_ref": delivered_revision, "head_exit_code": 0,
            }],
            "runtime_fallbacks": [], "known_gaps": [],
            "evidence_origin": "real",
        },
    }


def _review_report(verdict="pass"):
    status = "fail" if verdict == "reject" else "pass"
    findings = []
    blockers = []
    nits = []
    if verdict == "reject":
        findings = [{
            "id": "REV-001", "severity": "blocker",
            "category": "business-behavior", "location": "src/feature.py:10",
            "evidence": "核心验收未满足", "impact": "核心流程失败",
            "required_fix": "修复核心业务行为",
        }]
        blockers = ["REV-001"]
    elif verdict == "pass-with-nits":
        findings = [{
            "id": "REV-001", "severity": "nit",
            "category": "maintainability", "location": "src/feature.py:10",
            "evidence": "存在低风险建议项", "impact": "增加维护成本",
            "required_fix": "完成局部整理",
        }]
        nits = ["REV-001"]
    return {
        "reviewed_revision": "head-sha",
        "review_goals": ["复核交付是否满足验收"],
        "diff_reviewed": True, "tests_rerun": True,
        "integration_tests_rerun": True, "coverage_checked": True,
        "review_scope": {
            "changed_files": ["src/feature.py", "tests/int/test_feature.py"],
            "all_changed_files_reviewed": True,
            "all_outcomes_reviewed": True,
            "all_business_tests_rerun": True,
            "runtime_fallback_audit_completed": True,
        },
        "findings": findings,
        "outcome_mapping": [{"outcome": "outcome-works", "status": status}],
        "acceptance_mapping": [{"acceptance": "works", "status": status}],
        "integration_gate_mapping": [{
            "gate": "gate-1", "status": "pass",
            "commands": [{"cmd": "pytest -q", "exit_code": 0}],
            "metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers",
        }],
        "blockers": blockers, "nits": nits,
    }


_DEFAULT = object()


def _node(
    key, worker="alice", blocked_by=None, reviewer=_DEFAULT,
    contract=_DEFAULT, title=None,
):
    return Node(
        id=key,
        worker=worker,
        blocked_by=blocked_by or [],
        reviewer="bob" if reviewer is _DEFAULT else reviewer,
        contract=_contract() if contract is _DEFAULT else contract,
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

    def test_dispatch_inherits_manifest_source_issues(self):
        """develop issue 派发时继承 manifest.meta.source_issues,供 body/work show 溯源。"""
        nodes = [_node("a", contract=_contract())]
        manifest = _manifest(nodes, meta={
            "workspace_id": "ws",
            "project_id": "proj-1",
            "source_issues": [
                "plan-1",
                "acc-1",
                "dec-1",
            ],
        })
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)

        assert item.source_refs == [
            {"label": "Design", "issue_id": "plan-1"},
            {"label": "Acceptance document", "issue_id": "acc-1"},
            {"label": "Task decomposition", "issue_id": "dec-1"},
        ]
        assert "## Upstream issues (stay on target)" in item.description
        assert "- Design: `plan-1`" in item.description
        assert "omac work show plan-1" not in item.description

    def test_dispatch_appends_direct_dependency_issue_refs(self):
        """develop issue 同时链接直接 blocked_by 节点的 Multica issue。"""
        foundation = _node("foundation", title="Shared contract foundation")
        foundation.status = "done"
        foundation.work_item_id = "issue-foundation"
        data = _node("data", title="Persistence layer")
        data.status = "done"
        data.work_item_id = "issue-data"
        missing = _node("missing", title="Abandoned setup")
        missing.status = "abandoned"
        feature = _node(
            "feature", blocked_by=["foundation", "data", "missing"],
            contract=_contract())
        manifest = _manifest([foundation, data, missing, feature], meta={
            "workspace_id": "ws",
            "project_id": "proj-1",
            "source_issues": ["plan-1", "acc-1", "dec-1"],
        })
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        prerequisite_ids = {}
        for dependency in (foundation, data):
            item = eng.store.create_work_item(
                "ws", dependency.title, "done dependency",
                dag_key=dependency.id, worker=dependency.worker,
                reviewer=dependency.reviewer,
            )
            eng.store.set_node_contract(item.id, dependency.contract)
            eng.store.update_work_item_metadata(
                item.id,
                phase=TaskPhase.REVIEW,
                artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
                verification=_verification(),
                review_verdict="pass",
                review_report=_review_report("pass"),
            )
            eng.store.update_status(item.id, WorkItemStatus.DONE)
            dependency.work_item_id = item.id
            dependency.merged = True
            prerequisite_ids[dependency.id] = item.id

        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item = eng.store.get_work_item(manifest.nodes["feature"].work_item_id)

        assert item.source_refs[-2:] == [
            {
                "label": "Prerequisite implementation · Shared contract foundation",
                "issue_id": prerequisite_ids["foundation"],
            },
            {
                "label": "Prerequisite implementation · Persistence layer",
                "issue_id": prerequisite_ids["data"],
            },
        ]
        assert item.blocked_by == ["foundation", "data", "missing"]
        assert (
            "- Prerequisite implementation · Shared contract foundation: "
            f"`#{prerequisite_ids['foundation']}`"
            in item.description
        )
        assert f"omac work show {prerequisite_ids['foundation']}" not in item.description
        assert f"omac work show {prerequisite_ids['data']}" not in item.description
        assert "Abandoned setup" not in item.description

    def test_dispatch_develop_dag_key_includes_manifest_dag_suffix(self):
        """worker issue 的 DAG key 继承 plan/decompose 唯一后缀,避免不同流水线节点重名。"""
        nodes = [_node("foundation-contract-skeleton", worker="alice")]
        manifest = _manifest(nodes, meta={
            "workspace_id": "ws",
            "dag_key": "decompose-p-aaade213",
        })
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item = eng.store.get_work_item(
            manifest.nodes["foundation-contract-skeleton"].work_item_id)

        assert item.dag_key == "decompose-p-aaade213/foundation-contract-skeleton"
        assert item.title.startswith(
            "[DAG:decompose-p-aaade213/foundation-contract-skeleton] ")

    def test_max_parallel_limits_dispatch(self):
        """max_parallel=1 时首轮只派发 1 个节点。"""
        nodes = [_node("a"), _node("b")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)

        assert len(result.dispatched) == 1
        assert len(result.running) == 1

    def test_resume_tick_wakes_existing_in_progress_worker(self):
        """resume 时已处于 in_progress 的节点也要幂等补唤醒执行面。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        item_id = manifest.nodes["a"].work_item_id

        class RecordingRuntime(MockRuntime):
            def __init__(self, store):
                super().__init__(store)
                self.calls = []

            def wake(self, item_id, agent, role):
                self.calls.append((item_id, agent, role))
                super().wake(item_id, agent, role)

        runtime = RecordingRuntime(eng.store)
        result = tick(eng.store, runtime, manifest, path, max_parallel=1)

        assert result.state == "running"
        assert runtime.calls == [(item_id, "alice", "worker")]

    def test_worker_completed_without_submit_bounces_back_to_worker(self):
        """agent run 已终止但未 submit 时,同一 issue 转回 worker 继续处理。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        item_id = manifest.nodes["a"].work_item_id
        eng.store.get_work_item(item_id).agent_run_finished_without_submit = True

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        item = eng.store.get_work_item(item_id)

        assert item.status == WorkItemStatus.IN_PROGRESS
        assert manifest.nodes["a"].status == "in_progress"
        assert result.state == "running"
        assert result.running == ["a"]

    def test_worker_completed_without_submit_exhaustion_does_not_comment(self):
        """worker 未交付耗尽时不发平台评论,避免评论再次触发 agent run。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        item_id = manifest.nodes["a"].work_item_id
        eng.store.get_work_item(item_id).agent_run_finished_without_submit = True

        result = tick(
            eng.store, eng.runtime, manifest, path,
            max_parallel=1, retry_limits={"worker": 0},
        )

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert eng.store.get_comments(item_id) == []


# ==================== 2. 失败注入 → needs_decision ====================

class TestFailureInjection:
    @pytest.mark.parametrize(("reason", "expected"), [
        (DeliveryBlockReason.RETRY_EXHAUSTED, "retry.merge"),
        (DeliveryBlockReason.ASSIGNMENT_FAILED, "assignment failed"),
        (DeliveryBlockReason.WAKE_FAILED, "wake failed"),
        (DeliveryBlockReason.MISSING_PR, "pr_url"),
        (DeliveryBlockReason.MISSING_REVISION, "delivered_revision"),
    ])
    def test_merge_block_reports_actual_delivery_reason(
        self, tmp_path, monkeypatch, reason, expected,
    ):
        node = _node("a")
        node.status = "in_review"
        manifest = _manifest([node])
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = eng.store.create_work_item(
            "ws", "a", "d", dag_key="a", worker="alice", reviewer="bob",
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": "https://mock.example.com/pr/1"},
            verification=_verification(),
            review_verdict="pass",
            review_report=_review_report("pass"),
        )
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        node.work_item_id = item.id
        save_manifest(manifest, path)

        monkeypatch.setattr(
            "omac.pipeline.loop.validate_review_evidence",
            lambda *args, **kwargs: [],
        )

        def blocked_delivery(*args, **kwargs):
            node.status = "blocked"
            eng.store.update_status(item.id, WorkItemStatus.BLOCKED)
            return DeliveryResult(
                action=DeliveryAction.BLOCKED,
                blocked_reason=reason,
                detail="handoff detail",
            )

        monkeypatch.setattr("omac.pipeline.loop.run_merge_delivery", blocked_delivery)

        failures = collect_results(eng.store, eng.runtime, manifest, path)

        assert expected in failures["a"]

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
    def test_preloaded_done_without_authoritative_delivery_is_redispatched(self):
        node = _node("forged-done")
        node.status = "done"
        node.merged = True
        manifest = _manifest([node])
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = tick(
            eng.store, eng.runtime, manifest, path,
            config={"engine": "mock"},
        )

        assert result.state == "running"
        assert result.dispatched == ["forged-done"]
        assert manifest.nodes["forged-done"].status == "in_progress"
        assert manifest.nodes["forged-done"].work_item_id is not None

    def test_preloaded_done_cannot_reuse_cleared_decision_metadata(self):
        node = _node("forged-item-done")
        node.status = "done"
        manifest = _manifest([node])
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = eng.store.create_work_item(
            "ws", "forged", "d", dag_key="forged-item-done", worker="alice")
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": "https://pr/forged"},
            verification={"quality": {"delivered_revision": "head-sha"}},
            review_verdict="pass",
            review_report={"reviewed_revision": "head-sha"},
            decision_required={},
        )
        node.work_item_id = item.id
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "running"
        assert result.dispatched == ["forged-item-done"]
        assert manifest.nodes["forged-item-done"].status == "in_progress"

    def test_runtime_blocks_incomplete_contract_without_dag_check(self):
        contract = _contract()
        contract.objective = ""
        contract.source_of_truth = []
        node = _node("incomplete", contract=contract)
        manifest = _manifest([node])
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert manifest.nodes["incomplete"].status == "blocked"
        reason = next(
            entry["reason"] for entry in result.report["failed_nodes"]
            if entry["key"] == "incomplete")
        assert "objective" in reason
        assert "source_of_truth" in reason

    def test_done_nodes_reused_no_duplicate_issues(self):
        """tick 序列中途重建 loop,done 节点复用 work_item_id,不重复建。"""
        nodes = [_node("a"), _node("b", blocked_by=["a"])]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        # 第一轮 tick:派发 a
        r1 = tick(eng.store, eng.runtime, manifest, path)
        assert "a" in r1.dispatched

        # 第二轮 tick:a 的 Worker 交付完成并派发 Reviewer
        r2 = tick(eng.store, eng.runtime, manifest, path)
        assert "a" in r2.running

        # 第三轮 tick:Reviewer 完成,a 收敛并派发 b
        r3 = tick(eng.store, eng.runtime, manifest, path)
        assert "a" in r3.done

        # 记录 a 的 work_item_id
        a_item_id = manifest.nodes["a"].work_item_id
        assert a_item_id is not None

        # 丢失权威 work_item_id 的 done 不再可信，恢复时必须重新派发。
        eng2 = _engine()
        from omac.core.manifest import set_node
        set_node(manifest, "a", work_item_id=None)

        resumed = tick(eng2.store, eng2.runtime, manifest, path)
        assert "a" in resumed.dispatched
        assert manifest.nodes["a"].status == "in_progress"
        assert manifest.nodes["a"].work_item_id is not None

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
    def test_missing_reviewer_is_blocked_before_delivery(self):
        """develop 节点缺少独立 reviewer 时不得派发或完成。"""
        nodes = [_node("a", reviewer=None, contract=_contract())]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert "a" in result.failed
        assert manifest.nodes["a"].status == "blocked"

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

    @pytest.mark.parametrize("error_type", [AuthError, PlatformError])
    @pytest.mark.parametrize("failed_operation", ["assign", "wake"])
    def test_reviewer_handoff_failure_blocks_with_compensated_state(
        self, tmp_path, monkeypatch, error_type, failed_operation,
    ):
        """Reviewer assign/wake 失败必须形成可恢复的 blocked 状态。"""
        manifest = _manifest([_node("a", reviewer="bob")])
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        item = eng.store.create_work_item(
            "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob",
        )
        eng.store.set_node_contract(item.id, manifest.nodes["a"].contract)
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(),
        )
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        def fail(*args, **kwargs):
            raise error_type(f"reviewer {failed_operation} failed")

        if failed_operation == "assign":
            monkeypatch.setattr(eng.store, "assign_work_item", fail)
        else:
            monkeypatch.setattr(eng.runtime, "wake", fail)

        result = tick(eng.store, eng.runtime, manifest, path)

        got = eng.store.get_work_item(item.id)
        persisted = load_manifest(path)
        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert persisted.nodes["a"].status == "blocked"
        assert got.status == WorkItemStatus.BLOCKED
        assert got.phase == TaskPhase.AUTHORING
        reason = next(
            node["reason"] for node in result.report["failed_nodes"]
            if node["key"] == "a"
        )
        assert failed_operation in reason
        assert "reviewer bob" in reason
        assert f"omac node retry {path} a" in reason


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

    def test_reconcile_rejects_non_running_platform_done_without_delivery(self):
        """Platform DONE alone cannot bypass worker evidence, review, and merge."""
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
        # reconcile 不信任裸 DONE，节点恢复正常派发流程。
        assert "a" not in r.done
        assert "a" in r.dispatched
        assert r.state == "running"

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

    def test_reconcile_clears_missing_blocked_work_item(self):
        """用户删除 blocked issue 后,dag run 应清空旧 id 并重新派发。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        manifest.nodes["a"].work_item_id = "deleted-issue"
        manifest.nodes["a"].status = "blocked"
        save_manifest(manifest, path)

        r = tick(eng.store, eng.runtime, manifest, path)

        assert "a" in r.dispatched
        assert r.state == "running"
        assert manifest.nodes["a"].work_item_id != "deleted-issue"


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

    def test_first_unreviewed_delivery_recovers_from_stale_blocked_manifest(self):
        """首次 worker 合法补交时，旧 blocked manifest 也必须重新进入 reviewer gate。"""
        nodes = [_node("a", reviewer="bob", contract=_contract())]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        item = eng.store.create_work_item(
            "ws", "a", "d", dag_key="a", worker="alice", reviewer="bob",
        )
        eng.store.set_node_contract(item.id, _contract())
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(coverage=90),
        )
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "blocked"
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert got.status == WorkItemStatus.IN_REVIEW
        assert got.phase == TaskPhase.REVIEW


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
        assert any("Evidence gate" in c for c in eng.store.get_comments(item.id))

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
            verification=_verification(coverage=50),
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
            verification=_verification(),
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
        assert got.phase == TaskPhase.REVIEW
        # assign_log 含 reviewer 分配
        assert any(role == "reviewer" for _, _, role, _ in eng.store.assign_log)

    def test_valid_evidence_without_reviewer_is_blocked(self):
        """即使 Worker 证据合法，缺少独立 reviewer 也不得完成。"""
        contract = _contract()
        nodes = [_node("a", reviewer=None, contract=contract)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        item = self._manual_done_item(
            eng, "a", contract=contract,
            artifacts={"pr_url": "https://x/pr/1"},
            verification=_verification(),
        )

        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert "a" in result.failed
        assert manifest.nodes["a"].status == "blocked"

    def test_missing_contract_is_blocked_before_worker_evidence(self):
        """develop 节点缺少 contract 时不能绕过 quality evidence。"""
        nodes = [_node("a", reviewer="bob", contract=None)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert "a" in result.failed
        assert manifest.nodes["a"].status == "blocked"

    def test_duplicate_gate_names_block_before_worker_evidence(
        self, monkeypatch,
    ):
        contract = _contract()
        contract.integration_gates.append(dict(contract.integration_gates[0]))
        manifest = _manifest([_node("a", contract=contract)])
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = self._manual_done_item(
            eng,
            "a",
            contract=contract,
            artifacts={"pr_url": "https://x/pr/1"},
            verification=_verification(),
        )
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        def must_not_validate_worker_evidence(*args, **kwargs):
            raise AssertionError("worker evidence ran before contract validation")

        monkeypatch.setattr(
            "omac.pipeline.loop.validate_worker_evidence",
            must_not_validate_worker_evidence,
        )

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert any(
            "duplicate integration gate name" in comment
            for comment in eng.store.get_comments(item.id)
        )

    def test_duplicate_gate_names_block_before_reviewer_evidence(
        self, monkeypatch,
    ):
        contract = _contract()
        contract.integration_gates.append(dict(contract.integration_gates[0]))
        manifest = _manifest([_node("a", contract=contract)])
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = self._manual_done_item(
            eng,
            "a",
            contract=contract,
            artifacts={"pr_url": "https://x/pr/1"},
            verification=_verification(),
        )
        eng.store.update_work_item_metadata(
            item.id,
            phase=TaskPhase.REVIEW,
            review_verdict="pass",
            review_report=_review_report(),
        )
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        save_manifest(manifest, path)

        def must_not_validate_review_evidence(*args, **kwargs):
            raise AssertionError("reviewer evidence ran before contract validation")

        monkeypatch.setattr(
            "omac.pipeline.loop.validate_review_evidence",
            must_not_validate_review_evidence,
        )

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert any(
            "duplicate integration gate name" in comment
            for comment in eng.store.get_comments(item.id)
        )

    def test_required_contracts_use_manifest_project_root_at_runtime(
        self, tmp_path, monkeypatch,
    ):
        project_root = tmp_path / "project"
        manifest_dir = project_root / ".omac"
        manifest_dir.mkdir(parents=True)
        required_contract = project_root / "contracts" / "shared.md"
        required_contract.parent.mkdir()
        required_contract.write_text("# shared contract\n")
        unrelated_cwd = tmp_path / "elsewhere"
        unrelated_cwd.mkdir()
        monkeypatch.chdir(unrelated_cwd)

        contract = _contract()
        contract.required_contracts = ["contracts/shared.md"]
        manifest = _manifest([_node("a", contract=contract)])
        path = str(manifest_dir / "manifest.yaml")
        save_manifest(manifest, path)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"


# ==================== AITEAM-354:reviewer reject 有界回退受 retry.review 控制 ====================

class TestReviewerRejectBoundedFallback:
    """节点 reviewer reject 的「回到 worker」回退次数受 config.retry.review 控制。

    - retry.review=0 → reject 立即 blocked,不回退
    - retry.review=1 → 允许 1 次回退,第二次 reject 耗尽 → blocked
    - review_bounce 按节点按类独立计数
    通过 tick(..., retry_limits=...) 注入上限,与未来 dag run 读 config 消费同形。
    """

    @staticmethod
    def _simple_contract():
        return _contract(
            verification_commands=["pytest -q"],
            integration_gates=[{
                "name": "gate-1", "layer": "L1", "delivery_goal": "delivers",
                "source_of_truth": ["docs/d.md"], "covers": ["route"],
                "acceptance_refs": ["works"], "commands": ["pytest -q"],
                "required_metrics": {"route_coverage": 100},
                "artifacts": ["coverage.xml"],
            }],
            pr_base="main",
            coverage_gate=0,
        )

    def _setup_reject_node(self, eng, path, key="a", worker="alice", reviewer="bob",
                           contract=None):
        from omac.core.manifest import Manifest, Node
        contract = contract or self._simple_contract()
        node = Node(id=key, worker=worker, reviewer=reviewer, title=key,
                    description=f"Task {key}", contract=contract)
        manifest = Manifest(meta={"workspace_id": "ws"}, nodes={node.id: node})
        save_manifest(manifest, path)

        # tick 1: 派发 worker
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        # 手动模拟 worker 合规提交(DONE + 过证据门),让节点进入 in_review
        item = eng.store.get_work_item(manifest.nodes[key].work_item_id)
        eng.store.set_node_contract(item.id, contract)
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(
                gate_command="pytest -q", pr_base="main", coverage=90),
        )
        eng.store.update_status(item.id, __import__("omac").engines.models.WorkItemStatus.DONE)

        # tick 2: worker 完成 → 转评审(in_review + assign reviewer)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        from omac.core.manifest import set_node
        set_node(manifest, key, status="in_review")
        save_manifest(manifest, path)

        # 置为 reject 评审结论
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="reject",
            review_report=_review_report("reject"),
        )
        eng.store.update_status(
            item.id, __import__("omac").engines.models.WorkItemStatus.IN_REVIEW)
        return manifest, eng, item

    def test_retry_review_zero_blocks_immediately(self, tmp_path):
        """retry.review=0 → 首次 reject 立即 blocked,review_bounce 保持 0。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        manifest, eng, item = self._setup_reject_node(eng, str(tmp_path / "m.yaml"))
        path = str(tmp_path / "m.yaml")

        result = tick(eng.store, eng.runtime, manifest, path,
                      max_parallel=4, retry_limits={"review": 0})

        got = eng.store.get_work_item(item.id)
        assert manifest.nodes["a"].status == "blocked"
        assert got.bounces.review == 0
        assert any("retry limit" in c for c in eng.store.get_comments(item.id))
        assert result.state == "needs_decision"

    def test_valid_reject_report_still_bounces_worker(self, tmp_path):
        """结构合法的 reject report 是业务拒绝,不能因为证据合法就把节点置 done。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id,
            review_report=_review_report("reject"),
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict is None
        assert got.bounces.review == 1

    def test_pass_with_nits_accepts_worker_followup_without_second_review(self, tmp_path):
        """pass-with-nits 只回 worker 修一次;worker 重交后直接 done,不再派 reviewer。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="pass-with-nits",
            review_report=_review_report("pass-with-nits"),
        )

        first = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert first.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        got = eng.store.get_work_item(item.id)
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict == "pass-with-nits"
        assert got.decision_required is None
        assert got.bounces.review == 0

        reviewer_dispatches_before_followup = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"])
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(
                gate_command="pytest -q", pr_base="main", coverage=90,
                delivered_revision="head-sha-nits"),
        )
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        second = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert second.state == "converged"
        assert manifest.nodes["a"].status == "done"
        assert "a" not in second.failed
        reviewer_dispatches_after_followup = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"])
        assert reviewer_dispatches_after_followup == reviewer_dispatches_before_followup
        got = eng.store.get_work_item(item.id)
        assert got.status == WorkItemStatus.DONE
        assert got.review_verdict == "pass-with-nits"
        assert got.bounces.review == 0

    def test_invalid_pass_with_nits_report_cannot_skip_reviewer_evidence_gate(self, tmp_path):
        """pass-with-nits 流程不变，但前提是完整 report 已通过证据门。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        report = _review_report("pass-with-nits")
        report["review_scope"]["all_changed_files_reviewed"] = False
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="pass-with-nits",
            review_report=report,
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict is None
        assert got.bounces.review == 1

    def test_untrusted_done_node_is_reopened_for_authoritative_delivery(self, tmp_path):
        """缺少 merged 权威事实的 done 不得覆盖平台状态并伪造收口。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        manifest.nodes["a"].status = "done"
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        eng.store.update_work_item_metadata(item.id, review_verdict="pass-with-nits")
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS

    def test_done_node_with_reject_verdict_is_recovered_to_worker(self, tmp_path):
        """旧版本可能把合法 reject 误置 done;resume 应识别并转回 worker。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        manifest.nodes["a"].status = "done"
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="reject",
            review_report=_review_report("reject"),
        )
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict is None
        assert got.bounces.review == 1

    @pytest.mark.parametrize("stale_status", ["todo", "blocked", "done"])
    def test_unreviewed_worker_revision_reenters_review_from_stale_manifest(
        self, tmp_path, stale_status,
    ):
        """worker 返工已 submit 时，retry/todo 等旧状态不得绕过 reviewer gate。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / f"{stale_status}.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id,
            review_report=_review_report("reject"),
        )

        # reviewer reject → worker authoring；保留上一轮 report 作为返工上下文。
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert manifest.nodes["a"].status == "in_progress"

        # worker 合法重交，但旧 controller/manifest 留下 terminal 状态。
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(
                gate_command="pytest -q", pr_base="main", coverage=90),
        )
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].status = stale_status
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert got.status == WorkItemStatus.IN_REVIEW
        assert got.phase == TaskPhase.REVIEW

    def test_authoring_node_repairs_worker_manual_in_review(self, tmp_path):
        """authoring 阶段被 worker 手改成 in_review 时,拉回 in_progress 等合法 submit。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        manifest.nodes["a"].status = "in_progress"
        eng.store.reset_review(item.id)
        eng.store.update_work_item_metadata(item.id, phase=TaskPhase.AUTHORING)
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS

    def test_retry_review_one_allows_single_fallback(self, tmp_path):
        """retry.review=1 → 第 1 次 reject 回退 worker(bounce→1),第 2 次 reject 耗尽 → blocked。"""
        from omac.engines import create_engine
        from omac.core.manifest import set_node
        from omac.engines.models import WorkItemStatus
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        fpath = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, fpath)

        # 第 1 次 reject:回退 worker,review_bounce 0→1
        tick(eng.store, eng.runtime, manifest, fpath,
             max_parallel=4, retry_limits={"review": 1})
        got = eng.store.get_work_item(item.id)
        assert manifest.nodes["a"].status == "in_progress"
        assert got.bounces.review == 1
        # 评审结论已清除,等待重新评审
        assert got.review_verdict is None

        # 模拟 worker 修完重新提交(合规)→ 再次 in_review
        eng.store.set_node_contract(item.id, self._simple_contract())
        eng.store.update_work_item_metadata(
            item.id, review_verdict=None, review_report=None, review_comment=None,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(
                gate_command="pytest -q", pr_base="main", coverage=90))
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
        set_node(manifest, "a", status="in_review")
        save_manifest(manifest, fpath)
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="reject",
            review_report=_review_report("reject"),
        )
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

        # 第 2 次 reject:已耗尽 → blocked
        tick(eng.store, eng.runtime, manifest, fpath,
             max_parallel=4, retry_limits={"review": 1})
        got = eng.store.get_work_item(item.id)
        assert manifest.nodes["a"].status == "blocked"
        assert got.bounces.review == 1  # 不再增长,已达上界

    def test_retry_review_default_three_allows_multiple_fallbacks(self, tmp_path):
        """缺省(retry.review 未传入=3)→ 连续 3 次 reject 均回退 worker。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        fpath = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, fpath)

        # 不传 retry_limits:使用 DEFAULT_RETRY 缺省(review=3)
        for i in range(3):
            eng.store.update_work_item_metadata(item.id, review_verdict="reject")
            eng.store.update_status(
                item.id, __import__("omac").engines.models.WorkItemStatus.IN_REVIEW)
            tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
            got = eng.store.get_work_item(item.id)
            assert manifest.nodes["a"].status == "in_progress", f"第 {i+1} 次应回退 worker"
            # 推进:worker 修完重新提交 → in_review
            eng.store.set_node_contract(item.id, self._simple_contract())
            eng.store.update_work_item_metadata(
                item.id, review_verdict=None, review_report=None, review_comment=None,
                artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
                verification=_verification(
                    gate_command="pytest -q", pr_base="main", coverage=90))
            eng.store.update_status(
                item.id, __import__("omac").engines.models.WorkItemStatus.DONE)
            tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
            from omac.core.manifest import set_node
            set_node(manifest, "a", status="in_review")
            save_manifest(manifest, fpath)


class TestReviewerRejectFallbackRollback:
    """Nit 3:回退到 worker 失败时应回滚 review_bounce 并把节点标 blocked。"""

    @staticmethod
    def _simple_contract():
        return TestReviewerRejectBoundedFallback._simple_contract()

    def _setup_reject_node(self, eng, fpath, key="a", worker="alice", reviewer="bob"):
        from omac.core.manifest import Manifest, Node, set_node
        contract = self._simple_contract()
        node = Node(id=key, worker=worker, reviewer=reviewer, title=key,
                    description=f"Task {key}", contract=contract)
        manifest = Manifest(meta={"workspace_id": "ws"}, nodes={node.id: node})
        save_manifest(manifest, fpath)

        tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
        item = eng.store.get_work_item(manifest.nodes[key].work_item_id)
        eng.store.set_node_contract(item.id, contract)
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(
                gate_command="pytest -q", pr_base="main", coverage=90))
        from omac.engines.models import WorkItemStatus
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
        set_node(manifest, key, status="in_review")
        save_manifest(manifest, fpath)
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="reject",
            review_comment="核心业务行为未满足",
            review_report=_review_report("reject"),
        )
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        return manifest, eng, item

    @pytest.mark.parametrize("error_type", [AuthError, PlatformError])
    @pytest.mark.parametrize("failed_operation", ["assign", "wake"])
    def test_fallback_failure_restores_review_state_and_blocks(
        self, tmp_path, monkeypatch, error_type, failed_operation,
    ):
        """Reject 回派的 assign/wake 失败必须回滚并保留可诊断评审状态。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        fpath = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, fpath)

        def fail(*args, **kwargs):
            raise error_type(f"worker {failed_operation} failed")

        if failed_operation == "assign":
            monkeypatch.setattr(eng.store, "assign_work_item", fail)
        else:
            monkeypatch.setattr(eng.runtime, "wake", fail)

        result = tick(
            eng.store, eng.runtime, manifest, fpath,
            max_parallel=4, retry_limits={"review": 3},
        )

        got = eng.store.get_work_item(item.id)
        persisted = load_manifest(fpath)
        assert got.bounces.review == 0
        assert got.phase == TaskPhase.REVIEW
        assert got.review_verdict == "reject"
        assert got.review_comment == "核心业务行为未满足"
        assert manifest.nodes["a"].status == "blocked"
        assert persisted.nodes["a"].status == "blocked"
        assert got.status == WorkItemStatus.BLOCKED
        assert result.state == "needs_decision"
        reason = next(
            node["reason"] for node in result.report["failed_nodes"]
            if node["key"] == "a"
        )
        assert failed_operation in reason
        assert "worker alice" in reason
        assert f"omac node retry {fpath} a" in reason


class TestPipelineStateMachineRecoveryWindows:
    def test_runtime_review_uses_authoritative_changed_files(
        self, tmp_path,
    ):
        """运行时 Reviewer gate 不得只信 review_scope 自报文件列表。"""
        manifest = _manifest([_node("a", reviewer="bob")])
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        eng = _engine(
            MOCK_AUTO_COMPLETE="false",
            MOCK_PR_AUTHOR="alice",
            MOCK_PR_COMMIT_AUTHORS='["alice"]',
            MOCK_PR_BASE_REVISION="base-sha",
            MOCK_PR_CHANGED_FILES='["src/actual.py"]',
            MOCK_PR_HEAD_REVISION="head-sha",
        )
        item = eng.store.create_work_item(
            "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob",
        )
        report = _review_report("pass")
        report["integration_gate_mapping"][0]["commands"][0]["cmd"] = (
            "pytest tests/int"
        )
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(),
            review_verdict="pass",
            review_report=report,
            phase=TaskPhase.REVIEW,
        )
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        save_manifest(manifest, path)

        result = tick(
            eng.store,
            eng.runtime,
            manifest,
            path,
            retry_limits={"review": 0},
        )

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        reason = next(
            node["reason"] for node in result.report["failed_nodes"]
            if node["key"] == "a"
        )
        assert "authoritative PR changed_files" in reason

    def test_downstream_merge_waits_for_dependency_collection_to_settle(
        self, tmp_path, monkeypatch,
    ):
        """同轮上游失败时，下游不得在级联阻断前产生 merge 副作用。"""
        upstream = _node("a")
        downstream = _node("b", blocked_by=["a"])
        manifest = _manifest([downstream, upstream])
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        upstream_item = eng.store.create_work_item(
            "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob",
        )
        eng.store.update_status(upstream_item.id, WorkItemStatus.FAILED)
        manifest.nodes["a"].work_item_id = upstream_item.id
        manifest.nodes["a"].status = "in_progress"

        downstream_item = eng.store.create_work_item(
            "ws", "b", "Task b", dag_key="b", worker="alice", reviewer="bob",
        )
        eng.store.update_work_item_metadata(
            downstream_item.id,
            artifacts={"pr_url": "https://mock.example.com/pr/b"},
            verification=_verification(),
            review_verdict="pass",
            review_report=_review_report("pass"),
            phase=TaskPhase.REVIEW,
        )
        eng.store.update_status(downstream_item.id, WorkItemStatus.DONE)
        manifest.nodes["b"].work_item_id = downstream_item.id
        manifest.nodes["b"].status = "in_review"
        save_manifest(manifest, path)

        merge_calls = []

        def record_merge(*args, **kwargs):
            merge_calls.append("b")
            return DeliveryResult(DeliveryAction.PASS)

        monkeypatch.setattr(
            "omac.pipeline.loop.validate_review_evidence",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr("omac.pipeline.loop.run_merge_delivery", record_merge)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert merge_calls == []
        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert manifest.nodes["b"].status == "blocked"

    def test_created_work_item_id_is_durable_before_dispatch_metadata_write(
        self, tmp_path, monkeypatch,
    ):
        """建单成功后的平台故障不得让重启再次创建同一 DAG 工单。"""
        manifest = _manifest([_node("a")])
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        real_update = eng.store.update_work_item_metadata
        failed = False

        def fail_first_metadata_write(item_id, **kwargs):
            nonlocal failed
            if not failed and "description" in kwargs:
                failed = True
                raise PlatformError("issue metadata write failed; retry later")
            return real_update(item_id, **kwargs)

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", fail_first_metadata_write)

        result = tick(eng.store, eng.runtime, manifest, path)

        persisted = load_manifest(path)
        assert result.state == "needs_decision"
        assert persisted.nodes["a"].work_item_id is not None
        assert persisted.nodes["a"].status == "blocked"
        assert len(eng.store.list_work_items("ws")) == 1
        reason = next(
            node["reason"] for node in result.report["failed_nodes"]
            if node["key"] == "a"
        )
        assert "metadata write failed" in reason
        assert f"omac node retry {path} a" in reason

    def test_reviewer_phase_write_failure_blocks_without_split_state(
        self, tmp_path, monkeypatch,
    ):
        """Reviewer 阶段元数据写入失败时不得留下 IN_REVIEW/authoring 分裂。"""
        manifest = _manifest([_node("a", reviewer="bob")])
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = eng.store.create_work_item(
            "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob",
        )
        eng.store.set_node_contract(item.id, manifest.nodes["a"].contract)
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}"},
            verification=_verification(),
        )
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        real_update = eng.store.update_work_item_metadata
        failed = False

        def fail_review_phase_once(item_id, **kwargs):
            nonlocal failed
            if not failed and kwargs.get("phase") == TaskPhase.REVIEW:
                failed = True
                raise PlatformError("review phase persistence failed")
            return real_update(item_id, **kwargs)

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", fail_review_phase_once)

        result = tick(eng.store, eng.runtime, manifest, path)

        got = eng.store.get_work_item(item.id)
        persisted = load_manifest(path)
        assert result.state == "needs_decision"
        assert got.phase == TaskPhase.AUTHORING
        assert got.status == WorkItemStatus.BLOCKED
        assert persisted.nodes["a"].status == "blocked"
        reason = next(
            node["reason"] for node in result.report["failed_nodes"]
            if node["key"] == "a"
        )
        assert "review phase persistence failed" in reason
        assert f"omac node retry {path} a" in reason

    def test_reconcile_platform_read_failure_does_not_create_duplicate(
        self, tmp_path, monkeypatch,
    ):
        """平台读取故障不是 not-found；必须保留原 ID 并 fail closed。"""
        manifest = _manifest([_node("a")])
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = eng.store.create_work_item(
            "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob",
        )
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)
        real_get = eng.store.get_work_item

        def fail_read(item_id):
            if item_id == item.id:
                raise PlatformError("platform read failed; retry after connectivity recovers")
            return real_get(item_id)

        monkeypatch.setattr(eng.store, "get_work_item", fail_read)

        with pytest.raises(PlatformError, match="platform read failed"):
            tick(eng.store, eng.runtime, manifest, path)

        persisted = load_manifest(path)
        assert persisted.nodes["a"].work_item_id == item.id
        assert len(eng.store.list_work_items("ws")) == 1

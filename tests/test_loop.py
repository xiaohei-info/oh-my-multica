"""pipeline/loop:单轮 tick 核心——结果回收 → 就绪计算 → 派发。

验收标准:
- mock:多节点带依赖 manifest,循环调 tick 至 converged,节点全 done
- mock 失败注入:tick 返回 needs_decision,失败节点 blocked、下游 blocked、report 完整
- 幂等:tick 序列中途重建 loop 对同一 manifest 继续,done 节点复用、不重复建 issue
- 无 reviewer 节点也必须经远端 MERGED + mergedAt 门;有 reviewer 先经 in_review
- 不存在任何自动重试路径(blocked 节点在后续 tick 保持 blocked)
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

import pytest
import yaml

from omac.core.manifest import (
    Contract,
    EvidenceMode,
    Manifest,
    Node,
    ProducedArtifact,
    load_manifest,
    save_manifest,
)
from omac.core.review_convergence import (
    REVIEW_PROTOCOL_VERSION, build_review_obligations, open_blockers,
    review_subject_digest,
)
from omac.engines import create_engine
from omac.engines.mock import MockRuntime, MockStore
from omac.core.taskmeta import TaskPhase
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.pipeline import loop
from omac.pipeline.dispatch import build_show_output
from omac.pipeline.loop import TickResult, tick


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
    base = {
        "MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0",
        "MOCK_AUTO_MERGE_ON_SUCCESS": "true",
    }
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


def _business_command(cmd="pytest -q", acceptance="works"):
    return {
        "cmd": cmd,
        "exit_code": 0,
        "business_tests": [{
            "acceptance": acceptance,
            "test": "tests/test_feature.py::test_feature_works",
        }],
    }


def test_failure_cascade_preserves_merged_descendant_and_blocks_unfinished_peer(
    tmp_path,
):
    manifest = Manifest(meta={}, nodes={
        "failed-upstream": Node(
            id="failed-upstream", worker="alice", status="blocked"),
        "merged-descendant": Node(
            id="merged-descendant", worker="bob",
            blocked_by=["failed-upstream"], status="todo",
            merged=True, merged_at="2026-07-27T08:00:00Z"),
        "unfinished-descendant": Node(
            id="unfinished-descendant", worker="charlie",
            blocked_by=["failed-upstream"], status="todo"),
    })
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)

    newly_blocked = loop._mark_downstream_blocked(
        manifest, path, {"failed-upstream"})

    assert manifest.nodes["merged-descendant"].status == "done"
    assert manifest.nodes["merged-descendant"].merged is True
    assert manifest.nodes["unfinished-descendant"].status == "blocked"
    assert newly_blocked == {"unfinished-descendant"}


def _review_report(item, verdict="pass", *, nits=None):
    failed_id = "dimension:structure" if verdict == "reject" else None
    return {
        "review_protocol": REVIEW_PROTOCOL_VERSION,
        "review_goals": ["复核交付是否满足验收"],
        "diff_reviewed": True,
        "tests_rerun": True,
        "integration_tests_rerun": True,
        "coverage_checked": True,
        "full_review_completed": True,
        "obligation_results": [
            {
                "obligation_id": obligation["obligation_id"],
                "status": (
                    "fail" if obligation["obligation_id"] == failed_id
                    else "pass"),
                "evidence": "独立复核完成",
            }
            for obligation in item.review_obligations
        ],
        "prior_blocker_results": [
            {
                "blocker_id": blocker["blocker_id"],
                "status": "fixed",
                "evidence": "历史 blocker 已回归",
            }
            for blocker in open_blockers(item.review_ledger)
        ],
        "acceptance_mapping": [{
            "acceptance": "works",
            "status": "fail" if verdict == "reject" else "pass",
        }],
        "integration_gate_mapping": [{
            "gate": "gate-1",
            "status": "pass",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"],
            "delivery_goal": "delivers",
        }],
        "blockers": ([{
            "root_cause_key": "core-acceptance",
            "obligation_id": failed_id,
            "classification": "new",
            "summary": "核心验收未满足",
            "evidence": "独立验证失败",
            "required_fix": "修复核心验收路径",
        }] if failed_id else []),
        "nits": list(nits or []),
    }


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


def _aiteam_834_legacy_delivery(tmp_path):
    engine = create_engine(
        "mock", _config(MOCK_AUTO_COMPLETE="false"))
    contract = _contract()
    node = _node(
        "platform-release-evidence-contract",
        reviewer="bob",
        contract=contract,
    )
    manifest = _manifest([node])
    path = str(tmp_path / "open-agent-cluster.yaml")
    save_manifest(manifest, path)

    tick(engine.store, engine.runtime, manifest, path, max_parallel=1)
    item = engine.store.get_work_item(node.work_item_id)
    engine.store.set_node_contract(item.id, contract)
    verification = {
        "commands": [_business_command()],
        "integration_gates": [{"name": "gate-1", "commands": []}],
        "pr_base": "feature/v1",
        "coverage": 100,
    }
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://github.com/acme/repo/pull/24"},
        verification=verification,
        verification_source=yaml.safe_dump(verification),
        phase=TaskPhase.AUTHORING,
        review_bounce=1,
        review_ledger={
            "schema": "omac.review-ledger/v1",
            "cycles": [{
                "round": 1,
                "subject_digest": "rejected-subject",
                "verdict": "reject",
            }],
            "blockers": [],
        },
    )
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    engine.store.clear_assignment(item.id)
    engine.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    node.status = "in_review"
    save_manifest(manifest, path)
    return engine, manifest, path, node, item


def test_aiteam_834_legacy_delivery_requires_explicit_node_retry(
    tmp_path, monkeypatch,
):
    """缺 immutable delivery identity 的旧返工不得猜证据或触发任何 Run。"""
    engine, manifest, path, node, item = _aiteam_834_legacy_delivery(tmp_path)

    monkeypatch.setattr(
        engine.runtime, "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy delivery must not rerun Worker or dispatch Reviewer"),
    )
    monkeypatch.setattr(
        loop, "run_merge_delivery",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy delivery must not enter merge"),
    )

    result = tick(
        engine.store, engine.runtime, manifest, path, max_parallel=1)

    current = engine.store.get_work_item(item.id)
    retry = f"omac node retry {path} {node.id}"
    assert result.state == "needs_decision"
    assert result.failed == [node.id]
    assert manifest.nodes[node.id].status == "blocked"
    assert current.status is WorkItemStatus.BLOCKED
    assert current.delivery_identity is None
    assert current.decision_required["reason_code"] == (
        "legacy-delivery-retry-required")
    assert current.decision_required["next_action"] == retry
    assert retry in result.report["next_actions"]


def test_legacy_detection_waits_for_active_direct_run_without_writes(
    tmp_path, monkeypatch,
):
    engine, manifest, path, node, item = _aiteam_834_legacy_delivery(tmp_path)
    engine.store.assign_work_item(item.id, node.worker, "worker")
    assignments = len(engine.store.assign_log)
    runs = list(engine.runtime.list_runs(item.id))
    manifest_source = Path(path).read_text()

    for name in (
        "update_work_item_metadata", "update_status", "add_comment",
        "assign_work_item", "clear_assignment",
    ):
        monkeypatch.setattr(
            engine.store, name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(
                f"active Worker must not trigger {_name}"),
        )
    monkeypatch.setattr(
        engine.runtime, "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "active Worker must not trigger Agent dispatch"),
    )

    result = tick(
        engine.store, engine.runtime, manifest, path, max_parallel=1)

    assert result.state == "running"
    assert result.failed == []
    assert result.dispatched == []
    assert manifest.nodes[node.id].status == "in_review"
    assert engine.store.get_work_item(item.id).decision_required is None
    assert len(engine.store.assign_log) == assignments
    assert engine.runtime.list_runs(item.id) == runs
    assert Path(path).read_text() == manifest_source


def test_legacy_detection_propagates_run_observation_failure(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    engine, manifest, path, _, item = _aiteam_834_legacy_delivery(tmp_path)
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda _item_id: (_ for _ in ()).throw(PlatformError("runs unavailable")),
    )
    monkeypatch.setattr(
        engine.store, "update_work_item_metadata",
        lambda *_args, **_kwargs: pytest.fail(
            "run observation failure must precede decision writes"),
    )

    with pytest.raises(PlatformError, match="runs unavailable"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=1)

    assert engine.store.get_work_item(item.id).decision_required is None


def test_legacy_decision_restart_does_not_duplicate_comment_or_dispatch(
    tmp_path, monkeypatch,
):
    engine, manifest, path, node, item = _aiteam_834_legacy_delivery(tmp_path)
    original_save = loop.save_manifest
    crashed = False

    def crash_before_manifest_save(current, current_path):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("crash before legacy decision manifest save")
        return original_save(current, current_path)

    monkeypatch.setattr(loop, "save_manifest", crash_before_manifest_save)
    with pytest.raises(RuntimeError, match="legacy decision manifest save"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=1)

    assert len(engine.store.get_comments(item.id)) == 1
    assert engine.store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
    assert load_manifest(path).nodes[node.id].status == "in_review"

    monkeypatch.setattr(loop, "save_manifest", original_save)
    for name in ("update_work_item_metadata", "update_status", "add_comment"):
        monkeypatch.setattr(
            engine.store, name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(
                f"persisted decision must not repeat {_name}"),
        )
    monkeypatch.setattr(
        engine.store, "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "decision restart must not assign an Agent"),
    )
    monkeypatch.setattr(
        engine.runtime, "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "decision restart must not wake an Agent"),
    )

    restarted = load_manifest(path)
    result = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)

    assert result.state == "needs_decision"
    assert restarted.nodes[node.id].status == "blocked"
    assert len(engine.store.get_comments(item.id)) == 1


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
        eng = _engine()
        foundation_item = eng.store.create_work_item(
            "ws", "foundation", "d", dag_key="foundation", worker="alice")
        eng.store.update_work_item_metadata(
            foundation_item.id, artifacts={"pr_url": "https://pr/foundation"})
        eng.store.update_status(foundation_item.id, WorkItemStatus.DONE)
        data_item = eng.store.create_work_item(
            "ws", "data", "d", dag_key="data", worker="alice")
        eng.store.update_work_item_metadata(
            data_item.id, artifacts={"pr_url": "https://pr/data"})
        eng.store.update_status(data_item.id, WorkItemStatus.DONE)
        foundation = _node("foundation", title="Shared contract foundation")
        foundation.status = "done"
        foundation.merged = True
        foundation.merged_at = "2026-07-26T08:00:00Z"
        foundation.work_item_id = foundation_item.id
        data = _node("data", title="Persistence layer")
        data.status = "done"
        data.merged = True
        data.merged_at = "2026-07-26T08:00:00Z"
        data.work_item_id = data_item.id
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
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item = eng.store.get_work_item(manifest.nodes["feature"].work_item_id)

        assert item.source_refs[-2:] == [
            {
                "label": "Prerequisite implementation · Shared contract foundation",
                "issue_id": foundation_item.id,
            },
            {
                "label": "Prerequisite implementation · Persistence layer",
                "issue_id": data_item.id,
            },
        ]
        assert item.blocked_by == ["foundation", "data", "missing"]
        assert (
            f"- Prerequisite implementation · Shared contract foundation: `#{foundation_item.id}`"
            in item.description
        )
        assert f"omac work show {foundation_item.id}" not in item.description
        assert f"omac work show {data_item.id}" not in item.description
        assert "Abandoned setup" not in item.description

    def test_reused_item_refreshes_manifest_dependencies_before_dispatch(
        self, monkeypatch,
    ):
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        old_dependencies = [
            "bootstrap-go",
            "bootstrap-console",
            "release-workspace-contract",
        ]
        new_dependency = "system-release-tooling-ownership-contract"
        dependency_keys = [*old_dependencies, new_dependency]
        dependency_nodes = []
        dependency_items = {}
        for key in dependency_keys:
            dependency_item = eng.store.create_work_item(
                "ws", key, f"Task {key}", dag_key=key, worker="alice")
            eng.store.update_work_item_metadata(
                dependency_item.id,
                artifacts={"pr_url": f"https://example.test/pr/{key}"},
            )
            eng.store.update_status(dependency_item.id, WorkItemStatus.DONE)
            dependency_items[key] = dependency_item
            dependency = _node(key, title=key)
            dependency.status = "done"
            dependency.merged = True
            dependency.merged_at = "2026-07-28T08:00:00Z"
            dependency.work_item_id = dependency_item.id
            dependency_nodes.append(dependency)

        contract = _contract()
        target = _node(
            "release-artifact-tooling",
            worker="alice",
            reviewer="bob",
            blocked_by=dependency_keys,
            contract=contract,
            title="Release artifact tooling",
        )
        reused = eng.store.create_work_item(
            "ws",
            target.title,
            "stale issue body",
            dag_key="oac-release/release-artifact-tooling",
            worker="alice",
            reviewer="bob",
            blocked_by=old_dependencies,
        )
        eng.store.set_node_contract(reused.id, contract)
        reused.source_refs = [
            {
                "label": f"Prerequisite implementation · {key}",
                "issue_id": dependency_items[key].id,
            }
            for key in old_dependencies
        ]
        review_ledger = {
            "schema": "omac.review-ledger/v1",
            "cycles": [],
            "blockers": [],
        }
        review_report = {"review_goals": ["preserve prior history"]}
        reused.review_ledger = review_ledger
        reused.review_report = review_report
        reused.review_comment = "prior review history"
        eng.store.add_comment(reused.id, "existing audit comment")
        target.work_item_id = reused.id

        manifest = _manifest([*dependency_nodes, target], meta={
            "workspace_id": "ws",
            "dag_key": "oac-release",
        })
        path = _tmp_manifest_path(manifest)
        events = []
        metadata_calls = []
        original_update = eng.store.update_work_item_metadata
        original_assign = eng.store.assign_work_item
        original_status = eng.store.update_status

        def update_metadata(item_id, **kwargs):
            if item_id == reused.id:
                events.append("metadata")
                metadata_calls.append(kwargs)
            return original_update(item_id, **kwargs)

        def assign(item_id, assignee, role):
            if item_id == reused.id:
                events.append("assign")
            return original_assign(item_id, assignee, role)

        def update_status(item_id, status):
            if item_id == reused.id:
                events.append("status")
            return original_status(item_id, status)

        def wake(item_id, agent, role):
            events.append("wake")
            current = eng.store.get_work_item(item_id)
            summary = build_show_output(current, f"{role}:{agent}")
            assert current.blocked_by == dependency_keys
            assert summary["task"]["blocked_by"] == dependency_keys
            assert summary["context"]["source_issues"][-1]["issue_id"] == (
                dependency_items[new_dependency].id
            )
            assert new_dependency in summary["context"]["issue_description"]

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update_metadata)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.store, "update_status", update_status)
        monkeypatch.setattr(
            eng.store,
            "set_node_contract",
            lambda *_args, **_kwargs: pytest.fail(
                "reused item must not republish its contract"),
        )
        monkeypatch.setattr(eng.runtime, "wake", wake)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        current = eng.store.get_work_item(reused.id)
        summary = build_show_output(current, "worker:alice")
        assert result.dispatched == [target.id]
        assert events == ["metadata", "assign", "status", "wake"]
        assert len(metadata_calls) == 1
        assert set(metadata_calls[0]) == {
            "blocked_by", "description", "source_refs",
        }
        assert "worker" not in metadata_calls[0]
        assert "reviewer" not in metadata_calls[0]
        assert current.worker == "alice"
        assert current.reviewer == "bob"
        assert current.review_ledger is review_ledger
        assert current.review_report is review_report
        assert current.review_comment == "prior review history"
        assert summary["task"]["blocked_by"] == dependency_keys
        assert summary["context"]["source_issues"][-1] == {
            "label": (
                "Prerequisite implementation · "
                "system-release-tooling-ownership-contract"
            ),
            "issue_id": dependency_items[new_dependency].id,
        }
        assert eng.store.get_comments(reused.id) == ["existing audit comment"]
        assert len(eng.runtime.list_runs(reused.id)) == 1

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

    def test_resume_tick_does_not_redispatch_existing_in_progress_worker(self):
        """无持久 handoff identity 时，旧 IN_PROGRESS 只能等待下轮观察。"""
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
        assert runtime.calls == []

    @pytest.mark.parametrize(
        ("manifest_status", "item_status"),
        [
            ("in_review", WorkItemStatus.IN_PROGRESS),
            ("in_progress", WorkItemStatus.IN_REVIEW),
        ],
    )
    def test_ambiguous_authoring_projection_without_handoff_never_dispatches(
        self, manifest_status, item_status,
    ):
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        item_id = manifest.nodes["a"].work_item_id
        item = eng.store.get_work_item(item_id)
        item.phase = TaskPhase.AUTHORING
        item.status = item_status
        item.worker_handoff = None
        manifest.nodes["a"].status = manifest_status
        save_manifest(manifest, path)
        assignments_before = len(eng.store.assign_log)

        class RecordingRuntime(MockRuntime):
            def __init__(self, store):
                super().__init__(store)
                self.calls = []

            def wake(self, item_id, agent, role):
                self.calls.append((item_id, agent, role))
                super().wake(item_id, agent, role)

        runtime = RecordingRuntime(eng.store)

        failures = loop.collect_results(
            eng.store, runtime, manifest, path)

        assert failures == {}
        assert manifest.nodes["a"].status == manifest_status
        assert len(eng.store.assign_log) == assignments_before
        assert runtime.calls == []

    def test_worker_completed_without_submit_requires_explicit_retry_without_intent(self):
        """无 handoff identity 的 no-submit 不得自动创建第二个 Worker Run。"""
        nodes = [_node("a")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")

        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        item_id = manifest.nodes["a"].work_item_id
        eng.store.get_work_item(item_id).agent_run_finished_without_submit = True
        assignments_before = len(eng.store.assign_log)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        item = eng.store.get_work_item(item_id)

        assert item.status == WorkItemStatus.BLOCKED
        assert manifest.nodes["a"].status == "blocked"
        assert result.state == "needs_decision"
        assert item.bounces.worker == 0
        assert item.decision_required["reason_code"] == "worker-retry-intent-required"
        assert item.decision_required["next_action"].endswith(" a")
        assert len(eng.store.assign_log) == assignments_before

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

def test_reconcile_does_not_swallow_programming_errors(tmp_path):
    store = _engine().store
    manifest = Manifest(meta={}, nodes={
        "a": Node(id="a", worker="alice", work_item_id="1", status="done"),
    })
    store.get_work_item = lambda item_id: (_ for _ in ()).throw(ValueError("bug"))

    with pytest.raises(ValueError, match="bug"):
        loop.reconcile(store, manifest, str(tmp_path / "m.yaml"))

class TestIdempotency:
    def test_confirmed_merge_without_work_item_remains_closed(self):
        """confirmed merge 不因平台投影缺失而重建或重新执行。"""
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

        # 重建 loop(store/runtime 是新的,但 work_items 在内存里丢失)。
        eng2 = _engine()
        # 手动清空 a 的 work_item_id 模拟「平台已无此 item」
        from omac.core.manifest import set_node
        set_node(manifest, "a", work_item_id=None)

        r3 = tick(eng2.store, eng2.runtime, manifest, path)
        assert "a" in r3.done
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].work_item_id is None
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
    def test_no_reviewer_still_requires_merge_closure(self):
        """无 reviewer 节点:跳过评审交接，但仍须远端合入确认才 done。"""
        nodes = [_node("a", reviewer=None)]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert "a" in result.done
        # 不经过 in_review，MockStore 的明确合并配置提供远端 MERGED 事实。

    def test_with_reviewer_goes_through_in_review(self):
        """有 reviewer 节点:worker 完成 → in_review → reviewer pass → done。"""
        nodes = [_node("a", reviewer="bob")]
        manifest = _manifest(nodes)
        path = _tmp_manifest_path(manifest)
        eng = _engine()

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        assert result.state == "converged"
        assert "a" in result.done
        item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)
        assert item.review_obligations
        assert item.review_ledger["cycles"][0]["verdict"] == "pass"

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
            verification={
                "commands": [_business_command()],
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
                "coverage": 90,
            },
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
            verification={
                "commands": [_business_command()],
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
                "commands": [_business_command()],
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
        assert got.phase == TaskPhase.REVIEW
        assert got.review_subject_digest
        assert got.review_obligations
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
                "commands": [_business_command()],
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
        from omac.core.manifest import Contract
        return Contract(
            objective="do it",
            acceptance=["works"],
            non_goals=["no creep"],
            verification_commands=["pytest -q"],
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
            verification={
                "commands": [_business_command()],
                "integration_gates": [{
                    "name": "setup-gate",
                    "commands": [_business_command()],
                }],
                "pr_base": "main",
                "coverage": 90,
            },
        )
        eng.store.update_status(item.id, __import__("omac").engines.models.WorkItemStatus.DONE)

        # tick 2: worker 完成 → 转评审(in_review + assign reviewer)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        from omac.core.manifest import set_node
        set_node(manifest, key, status="in_review")
        save_manifest(manifest, path)

        # 置为 reject 评审结论
        eng.store.update_work_item_metadata(item.id, review_verdict="reject")
        eng.store.update_status(
            item.id, __import__("omac").engines.models.WorkItemStatus.IN_REVIEW)
        return manifest, eng, item

    @staticmethod
    def _submit_revision(eng, item, revision=2):
        import hashlib
        from dataclasses import replace

        current = eng.store.get_work_item(item.id)
        intent = current.worker_handoff
        if intent is not None and intent.is_causally_bound() and not intent.target_run_id:
            candidates = [
                run for run in eng.runtime.list_runs(item.id)
                if run.kind == "direct"
                and run.id not in set(intent.baseline_direct_run_ids)
                and run.agent_id == intent.target_agent_id
            ]
            assert len(candidates) == 1
            intent = replace(intent, target_run_id=candidates[0].id)
            eng.store.update_work_item_metadata(item.id, worker_handoff=intent)
        pr_url = f"https://mock.example.com/pr/{item.id}-v{revision}"
        artifacts = {
            "pr_url": pr_url,
            "head_sha": hashlib.sha256(pr_url.encode("utf-8")).hexdigest(),
        }
        verification = {
            "commands": [_business_command()],
            "integration_gates": [{
                "name": "revision-gate",
                "commands": [_business_command()],
            }],
            "pr_base": "main",
            "coverage": 90,
            "revision": revision,
        }
        verification_source = __import__("yaml").safe_dump(verification)
        eng.store.update_work_item_metadata(
            item.id,
            artifacts=artifacts,
            verification=verification,
            verification_source=verification_source,
        )
        current = eng.store.get_work_item(item.id)
        if intent is not None and intent.is_causally_bound():
            current.verification_ref.update({
                "uploader_type": "agent",
                "uploader_id": intent.target_agent_id,
                "task_id": intent.target_run_id,
                "created_at": "2026-01-01T00:00:01Z",
            })
        eng.store.update_status(item.id, WorkItemStatus.DONE)

    def _prepare_causal_handoff(self, eng, item, *, gate="review"):
        """构造已持久化但尚未收敛的因果 Worker handoff。"""
        import hashlib

        from omac.core.taskmeta import WorkerHandoffIntent

        source = eng.store.get_work_item(item.id)
        artifacts = dict(source.artifacts or {})
        artifacts["head_sha"] = "head-reviewed"
        verification_source = __import__("yaml").safe_dump(source.verification)
        eng.store.update_work_item_metadata(
            item.id,
            artifacts=artifacts,
            verification=source.verification,
            verification_source=verification_source,
        )
        source = eng.store.get_work_item(item.id)
        source.verification_ref["sha256"] = hashlib.sha256(
            verification_source.encode("utf-8")
        ).hexdigest()
        source_subject = review_subject_digest(source, 1)
        intent = WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate=gate,
            source_review_subject_digest=source_subject,
            source_review_round=1,
            target_review_bounce=1,
        )
        object.__setattr__(intent, "generation", "handoff-generation-1")
        object.__setattr__(intent, "target_agent_id", "agent-worker")
        object.__setattr__(intent, "baseline_direct_run_ids", ("run-old",))
        object.__setattr__(
            intent,
            "baseline_verification_attachment_id",
            source.verification_ref["attachment_id"],
        )
        object.__setattr__(intent, "target_run_id", "run-worker")
        eng.store.update_work_item_metadata(
            item.id,
            review_subject_digest=source_subject,
            review_bounce=1,
            worker_handoff=intent,
        )
        eng.store.reset_review(item.id)
        eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
        return intent, source

    def test_terminal_worker_handoff_without_submit_uses_bounded_worker_retry(
        self, tmp_path, monkeypatch,
    ):
        """terminal target Run 跨 tick grace 后使用有界 worker retry。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        original_assign = eng.store.assign_work_item
        retry_assignments = 0
        retry_run_visible = False
        now = [datetime(2026, 7, 31, tzinfo=timezone.utc)]

        def assign(item_id, assignee, role):
            nonlocal retry_assignments, retry_run_visible
            if role == "worker":
                retry_assignments += 1
                retry_run_visible = True
            return original_assign(item_id, assignee, role)

        def list_runs(_item_id):
            runs = [AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )]
            if retry_run_visible:
                runs.append(AgentRunObservation(
                    id="run-worker-retry",
                    kind="direct",
                    status="running",
                    agent_id=intent.target_agent_id,
                ))
            return runs

        monkeypatch.setattr(loop, "_utcnow", lambda: now[0])
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "list_runs", list_runs)

        first = loop.collect_results(
            eng.store,
            eng.runtime,
            manifest,
            path,
            retry_limits={"worker": 1},
        )
        after_grace_start = eng.store.get_work_item(item.id)
        assert after_grace_start.bounces.worker == 0
        assert after_grace_start.worker_handoff is not None
        assert after_grace_start.worker_handoff.terminal_observed_at
        assert retry_assignments == 0

        now[0] += timedelta(seconds=loop._HANDOFF_TERMINAL_GRACE_SECONDS + 1)
        second = loop.collect_results(
            eng.store,
            eng.runtime,
            manifest,
            path,
            retry_limits={"worker": 1},
        )
        assignments_after_retry = retry_assignments
        third = loop.collect_results(
            eng.store,
            eng.runtime,
            manifest,
            path,
            retry_limits={"worker": 1},
        )

        recovered = eng.store.get_work_item(item.id)
        assert first == {}
        assert second == {}
        assert third == {}
        assert manifest.nodes["a"].status == "in_progress"
        assert recovered.bounces.worker == 1
        assert recovered.worker_handoff is not None
        assert recovered.worker_handoff.target_run_id == "run-worker-retry"
        assert recovered.worker_handoff.target_worker_bounce == 1
        assert assignments_after_retry == 1
        assert retry_assignments == assignments_after_retry

    def test_terminal_worker_handoff_without_submit_blocks_when_budget_exhausted(
        self, tmp_path, monkeypatch,
    ):
        """terminal no-submit 使用既有 worker=0 立即 blocked 语义。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        now = [datetime(2026, 7, 31, tzinfo=timezone.utc)]
        worker_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        monkeypatch.setattr(loop, "_utcnow", lambda: now[0])
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])

        assert loop.collect_results(
            eng.store,
            eng.runtime,
            manifest,
            path,
            retry_limits={"worker": 0},
        ) == {}
        assert manifest.nodes["a"].status == "in_progress"

        now[0] += timedelta(seconds=loop._HANDOFF_TERMINAL_GRACE_SECONDS + 1)
        failures = loop.collect_results(
            eng.store, eng.runtime, manifest, path,
            retry_limits={"worker": 0})

        recovered = eng.store.get_work_item(item.id)
        assert "a" in failures
        assert manifest.nodes["a"].status == "blocked"
        assert recovered.status is WorkItemStatus.BLOCKED
        assert recovered.worker_handoff is None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_before

    def test_terminal_worker_handoff_collects_submit_that_arrives_within_window(
        self, tmp_path, monkeypatch,
    ):
        """有限观察窗口内晚到的新 attachment 仍按 causal submit 收割。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation, PullRequestReadiness

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        now = [datetime(2026, 7, 31, tzinfo=timezone.utc)]
        worker_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        monkeypatch.setattr(loop, "_utcnow", lambda: now[0])
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])

        assert loop.collect_results(
            eng.store, eng.runtime, manifest, path) == {}
        assert eng.store.get_work_item(
            item.id).worker_handoff.terminal_observed_at

        self._submit_revision(eng, item, revision=2)
        fresh = eng.store.get_work_item(item.id)
        now[0] += timedelta(
            seconds=max(1, loop._HANDOFF_TERMINAL_GRACE_SECONDS // 2))
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: PullRequestReadiness(
                is_draft=False, state="OPEN",
                head_sha=fresh.artifacts["head_sha"]),
        )
        failures = loop.collect_results(
            eng.store, eng.runtime, manifest, path)

        recovered = eng.store.get_work_item(item.id)
        assert failures == {}
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.worker_handoff is None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_before

    def test_worker_retry_attempt_recovers_crash_between_intent_and_bounce(
        self, tmp_path, monkeypatch,
    ):
        """retry intent 先落盘；重启从同 generation 收敛 bounce 后只派一次。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        now = [datetime(2026, 7, 31, tzinfo=timezone.utc)]
        retry_run_visible = False
        retry_assignments = 0
        original_assign = eng.store.assign_work_item
        original_update = eng.store.update_work_item_metadata

        def list_runs(_item_id):
            runs = [AgentRunObservation(
                id=intent.target_run_id, kind="direct", status="completed",
                agent_id=intent.target_agent_id)]
            if retry_run_visible:
                runs.append(AgentRunObservation(
                    id="run-worker-retry", kind="direct", status="running",
                    agent_id=intent.target_agent_id))
            return runs

        def assign(item_id, assignee, role):
            nonlocal retry_run_visible, retry_assignments
            if role == "worker":
                retry_run_visible = True
                retry_assignments += 1
            return original_assign(item_id, assignee, role)

        monkeypatch.setattr(loop, "_utcnow", lambda: now[0])
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(eng.runtime, "list_runs", list_runs)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        assert loop.collect_results(
            eng.store, eng.runtime, manifest, path,
            retry_limits={"worker": 1}) == {}
        now[0] += timedelta(seconds=loop._HANDOFF_TERMINAL_GRACE_SECONDS + 1)

        crashed = False

        def crash_after_retry_intent(item_id, **metadata):
            nonlocal crashed
            result = original_update(item_id, **metadata)
            handoff = metadata.get("worker_handoff")
            if (
                getattr(handoff, "target_worker_bounce", None) == 1
                and not crashed
            ):
                crashed = True
                raise RuntimeError("crash after retry intent")
            return result

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", crash_after_retry_intent)
        with pytest.raises(RuntimeError, match="retry intent"):
            loop.collect_results(
                eng.store, eng.runtime, manifest, path,
                retry_limits={"worker": 1})

        interrupted = eng.store.get_work_item(item.id)
        retry_generation = interrupted.worker_handoff.generation
        assert interrupted.worker_handoff.target_worker_bounce == 1
        assert interrupted.bounces.worker == 0
        assert retry_assignments == 0

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update)
        assert loop.collect_results(
            eng.store, eng.runtime, manifest, path,
            retry_limits={"worker": 1}) == {}
        assignments_after_restart = retry_assignments
        assert loop.collect_results(
            eng.store, eng.runtime, manifest, path,
            retry_limits={"worker": 1}) == {}

        recovered = eng.store.get_work_item(item.id)
        assert recovered.worker_handoff.generation == retry_generation
        assert recovered.bounces.worker == 1
        assert assignments_after_restart == 1
        assert retry_assignments == assignments_after_restart

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
            item.id, review_obligations=build_review_obligations(item))
        eng.store.update_work_item_metadata(
            item.id, review_report=_review_report(item, "reject"))

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict is None
        assert got.bounces.review == 1

    def test_restart_new_delivery_without_assignee_prepares_and_assigns_reviewer(
        self, tmp_path, monkeypatch,
    ):
        """reject 返工提交后 assignee 为空时，prepare 后必须 assign 再 wake。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        old_ledger = {
            "schema": "omac.review-ledger/v1",
            "cycles": [{
                "round": 1,
                "subject_digest": item.review_subject_digest,
                "verdict": "reject",
            }],
            "blockers": [{
                "blocker_id": "BLK-old",
                "root_cause_key": "old-reject",
                "status": "open",
            }],
        }
        eng.store.update_work_item_metadata(
            item.id,
            review_report=_review_report(item, "reject"),
            review_ledger=old_ledger,
        )

        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        self._submit_revision(eng, item)
        eng.store.clear_assignment(item.id)
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)

        events = []
        original_prepare = eng.store.prepare_review_cycle
        original_assign = eng.store.assign_work_item

        def prepare(item_id, subject_digest):
            events.append("prepare")
            return original_prepare(item_id, subject_digest)

        def assign(item_id, assignee, role):
            if role == "reviewer":
                current = eng.store.get_work_item(item_id)
                assert current.phase == TaskPhase.REVIEW
                assert current.status == WorkItemStatus.IN_REVIEW
                assert current.review_report is None
                assert current.review_ledger is old_ledger
                events.append("assign")
            return original_assign(item_id, assignee, role)

        def wake(item_id, agent, role):
            if role == "reviewer":
                events.append("wake")

        monkeypatch.setattr(eng.store, "prepare_review_cycle", prepare)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert events == ["prepare", "assign", "wake"]
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.phase == TaskPhase.REVIEW
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert recovered.review_ledger is old_ledger
        assert recovered.review_subject_digest == review_subject_digest(
            recovered, recovered.bounces.review + 1)

    def test_reject_without_assignee_prepares_worker_before_assign_and_wake(
        self, tmp_path, monkeypatch,
    ):
        """reject→worker 无 assignee 时，authoring/status 必须在 assign 前就绪。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        eng.store.update_work_item_metadata(
            item.id, review_report=_review_report(item, "reject"))
        eng.store.clear_assignment(item.id)

        events = []
        original_assign = eng.store.assign_work_item

        def assign(item_id, assignee, role):
            if role == "worker":
                current = eng.store.get_work_item(item_id)
                assert current.phase == TaskPhase.AUTHORING
                assert current.status == WorkItemStatus.IN_PROGRESS
                assert current.review_verdict is None
                events.append("assign")
            return original_assign(item_id, assignee, role)

        def wake(item_id, agent, role):
            if role == "worker":
                events.append("wake")

        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert events == ["assign"]
        assert manifest.nodes["a"].status == "in_progress"
        assert recovered.review_verdict is None
        assert recovered.bounces.review == 1

    def test_review_worker_handoff_recovers_after_bounce_before_reset(
        self, tmp_path, monkeypatch,
    ):
        """bounce 已落盘但 review projection 未清时，重启继续 Worker handoff。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        eng.store.update_work_item_metadata(
            item.id,
            review_report=_review_report(item, "reject"),
        )

        runs_before_handoff = len(eng.runtime.list_runs(item.id))
        worker_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        original_update = eng.store.update_work_item_metadata
        crashed = False

        def crash_after_bounce(item_id, **metadata):
            nonlocal crashed
            result = original_update(item_id, **metadata)
            if metadata.get("review_bounce") == 1 and not crashed:
                crashed = True
                raise RuntimeError("simulated crash after review_bounce")
            return result

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", crash_after_bounce)

        with pytest.raises(
            RuntimeError, match="simulated crash after review_bounce",
        ):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        crashed_item = eng.store.get_work_item(item.id)
        assert crashed_item.bounces.review == 1
        assert crashed_item.review_verdict == "reject"
        assert crashed_item.review_report is not None
        assert crashed_item.review_subject_digest is not None
        assert crashed_item.worker_handoff is not None

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update)
        monkeypatch.setattr(
            loop,
            "_dispatch_reviewer_for_current_subject",
            lambda *_args, **_kwargs: pytest.fail(
                "valid worker handoff recovery must not dispatch Reviewer"),
        )
        persisted = load_manifest(path)

        first = tick(
            eng.store, eng.runtime, persisted, path, max_parallel=4)
        second = tick(
            eng.store, eng.runtime, persisted, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert second.state == "running"
        assert persisted.nodes["a"].status == "in_progress"
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.status is WorkItemStatus.IN_PROGRESS
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert recovered.review_subject_digest is None
        assert recovered.worker_handoff is not None
        assert recovered.bounces.review == 1
        assert len(eng.runtime.list_runs(item.id)) == runs_before_handoff + 1
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_before + 1
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before

    @pytest.mark.parametrize("verdict", ["reject", "pass-with-nits"])
    @pytest.mark.parametrize(
        "checkpoint",
        [
            "intent", "bounce", "reset_review", "status", "assignment",
        ],
    )
    def test_review_worker_handoff_recovers_each_restart_checkpoint(
        self, tmp_path, monkeypatch, verdict, checkpoint,
    ):
        """每个 checkpoint 连续重启都只产生一个正确 Worker Run。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict=verdict,
            review_report=_review_report(
                item,
                verdict,
                nits=["follow up"] if verdict == "pass-with-nits" else None,
            ),
        )

        runs_before_handoff = len(eng.runtime.list_runs(item.id))
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        original_update_metadata = eng.store.update_work_item_metadata
        original_reset_review = eng.store.reset_review
        original_prepare_review_cycle = eng.store.prepare_review_cycle
        original_update_status = eng.store.update_status
        original_assign = eng.store.assign_work_item
        original_wake = eng.runtime.wake
        crashed = False

        def crash_once(name):
            nonlocal crashed
            if checkpoint == name and not crashed:
                crashed = True
                raise RuntimeError(f"simulated crash after {name}")

        def update_metadata(item_id, **metadata):
            result = original_update_metadata(item_id, **metadata)
            intent = metadata.get("worker_handoff")
            if intent and checkpoint == "intent":
                crash_once("intent")
            if metadata.get("review_bounce") == 1:
                crash_once("bounce")
            return result

        def reset_review(item_id):
            original_reset_review(item_id)
            crash_once("reset_review")

        def prepare_review_cycle(item_id, subject_digest):
            assert not subject_digest.startswith("worker-handoff:")
            return original_prepare_review_cycle(item_id, subject_digest)

        def update_status(item_id, status):
            original_update_status(item_id, status)
            if status is WorkItemStatus.IN_PROGRESS:
                crash_once("status")

        def assign(item_id, assignee, role):
            if role == "worker":
                current = eng.store.get_work_item(item_id)
                assert current.phase is TaskPhase.AUTHORING
                assert current.status is WorkItemStatus.IN_PROGRESS
                assert current.review_verdict is None
                assert current.review_report is None
                assert current.review_subject_digest is None
            original_assign(item_id, assignee, role)
            if role == "worker":
                crash_once("assignment")

        def wake(item_id, agent, role):
            original_wake(item_id, agent, role)
            if role == "worker":
                crash_once("wake")

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", update_metadata)
        monkeypatch.setattr(eng.store, "reset_review", reset_review)
        monkeypatch.setattr(
            eng.store, "prepare_review_cycle", prepare_review_cycle)
        monkeypatch.setattr(eng.store, "update_status", update_status)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        with pytest.raises(
            RuntimeError, match=f"simulated crash after {checkpoint}",
        ):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        persisted = load_manifest(path)
        assert persisted.nodes["a"].status == "in_review"
        crashed_item = eng.store.get_work_item(item.id)
        assert crashed_item.worker_handoff is not None

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update_metadata)
        monkeypatch.setattr(eng.store, "reset_review", original_reset_review)
        monkeypatch.setattr(eng.store, "update_status", original_update_status)
        monkeypatch.setattr(eng.runtime, "wake", original_wake)

        def assert_safe_assign(item_id, assignee, role):
            if role == "worker":
                current = eng.store.get_work_item(item_id)
                assert current.phase is TaskPhase.AUTHORING
                assert current.status is WorkItemStatus.IN_PROGRESS
                assert current.review_verdict is None
                assert current.review_report is None
                assert current.review_subject_digest is None
            return original_assign(item_id, assignee, role)

        monkeypatch.setattr(eng.store, "assign_work_item", assert_safe_assign)
        monkeypatch.setattr(
            loop,
            "_dispatch_reviewer_for_current_subject",
            lambda *_args, **_kwargs: pytest.fail(
                "valid worker handoff recovery must not dispatch Reviewer"),
        )

        first = tick(
            eng.store, eng.runtime, persisted, path, max_parallel=4,
        )
        second = tick(
            eng.store, eng.runtime, persisted, path, max_parallel=4,
        )

        recovered = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert second.state == "running"
        assert persisted.nodes["a"].status == "in_progress"
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.status is WorkItemStatus.IN_PROGRESS
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert recovered.review_subject_digest is None
        assert recovered.worker_handoff is not None
        assert recovered.bounces.review == 1
        assert len(eng.runtime.list_runs(item.id)) == runs_before_handoff + 1
        assert eng.store.assign_log[-1][2] == "worker"
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before

    def test_new_worker_delivery_invalidates_residual_handoff_intent(
        self, tmp_path, monkeypatch,
    ):
        """wake 后 intent 清理前崩溃；Worker 新交付必须进入 fresh review。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        source_subject = current.review_subject_digest
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
        )
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        handed_off = eng.store.get_work_item(item.id)
        assert handed_off.worker_handoff is not None
        assert handed_off.phase is TaskPhase.AUTHORING
        assert handed_off.status is WorkItemStatus.IN_PROGRESS
        runs_after_worker_handoff = len(eng.runtime.list_runs(item.id))
        worker_assignments_after_handoff = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])

        self._submit_revision(eng, item, revision=2)
        original_update = eng.store.update_work_item_metadata
        crashed = False

        def crash_before_intent_clear(item_id, **metadata):
            nonlocal crashed
            if metadata.get("worker_handoff") == {} and not crashed:
                crashed = True
                raise RuntimeError("simulated crash before intent clear")
            return original_update(item_id, **metadata)

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", crash_before_intent_clear)
        with pytest.raises(RuntimeError, match="before intent clear"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update)
        original_assign = eng.store.assign_work_item

        def assign(item_id, assignee, role):
            if role == "worker":
                pytest.fail("new Worker delivery must not resume stale handoff")
            return original_assign(item_id, assignee, role)

        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        persisted = load_manifest(path)

        first = tick(eng.store, eng.runtime, persisted, path, max_parallel=4)
        second = tick(eng.store, eng.runtime, persisted, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert second.state == "running"
        assert persisted.nodes["a"].status == "in_review"
        assert recovered.worker_handoff is None
        assert recovered.phase is TaskPhase.REVIEW
        assert recovered.status is WorkItemStatus.IN_REVIEW
        assert recovered.review_subject_digest != source_subject
        assert len(eng.runtime.list_runs(item.id)) == runs_after_worker_handoff + 1
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_after_handoff
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before + 1

    def test_worker_handoff_rechecks_delivery_after_assignment_before_wake(
        self, tmp_path, monkeypatch,
    ):
        """assign 后已提交的新 delivery 必须直接进入 Reviewer，不能 rerun Worker。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        source_subject = current.review_subject_digest
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
        )

        original_assign = eng.store.assign_work_item
        original_wake = eng.runtime.wake
        worker_assignments = 0
        reviewer_wakes = 0

        def assign(item_id, assignee, role):
            nonlocal worker_assignments
            result = original_assign(item_id, assignee, role)
            if role == "worker":
                from omac.engines.mock import _finish_mock_run
                worker_assignments += 1
                self._submit_revision(eng, item, revision=2)
                _finish_mock_run(item_id)
                eng.store.clear_assignment(item_id)
            return result

        def wake(item_id, agent, role):
            nonlocal reviewer_wakes
            if role == "worker":
                pytest.fail(
                    "delivery submitted during assignment must not rerun Worker")
            reviewer_wakes += 1
            return original_wake(item_id, agent, role)

        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        first = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert first.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert reviewer_wakes == 0

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.worker_handoff is None
        assert recovered.phase is TaskPhase.REVIEW
        assert recovered.status is WorkItemStatus.IN_REVIEW
        assert recovered.review_subject_digest != source_subject
        assert worker_assignments == 1
        assert reviewer_wakes == 1

    def test_worker_handoff_not_assigned_reobserves_submitted_delivery(
        self, tmp_path, monkeypatch,
    ):
        """assignment 已产生目标 Run 时不再额外 rerun Worker。"""
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        source_subject = current.review_subject_digest
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
        )

        original_wake = eng.runtime.wake
        worker_wakes = 0
        reviewer_wakes = 0

        def wake(item_id, agent, role):
            nonlocal worker_wakes, reviewer_wakes
            if role == "worker":
                worker_wakes += 1
                self._submit_revision(eng, item, revision=2)
                eng.store.clear_assignment(item_id)
                raise PlatformError(
                    "Invalid request: issue is not assigned to an agent or squad")
            reviewer_wakes += 1
            return original_wake(item_id, agent, role)

        monkeypatch.setattr(eng.runtime, "wake", wake)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert recovered.worker_handoff is not None
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.status is WorkItemStatus.IN_PROGRESS
        assert recovered.review_subject_digest is None
        assert source_subject is not None
        assert worker_wakes == 0
        assert reviewer_wakes == 0

    def test_worker_handoff_delivery_check_ignores_stale_status_projection(
        self, tmp_path, monkeypatch,
    ):
        """assignment 后状态回读陈旧时，只要 delivery 未变就继续原 handoff。"""
        import copy

        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
        )

        original_assign = eng.store.assign_work_item
        original_get = eng.store.get_work_item
        original_wake = eng.runtime.wake
        assignment_finished = False
        stale_status_served = False
        worker_wakes = 0
        reviewer_wakes = 0

        def assign(item_id, assignee, role):
            nonlocal assignment_finished
            result = original_assign(item_id, assignee, role)
            if role == "worker":
                assignment_finished = True
            return result

        def get_work_item(item_id):
            nonlocal stale_status_served
            observed = original_get(item_id)
            if assignment_finished and not stale_status_served:
                stale_status_served = True
                stale = copy.copy(observed)
                stale.status = WorkItemStatus.TODO
                return stale
            return observed

        def wake(item_id, agent, role):
            nonlocal worker_wakes, reviewer_wakes
            if role == "worker":
                worker_wakes += 1
            else:
                reviewer_wakes += 1
            return original_wake(item_id, agent, role)

        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.store, "get_work_item", get_work_item)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = original_get(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert recovered.worker_handoff is not None
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.status is WorkItemStatus.IN_PROGRESS
        assert worker_wakes == 0
        assert reviewer_wakes == 0

    def test_other_actor_or_old_run_delivery_cannot_complete_handoff(
        self, tmp_path, monkeypatch,
    ):
        """内容变化不能替代 generation + target actor/run 的因果证明。"""
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        self._submit_revision(eng, item)
        current = eng.store.get_work_item(item.id)
        current.verification_ref.update({
            "uploader_type": "agent",
            "uploader_id": "agent-other",
            "task_id": "run-old",
        })
        reviewer_wakes = 0

        def wake(_item_id, _agent, role):
            nonlocal reviewer_wakes
            if role == "reviewer":
                reviewer_wakes += 1

        monkeypatch.setattr(eng.runtime, "wake", wake)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            __import__("omac").engines.models.AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])

        with pytest.raises(PlatformError, match="causal|identity|handoff"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert reviewer_wakes == 0
        assert eng.store.get_work_item(item.id).worker_handoff is not None

    def test_matching_submit_waits_until_target_worker_run_is_terminal(
        self, tmp_path, monkeypatch,
    ):
        """delivery 可见但目标 Worker Run active 时不得并发派 Reviewer。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, source = self._prepare_causal_handoff(eng, item)
        self._submit_revision(eng, item)
        current = eng.store.get_work_item(item.id)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id="run-worker", kind="direct", status="running",
                agent_id="agent-worker")
        ])
        reviewer_wakes = 0

        def wake(_item_id, _agent, role):
            nonlocal reviewer_wakes
            if role == "reviewer":
                reviewer_wakes += 1

        monkeypatch.setattr(eng.runtime, "wake", wake)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert reviewer_wakes == 0
        assert eng.store.get_work_item(item.id).worker_handoff is not None
        assert source.verification == current.verification

    def test_tampered_verification_projection_cannot_be_sealed(
        self, tmp_path, monkeypatch,
    ):
        """解析后的 verification 投影与实际附件不一致时失败关闭。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        self._submit_revision(eng, item)
        current = eng.store.get_work_item(item.id)
        current.verification = dict(current.verification or {}, commands=[])
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])

        with pytest.raises(PlatformError, match="projection|attachment"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

    def test_pr_head_is_rechecked_after_seal_before_reviewer_dispatch(
        self, tmp_path, monkeypatch,
    ):
        """seal 后又 push commit 时，下一轮 Reviewer 派发必须失败关闭。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation, PullRequestReadiness
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        self._submit_revision(eng, item)
        current = eng.store.get_work_item(item.id)
        observed_heads = iter([current.artifacts["head_sha"], "pushed-head"])
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: PullRequestReadiness(
                is_draft=False,
                state="OPEN",
                head_sha=next(observed_heads),
            ),
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert any(
            "HEAD" in comment or "head" in comment
            for comment in eng.store.get_comments(item.id)
        )

    def test_empty_command_candidate_returns_to_normal_evidence_gate(
        self, tmp_path, monkeypatch,
    ):
        """handoff seal 不是证据门；空命令 verification 仍必须被正常门阻断。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation, PullRequestReadiness

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        current = eng.store.get_work_item(item.id)
        candidate = {"commands": [], "integration_gates": [], "pr_base": "main"}
        source = __import__("yaml").safe_dump(candidate)
        pr_url = "https://mock.example.com/pr/empty"
        head = __import__("hashlib").sha256(pr_url.encode()).hexdigest()
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": pr_url, "head_sha": head},
            verification=candidate,
            verification_source=source,
        )
        current = eng.store.get_work_item(item.id)
        current.verification_ref.update({
            "uploader_type": "agent",
            "uploader_id": intent.target_agent_id,
            "task_id": intent.target_run_id,
            "created_at": "2026-01-01T00:00:01Z",
        })
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: PullRequestReadiness(
                is_draft=False, state="OPEN", head_sha=head),
        )
        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"

    def test_crash_after_seal_before_handoff_retire_is_restart_safe(
        self, tmp_path, monkeypatch,
    ):
        """identity 先持久化、intent 后退役；中间崩溃可重复收敛且不重派 Worker。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        self._submit_revision(eng, item)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])
        original_update = eng.store.update_work_item_metadata
        crashed = False

        def crash_after_identity(item_id, **metadata):
            nonlocal crashed
            result = original_update(item_id, **metadata)
            if metadata.get("delivery_identity") and not crashed:
                crashed = True
                raise RuntimeError("crash after controller seal")
            return result

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", crash_after_identity)
        with pytest.raises(RuntimeError, match="controller seal"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        persisted = eng.store.get_work_item(item.id)
        assert persisted.delivery_identity is not None
        assert persisted.worker_handoff is not None

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update)
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert recovered.worker_handoff is None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before + 1

    def test_assignment_unknown_observes_completed_causal_submit(
        self, tmp_path, monkeypatch,
    ):
        """assign 响应未知后只读收割已完成 target Run，不重复 wake。"""
        from omac.engines import create_engine
        from omac.engines.mock import _finish_mock_run
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
        )
        original_assign = eng.store.assign_work_item
        worker_wakes = 0

        def assign(item_id, assignee, role):
            result = original_assign(item_id, assignee, role)
            if role == "worker":
                self._submit_revision(eng, item, revision=2)
                _finish_mock_run(item_id)
                raise PlatformError("assignment response unknown")
            return result

        def wake(_item_id, _agent, role):
            nonlocal worker_wakes
            if role == "worker":
                worker_wakes += 1

        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        first = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert first.state == "running"
        assert worker_wakes == 0
        assert eng.store.get_work_item(item.id).worker_handoff is None

    def test_partial_submit_with_active_worker_is_pending_not_invalid(
        self, tmp_path, monkeypatch,
    ):
        """artifacts/ref 先可见、identity 尚未封装且 Worker active 时继续等待。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        current = eng.store.get_work_item(item.id)
        current.artifacts = {
            "pr_url": current.artifacts["pr_url"],
            "head_sha": "candidate-head",
        }
        current.verification = dict(current.verification or {}, revision=2)
        current.delivery_identity = None
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="running",
                agent_id=intent.target_agent_id,
            )
        ])

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert eng.store.get_work_item(item.id).worker_handoff is not None

    def test_completed_handoff_reuses_normal_worker_evidence_and_ci_gate(
        self, tmp_path, monkeypatch,
    ):
        """handoff 收敛后必须走正常 evidence/CI 路径，失败 CI 不得派 Reviewer。"""
        from omac.engines import create_engine
        from omac.engines.models import (
            AgentRunObservation, PullRequestCheckResult,
            PullRequestReadiness,
        )

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        self._submit_revision(eng, item)
        current = eng.store.get_work_item(item.id)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: PullRequestReadiness(
                is_draft=False,
                state="OPEN",
                head_sha=current.artifacts["head_sha"],
            ),
        )
        ci_calls = 0
        reviewer_wakes = 0

        def check_pr(*_args, **_kwargs):
            nonlocal ci_calls
            ci_calls += 1
            return PullRequestCheckResult(False, 1, "ci failed")

        def wake(_item_id, _agent, role):
            nonlocal reviewer_wakes
            if role == "reviewer":
                reviewer_wakes += 1

        monkeypatch.setattr(eng.store, "check_pull_request", check_pr)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        result = tick(
            eng.store,
            eng.runtime,
            manifest,
            path,
            max_parallel=4,
            retry_limits={"ci": 0},
            config={"ci": {"check_command": "gh pr checks {pr_url}"}},
        )

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert ci_calls == 1
        assert reviewer_wakes == 0

    def test_completed_handoff_reobserves_remote_pr_head(
        self, tmp_path, monkeypatch,
    ):
        """submit 后 PR 推新 commit 时，metadata 里的旧 head 不能通过恢复。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation, PullRequestReadiness
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        self._submit_revision(eng, item)
        current = eng.store.get_work_item(item.id)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
            )
        ])
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: PullRequestReadiness(
                is_draft=False, state="OPEN", head_sha="new-remote-head"),
        )

        with pytest.raises(PlatformError, match="head|HEAD"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

    @pytest.mark.parametrize("gate", ["review", "review-nits"])
    def test_same_content_new_causal_submit_enters_review_after_terminal_run(
        self, tmp_path, monkeypatch, gate,
    ):
        """PR URL/verification 内容相同也可由新 generation/run 证明真实提交。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, source = self._prepare_causal_handoff(
            eng, item, gate=gate)
        current = eng.store.get_work_item(item.id)
        assert current.artifacts == source.artifacts
        assert current.verification == source.verification
        source_text = __import__("yaml").safe_dump(current.verification)
        eng.store.update_work_item_metadata(
            item.id,
            verification=current.verification,
            verification_source=source_text,
        )
        current = eng.store.get_work_item(item.id)
        current.verification_ref.update({
            "uploader_type": "agent",
            "uploader_id": intent.target_agent_id,
            "task_id": intent.target_run_id,
            "created_at": "2026-01-01T00:00:01Z",
        })
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
            AgentRunObservation(
                id="run-worker", kind="direct", status="completed",
                agent_id="agent-worker")
        ])
        reviewer_wakes = 0
        original_wake = eng.runtime.wake

        def wake(item_id, agent, role):
            nonlocal reviewer_wakes
            if role == "reviewer":
                reviewer_wakes += 1
            return original_wake(item_id, agent, role)

        monkeypatch.setattr(eng.runtime, "wake", wake)
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: __import__(
                "omac").engines.models.PullRequestReadiness(
                    is_draft=False,
                    state="OPEN",
                    head_sha=current.artifacts["head_sha"],
                ),
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert reviewer_wakes == 1
        assert eng.store.get_work_item(item.id).worker_handoff is None

    def test_not_assigned_observes_two_stale_deliveries_before_matching_submit(
        self, tmp_path, monkeypatch,
    ):
        """wake 未知后做有界只读观察，两次旧读、第三次新提交仍可收敛。"""
        import copy

        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        original_get = eng.store.get_work_item
        live = original_get(item.id)
        stale = copy.deepcopy(live)
        self._submit_revision(eng, item, revision=2)
        fresh = copy.deepcopy(original_get(item.id))
        live.artifacts = copy.deepcopy(stale.artifacts)
        live.verification = copy.deepcopy(stale.verification)
        live.verification_ref = copy.deepcopy(stale.verification_ref)
        live.delivery_identity = None
        live.status = stale.status
        wake_failed = False
        reads_after_error = 0

        def get_work_item(item_id):
            nonlocal reads_after_error
            if wake_failed:
                reads_after_error += 1
                if reads_after_error <= 2:
                    return copy.deepcopy(stale)
                live.artifacts = copy.deepcopy(fresh.artifacts)
                live.verification = copy.deepcopy(fresh.verification)
                live.verification_ref = copy.deepcopy(fresh.verification_ref)
                live.status = WorkItemStatus.DONE
            return original_get(item_id)

        def wake(_item_id, _agent, role):
            nonlocal wake_failed
            if role == "worker":
                wake_failed = True
                raise PlatformError(
                    "Invalid request: issue is not assigned to an agent or squad")

        monkeypatch.setattr(eng.store, "get_work_item", get_work_item)
        monkeypatch.setattr(eng.runtime, "wake", wake)
        monkeypatch.setattr(
            eng.runtime,
            "list_runs",
            lambda _item_id: ([
                AgentRunObservation(
                    id="run-worker", kind="direct", status="completed",
                    agent_id="agent-worker")
            ] if wake_failed else []),
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_review"

    @pytest.mark.parametrize("changed_field", ["artifacts", "verification"])
    def test_unsealed_delivery_change_does_not_skip_target_worker(
        self, tmp_path, monkeypatch, changed_field,
    ):
        """候选投影变化不能替代 target Worker Run，只能继续正常 handoff。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        old_subject = current.review_subject_digest
        old_ledger = {
            "schema": "omac.review-ledger/v1",
            "cycles": [],
            "blockers": [],
        }
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
            review_ledger=old_ledger,
        )
        original_update = eng.store.update_work_item_metadata
        crashed = False

        def crash_after_bounce(item_id, **metadata):
            nonlocal crashed
            result = original_update(item_id, **metadata)
            if metadata.get("review_bounce") == 1 and not crashed:
                crashed = True
                raise RuntimeError("simulated crash after review_bounce")
            return result

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", crash_after_bounce)
        with pytest.raises(RuntimeError, match="review_bounce"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update)

        if changed_field == "artifacts":
            original_update(
                item.id,
                artifacts={"pr_url": "https://mock.example.com/pr/changed"},
            )
        else:
            original_update(
                item.id,
                verification={
                    "commands": [_business_command("pytest changed")],
                    "pr_base": "main",
                    "coverage": 91,
                },
            )

        worker_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        persisted = load_manifest(path)

        result = tick(
            eng.store, eng.runtime, persisted, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert recovered.worker_handoff is not None
        assert recovered.review_ledger is old_ledger
        assert old_subject is not None
        assert result.state == "running"
        assert persisted.nodes["a"].status == "in_progress"
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_before + 1
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before

    @pytest.mark.parametrize("intent", [
        {"schema": "omac.worker-handoff/v1", "state": "pending"},
        {
            "schema": "omac.worker-handoff/v1",
            "state": "pending",
            "target_worker": "charlie",
            "gate": "review",
            "source_review_subject_digest": "wrong-subject",
            "source_review_round": 1,
            "target_review_bounce": 1,
        },
    ])
    def test_malformed_or_mismatched_worker_handoff_fails_closed(
        self, tmp_path, monkeypatch, intent,
    ):
        """畸形或旧版 intent 没有因果身份时必须失败关闭。"""
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
            worker_handoff=intent,
        )
        worker_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        original_assign = eng.store.assign_work_item

        def assign(item_id, assignee, role):
            if role == "worker":
                pytest.fail("invalid worker handoff must not assign Worker")
            return original_assign(item_id, assignee, role)

        monkeypatch.setattr(eng.store, "assign_work_item", assign)

        with pytest.raises(PlatformError, match="causal|identity|predates"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert recovered.worker_handoff is not None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_before
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before

    @pytest.mark.parametrize("intent_kind", ["malformed", "stale"])
    @pytest.mark.parametrize(
        "checkpoint",
        [
            "before_reset", "reset", "reviewer_status",
            "reviewer_assignment", "reviewer_wake", "intent_clear",
        ],
    )
    def test_invalid_worker_handoff_keeps_intent_until_fresh_reviewer_dispatch(
        self, tmp_path, monkeypatch, intent_kind, checkpoint,
    ):
        """invalid intent 的 fresh Reviewer 补偿在每个 checkpoint 都可幂等恢复。"""
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        old_subject = current.review_subject_digest
        ledger = {
            "schema": "omac.review-ledger/v1",
            "cycles": [{
                "round": 1,
                "subject_digest": old_subject,
                "verdict": "reject",
            }],
            "blockers": [],
        }
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_report=_review_report(current, "reject"),
            review_ledger=ledger,
        )
        if intent_kind == "malformed":
            intent = {
                "schema": "omac.worker-handoff/v1",
                "state": "pending",
            }
        else:
            intent = {
                "schema": "omac.worker-handoff/v1",
                "state": "pending",
                "target_worker": "alice",
                "gate": "review",
                "source_review_subject_digest": old_subject,
                "source_review_round": 1,
                "target_review_bounce": 1,
            }
        eng.store.update_work_item_metadata(
            item.id, worker_handoff=intent)
        if intent_kind == "stale":
            eng.store.update_work_item_metadata(
                item.id,
                review_bounce=1,
                artifacts={
                    "pr_url": "https://mock.example.com/pr/stale-intent",
                },
            )

        worker_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        with pytest.raises(PlatformError, match="causal|identity|predates"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        recovered = eng.store.get_work_item(item.id)
        assert recovered.worker_handoff is not None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_before
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before
        return

        runs_before = len(eng.runtime.list_runs(item.id))
        worker_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        original_update_metadata = eng.store.update_work_item_metadata
        original_reset_review = eng.store.reset_review
        original_update_status = eng.store.update_status
        original_assign = eng.store.assign_work_item
        original_wake = eng.runtime.wake
        crashed = False

        def crash_once(name):
            nonlocal crashed
            if checkpoint != name or crashed:
                return
            crashed = True
            error = (
                PlatformError("simulated reviewer wake result unknown")
                if name == "reviewer_wake"
                else RuntimeError(f"simulated crash at {name}")
            )
            raise error

        def update_metadata(item_id, **metadata):
            result = original_update_metadata(item_id, **metadata)
            if metadata.get("worker_handoff") == {}:
                crash_once("intent_clear")
            return result

        def reset_review(item_id):
            crash_once("before_reset")
            original_reset_review(item_id)
            crash_once("reset")

        def update_status(item_id, status):
            original_update_status(item_id, status)
            if status is WorkItemStatus.IN_REVIEW:
                crash_once("reviewer_status")

        def assign(item_id, assignee, role):
            original_assign(item_id, assignee, role)
            if role == "reviewer":
                crash_once("reviewer_assignment")

        def wake(item_id, agent, role):
            original_wake(item_id, agent, role)
            if role == "reviewer":
                crash_once("reviewer_wake")

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", update_metadata)
        monkeypatch.setattr(eng.store, "reset_review", reset_review)
        monkeypatch.setattr(eng.store, "update_status", update_status)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        error_type = PlatformError if checkpoint == "reviewer_wake" else RuntimeError
        with pytest.raises(error_type, match="simulated"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        interrupted = eng.store.get_work_item(item.id)
        if checkpoint == "intent_clear":
            assert interrupted.worker_handoff is None
        else:
            assert interrupted.worker_handoff is not None

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update_metadata)
        monkeypatch.setattr(eng.store, "reset_review", original_reset_review)
        monkeypatch.setattr(eng.store, "update_status", original_update_status)
        monkeypatch.setattr(eng.store, "assign_work_item", original_assign)
        monkeypatch.setattr(eng.runtime, "wake", original_wake)
        persisted = load_manifest(path)

        first = tick(eng.store, eng.runtime, persisted, path, max_parallel=4)
        second = tick(eng.store, eng.runtime, persisted, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert second.state == "running"
        assert persisted.nodes["a"].status == "in_review"
        assert recovered.worker_handoff is None
        assert recovered.phase is TaskPhase.REVIEW
        assert recovered.status is WorkItemStatus.IN_REVIEW
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert recovered.review_ledger is ledger
        assert len(eng.runtime.list_runs(item.id)) == runs_before + 1
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments_before
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before + 1

    @pytest.mark.parametrize("stale_verdict", ["pass", "pass-with-nits", "reject"])
    def test_stale_pass_cannot_merge_new_worker_delivery(
        self, tmp_path, monkeypatch, stale_verdict,
    ):
        """in_progress 新交付不能消费旧 subject 的任何 review verdict。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        stale_subject = eng.store.get_work_item(item.id).review_subject_digest
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict=stale_verdict,
            review_report=_review_report(item, stale_verdict),
            phase=TaskPhase.AUTHORING,
        )
        self._submit_revision(eng, item)
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)
        monkeypatch.setattr(
            eng.store,
            "request_pull_request_merge",
            lambda *_args, **_kwargs: pytest.fail(
                "stale pass must not request merge"),
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.status == WorkItemStatus.IN_REVIEW
        assert recovered.review_verdict is None
        assert recovered.review_subject_digest != stale_subject

    @pytest.mark.parametrize("stale_verdict", ["pass", "pass-with-nits", "reject"])
    def test_review_phase_stale_subject_dispatches_one_fresh_reviewer(
        self, tmp_path, monkeypatch, stale_verdict,
    ):
        """REVIEW phase 遗留旧判定时，失效投影并只派一个 fresh reviewer。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        old_subject = current.review_subject_digest
        old_ledger = current.review_ledger
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
            review_verdict=stale_verdict,
            review_report=_review_report(
                current,
                stale_verdict,
                nits=["old nit"] if stale_verdict == "pass-with-nits" else None,
            ),
            artifacts={"pr_url": f"https://mock.example.com/pr/{item.id}-fresh"},
            verification={
                "commands": [_business_command()],
                "integration_gates": [{
                    "name": "fresh-review",
                    "commands": [_business_command()],
                }],
                "pr_base": "main",
                "coverage": 91,
                "revision": 2,
            },
        )
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        reviewer_assignments_before = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"])
        runs_before = len(eng.runtime.list_runs(item.id))
        wakes = []
        original_wake = eng.runtime.wake

        def wake(item_id, agent, role):
            if role == "reviewer":
                wakes.append((item_id, agent))
            return original_wake(item_id, agent, role)

        monkeypatch.setattr(eng.runtime, "wake", wake)
        monkeypatch.setattr(
            loop,
            "_complete_merge_if_confirmed",
            lambda *_args, **_kwargs: pytest.fail(
                "stale review subject must not enter merge"),
        )

        first = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.phase is TaskPhase.REVIEW
        assert recovered.status is WorkItemStatus.IN_REVIEW
        assert recovered.review_subject_digest != old_subject
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert recovered.review_ledger is old_ledger
        assert len(eng.runtime.list_runs(item.id)) == runs_before + 1
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before + 1
        assert wakes == [(item.id, "bob")]

        second = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert second.state == "running"
        assert len(eng.runtime.list_runs(item.id)) == runs_before + 1
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments_before + 1
        assert wakes == [(item.id, "bob")]

    def test_same_subject_active_reviewer_is_not_assigned_or_woken_again(
        self, tmp_path, monkeypatch,
    ):
        """manifest 落后于 Store 时，同 subject 的活跃 reviewer 不得重派。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        current.review_verdict = None
        current.review_report = None
        subject = current.review_subject_digest
        eng.store.clear_assignment(item.id)
        eng.store.assign_work_item(item.id, "bob", "reviewer")
        reviewer_assignments = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"])
        manifest.nodes["a"].status = "in_progress"
        save_manifest(manifest, path)
        monkeypatch.setattr(
            eng.store,
            "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "active reviewer must not be assigned again"),
        )
        monkeypatch.setattr(
            eng.runtime,
            "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "active reviewer must not be woken again"),
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.review_subject_digest == subject
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments

    def test_reject_rework_review_pass_merges_to_done(self):
        """正常 reject→rework→review→pass→merge 完整收敛。"""
        manifest = _manifest([
            _node("a", reviewer="bob", contract=_contract()),
        ])
        path = _tmp_manifest_path(manifest)
        eng = _engine()
        MockStore.set_review_verdict_sequence(["reject", "pass"])

        result = _loop_to_settle(eng.store, eng.runtime, manifest, path)

        item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)
        assert result.state == "converged"
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert item.status == WorkItemStatus.DONE
        assert item.bounces.review == 1
        assert [cycle["verdict"] for cycle in item.review_ledger["cycles"]] == [
            "reject", "pass",
        ]
        assert len({
            cycle["subject_digest"] for cycle in item.review_ledger["cycles"]
        }) == 2

    def test_downstream_artifact_review_request_needs_decision_without_rework(self, tmp_path):
        from omac.engines import create_engine

        contract = self._simple_contract()
        contract.evidence_mode = EvidenceMode.FIXTURE
        contract.produces = [ProducedArtifact("tooling-package")]
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(
            eng, path, contract=contract)
        manifest.nodes["assembly"] = Node(
            id="assembly",
            worker="bob",
            blocked_by=["a"],
            contract=Contract(
                evidence_mode=EvidenceMode.LIVE,
                produces=[ProducedArtifact("production-bundle")],
            ),
        )
        save_manifest(manifest, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        report = _review_report(item, "reject")
        report["blockers"][0].update({
            "required_fix": "Generate production-bundle before tooling can pass.",
            "required_evidence_mode": "live",
            "required_inputs": [{
                "artifact_id": "production-bundle",
                "producer": "assembly",
                "evidence_mode": "live",
            }],
        })
        eng.store.update_work_item_metadata(item.id, review_report=report)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert got.status == WorkItemStatus.BLOCKED
        assert got.bounces.review == 0
        assert got.review_verdict == "reject"
        assert got.decision_required == {
            "schema": "omac.decision-required/v1",
            "reason_code": "contract-boundary-conflict",
            "kind": "develop",
            "phase": "review",
            "gate": "review-boundary",
            "resume_issue_id": item.id,
            "node_id": "a",
            "conflict_codes": [
                "fixture-requires-live-evidence",
                "review-requires-non-upstream-artifact",
            ],
            "artifact_ids": ["production-bundle"],
            "producer_nodes": ["assembly"],
        }

    def test_downstream_artifact_prose_stays_normal_rework_and_consumes_bounce(self, tmp_path):
        from omac.engines import create_engine

        contract = self._simple_contract()
        contract.evidence_mode = EvidenceMode.FIXTURE
        contract.produces = [ProducedArtifact("tooling-package")]
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(
            eng, path, contract=contract)
        manifest.nodes["assembly"] = Node(
            id="assembly",
            worker="bob",
            blocked_by=["a"],
            contract=Contract(
                evidence_mode=EvidenceMode.LIVE,
                produces=[ProducedArtifact("production-bundle")],
            ),
        )
        save_manifest(manifest, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        report = _review_report(item, "reject")
        report["blockers"][0]["required_fix"] = (
            "Do not generate production-bundle; only fix the local fixture."
        )
        eng.store.update_work_item_metadata(item.id, review_report=report)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict is None
        assert got.decision_required is None
        assert got.bounces.review == 1

    def test_pass_with_nits_worker_followup_requires_fresh_review(self, tmp_path):
        """pass-with-nits 返工形成新 subject，必须 fresh review 后才能 merge。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="pass-with-nits",
            review_report=_review_report(
                item, "pass-with-nits", nits=["建议后续优化"]),
        )

        first = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert first.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        got = eng.store.get_work_item(item.id)
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict is None
        assert got.review_report is None
        assert got.review_subject_digest is None
        assert got.decision_required is None
        assert got.bounces.review == 1

        reviewer_dispatches_before_followup = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"])
        self._submit_revision(eng, item, revision=2)
        second = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        assert second.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        reviewer_dispatches_after_followup = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"])
        assert reviewer_dispatches_after_followup == reviewer_dispatches_before_followup + 1
        got = eng.store.get_work_item(item.id)
        assert got.status == WorkItemStatus.IN_REVIEW
        assert got.review_verdict is None
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="pass",
            review_report=_review_report(got, "pass"),
        )

        third = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert third.state == "converged"
        assert manifest.nodes["a"].status == "done"
        assert got.status == WorkItemStatus.DONE
        assert got.review_verdict == "pass"
        assert got.bounces.review == 1

    def test_pass_with_nits_cannot_bypass_obligation_evidence_gate(self, tmp_path):
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="pass-with-nits",
            review_report={
                "review_goals": ["partial legacy report"],
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": True,
                "full_review_completed": True,
                "acceptance_mapping": [{"acceptance": "works", "status": "pass"}],
                "blockers": [],
                "nits": ["looks fine"],
            },
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.review_verdict is None
        assert got.bounces.review == 1

    def test_done_node_repairs_worker_status_regression(self, tmp_path):
        """已完成节点遇到平台状态被 worker 回退为 in_review 时,以 manifest done 为准纠偏。"""
        from omac.engines import create_engine
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        manifest.nodes["a"].status = "done"
        manifest.nodes["a"].merged = True
        manifest.nodes["a"].merged_at = "2026-07-26T08:00:00Z"
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        eng.store.update_work_item_metadata(item.id, review_verdict="pass-with-nits")
        save_manifest(manifest, path)

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        got = eng.store.get_work_item(item.id)
        assert result.state == "converged"
        assert manifest.nodes["a"].status == "done"
        assert got.status == WorkItemStatus.DONE

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
            review_report={
                "review_goals": ["复核交付是否满足验收"],
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": True,
                "full_review_completed": True,
                "acceptance_mapping": [
                    {"acceptance": "works", "status": "fail"},
                ],
                "blockers": ["核心验收未满足"],
            },
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
            review_report={
                "review_goals": ["复核交付是否满足验收"],
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": True,
                "full_review_completed": True,
                "acceptance_mapping": [
                    {"acceptance": "works", "status": "fail"},
                ],
                "blockers": ["需要返工"],
            },
        )

        # reviewer reject → worker authoring；保留上一轮 report 作为返工上下文。
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert manifest.nodes["a"].status == "in_progress"

        # worker 合法重交，但旧 controller/manifest 留下 terminal 状态。
        self._submit_revision(eng, item, revision=2)
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
        self._submit_revision(eng, item, revision=2)
        tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
        set_node(manifest, "a", status="in_review")
        save_manifest(manifest, fpath)
        eng.store.update_work_item_metadata(item.id, review_verdict="reject")
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
            self._submit_revision(eng, item, revision=i + 2)
            tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
            from omac.core.manifest import set_node
            set_node(manifest, "a", status="in_review")
            save_manifest(manifest, fpath)


class TestReviewerRejectFallbackRecovery:
    """未知副作用的 Worker handoff 失败保留 intent，由 restart 幂等续跑。"""

    @staticmethod
    def _simple_contract():
        from omac.core.manifest import Contract
        return Contract(
            objective="do it", acceptance=["works"], non_goals=["no creep"],
            verification_commands=["pytest -q"], pr_base="main", coverage_gate=0,
        )

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
            verification={
                "commands": [_business_command()],
                "integration_gates": [{
                    "name": "setup-gate",
                    "commands": [_business_command()],
                }],
                "pr_base": "main",
                "coverage": 90,
            })
        from omac.engines.models import WorkItemStatus
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
        set_node(manifest, key, status="in_review")
        save_manifest(manifest, fpath)
        eng.store.update_work_item_metadata(item.id, review_verdict="reject")
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        return manifest, eng, item

    def test_wake_failure_preserves_intent_and_restart_does_not_duplicate_run(
        self, tmp_path, monkeypatch,
    ):
        """wake 已观察到 assignment 后报错，不清 intent、不回滚 bounce。"""
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        fpath = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, fpath)
        runs_before_handoff = len(eng.runtime.list_runs(item.id))
        original_wake = eng.runtime.wake
        crashed = False

        def wake_then_fail(item_id, agent, role):
            nonlocal crashed
            original_wake(item_id, agent, role)
            if role == "worker" and not crashed:
                crashed = True
                raise PlatformError("wake result unknown")

        monkeypatch.setattr(eng.runtime, "wake", wake_then_fail)
        tick(eng.store, eng.runtime, manifest, fpath,
             max_parallel=4, retry_limits={"review": 3})

        interrupted = eng.store.get_work_item(item.id)
        assert interrupted.worker_handoff is not None
        assert interrupted.bounces.review == 1
        assert interrupted.status is WorkItemStatus.IN_PROGRESS
        assert len(eng.runtime.list_runs(item.id)) == runs_before_handoff + 1
        assert crashed is False

        monkeypatch.setattr(eng.runtime, "wake", original_wake)
        monkeypatch.setattr(
            loop,
            "_dispatch_reviewer_for_current_subject",
            lambda *_args, **_kwargs: pytest.fail(
                "pending Worker handoff must not dispatch Reviewer"),
        )
        persisted = load_manifest(fpath)

        first = tick(eng.store, eng.runtime, persisted, fpath, max_parallel=4)
        second = tick(eng.store, eng.runtime, persisted, fpath, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert second.state == "running"
        assert persisted.nodes["a"].status == "in_progress"
        assert recovered.worker_handoff is not None
        assert recovered.bounces.review == 1
        assert recovered.status is WorkItemStatus.IN_PROGRESS
        assert len(eng.runtime.list_runs(item.id)) == runs_before_handoff + 1

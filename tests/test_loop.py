"""pipeline/loop:单轮 tick 核心——结果回收 → 就绪计算 → 派发。

验收标准:
- mock:多节点带依赖 manifest,循环调 tick 至 converged,节点全 done
- mock 失败注入:tick 返回 needs_decision,失败节点 blocked、下游 blocked、report 完整
- 幂等:tick 序列中途重建 loop 对同一 manifest 继续,done 节点复用、不重复建 issue
- 无 reviewer 节点也必须经远端 MERGED + mergedAt 门;有 reviewer 先经 in_review
- 不存在任何自动重试路径(blocked 节点在后续 tick 保持 blocked)
"""
from copy import deepcopy
import os
from dataclasses import replace
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
    _dump_contract,
    load_manifest,
    save_manifest,
    set_node,
)
from omac.core.review_convergence import (
    REVIEW_PROTOCOL_VERSION, build_review_obligations, open_blockers,
    review_convergence_decision, review_subject_digest,
)
from omac.engines import create_engine
from omac.engines.mock import MockRuntime, MockStore
from omac.core.taskmeta import (
    MACHINE_FEEDBACK_REF_KEY,
    REVIEW_REPORT_REF_KEY,
    ReviewerRunBaseline,
    TaskPhase,
    WorkerHandoffIntent,
)
from omac.engines.models import (
    AgentRunObservation,
    EngineConfig,
    RuntimeCapabilities,
    WorkItemControlProjection,
    WorkItemPayload,
    WorkItemStatus,
)
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.dispatch import build_show_output, submit as submit_work
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


def test_direct_run_observation_follows_one_platform_retry_chain():
    observed = loop._observe_direct_run_attempt(
        [
            AgentRunObservation(
                id="run-3", kind="direct", status="completed",
                agent_id="agent-1", retry_of_run_id="run-2",
            ),
            AgentRunObservation(
                id="run-2", kind="direct", status="failed",
                agent_id="agent-1", retry_of_run_id="run-1",
                error="read timeout while waiting for provider response",
            ),
            AgentRunObservation(
                id="run-1", kind="direct", status="failed",
                agent_id="agent-1",
                error="read timeout while waiting for provider response",
            ),
        ],
        "agent-1",
        target_run_id="run-1",
    )

    assert observed.state == "terminal"
    assert observed.target_run_id == "run-3"
    assert observed.terminal is not None
    assert observed.terminal.run.id == "run-3"


def test_direct_run_observation_rejects_retry_chain_forks():
    observed = loop._observe_direct_run_attempt(
        [
            AgentRunObservation(
                id="run-3a", kind="direct", status="completed",
                agent_id="agent-1", retry_of_run_id="run-2",
            ),
            AgentRunObservation(
                id="run-3b", kind="direct", status="completed",
                agent_id="agent-1", retry_of_run_id="run-2",
            ),
            AgentRunObservation(
                id="run-2", kind="direct", status="failed",
                agent_id="agent-1", retry_of_run_id="run-1",
                error="read timeout while waiting for provider response",
            ),
            AgentRunObservation(
                id="run-1", kind="direct", status="failed",
                agent_id="agent-1",
                error="read timeout while waiting for provider response",
            ),
        ],
        "agent-1",
        target_run_id="run-1",
    )

    assert observed.state == "unexpected"
    assert observed.detail == "ambiguous target Run"


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
        worker_handoff={},
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


def _pending_handoff_preparation_fixture(tmp_path):
    engine = _engine(MOCK_AUTO_COMPLETE="false")
    node = _node("handoff-preparation", contract=_contract())
    item = engine.store.create_work_item(
        "ws",
        node.title or node.id,
        "stale review projection",
        dag_key=node.id,
        worker=node.worker,
        reviewer=node.reviewer,
    )
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_comment="stale blocker",
        review_subject_digest="stale-review-subject",
    )
    engine.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    node.work_item_id = item.id
    manifest = _manifest([node])
    path = str(tmp_path / "handoff-preparation.yaml")
    save_manifest(manifest, path)
    return engine, manifest, path, node, item


def test_handoff_preparation_timeout_after_apply_observes_and_dispatches(
    tmp_path, monkeypatch,
):
    """A timed-out idempotent write may be accepted only after authority confirms it."""
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    original_reset = engine.store.reset_review
    reset_calls = 0

    def reset_then_timeout(item_id):
        nonlocal reset_calls
        reset_calls += 1
        original_reset(item_id)
        raise PlatformError("Request timed out: server did not respond")

    monkeypatch.setattr(engine.store, "reset_review", reset_then_timeout)
    monkeypatch.setattr(
        engine.store,
        "is_transient_transport_error",
        lambda _error: True,
        raising=False,
    )

    dispatched = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    current = engine.store.get_work_item(item.id)
    assert dispatched == [node.id]
    assert reset_calls == 1
    assert manifest.nodes[node.id].status == "in_progress"
    assert current.status is WorkItemStatus.IN_PROGRESS
    assert current.phase is TaskPhase.AUTHORING
    assert current.review_verdict is None
    assert current.worker_handoff is not None
    assert len(engine.runtime.list_runs(item.id)) == 1
    assert engine.store.get_comments(item.id) == []


def test_handoff_intent_timeout_after_apply_confirms_same_generation(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    original_update = engine.store.update_work_item_metadata
    attempted = []

    def update_then_timeout(item_id, **metadata):
        intent = metadata.get("worker_handoff")
        result = original_update(item_id, **metadata)
        if intent is not None and not attempted:
            attempted.append(intent)
            raise PlatformError("Request timed out: server did not respond")
        return result

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", update_then_timeout)
    monkeypatch.setattr(
        engine.store, "is_transient_transport_error", lambda _error: True)

    dispatched = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    current = engine.store.get_work_item(item.id)
    assert dispatched == [node.id]
    assert current.worker_handoff is not None
    assert current.worker_handoff.generation == attempted[0].generation
    assert len(engine.runtime.list_runs(item.id)) == 1
    assert engine.store.get_comments(item.id) == []


def test_handoff_intent_timeout_before_apply_keeps_manifest_retryable(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    original_update = engine.store.update_work_item_metadata
    original_bounces = engine.store.get_work_item(item.id).bounces
    attempted = []

    def timeout_before_intent(item_id, **metadata):
        intent = metadata.get("worker_handoff")
        if intent is not None and not attempted:
            attempted.append(intent)
            raise PlatformError("Request timed out: server did not respond")
        return original_update(item_id, **metadata)

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", timeout_before_intent)
    monkeypatch.setattr(
        engine.store, "is_transient_transport_error", lambda _error: True)

    first = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    interrupted = engine.store.get_work_item(item.id)
    assert first == []
    assert manifest.nodes[node.id].status == "todo"
    assert interrupted.worker_handoff is None
    assert interrupted.bounces == original_bounces
    assert engine.runtime.list_runs(item.id) == []
    assert engine.store.get_comments(item.id) == []

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update)
    restarted = load_manifest(path)
    result = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)

    recovered = engine.store.get_work_item(item.id)
    assert result.state == "running"
    assert recovered.worker_handoff is not None
    assert recovered.worker_handoff.generation != attempted[0].generation
    assert len(engine.runtime.list_runs(item.id)) == 1


def test_handoff_intent_read_failure_keeps_todo_and_reuses_persisted_intent(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    original_update = engine.store.update_work_item_metadata
    original_observe = engine.store.observe_work_item_control
    attempted = []
    intent_written = False

    def persist_intent(item_id, **metadata):
        nonlocal intent_written
        result = original_update(item_id, **metadata)
        intent = metadata.get("worker_handoff")
        if intent is not None and not attempted:
            attempted.append(intent)
            intent_written = True
        return result

    def unreadable_after_intent(item_id):
        if intent_written:
            raise PlatformError("Request timed out: server did not respond")
        return original_observe(item_id)

    monkeypatch.setattr(engine.store, "update_work_item_metadata", persist_intent)
    monkeypatch.setattr(
        engine.store, "observe_work_item_control", unreadable_after_intent)
    monkeypatch.setattr(
        engine.store, "is_transient_transport_error", lambda _error: True)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    first = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    assert first == []
    assert manifest.nodes[node.id].status == "todo"
    assert engine.runtime.list_runs(item.id) == []
    assert engine.store.get_comments(item.id) == []

    monkeypatch.setattr(engine.store, "observe_work_item_control", original_observe)
    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update)
    persisted = engine.store.get_work_item(item.id).worker_handoff
    assert persisted is not None
    assert persisted.generation == attempted[0].generation

    restarted = load_manifest(path)
    result = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)

    recovered = engine.store.get_work_item(item.id)
    assert result.state == "running"
    assert recovered.worker_handoff.generation == attempted[0].generation
    assert len(engine.runtime.list_runs(item.id)) == 1


def test_handoff_preparation_timeout_before_apply_stays_pending_and_restarts(
    tmp_path, monkeypatch,
):
    """An unconfirmed preparation keeps its intent and creates no duplicate Run."""
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    original_reset = engine.store.reset_review
    original_bounces = engine.store.get_work_item(item.id).bounces

    monkeypatch.setattr(
        engine.store,
        "reset_review",
        lambda _item_id: (_ for _ in ()).throw(
            PlatformError("Request timed out: server did not respond")),
    )
    monkeypatch.setattr(
        engine.store,
        "is_transient_transport_error",
        lambda _error: True,
        raising=False,
    )

    first = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    interrupted = engine.store.get_work_item(item.id)
    assert first == []
    assert manifest.nodes[node.id].status == "in_progress"
    assert interrupted.status is WorkItemStatus.IN_REVIEW
    assert interrupted.phase is TaskPhase.REVIEW
    assert interrupted.worker_handoff is not None
    assert interrupted.bounces == original_bounces
    assert engine.runtime.list_runs(item.id) == []
    assert engine.store.get_comments(item.id) == []

    monkeypatch.setattr(engine.store, "reset_review", original_reset)
    restarted = load_manifest(path)
    second = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)
    third = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)

    recovered = engine.store.get_work_item(item.id)
    assert second.state == "running"
    assert third.state == "running"
    assert recovered.status is WorkItemStatus.IN_PROGRESS
    assert recovered.phase is TaskPhase.AUTHORING
    assert len(engine.runtime.list_runs(item.id)) == 1
    assert len([
        entry for entry in engine.store.assign_log
        if entry[0] == item.id and entry[2] == "worker"
    ]) == 1


def test_handoff_preparation_hard_error_still_blocks(tmp_path, monkeypatch):
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    monkeypatch.setattr(
        engine.store,
        "reset_review",
        lambda _item_id: (_ for _ in ()).throw(
            PlatformError("validation rejected: invalid metadata")),
    )
    monkeypatch.setattr(
        engine.store,
        "is_transient_transport_error",
        lambda _error: False,
        raising=False,
    )

    dispatched = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    current = engine.store.get_work_item(item.id)
    assert dispatched == []
    assert manifest.nodes[node.id].status == "blocked"
    assert current.status is WorkItemStatus.BLOCKED
    assert current.worker_handoff is not None
    assert engine.runtime.list_runs(item.id) == []
    assert len(engine.store.get_comments(item.id)) == 1


def test_handoff_successful_write_observes_stale_projection_until_converged(
    tmp_path, monkeypatch,
):
    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    original_reset = engine.store.reset_review
    original_observe = engine.store.observe_work_item_control
    stale = WorkItemControlProjection(replace(
        original_observe(item.id).work_item))
    stale_reads = 0
    reset_applied = False

    def reset(item_id):
        nonlocal reset_applied
        original_reset(item_id)
        reset_applied = True

    def observe(item_id):
        nonlocal stale_reads
        if reset_applied and stale_reads < 2:
            stale_reads += 1
            return stale
        return original_observe(item_id)

    monkeypatch.setattr(engine.store, "reset_review", reset)
    monkeypatch.setattr(engine.store, "observe_work_item_control", observe)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    dispatched = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    assert dispatched == [node.id]
    assert stale_reads == 2
    assert len(engine.runtime.list_runs(item.id)) == 1
    assert engine.store.get_comments(item.id) == []


def test_handoff_successful_write_stays_pending_when_projection_never_converges(
    tmp_path, monkeypatch,
):
    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    original_reset = engine.store.reset_review
    original_observe = engine.store.observe_work_item_control
    stale = WorkItemControlProjection(replace(
        original_observe(item.id).work_item))
    reset_applied = False

    def reset(item_id):
        nonlocal reset_applied
        original_reset(item_id)
        reset_applied = True

    def observe(item_id):
        if reset_applied:
            return stale
        return original_observe(item_id)

    monkeypatch.setattr(engine.store, "reset_review", reset)
    monkeypatch.setattr(engine.store, "observe_work_item_control", observe)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    first = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    assert first == []
    assert manifest.nodes[node.id].status == "in_progress"
    assert engine.runtime.list_runs(item.id) == []
    assert engine.store.get_comments(item.id) == []

    monkeypatch.setattr(engine.store, "observe_work_item_control", original_observe)
    restarted = load_manifest(path)
    result = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)

    assert result.state == "running"
    assert len(engine.runtime.list_runs(item.id)) == 1


def _set_stale_handoff_body_facts(engine, item):
    engine.store.update_work_item_metadata(
        item.id,
        source_refs=[{"label": "stale", "issue_id": "old"}],
        blocked_by=["stale-dependency"],
    )


def test_handoff_body_partial_apply_stays_pending_and_restarts(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    manifest.meta["source_issues"] = ["plan-current"]
    _set_stale_handoff_body_facts(engine, item)
    original_update = engine.store.update_work_item_metadata
    body_attempted = False

    def partially_apply_body(item_id, **metadata):
        nonlocal body_attempted
        if "description" in metadata and not body_attempted:
            body_attempted = True
            original_update(item_id, description=metadata["description"])
            raise PlatformError("Request timed out: server did not respond")
        return original_update(item_id, **metadata)

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", partially_apply_body)
    monkeypatch.setattr(
        engine.store, "is_transient_transport_error", lambda _error: True)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    first = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    interrupted = engine.store.get_work_item(item.id)
    stale_source_refs = list(interrupted.source_refs)
    assert first == []
    assert manifest.nodes[node.id].status == "in_progress"
    assert interrupted.worker_handoff is not None
    assert interrupted.source_refs == [
        {"label": "stale", "issue_id": "old"}]
    assert interrupted.blocked_by == ["stale-dependency"]
    assert engine.runtime.list_runs(item.id) == []
    assert engine.store.get_comments(item.id) == []

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update)
    restarted = load_manifest(path)
    result = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)

    assert result.state == "running"
    recovered = engine.store.get_work_item(item.id)
    assert recovered.source_refs != stale_source_refs
    assert recovered.blocked_by == []
    assert len(engine.runtime.list_runs(item.id)) == 1


def test_handoff_body_timeout_after_full_apply_observes_and_dispatches(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    manifest.meta["source_issues"] = ["plan-current"]
    _set_stale_handoff_body_facts(engine, item)
    original_update = engine.store.update_work_item_metadata
    body_attempted = False

    def apply_body_then_timeout(item_id, **metadata):
        nonlocal body_attempted
        result = original_update(item_id, **metadata)
        if "description" in metadata and not body_attempted:
            body_attempted = True
            raise PlatformError("Request timed out: server did not respond")
        return result

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", apply_body_then_timeout)
    monkeypatch.setattr(
        engine.store, "is_transient_transport_error", lambda _error: True)

    dispatched = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    current = engine.store.get_work_item(item.id)
    assert dispatched == [node.id]
    assert current.source_refs != [
        {"label": "stale", "issue_id": "old"}]
    assert current.blocked_by == []
    assert len(engine.runtime.list_runs(item.id)) == 1


def test_handoff_body_read_failure_stays_pending_and_restarts(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    engine, manifest, path, node, item = _pending_handoff_preparation_fixture(
        tmp_path)
    manifest.meta["source_issues"] = ["plan-current"]
    _set_stale_handoff_body_facts(engine, item)
    original_update = engine.store.update_work_item_metadata
    original_observe = engine.store.observe_work_item_control
    body_applied = False

    def apply_body(item_id, **metadata):
        nonlocal body_applied
        result = original_update(item_id, **metadata)
        if "description" in metadata:
            body_applied = True
        return result

    def unreadable_body(item_id):
        if body_applied:
            raise PlatformError("Request timed out: server did not respond")
        return original_observe(item_id)

    monkeypatch.setattr(engine.store, "update_work_item_metadata", apply_body)
    monkeypatch.setattr(
        engine.store, "observe_work_item_control", unreadable_body)
    monkeypatch.setattr(
        engine.store, "is_transient_transport_error", lambda _error: True)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    first = loop._dispatch(
        engine.store, engine.runtime, manifest, path, [node.id], 1)

    assert first == []
    assert manifest.nodes[node.id].status == "in_progress"
    assert engine.runtime.list_runs(item.id) == []
    assert engine.store.get_comments(item.id) == []

    monkeypatch.setattr(engine.store, "observe_work_item_control", original_observe)
    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update)
    restarted = load_manifest(path)
    result = tick(
        engine.store, engine.runtime, restarted, path, max_parallel=1)

    assert result.state == "running"
    assert len(engine.runtime.list_runs(item.id)) == 1


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

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update_metadata)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.store, "update_status", update_status)
        monkeypatch.setattr(
            eng.store,
            "set_node_contract",
            lambda *_args, **_kwargs: pytest.fail(
                "reused item must not republish its contract"),
        )
        monkeypatch.setattr(
            eng.runtime,
            "wake",
            lambda *_args: pytest.fail(
                "assignment-created Run must not also be woken"),
        )

        result = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        current = eng.store.get_work_item(reused.id)
        summary = build_show_output(current, "worker:alice")
        assert result.dispatched == [target.id]
        assert events == ["metadata", "status", "metadata", "assign", "metadata"]
        assert len(metadata_calls) == 3
        assert set(metadata_calls[0]) == {"worker_handoff"}
        assert set(metadata_calls[1]) == {
            "blocked_by", "description", "source_refs",
        }
        assert set(metadata_calls[2]) == {"worker_handoff"}
        assert "worker" not in metadata_calls[1]
        assert "reviewer" not in metadata_calls[1]
        assert current.worker_handoff is not None
        assert current.worker_handoff.gate == "explicit-dispatch"
        assert current.worker_handoff.target_run_id is not None
        assert current.worker == "alice"
        assert current.reviewer == "bob"
        assert current.review_ledger is review_ledger
        assert current.review_report is None
        assert current.review_comment is None
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

    def test_reused_todo_dispatch_recovers_same_intent_after_crash_before_wake(
        self, monkeypatch,
    ):
        """同 assignee 不产生 Run 时，重启只能按持久 intent 补首次 wake。"""
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = eng.store.create_work_item(
            "ws", "a", "Task a", dag_key="a", worker="alice")
        eng.store.assign_work_item(item.id, "alice", "worker")
        eng.store.update_status(item.id, WorkItemStatus.DONE)
        eng.store.update_status(item.id, WorkItemStatus.TODO)
        old_runs = list(eng.runtime.list_runs(item.id))
        assert len(old_runs) == 1 and old_runs[0].terminal

        node = _node("a", worker="alice")
        node.work_item_id = item.id
        manifest = _manifest([node])
        path = _tmp_manifest_path(manifest)

        class CrashBeforeWakeRuntime(MockRuntime):
            def __init__(self, store):
                super().__init__(store)
                self.runs = list(old_runs)
                self.crash_before_wake = True
                self.wake_calls = 0

            def list_runs(self, item_id):  # noqa: ARG002
                return list(self.runs)

            def wake(self, item_id, agent, role):  # noqa: ARG002
                self.wake_calls += 1
                if self.crash_before_wake:
                    raise RuntimeError("crash before first wake")
                self.runs.append(AgentRunObservation(
                    id="run-explicit-retry",
                    kind="direct",
                    status="running",
                    agent_id=eng.store.resolve_agent_id(agent),
                ))

        runtime = CrashBeforeWakeRuntime(eng.store)
        monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0)

        with pytest.raises(RuntimeError, match="crash before first wake"):
            tick(eng.store, runtime, manifest, path, max_parallel=1)

        interrupted = eng.store.get_work_item(item.id)
        assert interrupted.status is WorkItemStatus.IN_PROGRESS
        assert interrupted.worker_handoff is not None
        assert interrupted.worker_handoff.gate == "explicit-dispatch"
        assert interrupted.worker_handoff.target_worker_bounce == 0
        generation = interrupted.worker_handoff.generation
        assert len(runtime.runs) == 1

        runtime.crash_before_wake = False
        restarted = load_manifest(path)
        recovered = tick(
            eng.store, runtime, restarted, path, max_parallel=1)

        current = eng.store.get_work_item(item.id)
        assert recovered.state == "running"
        assert restarted.nodes["a"].status == "in_progress"
        assert current.worker_handoff is not None
        assert current.worker_handoff.generation == generation
        assert current.worker_handoff.target_run_id == "run-explicit-retry"
        assert len(runtime.runs) == 2

        tick(eng.store, runtime, restarted, path, max_parallel=1)
        assert len(runtime.runs) == 2
        assert runtime.wake_calls == 2

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

    def test_active_worker_handoff_reconcile_does_not_refresh_issue_body(
        self, monkeypatch,
    ):
        manifest = _manifest([_node("a")])
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        body_updates = 0
        original_update = eng.store.update_work_item_metadata

        def update(item_id, **kwargs):
            nonlocal body_updates
            if "description" in kwargs:
                body_updates += 1
            return original_update(item_id, **kwargs)

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)

        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)

        assert body_updates == 0

    def test_first_worker_wake_refreshes_issue_body_exactly_once(
        self, monkeypatch,
    ):
        manifest = _manifest([_node("a")])
        path = _tmp_manifest_path(manifest)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        runs = []
        body_updates = 0
        wake_calls = 0
        original_update = eng.store.update_work_item_metadata

        def update(item_id, **kwargs):
            nonlocal body_updates
            if "description" in kwargs:
                body_updates += 1
            return original_update(item_id, **kwargs)

        def assign(item_id, assignee, role):
            item = eng.store.get_work_item(item_id)
            item.worker = assignee
            item.platform_assignee_id = eng.store.resolve_agent_id(assignee)

        def wake(_item_id, agent, role):
            nonlocal wake_calls
            wake_calls += 1
            runs.append(AgentRunObservation(
                id="run-worker-1", kind="direct", status="running",
                agent_id=eng.store.resolve_agent_id(agent),
            ))

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
        monkeypatch.setattr(eng.runtime, "wake", wake)

        tick(eng.store, eng.runtime, manifest, path, max_parallel=1)

        assert body_updates == 1
        assert wake_calls == 1

    def test_delayed_visible_worker_run_does_not_refresh_issue_body(
        self, monkeypatch,
    ):
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        node = _node("a")
        item = eng.store.create_work_item(
            "ws", "a", "stale body", dag_key="a", worker="alice")
        eng.store.set_node_contract(item.id, node.contract)
        eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
        node.work_item_id = item.id
        node.status = "in_progress"
        manifest = _manifest([node])
        agent_id = eng.store.resolve_agent_id("alice")
        intent = WorkerHandoffIntent(
            schema="omac.worker-handoff/v1", state="pending",
            target_worker="alice", gate="explicit-dispatch",
            source_review_subject_digest="authoring-subject",
            source_review_round=1, target_review_bounce=0,
            generation="handoff-delayed-visible",
            target_agent_id=agent_id, target_worker_bounce=0,
        )
        eng.store.update_work_item_metadata(item.id, worker_handoff=intent)
        list_calls = 0
        body_updates = 0
        original_update = eng.store.update_work_item_metadata

        def list_runs(_item_id):
            nonlocal list_calls
            list_calls += 1
            if list_calls == 1:
                return []
            return [AgentRunObservation(
                id="run-worker-delayed", kind="direct", status="running",
                agent_id=agent_id,
            )]

        def update(item_id, **kwargs):
            nonlocal body_updates
            if "description" in kwargs:
                body_updates += 1
            return original_update(item_id, **kwargs)

        monkeypatch.setattr(eng.runtime, "list_runs", list_runs)
        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(
            eng.store, "assign_work_item",
            lambda *_args: pytest.fail("visible Run must not assign again"),
        )
        monkeypatch.setattr(
            eng.runtime, "wake",
            lambda *_args: pytest.fail("visible Run must not wake again"),
        )

        result = loop._dispatch_worker_handoff(
            eng.store, eng.runtime, manifest, "a")

        assert result.state == "waiting"
        assert body_updates == 0

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
        eng.store.update_work_item_metadata(item_id, worker_handoff={})
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
        eng.store.update_work_item_metadata(item_id, worker_handoff={})
        eng.store.get_work_item(item_id).agent_run_finished_without_submit = True

        result = tick(
            eng.store, eng.runtime, manifest, path,
            max_parallel=1, retry_limits={"worker": 0},
        )

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert eng.store.get_comments(item_id) == []


def _transient_worker_handoff_fixture(tmp_path):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    manifest = _manifest([_node("a", reviewer="bob", contract=_contract())])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)
    preserved_verification = {"marker": "preserve-existing-delivery"}
    eng.store.update_work_item_metadata(
        item.id,
        verification=preserved_verification,
        verification_source=yaml.safe_dump(preserved_verification),
    )
    item = eng.store.get_work_item(item.id)
    agent_id = eng.store.resolve_agent_id("alice")
    intent = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="alice",
        gate="explicit-dispatch",
        source_review_subject_digest="authoring-subject",
        source_review_round=1,
        target_review_bounce=0,
        generation="handoff-transient-1",
        target_agent_id=agent_id,
        baseline_direct_run_ids=(),
        baseline_verification_attachment_id=(
            item.verification_ref or {}).get("attachment_id"),
        target_run_id="run-capacity-1",
        target_worker_bounce=0,
    )
    eng.store.update_work_item_metadata(item.id, worker_handoff=intent)
    return eng, manifest, path, item, agent_id


@pytest.mark.parametrize("error", [
    "Selected model is at capacity. Please try a different model.",
    "Our servers are currently overloaded",
    "Hermes provider error: Our servers are currently overloaded. Please try again later.",
    "provider error: HTTP 429 Too Many Requests",
    "transport status: HTTP 502 Bad Gateway",
    "runtime status: 503 Service Unavailable",
    "Hermes provider error: HTTP 504 Gateway Timeout",
    "connection timeout while opening provider stream",
    "read timeout while waiting for provider response",
    "provider error: connection timeout",
    "runtime status: read timeout",
    "Hermes provider error: connect timeout",
])
def test_transient_runtime_failure_allowlist(error):
    assert loop._is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


@pytest.mark.parametrize("error", [
    "HTTP 401 Missing Authentication header",
    "HTTP 403 Forbidden",
    "quota exhausted for this account",
    "billing credits exhausted",
    "model does not exist",
    "invalid model identifier",
    "request rejected by network security policy",
    "request refused by safety policy",
    "business validation failed",
    "acceptance failure: required behavior is missing",
    "unknown provider failure",
    "worker exited without submitting",
    "request rejected by network security policy because the prompt contains the phrase read timeout",
    "quota exhausted after an upstream connection timeout",
    "model does not exist; request body mentioned HTTP 503 Service Unavailable",
    "HTTP 401 Missing Authentication header; diagnostic text includes 429",
    "business validation failed: the acceptance case expects a read timeout",
    "unknown provider failure while documenting overloaded behavior",
    "documentation example: provider overloaded",
    "test assertion expected HTTP 503",
    "review evidence quoted selected model is at capacity. Please try a different model.",
    "API docs describe 429 responses",
    "business text mentions provider overloaded as an example",
    "business output documents that the provider is overloaded",
    "test assertion expected HTTP 503 Service Unavailable from the provider",
    "review evidence quotes: Selected model is at capacity. Please try a different model.",
    "provider rejected the prompt because it discusses overloaded infrastructure",
    "documentation for the HTTP API mentions 429 responses",
])
def test_transient_runtime_failure_rejects_non_allowlisted_errors(error):
    assert not loop._is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


@pytest.mark.parametrize("error", [
    "429",
    "503",
    "operation overloaded",
    "read timeout",
    "connection timeout",
])
def test_transient_runtime_failure_requires_provider_transport_context(error):
    assert not loop._is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


def test_worker_capacity_failure_reruns_without_consuming_business_bounce(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, agent_id = _transient_worker_handoff_fixture(
        tmp_path)
    runs = [AgentRunObservation(
        id="run-capacity-1", kind="direct", status="failed",
        agent_id=agent_id,
        error="Selected model is at capacity. Please try a different model.",
    )]
    wake_calls = []

    def wake(_item_id, _agent, role):
        wake_calls.append(role)
        runs.append(AgentRunObservation(
            id="run-capacity-retry", kind="direct", status="running",
            agent_id=agent_id))

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    failures = loop.collect_results(
        eng.store, eng.runtime, manifest, path,
        retry_limits={"worker": 1, "review": 1},
    )

    current = eng.store.get_work_item(item.id)
    assert failures == {}
    assert wake_calls == ["worker"]
    assert current.bounces.worker == 0
    assert current.bounces.review == 0
    assert current.phase is TaskPhase.AUTHORING
    assert current.verification == item.verification
    assert current.verification_ref == item.verification_ref
    assert current.worker_handoff.target_run_id == "run-capacity-retry"
    assert current.decision_required is None
    assert manifest.nodes["a"].status == "in_progress"


def test_initial_worker_capacity_failure_uses_causal_handoff_and_restarts_once(
    tmp_path, monkeypatch,
):
    """首次派发也必须先持久化 Run 因果身份，再做瞬时失败恢复。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    manifest = _manifest([_node("a", reviewer="bob", contract=_contract())])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)

    tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)
    intent = item.worker_handoff
    assert intent is not None
    assert intent.gate == "explicit-dispatch"
    assert intent.target_run_id is not None

    runs = [AgentRunObservation(
        id=intent.target_run_id,
        kind="direct",
        status="failed",
        agent_id=intent.target_agent_id,
        error="Selected model is at capacity. Please try a different model.",
    )]
    wake_calls = 0

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "worker"
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-initial-capacity-retry",
            kind="direct",
            status="running",
            agent_id=intent.target_agent_id,
        ))

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    persisted = load_manifest(path)
    assert loop.collect_results(
        eng.store, eng.runtime, persisted, path) == {}

    recovered = eng.store.get_work_item(item.id)
    assert wake_calls == 1
    assert recovered.worker_handoff is not None
    assert recovered.worker_handoff.target_run_id == "run-initial-capacity-retry"
    assert recovered.bounces.worker == 0
    assert recovered.bounces.review == 0
    assert persisted.nodes["a"].status == "in_progress"


def test_legacy_initial_worker_capacity_failure_migrates_and_restarts_once(
    tmp_path, monkeypatch,
):
    """旧首次派发无 handoff 时，只迁移唯一可证明的 transient Worker Run。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    node = _node("a", reviewer="bob", contract=_contract())
    item = eng.store.create_work_item(
        "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob")
    item.created_at = "2026-08-01T00:59:00Z"
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    node.status = "in_progress"
    node.work_item_id = item.id
    manifest = _manifest([node])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    worker_id = eng.store.resolve_agent_id("alice")
    runs = [AgentRunObservation(
        id="run-legacy-capacity",
        kind="direct",
        status="failed",
        agent_id=worker_id,
        created_at="2026-08-01T01:00:00Z",
        updated_at="2026-08-01T01:00:10Z",
        error="Selected model is at capacity. Please try a different model.",
    )]
    wake_calls = 0

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "worker"
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-legacy-capacity-retry",
            kind="direct",
            status="running",
            agent_id=worker_id,
            created_at="2026-08-01T01:01:00Z",
        ))

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    restarted = load_manifest(path)
    assert loop.collect_results(
        eng.store, eng.runtime, restarted, path) == {}

    recovered = eng.store.get_work_item(item.id)
    assert wake_calls == 1
    assert recovered.worker_handoff is not None
    assert recovered.worker_handoff.gate == "explicit-dispatch"
    assert recovered.worker_handoff.baseline_direct_run_ids == (
        "run-legacy-capacity",)
    assert recovered.worker_handoff.target_run_id == (
        "run-legacy-capacity-retry")
    assert recovered.bounces.worker == recovered.bounces.review == 0
    assert recovered.decision_required is None
    assert restarted.nodes["a"].status == "in_progress"


def test_legacy_initial_worker_migration_replaces_stale_control_snapshot(
    tmp_path, monkeypatch,
):
    """同轮迁移必须用新 WorkItem 继续 handoff，不能复用旧 control snapshot。"""
    import copy

    eng = _engine(MOCK_AUTO_COMPLETE="false")
    node = _node("a", reviewer="bob", contract=_contract())
    item = eng.store.create_work_item(
        "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob")
    item.created_at = "2026-08-01T00:59:00Z"
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    node.status = "in_progress"
    node.work_item_id = item.id
    manifest = _manifest([node])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    worker_id = eng.store.resolve_agent_id("alice")
    runs = [AgentRunObservation(
        id="run-legacy-capacity",
        kind="direct",
        status="failed",
        agent_id=worker_id,
        created_at="2026-08-01T01:00:00Z",
        updated_at="2026-08-01T01:00:10Z",
        error="Selected model is at capacity. Please try a different model.",
    )]
    wake_calls = 0

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "worker"
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-legacy-capacity-retry",
            kind="direct",
            status="running",
            agent_id=worker_id,
            created_at="2026-08-01T01:01:00Z",
        ))

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)
    stale = WorkItemControlProjection(copy.deepcopy(item))

    assert loop.collect_results(
        eng.store,
        eng.runtime,
        manifest,
        path,
        observations={"a": stale},
    ) == {}
    assert loop.collect_results(
        eng.store,
        eng.runtime,
        manifest,
        path,
        observations={
            "a": WorkItemControlProjection(
                copy.deepcopy(eng.store.get_work_item(item.id)))
        },
    ) == {}

    recovered = eng.store.get_work_item(item.id)
    assert wake_calls == 1
    assert recovered.worker_handoff is not None
    assert recovered.worker_handoff.baseline_direct_run_ids == (
        "run-legacy-capacity",)
    assert recovered.worker_handoff.target_run_id == (
        "run-legacy-capacity-retry")
    assert recovered.bounces.worker == recovered.bounces.review == 0
    assert recovered.decision_required is None


def test_legacy_initial_worker_active_run_keeps_waiting_without_migration(
    tmp_path, monkeypatch,
):
    """部署期间仍在执行的旧 Worker 保持原状态，不迁移、不阻塞、不重派。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    node = _node("a", reviewer="bob", contract=_contract())
    item = eng.store.create_work_item(
        "ws", "a", "Task a", dag_key="a", worker="alice", reviewer="bob")
    item.created_at = "2026-08-01T00:59:00Z"
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    node.status = "in_progress"
    node.work_item_id = item.id
    manifest = _manifest([node])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    worker_id = eng.store.resolve_agent_id("alice")
    monkeypatch.setattr(
        type(eng.runtime),
        "capabilities",
        property(lambda _self: RuntimeCapabilities()),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
        AgentRunObservation(
            id="run-legacy-active",
            kind="direct",
            status="running",
            agent_id=worker_id,
            created_at="2026-08-01T01:00:00Z",
        )
    ])
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("active legacy Worker 不得再次 wake"),
    )

    assert loop.collect_results(
        eng.store, eng.runtime, manifest, path) == {}

    current = eng.store.get_work_item(item.id)
    assert current.status is WorkItemStatus.IN_PROGRESS
    assert current.worker_handoff is None
    assert current.decision_required is None
    assert manifest.nodes["a"].status == "in_progress"


def test_legacy_initial_worker_without_stable_run_identity_fails_closed(
    tmp_path, monkeypatch,
):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    node = _node("a", contract=_contract())
    item = eng.store.create_work_item(
        "ws", "a", "Task a", dag_key="a", worker="alice")
    item.created_at = "2026-08-01T00:59:00Z"
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    node.status = "in_progress"
    node.work_item_id = item.id
    manifest = _manifest([node])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    monkeypatch.setattr(
        type(eng.runtime),
        "capabilities",
        property(lambda _self: RuntimeCapabilities()),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
        AgentRunObservation(
            id="run-legacy-capacity", kind="direct", status="failed",
            agent_id=eng.store.resolve_agent_id("alice"),
            created_at="2026-08-01T01:00:00Z",
            updated_at="2026-08-01T01:00:10Z",
            error="Selected model is at capacity. Please try a different model.",
        )
    ])

    failures = loop.collect_results(
        eng.store, eng.runtime, manifest, path)

    current = eng.store.get_work_item(item.id)
    assert "a" in failures
    assert current.worker_handoff is None
    assert current.status is WorkItemStatus.BLOCKED
    assert current.decision_required["reason_code"] == (
        "legacy-worker-handoff-migration-unproven")


def test_transient_worker_rerun_response_unknown_observes_created_run(
    tmp_path, monkeypatch,
):
    from omac.errors import PlatformError

    eng, manifest, path, item, agent_id = _transient_worker_handoff_fixture(
        tmp_path)
    runs = [AgentRunObservation(
        id="run-capacity-1", kind="direct", status="failed",
        agent_id=agent_id, error="Our servers are currently overloaded")]
    wake_calls = 0

    def wake(_item_id, _agent, _role):
        nonlocal wake_calls
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-capacity-retry", kind="direct", status="queued",
            agent_id=agent_id))
        raise PlatformError("rerun response unavailable")

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(
        eng.store, eng.runtime, manifest, path) == {}
    assert wake_calls == 1
    assert eng.store.get_work_item(
        item.id).worker_handoff.target_run_id == "run-capacity-retry"


def test_transient_worker_retry_is_restart_safe_and_does_not_duplicate_active_run(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, agent_id = _transient_worker_handoff_fixture(
        tmp_path)
    runs = [AgentRunObservation(
        id="run-capacity-1", kind="direct", status="failed",
        agent_id=agent_id, error="runtime status: HTTP 503 Service Unavailable")]
    wake_calls = 0

    def wake(_item_id, _agent, _role):
        nonlocal wake_calls
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-capacity-retry", kind="direct", status="running",
            agent_id=agent_id))

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    assert wake_calls == 1
    assert eng.store.get_work_item(item.id).bounces.worker == 0


def test_consecutive_transient_worker_failures_stop_at_infrastructure_limit(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, agent_id = _transient_worker_handoff_fixture(
        tmp_path)
    runs = [
        AgentRunObservation(
            id="run-capacity-1", kind="direct", status="failed",
            agent_id=agent_id, created_at="2026-07-31T10:00:00Z",
            error="Our servers are currently overloaded"),
        AgentRunObservation(
            id="run-capacity-2", kind="direct", status="failed",
            agent_id=agent_id, created_at="2026-07-31T10:01:00Z",
            error="provider error: HTTP 429 Too Many Requests"),
    ]
    current = eng.store.get_work_item(item.id)
    eng.store.update_work_item_metadata(
        item.id,
        worker_handoff=WorkerHandoffIntent(
            **{
                **current.worker_handoff.as_dict(),
                "baseline_direct_run_ids": (),
                "target_run_id": "run-capacity-2",
            }
        ),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("exhausted infrastructure retry must not wake"),
    )

    result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    blocked = eng.store.get_work_item(item.id)

    assert result.state == "needs_decision"
    assert manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.phase is TaskPhase.AUTHORING
    assert blocked.bounces.worker == 0
    assert blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == (
        "transient-runtime-retry-exhausted")


def test_worker_transient_count_excludes_handoff_baseline_history(
    tmp_path, monkeypatch,
):
    """当前 handoff 第一次 transient 失败不能被历史同 agent 失败提前耗尽。"""
    eng, manifest, path, item, agent_id = _transient_worker_handoff_fixture(
        tmp_path)
    runs = [
        AgentRunObservation(
            id="run-capacity-history", kind="direct", status="failed",
            agent_id=agent_id, created_at="2026-08-01T00:59:00Z",
            error="Our servers are currently overloaded"),
        AgentRunObservation(
            id="run-capacity-current", kind="direct", status="failed",
            agent_id=agent_id, created_at="2026-08-01T01:00:00Z",
            error="provider error: HTTP 503 Service Unavailable"),
    ]
    current = eng.store.get_work_item(item.id)
    eng.store.update_work_item_metadata(
        item.id,
        worker_handoff=WorkerHandoffIntent(
            **{
                **current.worker_handoff.as_dict(),
                "baseline_direct_run_ids": ("run-capacity-history",),
                "target_run_id": "run-capacity-current",
            }
        ),
    )
    wake_calls = 0

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "worker"
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-capacity-current-retry",
            kind="direct",
            status="running",
            agent_id=agent_id,
            created_at="2026-08-01T01:01:00Z",
        ))

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}

    recovered = eng.store.get_work_item(item.id)
    assert wake_calls == 1
    assert recovered.status is WorkItemStatus.IN_PROGRESS
    assert recovered.worker_handoff.baseline_direct_run_ids == (
        "run-capacity-current", "run-capacity-history")
    assert recovered.worker_handoff.target_run_id == (
        "run-capacity-current-retry")
    assert recovered.decision_required is None


def _reviewer_runtime_failure_fixture(tmp_path):
    import hashlib

    from omac.engines import mock as mock_engine

    eng = _engine(MOCK_AUTO_COMPLETE="false")
    contract = _contract()
    manifest = _manifest([_node("a", reviewer="bob", contract=contract)])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)
    eng.store.set_node_contract(item.id, manifest.nodes["a"].contract)
    verification = eng.store._mock_verification(item.id)
    eng.store.update_work_item_metadata(
        item.id,
        artifacts={
            "pr_url": "https://mock.example/pr/1",
            "head_sha": hashlib.sha256(
                b"https://mock.example/pr/1").hexdigest(),
        },
        verification=verification,
        verification_source=yaml.safe_dump(verification),
    )
    mock_engine._finish_mock_run(item.id)
    eng.store.update_status(item.id, WorkItemStatus.DONE)
    tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    current = eng.store.get_work_item(item.id)
    reviewer_id = eng.store.resolve_agent_id("bob")
    return eng, manifest, path, current, reviewer_id


def _provisional_reviewer_decision_fixture(
    tmp_path,
    *,
    trigger_kind="issue_assignment",
    run_status="running",
):
    eng, manifest, path, current, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    cutoff = "2026-08-03T00:00:00Z"
    identity = replace(
        current.delivery_identity,
        verification_created_at=cutoff,
    )
    subject = loop._review_subject_for_current_delivery(
        manifest, "a", replace(current, delivery_identity=identity))
    baseline = replace(
        current.reviewer_run_baseline,
        subject_digest=subject,
        target_reviewer="bob",
        target_agent_id=reviewer_id,
        cutoff_created_at=cutoff,
        target_run_id=None,
    )
    decision = {
        "schema": "omac.decision-required/v1",
        "reason_code": "reviewer-run-baseline-unavailable",
        "kind": "develop",
        "phase": "review",
        "gate": "reviewer",
        "resume_issue_id": current.id,
        "node_id": "a",
        "failure_class": "unproven-reviewer-run-causality",
        "next_action": f"omac node retry {path} a --stage review",
    }
    eng.store.update_work_item_metadata(
        current.id,
        review_subject_digest=subject,
        reviewer_run_baseline=baseline,
        delivery_identity=identity,
        decision_required=decision,
    )
    eng.store.update_status(current.id, WorkItemStatus.BLOCKED)
    manifest.nodes["a"].status = "blocked"
    save_manifest(manifest, path)

    runs = list(eng.runtime.list_runs(current.id))
    reviewer_run_index = next(
        index for index, run in enumerate(runs)
        if run.agent_id == reviewer_id and run.id not in baseline.baseline_direct_run_ids
    )
    runs[reviewer_run_index] = replace(
        runs[reviewer_run_index],
        status=run_status,
        created_at="2026-08-03T00:00:01Z",
        trigger_kind=trigger_kind,
    )
    return (
        eng,
        manifest,
        path,
        eng.store.get_work_item(current.id),
        reviewer_id,
        runs,
        runs[reviewer_run_index].id,
    )


def _multica_deferred_reviewer_projection(item):
    from omac.engines.multica import MulticaStore

    multica = MulticaStore(EngineConfig(
        engine_type="multica", workspace_id="ws"))
    projection = multica._issue_to_control_projection({
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "status": "blocked",
        "metadata": {
            "dag_key": item.dag_key,
            "kind": "develop",
            "phase": "review",
            "worker": item.worker,
            "reviewer": item.reviewer,
            "artifacts": item.artifacts,
            "verification_ref": item.verification_ref,
            "review_subject_digest": item.review_subject_digest,
            "reviewer_run_baseline": item.reviewer_run_baseline.as_dict(),
            "delivery_identity": item.delivery_identity.as_dict(),
            "decision_required": item.decision_required,
            "contract_ref": item.contract_ref,
        },
    }, "ws")
    assert projection.deferred_payloads == frozenset({
        WorkItemPayload.VERIFICATION,
        WorkItemPayload.CONTRACT,
    })
    return projection


def _delayed_reviewer_verdict_fixture(tmp_path, verdict):
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    runs = list(eng.runtime.list_runs(item.id))
    target_run_id = runs[-1].id
    report = _review_report(item, verdict)
    eng.store.update_work_item_metadata(
        item.id,
        review_verdict=verdict,
        review_report=report,
        review_report_source=yaml.safe_dump(report),
        reviewer_run_baseline=replace(
            item.reviewer_run_baseline,
            target_run_id=target_run_id,
        ),
        decision_required={
            "schema": "omac.decision-required/v1",
            "reason_code": "reviewer-run-baseline-unavailable",
            "kind": "develop",
            "phase": "review",
            "gate": "reviewer",
            "resume_issue_id": item.id,
            "node_id": "a",
            "failure_class": "unproven-reviewer-run-causality",
            "next_action": f"omac node retry {path} a --stage review",
        },
    )
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    manifest.nodes["a"].status = "in_review"
    save_manifest(manifest, path)
    return eng, manifest, path, item.id, target_run_id, report


@pytest.mark.parametrize("verdict", ["pass", "reject"])
def test_runner_clears_delayed_reviewer_decision_before_consuming_verdict(
    tmp_path, monkeypatch, verdict,
):
    """decision clear 响应未知后，restart 继续消费同一 pass/reject。"""
    from omac.errors import PlatformError

    eng, manifest, path, item_id, target_run_id, report = (
        _delayed_reviewer_verdict_fixture(tmp_path, verdict))
    runs_before = list(eng.runtime.list_runs(item_id))
    assignments_before = len(eng.store.assign_log)
    original_update = eng.store.update_work_item_metadata
    original_assign = eng.store.assign_work_item
    original_wake = eng.runtime.wake
    failed = False

    def update(target_item_id, **metadata):
        nonlocal failed
        result = original_update(target_item_id, **metadata)
        if metadata.get("decision_required") == {} and not failed:
            failed = True
            raise PlatformError("decision clear response unknown")
        return result

    monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
    monkeypatch.setattr(
        eng.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "unknown decision clear must stop before assignment"),
    )
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "unknown decision clear must stop before wake"),
    )

    with pytest.raises(PlatformError, match="decision clear response unknown"):
        loop.collect_results(eng.store, eng.runtime, manifest, path)

    interrupted = eng.store.get_work_item(item_id)
    assert not interrupted.decision_required
    assert interrupted.review_verdict == verdict
    assert interrupted.review_report == report
    assert interrupted.reviewer_run_baseline.target_run_id == target_run_id
    assert eng.runtime.list_runs(item_id) == runs_before
    assert len(eng.store.assign_log) == assignments_before

    monkeypatch.setattr(eng.store, "update_work_item_metadata", original_update)
    monkeypatch.setattr(eng.store, "assign_work_item", original_assign)
    monkeypatch.setattr(eng.runtime, "wake", original_wake)

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    recovered = eng.store.get_work_item(item_id)
    assert not recovered.decision_required
    if verdict == "reject":
        assert manifest.nodes["a"].status == "in_progress"
        assert recovered.bounces.review == 1
    else:
        assert manifest.nodes["a"].status in {"merging", "done"}
    reviewer_assignments = [
        entry for entry in eng.store.assign_log[assignments_before:]
        if entry[2] == "reviewer"
    ]
    assert reviewer_assignments == []


@pytest.mark.parametrize(
    "invalid_case",
    [
        "stale-subject",
        "wrong-gate",
        "missing-failure-class",
        "operator-decision-extra",
    ],
)
def test_runner_preserves_invalid_delayed_reviewer_decision(
    tmp_path, monkeypatch, invalid_case,
):
    """Runner 不得清除、消费或重写非 canonical 专用 decision。"""
    eng, manifest, path, item_id, _target_run_id, report = (
        _delayed_reviewer_verdict_fixture(tmp_path, "pass"))
    current = eng.store.get_work_item(item_id)
    decision = dict(current.decision_required)
    metadata = {}
    if invalid_case == "stale-subject":
        metadata["reviewer_run_baseline"] = replace(
            current.reviewer_run_baseline,
            subject_digest="stale-subject",
        )
    elif invalid_case == "wrong-gate":
        decision["gate"] = "human-confirmation"
    elif invalid_case == "missing-failure-class":
        decision.pop("failure_class")
    else:
        decision["operator_decision"] = True
    metadata["decision_required"] = decision
    eng.store.update_work_item_metadata(item_id, **metadata)
    eng.store.update_status(item_id, WorkItemStatus.BLOCKED)

    assignments_before = len(eng.store.assign_log)
    decision_writes = []
    original_update = eng.store.update_work_item_metadata

    def update(target_item_id, **updated):
        if "decision_required" in updated:
            decision_writes.append(updated["decision_required"])
        return original_update(target_item_id, **updated)

    monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
    monkeypatch.setattr(
        eng.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid marker must fail before assignment"),
    )
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid marker must fail before wake"),
    )

    failures = loop.collect_results(eng.store, eng.runtime, manifest, path)

    blocked = eng.store.get_work_item(item_id)
    assert "a" in failures
    assert manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision
    assert blocked.review_verdict == "pass"
    assert blocked.review_report == report
    assert len(eng.store.assign_log) == assignments_before
    assert decision_writes == []


@pytest.mark.parametrize(
    "case",
    [
        "missing-reason-code",
        "tampered-reason-code",
        "non-object",
        "unknown-existing-decision",
    ],
)
def test_runner_preserves_noncanonical_existing_reviewer_decision(
    tmp_path, monkeypatch, case,
):
    """Runner 只能消费精确 canonical 的 reviewer 恢复 decision。"""
    eng, manifest, path, item_id, _target_run_id, report = (
        _delayed_reviewer_verdict_fixture(tmp_path, "reject"))
    current = eng.store.get_work_item(item_id)
    decision = deepcopy(current.decision_required)
    if case == "missing-reason-code":
        decision.pop("reason_code")
    elif case == "tampered-reason-code":
        decision["reason_code"] = "reviewer-run-baseline-unavailable-tampered"
    elif case == "non-object":
        decision = ["reviewer-run-baseline-unavailable"]
    else:
        decision = {
            "schema": "omac.decision-required/v1",
            "reason_code": "guard-budget-exhausted",
        }
    eng.store.update_work_item_metadata(
        item_id, decision_required=decision)
    eng.store.update_status(item_id, WorkItemStatus.BLOCKED)
    assignments = len(eng.store.assign_log)
    decision_writes = []
    original_update = eng.store.update_work_item_metadata

    def update(target_item_id, **updated):
        if "decision_required" in updated:
            decision_writes.append(updated["decision_required"])
        return original_update(target_item_id, **updated)

    monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
    monkeypatch.setattr(
        eng.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "noncanonical decision must fail before assignment"),
    )
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "noncanonical decision must fail before wake"),
    )

    failures = loop.collect_results(eng.store, eng.runtime, manifest, path)

    blocked = eng.store.get_work_item(item_id)
    assert "a" in failures
    assert manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision
    assert blocked.review_verdict == "reject"
    assert blocked.review_report == report
    assert len(eng.store.assign_log) == assignments
    assert decision_writes == []


def test_runner_classifies_decision_before_reviewer_no_submit_recovery(
    tmp_path, monkeypatch,
):
    """A formal no-submit Run cannot bypass an existing unknown decision."""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    current = eng.store.get_work_item(item.id)
    baseline = current.reviewer_run_baseline
    decision = {
        "schema": "omac.decision-required/v1",
        "reason_code": "guard-budget-exhausted",
    }
    eng.store.update_work_item_metadata(
        item.id, decision_required=decision)
    eng.store.update_status(item.id, WorkItemStatus.BLOCKED)

    runs = [
        replace(
            run,
            status="completed",
            updated_at="2026-08-01T01:01:30Z",
            trigger_kind="issue_assignment",
        ) if (
            run.agent_id == reviewer_id
            and run.id not in baseline.baseline_direct_run_ids
        ) else run
        for run in eng.runtime.list_runs(item.id)
    ]
    assign_calls = []
    wake_calls = []

    def assign(*args, **kwargs):
        assign_calls.append((args, kwargs))

    def wake(_item_id, _agent, _role):
        wake_calls.append((_item_id, _agent, _role))
        runs.append(AgentRunObservation(
            id="run-reviewer-retry",
            kind="direct",
            status="running",
            agent_id=reviewer_id,
            created_at="2026-08-01T01:02:00Z",
            trigger_kind="rerun",
        ))

    monkeypatch.setattr(
        loop, "_utcnow",
        lambda: datetime(2026, 8, 1, 1, 3, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.store, "assign_work_item", assign)
    monkeypatch.setattr(eng.runtime, "wake", wake)

    failures = loop.collect_results(eng.store, eng.runtime, manifest, path)

    blocked = eng.store.get_work_item(item.id)
    assert "a" in failures
    assert manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision
    assert blocked.reviewer_run_baseline == baseline
    assert assign_calls == []
    assert wake_calls == []


def test_reviewer_capacity_failure_reruns_without_review_bounce(
    tmp_path, monkeypatch,
):
    eng, manifest, path, current, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    runs = [AgentRunObservation(
        id="run-review-capacity", kind="direct", status="failed",
        agent_id=reviewer_id, created_at="2026-08-01T01:01:00Z",
        error="Selected model is at capacity. Please try a different model.",
    )]
    wake_calls = []

    def wake(_item_id, _agent, role):
        wake_calls.append(role)
        runs.append(AgentRunObservation(
            id="run-review-retry", kind="direct", status="running",
            agent_id=reviewer_id, created_at="2026-08-01T01:02:00Z"))

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    retried = eng.store.get_work_item(current.id)
    assert wake_calls == ["reviewer"]
    assert retried.phase is TaskPhase.REVIEW
    assert retried.bounces.review == current.bounces.review == 0
    assert retried.verification == current.verification
    assert manifest.nodes["a"].status == "in_review"


def test_reviewer_completed_without_verdict_restarts_once_from_runtime_facts(
    tmp_path, monkeypatch,
):
    """Multica 保持 in_progress/review 时也必须识别 Reviewer 无提交终态。"""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    worker_id = eng.store.resolve_agent_id("alice")
    runs = [
        AgentRunObservation(
            id="run-worker-old",
            kind="direct",
            status="completed",
            agent_id=worker_id,
            created_at="2026-08-01T01:00:00Z",
        ),
        AgentRunObservation(
            id="run-reviewer-no-submit",
            kind="direct",
            status="completed",
            agent_id=reviewer_id,
            created_at="2026-08-01T01:01:00Z",
            updated_at="2026-08-01T01:02:00Z",
        ),
    ]
    wake_calls = 0

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "reviewer"
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-reviewer-retry",
            kind="direct",
            status="running",
            agent_id=reviewer_id,
            created_at="2026-08-01T01:02:00Z",
        ))

    monkeypatch.setattr(
        loop, "_utcnow",
        lambda: datetime(2026, 8, 1, 1, 3, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    persisted = load_manifest(path)
    assert loop.collect_results(
        eng.store, eng.runtime, persisted, path) == {}

    recovered = eng.store.get_work_item(item.id)
    assert wake_calls == 1
    assert recovered.phase is TaskPhase.REVIEW
    assert recovered.status is WorkItemStatus.IN_REVIEW
    assert recovered.review_verdict is None
    assert recovered.bounces.review == item.bounces.review == 0
    assert persisted.nodes["a"].status == "in_review"


def test_reviewer_completed_waits_for_late_verdict_within_grace(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    runs = [AgentRunObservation(
        id="run-reviewer-completed",
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2026-08-01T01:01:00Z",
        updated_at="2026-08-01T01:02:00Z",
    )]
    monkeypatch.setattr(
        loop, "_utcnow",
        lambda: datetime(2026, 8, 1, 1, 2, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("grace 内不得派发任何恢复 Run"),
    )

    assert loop.collect_results(
        eng.store, eng.runtime, manifest, path) == {}
    eng.store.update_work_item_metadata(item.id, review_verdict="pass")

    current = eng.store.get_work_item(item.id)
    assert current.review_verdict == "pass"
    assert current.bounces.review == item.bounces.review == 0


def test_reviewer_history_before_current_subject_is_not_no_submit(
    tmp_path, monkeypatch,
):
    """当前 subject 的 Run 尚不可见时，不得消费同 reviewer 的历史终态。"""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    baseline = item.reviewer_run_baseline
    assert baseline is not None
    assert baseline.baseline_direct_run_ids
    visible_runs = [AgentRunObservation(
        id=baseline.baseline_direct_run_ids[-1],
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2026-08-01T00:00:00Z",
        updated_at="2026-08-01T00:01:00Z",
    )]
    wake_calls = 0

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "reviewer"
        wake_calls += 1
        # 模拟 Multica 最终一致性：本次 subject 的新 Run 尚未出现在列表中。

    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(visible_runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(
        eng.store, eng.runtime, manifest, path) == {}
    current = eng.store.get_work_item(item.id)
    assert wake_calls == 0
    assert current.status is WorkItemStatus.IN_REVIEW
    assert current.phase is TaskPhase.REVIEW
    assert current.review_verdict is None
    assert current.decision_required is None
    assert manifest.nodes["a"].status == "in_review"


def test_late_visible_reviewer_history_before_delivery_cutoff_is_ignored(
    tmp_path, monkeypatch,
):
    """ID snapshot 漏掉的历史 Run 仍由稳定 delivery cutoff 排除。"""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    cutoff = item.delivery_identity.verification_created_at
    baseline = ReviewerRunBaseline(
        schema="omac.reviewer-run-baseline/v1",
        subject_digest=item.review_subject_digest,
        target_reviewer="bob",
        target_agent_id=reviewer_id,
        cutoff_created_at=cutoff,
        generation="review-dispatch-1",
        attempt=1,
        baseline_direct_run_ids=item.reviewer_run_baseline.baseline_direct_run_ids,
    )
    eng.store.update_work_item_metadata(
        item.id, reviewer_run_baseline=baseline)
    late_visible_history = AgentRunObservation(
        id="run-reviewer-late-history",
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2025-12-31T23:59:59Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        eng.runtime, "list_runs", lambda _item_id: [late_visible_history])
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("delivery cutoff 前的历史 Run 不得触发 retry"),
    )

    assert loop.collect_results(
        eng.store, eng.runtime, manifest, path) == {}

    current = eng.store.get_work_item(item.id)
    assert current.status is WorkItemStatus.IN_REVIEW
    assert current.reviewer_run_baseline.target_run_id is None
    assert current.decision_required is None
    assert manifest.nodes["a"].status == "in_review"


def test_reviewer_no_submit_retry_blocks_when_run_stays_invisible(
    tmp_path, monkeypatch,
):
    """retry wake 成功但 bounded observation 不见 Run 时必须 fail closed。"""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    cutoff = item.delivery_identity.verification_created_at
    baseline = ReviewerRunBaseline(
        schema="omac.reviewer-run-baseline/v1",
        subject_digest=item.review_subject_digest,
        target_reviewer="bob",
        target_agent_id=reviewer_id,
        cutoff_created_at=cutoff,
        generation="review-dispatch-1",
        attempt=1,
        baseline_direct_run_ids=item.reviewer_run_baseline.baseline_direct_run_ids,
    )
    eng.store.update_work_item_metadata(
        item.id, reviewer_run_baseline=baseline)
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    initial = AgentRunObservation(
        id="run-reviewer-no-submit",
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2026-08-01T01:01:00Z",
        updated_at="2026-08-01T01:01:10Z",
    )
    wake_calls = 0

    def list_runs(_item_id):
        return [initial]

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "reviewer"
        wake_calls += 1

    monkeypatch.setattr(
        loop, "_utcnow",
        lambda: datetime(2026, 8, 1, 1, 2, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(eng.runtime, "list_runs", list_runs)
    monkeypatch.setattr(eng.runtime, "wake", wake)

    failures = loop.collect_results(
        eng.store, eng.runtime, manifest, path)
    restarted = load_manifest(path)
    loop.collect_results(
        eng.store, eng.runtime, restarted, path)

    recovered = eng.store.get_work_item(item.id)
    assert "a" in failures
    assert wake_calls == 1
    assert recovered.reviewer_run_baseline.attempt == 2
    assert recovered.reviewer_run_baseline.target_run_id is None
    assert recovered.status is WorkItemStatus.BLOCKED
    assert recovered.decision_required["reason_code"] == (
        "reviewer-run-dispatch-unresolved")
    assert restarted.nodes["a"].status == "blocked"


def test_reviewer_no_submit_retry_crash_after_intent_fails_closed_on_restart(
    tmp_path, monkeypatch,
):
    """retry intent 落盘后、wake 前崩溃，重启不得 wake 或永久等待。"""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    cutoff = item.delivery_identity.verification_created_at
    baseline = ReviewerRunBaseline(
        schema="omac.reviewer-run-baseline/v1",
        subject_digest=item.review_subject_digest,
        target_reviewer="bob",
        target_agent_id=reviewer_id,
        cutoff_created_at=cutoff,
        generation="review-dispatch-1",
        attempt=1,
        baseline_direct_run_ids=item.reviewer_run_baseline.baseline_direct_run_ids,
    )
    eng.store.update_work_item_metadata(
        item.id, reviewer_run_baseline=baseline)
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    initial = AgentRunObservation(
        id="run-reviewer-no-submit",
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2026-08-01T01:01:00Z",
        updated_at="2026-08-01T01:01:10Z",
    )
    monkeypatch.setattr(
        loop, "_utcnow",
        lambda: datetime(2026, 8, 1, 1, 2, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        eng.runtime, "list_runs", lambda _item_id: [initial])
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("crash checkpoint 前不得到达 wake"),
    )
    original_update = eng.store.update_work_item_metadata
    crashed = False

    def crash_after_retry_intent(item_id, **metadata):
        nonlocal crashed
        result = original_update(item_id, **metadata)
        retry = metadata.get("reviewer_run_baseline")
        if getattr(retry, "attempt", 0) == 2 and not crashed:
            crashed = True
            raise RuntimeError("crash after reviewer retry intent")
        return result

    monkeypatch.setattr(
        eng.store, "update_work_item_metadata", crash_after_retry_intent)
    with pytest.raises(RuntimeError, match="reviewer retry intent"):
        loop.collect_results(eng.store, eng.runtime, manifest, path)

    interrupted = eng.store.get_work_item(item.id)
    assert interrupted.reviewer_run_baseline.attempt == 2
    assert interrupted.reviewer_run_baseline.target_run_id is None

    monkeypatch.setattr(
        eng.store, "update_work_item_metadata", original_update)
    wake_calls = 0

    def wake_after_restart(*_args):
        nonlocal wake_calls
        wake_calls += 1

    monkeypatch.setattr(eng.runtime, "wake", wake_after_restart)
    restarted = load_manifest(path)
    failures = loop.collect_results(
        eng.store, eng.runtime, restarted, path)

    recovered = eng.store.get_work_item(item.id)
    assert "a" in failures
    assert wake_calls == 0
    assert recovered.status is WorkItemStatus.BLOCKED
    assert recovered.decision_required["reason_code"] == (
        "reviewer-run-dispatch-unresolved")


def test_legacy_review_projection_uses_delivery_time_not_issue_update_time(
    tmp_path, monkeypatch,
):
    """AITEAM-826:issue 晚更新不能吞掉当前 reviewer Run。"""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    eng.store.update_work_item_metadata(item.id, reviewer_run_baseline={})
    current = eng.store.get_work_item(item.id)
    current.updated_at = "2026-07-31T16:09:39Z"
    assert current.delivery_identity is not None
    current.delivery_identity = replace(
        current.delivery_identity,
        verification_created_at="2026-07-31T16:07:00Z",
    )
    current.review_subject_digest = review_subject_digest(
        current, max(1, current.bounces.review + 1))
    runs = [
        AgentRunObservation(
            id="run-reviewer-history",
            kind="direct",
            status="completed",
            agent_id=reviewer_id,
            created_at="2026-07-31T16:06:30Z",
            updated_at="2026-07-31T16:06:59Z",
        ),
        AgentRunObservation(
            id="run-reviewer-current-subject",
            kind="direct",
            status="completed",
            agent_id=reviewer_id,
            created_at="2026-07-31T16:08:23Z",
            updated_at="2026-07-31T16:08:50Z",
        ),
    ]
    wake_calls = 0

    def wake(_item_id, _agent, role):
        nonlocal wake_calls
        assert role == "reviewer"
        wake_calls += 1
        runs.append(AgentRunObservation(
            id="run-reviewer-current-subject-retry",
            kind="direct",
            status="running",
            agent_id=reviewer_id,
            created_at="2026-07-31T16:10:01Z",
        ))

    monkeypatch.setattr(
        loop, "_utcnow",
        lambda: datetime(2026, 7, 31, 16, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(eng.runtime, "wake", wake)

    assert loop.collect_results(
        eng.store, eng.runtime, manifest, path) == {}

    recovered = eng.store.get_work_item(item.id)
    assert wake_calls == 1
    assert recovered.reviewer_run_baseline is not None
    assert recovered.reviewer_run_baseline.baseline_direct_run_ids == (
        "run-reviewer-current-subject", "run-reviewer-history")
    assert recovered.reviewer_run_baseline.attempt == 2
    assert recovered.reviewer_run_baseline.target_run_id == (
        "run-reviewer-current-subject-retry")
    assert recovered.bounces.review == 0
    assert manifest.nodes["a"].status == "in_review"


def test_legacy_review_projection_without_delivery_time_fails_closed(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    eng.store.update_work_item_metadata(
        item.id,
        reviewer_run_baseline={},
        delivery_identity={},
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = review_subject_digest(
        current, max(1, current.bounces.review + 1))
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [
        AgentRunObservation(
            id="run-reviewer-ambiguous",
            kind="direct",
            status="completed",
            agent_id=reviewer_id,
            created_at="2026-07-31T16:08:23Z",
            updated_at="2026-07-31T16:08:50Z",
        )
    ])
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("缺少 delivery cutoff 时不得猜测重派"),
    )

    failures = loop.collect_results(
        eng.store, eng.runtime, manifest, path)
    blocked = eng.store.get_work_item(item.id)

    assert "a" in failures
    assert manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required["reason_code"] == (
        "reviewer-run-baseline-unavailable")
    assert blocked.bounces.review == 0


@pytest.mark.parametrize(
    "identity_created_at, baseline_created_at, incomplete_identity",
    [
        pytest.param(
            "2026-08-01T01:00:00", None, False,
            id="legacy-naive-identity-cutoff",
        ),
        pytest.param(
            "not-a-time", None, False,
            id="legacy-malformed-identity-cutoff",
        ),
        pytest.param(
            "2026-08-01T01:00:00Z", "2026-08-01T01:00:00", False,
            id="persisted-naive-cutoff",
        ),
        pytest.param(
            "2026-08-01T01:00:00Z", "not-a-time", False,
            id="persisted-malformed-cutoff",
        ),
        pytest.param(
            "2026-08-01T01:00:00Z", "2026-08-01T01:00:01Z", False,
            id="persisted-mismatched-cutoff",
        ),
        pytest.param(
            "2026-08-01T01:00:00Z", "2026-08-01T01:00:00Z", True,
            id="incomplete-delivery-identity",
        ),
    ],
)
def test_invalid_persisted_reviewer_cutoff_fails_closed_across_collects(
    tmp_path, monkeypatch, identity_created_at, baseline_created_at,
    incomplete_identity,
):
    eng, manifest, path, item, _reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    identity = replace(
        item.delivery_identity,
        verification_created_at=identity_created_at,
        run_id=None if incomplete_identity else item.delivery_identity.run_id,
    )
    item.delivery_identity = identity
    item.review_subject_digest = review_subject_digest(
        item, max(1, item.bounces.review + 1))
    item.reviewer_run_baseline = replace(
        item.reviewer_run_baseline,
        subject_digest=item.review_subject_digest,
        cutoff_created_at=baseline_created_at,
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("invalid reviewer cutoff must not wake"),
    )

    for attempt in range(3):
        failures = loop.collect_results(
            eng.store, eng.runtime, manifest, path)
        blocked = eng.store.get_work_item(item.id)

        if attempt == 0:
            assert "a" in failures
        assert blocked.status is WorkItemStatus.BLOCKED
        assert blocked.decision_required["reason_code"] == (
            "reviewer-run-baseline-unavailable")
        assert manifest.nodes["a"].status == "blocked"
        manifest = load_manifest(path)


def test_persisted_reviewer_cutoff_accepts_equivalent_instant(tmp_path, monkeypatch):
    eng, manifest, path, item, _reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    item.delivery_identity = replace(
        item.delivery_identity,
        verification_created_at="2026-08-01T01:00:00Z",
    )
    item.review_subject_digest = review_subject_digest(
        item, max(1, item.bounces.review + 1))
    item.reviewer_run_baseline = replace(
        item.reviewer_run_baseline,
        subject_digest=item.review_subject_digest,
        cutoff_created_at="2026-08-01T09:00:00+08:00",
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("equivalent cutoff must not wake"),
    )

    assert loop.collect_results(
        eng.store, eng.runtime, manifest, path) == {}
    current = eng.store.get_work_item(item.id)
    assert current.status is WorkItemStatus.IN_REVIEW
    assert current.decision_required is None
    assert manifest.nodes["a"].status == "in_review"


def test_reviewer_dispatch_rejects_naive_delivery_cutoff(tmp_path):
    """Sealed verification cutoff 必须带时区，不能把 TypeError 留到观察阶段。"""
    from omac.errors import PlatformError

    eng, manifest, _path, item, _reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    current = eng.store.get_work_item(item.id)
    naive = "2026-01-01T00:00:01"
    current.verification_ref["created_at"] = naive
    current.delivery_identity = replace(
        current.delivery_identity, verification_created_at=naive)
    current.reviewer_run_baseline = None

    with pytest.raises(PlatformError, match="verification time"):
        loop._dispatch_reviewer_for_current_subject(
            eng.store, eng.runtime, manifest, "a")


def test_reviewer_dispatch_refreshes_reused_issue_with_control_protocol(
    tmp_path, monkeypatch,
):
    eng, manifest, _path, item, _reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    eng.store.update_work_item_metadata(item.id, description="stale issue body")
    eng.store.clear_assignment(item.id)
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(eng.runtime, "wake", lambda *_args: None)

    loop._dispatch_reviewer_for_current_subject(
        eng.store, eng.runtime, manifest, "a")

    refreshed = eng.store.get_work_item(item.id)
    first_screen = refreshed.description.split("# ", 1)[0]
    assert "omac work show" in first_screen
    assert "multica issue comment" in first_screen
    assert "omac work submit" in first_screen
    assert (
        "Execution role: Independent reviewer" in refreshed.description
        or "执行角色: 独立评审者" in refreshed.description
    )


def test_initial_develop_reviewer_handoff_assigns_without_starting_run(
    tmp_path, monkeypatch,
):
    import hashlib

    eng = _engine(MOCK_AUTO_COMPLETE="false")
    manifest = _manifest([_node("a", reviewer="bob", contract=_contract())])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)
    eng.store.set_node_contract(item.id, manifest.nodes["a"].contract)
    verification = eng.store._mock_verification(item.id)
    eng.store.update_work_item_metadata(
        item.id,
        artifacts={
            "pr_url": "https://mock.example/pr/1",
            "head_sha": hashlib.sha256(
                b"https://mock.example/pr/1").hexdigest(),
        },
        verification=verification,
        verification_source=yaml.safe_dump(verification),
    )
    from omac.engines import mock as mock_engine
    mock_engine._finish_mock_run(item.id)
    eng.store.update_status(item.id, WorkItemStatus.DONE)
    original_assign = eng.store.assign_work_item
    reviewer_calls = []

    def assign(item_id, assignee, role, **kwargs):
        if role == "reviewer":
            reviewer_calls.append(kwargs)
        return original_assign(item_id, assignee, role, **kwargs)

    monkeypatch.setattr(eng.store, "assign_work_item", assign)

    tick(eng.store, eng.runtime, manifest, path, max_parallel=1)

    assert reviewer_calls == [{"start_run": False}]


def test_mock_silent_assignment_defers_run_until_wake():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = eng.store.create_work_item(
        workspace_id="ws",
        title="review",
        description="review",
        dag_key="review-a",
        worker="alice",
        reviewer="bob",
    )

    eng.store.assign_work_item(
        item.id, "bob", "reviewer", start_run=False)
    assert eng.runtime.list_runs(item.id) == []

    eng.runtime.wake(item.id, "bob", "reviewer")
    first = eng.runtime.list_runs(item.id)
    assert len(first) == 1
    assert first[0].active
    assert first[0].agent_id == eng.store.resolve_agent_id("bob")

    eng.runtime.wake(item.id, "bob", "reviewer")
    assert eng.runtime.list_runs(item.id) == first


def test_initial_reviewer_dispatch_crash_after_assignment_fails_closed(
    tmp_path, monkeypatch,
):
    from omac.engines import mock as mock_engine

    eng, manifest, _path, item, _reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    mock_engine._finish_mock_run(item.id)
    eng.store.clear_assignment(item.id)
    eng.store.update_work_item_metadata(item.id, reviewer_run_baseline={})

    def crash_before_wake(*_args):
        raise RuntimeError("crash after suppressed reviewer assignment")

    monkeypatch.setattr(eng.runtime, "wake", crash_before_wake)
    with pytest.raises(RuntimeError, match="suppressed reviewer assignment"):
        loop._dispatch_reviewer_for_current_subject(
            eng.store, eng.runtime, manifest, "a")

    interrupted = eng.store.get_work_item(item.id)
    assert interrupted.status is WorkItemStatus.IN_REVIEW
    assert interrupted.phase is TaskPhase.REVIEW
    assert interrupted.reviewer == "bob"
    assert interrupted.reviewer_run_baseline.target_run_id is None
    assert not eng.runtime.is_active(item.id)

    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("restart must not rerun an ambiguous dispatch"),
    )
    with pytest.raises(
        loop._ReviewerDispatchUnresolved,
        match="no uniquely observable target Run",
    ):
        loop._dispatch_reviewer_for_current_subject(
            eng.store, eng.runtime, manifest, "a")


@pytest.mark.parametrize(
    "trigger_kind, accepted",
    [
        ("issue_assignment", True),
        ("rerun", True),
        ("comment", False),
        ("manual", False),
        (None, False),
    ],
)
def test_initial_reviewer_dispatch_recovery_requires_formal_dispatch(
    tmp_path, monkeypatch, trigger_kind, accepted,
):
    from omac.engines import mock as mock_engine

    eng, manifest, _path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    mock_engine._finish_mock_run(item.id)
    eng.store.clear_assignment(item.id)
    eng.store.update_work_item_metadata(item.id, reviewer_run_baseline={})
    def crash_wake(*_args):
        raise RuntimeError("dispatch crash")

    monkeypatch.setattr(
        eng.runtime,
        "wake",
        crash_wake,
    )
    with pytest.raises(RuntimeError, match="dispatch crash"):
        loop._dispatch_reviewer_for_current_subject(
            eng.store, eng.runtime, manifest, "a")

    candidate = AgentRunObservation(
        id=f"run-{trigger_kind}",
        kind="direct",
        status="running",
        agent_id=reviewer_id,
        created_at="2026-01-01T00:00:02Z",
        trigger_kind=trigger_kind,
    )
    monkeypatch.setattr(
        eng.runtime, "list_runs", lambda _item_id: [candidate])
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("recovery must never rerun"),
    )

    if not accepted:
        with pytest.raises(
            loop._ReviewerDispatchUnresolved,
            match="not a formal assignment/rerun dispatch",
        ):
            loop._dispatch_reviewer_for_current_subject(
                eng.store, eng.runtime, manifest, "a")
        return

    assert loop._dispatch_reviewer_for_current_subject(
        eng.store, eng.runtime, manifest, "a") is False
    recovered = eng.store.get_work_item(item.id)
    assert recovered.reviewer_run_baseline.target_run_id == candidate.id


def test_develop_reviewer_retry_assigns_without_resuming_old_session(
    tmp_path, monkeypatch,
):
    eng, _manifest, _path, item, _reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    original_assign = eng.store.assign_work_item
    reviewer_calls = []

    def assign(item_id, assignee, role, **kwargs):
        if role == "reviewer":
            reviewer_calls.append(kwargs)
        return original_assign(item_id, assignee, role, **kwargs)

    monkeypatch.setattr(eng.store, "assign_work_item", assign)
    monkeypatch.setattr(eng.runtime, "wake", lambda *_args: None)

    assert loop._resume_reviewer_run(
        eng.store, eng.runtime, _manifest.nodes["a"])

    assert reviewer_calls == [{"start_run": False}]


def test_reviewer_completed_without_verdict_is_bounded_by_run_attempts(
    tmp_path, monkeypatch,
):
    """Reviewer 连续无结构化提交不能无限重派，也不消耗业务 bounce。"""
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    runs = [
        AgentRunObservation(
            id="run-reviewer-no-submit-1",
            kind="direct",
            status="completed",
            agent_id=reviewer_id,
            created_at="2026-08-01T01:01:00Z",
            updated_at="2026-08-01T01:01:30Z",
        ),
        AgentRunObservation(
            id="run-reviewer-no-submit-2",
            kind="direct",
            status="completed",
            agent_id=reviewer_id,
            created_at="2026-08-01T01:02:00Z",
            updated_at="2026-08-01T01:02:30Z",
        ),
    ]
    baseline = eng.store.get_work_item(item.id).reviewer_run_baseline
    eng.store.update_work_item_metadata(
        item.id,
        reviewer_run_baseline=replace(
            baseline,
            generation="review-attempt-2",
            attempt=2,
            baseline_direct_run_ids=("run-reviewer-no-submit-1",),
            target_run_id="run-reviewer-no-submit-2",
        ),
    )
    monkeypatch.setattr(
        loop, "_utcnow",
        lambda: datetime(2026, 8, 1, 1, 3, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("exhausted reviewer retry must not wake"),
    )

    result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    blocked = eng.store.get_work_item(item.id)

    assert result.state == "needs_decision"
    assert manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.phase is TaskPhase.REVIEW
    assert blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == (
        "reviewer-run-no-submit-retry-exhausted")


def test_nonretryable_worker_failure_blocks_without_business_bounce(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, agent_id = _transient_worker_handoff_fixture(
        tmp_path)
    runs = [AgentRunObservation(
        id="run-capacity-1", kind="direct", status="failed",
        agent_id=agent_id,
        error="request rejected by network security policy",
    )]
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("nonretryable failure must not wake"),
    )

    result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    blocked = eng.store.get_work_item(item.id)

    assert result.state == "needs_decision"
    assert manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.phase is TaskPhase.AUTHORING
    assert blocked.verification == item.verification
    assert blocked.verification_ref == item.verification_ref
    assert blocked.bounces.worker == 0
    assert blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == (
        "nonretryable-runtime-failure")


def test_worker_runtime_failure_next_action_recovers_authoring(
    tmp_path, monkeypatch, capsys,
):
    """Worker runtime decision 的可执行命令不得把节点切到 review。"""
    from shlex import split

    from omac.cli import exit_codes
    from omac.cli.main import main

    eng, manifest, path, item, agent_id = _transient_worker_handoff_fixture(
        tmp_path)
    runs = [AgentRunObservation(
        id="run-capacity-1", kind="direct", status="failed",
        agent_id=agent_id,
        error="request rejected by network security policy",
    )]
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("runtime decision must not wake"),
    )

    result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    blocked = eng.store.get_work_item(item.id)
    next_action = blocked.decision_required["next_action"]

    assert result.state == "needs_decision"
    assert blocked.phase is TaskPhase.AUTHORING
    assert blocked.worker_handoff is not None
    assert "--stage review" not in next_action

    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws")
    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *_args, **_kwargs: eng)

    command = split(next_action)
    assert command[0] == "omac"
    assert main(command[1:]) == exit_codes.OK
    capsys.readouterr()

    recovered = eng.store.get_work_item(item.id)
    assert recovered.phase is TaskPhase.AUTHORING
    assert recovered.status is WorkItemStatus.TODO
    assert recovered.worker_handoff == blocked.worker_handoff
    assert load_manifest(path).nodes["a"].status == "todo"


def test_nonretryable_reviewer_failure_blocks_without_review_bounce(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    runs = [AgentRunObservation(
        id="run-review-policy", kind="direct", status="failed",
        agent_id=reviewer_id, created_at="2026-08-01T01:01:00Z",
        error="HTTP 403 forbidden by provider safety policy",
    )]
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("nonretryable reviewer must not wake"),
    )

    result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    blocked = eng.store.get_work_item(item.id)

    assert result.state == "needs_decision"
    assert blocked.phase is TaskPhase.REVIEW
    assert blocked.verification == item.verification
    assert blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == (
        "nonretryable-runtime-failure")


def test_reviewer_runtime_failure_next_action_recovers_review(
    tmp_path, monkeypatch, capsys,
):
    """Reviewer runtime retry must stay in review and preserve its delivery."""
    from shlex import split

    from omac.cli import exit_codes
    from omac.cli.main import main

    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    runs = [AgentRunObservation(
        id="run-review-policy",
        kind="direct",
        status="failed",
        agent_id=reviewer_id,
        created_at="2026-08-01T01:01:00Z",
        error="HTTP 403 forbidden by provider safety policy",
    )]
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime,
        "wake",
        lambda *_args: pytest.fail("runtime decision must not wake"),
    )

    result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    blocked = eng.store.get_work_item(item.id)
    next_action = blocked.decision_required["next_action"]

    assert result.state == "needs_decision"
    assert blocked.phase is TaskPhase.REVIEW
    assert next_action.endswith(" --stage review")

    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws")
    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *_args, **_kwargs: eng)

    command = split(next_action)
    assert command[0] == "omac"
    assert main(command[1:]) == exit_codes.OK
    capsys.readouterr()

    recovered = eng.store.get_work_item(item.id)
    assert recovered.phase is TaskPhase.REVIEW
    assert recovered.status is WorkItemStatus.IN_REVIEW
    assert recovered.review_subject_digest is not None
    assert recovered.reviewer_run_baseline is None
    assert load_manifest(path).nodes["a"].status == "in_review"


def test_reviewer_transient_failures_stop_at_infrastructure_limit(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    runs = [
        AgentRunObservation(
            id="run-review-1", kind="direct", status="failed",
            agent_id=reviewer_id, created_at="2026-07-31T10:00:00Z",
            error="Our servers are currently overloaded"),
        AgentRunObservation(
            id="run-review-2", kind="direct", status="failed",
            agent_id=reviewer_id, created_at="2026-07-31T10:01:00Z",
            error="provider error: HTTP 503 Service Unavailable"),
    ]
    baseline = eng.store.get_work_item(item.id).reviewer_run_baseline
    eng.store.update_work_item_metadata(
        item.id,
        reviewer_run_baseline=replace(
            baseline,
            generation="review-attempt-2",
            attempt=2,
            baseline_direct_run_ids=("run-review-1",),
            target_run_id="run-review-2",
        ),
    )
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("exhausted reviewer retry must not wake"),
    )

    result = tick(eng.store, eng.runtime, manifest, path, max_parallel=1)
    blocked = eng.store.get_work_item(item.id)

    assert result.state == "needs_decision"
    assert blocked.phase is TaskPhase.REVIEW
    assert blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == (
        "transient-runtime-retry-exhausted")


def test_reviewer_ignores_comment_run_and_never_retries_other_agent_failure(
    tmp_path, monkeypatch,
):
    eng, manifest, path, item, reviewer_id = (
        _reviewer_runtime_failure_fixture(tmp_path))
    baseline = item.reviewer_run_baseline
    assert baseline is not None
    assert baseline.baseline_direct_run_ids
    runs = [
        AgentRunObservation(
            id=baseline.baseline_direct_run_ids[-1],
            kind="direct", status="failed",
            agent_id=reviewer_id, created_at="2026-07-31T10:00:00Z",
            error="Our servers are currently overloaded"),
        AgentRunObservation(
            id="run-other-agent", kind="direct", status="failed",
            agent_id="agent-other", created_at="2026-07-31T10:01:00Z",
            error="Our servers are currently overloaded"),
        AgentRunObservation(
            id="run-comment", kind="comment", status="failed",
            agent_id=reviewer_id, created_at="2026-07-31T10:02:00Z",
            error="Our servers are currently overloaded"),
    ]
    wake_calls = []
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: wake_calls.append("wake"),
    )

    assert loop.collect_results(eng.store, eng.runtime, manifest, path) == {}
    assert wake_calls == []
    assert eng.store.get_work_item(item.id).bounces.review == 0


# ==================== 2. 失败注入 → needs_decision ====================

class TestFailureInjection:
    @staticmethod
    def _blocked_manifest_with_formal_run(
        tmp_path, *, role: str, run_status: str = "running",
        trigger_kind: str | None = "issue_assignment",
    ):
        reviewer = "bob" if role == "reviewer" else None
        active = _node("active", reviewer=reviewer, contract=_contract())
        decision = _node("decision")
        active.status = "blocked"
        decision.status = "blocked"
        manifest = _manifest([active, decision])
        path = str(tmp_path / f"{role}-active.yaml")
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        item = eng.store.create_work_item(
            "ws", "active", "formal run", dag_key="active",
            worker=active.worker, reviewer=reviewer,
            initial_status=WorkItemStatus.BLOCKED,
        )
        active.work_item_id = item.id
        decision_item = eng.store.create_work_item(
            "ws", "decision", "caller decision", dag_key="decision",
            worker=decision.worker,
            initial_status=WorkItemStatus.BLOCKED,
        )
        decision.work_item_id = decision_item.id
        agent = reviewer or active.worker
        agent_id = eng.store.resolve_agent_id(agent)
        run_id = f"run-{role}"
        if role == "reviewer":
            eng.store.update_work_item_metadata(
                item.id,
                phase=TaskPhase.REVIEW,
                review_subject_digest="subject-1",
                reviewer_run_baseline=ReviewerRunBaseline(
                    schema="omac.reviewer-run-baseline/v1",
                    subject_digest="subject-1",
                    target_reviewer=reviewer,
                    target_agent_id=agent_id,
                    cutoff_created_at="2026-08-01T00:00:00Z",
                    generation="review-1",
                ),
            )
        else:
            eng.store.update_work_item_metadata(
                item.id,
                phase=TaskPhase.AUTHORING,
                worker_handoff=WorkerHandoffIntent(
                    schema="omac.worker-handoff/v1",
                    state="pending",
                    target_worker=active.worker,
                    gate="explicit-dispatch",
                    source_review_subject_digest="operator-retry-1",
                    source_review_round=1,
                    target_review_bounce=0,
                    generation="worker-1",
                    target_agent_id=agent_id,
                    target_worker_bounce=0,
                ),
            )
        eng.store.clear_assignment(item.id)
        observations = {
            "active": eng.store.observe_work_item_control(item.id),
            "decision": eng.store.observe_work_item_control(decision_item.id),
        }
        runs = [AgentRunObservation(
            id=run_id,
            kind="direct",
            status=run_status,
            agent_id=agent_id,
            created_at="2026-08-01T00:01:00Z",
            trigger_kind=trigger_kind,
        )]
        save_manifest(manifest, path)
        return eng, manifest, path, runs, observations

    @pytest.mark.parametrize("trigger_kind", ["issue_assignment", "rerun"])
    def test_provisional_reviewer_decision_is_cleared_by_unique_formal_run(
        self, tmp_path, monkeypatch, trigger_kind,
    ):
        (
            eng, manifest, path, item, _reviewer_id, runs, target_run_id,
        ) = _provisional_reviewer_decision_fixture(
            tmp_path, trigger_kind=trigger_kind)
        projection = _multica_deferred_reviewer_projection(item)
        hydrated_payloads = []

        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, {"a": projection}),
        )
        monkeypatch.setattr(
            eng.runtime, "list_runs", lambda _item_id: list(runs))

        def hydrate(observed, plan):
            hydrated_payloads.append(plan)
            current = eng.store.get_work_item(observed.work_item.id)
            return replace(
                observed.work_item,
                verification=current.verification,
                contract=_dump_contract(current.contract),
            )

        monkeypatch.setattr(
            eng.store, "hydrate_work_item_evidence", hydrate)
        monkeypatch.setattr(
            eng.store,
            "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "provisional decision recovery must not assign"),
        )
        monkeypatch.setattr(
            eng.runtime,
            "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "provisional decision recovery must not wake"),
        )

        result = tick(eng.store, eng.runtime, manifest, path)

        recovered = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert result.running == ["a"]
        assert result.failed == []
        assert manifest.nodes["a"].status == "in_review"
        assert not recovered.decision_required
        assert recovered.reviewer_run_baseline.target_run_id == target_run_id
        assert hydrated_payloads == [frozenset({
            WorkItemPayload.VERIFICATION,
            WorkItemPayload.CONTRACT,
        })]

        restarted = load_manifest(path)
        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(False, {
                "a": WorkItemControlProjection(
                    eng.store.get_work_item(item.id)),
            }),
        )
        second = tick(eng.store, eng.runtime, restarted, path)

        assert second.state == "running"
        assert restarted.nodes["a"].status == "in_review"
        assert not eng.store.get_work_item(item.id).decision_required

    @pytest.mark.parametrize(
        "invalid_case",
        [
            "malformed-decision",
            "wrong-schema",
            "unknown-reason",
            "wrong-kind",
            "wrong-phase",
            "wrong-node",
            "wrong-resume-issue",
            "wrong-gate",
            "missing-gate",
            "wrong-failure-class",
            "missing-failure-class",
            "wrong-next-action",
            "missing-next-action",
            "operator-decision-extra",
            "wrong-subject",
            "stale-authoritative-subject",
            "contract-mismatch",
            "delivery-cutoff-mismatch",
            "wrong-agent",
            "stale-run",
            "ambiguous-run",
            "comment-run",
            "manual-run",
            "missing-trigger",
            "terminal-failure",
        ],
    )
    def test_invalid_provisional_reviewer_decision_stays_blocked(
        self, tmp_path, monkeypatch, invalid_case,
    ):
        (
            eng, manifest, path, item, reviewer_id, runs, _target_run_id,
        ) = _provisional_reviewer_decision_fixture(tmp_path)
        current = eng.store.get_work_item(item.id)
        decision = dict(current.decision_required)
        baseline = current.reviewer_run_baseline

        if invalid_case == "malformed-decision":
            decision = ["not-an-object"]
        elif invalid_case == "wrong-schema":
            decision["schema"] = "omac.decision-required/v2"
        elif invalid_case == "unknown-reason":
            decision["reason_code"] = "operator-product-decision"
        elif invalid_case == "wrong-kind":
            decision["kind"] = "acceptance"
        elif invalid_case == "wrong-phase":
            decision["phase"] = "authoring"
        elif invalid_case == "wrong-node":
            decision["node_id"] = "other"
        elif invalid_case == "wrong-resume-issue":
            decision["resume_issue_id"] = "other-issue"
        elif invalid_case == "wrong-gate":
            decision["gate"] = "human-confirmation"
        elif invalid_case == "missing-gate":
            decision.pop("gate")
        elif invalid_case == "wrong-failure-class":
            decision["failure_class"] = "operator-product-decision"
        elif invalid_case == "missing-failure-class":
            decision.pop("failure_class")
        elif invalid_case == "wrong-next-action":
            decision["next_action"] = "wait for product owner"
        elif invalid_case == "missing-next-action":
            decision.pop("next_action")
        elif invalid_case == "operator-decision-extra":
            decision["operator_decision"] = True
        elif invalid_case == "wrong-subject":
            baseline = replace(baseline, subject_digest="stale-subject")
        elif invalid_case == "stale-authoritative-subject":
            baseline = replace(baseline, subject_digest="stale-subject")
            eng.store.update_work_item_metadata(
                item.id, review_subject_digest="stale-subject")
        elif invalid_case == "contract-mismatch":
            current.contract = {"schema": "wrong-contract/v1"}
        elif invalid_case == "delivery-cutoff-mismatch":
            eng.store.update_work_item_metadata(
                item.id,
                delivery_identity=replace(
                    current.delivery_identity,
                    verification_created_at="2026-08-03T00:00:02Z",
                ),
            )
        elif invalid_case == "wrong-agent":
            runs[0] = replace(runs[0], agent_id="foreign-agent")
            for index, run in enumerate(runs):
                if run.agent_id == reviewer_id:
                    runs[index] = replace(run, agent_id="foreign-agent")
        elif invalid_case == "stale-run":
            runs[:] = [
                replace(run, created_at="2026-08-02T23:59:59Z")
                if run.agent_id == reviewer_id else run
                for run in runs
            ]
        elif invalid_case == "ambiguous-run":
            runs.append(AgentRunObservation(
                id="run-reviewer-ambiguous",
                kind="direct",
                status="running",
                agent_id=reviewer_id,
                created_at="2026-08-03T00:00:02Z",
                trigger_kind="issue_assignment",
            ))
        elif invalid_case in {"comment-run", "manual-run", "missing-trigger"}:
            trigger = {
                "comment-run": "comment",
                "manual-run": "manual",
                "missing-trigger": None,
            }[invalid_case]
            runs[:] = [
                replace(run, trigger_kind=trigger)
                if run.agent_id == reviewer_id else run
                for run in runs
            ]
        elif invalid_case == "terminal-failure":
            runs[:] = [
                replace(run, status="failed", error="reviewer failed")
                if run.agent_id == reviewer_id else run
                for run in runs
            ]

        eng.store.update_work_item_metadata(
            item.id,
            reviewer_run_baseline=baseline,
            decision_required=decision,
        )
        projection = eng.store.observe_work_item_control(item.id)
        clear_calls = []
        original_update = eng.store.update_work_item_metadata

        def update(item_id, **metadata):
            if metadata.get("decision_required") == {}:
                clear_calls.append(item_id)
            return original_update(item_id, **metadata)

        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, {"a": projection}),
        )
        monkeypatch.setattr(
            eng.runtime, "list_runs", lambda _item_id: list(runs))
        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(
            eng.store,
            "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "invalid provisional decision must not assign"),
        )
        monkeypatch.setattr(
            eng.runtime,
            "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "invalid provisional decision must not wake"),
        )

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert result.running == []
        assert result.failed == ["a"]
        assert manifest.nodes["a"].status == "blocked"
        assert eng.store.get_work_item(item.id).decision_required == decision
        assert clear_calls == []

    @pytest.mark.parametrize("failure_mode", ["download", "malformed"])
    def test_provisional_decision_hydration_failure_has_zero_writes(
        self, tmp_path, monkeypatch, failure_mode,
    ):
        (
            eng, manifest, path, item, _reviewer_id, runs, _target_run_id,
        ) = _provisional_reviewer_decision_fixture(tmp_path)
        projection = _multica_deferred_reviewer_projection(item)
        decision = dict(item.decision_required)
        original_file = Path(path).read_bytes()
        clear_calls = []
        original_update = eng.store.update_work_item_metadata

        def update(item_id, **metadata):
            if metadata.get("decision_required") == {}:
                clear_calls.append(item_id)
            return original_update(item_id, **metadata)

        def hydrate(observed, _plan):
            if failure_mode == "download":
                raise PlatformError("attachment download failed")
            current = eng.store.get_work_item(observed.work_item.id)
            return replace(
                observed.work_item,
                verification=current.verification,
                contract=None,
            )

        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, {"a": projection}),
        )
        monkeypatch.setattr(
            eng.runtime, "list_runs", lambda _item_id: list(runs))
        monkeypatch.setattr(
            eng.store, "hydrate_work_item_evidence", hydrate)
        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(
            eng.store,
            "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "hydration failure must not assign"),
        )
        monkeypatch.setattr(
            eng.runtime,
            "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "hydration failure must not wake"),
        )

        with pytest.raises(PlatformError):
            tick(eng.store, eng.runtime, manifest, path)

        assert manifest.nodes["a"].status == "blocked"
        assert eng.store.get_work_item(item.id).decision_required == decision
        assert Path(path).read_bytes() == original_file
        assert clear_calls == []

    @pytest.mark.parametrize(
        "crash_point",
        ["clear-response-unknown", "status-response-unknown", "manifest-save"],
    )
    def test_provisional_decision_clear_is_restart_safe(
        self, tmp_path, monkeypatch, crash_point,
    ):
        (
            eng, manifest, path, item, _reviewer_id, runs, target_run_id,
        ) = _provisional_reviewer_decision_fixture(tmp_path)
        projection = eng.store.observe_work_item_control(item.id)
        assignments_before = list(eng.store.assign_log)
        original_update = eng.store.update_work_item_metadata
        original_update_status = eng.store.update_status
        original_save = loop.save_manifest
        crashed = False

        def update(item_id, **metadata):
            nonlocal crashed
            result = original_update(item_id, **metadata)
            if (
                crash_point == "clear-response-unknown"
                and metadata.get("decision_required") == {}
                and not crashed
            ):
                crashed = True
                raise PlatformError("decision clear response unknown")
            return result

        def update_status(item_id, status):
            nonlocal crashed
            result = original_update_status(item_id, status)
            if (
                crash_point == "status-response-unknown"
                and status is WorkItemStatus.IN_REVIEW
                and not crashed
            ):
                crashed = True
                raise PlatformError("review status response unknown")
            return result

        def save(current, target):
            nonlocal crashed
            if crash_point == "manifest-save" and not crashed:
                crashed = True
                raise RuntimeError("crash before manifest save")
            return original_save(current, target)

        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, {"a": projection}),
        )
        monkeypatch.setattr(
            eng.runtime, "list_runs", lambda _item_id: list(runs))
        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(eng.store, "update_status", update_status)
        monkeypatch.setattr(loop, "save_manifest", save)
        monkeypatch.setattr(
            eng.store,
            "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "decision recovery must not assign"),
        )
        monkeypatch.setattr(
            eng.runtime,
            "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "decision recovery must not wake"),
        )

        error_type = (
            PlatformError
            if crash_point != "manifest-save"
            else RuntimeError
        )
        with pytest.raises(error_type):
            tick(eng.store, eng.runtime, manifest, path)

        assert not eng.store.get_work_item(item.id).decision_required
        persisted = load_manifest(path)
        assert persisted.nodes["a"].status == "blocked"

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", original_update)
        monkeypatch.setattr(
            eng.store, "update_status", original_update_status)
        monkeypatch.setattr(loop, "save_manifest", original_save)
        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, {"a": eng.store.observe_work_item_control(item.id)}),
        )

        recovered = tick(eng.store, eng.runtime, persisted, path)

        current = eng.store.get_work_item(item.id)
        assert recovered.state == "running"
        assert persisted.nodes["a"].status == "in_review"
        assert current.reviewer_run_baseline.target_run_id == target_run_id
        assert eng.store.assign_log == assignments_before

    def test_provisional_reviewer_completion_is_collected_after_restoration(
        self, tmp_path, monkeypatch,
    ):
        from omac.engines import mock as mock_engine

        (
            eng, manifest, path, item, reviewer_id, runs, target_run_id,
        ) = _provisional_reviewer_decision_fixture(tmp_path)
        original_list_runs = eng.runtime.list_runs
        monkeypatch.setattr(
            eng.runtime, "list_runs", lambda _item_id: list(runs))

        first = tick(eng.store, eng.runtime, manifest, path)

        assert first.state == "running"
        current = eng.store.get_work_item(item.id)
        assert current.reviewer_run_baseline.target_run_id == target_run_id
        report = _review_report(current, "reject")
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="reject",
            review_report=report,
            review_report_source=yaml.safe_dump(report),
        )
        runs[:] = [
            replace(run, status="completed")
            if run.id == target_run_id else run
            for run in runs
        ]
        mock_engine._finish_mock_run(item.id)
        monkeypatch.setattr(eng.runtime, "list_runs", original_list_runs)

        second = tick(eng.store, eng.runtime, manifest, path)

        recovered = eng.store.get_work_item(item.id)
        assert second.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.bounces.review == 1
        assert not recovered.decision_required

    @pytest.mark.parametrize("role", ["worker", "reviewer"])
    @pytest.mark.parametrize("trigger_kind", ["issue_assignment", "rerun"])
    def test_formal_active_run_keeps_runner_alive_with_other_blocked_node(
        self, tmp_path, monkeypatch, role, trigger_kind,
    ):
        eng, manifest, path, runs, observations = (
            self._blocked_manifest_with_formal_run(
                tmp_path, role=role, trigger_kind=trigger_kind)
        )
        run_reads = []
        monkeypatch.setattr(
            loop, "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, observations),
        )
        monkeypatch.setattr(
            loop, "collect_results", lambda *_args, **_kwargs: {})
        def list_runs(item_id):
            run_reads.append(item_id)
            return list(runs)

        monkeypatch.setattr(eng.runtime, "list_runs", list_runs)
        monkeypatch.setattr(
            eng.store,
            "observe_work_item_control",
            lambda _item_id: pytest.fail(
                "tick must reuse reconcile observations"),
        )
        assignments_before = list(eng.store.assign_log)
        runs_before = list(runs)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "running"
        assert result.running == ["active"]
        assert result.failed == ["decision"]
        assert result.report == {}
        assert run_reads == [manifest.nodes["active"].work_item_id]
        assert manifest.nodes["active"].status == (
            "in_review" if role == "reviewer" else "in_progress")
        assert eng.store.assign_log == assignments_before
        assert runs == runs_before

        restarted = load_manifest(path)
        second = tick(eng.store, eng.runtime, restarted, path)

        assert second.state == "running"
        assert second.running == ["active"]
        assert second.failed == ["decision"]
        assert eng.store.assign_log == assignments_before
        assert runs == runs_before

    @pytest.mark.parametrize("invalid_ledger", [
        {
            "schema": "future.review-ledger/v9",
            "cycles": [{"round": 3, "open_count": 1}],
            "blockers": [],
        },
        {
            "schema": "omac.review-ledger/v1",
            "cycles": [
                {
                    "round": 1,
                    "open_count": 1,
                    "open_blocker_ids": ["BLK-core"],
                    "reported_blocker_ids": ["BLK-core"],
                },
                {
                    "round": 2,
                    "open_count": 1,
                    "open_blocker_ids": [],
                    "reported_blocker_ids": [],
                },
                {
                    "round": 3,
                    "open_count": 1,
                    "open_blocker_ids": ["BLK-core"],
                    "reported_blocker_ids": ["BLK-core"],
                },
            ],
            "blockers": [{
                "blocker_id": "BLK-core",
                "root_cause_key": "core-acceptance",
                "obligation_id": "dimension:structure",
                "status": "open",
                "classification": "unchanged",
                "first_seen_round": 1,
                "last_seen_round": 3,
                "seen_count": 2,
            }],
        },
        {
            "schema": "omac.review-ledger/v1",
            "cycles": [
                {
                    "round": round_index,
                    "new_count": 1 if round_index == 1 else 0,
                    "fixed_count": 0,
                    "regressed_count": 0,
                    "unchanged_count": 0 if round_index == 1 else 1,
                    "open_count": 1,
                    "prior_open_blocker_ids": (
                        [] if round_index == 1 else ["BLK-core"]
                    ),
                    "open_blocker_ids": ["BLK-core"],
                    "reported_blocker_ids": ["BLK-core"],
                }
                for round_index in range(1, 4)
            ],
            "blockers": [{
                "blocker_id": "BLK-core",
                "root_cause_key": "core-acceptance",
                "obligation_id": "dimension:structure",
                "status": "open",
                "classification": "fixed",
                "first_seen_round": 1,
                "last_seen_round": 3,
                "seen_count": 3,
            }],
        },
    ], ids=["wrong-schema", "cycle-id-underreport", "open-fixed"])
    def test_active_worker_restore_validates_deferred_ledger_before_manifest_write(
        self, tmp_path, monkeypatch, invalid_ledger,
    ):
        eng, manifest, path, runs, observations = (
            self._blocked_manifest_with_formal_run(tmp_path, role="worker")
        )
        item_id = manifest.nodes["active"].work_item_id
        current = eng.store.get_work_item(item_id)
        intent = replace(
            current.worker_handoff,
            gate="review",
            source_review_round=3,
            target_review_bounce=3,
        )
        eng.store.update_work_item_metadata(item_id, worker_handoff=intent)
        current = eng.store.get_work_item(item_id)
        current.review_ledger = None
        current.review_ledger_ref = {
            "attachment_id": "review-ledger",
            "sha256": "a" * 64,
        }
        deferred = WorkItemControlProjection(
            current,
            deferred_payloads=frozenset({WorkItemPayload.REVIEW_LEDGER}),
        )
        observations["active"] = deferred
        before = Path(path).read_bytes()

        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(False, observations),
        )
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
        monkeypatch.setattr(
            eng.store,
            "hydrate_work_item_evidence",
            lambda projection, _plan: replace(
                projection.work_item,
                review_ledger=invalid_ledger,
            ),
        )
        for target, name in (
            (eng.store, "update_work_item_metadata"),
            (eng.store, "update_status"),
            (eng.store, "assign_work_item"),
            (eng.runtime, "wake"),
        ):
            monkeypatch.setattr(
                target,
                name,
                lambda *_args, _name=name, **_kwargs: pytest.fail(
                    f"invalid ledger restore must not call {_name}"),
            )
        monkeypatch.setattr(
            loop,
            "save_manifest",
            lambda *_args, **_kwargs: pytest.fail(
                "invalid ledger restore must not save manifest"),
        )

        with pytest.raises(PlatformError, match="review ledger"):
            tick(eng.store, eng.runtime, manifest, path)

        assert manifest.nodes["active"].status == "blocked"
        assert observations["active"] is deferred
        assert Path(path).read_bytes() == before

    @pytest.mark.parametrize("role", ["worker", "reviewer"])
    @pytest.mark.parametrize("trigger_kind", ["comment", "manual", None])
    def test_nonformal_active_run_cannot_restore_blocked_stage(
        self, tmp_path, monkeypatch, role, trigger_kind,
    ):
        eng, manifest, path, runs, observations = (
            self._blocked_manifest_with_formal_run(
                tmp_path, role=role, trigger_kind=trigger_kind)
        )
        monkeypatch.setattr(
            loop, "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, observations),
        )
        monkeypatch.setattr(
            loop, "collect_results", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(
            eng.runtime, "list_runs", lambda _item_id: list(runs))
        assignments_before = list(eng.store.assign_log)

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert result.running == []
        assert set(result.failed) == {"active", "decision"}
        assert manifest.nodes["active"].status == "blocked"
        assert eng.store.assign_log == assignments_before

        restarted = load_manifest(path)
        second = tick(eng.store, eng.runtime, restarted, path)

        assert second.state == "needs_decision"
        assert second.running == []
        assert set(second.failed) == {"active", "decision"}
        assert eng.store.assign_log == assignments_before

    def test_active_worker_is_not_cascade_blocked_with_blocked_downstream(
        self, tmp_path, monkeypatch,
    ):
        eng, manifest, path, runs, observations = (
            self._blocked_manifest_with_formal_run(tmp_path, role="worker")
        )
        manifest.nodes["decision"].blocked_by = ["active"]
        save_manifest(manifest, path)
        monkeypatch.setattr(
            loop, "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, observations),
        )
        monkeypatch.setattr(
            loop, "collect_results", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(
            eng.runtime, "list_runs", lambda _item_id: list(runs))

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "running"
        assert result.running == ["active"]
        assert result.failed == ["decision"]
        assert manifest.nodes["active"].status == "in_progress"
        assert manifest.nodes["decision"].status == "blocked"

    @pytest.mark.parametrize("verdict", ["reject", "pass-with-nits"])
    def test_recovered_active_reviewer_completion_preserves_rework_context(
        self, tmp_path, verdict,
    ):
        from omac.engines import mock as mock_engine

        eng, manifest, path, item, _reviewer_id = (
            _reviewer_runtime_failure_fixture(tmp_path)
        )
        decision = _node("decision")
        decision.status = "blocked"
        decision_item = eng.store.create_work_item(
            "ws", "decision", "caller decision", dag_key="decision",
            worker=decision.worker,
            initial_status=WorkItemStatus.BLOCKED,
        )
        decision.work_item_id = decision_item.id
        manifest.nodes["decision"] = decision
        manifest.nodes["a"].status = "blocked"
        save_manifest(manifest, path)
        reviewer_assignments = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])

        first = tick(eng.store, eng.runtime, manifest, path)

        assert first.state == "running"
        assert first.running == ["a"]
        assert first.failed == ["decision"]
        assert manifest.nodes["a"].status == "in_review"
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments

        current = eng.store.get_work_item(item.id)
        nits = (
            ["tighten the recovery assertion"]
            if verdict == "pass-with-nits" else None
        )
        report = _review_report(current, verdict, nits=nits)
        report_path = tmp_path / f"{verdict}-review.yaml"
        report_path.write_text(yaml.safe_dump(report))
        submit_work(
            eng.store,
            item.id,
            verdict=verdict,
            report_file=str(report_path),
        )
        mock_engine._finish_mock_run(item.id)

        second = tick(eng.store, eng.runtime, manifest, path)

        recovered = eng.store.get_work_item(item.id)
        assert second.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.bounces.review == 1
        show = build_show_output(recovered, "worker:alice")
        if verdict == "pass-with-nits":
            assert show["context"]["previous_review"] == {
                "verdict": "pass-with-nits",
                "report_ref": recovered.worker_handoff.source_review_feedback[
                    "report_ref"],
                "nits": nits,
            }
            assert "required_closures" not in show["context"]
        else:
            assert "previous_review" not in show["context"]
            assert show["context"]["required_closures"] == [{
                "blocker_id": recovered.review_ledger["blockers"][0][
                    "blocker_id"],
                "obligation_id": "dimension:structure",
                "root_cause_key": "core-acceptance",
                "summary": "核心验收未满足",
                "required_fix": "修复核心验收路径",
            }]
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == 2

    def test_terminal_formal_run_does_not_hide_needs_decision(
        self, tmp_path, monkeypatch,
    ):
        eng, manifest, path, runs, observations = (
            self._blocked_manifest_with_formal_run(
                tmp_path, role="reviewer", run_status="completed")
        )
        run_reads = []
        monkeypatch.setattr(
            loop, "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, observations),
        )
        monkeypatch.setattr(
            loop, "collect_results", lambda *_args, **_kwargs: {})
        def list_runs(item_id):
            run_reads.append(item_id)
            return list(runs)

        monkeypatch.setattr(eng.runtime, "list_runs", list_runs)
        monkeypatch.setattr(
            eng.store,
            "observe_work_item_control",
            lambda _item_id: pytest.fail(
                "tick must reuse reconcile observations"),
        )

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert result.running == []
        assert set(result.failed) == {"active", "decision"}
        assert result.report
        assert run_reads == [manifest.nodes["active"].work_item_id]

    def test_foreign_active_run_does_not_hide_needs_decision(
        self, tmp_path, monkeypatch,
    ):
        eng, manifest, path, runs, observations = (
            self._blocked_manifest_with_formal_run(
                tmp_path, role="reviewer")
        )
        run_reads = []
        runs[0] = replace(
            runs[0], agent_id=eng.store.resolve_agent_id("charlie"))
        monkeypatch.setattr(
            loop, "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(
                False, observations),
        )
        monkeypatch.setattr(
            loop, "collect_results", lambda *_args, **_kwargs: {})
        def list_runs(item_id):
            run_reads.append(item_id)
            return list(runs)

        monkeypatch.setattr(eng.runtime, "list_runs", list_runs)
        monkeypatch.setattr(
            eng.store,
            "observe_work_item_control",
            lambda _item_id: pytest.fail(
                "tick must reuse reconcile observations"),
        )

        result = tick(eng.store, eng.runtime, manifest, path)

        assert result.state == "needs_decision"
        assert result.running == []
        assert result.report
        assert run_reads == [manifest.nodes["active"].work_item_id]

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
        nodes = [_node("a", reviewer="bob", contract=_contract())]
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
        nodes = [_node("a", reviewer="bob", contract=_contract())]
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
        import hashlib

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
        verification = {
            "commands": [_business_command()],
            "integration_gates": [{
                "name": "setup-gate",
                "commands": [_business_command()],
            }],
            "pr_base": "main",
            "coverage": 90,
        }
        pr_url = f"https://mock.example.com/pr/{item.id}"
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={
                "pr_url": pr_url,
                "head_sha": hashlib.sha256(pr_url.encode("utf-8")).hexdigest(),
            },
            verification=verification,
            verification_source=yaml.safe_dump(verification),
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
        source_verdict = "pass-with-nits" if gate == "review-nits" else None
        source_feedback = (
            {
                "verdict": "pass-with-nits",
                "nits": ["follow up"],
                "report_ref": {
                    "attachment_id": "review-report-1",
                    "sha256": "a" * 64,
                },
            }
            if gate == "review-nits" else None
        )
        intent = WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate=gate,
            source_review_subject_digest=source_subject,
            source_review_round=1,
            source_review_verdict=source_verdict,
            source_review_feedback=source_feedback,
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
            delivery_identity={},
        )
        eng.store.reset_review(item.id)
        eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
        return intent, source

    @staticmethod
    def _stalled_review_ledger(cycle_count=3):
        blocker_id = "BLK-core"
        return {
            "schema": "omac.review-ledger/v1",
            "cycles": [
                {
                    "round": round_index,
                    "subject_digest": f"subject-{round_index}",
                    "report_digest": f"report-{round_index}",
                    "verdict": "reject",
                    "new_count": 1 if round_index == 1 else 0,
                    "fixed_count": 0,
                    "regressed_count": 0,
                    "unchanged_count": 0 if round_index == 1 else 1,
                    "open_count": 1,
                    "prior_open_blocker_ids": (
                        [] if round_index == 1 else [blocker_id]
                    ),
                    "open_blocker_ids": [blocker_id],
                    "reported_blocker_ids": [blocker_id],
                }
                for round_index in range(1, cycle_count + 1)
            ],
            "blockers": [{
                "blocker_id": blocker_id,
                "root_cause_key": "core-acceptance",
                "obligation_id": "dimension:structure",
                "summary": "core contract is incomplete",
                "evidence": "the same invariant still fails",
                "required_fix": "close the contract root",
                "status": "open",
                "classification": "unchanged",
                "first_seen_round": 1,
                "last_seen_round": cycle_count,
                "seen_count": cycle_count,
            }],
        }

    @staticmethod
    def _late_scope_expanding_review_ledger():
        blocker_id = "BLK-late"
        return {
            "schema": "omac.review-ledger/v1",
            "cycles": [
                {
                    "round": round_index,
                    "open_count": 0,
                    "open_blocker_ids": [],
                    "reported_blocker_ids": [],
                }
                for round_index in range(1, 6)
            ] + [{
                "round": 6,
                "open_count": 1,
                "open_blocker_ids": [blocker_id],
                "reported_blocker_ids": [blocker_id],
            }],
            "blockers": [{
                "blocker_id": blocker_id,
                "root_cause_key": "late-scope",
                "obligation_id": "dimension:structure",
                "summary": "late scope appeared",
                "evidence": "cycle six introduced a new root",
                "required_fix": "reconsider the task boundary",
                "status": "open",
                "classification": "new",
                "first_seen_round": 6,
                "last_seen_round": 6,
                "seen_count": 1,
            }],
        }

    @pytest.mark.parametrize("terminal_status", ["completed", "cancelled"])
    def test_terminal_worker_handoff_without_submit_uses_bounded_worker_retry(
        self, tmp_path, monkeypatch, terminal_status,
    ):
        """completed/cancelled no-submit 跨 grace 后使用业务 worker retry。"""
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
                status=terminal_status,
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

    def test_multica_like_terminal_worker_handoff_recovers_without_run_flags(
        self, tmp_path, monkeypatch,
    ):
        """平台保持 in_review/authoring 时，Run 事实仍驱动 grace 后恢复。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        assert eng.store.get_work_item(item.id).phase is TaskPhase.AUTHORING
        assert not eng.store.get_work_item(item.id).agent_run_failed
        assert not eng.store.get_work_item(
            item.id).agent_run_finished_without_submit

        now = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
        retry_run_visible = False
        wake_calls = 0

        def list_runs(_item_id):
            runs = [AgentRunObservation(
                id=intent.target_run_id,
                kind="direct",
                status="completed",
                agent_id=intent.target_agent_id,
                created_at="2026-08-01T00:00:00Z",
            )]
            if retry_run_visible:
                runs.append(AgentRunObservation(
                    id="run-worker-multica-retry",
                    kind="direct",
                    status="running",
                    agent_id=intent.target_agent_id,
                    created_at="2026-08-01T00:01:00Z",
                ))
            return runs

        def wake(_item_id, _agent, role):
            nonlocal retry_run_visible, wake_calls
            assert role == "worker"
            wake_calls += 1
            retry_run_visible = True

        monkeypatch.setattr(loop, "_utcnow", lambda: now[0])
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(eng.runtime, "list_runs", list_runs)
        monkeypatch.setattr(eng.runtime, "wake", wake)

        assert loop.collect_results(
            eng.store, eng.runtime, manifest, path,
            retry_limits={"worker": 1},
        ) == {}
        now[0] += timedelta(seconds=loop._HANDOFF_TERMINAL_GRACE_SECONDS + 1)
        assert loop.collect_results(
            eng.store, eng.runtime, manifest, path,
            retry_limits={"worker": 1},
        ) == {}
        persisted = load_manifest(path)
        assert loop.collect_results(
            eng.store, eng.runtime, persisted, path,
            retry_limits={"worker": 1},
        ) == {}

        recovered = eng.store.get_work_item(item.id)
        assert wake_calls == 1
        assert recovered.worker_handoff is not None
        assert recovered.worker_handoff.target_run_id == (
            "run-worker-multica-retry")
        assert recovered.bounces.worker == 1
        assert recovered.agent_run_failed is False
        assert recovered.agent_run_finished_without_submit is False
        assert persisted.nodes["a"].status == "in_progress"

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

    def test_non_converging_review_blocks_before_another_worker_handoff(
        self, tmp_path, monkeypatch,
    ):
        """Two failed reworks stop at cycle 3, before another handoff."""
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        current.review_ledger = self._stalled_review_ledger(2)
        eng.store.update_work_item_metadata(
            item.id, review_ledger=current.review_ledger, review_bounce=2)
        current = eng.store.get_work_item(item.id)
        eng.store.prepare_review_cycle(
            item.id, review_subject_digest(current, 3))
        current = eng.store.get_work_item(item.id)
        report = _review_report(current, "reject")
        report["prior_blocker_results"][0]["status"] = "unchanged"
        report["blockers"][0]["classification"] = "unchanged"
        report_path = tmp_path / "review.yaml"
        report_path.write_text(yaml.safe_dump(report))
        submit_work(
            eng.store, item.id, verdict="reject", report_file=str(report_path))
        submitted = eng.store.get_work_item(item.id)
        assert len(submitted.review_ledger["cycles"]) == 3
        convergence = review_convergence_decision(submitted.review_ledger)
        assert convergence is not None, submitted.review_ledger
        assert convergence[
            "reason_code"] == "review-convergence-stalled"

        monkeypatch.setattr(
            loop,
            "_dispatch_worker_handoff",
            lambda *_args, **_kwargs: pytest.fail(
                "non-converging review must not dispatch another worker"),
        )

        result = tick(
            eng.store,
            eng.runtime,
            manifest,
            path,
            max_parallel=4,
            retry_limits={"review": 20},
        )

        blocked = eng.store.get_work_item(item.id)
        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert blocked.status is WorkItemStatus.BLOCKED
        assert blocked.bounces.review == 2
        assert blocked.decision_required["reason_code"] == (
            "review-convergence-stalled")
        assert blocked.decision_required["recommended_action"] == "dag-amendment"
        assert blocked.decision_required["convergence"]["cycle_count"] == 3

    @pytest.mark.parametrize("terminal_observed", [False, True])
    def test_restart_consumes_convergence_before_existing_worker_handoff(
        self, tmp_path, monkeypatch, terminal_observed,
    ):
        """Pending and finished handoffs are retired by the same decision."""
        from dataclasses import replace

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        intent = replace(
            intent, source_review_round=3, target_review_bounce=3)
        eng.store.update_work_item_metadata(item.id, worker_handoff=intent)
        if terminal_observed:
            intent = replace(
                intent,
                terminal_observed_at="2026-08-03T00:00:00+00:00",
            )
            eng.store.update_work_item_metadata(
                item.id, worker_handoff=intent)
            eng.store.get_work_item(
                item.id).agent_run_finished_without_submit = True
        eng.store.update_work_item_metadata(
            item.id, review_ledger=self._stalled_review_ledger())
        set_node(manifest, "a", status="in_progress")
        save_manifest(manifest, path)

        assignments_before = list(eng.store.assign_log)
        runs_before = list(eng.runtime.list_runs(item.id))
        decision_writes = 0
        blocked_writes = 0
        original_update = eng.store.update_work_item_metadata
        original_status = eng.store.update_status

        def update(item_id, **metadata):
            nonlocal decision_writes
            decision = metadata.get("decision_required")
            if isinstance(decision, dict) and decision.get(
                "reason_code") == "review-convergence-stalled":
                decision_writes += 1
            return original_update(item_id, **metadata)

        def update_status(item_id, status):
            nonlocal blocked_writes
            if status is WorkItemStatus.BLOCKED:
                blocked_writes += 1
            return original_status(item_id, status)

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(eng.store, "update_status", update_status)
        monkeypatch.setattr(
            loop,
            "_dispatch_worker_handoff",
            lambda *_args, **_kwargs: pytest.fail(
                "convergence decision must precede handoff observation/retry"),
        )

        first = tick(eng.store, eng.runtime, manifest, path)
        persisted = load_manifest(path)
        second = tick(eng.store, eng.runtime, persisted, path)

        blocked = eng.store.get_work_item(item.id)
        assert first.state == "needs_decision"
        assert second.state == "needs_decision"
        assert persisted.nodes["a"].status == "blocked"
        assert blocked.status is WorkItemStatus.BLOCKED
        assert blocked.decision_required["reason_code"] == (
            "review-convergence-stalled")
        assert blocked.worker_handoff == intent
        assert decision_writes == 1
        assert blocked_writes == 1
        assert eng.store.assign_log == assignments_before
        assert eng.runtime.list_runs(item.id) == runs_before

    def test_active_worker_restore_consumes_convergence_before_any_write(
        self, tmp_path, monkeypatch,
    ):
        """An active Run cannot publish in_progress before its decision."""
        eng, manifest, path, runs, observations = (
            TestFailureInjection._blocked_manifest_with_formal_run(
                tmp_path, role="worker")
        )
        item_id = manifest.nodes["active"].work_item_id
        current = eng.store.get_work_item(item_id)
        intent = replace(
            current.worker_handoff,
            gate="review",
            source_review_round=3,
            target_review_bounce=3,
        )
        eng.store.update_work_item_metadata(
            item_id,
            worker_handoff=intent,
            review_ledger=self._stalled_review_ledger(),
        )
        observations["active"] = eng.store.observe_work_item_control(item_id)
        events = []
        original_decision = loop.review_convergence_decision
        original_save = loop.save_manifest
        original_update = eng.store.update_work_item_metadata
        original_status = eng.store.update_status

        monkeypatch.setattr(
            loop,
            "reconcile_with_observations",
            lambda *_args, **_kwargs: loop.ReconcileResult(False, observations),
        )
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
        monkeypatch.setattr(
            eng.store,
            "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "convergence-blocked restore must not assign"),
        )
        monkeypatch.setattr(
            eng.runtime,
            "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "convergence-blocked restore must not wake"),
        )

        def decide(ledger):
            events.append("decision")
            return original_decision(ledger)

        def save(current_manifest, current_path):
            events.append(f"manifest:{current_manifest.nodes['active'].status}")
            return original_save(current_manifest, current_path)

        def update(item_id, **metadata):
            events.append("store:update-metadata")
            return original_update(item_id, **metadata)

        def update_status(item_id, status):
            events.append(f"store:status:{status.value}")
            return original_status(item_id, status)

        monkeypatch.setattr(loop, "review_convergence_decision", decide)
        monkeypatch.setattr(loop, "save_manifest", save)
        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(eng.store, "update_status", update_status)

        result = tick(eng.store, eng.runtime, manifest, path)

        blocked = eng.store.get_work_item(item_id)
        assert events[0] == "decision"
        assert "manifest:in_progress" not in events
        assert result.state == "needs_decision"
        assert manifest.nodes["active"].status == "blocked"
        assert blocked.decision_required["reason_code"] == (
            "review-convergence-stalled")

    def test_impossible_ledger_count_fails_before_worker_handoff_side_effects(
        self, tmp_path, monkeypatch,
    ):
        """Canonical cycle sightings and seen_count must agree exactly."""
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        intent = replace(
            intent, source_review_round=3, target_review_bounce=3)
        ledger = self._stalled_review_ledger()
        ledger["blockers"][0]["seen_count"] = 2
        eng.store.update_work_item_metadata(
            item.id, worker_handoff=intent, review_ledger=ledger)
        set_node(manifest, "a", status="in_progress")
        save_manifest(manifest, path)
        before = Path(path).read_bytes()

        monkeypatch.setattr(
            loop,
            "_dispatch_worker_handoff",
            lambda *_args, **_kwargs: pytest.fail(
                "invalid ledger must not reach worker handoff"),
        )
        for target, name in (
            (eng.store, "update_work_item_metadata"),
            (eng.store, "update_status"),
            (eng.store, "assign_work_item"),
            (eng.runtime, "wake"),
        ):
            monkeypatch.setattr(
                target,
                name,
                lambda *_args, _name=name, **_kwargs: pytest.fail(
                    f"invalid ledger must not call {_name}"),
            )

        with pytest.raises(PlatformError, match="seen_count"):
            tick(eng.store, eng.runtime, manifest, path)

        assert manifest.nodes["a"].status == "in_progress"
        assert Path(path).read_bytes() == before

    @pytest.mark.parametrize("forgery", [
        "first-seen-round",
        "current-status",
        "current-classification-fixed",
        "current-classification-deeper",
        "cycle-id-underreport",
        "latest-reported-not-open",
    ])
    def test_forged_blocker_projection_fails_before_worker_handoff_side_effects(
        self, tmp_path, monkeypatch, forgery,
    ):
        """Canonical cycle facts cannot be reinterpreted by blocker fields."""
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        if forgery == "first-seen-round":
            round_index = 6
            ledger = self._late_scope_expanding_review_ledger()
            ledger["blockers"][0]["first_seen_round"] = 1
        elif forgery == "current-status":
            round_index = 3
            ledger = self._stalled_review_ledger()
            ledger["blockers"][0].update({
                "status": "fixed",
                "classification": "fixed",
            })
        elif forgery == "current-classification-fixed":
            round_index = 3
            ledger = self._stalled_review_ledger()
            ledger["blockers"][0]["classification"] = "fixed"
        elif forgery == "current-classification-deeper":
            round_index = 3
            ledger = self._stalled_review_ledger()
            ledger["blockers"][0]["classification"] = "deeper"
        elif forgery == "cycle-id-underreport":
            round_index = 3
            ledger = self._stalled_review_ledger()
            ledger["cycles"][1]["open_blocker_ids"] = []
            ledger["cycles"][1]["reported_blocker_ids"] = []
            ledger["blockers"][0]["seen_count"] = 2
        else:
            round_index = 3
            ledger = self._stalled_review_ledger()
            ledger["cycles"][-1]["open_blocker_ids"] = []
            ledger["blockers"][0].update({
                "status": "fixed",
                "classification": "fixed",
            })
        intent = replace(
            intent,
            source_review_round=round_index,
            target_review_bounce=round_index,
        )
        eng.store.update_work_item_metadata(
            item.id, worker_handoff=intent, review_ledger=ledger)
        set_node(manifest, "a", status="in_progress")
        save_manifest(manifest, path)
        before = Path(path).read_bytes()

        monkeypatch.setattr(
            loop,
            "_dispatch_worker_handoff",
            lambda *_args, **_kwargs: pytest.fail(
                "forged blocker projection must not reach worker handoff"),
        )
        for target, name in (
            (eng.store, "update_work_item_metadata"),
            (eng.store, "update_status"),
            (eng.store, "assign_work_item"),
            (eng.runtime, "wake"),
        ):
            monkeypatch.setattr(
                target,
                name,
                lambda *_args, _name=name, **_kwargs: pytest.fail(
                    f"forged blocker projection must not call {_name}"),
            )
        monkeypatch.setattr(
            loop,
            "save_manifest",
            lambda *_args, **_kwargs: pytest.fail(
                "forged blocker projection must not save manifest"),
        )

        with pytest.raises(PlatformError, match="review ledger"):
            tick(eng.store, eng.runtime, manifest, path)

        assert manifest.nodes["a"].status == "in_progress"
        assert Path(path).read_bytes() == before

    @pytest.mark.parametrize(
        ("round_index", "requires_ledger"),
        [(1, False), (3, True)],
    )
    def test_review_handoff_hydration_plan_requires_authoritative_ledger(
        self, tmp_path, round_index, requires_ledger,
    ):
        """No recovery threshold may bypass the authoritative ledger."""
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        set_node(manifest, "a", status="in_progress")
        eng.store.update_work_item_metadata(
            item.id,
            worker_handoff=replace(
                intent,
                source_review_round=round_index,
                target_review_bounce=round_index,
            ),
        )
        current = eng.store.get_work_item(item.id)
        current.review_ledger = None
        current.review_ledger_ref = {
            "attachment_id": "review-ledger-attachment",
            "sha256": "a" * 64,
        }
        projection = WorkItemControlProjection(
            current,
            deferred_payloads=frozenset({WorkItemPayload.REVIEW_LEDGER}),
        )

        plan = loop._build_work_item_hydration_plan(
            manifest.nodes["a"], projection)

        assert (
            WorkItemPayload.REVIEW_LEDGER in plan
        ) is requires_ledger

    @pytest.mark.parametrize("invalid_ledger", [
        {},
        {"schema": "future.review-ledger/v9", "cycles": [], "blockers": []},
        {"schema": "omac.review-ledger/v1", "cycles": {}, "blockers": []},
        {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": {}},
        {"schema": "omac.review-ledger/v1", "cycles": ["bad"], "blockers": []},
        {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": ["bad"]},
        {
            "schema": "omac.review-ledger/v1",
            "cycles": [{"round": 1}],
            "blockers": [],
        },
        {
            "schema": "omac.review-ledger/v1",
            "cycles": [{"round": 3, "open_count": 1}],
            "blockers": [{
                "blocker_id": "BLK-1", "root_cause_key": "root-1",
                "obligation_id": "dimension:structure", "status": "open",
                "classification": "deeper", "first_seen_round": 1,
            }],
        },
    ])
    def test_invalid_hydrated_review_ledger_fails_before_handoff_side_effects(
        self, tmp_path, monkeypatch, invalid_ledger,
    ):
        """A parseable deferred ledger is still untrusted until canonical validation."""
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        intent = replace(
            intent, source_review_round=3, target_review_bounce=3)
        eng.store.update_work_item_metadata(item.id, worker_handoff=intent)
        current = eng.store.get_work_item(item.id)
        current.review_ledger = None
        current.review_ledger_ref = {
            "attachment_id": "review-ledger", "sha256": "a" * 64}
        set_node(manifest, "a", status="in_progress")
        save_manifest(manifest, path)
        deferred = WorkItemControlProjection(
            current,
            deferred_payloads=frozenset({WorkItemPayload.REVIEW_LEDGER}),
        )

        monkeypatch.setattr(
            eng.store, "observe_work_item_control", lambda _item_id: deferred)
        monkeypatch.setattr(
            eng.store,
            "hydrate_work_item_evidence",
            lambda projection, _plan: replace(
                projection.work_item, review_ledger=invalid_ledger),
        )
        before = Path(path).read_bytes()
        assignments = list(eng.store.assign_log)
        for target, name in (
            (eng.store, "update_work_item_metadata"),
            (eng.store, "update_status"),
            (eng.store, "assign_work_item"),
            (eng.runtime, "wake"),
        ):
            monkeypatch.setattr(
                target, name,
                lambda *_args, _name=name, **_kwargs: pytest.fail(
                    f"invalid ledger must not call {_name}"),
            )

        for current_manifest in (manifest, load_manifest(path)):
            with pytest.raises(PlatformError, match="review ledger"):
                tick(eng.store, eng.runtime, current_manifest, path)
            assert current_manifest.nodes["a"].status == "in_progress"
            assert Path(path).read_bytes() == before
            assert eng.store.assign_log == assignments
            persisted = eng.store.get_work_item(item.id)
            assert persisted.status is WorkItemStatus.IN_PROGRESS
            assert persisted.worker_handoff == intent
            assert persisted.review_ledger is None
            assert persisted.review_ledger_ref == current.review_ledger_ref

    def test_truncated_review_ledger_fails_before_runner_handoff(
        self, tmp_path, monkeypatch,
    ):
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        intent = replace(
            intent, source_review_round=10, target_review_bounce=10)
        ledger = self._stalled_review_ledger(1)
        ledger["cycles"][0]["round"] = 10
        ledger["blockers"][0].update({
            "classification": "deeper",
            "last_seen_round": 10,
            "seen_count": 1,
        })
        eng.store.update_work_item_metadata(
            item.id, worker_handoff=intent, review_ledger=ledger)
        set_node(manifest, "a", status="in_progress")
        save_manifest(manifest, path)
        before = Path(path).read_bytes()
        assignments = list(eng.store.assign_log)

        monkeypatch.setattr(
            loop,
            "_dispatch_worker_handoff",
            lambda *_args, **_kwargs: pytest.fail(
                "truncated cycle history must not reach handoff dispatch"),
        )

        with pytest.raises(PlatformError, match="review ledger"):
            tick(eng.store, eng.runtime, manifest, path)

        assert manifest.nodes["a"].status == "in_progress"
        assert Path(path).read_bytes() == before
        assert eng.store.assign_log == assignments

    def test_valid_nonconverging_review_ledger_continues_handoff_retry(
        self, tmp_path, monkeypatch,
    ):
        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        intent, _source = self._prepare_causal_handoff(eng, item)
        intent = replace(
            intent, source_review_round=3, target_review_bounce=3)
        ledger = self._stalled_review_ledger()
        ledger["blockers"][0]["classification"] = "deeper"
        ledger["cycles"][-1]["unchanged_count"] = 0
        eng.store.update_work_item_metadata(
            item.id, worker_handoff=intent, review_ledger=ledger)
        set_node(manifest, "a", status="in_progress")
        calls = []

        def dispatch(*_args, **_kwargs):
            calls.append(True)
            return loop._WorkerHandoffResult("waiting", intent)

        monkeypatch.setattr(loop, "_dispatch_worker_handoff", dispatch)

        assert loop.collect_results(
            eng.store, eng.runtime, manifest, path) == {}
        assert calls == [True]
        assert manifest.nodes["a"].status == "in_progress"

    def test_pass_with_nits_at_review_limit_needs_decision_without_handoff(
        self, tmp_path, monkeypatch,
    ):
        """final nits 保留 Reviewer 事实并交人决策，不能绕过 review budget。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        report = _review_report(
            item, "pass-with-nits", nits=["operator decision required"])
        eng.store.update_work_item_metadata(
            item.id,
            review_bounce=9,
            review_verdict="pass-with-nits",
            review_report=report,
        )
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_subject_digest=review_subject_digest(current, 10),
        )
        worker_assignments = len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ])
        runs_before = len(eng.runtime.list_runs(item.id))
        monkeypatch.setattr(
            loop,
            "_dispatch_worker_handoff",
            lambda *_args, **_kwargs: pytest.fail(
                "exhausted pass-with-nits must not dispatch worker handoff"),
        )

        result = tick(
            eng.store,
            eng.runtime,
            manifest,
            path,
            max_parallel=4,
            retry_limits={"review": 9},
        )

        got = eng.store.get_work_item(item.id)
        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert got.status == WorkItemStatus.BLOCKED
        assert got.bounces.review == 9
        assert got.review_verdict == "pass-with-nits"
        assert got.review_report == report
        assert got.worker_handoff is None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "worker"
        ]) == worker_assignments
        assert len(eng.runtime.list_runs(item.id)) == runs_before
        assert got.decision_required == {
            "schema": "omac.decision-required/v1",
            "reason_code": "review-nits-budget-exhausted",
            "kind": "develop",
            "phase": "review",
            "gate": "review-nits",
            "rounds": 9,
            "consumed": 9,
            "limit": 9,
            "resume_issue_id": item.id,
            "node_id": "a",
            "verdict": "pass-with-nits",
        }

    def test_pass_with_nits_below_review_limit_dispatches_fresh_review(
        self, tmp_path,
    ):
        """未耗尽时 nits 仍回 Worker，bounce +1，重交后必须 fresh review。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        report = _review_report(
            item, "pass-with-nits", nits=["follow up"])
        eng.store.update_work_item_metadata(
            item.id,
            review_bounce=1,
            review_verdict="pass-with-nits",
            review_report=report,
            review_report_source=yaml.safe_dump(report),
        )
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_subject_digest=review_subject_digest(current, 2),
        )

        first = tick(
            eng.store,
            eng.runtime,
            manifest,
            path,
            max_parallel=4,
            retry_limits={"review": 2},
        )

        got = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.bounces.review == 2
        assert got.review_verdict is None
        assert got.review_report is None
        show = build_show_output(got, "worker:alice")
        assert show["context"]["previous_review"] == {
            "verdict": "pass-with-nits",
            "report_ref": got.worker_handoff.source_review_feedback[
                "report_ref"],
            "nits": ["follow up"],
        }

        reviewer_assignments = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])
        self._submit_revision(eng, item, revision=2)
        second = tick(
            eng.store,
            eng.runtime,
            manifest,
            path,
            max_parallel=4,
            retry_limits={"review": 2},
        )

        assert second.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert eng.store.get_work_item(item.id).worker_handoff is None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments + 1

    @pytest.mark.parametrize(
        "report_ref",
        [
            pytest.param(None, id="legacy-inline-only"),
            pytest.param({
                "attachment_id": "",
                "sha256": "a" * 64,
            }, id="wrong-attachment"),
            pytest.param({
                "attachment_id": "review-1",
                "sha256": "not-a-sha",
            }, id="wrong-sha"),
        ],
    )
    def test_review_nits_missing_exact_report_ref_has_zero_side_effects(
        self, tmp_path, monkeypatch, report_ref,
    ):
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        report = _review_report(
            item, "pass-with-nits", nits=["follow up"])
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="pass-with-nits",
            review_report=report,
        )
        current = eng.store.get_work_item(item.id)
        current.review_report_ref = report_ref
        before = {
            "status": current.status,
            "phase": current.phase,
            "review_bounce": current.bounces.review,
            "review_verdict": current.review_verdict,
            "review_report": current.review_report,
            "worker_handoff": current.worker_handoff,
            "assignments": list(eng.store.assign_log),
            "runs": list(eng.runtime.list_runs(item.id)),
        }

        def unexpected_write(*_args, **_kwargs):
            pytest.fail("invalid review-nits source must not write or dispatch")

        monkeypatch.setattr(
            eng.store, "update_work_item_metadata", unexpected_write)
        monkeypatch.setattr(eng.store, "reset_review", unexpected_write)
        monkeypatch.setattr(eng.store, "update_status", unexpected_write)
        monkeypatch.setattr(eng.store, "assign_work_item", unexpected_write)
        monkeypatch.setattr(eng.runtime, "wake", unexpected_write)
        monkeypatch.setattr(eng.runtime, "list_runs", unexpected_write)

        with pytest.raises(PlatformError, match="review report ref|feedback"):
            loop._dispatch_worker_handoff(
                eng.store,
                eng.runtime,
                manifest,
                "a",
                review_bounce=1,
                gate="review-nits",
            )

        after = eng.store.get_work_item(item.id)
        assert after.status is before["status"]
        assert after.phase is before["phase"]
        assert after.bounces.review == before["review_bounce"]
        assert after.review_verdict == before["review_verdict"]
        assert after.review_report == before["review_report"]
        assert after.worker_handoff is before["worker_handoff"]
        assert eng.store.assign_log == before["assignments"]

    def test_review_continuation_authorizes_pass_with_nits_rework(
        self, tmp_path,
    ):
        """显式 continuation 扩大绝对上限后允许 final nits 再返工一轮。"""
        from omac.engines import create_engine

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        report = _review_report(
            item, "pass-with-nits", nits=["authorized follow up"])
        eng.store.update_work_item_metadata(
            item.id,
            review_bounce=9,
            review_continuation={
                "schema": "omac.review-continuation/v1",
                "stage": "develop",
                "mode": "producer-rework",
                "authorized_through_round": 10,
                "decision_count": 1,
                "reason": "operator approved one additional review round",
            },
            review_verdict="pass-with-nits",
            review_report=report,
            review_report_source=yaml.safe_dump(report),
        )
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_subject_digest=review_subject_digest(current, 10),
        )

        result = tick(
            eng.store,
            eng.runtime,
            manifest,
            path,
            max_parallel=4,
            retry_limits={"review": 9},
        )

        got = eng.store.get_work_item(item.id)
        assert result.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert got.status == WorkItemStatus.IN_PROGRESS
        assert got.bounces.review == 10
        assert got.review_verdict is None

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

        def assign(item_id, assignee, role, **kwargs):
            if role == "reviewer":
                current = eng.store.get_work_item(item_id)
                assert current.phase == TaskPhase.REVIEW
                assert current.status == WorkItemStatus.IN_REVIEW
                assert current.review_report is None
                assert current.review_ledger is old_ledger
                events.append("assign")
            return original_assign(item_id, assignee, role, **kwargs)

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

    def test_multica_cleared_decision_resumes_worker_handoff_after_restart(
        self, tmp_path, monkeypatch,
    ):
        """Multica 的 {} 墓碑必须视为已 reset，重启后直接继续唯一派发。"""
        from omac.engines import create_engine
        from omac.engines.multica import MulticaStore

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        eng.store.update_work_item_metadata(
            item.id, review_obligations=build_review_obligations(item))
        eng.store.update_work_item_metadata(
            item.id, review_report=_review_report(item, "reject"))

        raw_cleared = {
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "status": "in_progress",
            "metadata": {
                "dag_key": item.dag_key,
                "kind": "develop",
                "phase": "authoring",
                "decision_required": "{}",
            },
        }
        multica = MulticaStore(EngineConfig(
            engine_type="multica", workspace_id="ws"))
        original_reset = eng.store.reset_review
        reset_calls = 0
        crashed = False

        def reset_review(item_id):
            nonlocal reset_calls, crashed
            reset_calls += 1
            original_reset(item_id)
            current = eng.store.get_work_item(item_id)
            current.decision_required = multica._issue_to_control_projection(
                raw_cleared, "ws").work_item.decision_required
            if not crashed:
                crashed = True
                raise RuntimeError("simulated crash after multica reset")

        runs = list(eng.runtime.list_runs(item.id))
        events = []
        worker_id = eng.store.resolve_agent_id("alice")

        def assign(_item_id, _assignee, role):
            assert role == "worker"
            events.append("assign")

        def wake(_item_id, _agent, role):
            assert role == "worker"
            events.append("wake")
            runs.append(AgentRunObservation(
                id="run-worker-after-reset", kind="direct", status="running",
                agent_id=worker_id,
            ))

        monkeypatch.setattr(eng.store, "reset_review", reset_review)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)
        monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))
        monkeypatch.setattr(eng.runtime, "wake", wake)
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

        with pytest.raises(RuntimeError, match="after multica reset"):
            tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        persisted = load_manifest(path)
        first = tick(eng.store, eng.runtime, persisted, path, max_parallel=4)
        second = tick(eng.store, eng.runtime, persisted, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert second.state == "running"
        assert reset_calls == 1
        assert events == ["assign", "wake"]
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.decision_required is None
        assert recovered.worker_handoff is not None
        assert recovered.worker_handoff.target_run_id == "run-worker-after-reset"

    def test_worker_handoff_resets_residual_reviewer_baseline_before_assign(
        self, tmp_path, monkeypatch,
    ):
        eng, manifest, _path, item, _agent_id = _transient_worker_handoff_fixture(
            tmp_path)
        current = eng.store.get_work_item(item.id)
        baseline_run_ids = tuple(sorted(
            run.id for run in eng.runtime.list_runs(item.id)
            if run.kind == "direct"
        ))
        intent = replace(
            current.worker_handoff,
            target_run_id=None,
            baseline_direct_run_ids=baseline_run_ids,
        )
        eng.store.update_work_item_metadata(
            item.id,
            worker_handoff=intent,
            reviewer_run_baseline=ReviewerRunBaseline(
                schema="omac.reviewer-run-baseline/v1",
                subject_digest="old-subject",
                target_reviewer="bob",
                target_agent_id="reviewer-1",
                cutoff_created_at="2026-08-01T00:00:00Z",
                generation="review-old",
                attempt=1,
            ),
        )
        reset_calls = 0
        original_reset = eng.store.reset_review

        class AssignmentReached(Exception):
            pass

        def reset_review(item_id):
            nonlocal reset_calls
            reset_calls += 1
            original_reset(item_id)

        def assign(item_id, assignee, role):
            assert eng.store.get_work_item(item_id).reviewer_run_baseline is None
            raise AssignmentReached

        monkeypatch.setattr(eng.store, "reset_review", reset_review)
        monkeypatch.setattr(eng.store, "assign_work_item", assign)

        with pytest.raises(AssignmentReached):
            loop._dispatch_worker_handoff(
                eng.store, eng.runtime, manifest, "a")

        assert reset_calls == 1

    def test_worker_handoff_final_check_blocks_baseline_drift_before_assign(
        self, tmp_path, monkeypatch,
    ):
        eng, manifest, _path, item, _agent_id = _transient_worker_handoff_fixture(
            tmp_path)
        current = eng.store.get_work_item(item.id)
        intent = replace(
            current.worker_handoff,
            target_run_id=None,
            baseline_direct_run_ids=tuple(sorted(
                run.id for run in eng.runtime.list_runs(item.id)
                if run.kind == "direct"
            )),
        )
        eng.store.update_work_item_metadata(item.id, worker_handoff=intent)
        original_update = eng.store.update_work_item_metadata
        drifted = False

        def update(item_id, **metadata):
            nonlocal drifted
            result = original_update(item_id, **metadata)
            if "description" in metadata and not drifted:
                drifted = True
                original_update(
                    item_id,
                    reviewer_run_baseline=ReviewerRunBaseline(
                        schema="omac.reviewer-run-baseline/v1",
                        subject_digest="drift-subject",
                        target_reviewer="bob",
                        target_agent_id="reviewer-1",
                        cutoff_created_at="2026-08-01T00:00:00Z",
                        generation="review-drift",
                        attempt=1,
                    ),
                )
            return result

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(
            eng.store, "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "residual reviewer baseline must block assignment"),
        )
        monkeypatch.setattr(
            eng.runtime, "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "residual reviewer baseline must block wake"),
        )
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

        result = loop._dispatch_worker_handoff(
            eng.store, eng.runtime, manifest, "a")

        assert result.state == "pending-preparation"
        assert eng.store.get_work_item(item.id).reviewer_run_baseline is not None

    @pytest.mark.parametrize(
        ("key", "field"),
        [
            (MACHINE_FEEDBACK_REF_KEY, "machine_feedback_ref"),
            (REVIEW_REPORT_REF_KEY, "review_report_ref"),
        ],
    )
    @pytest.mark.parametrize("raw", [[], ["fact"]], ids=["empty-list", "list"])
    def test_multica_invalid_review_ref_projection_is_not_clear(
        self, key, field, raw,
    ):
        from omac.engines.multica import MulticaStore

        store = MulticaStore(EngineConfig(
            engine_type="multica", workspace_id="ws"))
        item = store._issue_to_control_projection({
            "id": "issue-1",
            "title": "review",
            "description": "review",
            "status": "in_progress",
            "metadata": {
                "dag_key": "develop-a",
                "kind": "develop",
                "phase": "authoring",
                key: raw,
            },
        }, "ws").work_item

        assert getattr(item, field) is not None
        assert not loop._review_projection_is_clear(item)

    @pytest.mark.parametrize(
        ("key", "field"),
        [
            (MACHINE_FEEDBACK_REF_KEY, "machine_feedback_ref"),
            (REVIEW_REPORT_REF_KEY, "review_report_ref"),
        ],
    )
    @pytest.mark.parametrize("raw", [[], ["fact"]], ids=["empty-list", "list"])
    def test_worker_handoff_final_check_blocks_invalid_review_ref(
        self, tmp_path, monkeypatch, key, field, raw,
    ):
        from omac.engines.multica import MulticaStore

        eng, manifest, _path, item, _agent_id = _transient_worker_handoff_fixture(
            tmp_path)
        current = eng.store.get_work_item(item.id)
        intent = replace(
            current.worker_handoff,
            target_run_id=None,
            baseline_direct_run_ids=tuple(sorted(
                run.id for run in eng.runtime.list_runs(item.id)
                if run.kind == "direct"
            )),
        )
        eng.store.update_work_item_metadata(item.id, worker_handoff=intent)
        multica = MulticaStore(EngineConfig(
            engine_type="multica", workspace_id="ws"))
        projected = multica._issue_to_control_projection({
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "status": "in_progress",
            "metadata": {
                "dag_key": item.dag_key,
                "kind": "develop",
                "phase": "authoring",
                key: raw,
            },
        }, "ws").work_item
        original_update = eng.store.update_work_item_metadata
        drifted = False

        def update(item_id, **metadata):
            nonlocal drifted
            result = original_update(item_id, **metadata)
            if "description" in metadata and not drifted:
                drifted = True
                setattr(eng.store.get_work_item(item_id), field, getattr(projected, field))
            return result

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)
        monkeypatch.setattr(
            eng.store, "assign_work_item",
            lambda *_args, **_kwargs: pytest.fail(
                "invalid review ref must block assignment"),
        )
        monkeypatch.setattr(
            eng.runtime, "wake",
            lambda *_args, **_kwargs: pytest.fail(
                "invalid review ref must block wake"),
        )
        monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

        result = loop._dispatch_worker_handoff(
            eng.store, eng.runtime, manifest, "a")

        assert result.state == "pending-preparation"
        assert getattr(eng.store.get_work_item(item.id), field) is not None

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
        report = _review_report(
            item,
            verdict,
            nits=["follow up"] if verdict == "pass-with-nits" else None,
        )
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict=verdict,
            review_report=report,
            review_report_source=(
                yaml.safe_dump(report)
                if verdict == "pass-with-nits" else None
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
        if verdict == "pass-with-nits":
            assert crashed_item.worker_handoff.source_review_verdict == (
                "pass-with-nits")
            assert crashed_item.worker_handoff.source_review_feedback[
                "verdict"] == verdict
            assert crashed_item.worker_handoff.source_review_feedback[
                "nits"] == ["follow up"]
        else:
            assert crashed_item.worker_handoff.source_review_feedback is None

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
        if verdict == "pass-with-nits":
            assert recovered.worker_handoff.source_review_feedback[
                "verdict"] == verdict
        else:
            assert recovered.worker_handoff.source_review_feedback is None
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

        def assign(item_id, assignee, role, **kwargs):
            if role == "worker":
                pytest.fail("new Worker delivery must not resume stale handoff")
            return original_assign(item_id, assignee, role, **kwargs)

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

    def test_review_verdict_handoff_uses_hydrated_reconcile_projection(
        self, tmp_path, monkeypatch,
    ):
        """Review subject validation must use the already hydrated delivery snapshot."""
        from omac.engines import create_engine
        from types import SimpleNamespace

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_obligations=build_review_obligations(current),
        )
        current = eng.store.get_work_item(item.id)
        eng.store.update_work_item_metadata(
            item.id,
            review_report=_review_report(current, "reject"),
        )
        current = eng.store.get_work_item(item.id)
        assert current.verification is not None
        observed = WorkItemControlProjection(current)
        calls = []

        def dispatch(_store, _runtime, _manifest, _key, **kwargs):
            projection = kwargs.get("projection")
            assert projection is observed
            assert projection.work_item.verification is current.verification
            calls.append(True)
            return SimpleNamespace(state="waiting")

        monkeypatch.setattr(loop, "_dispatch_worker_handoff", dispatch)

        loop.collect_results(
            eng.store,
            eng.runtime,
            manifest,
            path,
            observations={"a": observed},
        )

        assert calls == [True]

    def test_review_handoff_persistent_stale_source_fails_before_writes(
        self, tmp_path, monkeypatch,
    ):
        """A second stale observation remains a hard fail-closed boundary."""
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        stale = replace(
            current,
            bounces=replace(
                current.bounces,
                review=current.bounces.review + 1,
            ),
        )
        stale_projection = WorkItemControlProjection(stale)
        monkeypatch.setattr(
            eng.store,
            "observe_work_item_control",
            lambda _item_id: stale_projection,
        )
        for target, name in (
            (eng.store, "update_work_item_metadata"),
            (eng.store, "update_status"),
            (eng.store, "assign_work_item"),
            (eng.runtime, "wake"),
        ):
            monkeypatch.setattr(
                target,
                name,
                lambda *_args, _name=name, **_kwargs: pytest.fail(
                    f"persistent stale source must not call {_name}"),
            )

        with pytest.raises(PlatformError, match="source is stale"):
            loop._dispatch_worker_handoff(
                eng.store,
                eng.runtime,
                manifest,
                "a",
                review_bounce=current.bounces.review + 1,
                gate="review",
                projection=stale_projection,
            )

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

        def assign(item_id, assignee, role, **kwargs):
            nonlocal worker_assignments
            result = original_assign(item_id, assignee, role, **kwargs)
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

    def test_delivery_candidate_change_defers_without_stopping_runner(
        self, tmp_path, monkeypatch,
    ):
        """并发新交付只延后本节点，下一轮按最新事实重新封存。"""
        from omac.engines import create_engine
        from omac.engines.models import AgentRunObservation
        from omac.pipeline import loop as loop_module

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

        original_match = loop_module._control_matches_handoff_candidate
        observations = 0

        def candidate_changes_once(current, current_intent, identity):
            nonlocal observations
            observations += 1
            if observations == 1:
                return False
            return original_match(current, current_intent, identity)

        monkeypatch.setattr(
            loop_module,
            "_control_matches_handoff_candidate",
            candidate_changes_once,
        )
        reviewer_assignments = len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ])

        first = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        deferred = eng.store.get_work_item(item.id)
        assert first.state == "running"
        assert manifest.nodes["a"].status == "in_progress"
        assert deferred.worker_handoff is not None
        assert deferred.delivery_identity is None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments

        second = tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        recovered = eng.store.get_work_item(item.id)
        assert second.state == "running"
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.worker_handoff is None
        assert recovered.delivery_identity is not None
        assert len([
            entry for entry in eng.store.assign_log if entry[2] == "reviewer"
        ]) == reviewer_assignments + 1

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
            "HEAD" in failed["reason"] or "head" in failed["reason"]
            for failed in result.report["failed_nodes"]
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

    def test_review_handoff_with_zero_bounce_has_no_lifecycle_side_effects(
        self, tmp_path, monkeypatch,
    ):
        from omac.core.taskmeta import WorkerHandoffIntent
        from omac.engines import create_engine
        from omac.errors import PlatformError

        eng = create_engine("mock", _config(MOCK_AUTO_COMPLETE="false"))
        path = str(tmp_path / "m.yaml")
        manifest, eng, item = self._setup_reject_node(eng, path)
        current = eng.store.get_work_item(item.id)
        intent = WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="review",
            source_review_subject_digest=current.review_subject_digest,
            source_review_round=1,
            target_review_bounce=0,
            generation="handoff-malformed-zero-review-bounce",
            target_agent_id=eng.store.resolve_agent_id("alice"),
            baseline_direct_run_ids=tuple(sorted(
                run.id for run in eng.runtime.list_runs(item.id)
                if run.kind == "direct"
            )),
            baseline_verification_attachment_id=str(
                (current.verification_ref or {}).get("attachment_id") or ""
            ) or None,
            target_worker_bounce=current.bounces.worker,
        )
        eng.store.update_work_item_metadata(item.id, worker_handoff=intent)

        for target, name in (
            (eng.store, "reset_review"),
            (eng.store, "update_status"),
            (eng.store, "assign_work_item"),
            (eng.runtime, "wake"),
        ):
            monkeypatch.setattr(
                target,
                name,
                lambda *_args, _name=name, **_kwargs: pytest.fail(
                    f"malformed handoff must not call {_name}"),
            )

        with pytest.raises(PlatformError, match="causal identity"):
            loop._dispatch_worker_handoff(
                eng.store, eng.runtime, manifest, "a")

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

    def test_stale_pass_without_sealed_identity_fails_closed(
        self, tmp_path, monkeypatch,
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
            review_verdict="pass",
            review_report=_review_report(item, "pass"),
            phase=TaskPhase.AUTHORING,
        )
        self._submit_revision(eng, item)
        eng.store.update_work_item_metadata(item.id, delivery_identity={})
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
        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert recovered.status == WorkItemStatus.BLOCKED
        assert recovered.review_verdict is None
        assert recovered.review_subject_digest != stale_subject
        assert recovered.decision_required["reason_code"] == (
            "reviewer-run-baseline-unavailable")

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
        eng.store.assign_work_item(
            item.id, "bob", "reviewer", start_run=False)
        eng.runtime.wake(item.id, "bob", "reviewer")
        active_run = eng.runtime.list_runs(item.id)[-1]
        eng.store.update_work_item_metadata(
            item.id,
            reviewer_run_baseline=replace(
                current.reviewer_run_baseline,
                target_run_id=active_run.id,
            ),
        )
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
        original_update = eng.store.update_work_item_metadata

        def update(item_id, **kwargs):
            if "description" in kwargs:
                pytest.fail("active reviewer must not refresh issue body")
            return original_update(item_id, **kwargs)

        monkeypatch.setattr(eng.store, "update_work_item_metadata", update)

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
        report = _review_report(
            item, "pass-with-nits", nits=["建议后续优化"])
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="pass-with-nits",
            review_report=report,
            review_report_source=yaml.safe_dump(report),
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
            if i == 2:
                ledger = self._stalled_review_ledger()
                ledger["blockers"][0]["classification"] = "deeper"
                ledger["cycles"][-1]["unchanged_count"] = 0
                eng.store.update_work_item_metadata(
                    item.id, review_ledger=ledger)
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
        import hashlib

        from omac.core.manifest import Manifest, Node, set_node
        contract = self._simple_contract()
        node = Node(id=key, worker=worker, reviewer=reviewer, title=key,
                    description=f"Task {key}", contract=contract)
        manifest = Manifest(meta={"workspace_id": "ws"}, nodes={node.id: node})
        save_manifest(manifest, fpath)

        tick(eng.store, eng.runtime, manifest, fpath, max_parallel=4)
        item = eng.store.get_work_item(manifest.nodes[key].work_item_id)
        eng.store.set_node_contract(item.id, contract)
        verification = {
            "commands": [_business_command()],
            "integration_gates": [{
                "name": "setup-gate",
                "commands": [_business_command()],
            }],
            "pr_base": "main",
            "coverage": 90,
        }
        pr_url = f"https://mock.example.com/pr/{item.id}"
        eng.store.update_work_item_metadata(
            item.id,
            artifacts={
                "pr_url": pr_url,
                "head_sha": hashlib.sha256(pr_url.encode("utf-8")).hexdigest(),
            },
            verification=verification,
            verification_source=yaml.safe_dump(verification),
        )
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

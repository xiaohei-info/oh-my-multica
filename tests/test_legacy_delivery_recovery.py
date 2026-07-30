"""Legacy completed Worker delivery 的一次性 Controller 封存恢复。"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
import yaml

from omac.core.manifest import Contract, Manifest, Node, save_manifest
from omac.core.taskmeta import TaskPhase
from omac.engines import create_engine
from omac.engines.models import (
    AgentRunObservation,
    EngineConfig,
    PullRequestCheckResult,
    PullRequestReadiness,
    WorkItemStatus,
)
from omac.errors import PlatformError
from omac.pipeline.loop import tick


def _config():
    return EngineConfig(
        engine_type="mock",
        workspace_id="ws",
        extra={
            "MOCK_AUTO_COMPLETE": "false",
            "MOCK_AUTO_COMPLETE_DELAY": "0",
            "MOCK_AUTO_MERGE_ON_SUCCESS": "true",
        },
    )


def _contract():
    return Contract(
        objective="deliver the platform contract",
        acceptance=["works"],
        non_goals=["no scope creep"],
        verification_commands=["pytest -q"],
        pr_base="main",
        coverage_gate=0,
    )


def _verification():
    return {
        "commands": [{
            "cmd": "pytest -q",
            "exit_code": 0,
            "business_tests": [{
                "acceptance": "works",
                "test": "tests/test_platform.py::test_platform_contract",
            }],
        }],
        "integration_gates": [{"name": "legacy-gate", "commands": []}],
        "pr_base": "main",
        "coverage": 100,
    }


def _legacy_completed_delivery(tmp_path, *, manifest_status="in_review"):
    """构造 AITEAM-834 形状：旧交付已完成，但没有 identity/handoff。"""
    engine = create_engine("mock", _config())
    contract = _contract()
    node = Node(
        id="platform-release-evidence-contract",
        worker="alice",
        reviewer="bob",
        title="Platform release evidence contract",
        description="Implement the platform release evidence contract",
        contract=contract,
    )
    manifest = Manifest(meta={"workspace_id": "ws"}, nodes={node.id: node})
    path = str(tmp_path / "open-agent-cluster.yaml")
    save_manifest(manifest, path)

    tick(engine.store, engine.runtime, manifest, path, max_parallel=4)
    item = engine.store.get_work_item(node.work_item_id)
    engine.store.set_node_contract(item.id, contract)
    pr_url = "https://mock.example.com/pr/24"
    verification = _verification()
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={
            "pr_url": pr_url,
        },
        verification=verification,
        verification_source=yaml.safe_dump(verification),
        phase=TaskPhase.AUTHORING,
        review_bounce=1,
        review_ledger={
            "schema": "omac.review-ledger/v1",
            "cycles": [{
                "round": 1,
                "subject_digest": "historical-review-subject",
                "verdict": "reject",
            }],
            "blockers": [],
        },
    )
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    engine.store.clear_assignment(item.id)
    # 复现旧 Runner 在 rerun 前把平台投影改回 in_progress、随后退出的现场。
    engine.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)

    current = engine.store.get_work_item(item.id)
    assert current.delivery_identity is None
    assert current.worker_handoff is None
    assert current.status is WorkItemStatus.IN_PROGRESS
    assert current.phase is TaskPhase.AUTHORING
    assert len(engine.runtime.list_runs(item.id)) == 1
    manifest.nodes[node.id].status = manifest_status
    save_manifest(manifest, path)
    return engine, manifest, path, current


def test_first_worker_delivery_keeps_existing_protocol(tmp_path, monkeypatch):
    engine, manifest, path, item = _legacy_completed_delivery(
        tmp_path, manifest_status="in_progress")
    current = engine.store.get_work_item(item.id)
    current.bounces.review = 0
    current.review_ledger = None
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    wakes = _reviewer_wakes(engine, monkeypatch)

    result = tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    recovered = engine.store.get_work_item(item.id)
    assert result.state == "running"
    assert recovered.delivery_identity is None
    assert recovered.phase is TaskPhase.REVIEW
    assert [role for _, _, role in wakes] == ["reviewer"]


def _reviewer_wakes(engine, monkeypatch):
    calls = []
    original = engine.runtime.wake

    def wake(item_id, agent, role):
        calls.append((item_id, agent, role))
        return original(item_id, agent, role)

    monkeypatch.setattr(engine.runtime, "wake", wake)
    return calls


def test_legacy_completed_delivery_is_sealed_then_uses_normal_review_path(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    wakes = _reviewer_wakes(engine, monkeypatch)

    result = tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    recovered = engine.store.get_work_item(item.id)
    assert result.state == "running"
    assert manifest.nodes[item.dag_key].status == "in_review"
    assert recovered.delivery_identity is not None
    assert recovered.delivery_identity.worker == "alice"
    assert recovered.delivery_identity.run_id == "mock-run-1"
    assert recovered.delivery_identity.handoff_generation.startswith("legacy-")
    assert recovered.artifacts["head_sha"] == hashlib.sha256(
        recovered.artifacts["pr_url"].encode()).hexdigest()
    assert recovered.phase is TaskPhase.REVIEW
    assert [role for _, _, role in wakes] == ["reviewer"]


def test_legacy_seal_is_idempotent_across_reconcile_and_manifest_recovery(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    wakes = _reviewer_wakes(engine, monkeypatch)

    first = tick(engine.store, engine.runtime, manifest, path, max_parallel=4)
    persisted_identity = engine.store.get_work_item(item.id).delivery_identity
    second = tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    assert first.state == "running"
    assert second.state == "running"
    assert persisted_identity is not None
    assert engine.store.get_work_item(item.id).delivery_identity == persisted_identity
    assert [role for _, _, role in wakes] == ["reviewer"]
    assert len(engine.runtime.list_runs(item.id)) == 2


def test_legacy_completed_delivery_still_runs_ci_before_reviewer(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(
        tmp_path, manifest_status="in_progress")
    wakes = _reviewer_wakes(engine, monkeypatch)
    ci_calls = 0

    def fail_ci(*_args, **_kwargs):
        nonlocal ci_calls
        ci_calls += 1
        return PullRequestCheckResult(False, 1, "legacy CI failed")

    monkeypatch.setattr(engine.store, "check_pull_request", fail_ci)

    result = tick(
        engine.store,
        engine.runtime,
        manifest,
        path,
        max_parallel=4,
        retry_limits={"ci": 0},
        config={"ci": {"check_command": "gh pr checks {pr_url}"}},
    )

    assert result.state == "needs_decision"
    assert manifest.nodes[item.dag_key].status == "blocked"
    assert engine.store.get_work_item(item.id).delivery_identity is not None
    assert ci_calls == 1
    assert not [role for _, _, role in wakes if role == "reviewer"]


@pytest.mark.parametrize("run_status", ["running", "queued"])
def test_legacy_upgrade_fails_closed_when_any_run_is_active(
    tmp_path, monkeypatch, run_status,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [
        AgentRunObservation(
            id="mock-run-1", kind="direct", status="completed",
            agent_id="mock-agent-alice",
        ),
        AgentRunObservation(
            id="active-run", kind="direct", status=run_status,
            agent_id="mock-agent-bob",
        ),
    ])
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail("legacy ambiguity must not wake"),
    )

    with pytest.raises(PlatformError, match="active|Run"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    assert engine.store.get_work_item(item.id).delivery_identity is None


def test_legacy_upgrade_fails_closed_with_multiple_target_worker_runs(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    current = engine.store.get_work_item(item.id)
    current.verification_ref["task_id"] = None
    current.verification_ref["created_at"] = "2026-07-30T01:31:42Z"
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [
        AgentRunObservation(
            id="worker-old", kind="direct", status="completed",
            agent_id="mock-agent-alice",
            created_at="2026-07-30T01:20:00Z",
            updated_at="2026-07-30T01:40:00Z",
        ),
        AgentRunObservation(
            id="worker-current", kind="direct", status="completed",
            agent_id="mock-agent-alice",
            created_at="2026-07-30T01:30:00Z",
            updated_at="2026-07-30T01:35:00Z",
        ),
    ])

    with pytest.raises(PlatformError, match="unique|ambiguous|multiple"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    assert engine.store.get_work_item(item.id).delivery_identity is None


def test_legacy_upgrade_uses_attachment_to_select_one_of_historical_worker_runs(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [
        AgentRunObservation(
            id="worker-old", kind="direct", status="completed",
            agent_id="mock-agent-alice",
            created_at="2026-07-29T19:28:09Z",
            updated_at="2026-07-29T19:49:17Z",
        ),
        AgentRunObservation(
            id="mock-run-1", kind="direct", status="completed",
            agent_id="mock-agent-alice",
            created_at="2026-07-30T01:23:28Z",
            updated_at="2026-07-30T01:31:49Z",
        ),
    ])
    current = engine.store.get_work_item(item.id)
    current.verification_ref["task_id"] = None
    current.verification_ref["created_at"] = "2026-07-30T01:31:42Z"

    tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    assert engine.store.get_work_item(item.id).delivery_identity.run_id == "mock-run-1"


def test_legacy_upgrade_fails_closed_for_wrong_attachment_uploader(
    tmp_path,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    current = engine.store.get_work_item(item.id)
    current.verification_ref["uploader_id"] = "mock-agent-eve"

    with pytest.raises(PlatformError, match="causal|uploader|Worker Run"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)


def test_legacy_upgrade_fails_closed_for_wrong_attachment_task(
    tmp_path,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    engine.store.get_work_item(item.id).verification_ref["task_id"] = "other-run"

    with pytest.raises(PlatformError, match="causal|Worker Run"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)


def test_legacy_upgrade_fails_closed_for_attachment_outside_run_window(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    run = engine.runtime.list_runs(item.id)[0]
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [
        replace(
            run,
            created_at="2026-07-30T01:30:00Z",
            updated_at="2026-07-30T01:31:49Z",
        )
    ])
    engine.store.get_work_item(item.id).verification_ref["created_at"] = (
        "2026-07-30T01:32:00Z")

    with pytest.raises(PlatformError, match="causal|Worker Run"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)


def test_legacy_upgrade_fails_closed_for_attachment_sha_tampering(
    tmp_path,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    engine.store.get_work_item(item.id).verification_ref["sha256"] = "0" * 64

    with pytest.raises(PlatformError, match="digest|SHA"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)


def test_legacy_upgrade_fails_closed_for_remote_pr_head_drift(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    observed = 0
    submitted_head = hashlib.sha256(
        engine.store.get_work_item(item.id).artifacts["pr_url"].encode()
    ).hexdigest()

    def readiness(_pr_url):
        nonlocal observed
        observed += 1
        return PullRequestReadiness(
            is_draft=False,
            state="OPEN",
            head_sha=submitted_head if observed == 1 else "new-head",
        )

    monkeypatch.setattr(
        engine.store,
        "read_pull_request_readiness",
        readiness,
    )

    with pytest.raises(PlatformError, match="HEAD|head|sealed identity"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)


def test_legacy_upgrade_fails_closed_without_verification(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    current = engine.store.get_work_item(item.id)
    current.verification = None
    current.verification_ref = None
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail("incomplete legacy delivery must not rerun"),
    )

    with pytest.raises(PlatformError, match="incomplete|verification"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)


def test_legacy_upgrade_fails_closed_for_stale_review_projection(
    tmp_path,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    current = engine.store.get_work_item(item.id)
    current.review_verdict = "reject"
    current.review_subject_digest = "stale-subject"

    with pytest.raises(PlatformError, match="review projection|review"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)


def test_crash_after_legacy_seal_before_reviewer_is_restart_safe(
    tmp_path, monkeypatch,
):
    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    original_update = engine.store.update_work_item_metadata
    crashed = False

    def update(item_id, **metadata):
        nonlocal crashed
        result = original_update(item_id, **metadata)
        if metadata.get("delivery_identity") and not crashed:
            crashed = True
            raise RuntimeError("crash after legacy seal")
        return result

    monkeypatch.setattr(engine.store, "update_work_item_metadata", update)
    with pytest.raises(RuntimeError, match="after legacy seal"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    sealed = engine.store.get_work_item(item.id)
    assert sealed.delivery_identity is not None
    assert sealed.phase is TaskPhase.AUTHORING
    assert len(engine.runtime.list_runs(item.id)) == 1

    monkeypatch.setattr(engine.store, "update_work_item_metadata", original_update)
    wakes = _reviewer_wakes(engine, monkeypatch)
    recovered = tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    assert recovered.state == "running"
    assert manifest.nodes[item.dag_key].status == "in_review"
    assert [role for _, _, role in wakes] == ["reviewer"]


def test_crash_before_manifest_save_does_not_duplicate_reviewer(
    tmp_path, monkeypatch,
):
    from omac.core.manifest import load_manifest
    from omac.pipeline import loop

    engine, manifest, path, item = _legacy_completed_delivery(tmp_path)
    wakes = _reviewer_wakes(engine, monkeypatch)
    original_save = loop.save_manifest
    crashed = False

    def save(current, current_path):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("crash before manifest save")
        return original_save(current, current_path)

    monkeypatch.setattr(loop, "save_manifest", save)
    with pytest.raises(RuntimeError, match="before manifest save"):
        tick(engine.store, engine.runtime, manifest, path, max_parallel=4)

    assert engine.store.get_work_item(item.id).delivery_identity is not None
    assert [role for _, _, role in wakes] == ["reviewer"]

    monkeypatch.setattr(loop, "save_manifest", original_save)
    recovered_manifest = load_manifest(path)
    result = tick(
        engine.store, engine.runtime, recovered_manifest, path, max_parallel=4)

    assert result.state == "running"
    assert recovered_manifest.nodes[item.dag_key].status == "in_review"
    assert [role for _, _, role in wakes] == ["reviewer"]

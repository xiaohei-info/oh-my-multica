"""cli.node: show / retry / abandon —— exit 20 后的显式决策工具(§7.5)。"""
from copy import deepcopy
import hashlib
import os

import pytest
import yaml

from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import Contract, load_manifest, save_manifest, Manifest, Node


def _write_manifest(tmp_path, nodes_yaml):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump({"meta": {}, "nodes": nodes_yaml}, allow_unicode=True))
    return str(p)


def _pass_with_nits_fixture(tmp_path, monkeypatch):
    """A blocked develop review with a controller-sealed current delivery."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.core.review_convergence import review_subject_digest
    from omac.core.taskmeta import (
        DeliveryIdentity, DELIVERY_IDENTITY_SCHEMA, TaskKind, TaskPhase,
    )
    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", reviewer="alice",
        kind=TaskKind.DEVELOP,
        initial_status=WorkItemStatus.BLOCKED,
    )
    pr_url = "https://example.test/pr/1"
    head_sha = hashlib.sha256(pr_url.encode()).hexdigest()
    verification = {
        "commands": [], "integration_gates": [], "pr_base": "main",
        "coverage": 100,
    }
    report = {
        "review_goals": ["verify delivery"],
        "full_review_completed": True,
        "diff_reviewed": True,
        "tests_rerun": True,
        "coverage_checked": True,
        "acceptance_mapping": [],
        "blockers": [],
        "nits": ["non-blocking follow-up"],
    }
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        artifacts={"pr_url": pr_url, "head_sha": head_sha},
        verification=verification,
        verification_source=yaml.safe_dump(verification),
        review_verdict="pass-with-nits",
        review_report=report,
        review_report_source=yaml.safe_dump(report),
    )
    current = engine.store.get_work_item(item.id)
    ref = current.verification_ref
    run_id = "worker-run-1"
    engine.store.update_work_item_metadata(
        item.id,
        delivery_identity=DeliveryIdentity(
            schema=DELIVERY_IDENTITY_SCHEMA,
            handoff_generation="handoff-1",
            worker="bob",
            agent_id=engine.store.resolve_agent_id("bob"),
            run_id=run_id,
            pr_url=pr_url,
            pr_head_sha=head_sha,
            verification_sha256=ref["sha256"],
            verification_attachment_id=ref["attachment_id"],
            verification_comment_id=ref["comment_id"],
            verification_uploader_type="system",
            verification_task_id=run_id,
            verification_created_at=ref["created_at"],
        ),
    )
    current = engine.store.get_work_item(item.id)
    subject = review_subject_digest(current, 1)
    engine.store.update_work_item_metadata(
        item.id,
        review_subject_digest=subject,
        decision_required={
            "schema": "omac.decision-required/v1",
            "reason_code": "review-nits-acceptance-required",
            "kind": "develop",
            "phase": "review",
            "gate": "review-nits",
            "resume_issue_id": item.id,
            "node_id": "b",
            "review_subject_digest": subject,
            "review_report_ref": current.review_report_ref,
            "verdict": "pass-with-nits",
            "next_action": "omac node accept-nits manifest.yaml b",
        },
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "reviewer": "alice",
        "status": "blocked", "work_item_id": item.id,
        "recovery_marker": True,
    }])
    return engine, path, item.id


def _basic_nodes():
    return [
        {"id": "a", "worker": "alice", "status": "done",
         "work_item_id": "1"},
        {"id": "b", "worker": "bob", "blocked_by": ["a"], "status": "blocked",
         "work_item_id": "2",
         "contract": {"objective": "do b", "acceptance": ["b works"],
                      "verification_commands": ["pytest -q"],
                      "pr_base": "main", "coverage_gate": 90}},
        {"id": "c", "worker": "charlie", "blocked_by": ["b"], "status": "todo"},
    ]


def _old_worker_handoff(worker="bob"):
    return {
        "schema": "omac.worker-handoff/v1",
        "state": "pending",
        "target_worker": worker,
        "gate": "review",
        "source_review_subject_digest": "old-review-subject",
        "source_review_round": 1,
        "target_review_bounce": 1,
    }


def _delayed_reviewer_retry_fixture(tmp_path, monkeypatch):
    """Build the persisted state from a Reviewer Run hidden at first observe."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.core.review_convergence import review_subject_digest
    from omac.core.taskmeta import TaskPhase
    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", reviewer="alice")
    verification = {
        "commands": [],
        "integration_gates": [],
        "pr_base": "main",
        "coverage": 100,
    }
    verification_ref = {
        "attachment_id": "attachment-1",
        "comment_id": "comment-1",
        "created_at": "2026-08-02T13:30:00Z",
    }
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "head-1"},
        verification=verification,
        delivery_identity={
            "schema": "omac.delivery-identity/v1",
            "handoff_generation": "handoff-1",
            "worker": "bob",
            "agent_id": "agent-bob",
            "run_id": "run-worker",
            "pr_url": "https://example.test/pr/1",
            "pr_head_sha": "head-1",
            "verification_sha256": "sha-1",
            "verification_attachment_id": "attachment-1",
            "verification_comment_id": "comment-1",
            "verification_uploader_id": "agent-bob",
            "verification_uploader_type": "agent",
            "verification_created_at": "2026-08-02T13:30:00Z",
        },
    )
    current = engine.store.get_work_item(item.id)
    current.verification_ref = verification_ref
    subject = review_subject_digest(current, 1)
    reviewer_id = engine.store.resolve_agent_id("alice")
    report = {
        "full_review_completed": True,
        "blockers": ["repair the rejected delivery"],
        "nits": [],
    }
    engine.store.update_work_item_metadata(
        item.id,
        review_subject_digest=subject,
        review_verdict="reject",
        review_report=report,
        review_report_source=yaml.safe_dump(report),
        reviewer_run_baseline={
            "schema": "omac.reviewer-run-baseline/v1",
            "subject_digest": subject,
            "target_reviewer": "alice",
            "target_agent_id": reviewer_id,
            "cutoff_created_at": "2026-08-02T13:30:00Z",
            "generation": "review-hidden-run",
            "attempt": 1,
            "baseline_direct_run_ids": ["run-worker"],
            "target_run_id": None,
        },
        decision_required={
            "schema": "omac.decision-required/v1",
            "reason_code": "reviewer-run-baseline-unavailable",
            "kind": "develop",
            "phase": "review",
            "gate": "reviewer",
            "resume_issue_id": item.id,
            "node_id": "b",
            "failure_class": "unproven-reviewer-run-causality",
            "next_action": "omac node retry m.yaml b",
        },
    )
    engine.store.clear_assignment(item.id)
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "reviewer": "alice",
        "status": "blocked",
        "work_item_id": item.id,
    }])
    return engine, path, item.id, reviewer_id, subject, report


def _dispatch_unresolved_reviewer_retry_fixture(tmp_path, monkeypatch):
    """Build the persisted state of a continuation dispatch whose Run outcome
    was never proven (reason_code reviewer-run-dispatch-unresolved).

    与 `_delayed_reviewer_retry_fixture` 的区别:reviewer 尚未提交任何裁决
    (review_verdict/report 为空),baseline 是 attempt=2 的续跑世代且没有
    target Run——正是 `_retry_reviewer_attempt` 观察窗口空手而归后的现场。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.core.review_convergence import review_subject_digest
    from omac.core.taskmeta import TaskPhase
    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", reviewer="alice")
    verification = {
        "commands": [],
        "integration_gates": [],
        "pr_base": "main",
        "coverage": 100,
    }
    verification_ref = {
        "attachment_id": "attachment-1",
        "comment_id": "comment-1",
        "created_at": "2026-08-02T13:30:00Z",
    }
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "head-1"},
        verification=verification,
        delivery_identity={
            "schema": "omac.delivery-identity/v1",
            "handoff_generation": "handoff-1",
            "worker": "bob",
            "agent_id": "agent-bob",
            "run_id": "run-worker",
            "pr_url": "https://example.test/pr/1",
            "pr_head_sha": "head-1",
            "verification_sha256": "sha-1",
            "verification_attachment_id": "attachment-1",
            "verification_comment_id": "comment-1",
            "verification_uploader_id": "agent-bob",
            "verification_uploader_type": "agent",
            "verification_created_at": "2026-08-02T13:30:00Z",
        },
    )
    current = engine.store.get_work_item(item.id)
    current.verification_ref = verification_ref
    subject = review_subject_digest(current, 1)
    reviewer_id = engine.store.resolve_agent_id("alice")
    engine.store.update_work_item_metadata(
        item.id,
        review_subject_digest=subject,
        reviewer_run_baseline={
            "schema": "omac.reviewer-run-baseline/v1",
            "subject_digest": subject,
            "target_reviewer": "alice",
            "target_agent_id": reviewer_id,
            "cutoff_created_at": "2026-08-02T13:30:00Z",
            "generation": "review-dispatch-unresolved",
            "attempt": 2,
            "baseline_direct_run_ids": ["run-worker"],
            "target_run_id": None,
        },
        decision_required={
            "schema": "omac.decision-required/v1",
            "reason_code": "reviewer-run-dispatch-unresolved",
            "kind": "develop",
            "phase": "review",
            "gate": "reviewer",
            "resume_issue_id": item.id,
            "node_id": "b",
            "failure_class": "unproven-reviewer-run-causality",
            "next_action": "omac node retry m.yaml b --stage review",
        },
    )
    engine.store.clear_assignment(item.id)
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "reviewer": "alice",
        "status": "blocked",
        "work_item_id": item.id,
    }])
    return engine, path, item.id, reviewer_id, subject


# ---------------- show ----------------

def test_show_missing_manifest_is_validation(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main(["node", "show", "nope.yaml", "a"])
    assert code == exit_codes.VALIDATION
    assert "Manifest file not found" in capsys.readouterr().err


def test_show_unknown_node_is_validation(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    code = main(["node", "show", path, "ghost"])
    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "ghost" in err and "a" in err  # 报错即教学:列出可用节点


def test_show_json_contains_contract_and_evidence_fields(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 无引擎配置 → 降级 contract-only,evidence=null
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "show", path, "b", "--output", "json"]) == exit_codes.OK
    out = capsys.readouterr().out
    import json
    payload = json.loads(out)
    assert payload["node_key"] == "b"
    assert payload["status"] == "blocked"
    assert payload["contract"]["objective"] == "do b"
    assert payload["contract"]["acceptance"] == ["b works"]
    assert payload["contract"]["verification_commands"] == ["pytest -q"]
    assert payload["contract"]["coverage_gate"] == 90
    assert "evidence" in payload
    assert payload["rollback_count"] == 0


def test_show_reads_evidence_from_mock_engine(tmp_path, capsys, monkeypatch):
    """有 work_item_id 且引擎可解析时,show 从 store.get_work_item 取证据链。

    mock 引擎是内存态:注入同一个 store 实例,验证 show 的读证据逻辑。
    """
    import json
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    engine = create_engine("mock", EngineConfig("mock", "ws-1",
                                                extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    item.artifacts = {"pr_url": "https://mock.example.com/pr/x"}
    item.verification = {"commands": [{"cmd": "pytest -q", "exit_code": 0}]}
    item.review_verdict = "pass"

    # 让 node show 用同一个内存 store(模拟 multica 持久化)
    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)

    nodes = [{"id": "b", "worker": "bob", "status": "in_review",
              "work_item_id": item.id}]
    path = _write_manifest(tmp_path, nodes)

    assert main(["node", "show", path, "b", "--output", "json"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence"] is not None
    assert payload["evidence"]["work_item_id"] == item.id
    assert payload["evidence"]["artifacts"]["pr_url"] == "https://mock.example.com/pr/x"
    assert payload["evidence"]["review_verdict"] == "pass"


def test_show_degrades_when_engine_unresolvable(tmp_path, capsys, monkeypatch):
    """无引擎配置 + 无 env:show 降级为 contract-only,evidence=null,不报错。"""
    import json
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMAC_ENGINE", raising=False)
    monkeypatch.delenv("OMAC_WORKSPACE_ID", raising=False)
    path = _write_manifest(tmp_path, [
        {"id": "b", "worker": "bob", "status": "blocked", "work_item_id": "9",
         "contract": {"objective": "x", "acceptance": [], "verification_commands": []}}])
    assert main(["node", "show", path, "b", "--output", "json"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence"] is None
    assert payload["contract"]["objective"] == "x"


# ---------------- retry ----------------

def test_retry_resets_to_todo_and_keeps_work_item_id(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    m = load_manifest(path)
    assert m.nodes["b"].status == "todo"
    assert m.nodes["b"].work_item_id == "2"   # 保留
    assert m.nodes["b"].worker == "bob"        # 未改派


def test_retry_review_preserves_sealed_delivery_and_resumes_reviewer(
    tmp_path, capsys, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.core.taskmeta import TaskPhase
    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", reviewer="alice")
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "head-1"},
        delivery_identity={
            "schema": "omac.delivery-identity/v1",
            "handoff_generation": "handoff-1",
            "worker": "bob",
            "agent_id": "agent-bob",
            "run_id": "run-worker",
            "pr_url": "https://example.test/pr/1",
            "pr_head_sha": "head-1",
            "verification_sha256": "sha-1",
            "verification_attachment_id": "attachment-1",
            "verification_comment_id": "comment-1",
            "verification_uploader_id": "agent-bob",
            "verification_uploader_type": "agent",
            "verification_created_at": "2026-08-01T01:00:00Z",
        },
        review_verdict="reject",
    )
    engine.store.get_work_item(item.id).verification_ref = {
        "attachment_id": "attachment-1", "comment_id": "comment-1",
    }
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "reviewer": "alice",
        "status": "blocked", "work_item_id": item.id,
    }])

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()

    manifest = load_manifest(path)
    resumed = engine.store.get_work_item(item.id)
    assert manifest.nodes["b"].status == "in_review"
    assert resumed.status is WorkItemStatus.IN_REVIEW
    assert resumed.phase is TaskPhase.REVIEW
    assert resumed.delivery_identity is not None
    assert resumed.delivery_identity.run_id == "run-worker"
    assert resumed.review_verdict is None
    assert resumed.review_subject_digest


def test_retry_bounds_operator_handoff_direct_run_baseline(
    tmp_path, capsys, monkeypatch,
):
    """operator retry must cap handoff Run IDs and persist its causal cutoff."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.core.taskmeta import TaskPhase
    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import AgentRunObservation, WorkItemStatus

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", reviewer="alice")
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_bounce=1,
        review_verdict="reject",
        review_subject_digest="rejected-subject",
        artifacts={"pr_url": "https://example.test/pr/1"},
        verification={"commands": []},
        review_report={"blockers": [{
            "summary": "restore the missing context",
            "required_fix": "provide the next change",
        }]},
        review_report_source=yaml.safe_dump({
            "blockers": [{
                "summary": "restore the missing context",
                "required_fix": "provide the next change",
            }],
        }),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    runs = [AgentRunObservation(
        id=f"run-{index:02d}",
        kind="direct",
        status="completed",
        created_at=f"2026-08-01T00:{index:02d}:00Z",
    ) for index in range(21)]
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: runs)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "reviewer": "alice",
        "status": "blocked",
        "work_item_id": item.id,
    }])

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    handoff = engine.store.get_work_item(item.id).worker_handoff
    assert handoff is not None
    assert len(handoff.baseline_direct_run_ids) == 20
    assert handoff.baseline_direct_run_ids == tuple(
        f"run-{index:02d}" for index in range(1, 21))
    assert handoff.baseline_cutoff_created_at == "2026-08-01T00:20:00Z"


@pytest.mark.parametrize("trigger_kind", ["issue_assignment", "rerun"])
def test_retry_review_recovers_delayed_visible_dispatch_and_preserves_reject(
    tmp_path, capsys, monkeypatch, trigger_kind,
):
    """显式 review retry 只绑定已存在 Run，不清除当前 subject 的报告。"""
    from omac.core.taskmeta import TaskPhase
    from omac.engines.models import AgentRunObservation, WorkItemStatus
    from omac.pipeline.loop import tick

    engine, path, item_id, reviewer_id, subject, report = (
        _delayed_reviewer_retry_fixture(tmp_path, monkeypatch))
    candidate = AgentRunObservation(
        id="ae2e1a1d-65e4-402c-90b5-61e4c8389d9a",
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2026-08-02T13:34:35Z",
        updated_at="2026-08-02T13:35:00Z",
        trigger_kind=trigger_kind,
    )
    original_list_runs = engine.runtime.list_runs
    original_assign = engine.store.assign_work_item
    original_wake = engine.runtime.wake
    monkeypatch.setattr(
        engine.runtime, "list_runs", lambda _item_id: [candidate])
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "causal retry must not assign an Agent"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "causal retry must not create or rerun an Agent Run"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()

    resumed = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "in_review"
    assert resumed.status is WorkItemStatus.IN_REVIEW
    assert resumed.phase is TaskPhase.REVIEW
    assert resumed.review_subject_digest == subject
    assert resumed.review_verdict == "reject"
    assert resumed.review_report == report
    assert resumed.review_report_ref is not None
    assert resumed.decision_required["reason_code"] == (
        "reviewer-run-baseline-unavailable")
    assert resumed.reviewer_run_baseline.target_run_id == candidate.id

    # A fresh controller process consumes the already submitted reject rather
    # than dispatching another Reviewer or replaying the Worker delivery.
    monkeypatch.setattr(engine.runtime, "list_runs", original_list_runs)
    monkeypatch.setattr(engine.store, "assign_work_item", original_assign)
    monkeypatch.setattr(engine.runtime, "wake", original_wake)
    manifest = load_manifest(path)
    tick(engine.store, engine.runtime, manifest, path, max_parallel=1)
    consumed = engine.store.get_work_item(item_id)
    assert manifest.nodes["b"].status == "in_progress"
    assert consumed.bounces.review == 1
    assert consumed.phase is TaskPhase.AUTHORING


@pytest.mark.parametrize(
    "case",
    [
        "missing", "ambiguous", "foreign", "stale", "missing-time",
        "assignment-ambiguous", "assignment-foreign", "assignment-stale",
        "comment-trigger", "manual-trigger", "missing-trigger", "subject-mismatch",
        "identity-mismatch", "pr-head-drift", "verification-drift",
        "contract-drift",
    ],
)
def test_retry_review_delayed_run_recovery_fails_closed(
    tmp_path, capsys, monkeypatch, case,
):
    """不能唯一证明 Run 因果关系时保留 decision，且不产生平台执行副作用。"""
    from dataclasses import replace

    from omac.engines.models import AgentRunObservation, WorkItemStatus

    engine, path, item_id, reviewer_id, subject, report = (
        _delayed_reviewer_retry_fixture(tmp_path, monkeypatch))
    matching = AgentRunObservation(
        id="run-reviewer-current",
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2026-08-02T13:34:35Z",
        trigger_kind="rerun",
    )
    runs = {
        "missing": [],
        "ambiguous": [
            matching,
            replace(matching, id="run-reviewer-second", created_at="2026-08-02T13:34:36Z"),
        ],
        "foreign": [replace(matching, agent_id="agent-foreign")],
        "stale": [replace(matching, created_at="2026-08-02T13:29:59Z")],
        "missing-time": [replace(matching, created_at=None)],
        "assignment-ambiguous": [
            replace(matching, trigger_kind="issue_assignment"),
            replace(
                matching,
                id="run-reviewer-assignment-second",
                created_at="2026-08-02T13:34:36Z",
                trigger_kind="issue_assignment",
            ),
        ],
        "assignment-foreign": [replace(
            matching,
            agent_id="agent-foreign",
            trigger_kind="issue_assignment",
        )],
        "assignment-stale": [replace(
            matching,
            created_at="2026-08-02T13:29:59Z",
            trigger_kind="issue_assignment",
        )],
        "comment-trigger": [replace(matching, trigger_kind="comment")],
        "manual-trigger": [replace(matching, trigger_kind="manual")],
        "missing-trigger": [replace(matching, trigger_kind=None)],
        "subject-mismatch": [matching],
        "identity-mismatch": [matching],
        "pr-head-drift": [matching],
        "verification-drift": [matching],
        "contract-drift": [matching],
    }[case]
    if case == "subject-mismatch":
        current = engine.store.get_work_item(item_id)
        engine.store.update_work_item_metadata(
            item_id,
            reviewer_run_baseline=replace(
                current.reviewer_run_baseline,
                subject_digest="different-subject",
            ),
        )
    if case == "identity-mismatch":
        current = engine.store.get_work_item(item_id)
        engine.store.update_work_item_metadata(
            item_id,
            delivery_identity=replace(
                current.delivery_identity,
                verification_created_at="2026-08-02T13:31:00Z",
            ),
        )
    if case == "pr-head-drift":
        current = engine.store.get_work_item(item_id)
        engine.store.update_work_item_metadata(
            item_id,
            artifacts={
                "pr_url": "https://example.test/pr/1",
                "head_sha": "head-2",
            },
            delivery_identity=replace(
                current.delivery_identity,
                pr_head_sha="head-2",
            ),
        )
    if case == "verification-drift":
        current = engine.store.get_work_item(item_id)
        changed_verification = dict(current.verification)
        changed_verification["coverage"] = 99
        engine.store.update_work_item_metadata(
            item_id,
            verification=changed_verification,
            delivery_identity=replace(
                current.delivery_identity,
                verification_sha256="sha-2",
            ),
        )
    if case == "contract-drift":
        manifest = load_manifest(path)
        manifest.nodes["b"].contract = Contract(
            objective="changed contract",
            acceptance=["changed contract works"],
            verification_commands=["pytest -q"],
        )
        save_manifest(manifest, path)
    before = engine.store.get_work_item(item_id)
    decision = dict(before.decision_required)
    baseline = before.reviewer_run_baseline
    assignments = len(engine.store.assign_log)
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: list(runs))
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "failed-closed recovery must not assign"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "failed-closed recovery must not wake"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.VALIDATION
    capsys.readouterr()

    current = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "blocked"
    assert current.status is WorkItemStatus.BLOCKED
    assert current.decision_required == decision
    assert current.reviewer_run_baseline == baseline
    assert current.review_subject_digest == subject
    assert current.review_verdict == "reject"
    assert current.review_report == report
    assert len(engine.store.assign_log) == assignments


@pytest.mark.parametrize(
    "case",
    [
        "missing-reason-code",
        "tampered-reason-code",
        "non-object",
        "unknown-existing-decision",
    ],
)
def test_retry_review_preserves_noncanonical_existing_decision(
    tmp_path, capsys, monkeypatch, case,
):
    """任何非 canonical 已有 decision 都必须原样失败关闭。"""
    from omac.engines.models import WorkItemStatus

    engine, path, item_id, _reviewer_id, subject, report = (
        _delayed_reviewer_retry_fixture(tmp_path, monkeypatch))
    current = engine.store.get_work_item(item_id)
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
    engine.store.update_work_item_metadata(
        item_id, decision_required=decision)
    assignments = len(engine.store.assign_log)
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "noncanonical decision must not assign"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "noncanonical decision must not wake"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.VALIDATION
    capsys.readouterr()

    blocked = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision
    assert blocked.review_subject_digest == subject
    assert blocked.review_verdict == "reject"
    assert blocked.review_report == report
    assert len(engine.store.assign_log) == assignments


def test_retry_review_classifies_decision_before_submitted_evidence(
    tmp_path, capsys, monkeypatch,
):
    """Reviewer evidence incomplete must not bypass decision classification."""
    from omac.engines.models import WorkItemStatus

    engine, path, item_id, _reviewer_id, subject, _report = (
        _delayed_reviewer_retry_fixture(tmp_path, monkeypatch))
    current = engine.store.get_work_item(item_id)
    decision = {
        "schema": "omac.decision-required/v1",
        "reason_code": "guard-budget-exhausted",
    }
    current.review_report = None
    current.review_report_ref = None
    engine.store.update_work_item_metadata(
        item_id, decision_required=decision)
    assignments = len(engine.store.assign_log)
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "incomplete evidence must not bypass the existing decision"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "incomplete evidence must not start a Reviewer Run"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.VALIDATION
    capsys.readouterr()

    blocked = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision
    assert blocked.review_subject_digest == subject
    assert blocked.review_verdict == "reject"
    assert blocked.review_report is None
    assert blocked.review_report_ref is None
    assert len(engine.store.assign_log) == assignments


@pytest.mark.parametrize(
    "checkpoint",
    ["baseline", "status", "manifest"],
)
def test_retry_review_delayed_run_recovery_is_restart_safe(
    tmp_path, capsys, monkeypatch, checkpoint,
):
    """每个持久化断点崩溃后，重复 retry 只补齐同一恢复事实。"""
    from omac.core.taskmeta import TaskPhase
    from omac.engines.models import AgentRunObservation, WorkItemStatus

    engine, path, item_id, reviewer_id, subject, report = (
        _delayed_reviewer_retry_fixture(tmp_path, monkeypatch))
    candidate = AgentRunObservation(
        id="run-reviewer-delayed",
        kind="direct",
        status="completed",
        agent_id=reviewer_id,
        created_at="2026-08-02T13:34:35Z",
        updated_at="2026-08-02T13:35:00Z",
        trigger_kind="rerun",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs", lambda _item_id: [candidate])
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "restart-safe recovery must not assign"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "restart-safe recovery must not wake"),
    )

    import omac.cli.commands.node as node_mod

    original_update_metadata = engine.store.update_work_item_metadata
    original_update_status = engine.store.update_status
    original_save_manifest = node_mod.save_manifest
    crashed = False

    def crash(name):
        nonlocal crashed
        if checkpoint == name and not crashed:
            crashed = True
            raise RuntimeError(f"crash at {name}")

    def update_metadata(target_item_id, **metadata):
        result = original_update_metadata(target_item_id, **metadata)
        if "reviewer_run_baseline" in metadata:
            crash("baseline")
        return result

    def update_status(target_item_id, status):
        result = original_update_status(target_item_id, status)
        if status is WorkItemStatus.IN_REVIEW:
            crash("status")
        return result

    def save(manifest, manifest_path):
        result = original_save_manifest(manifest, manifest_path)
        crash("manifest")
        return result

    monkeypatch.setattr(engine.store, "update_work_item_metadata", update_metadata)
    monkeypatch.setattr(engine.store, "update_status", update_status)
    monkeypatch.setattr(node_mod, "save_manifest", save)

    with pytest.raises(RuntimeError, match=f"crash at {checkpoint}"):
        main(["node", "retry", path, "b", "--stage", "review"])

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update_metadata)
    monkeypatch.setattr(engine.store, "update_status", original_update_status)
    monkeypatch.setattr(node_mod, "save_manifest", original_save_manifest)

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()
    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()

    recovered = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "in_review"
    assert recovered.status is WorkItemStatus.IN_REVIEW
    assert recovered.phase is TaskPhase.REVIEW
    assert recovered.review_subject_digest == subject
    assert recovered.review_verdict == "reject"
    assert recovered.review_report == report
    assert recovered.review_report_ref is not None
    assert recovered.decision_required["reason_code"] == (
        "reviewer-run-baseline-unavailable")
    assert recovered.reviewer_run_baseline.target_run_id == candidate.id


def test_retry_completed_review_without_decision_resets_for_fresh_reviewer(
    tmp_path, capsys, monkeypatch,
):
    """空 decision 的 completed review 是普通 retry，不能复用旧 verdict。"""
    from dataclasses import replace

    from omac.core.taskmeta import TaskPhase
    from omac.engines.models import WorkItemStatus

    engine, path, item_id, _reviewer_id, _subject, _report = (
        _delayed_reviewer_retry_fixture(tmp_path, monkeypatch))
    current = engine.store.get_work_item(item_id)
    engine.store.update_work_item_metadata(
        item_id,
        reviewer_run_baseline=replace(
            current.reviewer_run_baseline,
            target_run_id="normal-completed-review-run",
        ),
        decision_required={},
    )
    engine.store.update_status(item_id, WorkItemStatus.IN_REVIEW)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "in_review"
    save_manifest(manifest, path)
    monkeypatch.setattr(
        engine.runtime,
        "list_runs",
        lambda _item_id: pytest.fail(
            "ordinary completed review retry must not bind an old Run"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()

    resumed = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "in_review"
    assert resumed.status is WorkItemStatus.IN_REVIEW
    assert resumed.phase is TaskPhase.REVIEW
    assert resumed.review_verdict is None
    assert resumed.review_report is None
    assert resumed.review_report_ref is None
    assert resumed.reviewer_run_baseline is None


def test_retry_review_without_submitted_report_keeps_normal_retry_semantics(
    tmp_path, capsys, monkeypatch,
):
    """无结构化报告时仍是普通 review retry，不尝试绑定历史 Run。"""
    from omac.core.taskmeta import TaskPhase
    from omac.engines.models import WorkItemStatus

    engine, path, item_id, _reviewer_id, _subject, _report = (
        _delayed_reviewer_retry_fixture(tmp_path, monkeypatch))
    current = engine.store.get_work_item(item_id)
    current.review_report = None
    current.review_report_ref = None
    monkeypatch.setattr(
        engine.runtime,
        "list_runs",
        lambda _item_id: pytest.fail(
            "ordinary review retry must not inspect delayed Run recovery"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()

    resumed = engine.store.get_work_item(item_id)
    assert resumed.status is WorkItemStatus.IN_REVIEW
    assert resumed.phase is TaskPhase.REVIEW
    assert resumed.review_verdict is None
    assert resumed.review_report is None
    assert resumed.reviewer_run_baseline is None


@pytest.mark.parametrize("trigger_kind", ["issue_assignment", "rerun"])
def test_retry_review_dispatch_unresolved_adopts_delayed_active_run(
    tmp_path, capsys, monkeypatch, trigger_kind,
):
    """续跑派发结果未确证时,显式 retry 必须认领延迟可见的活跃 Run,
    而不是重置评审阶段再派发一个重复 reviewer。"""
    from omac.core.taskmeta import TaskPhase
    from omac.engines.models import AgentRunObservation, WorkItemStatus

    engine, path, item_id, reviewer_id, subject = (
        _dispatch_unresolved_reviewer_retry_fixture(tmp_path, monkeypatch))
    candidate = AgentRunObservation(
        id="run-reviewer-continuation",
        kind="direct",
        status="running",
        agent_id=reviewer_id,
        created_at="2026-08-02T13:40:00Z",
        trigger_kind=trigger_kind,
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs", lambda _item_id: [candidate])
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "adopting a delayed Run must not assign an Agent"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "adopting a delayed Run must not create or rerun an Agent Run"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()

    resumed = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "in_review"
    assert resumed.status is WorkItemStatus.IN_REVIEW
    assert resumed.phase is TaskPhase.REVIEW
    assert resumed.review_subject_digest == subject
    assert resumed.review_verdict is None
    assert resumed.review_report is None
    assert resumed.decision_required["reason_code"] == (
        "reviewer-run-dispatch-unresolved")
    assert resumed.reviewer_run_baseline.target_run_id == candidate.id
    assert resumed.reviewer_run_baseline.attempt == 2


def test_retry_review_dispatch_unresolved_without_run_resets_review_stage(
    tmp_path, capsys, monkeypatch,
):
    """续跑 Run 确实没有产生时,显式 retry 重置评审阶段以便重新派发,
    且 retry 本身不产生任何 Agent 派发副作用。"""
    from omac.core.taskmeta import TaskPhase
    from omac.engines.models import WorkItemStatus

    engine, path, item_id, _reviewer_id, subject = (
        _dispatch_unresolved_reviewer_retry_fixture(tmp_path, monkeypatch))
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "node retry must not assign an Agent"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "node retry must not create or rerun an Agent Run"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.OK
    capsys.readouterr()

    resumed = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "in_review"
    assert resumed.status is WorkItemStatus.IN_REVIEW
    assert resumed.phase is TaskPhase.REVIEW
    assert resumed.review_verdict is None
    assert resumed.review_report is None
    assert resumed.reviewer_run_baseline is None
    assert not resumed.decision_required
    assert resumed.review_subject_digest
    assert resumed.review_subject_digest != subject


@pytest.mark.parametrize(
    "case", ["tampered-reason-code", "missing-reason-code"])
def test_retry_review_dispatch_unresolved_tampered_decision_fails_closed(
    tmp_path, capsys, monkeypatch, case,
):
    """篡改后的 dispatch-unresolved 决策仍必须按未知决策失败关闭。"""
    from copy import deepcopy

    from omac.engines.models import WorkItemStatus

    engine, path, item_id, _reviewer_id, subject = (
        _dispatch_unresolved_reviewer_retry_fixture(tmp_path, monkeypatch))
    current = engine.store.get_work_item(item_id)
    decision = deepcopy(current.decision_required)
    if case == "tampered-reason-code":
        decision["reason_code"] = "reviewer-run-dispatch-unresolved-tampered"
    else:
        decision.pop("reason_code")
    engine.store.update_work_item_metadata(item_id, decision_required=decision)
    monkeypatch.setattr(
        engine.store,
        "assign_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "a tampered decision must not assign an Agent"),
    )
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *_args, **_kwargs: pytest.fail(
            "a tampered decision must not start an Agent Run"),
    )

    assert main([
        "node", "retry", path, "b", "--stage", "review",
    ]) == exit_codes.VALIDATION
    capsys.readouterr()

    blocked = engine.store.get_work_item(item_id)
    assert load_manifest(path).nodes["b"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision
    assert blocked.review_subject_digest == subject
    assert blocked.reviewer_run_baseline.attempt == 2


def test_retry_explicitly_clears_confirmed_merge_closure(
    tmp_path, capsys, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, [{
        "id": "merged-node",
        "worker": "bob",
        "status": "done",
        "work_item_id": "issue-merged",
        "merged": True,
        "merged_at": "2026-07-30T00:00:00Z",
    }])

    assert main(["node", "retry", path, "merged-node"]) == exit_codes.OK

    node = load_manifest(path).nodes["merged-node"]
    assert node.status == "todo"
    assert node.merged is False
    assert node.merged_at is None
    assert node.merge_request_state is None


def test_retry_reassignment_survives_reconcile_and_dispatches_new_worker(
    tmp_path, capsys, monkeypatch,
):
    """显式 retry 必须同步平台 authoring + todo，再派发新的 worker。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import TaskPhase, WorkItemStatus
    from omac.pipeline.loop import tick

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_comment="fix the rejected delivery",
    )
    engine.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)

    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "status": "blocked",
        "work_item_id": item.id,
    }])

    assert main([
        "node", "retry", path, "b", "--worker", "charlie",
    ]) == exit_codes.OK
    capsys.readouterr()
    retried = engine.store.get_work_item(item.id)
    assert retried.status == WorkItemStatus.TODO
    assert retried.phase == TaskPhase.AUTHORING
    assert retried.review_verdict is None

    manifest = load_manifest(path)
    result = tick(engine.store, engine.runtime, manifest, path, max_parallel=1)

    assert result.dispatched == ["b"]
    assert manifest.nodes["b"].status == "in_progress"
    assert engine.store.get_work_item(item.id).worker == "charlie"


@pytest.mark.parametrize("replacement", [None, "charlie"])
def test_retry_retires_old_handoff_and_waits_for_new_worker_submit(
    tmp_path, capsys, monkeypatch, replacement,
):
    """显式 retry 退役旧 handoff；新 Worker 未交付前不能提前派 Reviewer。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus
    from omac.core.taskmeta import TaskPhase
    from omac.pipeline.loop import tick

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", reviewer="alice")
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_subject_digest="old-review-subject",
        worker_handoff=_old_worker_handoff(),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "reviewer": "alice",
        "status": "blocked",
        "work_item_id": item.id,
    }])

    command = ["node", "retry", path, "b"]
    if replacement:
        command.extend(["--worker", replacement])
    assert main(command) == exit_codes.OK
    capsys.readouterr()

    expected_worker = replacement or "bob"
    retried = engine.store.get_work_item(item.id)
    assert retried.worker_handoff is None
    assert retried.phase is TaskPhase.AUTHORING
    assert retried.status is WorkItemStatus.TODO

    manifest = load_manifest(path)
    first = tick(engine.store, engine.runtime, manifest, path, max_parallel=1)
    assert first.dispatched == ["b"]
    assert engine.store.get_work_item(item.id).worker == expected_worker
    first_handoff = engine.store.get_work_item(item.id).worker_handoff
    assert first_handoff is not None
    assert first_handoff.gate == "explicit-dispatch"
    assert first_handoff.target_worker_bounce == 0
    reviewer_assignments = len([
        entry for entry in engine.store.assign_log if entry[2] == "reviewer"
    ])

    second = tick(engine.store, engine.runtime, manifest, path, max_parallel=1)
    assert second.state == "running"
    assert manifest.nodes["b"].status == "in_progress"
    assert len([
        entry for entry in engine.store.assign_log if entry[2] == "reviewer"
    ]) == reviewer_assignments

    from omac.pipeline.dispatch import submit

    verification_file = tmp_path / "verification-retry.yaml"
    verification_file.write_text(yaml.safe_dump({
        "commands": [],
        "integration_gates": [],
        "pr_base": "main",
        "coverage": 100,
    }))
    submit(
        engine.store,
        item.id,
        pr_url="https://github.com/acme/repo/pull/25",
        verification_file=str(verification_file),
    )

    third = tick(engine.store, engine.runtime, manifest, path, max_parallel=1)
    reviewed = engine.store.get_work_item(item.id)
    assert third.state == "running"
    assert manifest.nodes["b"].status == "in_review"
    assert reviewed.phase is TaskPhase.REVIEW
    assert reviewed.status is WorkItemStatus.IN_REVIEW
    assert len([
        entry for entry in engine.store.assign_log if entry[2] == "reviewer"
    ]) == reviewer_assignments + 1


def test_retry_legacy_delivery_isolates_old_evidence_until_fresh_submit(
    tmp_path, capsys, monkeypatch,
):
    """显式 retry 复用同一 PR，但旧附件不能满足新的 Worker generation。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import PullRequestCheckResult, WorkItemStatus
    from omac.core.taskmeta import TaskPhase
    from omac.pipeline.dispatch import submit
    from omac.pipeline.loop import tick

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    contract = Contract(
        objective="fix the rejected delivery",
        acceptance=["works"],
        non_goals=["no scope creep"],
        verification_commands=["pytest -q"],
        pr_base="main",
        coverage_gate=0,
    )
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", reviewer="alice")
    engine.store.set_node_contract(item.id, contract)
    engine.store.assign_work_item(item.id, "bob", "worker")
    old_pr = "https://github.com/acme/repo/pull/24"
    old_verification = {
        "commands": [{
            "cmd": "pytest -q",
            "exit_code": 0,
            "business_tests": [{
                "acceptance": "works",
                "test": "tests/test_delivery.py::test_old",
            }],
        }],
        "integration_gates": [{"name": "smoke", "commands": []}],
        "pr_base": "main",
        "coverage": 100,
    }
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": old_pr},
        verification=old_verification,
        verification_source=yaml.safe_dump(old_verification),
        review_report={"blockers": [{
            "summary": "restore the legacy rework context",
            "required_fix": "make the new change explicit",
        }]},
        review_report_source=yaml.safe_dump({
            "blockers": [{
                "summary": "restore the legacy rework context",
                "required_fix": "make the new change explicit",
            }],
        }),
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
        decision_required={
            "schema": "omac.decision-required/v1",
            "reason_code": "legacy-delivery-retry-required",
        },
    )
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    engine.store.clear_assignment(item.id)
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    old_attachment = engine.store.get_work_item(item.id).verification_ref[
        "attachment_id"]

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "reviewer": "alice",
        "status": "blocked",
        "work_item_id": item.id,
        "contract": {
            "objective": contract.objective,
            "acceptance": contract.acceptance,
            "non_goals": contract.non_goals,
            "verification_commands": contract.verification_commands,
            "pr_base": contract.pr_base,
            "coverage_gate": contract.coverage_gate,
        },
    }])

    runs_before_retry = len(engine.runtime.list_runs(item.id))
    assignments_before_retry = len(engine.store.assign_log)
    original_save = node_mod.save_manifest
    crashed = False

    def crash_before_manifest_save(manifest, manifest_path):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("crash before retry manifest save")
        return original_save(manifest, manifest_path)

    monkeypatch.setattr(node_mod, "save_manifest", crash_before_manifest_save)
    with pytest.raises(RuntimeError, match="retry manifest save"):
        main(["node", "retry", path, "b"])

    assert load_manifest(path).nodes["b"].status == "blocked"
    assert len(engine.runtime.list_runs(item.id)) == runs_before_retry
    assert len(engine.store.assign_log) == assignments_before_retry

    monkeypatch.setattr(node_mod, "save_manifest", original_save)
    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    retried = engine.store.get_work_item(item.id)
    assert retried.status is WorkItemStatus.TODO
    assert retried.phase is TaskPhase.AUTHORING
    assert retried.decision_required is None
    assert retried.worker_handoff is not None
    assert retried.worker_handoff.is_causally_bound()
    assert retried.worker_handoff.target_worker_bounce == retried.bounces.worker
    assert retried.worker_handoff.baseline_verification_attachment_id == (
        old_attachment)
    assert retried.artifacts["pr_url"] == old_pr
    assert retried.verification == old_verification

    manifest = load_manifest(path)
    retry_generation = retried.worker_handoff.generation
    assignments_before = len(engine.store.assign_log)
    first = tick(engine.store, engine.runtime, manifest, path, max_parallel=1)
    assert first.dispatched == ["b"]
    dispatched_handoff = engine.store.get_work_item(item.id).worker_handoff
    assert dispatched_handoff is not None
    assert dispatched_handoff.generation == retry_generation
    assert dispatched_handoff.gate == "operator-retry"
    assert [
        entry[2] for entry in engine.store.assign_log[assignments_before:]
    ] == ["worker"]

    waiting = tick(
        engine.store, engine.runtime, manifest, path, max_parallel=1)
    assert waiting.state == "running"
    assert not [
        entry for entry in engine.store.assign_log[assignments_before:]
        if entry[2] == "reviewer"
    ]

    verification_file = tmp_path / "verification.yaml"
    fresh_verification = {
        "commands": [{
            "cmd": "pytest -q",
            "exit_code": 0,
            "business_tests": [{
                "acceptance": "works",
                "test": "tests/test_delivery.py::test_fresh",
            }],
        }],
        "integration_gates": [{"name": "smoke", "commands": []}],
        "pr_base": "main",
        "coverage": 100,
    }
    verification_file.write_text(yaml.safe_dump(fresh_verification))
    submit(
        engine.store,
        item.id,
        pr_url=old_pr,
        verification_file=str(verification_file),
    )
    ci_calls = 0

    def pass_ci(_pr_url, *_args):
        nonlocal ci_calls
        ci_calls += 1
        return PullRequestCheckResult(True, 0, "green")

    monkeypatch.setattr(engine.store, "check_pull_request", pass_ci)
    reviewed = tick(
        engine.store,
        engine.runtime,
        manifest,
        path,
        max_parallel=1,
        config={"ci": {"check_command": "gh pr checks {pr_url}"}},
    )

    current = engine.store.get_work_item(item.id)
    assert reviewed.state == "running"
    assert manifest.nodes["b"].status == "in_progress"
    assert current.delivery_identity is None
    assert current.worker_handoff is not None
    assert current.worker_handoff.baseline_pr_head_sha is None
    assert current.worker_handoff.baseline_verification_attachment_id == old_attachment
    assert current.phase is TaskPhase.AUTHORING
    assert ci_calls == 0


@pytest.mark.parametrize("replacement", [None, "charlie"])
@pytest.mark.parametrize("checkpoint", ["before_switch", "after_switch"])
def test_retry_handoff_retirement_is_restart_safe(
    tmp_path, monkeypatch, replacement, checkpoint,
):
    """clear 写入前后崩溃都不提交半份 manifest，重复 retry 可幂等恢复。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus
    from omac.core.taskmeta import TaskPhase

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_subject_digest="old-review-subject",
        worker_handoff=_old_worker_handoff(),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "status": "blocked",
        "work_item_id": item.id,
    }])
    original_restore = engine.store.restore_authoring_generation
    crashed = False

    def crash_at_switch(item_id, contract, generation, bounce_baseline=None):
        nonlocal crashed
        if not crashed:
            crashed = True
            if checkpoint == "before_switch":
                raise RuntimeError("crash before authoring generation switch")
            result = original_restore(
                item_id, contract, generation, bounce_baseline)
            raise RuntimeError("crash after authoring generation switch")
        return original_restore(item_id, contract, generation, bounce_baseline)

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", crash_at_switch)
    command = ["node", "retry", path, "b"]
    if replacement:
        command.extend(["--worker", replacement])

    with pytest.raises(RuntimeError, match="authoring generation switch"):
        main(command)

    interrupted_manifest = load_manifest(path)
    assert interrupted_manifest.nodes["b"].status == "blocked"
    assert interrupted_manifest.nodes["b"].worker == "bob"
    interrupted_item = engine.store.get_work_item(item.id)
    assert (interrupted_item.worker_handoff is None) is (
        checkpoint == "after_switch")
    assert (interrupted_item.review_generation is not None) is (
        checkpoint == "after_switch")

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", original_restore)
    assert main(command) == exit_codes.OK
    assert main(command) == exit_codes.OK

    recovered_manifest = load_manifest(path)
    recovered_item = engine.store.get_work_item(item.id)
    assert recovered_manifest.nodes["b"].status == "todo"
    assert recovered_manifest.nodes["b"].worker == (replacement or "bob")
    assert recovered_item.worker_handoff is None
    assert recovered_item.phase is TaskPhase.AUTHORING
    assert recovered_item.status is WorkItemStatus.TODO


def test_retry_clears_recovery_marker_after_retiring_recovery_facts(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.core.taskmeta import TaskPhase
    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        decision_required={"reason_code": "operator-retry"},
        worker_handoff=_old_worker_handoff(),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "status": "blocked",
        "work_item_id": item.id, "recovery_marker": True,
    }])

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    recovered = engine.store.get_work_item(item.id)
    assert recovered.worker_handoff is None
    assert recovered.reviewer_run_baseline is None
    assert recovered.decision_required is None
    assert load_manifest(path).nodes["b"].recovery_marker is False


def test_retry_platform_failure_preserves_recovery_marker_and_business_fields(
    tmp_path, monkeypatch,
):
    """平台恢复写入失败时保留观察 marker，不能提交业务字段变更。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.errors import PlatformError

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *args, **kwargs: (_ for _ in ()).throw(PlatformError("offline")),
    )

    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "status": "blocked",
        "work_item_id": item.id,
    }])

    assert main([
        "node", "retry", path, "b", "--worker", "charlie",
    ]) == exit_codes.PLATFORM
    manifest = load_manifest(path)
    assert manifest.nodes["b"].worker == "bob"
    assert manifest.nodes["b"].status == "blocked"
    assert manifest.nodes["b"].work_item_id == item.id
    assert manifest.nodes["b"].recovery_marker is True


def test_retry_platform_read_failure_preserves_recovery_marker_and_business_fields(
    tmp_path, monkeypatch,
):
    """平台工单读取未知时，预先持久化的恢复提示必须保留。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.errors import PlatformError

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(
        engine.store,
        "get_work_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(PlatformError("offline")),
    )
    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "status": "blocked",
        "work_item_id": item.id,
    }])

    assert main(["node", "retry", path, "b"]) == exit_codes.PLATFORM
    manifest = load_manifest(path)
    assert manifest.nodes["b"].worker == "bob"
    assert manifest.nodes["b"].status == "blocked"
    assert manifest.nodes["b"].work_item_id == item.id
    assert manifest.nodes["b"].recovery_marker is True


def test_retry_preserves_stale_mock_work_item_id_for_reconcile(tmp_path, monkeypatch):
    """跨进程 mock 恢复保留旧 ID，由下一次 dag run 的 reconcile 统一清理。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine

    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)

    path = _write_manifest(tmp_path, [{
        "id": "b",
        "worker": "bob",
        "status": "blocked",
        "work_item_id": "stale-id",
    }])

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    manifest = load_manifest(path)
    assert manifest.nodes["b"].status == "todo"
    assert manifest.nodes["b"].work_item_id == "stale-id"


def test_plain_retry_records_rejected_pr_head_for_worker_delta(
        tmp_path, capsys, monkeypatch):
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    current = engine.store.get_work_item(item_id)
    old_head = current.delivery_identity.pr_head_sha
    engine.store.update_work_item_metadata(
        item_id,
        review_verdict="reject",
        review_bounce=1,
        decision_required={},
        review_nits_acceptance={},
    )
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "blocked"
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    retried = engine.store.get_work_item(item_id)
    assert retried.worker_handoff is not None
    assert retried.worker_handoff.gate == "operator-retry"
    assert retried.worker_handoff.source_review_verdict == "reject"
    assert retried.worker_handoff.baseline_pr_head_sha == old_head
    assert retried.worker_handoff.source_review_feedback["verdict"] == "reject"
    assert retried.worker_handoff.source_review_feedback["report_ref"]


def test_plain_retry_without_live_verdict_still_records_existing_head(
        tmp_path, capsys, monkeypatch):
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    current = engine.store.get_work_item(item_id)
    old_head = current.delivery_identity.pr_head_sha
    engine.store.update_work_item_metadata(
        item_id,
        review_verdict="",
        review_bounce=1,
        decision_required={},
        review_nits_acceptance={},
    )
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "blocked"
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    retried = engine.store.get_work_item(item_id)
    assert retried.worker_handoff is not None
    assert retried.worker_handoff.gate == "operator-retry"
    assert retried.worker_handoff.source_review_verdict is None
    assert retried.worker_handoff.baseline_pr_head_sha == old_head


def test_plain_retry_preserves_prior_handoff_rejected_head_when_projection_is_incomplete(
        tmp_path, capsys, monkeypatch):
    from dataclasses import replace
    from omac.core.taskmeta import WorkerHandoffIntent
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    current = engine.store.get_work_item(item_id)
    old_head = current.delivery_identity.pr_head_sha
    prior = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="bob",
        gate="operator-retry",
        source_review_verdict="reject",
        generation="prior-retry",
        target_agent_id=engine.store.resolve_agent_id("bob"),
        baseline_verification_attachment_id=current.verification_ref["attachment_id"],
        baseline_pr_head_sha=old_head,
    )
    engine.store.update_work_item_metadata(
        item_id,
        artifacts={"pr_url": current.artifacts["pr_url"]},
        delivery_identity=replace(current.delivery_identity, pr_head_sha=None),
        review_verdict="",
        review_bounce=0,
        worker_handoff=prior,
        decision_required={},
        review_nits_acceptance={},
    )
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "blocked"
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    retried = engine.store.get_work_item(item_id)
    assert retried.worker_handoff is not None
    assert retried.worker_handoff.source_review_verdict == "reject"
    assert retried.worker_handoff.baseline_pr_head_sha == old_head


def test_plain_retry_recovers_review_feedback_from_store_comments(
        tmp_path, capsys, monkeypatch):
    from omac.core.taskmeta import WORKER_REWORK_FEEDBACK_SCHEMA
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    current = engine.store.get_work_item(item_id)
    report_ref = current.review_report_ref
    engine.store.reset_review(item_id)
    engine.store.update_work_item_metadata(item_id, review_bounce=1)
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    recovery = {
        "verdict": "reject",
        "report_ref": report_ref,
        "blockers": [{
            "root_cause_key": "auth-boundary",
            "summary": "add the missing auth method",
            "required_fix": "implement auth method",
        }],
    }
    monkeypatch.setattr(
        engine.store,
        "recover_review_rework_context",
        lambda _item_id: recovery,
    )
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "blocked"
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    retried = engine.store.get_work_item(item_id)
    feedback = retried.worker_handoff.source_review_feedback
    assert feedback["schema"] == WORKER_REWORK_FEEDBACK_SCHEMA
    assert feedback["verdict"] == "reject"
    assert feedback["report_ref"] == report_ref
    assert feedback["blockers"][0]["required_fix"] == "implement auth method"


def test_plain_retry_reject_without_review_context_fails_closed(
        tmp_path, capsys, monkeypatch):
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    engine.store.reset_review(item_id)
    engine.store.update_work_item_metadata(
        item_id,
        review_verdict="reject",
        review_bounce=0,
    )
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "blocked"
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.NEEDS_DECISION
    payload = capsys.readouterr()
    assert "prior review rework context is unavailable" in payload.err
    assert engine.store.get_work_item(item_id).status is WorkItemStatus.BLOCKED
    assert load_manifest(path).nodes["b"].status == "blocked"


def test_plain_retry_reject_with_zero_review_bounce_records_existing_head(
        tmp_path, capsys, monkeypatch):
    from omac.core.taskmeta import TaskPhase
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    current = engine.store.get_work_item(item_id)
    old_head = current.delivery_identity.pr_head_sha
    engine.store.update_work_item_metadata(
        item_id,
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_bounce=0,
        decision_required={},
        review_nits_acceptance={},
    )
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "blocked"
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    retried = engine.store.get_work_item(item_id)
    assert retried.worker_handoff is not None
    assert retried.worker_handoff.source_review_verdict == "reject"
    assert retried.worker_handoff.baseline_pr_head_sha == old_head


def test_plain_retry_falls_back_to_artifact_head_for_incomplete_identity(
        tmp_path, capsys, monkeypatch):
    from dataclasses import replace
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    current = engine.store.get_work_item(item_id)
    old_head = current.delivery_identity.pr_head_sha
    engine.store.update_work_item_metadata(
        item_id,
        review_verdict="reject",
        review_bounce=1,
        delivery_identity=replace(
            current.delivery_identity, pr_head_sha=None),
        decision_required={},
        review_nits_acceptance={},
    )
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "blocked"
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()

    retried = engine.store.get_work_item(item_id)
    assert retried.worker_handoff is not None
    assert retried.worker_handoff.baseline_pr_head_sha == old_head


def test_accept_nits_restores_review_preserves_reviewer_facts_and_is_idempotent(
    tmp_path, capsys, monkeypatch,
):
    import json
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    before = engine.store.get_work_item(item_id)
    verdict = before.review_verdict
    report = deepcopy(before.review_report)
    report_ref = deepcopy(before.review_report_ref)
    subject = before.review_subject_digest

    assert main(["node", "accept-nits", path, "b"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "in_review"

    accepted = engine.store.get_work_item(item_id)
    assert accepted.status is WorkItemStatus.IN_REVIEW
    assert accepted.phase.value == "review"
    assert accepted.review_verdict == verdict == "pass-with-nits"
    assert accepted.review_report == report
    assert accepted.review_report_ref == report_ref
    assert accepted.review_subject_digest == subject
    assert accepted.decision_required in (None, {})
    assert accepted.review_nits_acceptance == {
        "schema": "omac.review-nits-acceptance/v1",
        "review_subject_digest": subject,
        "review_report_ref": report_ref,
        "verdict": "pass-with-nits",
    }
    assert load_manifest(path).nodes["b"].status == "in_review"

    # A second invocation observes the same bounded marker and performs no new
    # assignment/run or reviewer-fact mutation.
    assignments = list(engine.store.assign_log)
    assert main(["node", "accept-nits", path, "b"]) == exit_codes.OK
    capsys.readouterr()
    repeated = engine.store.get_work_item(item_id)
    assert engine.store.assign_log == assignments
    assert repeated.review_verdict == verdict
    assert repeated.review_report == report
    assert repeated.review_report_ref == report_ref
    assert repeated.review_nits_acceptance["review_subject_digest"] == subject


def test_accept_nits_rejects_active_direct_run_without_writes(
    tmp_path, capsys, monkeypatch,
):
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    engine.store.assign_work_item(item_id, "alice", "reviewer")
    before = engine.store.get_work_item(item_id)
    assignments = list(engine.store.assign_log)

    assert main(["node", "accept-nits", path, "b"]) == exit_codes.VALIDATION
    capsys.readouterr()
    current = engine.store.get_work_item(item_id)
    assert current.status is WorkItemStatus.BLOCKED
    assert current.decision_required == before.decision_required
    assert current.review_nits_acceptance is None
    assert engine.store.assign_log == assignments
    assert load_manifest(path).nodes["b"].status == "blocked"


def test_retry_clears_review_nits_acceptance_marker_and_returns_authoring(
    tmp_path, capsys, monkeypatch,
):
    from omac.engines.models import WorkItemStatus

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    marker = {
        "schema": "omac.review-nits-acceptance/v1",
        "review_subject_digest": engine.store.get_work_item(
            item_id).review_subject_digest,
        "review_report_ref": engine.store.get_work_item(item_id).review_report_ref,
        "verdict": "pass-with-nits",
    }
    engine.store.update_work_item_metadata(
        item_id, review_nits_acceptance=marker, decision_required={})
    engine.store.update_status(item_id, WorkItemStatus.IN_REVIEW)
    manifest = load_manifest(path)
    manifest.nodes["b"].status = "in_review"
    manifest.nodes["b"].recovery_marker = False
    save_manifest(manifest, path)

    assert main(["node", "retry", path, "b"]) == exit_codes.OK
    capsys.readouterr()
    current = engine.store.get_work_item(item_id)
    assert current.review_nits_acceptance is None
    assert current.review_verdict is None
    assert current.review_report is None
    assert current.phase.value == "authoring"
    assert current.status is WorkItemStatus.TODO
    assert load_manifest(path).nodes["b"].status == "todo"


def test_accept_marks_done_and_updates_platform_status(tmp_path, capsys, monkeypatch):
    """人工接受已知风险后,节点视为 done,下次 dag run 可继续推进。"""
    import json
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.core.taskmeta import TaskKind
    from omac.engines.models import WorkItemStatus
    engine = create_engine("mock", EngineConfig("mock", "ws-1",
                                                extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item(
        "ws-1", "t", "d", "b", "bob", kind=TaskKind.PLAN)
    engine.store.update_work_item_metadata(
        item.id,
        review_verdict="pass-with-nits",
        decision_required={"verdict": "pass-with-nits"},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)

    path = _write_manifest(tmp_path, [
        {"id": "b", "worker": "bob", "status": "blocked",
         "work_item_id": item.id},
        {"id": "c", "worker": "charlie", "blocked_by": ["b"], "status": "todo"},
    ])

    assert main(["node", "accept", path, "b"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    m = load_manifest(path)
    assert payload["status"] == "done"
    assert m.nodes["b"].status == "done"
    assert engine.store.get_work_item(item.id).status == WorkItemStatus.DONE


def test_accept_rejects_develop_pr_without_confirmed_merge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from types import SimpleNamespace
    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine("mock", EngineConfig(
        "mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    engine.store.update_work_item_metadata(
        item.id, artifacts={"pr_url": "https://example.com/pr/1"})
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    engine.store.observe_pull_request = lambda pr_url: SimpleNamespace(
        state="open", merged_at=None)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "status": "blocked", "work_item_id": item.id,
    }])

    assert main(["node", "accept", path, "b"]) == exit_codes.VALIDATION
    assert load_manifest(path).nodes["b"].status == "blocked"
    assert engine.store.get_work_item(item.id).status is WorkItemStatus.BLOCKED


def test_accept_rejects_develop_node_without_pr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine("mock", EngineConfig(
        "mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "status": "blocked", "work_item_id": item.id,
    }])

    assert main(["node", "accept", path, "b"]) == exit_codes.VALIDATION
    assert load_manifest(path).nodes["b"].status == "blocked"


def test_accept_rejects_node_without_work_item(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "status": "blocked",
    }])

    assert main(["node", "accept", path, "b"]) == exit_codes.VALIDATION
    assert load_manifest(path).nodes["b"].status == "blocked"


def test_accept_rejects_merged_flag_without_authoritative_timestamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")

    from omac.engines import EngineConfig, create_engine
    from omac.engines.models import WorkItemStatus

    engine = create_engine("mock", EngineConfig(
        "mock", "ws-1", extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item("ws-1", "t", "d", "b", "bob")
    engine.store.update_work_item_metadata(
        item.id, artifacts={"pr_url": "https://example.com/pr/1"})
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    import omac.cli.commands.node as node_mod
    monkeypatch.setattr(node_mod, "create_engine", lambda *a, **kw: engine)
    path = _write_manifest(tmp_path, [{
        "id": "b", "worker": "bob", "status": "blocked", "work_item_id": item.id,
        "merged": True,
    }])

    assert main(["node", "accept", path, "b"]) == exit_codes.VALIDATION
    assert load_manifest(path).nodes["b"].status == "blocked"


def test_retry_reassign_worker_validated_against_config(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # config.roles.workers 提供 agent 池
    main(["config", "set", "roles.workers", '["alice", "bob", "dave"]'])
    capsys.readouterr()
    path = _write_manifest(tmp_path, _basic_nodes())

    # 非法 worker → exit 5
    assert main(["node", "retry", path, "b", "--worker", "ghost"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "ghost" in err

    # 合法 worker → 生效
    assert main(["node", "retry", path, "b", "--worker", "dave"]) == exit_codes.OK
    capsys.readouterr()
    m = load_manifest(path)
    assert m.nodes["b"].worker == "dave"
    assert m.nodes["b"].status == "todo"


def test_retry_worker_validated_via_env_workspace(tmp_path, capsys, monkeypatch):
    """env-only(无 config.yaml):--worker 仍应通过 engine.config.workspace_id
    校验 agent 池,非法 worker 应 exit 5 且 manifest 不变。(reviewer blocker)
    """
    import json
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws-1")
    # 不写任何 config.yaml —— 模拟纯 env 使用路径

    nodes = [{"id": "b", "worker": "bob", "status": "blocked"}]
    path = _write_manifest(tmp_path, nodes)

    code = main(["node", "retry", path, "b", "--worker", "ghost"])
    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    # exit 5 的报错不要求精确措辞,但应拒绝改派
    from omac.core.manifest import load_manifest
    m = load_manifest(path)
    assert m.nodes["b"].worker == "bob"            # manifest 未被改写
    assert m.nodes["b"].status == "blocked"        # 未重置 todo

    # 池内 worker(charlie 在 mock 默认池)→ 放行
    assert main(["node", "retry", path, "b", "--worker", "charlie"]) == exit_codes.OK
    capsys.readouterr()
    m = load_manifest(path)
    assert m.nodes["b"].worker == "charlie"


def test_retry_hints_rerun(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    main(["node", "retry", path, "b"])
    assert "dag run" in capsys.readouterr().err


# ---------------- abandon ----------------

def test_abandon_marks_abandoned_and_unlocks_downstream(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    assert main(["node", "abandon", path, "b"]) == exit_codes.OK
    m = load_manifest(path)
    assert m.nodes["b"].status == "abandoned"


def test_abandon_downstream_becomes_ready(tmp_path, monkeypatch):
    """abandon 后下游在下轮 tick 进入就绪集(graph 层语义)。"""
    from omac.core.graph import ready_nodes
    issues = {
        "a": {"status": "done", "blocked_by": []},
        "b": {"status": "abandoned", "blocked_by": ["a"]},
        "c": {"status": "todo", "blocked_by": ["b"]},
    }
    assert ready_nodes(issues) == ["c"]


def test_abandon_reports_affected_downstream(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_manifest(tmp_path, _basic_nodes())
    main(["node", "abandon", path, "a"])
    import json
    payload = json.loads(capsys.readouterr().out)
    # a 的传递下游:b、c
    assert "b" in payload["affected_downstream"]
    assert "c" in payload["affected_downstream"]

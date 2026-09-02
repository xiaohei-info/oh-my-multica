"""work show 的 kind × phase 事实包 + submit 模板/左移门/退出码。"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest
import yaml

from omac.cli.main import main
from omac.cli import exit_codes
from omac.cli.commands import work as work_cmd
from omac.core.manifest import (
    ConsumedArtifact,
    Contract,
    EvidenceMode,
    Manifest,
    Node,
    ProducedArtifact,
    _load_contract,
    save_manifest,
)
from omac.core.contract_boundaries import responsibility_summary
from omac.core.review_convergence import (
    REVIEW_PROTOCOL_VERSION,
    advance_review_ledger,
    build_review_obligations,
)
from omac.core.taskmeta import (
    TaskKind, TaskPhase, WorkerHandoffIntent, WORKER_REWORK_FEEDBACK_SCHEMA,
)
from omac.engines import create_engine
from omac.engines.models import (
    EngineConfig, PullRequestReadiness, PullRequestReadinessFailure,
    WorkItemControlProjection, WorkItemPayload, WorkItemStatus,
)
from omac.errors import AuthError, PlatformError, ValidationError
from omac.pipeline import dispatch as dispatch_mod
from omac.pipeline.dispatch import (
    SUBMIT_PARAM_SPECS,
    SUBMIT_PARAMS_BY_KIND_PHASE,
    build_show_output,
    submit_template_for,
)
from omac.pipeline.loop import tick


def _store(auto_complete: str = "false"):
    config = EngineConfig(
        engine_type="mock", workspace_id="mock-workspace",
        extra={"MOCK_AUTO_COMPLETE": auto_complete,
              "MOCK_AUTO_COMPLETE_DELAY": "0"})
    return create_engine("mock", config).store


def _make_item(store, kind: TaskKind, phase: TaskPhase, dag_key: str = "a",
               with_contract: bool = False, with_deliverable: bool = False,
               with_verification: bool = False):
    item = store.create_work_item(
        "mock-workspace", f"title-{kind.value}", "desc",
        dag_key=dag_key, worker="alice", reviewer="bob",
        kind=kind)
    store.update_work_item_metadata(item.id, phase=phase)
    if with_contract:
        # 走真实 dispatch 路径:set_node_contract 下发 contract(§7.4),
        # 验证 work show 能读回完整上下文(回归 set_node_contract → work show 链路)。
        store.set_node_contract(item.id, {
            "objective": "实现 X",
            "acceptance": ["A 工作", "B 工作"],
            "non_goals": ["不做 Y"],
            "verification_commands": ["pytest -q"],
            "integration_gates": [],
            "pr_base": "feature/v1",
            "coverage_gate": 90,
        })
    if with_deliverable:
        store.update_work_item_metadata(
            item.id, phase=phase, deliverable="# 计划正文")
    if with_verification:
        store.update_work_item_metadata(
            item.id, phase=phase,
            artifacts={"pr_url": "https://example.test/pr/42"},
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0,
                              "summary": "ok"}],
                "pr_base": "feature/v1",
                "coverage": 92,
                "env_setup": ["docker compose up -d db"],
            })
    return store.get_work_item(item.id)


def test_work_show_authoring_exposes_machine_gate_feedback_without_report():
    store = _store()
    item = _make_item(store, TaskKind.ACCEPTANCE, TaskPhase.AUTHORING)
    store.update_work_item_metadata(
        item.id, review_comment="machine gate: expected template is too generic")

    output = build_show_output(
        store.get_work_item(item.id), "planner:alice")

    assert output["context"]["previous_review"] == {
        "comment": "machine gate: expected template is too generic",
    }


def test_work_show_operator_retry_requires_fresh_submission():
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
    store.update_work_item_metadata(
        item.id,
        worker_handoff=WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="operator-retry",
            generation="handoff-1",
            target_agent_id="agent-alice",
            baseline_direct_run_ids=("old-run",),
            baseline_verification_attachment_id="old-verification",
        ),
    )

    output = build_show_output(
        store.get_work_item(item.id), "worker:alice", language="en")

    assert output["context"]["retry"] == {
        "gate": "operator-retry",
        "requires_fresh_submission": True,
        "previous_verification_is_baseline_only": True,
    }
    assert "Do not reuse or merely cite prior verification" in output["protocol"]
    assert "baseline-only and cannot be used as evidence" in output["protocol"]
    assert "Even when no code change is needed" in output["protocol"]
    assert "omac work submit" in output["protocol"]

    chinese = build_show_output(
        store.get_work_item(item.id), "worker:alice", language="cn")
    assert chinese["context"]["retry"] == output["context"]["retry"]
    assert "禁止复用或仅引用" in chinese["protocol"]
    assert "旧 verification 仅作为 baseline，不能作为本轮证据" in chinese["protocol"]
    assert "即使代码无需修改" in chinese["protocol"]


def test_work_show_operator_retry_exposes_rework_feedback_context():
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
    report_ref = {
        "attachment_id": "review-report",
        "sha256": "a" * 64,
        "filename": "review-report.yaml",
    }
    ledger_ref = {
        "attachment_id": "review-ledger",
        "sha256": "b" * 64,
        "filename": "review-ledger.yaml",
    }
    store.update_work_item_metadata(
        item.id,
        worker_handoff=WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="operator-retry",
            source_review_subject_digest="subject-1",
            source_review_round=1,
            source_review_verdict="reject",
            source_review_feedback={
                "schema": WORKER_REWORK_FEEDBACK_SCHEMA,
                "verdict": "reject",
                "report_ref": report_ref,
                "ledger_ref": ledger_ref,
                "blockers": [{
                    "root_cause_key": "auth-boundary",
                    "summary": "Authentication path is incomplete",
                    "required_fix": "Add the missing auth method",
                }],
            },
            target_review_bounce=1,
            generation="handoff-1",
            target_agent_id="agent-alice",
            baseline_direct_run_ids=("old-run",),
        ),
    )

    output = build_show_output(
        store.get_work_item(item.id), "worker:alice", language="en")

    assert output["context"]["previous_review"] == {
        "verdict": "reject",
        "report_ref": report_ref,
        "ledger_ref": ledger_ref,
        "blockers": [{
            "root_cause_key": "auth-boundary",
            "summary": "Authentication path is incomplete",
            "required_fix": "Add the missing auth method",
        }],
    }
    assert "context.previous_review" in output["protocol"]
    assert "address every preserved blocker" in output["protocol"]


def test_work_show_normal_authoring_has_no_operator_retry_instruction():
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)

    output = build_show_output(
        store.get_work_item(item.id), "worker:alice", language="en")

    assert "retry" not in output["context"]
    assert "Do not reuse or merely cite prior verification" not in output["protocol"]


@pytest.mark.parametrize("kind", [
    TaskKind.PLAN,
    TaskKind.ACCEPTANCE,
    TaskKind.DECOMPOSE,
    TaskKind.AMENDMENT,
    TaskKind.FINAL_ACCEPTANCE,
])
def test_work_show_operator_retry_is_develop_only(kind):
    store = _store()
    item = _make_item(store, kind, TaskPhase.AUTHORING)
    store.update_work_item_metadata(
        item.id,
        worker_handoff=WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="operator-retry",
        ),
    )

    output = build_show_output(
        store.get_work_item(item.id), "worker:alice", language="en")

    assert "retry" not in output["context"]
    assert "baseline-only" not in output["protocol"]


def test_work_show_projects_compact_contract_responsibility():
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
    store.set_node_contract(item.id, Contract(
        objective="tooling",
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
        consumes=[ConsumedArtifact(
            artifact_id="source-contracts",
            producer="source-contracts",
            evidence_mode=EvidenceMode.ARTIFACT,
        )],
    ))

    output = build_show_output(
        store.get_work_item(item.id), "worker:alice")

    assert output["context"]["responsibility"] == {
        "evidence_mode": "fixture",
        "input_policy": "allowlist",
        "allowed_inputs": [{
            "artifact_id": "source-contracts",
            "producer": "source-contracts",
            "evidence_mode": "artifact",
        }],
        "produces": ["tooling-package"],
        "boundary_rule": (
            "Only declared consumes are allowed external inputs; outputs from "
            "non-upstream or downstream nodes are outside this contract."
        ),
    }


def test_work_show_projects_transitional_upstream_input_policy():
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
    store.set_node_contract(item.id, Contract(
        objective="tooling",
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
    ))

    output = build_show_output(store.get_work_item(item.id), "worker:alice")

    responsibility = output["context"]["responsibility"]
    assert responsibility["input_policy"] == "transitional-upstream"
    assert responsibility["allowed_inputs"] is None
    assert "transitive upstream" in responsibility["boundary_rule"]


def test_work_show_projects_explicit_null_as_invalid_for_raw_and_loaded_contracts():
    raw = {"evidence_mode": "fixture", "consumes": None}
    loaded = _load_contract(raw)

    assert responsibility_summary(raw)["input_policy"] == "invalid"
    assert responsibility_summary(loaded)["input_policy"] == "invalid"


def test_orchestrator_show_projects_boundary_schema_without_history_payloads():
    store = _store()
    item = _make_item(store, TaskKind.DECOMPOSE, TaskPhase.AUTHORING)

    output = build_show_output(
        item, "orchestrator:alice")

    assert output["context"]["contract_boundary_schema"] == {
        "evidence_mode": ["fixture", "artifact", "live"],
        "produces": [{"artifact_id": "stable-artifact-id"}],
        "consumes": [{
            "artifact_id": "stable-artifact-id",
            "producer": "upstream-node-id",
            "evidence_mode": "artifact",
        }],
        "consumes_semantics": {
            "omitted": "transitional upstream inputs not yet enumerated",
            "empty": "no external inputs",
            "non_empty": "strict artifact allowlist",
            "null": "invalid; consumes must be omitted or a list",
        },
    }
    assert "deliverable" not in output["context"]["contract_boundary_schema"]


# amendment 与 decompose 一样含 authoring/review；final-acceptance 仅 authoring。
COMBINATIONS = [
    (TaskKind.PLAN, TaskPhase.AUTHORING),
    (TaskKind.PLAN, TaskPhase.REVIEW),
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING),
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW),
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING),
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW),
    (TaskKind.AMENDMENT, TaskPhase.AUTHORING),
    (TaskKind.AMENDMENT, TaskPhase.REVIEW),
    (TaskKind.DEVELOP, TaskPhase.AUTHORING),
    (TaskKind.DEVELOP, TaskPhase.REVIEW),
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING),
]


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_security_sensitive_protocol_is_develop_review_only(kind, phase):
    store = _store()
    item = _make_item(
        store,
        kind,
        phase,
        with_contract=(phase == TaskPhase.AUTHORING),
        with_deliverable=(phase == TaskPhase.REVIEW),
        with_verification=(kind == TaskKind.DEVELOP
                           and phase == TaskPhase.REVIEW),
    )
    identity = (
        f"worker:{item.worker}"
        if phase == TaskPhase.AUTHORING
        else f"reviewer:{item.reviewer}"
    )

    english = build_show_output(item, identity, language="en")["protocol"]
    chinese = build_show_output(item, identity, language="cn")["protocol"]
    is_develop_review = (
        kind == TaskKind.DEVELOP and phase == TaskPhase.REVIEW)

    has_english_guidance = (
        "authorized software-delivery review" in english.lower())
    has_chinese_guidance = "授权的软件交付评审" in chinese
    assert has_english_guidance is is_develop_review
    assert has_chinese_guidance is is_develop_review

    if not is_develop_review:
        return

    for phrase in (
        "including declared security-negative tests",
        "outside that declared scope",
        "do not initiate network probing",
        "construct, extend, or optimize",
        "summary, severity, impact, required_fix, and evidence",
        "do not reproduce sensitive payloads, credentials, network targets, "
        "or raw sensitive logs",
        "evidence boundary or environment blocker",
        "never fabricate a pass",
    ):
        assert phrase in english.lower()

    for phrase in (
        "已声明的安全负向测试",
        "仍必须执行",
        "只禁止 reviewer 在声明范围外自行增加网络探测",
        "构造、扩展、优化",
        "summary、severity、impact、required_fix 和 evidence",
        "不得在 reasoning/report 中复现敏感 payload、凭证、网络目标或原始敏感日志",
        "证据边界或环境阻塞",
        "不得伪造 pass",
    ):
        assert phrase in chinese.lower()

EXPECTED_GUIDE_REFS = {
    (TaskKind.PLAN, TaskPhase.AUTHORING): [
        "omac guide role planner", "omac guide artifact design"],
    (TaskKind.PLAN, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact design"],
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING): [
        "omac guide role planner", "omac guide artifact acceptance"],
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact acceptance"],
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING): [
        "omac guide role orchestrator", "omac guide artifact manifest"],
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact manifest"],
    (TaskKind.AMENDMENT, TaskPhase.AUTHORING): [
        "omac guide role orchestrator", "omac guide artifact manifest"],
    (TaskKind.AMENDMENT, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact manifest"],
    (TaskKind.DEVELOP, TaskPhase.AUTHORING): [
        "omac guide role worker", "omac guide artifact evidence"],
    (TaskKind.DEVELOP, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact evidence"],
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING): [
        "omac guide role acceptor", "omac guide artifact acceptance",
        "omac guide artifact evidence"],
}


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_show_output_structure(kind, phase):
    """每种组合都输出完整任务、上下文、协议、权威顺序、guide 与 submit。"""
    store = _store()
    with_contract = (phase == TaskPhase.AUTHORING)
    item = _make_item(store, kind, phase, with_contract=with_contract,
                      with_deliverable=(phase == TaskPhase.REVIEW),
                      with_verification=(kind == TaskKind.DEVELOP
                                         and phase == TaskPhase.REVIEW))
    identity = f"worker:{item.worker}" if phase == TaskPhase.AUTHORING \
        else f"reviewer:{item.reviewer}"
    out = build_show_output(item, identity)

    assert "task" in out
    assert "context" in out
    assert "protocol" in out
    assert "submit" in out
    assert out["control"] == {
        "platform_writes": "omac-only",
        "submit_is_terminal": True,
        "post_submit_actions": [],
        "submit_confirmation": {
            "may_run_long": True,
            "terminal_result_required": True,
            "wait_when": [
                "running",
                "session",
                "missing_tool_result",
                "incomplete_output",
                "unknown_result",
            ],
            "success_requires": {
                "exit_code": 0,
                "json": {
                    "ok": True,
                    "terminal": True,
                    "next_action": "stop",
                },
            },
            "validation_error": "fix-and-resubmit",
        },
    }

    # 任务标识
    assert out["task"]["kind"] == kind.value
    assert out["task"]["phase"] == phase.value
    assert out["task"]["dag_key"] == "a"
    assert out["task"]["identity"] == identity
    assert out["task"]["status"] == item.status.value
    assert out["task"]["blocked_by"] == item.blocked_by
    assert out["task"]["wave"] == item.wave
    assert out["task"]["bounces"] == item.bounces.as_dict()
    assert out["context"]["issue_description"] == item.description
    assert out["authority"] == [
        "Current facts from work show",
        "contract / previous_review",
        "role guide",
        "artifact guide",
        "workflow overview",
    ]
    assert out["guide_refs"] == EXPECTED_GUIDE_REFS[(kind, phase)]

    # 协议非空
    assert out["protocol"].strip() != ""
    assert "Do not edit platform status" in out["protocol"]
    assert "Submitting successfully is the final action" in out["protocol"]

    # submit 模板以 omac work submit <id> 开头
    assert out["submit"].startswith(f"omac work submit {item.id}")

    # authoring 阶段 context 含 contract
    if phase == TaskPhase.AUTHORING:
        assert "contract" in out["context"]

    # develop×review 阶段 context 含 env_setup 复跑清单
    if kind == TaskKind.DEVELOP and phase == TaskPhase.REVIEW:
        assert "env_setup" in out["context"]
        assert out["context"]["env_setup"] == ["docker compose up -d db"]
        assert out["context"]["artifacts"] == {
            "pr_url": "https://example.test/pr/42"}
        assert out["context"]["verification"]["coverage"] == 92


def test_reviewer_show_declares_reviewer_only_role_guard():
    store = _store()
    item = _make_item(
        store, TaskKind.DEVELOP, TaskPhase.REVIEW,
        with_deliverable=True, with_verification=True)

    output = build_show_output(item, "reviewer:bob", language="en")
    guard = output["context"]["reviewer_role_guard"]

    assert guard["role"] == "reviewer-only"
    assert guard["reviewer_only"] is True
    assert any("inspect current facts" in action for action in guard["allowed_actions"])
    assert any("generate a structured review report" in action for action in guard["allowed_actions"])
    assert any(
        "omac work submit <issue-id> --verdict" in action
        and "--report-file <report-file>" in action
        for action in guard["allowed_actions"]
    )
    for forbidden in (
        "omac dag amend propose",
        "omac dag amend accept",
        "modify the manifest",
        "modify platform state",
        "perform operator recovery",
    ):
        assert forbidden in guard["forbidden_actions"]
        assert forbidden in output["protocol"]


def test_worker_submit_success_requires_a_confirmed_terminal_tool_result():
    """Worker 不能把仍在运行、缺 tool_result 或未知结果口头当成提交成功。"""
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)

    english = build_show_output(item, f"worker:{item.worker}", language="en")
    chinese = build_show_output(item, f"worker:{item.worker}", language="cn")

    confirmation = english["control"]["submit_confirmation"]
    assert confirmation["may_run_long"] is True
    assert confirmation["terminal_result_required"] is True
    assert confirmation["success_requires"] == {
        "exit_code": 0,
        "json": {"ok": True, "terminal": True, "next_action": "stop"},
    }
    assert confirmation["validation_error"] == "fix-and-resubmit"
    for state in (
        "running",
        "session",
        "missing_tool_result",
        "incomplete_output",
        "unknown_result",
    ):
        assert state in confirmation["wait_when"]

    for phrase in (
        "omac work show",
        "omac work submit",
        "may run for a long time",
        "empty or incomplete output",
        "timeout/yield is not an empty result",
        "sufficiently long wait/yield",
        "retain and resume the continuation",
        "wait or poll",
        "final tool result",
        "exit code 0",
        '"ok": true',
        '"terminal": true',
        '"next_action": "stop"',
        "must not claim success",
        "fix it and submit again",
    ):
        assert phrase in english["protocol"].lower()

    for phrase in (
        "omac work show",
        "omac work submit",
        "可能长时间运行",
        "空输出或不完整输出",
        "timeout/yield 不是空结果",
        "足够长的 wait/yield",
        "保留并恢复续接句柄",
        "等待或轮询",
        "最终 tool_result",
        "退出码 0",
        '"ok": true',
        '"terminal": true',
        '"next_action": "stop"',
        "不得宣称成功",
        "修复后重新提交",
    ):
        assert phrase in chinese["protocol"].lower()


def test_authoring_show_uses_review_ledger_after_current_projection_reset():
    """reject reset 后旧 report 失效，worker 从 ledger 读取持久化返工义务。"""
    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING, with_contract=True)
    report = {
        "verdict": "reject",
        "blockers": ["缺少积分体系的持久化方案"],
        "nits": ["补充排行榜刷新策略"],
    }
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [{
            "round": 1,
            "subject_digest": "review-subject-1",
            "verdict": "reject",
        }],
        "blockers": [{
            "blocker_id": "BLK-persistence",
            "obligation_id": "dimension:structure",
            "root_cause_key": "missing-persistence",
            "summary": "缺少积分体系的持久化方案",
            "required_fix": "补充积分持久化设计",
            "status": "open",
        }],
    }
    store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_report=report,
        review_report_source="/tmp/omac-review-report.yaml",
        review_ledger=ledger,
        review_ledger_source="/tmp/omac-review-ledger.yaml",
        review_generation="review-generation-1",
        review_ledger_generation="review-generation-1",
    )
    store.reset_review(item.id)

    out = build_show_output(store.get_work_item(item.id), "worker:alice")

    assert "previous_review" not in out["context"]
    assert out["context"]["required_closures"] == [{
        "blocker_id": "BLK-persistence",
        "obligation_id": "dimension:structure",
        "root_cause_key": "missing-persistence",
        "summary": "缺少积分体系的持久化方案",
        "required_fix": "补充积分持久化设计",
    }]
    assert out["context"]["review_ledger_ref"]["filename"] == (
        "omac-review-ledger.yaml")


def test_authoring_show_does_not_project_ledger_from_previous_review_generation():
    """普通 reject 保持同代 ledger；amendment 切代后旧 ledger 只剩审计价值。"""
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [{"round": 1, "new_count": 1}],
        "blockers": [{
            "blocker_id": "BLK-old",
            "obligation_id": "dimension:structure",
            "root_cause_key": "old-generation",
            "summary": "old generation blocker",
            "required_fix": "historical only",
            "status": "open",
        }],
    }
    store.update_work_item_metadata(
        item.id,
        review_ledger=ledger,
        review_ledger_source=yaml.safe_dump(ledger, sort_keys=False),
    )
    current = store.get_work_item(item.id)
    current.review_generation = "amendment-aiteam-850"
    current.review_ledger_generation = "review-aiteam-849"

    out = build_show_output(current, "worker:alice")

    assert "review_state" not in out["context"]
    assert "required_closures" not in out["context"]
    assert current.review_ledger == ledger
    assert current.review_ledger_ref is not None


def test_work_show_distinguishes_absolute_bounces_from_current_generation_budget():
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    store.update_work_item_metadata(
        item.id, worker_bounce=17, review_bounce=3, merge_bounce=0)
    current = store.get_work_item(item.id)
    current.bounce_baseline = {"worker": 14, "review": 3, "merge": 0}

    out = build_show_output(current, "worker:alice")

    assert out["task"]["bounces"]["worker_bounce"] == 17
    assert out["task"]["bounce_budget"] == {
        "counter_semantics": "absolute-audit",
        "absolute": {"worker": 17, "review": 3, "merge": 0},
        "current_generation": {
            "baseline": {"worker": 14, "review": 3, "merge": 0},
            "consumed": {"worker": 3, "review": 0, "merge": 0},
        },
    }


def test_review_submit_does_not_advance_raw_ledger_from_previous_generation(
    tmp_path, monkeypatch,
):
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.REVIEW,
                      with_contract=True)
    old_ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [{"round": 1}],
        "blockers": [],
    }
    store.update_work_item_metadata(
        item.id,
        review_generation="amendment-aiteam-850",
        review_ledger_generation="review-aiteam-849",
        review_ledger=old_ledger,
        review_ledger_source=yaml.safe_dump(old_ledger),
        review_subject_digest="subject-new-generation",
        review_obligations=[{"obligation_id": "dimension:authority"}],
    )
    report_file = tmp_path / "review.yaml"
    report_file.write_text("verdict: pass\n")
    observed = []

    monkeypatch.setattr(
        dispatch_mod, "_validate_review",
        lambda *_args, **_kwargs: {"verdict": "pass"})

    def advance(previous, *_args, **_kwargs):
        observed.append(previous)
        return {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": []}

    monkeypatch.setattr(dispatch_mod, "advance_review_ledger", advance)

    dispatch_mod.submit(
        store, item.id, verdict="pass", report_file=str(report_file))

    assert observed == [None]
    current = store.get_work_item(item.id)
    assert current.review_ledger_generation == "amendment-aiteam-850"


def test_first_review_in_new_generation_starts_a_fresh_ledger(tmp_path):
    """Reviewer validation and ledger persistence must use one current projection."""
    store = _store()
    item = store.create_work_item(
        "mock-workspace", "develop", "desc", dag_key="develop-generation",
        worker="alice", reviewer="bob", kind=TaskKind.DEVELOP,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    store.set_node_contract(item.id, Contract(
        objective="do it",
        acceptance=["works"],
        verification_commands=["pytest -q"],
        integration_gates=[],
        pr_base="main",
    ))
    item = store.get_work_item(item.id)
    obligations = build_review_obligations(item)

    def rejected_report(evidence):
        return {
            "review_protocol": REVIEW_PROTOCOL_VERSION,
            "full_review_completed": True,
            "obligation_results": [{
                "obligation_id": obligation["obligation_id"],
                "status": (
                    "fail"
                    if obligation["obligation_id"] == "dimension:structure"
                    else "pass"
                ),
                "evidence": evidence,
            } for obligation in obligations],
            "prior_blocker_results": [],
            "blockers": [{
                "root_cause_key": "same-root",
                "obligation_id": "dimension:structure",
                "classification": "new",
                "summary": "generation-local blocker",
                "evidence": evidence,
                "required_fix": "fix this generation",
            }],
            "nits": [],
            "review_goals": ["review the current generation"],
            "diff_reviewed": True,
            "tests_rerun": True,
            "coverage_checked": True,
            "acceptance_mapping": [{"acceptance": "works", "status": "fail"}],
        }

    old_report = rejected_report("old generation evidence")
    old_ledger = advance_review_ledger(
        None, old_report, verdict="reject",
        subject_digest="old-subject", round_index=1,
    )
    store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_obligations=obligations,
        review_ledger=old_ledger,
        review_ledger_source=yaml.safe_dump(old_ledger, sort_keys=False),
        review_generation="review-old",
        review_ledger_generation="review-old",
        review_subject_digest="new-subject",
    )
    store.update_work_item_metadata(
        item.id,
        review_generation="amendment-new",
        review_ledger_generation="review-old",
        review_verdict="",
        review_report={},
        decision_required={},
        phase=TaskPhase.REVIEW,
    )
    report_file = tmp_path / "report.yaml"
    report_file.write_text(yaml.safe_dump(
        rejected_report("new generation evidence"), sort_keys=False))

    dispatch_mod.submit(
        store, item.id, verdict="reject", report_file=str(report_file))

    current = store.get_work_item(item.id)
    assert current.review_ledger_generation == "amendment-new"
    assert len(current.review_ledger["cycles"]) == 1
    assert current.review_ledger["cycles"][0]["subject_digest"] == "new-subject"
    assert current.review_ledger["blockers"][0]["classification"] == "new"


def test_authoring_show_includes_source_issue_refs():
    """worker 只拿 issue id 时,work show 必须给出上游 issue 链路。"""
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING, with_contract=True)
    store.update_work_item_metadata(
        item.id,
        source_refs=[
            {"label": "设计方案", "issue_id": "plan-1",
             "url": "https://multica.ai/workspaces/ws/issues/plan-1"},
            {"label": "验收文档", "issue_id": "acc-1"},
        ],
    )

    out = build_show_output(store.get_work_item(item.id), "worker:alice")

    assert out["context"]["source_issues"] == [
        {"label": "设计方案", "issue_id": "plan-1",
         "url": "https://multica.ai/workspaces/ws/issues/plan-1"},
        {"label": "验收文档", "issue_id": "acc-1"},
    ]


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_submit_template_matches_registered_params(kind, phase):
    """submit 模板使用的参数名必须与 SUBMIT_PARAM_SPECS 注册的一致(防漂移)。"""
    template = submit_template_for(kind, phase, "42")
    expected_params = SUBMIT_PARAMS_BY_KIND_PHASE[(kind, phase)]
    # 模板中每个 --xxx 都出现在注册表中
    for param in expected_params:
        assert param in template, f"模板缺少参数 {param}: {template}"
    # 模板中不应出现未注册的 -- 参数
    import re
    used_flags = re.findall(r"--\w+(?:-\w+)*", template)
    for flag in used_flags:
        assert flag in SUBMIT_PARAM_SPECS, \
            f"模板使用了未注册参数 {flag}: {template}"


def test_all_kind_phase_pairs_covered():
    """全部受支持组合都在 SUBMIT_PARAMS_BY_KIND_PHASE 中有定义。"""
    assert set(SUBMIT_PARAMS_BY_KIND_PHASE.keys()) == set(COMBINATIONS)


def test_show_cli_json_output(tmp_path, monkeypatch, capsys):
    """CLI 入口:work show --output json 输出合法 JSON,exit 0。"""
    monkeypatch.chdir(tmp_path)
    # 写配置指向 mock 引擎
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)

    assert main(["work", "show", item.id, "--output", "json"]) == exit_codes.OK
    import json
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["task"]["kind"] == "develop"
    assert data["task"]["phase"] == "authoring"
    assert data["context"]["contract"]["objective"] == "实现 X"
    assert data["submit"].startswith(f"omac work submit {item.id}")


def test_work_show_legacy_convergence_snapshot_returns_structured_exit_20(
    aiteam_849_legacy_snapshot, monkeypatch, capsys,
):
    snapshot = aiteam_849_legacy_snapshot["work_item"]
    store = _store()
    item = store.create_work_item(
        "mock-workspace",
        "legacy convergence snapshot",
        "redacted production snapshot",
        dag_key=snapshot["dag_key"],
        worker=snapshot["worker_handoff"]["target_worker"],
        reviewer="reviewer-redacted",
        kind=TaskKind(snapshot["kind"]),
        initial_status=WorkItemStatus(snapshot["status"]),
    )
    store.update_work_item_metadata(
        item.id,
        phase=TaskPhase(snapshot["phase"]),
        worker_bounce=snapshot["bounces"]["worker"],
        ci_bounce=snapshot["bounces"]["ci"],
        review_bounce=snapshot["bounces"]["review"],
        merge_bounce=snapshot["bounces"]["merge"],
        decision_required=snapshot["decision_required"],
        review_ledger=snapshot["review_ledger"],
        worker_handoff=snapshot["worker_handoff"],
    )
    monkeypatch.setattr(work_cmd, "_resolve_store", lambda: store)

    assert main(["work", "show", item.id, "--output", "json"]) == (
        exit_codes.NEEDS_DECISION
    )
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["ok"] is False
    assert output["decision_required"]["reason_code"] == (
        "review-convergence-ledger-unverifiable"
    )
    assert output["exit_code"] == exit_codes.NEEDS_DECISION
    assert output["next_action"].startswith("omac dag amend propose ")
    assert "Traceback" not in captured.err


def test_show_cli_defaults_to_agent_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING)

    assert main(["work", "show", item.id]) == exit_codes.OK
    data = json.loads(capsys.readouterr().out)
    assert data["task"]["title"] == item.title
    assert data["guide_refs"] == EXPECTED_GUIDE_REFS[
        (TaskKind.PLAN, TaskPhase.AUTHORING)]


def test_work_read_materializes_named_upstream_deliverable(
    tmp_path, monkeypatch, capsys,
):
    """Agent 可通过当前 issue 的稳定 source label 读取上游附件正文。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    upstream = store.create_work_item(
        "mock-workspace", "acceptance", "acceptance",
        dag_key="acceptance-p1", worker="alice",
        kind=TaskKind.ACCEPTANCE)
    store.update_work_item_metadata(
        upstream.id, deliverable="schema: omac.acceptance/v1\nflows: []\n")
    current = store.create_work_item(
        "mock-workspace", "decompose", "decompose",
        dag_key="decompose-p1", worker="bob", kind=TaskKind.DECOMPOSE)
    store.update_work_item_metadata(
        current.id,
        source_refs=[{"label": "acceptance", "issue_id": upstream.id}],
    )
    output_file = tmp_path / "acceptance.yaml"

    assert main([
        "work", "read", current.id,
        "--source", "acceptance",
        "--output-file", str(output_file),
    ]) == exit_codes.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["source"] == "acceptance"
    assert payload["source_issue_id"] == upstream.id
    assert payload["bytes"] == output_file.stat().st_size
    assert output_file.read_text() == "schema: omac.acceptance/v1\nflows: []\n"


def test_work_show_localizes_omac_prose_without_changing_facts(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    main(["config", "set", "language", "cn"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING,
                      with_contract=True)

    assert main(["work", "show", item.id]) == exit_codes.OK
    chinese = json.loads(capsys.readouterr().out)

    main(["config", "set", "language", "en"])
    capsys.readouterr()
    assert main(["work", "show", item.id]) == exit_codes.OK
    english = json.loads(capsys.readouterr().out)

    assert chinese["protocol"] != english["protocol"]
    assert english["protocol"].startswith("Write two required artifacts:")
    assert chinese["authority"] != english["authority"]
    assert english["authority"][0] == "Current facts from work show"
    assert chinese["task"] == english["task"]
    assert chinese["context"] == english["context"]
    assert chinese["guide_refs"] == english["guide_refs"]
    assert chinese["submit"] == english["submit"]


def test_show_cli_table_output(tmp_path, monkeypatch, capsys):
    """人类调试可显式请求 markdown 相位视图。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.REVIEW,
                      with_deliverable=True)

    assert main(["work", "show", item.id, "--output", "table"]) == exit_codes.OK
    out = capsys.readouterr().out
    # markdown 段头(相位视图):任务头 / 现在做什么 / 完成后交付
    assert "# Task" in out
    assert "## What to do now" in out
    assert "## Submit when finished" in out
    assert "plan" in out


def test_show_cli_source_issue_commands_include_engine_env(tmp_path, monkeypatch, capsys):
    """work show 输出的上游 issue 命令也必须可复制执行。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    store.update_work_item_metadata(
        item.id,
        source_refs=[{"label": "设计方案", "issue_id": "plan-1"}],
    )

    assert main(["work", "show", item.id, "--output", "table"]) == exit_codes.OK
    out = capsys.readouterr().out

    assert "## Upstream issues (stay on target)" in out
    assert "OMAC_ENGINE=mock OMAC_WORKSPACE_ID=mock-workspace omac work show plan-1 --output json" in out


def test_show_identity_reflects_role_not_generic_worker(tmp_path, monkeypatch, capsys):
    """身份按角色如实标注:plan×authoring 是 planner,不再一律标 worker。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING)
    assert main(["work", "show", item.id, "--output", "table"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "planner" in out
    assert "worker:" not in out  # plan 的产出者不是 worker


def test_plan_authoring_action_not_role_mixed():
    """点5:plan×authoring 的「现在做什么」只讲 plan,不掺 acceptance 任务;深度指向 guide。"""
    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING)
    out = build_show_output(item, "worker:alice")
    proto = out["protocol"]
    # 不再把 acceptance(验收文档)任务塞进 plan 的视图
    assert "Write the acceptance document:" not in proto
    assert out["guide_refs"] == [
        "omac guide role planner", "omac guide artifact design"]
    assert "omac guide" not in proto


def test_review_show_surfaces_deliverable_and_env_setup(tmp_path, monkeypatch, capsys):
    """review 阶段 show 顶出只有此刻才存在的实例数据:评审对象(deliverable)+ env_setup。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.REVIEW,
                      with_deliverable=True, with_verification=True)
    assert main(["work", "show", item.id, "--output", "table"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "Review target" in out
    assert "docker compose up -d db" in out  # worker 的 env_setup 复跑清单
    assert "omac guide role reviewer" in out


def test_set_node_contract_visible_in_show():
    """回归:set_node_contract 下发的 contract 必须在 work show 中可见(真实 dispatch 路径)。

    这是被派发 agent 第一入口的关键链路:dispatch 侧调用 set_node_contract 下发契约,
    被派发 agent 调 work show 必须能读回完整 contract,否则拿到的是空上下文。
    """
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    # 从 store 重新读取(模拟 agent 侧 get_work_item),确认 contract 已持久化
    got = store.get_work_item(item.id)
    assert got.contract is not None, (
        "set_node_contract 后 WorkItem.contract 必须非空,否则 work show 拿不到上下文")
    assert got.contract["objective"] == "实现 X"
    # 走 build_show_output 验证上下文完整
    out = build_show_output(got, f"worker:{got.worker}")
    assert out["context"]["contract"] is not None
    assert out["context"]["contract"]["acceptance"] == ["A 工作", "B 工作"]
    assert out["context"]["contract"]["pr_base"] == "feature/v1"




def test_develop_authoring_action_and_submit_cover_pr_flow():
    """develop x authoring:「现在做什么」点明推分支/开 PR/worker 自建;
    精确的 --pr-url 交付命令在 submit 段(不再把整条命令塞进协议文本)。"""
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
    out = build_show_output(item, f"worker:{item.worker}")
    protocol = out["protocol"]
    # 动作点明 PR 三步的要害
    assert "Push a branch" in protocol, protocol
    assert "PR" in protocol, protocol
    assert "worker creates it" in protocol, protocol
    assert "do not manually change the issue status" in protocol
    # 精确交付命令归 submit 段(相位视图:动作与命令分离)
    assert "--pr-url" in out["submit"]


def test_work_resolve_store_preserves_workspace_slug_from_config(tmp_path, monkeypatch):
    """work show 渲染上游 issue 链需要 workspace_slug 才能生成 mention 链接。"""
    from omac.cli.commands import work as work_cmd

    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".omac"
    cfg_dir.mkdir()
    with open(cfg_dir / "config.yaml", "w") as f:
        yaml.safe_dump({
            "engine": "mock",
            "workspace": "mock-workspace",
            "workspace_slug": "guantik-aiteam",
        }, f)

    store = work_cmd._resolve_store()

    assert store.config.extra["workspace_slug"] == "guantik-aiteam"


def test_develop_show_mentions_issue_key_for_pr_autolink():
    """有平台 issue key 时,work show 指导 worker 让 PR 自动关联到 Multica issue。"""
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
    item.identifier = "AITEAM-762"

    out = build_show_output(item, f"worker:{item.worker}")

    assert out["task"]["issue_key"] == "AITEAM-762"
    assert "AITEAM-762" in out["protocol"]
    assert "branch name, title, or body" in out["protocol"]


def test_show_treats_in_review_status_as_review_phase():
    """旧 issue 缺 phase=review 时,平台 in_review 仍应给 reviewer 正确上下文。"""
    store = _store()
    item = _make_item(
        store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
        with_contract=True, with_verification=True)
    store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://x/pr/1"})
    store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    got = store.get_work_item(item.id)
    out = build_show_output(got, f"reviewer:{got.reviewer}")

    assert out["task"]["phase"] == "review"
    assert "deliverable" in out["context"]
    assert "env_setup" in out["context"]
    assert "--verdict" in out["submit"]


def test_plan_review_show_surfaces_project_rules(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()
    store = _store()
    item = _make_item(
        store,
        TaskKind.PLAN,
        TaskPhase.REVIEW,
        with_deliverable=True,
    )
    store.update_work_item_metadata(
        item.id,
        project_rules="## Project rules\n\n- Preserve compatibility.\n",
    )

    assert main(["work", "show", item.id]) == exit_codes.OK
    out = json.loads(capsys.readouterr().out)
    assert out["context"]["project_rules"].startswith("## Project rules")


def test_show_unknown_issue_id(tmp_path, monkeypatch, capsys):
    """issue_id 不存在时给出教学性报错,exit 5。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    assert main(["work", "show", "99999"]) == exit_codes.VALIDATION
    err = json.loads(capsys.readouterr().err)
    assert err["ok"] is False
    assert err["error"]["exit_code"] == exit_codes.VALIDATION
    assert "99999" in err["error"]["message"]



def _config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "false"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="mock-workspace", extra=base)


def _engine(**extra):
    return create_engine("mock", _config(**extra))


CONTRACT = Contract(
    objective="do it",
    acceptance=["works"],
    non_goals=["no creep"],
    verification_commands=["pytest -q"],
    integration_gates=[{
        "name": "gate-1", "layer": "L1", "delivery_goal": "delivers",
        "source_of_truth": ["docs/d.md"], "covers": ["route"],
        "acceptance_refs": ["works"], "commands": ["pytest tests/int"],
        "required_metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
    }],
    pr_base="feature/v1",
    coverage_gate=90,
)


def _make_verification(pr_base="feature/v1", coverage=95):
    return {
        "commands": [{
            "cmd": "pytest -q",
            "exit_code": 0,
            "business_tests": [{
                "acceptance": "works",
                "test": "tests/test_feature.py::test_feature_works",
            }],
        }],
        "integration_gates": [{
            "name": "gate-1",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"],
            "delivery_goal": "delivers",
        }],
        "env_setup": ["pip install -r requirements.txt", "docker compose up -d db"],
        "pr_base": pr_base,
        "coverage": coverage,
    }


def _make_review_report(integration_gates=True):
    report = {
        "review_goals": ["验收映射覆盖 contract.acceptance"],
        "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "full_review_completed": True,
        "acceptance_mapping": [{"acceptance": "works", "status": "pass"}],
        "blockers": [], "nits": [],
    }
    if integration_gates:
        report["integration_tests_rerun"] = True
        report["integration_gate_mapping"] = [{
            "gate": "gate-1", "status": "pass",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers",
        }]
    return report


class _SelectiveSubmitStore:
    """Expose control facts while making unrelated attachment reads fail loudly."""

    def __init__(self, backing, item, *, fail_on=()):
        self._backing = backing
        self._source = deepcopy(item)
        self.config = backing.config
        self.fail_on = frozenset(fail_on)
        self.observe_calls = 0
        self.full_get_calls = 0
        self.hydration_plans = []

        control = deepcopy(item)
        for payload in WorkItemPayload:
            setattr(control, payload.value, None)
        self._projection = WorkItemControlProjection(
            control,
            frozenset(WorkItemPayload),
        )

    def __getattr__(self, name):
        return getattr(self._backing, name)

    def get_work_item(self, item_id):
        self.full_get_calls += 1
        raise AssertionError("submit must not use complete work-item hydration")

    def observe_work_item_control(self, item_id):
        assert item_id == self._source.id
        self.observe_calls += 1
        return self._projection

    def hydrate_work_item_evidence(self, projection, plan):
        requested = frozenset(plan)
        self.hydration_plans.append(requested)
        unavailable = requested & self.fail_on
        if unavailable:
            names = ", ".join(sorted(payload.value for payload in unavailable))
            raise PlatformError(f"attachment download timeout: {names}")
        hydrated = deepcopy(projection.work_item)
        for payload in requested:
            setattr(hydrated, payload.value, deepcopy(
                getattr(self._source, payload.value)))
        return hydrated


@pytest.mark.parametrize(("kind", "phase", "expected"), [
    (TaskKind.DEVELOP, TaskPhase.AUTHORING, {WorkItemPayload.CONTRACT}),
    (TaskKind.DEVELOP, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.PLAN, TaskPhase.AUTHORING, set()),
    (TaskKind.PLAN, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.PROJECT_RULES,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING, set()),
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING, set()),
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.AMENDMENT, TaskPhase.AUTHORING, set()),
    (TaskKind.AMENDMENT, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING, {
        WorkItemPayload.CONTRACT,
    }),
])
def test_submit_hydration_plan_is_explicit_by_kind_and_phase(
    kind, phase, expected,
):
    assert dispatch_mod.submit_hydration_plan(kind, phase) == frozenset(expected)


def test_submit_hydration_registry_covers_every_supported_spec():
    supported = {
        (kind, phase)
        for kind, phases in dispatch_mod.SPECS.items()
        for phase in phases
    }

    assert set(dispatch_mod.SUBMIT_HYDRATION_BY_KIND_PHASE) == supported


def test_develop_authoring_submit_ignores_unrelated_historical_attachments(
    tmp_path,
):
    backing = _store()
    item = backing.create_work_item(
        "mock-workspace", "develop", "desc", dag_key="develop",
        worker="alice", reviewer="bob", kind=TaskKind.DEVELOP,
    )
    backing.set_node_contract(item.id, CONTRACT)
    item = backing.get_work_item(item.id)
    store = _SelectiveSubmitStore(
        backing,
        item,
        fail_on={
            WorkItemPayload.VERIFICATION,
            WorkItemPayload.REVIEW_REPORT,
            WorkItemPayload.REVIEW_LEDGER,
            WorkItemPayload.REVIEW_OBLIGATIONS,
            WorkItemPayload.DELIVERABLE,
            WorkItemPayload.PROJECT_RULES,
        },
    )
    verification_file = tmp_path / "verification.yaml"
    verification_file.write_text(yaml.safe_dump(_make_verification()))

    result = dispatch_mod.submit(
        store,
        item.id,
        pr_url="https://example.test/pr/42",
        verification_file=str(verification_file),
    )

    assert result.advanced_to is WorkItemStatus.DONE
    assert store.full_get_calls == 0
    assert store.observe_calls == 1
    assert store.hydration_plans == [frozenset({WorkItemPayload.CONTRACT})]
    assert backing.get_work_item(item.id).verification is not None


def test_cli_submit_does_not_preload_complete_work_item(
    tmp_path, monkeypatch, capsys,
):
    backing = _store()
    item = backing.create_work_item(
        "mock-workspace", "develop", "desc", dag_key="develop",
        worker="alice", reviewer="bob", kind=TaskKind.DEVELOP,
    )
    backing.set_node_contract(item.id, CONTRACT)
    item = backing.get_work_item(item.id)
    store = _SelectiveSubmitStore(backing, item)
    monkeypatch.setattr(work_cmd, "_resolve_store", lambda: store)
    verification_file = tmp_path / "verification.yaml"
    verification_file.write_text(yaml.safe_dump(_make_verification()))

    rc = main([
        "work", "submit", item.id,
        "--pr-url", "https://example.test/pr/42",
        "--verification-file", str(verification_file),
    ])

    assert rc == exit_codes.OK, capsys.readouterr()
    assert store.full_get_calls == 0
    assert store.observe_calls == 1
    assert store.hydration_plans == [frozenset({WorkItemPayload.CONTRACT})]


@pytest.mark.parametrize("read_error", [
    PlatformError("attachment service unavailable"),
    AuthError("multica authentication expired"),
])
def test_cli_submit_preserves_work_item_read_failure_contract(
    tmp_path, monkeypatch, capsys, read_error,
):
    class ReadFailureStore:
        config = EngineConfig(
            engine_type="multica", workspace_id="workspace-1")

        def list_members(self, workspace_id):
            return []

        def observe_work_item_control(self, item_id):
            raise read_error

    monkeypatch.setattr(work_cmd, "_resolve_store", ReadFailureStore)

    rc = main([
        "work", "submit", "issue-1",
        "--pr-url", "https://example.test/pr/42",
        "--verification-file", str(tmp_path / "verification.yaml"),
    ])

    assert rc == exit_codes.VALIDATION
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["exit_code"] == exit_codes.VALIDATION
    assert error["error"]["message"] == (
        f"Could not read work item 'issue-1': {read_error}")


def test_review_submit_fails_closed_when_required_target_is_unavailable(
    tmp_path,
):
    backing = _store()
    item = backing.create_work_item(
        "mock-workspace", "plan", "desc", dag_key="plan",
        worker="alice", reviewer="bob", kind=TaskKind.PLAN,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    backing.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        deliverable="# Plan\n",
        project_rules="## Rules\n",
    )
    item = backing.get_work_item(item.id)
    store = _SelectiveSubmitStore(
        backing, item, fail_on={WorkItemPayload.DELIVERABLE})
    report_file = tmp_path / "report.yaml"
    report_file.write_text(yaml.safe_dump(_make_review_report()))

    with pytest.raises(PlatformError, match="deliverable"):
        dispatch_mod.submit(
            store,
            item.id,
            verdict="pass",
            report_file=str(report_file),
        )

    assert backing.get_work_item(item.id).review_verdict is None


def test_submit_hydration_accepts_empty_review_obligations_and_ledger():
    backing = _store()
    item = backing.create_work_item(
        "mock-workspace", "develop", "desc", dag_key="develop",
        worker="alice", reviewer="bob", kind=TaskKind.DEVELOP,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    backing.set_node_contract(item.id, CONTRACT)
    backing.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW)
    item = backing.get_work_item(item.id)
    item.review_obligations = []
    item.review_ledger = {}
    store = _SelectiveSubmitStore(backing, item)

    hydrated, kind, phase = dispatch_mod.load_submit_context(store, item.id)

    assert kind is TaskKind.DEVELOP
    assert phase is TaskPhase.REVIEW
    assert hydrated.review_obligations == []
    assert hydrated.review_ledger == {}


# ==================== 参数校验(直接调 dispatch.validate_params) ===========================

class TestParamValidation:

    def test_develop_authoring_missing_param(self):
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.validate_params(
                dispatch_mod.TaskKind.DEVELOP,
                dispatch_mod.TaskPhase.AUTHORING,
                {"pr_url": "https://x/pr/1"},  # 缺 verification_file
            )
        msg = str(exc.value)
        assert "verification-file" in msg
        assert "Missing parameters" in msg

    def test_plan_authoring_extra_param(self):
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.validate_params(
                dispatch_mod.TaskKind.PLAN,
                dispatch_mod.TaskPhase.AUTHORING,
                {"plan_file": "p.md", "verdict": "pass"},  # verdict 多余
            )
        assert "多余" in str(exc.value) or "verdict" in str(exc.value)

    def test_develop_authoring_correct_passes(self):
        # 不应抛
        dispatch_mod.validate_params(
            dispatch_mod.TaskKind.DEVELOP,
            dispatch_mod.TaskPhase.AUTHORING,
            {"pr_url": "https://x/pr/1", "verification_file": "v.yaml"},
        )

    def test_final_acceptance_has_no_review(self):
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.validate_params(
                dispatch_mod.TaskKind.FINAL_ACCEPTANCE,
                dispatch_mod.TaskPhase.REVIEW,
                {"verdict": "pass", "report_file": "r.yaml"},
            )
        assert "final-acceptance" in str(exc.value)

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError):
            dispatch_mod._kind("not-a-kind")


# ==================== 每个 kind × phase 成功 + 内容校验打回 ====================

class TestSubmitPerKindPhase:

    def test_develop_authoring_rejects_verification_without_business_tests(
            self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        verification = _make_verification()
        del verification["commands"][0]["business_tests"]
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(verification))

        with pytest.raises(ValidationError, match="missing business test for acceptance"):
            dispatch_mod.submit(
                eng.store, item.id,
                pr_url="https://x/pr/1", verification_file=str(vfile),
            )

        got = eng.store.get_work_item(item.id)
        assert got.verification is None
        assert got.status == WorkItemStatus.TODO

    def test_review_rejects_report_without_full_review_completed(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            reviewer="bob", kind=dispatch_mod.TaskKind.DEVELOP,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        item.phase = dispatch_mod.TaskPhase.REVIEW
        eng.store.set_node_contract(item.id, CONTRACT)
        report = _make_review_report()
        del report["full_review_completed"]
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(report))

        with pytest.raises(ValidationError, match="full_review_completed"):
            dispatch_mod.submit(
                eng.store, item.id, verdict="pass", report_file=str(rfile),
            )

        got = eng.store.get_work_item(item.id)
        assert got.review_report is None
        assert got.review_verdict is None

    # ---------- develop ----------

    def test_develop_authoring_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))

        result = dispatch_mod.submit(
            eng.store, item.id,
            pr_url="https://x/pr/1", verification_file=str(vfile),
        )
        assert result.kind == dispatch_mod.TaskKind.DEVELOP
        assert result.phase == dispatch_mod.TaskPhase.AUTHORING
        assert result.advanced_to == WorkItemStatus.DONE
        got = eng.store.get_work_item(item.id)
        assert got.artifacts == {"pr_url": "https://x/pr/1"}
        assert got.verification["pr_base"] == "feature/v1"
        assert got.verification_ref["filename"] == "omac-verification.yaml"
        assert got.status == WorkItemStatus.DONE

    def test_develop_rework_submit_persists_candidate_without_sealing_identity(
        self, tmp_path, monkeypatch,
    ):
        """Agent submit 只写候选交付，因果 identity 由 controller 封装。"""
        from omac.core.taskmeta import WorkerHandoffIntent

        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            reviewer="bob", kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        eng.store.assign_work_item(item.id, "alice", "worker")
        target_run = eng.runtime.list_runs(item.id)[-1]
        intent = WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="review",
            source_review_subject_digest="subject-1",
            source_review_round=1,
            target_review_bounce=1,
            generation="handoff-generation-1",
            target_agent_id=eng.store.resolve_agent_id("alice"),
            baseline_direct_run_ids=(),
            target_run_id=target_run.id,
        )
        eng.store.update_work_item_metadata(
            item.id, worker_handoff=intent, phase=TaskPhase.AUTHORING)
        eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
        vfile = tmp_path / "verification.yaml"
        verification_source = yaml.safe_dump(_make_verification())
        vfile.write_text(verification_source)
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: PullRequestReadiness(
                is_draft=False, state="OPEN", head_sha="head-new"),
        )

        dispatch_mod.submit(
            eng.store,
            item.id,
            pr_url="https://github.com/acme/snake/pull/1",
            verification_file=str(vfile),
        )

        got = eng.store.get_work_item(item.id)
        assert got.delivery_identity is None
        assert got.artifacts["head_sha"] == "head-new"

    def test_develop_rework_submit_does_not_trust_environment_actor_hints(
        self, tmp_path, monkeypatch,
    ):
        """Agent 可覆盖的环境变量不会被封装为 delivery identity。"""
        from omac.core.taskmeta import WorkerHandoffIntent

        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            reviewer="bob", kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        intent = WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="review",
            source_review_subject_digest="subject-1",
            source_review_round=1,
            target_review_bounce=1,
            generation="handoff-generation-1",
            target_agent_id=eng.store.resolve_agent_id("alice"),
            baseline_direct_run_ids=("run-old",),
            target_run_id="run-new",
        )
        eng.store.update_work_item_metadata(
            item.id, worker_handoff=intent, phase=TaskPhase.AUTHORING)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))
        monkeypatch.setattr(
            eng.store,
            "read_pull_request_readiness",
            lambda _pr_url: PullRequestReadiness(
                is_draft=False, state="OPEN", head_sha="head-new"),
        )
        monkeypatch.setenv("MULTICA_AGENT_ID", eng.store.resolve_agent_id("bob"))
        monkeypatch.setenv("MULTICA_AGENT_NAME", "bob")
        monkeypatch.setenv("MULTICA_TASK_ID", "run-old")

        dispatch_mod.submit(
            eng.store,
            item.id,
            pr_url="https://github.com/acme/snake/pull/1",
            verification_file=str(vfile),
        )

        got = eng.store.get_work_item(item.id)
        assert got.delivery_identity is None
        assert got.status == WorkItemStatus.DONE

    def test_develop_authoring_content_rejected_atomic(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification(coverage=50)))  # 低于 gate

        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(
                eng.store, item.id,
                pr_url="https://x/pr/1", verification_file=str(vfile),
            )
        assert "gate" in str(exc.value).lower() or "coverage" in str(exc.value).lower()
        got = eng.store.get_work_item(item.id)
        assert got.artifacts is None
        assert got.verification is None
        assert got.status == WorkItemStatus.TODO

    def test_develop_authoring_rejects_github_draft_pr_atomic(self, tmp_path, monkeypatch):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))

        store = eng.store
        monkeypatch.setattr(
            store, "read_pull_request_readiness",
            lambda pr_url: PullRequestReadiness(is_draft=True, state="OPEN"),
        )

        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(
                eng.store, item.id,
                pr_url="https://github.com/acme/snake/pull/1",
                verification_file=str(vfile),
            )

        assert "draft" in str(exc.value).lower()
        got = eng.store.get_work_item(item.id)
        assert got.artifacts is None
        assert got.verification is None
        assert got.status == WorkItemStatus.TODO

    def test_develop_authoring_rejects_unknown_readiness_result(self, tmp_path, monkeypatch):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))
        monkeypatch.setattr(
            eng.store, "read_pull_request_readiness",
            lambda pr_url: PullRequestReadinessFailure("unknown", "bad"),
        )

        with pytest.raises(ValidationError, match="readiness"):
            dispatch_mod.submit(
                eng.store, item.id,
                pr_url="https://github.com/acme/snake/pull/1",
                verification_file=str(vfile),
            )

    def test_develop_authoring_accepts_ready_pr_without_multica_issue_key(self, tmp_path, monkeypatch):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        item.identifier = "AITEAM-762"
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))

        monkeypatch.setattr(
            eng.store, "read_pull_request_readiness",
            lambda pr_url: PullRequestReadiness(is_draft=False, state="OPEN"),
        )

        result = dispatch_mod.submit(
            eng.store, item.id,
            pr_url="https://github.com/acme/snake/pull/1",
            verification_file=str(vfile),
        )

        assert result.advanced_to == WorkItemStatus.DONE

    def test_develop_authoring_accepts_github_ready_pr(self, tmp_path, monkeypatch):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))

        monkeypatch.setattr(
            eng.store, "read_pull_request_readiness",
            lambda pr_url: PullRequestReadiness(is_draft=False, state="OPEN"),
        )

        result = dispatch_mod.submit(
            eng.store, item.id,
            pr_url="https://github.com/acme/snake/pull/1",
            verification_file=str(vfile),
        )

        assert result.advanced_to == WorkItemStatus.DONE

    # ---------- review ----------

    def test_develop_review_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice", reviewer="bob",
            kind=dispatch_mod.TaskKind.DEVELOP,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        item.phase = dispatch_mod.TaskPhase.REVIEW
        eng.store.set_node_contract(item.id, CONTRACT)
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))

        dispatch_mod.submit(
            eng.store, item.id, verdict="pass", report_file=str(rfile),
        )
        got = eng.store.get_work_item(item.id)
        assert got.review_verdict == "pass"
        assert got.review_report["acceptance_mapping"][0]["acceptance"] == "works"
        assert got.review_report_ref["filename"] == "omac-review-report.yaml"

    def test_review_submit_cli_tells_reviewer_not_to_change_status(
            self, tmp_path, monkeypatch, capsys):
        """review submit 只提交 verdict,CLI 不应诱导 reviewer 手动保持 in_review。"""
        monkeypatch.chdir(tmp_path)
        main(["config", "set", "engine", "mock"])
        main(["config", "set", "workspace", "mock-workspace"])
        capsys.readouterr()

        store = _store()
        item = store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice", reviewer="bob",
            kind=dispatch_mod.TaskKind.DEVELOP,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        item.phase = dispatch_mod.TaskPhase.REVIEW
        store.update_work_item_metadata(
            item.id, review_comment="旧机器门错误")
        store.set_node_contract(item.id, CONTRACT)
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))

        rc = main(["work", "submit", item.id, "--verdict", "pass",
                   "--report-file", str(rfile)])

        assert rc == exit_codes.OK
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["submitted_phase"] == "review"
        assert out["next_phase"] is None
        assert out["deliverable_key"] == "review_report"
        assert out["advanced_to"] == "in_review"
        assert out["verdict"] == "pass"
        assert out["terminal"] is True
        assert out["next_action"] == "stop"
        assert "Review submission is complete" in out["message"]
        assert "Do not add an issue comment" in out["message"]
        assert store.get_work_item(item.id).review_comment == ""

    def test_review_reject_verdict_is_structured_verdict(self, tmp_path):
        """reviewer reject 必须能经 work submit 写入结构化 verdict/report。

        guide/help/run_task 都把 reject 作为合法评审结论;这里防止 submit
        左移校验把“不通过”挡在 metadata 外,导致编排永远等不到 review_verdict。
        """
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        item.phase = dispatch_mod.TaskPhase.REVIEW
        rfile = tmp_path / "report.yaml"
        report = _make_review_report()
        report["blockers"] = ["验收映射不满足"]
        report["acceptance_mapping"][0]["status"] = "fail"
        rfile.write_text(yaml.safe_dump(report))

        result = dispatch_mod.submit(
            eng.store, item.id, verdict="reject", report_file=str(rfile),
        )

        got = eng.store.get_work_item(item.id)
        assert result.advanced_to == WorkItemStatus.IN_REVIEW
        assert got.review_verdict == "reject"
        assert got.review_report["blockers"] == ["验收映射不满足"]
        assert got.review_report_ref["filename"] == "omac-review-report.yaml"

    def test_plan_review_without_deliverable_rejected(self, tmp_path):
        """review 相位没有评审对象时,不得允许 reviewer 写 verdict 掩盖半提交状态。"""
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice", reviewer="bob",
            kind=dispatch_mod.TaskKind.PLAN,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        eng.store.update_work_item_metadata(item.id, phase=dispatch_mod.TaskPhase.REVIEW)
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))

        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(
                eng.store, item.id, verdict="pass", report_file=str(rfile),
            )
        assert "Review target is missing" in str(exc.value)

    def test_plan_review_without_project_rules_rejected(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice", reviewer="bob",
            kind=dispatch_mod.TaskKind.PLAN,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        eng.store.update_work_item_metadata(
            item.id,
            deliverable="# Design\n",
            phase=dispatch_mod.TaskPhase.REVIEW,
        )
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))

        with pytest.raises(ValidationError, match="project rules"):
            dispatch_mod.submit(
                eng.store, item.id, verdict="pass", report_file=str(rfile),
            )

    # ---------- plan ----------

    def test_plan_authoring_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan\n\n## Summary\nsteps")
        rules_file = tmp_path / "project-rules.md"
        rules_file.write_text("## Project rules\n\n- Keep APIs backward compatible.\n")
        result = dispatch_mod.submit(
            eng.store,
            item.id,
            plan_file=str(pfile),
            project_rules_file=str(rules_file),
        )
        got = eng.store.get_work_item(item.id)
        assert result.phase == TaskPhase.AUTHORING
        assert result.next_phase == TaskPhase.REVIEW
        assert got.deliverable.startswith("# Plan")
        assert got.project_rules.startswith("## Project rules")
        assert got.status == WorkItemStatus.IN_REVIEW

    def test_plan_authoring_requires_project_rules_atomically(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan\n")

        with pytest.raises(ValidationError, match="project-rules"):
            dispatch_mod.submit(eng.store, item.id, plan_file=str(pfile))

        got = eng.store.get_work_item(item.id)
        assert got.deliverable is None
        assert got.project_rules is None
        assert got.status == WorkItemStatus.TODO

    def test_plan_authoring_rejects_omac_markers_in_project_rules(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan\n")
        rules_file = tmp_path / "project-rules.md"
        rules_file.write_text(
            "<!-- OMAC:PROJECT_RULES:START -->\ninvalid\n"
            "<!-- OMAC:PROJECT_RULES:END -->\n"
        )

        with pytest.raises(ValidationError, match="must not contain OMAC markers"):
            dispatch_mod.submit(
                eng.store,
                item.id,
                plan_file=str(pfile),
                project_rules_file=str(rules_file),
            )

        got = eng.store.get_work_item(item.id)
        assert got.deliverable is None
        assert got.project_rules is None

    def test_plan_authoring_cli_tells_producer_to_stop(self, tmp_path, monkeypatch, capsys):
        """产出提交成功后的 CLI 文案不能诱导 planner 继续执行 reviewer 协议。"""
        monkeypatch.chdir(tmp_path)
        main(["config", "set", "engine", "mock"])
        main(["config", "set", "workspace", "mock-workspace"])
        capsys.readouterr()

        store = _store()
        item = store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan\n")
        rules_file = tmp_path / "project-rules.md"
        rules_file.write_text("## Project rules\n\n- Preserve compatibility.\n")

        rc = main([
            "work", "submit", item.id,
            "--plan-file", str(pfile),
            "--project-rules-file", str(rules_file),
        ])
        assert rc == exit_codes.OK
        out = json.loads(capsys.readouterr().out)
        assert out["submitted_phase"] == "authoring"
        assert out["next_phase"] == "review"
        assert out["deliverable_keys"] == ["plan", "project_rules"]
        assert "verdict" not in out
        assert out["terminal"] is True
        assert out["next_action"] == "stop"
        assert "Authoring is complete" in out["message"]
        assert "Do not submit a verdict" in out["message"]
        assert "wait for the OMAC loop" in out["message"]

    def test_plan_authoring_empty_rejected(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("   \n")
        rules_file = tmp_path / "project-rules.md"
        rules_file.write_text("## Project rules\n")
        with pytest.raises(ValidationError):
            dispatch_mod.submit(
                eng.store, item.id,
                plan_file=str(pfile),
                project_rules_file=str(rules_file),
            )
        assert eng.store.get_work_item(item.id).deliverable is None

    # ---------- acceptance ----------

    def test_acceptance_authoring_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.ACCEPTANCE,
        )
        eng.store.update_work_item_metadata(
            item.id,
            review_verdict="reject",
            review_comment="stale blocker",
            machine_feedback={
                "schema": "omac.machine-feedback/v1",
                "gate": "machine-gate",
                "error_count": 1,
                "errors": ["stale machine finding"],
            },
            decision_required={"decision": "revise"},
        )
        afile = tmp_path / "acceptance.yaml"
        afile.write_text(yaml.safe_dump({
            "flows": [{"id": "login", "name": "登录", "actions": [
                {"step": "open", "how": "GET /login", "expected": "表单"},
            ]}],
        }))
        result = dispatch_mod.submit(
            eng.store, item.id, acceptance_file=str(afile))
        got = eng.store.get_work_item(item.id)
        assert result.phase == TaskPhase.AUTHORING
        assert result.next_phase == TaskPhase.REVIEW
        assert "flows" in got.deliverable
        assert got.status == WorkItemStatus.IN_REVIEW
        assert not got.review_verdict
        assert not got.review_comment
        assert got.machine_feedback is None
        assert got.machine_feedback_ref is None
        assert got.decision_required == {}

    def test_acceptance_authoring_schema_rejected(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.ACCEPTANCE,
        )
        afile = tmp_path / "acceptance.yaml"
        afile.write_text(yaml.safe_dump({"flows": [{"id": "x"}]}), )  # 缺 name/actions
        with pytest.raises(ValidationError):
            dispatch_mod.submit(eng.store, item.id, acceptance_file=str(afile))

    # ---------- decompose ----------

    def test_decompose_authoring_success(self, tmp_path):
        eng = _engine()
        members = set(eng.store.list_members("mock-workspace"))
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DECOMPOSE,
        )
        mfile = tmp_path / "manifest.yaml"
        mfile.write_text(yaml.safe_dump({
            "meta": {},
            "nodes": [{"id": "b", "worker": sorted(members)[0],
                       "contract": {
                           "objective": "x", "acceptance": ["y"], "non_goals": ["z"],
                           "source_of_truth": ["docs/b.md"],
                           "verification_commands": ["pytest -q"],
                           "integration_gates": [{
                               "name": "g", "layer": "L1", "delivery_goal": "d",
                               "source_of_truth": ["s"], "covers": ["c"],
                               "acceptance_refs": ["y"], "commands": ["c1"],
                               "required_metrics": {}, "artifacts": [],
                           }],
                           "pr_base": "feature/v1", "coverage_gate": 90,
                       }}],
        }))
        result = dispatch_mod.submit(
            eng.store, item.id, manifest_file=str(mfile), agent_pool=members)
        got = eng.store.get_work_item(item.id)
        assert result.phase == TaskPhase.AUTHORING
        assert result.next_phase == TaskPhase.REVIEW
        assert got.deliverable is not None
        assert got.status == WorkItemStatus.IN_REVIEW

    def test_decompose_authoring_lint_rejected(self, tmp_path):
        eng = _engine()
        members = set(eng.store.list_members("mock-workspace"))
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DECOMPOSE,
        )
        mfile = tmp_path / "manifest.yaml"
        mfile.write_text(yaml.safe_dump({
            "meta": {},
            # reviewer == worker 违反 lint
            "nodes": [{"id": "b", "worker": sorted(members)[0],
                       "reviewer": sorted(members)[0],
                       "contract": {
                           "objective": "x", "acceptance": ["y"], "non_goals": ["z"],
                           "verification_commands": ["pytest -q"],
                           "integration_gates": [{
                               "name": "g", "layer": "L1", "delivery_goal": "d",
                               "source_of_truth": ["s"], "covers": ["c"],
                               "acceptance_refs": ["y"], "commands": ["c1"],
                               "required_metrics": {}, "artifacts": [],
                           }],
                           "pr_base": "feature/v1", "coverage_gate": 90,
                       }}],
        }))
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(eng.store, item.id, manifest_file=str(mfile),
                                agent_pool=members)
        assert "lint" in str(exc.value) or "reviewer" in str(exc.value)

    # ---------- final-acceptance ----------

    def test_final_acceptance_authoring_success(self, tmp_path):
        eng = _engine()
        acceptance_doc = {"flows": [
            {"id": "login", "name": "登录", "actions": [
                {"step": "open", "how": "GET /login", "expected": "表单"}]}]}
        contract = Contract(
            objective="accept", non_goals=["x"], acceptance=["login"],
            verification_commands=["pytest -q"],
            integration_gates=[{
                "name": "g", "layer": "L1", "delivery_goal": "d",
                "source_of_truth": ["s"], "covers": ["c"],
                "acceptance_refs": ["login"], "commands": ["c1"],
                "required_metrics": {}, "artifacts": [],
            }],
            pr_base="feature/v1", coverage_gate=90,
            acceptance_doc=acceptance_doc,
        )
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.FINAL_ACCEPTANCE,
        )
        eng.store.set_node_contract(item.id, contract)
        rfile = tmp_path / "results.yaml"
        rfile.write_text(yaml.safe_dump([{"id": "login", "status": "pass"}]))
        result = dispatch_mod.submit(eng.store, item.id,
                                     acceptance_results_file=str(rfile))
        assert result.advanced_to == WorkItemStatus.DONE

    def test_final_acceptance_authoring_missing_flow_rejected(self, tmp_path):
        eng = _engine()
        acceptance_doc = {"flows": [
            {"id": "login", "name": "登录", "actions": [
                {"step": "open", "how": "GET /login", "expected": "表单"}]}]}
        contract = Contract(
            objective="accept", non_goals=["x"], acceptance=["login"],
            verification_commands=["pytest -q"],
            integration_gates=[], pr_base="feature/v1", coverage_gate=90,
            acceptance_doc=acceptance_doc,
        )
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.FINAL_ACCEPTANCE,
        )
        eng.store.set_node_contract(item.id, contract)
        rfile = tmp_path / "results.yaml"
        rfile.write_text(yaml.safe_dump([]))  # 漏项 login
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(eng.store, item.id,
                                acceptance_results_file=str(rfile))
        assert "login" in str(exc.value)


# ==================== CLI 退出码映射(smoke) ===========================

class TestCliExitCodes:

    def test_submit_missing_engine_workspace_raises_generic(self, capsys):
        """CLI 入口层:缺配置时应以 exit 5(ValidationError) 干净退出,不崩溃。

        当前 main 走 resolve_engine_settings,缺 engine / workspace 时 raise
        ValidationError(§5.1:校验 → exit 5);omac.cli.main.main 捕获后映射为退出码,
        由 entry() 包装后才 sys.exit,因此断言返回值而非 SystemExit。
        """
        rc = main(["work", "submit", "1", "--plan-file", "p.md"])
        assert rc == exit_codes.VALIDATION
        err = json.loads(capsys.readouterr().err)
        assert err["ok"] is False
        assert err["action"] == "submit"
        assert "config.yaml" in err["error"]["message"]


# ==================== mock e2e:submit → loop 收割必过 ===========================

class TestSubmitMissingCli:
    """CLI 层:e2e 派发 develop + work submit 缺 pr_url -> exit 5,报错精确。"""

    def test_develop_authoring_missing_pr_url_exits_five(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(["config", "set", "engine", "mock"])
        main(["config", "set", "workspace", "mock-workspace"])
        capsys.readouterr()

        store = _store()
        item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
        # 故意只给 verification_file,缺 pr_url
        vfile = tmp_path / "v.yaml"
        vfile.write_text("commands: []")
        rc = main(["work", "submit", item.id, "--verification-file", str(vfile)])
        assert rc == exit_codes.VALIDATION, capsys.readouterr()
        err = json.loads(capsys.readouterr().err)
        assert err["ok"] is False
        assert err["issue_id"] == item.id
        assert "pr-url" in err["error"]["message"], err

    def test_decompose_submit_uses_workspace_agent_pool(
            self, tmp_path, monkeypatch, capsys):
        """CLI 标准路径应自动用 WorkItemStore 成员池校验 manifest。

        否则 agent 必须手动绕到 Python API 传 agent_pool,真实 work submit 会把
        合法 workspace 成员全部误判为 not in agent pool。
        """
        monkeypatch.chdir(tmp_path)
        main(["config", "set", "engine", "mock"])
        main(["config", "set", "workspace", "mock-workspace"])
        capsys.readouterr()

        store = _store()
        members = sorted(store.list_members("mock-workspace"))
        worker, reviewer = members[0], members[1]
        item = store.create_work_item(
            "mock-workspace", "decompose", "desc", dag_key="d",
            worker="alice", kind=dispatch_mod.TaskKind.DECOMPOSE,
        )
        mfile = tmp_path / "manifest.yaml"
        mfile.write_text(yaml.safe_dump({
            "meta": {},
            "nodes": [{
                "id": "foundation",
                "worker": worker,
                "reviewer": reviewer,
                "contract": {
                    "objective": "x",
                    "acceptance": ["y"],
                    "non_goals": ["z"],
                    "source_of_truth": ["docs/design.md"],
                    "verification_commands": ["pytest -q"],
                    "integration_gates": [{
                        "name": "gate",
                        "layer": "L1",
                        "delivery_goal": "d",
                        "source_of_truth": ["docs/design.md"],
                        "covers": ["c"],
                        "acceptance_refs": ["y"],
                        "commands": ["pytest -q"],
                        "required_metrics": {},
                        "artifacts": [],
                    }],
                    "pr_base": "feature/v1",
                    "coverage_gate": 90,
                },
            }],
        }))

        rc = main(["work", "submit", item.id, "--manifest-file", str(mfile)])

        assert rc == exit_codes.OK, capsys.readouterr()
        got = store.get_work_item(item.id)
        assert got.status == WorkItemStatus.IN_REVIEW
        assert got.deliverable is not None


class TestSubmitLoopE2E:
    """验证 submit 左移校验与 loop 权威门 schema 同源,submit 过的证据,loop 必过。"""

    def test_develop_submit_then_loop_harvests(self, tmp_path):
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        store = eng.store
        contract = CONTRACT
        members = sorted(store.list_members("mock-workspace"))
        a_worker = members[0]

        # 1. 建 manifest + 节点(无 reviewer → worker 完成后 loop 直接 done)
        node_a = Node(id="a", worker=a_worker, contract=contract, title="a")
        manifest = Manifest(meta={"workspace_id": "mock-workspace"},
                            nodes={"a": node_a})
        mpath = str(tmp_path / "omac.yaml")
        save_manifest(manifest, mpath)

        # 2. 模拟 loop dispatch 派发 a:建 work item + 落 contract + 标 IN_PROGRESS
        it_a = store.create_work_item("mock-workspace", "a", "d", dag_key="a",
                                      worker=a_worker)
        store.set_node_contract(it_a.id, contract)
        store.assign_work_item(it_a.id, a_worker, "worker")
        store.update_status(it_a.id, WorkItemStatus.IN_PROGRESS)
        manifest.nodes["a"].work_item_id = it_a.id
        save_manifest(manifest, mpath)

        # 3. worker 完成 → work submit(左移校验 schema 同源验证)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))
        result = dispatch_mod.submit(
            store, it_a.id,
            pr_url="https://x/pr/1", verification_file=str(vfile),
        )
        assert result.advanced_to == WorkItemStatus.DONE
        assert store.get_work_item(it_a.id).status == WorkItemStatus.DONE

        # 4. loop tick 收割:证据门必过 → 节点 done
        result = tick(store, eng.runtime, manifest, mpath, max_parallel=4)
        assert manifest.nodes["a"].status == "done"
        assert "a" in result.done
        # schema 同源断言:权威的 validate_worker_evidence 对同一 verification 必须过
        from omac.core import evidence as evidence_mod
        item_after = store.get_work_item(it_a.id)
        assert evidence_mod.validate_worker_evidence(node_a, item_after) == []


# ==================== 评审员路由:phase/status 不一致时的正确路由 ============================

class TestPhaseResolution:
    """status 已是 IN_REVIEW 但 phase metadata 滞后为 AUTHORING 时,
    work submit 必须按 review 路由(由 loop/plan 流水线驱动 status)。
    """

    def test_plan_authoring_then_reviewer_submit_routed_as_review(self, tmp_path):
        import yaml
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        # authoring submit
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan")
        rules_file = tmp_path / "project-rules.md"
        rules_file.write_text("## Project rules\n")
        r1 = dispatch_mod.submit(
            eng.store, item.id,
            plan_file=str(pfile),
            project_rules_file=str(rules_file),
        )
        assert r1.phase == dispatch_mod.TaskPhase.AUTHORING
        assert r1.next_phase == dispatch_mod.TaskPhase.REVIEW
        assert eng.store.get_work_item(item.id).status == WorkItemStatus.IN_REVIEW

        # 模拟 loop 只改 status 未改 phase 的旧行为:把 phase 滞回 AUTHORING
        eng.store.update_work_item_metadata(item.id, phase=dispatch_mod.TaskPhase.AUTHORING)

        # reviewer submit 即便 phase metadata = AUTHORING,也应路由为 review
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))
        r2 = dispatch_mod.submit(eng.store, item.id, verdict="pass", report_file=str(rfile))
        assert r2.phase == dispatch_mod.TaskPhase.REVIEW
        got = eng.store.get_work_item(item.id)
        assert got.review_verdict == "pass"

    def test_authoring_rejected_leaves_metadata_atomic(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("   \n")  # empty
        rules_file = tmp_path / "project-rules.md"
        rules_file.write_text("## Project rules\n")
        with pytest.raises(ValidationError):
            dispatch_mod.submit(
                eng.store, item.id,
                plan_file=str(pfile),
                project_rules_file=str(rules_file),
            )
        got = eng.store.get_work_item(item.id)
        assert got.deliverable is None
        assert got.status == WorkItemStatus.TODO
        assert got.phase == dispatch_mod.TaskPhase.AUTHORING

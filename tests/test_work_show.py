"""work show 的 kind × phase 事实包:由一条 WorkItem 的事实纯投影出 agent 看到的上下文(不涉及平台)。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from _support.workitems import COMBINATIONS, EXPECTED_GUIDE_REFS, make_item, with_contract
from omac.core.contract_boundaries import responsibility_summary
from omac.core.manifest import (
    ConsumedArtifact, Contract, EvidenceMode, ProducedArtifact, _load_contract,
)
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase, WorkerHandoffIntent, WORKER_REWORK_FEEDBACK_SCHEMA
from omac.core.workitem import WorkItemStatus
from omac.pipeline.dispatch import build_show_output




def test_work_show_authoring_exposes_machine_gate_feedback_without_report():
    item = make_item(TaskKind.ACCEPTANCE, TaskPhase.AUTHORING)
    item = replace(item, review_comment="machine gate: expected template is too generic")

    output = build_show_output(
        item, "planner:alice")

    assert output["context"]["previous_review"] == {
        "comment": "machine gate: expected template is too generic",
    }


def test_work_show_operator_retry_requires_fresh_submission():
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)
    item = replace(item, worker_handoff=WorkerHandoffIntent(
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
        item, "worker:alice", language="en")

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
        item, "worker:alice", language="cn")
    assert chinese["context"]["retry"] == output["context"]["retry"]
    assert "禁止复用或仅引用" in chinese["protocol"]
    assert "旧 verification 仅作为 baseline，不能作为本轮证据" in chinese["protocol"]
    assert "即使代码无需修改" in chinese["protocol"]


def test_work_show_operator_retry_exposes_rework_feedback_context():
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)
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
    item = replace(item, worker_handoff=WorkerHandoffIntent(
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
        item, "worker:alice", language="en")

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
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)

    output = build_show_output(
        item, "worker:alice", language="en")

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
    item = make_item(kind, TaskPhase.AUTHORING)
    item = replace(item, worker_handoff=WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="operator-retry",
        ),
    )

    output = build_show_output(
        item, "worker:alice", language="en")

    assert "retry" not in output["context"]
    assert "baseline-only" not in output["protocol"]


def test_work_show_projects_compact_contract_responsibility():
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)
    item = with_contract(item, Contract(
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
        item, "worker:alice")

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
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)
    item = with_contract(item, Contract(
        objective="tooling",
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
    ))

    output = build_show_output(item, "worker:alice")

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
    item = make_item(TaskKind.DECOMPOSE, TaskPhase.AUTHORING)

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




@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_security_sensitive_protocol_is_develop_review_only(kind, phase):
    item = make_item(kind,
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


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_show_output_structure(kind, phase):
    """每种组合都输出完整任务、上下文、协议、权威顺序、guide 与 submit。"""
    with_contract = (phase == TaskPhase.AUTHORING)
    item = make_item(kind, phase, with_contract=with_contract,
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
    if kind == TaskKind.DEVELOP and phase == TaskPhase.AUTHORING:
        assert out["control"].pop("report_blocker") == f"omac work block {item.id} --report-file <blocker.yaml>"
    if kind == TaskKind.AMENDMENT and phase == TaskPhase.AUTHORING:
        assert out["control"].pop("report_blocker") == f"omac work block {item.id} --report-file <scope-gap.yaml>"
        template = out["control"].pop("blocker_report_template")
        assert template["schema"] == "omac.amendment-scope-gap/v1"
        assert template["issue_id"] == item.id
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
    item = make_item(TaskKind.DEVELOP, TaskPhase.REVIEW,
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
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)

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




def test_authoring_show_does_not_project_ledger_from_previous_review_generation():
    """普通 reject 保持同代 ledger；amendment 切代后旧 ledger 只剩审计价值。"""
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING,
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
    current = replace(item, review_ledger=ledger, review_generation="amendment-proj-850",
                      review_ledger_generation="review-legacy-convergence")

    out = build_show_output(current, "worker:alice")

    assert "review_state" not in out["context"]
    assert "required_closures" not in out["context"]
    assert current.review_ledger == ledger


def test_work_show_distinguishes_absolute_bounces_from_current_generation_budget():
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    current = replace(item, bounces=Bounces(worker=17, review=3, merge=0), bounce_baseline={"worker": 14, "review": 3, "merge": 0})

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


def test_authoring_show_includes_source_issue_refs():
    """worker 只拿 issue id 时,work show 必须给出上游 issue 链路。"""
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING, with_contract=True)
    item = replace(item, source_refs=[
            {"label": "设计方案", "issue_id": "plan-1",
             "url": "https://multica.ai/workspaces/ws/issues/plan-1"},
            {"label": "验收文档", "issue_id": "acc-1"},
        ],
    )

    out = build_show_output(item, "worker:alice")

    assert out["context"]["source_issues"] == [
        {"label": "设计方案", "issue_id": "plan-1",
         "url": "https://multica.ai/workspaces/ws/issues/plan-1"},
        {"label": "验收文档", "issue_id": "acc-1"},
    ]


def test_plan_authoring_action_not_role_mixed():
    """点5:plan×authoring 的「现在做什么」只讲 plan,不掺 acceptance 任务;深度指向 guide。"""
    item = make_item(TaskKind.PLAN, TaskPhase.AUTHORING)
    out = build_show_output(item, "worker:alice")
    proto = out["protocol"]
    # 不再把 acceptance(验收文档)任务塞进 plan 的视图
    assert "Write the acceptance document:" not in proto
    assert out["guide_refs"] == [
        "omac guide role planner", "omac guide artifact design"]
    assert "omac guide" not in proto


def test_set_node_contract_visible_in_show():
    """回归:set_node_contract 下发的 contract 必须在 work show 中可见(真实 dispatch 路径)。

    这是被派发 agent 第一入口的关键链路:dispatch 侧调用 set_node_contract 下发契约,
    被派发 agent 调 work show 必须能读回完整 contract,否则拿到的是空上下文。
    """
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    # 从 store 重新读取(模拟 agent 侧 get_work_item),确认 contract 已持久化
    got = item
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
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)
    out = build_show_output(item, f"worker:{item.worker}")
    protocol = out["protocol"]
    # 动作点明 PR 三步的要害
    assert "Push a branch" in protocol, protocol
    assert "PR" in protocol, protocol
    assert "worker creates it" in protocol, protocol
    assert "do not manually change the issue status" in protocol
    # 精确交付命令归 submit 段(相位视图:动作与命令分离)
    assert "--pr-url" in out["submit"]


def test_develop_show_mentions_issue_key_for_pr_autolink():
    """有平台 issue key 时,work show 指导 worker 让 PR 自动关联到 Multica issue。"""
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING)
    item.identifier = "PROJ-762"

    out = build_show_output(item, f"worker:{item.worker}")

    assert out["task"]["issue_key"] == "PROJ-762"
    assert "PROJ-762" in out["protocol"]
    assert "branch name, title, or body" in out["protocol"]


def test_show_treats_in_review_status_as_review_phase():
    """旧 issue 缺 phase=review 时,平台 in_review 仍应给 reviewer 正确上下文。"""
    item = make_item(TaskKind.DEVELOP, TaskPhase.AUTHORING,
        with_contract=True, with_verification=True)
    item = replace(item, artifacts={"pr_url": "https://x/pr/1"})
    item = replace(item, status=WorkItemStatus.IN_REVIEW)

    got = item
    out = build_show_output(got, f"reviewer:{got.reviewer}")

    assert out["task"]["phase"] == "review"
    assert "deliverable" in out["context"]
    assert "env_setup" in out["context"]
    assert "--verdict" in out["submit"]


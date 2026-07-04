"""dispatch — 协议文本与 submit 模板(设计文档 §7.4)。

work show 按(kind × phase × 身份)输出任务上下文与执行协议。
协议文本与 submit 参数在此集中定义,供 work show 与 work submit 共享,
避免双份拷贝导致漂移(验收标准:submit 模板与实际参数一致)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from omac.core.taskmeta import TaskKind, TaskPhase


# ==================== 协议文本(按角色集中,避免双份) ====================

PLANNER_AUTHORING_PROTOCOL = """你是 planner。

plan(制定计划):分析需求,制定实施计划;计划须锚定验收目标、可执行、可验证。
acceptance(验收文档):基于定稿计划列出业务流程,逐条转换为用户视角、端到端、
可执行的验收动作;验收文档将作为开发锚点与总控验收目标清单。

交付:omac work submit <issue-id> --plan-file <path>
     或 omac work submit <issue-id> --acceptance-file <path>
"""

ORCHESTRATOR_AUTHORING_PROTOCOL = """你是 orchestrator。

decompose:把计划/设计文档拆解为 manifest DAG。每个节点须有明确 contract
(objective / acceptance / non_goals / verification_commands / pr_base /
coverage_gate);acceptance 须锚定验收文档条目;DAG 无环;
worker/reviewer 在 agent 池内。

交付:omac work submit <issue-id> --manifest-file <path>
"""

WORKER_AUTHORING_PROTOCOL = """你是 worker(develop × authoring)。永远只需要两个命令:

1. omac work show <issue-id> —— 取 contract 全量(objective/acceptance/
   non_goals/verification_commands/pr_base/coverage_gate)与本协议
2. 完成后 omac work submit <issue-id> --pr-url <PR> --verification-file <path>

铁律:
- 契约先行:只消费共享契约,不平行重定义
- TDD:测试与实现同步;完成必须有证据,不接受自述
- PR base 指向 contract.pr_base(集成分支),不直接打主干
- non_goals 是红线,越界即 reject

verification-file 结构(缺什么当场打回):
  commands:            # 必须覆盖 contract.verification_commands,exit_code 全 0
    - { cmd: "...", exit_code: 0, summary: "..." }
  integration_gates:   # 逐项覆盖 contract.integration_gates
  pr_base: feature/v1  # 必须等于 contract.pr_base
  coverage: 92         # 必须 ≥ coverage_gate
  env_setup:           # contract 声明集成门/env 依赖时必填:环境构建步骤,
    - "docker compose up -d db"       # reviewer 照做即可复跑
"""

REVIEWER_PROTOCOL = """你是 reviewer。同一 issue 被转派给你(阶段=review)。
产出者的交付物与讨论都在这条 issue 时间线上。

1. omac work show <issue-id> —— 取评审对象、contract、worker 的 env_setup
2. 独立复跑:按 env_setup 搭环境,重跑验证命令与集成测试——只读共享态,
   不信任何自述
3. omac work submit <issue-id> --verdict pass|pass-with-nits|reject --report-file <path>

report-file 结构:
  review_goals:            # 必填:评审所依据的目标(验收映射/覆盖率/集成门/设计引用)
    - "acceptance 全覆盖且逐条可验证"
  diff_reviewed: true
  tests_rerun: true
  integration_tests_rerun: true   # contract 有集成门时必填
  coverage_checked: true
  acceptance_mapping:      # 逐条映射 contract.acceptance
    - { acceptance: "...", evidence: "...", status: pass }
  integration_gate_mapping: [ ... ]
  blockers: []             # pass 时必须为空
  nits: []

reject 时 issue 转回产出者,你的评审目标与意见一并可见——
让开发者朝目标修,而不是只修列出的问题。
"""

ACCEPTOR_AUTHORING_PROTOCOL = """你是 acceptor(final-acceptance × authoring)。

DAG 收敛后,以验收文档为目标清单做用户视角的端到端走查:
1. omac work show <issue-id> —— 取验收文档逐条动作清单 + 各节点 env_setup 汇总
2. 逐条执行验收动作,记录 pass/fail + 问题
3. omac work submit <issue-id> --acceptance-results-file <path>

acceptance-results-file 结构(逐项映射验收文档条目,漏项当场打回):
  results:
    - { acceptance: "...", status: pass|fail, evidence: "...", issue: "..." }
"""


def _protocol_for(kind: TaskKind, phase: TaskPhase) -> str:
    """按(kind × phase)取执行协议文本。"""
    if phase == TaskPhase.REVIEW:
        return REVIEWER_PROTOCOL
    # authoring
    if kind == TaskKind.PLAN:
        return PLANNER_AUTHORING_PROTOCOL
    if kind == TaskKind.ACCEPTANCE:
        return PLANNER_AUTHORING_PROTOCOL
    if kind == TaskKind.DECOMPOSE:
        return ORCHESTRATOR_AUTHORING_PROTOCOL
    if kind == TaskKind.DEVELOP:
        return WORKER_AUTHORING_PROTOCOL
    if kind == TaskKind.FINAL_ACCEPTANCE:
        return ACCEPTOR_AUTHORING_PROTOCOL
    return ""


# ==================== submit 参数(单一事实源,防漂移) ====================

# 全部 submit 参数名 → argparse 注册 kwargs
SUBMIT_PARAM_SPECS: Dict[str, Dict[str, Any]] = {
    "--plan-file": {},
    "--acceptance-file": {},
    "--manifest-file": {},
    "--pr-url": {},
    "--verification-file": {},
    "--verdict": {"choices": ["pass", "pass-with-nits", "reject"]},
    "--report-file": {},
    "--acceptance-results-file": {},
}

# (kind, phase) → 该组合使用的 submit 参数名(有序)
SUBMIT_PARAMS_BY_KIND_PHASE: Dict[Tuple[TaskKind, TaskPhase], List[str]] = {
    (TaskKind.PLAN, TaskPhase.AUTHORING): ["--plan-file"],
    (TaskKind.PLAN, TaskPhase.REVIEW): ["--verdict", "--report-file"],
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING): ["--acceptance-file"],
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW): ["--verdict", "--report-file"],
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING): ["--manifest-file"],
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW): ["--verdict", "--report-file"],
    (TaskKind.DEVELOP, TaskPhase.AUTHORING): ["--pr-url", "--verification-file"],
    (TaskKind.DEVELOP, TaskPhase.REVIEW): ["--verdict", "--report-file"],
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING): ["--acceptance-results-file"],
}


def submit_params_for(kind: TaskKind, phase: TaskPhase) -> List[str]:
    """按(kind × phase)返回 submit 参数名列表(与 work submit 注册一致)。"""
    return SUBMIT_PARAMS_BY_KIND_PHASE.get((kind, phase), [])


def submit_template_for(kind: TaskKind, phase: TaskPhase, issue_id: str) -> str:
    """按(kind × phase)生成精确的 submit 命令模板(参数带路径占位)。"""
    params = submit_params_for(kind, phase)
    if not params:
        return f"omac work submit {issue_id}"
    parts = [f"omac work submit {issue_id}"]
    for p in params:
        if p == "--verdict":
            parts.append(f"{p} <pass|pass-with-nits|reject>")
        else:
            # 取占位名:去掉 --,替换 - 为 _
            placeholder = p[2:].replace("-", "_")
            parts.append(f"{p} <{placeholder}>")
    return " ".join(parts)


# ==================== show 输出构建 ====================

def _env_setup_checklist(item: Any) -> Optional[List[str]]:
    """develop×review:从 worker 的 verification 提取 env_setup 复跑清单。"""
    verification = getattr(item, "verification", None)
    if not verification or not isinstance(verification, dict):
        return None
    env_setup = verification.get("env_setup")
    if not env_setup or not isinstance(env_setup, list):
        return None
    return list(env_setup)


def build_show_output(item: Any, identity: str) -> Dict[str, Any]:
    """构建 work show 的完整输出结构(四段)。

    参数:
        item: WorkItem(来自 store.get_work_item)
        identity: 当前 agent 的身份描述(如 "worker:alice" 或 "reviewer:bob")

    返回 dict,四段:
        task: 任务标识(kind/phase/dag_key/issue_id/title/worker/reviewer)
        context: 完整上下文(contract 全量 or 评审对象 + env_setup)
        protocol: 该 kind×phase 的执行协议
        submit: 精确的 submit 命令模板
    """
    kind: TaskKind = item.kind
    phase: TaskPhase = item.phase

    task = {
        "kind": kind.value,
        "phase": phase.value,
        "dag_key": item.dag_key,
        "issue_id": item.id,
        "title": item.title,
        "worker": item.worker,
        "reviewer": item.reviewer,
        "identity": identity,
    }

    # 完整上下文:authoring 给 contract 全量;review 给评审对象 + env_setup
    contract = getattr(item, "contract", None)
    if isinstance(contract, dict):
        contract_payload = contract
    elif contract is not None:
        # Contract dataclass → dict
        contract_payload = {
            k: v for k, v in vars(contract).items()
            if v is not None and v != [] and v != 90
        }
    else:
        contract_payload = None

    if phase == TaskPhase.AUTHORING:
        context: Dict[str, Any] = {
            "contract": contract_payload,
        }
    else:
        # review 阶段:评审对象(deliverable) + contract + worker 的 env_setup
        context = {
            "deliverable": item.deliverable,
            "contract": contract_payload,
        }
        env_setup = _env_setup_checklist(item)
        if env_setup is not None:
            context["env_setup"] = env_setup

    protocol = _protocol_for(kind, phase)
    submit = submit_template_for(kind, phase, item.id)

    return {
        "task": task,
        "context": context,
        "protocol": protocol,
        "submit": submit,
    }

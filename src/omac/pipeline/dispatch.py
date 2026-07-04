"""dispatch — 协议文本、submit 模板与左移校验入口(设计文档 §7.4)。

work show 按(kind × phase × 身份)输出任务上下文与执行协议。
协议文本与 submit 参数在此集中定义,供 work show 与 work submit 共享,
避免双份拷贝导致漂移(验收标准:submit 模板与实际参数一致)。

work submit 的左移参数门 + 证据校验 + 原子 metadata 写入 + 阶段推进,
由 cli.commands.work 调用;复用 P2.2 evidence validators 与 core/lint。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from omac.core import evidence as evidence_mod
from omac.core.acceptance import load_acceptance_doc, load_acceptance_doc_file
from omac.core.lint import lint as lint_manifest
from omac.core.manifest import _load_contract, load_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines.models import WorkItem, WorkItemStatus
from omac.engines.store import WorkItemStore
from omac.errors import ValidationError



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


# ==================== work submit 左移校验(P2.4) ====================

ALL_PARAMS = (
    "plan_file",
    "acceptance_file",
    "manifest_file",
    "pr_url",
    "verification_file",
    "verdict",
    "report_file",
    "acceptance_results_file",
)

# kind * phase → 该组合合法且必填的参数名。
SPECS: Dict[TaskKind, Dict[TaskPhase, tuple]] = {
    TaskKind.PLAN: {
        TaskPhase.AUTHORING: ("plan_file",),
        TaskPhase.REVIEW: ("verdict", "report_file"),
    },
    TaskKind.ACCEPTANCE: {
        TaskPhase.AUTHORING: ("acceptance_file",),
        TaskPhase.REVIEW: ("verdict", "report_file"),
    },
    TaskKind.DECOMPOSE: {
        TaskPhase.AUTHORING: ("manifest_file",),
        TaskPhase.REVIEW: ("verdict", "report_file"),
    },
    TaskKind.DEVELOP: {
        TaskPhase.AUTHORING: ("pr_url", "verification_file"),
        TaskPhase.REVIEW: ("verdict", "report_file"),
    },
    TaskKind.FINAL_ACCEPTANCE: {
        TaskPhase.AUTHORING: ("acceptance_results_file",),
    },
}


def _kind(value: Any) -> TaskKind:
    if isinstance(value, TaskKind):
        return value
    try:
        return TaskKind(str(value))
    except ValueError:
        raise ValidationError(
            f"未知的任务类型 {value!r} —— 应为: "
            f"{', '.join(k.value for k in TaskKind)}"
        )


def _phase(value: Any) -> TaskPhase:
    if isinstance(value, TaskPhase):
        return value
    try:
        return TaskPhase(str(value))
    except ValueError:
        raise ValidationError(
            f"未知的阶段 {value!r} —— 应为: "
            f"{', '.join(p.value for p in TaskPhase)}"
        )


def _param_cli_name(param: str) -> str:
    return "--" + param.replace("_", "-")


def validate_params(kind: TaskKind, phase: TaskPhase, provided: Dict[str, Any]) -> None:
    """参数按 kind×phase 校验:缺 / 多 / 错 → raise ValidationError(报错即教学)。"""

    if kind not in SPECS or phase not in SPECS[kind]:
        available = ", ".join(p.value for p in SPECS.get(kind, {})) or "无"
        raise ValidationError(
            f"{kind.value} 没有 {phase.value} 阶段的交付 —— "
            f"该 kind 可用的阶段为: {available}"
        )

    expected = set(SPECS[kind][phase])
    given = {name for name, value in provided.items() if value is not None}

    missing = sorted(expected - given)
    extra = sorted(given - expected)

    if not missing and not extra:
        return

    spec_human = " + ".join(_param_cli_name(p) for p in sorted(expected))
    lines = []
    if missing:
        lines.append(
            f"缺少参数({kind.value} × {phase.value} 需要): "
            + ", ".join(_param_cli_name(m) for m in missing)
        )
    if extra:
        lines.append(
            f"多余参数({kind.value} × {phase.value} 不需要): "
            + ", ".join(_param_cli_name(e) for e in extra)
        )
    lines.append(f"正确用法: omac work submit <issue-id> {spec_human}")
    raise ValidationError("\n".join(lines))


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        raise ValidationError(f"文件不存在: {path}")
    except OSError as exc:
        raise ValidationError(f"无法读取文件 {path}: {exc}")


def _parse_structured(path: str) -> Any:
    """交付结构文件统一解析:优先 JSON,失败回退 YAML;plan 交付不在此列(纯文本)。"""
    text = _read_text(path)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        if data is None:
            raise ValidationError(f"{path} 内容为空(null)")
        return data
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"{path} 既不是合法 JSON 也不是合法 YAML: {exc}\n"
            "请修正文件内容后重试"
        )
    if data is None:
        raise ValidationError(f"{path} 内容为空")
    return data


def _contract_from_item(item: WorkItem) -> Any:
    """把 work item 上的 contract 统一成 Contract 对象(若已是则透传)。

    multica 落在 metadata 里,get 回的是 dict;mock 直接把 Contract 挂回 item。
    """
    from ..core.manifest import Contract as _Contract

    raw = getattr(item, "contract", None)
    if raw is None:
        return None
    if isinstance(raw, _Contract):
        return raw
    return _load_contract(raw)


# 供左移校验用的轻量 node / item 形态(P2.2 validators 只看这几个属性)。
class _Node:
    def __init__(self, contract: Any):
        self.contract = contract


class _Item:
    def __init__(
        self,
        artifacts: Optional[Dict[str, Any]] = None,
        verification: Optional[Dict[str, Any]] = None,
        review_verdict: Optional[str] = None,
        review_report: Optional[Dict[str, Any]] = None,
    ):
        self.artifacts = artifacts
        self.verification = verification
        self.review_verdict = review_verdict
        self.review_report = review_report


def _validate_plan_authoring(plan_file: str) -> str:
    """plan 交付做基础结构校验:文件存在且非空。返回文件内容。"""
    content = _read_text(plan_file)
    if not content.strip():
        raise ValidationError(f"plan 文件为空: {plan_file}")
    return content


def _validate_acceptance_authoring(acceptance_file: str) -> str:
    """acceptance 交付按验收文档 schema 校验。返回文件内容。"""
    try:
        load_acceptance_doc_file(acceptance_file)
    except (ValueError, OSError) as exc:
        raise ValidationError(f"acceptance 文件校验失败: {exc}")
    return _read_text(acceptance_file)


def _validate_decompose_authoring(manifest_file: str, pool: Set[str]) -> str:
    """decompose 交付做基础结构校验 + manifest 过 core/lint。返回文件内容。"""
    content = _read_text(manifest_file)
    try:
        manifest = load_manifest(manifest_file)
    except (ValueError, OSError) as exc:
        raise ValidationError(f"manifest 解析失败: {exc}")
    errors = lint_manifest(manifest, pool)
    if errors:
        raise ValidationError("manifest lint 失败:\n  - " + "\n  - ".join(errors))
    return content


def _validate_develop_authoring(
    pr_url: str, verification_file: str, item: WorkItem
) -> Dict[str, Any]:
    """develop × authoring 左移校验:复用 P2.2 validate_worker_evidence。"""
    verification = _parse_structured(verification_file)
    node = _Node(_contract_from_item(item))
    probe = _Item(artifacts={"pr_url": pr_url}, verification=verification)
    errors = evidence_mod.validate_worker_evidence(node, probe)
    if errors:
        raise ValidationError(
            "verification 证据校验失败:\n  - " + "\n  - ".join(errors)
        )
    return verification


def _validate_review(
    kind: TaskKind, verdict: str, report_file: str, item: WorkItem
) -> Dict[str, Any]:
    """review 阶段(各 kind 共用)左移校验:复用 P2.2 validate_review_evidence。"""
    report = _parse_structured(report_file)
    if verdict not in evidence_mod.REVIEW_APPROVE:
        raise ValidationError(
            f"verdict={verdict!r} 不可用于通过 —— review 通过需 pass 或 "
            "pass-with-nits;如需回退请走平台的驳回/重派流程(参见 §7.4)"
        )
    node = _Node(_contract_from_item(item))
    probe = _Item(review_verdict=verdict, review_report=report)
    errors = evidence_mod.validate_review_evidence(node, probe)
    if errors:
        raise ValidationError(
            "review report 校验失败:\n  - " + "\n  - ".join(errors)
        )
    return report


def _validate_final_acceptance_authoring(
    results_file: str, item: WorkItem
) -> Dict[str, Any]:
    """final-acceptance × authoring 左移校验:复用 P2.2 validate_acceptance_results。"""
    results = _parse_structured(results_file)

    raw_doc = None
    contract = getattr(item, "contract", None)
    if isinstance(contract, dict):
        raw_doc = contract.get("acceptance_doc")
    elif contract is not None:
        raw_doc = getattr(contract, "acceptance_doc", None)

    if raw_doc is None:
        raise ValidationError(
            "final-acceptance 缺少关联的 acceptance_doc —— "
            "需先在 contract.acceptance_doc 中挂载验收文档(参见 §8)"
        )

    try:
        acceptance_doc = load_acceptance_doc(raw_doc) if isinstance(raw_doc, dict) else raw_doc
    except ValueError as exc:
        raise ValidationError(f"关联的 acceptance_doc 不合法: {exc}")

    errors = evidence_mod.validate_acceptance_results(acceptance_doc, results)
    if errors:
        raise ValidationError(
            "acceptance-results 校验失败:\n  - " + "\n  - ".join(errors)
        )
    return results


def _resolve_phase(item: WorkItem, declared: TaskPhase) -> TaskPhase:
    """把 work item 的阶段归一化为可路由的 phase。

    设计文档 §7.4:平台状态(status)由 loop / plan 流水线驱动,phase 只是
    metadata 的快拍。当 status 已经是 IN_REVIEW 时(无论 phase 字段是否更新),
    按审稿阶段路由 —— 否则同一张 issue 上后续 work submit 会被误派为 authoring。
    """
    status = getattr(item, "status", None)
    if status == WorkItemStatus.IN_REVIEW and declared == TaskPhase.AUTHORING:
        return TaskPhase.REVIEW
    return declared


class SubmitResult:
    """submit 成功后的结果(用于 cli 层展示)。"""

    def __init__(
        self,
        kind: TaskKind,
        phase: TaskPhase,
        deliverable_key: str,
        advanced_to: WorkItemStatus,
    ):
        self.kind = kind
        self.phase = phase
        self.deliverable_key = deliverable_key
        self.advanced_to = advanced_to


def submit(
    store: WorkItemStore,
    issue_id: str,
    *,
    plan_file: Optional[str] = None,
    acceptance_file: Optional[str] = None,
    manifest_file: Optional[str] = None,
    pr_url: Optional[str] = None,
    verification_file: Optional[str] = None,
    verdict: Optional[str] = None,
    report_file: Optional[str] = None,
    acceptance_results_file: Optional[str] = None,
    agent_pool: Optional[Set[str]] = None,
) -> SubmitResult:
    """work submit 的核心入口。

    按 kind×phase 校验参数 → 左移证据校验 → 原子写 metadata + 阶段推进。
    任何校验失败统一 raise ValidationError(调用方转 exit 5),不做任何
    metadata 写入(原子性)。
    """

    item = store.get_work_item(issue_id)
    kind = _kind(item.kind.value if hasattr(item.kind, "value") else item.kind)
    raw_phase = _phase(item.phase.value if hasattr(item.phase, "value") else item.phase)
    phase = _resolve_phase(item, raw_phase)

    provided = {
        "plan_file": plan_file,
        "acceptance_file": acceptance_file,
        "manifest_file": manifest_file,
        "pr_url": pr_url,
        "verification_file": verification_file,
        "verdict": verdict,
        "report_file": report_file,
        "acceptance_results_file": acceptance_results_file,
    }
    validate_params(kind, phase, provided)

    pool = set(agent_pool) if agent_pool is not None else set()

    # ---------- develop × authoring ----------
    if kind == TaskKind.DEVELOP and phase == TaskPhase.AUTHORING:
        verification = _validate_develop_authoring(pr_url, verification_file, item)
        store.update_work_item_metadata(
            issue_id,
            artifacts={"pr_url": pr_url},
            verification=verification,
        )
        store.update_status(issue_id, WorkItemStatus.DONE)
        return SubmitResult(kind, phase, "verification", WorkItemStatus.DONE)

    # ---------- review(各 kind 共用) ----------
    if phase == TaskPhase.REVIEW:
        report = _validate_review(kind, verdict, report_file, item)
        store.update_work_item_metadata(
            issue_id,
            review_verdict=verdict,
            review_report=report,
            phase=TaskPhase.REVIEW,
        )
        # 状态保持 IN_REVIEW,由 loop / plan 流水线收割判定 done / blocked。
        return SubmitResult(kind, phase, "review_report", WorkItemStatus.IN_REVIEW)

    # ---------- final-acceptance × authoring ----------
    if kind == TaskKind.FINAL_ACCEPTANCE and phase == TaskPhase.AUTHORING:
        _validate_final_acceptance_authoring(acceptance_results_file, item)
        store.update_work_item_metadata(
            issue_id,
            deliverable=_read_text(acceptance_results_file),
        )
        store.update_status(issue_id, WorkItemStatus.DONE)
        return SubmitResult(kind, phase, "acceptance_results", WorkItemStatus.DONE)

    # ---------- plan × authoring ----------
    if kind == TaskKind.PLAN and phase == TaskPhase.AUTHORING:
        content = _validate_plan_authoring(plan_file)
        store.update_work_item_metadata(
            issue_id, deliverable=content, phase=TaskPhase.REVIEW,
        )
        store.update_status(issue_id, WorkItemStatus.IN_REVIEW)
        return SubmitResult(kind, TaskPhase.REVIEW, "plan", WorkItemStatus.IN_REVIEW)

    # ---------- acceptance × authoring ----------
    if kind == TaskKind.ACCEPTANCE and phase == TaskPhase.AUTHORING:
        content = _validate_acceptance_authoring(acceptance_file)
        store.update_work_item_metadata(
            issue_id, deliverable=content, phase=TaskPhase.REVIEW,
        )
        store.update_status(issue_id, WorkItemStatus.IN_REVIEW)
        return SubmitResult(kind, TaskPhase.REVIEW, "acceptance", WorkItemStatus.IN_REVIEW)

    # ---------- decompose × authoring ----------
    if kind == TaskKind.DECOMPOSE and phase == TaskPhase.AUTHORING:
        content = _validate_decompose_authoring(manifest_file, pool)
        store.update_work_item_metadata(
            issue_id, deliverable=content, phase=TaskPhase.REVIEW,
        )
        store.update_status(issue_id, WorkItemStatus.IN_REVIEW)
        return SubmitResult(kind, TaskPhase.REVIEW, "manifest", WorkItemStatus.IN_REVIEW)

    raise ValidationError(f"未支持的交付组合: {kind.value} × {phase.value}")


# ==================== 派发 issue body 三段式模板(§7.4) ====================


# 任务类型 → 角色 / 角色说明文本(同源 guide;模板只引用 guide 不复制其内容)
KIND_ROLE = {
    TaskKind.PLAN: "planner",
    TaskKind.ACCEPTANCE: "planner",
    TaskKind.DECOMPOSE: "orchestrator",
    TaskKind.DEVELOP: "worker",
    TaskKind.FINAL_ACCEPTANCE: "acceptor",
}

KIND_GUIDE = {
    # 各 issue 类型指向对应的 guide topic;模板与 guide 同源、不重复
    TaskKind.PLAN: "workflow",
    TaskKind.ACCEPTANCE: "workflow",
    TaskKind.DECOMPOSE: "manifest",
    TaskKind.DEVELOP: "worker",
    TaskKind.FINAL_ACCEPTANCE: "roles",
}

KIND_LABEL = {
    TaskKind.PLAN: "plan",
    TaskKind.ACCEPTANCE: "acceptance",
    TaskKind.DECOMPOSE: "decompose",
    TaskKind.DEVELOP: "develop",
    TaskKind.FINAL_ACCEPTANCE: "final-acceptance",
}


def _contract_summary(contract, key, fallback):
    """从 contract 取字段摘要,缺失 gives 占位(人可读)。"""
    if contract is None:
        return fallback
    value = getattr(contract, key, None)
    if isinstance(value, list):
        return value if value else fallback
    return value if value not in (None, "") else fallback


def render_issue_body(node, contract, kind, issue_id):
    """三段式派发模板(设计文档 §7.4)。

    第一段 bootstrap:两条命令(work show / work submit 精确模板) +
    omac guide <topic> 指引 + 必须经 omac 交互;第二段简报(title/objective/
    source_of_truth/acceptance 摘要);第三段硬约束(non_goals/pr_base/reviewer 独立
    复跑等铁律)。模板文本与 guide 同源,不复制。
    """
    role = KIND_ROLE.get(kind, "worker")
    label = KIND_LABEL.get(kind, kind.value)
    guide_topic = KIND_GUIDE.get(kind, "workflow")

    # ---- 第一段:bootstrap ----
    title = getattr(node, "title", None) or getattr(node, "id", issue_id)
    base_cmd = f"omac work show {issue_id}"
    submit_cmd = submit_template_for(kind, TaskPhase.AUTHORING, issue_id)
    bootstrap = (
        f"你被分配了一件 {label} 任务(必须经 omac 交互):\n"
        f"  1. {base_cmd}  —— 获取完整任务上下文与执行协议（你的 contract 全量）\n"
        f"  2. {submit_cmd}  —— 完成后交付（show 输出里有本角色精确交付参数）\n"
        f"遇到不明确的地方:运行 omac guide {guide_topic} 查阅「{role}」角色说明与执行清单。"
    )

    # ---- 第二段:任务简报(人可读) ----
    objective = _contract_summary(contract, "objective", "见 contract.objective")
    source_of_truth = _contract_summary(
        contract, "source_of_truth", "见 contract.source_of_truth")
    acceptance = _contract_summary(contract, "acceptance", "见 contract.acceptance")

    def _lines(value):
        if isinstance(value, list):
            if not value:
                return "（未声明）"
            return "\n".join(f"- {v}" for v in value)
        return str(value)

    briefing = (
        "## 简报\n"
        f"- title: {title}\n"
        f"- objective: {_lines(objective)}\n"
        f"- source_of_truth: {_lines(source_of_truth)}\n"
        f"- acceptance: {_lines(acceptance)}"
    )

    # ---- 第三段:硬约束(铁律) ----
    non_goals = _contract_summary(contract, "non_goals", None)
    pr_base = _contract_summary(contract, "pr_base", None)
    reviewer = getattr(node, "reviewer", None)

    rules = []
    rules.append("契约先行:只消费同源 contract,不平行重定义（TDD 同步）")
    if non_goals:
        rules.append(
            "non_goals 是红线,越界即 reject:\n"
            + "\n".join(f"  - {g}" for g in non_goals))
    rules.append("完成必须有结构化证据（verification/report）,不接受自述")
    if pr_base:
        rules.append(f"PR base 必须指向集成分支（pr_base={pr_base}）,不合主干")
    if reviewer:
        rules.append(
            f"reviewer（{reviewer}）独立复跑验证命令与集成测试,"
            "按 env_setup 重建环境、不信任何自述")
    if contract is not None and getattr(contract, "coverage_gate", None) not in (None,):
        rules.append(
            f"改动分支覆盖 ≥ coverage_gate={contract.coverage_gate}")
    rules.append("平台状态由 loop 推进,不手动改 issue 状态/assignee")
    hard = "## 硬约束（铁律）\n" + "\n".join(f"- {r}" for r in rules)

    return "\n\n".join([bootstrap, briefing, hard])


def render_review_rollout_comment(node, contract, verdict: Optional[str], report=None,
                                  item_id=None):
    """review 转派评论模板(设计文档 §7.4 阶段交接)。

    包含:阶段变更说明 + 评审对象定位。三种语境:
      - verdict=None:worker 交付完毕,转派 reviewer 接手(进入 review);
      - pass / pass-with-nits:reviewer 给出通过结论(含 nits);
      - reject:转回 worker 返工,附 review_goals + blockers + nits,
        让开发者朝目标修。
    report 缺省视为空结构;item_id 用于定位评审对象(缺省用节点 id)。
    """
    report = report or {}
    reviewer = getattr(node, "reviewer", "reviewer")
    location = item_id if item_id is not None else getattr(node, "id", "issue")

    def _bul(label, items):
        if not items:
            return ""
        return label + "\n" + "\n".join(f"  - {x}" for x in items)

    if verdict is None:
        heading = "阶段变更:worker 交付完毕,转派 reviewer 进入 review"
        body = (
            f"评审对象(本 issue={location}):交付物 / contract / worker env_setup "
            f"(reviewer={reviewer})。\n"
            f"请 reviewer 独立复跑(env_setup + verification_commands)后 "
            f"omac work submit {location} --verdict ... --report-file ..."
        )
        return f"## {heading}\n{body}"

    if verdict in ("pass", "pass-with-nits"):
        heading = f"verdict={verdict}: reviewer 评审通过"
        body_lines = [f"评审对象(issue={location})交付通过(reviewer={reviewer})。"]
        if verdict == "pass-with-nits":
            n = _bul("nits(建议项,不阻塞):", report.get("nits") or [])
            if n:
                body_lines.append(n)
        body_lines.append("由 loop 推进下一步(节点完成 / 后续节点解锁)。")
        return "## {}\n{}".format(heading, "\n".join(body_lines))

    # reject → 回转 worker
    heading = "verdict=reject: 转回 worker 返工(朝评审目标修,不只是列出的问题)"
    goals = report.get("review_goals") or ["独立复跑验证 + 验收映射 + 契约遵守"]
    blockers = report.get("blockers") or []
    nits = report.get("nits") or []
    body_lines = [
        f"评审对象(issue={location})未通过(reviewer={reviewer}),回转 worker 返工。"
    ]
    body_lines.append(_bul("评审目标(review_goals):", goals))
    if blockers:
        body_lines.append(_bul("阻塞项(blockers):", blockers))
    if nits:
        body_lines.append(_bul("建议项(nits):", nits))
    body_lines.append(
        f"请按评审目标修完后重新 "
        f"omac work submit {location} --pr-url ... --verification-file ..."
    )
    return "## {}\n{}".format(heading, "\n".join(body_lines))

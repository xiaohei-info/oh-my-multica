"""dispatch — 协议文本、submit 模板与左移校验入口(设计文档 §7.4)。

work show 按(kind × phase × 身份)输出任务上下文与执行协议。
协议文本与 submit 参数在此集中定义,供 work show 与 work submit 共享,
避免双份拷贝导致漂移(验收标准:submit 模板与实际参数一致)。

work submit 的左移参数门 + 证据校验 + 原子 metadata 写入 + 阶段推进,
由 cli.commands.work 调用;复用 P2.2 evidence validators 与 core/lint。
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from omac.core import evidence as evidence_mod
from omac.core.acceptance import load_acceptance_doc, load_acceptance_doc_file
from omac.core.lint import lint as lint_manifest, lint_increment
from omac.core.manifest import _load_contract, load_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines.models import WorkItem, WorkItemStatus
from omac.engines.store import WorkItemStore
from omac.errors import ValidationError




# 注入到 project description 的常驻 Agent 路由。这里只说明入口和权威顺序，
# 不复制具体角色协议；当前任务的动态事实由 work show 生成。
OMAC_PROJECT_DESCRIPTION = """本 project 由 omac 编排。**判据:只有标题带 `[DAG:...]` 前缀的 issue 是 omac 派发的
执行任务**,需经 omac 处理;无此前缀的 issue 按其 body 常规处理(不要把它当被派发任务、
不要对它跑 omac work show/submit —— 但若 body 明确要求你运行 omac 命令,照 body 执行)。

被指派到 `[DAG:...]` 任务时(无论你的角色是 planner/orchestrator/worker/reviewer/acceptor),
omac CLI 已在 PATH 上。第一步只运行:

  omac work show <该 issue id> --output json

把返回的 task/context/protocol/authority 当作当前实例事实;按 guide_refs 只读取本任务需要的
静态 guide,静态 guide 不得覆盖实例事实。完成后执行返回的精确 submit 命令。
不要编造参数,也不要手改 issue metadata 或平台状态。
"""


# work show 的「现在做什么」——严格按当前这件任务(kind × phase)收窄,不 role-mix。
# 静态深度(交付文件 schema、铁律清单)全在 guide,协议不再内联复制;show 只给一句话
# 动作。对应 guide topic 通过独立的 guide_refs 字段返回,避免把命令混进动作语义。
_AUTHORING_ACTION = {
    TaskKind.PLAN:
        "编写设计方案:分析需求,产出锚定验收目标、可执行、可验证的方案文档。",
    TaskKind.ACCEPTANCE:
        "编写验收文档:把定稿设计方案的业务流程逐条转成用户视角、端到端、可执行的验收动作。",
    TaskKind.DECOMPOSE:
        "把设计方案/验收拆成 manifest DAG:每节点带完整 contract、acceptance 锚定验收文档、DAG 无环。",
    TaskKind.DEVELOP:
        "推分支 + 开 PR(base=contract.pr_base,worker 自建、omac 不代建),TDD 同步,产出结构化验证证据;"
        "不要手动改 issue 状态/assignee/rerun/cancel。",
    TaskKind.FINAL_ACCEPTANCE:
        "以验收文档为清单做用户视角端到端走查,逐条记录 pass/fail + 证据。",
}
_REVIEW_ACTION = (
    "独立复跑:按 env_setup 搭环境,重跑验证命令与集成测试,只读共享态、"
    "不信自述,按 contract/验收目标给 verdict。")


def _next_action(kind: TaskKind, phase: TaskPhase) -> str:
    """「现在做什么」:只陈述当前动作,不混入静态 guide 命令。"""
    return _REVIEW_ACTION if phase == TaskPhase.REVIEW \
        else _AUTHORING_ACTION.get(kind, "")


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


# Agent 消费内容的权威顺序。越靠前越具体、越接近当前实例。
AUTHORITY_ORDER = [
    "work show 当前实例事实",
    "contract / previous_review",
    "role guide",
    "artifact guide",
    "workflow 总览",
]


# 当前 kind × phase 所需的最小静态知识集合。保持命令完整,Agent 可直接执行。
GUIDE_REFS_BY_KIND_PHASE: Dict[Tuple[TaskKind, TaskPhase], List[str]] = {
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
    (TaskKind.DEVELOP, TaskPhase.AUTHORING): [
        "omac guide role worker", "omac guide artifact evidence"],
    (TaskKind.DEVELOP, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact evidence"],
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING): [
        "omac guide role acceptor",
        "omac guide artifact acceptance",
        "omac guide artifact evidence",
    ],
}


def guide_refs_for(kind: TaskKind, phase: TaskPhase) -> List[str]:
    """返回当前任务所需 guide 命令的副本,防止调用方修改单一事实源。"""
    return list(GUIDE_REFS_BY_KIND_PHASE.get((kind, phase), []))


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


def _previous_review_context(item: Any) -> Optional[Dict[str, Any]]:
    report = getattr(item, "review_report", None)
    report_ref = getattr(item, "review_report_ref", None)
    if not report and not report_ref:
        return None

    previous: Dict[str, Any] = {}
    verdict = getattr(item, "review_verdict", None)
    if not verdict and isinstance(report, dict):
        verdict = report.get("verdict")
    if verdict:
        previous["verdict"] = verdict
    if report:
        previous["report"] = report
    if report_ref:
        previous["report_ref"] = report_ref
    return previous

def build_show_output(item: Any, identity: str) -> Dict[str, Any]:
    """构建 work show 的 Agent-first 完整事实包。

    参数:
        item: WorkItem(来自 store.get_work_item)
        identity: 当前 agent 的身份描述(如 "worker:alice" 或 "reviewer:bob")

    返回 dict:
        task: 任务标识(kind/phase/status/dag_key/issue_id/title/worker/reviewer)
        context: 完整上下文(contract 全量 or 评审对象 + env_setup)
        protocol: 该 kind×phase 的执行协议
        submit: 精确的 submit 命令模板
        authority: 冲突时的内容权威顺序
        guide_refs: 当前任务所需的最小静态知识集合
    """
    kind: TaskKind = item.kind
    phase: TaskPhase = _resolve_phase(item, item.phase)

    task = {
        "kind": kind.value,
        "phase": phase.value,
        "status": (
            item.status.value
            if hasattr(item.status, "value")
            else str(item.status)
        ),
        "dag_key": item.dag_key,
        "issue_id": item.id,
        "issue_key": getattr(item, "identifier", None),
        "title": item.title,
        "worker": item.worker,
        "reviewer": item.reviewer,
        "identity": identity,
        "blocked_by": list(getattr(item, "blocked_by", None) or []),
        "wave": getattr(item, "wave", None),
        "bounces": (
            item.bounces.as_dict()
            if hasattr(getattr(item, "bounces", None), "as_dict")
            else {}
        ),
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

    context: Dict[str, Any] = {
        "issue_description": getattr(item, "description", ""),
        "contract": contract_payload,
    }
    contract_ref = getattr(item, "contract_ref", None)
    if contract_ref is not None:
        context["contract_ref"] = contract_ref
    decision_required = getattr(item, "decision_required", None)
    if decision_required is not None:
        context["decision_required"] = decision_required

    if phase == TaskPhase.AUTHORING:
        previous_review = _previous_review_context(item)
        if previous_review is not None:
            context["previous_review"] = previous_review
    else:
        # review 阶段:评审对象(deliverable) + contract + worker 的 env_setup
        context["deliverable"] = item.deliverable
        for key in (
            "deliverable_ref",
            "artifacts",
            "verification",
            "verification_ref",
        ):
            value = getattr(item, key, None)
            if value is not None:
                context[key] = value
        env_setup = _env_setup_checklist(item)
        if env_setup is not None:
            context["env_setup"] = env_setup

    source_refs = normalize_source_refs(getattr(item, "source_refs", None))
    if source_refs:
        context["source_issues"] = source_refs

    protocol = _next_action(kind, phase)
    issue_key = getattr(item, "identifier", None)
    if kind == TaskKind.DEVELOP and phase == TaskPhase.AUTHORING and issue_key:
        protocol += (
            f"\n建议让 GitHub PR 分支名、标题或正文包含 `{issue_key}`，"
            "这样 Multica 可以把 PR 自动关联到本 issue；缺失时仍可交付。"
        )
    submit = submit_template_for(kind, phase, item.id)

    return {
        "task": task,
        "context": context,
        "protocol": protocol,
        "submit": submit,
        "authority": list(AUTHORITY_ORDER),
        "guide_refs": guide_refs_for(kind, phase),
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


def _validate_decompose_authoring(
    manifest_file: str, pool: Set[str], base_manifest: Any = None,
) -> str:
    """decompose 交付做基础结构校验 + manifest 过 lint。返回文件内容。

    base_manifest 提供时(增量 decompose),用 lint_increment 校验(允许引用既有节点);
    否则 standalone lint(整图必须自洽)。
    """
    content = _read_text(manifest_file)
    try:
        manifest = load_manifest(manifest_file)
    except (ValueError, OSError) as exc:
        raise ValidationError(f"manifest 解析失败: {exc}")
    if base_manifest is not None:
        errors = lint_increment(manifest, base_manifest, pool)
    else:
        errors = lint_manifest(manifest, pool)
    if errors:
        raise ValidationError("manifest lint 失败:\n  - " + "\n  - ".join(errors))
    return content


def _validate_develop_authoring(
    pr_url: str, verification_file: str, item: WorkItem
) -> Dict[str, Any]:
    """develop × authoring 左移校验:复用 P2.2 validate_worker_evidence。"""
    _validate_pr_ready_for_handoff(pr_url)
    verification = _parse_structured(verification_file)
    node = _Node(_contract_from_item(item))
    probe = _Item(artifacts={"pr_url": pr_url}, verification=verification)
    errors = evidence_mod.validate_worker_evidence(node, probe)
    if errors:
        raise ValidationError(
            "verification 证据校验失败:\n  - " + "\n  - ".join(errors)
        )
    return verification


def _is_github_pr_url(pr_url: str) -> bool:
    return isinstance(pr_url, str) and pr_url.startswith("https://github.com/") and "/pull/" in pr_url


def _validate_pr_ready_for_handoff(pr_url: str) -> None:
    """worker 交付前置门:GitHub PR 必须不是 draft,否则不进入 CI/review/merge。"""
    if not _is_github_pr_url(pr_url):
        return
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "isDraft,state"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise ValidationError(
            "GitHub PR ready 检查需要 gh CLI。请安装并登录后重试: "
            "brew install gh && gh auth login")
    except subprocess.TimeoutExpired:
        raise ValidationError(
            f"GitHub PR ready 检查超时: {pr_url}。请确认网络/GitHub 可达后重试。")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ValidationError(
            f"GitHub PR ready 检查失败: {pr_url}\n{detail}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise ValidationError(
            f"GitHub PR ready 检查返回非 JSON: {pr_url}\n{(proc.stdout or '').strip()}")
    if payload.get("isDraft") is True:
        raise ValidationError(
            f"GitHub PR 仍是 draft,不能交付给下游 CI/review/merge: {pr_url}\n"
            "请先执行 `gh pr ready <pr-url>` 或在 GitHub 页面 Mark ready for review。")
    state = payload.get("state")
    if state and state != "OPEN":
        raise ValidationError(
            f"GitHub PR 状态不是 OPEN,不能交付: {pr_url} (state={state})")

def _validate_review(
    kind: TaskKind, verdict: str, report_file: str, item: WorkItem
) -> Dict[str, Any]:
    """review 阶段(各 kind 共用)左移校验:复用 P2.2 validate_review_evidence。"""
    if kind in (TaskKind.PLAN, TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE) and not item.deliverable:
        raise ValidationError(
            "评审对象缺失:产出正文未提交或提交失败,不能写 review verdict。"
            "请让产出者重新执行 omac work submit。"
        )
    report = _parse_structured(report_file)
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
    """submit 成功结果；phase 是本次提交阶段，next_phase 是推进后的阶段。"""

    def __init__(
        self,
        kind: TaskKind,
        phase: TaskPhase,
        deliverable_key: str,
        advanced_to: WorkItemStatus,
        next_phase: Optional[TaskPhase] = None,
        message: Optional[str] = None,
    ):
        self.kind = kind
        self.phase = phase
        self.deliverable_key = deliverable_key
        self.advanced_to = advanced_to
        self.next_phase = next_phase
        self.message = message


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
    base_manifest: Optional[Any] = None,
) -> SubmitResult:
    """work submit 的核心入口。

    按 kind×phase 校验参数 → 左移证据校验 → 原子写 metadata + 阶段推进。
    任何校验失败统一 raise ValidationError(调用方转 exit 5),不做任何
    metadata 写入(原子性)。

    base_manifest: decompose 增量模式时既有 manifest 基线。提供时,decompose 用
    lint_increment(含对既有+增量全集的依赖引用校验)替代 standalone lint。
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
            verification_source=_read_text(verification_file),
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
            review_report_source=_read_text(report_file),
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
        return SubmitResult(
            kind, phase, "plan", WorkItemStatus.IN_REVIEW,
            next_phase=TaskPhase.REVIEW,
            message="产出阶段已结束；不要提交 verdict，不要执行 reviewer 协议。等待 omac loop 转派 reviewer 或人工确认。",
        )

    # ---------- acceptance × authoring ----------
    if kind == TaskKind.ACCEPTANCE and phase == TaskPhase.AUTHORING:
        content = _validate_acceptance_authoring(acceptance_file)
        store.update_work_item_metadata(
            issue_id, deliverable=content, phase=TaskPhase.REVIEW,
        )
        store.update_status(issue_id, WorkItemStatus.IN_REVIEW)
        return SubmitResult(
            kind, phase, "acceptance", WorkItemStatus.IN_REVIEW,
            next_phase=TaskPhase.REVIEW,
            message="产出阶段已结束；不要提交 verdict，不要执行 reviewer 协议。等待 omac loop 转派 reviewer 或人工确认。",
        )

    # ---------- decompose × authoring ----------
    if kind == TaskKind.DECOMPOSE and phase == TaskPhase.AUTHORING:
        content = _validate_decompose_authoring(
            manifest_file, pool, base_manifest=base_manifest)
        store.update_work_item_metadata(
            issue_id, deliverable=content, phase=TaskPhase.REVIEW,
        )
        store.update_status(issue_id, WorkItemStatus.IN_REVIEW)
        return SubmitResult(
            kind, phase, "manifest", WorkItemStatus.IN_REVIEW,
            next_phase=TaskPhase.REVIEW,
            message="产出阶段已结束；不要提交 verdict，不要执行 reviewer 协议。等待 omac loop 转派 reviewer 或人工确认。",
        )

    raise ValidationError(f"未支持的交付组合: {kind.value} × {phase.value}")


# ==================== Human-first 派发 issue body ====================


# 任务类型 → 角色 / 角色说明文本(同源 guide;模板只引用 guide 不复制其内容)
KIND_ROLE = {
    TaskKind.PLAN: "planner",
    TaskKind.ACCEPTANCE: "planner",
    TaskKind.DECOMPOSE: "orchestrator",
    TaskKind.DEVELOP: "worker",
    TaskKind.FINAL_ACCEPTANCE: "acceptor",
}

KIND_GUIDE = {
    # 各 issue 类型指向对应的角色 guide topic;模板与 guide 同源、不重复
    TaskKind.PLAN: "role planner",
    TaskKind.ACCEPTANCE: "role planner",
    TaskKind.DECOMPOSE: "role orchestrator",
    TaskKind.DEVELOP: "role worker",
    TaskKind.FINAL_ACCEPTANCE: "role acceptor",
}

KIND_LABEL = {
    TaskKind.PLAN: "plan",
    TaskKind.ACCEPTANCE: "acceptance",
    TaskKind.DECOMPOSE: "decompose",
    TaskKind.DEVELOP: "develop",
    TaskKind.FINAL_ACCEPTANCE: "final-acceptance",
}

KIND_HUMAN_LABEL = {
    TaskKind.PLAN: "设计方案",
    TaskKind.ACCEPTANCE: "验收定义",
    TaskKind.DECOMPOSE: "任务拆解",
    TaskKind.DEVELOP: "开发实现",
    TaskKind.FINAL_ACCEPTANCE: "最终验收",
}

ROLE_HUMAN_LABEL = {
    "planner": "方案规划者",
    "orchestrator": "任务编排者",
    "worker": "开发执行者",
    "reviewer": "独立评审者",
    "acceptor": "最终验收者",
}


def _contract_summary(contract, key, fallback):
    """从 contract 取字段摘要,缺失 gives 占位(人可读)。"""
    if contract is None:
        return fallback
    value = contract.get(key) if isinstance(contract, Mapping) else getattr(contract, key, None)
    if isinstance(value, list):
        return value if value else fallback
    return value if value not in (None, "") else fallback


def _command_env_prefix(engine_env: Optional[Dict[str, str]] = None) -> str:
    if not engine_env:
        return ""
    parts = []
    for key in ("OMAC_ENGINE", "OMAC_WORKSPACE_ID", "OMAC_PROJECT_ID"):
        value = engine_env.get(key)
        if value:
            parts.append(f"{key}={value}")
    return (" ".join(parts) + " ") if parts else ""


def normalize_source_refs(
    source_refs=None,
    *,
    labels: Optional[List[str]] = None,
    engine_env: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    """把上游 issue 引用规整成稳定小对象;只存引用,不存上游正文。"""
    refs: List[Dict[str, str]] = []
    for idx, raw in enumerate(source_refs or []):
        if isinstance(raw, dict):
            issue_id = str(raw.get("issue_id") or raw.get("id") or raw.get("ref") or "").strip()
            if not issue_id:
                continue
            ref: Dict[str, str] = {"issue_id": issue_id}
            for key in ("label", "kind", "url"):
                value = raw.get(key)
                if value:
                    ref[key] = str(value)
            refs.append(ref)
        else:
            issue_id = str(raw).strip()
            if issue_id:
                ref = {"issue_id": issue_id}
                if labels and idx < len(labels):
                    ref["label"] = labels[idx]
                refs.append(ref)
    for ref in refs:
        if "url" not in ref and engine_env:
            if engine_env.get("OMAC_ENGINE") == "multica" and engine_env.get("OMAC_WORKSPACE_SLUG"):
                ref["url"] = f"mention://issue/{ref['issue_id']}"
    return refs


def _source_ref_label(ref: Dict[str, str]) -> str:
    return ref.get("label") or ref.get("kind") or "source"


def _source_ref_link(ref: Dict[str, str]) -> str:
    issue_id = ref["issue_id"]
    if ref.get("url"):
        return f"[{issue_id}]({ref['url']})"
    if issue_id.startswith("#"):
        return f"`{issue_id}`"
    if issue_id.isdigit():
        return f"`#{issue_id}`"
    return f"`{issue_id}`"


def render_source_refs_section(
    source_refs=None,
    *,
    engine_env: Optional[Dict[str, str]] = None,
    include_commands: bool = True,
) -> str:
    """渲染上游 issue 链接;Agent 视图可附带可复制的 work show 命令。"""
    refs = normalize_source_refs(source_refs, engine_env=engine_env)
    if not refs:
        return ""
    env_prefix = _command_env_prefix(engine_env)
    lines = [
        "## 上游 issue（防跑偏）",
        "本任务承接以下上游 issue，用于追溯需求与决策；若与当前 contract 冲突，先请求确认：",
    ]
    for ref in refs:
        label = _source_ref_label(ref)
        issue_id = ref["issue_id"]
        link = _source_ref_link(ref)
        prefix = f"- {label}: " if label != "source" else "- "
        lines.append(f"{prefix}{link}")
        if include_commands:
            lines.append(
                f"```bash\n{env_prefix}omac work show {issue_id} --output json\n```"
            )
    return "\n".join(lines)


def render_issue_body(node, contract, kind, issue_id, source_refs=None, engine_env=None, issue_key=None):
    """渲染 Human-first issue body,顶部仅保留一个 Agent JSON 入口。"""
    role = KIND_ROLE.get(kind, "worker")
    label = KIND_LABEL.get(kind, kind.value)
    title = getattr(node, "title", None) or getattr(node, "id", issue_id)
    env_prefix = _command_env_prefix(engine_env)

    bootstrap = (
        "> **Agent 入口:** 先读取当前任务的权威 JSON 上下文。\n\n"
        f"```bash\n{env_prefix}omac work show {issue_id} --output json\n```"
    )

    summary_lines = [
        f"- 类型: {KIND_HUMAN_LABEL.get(kind, label)}（`{label}`）",
        f"- 执行角色: {ROLE_HUMAN_LABEL.get(role, role)}（`{role}`）",
    ]
    objective = _contract_summary(contract, "objective", None)
    if objective:
        summary_lines.append(f"- 目标: {objective}")
    source_of_truth = _contract_summary(contract, "source_of_truth", None)
    if source_of_truth:
        values = source_of_truth if isinstance(source_of_truth, list) else [source_of_truth]
        summary_lines.extend(f"- 依据: `{value}`" for value in values)
    summary = f"# {title}\n\n## 任务摘要\n" + "\n".join(summary_lines)

    acceptance = _contract_summary(contract, "acceptance", None)
    completion = ""
    if acceptance:
        values = acceptance if isinstance(acceptance, list) else [acceptance]
        completion = "## 完成标准\n" + "\n".join(f"- {value}" for value in values)

    description = (getattr(node, "description", "") or "").strip()
    detail = f"## 任务详情\n{description}" if description else ""

    non_goals = _contract_summary(contract, "non_goals", None)
    exclusions = ""
    if non_goals:
        values = non_goals if isinstance(non_goals, list) else [non_goals]
        exclusions = "## 非目标\n" + "\n".join(f"- {value}" for value in values)

    scope_paths = _contract_summary(contract, "scope_paths", None)
    scope = ""
    if scope_paths:
        values = scope_paths if isinstance(scope_paths, list) else [scope_paths]
        scope = "## 主要代码归属范围\n" + "\n".join(
            f"- `{value}`" for value in values)

    constraints = []
    pr_base = _contract_summary(contract, "pr_base", None)
    if pr_base:
        constraints.append(f"- PR 基线: `{pr_base}`")
    coverage_gate = _contract_summary(contract, "coverage_gate", None)
    if kind == TaskKind.DEVELOP and coverage_gate is not None:
        constraints.append(f"- 改动分支覆盖率: `≥ {coverage_gate}%`")
    if kind == TaskKind.DEVELOP and issue_key:
        constraints.append(f"- PR 关联标识: `{issue_key}`")
    delivery_constraints = ""
    if constraints:
        delivery_constraints = "## 交付约束\n" + "\n".join(constraints)

    repo_urls = _contract_summary(contract, "repo_urls", None)
    repositories = ""
    if repo_urls:
        repositories = "## 目标仓库\n" + "\n".join(
            f"- `{url}`" for url in repo_urls)

    origin = render_source_refs_section(
        source_refs, engine_env=engine_env, include_commands=False)

    return "\n\n".join(
        p for p in [
            bootstrap,
            summary,
            completion,
            detail,
            exclusions,
            scope,
            delivery_constraints,
            repositories,
            origin,
        ] if p)


def render_review_rollout_comment(node, contract, verdict: Optional[str], report=None,
                                  item_id=None, kind: TaskKind = TaskKind.DEVELOP):
    """review 转派评论模板(设计文档 §7.4 阶段交接)。

    包含:阶段变更说明 + 评审对象定位。三种语境:
      - verdict=None:产出者交付完毕,转派 reviewer 接手(进入 review);
      - pass / pass-with-nits:reviewer 给出通过结论(含 nits);
      - reject:转回产出者返工,附 review_goals + blockers + nits,让其朝目标修。
    report 缺省视为空结构;item_id 用于定位评审对象(缺省用节点 id)。
    kind 决定 submit 模板:develop→--pr-url;plan/acceptance/decompose→--plan-file 等
    (与 work show 同源,不写死,避免给产出者发错重交命令)。
    """
    report = report or {}
    reviewer = getattr(node, "reviewer", "reviewer")
    location = item_id if item_id is not None else getattr(node, "id", "issue")
    review_submit = submit_template_for(kind, TaskPhase.REVIEW, location)
    author_submit = submit_template_for(kind, TaskPhase.AUTHORING, location)

    def _bul(label, items):
        if not items:
            return ""
        return label + "\n" + "\n".join(f"  - {x}" for x in items)

    if verdict is None:
        heading = "阶段变更:产出者交付完毕,转派 reviewer 进入 review"
        body = (
            f"评审对象(本 issue={location}):交付物 / contract / 复跑清单(如有) "
            f"(reviewer={reviewer})。先 omac work show {location} --output json 取权威上下文,\n"
            f"独立复跑后 {review_submit}"
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

    # reject → 回转产出者
    heading = "verdict=reject: 转回产出者返工(朝评审目标修,不只是列出的问题)"
    goals = report.get("review_goals") or ["独立复跑验证 + 验收映射 + 契约遵守"]
    blockers = report.get("blockers") or []
    nits = report.get("nits") or []
    body_lines = [
        f"评审对象(issue={location})未通过(reviewer={reviewer}),回转产出者返工。"
    ]
    body_lines.append(_bul("评审目标(review_goals):", goals))
    if blockers:
        body_lines.append(_bul("阻塞项(blockers):", blockers))
    if nits:
        body_lines.append(_bul("建议项(nits):", nits))
    body_lines.append(f"请按评审目标修完后重新 {author_submit}")
    return "## {}\n{}".format(heading, "\n".join(body_lines))

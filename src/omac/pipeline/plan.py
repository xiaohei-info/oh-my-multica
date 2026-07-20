"""plan create 流水线编排(§7.2):设计方案→验收文档→拆解,三阶段全部经 tasks.run_task。

双模式一条流水线:
  --doc 给了 → 跳过 planner 设计环节,直接进验收文档 + 拆解
  没给     → planner 从零编写设计方案,评审通过后继续全程内置 review 门(--no-review 一刀切跳过,--no-acceptance 跳过验收文档);
每个 LLM 环节修订有界(读 config.retry.review,缺省 ≤3),耗尽 → NeedsDecision(exit 20)。
每个 phase 一条 issue,产出 → (lint 机器门)→ 评审 → 回退修订都在同一条 issue 上。

经 run_task 的 delivery 交付约定:
  - plan 阶段 planner 交付 delivery["plan"];
  - acceptance 阶段 planner 交付 delivery["acceptance"];
  - decompose 阶段 orchestrator 交付 delivery["manifest"]。
真实 multica 写侧可用 comment/attachment 承载正文,metadata 只存引用;
读侧仍还原为 WorkItem.deliverable,让 pipeline 不关心平台存储细节。

上游产物通过 payload["source_of_truth"](dict[标签 -> 文本正文])传入,
run_task 把它以 issue body「上游产物(只读上下文)」段落到 issue description,
使真实 planner/orchestrator 在 `omac work show`/issue body 中能取得上游输入。
"""
from __future__ import annotations

import os
import re
import shlex
import time
import yaml
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..core import acceptance as acceptance_mod
from ..core.config import CONFIG_DIR, CONFIG_PATH
from ..core.gitsync import commit_files, ensure_config_synced, ensure_files_clean
from ..core.lint import lint, lint_increment
from ..core.manifest import (
    Manifest,
    loads_manifest,
    project_root_from_manifest_path,
    save_manifest,
)
from ..core.project_rules import read_agents_snapshot, write_project_rules
from ..core.taskmeta import TaskKind, make_dag_key, make_plan_id
from ..engines.models import WorkItem, WorkItemStatus
from ..errors import ValidationError
from ..i18n import CN, ui
from .tasks import run_task


@dataclass
class PlanContext:
    """plan 流水线的共享上下文(引擎、空间、角色、开关)。

    由 cli.commands.plan.run() 装配,解耦 CLI 入参与 pipeline 逻辑。
    """

    engine: Any
    workspace_id: str
    planner: str
    orchestrator: str
    reviewers: List[str]
    max_revisions: int
    no_review: bool
    no_acceptance: bool
    members: set
    confirm: bool = True
    language: str = CN

    def poll(self, interval: Optional[float] = None) -> Callable[[], None]:
        """构造一个阻塞轮询闭包(真实场景用,测试注入 no-op)。"""
        if interval is not None and interval <= 0:
            return lambda: None
        return lambda: time.sleep(interval if interval is not None else 0.1)


# run_task 交付落在 artifacts 里的文本 key
_PLAN_KEY = "plan"
_ACCEPTANCE_KEY = "acceptance"
_MANIFEST_KEY = "manifest"


def _emit_plan_next_steps(manifest_path: str, acceptance_path: Optional[str] = None,
                          language: str = CN) -> None:
    """plan 收敛后的 agent 可见衔接契约。"""
    print(ui("Plan complete", "plan 完成", language=language))
    print(f"manifest: {manifest_path}")
    if acceptance_path and os.path.exists(acceptance_path):
        print(f"acceptance: {acceptance_path}")
    print(ui(
        f"Next: omac dag run {shlex.quote(manifest_path)}",
        f"下一步: omac dag run {shlex.quote(manifest_path)}",
        language=language,
    ))


def plan_id_from_dag_key(dag_key: str) -> str:
    """从 plan 流水线任一阶段 dag_key 取出同一个 plan_id。"""
    value = (dag_key or "").strip()
    for prefix in ("plan-", "acceptance-", "decompose-"):
        if value.startswith(prefix):
            plan_id = value[len(prefix):]
            if plan_id:
                return plan_id
    raise ValidationError(ui(
        f"Could not parse plan_id from dag_key {dag_key}; expected plan-p-xxxx",
        f"无法从 dag_key 解析 plan_id:{dag_key} —— 期望形如 plan-p-xxxx"))


def plan_dag_key_from_id(plan_id: str) -> str:
    value = (plan_id or "").strip()
    if not value:
        raise ValidationError(ui("--plan-id cannot be empty", "--plan-id 不能为空"))
    if value.startswith("plan-"):
        return value
    if value.startswith(("acceptance-", "decompose-")):
        value = plan_id_from_dag_key(value)
    return make_dag_key(TaskKind.PLAN, scope=value)


def _phase_text(delivery: Dict[str, Any], key: str) -> str:
    """从 run_task 返回的 delivery 取某 key 的文本交付。"""
    value = delivery.get(key)
    if not value:
        raise ValidationError(ui(
            f"Stage delivery is missing '{key}'. Check the delivery contract and submitted artifacts.",
            f"阶段交付缺少 '{key}' —— 产出者未在 artifacts 中交付;请检查交付契约。"))
    return str(value)


def _read_file(path: str) -> str:
    if not os.path.exists(path):
        raise ValidationError(ui(
            f"File not found: {path}. Check the --doc path.",
            f"文件不存在: {path} —— 请确认 --doc 路径"))
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_if_missing(dirpath: str) -> None:
    os.makedirs(dirpath or ".", exist_ok=True)


def _validate_acceptance(text: str) -> acceptance_mod.AcceptanceDoc:
    """按 core/acceptance schema 校验验收文档文本(结构不全即报错)。"""
    import yaml

    raw = yaml.safe_load(text)
    return acceptance_mod.load_acceptance_doc(raw)


def _find_by_dag_key(ctx: PlanContext, kind: TaskKind, dag_key: str) -> Optional[WorkItem]:
    matches = [
        item for item in ctx.engine.store.list_work_items(ctx.workspace_id)
        if item.kind == kind and item.dag_key == dag_key
    ]
    if len(matches) > 1:
        raise ValidationError(ui(
            f"dag_key is not unique: {dag_key}. Resolve duplicate platform issues first.",
            f"dag_key 不唯一:{dag_key} —— 平台数据异常,请先人工处理重复 issue。"))
    return matches[0] if matches else None


def _require_by_dag_key(ctx: PlanContext, kind: TaskKind, dag_key: str) -> WorkItem:
    item = _find_by_dag_key(ctx, kind, dag_key)
    if item is None:
        raise ValidationError(ui(
            f"No {kind.value} issue matches {dag_key}. Use the DAG key printed by "
            "plan create or shown in the issue title.",
            f"未找到 {dag_key} 对应的 {kind.value} issue —— "
            "请确认使用的是 plan create 输出/issue 标题里的 DAG 标识。"))
    return item


def _name_from_plan_issue(item: WorkItem) -> str:
    title = re.sub(r"^(\[DAG:[^\]]+\]\s*)+", "", item.title or "").strip()
    for suffix in (" design", " 设计方案"):
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
            break
    return title or plan_id_from_dag_key(item.dag_key)


def _compose_guard(
    members: set,
    *,
    project_root: str,
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None,
    base_manifest: Optional[Manifest] = None,
) -> Callable[[WorkItem], List[str]]:
    """造 decompose 的 lint 机器门(零 token,≤ max_revisions 轮)。

    从交付 artifacts 取 manifest 文本,解析后跑 core/lint(有验收文档时附加
    锚定校验:contract.acceptance 每条须锚定验收文档 flow.id)。
    返回错误字符串列表(空 = 通过)。
    """

    def guard(item: WorkItem) -> List[str]:
        text = getattr(item, "deliverable", None)
        if not text:
            return [ui(
                f"Delivery is missing '{_MANIFEST_KEY}'; the orchestrator did not submit a manifest.",
                f"交付缺少 '{_MANIFEST_KEY}' —— orchestrator 未产出 manifest")]
        try:
            manifest = loads_manifest(text, project_root=project_root)
        except (TypeError, ValueError, yaml.YAMLError) as exc:
            return [ui(
                f"Could not parse generated manifest YAML or schema: {exc}. "
                "Regenerate a YAML mapping with valid meta, nodes, and contract field types.",
                f"无法解析生成的 manifest YAML 或 schema: {exc}。"
                "请重新生成顶层为 mapping、且 meta、nodes、contract 字段类型有效的 YAML。",
            )]
        if base_manifest is None:
            return lint(manifest, members, acceptance=acceptance_doc)

        errors = lint_increment(manifest, base_manifest, members)
        if acceptance_doc is not None:
            standalone_errors = set(lint(manifest, members))
            errors.extend(
                error
                for error in lint(
                    manifest, members, acceptance=acceptance_doc)
                if error not in standalone_errors
            )
        return list(dict.fromkeys(errors))

    return guard


def plan_create(
    ctx: PlanContext,
    name: str,
    *,
    doc_path: Optional[str] = None,
    goal_text: Optional[str] = None,
    poll: Optional[Callable[[], None]] = None,
) -> int:
    """omac plan create 的主编排。返回退出码契约约定的状态(0 / 5 / 20)。

    校验问题 → raise ValidationError(exit 5);修订耗尽 → run_task 内部抛
    NeedsDecision(exit 20);正常收敛 → return 0。
    """
    store = ctx.engine.store
    # 派单前:真实引擎下自动把 config 同步到 main,否则隔离区 agent clone 后读不到。
    ensure_config_synced(CONFIG_PATH, branch="main", engine_type=store.config.engine_type)
    if doc_path is None:
        ensure_files_clean(["AGENTS.md"], engine_type=store.config.engine_type)
    base_dir = CONFIG_DIR
    manifest_path = os.path.join(base_dir, f"{name}.yaml")
    project_root = project_root_from_manifest_path(manifest_path)
    acceptance_path = os.path.join(base_dir, f"{name}.acceptance.yaml")
    reviewers = [] if ctx.no_review else ctx.reviewers
    poll_cb = poll if poll is not None else ctx.poll()
    plan_id = make_plan_id()
    agents_snapshot = read_agents_snapshot() if doc_path is None else None

    acceptance_text: Optional[str] = None
    project_rules_text: Optional[str] = None
    # provenance:各阶段源头 issue,后续阶段带上引用防跑偏(--doc 时无 plan issue)。
    plan_item_id: Optional[str] = None
    acceptance_item_id: Optional[str] = None

    # ── phase 1:设计方案(跳过如果有 --doc) ──
    if doc_path is not None:
        plan_text = _read_file(doc_path)
    else:
        plan_payload: Dict[str, Any] = {
            "title": ui(
                f"{name} design", f"{name} 设计方案", language=ctx.language),
            "source_of_truth": {},
        }
        if goal_text:
            # 需求经 source_of_truth 通道进 planner 的 issue body(与 phase 2/3 同源),
            # 让 planner 据此编写设计方案,而非凭一个标题空想。
            plan_payload["source_of_truth"][
                ui("Request", "需求", language=ctx.language)] = goal_text
        if agents_snapshot.exists and agents_snapshot.content:
            plan_payload["source_of_truth"]["AGENTS.md"] = agents_snapshot.content
        res = run_task(
            ctx.engine,
            TaskKind.PLAN,
            plan_payload,
            ctx.planner,
            reviewers=reviewers,
            max_revisions=ctx.max_revisions,
            poll=poll_cb,
            confirm=ctx.confirm,
            dag_key=make_dag_key(TaskKind.PLAN, scope=plan_id),
        )
        plan_item_id = res["item_id"]
        plan_text = _phase_text(res["delivery"], _PLAN_KEY)
        project_rules_text = _phase_text(res["delivery"], "project_rules")

    # ── phase 2:验收文档(跳过如果 --no-acceptance) ──
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None
    if not ctx.no_acceptance:
        res = run_task(
            ctx.engine,
            TaskKind.ACCEPTANCE,
            {"title": ui(
                f"{name} acceptance document", f"{name} 验收文档",
                language=ctx.language),
             "source_of_truth": {"plan": plan_text}},
            ctx.planner,
            reviewers=reviewers,
            max_revisions=ctx.max_revisions,
            poll=poll_cb,
            confirm=ctx.confirm,
            source_refs=[r for r in [plan_item_id] if r],
            dag_key=make_dag_key(TaskKind.ACCEPTANCE, scope=plan_id),
        )
        acceptance_item_id = res["item_id"]
        acceptance_text = _phase_text(res["delivery"], _ACCEPTANCE_KEY)
        acceptance_doc = _validate_acceptance(acceptance_text)
        _write_if_missing(base_dir)
        with open(acceptance_path, "w", encoding="utf-8") as fh:
            fh.write(acceptance_text)

    # ── phase 3:拆解(经 lint 机器门 ≤ max_revisions 轮 + 内置 review) ──
    decompose_inputs = {"plan": plan_text}
    if acceptance_text is not None:
        decompose_inputs["acceptance"] = acceptance_text
    guard = _compose_guard(
        ctx.members,
        project_root=project_root,
        acceptance_doc=acceptance_doc,
    )
    res = run_task(
        ctx.engine,
        TaskKind.DECOMPOSE,
        {"title": ui(
            f"{name} decomposition", f"{name} 拆解", language=ctx.language),
         "source_of_truth": decompose_inputs},
        ctx.orchestrator,
        reviewers=reviewers,
        max_revisions=ctx.max_revisions,
        poll=poll_cb,
        guard=guard,
        source_refs=[r for r in [plan_item_id, acceptance_item_id] if r],
        dag_key=make_dag_key(TaskKind.DECOMPOSE, scope=plan_id),
    )
    decompose_item_id = res["item_id"]
    manifest_text = _phase_text(res["delivery"], _MANIFEST_KEY)
    _write_if_missing(base_dir)

    # provenance:把塑造本 DAG 的源头 issue(设计/验收/拆解)记入 manifest meta,
    # 让 dag run 派发的 develop issue 也能溯源,防后续执行跑偏。
    source_issues = [r for r in [plan_item_id, acceptance_item_id, decompose_item_id] if r]
    manifest = loads_manifest(manifest_text, project_root=project_root)
    manifest.meta["plan_id"] = plan_id
    manifest.meta.setdefault("name", name)
    manifest.meta["acceptance_required"] = not ctx.no_acceptance
    if not ctx.no_acceptance:
        manifest.meta["acceptance_file"] = os.path.basename(acceptance_path)
    else:
        manifest.meta.pop("acceptance_file", None)
    if source_issues:
        manifest.meta["source_issues"] = source_issues
    save_manifest(manifest, manifest_path)
    output_paths = [manifest_path]
    if not ctx.no_acceptance:
        output_paths.append(acceptance_path)
    if project_rules_text is not None:
        write_project_rules(project_rules_text, agents_snapshot)
        output_paths.append("AGENTS.md")
    commit_files(
        output_paths, "chore(omac): sync plan outputs",
        engine_type=store.config.engine_type)
    _emit_plan_next_steps(manifest_path, acceptance_path, ctx.language)

    return 0


def plan_resume(
    ctx: PlanContext,
    *,
    dag_key: Optional[str] = None,
    plan_id: Optional[str] = None,
    name: Optional[str] = None,
    poll: Optional[Callable[[], None]] = None,
) -> int:
    """按唯一 plan_id/dag_key 恢复 plan create 流水线。

    续跑锚点是机器生成的 plan_id,不是人类可重复的 name。任一阶段存在已建
    issue 时复用原 issue,避免中断后创建第二条设计/验收/拆解 issue。
    """
    if dag_key:
        plan_id_value = plan_id_from_dag_key(dag_key)
    elif plan_id:
        plan_id_value = plan_id_from_dag_key(plan_id) if plan_id.startswith("plan-") else plan_id
    else:
        raise ValidationError(ui(
            "plan resume requires --dag-key or --plan-id",
            "plan resume 需要 --dag-key 或 --plan-id"))

    store = ctx.engine.store
    ensure_config_synced(CONFIG_PATH, branch="main", engine_type=store.config.engine_type)
    ensure_files_clean(["AGENTS.md"], engine_type=store.config.engine_type)
    base_dir = CONFIG_DIR
    reviewers = [] if ctx.no_review else ctx.reviewers
    poll_cb = poll if poll is not None else ctx.poll()
    agents_snapshot = read_agents_snapshot()

    plan_key = make_dag_key(TaskKind.PLAN, scope=plan_id_value)
    plan_item = _require_by_dag_key(ctx, TaskKind.PLAN, plan_key)
    if not plan_item.project_rules:
        # 历史 plan 只有设计文档时不能绕过新双交付契约。复用原 issue
        # 回到 authoring,由 planner 补交两份文件并重新走确认/review。
        store.reset_review(plan_item.id)
        store.update_status(plan_item.id, WorkItemStatus.TODO)
        plan_item = store.get_work_item(plan_item.id)
    resolved_name = name or _name_from_plan_issue(plan_item)
    manifest_path = os.path.join(base_dir, f"{resolved_name}.yaml")
    project_root = project_root_from_manifest_path(manifest_path)
    acceptance_path = os.path.join(base_dir, f"{resolved_name}.acceptance.yaml")

    res = run_task(
        ctx.engine,
        TaskKind.PLAN,
        {"title": ui(
            f"{resolved_name} design", f"{resolved_name} 设计方案",
            language=ctx.language),
         "source_of_truth": (
             {"AGENTS.md": agents_snapshot.content}
             if agents_snapshot.exists and agents_snapshot.content else {})},
        ctx.planner,
        reviewers=reviewers,
        max_revisions=ctx.max_revisions,
        poll=poll_cb,
        confirm=ctx.confirm,
        dag_key=plan_key,
        resume_item_id=plan_item.id,
    )
    plan_item_id = res["item_id"]
    plan_text = _phase_text(res["delivery"], _PLAN_KEY)
    project_rules_text = _phase_text(res["delivery"], "project_rules")

    acceptance_text: Optional[str] = None
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None
    acceptance_item_id: Optional[str] = None
    if not ctx.no_acceptance:
        acceptance_key = make_dag_key(TaskKind.ACCEPTANCE, scope=plan_id_value)
        acceptance_item = _find_by_dag_key(ctx, TaskKind.ACCEPTANCE, acceptance_key)
        res = run_task(
            ctx.engine,
            TaskKind.ACCEPTANCE,
            {"title": ui(
                f"{resolved_name} acceptance document", f"{resolved_name} 验收文档",
                language=ctx.language),
             "source_of_truth": {"plan": plan_text}},
            ctx.planner,
            reviewers=reviewers,
            max_revisions=ctx.max_revisions,
            poll=poll_cb,
            confirm=ctx.confirm,
            source_refs=[plan_item_id],
            dag_key=acceptance_key,
            resume_item_id=acceptance_item.id if acceptance_item else None,
        )
        acceptance_item_id = res["item_id"]
        acceptance_text = _phase_text(res["delivery"], _ACCEPTANCE_KEY)
        acceptance_doc = _validate_acceptance(acceptance_text)
        _write_if_missing(base_dir)
        with open(acceptance_path, "w", encoding="utf-8") as fh:
            fh.write(acceptance_text)

    decompose_inputs = {"plan": plan_text}
    if acceptance_text is not None:
        decompose_inputs["acceptance"] = acceptance_text
    decompose_key = make_dag_key(TaskKind.DECOMPOSE, scope=plan_id_value)
    decompose_item = _find_by_dag_key(ctx, TaskKind.DECOMPOSE, decompose_key)
    res = run_task(
        ctx.engine,
        TaskKind.DECOMPOSE,
        {"title": ui(
            f"{resolved_name} decomposition", f"{resolved_name} 拆解",
            language=ctx.language),
         "source_of_truth": decompose_inputs},
        ctx.orchestrator,
        reviewers=reviewers,
        max_revisions=ctx.max_revisions,
        poll=poll_cb,
        guard=_compose_guard(
            ctx.members,
            project_root=project_root,
            acceptance_doc=acceptance_doc,
        ),
        source_refs=[r for r in [plan_item_id, acceptance_item_id] if r],
        dag_key=decompose_key,
        resume_item_id=decompose_item.id if decompose_item else None,
    )
    decompose_item_id = res["item_id"]
    manifest_text = _phase_text(res["delivery"], _MANIFEST_KEY)
    _write_if_missing(base_dir)

    source_issues = [r for r in [plan_item_id, acceptance_item_id, decompose_item_id] if r]
    manifest = loads_manifest(manifest_text, project_root=project_root)
    manifest.meta["plan_id"] = plan_id_value
    manifest.meta.setdefault("name", resolved_name)
    manifest.meta["acceptance_required"] = not ctx.no_acceptance
    if not ctx.no_acceptance:
        manifest.meta["acceptance_file"] = os.path.basename(acceptance_path)
    else:
        manifest.meta.pop("acceptance_file", None)
    if source_issues:
        manifest.meta["source_issues"] = source_issues
    save_manifest(manifest, manifest_path)
    output_paths = [manifest_path]
    if not ctx.no_acceptance:
        output_paths.append(acceptance_path)
    write_project_rules(project_rules_text, agents_snapshot)
    output_paths.append("AGENTS.md")
    commit_files(
        output_paths, "chore(omac): sync plan outputs",
        engine_type=store.config.engine_type)
    _emit_plan_next_steps(manifest_path, acceptance_path, ctx.language)

    return 0

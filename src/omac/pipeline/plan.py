"""plan create 流水线编排(§7.2):设计方案→验收文档→拆解,三阶段全部经 tasks.run_task。

双模式一条流水线:
  --doc 给了 → 跳过 planner 设计环节,由 OMAC 发布承载文档的来源 issue(deliverable 附件),
                再进验收文档 + 拆解；目录输入会形成通用设计源清单,由 Agent 按仓库规则
                和内容读取完整文档集
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
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from ..core import acceptance as acceptance_mod
from ..core.config import CONFIG_DIR, CONFIG_PATH
from ..core.gitsync import commit_files, ensure_config_synced, ensure_files_clean
from ..core.lint import lint
from ..core.manifest import load_manifest, loads_manifest, manifest_files, save_manifest
from ..core.project_rules import read_agents_snapshot, write_project_rules
from ..core.review_preflight import plan_manifest_preflight
from ..core.review_continuation import (
    authorized_review_limit, build_review_continuation, review_continuation_mode,
)
from ..core.taskmeta import (
    TaskKind, TaskPhase, build_plan_profile, make_dag_key, make_plan_id, plan_id_of,
)
from ..core.workitem import WorkItem, WorkItemStatus
from ..errors import ValidationError
from ..i18n import CN, ui
from .plan_source import build_design_source, source_identity
from .tasks import run_task
from .apply import Ports, apply, apply_one
from .decide_transitions import rework_to_producer
from .decide_effects import CreateWorkItem, UpdateMetadata, UpdateStatus


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


def _phase_text(delivery: Dict[str, Any], key: str) -> str:
    """从 run_task 返回的 delivery 取某 key 的文本交付。"""
    value = delivery.get(key)
    if not value:
        raise ValidationError(ui(
            f"Stage delivery is missing '{key}'. Check the delivery contract and submitted artifacts.",
            f"阶段交付缺少 '{key}' —— 产出者未在 artifacts 中交付;请检查交付契约。"))
    return str(value)


def _write_if_missing(dirpath: str) -> None:
    os.makedirs(dirpath or ".", exist_ok=True)


def _validate_acceptance(text: str) -> acceptance_mod.AcceptanceDoc:
    """按 core/acceptance schema 校验验收文档文本(结构不全即报错)。"""
    raw = yaml.safe_load(text)
    return acceptance_mod.load_acceptance_doc(raw)


def _acceptance_guard(item: WorkItem) -> List[str]:
    """acceptance authoring 左移质量门；错误作为返工上下文交回 planner。"""
    try:
        acceptance_doc = _validate_acceptance(item.deliverable or "")
    except (ValueError, yaml.YAMLError) as exc:
        return [f"acceptance quality gate: {exc}"]
    if acceptance_doc.schema != "omac.acceptance/v2":
        return [
            "acceptance quality gate: new acceptance authoring must use "
            "omac.acceptance/v2 with explicit action.id; v1 remains readable "
            "for existing plans and active manifests"
        ]
    return []


def _find_by_dag_key(ctx: PlanContext, kind: TaskKind, dag_key: str) -> Optional[WorkItem]:
    return _find_stage_item(
        ctx.engine.store, ctx.workspace_id, kind, dag_key)


def _find_stage_item(
    store,
    workspace_id: str,
    kind: TaskKind,
    dag_key: str,
) -> Optional[WorkItem]:
    matches = [item for item in store.find_work_items_by_dag_key(workspace_id, dag_key) if item.kind == kind]
    if len(matches) > 1:
        raise ValidationError(ui(
            f"dag_key is not unique: {dag_key}. Resolve duplicate platform issues first.",
            f"dag_key 不唯一:{dag_key} —— 平台数据异常,请先人工处理重复 issue。"))
    return matches[0] if matches else None


def _continuation_selector(
    *,
    dag_key: Optional[str],
    plan_id: Optional[str],
    stage: Optional[str],
) -> tuple[TaskKind, str, str]:
    allowed = {
        TaskKind.PLAN.value: TaskKind.PLAN,
        TaskKind.ACCEPTANCE.value: TaskKind.ACCEPTANCE,
        TaskKind.DECOMPOSE.value: TaskKind.DECOMPOSE,
    }
    if dag_key:
        stage_value = next((prefix for prefix in allowed if dag_key.startswith(f"{prefix}-")), None)
        if stage_value is None:
            raise ValidationError(ui(
                f"Unsupported plan stage DAG key: {dag_key}",
                f"不支持的 plan stage DAG key:{dag_key}"))
        if stage and stage != stage_value:
            raise ValidationError(ui(
                f"--stage {stage} conflicts with --dag-key {dag_key}",
                f"--stage {stage} 与 --dag-key {dag_key} 冲突"))
        return allowed[stage_value], dag_key, plan_id_of(dag_key)
    if not plan_id or not stage:
        raise ValidationError(ui(
            "continue-review requires --dag-key, or --plan-id together with --stage",
            "continue-review 需要 --dag-key，或同时提供 --plan-id 与 --stage"))
    if stage not in allowed:
        raise ValidationError(ui(
            f"Unsupported review stage: {stage}",
            f"不支持的 review stage:{stage}"))
    plan_id_value = plan_id_of(plan_id)
    kind = allowed[stage]
    return kind, make_dag_key(kind, scope=plan_id_value), plan_id_value


def plan_continue_review(
    engine,
    configured_limit: int,
    *,
    dag_key: Optional[str] = None,
    plan_id: Optional[str] = None,
    stage: Optional[str] = None,
    issue_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist one operator-authorized review round without changing project config."""
    kind, stage_key, plan_id_value = _continuation_selector(
        dag_key=dag_key, plan_id=plan_id, stage=stage)
    store = engine.store
    if issue_id:
        item = store.get_work_item(issue_id)
        if item.kind != kind or item.dag_key != stage_key:
            raise ValidationError(ui(
                f"Issue {issue_id} is {item.kind.value}/{item.dag_key}, expected "
                f"{kind.value}/{stage_key}.",
                f"Issue {issue_id} 是 {item.kind.value}/{item.dag_key}，期望 "
                f"{kind.value}/{stage_key}。"))
    else:
        item = _find_stage_item(
            store, store.config.workspace_id, kind, stage_key)
        if item is None:
            raise ValidationError(ui(
                f"No {kind.value} issue matches {stage_key}.",
                f"未找到 {stage_key} 对应的 {kind.value} issue。"))

    if engine.runtime.is_active(item.id):
        raise ValidationError(ui(
            f"Work item {item.id} still has an active Agent run. Wait for it to finish "
            "before authorizing another review round; continue-review never cancels runs.",
            f"Work item {item.id} 仍有活跃 Agent run。请等待其结束后再授权额外 review；"
            "continue-review 永不取消运行。"))

    current_limit = authorized_review_limit(item, configured_limit)
    current_round = max(0, item.bounces.review)
    if current_round < current_limit:
        raise ValidationError(ui(
            f"Review round {current_round + 1} is already authorized through round "
            f"{current_limit}. Run plan resume instead of stacking another decision.",
            f"当前已授权到 review round {current_limit}；请运行 plan resume，"
            "不要重复叠加 decision。"))

    mode = review_continuation_mode(item)
    if mode is None:
        raise ValidationError(ui(
            f"Work item {item.id} is not in an exhausted reject or final-nits state.",
            f"Work item {item.id} 不处于 review reject 耗尽或 final-nits 待复评状态。"))

    decision_reason = (
        (reason or "operator approved one additional review round").strip()
        or "operator approved one additional review round"
    )
    if len(decision_reason.encode("utf-8")) > 1024:
        raise ValidationError(ui(
            "--reason must be at most 1024 UTF-8 bytes",
            "--reason 最多 1024 UTF-8 bytes"))
    continuation = build_review_continuation(
        item, configured_limit, mode=mode, reason=decision_reason)
    apply([
        UpdateMetadata(item.id, {'review_continuation': continuation}),
    ], Ports(store=store))
    if mode == "producer-rework":
        apply(rework_to_producer(item.id), Ports(store=store))
    elif item.status == WorkItemStatus.BLOCKED:
        apply([
            UpdateMetadata(item.id, {'decision_required': {}}),
            UpdateStatus(item.id, WorkItemStatus.IN_REVIEW),
        ], Ports(store=store))

    return {
        "item_id": item.id,
        "dag_key": stage_key,
        "plan_id": plan_id_value,
        "stage": kind.value,
        "mode": mode,
        "authorized_through_round": continuation["authorized_through_round"],
        "decision_count": continuation["decision_count"],
        "next_action": f"omac plan resume --plan-id {plan_id_value}",
    }


def _require_by_dag_key(ctx: PlanContext, kind: TaskKind, dag_key: str) -> WorkItem:
    item = _find_by_dag_key(ctx, kind, dag_key)
    if item is None:
        raise ValidationError(ui(
            f"No {kind.value} issue matches {dag_key}. Use the DAG key printed by "
            "plan create or shown in the issue title.",
            f"未找到 {dag_key} 对应的 {kind.value} issue —— "
            "请确认使用的是 plan create 输出/issue 标题里的 DAG 标识。"))
    return item


def _exact_stage_item(
    ctx: PlanContext,
    kind: TaskKind,
    dag_key: str,
    issue_id: str,
) -> WorkItem:
    """用精确 ID 绕过项目列表，并验证读得出的阶段身份。"""
    item = ctx.engine.store.get_work_item(issue_id)
    if item.kind != kind or item.dag_key != dag_key:
        raise ValidationError(ui(
            f"Issue {issue_id} is {item.kind.value}/{item.dag_key}, expected "
            f"{kind.value}/{dag_key}.",
            f"Issue {issue_id} 是 {item.kind.value}/{item.dag_key}，期望 "
            f"{kind.value}/{dag_key}。"))
    return item


def _unstarted_stage_snapshot(
    ctx: PlanContext,
    kind: TaskKind,
    dag_key: str,
    issue_id: str,
    worker: str,
) -> WorkItem:
    """旧巨型正文不可读时，仅为未派发 authoring issue 构造恢复快照。"""
    return WorkItem(
        id=issue_id,
        workspace_id=ctx.workspace_id,
        title=f"[DAG:{dag_key}]",
        description="",
        status=WorkItemStatus.TODO,
        dag_key=dag_key,
        worker=worker,
        kind=kind,
        phase=TaskPhase.AUTHORING,
    )


def _name_from_plan_issue(item: WorkItem) -> str:
    title = re.sub(r"^(\[DAG:[^\]]+\]\s*)+", "", item.title or "").strip()
    for suffix in (" design (--doc)", " 设计方案（--doc）", " design", " 设计方案"):
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
            break
    return title or plan_id_of(item.dag_key)


def _compose_guard(
    members: set,
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None,
    *,
    strict_plan: bool = False,
) -> Callable[[WorkItem], List[str]]:
    """造 decompose 的 lint 机器门(零 token,≤ max_revisions 轮)。

    从交付 artifacts 取 manifest 文本,解析后跑 core/lint(有验收文档时附加
    锚定校验:contract.acceptance 每条须锚定验收文档 flow.id)。strict_plan
    还执行新 plan 的 owner/scope/typed-I/O/责任冲突 preflight；旧运行
    manifest 由默认非 strict 路径保持兼容。返回错误字符串列表(空 = 通过)。
    """

    def guard(item: WorkItem) -> List[str]:
        text = getattr(item, "deliverable", None)
        if not text:
            return [ui(
                f"Delivery is missing '{_MANIFEST_KEY}'; the orchestrator did not submit a manifest.",
                f"交付缺少 '{_MANIFEST_KEY}' —— orchestrator 未产出 manifest")]
        try:
            manifest = loads_manifest(text)
        except Exception as exc:
            return [f"manifest preflight could not parse manifest: {exc}"]
        errors = lint(
            manifest,
            members,
            acceptance=acceptance_doc,
            require_explicit_responsibility=(
                acceptance_doc is not None
                and acceptance_doc.schema == "omac.acceptance/v2"
            ),
        )
        if strict_plan:
            errors.extend(plan_manifest_preflight(
                manifest, acceptance_doc=acceptance_doc))
        return list(dict.fromkeys(errors))

    return guard


def _profile_of(ctx: PlanContext, plan_id: str, name: str, source: str) -> Dict[str, Any]:
    return build_plan_profile(plan_id=plan_id, name=name, source=source, no_review=ctx.no_review,
                              no_acceptance=ctx.no_acceptance, confirm=ctx.confirm)


def _plan_stage_payload(ctx: PlanContext, name: str, goal_text: Optional[str],
                        agents_snapshot, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "title": ui(f"{name} design", f"{name} 设计方案", language=ctx.language),
        "source_of_truth": {},
        "plan_profile": profile,
    }
    if goal_text:
        # 需求经 source_of_truth 通道进 planner 的 issue body(与 phase 2/3 同源),
        # 让 planner 据此编写设计方案,而非凭一个标题空想。
        payload["source_of_truth"][ui("Request", "需求", language=ctx.language)] = goal_text
    if agents_snapshot.exists and agents_snapshot.content:
        payload["source_of_truth"]["AGENTS.md"] = agents_snapshot.content
    return payload


def _publish_doc_source(ctx: PlanContext, name: str, plan_id: str, text: str,
                        profile: Optional[Dict[str, Any]] = None) -> str:
    """--doc 等价于 planner 阶段已完成：发布承载文档的来源 issue，返回其 id。

    dag_key 用 doc- 前缀：这条 issue 没有 Agent 产出，不能被无 --doc 的 plan resume
    当作缺项目规范的 plan issue 退回 planner。不指派 Agent，因此不产生 Run。
    profile 只在新建时写入；老流水线没有 profile，resume 不补写。
    """
    store = ctx.engine.store
    dag_key = f"doc-{plan_id}"
    existing = _find_by_dag_key(ctx, TaskKind.PLAN, dag_key)
    title = ui(f"{name} design (--doc)", f"{name} 设计方案（--doc）", language=ctx.language)
    item = store.get_work_item(existing.id) if existing else apply_one(CreateWorkItem({
        'workspace_id': store.config.workspace_id,
        'title': title,
        'description': title,
        'dag_key': dag_key,
        'worker': ctx.planner,
        'kind': TaskKind.PLAN,
    }), Ports(store=store))
    # 文档变化时发布新版本；仍绑定旧摘要的下游 work read 会失败关闭。
    fields: Dict[str, Any] = {}
    if item.deliverable != text:
        fields['deliverable'] = text
    if profile is not None and item.plan_profile is None:
        fields['plan_profile'] = profile
    if fields:
        apply([UpdateMetadata(item.id, fields)], Ports(store=store))
    if item.status != WorkItemStatus.DONE:
        apply([UpdateStatus(item.id, WorkItemStatus.DONE)], Ports(store=store))
    return item.id


def _stage_source_refs(plan_item_id: Optional[str],
                       acceptance_item_id: Optional[str] = None) -> List[Dict[str, Any]]:
    refs = []
    if plan_item_id:
        refs.append({"label": "plan", "kind": "plan", "issue_id": plan_item_id})
    if acceptance_item_id:
        refs.append({"label": "acceptance", "kind": "acceptance", "issue_id": acceptance_item_id})
    return refs


def _run_plan_stage(ctx: PlanContext, name: str, *, goal_text: Optional[str], agents_snapshot,
                    reviewers, poll_cb, dag_key: str, resume_item_id: Optional[str] = None,
                    restart_active: bool = False, profile: Optional[Dict[str, Any]] = None,
                    refresh_source: bool = False) -> Tuple[str, str, str]:
    """phase 1:设计方案。返回 (plan_item_id, plan_text, project_rules_text)。"""
    res = run_task(
        ctx.engine,
        TaskKind.PLAN,
        _plan_stage_payload(ctx, name, goal_text, agents_snapshot, profile),
        ctx.planner,
        reviewers=reviewers,
        max_revisions=ctx.max_revisions,
        poll=poll_cb,
        confirm=ctx.confirm,
        dag_key=dag_key,
        resume_item_id=resume_item_id,
        restart_active=restart_active,
        refresh_source=refresh_source,
    )
    return (res["item_id"], _phase_text(res["delivery"], _PLAN_KEY),
            _phase_text(res["delivery"], "project_rules"))


def _run_acceptance_stage(
    ctx: PlanContext, name: str, *, plan_text: str, plan_item_id: Optional[str], reviewers,
    poll_cb, dag_key: str, resume_item_id: Optional[str], restart_active: bool = False,
    refresh_source: bool = False, title: Optional[str] = None,
) -> Tuple[str, acceptance_mod.AcceptanceDoc, str]:
    """phase 2:验收文档。返回 (text, doc, item_id);落盘在 _write_plan_outputs。"""
    res = run_task(
        ctx.engine,
        TaskKind.ACCEPTANCE,
        {"title": title or ui(
            f"{name} acceptance document", f"{name} 验收文档", language=ctx.language),
         "source_of_truth": {"plan": plan_text}},
        ctx.planner,
        reviewers=reviewers,
        max_revisions=ctx.max_revisions,
        poll=poll_cb,
        guard=_acceptance_guard,
        confirm=ctx.confirm,
        source_refs=_stage_source_refs(plan_item_id),
        dag_key=dag_key,
        resume_item_id=resume_item_id,
        restart_active=restart_active,
        refresh_source=refresh_source,
    )
    text = _phase_text(res["delivery"], _ACCEPTANCE_KEY)
    return text, _validate_acceptance(text), res["item_id"]


def _run_decompose_stage(
    ctx: PlanContext, name: str, *, plan_text: str, acceptance_text: Optional[str],
    acceptance_doc, plan_item_id: Optional[str], acceptance_item_id: Optional[str],
    reviewers, poll_cb, dag_key: str, resume_item_id: Optional[str], restart_active: bool = False,
    refresh_source: bool = False,
) -> Tuple[str, str]:
    """phase 3:拆解(经 lint 机器门 ≤ max_revisions 轮 + 内置 review)。返回 (manifest_text, item_id)。"""
    inputs = {"plan": plan_text}
    if acceptance_text is not None:
        inputs["acceptance"] = acceptance_text
    res = run_task(
        ctx.engine,
        TaskKind.DECOMPOSE,
        {"title": ui(f"{name} decomposition", f"{name} 拆解", language=ctx.language),
         "source_of_truth": inputs},
        ctx.orchestrator,
        reviewers=reviewers,
        max_revisions=ctx.max_revisions,
        poll=poll_cb,
        guard=_compose_guard(ctx.members, acceptance_doc=acceptance_doc, strict_plan=True),
        source_refs=_stage_source_refs(plan_item_id, acceptance_item_id),
        dag_key=dag_key,
        resume_item_id=resume_item_id,
        review_acceptance_doc=acceptance_doc,
        restart_active=restart_active,
        refresh_source=refresh_source,
    )
    return _phase_text(res["delivery"], _MANIFEST_KEY), res["item_id"]


def _materialized(manifest_path: str, plan_id: str) -> bool:
    """Whether the manifest on disk already is this plan's; another plan's DAG that has started is never replaced."""
    if not os.path.exists(manifest_path):
        return False
    existing = load_manifest(manifest_path)
    if existing.meta.get("plan_id") == plan_id:
        return True
    started = sorted(n.id for n in existing.nodes.values() if n.status != "todo" or n.work_item_id)
    if started:
        raise ValidationError(ui(
            f"{manifest_path} already belongs to another plan and its nodes have started ({', '.join(started)}). "
            "Choose a different --name; the running DAG's state is never overwritten.",
            f"{manifest_path} 属于另一个 plan 且其节点已开始（{', '.join(started)}）。"
            "请换一个 --name；运行中 DAG 的状态不会被覆盖。"))
    return False


def _write_plan_outputs(
    ctx: PlanContext, *, plan_id: str, name: str, manifest_text: str, acceptance_text: Optional[str],
    source_issues: List[str], manifest_path: str, acceptance_path: str, project_rules_text: Optional[str],
    agents_snapshot,
) -> None:
    """Materialize the reviewed outputs. A manifest that already is this plan's is never rewritten:
    its node state belongs to the running DAG; only a missing acceptance document is restored."""
    _write_if_missing(os.path.dirname(manifest_path))
    owned = _materialized(manifest_path, plan_id)
    output_paths: List[str] = []
    if not owned:
        # provenance:把塑造本 DAG 的源头 issue(设计/验收/拆解)记入 manifest meta,
        # 让 dag run 派发的 develop issue 也能溯源,防后续执行跑偏。
        # Reviewer 审查原始 decompose deliverable；最终文件使用同一个 Manifest
        # 执行模型做 canonical dump。权威 acceptance 原文单独落盘并由 meta.acceptance_file 引用。
        manifest = loads_manifest(manifest_text)
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
        output_paths += manifest_files(manifest_path)
    if acceptance_text is not None and (not owned or not os.path.exists(acceptance_path)):
        with open(acceptance_path, "w", encoding="utf-8") as fh:
            fh.write(acceptance_text)
        output_paths.append(acceptance_path)
    if project_rules_text is not None:
        write_project_rules(project_rules_text, agents_snapshot)
        output_paths.append("AGENTS.md")
    if output_paths:
        commit_files(
            output_paths, "chore(omac): sync plan outputs",
            engine_type=ctx.engine.store.config.engine_type)
    _emit_plan_next_steps(manifest_path, acceptance_path, ctx.language)


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
    engine_type = ctx.engine.store.config.engine_type
    # 派单前:真实引擎下自动把 config 同步到 main,否则隔离区 agent clone 后读不到。
    ensure_config_synced(CONFIG_PATH, branch="main", engine_type=engine_type)
    if doc_path is None:
        ensure_files_clean(["AGENTS.md"], engine_type=engine_type)
    base_dir = CONFIG_DIR
    manifest_path = os.path.join(base_dir, f"{name}.yaml")
    acceptance_path = os.path.join(base_dir, f"{name}.acceptance.yaml")
    plan_id = make_plan_id()
    _materialized(manifest_path, plan_id)      # refuse early when --name collides with a started DAG
    reviewers = [] if ctx.no_review else ctx.reviewers
    poll_cb = poll if poll is not None else ctx.poll()
    agents_snapshot = read_agents_snapshot() if doc_path is None else None
    stage = dict(reviewers=reviewers, poll_cb=poll_cb)

    # ── phase 1:设计方案(跳过如果有 --doc) ──
    plan_item_id: Optional[str] = None
    project_rules_text: Optional[str] = None
    if doc_path is not None:
        plan_text = build_design_source(doc_path, engine_type=engine_type, language=ctx.language)
        plan_item_id = _publish_doc_source(
            ctx, name, plan_id, plan_text, _profile_of(ctx, plan_id, name, "doc"))
    else:
        plan_item_id, plan_text, project_rules_text = _run_plan_stage(
            ctx, name, goal_text=goal_text, agents_snapshot=agents_snapshot,
            dag_key=make_dag_key(TaskKind.PLAN, scope=plan_id),
            profile=_profile_of(ctx, plan_id, name, "goal"), **stage)

    # ── phase 2:验收文档(跳过如果 --no-acceptance) ──
    acceptance_text: Optional[str] = None
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None
    acceptance_item_id: Optional[str] = None
    if not ctx.no_acceptance:
        acceptance_text, acceptance_doc, acceptance_item_id = _run_acceptance_stage(
            ctx, name, plan_text=plan_text, plan_item_id=plan_item_id,
            dag_key=make_dag_key(TaskKind.ACCEPTANCE, scope=plan_id), resume_item_id=None, **stage)

    # ── phase 3:拆解 ──
    manifest_text, decompose_item_id = _run_decompose_stage(
        ctx, name, plan_text=plan_text, acceptance_text=acceptance_text,
        acceptance_doc=acceptance_doc, plan_item_id=plan_item_id,
        acceptance_item_id=acceptance_item_id,
        dag_key=make_dag_key(TaskKind.DECOMPOSE, scope=plan_id), resume_item_id=None, **stage)

    _write_plan_outputs(
        ctx, plan_id=plan_id, name=name, manifest_text=manifest_text, acceptance_text=acceptance_text,
        source_issues=[r for r in [plan_item_id, acceptance_item_id, decompose_item_id] if r],
        manifest_path=manifest_path, acceptance_path=acceptance_path,
        project_rules_text=project_rules_text, agents_snapshot=agents_snapshot)
    return 0


def _resume_plan_id(dag_key: Optional[str], plan_id: Optional[str]) -> str:
    if dag_key or plan_id:
        return plan_id_of(dag_key or plan_id)
    raise ValidationError(ui(
        "plan resume requires --dag-key or --plan-id",
        "plan resume 需要 --dag-key 或 --plan-id"))


def _adopt_profile(ctx: PlanContext, plan_id: str, profile: Optional[Dict[str, Any]],
                   cli_flags: Dict[str, bool]) -> None:
    """The recorded shape wins; a flag that contradicts it is refused with the command that does not."""
    if profile is None:
        return
    conflicts = [flag for flag, key, recorded in (
        ("--no-review", "no_review", profile["no_review"]),
        ("--no-acceptance", "no_acceptance", profile["no_acceptance"]),
        ("--no-confirm", "no_confirm", not profile["confirm"]),
    ) if cli_flags.get(key) and not recorded]
    if conflicts:
        raise ValidationError(ui(
            f"{', '.join(conflicts)} contradicts how plan {plan_id} was created. A resumed pipeline keeps its shape: "
            f"run `omac plan resume --plan-id {plan_id}` without it.",
            f"{', '.join(conflicts)} 与 plan {plan_id} 创建时的形态冲突。续跑沿用原形态:"
            f"请去掉该参数运行 `omac plan resume --plan-id {plan_id}`。"))
    ctx.no_review, ctx.no_acceptance, ctx.confirm = profile["no_review"], profile["no_acceptance"], profile["confirm"]


@dataclass
class _Source:
    """The source of a resumed pipeline: its stage-1 issue and the text later stages read."""
    name: str
    item_id: str
    text: str
    project_rules: Optional[str] = None
    changed: bool = False       # an explicit --doc replaced the published source


def _resume_doc_source(ctx: PlanContext, plan_id: str, doc: Optional[WorkItem], name: Optional[str],
                       doc_path: Optional[str]) -> _Source:
    """A --doc pipeline: the source issue is the text; --doc only republishes it when its content differs."""
    profile = doc.plan_profile if doc else None
    name = name or (profile or {}).get("name") or (_name_from_plan_issue(doc) if doc else None)
    if not name:
        raise ValidationError(ui(
            f"plan resume --doc requires --name: no source issue doc-{plan_id} exists to take it from",
            f"plan resume --doc 需要 --name:不存在可推断名字的来源 issue doc-{plan_id}"))
    text = doc.deliverable if doc else None
    changed = False
    if doc_path is not None:
        fresh = build_design_source(
            doc_path, engine_type=ctx.engine.store.config.engine_type, language=ctx.language)
        changed = text is not None and source_identity(fresh) != source_identity(text)
        if text is None or changed:
            text = fresh
    if not text:
        raise ValidationError(ui(
            f"The source issue doc-{plan_id} carries no design text. Run "
            f"`omac plan resume --plan-id {plan_id} --doc <file-or-directory>` to publish it.",
            f"来源 issue doc-{plan_id} 没有设计文本。请运行 "
            f"`omac plan resume --plan-id {plan_id} --doc <文件或目录>` 发布。"))
    return _Source(name, _publish_doc_source(ctx, name, plan_id, text), text, changed=changed)


def _resume_goal_source(ctx: PlanContext, plan_key: str, name: Optional[str], agents_snapshot,
                        reviewers, poll_cb, restart_active: bool) -> _Source:
    store = ctx.engine.store
    plan_item = _require_by_dag_key(ctx, TaskKind.PLAN, plan_key)
    if not plan_item.project_rules:
        # 历史 plan 只有设计文档时不能绕过新双交付契约。复用原 issue
        # 回到 authoring,由 planner 补交两份文件并重新走确认/review。
        apply(rework_to_producer(plan_item.id), Ports(store=store))
        plan_item = store.get_work_item(plan_item.id)
    resolved = name or (plan_item.plan_profile or {}).get("name") or _name_from_plan_issue(plan_item)
    item_id, plan_text, rules = _run_plan_stage(
        ctx, resolved, goal_text=None, agents_snapshot=agents_snapshot, reviewers=reviewers,
        poll_cb=poll_cb, dag_key=plan_key, resume_item_id=plan_item.id, restart_active=restart_active)
    return _Source(resolved, item_id, plan_text, rules)


def _stage_item_for_resume(ctx: PlanContext, kind: TaskKind, plan_id: str, issue_id: Optional[str],
                           worker: str) -> Optional[WorkItem]:
    key = make_dag_key(kind, scope=plan_id)
    if issue_id is None:
        return _find_by_dag_key(ctx, kind, key)
    if kind == TaskKind.DECOMPOSE:
        return _unstarted_stage_snapshot(ctx, kind, key, issue_id, worker)
    return _exact_stage_item(ctx, kind, key, issue_id)


def _restart_target(ctx: PlanContext, plan_id: str, *, doc: bool, acceptance_issue_id: Optional[str],
                    decompose_issue_id: Optional[str]) -> TaskKind:
    """--restart-active acts on the first stage that is not finished; a finished stage is never touched."""
    stages = [] if doc else [(TaskKind.PLAN, None, ctx.planner)]
    if not ctx.no_acceptance:
        stages.append((TaskKind.ACCEPTANCE, acceptance_issue_id, ctx.planner))
    stages.append((TaskKind.DECOMPOSE, decompose_issue_id, ctx.orchestrator))
    for kind, issue_id, worker in stages:
        item = _stage_item_for_resume(ctx, kind, plan_id, issue_id, worker)
        if item is None:
            break
        if item.status != WorkItemStatus.DONE:
            return kind
    raise ValidationError(ui(
        "--restart-active found no unfinished stage issue with an Agent run to restart.",
        "--restart-active 没有找到可重启的未完成阶段 issue。"))


def plan_resume(
    ctx: PlanContext,
    *,
    dag_key: Optional[str] = None,
    plan_id: Optional[str] = None,
    name: Optional[str] = None,
    doc_path: Optional[str] = None,
    restart_active: bool = False,
    acceptance_issue_id: Optional[str] = None,
    decompose_issue_id: Optional[str] = None,
    cli_flags: Optional[Dict[str, bool]] = None,
    poll: Optional[Callable[[], None]] = None,
) -> int:
    """按唯一 plan_id/dag_key 恢复 plan create 流水线。

    续跑锚点是机器生成的 plan_id,不是人类可重复的 name。来源 issue 记录了流水线形态
    (doc / goal、是否评审、验收、确认),所以 `plan resume --plan-id` 不需要再带任何形态参数。
    任一阶段存在已建 issue 时复用原 issue,避免中断后创建第二条设计/验收/拆解 issue。
    """
    plan_id_value = _resume_plan_id(dag_key, plan_id)
    engine_type = ctx.engine.store.config.engine_type
    ensure_config_synced(CONFIG_PATH, branch="main", engine_type=engine_type)
    doc = _find_by_dag_key(ctx, TaskKind.PLAN, f"doc-{plan_id_value}")
    from_doc = doc is not None or doc_path is not None
    plan_key = make_dag_key(TaskKind.PLAN, scope=plan_id_value)
    goal_item = None if from_doc else _require_by_dag_key(ctx, TaskKind.PLAN, plan_key)
    anchor = doc or goal_item
    _adopt_profile(ctx, plan_id_value, anchor.plan_profile if anchor else None, cli_flags or {})
    if not from_doc:
        ensure_files_clean(["AGENTS.md"], engine_type=engine_type)
    reviewers = [] if ctx.no_review else ctx.reviewers
    poll_cb = poll if poll is not None else ctx.poll()
    agents_snapshot = None if from_doc else read_agents_snapshot()
    restart = (
        _restart_target(ctx, plan_id_value, doc=from_doc, acceptance_issue_id=acceptance_issue_id,
                        decompose_issue_id=decompose_issue_id)
        if restart_active else None)
    stage = dict(reviewers=reviewers, poll_cb=poll_cb)

    source = (
        _resume_doc_source(ctx, plan_id_value, doc, name, doc_path) if from_doc else
        _resume_goal_source(ctx, plan_key, name, agents_snapshot, restart_active=restart == TaskKind.PLAN, **stage))
    manifest_path = os.path.join(CONFIG_DIR, f"{source.name}.yaml")
    acceptance_path = os.path.join(CONFIG_DIR, f"{source.name}.acceptance.yaml")
    refresh = source.changed          # the first stage that is not finished reads the replaced source

    acceptance_text: Optional[str] = None
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None
    acceptance_item_id: Optional[str] = None
    if not ctx.no_acceptance:
        acceptance_item = _stage_item_for_resume(
            ctx, TaskKind.ACCEPTANCE, plan_id_value, acceptance_issue_id, ctx.planner)
        acceptance_text, acceptance_doc, acceptance_item_id = _run_acceptance_stage(
            ctx, source.name, plan_text=source.text, plan_item_id=source.item_id,
            dag_key=make_dag_key(TaskKind.ACCEPTANCE, scope=plan_id_value),
            resume_item_id=acceptance_item.id if acceptance_item else None,
            restart_active=restart == TaskKind.ACCEPTANCE, refresh_source=refresh,
            title=ui(f"{source.name} acceptance document", f"{source.name} 验收文档", language=ctx.language),
            **stage)
        refresh = refresh and bool(acceptance_item) and acceptance_item.status == WorkItemStatus.DONE

    decompose_item = _stage_item_for_resume(
        ctx, TaskKind.DECOMPOSE, plan_id_value, decompose_issue_id, ctx.orchestrator)
    manifest_text, decompose_item_id = _run_decompose_stage(
        ctx, source.name, plan_text=source.text, acceptance_text=acceptance_text,
        acceptance_doc=acceptance_doc, plan_item_id=source.item_id,
        acceptance_item_id=acceptance_item_id,
        dag_key=make_dag_key(TaskKind.DECOMPOSE, scope=plan_id_value),
        resume_item_id=decompose_item.id if decompose_item else None,
        restart_active=restart == TaskKind.DECOMPOSE, refresh_source=refresh, **stage)

    _write_plan_outputs(
        ctx, plan_id=plan_id_value, name=source.name, manifest_text=manifest_text,
        acceptance_text=acceptance_text,
        source_issues=[r for r in [source.item_id, acceptance_item_id, decompose_item_id] if r],
        manifest_path=manifest_path, acceptance_path=acceptance_path,
        project_rules_text=source.project_rules, agents_snapshot=agents_snapshot)
    return 0

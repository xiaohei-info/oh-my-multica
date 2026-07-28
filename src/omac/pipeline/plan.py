"""plan create 流水线编排(§7.2):设计方案→验收文档→拆解,三阶段全部经 tasks.run_task。

双模式一条流水线:
  --doc 给了 → 跳过 planner 设计环节,直接进验收文档 + 拆解；目录输入会
                形成通用设计源清单,由 Agent 按仓库规则和内容读取完整文档集
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

import hashlib
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from ..core import acceptance as acceptance_mod
from ..core.config import CONFIG_DIR, CONFIG_PATH
from ..core.gitsync import commit_files, ensure_config_synced, ensure_files_clean
from ..core.lint import lint
from ..core.manifest import loads_manifest, save_manifest
from ..core.project_rules import read_agents_snapshot, write_project_rules
from ..core.review_continuation import (
    authorized_review_limit, build_review_continuation,
)
from ..core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase, make_dag_key, make_plan_id,
)
from ..engines.models import WorkItem, WorkItemStatus
from ..errors import ValidationError
from ..i18n import CN, ui
from .tasks import (
    AuthoringTaskSpec,
    refresh_authoring_task,
    run_task,
)


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


def _read_file(path: str, *, language: str | None = None) -> str:
    if not os.path.exists(path):
        raise ValidationError(ui(
            f"File not found: {path}. Check the --doc path.",
            f"文件不存在: {path} —— 请确认 --doc 路径"))
    if os.path.isdir(path):
        return _describe_design_directory(path, language=language)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _describe_design_directory(path: str, *, language: str | None = None) -> str:
    """把设计目录转换为可远程读取的确定性源集合，不复制文档正文。"""
    repository_root = Path.cwd().resolve()
    directory = Path(path).resolve()
    try:
        directory_name = directory.relative_to(repository_root).as_posix()
    except ValueError as exc:
        raise ValidationError(ui(
            f"Design directory must be inside the current repository: {path}",
            f"设计文档目录必须位于当前仓库内:{path}")) from exc

    files = sorted(candidate for candidate in directory.rglob("*") if candidate.is_file())
    if not files:
        raise ValidationError(ui(
            f"Design directory contains no files: {path}",
            f"设计文档目录中没有文件:{path}"))

    entries = []
    for candidate in files:
        resolved = candidate.resolve()
        try:
            relative_path = resolved.relative_to(repository_root).as_posix()
        except ValueError as exc:
            raise ValidationError(ui(
                f"Design directory contains a file outside the repository: {candidate}",
                f"设计文档目录包含仓库外文件:{candidate}")) from exc
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        entries.append(f"- `{relative_path}` sha256=`{digest}`")

    revision = _repository_revision(repository_root)
    instructions = ui(
        "Recursively inspect this directory and read every design document and necessary linked material. "
        "Determine authority, currentness, scope, and conflicts from repository governance and document "
        "content. Do not assume that any filename or subdirectory has a built-in meaning. Complete only "
        "the current stage delivery required by `work show`, and preserve precise file and section "
        "references for downstream stages.",
        "递归检查该目录,阅读其中全部设计文档及其引用的必要资料。根据仓库治理规则和文档内容判断"
        "权威关系、当前有效性、适用范围与冲突,不得假设任何文件名或子目录具有内置语义。只完成 "
        "work show 当前阶段要求的交付,并为下游阶段保留精确文件与章节引用。",
        language=language,
    )
    return (
        "# OMAC design directory source set\n\n"
        f"repository_revision: `{revision}`\n\n"
        f"design_directory: `{directory_name}`\n\n"
        f"{instructions}\n\n"
        "## File inventory\n\n"
        + "\n".join(entries)
        + "\n"
    )


def _repository_revision(repository_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


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
    matches = [
        item for item in store.list_work_items(workspace_id)
        if item.kind == kind and item.dag_key == dag_key
    ]
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
        plan_id_value = plan_id_from_dag_key(dag_key)
        stage_value = next(
            (prefix for prefix in allowed if dag_key.startswith(f"{prefix}-")),
            None,
        )
        if stage_value is None:
            raise ValidationError(ui(
                f"Unsupported plan stage DAG key: {dag_key}",
                f"不支持的 plan stage DAG key:{dag_key}"))
        if stage and stage != stage_value:
            raise ValidationError(ui(
                f"--stage {stage} conflicts with --dag-key {dag_key}",
                f"--stage {stage} 与 --dag-key {dag_key} 冲突"))
        return allowed[stage_value], dag_key, plan_id_value
    if not plan_id or not stage:
        raise ValidationError(ui(
            "continue-review requires --dag-key, or --plan-id together with --stage",
            "continue-review 需要 --dag-key，或同时提供 --plan-id 与 --stage"))
    if stage not in allowed:
        raise ValidationError(ui(
            f"Unsupported review stage: {stage}",
            f"不支持的 review stage:{stage}"))
    plan_id_value = (
        plan_id_from_dag_key(plan_id)
        if plan_id.startswith(("plan-", "acceptance-", "decompose-"))
        else plan_id
    )
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

    decision = (
        item.decision_required
        if isinstance(item.decision_required, dict)
        else {}
    )
    projected_final_nits = (
        item.status == WorkItemStatus.BLOCKED
        and decision.get("schema") == DECISION_REQUIRED_SCHEMA
        and decision.get("gate") == "review-nits"
        and decision.get("rounds") == current_round
    )
    review_only = (
        item.phase == TaskPhase.REVIEW
        and item.status in {WorkItemStatus.IN_REVIEW, WorkItemStatus.BLOCKED}
        and (item.status != WorkItemStatus.BLOCKED or projected_final_nits)
        and not item.review_verdict
        and bool(item.deliverable)
    )
    if item.review_verdict == "reject":
        mode = "producer-rework"
    elif review_only:
        mode = "review-only"
    else:
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
    store.update_work_item_metadata(
        item.id, review_continuation=continuation)
    if mode == "producer-rework":
        store.reset_review(item.id)
        store.update_status(item.id, WorkItemStatus.TODO)
    elif projected_final_nits:
        store.update_work_item_metadata(item.id, decision_required={})
        store.update_status(item.id, WorkItemStatus.IN_REVIEW)

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
    for suffix in (" design", " 设计方案"):
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
            break
    return title or plan_id_from_dag_key(item.dag_key)


def _compose_guard(
    members: set,
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None,
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
        manifest = loads_manifest(text)
        return lint(
            manifest,
            members,
            acceptance=acceptance_doc,
            require_explicit_responsibility=(
                acceptance_doc is not None
                and acceptance_doc.schema == "omac.acceptance/v2"
            ),
        )

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
        plan_text = _read_file(doc_path, language=ctx.language)
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
            guard=_acceptance_guard,
            confirm=ctx.confirm,
            source_refs=[
                {"label": "plan", "kind": "plan", "issue_id": plan_item_id}
                for _ in [0] if plan_item_id
            ],
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
    guard = _compose_guard(ctx.members, acceptance_doc=acceptance_doc)
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
        source_refs=(
            ([{"label": "plan", "kind": "plan", "issue_id": plan_item_id}]
             if plan_item_id else [])
            + ([{"label": "acceptance", "kind": "acceptance",
                 "issue_id": acceptance_item_id}]
               if acceptance_item_id else [])
        ),
        dag_key=make_dag_key(TaskKind.DECOMPOSE, scope=plan_id),
        review_acceptance_doc=acceptance_doc,
    )
    decompose_item_id = res["item_id"]
    manifest_text = _phase_text(res["delivery"], _MANIFEST_KEY)
    _write_if_missing(base_dir)

    # provenance:把塑造本 DAG 的源头 issue(设计/验收/拆解)记入 manifest meta,
    # 让 dag run 派发的 develop issue 也能溯源,防后续执行跑偏。
    source_issues = [r for r in [plan_item_id, acceptance_item_id, decompose_item_id] if r]
    # Reviewer 审查原始 decompose deliverable；最终文件使用同一个 Manifest
    # 执行模型做 canonical dump。默认值/空字段可省略，权威 acceptance 原文
    # 单独落盘并由 meta.acceptance_file 引用，不能形成第二份可漂移的正文。
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
    doc_path: Optional[str] = None,
    restart_active: bool = False,
    acceptance_issue_id: Optional[str] = None,
    decompose_issue_id: Optional[str] = None,
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
    if doc_path is None:
        ensure_files_clean(["AGENTS.md"], engine_type=store.config.engine_type)
    base_dir = CONFIG_DIR
    reviewers = [] if ctx.no_review else ctx.reviewers
    poll_cb = poll if poll is not None else ctx.poll()
    agents_snapshot = read_agents_snapshot() if doc_path is None else None

    plan_key = make_dag_key(TaskKind.PLAN, scope=plan_id_value)
    plan_item_id: Optional[str] = None
    project_rules_text: Optional[str] = None
    if doc_path is not None:
        if not name:
            raise ValidationError(ui(
                "plan resume --doc requires --name because no plan issue exists",
                "plan resume --doc 需要同时提供 --name,因为该流水线没有 plan issue"))
        resolved_name = name
        plan_text = _read_file(doc_path, language=ctx.language)
    else:
        plan_item = _require_by_dag_key(ctx, TaskKind.PLAN, plan_key)
        if not plan_item.project_rules:
            # 历史 plan 只有设计文档时不能绕过新双交付契约。复用原 issue
            # 回到 authoring,由 planner 补交两份文件并重新走确认/review。
            store.reset_review(plan_item.id)
            store.update_status(plan_item.id, WorkItemStatus.TODO)
            plan_item = store.get_work_item(plan_item.id)
        resolved_name = name or _name_from_plan_issue(plan_item)
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

    manifest_path = os.path.join(base_dir, f"{resolved_name}.yaml")
    acceptance_path = os.path.join(base_dir, f"{resolved_name}.acceptance.yaml")

    acceptance_text: Optional[str] = None
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None
    acceptance_item_id: Optional[str] = None
    if not ctx.no_acceptance:
        acceptance_key = make_dag_key(TaskKind.ACCEPTANCE, scope=plan_id_value)
        acceptance_item = (
            _exact_stage_item(
                ctx, TaskKind.ACCEPTANCE, acceptance_key,
                acceptance_issue_id)
            if acceptance_issue_id else
            _find_by_dag_key(ctx, TaskKind.ACCEPTANCE, acceptance_key)
        )
        acceptance_spec = AuthoringTaskSpec(
            kind=TaskKind.ACCEPTANCE,
            title=ui(
                f"{resolved_name} acceptance document", f"{resolved_name} 验收文档",
                language=ctx.language),
            dag_key=acceptance_key,
            assignee=ctx.planner,
            source_refs=(
                [{"label": "plan", "kind": "plan", "issue_id": plan_item_id}]
                if plan_item_id else []
            ),
            source_of_truth={"plan": plan_text},
        )
        if acceptance_item is not None and restart_active:
            restart_review = (
                acceptance_item.phase == TaskPhase.REVIEW
                and bool(acceptance_item.deliverable)
            )
            ctx.engine.runtime.cancel(acceptance_item.id)
            store.clear_assignment(acceptance_item.id)
            if restart_review:
                # Reviewer 卡住时保留已提交交付与 review phase，只重新派发 Reviewer。
                store.update_status(acceptance_item.id, WorkItemStatus.IN_REVIEW)
                acceptance_item = store.get_work_item(acceptance_item.id)
            else:
                store.reset_review(acceptance_item.id)
                store.update_status(acceptance_item.id, WorkItemStatus.TODO)
                acceptance_item = refresh_authoring_task(
                    ctx.engine, acceptance_item.id, acceptance_spec,
                    item_snapshot=acceptance_item)
        res = run_task(
            ctx.engine,
            TaskKind.ACCEPTANCE,
            {"title": acceptance_spec.title,
             "source_of_truth": acceptance_spec.source_of_truth},
            ctx.planner,
            reviewers=reviewers,
            max_revisions=ctx.max_revisions,
            poll=poll_cb,
            guard=_acceptance_guard,
            confirm=ctx.confirm,
            source_refs=acceptance_spec.source_refs,
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
    decompose_item = (
        _unstarted_stage_snapshot(
            ctx, TaskKind.DECOMPOSE, decompose_key,
            decompose_issue_id, ctx.orchestrator)
        if decompose_issue_id else
        _find_by_dag_key(ctx, TaskKind.DECOMPOSE, decompose_key)
    )
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
        guard=_compose_guard(ctx.members, acceptance_doc=acceptance_doc),
        source_refs=(
            ([{"label": "plan", "kind": "plan", "issue_id": plan_item_id}]
             if plan_item_id else [])
            + ([{"label": "acceptance", "kind": "acceptance",
                 "issue_id": acceptance_item_id}]
               if acceptance_item_id else [])
        ),
        dag_key=decompose_key,
        resume_item_id=decompose_item.id if decompose_item else None,
        review_acceptance_doc=acceptance_doc,
    )
    decompose_item_id = res["item_id"]
    manifest_text = _phase_text(res["delivery"], _MANIFEST_KEY)
    _write_if_missing(base_dir)

    source_issues = [r for r in [plan_item_id, acceptance_item_id, decompose_item_id] if r]
    # 与 plan_create 保持同一 review → execution handoff：同一执行模型做
    # canonical dump，acceptance 权威正文单独落盘并由 manifest meta 引用。
    manifest = loads_manifest(manifest_text)
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
    if project_rules_text is not None:
        write_project_rules(project_rules_text, agents_snapshot)
        output_paths.append("AGENTS.md")
    commit_files(
        output_paths, "chore(omac): sync plan outputs",
        engine_type=store.config.engine_type)
    _emit_plan_next_steps(manifest_path, acceptance_path, ctx.language)

    return 0

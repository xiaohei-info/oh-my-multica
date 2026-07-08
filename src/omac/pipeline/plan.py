"""plan create 流水线编排(§7.2):计划→验收文档→拆解,三阶段全部经 tasks.run_task。

双模式一条流水线:
  --doc 给了 → 跳过 planner 制定计划环节,直接进验收文档 + 拆解
  没给     → planner 从零制定计划,评审通过后继续全程内置 review 门(--no-review 一刀切跳过,--no-acceptance 跳过验收文档);
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
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..core import acceptance as acceptance_mod
from ..core.config import CONFIG_DIR, CONFIG_PATH
from ..core.gitsync import ensure_config_synced
from ..core.lint import lint
from ..core.manifest import Manifest, loads_manifest, save_manifest
from ..core.taskmeta import TaskKind, make_dag_key, make_plan_id
from ..engines.models import WorkItem
from ..errors import ValidationError
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

    def poll(self, interval: Optional[float] = None) -> Callable[[], None]:
        """构造一个阻塞轮询闭包(真实场景用,测试注入 no-op)。"""
        if interval is not None and interval <= 0:
            return lambda: None
        return lambda: time.sleep(interval if interval is not None else 0.1)


# run_task 交付落在 artifacts 里的文本 key
_PLAN_KEY = "plan"
_ACCEPTANCE_KEY = "acceptance"
_MANIFEST_KEY = "manifest"


def _phase_text(delivery: Dict[str, Any], key: str) -> str:
    """从 run_task 返回的 delivery 取某 key 的文本交付。"""
    value = delivery.get(key)
    if not value:
        raise ValidationError(
            f"阶段交付缺少 '{key}' —— 产出者未在 artifacts 中交付;请检查交付契约。")
    return str(value)


def _read_file(path: str) -> str:
    if not os.path.exists(path):
        raise ValidationError(f"文件不存在: {path} —— 请确认 --doc 路径")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_if_missing(dirpath: str) -> None:
    os.makedirs(dirpath or ".", exist_ok=True)


def _validate_acceptance(text: str) -> acceptance_mod.AcceptanceDoc:
    """按 core/acceptance schema 校验验收文档文本(结构不全即报错)。"""
    import yaml

    raw = yaml.safe_load(text)
    return acceptance_mod.load_acceptance_doc(raw)


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
            return [f"交付缺少 '{_MANIFEST_KEY}' —— orchestrator 未产出 manifest"]
        manifest = loads_manifest(text)
        return lint(manifest, members, acceptance=acceptance_doc)

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
    base_dir = CONFIG_DIR
    manifest_path = os.path.join(base_dir, f"{name}.yaml")
    acceptance_path = os.path.join(base_dir, f"{name}.acceptance.yaml")
    reviewers = [] if ctx.no_review else ctx.reviewers
    poll_cb = poll if poll is not None else ctx.poll()
    plan_id = make_plan_id()

    acceptance_text: Optional[str] = None
    # provenance:各阶段源头 issue,后续阶段带上引用防跑偏(--doc 时无 plan issue)。
    plan_item_id: Optional[str] = None
    acceptance_item_id: Optional[str] = None

    # ── phase 1:制定计划(跳过如果有 --doc) ──
    if doc_path is not None:
        plan_text = _read_file(doc_path)
    else:
        plan_payload: Dict[str, Any] = {"title": f"{name} 计划"}
        if goal_text:
            # 需求经 source_of_truth 通道进 planner 的 issue body(与 phase 2/3 同源),
            # 让 planner 据此制定计划,而非凭一个标题空想。
            plan_payload["source_of_truth"] = {"需求": goal_text}
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

    # ── phase 2:验收文档(跳过如果 --no-acceptance) ──
    acceptance_doc: Optional[acceptance_mod.AcceptanceDoc] = None
    if not ctx.no_acceptance:
        res = run_task(
            ctx.engine,
            TaskKind.ACCEPTANCE,
            {"title": f"{name} 验收文档",
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
    guard = _compose_guard(ctx.members, acceptance_doc=acceptance_doc)
    res = run_task(
        ctx.engine,
        TaskKind.DECOMPOSE,
        {"title": f"{name} 拆解",
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

    # provenance:把塑造本 DAG 的源头 issue(计划/验收/拆解)记入 manifest meta,
    # 让 dag run 派发的 develop issue 也能溯源,防后续执行跑偏。
    source_issues = [r for r in [plan_item_id, acceptance_item_id, decompose_item_id] if r]
    manifest = loads_manifest(manifest_text)
    manifest.meta["plan_id"] = plan_id
    manifest.meta.setdefault("name", name)
    if source_issues:
        manifest.meta["source_issues"] = source_issues
    save_manifest(manifest, manifest_path)

    return 0

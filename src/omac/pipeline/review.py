"""plan check 的 review-only 门(P3.4),与 P3.1 pipeline/tasks.py::run_task 互补。

语义差异(因此独立成原语,不是重复):
- run_task = 产出 → 评审→修订 循环(产出者Agent 被派活、产出、评审拒收时由同一
  产出者修订,有界 ≤ config.retry.review 轮);
- run_review = 对调用者「已拆好的 manifest」做单次评审门(lint 之后)。
  调用者自拆 manifest,不存在「产出者 Agent 修订」这一环 —— reviewer 拒收即
  NeedsDecision(exit 20),交调用者改文件后重跑 omac plan check。

两者共享 Store/Runtime 原语与 verdict 轮询结构(设计文档 §12.4 红线:本层只调
WorkItemStore/AgentRuntime 接口,绝不直接 shell 平台 CLI)。
"""
from __future__ import annotations

from typing import Callable

from ..engines.models import WorkItemStatus
from ..errors import NeedsDecision


def run_review(
    engine,
    workspace_id: str,
    title: str,
    body: str,
    reviewer: str,
    *,
    poll: Callable[[], None],
) -> dict:
    """对一件已存在的交付物做单次 reviewer 评审,返回 review_report(pass)。

    建 work item(issue body 由调用方按 §7.4 三段式模板渲染后传入)→ mark_in_review
    → assign reviewer → wake → 轮询 verdict。
    pass 时返回 review_report;reject 时抛 NeedsDecision(exit 20,结构化报告)。

    poll 由调用方注入(真实场景 time.sleep,测试用 no-op),与 run_task 同构。
    """
    if not reviewer:
        raise NeedsDecision(
            "manifest review 未指定 reviewer —— "
            "请在 config.roles.reviewers 中配置至少一名 reviewer 后重跑",
            report={"verdict": "no-reviewer"})

    store = engine.store
    runtime = engine.runtime

    if not store.check_member_exists(workspace_id, reviewer):
        raise NeedsDecision(
            f"reviewer '{reviewer}' 不在工作空间成员池中 —— "
            "`omac config set roles.reviewers` 配置后重跑",
            report={"reviewer": reviewer, "verdict": "reviewer-not-in-pool"})

    item = store.create_work_item(
        workspace_id, title, body, dag_key="plan-check", worker=reviewer)

    store.mark_in_review(item.id)
    store.assign_work_item(item.id, reviewer, "reviewer")
    runtime.wake(item.id, reviewer, "reviewer")

    while True:
        cur = store.get_work_item(item.id)
        if cur.review_verdict == "pass":
            return cur.review_report or {}
        if cur.review_verdict == "pass-with-nits":
            raise NeedsDecision(
                f"manifest review 返回 pass-with-nits,需要调用者确认是否接受建议项",
                report={
                    "item_id": cur.id,
                    "reviewer": reviewer,
                    "verdict": "pass-with-nits",
                    "nits": (cur.review_report or {}).get("nits", []),
                })
        if cur.review_verdict == "reject":
            raise NeedsDecision(
                f"manifest review 被 reviewer '{reviewer}' 拒绝",
                report={
                    "item_id": cur.id,
                    "reviewer": reviewer,
                    "verdict": "reject",
                    "comment": cur.review_comment,
                    "blockers": (cur.review_report or {}).get("blockers", []),
                })
        poll()

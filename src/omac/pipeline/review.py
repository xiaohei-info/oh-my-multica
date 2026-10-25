"""dag check 的 review-only 门(P3.4),与 P3.1 pipeline/tasks.py::run_task 互补。

语义差异(因此独立成原语,不是重复):
- run_task = 产出 → 评审→修订 循环(产出者Agent 被派活、产出、评审拒收时由同一
  产出者修订,有界 ≤ config.retry.review 轮);
- run_review = 对调用者「已拆好的 manifest」做单次评审门(lint 之后)。
  调用者自拆 manifest,不存在「产出者 Agent 修订」这一环 —— reviewer 拒收即
  NeedsDecision(exit 20),交调用者改文件后重跑 omac dag check。

两者共享 Store/Runtime 原语与 verdict 轮询结构(docs/design/README.md §12.4 红线:本层只调
WorkItemStore/AgentRuntime 接口,绝不直接 shell 平台 CLI)。
"""
from __future__ import annotations

from typing import Callable, Optional

from ..core.review_preflight import run_manifest_preflight
from ..core.taskmeta import TaskKind
from ..core.workitem import WorkItemStatus
from ..errors import NeedsDecision, ValidationError
from ..i18n import ui
from .dispatch import reviewer_dispatch_stopped


def _dispatch_blocked(store, item_id: str, current) -> NeedsDecision:
    """Block the review item (if not yet) and build the exit-20 decision for a stopped dispatch."""
    if current.status != WorkItemStatus.BLOCKED:
        store.update_status(item_id, WorkItemStatus.BLOCKED)
    return NeedsDecision(
        ui(
            "Reviewer dispatch is blocked by the existing decision or platform status.",
            "reviewer 派发被已有 decision 或平台 blocked 状态阻塞。",
        ),
        report={
            "item_id": item_id,
            "reason_code": "reviewer-dispatch-blocked",
            "decision_required": current.decision_required,
        },
    )


def _check_reviewer(store, workspace_id: str, reviewer: str, deliverable: Optional[str]) -> None:
    if not reviewer:
        raise NeedsDecision(
            ui(
                "Manifest review has no reviewer. Configure at least one agent in "
                "config.roles.reviewers and rerun.",
                "manifest review 未指定 reviewer —— "
                "请在 config.roles.reviewers 中配置至少一名 reviewer 后重跑"),
            report={"verdict": "no-reviewer"})
    if deliverable is not None:
        preflight_errors = run_manifest_preflight(deliverable)
        if preflight_errors:
            raise ValidationError(
                "Manifest review preflight failed:\n  - "
                + "\n  - ".join(preflight_errors)
            )
    if not store.check_member_exists(workspace_id, reviewer):
        raise NeedsDecision(
            ui(
                f"Reviewer '{reviewer}' is not in the workspace member pool. "
                "Update roles.reviewers with `omac config set` and rerun.",
                f"reviewer '{reviewer}' 不在工作空间成员池中 —— "
                "`omac config set roles.reviewers` 配置后重跑"),
            report={"reviewer": reviewer, "verdict": "reviewer-not-in-pool"})


def _start_review(engine, workspace_id: str, title: str, body, reviewer: str,
                  deliverable: Optional[str]) -> str:
    """Create the review issue, mark it in review and dispatch the reviewer; returns the item id."""
    store, runtime = engine.store, engine.runtime
    item = store.create_work_item(
        workspace_id,
        title,
        title if callable(body) else body,
        dag_key="plan-check",
        worker=reviewer,
        kind=TaskKind.DECOMPOSE,
    )
    metadata = {}
    if callable(body):
        metadata["description"] = body(item.id)
    if deliverable is not None:
        metadata["deliverable"] = deliverable
    if metadata:
        store.update_work_item_metadata(item.id, **metadata)

    current = store.observe_work_item_control(item.id).work_item
    if reviewer_dispatch_stopped(current):
        raise _dispatch_blocked(store, item.id, current)
    store.mark_in_review(item.id)
    current = store.observe_work_item_control(item.id).work_item
    if reviewer_dispatch_stopped(current):
        raise _dispatch_blocked(store, item.id, current)
    if not runtime.dispatch_reviewer(store, item.id, reviewer):
        raise _dispatch_blocked(
            store, item.id, store.observe_work_item_control(item.id).work_item)
    return item.id


def _verdict_or_none(cur, reviewer: str) -> Optional[dict]:
    """Return the review report on pass; raise NeedsDecision on nits/reject; None while undecided."""
    if cur.review_verdict == "pass":
        return cur.review_report or {}
    if cur.review_verdict == "pass-with-nits":
        raise NeedsDecision(
            ui(
                "Manifest review returned pass-with-nits; the caller must decide whether to accept them.",
                "manifest review 返回 pass-with-nits,需要调用者确认是否接受建议项"),
            report={
                "item_id": cur.id,
                "reviewer": reviewer,
                "verdict": "pass-with-nits",
                "nits": (cur.review_report or {}).get("nits", []),
            })
    if cur.review_verdict == "reject":
        raise NeedsDecision(
            ui(
                f"Manifest review was rejected by reviewer '{reviewer}'.",
                f"manifest review 被 reviewer '{reviewer}' 拒绝"),
            report={
                "item_id": cur.id,
                "reviewer": reviewer,
                "verdict": "reject",
                "comment": cur.review_comment,
                "blockers": (cur.review_report or {}).get("blockers", []),
            })
    return None


def _raise_if_run_ended_silently(store, runtime, cur, reviewer: str) -> None:
    terminal_runs = [
        run for run in runtime.list_runs(cur.id)
        if run.kind == "direct" and run.terminal
    ]
    if not terminal_runs:
        return
    if cur.status != WorkItemStatus.BLOCKED:
        store.update_status(cur.id, WorkItemStatus.BLOCKED)
    raise NeedsDecision(
        ui(
            "Reviewer Agent Run ended without submitting a verdict; inspect "
            "the run and retry the same review issue.",
            "Reviewer Agent Run 已结束但未提交 verdict；请检查该 Run 后重试同一 review issue。",
        ),
        report={
            "item_id": cur.id,
            "reviewer": reviewer,
            "reason_code": "reviewer-completed-without-verdict",
            "outcome": "unknown_partial",
            "run_ids": [run.id for run in terminal_runs],
        },
    )


def run_review(
    engine,
    workspace_id: str,
    title: str,
    body: str | Callable[[str], str],
    reviewer: str,
    *,
    deliverable: Optional[str] = None,
    poll: Callable[[], None],
) -> dict:
    """对一件已存在的交付物做单次 reviewer 评审,返回 review_report(pass)。

    建 work item(issue body 由调用方按 Human-first 模板渲染后传入)→ mark_in_review
    → assign reviewer → wake → 轮询 verdict。
    pass 时返回 review_report;reject 时抛 NeedsDecision(exit 20,结构化报告)。

    poll 由调用方注入(真实场景 time.sleep,测试用 no-op),与 run_task 同构。
    """
    store, runtime = engine.store, engine.runtime
    _check_reviewer(store, workspace_id, reviewer, deliverable)
    item_id = _start_review(engine, workspace_id, title, body, reviewer, deliverable)
    while True:
        cur = store.get_work_item(item_id)
        report = _verdict_or_none(cur, reviewer)
        if report is not None:
            return report
        _raise_if_run_ended_silently(store, runtime, cur, reviewer)
        poll()

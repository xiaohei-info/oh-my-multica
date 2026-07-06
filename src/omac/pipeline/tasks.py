"""pipeline/tasks.py —— 派任务→等终态→取交付→有界修订循环(P3.1)。

plan 流水线(§7.2)与总控验收(§7.6)共用的确定性原语:建 issue → assign+wake
→ 轮询终态 → 取交付;reviewers 非空时进入 review 阶段(同一 issue 转派 reviewer),
reject 意见落 issue 后转回产出者修订,有界(默认 ≤3 轮),耗尽 → NeedsDecision。

issue body 取自 dispatch.render_issue_body(三段式 §7.4 模板),与 work show/submit
同源,不自行拼接。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from ..core.manifest import Contract, _load_contract
from ..core.taskmeta import TaskKind
from ..engines.models import WorkItem, WorkItemStatus
from ..errors import NeedsDecision
from .dispatch import render_issue_body


# 产出阶段终态(不含 IN_REVIEW:review 分支由本原语接管)
_AUTHORING_TERMINAL = (WorkItemStatus.DONE, WorkItemStatus.FAILED)


def _payload_contract(raw: Any) -> Any:
    """把 payload["contract"] 归一化为 Contract / None。"""
    if raw is None:
        return None
    if isinstance(raw, Contract):
        return raw
    if isinstance(raw, dict):
        return _load_contract(raw)
    return raw


def _pick_reviewer(reviewers: List[str], producer: str, round_index: int) -> str:
    """reviewers 池轮转,优先非产出者;池内仅产出者时回退自审。

    角色可自由指定(不强制 reviewer ≠ producer):有非产出者时优先选它以保留
    评审独立性;池里只剩产出者时回退到产出者自审(自审只是自检,真正的把关交给
    human gate)。不再报错。
    """
    candidates = [r for r in reviewers if r != producer] or list(reviewers)
    return candidates[round_index % len(candidates)]


def _poll_until(
    store,
    item_id: str,
    predicate: Callable[[WorkItem], bool],
    poll: Callable[[], None],
) -> WorkItem:
    """轮询 work item 直到 predicate 为真。

    poll 由调用方提供(如 time.sleep / asyncio 协作点),是本原语唯一的等待钩子:
    调用方需保证经若干次 poll 后 predicate 能收敛,否则本函数不会返回。
    """
    while True:
        item = store.get_work_item(item_id)
        if predicate(item):
            return item
        poll()


def _render_source_of_truth(source_of_truth: dict) -> str:
    """把上游产物(dict[标签 -> 文本])渲染成 issue body 的一个只读上下文段。

    与 contract.source_of_truth(引用路径列表)不同,这里是上游阶段产出的
    完整文本正文,直接落到 issue description,供真实 planner/orchestrator 在
    `omac work show`/issue body 中读取而不依赖产物注入。
    """
    sections = ["## 上游产物(只读上下文)"]
    for label, text in source_of_truth.items():
        if not text:
            continue
        sections.append(f"### {label}\n```\n{text.rstrip()}\n```")
    return "\n\n".join(sections)


def run_task(
    engine,
    kind: TaskKind,
    payload: Dict[str, Any],
    assignee: str,
    *,
    reviewers: Optional[List[str]] = None,
    max_revisions: int = 3,
    poll: Callable[[], None],
    guard: Optional[Callable[[WorkItem], List[str]]] = None,
) -> Dict[str, Any]:
    """派任务→等终态→取交付→有界修订循环。

    1. 建 issue(issue body 用 dispatch.render_issue_body 三段式模板),assign+wake;
    2. 轮询产出终态 → 取交付物(artifacts);
    3. reviewers 非空时进入 review 阶段:同一 issue 转派 reviewer → verdict;
       reject → 意见落 issue、reset_review 后转回产出者(计数) → 重取交付;
       耗尽 → NeedsDecision(报告含轮次与最后意见)。
    """
    store = engine.store
    runtime = engine.runtime
    workspace_id = store.config.workspace_id

    title = payload.get("title") or f"{kind.value} task"
    contract = _payload_contract(payload.get("contract"))
    source_of_truth = payload.get("source_of_truth") or {}

    # ── 建 issue(先占位 id,再用真实 id 渲染 body) ──
    item = store.create_work_item(
        workspace_id, title, "", dag_key=kind.value, worker=assignee, kind=kind,
    )
    item_id = item.id
    # body 里 reviewer 留 None:reviewer 在 review 阶段按轮次由 _pick_reviewer 动态选取,
    # 创建时没有「当前 reviewer」的概念,不写死池内第一位以免误导。
    body_node = SimpleNamespace(title=title, reviewer=None, id=item_id)
    body = render_issue_body(body_node, contract, kind, item_id)
    if source_of_truth:
        body = body + "\n\n" + _render_source_of_truth(source_of_truth)
    store.update_work_item_metadata(item_id, description=body)

    def _produce(hint: Optional[List[str]] = None) -> WorkItem:
        store.mark_in_progress(item_id)
        store.assign_work_item(item_id, assignee, "worker")
        runtime.wake(item_id, assignee, "worker")
        produced = _poll_until(
            store, item_id, lambda i: i.status in _AUTHORING_TERMINAL, poll)
        if produced.status == WorkItemStatus.FAILED:
            raise NeedsDecision(
                f"{kind.value} 产出阶段失败(item {item_id})",
                report={"item_id": item_id, "kind": kind.value, "rounds": 0,
                        "last_opinion": "producer failed"})
        if hint:
            store.add_comment(
                item_id,
                "产出修订(错误原文回贴):\n" + "\n".join(f"- {e}" for e in hint))
        return produced

    delivered = _produce()
    delivery = delivered.artifacts

    # 机器门(零 token):通过即止,耗尽转 NeedsDecision
    if guard is not None:
        for guard_round in range(1, max_revisions + 1):
            guard_errors: List[str] = guard(delivered)
            if not guard_errors:
                break
            store.reset_review(item_id)
            delivered = _produce(hint=guard_errors)
            delivery = delivered.artifacts
        else:
            raise NeedsDecision(
                f"{kind.value} 任务经 {max_revisions} 轮 machine-gate 仍未通过",
                report={"item_id": item_id, "kind": kind.value,
                        "rounds": max_revisions, "phase": "guard",
                        "last_opinion": "\n".join(guard_errors)})

    if not reviewers:
        return {"item_id": item_id, "delivery": delivery,
                "rounds": 0, "verdict": "pass", "kind": kind.value}

    # ── review 阶段(有界修订循环) ──
    last_opinion: Optional[str] = None
    for round_index in range(1, max_revisions + 1):
        reviewer = _pick_reviewer(reviewers, assignee, round_index - 1)

        store.mark_in_review(item_id)
        store.assign_work_item(item_id, reviewer, "reviewer")
        runtime.wake(item_id, reviewer, "reviewer")
        reviewed = _poll_until(
            store, item_id, lambda i: i.review_verdict is not None, poll)

        verdict = reviewed.review_verdict
        if verdict == "pass":
            store.mark_done(item_id)
            return {"item_id": item_id, "delivery": delivery,
                    "rounds": round_index, "verdict": "pass", "kind": kind.value}

        # reject: 意见落 issue, reset_review 清旧判定, 转回产出者修订
        last_opinion = reviewed.review_comment
        store.add_comment(
            item_id,
            f"reviewer {reviewer} 第 {round_index} 轮 reject: {last_opinion}")
        store.reset_review(item_id)
        delivered = _produce()
        delivery = delivered.artifacts

    raise NeedsDecision(
        f"{kind.value} 任务在 {max_revisions} 轮修订后仍未通过评审",
        report={"item_id": item_id, "kind": kind.value,
                "rounds": max_revisions, "last_opinion": last_opinion})

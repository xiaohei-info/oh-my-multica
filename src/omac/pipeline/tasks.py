"""pipeline/tasks.py —— 派任务→等终态→取交付→有界修订循环(P3.1)。

plan 流水线(§7.2)与总控验收(§7.6)共用的确定性原语:建 issue → assign+wake
→ 轮询终态 → 取交付;reviewers 非空时进入 review 阶段(同一 issue 转派 reviewer),
reject 只清旧评审判定并转回产出者修订,有界(默认 ≤3 轮),耗尽 → NeedsDecision。

issue body 取自 dispatch.render_issue_body(Human-first 模板),与 work show/submit
同源,不自行拼接。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from ..core import logsetup
from ..core.manifest import Contract, _load_contract
from ..core.taskmeta import DELIVERY_CONTENT_KEY, TaskKind, TaskPhase, make_dag_key
from ..engines.models import WorkItem, WorkItemStatus
from ..errors import NeedsDecision
from ..i18n import current_language, ui
from .dispatch import normalize_source_refs, render_issue_body

log = logsetup.get_logger(__name__)

_REVIEW_VERDICTS = {"pass", "pass-with-nits", "reject"}


@dataclass
class AuthoringTaskSpec:
    """创建 authoring issue 所需的稳定输入。"""

    kind: TaskKind
    title: str
    dag_key: str
    assignee: str
    description: str = ""
    contract: Any = None
    source_refs: List[Any] = field(default_factory=list)
    source_of_truth: Dict[str, str] = field(default_factory=dict)


def _markdown_fence_for(text: str) -> str:
    longest = max((len(m.group(0)) for m in re.finditer(r"`{3,}", text)), default=3)
    return "`" * max(4, longest + 1)


def _produced(item: WorkItem) -> bool:
    """产出阶段收敛判据:产出者交付后 issue 进入 REVIEW 阶段(plan/acceptance/
    decompose 经 work submit → IN_REVIEW+phase=REVIEW+deliverable),或直接终态
    (DONE/FAILED)。评审往返由本原语接管,故 IN_REVIEW 本身不算「未完」。"""
    return item.phase == TaskPhase.REVIEW or item.status in (
        WorkItemStatus.DONE, WorkItemStatus.FAILED)


def _delivery_of(kind: TaskKind, item: WorkItem) -> Dict[str, Any]:
    """把产出者交付正文(item.deliverable)按 kind 包成 delivery dict。

    交付正文落 issue metadata 的 deliverable 字段(与真实 work submit 同源),
    而非 artifacts —— 后者是 develop 节点的 pr_url 证据,两条通道不混用。
    """
    key = DELIVERY_CONTENT_KEY.get(kind, kind.value)
    return {key: item.deliverable}


def _has_review_verdict(item: WorkItem) -> bool:
    return item.review_verdict in _REVIEW_VERDICTS


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


def _render_source_of_truth(source_of_truth: dict, language: str | None = None) -> str:
    """把上游产物(dict[标签 -> 文本])渲染成 issue body 的只读上下文段。

    上游产物本身通常是 Markdown。不要再外包一层代码块,否则平台 Markdown
    对四反引号支持不完整时会破坏渲染,也会让人工审阅很难读。
    """
    language = language or current_language()
    sections = [f"## {ui('Upstream artifacts (read-only context)', '上游产物(只读上下文)', language=language)}"]
    for label, text in source_of_truth.items():
        if not text:
            continue
        content = text.rstrip()
        sections.append(
            f"### {label}\n\n"
            "<details>\n"
            f"<summary>{ui(f'View upstream artifact: {label}', f'查看 {label} 上游产物', language=language)}</summary>\n\n"
            f"{content}\n\n"
            "</details>"
        )
    return "\n\n".join(sections)


def _engine_env(engine) -> Dict[str, str]:
    config = engine.store.config
    env = {
        "OMAC_ENGINE": config.engine_type,
        "OMAC_WORKSPACE_ID": config.workspace_id,
    }
    if config.project_id:
        env["OMAC_PROJECT_ID"] = config.project_id
    workspace_slug = (config.extra or {}).get("workspace_slug") or (config.extra or {}).get("OMAC_WORKSPACE_SLUG")
    if workspace_slug:
        env["OMAC_WORKSPACE_SLUG"] = workspace_slug
    return env


def create_authoring_task(engine, spec: AuthoringTaskSpec) -> WorkItem:
    """创建并填充一个可直接执行的 authoring issue。"""
    store = engine.store
    item = store.create_work_item(
        workspace_id=store.config.workspace_id,
        title=spec.title,
        description=spec.title,
        dag_key=spec.dag_key,
        worker=spec.assignee,
        kind=spec.kind,
    )
    env = _engine_env(engine)
    refs = normalize_source_refs(spec.source_refs, engine_env=env)
    body_node = SimpleNamespace(
        title=spec.title,
        description=spec.description,
        reviewer=None,
        id=item.id,
    )
    body = render_issue_body(
        body_node,
        spec.contract,
        spec.kind,
        item.id,
        source_refs=refs,
        engine_env=env,
        issue_key=getattr(item, "identifier", None),
        language=current_language(),
    )
    if spec.source_of_truth:
        body += "\n\n" + _render_source_of_truth(
            spec.source_of_truth, current_language())
    if spec.contract is not None:
        store.set_node_contract(item.id, spec.contract)
    return store.update_work_item_metadata(
        item.id,
        description=body,
        source_refs=refs,
    )


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
    confirm: bool = False,
    source_refs: Optional[List[Any]] = None,
    dag_key: Optional[str] = None,
    resume_item_id: Optional[str] = None,
) -> Dict[str, Any]:
    """派任务→等终态→取交付→有界修订循环。

    1. 建 issue(issue body 用 dispatch.render_issue_body Human-first 模板),assign+wake;
    2. 轮询产出终态 → 取交付物(artifacts);
    3. reviewers 非空时进入 review 阶段:同一 issue 转派 reviewer → verdict;
       reject → reset_review 后转回产出者(计数),上轮评审由 work show 从 metadata 暴露 → 重取交付;
       耗尽 → NeedsDecision(报告含轮次与最后意见)。
    """
    store = engine.store
    runtime = engine.runtime
    workspace_id = store.config.workspace_id

    title = payload.get("title") or f"{kind.value} task"
    task_key = dag_key or make_dag_key(kind, title=title, unique=True)
    contract = _payload_contract(payload.get("contract"))
    source_of_truth = payload.get("source_of_truth") or {}

    if resume_item_id is not None:
        item = store.get_work_item(resume_item_id)
        item_id = item.id
    else:
        item = create_authoring_task(
            engine,
            AuthoringTaskSpec(
                kind=kind,
                title=title,
                dag_key=task_key,
                assignee=assignee,
                description=payload.get("description") or "",
                contract=contract,
                source_refs=list(source_refs or []),
                source_of_truth=source_of_truth,
            ),
        )
        item_id = item.id

    def _produce(hint: Optional[List[str]] = None) -> WorkItem:
        current = store.get_work_item(item_id)
        if hint is None and _produced(current) and current.status != WorkItemStatus.FAILED:
            return current
        store.mark_in_progress(item_id)
        store.assign_work_item(item_id, assignee, "worker")
        runtime.wake(item_id, assignee, "worker")
        produced = _poll_until(store, item_id, _produced, poll)
        if produced.status == WorkItemStatus.FAILED:
            log.info(logsetup.EVT_NODE_FAILED, kind=kind.value, id=item_id,
                     reason="producer failed")
            raise NeedsDecision(
                ui(
                    f"{kind.value} authoring failed (item {item_id})",
                    f"{kind.value} 产出阶段失败(item {item_id})"),
                report={"item_id": item_id, "kind": kind.value, "rounds": 0,
                        "last_opinion": "producer failed"})
        if hint:
            store.add_comment(item_id, ui(
                "Authoring revision (original errors):\n" + "\n".join(f"- {e}" for e in hint),
                "产出修订(错误原文回贴):\n" + "\n".join(f"- {e}" for e in hint)))
        return produced

    log.info(logsetup.EVT_DISPATCH, kind=kind.value, id=item_id, worker=assignee)
    delivered = _produce()
    delivery = _delivery_of(kind, delivered)

    if delivered.status == WorkItemStatus.DONE and delivered.review_verdict == "pass":
        log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
        return {"item_id": item_id, "delivery": delivery,
                "rounds": 0, "verdict": "pass", "kind": kind.value}

    # 机器门(零 token):通过即止,耗尽转 NeedsDecision
    if guard is not None:
        for guard_round in range(1, max_revisions + 1):
            guard_errors: List[str] = guard(delivered)
            if not guard_errors:
                break
            log.info(logsetup.EVT_REVISION, kind=kind.value, id=item_id,
                     gate="guard", round=guard_round, max=max_revisions)
            store.reset_review(item_id)
            delivered = _produce(hint=guard_errors)
            delivery = _delivery_of(kind, delivered)
        else:
            log.info(logsetup.EVT_NEEDS_DECISION, kind=kind.value, id=item_id,
                     gate="guard", rounds=max_revisions)
            raise NeedsDecision(
                ui(
                    f"{kind.value} did not pass the machine gate after {max_revisions} revisions",
                    f"{kind.value} 任务经 {max_revisions} 轮 machine-gate 仍未通过"),
                report={"item_id": item_id, "kind": kind.value,
                        "rounds": max_revisions, "phase": "guard",
                        "last_opinion": "\n".join(guard_errors)})

    # ── 人机门(human in the loop,可选) ──
    # 通过标准:人工把 issue 流转到 DONE(易于自动化识别),或 `omac plan confirm`。
    # 识别到 DONE 后:有 reviewer 则翻回 IN_REVIEW 继续评审;无 reviewer 则人工确认即终态。
    should_emit_human_gate = (
        confirm
        and not delivered.reviewer
        and delivered.review_verdict is None
    )
    waiting_for_human = (
        should_emit_human_gate
        and delivered.status != WorkItemStatus.DONE
    )
    if should_emit_human_gate:
        # 发事件,否则操作者看着像卡死;mock 自动确认过快时也保留该轨迹。
        log.info(logsetup.EVT_HUMAN_GATE_WAIT, kind=kind.value, id=item_id)
    if waiting_for_human:
        # 干等人把 issue 挪到 DONE。
        delivered = _poll_until(
            store, item_id, lambda i: i.status == WorkItemStatus.DONE, poll)
        delivery = _delivery_of(kind, delivered)

    if confirm and delivered.status == WorkItemStatus.DONE and delivered.review_verdict is None:
        if not reviewers:
            log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
            return {"item_id": item_id, "delivery": delivery,
                    "rounds": 0, "verdict": "pass", "kind": kind.value}
        store.mark_in_review(item_id)

    if not reviewers:
        # 产出者交付后停在 IN_REVIEW(work submit 语义);无 reviewer 时由本原语收口终态。
        store.mark_done(item_id)
        log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
        return {"item_id": item_id, "delivery": delivery,
                "rounds": 0, "verdict": "pass", "kind": kind.value}

    # ── review 阶段(有界修订循环) ──
    last_opinion: Optional[str] = None
    for round_index in range(1, max_revisions + 1):
        reviewer = _pick_reviewer(reviewers, assignee, round_index - 1)

        store.mark_in_review(item_id)
        store.assign_work_item(item_id, reviewer, "reviewer")
        log.info(logsetup.EVT_REVIEW_DISPATCH, kind=kind.value, id=item_id,
                 reviewer=reviewer)
        runtime.wake(item_id, reviewer, "reviewer")
        reviewed = _poll_until(
            store, item_id, _has_review_verdict, poll)

        verdict = reviewed.review_verdict
        log.info(logsetup.EVT_VERDICT, kind=kind.value, id=item_id,
                 verdict=verdict, round=round_index)
        if verdict == "pass":
            store.mark_done(item_id)
            log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
            return {"item_id": item_id, "delivery": delivery,
                    "rounds": round_index, "verdict": "pass", "kind": kind.value}

        if verdict == "pass-with-nits":
            log.info(logsetup.EVT_REVISION, kind=kind.value, id=item_id,
                     gate="review-nits", round=round_index, max=max_revisions)
            store.update_work_item_metadata(
                item_id, phase=TaskPhase.AUTHORING,
                review_comment="")
            delivered = _produce()
            delivery = _delivery_of(kind, delivered)
            store.mark_done(item_id)
            log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
            return {"item_id": item_id, "delivery": delivery,
                    "rounds": round_index, "verdict": "pass-with-nits", "kind": kind.value}

        # reject:评审 report 已在 metadata,reset_review 只清当前判定并转回产出者。
        # 返工上下文由下一轮 agent 通过 work show 读取,不写评论以免触发额外 run。
        last_opinion = reviewed.review_comment
        log.info(logsetup.EVT_REVISION, kind=kind.value, id=item_id,
                 gate="review", round=round_index, max=max_revisions)
        store.reset_review(item_id)
        delivered = _produce()
        delivery = _delivery_of(kind, delivered)

    log.info(logsetup.EVT_NEEDS_DECISION, kind=kind.value, id=item_id,
             gate="review", rounds=max_revisions)
    raise NeedsDecision(
        ui(
            f"{kind.value} did not pass review after {max_revisions} revisions",
            f"{kind.value} 任务在 {max_revisions} 轮修订后仍未通过评审"),
        report={"item_id": item_id, "kind": kind.value,
                "rounds": max_revisions, "last_opinion": last_opinion})

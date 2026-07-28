"""pipeline/tasks.py —— 派任务→等终态→取交付→有界修订循环(P3.1)。

plan 流水线(§7.2)与总控验收(§7.6)共用的确定性原语:建 issue → assign+wake
→ 轮询终态 → 取交付;reviewers 非空时进入 review 阶段(同一 issue 转派 reviewer),
reject 只清旧评审判定并转回产出者修订,有界(默认 ≤3 轮),耗尽 → NeedsDecision。

issue body 取自 dispatch.render_issue_body(Human-first 模板),与 work show/submit
同源,不自行拼接。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from ..core import logsetup
from ..core.evidence import validate_review_evidence
from ..core.manifest import Contract, _dump_contract, _load_contract
from ..core.restart import RestartState, deliverable_identity
from ..core.machine_feedback import (
    build_machine_feedback, machine_feedback_summary,
)
from ..core.review_convergence import build_review_obligations
from ..core.review_continuation import authorized_review_limit
from ..core.review_preflight import run_review_preflight
from ..core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, DELIVERY_CONTENT_KEY, TaskKind, TaskPhase,
    make_dag_key,
)
from ..engines.models import WorkItem, WorkItemStatus
from ..errors import NeedsDecision, PlatformError, ValidationError
from ..i18n import current_language, ui
from .dispatch import normalize_source_refs, render_issue_body

log = logsetup.get_logger(__name__)

_REVIEW_VERDICTS = {"pass", "pass-with-nits", "reject"}
_MAX_INLINE_SOURCE_BYTES = 64 * 1024
_RUN_VISIBILITY_MAX_POLLS = 3


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
    amendment_attempt: Optional[Dict[str, Any]] = None


def _restart_request_digest(spec: AuthoringTaskSpec) -> str:
    contract = spec.contract
    if isinstance(contract, Contract):
        contract = _dump_contract(contract)
    payload = {
        "kind": spec.kind.value,
        "title": spec.title,
        "dag_key": spec.dag_key,
        "assignee": spec.assignee,
        "description": spec.description,
        "contract": contract,
        "source_refs": spec.source_refs,
        "source_of_truth": spec.source_of_truth,
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _markdown_fence_for(text: str) -> str:
    longest = max((len(m.group(0)) for m in re.finditer(r"`{3,}", text)), default=3)
    return "`" * max(4, longest + 1)


def _produced(item: WorkItem) -> bool:
    """产出阶段收敛判据:产出者交付后 issue 进入 REVIEW 阶段(plan/acceptance/
    decompose 经 work submit → IN_REVIEW+phase=REVIEW+deliverable),或直接终态
    (DONE/FAILED)。评审往返由本原语接管,故 IN_REVIEW 本身不算「未完」。"""
    staged = (
        item.phase == TaskPhase.REVIEW
        and (
            item.status == WorkItemStatus.IN_REVIEW
            or _has_review_verdict(item)
        )
    ) or (
        item.phase == TaskPhase.CONFIRMATION
        and item.status == WorkItemStatus.IN_REVIEW
    )
    return staged or item.status in (
        WorkItemStatus.DONE, WorkItemStatus.FAILED, WorkItemStatus.BLOCKED)


def _delivery_of(kind: TaskKind, item: WorkItem) -> Dict[str, Any]:
    """把产出者交付正文(item.deliverable)按 kind 包成 delivery dict。

    交付正文落 issue metadata 的 deliverable 字段(与真实 work submit 同源),
    而非 artifacts —— 后者是 develop 节点的 pr_url 证据,两条通道不混用。
    """
    key = DELIVERY_CONTENT_KEY.get(kind, kind.value)
    delivery = {key: item.deliverable}
    if kind == TaskKind.PLAN and item.project_rules is not None:
        delivery["project_rules"] = item.project_rules
    return delivery


def _has_review_verdict(item: WorkItem) -> bool:
    return getattr(item, "review_verdict", None) in _REVIEW_VERDICTS


def _review_evidence_errors(contract: Any, item: WorkItem) -> List[str]:
    """重新验证持久化 Reviewer 证据，避免恢复时信任旧 CLI 写入的脏状态。"""
    return validate_review_evidence(
        SimpleNamespace(contract=contract), item)


def _restart_invalid_review(
    store,
    item_id: str,
    subject_digest: str,
    errors: List[str],
) -> WorkItem:
    """清除无效 verdict/report，并在同一评审对象上重新派审。"""
    store.reset_review(item_id)
    store.prepare_review_cycle(item_id, subject_digest)
    feedback = build_machine_feedback("review-evidence", errors)
    return store.update_work_item_metadata(
        item_id,
        machine_feedback=feedback,
        review_comment=machine_feedback_summary(item_id, feedback),
    )


def _review_opinion(item: WorkItem) -> Optional[str]:
    """从稳定评审字段提取可用于 NeedsDecision 的最后意见。"""
    if item.review_comment:
        return item.review_comment
    report = item.review_report
    if not isinstance(report, dict):
        return None
    blockers = report.get("blockers")
    if isinstance(blockers, list) and blockers:
        return "\n".join(str(blocker) for blocker in blockers)
    return None


def _review_continuation_action(item: WorkItem) -> Optional[str]:
    if item.kind not in {
        TaskKind.PLAN, TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE,
    }:
        return None
    return f"omac plan continue-review --dag-key {item.dag_key}"


def _rejected_verdict_was_counted(kind: TaskKind, item: WorkItem) -> bool:
    round_index = max(0, item.bounces.review)
    return (
        item.review_verdict == "reject"
        and round_index > 0
        and item.review_subject_digest
        == _review_subject_digest(kind, item, round_index)
    )


def _review_exhausted_error(
    kind: TaskKind,
    item: WorkItem,
    rounds: int,
    last_opinion: Optional[str],
    *,
    gate: str = "review",
) -> NeedsDecision:
    action = _review_continuation_action(item)
    instruction = (
        f" Run `{action}` to authorize exactly one additional review round, "
        "then run plan resume."
        if action else ""
    )
    instruction_zh = (
        f" 运行 `{action}` 明确授权额外一轮 review，然后再运行 plan resume。"
        if action else ""
    )
    report = {
        "item_id": item.id,
        "kind": kind.value,
        "phase": TaskPhase.REVIEW.value,
        "gate": gate,
        "rounds": rounds,
        "last_opinion": last_opinion,
        "resume_issue_id": item.id,
    }
    if action:
        report["next_action"] = action
    return NeedsDecision(
        ui(
            f"{kind.value} did not pass review after {rounds} revisions."
            f"{instruction}",
            f"{kind.value} 任务在 {rounds} 轮修订后仍未通过评审。"
            f"{instruction_zh}"),
        report=report,
    )


def _budget_exhausted_decision(
    kind: TaskKind,
    item: WorkItem,
    *,
    gate: str,
    rounds: int,
) -> Dict[str, Any]:
    """Build the bounded platform projection for an exhausted task budget."""
    decision: Dict[str, Any] = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": f"{gate}-budget-exhausted",
        "kind": kind.value,
        "phase": TaskPhase.REVIEW.value,
        "gate": gate,
        "rounds": rounds,
        "resume_issue_id": item.id,
    }
    for key in ("machine_feedback_ref", "review_report_ref", "review_ledger_ref"):
        value = getattr(item, key, None)
        if isinstance(value, dict) and value:
            decision[key] = value
    action = _review_continuation_action(item)
    if action:
        decision["next_action"] = action
    return decision


def _review_subject_digest(
    kind: TaskKind, item: WorkItem, round_index: int,
) -> str:
    """把 verdict 绑定到当前交付与评审轮次，避免跨交付复用旧判定。"""
    digest = hashlib.sha256()
    for value in (
        kind.value,
        str(round_index),
        item.deliverable or "",
        item.project_rules or "",
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


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


def _source_output_path(label: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", label).strip("-") or "source"
    suffix = ".yaml" if label in {"acceptance", "manifest"} else ".md"
    return f"/tmp/omac-{safe}{suffix}"


def _externalize_large_sources(
    source_of_truth: Dict[str, str],
    refs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把大型正文绑定到已有上游 issue，而不是复制进下游 description。"""
    enriched = [dict(ref) for ref in refs]
    for label, content in source_of_truth.items():
        size = len((content or "").encode("utf-8"))
        if size <= _MAX_INLINE_SOURCE_BYTES:
            continue
        candidates = [ref for ref in enriched if ref.get("label") == label]
        if not candidates and len(source_of_truth) == 1 and len(enriched) == 1:
            candidates = enriched
            candidates[0].setdefault("label", label)
        if not candidates:
            raise ValidationError(ui(
                f"Large upstream artifact '{label}' ({size} bytes) has no source issue reference. "
                "Create or preserve the upstream issue attachment before dispatching the next stage.",
                f"大型上游产物 '{label}'（{size} 字节）没有 source issue 引用。"
                "派发下一阶段前必须先创建或保留上游 issue 附件。",
            ))
        ref = candidates[0]
        ref.update({
            "label": label,
            "delivery_key": label,
            "content_externalized": True,
            "content_bytes": size,
            "content_sha256": hashlib.sha256(
                content.encode("utf-8")).hexdigest(),
        })
    return enriched


def _render_source_of_truth(
    source_of_truth: dict,
    refs: List[Dict[str, Any]],
    issue_id: str,
    engine_env: Dict[str, str],
    language: str | None = None,
) -> str:
    """把上游产物(dict[标签 -> 文本])渲染成 issue body 的只读上下文段。

    上游产物本身通常是 Markdown。不要再外包一层代码块,否则平台 Markdown
    对四反引号支持不完整时会破坏渲染,也会让人工审阅很难读。
    """
    language = language or current_language()
    env_prefix = " ".join(
        f"{key}={engine_env[key]}"
        for key in ("OMAC_ENGINE", "OMAC_WORKSPACE_ID", "OMAC_PROJECT_ID")
        if engine_env.get(key)
    )
    env_prefix = f"{env_prefix} " if env_prefix else ""
    sections = [f"## {ui('Upstream artifacts (read-only context)', '上游产物(只读上下文)', language=language)}"]
    for label, text in source_of_truth.items():
        if not text:
            continue
        ref = next((ref for ref in refs if ref.get("label") == label), None)
        if ref and ref.get("content_externalized") is True:
            output_path = _source_output_path(label)
            sections.append(ui(
                f"### {label}\n\n"
                "Large upstream artifact omitted from the issue body. "
                "The authoritative content remains on the upstream issue deliverable attachment.\n\n"
                f"- source issue: `{ref['issue_id']}`\n"
                f"- bytes: `{ref['content_bytes']}`\n"
                f"- sha256: `{ref['content_sha256']}`\n\n"
                "Materialize it through OMAC before working:\n\n"
                f"```bash\n{env_prefix}omac work read {issue_id} --source {label} "
                f"--output-file {output_path}\n```",
                f"### {label}\n\n"
                "大型上游产物未内联到 issue 正文；权威内容仍保存在上游 issue 的 deliverable 附件中。\n\n"
                f"- source issue: `{ref['issue_id']}`\n"
                f"- bytes: `{ref['content_bytes']}`\n"
                f"- sha256: `{ref['content_sha256']}`\n\n"
                "开始工作前先通过 OMAC 物化附件：\n\n"
                f"```bash\n{env_prefix}omac work read {issue_id} --source {label} "
                f"--output-file {output_path}\n```",
                language=language,
            ))
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
    return refresh_authoring_task(engine, item.id, spec, item_snapshot=item)


def refresh_authoring_task(
    engine,
    item_id: str,
    spec: AuthoringTaskSpec,
    *,
    item_snapshot: Optional[WorkItem] = None,
    restart: Optional[RestartState] = None,
) -> WorkItem:
    """先覆盖紧凑正文再完整读取，允许修复旧巨型 issue。"""
    store = engine.store
    env = _engine_env(engine)
    refs = normalize_source_refs(spec.source_refs, engine_env=env)
    refs = _externalize_large_sources(spec.source_of_truth, refs)
    body_node = SimpleNamespace(
        title=spec.title,
        description=spec.description,
        reviewer=None,
        id=item_id,
    )
    body = render_issue_body(
        body_node,
        spec.contract,
        spec.kind,
        item_id,
        source_refs=refs,
        engine_env=env,
        issue_key=getattr(item_snapshot, "identifier", None),
        language=current_language(),
    )
    if spec.source_of_truth:
        body += "\n\n" + _render_source_of_truth(
            spec.source_of_truth, refs, item_id, env, current_language())
    if restart is not None:
        store.guard_authoring_restart(
            item_id,
            generation=restart.generation,
            owner_nonce=restart.owner_nonce,
        )
    if spec.contract is not None:
        store.set_node_contract(item_id, spec.contract)
    if restart is not None:
        store.guard_authoring_restart(
            item_id,
            generation=restart.generation,
            owner_nonce=restart.owner_nonce,
        )
    refreshed = store.update_work_item_metadata(
        item_id,
        worker=spec.assignee,
        description=body,
        source_refs=refs,
        amendment_attempt=spec.amendment_attempt,
    )
    if restart is not None:
        store.guard_authoring_restart(
            item_id,
            generation=restart.generation,
            owner_nonce=restart.owner_nonce,
        )
    return refreshed


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
    pause_at_confirmation: bool = False,
    source_refs: Optional[List[Any]] = None,
    dag_key: Optional[str] = None,
    resume_item_id: Optional[str] = None,
    resume_item_snapshot: Optional[WorkItem] = None,
    restart_authoring: bool = False,
    amendment_attempt: Optional[Dict[str, Any]] = None,
    reuse_dag_key: bool = False,
    review_acceptance_doc: Any = None,
    review_amendment_manifest: Any = None,
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

    if restart_authoring and resume_item_id is None:
        raise ValidationError(ui(
            "--restart-authoring requires --resume-issue-id",
            "--restart-authoring 必须与 --resume-issue-id 一起使用"))
    if restart_authoring and kind != TaskKind.AMENDMENT:
        raise ValidationError(ui(
            "Authoring restart is supported only for amendment tasks",
            "只有 amendment 任务支持重开 authoring"))
    if restart_authoring and not store.capabilities.atomic_authoring_restart:
        raise NeedsDecision(
            ui(
                "This engine cannot safely restart authoring on the same issue",
                "当前引擎无法安全地在同一 issue 上重开 authoring"),
            report={
                "reason_code": "atomic-restart-unsupported",
                "engine": store.config.engine_type,
                "next_action": "Create a new amendment issue instead of resuming the old confirmation",
            },
        )
    if (
        restart_authoring
        and not runtime.capabilities.stable_direct_run_identity
    ):
        raise NeedsDecision(
            ui(
                "This runtime cannot bind restart dispatch to a stable direct Run identity",
                "当前 runtime 无法把 restart 派发绑定到稳定的 direct Run 身份"),
            report={
                "reason_code": "restart-run-identity-unsupported",
                "engine": store.config.engine_type,
            },
        )

    title = payload.get("title") or f"{kind.value} task"
    task_key = dag_key or make_dag_key(kind, title=title, unique=True)
    contract = _payload_contract(payload.get("contract"))
    source_of_truth = payload.get("source_of_truth") or {}
    spec = AuthoringTaskSpec(
        kind=kind,
        title=title,
        dag_key=task_key,
        assignee=assignee,
        description=payload.get("description") or "",
        contract=contract,
        source_refs=list(source_refs or []),
        source_of_truth=source_of_truth,
        amendment_attempt=amendment_attempt,
    )
    restart_ctx: Optional[RestartState] = None
    reused_created_item = False

    if resume_item_id is not None:
        item = resume_item_snapshot or store.get_work_item(resume_item_id)
        if restart_authoring:
            request_digest = _restart_request_digest(spec)
            baseline_runs = runtime.list_runs(item.id)
            existing_restart = item.authoring_restart
            recovering_same_request = (
                existing_restart is not None
                and existing_restart.request_digest == request_digest
            )
            if (
                any(run.active for run in baseline_runs)
                and not recovering_same_request
            ):
                raise NeedsDecision(
                    ui(
                        f"Amendment {item.id} has an active Agent run; wait for it to finish before restarting authoring",
                        f"amendment {item.id} 仍有活跃 Agent Run；等待其结束后再重开 authoring"),
                    report={
                        "item_id": item.id,
                        "kind": kind.value,
                        "phase": item.phase.value,
                        "reason_code": "active-agent-run",
                        "last_opinion": "active Agent run prevents authoring restart",
                    },
                )
            now = time.time()
            lease_seconds = max(
                90,
                int(store.config.polling_interval or 30) * 4,
            )
            candidate = RestartState(
                generation=secrets.token_hex(16),
                owner_nonce=secrets.token_hex(16),
                request_digest=request_digest,
                state="claimed",
                base_kind=item.kind.value,
                base_phase=item.phase.value,
                base_status=item.status.value,
                base_review_subject_digest=item.review_subject_digest or "",
                base_deliverable_identity=deliverable_identity(item),
                baseline_run_ids=(
                    existing_restart.baseline_run_ids
                    if recovering_same_request
                    else tuple(
                        run.id for run in baseline_runs if run.kind == "direct")
                ),
                lease_expires_at=now + lease_seconds,
            )
            claim = store.claim_authoring_restart(
                item.id, candidate, now=now)
            restart_ctx = claim.restart
            if claim.confirmation_reused:
                item = store.get_work_item(item.id)
            elif not claim.acquired:
                raise NeedsDecision(
                    ui(
                        f"Amendment restart generation {restart_ctx.generation} is owned by another process",
                        f"amendment restart generation {restart_ctx.generation} 正由另一进程持有"),
                    report={
                        "item_id": item.id,
                        "reason_code": "restart-claim-held",
                        "generation": restart_ctx.generation,
                        "state": restart_ctx.state,
                        "lease_expires_at": restart_ctx.lease_expires_at,
                    },
                )
            else:
                post_claim_runs = runtime.list_runs(item.id)
                unexpected = [
                    run for run in post_claim_runs
                    if run.kind == "direct"
                    and run.id not in set(restart_ctx.baseline_run_ids)
                ]
                unexpected_active_non_direct = any(
                    run.active and run.kind != "direct"
                    for run in post_claim_runs
                )
                if (
                    restart_ctx.state == "claimed"
                    and (
                        any(run.active for run in post_claim_runs)
                        or unexpected
                    )
                ) or unexpected_active_non_direct:
                    unknown = restart_ctx.evolve(
                        state="unknown_partial",
                        detail="run set changed after restart claim",
                    )
                    store.update_authoring_restart(
                        item.id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                        state=unknown,
                    )
                    raise NeedsDecision(
                        ui(
                            "Agent Run state changed after the restart claim; no cleanup or dispatch is safe",
                            "restart claim 后 Agent Run 状态发生变化；无法安全清理或派发"),
                        report={
                            "item_id": item.id,
                            "reason_code": "restart-run-race",
                            "generation": restart_ctx.generation,
                            "outcome": "unknown_partial",
                        },
                    )
                if restart_ctx.state == "claimed":
                    item = store.restart_authoring(
                        item.id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                    )
                    restart_ctx = item.authoring_restart
                else:
                    item = store.get_work_item(item.id)
                if restart_ctx is not None and restart_ctx.state == "cleaned":
                    item = refresh_authoring_task(
                        engine,
                        item.id,
                        spec,
                        item_snapshot=item,
                        restart=restart_ctx,
                    )
                    restart_ctx = store.update_authoring_restart(
                        item.id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                        state=restart_ctx.evolve(state="refreshed"),
                    )
                    item = store.get_work_item(item.id)
        elif (
            item.status == WorkItemStatus.TODO
            and item.phase == TaskPhase.AUTHORING
            and not item.deliverable
        ):
            item = refresh_authoring_task(
                engine, item.id, spec, item_snapshot=item)
        item_id = item.id
    else:
        item = (
            store.find_work_item_by_dag_key(store.config.workspace_id, task_key)
            if reuse_dag_key else None
        )
        reused_created_item = item is not None
        if item is None:
            try:
                item = create_authoring_task(engine, spec)
            except PlatformError:
                if not reuse_dag_key:
                    raise
                item = store.find_work_item_by_dag_key(
                    store.config.workspace_id, task_key)
                if item is None:
                    raise
                reused_created_item = True
        elif item.amendment_attempt not in (None, amendment_attempt):
            raise NeedsDecision(
                ui(
                    "The amendment attempt identity already belongs to different inputs",
                    "该 amendment attempt 身份已绑定到不同输入"),
                report={
                    "reason_code": "amendment-attempt-identity-conflict",
                    "item_id": item.id,
                    "dag_key": task_key,
                },
            )
        if item.description == spec.title:
            item = refresh_authoring_task(
                engine, item.id, spec, item_snapshot=item)
        item_id = item.id

    explicit_resume = resume_item_id is not None or reused_created_item
    resume_authoring_attempt_available = explicit_resume and not restart_authoring

    if (
        explicit_resume
        and item.phase == TaskPhase.CONFIRMATION
        and (
            item.status in (WorkItemStatus.FAILED, WorkItemStatus.BLOCKED)
            or item.agent_run_failed
            or item.agent_run_finished_without_submit
        )
    ):
        confirmation_round = max(1, item.bounces.review + 1)
        confirmation_is_consumable = (
            _has_review_verdict(item)
            and item.review_subject_digest == _review_subject_digest(
                kind, item, confirmation_round)
            and not _review_evidence_errors(contract, item)
        )
        if not confirmation_is_consumable:
            raise NeedsDecision(
                ui(
                    f"{kind.value} is already in human confirmation; an Agent run "
                    f"cannot be resumed (item {item_id})",
                    f"{kind.value} 已进入人工确认阶段，不能恢复 Agent run"
                    f"（item {item_id}）",
                ),
                report={
                    "item_id": item_id,
                    "kind": kind.value,
                    "phase": TaskPhase.CONFIRMATION.value,
                    "rounds": 0,
                    "last_opinion": "confirmation does not permit Agent rerun",
                },
            )

    def _raise_if_authoring_stopped(candidate: WorkItem) -> None:
        if candidate.phase != TaskPhase.AUTHORING:
            return
        if candidate.status not in (WorkItemStatus.FAILED, WorkItemStatus.BLOCKED):
            return
        outcome = (
            "blocked" if candidate.status == WorkItemStatus.BLOCKED
            else "failed"
        )
        log.info(logsetup.EVT_NODE_FAILED, kind=kind.value, id=item_id,
                 reason=f"producer {outcome}")
        raise NeedsDecision(
            ui(
                f"{kind.value} authoring {outcome} (item {item_id})",
                f"{kind.value} 产出阶段{'被阻塞' if outcome == 'blocked' else '失败'}"
                f"(item {item_id})"),
            report={"item_id": item_id, "kind": kind.value, "rounds": 0,
                    "last_opinion": f"producer {outcome}"})

    def _raise_completed_without_submit(
        *, reason_code: str = "completed-without-submit", role: str = "worker",
    ) -> None:
        nonlocal restart_ctx
        decision_phase = (
            TaskPhase.REVIEW if role == "reviewer" else TaskPhase.AUTHORING
        )
        decision = {
            "schema": DECISION_REQUIRED_SCHEMA,
            "reason_code": reason_code,
            "kind": kind.value,
            "phase": decision_phase.value,
            "resume_issue_id": item_id,
        }
        store.update_work_item_metadata(
            item_id,
            decision_required=decision,
            phase=decision_phase,
        )
        store.mark_blocked(item_id)
        if restart_ctx is not None:
            restart_ctx = store.update_authoring_restart(
                item_id,
                generation=restart_ctx.generation,
                owner_nonce=restart_ctx.owner_nonce,
                state=restart_ctx.evolve(
                    state="needs_decision", detail=reason_code),
            )
        log.info(
            logsetup.EVT_NEEDS_DECISION,
            kind=kind.value,
            id=item_id,
            gate=("review-verdict" if role == "reviewer" else "authoring-submit"),
            rounds=0,
        )
        raise NeedsDecision(
            ui(
                f"{kind.value} Agent run completed without a fresh structured deliverable (item {item_id})",
                f"{kind.value} Agent Run 已结束但没有提交新的结构化交付物（item {item_id}）"),
            report={
                "item_id": item_id,
                "kind": kind.value,
                "phase": decision_phase.value,
                "reason_code": reason_code,
                "rounds": 0,
                "last_opinion": (
                    "Reviewer run completed without verdict"
                    if role == "reviewer"
                    else "Agent run completed without omac work submit"
                ),
            },
        )

    def _renew_restart_lease() -> None:
        nonlocal restart_ctx
        if restart_ctx is None or restart_ctx.state in {
            "confirmation", "needs_decision", "unknown_partial",
        }:
            return
        restart_ctx = store.update_authoring_restart(
            item_id,
            generation=restart_ctx.generation,
            owner_nonce=restart_ctx.owner_nonce,
            state=restart_ctx.evolve(
                lease_expires_at=time.time() + max(
                    90, int(store.config.polling_interval or 30) * 4),
            ),
        )

    def _stop_restart(state: str, detail: str) -> None:
        nonlocal restart_ctx
        if restart_ctx is None:
            return
        restart_ctx = store.update_authoring_restart(
            item_id,
            generation=restart_ctx.generation,
            owner_nonce=restart_ctx.owner_nonce,
            state=restart_ctx.evolve(state=state, detail=detail),
        )

    def _wait_for_generation_run(
        *,
        baseline_ids: set[str],
        predicate: Callable[[WorkItem], bool],
        role: str,
    ) -> tuple[WorkItem, Optional[str]]:
        nonlocal restart_ctx
        dispatch_scope = "restart" if restart_ctx is not None else "dispatch"
        invisible_polls = 0
        observed_run_id = (
            restart_ctx.reviewer_run_id
            if restart_ctx is not None and role == "reviewer"
            else restart_ctx.worker_run_id if restart_ctx is not None else None
        )
        while True:
            current = store.get_work_item(item_id)
            visible_runs = runtime.list_runs(item_id)
            active_non_direct = [
                run for run in visible_runs
                if run.kind != "direct" and run.active
            ]
            if active_non_direct:
                _stop_restart(
                    "unknown_partial", "active non-direct run during restart")
                raise NeedsDecision(
                    ui(
                        "A non-direct Agent Run became active during amendment restart",
                        "amendment restart 期间出现了活跃的非 direct Agent Run"),
                    report={
                        "item_id": item_id,
                        "reason_code": f"{dispatch_scope}-run-race",
                        "generation": restart_ctx.generation if restart_ctx else None,
                        "run_ids": [run.id for run in active_non_direct],
                        "outcome": "unknown_partial",
                    },
                )
            new_direct = [
                run for run in visible_runs
                if run.kind == "direct" and run.id not in baseline_ids
            ]
            if len(new_direct) > 1:
                _stop_restart(
                    "unknown_partial", "multiple generation-owned direct runs")
                raise NeedsDecision(
                    ui(
                        "Multiple new direct Runs appeared for one restart generation",
                        "同一个 restart generation 出现了多个新的 direct Run"),
                    report={
                        "item_id": item_id,
                        "reason_code": f"{dispatch_scope}-multiple-runs",
                        "generation": restart_ctx.generation if restart_ctx else None,
                        "run_ids": [run.id for run in new_direct],
                        "outcome": "unknown_partial",
                    },
                )
            direct = new_direct
            if observed_run_id is not None:
                direct = [run for run in direct if run.id == observed_run_id]
            if direct:
                run = direct[0]
                observed_run_id = run.id
                invisible_polls = 0
                if restart_ctx is not None:
                    changes = (
                        {"reviewer_run_id": run.id}
                        if role == "reviewer"
                        else {"worker_run_id": run.id}
                    )
                    restart_ctx = store.update_authoring_restart(
                        item_id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                        state=restart_ctx.evolve(**changes),
                    )
                if predicate(current):
                    return current, observed_run_id
                if run.terminal:
                    _raise_completed_without_submit(
                        reason_code=(
                            "reviewer-completed-without-verdict"
                            if role == "reviewer"
                            else "completed-without-submit"
                        ),
                        role=role,
                    )
            else:
                if observed_run_id is not None and predicate(current):
                    return current, observed_run_id
                invisible_polls += 1
                if invisible_polls >= _RUN_VISIBILITY_MAX_POLLS:
                    _stop_restart(
                        "unknown_partial", f"{role} run was not observable")
                    raise NeedsDecision(
                        ui(
                            "The generation-owned Agent Run is still not visible; dispatch outcome is unknown",
                            "generation 对应的 Agent Run 仍不可见；派发结果未知"),
                        report={
                            "item_id": item_id,
                            "reason_code": f"{dispatch_scope}-run-not-observed",
                            "generation": restart_ctx.generation if restart_ctx else None,
                            "role": role,
                            "outcome": "unknown_partial",
                        },
                    )
            poll()
            _renew_restart_lease()

    def _dispatch_authoring_and_wait() -> WorkItem:
        nonlocal restart_ctx
        if restart_ctx is not None:
            if restart_ctx.state not in {"refreshed", "dispatching"}:
                current = store.get_work_item(item_id)
                if current.phase != TaskPhase.AUTHORING:
                    raise NeedsDecision(
                        ui(
                            "Restart recovery expected authoring before Worker dispatch",
                            "restart 恢复在派发 Worker 前预期处于 authoring"),
                        report={
                            "item_id": item_id,
                            "reason_code": "restart-phase-mismatch",
                            "generation": restart_ctx.generation,
                            "phase": current.phase.value,
                        },
                    )
                worker_baseline = tuple(sorted(
                    run.id for run in runtime.list_runs(item_id)
                    if run.kind == "direct"))
                restart_ctx = store.update_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                    state=restart_ctx.evolve(
                        state="dispatching",
                        baseline_run_ids=worker_baseline,
                        reviewer_baseline_run_ids=(),
                        worker_run_id=None,
                        reviewer_run_id=None,
                    ),
                )
            baseline = set(restart_ctx.baseline_run_ids)
            existing = [
                run for run in runtime.list_runs(item_id)
                if run.kind == "direct" and run.id not in baseline
            ]
            if restart_ctx.state == "refreshed":
                restart_ctx = store.update_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                    state=restart_ctx.evolve(state="dispatching"),
                )
            if restart_ctx.state == "dispatching" and not existing:
                store.guard_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                )
                store.mark_in_progress(item_id)
                store.guard_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                )
                store.clear_assignment(item_id)
                store.guard_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                )
                store.assign_work_item(item_id, assignee, "worker")
                store.guard_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                )
                runtime.wake_for_claim(item_id, assignee, "worker")
                store.guard_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                )
                log.info(
                    logsetup.EVT_DISPATCH,
                    kind=kind.value,
                    id=item_id,
                    worker=assignee,
                    generation=restart_ctx.generation,
                )
            def fresh_submission(candidate: WorkItem) -> bool:
                return (
                    _produced(candidate)
                    and candidate.deliverable_ref is not None
                    and deliverable_identity(candidate)
                    != restart_ctx.base_deliverable_identity
                )

            produced, run_id = _wait_for_generation_run(
                baseline_ids=baseline,
                predicate=fresh_submission,
                role="worker",
            )
            restart_ctx = store.update_authoring_restart(
                item_id,
                generation=restart_ctx.generation,
                owner_nonce=restart_ctx.owner_nonce,
                state=restart_ctx.evolve(
                    state="submitted", worker_run_id=run_id),
            )
            _raise_if_authoring_stopped(produced)
            return produced

        baseline = None
        if runtime.capabilities.stable_direct_run_identity:
            baseline = {
                run.id for run in runtime.list_runs(item_id)
                if run.kind == "direct"
            }
        store.mark_in_progress(item_id)
        store.assign_work_item(item_id, assignee, "worker")
        runtime.wake(item_id, assignee, "worker")
        log.info(
            logsetup.EVT_DISPATCH,
            kind=kind.value,
            id=item_id,
            worker=assignee,
        )
        if baseline is not None:
            produced, _run_id = _wait_for_generation_run(
                baseline_ids=baseline,
                predicate=_produced,
                role="worker",
            )
            _raise_if_authoring_stopped(produced)
            return produced
        produced = _poll_until(
            store,
            item_id,
            lambda candidate: (
                _produced(candidate)
                or candidate.agent_run_finished_without_submit
            ),
            poll,
        )
        if produced.agent_run_finished_without_submit and not _produced(produced):
            _raise_completed_without_submit()
        _raise_if_authoring_stopped(produced)
        return produced

    def _produce(hint: Optional[List[str]] = None) -> WorkItem:
        nonlocal resume_authoring_attempt_available

        current = store.get_work_item(item_id)
        if (
            hint is None
            and resume_authoring_attempt_available
            and current.phase == TaskPhase.AUTHORING
        ):
            if runtime.is_active(item_id):
                current = _poll_until(
                    store,
                    item_id,
                    lambda candidate: (
                        candidate.phase != TaskPhase.AUTHORING
                        or candidate.status in (
                            WorkItemStatus.DONE,
                            WorkItemStatus.FAILED,
                            WorkItemStatus.BLOCKED,
                        )
                        or candidate.agent_run_finished_without_submit
                    ),
                    poll,
                )
                if current.phase != TaskPhase.AUTHORING or (
                    current.status == WorkItemStatus.DONE
                ):
                    resume_authoring_attempt_available = False
                    return current
            if (
                current.status == WorkItemStatus.FAILED
                or current.agent_run_finished_without_submit
            ):
                resume_authoring_attempt_available = False
                return _dispatch_authoring_and_wait()
        _raise_if_authoring_stopped(current)
        if hint is None and _produced(current):
            return current
        if hint:
            _persist_machine_feedback(hint)
        return _dispatch_authoring_and_wait()

    def _persist_machine_feedback(errors: List[str]) -> None:
        feedback = build_machine_feedback("machine-gate", errors)
        store.update_work_item_metadata(
            item_id,
            machine_feedback=feedback,
            review_comment=machine_feedback_summary(item_id, feedback),
        )

    def _block_for_budget_decision(gate: str, rounds: int) -> WorkItem:
        current = store.get_work_item(item_id)
        decision = _budget_exhausted_decision(
            kind, current, gate=gate, rounds=rounds)
        store.update_work_item_metadata(
            item_id,
            decision_required=decision,
            phase=TaskPhase.REVIEW,
        )
        store.mark_blocked(item_id)
        return store.get_work_item(item_id)

    def _amendment_review_evidence(candidate: WorkItem) -> dict[str, str]:
        if kind != TaskKind.AMENDMENT or review_amendment_manifest is None:
            return {}
        from ..core.amendment import (
            _historical_contract_corrections, parse_proposal,
            historical_work_item_evidence_digest,
        )

        proposal = parse_proposal(candidate.deliverable or "")
        evidence = {}
        for correction in _historical_contract_corrections(
                review_amendment_manifest, proposal):
            node = review_amendment_manifest.nodes[correction["node"]]
            if not node.work_item_id:
                raise ValidationError(
                    f"historical contract correction node {node.id} requires a work item for evidence CAS")
            # Reviewer obligations must bind the same expanded Store facts
            # that reviewed amendment construction and apply verify consume.
            evidence[node.id] = historical_work_item_evidence_digest(
                store.get_work_item(node.work_item_id))
        return evidence

    delivered = _produce()
    delivery = _delivery_of(kind, delivered)

    if (
        delivered.status == WorkItemStatus.DONE
        and delivered.review_verdict in {"pass", "pass-with-nits"}
    ):
        log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
        return {"item_id": item_id, "delivery": delivery,
                "rounds": 0, "verdict": delivered.review_verdict,
                "kind": kind.value}

    initial_review_limit = authorized_review_limit(delivered, max_revisions)
    if (
        reviewers
        and _rejected_verdict_was_counted(kind, delivered)
        and delivered.bounces.review < initial_review_limit
    ):
        store.reset_review(item_id)
        store.update_status(item_id, WorkItemStatus.TODO)
        delivered = _produce()
        delivery = _delivery_of(kind, delivered)

    # 机器门(零 reviewer token):阶段 guard + 通用 review preflight。
    # 所有可确定判断先回给产出者，Reviewer 只消费通过后的语义问题。
    if guard is not None or reviewers:
        for guard_round in range(1, max_revisions + 1):
            guard_errors: List[str] = guard(delivered) if guard is not None else []
            if reviewers:
                guard_errors.extend(run_review_preflight(delivered))
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
            # Producer submit 会按真实协议清除已消费的 feedback。预算耗尽
            # 后必须把 exit 20 的完整问题重新持久化，供 operator/Author 读取。
            _persist_machine_feedback(guard_errors)
            _block_for_budget_decision("guard", max_revisions)
            raise NeedsDecision(
                ui(
                    f"{kind.value} did not pass the machine gate after {max_revisions} revisions",
                    f"{kind.value} 任务经 {max_revisions} 轮 machine-gate 仍未通过"),
                report={"item_id": item_id, "kind": kind.value,
                        "rounds": max_revisions, "phase": "guard",
                        "last_opinion": "\n".join(guard_errors)})

    def _finish_after_review(
        verdict: str,
        rounds: int,
        current_delivery: Dict[str, Any],
        *,
        prepare_confirmation: bool = True,
    ) -> Dict[str, Any]:
        nonlocal restart_ctx
        current = store.get_work_item(item_id)
        if current.review_comment or current.machine_feedback_ref:
            store.update_work_item_metadata(
                item_id, review_comment="", machine_feedback={})
        if not confirm:
            store.mark_done(item_id)
            log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
            return {"item_id": item_id, "delivery": current_delivery,
                    "rounds": rounds, "verdict": verdict, "kind": kind.value}

        if prepare_confirmation:
            store.clear_assignment(item_id)
            # unassign 在真实平台上可能触发 issue 状态写；阶段必须最后落盘，
            # 否则会出现 loop 已等待人工门、metadata 却仍停在 review 的竞态。
            store.update_work_item_metadata(
                item_id, phase=TaskPhase.CONFIRMATION)
        if restart_ctx is not None:
            restart_ctx = store.update_authoring_restart(
                item_id,
                generation=restart_ctx.generation,
                owner_nonce=restart_ctx.owner_nonce,
                state=restart_ctx.evolve(state="confirmation"),
            )
        log.info(logsetup.EVT_HUMAN_GATE_WAIT, kind=kind.value, id=item_id)
        if pause_at_confirmation:
            return {
                "item_id": item_id,
                "delivery": current_delivery,
                "rounds": rounds,
                "verdict": verdict,
                "kind": kind.value,
                "pending_confirmation": True,
            }
        confirmed = _poll_until(
            store, item_id, lambda i: i.status == WorkItemStatus.DONE, poll)
        confirmed_delivery = _delivery_of(kind, confirmed)
        log.info(logsetup.EVT_NODE_DONE, kind=kind.value, id=item_id)
        return {"item_id": item_id, "delivery": confirmed_delivery,
                "rounds": rounds, "verdict": verdict, "kind": kind.value}

    if delivered.phase == TaskPhase.CONFIRMATION:
        confirmation_round = max(1, delivered.bounces.review + 1)
        current_subject = _review_subject_digest(
            kind, delivered, confirmation_round)
        if (
            _has_review_verdict(delivered)
            and delivered.review_subject_digest == current_subject
        ):
            evidence_errors = _review_evidence_errors(contract, delivered)
            if not evidence_errors:
                return _finish_after_review(
                    delivered.review_verdict or "pass", confirmation_round,
                    delivery, prepare_confirmation=False)
            log.info(logsetup.EVT_REVISION, kind=kind.value, id=item_id,
                     gate="review-evidence", round=confirmation_round,
                     max=authorized_review_limit(delivered, max_revisions))
            delivered = _restart_invalid_review(
                store, item_id, current_subject, evidence_errors)
        else:
            # confirmation 只能消费仍绑定当前交付的评审结论。若产出者在
            # pass-with-nits 后提交了新交付，旧 verdict 不能替新交付背书。
            store.update_work_item_metadata(item_id, phase=TaskPhase.REVIEW)
            delivered = store.get_work_item(item_id)

    if not reviewers:
        return _finish_after_review("pass", 0, delivery)

    # ── review 阶段(有界修订循环) ──
    # review_bounce 是已经发生的评审回退次数。它必须跨进程持久化，
    # 否则 plan resume 会重置预算并让有界循环变成事实上的无限循环。
    review_bounce = max(0, delivered.bounces.review)
    review_limit = authorized_review_limit(delivered, max_revisions)
    last_opinion = _review_opinion(delivered)
    if review_bounce >= review_limit:
        log.info(logsetup.EVT_NEEDS_DECISION, kind=kind.value, id=item_id,
                 gate="review", rounds=review_bounce)
        delivered = _block_for_budget_decision("review", review_bounce)
        raise _review_exhausted_error(
            kind, delivered, review_bounce, last_opinion)

    for round_index in range(review_bounce + 1, review_limit + 1):
        reviewer = _pick_reviewer(reviewers, assignee, round_index - 1)
        subject_digest = _review_subject_digest(kind, delivered, round_index)
        current = store.get_work_item(item_id)
        if current.review_subject_digest != subject_digest:
            store.update_work_item_metadata(
                item_id,
                review_obligations=build_review_obligations(
                    current,
                    acceptance_doc=review_acceptance_doc,
                    amendment_manifest=review_amendment_manifest,
                    amendment_evidence=_amendment_review_evidence(current)),
            )
        reviewed = store.prepare_review_cycle(item_id, subject_digest)
        while True:
            if _has_review_verdict(reviewed):
                evidence_errors = _review_evidence_errors(contract, reviewed)
                if not evidence_errors:
                    break
                log.info(logsetup.EVT_REVISION, kind=kind.value, id=item_id,
                         gate="review-evidence", round=round_index,
                         max=review_limit)
                reviewed = _restart_invalid_review(
                    store, item_id, subject_digest, evidence_errors)
                if restart_ctx is not None:
                    restart_ctx = store.update_authoring_restart(
                        item_id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                        state=restart_ctx.evolve(
                            state="submitted",
                            reviewer_run_id=None,
                        ),
                    )

            store.mark_in_review(item_id)
            if restart_ctx is not None:
                recovering_review = restart_ctx.state == "reviewing"
                if recovering_review:
                    reviewer_baseline = set(
                        restart_ctx.reviewer_baseline_run_ids)
                else:
                    reviewer_baseline = {
                        run.id for run in runtime.list_runs(item_id)
                        if run.kind == "direct"
                    }
                    restart_ctx = store.update_authoring_restart(
                        item_id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                        state=restart_ctx.evolve(
                            state="reviewing",
                            baseline_run_ids=(),
                            reviewer_baseline_run_ids=tuple(
                                sorted(reviewer_baseline)),
                        ),
                    )
                    store.guard_authoring_restart(
                        item_id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                    )
                    store.clear_assignment(item_id)
                    store.guard_authoring_restart(
                        item_id,
                        generation=restart_ctx.generation,
                        owner_nonce=restart_ctx.owner_nonce,
                    )
                    store.assign_work_item(item_id, reviewer, "reviewer")
                    runtime.wake_for_claim(item_id, reviewer, "reviewer")
                    log.info(
                        logsetup.EVT_REVIEW_DISPATCH,
                        kind=kind.value,
                        id=item_id,
                        reviewer=reviewer,
                        generation=restart_ctx.generation,
                    )
                reviewed, reviewer_run_id = _wait_for_generation_run(
                    baseline_ids=reviewer_baseline,
                    predicate=_has_review_verdict,
                    role="reviewer",
                )
                restart_ctx = store.update_authoring_restart(
                    item_id,
                    generation=restart_ctx.generation,
                    owner_nonce=restart_ctx.owner_nonce,
                    state=restart_ctx.evolve(
                        reviewer_run_id=reviewer_run_id),
                )
            else:
                reviewer_baseline = None
                if runtime.capabilities.stable_direct_run_identity:
                    reviewer_baseline = {
                        run.id for run in runtime.list_runs(item_id)
                        if run.kind == "direct"
                    }
                store.assign_work_item(item_id, reviewer, "reviewer")
                log.info(logsetup.EVT_REVIEW_DISPATCH, kind=kind.value, id=item_id,
                         reviewer=reviewer)
                runtime.wake(item_id, reviewer, "reviewer")
                if reviewer_baseline is not None:
                    reviewed, _reviewer_run_id = _wait_for_generation_run(
                        baseline_ids=reviewer_baseline,
                        predicate=_has_review_verdict,
                        role="reviewer",
                    )
                else:
                    reviewed = _poll_until(
                        store, item_id, _has_review_verdict, poll)

        verdict = reviewed.review_verdict
        log.info(logsetup.EVT_VERDICT, kind=kind.value, id=item_id,
                 verdict=verdict, round=round_index)
        if verdict == "pass":
            return _finish_after_review("pass", round_index, delivery)

        if verdict == "pass-with-nits":
            log.info(logsetup.EVT_REVISION, kind=kind.value, id=item_id,
                     gate="review-nits", round=round_index, max=review_limit)
            store.update_work_item_metadata(
                item_id, phase=TaskPhase.AUTHORING,
                review_comment="", machine_feedback={},
                review_bounce=round_index)
            store.update_status(item_id, WorkItemStatus.TODO)
            delivered = _produce()
            delivery = _delivery_of(kind, delivered)
            if round_index >= review_limit:
                log.info(logsetup.EVT_NEEDS_DECISION, kind=kind.value,
                         id=item_id, gate="review-nits",
                         rounds=round_index)
                delivered = _block_for_budget_decision(
                    "review-nits", round_index)
                raise _review_exhausted_error(
                    kind, delivered, round_index, _review_opinion(reviewed),
                    gate="review-nits")

            # 产出者重新提交后，无论改动大小，最终交付都必须重新评审。
            # 下一轮 prepare_review_cycle 会用新交付 digest 清除旧 verdict。
            continue

        # reject:评审 report 已在 metadata,reset_review 只清当前判定并转回产出者。
        # 返工上下文由下一轮 agent 通过 work show 读取,不写评论以免触发额外 run。
        last_opinion = _review_opinion(reviewed)
        log.info(logsetup.EVT_REVISION, kind=kind.value, id=item_id,
                 gate="review", round=round_index, max=review_limit)
        store.update_work_item_metadata(
            item_id, review_bounce=round_index)
        if round_index >= review_limit:
            log.info(logsetup.EVT_NEEDS_DECISION, kind=kind.value, id=item_id,
                     gate="review", rounds=round_index)
            reviewed = _block_for_budget_decision("review", round_index)
            raise _review_exhausted_error(
                kind, reviewed, round_index, last_opinion)
        store.reset_review(item_id)
        store.update_status(item_id, WorkItemStatus.TODO)
        delivered = _produce()
        delivery = _delivery_of(kind, delivered)

    raise AssertionError("review loop exhausted without a terminal verdict")

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
from dataclasses import MISSING, dataclass, field, fields as dataclass_fields
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from ..core import logsetup
from ..core.evidence import validate_review_evidence
from ..core.manifest import Contract, _dump_contract, _load_contract
from ..core.machine_feedback import (
    build_machine_feedback, machine_feedback_summary,
)
from ..core.review_convergence import (
    bounded_decision_required, build_review_obligations,
)
from ..core.review_continuation import authorized_review_limit
from ..core.review_preflight import run_review_preflight
from ..core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, DELIVERY_CONTENT_KEY, TaskKind, TaskPhase,
    make_dag_key,
)
from ..core.workitem import WorkItem, WorkItemStatus
from ..errors import NeedsDecision, PlatformError, ValidationError
from ..i18n import current_language, ui
from .convergence import ResolutionState, resolve_convergence
from .dispatch import (
    normalize_source_refs, render_issue_body, reviewer_dispatch_stopped,
)
from .apply import Ports, apply, apply_one
from .decide_transitions import block_in_phase, block_if_needed
from .decide_task import (
    Fact, Stage, TERMINAL, TaskObs, decide_task_step, plan_machine_feedback,
)
from .decide_effects import (
    Assign,
    ClearAssignment,
    CreateWorkItem,
    DispatchReviewer,
    PrepareReviewCycle,
    ResetReview,
    SetAuthoringIdentity,
    SetNodeContract,
    UpdateMetadata,
    UpdateStatus,
    Wake,
)

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


def _raise_if_reviewer_dispatch_stopped(
    store, item_id: str, kind: TaskKind,
) -> None:
    """Keep persisted Reviewer control authoritative before assign/wake."""
    current = store.observe_work_item_control(item_id).work_item
    if not reviewer_dispatch_stopped(current):
        return
    apply(block_if_needed(item_id, current.status), Ports(store=store))
    raise NeedsDecision(
        ui(
            "Reviewer dispatch is blocked by the existing decision or platform status.",
            "reviewer 派发被已有 decision 或平台 blocked 状态阻塞。",
        ),
        report={
            "item_id": item_id,
            "kind": kind.value,
            "phase": TaskPhase.REVIEW.value,
            "reason_code": "reviewer-dispatch-blocked",
            "decision_required": current.decision_required,
        },
    )


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


def _new_attempt_recovery(kind: TaskKind, item_id: str) -> tuple[str, str]:
    if kind == TaskKind.AMENDMENT:
        action = (
            "omac dag amend propose <manifest> --report-file <report> "
            "--docs <docs> --blocked-node <node> --new-attempt "
            f"--supersedes-issue-id {item_id} --output json"
        )
        return action, (
            "Keep the existing confirmation unchanged and create a deterministic "
            "new amendment attempt with the current report/docs inputs."
        )
    return f"omac work show {item_id} --output json", (
        "Inspect the current Store facts and start a fresh task through its normal "
        "command; do not clear confirmation or simulate an in-place restart."
    )


def _confirmation_resume_errors(
    kind: TaskKind,
    contract: Any,
    item: WorkItem,
) -> list[str]:
    round_index = max(1, item.bounces.review + 1)
    expected_subject = _review_subject_digest(kind, item, round_index)
    errors = []
    if item.review_verdict not in {"pass", "pass-with-nits"}:
        errors.append("confirmation requires a Reviewer-pass verdict")
    if item.review_subject_digest != expected_subject:
        errors.append("review subject does not match the current deliverable")
    persisted_contract = (
        _payload_contract(item.contract) if item.contract is not None else contract
    )
    errors.extend(_review_evidence_errors(persisted_contract, item))
    return errors


def _contract_identity(contract: Any) -> tuple[str, Any]:
    payload = _contract_payload(_payload_contract(contract))
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), payload


def _validate_amendment_resume_contract(
    item: WorkItem, requested_contract: Any,
) -> Any:
    """Reject amendment resume when the caller changed the bound contract/docs."""
    if item.contract is None:
        return requested_contract
    persisted_digest, persisted_payload = _contract_identity(item.contract)
    requested_digest, _requested_payload = _contract_identity(requested_contract)
    if persisted_digest == requested_digest:
        return _payload_contract(item.contract)
    raise NeedsDecision(
        ui(
            "Amendment resume inputs changed the persisted issue contract. Re-run "
            "with exactly the original authoritative docs; do not silently replace "
            "the bound contract.",
            "amendment resume 输入改变了 issue 已持久化的 contract。请严格使用原始权威 "
            "docs 重试；不会静默替换已绑定 contract。",
        ),
        report={
            "reason_code": "amendment-resume-contract-drift",
            "item_id": item.id,
            "persisted_contract_sha256": persisted_digest,
            "requested_contract_sha256": requested_digest,
            "persisted_source_of_truth": (
                persisted_payload.get("source_of_truth", [])
                if isinstance(persisted_payload, dict) else []
            ),
            "next_action": (
                "omac dag amend propose <manifest> --resume-issue-id "
                f"{item.id} --docs <persisted-contract-source>"
            ),
        },
    )


def _resume_amendment_nits_confirmation(
    engine: Any, item: WorkItem, contract: Any,
) -> WorkItem:
    """Recover an in-review amendment nits verdict without dispatching again."""
    errors = _confirmation_resume_errors(TaskKind.AMENDMENT, contract, item)
    if errors:
        raise NeedsDecision(
            ui(
                f"amendment pass-with-nits cannot resume from current Store facts "
                f"(item {item.id})",
                f"amendment pass-with-nits 无法基于当前 Store 事实恢复 "
                f"（item {item.id}）。",
            ),
            report={
                "reason_code": "confirmation-not-consumable",
                "item_id": item.id,
                "kind": TaskKind.AMENDMENT.value,
                "phase": item.phase.value,
                "last_opinion": "; ".join(errors),
                "errors": errors,
            },
        )
    unsafe_runs = [
        run for run in engine.runtime.list_runs(item.id)
        if run.kind == "direct" and (run.active or not run.terminal)
    ]
    if unsafe_runs:
        raise NeedsDecision(
            ui(
                "Amendment Reviewer/Worker direct Run is still active or unknown; "
                "wait for an explicit terminal state before resuming.",
                "amendment Reviewer/Worker direct Run 仍 active 或状态未知；请等待明确终止后再恢复。",
            ),
            report={
                "reason_code": "amendment-resume-active-direct-run",
                "item_id": item.id,
                "run_ids": [run.id for run in unsafe_runs],
            },
        )
    apply([
        ClearAssignment(item.id),
        UpdateMetadata(item.id, {'phase': TaskPhase.CONFIRMATION}),
    ], Ports(store=engine.store))
    return engine.store.get_work_item(item.id)


def _restart_invalid_review(
    store,
    item_id: str,
    subject_digest: str,
    errors: List[str],
) -> WorkItem:
    """清除无效 verdict/report，并在同一评审对象上重新派审。"""
    feedback = build_machine_feedback("review-evidence", errors)
    return apply(
        [ResetReview(item_id), PrepareReviewCycle(item_id, subject_digest),
         *plan_machine_feedback(item_id, feedback, machine_feedback_summary(item_id, feedback))],
        Ports(store=store))[-1]


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
    item = apply_one(CreateWorkItem({
        'workspace_id': store.config.workspace_id,
        'title': spec.title,
        'description': spec.title,
        'dag_key': spec.dag_key,
        'worker': spec.assignee,
        'kind': spec.kind,
    }), Ports(store=store))
    if spec.amendment_attempt is not None:
        return finalize_authoring_shell(
            engine, item, spec, allow_created_platform_assignee=True)
    return refresh_authoring_task(engine, item.id, spec, item_snapshot=item)


def _authoring_materialization(
    engine, item_id: str, spec: AuthoringTaskSpec, item_snapshot: Optional[WorkItem],
) -> tuple[str, List[Dict[str, Any]]]:
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
    return body, refs


def _contract_payload(contract: Any) -> Any:
    if isinstance(contract, Contract):
        return _dump_contract(contract)
    return contract


_PRISTINE_AMENDMENT_INITIALIZATION_FIELDS = frozenset({
    "id", "workspace_id", "identifier", "title", "description", "status",
    "dag_key", "worker", "contract", "contract_ref", "source_refs", "kind",
    "phase", "amendment_attempt", "created_at", "updated_at",
})
_NO_FIELD_DEFAULT = object()


def _field_default(definition) -> Any:
    if definition.default is not MISSING:
        return definition.default
    if definition.default_factory is not MISSING:
        return definition.default_factory()
    return _NO_FIELD_DEFAULT


def _pristine_amendment_activity_projection(item: WorkItem) -> Dict[str, Any]:
    """Project every persisted fact outside the positive initialization schema."""
    definitions = {
        definition.name: definition for definition in dataclass_fields(WorkItem)
    }
    projection = {}
    for name, definition in definitions.items():
        if name in _PRISTINE_AMENDMENT_INITIALIZATION_FIELDS:
            continue
        default = _field_default(definition)
        value = getattr(item, name)
        if default is None and value == "":
            continue  # clear_assignment() writes an empty string for "no reviewer"; that is the initial state
        if default is _NO_FIELD_DEFAULT or value != default:
            projection[name] = value
    for name in set(vars(item)) - set(definitions):
        projection[f"unknown_attribute.{name}"] = getattr(item, name)
    return projection


def _pristine_amendment_shell_errors(
    item: WorkItem,
    spec: AuthoringTaskSpec,
    *,
    body: str,
    refs: List[Dict[str, Any]],
    workspace_id: str,
    finalized: bool,
) -> tuple[List[str], List[str]]:
    """Validate a recoverable/final shell and return identity/activity errors."""
    expected_title = f"[DAG:{spec.dag_key}] {spec.title}"
    contract_matches = (
        _contract_payload(item.contract) == _contract_payload(spec.contract))
    checks = [
        (bool(item.id), "id"),
        (item.workspace_id == workspace_id, "workspace_id"),
        (item.title == expected_title, "title"),
        (
            item.description == body
            if finalized else item.description in (spec.title, body),
            "description",
        ),
        (
            item.dag_key == spec.dag_key
            if finalized else item.dag_key in ("", spec.dag_key),
            "dag_key",
        ),
        (
            item.worker == spec.assignee
            if finalized else item.worker in (None, spec.assignee),
            "worker",
        ),
        (
            item.kind == TaskKind.AMENDMENT
            if finalized else item.kind in (TaskKind.DEVELOP, TaskKind.AMENDMENT),
            "kind",
        ),
        (
            contract_matches
            if finalized else item.contract is None or contract_matches,
            "contract",
        ),
        (item.contract_ref is None or isinstance(item.contract_ref, dict), "contract_ref"),
        (
            item.source_refs == refs
            if finalized else item.source_refs in ([], refs),
            "source_refs",
        ),
        (
            item.amendment_attempt == spec.amendment_attempt
            if finalized else item.amendment_attempt in (None, spec.amendment_attempt),
            "amendment_attempt",
        ),
    ]
    activity = _pristine_amendment_activity_projection(item)
    if item.status != WorkItemStatus.TODO:
        activity["status"] = item.status
    if item.phase != TaskPhase.AUTHORING:
        activity["phase"] = item.phase
    return [name for matches, name in checks if not matches], sorted(activity)


def _attempt_follow_up(item_id: str) -> tuple[str, str]:
    next_action = f"omac work show {item_id} --output json"
    recovery = (
        "Inspect the persisted attempt with the next_action command. If it was "
        "already dispatched, continue it with the normal current-phase command "
        f"and --resume-issue-id {item_id}; do not repeat --new-attempt."
    )
    return next_action, recovery


def _observe_attempt_active(engine, item_id: str, dag_key: str) -> bool:
    try:
        return engine.runtime.is_active(item_id)
    except PlatformError as exc:
        next_action, recovery = _attempt_follow_up(item_id)
        raise NeedsDecision(
            "Could not prove whether the deterministic amendment attempt is active",
            report={
                "reason_code": "amendment-attempt-observation-failed",
                "item_id": item_id,
                "dag_key": dag_key,
                "detail": str(exc),
                "next_action": next_action,
                "recovery": recovery,
            },
        ) from exc


def _reject_active_attempt(item: WorkItem, dag_key: str) -> None:
    next_action, recovery = _attempt_follow_up(item.id)
    raise NeedsDecision(
        "The deterministic amendment attempt already has an active Agent Run",
        report={
            "reason_code": "amendment-attempt-shell-active",
            "item_id": item.id,
            "dag_key": dag_key,
            "next_action": next_action,
            "recovery": recovery,
        },
    )


def _reject_started_attempt(item: WorkItem, dag_key: str, fields: List[str]) -> None:
    next_action, recovery = _attempt_follow_up(item.id)
    raise NeedsDecision(
        "The deterministic amendment attempt is no longer an undispatched authoring shell",
        report={
            "reason_code": "amendment-attempt-already-started",
            "item_id": item.id,
            "dag_key": dag_key,
            "status": item.status.value,
            "phase": item.phase.value,
            "fields": sorted(set(fields)),
            "next_action": next_action,
            "recovery": recovery,
        },
    )


def _is_pristine_created_amendment_shell(
    engine, item: WorkItem, spec: AuthoringTaskSpec,
) -> bool:
    """Recognize an orphaned TODO shell with only platform auto-assignment."""
    if item.amendment_attempt is not None:
        return False
    body, refs = _authoring_materialization(engine, item.id, spec, item)
    identity_errors, activity_fields = _pristine_amendment_shell_errors(
        item, spec, body=body, refs=refs,
        workspace_id=engine.store.config.workspace_id, finalized=False)
    return not identity_errors and set(activity_fields) <= {"platform_assignee_id"}


def finalize_authoring_shell(
    engine, item: WorkItem, spec: AuthoringTaskSpec, *,
    allow_created_platform_assignee: bool = False,
) -> WorkItem:
    """幂等完成 deterministic amendment shell；完整前绝不派发。"""
    if spec.kind != TaskKind.AMENDMENT or spec.amendment_attempt is None:
        raise ValidationError("Deterministic shell finalization is amendment-only")
    if _observe_attempt_active(engine, item.id, spec.dag_key):
        _reject_active_attempt(item, spec.dag_key)
    body, refs = _authoring_materialization(engine, item.id, spec, item)
    identity_errors, activity_fields = _pristine_amendment_shell_errors(
        item, spec, body=body, refs=refs,
        workspace_id=engine.store.config.workspace_id, finalized=False)
    if allow_created_platform_assignee:
        activity_fields = [
            field for field in activity_fields
            if field != "platform_assignee_id"
        ]
    if activity_fields:
        _reject_started_attempt(item, spec.dag_key, activity_fields)
    if identity_errors:
        raise NeedsDecision(
            "Deterministic amendment shell cannot be safely finalized",
            report={
                "reason_code": "amendment-attempt-shell-conflict",
                "item_id": item.id,
                "dag_key": spec.dag_key,
                "fields": identity_errors,
            },
        )
    store = engine.store
    apply_one(SetAuthoringIdentity(item.id, spec.dag_key, TaskKind.AMENDMENT), Ports(store=store))
    if spec.contract is not None and item.contract is None:
        apply([
            SetNodeContract(item.id, spec.contract),
        ], Ports(store=store))
    apply([
        UpdateMetadata(item.id, {
            'worker': spec.assignee,
            'description': body,
            'source_refs': refs,
            'amendment_attempt': spec.amendment_attempt,
            'phase': TaskPhase.AUTHORING,
        }),
    ], Ports(store=store))
    finalized = store.get_work_item(item.id)
    if allow_created_platform_assignee and finalized.platform_assignee_id:
        apply([
            ClearAssignment(item.id),
        ], Ports(store=store))
        finalized = store.get_work_item(item.id)
    if _observe_attempt_active(engine, item.id, spec.dag_key):
        _reject_active_attempt(finalized, spec.dag_key)
    identity_errors, activity_fields = _pristine_amendment_shell_errors(
        finalized, spec, body=body, refs=refs,
        workspace_id=store.config.workspace_id, finalized=True)
    if allow_created_platform_assignee:
        activity_fields = [
            field for field in activity_fields
            if field != "platform_assignee_id"
        ]
    if activity_fields:
        _reject_started_attempt(finalized, spec.dag_key, activity_fields)
    if identity_errors:
        raise NeedsDecision(
            "Deterministic amendment shell finalization did not converge",
            report={
                "reason_code": "amendment-attempt-shell-incomplete",
                "item_id": item.id,
                "dag_key": spec.dag_key,
                "fields": identity_errors,
            },
        )
    return finalized


def refresh_authoring_task(
    engine,
    item_id: str,
    spec: AuthoringTaskSpec,
    *,
    item_snapshot: Optional[WorkItem] = None,
) -> WorkItem:
    """先覆盖紧凑正文再完整读取，允许修复旧巨型 issue。"""
    store = engine.store
    body, refs = _authoring_materialization(
        engine, item_id, spec, item_snapshot)
    if spec.contract is not None:
        apply([
            SetNodeContract(item_id, spec.contract),
        ], Ports(store=store))
    return apply_one(UpdateMetadata(item_id, {
        'worker': spec.assignee,
        'description': body,
        'source_refs': refs,
        'amendment_attempt': spec.amendment_attempt,
    }), Ports(store=store))


def _resume_reviewer_completed_without_verdict(
    engine, item: WorkItem, expected_kind: TaskKind | None = None,
) -> WorkItem:
    """Clear only the bounded reviewer no-submit decision on safe resume."""
    decision = item.decision_required
    if not (
        item.phase == TaskPhase.REVIEW
        and item.status == WorkItemStatus.BLOCKED
        and isinstance(decision, dict)
        and decision.get("schema") == DECISION_REQUIRED_SCHEMA
        and decision.get("reason_code") == "reviewer-completed-without-verdict"
        and decision.get("resume_issue_id") == item.id
        and decision.get("phase") == TaskPhase.REVIEW.value
        and decision.get("kind") == getattr(item.kind, "value", item.kind)
        and (
            expected_kind is None
            or decision.get("kind") == getattr(expected_kind, "value", expected_kind)
        )
    ):
        return item
    runs = engine.runtime.list_runs(item.id)
    unsafe_runs = [run for run in runs if run.active or not run.terminal]
    if unsafe_runs:
        raise NeedsDecision(
            "The Reviewer Run is still active or its terminal state is unknown; "
            "wait before resuming.",
            report={
                "item_id": item.id,
                "reason_code": "reviewer-resume-run-not-terminal",
                "run_ids": [run.id for run in unsafe_runs],
                "next_action": f"omac work show {item.id} --output json",
            },
        )
    cleared = apply_one(UpdateMetadata(item.id, {'decision_required': {}}), Ports(store=engine.store))
    if cleared.requires_decision:
        raise NeedsDecision(
            "The reviewer no-submit decision could not be cleared safely.",
            report={
                "item_id": item.id,
                "reason_code": "reviewer-resume-decision-not-cleared",
                "next_action": f"omac work show {item.id} --output json",
            },
        )
    if cleared.status != WorkItemStatus.IN_REVIEW:
        apply([
            UpdateStatus(item.id, WorkItemStatus.IN_REVIEW),
        ], Ports(store=engine.store))
    verified = engine.store.get_work_item(item.id)
    if (
        verified.decision_required not in (None, {})
        or verified.phase != TaskPhase.REVIEW
        or verified.status != WorkItemStatus.IN_REVIEW
    ):
        raise NeedsDecision(
            "The reviewer no-submit decision did not clear to a reviewable state.",
            report={
                "item_id": item.id,
                "reason_code": "reviewer-resume-readback-mismatch",
                "next_action": f"omac work show {item.id} --output json",
            },
        )
    return verified


def _is_unstarted_authoring(item: WorkItem) -> bool:
    """No delivery and no stopped Run on record: the issue may be refreshed in place when it is resumed."""
    return bool(
        item.status == WorkItemStatus.TODO
        and item.phase == TaskPhase.AUTHORING
        and not item.deliverable
        and not item.deliverable_ref
        and not item.agent_run_failed
        and not item.agent_run_finished_without_submit
    )


def _is_stopped_authoring_with_delivery(item: WorkItem) -> bool:
    """A delivery is already present and the authoring Run stopped: resuming it in place would be unsafe."""
    return bool(
        item.phase == TaskPhase.AUTHORING
        and item.deliverable
        and (
            item.status in (WorkItemStatus.FAILED, WorkItemStatus.BLOCKED)
            or item.agent_run_failed
            or item.agent_run_finished_without_submit
        )
    )


def _guard_resume_authoring_terminal_run(
    engine, item: WorkItem, kind: TaskKind,
) -> WorkItem:
    """Stop a resumed authoring issue whose prior Run ended without submit."""
    if not (
        item.phase == TaskPhase.AUTHORING
        and item.status == WorkItemStatus.IN_PROGRESS
        and not item.deliverable
        and not item.deliverable_ref
        and not item.agent_run_finished_without_submit
    ):
        return item
    try:
        runs = engine.runtime.list_runs(item.id)
    except PlatformError as exc:
        raise NeedsDecision(
            "Could not safely observe the resumed authoring Run; no new Run was started.",
            report={
                "item_id": item.id,
                "kind": kind.value,
                "reason_code": "resume-authoring-run-observation-failed",
                "detail": str(exc),
                "next_action": f"omac work show {item.id} --output json",
            },
        ) from exc
    direct_runs = [run for run in runs if run.kind == "direct"]
    unsafe_runs = [run for run in direct_runs if run.active or not run.terminal]
    if unsafe_runs:
        raise NeedsDecision(
            "The resumed authoring Run is active or its terminal state is unknown; "
            "no duplicate Run was started.",
            report={
                "item_id": item.id,
                "kind": kind.value,
                "reason_code": "resume-authoring-run-not-terminal",
                "run_ids": [run.id for run in unsafe_runs],
                "next_action": f"omac work show {item.id} --output json",
            },
        )
    if not direct_runs:
        return item
    latest = max(
        direct_runs,
        key=lambda run: run.updated_at or run.created_at or "",
    )
    if not latest.terminal:
        raise NeedsDecision(
            "The resumed authoring Run has no explicit terminal result; "
            "no duplicate Run was started.",
            report={
                "item_id": item.id,
                "kind": kind.value,
                "reason_code": "resume-authoring-run-not-terminal",
                "run_ids": [latest.id],
                "next_action": f"omac work show {item.id} --output json",
            },
        )
    # A resumed Run can also submit between the input snapshot and list_runs.
    item = engine.store.get_work_item(item.id)
    if _produced(item):
        return item
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": "completed-without-submit",
        "kind": kind.value,
        "phase": TaskPhase.AUTHORING.value,
        "resume_issue_id": item.id,
        "run_id": latest.id,
        "next_action": f"omac node retry <manifest> <node>",
    }
    apply(block_in_phase(item.id, decision, TaskPhase.AUTHORING), Ports(store=engine.store))
    raise NeedsDecision(
        "The previous authoring Run ended without submitting a deliverable; "
        "no duplicate Run was started.",
        report={**decision, "outcome": "unknown_partial"},
    )


@dataclass
class _Stop:
    """Why the run stops with NeedsDecision; the exception is built after the table has blocked the issue."""

    kind: str                  # "guard" | "review" | "convergence"
    gate: str
    rounds: int
    opinion: Optional[str]
    logged: bool = False       # the needs-decision event was already logged before the first effect
    decision: Optional[Dict[str, Any]] = None


class _TaskRun:
    """One synchronous authoring task: create or resume, produce, machine gate, bounded review loop.

    State that used to live in closures is explicit: ``item_id``, ``delivered``/``delivery`` and the
    resume flags. Every durable effect still goes through the store/runtime ports in the same order.
    """

    def __init__(
        self, engine, kind, payload, assignee, *, reviewers, max_revisions, poll, guard, confirm,
        pause_at_confirmation, source_refs, dag_key, resume_item_id, amendment_attempt,
        reuse_dag_key, review_acceptance_doc, review_amendment_manifest,
    ):
        self.engine = engine
        self.store = engine.store
        self.runtime = engine.runtime
        self.kind = kind
        self.assignee = assignee
        self.reviewers = reviewers
        self.max_revisions = max_revisions
        self.poll = poll
        self.guard = guard
        self.confirm = confirm
        self.pause_at_confirmation = pause_at_confirmation
        self.resume_item_id = resume_item_id
        self.amendment_attempt = amendment_attempt
        self.reuse_dag_key = reuse_dag_key
        self.review_acceptance_doc = review_acceptance_doc
        self.review_amendment_manifest = review_amendment_manifest
        title = payload.get("title") or f"{kind.value} task"
        self.task_key = dag_key or make_dag_key(kind, title=title, unique=True)
        self.contract = _payload_contract(payload.get("contract"))
        self.spec = AuthoringTaskSpec(
            kind=kind,
            title=title,
            dag_key=self.task_key,
            assignee=assignee,
            description=payload.get("description") or "",
            contract=self.contract,
            source_refs=list(source_refs or []),
            source_of_truth=payload.get("source_of_truth") or {},
            amendment_attempt=amendment_attempt,
        )
        self.reused_created_item = False
        self.created_after_ambiguous_failure = False
        self.item_id = ""
        self.explicit_resume = False
        self.resume_authoring_attempt_available = False
        self.pristine_dispatch_required = False
        self.resumed_confirmation: WorkItem | None = None
        self.delivered: WorkItem | None = None
        self.delivery: Dict[str, Any] = {}
        self.ports = Ports(store=self.store, runtime=self.runtime)
        self.reviewed: WorkItem | None = None
        self.reviewer = ""
        self.subject_digest = ""
        self.round_index = 0
        self.next_round = 1
        self.review_limit = 0
        self.gate_round = 0
        self.gate_errors: List[str] = []
        self.last_opinion: Optional[str] = None
        self.stop: Optional[_Stop] = None
        self.finish_args: tuple[str, int, bool] = ("pass", 0, True)
        self.refresh_after_effects = False
        self.result: Dict[str, Any] = {}
        self.on_done: Optional[Callable[[], None]] = None

    def _open_item(self) -> WorkItem:
        if self.resume_item_id is not None:
            # Store 当前事实是 resume 的唯一授权来源。调用方 snapshot 仅为兼容保留，
            # 绝不能在读取失败时授权 refresh 或其他副作用。
            item = self.store.get_work_item(self.resume_item_id)
            if self.kind == TaskKind.AMENDMENT:
                self.contract = _validate_amendment_resume_contract(item, self.contract)
                self.spec.contract = self.contract
            item = _resume_reviewer_completed_without_verdict(self.engine, item, self.kind)
            item = _guard_resume_authoring_terminal_run(self.engine, item, self.kind)
            if _is_unstarted_authoring(item):
                item = refresh_authoring_task(
                    self.engine, item.id, self.spec, item_snapshot=item)
            self.item_id = item.id
        else:
            try:
                item = (
                    self.store.find_work_item_by_dag_key(self.store.config.workspace_id, self.task_key)
                    if self.reuse_dag_key else None
                )
            except PlatformError as exc:
                raise NeedsDecision(
                    "Could not safely observe the deterministic amendment attempt identity",
                    report={
                        "reason_code": "amendment-attempt-observation-failed",
                        "dag_key": self.task_key,
                        "detail": str(exc),
                        "next_action": "omac work show <attempt-issue-id> --output json",
                        "recovery": (
                            "Restore Store visibility and inspect the existing attempt before "
                            "retrying; do not create or dispatch while its identity is unknown."
                        ),
                    },
                ) from exc
            self.reused_created_item = item is not None
            if item is None:
                try:
                    item = create_authoring_task(self.engine, self.spec)
                except PlatformError:
                    if not self.reuse_dag_key:
                        raise
                    try:
                        item = self.store.find_work_item_by_dag_key(
                            self.store.config.workspace_id, self.task_key)
                    except PlatformError as observation_exc:
                        raise NeedsDecision(
                            "Issue creation was ambiguous and the deterministic attempt could not be observed",
                            report={
                                "reason_code": "amendment-attempt-observation-failed",
                                "dag_key": self.task_key,
                                "detail": str(observation_exc),
                                "next_action": (
                                    "omac work show <attempt-issue-id> --output json"),
                                "recovery": (
                                    "Restore Store visibility and inspect the existing attempt; "
                                    "do not create or dispatch while creation is ambiguous."
                                ),
                            },
                        ) from observation_exc
                    if item is None:
                        raise
                    self.reused_created_item = True
                    self.created_after_ambiguous_failure = True
            if self.reused_created_item:
                allow_platform_assignee = (
                    self.created_after_ambiguous_failure
                    or _is_pristine_created_amendment_shell(self.engine, item, self.spec)
                )
                item = finalize_authoring_shell(
                    self.engine,
                    item,
                    self.spec,
                    allow_created_platform_assignee=allow_platform_assignee,
                )
            self.item_id = item.id
        return item

    def _check_resume(self, item: WorkItem) -> None:
        self.explicit_resume = self.resume_item_id is not None or self.reused_created_item
        self.resume_authoring_attempt_available = self.explicit_resume
        self.pristine_dispatch_required = self.amendment_attempt is not None
        self.resumed_confirmation: WorkItem | None = None

        if self.resume_item_id is not None and item.phase == TaskPhase.CONFIRMATION:
            confirmation_errors = _confirmation_resume_errors(self.kind, self.contract, item)
            if confirmation_errors:
                next_action, recovery = _new_attempt_recovery(self.kind, item.id)
                raise NeedsDecision(
                    ui(
                        f"{self.kind.value} confirmation cannot be consumed from current "
                        f"Store facts (item {self.item_id})",
                        f"{self.kind.value} confirmation 无法基于当前 Store 事实安全消费"
                        f"（item {self.item_id}）",
                    ),
                    report={
                        "reason_code": "confirmation-not-consumable",
                        "item_id": self.item_id,
                        "kind": self.kind.value,
                        "phase": TaskPhase.CONFIRMATION.value,
                        "rounds": 0,
                        "last_opinion": "; ".join(confirmation_errors),
                        "errors": confirmation_errors,
                        "next_action": next_action,
                        "recovery": recovery,
                    },
                )
            self.resumed_confirmation = item

        if (
            self.resume_item_id is not None
            and self.kind == TaskKind.AMENDMENT
            and item.phase == TaskPhase.REVIEW
            and item.review_verdict == "pass-with-nits"
        ):
            # A reviewer may have submitted the final nits verdict just before the
            # process stopped, leaving the issue in review rather than confirmation.
            # Recover that same sealed delivery in place; never redispatch either
            # role merely to manufacture a reviewed amendment file.
            self.resumed_confirmation = _resume_amendment_nits_confirmation(
                self.engine, item, self.contract)

        if self.resume_item_id is not None and _is_stopped_authoring_with_delivery(item):
            next_action, recovery = _new_attempt_recovery(self.kind, item.id)
            raise NeedsDecision(
                ui(
                    f"{self.kind.value} authoring cannot resume a stopped run while a "
                    f"deliverable is already present (item {self.item_id})",
                    f"{self.kind.value} authoring 已有交付物且原 Run 已停止，不能直接恢复"
                    f"（item {self.item_id}）",
                ),
                report={
                    "reason_code": "unsafe-authoring-resume",
                    "item_id": self.item_id,
                    "kind": self.kind.value,
                    "phase": TaskPhase.AUTHORING.value,
                    "rounds": 0,
                    "last_opinion": (
                        "stopped authoring run already has a persisted deliverable"),
                    "next_action": next_action,
                    "recovery": recovery,
                },
            )

    def _raise_if_authoring_stopped(self, candidate: WorkItem) -> None:
        if (self.kind == TaskKind.AMENDMENT and candidate.decision_required
                and candidate.decision_required.get("reason_code") == "amendment-scope-gap"):
            raise NeedsDecision("Planner submitted a terminal assessment scope/source gap",
                                report=candidate.decision_required)
        if candidate.phase != TaskPhase.AUTHORING:
            return
        if candidate.status not in (WorkItemStatus.FAILED, WorkItemStatus.BLOCKED):
            return
        outcome = (
            "blocked" if candidate.status == WorkItemStatus.BLOCKED
            else "failed"
        )
        log.info(logsetup.EVT_NODE_FAILED, kind=self.kind.value, id=self.item_id,
                 reason=f"producer {outcome}")
        raise NeedsDecision(
            ui(
                f"{self.kind.value} authoring {outcome} (item {self.item_id})",
                f"{self.kind.value} 产出阶段{'被阻塞' if outcome == 'blocked' else '失败'}"
                f"(item {self.item_id})"),
            report={"item_id": self.item_id, "kind": self.kind.value, "rounds": 0,
                    "last_opinion": f"producer {outcome}"})

    def _raise_completed_without_submit(self, *, reason_code: str = "completed-without-submit", role: str = "worker",
    ) -> None:
        decision_phase = (
            TaskPhase.REVIEW if role == "reviewer" else TaskPhase.AUTHORING
        )
        decision = {
            "schema": DECISION_REQUIRED_SCHEMA,
            "reason_code": reason_code,
            "kind": self.kind.value,
            "phase": decision_phase.value,
            "resume_issue_id": self.item_id,
        }
        apply(block_in_phase(self.item_id, decision, decision_phase), Ports(store=self.store))
        log.info(
            logsetup.EVT_NEEDS_DECISION,
            kind=self.kind.value,
            id=self.item_id,
            gate=("review-verdict" if role == "reviewer" else "authoring-submit"),
            rounds=0,
        )
        raise NeedsDecision(
            ui(
                f"{self.kind.value} Agent run completed without a fresh structured deliverable (item {self.item_id})",
                f"{self.kind.value} Agent Run 已结束但没有提交新的结构化交付物（item {self.item_id}）"),
            report={
                "item_id": self.item_id,
                "kind": self.kind.value,
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

    def _wait_for_dispatched_run(self, *,
        baseline_ids: set[str],
        predicate: Callable[[WorkItem], bool],
        role: str,
    ) -> tuple[WorkItem, Optional[str]]:
        invisible_polls = 0
        observed_run_id = None
        while True:
            current = self.store.get_work_item(self.item_id)
            visible_runs = self.runtime.list_runs(self.item_id)
            active_non_direct = [
                run for run in visible_runs
                if run.kind != "direct" and run.active
            ]
            if active_non_direct:
                raise NeedsDecision(
                    ui(
                        "A non-direct Agent Run became active during direct dispatch",
                        "direct 派发期间出现了活跃的非 direct Agent Run"),
                    report={
                        "item_id": self.item_id,
                        "reason_code": "dispatch-run-race",
                        "run_ids": [run.id for run in active_non_direct],
                        "outcome": "unknown_partial",
                    },
                )
            new_direct = [
                run for run in visible_runs
                if run.kind == "direct" and run.id not in baseline_ids
            ]
            if len(new_direct) > 1:
                raise NeedsDecision(
                    ui(
                        "Multiple new direct Runs appeared for one dispatch",
                        "一次派发出现了多个新的 direct Run"),
                    report={
                        "item_id": self.item_id,
                        "reason_code": "dispatch-multiple-runs",
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
                if predicate(current):
                    return current, observed_run_id
                if run.terminal:
                    # The submission can land while list_runs is in flight.
                    # Never overwrite it using the earlier WorkItem snapshot.
                    current = self.store.get_work_item(self.item_id)
                    if predicate(current):
                        return current, observed_run_id
                    self._raise_if_authoring_stopped(current)
                    if role == "reviewer":
                        _raise_if_reviewer_dispatch_stopped(
                            self.store, self.item_id, self.kind)
                    self._raise_completed_without_submit(
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
                    raise NeedsDecision(
                        ui(
                            "The dispatched Agent Run is still not visible; dispatch outcome is unknown",
                            "已派发的 Agent Run 仍不可见；派发结果未知"),
                        report={
                            "item_id": self.item_id,
                            "reason_code": "dispatch-run-not-observed",
                            "role": role,
                            "outcome": "unknown_partial",
                        },
                    )
            self.poll()

    def _verify_pristine_attempt_before_dispatch(self) -> None:
        if not self.pristine_dispatch_required:
            return
        # CLI 持有同 manifest 的 host-local 锁；这里再用 active observation
        # 夹住最后一次 Store 重读，只消费当前可见事实。Multica 外部直写没有
        # conditional CAS，不能把此观察宣称为跨平台 compare-and-dispatch。
        current = self.store.get_work_item(self.item_id)
        if _observe_attempt_active(self.engine, self.item_id, self.task_key):
            _reject_active_attempt(current, self.task_key)
        current = self.store.get_work_item(self.item_id)
        body, refs = _authoring_materialization(
            self.engine, self.item_id, self.spec, current)
        identity_errors, activity_fields = _pristine_amendment_shell_errors(
            current, self.spec, body=body, refs=refs,
            workspace_id=self.store.config.workspace_id, finalized=True)
        if activity_fields:
            _reject_started_attempt(current, self.task_key, activity_fields)
        if identity_errors:
            raise NeedsDecision(
                "Deterministic amendment shell changed before dispatch",
                report={
                    "reason_code": "amendment-attempt-shell-incomplete",
                    "item_id": self.item_id,
                    "dag_key": self.task_key,
                    "fields": identity_errors,
                },
            )
        self.pristine_dispatch_required = False

    def _dispatch_authoring_and_wait(self) -> WorkItem:
        self._verify_pristine_attempt_before_dispatch()
        baseline = None
        if self.runtime.capabilities.stable_direct_run_identity:
            baseline = {
                run.id for run in self.runtime.list_runs(self.item_id)
                if run.kind == "direct"
            }
        apply([
            UpdateStatus(self.item_id, WorkItemStatus.IN_PROGRESS),
            Assign(self.item_id, self.assignee, "worker"),
            Wake(self.item_id, self.assignee, "worker"),
        ], Ports(store=self.store, runtime=self.runtime))
        log.info(
            logsetup.EVT_DISPATCH,
            kind=self.kind.value,
            id=self.item_id,
            worker=self.assignee,
        )
        if baseline is not None:
            produced, _run_id = self._wait_for_dispatched_run(
                baseline_ids=baseline,
                predicate=_produced,
                role="worker",
            )
            self._raise_if_authoring_stopped(produced)
            return produced
        produced = _poll_until(
            self.store,
            self.item_id,
            lambda candidate: (
                _produced(candidate)
                or candidate.agent_run_finished_without_submit
            ),
            self.poll,
        )
        if produced.agent_run_finished_without_submit and not _produced(produced):
            self._raise_completed_without_submit()
        self._raise_if_authoring_stopped(produced)
        return produced

    def _produce(self, hint: Optional[List[str]] = None) -> WorkItem:

        current = self.store.get_work_item(self.item_id)
        if (
            hint is None
            and self.resume_authoring_attempt_available
            and current.phase == TaskPhase.AUTHORING
        ):
            if self.runtime.is_active(self.item_id):
                current = _poll_until(
                    self.store,
                    self.item_id,
                    lambda candidate: (
                        candidate.phase != TaskPhase.AUTHORING
                        or candidate.status in (
                            WorkItemStatus.DONE,
                            WorkItemStatus.FAILED,
                            WorkItemStatus.BLOCKED,
                        )
                        or candidate.agent_run_finished_without_submit
                    ),
                    self.poll,
                )
                if current.phase != TaskPhase.AUTHORING or (
                    current.status == WorkItemStatus.DONE
                ):
                    self.resume_authoring_attempt_available = False
                    return current
            if (
                current.status == WorkItemStatus.FAILED
                or current.agent_run_finished_without_submit
            ):
                self.resume_authoring_attempt_available = False
                return self._dispatch_authoring_and_wait()
        self._raise_if_authoring_stopped(current)
        if hint is None and _produced(current):
            return current
        if hint:
            self._persist_machine_feedback(hint)
        return self._dispatch_authoring_and_wait()

    def _persist_machine_feedback(self, errors: List[str]) -> None:
        feedback = build_machine_feedback("machine-gate", errors)
        apply(plan_machine_feedback(
            self.item_id, feedback, machine_feedback_summary(self.item_id, feedback)), self.ports)

    def _amendment_review_evidence(self, candidate: WorkItem) -> dict[str, str]:
        if self.kind != TaskKind.AMENDMENT or self.review_amendment_manifest is None:
            return {}
        from ..core.amendment import (
            _historical_contract_corrections, parse_proposal,
            historical_work_item_evidence_digest,
        )

        proposal = parse_proposal(candidate.deliverable or "")
        evidence = {}
        for correction in _historical_contract_corrections(
                self.review_amendment_manifest, proposal):
            node = self.review_amendment_manifest.nodes[correction["node"]]
            if not node.work_item_id:
                raise ValidationError(
                    f"historical contract correction node {node.id} requires a work item for evidence CAS")
            # Reviewer obligations must bind the same expanded Store facts
            # that reviewed amendment construction and apply verify consume.
            evidence[node.id] = historical_work_item_evidence_digest(
                self.store.get_work_item(node.work_item_id))
        return evidence

    # ── stage handlers: observe, then name what was seen. The table (decide_task) decides what follows. ──────

    def run(self) -> Dict[str, Any]:
        handlers = {
            Stage.OPEN: self._at_open, Stage.CHECK_RESUME: self._at_check_resume,
            Stage.PRODUCE: self._at_produce, Stage.RESOLVE_INITIAL: self._at_resolve_initial,
            Stage.PRODUCE_REWORK: self._at_produce, Stage.MACHINE_GATE: self._at_machine_gate,
            Stage.PRODUCE_HINTED: self._at_produce_hinted, Stage.RESUME_REVIEW: self._at_resume_review,
            Stage.REVIEWER_CHECK: self._at_reviewer_check, Stage.REVIEW_ENTER: self._at_review_enter,
            Stage.REVIEW_ROUND: self._at_review_round, Stage.REVIEW_CHECK: self._at_review_check,
            Stage.REVIEW_DISPATCH: self._at_review_dispatch, Stage.REVIEW_VERDICT: self._at_review_verdict,
            Stage.PRODUCE_NITS: self._at_produce_nits, Stage.PRODUCE_REJECT: self._at_produce_reject,
            Stage.BLOCK_BUDGET: self._at_block_budget, Stage.FINISH: self._at_finish,
            Stage.FINISH_WAIT: self._at_finish_wait,
        }
        stage = Stage.OPEN
        while stage not in TERMINAL:
            fact, obs = handlers[stage]()
            transition = decide_task_step(stage, fact, obs)
            results = apply(transition.effects, self.ports)
            if stage is Stage.REVIEW_ROUND or fact is Fact.EVIDENCE_INVALID:
                self.reviewed = results[-1]  # PrepareReviewCycle / the metadata write return the current item
            stage = transition.next
        if stage is Stage.STOPPED:
            raise self._stop_error()
        if self.on_done is not None:
            self.on_done()
        return self.result

    def _obs(self, **fields) -> TaskObs:
        return TaskObs(item_id=self.item_id, round_index=self.round_index, **fields)

    def _at_open(self):
        self._item = self._open_item()
        return Fact.OPENED, self._obs()

    def _at_check_resume(self):
        self._check_resume(self._item)
        if self.resumed_confirmation is None:
            return Fact.FRESH, self._obs()
        self.delivered = self.resumed_confirmation
        self.delivery = _delivery_of(self.kind, self.delivered)
        return Fact.CONFIRMATION_RESUMED, self._obs()

    def _at_produce(self):
        self.delivered = self._produce()
        self.delivery = _delivery_of(self.kind, self.delivered)
        return Fact.PRODUCED, self._obs()

    def _at_resolve_initial(self):
        if (
            self.delivered.status == WorkItemStatus.DONE
            and self.delivered.review_verdict in {"pass", "pass-with-nits"}
            and not (self.confirm and self.delivered.phase == TaskPhase.REVIEW)
        ):
            log.info(logsetup.EVT_NODE_DONE, kind=self.kind.value, id=self.item_id)
            self.result = {"item_id": self.item_id, "delivery": self.delivery,
                           "rounds": 0, "verdict": self.delivered.review_verdict,
                           "kind": self.kind.value}
            return Fact.ALREADY_PASSED, self._obs()
        initial_review_limit = authorized_review_limit(self.delivered, self.max_revisions)
        if (
            self.reviewers
            and _rejected_verdict_was_counted(self.kind, self.delivered)
            and self.delivered.bounces.review < initial_review_limit
        ):
            return Fact.COUNTED_REJECT_RETRY, self._obs()
        return Fact.PROCEED, self._obs()

    def _at_machine_gate(self):
        if self.resumed_confirmation is not None or not (self.guard is not None or self.reviewers):
            return Fact.GATE_CLEAR, self._obs()
        if self.gate_round < self.max_revisions:
            errors: List[str] = self.guard(self.delivered) if self.guard is not None else []
            if self.reviewers:
                errors.extend(run_review_preflight(self.delivered))
            errors = list(dict.fromkeys(errors))
            if not errors:
                return Fact.GATE_CLEAR, self._obs()
            self.gate_round += 1
            self.gate_errors = errors
            log.info(logsetup.EVT_REVISION, kind=self.kind.value, id=self.item_id,
                     gate="guard", round=self.gate_round, max=self.max_revisions)
            return Fact.GATE_RETRY, self._obs()
        log.info(logsetup.EVT_NEEDS_DECISION, kind=self.kind.value, id=self.item_id,
                 gate="guard", rounds=self.max_revisions)
        self.stop = _Stop("guard", "guard", self.max_revisions, "\n".join(self.gate_errors), logged=True)
        # The producer's submit clears consumed feedback; once the budget is spent the full problem list is
        # persisted again so the operator and the Author can read it.
        feedback = build_machine_feedback("machine-gate", self.gate_errors)
        return Fact.GATE_EXHAUSTED, self._obs(
            feedback=feedback, feedback_summary=machine_feedback_summary(self.item_id, feedback))

    def _at_produce_hinted(self):
        self.delivered = self._produce(hint=self.gate_errors)
        self.delivery = _delivery_of(self.kind, self.delivered)
        return Fact.PRODUCED, self._obs()

    def _at_resume_review(self):
        delivered = self.delivered
        if (
            delivered.phase == TaskPhase.REVIEW
            and delivered.review_verdict == "pass"
            and delivered.status in {WorkItemStatus.IN_REVIEW, WorkItemStatus.DONE}
            and not _review_evidence_errors(self.contract, delivered)
        ):
            self.finish_args = ("pass", max(1, delivered.bounces.review + 1), True)
            return Fact.FINISH_PASS, self._obs()
        if delivered.phase != TaskPhase.CONFIRMATION:
            return Fact.CONTINUE, self._obs()
        confirmation_round = max(1, delivered.bounces.review + 1)
        if self.resumed_confirmation is not None:
            self.finish_args = (delivered.review_verdict or "pass", confirmation_round, False)
            return Fact.FINISH_CONFIRMED, self._obs()
        current_subject = _review_subject_digest(self.kind, delivered, confirmation_round)
        if not (_has_review_verdict(delivered) and delivered.review_subject_digest == current_subject):
            # confirmation only consumes a verdict still bound to the current delivery; a new delivery after
            # pass-with-nits cannot be vouched for by the old verdict.
            self.refresh_after_effects = True
            return Fact.STALE_CONFIRMATION, self._obs()
        evidence_errors = _review_evidence_errors(self.contract, delivered)
        if not evidence_errors:
            self.finish_args = (delivered.review_verdict or "pass", confirmation_round, False)
            return Fact.FINISH_CONFIRMED, self._obs()
        log.info(logsetup.EVT_REVISION, kind=self.kind.value, id=self.item_id,
                 gate="review-evidence", round=confirmation_round,
                 max=authorized_review_limit(delivered, self.max_revisions))
        self.delivered = _restart_invalid_review(
            self.store, self.item_id, current_subject, evidence_errors)
        return Fact.CONTINUE, self._obs()

    def _at_reviewer_check(self):
        if self.refresh_after_effects:
            self.refresh_after_effects = False
            self.delivered = self.store.get_work_item(self.item_id)
        if self.reviewers:
            return Fact.HAS_REVIEWERS, self._obs()
        self.finish_args = ("pass", 0, True)
        return Fact.NO_REVIEWERS, self._obs()

    def _at_review_enter(self):
        review_bounce = max(0, self.delivered.bounces.review)
        self.review_limit = authorized_review_limit(self.delivered, self.max_revisions)
        self.next_round = review_bounce + 1
        if review_bounce < self.review_limit:
            return Fact.ROUNDS_REMAIN, self._obs()
        self.stop = _Stop("review", "review", review_bounce, _review_opinion(self.delivered))
        return Fact.BUDGET_EXHAUSTED, self._obs()

    def _at_review_round(self):
        if self.next_round > self.review_limit:
            raise AssertionError("review loop exhausted without a terminal verdict")
        self.round_index = self.next_round
        self.reviewer = _pick_reviewer(self.reviewers, self.assignee, self.round_index - 1)
        self.subject_digest = _review_subject_digest(self.kind, self.delivered, self.round_index)
        current = self.store.get_work_item(self.item_id)
        if current.review_subject_digest == self.subject_digest:
            return Fact.PREPARE, self._obs(subject_digest=self.subject_digest)
        obligations = build_review_obligations(
            current,
            acceptance_doc=self.review_acceptance_doc,
            amendment_manifest=self.review_amendment_manifest,
            amendment_evidence=self._amendment_review_evidence(current))
        return Fact.PREPARE_FRESH_OBLIGATIONS, self._obs(
            subject_digest=self.subject_digest, obligations=obligations)

    def _at_review_check(self):
        if not _has_review_verdict(self.reviewed):
            return Fact.NEEDS_DISPATCH, self._obs()
        evidence_errors = _review_evidence_errors(self.contract, self.reviewed)
        if not evidence_errors:
            return Fact.VERDICT_READY, self._obs()
        log.info(logsetup.EVT_REVISION, kind=self.kind.value, id=self.item_id,
                 gate="review-evidence", round=self.round_index, max=self.review_limit)
        feedback = build_machine_feedback("review-evidence", evidence_errors)
        return Fact.EVIDENCE_INVALID, self._obs(
            subject_digest=self.subject_digest, feedback=feedback,
            feedback_summary=machine_feedback_summary(self.item_id, feedback))

    def _at_review_dispatch(self):
        _raise_if_reviewer_dispatch_stopped(self.store, self.item_id, self.kind)
        reviewer_baseline = None
        if self.runtime.capabilities.stable_direct_run_identity:
            reviewer_baseline = {
                run.id for run in self.runtime.list_runs(self.item_id) if run.kind == "direct"
            }
        _raise_if_reviewer_dispatch_stopped(self.store, self.item_id, self.kind)
        dispatched = apply_one(DispatchReviewer(self.item_id, self.reviewer), self.ports)
        if not dispatched:
            _raise_if_reviewer_dispatch_stopped(self.store, self.item_id, self.kind)
        log.info(logsetup.EVT_REVIEW_DISPATCH, kind=self.kind.value, id=self.item_id,
                 reviewer=self.reviewer)
        if reviewer_baseline is not None:
            self.reviewed, _run_id = self._wait_for_dispatched_run(
                baseline_ids=reviewer_baseline, predicate=_has_review_verdict, role="reviewer")
        else:
            self.reviewed = _poll_until(self.store, self.item_id, _has_review_verdict, self.poll)
        return Fact.REVIEWED, self._obs()

    def _at_review_verdict(self):
        reviewed, round_index = self.reviewed, self.round_index
        verdict = reviewed.review_verdict
        log.info(logsetup.EVT_VERDICT, kind=self.kind.value, id=self.item_id,
                 verdict=verdict, round=round_index)
        if verdict == "pass":
            self.finish_args = ("pass", round_index, True)
            return Fact.PASS, self._obs()
        if verdict == "reject":
            resolution = resolve_convergence(
                reviewed, kind=self.kind.value, recommended_action="reconsider-task-boundary")
            resolution.raise_if_invalid(PlatformError, self.item_id)
            if resolution.state is ResolutionState.NEEDS_DECISION:
                return self._convergence_stop(reviewed, resolution)
        if verdict == "pass-with-nits":
            if self.kind == TaskKind.AMENDMENT:
                # A running-DAG amendment is already a reviewed control-plane change: the operator must
                # explicitly accept the nits, then run `dag amend accept`.
                self.finish_args = ("pass-with-nits", round_index, True)
                return Fact.AMENDMENT_NITS, self._obs()
            log.info(logsetup.EVT_REVISION, kind=self.kind.value, id=self.item_id,
                     gate="review-nits", round=round_index, max=self.review_limit)
            return Fact.NITS_RETRY, self._obs()
        # reject: the report is already in metadata; reset_review clears only the current verdict and returns the
        # item to the producer. Rework context comes from `work show`, never from a comment (it would trigger a Run).
        self.last_opinion = _review_opinion(reviewed)
        log.info(logsetup.EVT_REVISION, kind=self.kind.value, id=self.item_id,
                 gate="review", round=round_index, max=self.review_limit)
        if round_index >= self.review_limit:
            self.stop = _Stop("review", "review", round_index, self.last_opinion)
            return Fact.REJECT_EXHAUSTED, self._obs()
        return Fact.REJECT_RETRY, self._obs()

    def _convergence_stop(self, reviewed, resolution):
        decision = bounded_decision_required(resolution.decision)
        log.info(
            logsetup.EVT_NEEDS_DECISION, kind=self.kind.value, id=self.item_id, gate="review-convergence",
            rounds=resolution.convergence["cycle_count"], mode=resolution.convergence["mode"])
        self.stop = _Stop("convergence", "review-convergence", 0, None, decision=decision)
        return Fact.CONVERGENCE_STOP, self._obs(
            decision=decision,
            decision_current=reviewed.decision_required == decision and reviewed.phase == TaskPhase.REVIEW,
            already_blocked=reviewed.status == WorkItemStatus.BLOCKED)

    def _at_produce_nits(self):
        self.delivered = self._produce()
        self.delivery = _delivery_of(self.kind, self.delivered)
        if self.round_index >= self.review_limit:
            self.stop = _Stop("review", "review-nits", self.round_index, _review_opinion(self.reviewed))
            return Fact.PRODUCED_AT_LIMIT, self._obs()
        # Whatever the size of the change, the final delivery is reviewed again: the next round's
        # prepare_review_cycle clears the old verdict with the new subject digest.
        self.next_round = self.round_index + 1
        return Fact.PRODUCED, self._obs()

    def _at_produce_reject(self):
        self.delivered = self._produce()
        self.delivery = _delivery_of(self.kind, self.delivered)
        self.next_round = self.round_index + 1
        return Fact.PRODUCED, self._obs()

    def _at_block_budget(self):
        stop = self.stop
        if not stop.logged:
            log.info(logsetup.EVT_NEEDS_DECISION, kind=self.kind.value, id=self.item_id,
                     gate=stop.gate, rounds=stop.rounds)
        current = self.store.get_work_item(self.item_id)
        decision = _budget_exhausted_decision(self.kind, current, gate=stop.gate, rounds=stop.rounds)
        return Fact.OBSERVED, self._obs(decision=decision)

    def _stop_error(self) -> NeedsDecision:
        stop = self.stop
        if stop.kind == "convergence":
            return NeedsDecision(
                ui(f"{self.kind.value} review is not converging within the current task boundary.",
                   f"{self.kind.value} 评审无法在当前任务边界内收敛。"),
                report=stop.decision)
        if stop.kind == "guard":
            return NeedsDecision(
                ui(f"{self.kind.value} did not pass the machine gate after {self.max_revisions} revisions",
                   f"{self.kind.value} 任务经 {self.max_revisions} 轮 machine-gate 仍未通过"),
                report={"item_id": self.item_id, "kind": self.kind.value,
                        "rounds": self.max_revisions, "phase": "guard", "last_opinion": stop.opinion})
        blocked = self.store.get_work_item(self.item_id)
        extra = {} if stop.gate == "review" else {"gate": stop.gate}
        return _review_exhausted_error(self.kind, blocked, stop.rounds, stop.opinion, **extra)

    def _at_finish(self):
        verdict, rounds, prepare = self.finish_args
        current = self.store.get_work_item(self.item_id)
        obs = self._obs(
            stale_feedback=bool(current.review_comment or current.machine_feedback_ref),
            prepare_confirmation=prepare)
        if not self.confirm:
            self.result = {"item_id": self.item_id, "delivery": self.delivery,
                           "rounds": rounds, "verdict": verdict, "kind": self.kind.value}
            self.on_done = lambda: log.info(logsetup.EVT_NODE_DONE, kind=self.kind.value, id=self.item_id)
            return Fact.UNCONFIRMED, obs
        if self.pause_at_confirmation:
            self.result = {"item_id": self.item_id, "delivery": self.delivery, "rounds": rounds,
                           "verdict": verdict, "kind": self.kind.value, "pending_confirmation": True}
            self.on_done = lambda: log.info(
                logsetup.EVT_HUMAN_GATE_WAIT, kind=self.kind.value, id=self.item_id)
            return Fact.CONFIRM_PAUSE, obs
        return Fact.CONFIRM_WAIT, obs

    def _at_finish_wait(self):
        verdict, rounds, _prepare = self.finish_args
        log.info(logsetup.EVT_HUMAN_GATE_WAIT, kind=self.kind.value, id=self.item_id)
        confirmed = _poll_until(
            self.store, self.item_id, lambda i: i.status == WorkItemStatus.DONE, self.poll)
        log.info(logsetup.EVT_NODE_DONE, kind=self.kind.value, id=self.item_id)
        self.result = {"item_id": self.item_id, "delivery": _delivery_of(self.kind, confirmed),
                       "rounds": rounds, "verdict": verdict, "kind": self.kind.value}
        return Fact.CONFIRMED, self._obs()


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
    return _TaskRun(
        engine, kind, payload, assignee, reviewers=reviewers, max_revisions=max_revisions, poll=poll,
        guard=guard, confirm=confirm, pause_at_confirmation=pause_at_confirmation,
        source_refs=source_refs, dag_key=dag_key, resume_item_id=resume_item_id,
        amendment_attempt=amendment_attempt, reuse_dag_key=reuse_dag_key,
        review_acceptance_doc=review_acceptance_doc,
        review_amendment_manifest=review_amendment_manifest,
    ).run()

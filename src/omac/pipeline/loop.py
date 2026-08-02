"""pipeline/loop — 确定性单轮 tick(结果回收 → 就绪计算 → 派发)。

设计文档 §7.3:sync → decide → dispatch,状态全在 manifest + 平台,幂等。
硬性约束(§2.4):业务失败无自动重试——blocked 节点在后续 tick 保持 blocked,
重试只经 `omac node retry` 显式决策。明确 allowlist 的执行基础设施瞬时失败
复用同一 Run 派发路径做一次有界恢复,不占业务 bounce。abandoned 上游视同
依赖已满足(P1.4)。
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Set, Tuple

import yaml

from ..core import graph, logsetup
from ..core.amendment import ensure_amendment_apply_complete
from ..core.config import DEFAULT_RETRY
from ..core.contract_boundaries import (
    build_contract_boundary_decision,
    contract_boundary_conflicts,
)
from ..core.evidence import validate_review_evidence, validate_worker_evidence
from ..core.review_convergence import (
    build_review_convergence_decision, build_review_obligations,
    review_convergence_decision, review_subject_digest)
from ..core.retry_budget import consumed_bounces, review_rework_budget
from ..core.stage_recovery import stage_recovery_subject, validate_stage_recovery
from ..core.gitsync import commit_manifest
from ..core.manifest import (
    Manifest, _dump_contract, confirmed_merge_is_closed, save_manifest,
    set_node,
)
from ..pipeline.delivery import (
    advance_delivery, block_unproven_merge_request,
    merge_bounce_attempt, merge_request_state_is_valid, run_merge_delivery,
)
from ..engines.models import (
    AgentRunObservation,
    PullRequestReadiness, PullRequestReadinessFailure, PullRequestState,
    WorkItemControlProjection, WorkItemHydrationPlan, WorkItemPayload,
    WorkItemStatus,
)
from ..engines.runtime import AgentRuntime
from ..engines.store import WorkItemStore
from ..errors import AuthError, PlatformError, WorkItemNotFoundError
from ..i18n import current_language, ui
from ..pipeline.dispatch import normalize_source_refs, render_issue_body
from ..core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, DELIVERY_IDENTITY_SCHEMA,
    REVIEWER_RUN_BASELINE_SCHEMA, WORKER_HANDOFF_SCHEMA,
    DeliveryIdentity, ReviewerRunBaseline, TaskKind, TaskPhase,
    WorkerHandoffIntent, parse_delivery_identity,
)

log = logsetup.get_logger(__name__)

# dag 节点统一 kind(事件字段;与 run_task 的 plan/decompose/acceptance 区分)
_DAG_KIND = "develop"

# manifest status 字符串常量
RUNNING_STATUSES = {"in_progress", "ci_check", "in_review", "merging"}
FAILED_STATUSES = {"blocked", "failed"}
TERMINAL_STATUSES = {"done", "blocked", "failed", "cancelled", "abandoned"}

# WorkItemStatus(平台枚举)→ manifest status 字符串
_PLATFORM_TO_MANIFEST: Dict[str, str] = {
    "todo": "todo",
    "in_progress": "in_progress",
    "in_review": "in_review",
    "done": "done",
    "failed": "failed",
    "blocked": "blocked",
}
_MISSING_WORK_ITEM = object()
_HANDOFF_OBSERVATION_ATTEMPTS = 3
_HANDOFF_OBSERVATION_INTERVAL = 0.5
_HANDOFF_TERMINAL_GRACE_SECONDS = 30
_TRANSIENT_RUNTIME_MAX_RUNS = 2
_TRANSIENT_RUNTIME_RETRY_BACKOFF_SECONDS = 1.0
_WORKER_DELIVERY_PAYLOADS = frozenset({
    WorkItemPayload.VERIFICATION,
    WorkItemPayload.CONTRACT,
})
_REVIEW_CONFIRMATION_PAYLOADS = frozenset({
    WorkItemPayload.DELIVERABLE,
    WorkItemPayload.PROJECT_RULES,
    WorkItemPayload.VERIFICATION,
    WorkItemPayload.REVIEW_REPORT,
    WorkItemPayload.REVIEW_LEDGER,
    WorkItemPayload.REVIEW_OBLIGATIONS,
    WorkItemPayload.MACHINE_FEEDBACK,
    WorkItemPayload.CONTRACT,
})


@dataclass(frozen=True)
class _WorkerHandoffResult:
    """One read-only handoff observation and an optional delivery candidate."""

    state: str
    intent: WorkerHandoffIntent | None
    projection: WorkItemControlProjection | None = None
    delivery_identity: DeliveryIdentity | None = None


class _WorkerHandoffCandidateChanged(Exception):
    """Unsealed delivery changed between observation and controller commit."""


class _ReviewerDispatchUnresolved(PlatformError):
    """A persisted reviewer assignment has no uniquely observable Run."""


@dataclass(frozen=True)
class _HandoffPreparationResult:
    """One bounded authoritative observation of an idempotent preparation write."""

    state: str
    projection: WorkItemControlProjection | None = None


@dataclass(frozen=True)
class _RunFailure:
    run: AgentRunObservation
    classification: str
    consecutive_runs: int

    @property
    def exhausted(self) -> bool:
        return (
            self.classification == "transient"
            and self.consecutive_runs >= _TRANSIENT_RUNTIME_MAX_RUNS
        )


@dataclass(frozen=True)
class _ExpectedTerminalRun:
    """Latest terminal direct Run for one causally bounded agent dispatch."""

    run: AgentRunObservation
    outcome: str
    consecutive_runs: int


@dataclass(frozen=True)
class _DirectRunAttempt:
    state: str
    target_run_id: str | None = None
    terminal: _ExpectedTerminalRun | None = None
    detail: str = ""


_RETRYABLE_RUN_ERROR_SIGNATURES = (
    re.compile(
        r"selected model is at capacity\. please try a different model\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:hermes provider error:\s*)?our servers are currently overloaded"
        r"(?:\.|\. please try again later\.?)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:provider|runtime|transport) (?:error|status)|"
        r"hermes provider error):\s*(?:http\s+)?"
        r"(?:429(?:\s+too many requests)?|502(?:\s+bad gateway)?|"
        r"503(?:\s+service unavailable)?|504(?:\s+gateway timeout)?)\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:provider|runtime|transport) (?:error|status)|"
        r"hermes provider error):\s*"
        r"(?:connection timeout(?: while opening provider stream)?|"
        r"connect timeout|read timeout(?: while waiting for provider response)?)\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:provider|runtime|transport) "
        r"(?:connection|connect|read) timeout\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"connection timeout while opening provider stream\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"read timeout while waiting for provider response\.?",
        re.IGNORECASE,
    ),
)


def _is_retryable_transient_run_failure(run: AgentRunObservation) -> bool:
    """Classify only explicit provider/transport failures as retryable."""
    if run.status != "failed" or not run.error:
        return False
    error = run.error.strip()
    return any(
        signature.fullmatch(error)
        for signature in _RETRYABLE_RUN_ERROR_SIGNATURES
    )


def _ordered_direct_runs(
    runs: List[AgentRunObservation],
) -> List[AgentRunObservation]:
    indexed = [
        (index, run) for index, run in enumerate(runs)
        if run.kind == "direct"
    ]
    indexed.sort(
        key=lambda pair: (
            pair[1].created_at or pair[1].updated_at or "", -pair[0]
        ),
        reverse=True,
    )
    return [run for _index, run in indexed]


def _latest_run_failure(
    runtime: AgentRuntime,
    item_id: str,
    agent_id: str,
    *,
    baseline_direct_run_ids: Tuple[str, ...] = (),
    target_run_id: str | None = None,
) -> _RunFailure | None:
    """Return the latest expected failed Run and its derived classification."""
    observed = _observe_direct_run_attempt(
        runtime.list_runs(item_id),
        agent_id,
        baseline_direct_run_ids=baseline_direct_run_ids,
        target_run_id=target_run_id,
    )
    terminal = observed.terminal
    if terminal is None or terminal.outcome == "finished-without-submit":
        return None
    classification = (
        "transient"
        if terminal.outcome == "transient-failure"
        else "nonretryable"
    )
    return _RunFailure(
        terminal.run, classification, terminal.consecutive_runs)


def _observe_direct_run_attempt(
    runs: List[AgentRunObservation],
    agent_id: str,
    *,
    baseline_direct_run_ids: Tuple[str, ...] = (),
    cutoff_created_at: str | None = None,
    target_run_id: str | None = None,
    attempt: int = 1,
) -> _DirectRunAttempt:
    """Bind and classify one causal direct-Run attempt."""
    baseline = set(baseline_direct_run_ids)
    cutoff = _parse_platform_time(cutoff_created_at)
    expected = []
    for run in _ordered_direct_runs(runs):
        if run.agent_id != agent_id or run.id in baseline:
            continue
        if cutoff_created_at:
            created = _parse_platform_time(run.created_at)
            if created is None or created.tzinfo is None:
                return _DirectRunAttempt(
                    "unexpected", detail=f"Run {run.id} has no usable creation time")
            if cutoff is None or cutoff.tzinfo is None:
                return _DirectRunAttempt("unexpected", detail="invalid Run cutoff")
            if created <= cutoff:
                continue
        expected.append(run)
    if target_run_id:
        by_id = {run.id: run for run in expected}
        if len(by_id) != len(expected):
            return _DirectRunAttempt("unexpected", detail="ambiguous target Run")
        latest = by_id.get(target_run_id)
        if latest is None:
            return _DirectRunAttempt("missing", target_run_id)
        target_index = expected.index(latest)
        if target_index:
            chain_ids = {latest.id}
            while True:
                children = [
                    run for run in expected[:target_index]
                    if run.retry_of_run_id == latest.id
                ]
                if not children:
                    break
                if len(children) != 1 or not latest.terminal:
                    return _DirectRunAttempt(
                        "unexpected", detail="ambiguous target Run")
                latest = children[0]
                if latest.id in chain_ids:
                    return _DirectRunAttempt(
                        "unexpected", detail="ambiguous target Run")
                chain_ids.add(latest.id)
            if (
                len(chain_ids) != target_index + 1
                or expected[0].id != latest.id
            ):
                return _DirectRunAttempt(
                    "unexpected", detail="ambiguous target Run")
            target_run_id = latest.id
        if any(run.id != target_run_id and not run.terminal for run in expected):
            return _DirectRunAttempt("unexpected", detail="ambiguous target Run")
    else:
        if len(expected) > 1:
            return _DirectRunAttempt("unexpected", detail="ambiguous post-baseline Runs")
        if not expected:
            return _DirectRunAttempt("missing")
        latest = expected[0]
        target_run_id = latest.id
    if latest.active:
        return _DirectRunAttempt("active", target_run_id)
    if not latest.terminal:
        return _DirectRunAttempt("missing", target_run_id)
    if latest.status == "failed":
        outcome = (
            "transient-failure"
            if _is_retryable_transient_run_failure(latest)
            else "nonretryable-failure"
        )
    else:
        outcome = "finished-without-submit"

    consecutive = 0
    for run in expected:
        if not run.terminal:
            break
        if outcome == "transient-failure":
            matches = _is_retryable_transient_run_failure(run)
        elif outcome == "nonretryable-failure":
            matches = (
                run.status == "failed"
                and not _is_retryable_transient_run_failure(run)
            )
        else:
            matches = run.status in {"completed", "cancelled"}
        if not matches:
            break
        consecutive += 1
    terminal = _ExpectedTerminalRun(latest, outcome, max(consecutive, attempt))
    return _DirectRunAttempt("terminal", target_run_id, terminal)


def _formal_reviewer_dispatch_target(
    runs: List[AgentRunObservation],
    observed: _DirectRunAttempt,
) -> tuple[AgentRunObservation | None, str | None]:
    """Return the one formal Reviewer dispatch shared by recovery paths."""
    if observed.state not in {"active", "terminal"}:
        return None, observed.detail or "no uniquely observable target Run"
    target = next(
        (run for run in runs if run.id == observed.target_run_id),
        None,
    )
    if target is None or target.trigger_kind not in {"issue_assignment", "rerun"}:
        return None, "post-baseline reviewer Run is not a formal reviewer dispatch"
    return target, None


def _resolved_worker_handoff_dispatch(
    result: _WorkerHandoffResult,
) -> _WorkerHandoffResult | None:
    if result.state in {
        "complete", "complete-unsealed", "finished-without-submit", "transient-failure",
        "nonretryable-failure",
    }:
        return result
    if result.state in {"waiting", "pending-submit"}:
        return replace(result, state="waiting")
    return None


def _project_root_from_manifest_path(manifest_path: str) -> str:
    parent = Path(manifest_path).resolve().parent
    if parent.name == ".omac":
        return str(parent.parent)
    return str(parent)


def _store_env(store: WorkItemStore) -> dict:
    env = {
        "OMAC_ENGINE": store.config.engine_type,
        "OMAC_WORKSPACE_ID": store.config.workspace_id,
    }
    if store.config.project_id:
        env["OMAC_PROJECT_ID"] = store.config.project_id
    workspace_slug = (store.config.extra or {}).get("workspace_slug") or (store.config.extra or {}).get("OMAC_WORKSPACE_SLUG")
    if workspace_slug:
        env["OMAC_WORKSPACE_SLUG"] = workspace_slug
    return env


@dataclass
class TickResult:
    """单轮 tick 的结果。

    state: converged(全部 done) | running(有进行中节点或正式活跃 Run) |
           needs_decision(有失败且无正式活跃 Run)
    report: 仅 needs_decision 时有内容——失败节点 + 证据摘要 + 受阻下游
    """
    state: str
    done: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    running: List[str] = field(default_factory=list)
    dispatched: List[str] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconcileResult:
    """One atomic reconcile outcome and the exact observations that produced it."""

    changed: bool
    observations: Dict[str, WorkItemControlProjection | None]


def _build_snapshot(manifest: Manifest) -> dict:
    """从 manifest 构建 graph 模块所需的 snapshot dict。"""
    return {
        key: {"status": node.status, "blocked_by": list(node.blocked_by)}
        for key, node in manifest.nodes.items()
    }


def _has_unreviewed_worker_delivery(node, item) -> bool:
    """识别 worker 已交付、但 manifest 仍残留 terminal 状态的节点。"""
    if confirmed_merge_is_closed(node):
        return False
    phase = getattr(item, "phase", TaskPhase.AUTHORING)
    review_subject_changed = bool(
        phase == TaskPhase.REVIEW
        and item.review_subject_digest
        and item.review_subject_digest != _current_review_subject(item)
    )
    delivery_identity_changed = bool(
        phase == TaskPhase.REVIEW
        and item.delivery_identity is not None
        and not _control_matches_delivery_identity(item)
    )
    return bool(
        node.reviewer
        and item.status == WorkItemStatus.DONE
        and (
            phase == TaskPhase.AUTHORING
            or review_subject_changed
            or delivery_identity_changed
        )
        and item.artifacts
        and item.verification
        and not _current_delivery_passed_review(item)
    )


def _current_review_subject(item) -> str:
    return review_subject_digest(item, max(1, item.bounces.review + 1))


def _review_subject_for_round(
    manifest: Manifest, key: str, item, round_index: int,
) -> str:
    """返回指定 round 的合法 subject，兼容已接受 amendment 的 review recovery。"""
    ledger = manifest.meta.get("amendment_apply")
    entries = ledger.get("nodes") if isinstance(ledger, dict) else None
    entry = entries.get(key) if isinstance(entries, dict) else None
    expected = (
        entry.get("expected_review_subject")
        if isinstance(entry, dict) and entry.get("stage") == "review"
        else None
    )
    if (
        isinstance(expected, str)
        and expected
        and expected == stage_recovery_subject(manifest.nodes[key], item)
    ):
        return expected
    return review_subject_digest(item, round_index)


def _review_subject_for_current_delivery(
    manifest: Manifest, key: str, item,
) -> str:
    return _review_subject_for_round(
        manifest, key, item, max(1, item.bounces.review + 1))


def _review_subject_is_current(
    manifest: Manifest, key: str, item,
) -> bool:
    return (
        item.review_subject_digest
        == _review_subject_for_current_delivery(manifest, key, item)
    )


def _delayed_reviewer_recovery_marker_error(
    manifest: Manifest,
    key: str,
    item,
    reviewer: str,
    reviewer_id: str,
    *,
    require_target: bool,
) -> str | None:
    """Validate the dedicated delayed-Run marker against current authority."""
    decision = item.decision_required
    if not (
        isinstance(decision, dict)
        and decision.get("reason_code") == "reviewer-run-baseline-unavailable"
        and decision.get("phase") == TaskPhase.REVIEW.value
        and decision.get("resume_issue_id") == item.id
        and decision.get("node_id") in {None, key}
    ):
        return "the persisted recovery decision does not identify this review"

    baseline = item.reviewer_run_baseline
    if baseline is None or not baseline.is_causally_bound():
        return "the persisted reviewer Run baseline is incomplete"
    if require_target and not baseline.target_run_id:
        return "the persisted reviewer Run baseline has no target Run"

    authoritative_subject = _review_subject_for_current_delivery(
        manifest, key, item)
    if (
        authoritative_subject != item.review_subject_digest
        or authoritative_subject != baseline.subject_digest
        or baseline.target_reviewer != reviewer
        or baseline.target_agent_id != reviewer_id
    ):
        return "the authoritative review subject or reviewer identity does not match"

    item_contract = item.contract
    if item_contract is not None and not isinstance(item_contract, dict):
        item_contract = _dump_contract(item_contract)
    node_contract = (
        _dump_contract(manifest.nodes[key].contract)
        if manifest.nodes[key].contract else None
    )
    if item_contract != node_contract:
        return "the manifest and persisted node contracts do not match"

    try:
        validate_stage_recovery(item, TaskPhase.REVIEW.value)
    except ValueError as exc:
        return str(exc)
    identity = item.delivery_identity
    if (
        identity is None
        or identity.verification_created_at != baseline.cutoff_created_at
    ):
        return "the delivery identity does not match the reviewer Run cutoff"
    return None


def _current_delivery_passed_review(item) -> bool:
    return (
        item.review_verdict in {"pass", "pass-with-nits"}
        and item.review_subject_digest == _current_review_subject(item)
    )


def _pull_request_url(item) -> str:
    artifacts = getattr(item, "artifacts", None)
    if not isinstance(artifacts, dict):
        return ""
    return artifacts.get("pr_url") or artifacts.get("pr") or ""


def _pull_request_state(observation) -> PullRequestState:
    state = getattr(observation, "state", PullRequestState.UNKNOWN)
    if isinstance(state, PullRequestState):
        return state
    try:
        return PullRequestState(str(state).lower())
    except ValueError:
        return PullRequestState.UNKNOWN


def _reviewer_run_needs_resume(item) -> bool:
    """仅在 REVIEW 阶段识别失联 reviewer run，绝不回退 worker 交付。"""
    return (
        getattr(item, "phase", TaskPhase.AUTHORING) == TaskPhase.REVIEW
        and (
            getattr(item, "agent_run_failed", False)
            or getattr(item, "agent_run_finished_without_submit", False)
        )
    )


def _requires_pull_request_observation(node, item) -> bool:
    """判断 reconcile 的只读阶段是否需要查询远端 PR。"""
    if node.status != "done":
        return False
    if _has_unreviewed_worker_delivery(node, item):
        return False
    if item.review_verdict == "reject":
        return False
    if getattr(item, "kind", TaskKind.DEVELOP) != TaskKind.DEVELOP:
        return False
    if not _pull_request_url(item):
        return False
    if not merge_request_state_is_valid(node.merge_request_state):
        return False
    return not (
        confirmed_merge_is_closed(node)
    )


def _control_matches_delivery_identity(item) -> bool:
    identity = parse_delivery_identity(getattr(item, "delivery_identity", None))
    if identity is None or not identity.is_complete():
        return False
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    declared_sha = str(verification_ref.get("sha256") or "").strip()
    return bool(
        identity.pr_url == (artifacts.get("pr_url") or artifacts.get("pr"))
        and identity.pr_head_sha == artifacts.get("head_sha")
        and identity.verification_attachment_id
        == verification_ref.get("attachment_id")
        and identity.verification_comment_id == verification_ref.get("comment_id")
        and bool(declared_sha)
        and identity.verification_sha256 == declared_sha
    )


def _worker_handoff_has_new_delivery(item, intent: WorkerHandoffIntent) -> bool:
    """Return whether control facts prove a new Worker submission exists."""
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    attachment_id = str(
        verification_ref.get("attachment_id") or "").strip()
    return bool(
        item.status == WorkItemStatus.DONE
        and artifacts
        and attachment_id
        and attachment_id != intent.baseline_verification_attachment_id
    )


def _build_work_item_hydration_plan(
    node, projection: WorkItemControlProjection,
) -> WorkItemHydrationPlan:
    """Plan only evidence required by the lifecycle decision for one node."""
    if confirmed_merge_is_closed(node):
        return frozenset()

    item = projection.work_item
    payloads: Set[WorkItemPayload] = set()
    has_delivery = bool(
        isinstance(item.artifacts, dict) and item.artifacts
        and projection.has_payload(WorkItemPayload.VERIFICATION)
    )
    authoring_delivery = bool(
        item.status == WorkItemStatus.DONE
        and item.phase == TaskPhase.AUTHORING
        and has_delivery
    )
    manifest_allows_lifecycle_progress = (
        node.status not in FAILED_STATUSES and node.status != "abandoned"
    )
    review_or_confirmation = bool(
        node.status in {"in_review", "merging"}
        or (
            manifest_allows_lifecycle_progress
            and item.phase == TaskPhase.REVIEW
            and item.status in {
                WorkItemStatus.IN_REVIEW,
                WorkItemStatus.DONE,
                WorkItemStatus.FAILED,
                WorkItemStatus.BLOCKED,
            }
        )
        or (
            manifest_allows_lifecycle_progress
            and item.review_verdict == "reject"
        )
        or (
            node.status == "done"
            and getattr(item, "kind", TaskKind.DEVELOP) == TaskKind.DEVELOP
            and not confirmed_merge_is_closed(node)
        )
    )
    delivery_drift = bool(
        item.delivery_identity is not None
        and not _control_matches_delivery_identity(item)
    )

    if (
        item.worker_handoff is not None
        and _worker_handoff_has_new_delivery(item, item.worker_handoff)
    ):
        # Handoff observation reads the verification attachment once together
        # with its causal comment/Run facts.  Reconcile only preloads the other
        # Worker evidence needed before that candidate can be committed.
        payloads.add(WorkItemPayload.CONTRACT)
    elif item.worker_handoff is None and authoring_delivery:
        payloads.update(_WORKER_DELIVERY_PAYLOADS)
    if review_or_confirmation:
        payloads.update(_REVIEW_CONFIRMATION_PAYLOADS)
    if delivery_drift:
        payloads.update(
            _REVIEW_CONFIRMATION_PAYLOADS
            if item.phase == TaskPhase.REVIEW
            else _WORKER_DELIVERY_PAYLOADS
        )
    return frozenset(payloads)


def _hydrate_worker_collect_evidence(
    store: WorkItemStore,
    projection: WorkItemControlProjection,
) -> WorkItemControlProjection:
    return _hydrate_work_item_payloads(
        store, projection, _WORKER_DELIVERY_PAYLOADS)


def _hydrate_work_item_payloads(
    store: WorkItemStore,
    projection: WorkItemControlProjection,
    requested: WorkItemHydrationPlan,
) -> WorkItemControlProjection:
    plan = requested & projection.deferred_payloads
    if not plan:
        return projection
    item = store.hydrate_work_item_evidence(projection, plan)
    return replace(
        projection,
        work_item=item,
        deferred_payloads=projection.deferred_payloads - plan,
    )


def _observe_reconcile_inputs(
    store: WorkItemStore, manifest: Manifest, max_parallel: int = 4,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Observe controls for all nodes, plan hydration, then load required evidence."""
    controls: Dict[str, Any] = {}
    observations: Dict[str, Any] = {}
    pull_requests: Dict[str, Any] = {}

    # Phase 1: every Issue control envelope is read before any attachment body.
    # Adapters may explicitly allow bounded parallel reads; results remain an
    # all-or-nothing barrier and are published in manifest order below.
    control_jobs = [
        (key, node.work_item_id)
        for key, node in manifest.nodes.items()
        if node.work_item_id
    ]

    def observe_control(job: Tuple[str, str]) -> Tuple[str, Any]:
        key, item_id = job
        try:
            projection = store.observe_work_item_control(item_id)
        except WorkItemNotFoundError:
            projection = _MISSING_WORK_ITEM
        return key, projection

    if control_jobs:
        requested_parallelism = max(1, max_parallel)
        workers = max(1, min(
            len(control_jobs),
            requested_parallelism,
            store.control_observation_parallelism(requested_parallelism),
        ))
        if workers == 1:
            control_results = [observe_control(job) for job in control_jobs]
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="omac-control",
            ) as executor:
                control_results = list(executor.map(observe_control, control_jobs))
        controls = dict(control_results)

    # Phase 2: the complete control snapshot produces an explicit hydration plan.
    hydration_jobs: List[
        Tuple[str, WorkItemControlProjection, WorkItemHydrationPlan]
    ] = []
    for key, node in manifest.nodes.items():
        projection = controls.get(key)
        if projection is None:
            continue
        if projection is _MISSING_WORK_ITEM:
            continue
        plan = _build_work_item_hydration_plan(node, projection)
        if plan:
            hydration_jobs.append((key, projection, plan))

    def hydrate(
        job: Tuple[str, WorkItemControlProjection, WorkItemHydrationPlan],
    ) -> Tuple[str, WorkItemControlProjection]:
        key, projection, plan = job
        item = store.hydrate_work_item_evidence(projection, plan)
        return key, replace(
            projection,
            work_item=item,
            deferred_payloads=projection.deferred_payloads - plan,
        )

    hydrated: Dict[str, WorkItemControlProjection] = {}
    if hydration_jobs:
        requested_parallelism = max(1, max_parallel)
        workers = max(1, min(
            len(hydration_jobs),
            requested_parallelism,
            store.evidence_hydration_parallelism(requested_parallelism),
        ))
        if workers == 1:
            results = [hydrate(job) for job in hydration_jobs]
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="omac-evidence",
            ) as executor:
                results = list(executor.map(hydrate, hydration_jobs))
        hydrated = dict(results)

    # Publish the complete read barrier in manifest order only after every
    # required hydration succeeded. Completion order never affects decisions.
    for key in manifest.nodes:
        projection = controls.get(key)
        if projection is None:
            continue
        if projection is _MISSING_WORK_ITEM:
            observations[key] = _MISSING_WORK_ITEM
        else:
            observations[key] = hydrated.get(key, projection)

    # Phase 3: remote PR facts are read only after every required payload read
    # succeeded, preserving the existing all-or-nothing reconcile observation.
    for key, node in manifest.nodes.items():
        observation = observations.get(key)
        if observation is None or observation is _MISSING_WORK_ITEM:
            continue
        item = observation.work_item
        if not _requires_pull_request_observation(node, item):
            continue
        pull_requests[key] = store.observe_pull_request(
            _pull_request_url(item))
    return observations, pull_requests


def _resume_reviewer_run(store, runtime, node) -> bool:
    """在同一 issue 恢复 reviewer，不重置既有 worker/评审对象事实。"""
    if not node.reviewer:
        return False
    item_id = node.work_item_id
    store.update_status(item_id, WorkItemStatus.IN_REVIEW)
    store.assign_work_item(
        item_id, node.reviewer, "reviewer", start_run=False)
    runtime.wake(item_id, node.reviewer, "reviewer")
    return True


def _block_runtime_failure(
    store: WorkItemStore,
    manifest: Manifest,
    manifest_path: str,
    key: str,
    item,
    role: str,
    failure: _RunFailure,
) -> str:
    """Stop in the current business stage without consuming business bounce."""
    retry = f"omac node retry {manifest_path} {key}"
    exhausted = failure.classification == "transient"
    if exhausted:
        reason_code = "transient-runtime-retry-exhausted"
        failure_class = "transient-provider-or-transport"
        reason = ui(
            f"{role.title()} infrastructure retry limit "
            f"({_TRANSIENT_RUNTIME_MAX_RUNS} Runs) exhausted for Run "
            f"{failure.run.id}. Run `{retry}` after the provider recovers.",
            f"{role} 执行基础设施瞬时失败已达到上限"
            f"({_TRANSIENT_RUNTIME_MAX_RUNS} 个 Run)，最新 Run 为 "
            f"{failure.run.id}。请在 provider 恢复后执行 `{retry}`。",
        )
    else:
        reason_code = "nonretryable-runtime-failure"
        failure_class = "nonretryable-agent-run"
        reason = ui(
            f"{role.title()} Run {failure.run.id} failed with a non-retryable "
            f"or unknown error. Inspect it with `omac work show {item.id} "
            "--output json` before an explicit operator decision.",
            f"{role} Run {failure.run.id} 因不可重试或未知错误失败。"
            f"请先执行 `omac work show {item.id} --output json` 检查，"
            "再做显式人工决策。",
        )
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": reason_code,
        "kind": TaskKind.DEVELOP.value,
        "phase": item.phase.value,
        "gate": role,
        "resume_issue_id": item.id,
        "node_id": key,
        "run_id": failure.run.id,
        "failure_class": failure_class,
        "next_action": retry,
    }
    store.update_work_item_metadata(item.id, decision_required=decision)
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    set_node(manifest, key, status="blocked")
    return reason


def _block_reviewer(
    store: WorkItemStore,
    manifest: Manifest,
    manifest_path: str,
    key: str,
    item,
    reason_code: str,
    detail: str,
    run_id: str | None = None,
) -> str:
    retry = f"omac node retry {manifest_path} {key}"
    reason = ui(
        f"Reviewer recovery is unsafe: {detail}. Inspect the Runs, then use "
        f"`{retry}` after an explicit operator decision.",
        f"reviewer 恢复不安全：{detail}。请检查 Runs，并在人工决策后执行 "
        f"`{retry}`。",
    )
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": reason_code,
        "kind": TaskKind.DEVELOP.value,
        "phase": TaskPhase.REVIEW.value,
        "gate": "reviewer",
        "resume_issue_id": item.id,
        "node_id": key,
        "failure_class": "unproven-reviewer-run-causality",
        "next_action": retry,
    }
    if run_id:
        decision["run_id"] = run_id
    store.update_work_item_metadata(item.id, decision_required=decision)
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    set_node(manifest, key, status="blocked")
    return reason


def _block_review_rework_budget(
    store: WorkItemStore,
    manifest: Manifest,
    key: str,
    item,
    budget,
    *,
    gate: str,
    reason: str,
) -> str:
    """Preserve Reviewer facts and project an exhausted rework decision."""
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": f"{gate}-budget-exhausted",
        "kind": TaskKind.DEVELOP.value,
        "phase": TaskPhase.REVIEW.value,
        "gate": gate,
        "rounds": budget.current_round,
        "consumed": budget.consumed,
        "limit": budget.authorized_through_round,
        "resume_issue_id": item.id,
        "node_id": key,
        "verdict": item.review_verdict,
    }
    for field in ("review_report_ref", "review_ledger_ref"):
        value = getattr(item, field, None)
        if isinstance(value, dict) and value:
            decision[field] = value
    store.update_work_item_metadata(item.id, decision_required=decision)
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    store.add_comment(item.id, ui(
        f"Review retry limit ({budget.authorized_through_round}) exhausted: {reason}",
        f"评审回退上界({budget.authorized_through_round})已耗尽: {reason}",
    ))
    set_node(manifest, key, status="blocked")
    log.info(
        logsetup.EVT_NEEDS_DECISION,
        kind=_DAG_KIND,
        node=key,
        id=item.id,
        gate=gate,
        rounds=budget.current_round,
        consumed=budget.consumed,
        max=budget.authorized_through_round,
    )
    return ui(
        f"Review rework limit {budget.authorized_through_round} exhausted: {reason}",
        f"评审返工上界 {budget.authorized_through_round} 已耗尽: {reason}",
    )


def _block_review_non_convergence(
    store: WorkItemStore,
    manifest: Manifest,
    key: str,
    item,
    convergence: dict,
) -> str:
    """Stop semantic retries and preserve the ledger for DAG amendment."""
    decision = build_review_convergence_decision(
        item,
        convergence,
        kind=TaskKind.DEVELOP.value,
        node_id=key,
        recommended_action="dag-amendment",
    )
    store.update_work_item_metadata(
        item.id,
        decision_required=decision,
        phase=TaskPhase.REVIEW,
    )
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    set_node(manifest, key, status="blocked")
    log.info(
        logsetup.EVT_NEEDS_DECISION,
        kind=_DAG_KIND,
        node=key,
        id=item.id,
        gate="review-convergence",
        rounds=convergence["cycle_count"],
        mode=convergence["mode"],
    )
    return ui(
        "Review is not converging within the current node boundary; "
        "a DAG amendment is required.",
        "评审无法在当前节点边界内收敛；需要 DAG amendment 重新拆分。",
    )


def _reviewer_no_submit_grace_state(
    store: WorkItemStore,
    runtime: AgentRuntime,
    item,
    baseline: ReviewerRunBaseline,
    terminal: _ExpectedTerminalRun,
) -> str:
    """Return submitted, waiting, elapsed, or unavailable without mutating runs."""
    observed = terminal
    for attempt in range(_HANDOFF_OBSERVATION_ATTEMPTS):
        fresh = store.observe_work_item_control(item.id).work_item
        if fresh.review_verdict:
            return "submitted"

        ended_at = _parse_platform_time(observed.run.updated_at)
        if observed.run.updated_at and (
            ended_at is None or ended_at.tzinfo is None
        ):
            return "unavailable"
        if ended_at is not None:
            age = (_utcnow() - ended_at).total_seconds()
            if age < 0:
                return "unavailable"
            return (
                "elapsed"
                if age >= _HANDOFF_TERMINAL_GRACE_SECONDS
                else "waiting"
            )

        if attempt + 1 >= _HANDOFF_OBSERVATION_ATTEMPTS:
            break
        time.sleep(_HANDOFF_OBSERVATION_INTERVAL)
        refreshed = _observe_direct_run_attempt(
            runtime.list_runs(item.id),
            baseline.target_agent_id,
            baseline_direct_run_ids=baseline.baseline_direct_run_ids,
            cutoff_created_at=baseline.cutoff_created_at,
            target_run_id=baseline.target_run_id,
            attempt=baseline.attempt,
        )
        if refreshed.state != "terminal":
            return "waiting"
        observed = refreshed.terminal
    return "unavailable"


def _reviewer_run_baseline_for_observation(
    store: WorkItemStore,
    runtime: AgentRuntime,
    item,
    reviewer: str,
    reviewer_id: str,
) -> tuple[ReviewerRunBaseline | None, str | None]:
    """Load the baseline, or derive it from the sealed delivery's creation time."""
    baseline = item.reviewer_run_baseline
    if baseline is not None:
        if (
            baseline.subject_digest == item.review_subject_digest
            and baseline.target_reviewer == reviewer
            and baseline.target_agent_id == reviewer_id
        ):
            identity = _delivery_identity(item)
            identity_cutoff = (
                _parse_platform_time(identity.verification_created_at)
                if identity is not None and identity.is_complete() else None
            )
            if identity_cutoff is None or identity_cutoff.tzinfo is None:
                return None, (
                    "the current delivery identity or verification time is unusable")
            if not baseline.cutoff_created_at:
                baseline = replace(
                    baseline, cutoff_created_at=identity.verification_created_at,
                    generation=f"review-{secrets.token_hex(8)}")
                store.update_work_item_metadata(
                    item.id, reviewer_run_baseline=baseline)
            cutoff = _parse_platform_time(baseline.cutoff_created_at)
            if cutoff is None or cutoff.tzinfo is None or cutoff != identity_cutoff:
                return None, (
                    "the persisted reviewer Run cutoff is unusable or mismatched")
            if not baseline.is_causally_bound():
                return None, "the persisted reviewer Run baseline is incomplete"
            return baseline, None
        return None, "the persisted reviewer Run baseline is incomplete or stale"

    identity = _delivery_identity(item)
    if identity is None or not identity.is_complete():
        return None, "the current delivery identity is missing or incomplete"
    cutoff = _parse_platform_time(identity.verification_created_at)
    if (
        not item.review_subject_digest
        or cutoff is None
        or cutoff.tzinfo is None
        or not runtime.capabilities.stable_direct_run_identity
    ):
        return None, (
            "the sealed delivery verification time or stable Run identity "
            "support is unavailable")

    direct_runs = [
        run for run in runtime.list_runs(item.id)
        if run.kind == "direct" and run.agent_id == reviewer_id
    ]
    historical_ids = []
    for run in direct_runs:
        created_at = _parse_platform_time(run.created_at)
        if created_at is None or created_at.tzinfo is None:
            return None, f"Reviewer Run {run.id} has no usable creation time"
        if created_at <= cutoff:
            historical_ids.append(run.id)
    baseline = ReviewerRunBaseline(
        schema=REVIEWER_RUN_BASELINE_SCHEMA,
        subject_digest=item.review_subject_digest,
        target_reviewer=reviewer,
        target_agent_id=reviewer_id,
        cutoff_created_at=identity.verification_created_at,
        generation=f"review-{secrets.token_hex(8)}",
        baseline_direct_run_ids=tuple(sorted(historical_ids)),
    )
    store.update_work_item_metadata(
        item.id, reviewer_run_baseline=baseline)
    return baseline, None


def _retry_reviewer_attempt(store, runtime, node, item, baseline) -> str | None:
    runs = runtime.list_runs(item.id)
    retry = replace(
        baseline,
        generation=f"review-{secrets.token_hex(8)}",
        attempt=baseline.attempt + 1,
        baseline_direct_run_ids=tuple(sorted(
            run.id for run in runs if run.kind == "direct")),
        target_run_id=None,
    )
    store.update_work_item_metadata(item.id, reviewer_run_baseline=retry)
    wake_error = None
    try:
        _resume_reviewer_run(store, runtime, node)
    except PlatformError as exc:
        wake_error = str(exc)
    for attempt in range(_HANDOFF_OBSERVATION_ATTEMPTS):
        observed = _observe_direct_run_attempt(
            runtime.list_runs(item.id), retry.target_agent_id,
            baseline_direct_run_ids=retry.baseline_direct_run_ids,
            cutoff_created_at=retry.cutoff_created_at,
            attempt=retry.attempt)
        if observed.state in {"active", "terminal"}:
            store.update_work_item_metadata(
                item.id, reviewer_run_baseline=replace(
                    retry, target_run_id=observed.target_run_id))
            return None
        if observed.state == "unexpected":
            return observed.detail
        if attempt + 1 < _HANDOFF_OBSERVATION_ATTEMPTS:
            time.sleep(_HANDOFF_OBSERVATION_INTERVAL)
    return wake_error or "retry generation has no uniquely observable target Run"


def _dispatch_reviewer_for_current_subject(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    key: str,
) -> bool:
    """幂等完成当前交付的 reviewer handoff；同 subject 不重置评审事实。"""
    node = manifest.nodes[key]
    item_id = node.work_item_id
    current = store.get_work_item(item_id)
    _validate_controller_sealed_delivery(store, current)
    subject_digest = _review_subject_for_current_delivery(
        manifest, key, current)
    subject_changed = current.review_subject_digest != subject_digest
    if subject_changed:
        # 先解除旧 subject 的 assignment；若随后崩溃，reviewer metadata 为空，
        # restart 不会把旧 active Run 误认成新 subject 已派发。
        store.clear_assignment(item_id)
        store.update_work_item_metadata(
            item_id,
            review_obligations=build_review_obligations(current),
        )
        current = store.prepare_review_cycle(item_id, subject_digest)

    reviewer_id = store.resolve_agent_id(node.reviewer)
    baseline = current.reviewer_run_baseline
    if (
        baseline is None
        or not baseline.is_causally_bound()
        or baseline.subject_digest != subject_digest
        or baseline.target_reviewer != node.reviewer
        or baseline.target_agent_id != reviewer_id
    ):
        if not runtime.capabilities.stable_direct_run_identity:
            raise PlatformError(
                "Reviewer recovery requires stable direct Run identity support")
        identity = _delivery_identity(current)
        cutoff = (
            _parse_platform_time(identity.verification_created_at)
            if identity is not None else None
        )
        if (
            identity is None
            or not identity.is_complete()
            or cutoff is None
            or cutoff.tzinfo is None
        ):
            raise PlatformError(
                "Reviewer dispatch requires a sealed delivery verification time")
        baseline = ReviewerRunBaseline(
            schema=REVIEWER_RUN_BASELINE_SCHEMA,
            subject_digest=subject_digest,
            target_reviewer=node.reviewer,
            target_agent_id=reviewer_id,
            cutoff_created_at=identity.verification_created_at,
            generation=f"review-{secrets.token_hex(8)}",
            baseline_direct_run_ids=tuple(sorted(
                run.id for run in runtime.list_runs(item_id)
                if run.kind == "direct"
            )),
        )
        store.update_work_item_metadata(
            item_id, reviewer_run_baseline=baseline)
        current = store.get_work_item(item_id)

    if (
        not subject_changed
        and baseline.target_run_id is not None
        and current.phase == TaskPhase.REVIEW
        and current.status == WorkItemStatus.IN_REVIEW
        and current.reviewer == node.reviewer
        and runtime.is_active(item_id)
    ):
        return False

    assignment_prepared = (
        not subject_changed
        and baseline.target_run_id is None
        and current.phase == TaskPhase.REVIEW
        and current.status == WorkItemStatus.IN_REVIEW
        and current.reviewer == node.reviewer
    )
    if assignment_prepared:
        for attempt in range(_HANDOFF_OBSERVATION_ATTEMPTS):
            runs = runtime.list_runs(item_id)
            observed = _observe_direct_run_attempt(
                runs, reviewer_id,
                baseline_direct_run_ids=baseline.baseline_direct_run_ids,
                cutoff_created_at=baseline.cutoff_created_at,
                attempt=baseline.attempt,
            )
            if observed.state in {"active", "terminal"}:
                _target, target_error = _formal_reviewer_dispatch_target(
                    runs, observed)
                if target_error is not None:
                    raise _ReviewerDispatchUnresolved(target_error)
                store.update_work_item_metadata(
                    item_id, reviewer_run_baseline=replace(
                        baseline, target_run_id=observed.target_run_id))
                return False
            if observed.state == "unexpected":
                raise _ReviewerDispatchUnresolved(observed.detail)
            if attempt + 1 < _HANDOFF_OBSERVATION_ATTEMPTS:
                time.sleep(_HANDOFF_OBSERVATION_INTERVAL)
        raise _ReviewerDispatchUnresolved(
            "persisted reviewer assignment has no uniquely observable target Run")

    _refresh_develop_issue_body(
        store, manifest, key, phase=TaskPhase.REVIEW)
    store.update_status(item_id, WorkItemStatus.IN_REVIEW)
    store.assign_work_item(
        item_id, node.reviewer, "reviewer", start_run=False)
    runtime.wake(item_id, node.reviewer, "reviewer")
    return True


def _dispatch_worker_handoff(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    key: str,
    *,
    review_bounce: int | None = None,
    gate: str | None = None,
    projection: WorkItemControlProjection | None = None,
) -> _WorkerHandoffResult:
    """幂等完成 review→worker handoff；assign 可能立即启动 Run。"""
    node = manifest.nodes[key]
    item_id = node.work_item_id
    current = (
        projection or store.observe_work_item_control(item_id)
    ).work_item
    intent = current.worker_handoff
    if intent is None:
        if review_bounce is None or gate is None:
            raise PlatformError(
                f"Worker handoff intent is missing for work item {item_id}")
        if gate == "explicit-dispatch":
            source_round = max(1, current.bounces.review)
            source_subject = stage_recovery_subject(node, current)
        else:
            source_round = max(1, current.bounces.review + 1)
            source_subject = current.review_subject_digest
            if (
                not source_subject
                or source_subject != _review_subject_for_round(
                    manifest, key, current, source_round)
            ):
                raise PlatformError(
                    f"Worker handoff source is stale for work item {item_id}")
        if not runtime.capabilities.stable_direct_run_identity:
            raise PlatformError(
                "Worker handoff requires stable direct Run identity support")
        try:
            target_agent_id = store.resolve_agent_id(node.worker)
            baseline_run_ids = tuple(sorted(
                run.id for run in runtime.list_runs(item_id)
                if run.kind == "direct"
            ))
        except PlatformError as exc:
            if store.is_transient_transport_error(exc):
                return _WorkerHandoffResult("pending-initialization", None)
            raise
        intent = WorkerHandoffIntent(
            schema=WORKER_HANDOFF_SCHEMA,
            state="pending",
            target_worker=node.worker,
            gate=gate,
            source_review_subject_digest=source_subject,
            source_review_round=source_round,
            target_review_bounce=review_bounce,
            generation=f"handoff-{secrets.token_hex(8)}",
            target_agent_id=target_agent_id,
            baseline_direct_run_ids=baseline_run_ids,
            baseline_verification_attachment_id=(
                str((current.verification_ref or {}).get("attachment_id") or "")
                or None
            ),
            target_worker_bounce=current.bounces.worker,
        )
        if current.delivery_identity is not None:
            preparation = _apply_observed_handoff_preparation_write(
                store,
                item_id,
                lambda: store.update_work_item_metadata(
                    item_id, delivery_identity={}),
                lambda item: item.delivery_identity is None,
            )
            if preparation.state == "pending":
                return _WorkerHandoffResult("pending-initialization", None)
            projection = preparation.projection
            current = projection.work_item
        preparation = _apply_observed_handoff_preparation_write(
            store,
            item_id,
            lambda: store.update_work_item_metadata(
                item_id, worker_handoff=intent),
            lambda item: (
                item.worker_handoff is not None
                and item.worker_handoff.generation == intent.generation
            ),
        )
        if preparation.state == "pending":
            observed_intent = (
                preparation.projection.work_item.worker_handoff
                if preparation.projection is not None else None
            )
            if (
                observed_intent is not None
                and observed_intent.generation != intent.generation
            ):
                raise PlatformError(
                    f"Worker handoff intent conflicts for work item {item_id}")
            return _WorkerHandoffResult("pending-initialization", None)
        projection = preparation.projection
        current = projection.work_item

    if not intent.is_causally_bound():
        raise PlatformError(
            f"Worker handoff lacks causal identity for work item {item_id}")

    target_worker_bounce = intent.target_worker_bounce
    if target_worker_bounce is not None:
        if current.bounces.worker < target_worker_bounce:
            preparation = _apply_observed_handoff_preparation_write(
                store,
                item_id,
                lambda: store.update_work_item_metadata(
                    item_id, worker_bounce=target_worker_bounce),
                lambda item: item.bounces.worker == target_worker_bounce,
            )
            if preparation.state == "pending":
                return _WorkerHandoffResult(
                    "pending-preparation", intent)
            projection = preparation.projection
            current = projection.work_item
        if current.bounces.worker != target_worker_bounce:
            raise PlatformError(
                f"Worker handoff bounce does not match retry attempt for "
                f"work item {item_id}")

    if intent.is_causally_bound():
        try:
            result = _observe_worker_handoff(
                store, runtime, manifest, key, intent, projection=projection)
        except PlatformError as exc:
            if store.is_transient_transport_error(exc):
                return _WorkerHandoffResult("pending-preparation", intent)
            raise
        if result.state == "pending-submit":
            result = _observe_worker_handoff_bounded(
                store, runtime, manifest, key, result.intent)
        intent = result.intent
        resolved = _resolved_worker_handoff_dispatch(result)
        if resolved is not None:
            return resolved

    target_bounce = intent.target_review_bounce
    if target_bounce is None:
        raise PlatformError(
            f"Worker handoff target bounce is missing for work item {item_id}")
    if current.bounces.review < target_bounce:
        preparation = _apply_observed_handoff_preparation_write(
            store,
            item_id,
            lambda: store.update_work_item_metadata(
                item_id, review_bounce=target_bounce),
            lambda item: item.bounces.review == target_bounce,
        )
        if preparation.state == "pending":
            return _WorkerHandoffResult("pending-preparation", intent)
        projection = preparation.projection
        current = projection.work_item
    if current.bounces.review != target_bounce:
        raise PlatformError(
            f"Worker handoff bounce does not match for work item {item_id}")

    # reset_review 的接口契约负责一次性清除当前 review projection 并回 AUTHORING。
    # handoff 不制造瞬时 review cycle，避免新增可崩溃的持久化中间态。
    if not _worker_handoff_review_is_reset(current):
        preparation = _apply_observed_handoff_preparation_write(
            store,
            item_id,
            lambda: store.reset_review(item_id),
            _worker_handoff_review_is_reset,
        )
        if preparation.state == "pending":
            return _WorkerHandoffResult("pending-preparation", intent)
        projection = preparation.projection
        current = projection.work_item

    if current.status != WorkItemStatus.IN_PROGRESS:
        preparation = _apply_observed_handoff_preparation_write(
            store,
            item_id,
            lambda: store.update_status(item_id, WorkItemStatus.IN_PROGRESS),
            lambda item: item.status == WorkItemStatus.IN_PROGRESS,
        )
        if preparation.state == "pending":
            return _WorkerHandoffResult("pending-preparation", intent)
        projection = preparation.projection
        current = projection.work_item
    if (
        current.status != WorkItemStatus.IN_PROGRESS
        or not _worker_handoff_review_is_reset(current)
    ):
        raise PlatformError(
            f"Worker handoff preparation did not persist for work item {item_id}")

    try:
        result = _observe_worker_handoff(
            store, runtime, manifest, key, intent)
    except PlatformError as exc:
        if store.is_transient_transport_error(exc):
            return _WorkerHandoffResult("pending-preparation", intent)
        raise
    intent = result.intent
    resolved = _resolved_worker_handoff_dispatch(result)
    if resolved is not None:
        return resolved

    body_item_id, body_metadata = _develop_issue_body_metadata(
        store,
        manifest,
        key,
        phase=TaskPhase.AUTHORING,
        item=current,
    )
    preparation = _apply_observed_handoff_preparation_write(
        store,
        body_item_id,
        lambda: store.update_work_item_metadata(
            body_item_id, **body_metadata),
        lambda item: _develop_issue_body_matches(item, body_metadata),
    )
    if preparation.state == "pending":
        return _WorkerHandoffResult("pending-preparation", intent)

    preparation = _observe_handoff_preparation_bounded(
        store,
        item_id,
        lambda item: (
            item.status == WorkItemStatus.IN_PROGRESS
            and _worker_handoff_review_is_reset(item)
            and _develop_issue_body_matches(item, body_metadata)
        ),
    )
    if preparation.state == "pending":
        return _WorkerHandoffResult("pending-preparation", intent)

    # assign_work_item 自身负责观察当前 assignee并幂等修复。目标 Run 的
    # 身份由后续只读观察绑定到持久 handoff，而不是由 assignment 成功猜测。
    try:
        store.assign_work_item(item_id, intent.target_worker, "worker")
    except PlatformError as assign_error:
        result = _observe_worker_handoff_bounded(
            store, runtime, manifest, key, intent)
        intent = result.intent
        resolved = _resolved_worker_handoff_dispatch(result)
        if resolved is not None:
            return resolved
        raise assign_error

    result = _observe_worker_handoff_bounded(
        store, runtime, manifest, key, intent)
    intent = result.intent
    resolved = _resolved_worker_handoff_dispatch(result)
    if resolved is not None:
        return resolved

    try:
        runtime.wake(item_id, intent.target_worker, "worker")
    except PlatformError as wake_error:
        result = _observe_worker_handoff_bounded(
            store, runtime, manifest, key, intent)
        intent = result.intent
        resolved = _resolved_worker_handoff_dispatch(result)
        if resolved is not None:
            return resolved
        raise wake_error

    result = _observe_worker_handoff_bounded(
        store, runtime, manifest, key, intent)
    resolved = _resolved_worker_handoff_dispatch(result)
    if resolved is not None:
        return resolved
    raise PlatformError(
        f"Worker handoff dispatch outcome is unknown for work item {item_id}")


def _review_projection_is_clear(item) -> bool:
    return bool(
        item.review_verdict is None
        and item.review_comment in {None, ""}
        and item.machine_feedback in (None, {})
        and item.machine_feedback_ref is None
        and item.review_report is None
        and item.review_report_ref is None
        and item.review_subject_digest is None
        and not item.requires_decision
        and item.reviewer_run_baseline is None
    )


def _worker_handoff_review_is_reset(item) -> bool:
    return bool(
        item.phase == TaskPhase.AUTHORING
        and _review_projection_is_clear(item)
    )


def _develop_issue_body_matches(item, metadata: Dict[str, Any]) -> bool:
    return bool(
        item.description == metadata["description"]
        and item.source_refs == metadata["source_refs"]
        and item.blocked_by == metadata["blocked_by"]
    )


def _observe_handoff_preparation_bounded(
    store: WorkItemStore,
    item_id: str,
    is_applied: Callable[[Any], bool],
) -> _HandoffPreparationResult:
    last_projection = None
    for attempt in range(_HANDOFF_OBSERVATION_ATTEMPTS):
        try:
            projection = store.observe_work_item_control(item_id)
        except PlatformError as exc:
            if not store.is_transient_transport_error(exc):
                raise
        else:
            last_projection = projection
            if is_applied(projection.work_item):
                return _HandoffPreparationResult("applied", projection)
        if attempt + 1 < _HANDOFF_OBSERVATION_ATTEMPTS:
            time.sleep(_HANDOFF_OBSERVATION_INTERVAL)
    return _HandoffPreparationResult("pending", last_projection)


def _apply_observed_handoff_preparation_write(
    store: WorkItemStore,
    item_id: str,
    write: Callable[[], Any],
    is_applied: Callable[[Any], bool],
) -> _HandoffPreparationResult:
    """Apply one idempotent preparation write and prove its outcome.

    A recognized transport failure is not success and not a business failure.
    A bounded authoritative observation decides whether this step completed;
    stale or unavailable projections leave the handoff pending.
    """
    try:
        write()
    except PlatformError as exc:
        if not store.is_transient_transport_error(exc):
            raise
    return _observe_handoff_preparation_bounded(
        store, item_id, is_applied)


def _observe_worker_handoff_bounded(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    key: str,
    intent: WorkerHandoffIntent,
) -> _WorkerHandoffResult:
    """对最终一致的 Run/delivery 投影做有限只读观察。"""
    result = _WorkerHandoffResult("missing", intent)
    for attempt in range(_HANDOFF_OBSERVATION_ATTEMPTS):
        result = _observe_worker_handoff(
            store, runtime, manifest, key, result.intent)
        if result.state not in {"missing", "pending-submit"}:
            return result
        if attempt + 1 < _HANDOFF_OBSERVATION_ATTEMPTS:
            time.sleep(_HANDOFF_OBSERVATION_INTERVAL)
    return result


def _next_worker_handoff_attempt(
    store: WorkItemStore,
    runtime: AgentRuntime,
    item,
    *,
    consume_business_bounce: bool = True,
) -> WorkerHandoffIntent:
    """Create one persisted retry generation after a terminal no-submit Run."""
    intent = item.worker_handoff
    if intent is None or not intent.is_causally_bound():
        raise PlatformError(
            f"Worker handoff is not retryable for work item {item.id}")
    direct_runs = [
        run for run in runtime.list_runs(item.id) if run.kind == "direct"
    ]
    if any(run.active for run in direct_runs):
        raise PlatformError(
            f"Worker handoff still has an active Run for work item {item.id}")
    allowed_run_ids = set(intent.baseline_direct_run_ids)
    if intent.target_run_id:
        allowed_run_ids.add(intent.target_run_id)
    if any(run.id not in allowed_run_ids for run in direct_runs):
        raise PlatformError(
            f"Worker handoff observed an unexpected terminal Run for work item "
            f"{item.id}")
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    return replace(
        intent,
        generation=f"handoff-{secrets.token_hex(8)}",
        baseline_direct_run_ids=tuple(sorted(run.id for run in direct_runs)),
        baseline_verification_attachment_id=(
            str(verification_ref.get("attachment_id") or "") or None
        ),
        target_run_id=None,
        target_worker_bounce=(
            item.bounces.worker + 1
            if consume_business_bounce else item.bounces.worker
        ),
        terminal_observed_at=None,
    )


def _delivery_identity(item) -> DeliveryIdentity | None:
    return parse_delivery_identity(getattr(item, "delivery_identity", None))


def _parse_platform_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _observe_terminal_without_submit(
    store: WorkItemStore,
    item_id: str,
    intent: WorkerHandoffIntent,
) -> tuple[str, WorkerHandoffIntent]:
    observed_at = _parse_platform_time(intent.terminal_observed_at)
    if (
        intent.terminal_observed_at
        and (observed_at is None or observed_at.tzinfo is None)
    ):
        raise PlatformError(
            f"Worker handoff terminal observation is invalid for work item "
            f"{item_id}")
    if observed_at is None:
        intent = replace(
            intent, terminal_observed_at=_utcnow().isoformat())
        store.update_work_item_metadata(item_id, worker_handoff=intent)
        return "pending-submit", intent
    if (_utcnow() - observed_at).total_seconds() < _HANDOFF_TERMINAL_GRACE_SECONDS:
        return "pending-submit", intent
    return "finished-without-submit", intent


def _attachment_is_causal_for_run(observation, run, agent_id: str) -> bool:
    if observation.task_id:
        return bool(
            observation.task_id == run.id
            and (
                not observation.uploader_id
                or observation.uploader_id == agent_id
            )
        )
    created = _parse_platform_time(observation.created_at)
    run_started = _parse_platform_time(run.created_at)
    run_ended = _parse_platform_time(run.updated_at)
    return bool(
        observation.uploader_type == "agent"
        and observation.uploader_id == agent_id
        and created is not None
        and run_started is not None
        and run_ended is not None
        and run_started <= created <= run_ended
    )


def _observe_delivery_projection(store: WorkItemStore, item, attachment=None):
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    pr_url = str(artifacts.get("pr_url") or artifacts.get("pr") or "").strip()
    submitted_head = str(artifacts.get("head_sha") or "").strip()
    if not pr_url or not submitted_head or not verification_ref:
        raise PlatformError(f"Worker delivery is incomplete for work item {item.id}")

    attachment = attachment or store.observe_verification_attachment(
        item.id, verification_ref)
    try:
        attachment_verification = yaml.safe_load(
            attachment.content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PlatformError(
            f"Verification attachment cannot be parsed for work item {item.id}") from exc
    if attachment_verification != item.verification:
        raise PlatformError(
            f"Parsed verification projection does not match downloaded attachment "
            f"for work item {item.id}")

    readiness = store.read_pull_request_readiness(pr_url)
    if isinstance(readiness, PullRequestReadinessFailure):
        raise PlatformError(
            f"Remote PR HEAD observation failed for {pr_url}: {readiness.detail}")
    if not isinstance(readiness, PullRequestReadiness) or not readiness.head_sha:
        raise PlatformError(f"Remote PR HEAD is unavailable for {pr_url}")
    if readiness.head_sha != submitted_head:
        raise PlatformError(
            f"Remote PR HEAD changed after Worker submit for {pr_url}: "
            f"submitted={submitted_head}, current={readiness.head_sha}")
    return attachment, pr_url, readiness.head_sha


def _seal_worker_delivery(
    store: WorkItemStore,
    manifest: Manifest,
    key: str,
    current,
    intent: WorkerHandoffIntent,
    target_run,
    *,
    attachment=None,
) -> DeliveryIdentity:
    attachment, pr_url, remote_head = _observe_delivery_projection(
        store, current, attachment)
    if (
        intent.baseline_verification_attachment_id
        and attachment.attachment_id
        == intent.baseline_verification_attachment_id
    ):
        raise PlatformError(
            f"Worker handoff {intent.generation} did not create a new verification attachment")
    if not _attachment_is_causal_for_run(
        attachment, target_run, str(intent.target_agent_id),
    ):
        raise PlatformError(
            f"Verification attachment is not causally bound to Worker Run "
            f"{target_run.id}")
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation=intent.generation,
        worker=intent.target_worker,
        agent_id=intent.target_agent_id,
        run_id=target_run.id,
        pr_url=pr_url,
        pr_head_sha=remote_head,
        verification_sha256=attachment.sha256,
        verification_attachment_id=attachment.attachment_id,
        verification_comment_id=attachment.comment_id,
        verification_uploader_id=attachment.uploader_id,
        verification_uploader_type=attachment.uploader_type,
        verification_task_id=attachment.task_id,
        verification_created_at=attachment.created_at,
    )


def _validate_controller_sealed_delivery(
    store: WorkItemStore, item,
) -> None:
    identity = _delivery_identity(item)
    if identity is None:
        return
    if not identity.is_complete():
        raise PlatformError(
            f"Controller-sealed delivery identity is incomplete for work item {item.id}")
    attachment, pr_url, remote_head = _observe_delivery_projection(store, item)
    if (
        identity.pr_url != pr_url
        or identity.pr_head_sha != remote_head
        or identity.verification_attachment_id != attachment.attachment_id
        or identity.verification_comment_id != attachment.comment_id
    ):
        raise PlatformError(
            f"Current delivery projection does not match sealed identity for "
            f"work item {item.id}")
    if (
        attachment.sha256 != identity.verification_sha256
        or attachment.attachment_id != identity.verification_attachment_id
        or attachment.comment_id != identity.verification_comment_id
        or attachment.uploader_id != identity.verification_uploader_id
        or attachment.uploader_type != identity.verification_uploader_type
        or attachment.task_id != identity.verification_task_id
        or attachment.created_at != identity.verification_created_at
    ):
        raise PlatformError(
            f"Verification attachment no longer matches sealed identity for "
            f"work item {item.id}")


def _observe_handoff_verification(
    store: WorkItemStore,
    projection: WorkItemControlProjection,
):
    """Hydrate verification from the same attachment observation used to seal."""
    item = projection.work_item
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    attachment = store.observe_verification_attachment(item.id, verification_ref)
    try:
        verification = yaml.safe_load(attachment.content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PlatformError(
            f"Verification attachment cannot be parsed for work item {item.id}") from exc
    if item.verification is not None and item.verification != verification:
        raise PlatformError(
            f"Parsed verification projection does not match downloaded attachment "
            f"for work item {item.id}")
    return (
        replace(
            projection,
            work_item=replace(item, verification=verification),
            deferred_payloads=(
                projection.deferred_payloads - {WorkItemPayload.VERIFICATION}
            ),
        ),
        attachment,
    )


def _control_matches_handoff_candidate(
    item, intent: WorkerHandoffIntent, identity: DeliveryIdentity,
) -> bool:
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    current_intent = item.worker_handoff
    same_causal_intent = bool(
        current_intent is not None
        and replace(current_intent, terminal_observed_at=None)
        == replace(intent, terminal_observed_at=None)
    )
    return bool(
        same_causal_intent
        and _worker_handoff_has_new_delivery(item, intent)
        and identity.pr_url == (artifacts.get("pr_url") or artifacts.get("pr"))
        and identity.pr_head_sha == artifacts.get("head_sha")
        and identity.verification_attachment_id
        == verification_ref.get("attachment_id")
        and identity.verification_comment_id == verification_ref.get("comment_id")
        and identity.verification_sha256 == verification_ref.get("sha256")
    )


def _commit_worker_handoff_delivery(
    store: WorkItemStore,
    item_id: str,
    result: _WorkerHandoffResult,
) -> WorkItemControlProjection:
    identity = result.delivery_identity
    projection = result.projection
    if identity is None or projection is None:
        raise PlatformError(
            f"Worker handoff delivery candidate is incomplete for work item {item_id}")
    current = store.observe_work_item_control(item_id).work_item
    existing = _delivery_identity(current)
    if not _control_matches_handoff_candidate(current, result.intent, identity):
        if existing is None:
            raise _WorkerHandoffCandidateChanged(
                f"Worker handoff delivery candidate changed before commit for "
                f"work item {item_id}")
        raise PlatformError(
            f"Worker handoff delivery candidate changed before commit for "
            f"work item {item_id}")
    if existing is not None and existing.as_dict() != identity.as_dict():
        raise PlatformError(
            f"Persisted delivery identity does not match platform facts for "
            f"handoff {result.intent.generation}")
    if existing is None:
        store.update_work_item_metadata(item_id, delivery_identity=identity)
    persisted = _delivery_identity(
        store.observe_work_item_control(item_id).work_item)
    if persisted is None or persisted.as_dict() != identity.as_dict():
        raise PlatformError(
            f"Controller-sealed delivery identity did not persist for "
            f"handoff {result.intent.generation}")
    store.update_work_item_metadata(item_id, worker_handoff={})
    return replace(
        projection,
        work_item=replace(
            projection.work_item,
            delivery_identity=identity,
            worker_handoff=None,
        ),
    )


def _finalize_worker_handoff_delivery(
    store: WorkItemStore,
    node,
    result: _WorkerHandoffResult,
) -> tuple[Any, list]:
    if result.state != "complete" or result.projection is None:
        raise PlatformError(
            f"Worker handoff is not ready for work item {node.work_item_id}")
    projection = _hydrate_worker_collect_evidence(
        store, result.projection)
    gate_errors = validate_worker_evidence(node, projection.work_item)
    projection = _commit_worker_handoff_delivery(
        store,
        node.work_item_id,
        replace(result, projection=projection),
    )
    return projection.work_item, gate_errors


def _finalize_worker_handoff_or_defer(
    store: WorkItemStore,
    manifest: Manifest,
    key: str,
    node,
    result: _WorkerHandoffResult,
) -> tuple[Any, list] | None:
    try:
        return _finalize_worker_handoff_delivery(store, node, result)
    except _WorkerHandoffCandidateChanged:
        set_node(manifest, key, status="in_progress")
        log.info(
            "worker_handoff_delivery_deferred",
            kind=_DAG_KIND,
            node=key,
            id=node.work_item_id,
            reason="candidate-changed",
        )
        return None


def _observe_worker_handoff(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    key: str,
    intent: WorkerHandoffIntent,
    *,
    projection: WorkItemControlProjection | None = None,
) -> _WorkerHandoffResult:
    """只读观察 handoff 的 Run→submit 因果链。"""
    item_id = manifest.nodes[key].work_item_id
    projection = projection or store.observe_work_item_control(item_id)
    current = projection.work_item
    runs = runtime.list_runs(item_id)
    baseline = set(intent.baseline_direct_run_ids)

    if not intent.is_causally_bound():
        raise PlatformError(
            f"Worker handoff lacks causal identity for work item {item_id}")

    if any(
        run.kind == "direct" and run.id not in baseline
        and run.agent_id != intent.target_agent_id
        for run in runs
    ):
        raise PlatformError(
            f"Worker handoff observed non-causal direct Runs for work item {item_id}")
    observed = _observe_direct_run_attempt(
        runs, intent.target_agent_id,
        baseline_direct_run_ids=intent.baseline_direct_run_ids,
        target_run_id=intent.target_run_id)
    if observed.state == "unexpected":
        raise PlatformError(observed.detail)
    if observed.target_run_id and observed.target_run_id != intent.target_run_id:
        intent = replace(intent, target_run_id=observed.target_run_id)
        store.update_work_item_metadata(item_id, worker_handoff=intent)
    if observed.state == "active":
        return _WorkerHandoffResult("waiting", intent, projection)
    if observed.state == "terminal":
        target_run = observed.terminal.run
        if target_run.status == "failed":
            state = (
                "transient-failure"
                if _is_retryable_transient_run_failure(target_run)
                else "nonretryable-failure"
            )
            return _WorkerHandoffResult(
                state, intent, projection)
        if (
            manifest.nodes[key].reviewer is None
            and manifest.nodes[key].contract is None
            and current.status == WorkItemStatus.DONE
            and isinstance(current.artifacts, dict)
            and current.artifacts
        ):
            return _WorkerHandoffResult(
                "complete-unsealed", intent, projection)
        if not _worker_handoff_has_new_delivery(current, intent):
            state, intent = _observe_terminal_without_submit(
                store, item_id, intent)
            return _WorkerHandoffResult(state, intent, projection)
        projection, attachment = _observe_handoff_verification(
            store, projection)
        current = projection.work_item
        sealed = _seal_worker_delivery(
            store,
            manifest,
            key,
            current,
            intent,
            target_run,
            attachment=attachment,
        )
        existing = _delivery_identity(current)
        if existing is not None and existing.as_dict() != sealed.as_dict():
            raise PlatformError(
                f"Persisted delivery identity does not match platform facts for "
                f"handoff {intent.generation}")
        return _WorkerHandoffResult(
            "complete", intent, projection, sealed)
    return _WorkerHandoffResult("missing", intent, projection)


def _review_projection_present(item) -> bool:
    return not _review_projection_is_clear(item)


def _recover_legacy_initial_worker(store, runtime, manifest, key, item, path):
    node = manifest.nodes[key]
    if not (
        node.status == "in_progress"
        and item.status == WorkItemStatus.IN_PROGRESS
        and item.phase == TaskPhase.AUTHORING
        and item.worker_handoff is None
        and not item.agent_run_failed
        and not item.agent_run_finished_without_submit
    ):
        return item, None
    runs = runtime.list_runs(item.id)
    if not runs:
        return item, None
    stable_ids = bool(getattr(
        getattr(runtime, "capabilities", None),
        "stable_direct_run_identity", False))
    has_facts = bool(
        not stable_ids or item.bounces.worker or item.bounces.review
        or item.artifacts or item.verification or item.verification_ref
        or _delivery_identity(item) or _review_projection_present(item)
        or item.reviewer_run_baseline or item.review_obligations
        or item.review_ledger or item.review_continuation)
    worker_id = store.resolve_agent_id(node.worker)
    wrong_actor = any(
        run.kind == "direct" and run.agent_id != worker_id for run in runs)
    observed = _observe_direct_run_attempt(
        runs, worker_id, cutoff_created_at=item.created_at)
    if not wrong_actor and observed.state == "active":
        return item, None
    if (
        not has_facts and not wrong_actor and observed.state == "terminal"
        and observed.terminal.outcome == "transient-failure"
        and observed.terminal.consecutive_runs == 1
    ):
        intent = WorkerHandoffIntent(
            schema=WORKER_HANDOFF_SCHEMA, state="pending",
            target_worker=node.worker, gate="explicit-dispatch",
            source_review_subject_digest=stage_recovery_subject(node, item),
            source_review_round=1, target_review_bounce=0,
            generation=f"handoff-{secrets.token_hex(8)}",
            target_agent_id=worker_id, target_run_id=observed.target_run_id,
            target_worker_bounce=0)
        store.update_work_item_metadata(item.id, worker_handoff=intent)
        return store.observe_work_item_control(item.id).work_item, None
    retry = f"omac node retry {path} {key}"
    reason = ui(
        f"Legacy Worker Run causality is unsafe; inspect Runs, then use `{retry}`.",
        f"旧版 Worker Run 因果关系不安全；请检查 Runs 后执行 `{retry}`。")
    store.update_work_item_metadata(item.id, decision_required={
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": "legacy-worker-handoff-migration-unproven",
        "kind": TaskKind.DEVELOP.value, "phase": TaskPhase.AUTHORING.value,
        "gate": "worker", "resume_issue_id": item.id, "node_id": key,
        "next_action": retry})
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    set_node(manifest, key, status="blocked")
    return item, reason


def _legacy_delivery_requires_retry(manifest, key, node, item) -> bool:
    has_delivery = bool(
        isinstance(item.artifacts, dict) and item.artifacts
        or isinstance(item.verification, dict) and item.verification
        or isinstance(item.verification_ref, dict) and item.verification_ref
    )
    return bool(
        node.reviewer
        and item.phase == TaskPhase.AUTHORING
        and item.bounces.review > 0
        and consumed_bounces(manifest, key, item, "review") == item.bounces.review
        and item.worker_handoff is None
        and _delivery_identity(item) is None
        and has_delivery
    )


def _complete_merge_if_confirmed(
    store: WorkItemStore, runtime: AgentRuntime, manifest: Manifest, key: str,
    retry_limits: dict, config: dict, manifest_path: str,
) -> str:
    node = manifest.nodes[key]
    item = store.get_work_item(node.work_item_id)
    _validate_controller_sealed_delivery(store, item)
    recovering_merge_handoff = (
        merge_bounce_attempt(node.merge_request_state) is not None
        and item.phase == TaskPhase.AUTHORING
        and item.review_verdict is None
        and item.review_report is None
        and item.review_subject_digest is None
    )
    if (
        node.reviewer
        and not recovering_merge_handoff
        and not _review_subject_is_current(
            manifest, key, item,
        )
    ):
        _dispatch_reviewer_for_current_subject(
            store, runtime, manifest, key)
        set_node(manifest, key, status="in_review")
        save_manifest(manifest, manifest_path)
        return "review"
    action = run_merge_delivery(
        config, manifest, key, store, runtime, retry_limits, manifest_path)
    if action == "pass":
        store.update_status(node.work_item_id, WorkItemStatus.DONE)
        set_node(manifest, key, status="done")
        log.info(logsetup.EVT_NODE_DONE, kind=_DAG_KIND, node=key,
                 id=node.work_item_id)
    return action


# ==================== reconcile ====================

def reconcile_with_observations(
    store: WorkItemStore,
    manifest: Manifest,
    manifest_path: str,
    max_parallel: int = 4,
) -> ReconcileResult:
    """逐节点拿 work_item_id 去平台核对真实状态,同步回 manifest。

    - work_item_id 指向的 item 平台不存在 → 清空 work_item_id,标 todo 走新建
    - 平台状态与 manifest 不一致 → 以平台为准写回 manifest

    运行中节点(in_progress/in_review)的终态回收由 collect_results 统一处理
    (证据门 + 阶段交接),reconcile 不同步其状态,避免把平台 DONE 直接写成
    manifest done 而短路证据门和 reviewer 交接。

    一轮 reconcile 是 manifest 侧的原子观察：所有平台读取与状态计算先在
    候选副本上完成。未知的平台/认证结果直接向上传播；只有整轮成功后才
    原子写盘并替换调用者持有的 manifest，避免第 N 个读取失败留下前 N-1
    个节点的部分状态。
    """
    ensure_amendment_apply_complete(manifest, manifest_path)
    observations, pull_requests = _observe_reconcile_inputs(
        store, manifest, max_parallel=max_parallel)
    candidate = copy.deepcopy(manifest)
    changed = _reconcile_candidate(
        store, candidate, manifest_path, observations, pull_requests)
    if changed:
        save_manifest(candidate, manifest_path)
        manifest.meta = candidate.meta
        manifest.nodes = candidate.nodes
    return ReconcileResult(
        changed=changed,
        observations={
            key: (
                None
                if observations.get(key) is _MISSING_WORK_ITEM
                else observations.get(key)
            )
            for key in manifest.nodes
        },
    )


def reconcile(
    store: WorkItemStore,
    manifest: Manifest,
    manifest_path: str,
    max_parallel: int = 4,
) -> bool:
    """Compatibility entry point returning only whether manifest state changed."""
    return reconcile_with_observations(
        store, manifest, manifest_path, max_parallel=max_parallel).changed


def _reconcile_candidate(
    store: WorkItemStore, manifest: Manifest, manifest_path: str,
    observations: Dict[str, Any], pull_requests: Dict[str, Any],
) -> bool:
    """用已完整观察的事实计算候选；此阶段不得再执行平台读取。"""
    changed = False
    for key, node in manifest.nodes.items():
        if not node.work_item_id:
            if confirmed_merge_is_closed(node):
                if node.status != "done":
                    set_node(manifest, key, status="done")
                    changed = True
            elif node.status == "done":
                set_node(manifest, key, status="blocked")
                changed = True
            continue
        observation = observations[key]
        if observation is _MISSING_WORK_ITEM:
            if confirmed_merge_is_closed(node):
                if node.status != "done":
                    set_node(manifest, key, status="done")
                    changed = True
            elif node.status == "done":
                set_node(manifest, key, status="blocked")
                changed = True
            elif node.status not in {"done", "abandoned"}:
                set_node(manifest, key, work_item_id=None, status="todo")
                changed = True
            continue
        item = observation.work_item

        # confirmed merge closure is the sole ordinary-reconcile terminal
        # invariant. Explicit amendment/retry must retire it before any
        # authoring/review recovery is eligible again.
        if confirmed_merge_is_closed(node):
            if (
                item.status != WorkItemStatus.DONE
                or item.platform_assignee_id is not None
            ):
                store.normalize_confirmed_merge(node.work_item_id)
            if node.status != "done":
                set_node(manifest, key, status="done")
                changed = True
            continue

        # reviewer reject 后，worker 可在 manifest 仍 todo/blocked/done 时通过正式
        # work submit 写入新交付。todo 常见于 node retry 后、下一次 tick 前提交。
        # 此时不能把平台 DONE 直接同步成业务 done，必须回到 collect_results
        # 重新过证据门并派发 reviewer。
        if (
            node.status in FAILED_STATUSES or node.status in {"todo", "done"}
        ) and _has_unreviewed_worker_delivery(node, item):
            set_node(manifest, key, status="in_progress")
            changed = True
            continue

        # blocked/failed 是 OMAC 已持久化的失败决策。平台投影可能因为进程在
        # manifest-first 转换后、平台状态写入前崩溃而短暂滞后；不能用这个
        # 旧投影反向解锁节点。显式 node retry 会先把 manifest 改为 todo，
        # 合法的新 worker 交付则由上面的证据分支接管。
        if node.status in FAILED_STATUSES:
            continue

        # todo 是首次派发、operator retry 或 amendment recovery 的显式意图。
        # 若尚无 causal worker handoff，平台上的旧 authoring/review/status 投影
        # 不能反向夺回控制权；新 Worker delivery 已由上面的证据分支处理。
        if (
            node.status == "todo"
            and item.worker_handoff is None
            and item.status != WorkItemStatus.DONE
        ):
            continue

        # 运行中节点的终态回收归 collect_results(证据门 + 阶段交接)
        if node.status in RUNNING_STATUSES:
            continue

        # abandoned 是调用者显式决策(omac node abandon),不归 reconcile 同步,
        # 否则平台侧仍 DONE/BLOCKED 的 work_item 会把 manifest 的 abandoned 覆盖回 done/blocked
        if node.status == "abandoned":
            continue

        # done 是 OMAC 已收口的业务状态。若 worker/平台把投影回退为 in_review/in_progress,
        # 不反向污染 manifest,而是把平台投影修回 done。
        if node.status == "done":
            # 兼容旧版本坏状态:结构合法的 reject 曾可能被误收口为 done。
            # reject 是业务未通过,必须回到 review 回收路径处理有界返工。
            if item.review_verdict == "reject":
                set_node(manifest, key, status="in_review")
                changed = True
                continue
            if getattr(item, "kind", TaskKind.DEVELOP) == TaskKind.DEVELOP:
                pr_url = _pull_request_url(item)
                if not pr_url:
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(node.work_item_id, ui(
                        "⚠️ Develop done requires a PR with confirmed remote merge facts.",
                        "⚠️ develop done 必须有带远端合入确认事实的 PR。"))
                    set_node(manifest, key, status="blocked")
                    changed = True
                    continue
                if not merge_request_state_is_valid(node.merge_request_state):
                    block_unproven_merge_request(
                        node, item, store, manifest_path, key,
                        f"Historical merge request state {node.merge_request_state!r} is invalid.")
                    changed = True
                    continue
                if confirmed_merge_is_closed(node):
                    if item.status != WorkItemStatus.DONE:
                        store.update_status(node.work_item_id, WorkItemStatus.DONE)
                    continue
                observation = pull_requests[key]
                state = _pull_request_state(observation)
                if state == PullRequestState.MERGED and getattr(observation, "merged_at", None):
                    node.merged = True
                    node.merged_at = observation.merged_at
                    node.merge_request_state = None
                    if item.status != WorkItemStatus.DONE:
                        store.update_status(node.work_item_id, WorkItemStatus.DONE)
                    changed = True
                    continue
                if state in {PullRequestState.OPEN, PullRequestState.PENDING}:
                    store.update_status(node.work_item_id, WorkItemStatus.IN_REVIEW)
                    if state == PullRequestState.PENDING:
                        node.merge_request_state = "requested"
                    set_node(manifest, key, status="merging")
                    changed = True
                    continue
                if state == PullRequestState.UNKNOWN:
                    store.update_status(node.work_item_id, WorkItemStatus.IN_REVIEW)
                    set_node(manifest, key, status="merging")
                    changed = True
                    continue
                detail = getattr(observation, "detail", "") or ui(
                    "PR closed without merge or merge facts are unavailable",
                    "PR 未合入即关闭，或无法获得合入事实")
                store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                store.add_comment(node.work_item_id, ui(
                    f"⚠️ Historical done state lacks confirmed merge; refusing closure. {detail}",
                    f"⚠️ 历史 done 状态缺少已确认的合入事实；拒绝收口。{detail}"))
                set_node(manifest, key, status="blocked")
                changed = True
                continue
            if item.review_verdict == "pass-with-nits":
                store.reset_review(node.work_item_id)
            if item.status != WorkItemStatus.DONE:
                store.update_status(node.work_item_id, WorkItemStatus.DONE)
            continue

        platform_status = item.status.value if hasattr(item.status, "value") else str(item.status)
        manifest_status = _PLATFORM_TO_MANIFEST.get(platform_status, platform_status)
        if manifest_status != node.status:
            set_node(manifest, key, status=manifest_status)
            changed = True

    return changed


# ==================== collect_results(SYNC) ====================


def collect_results(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    manifest_path: str,
    retry_limits: dict | None = None,
    config: dict | None = None,
    observations: Dict[str, WorkItemControlProjection | None] | None = None,
) -> Dict[str, str]:
    """SYNC:回收进行中节点的结果。

    返回 {node_key: failure_reason} —— 空 dict 表示无新失败。

    retry_limits: config.retry 解析后的 {ci, review, merge} 上界(None = 全缺省 3)。
    reviewer reject 触发的「回到 worker」回退受 retry_limits["review"] 约束(0 = 立即 blocked)。
    config: 项目配置;用于决定是否启用 ci 门(§7.3)。显式配置 ci.check_command
    或检测到 .github/workflows 时启用,否则跳过。

    in_progress 节点:
      worker DONE + 证据门过 → 有 reviewer: 转 in_review + assign reviewer + wake
                               无 reviewer: 标 done
      worker DONE + 证据门不过 → blocked,失败原因经 add_comment 回贴
      worker FAILED / BLOCKED → blocked
    in_review 节点:
      reviewer pass → merge(if configured) → done;reject → blocked(P4 前先 blocked)
    """
    # Standalone callers retain the complete-read fallback.  ``tick`` passes
    # the fresh, atomic reconcile observations so collection does not repeat
    # the same Issue and attachment reads.
    if observations is None:
        running_observations = {
            key: WorkItemControlProjection(store.get_work_item(node.work_item_id))
            for key, node in manifest.nodes.items()
            if node.status in RUNNING_STATUSES and node.work_item_id
        }
    else:
        running_observations = {}
        for key, node in manifest.nodes.items():
            if node.status not in RUNNING_STATUSES or not node.work_item_id:
                continue
            projection = observations.get(key)
            if projection is None:
                raise PlatformError(
                    f"Fresh reconcile observation is missing for running node {key}")
            required = _build_work_item_hydration_plan(node, projection)
            missing = required & projection.deferred_payloads
            if missing:
                names = ", ".join(sorted(payload.value for payload in missing))
                raise PlatformError(
                    f"Fresh reconcile observation lacks collect evidence for "
                    f"running node {key}: {names}")
            running_observations[key] = projection
    failures: Dict[str, str] = {}
    pending_review: List[Tuple[str, str, str]] = []  # (key, item_id, reviewer)

    limits = dict(DEFAULT_RETRY)
    if retry_limits:
        for k, v in retry_limits.items():
            if k in limits:
                limits[k] = v

    for key, node in manifest.nodes.items():
        if node.status not in RUNNING_STATUSES or not node.work_item_id:
            continue

        projection = running_observations[key]
        item = projection.work_item
        worker_gate_errors = None

        if _legacy_delivery_requires_retry(manifest, key, node, item):
            if any(run.kind == "direct" and run.active
                   for run in runtime.list_runs(node.work_item_id)):
                continue
            retry = f"omac node retry {manifest_path} {key}"
            reason = ui(
                "Legacy rework delivery lacks an immutable submitted head or "
                f"controller-sealed delivery identity. Run `{retry}`; the old "
                "verification and Reviewer verdict cannot be reused.",
                "旧返工交付缺少不可变 submitted head 或 Controller 封存的 "
                f"delivery identity。请运行 `{retry}`；旧 verification 和 "
                "Reviewer verdict 不得复用。",
            )
            decision = {
                "schema": DECISION_REQUIRED_SCHEMA, "reason_code": "legacy-delivery-retry-required",
                "kind": TaskKind.DEVELOP.value, "phase": TaskPhase.AUTHORING.value,
                "gate": "delivery-identity", "resume_issue_id": node.work_item_id,
                "node_id": key, "next_action": retry,
            }
            if item.decision_required != decision:
                store.update_work_item_metadata(
                    node.work_item_id, decision_required=decision)
                store.add_comment(node.work_item_id, reason)
            if item.status != WorkItemStatus.BLOCKED:
                store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
            set_node(manifest, key, status="blocked")
            failures[key] = reason
            continue

        item, legacy_failure = _recover_legacy_initial_worker(
            store, runtime, manifest, key, item, manifest_path)
        if legacy_failure:
            failures[key] = legacy_failure
            continue
        if item is not projection.work_item:
            projection = replace(projection, work_item=item)

        if item.worker_handoff is not None:
            handoff_intent = item.worker_handoff
            if not handoff_intent.is_causally_bound():
                raise PlatformError(
                    f"Worker handoff for {node.work_item_id} predates causal "
                    "delivery identity support; refusing to infer completion")
            handoff = _dispatch_worker_handoff(
                store, runtime, manifest, key, projection=projection)
            if handoff.state in {
                "transient-failure", "nonretryable-failure",
            }:
                failure = _latest_run_failure(
                    runtime,
                    node.work_item_id,
                    handoff.intent.target_agent_id,
                    baseline_direct_run_ids=(
                        handoff.intent.baseline_direct_run_ids),
                    target_run_id=handoff.intent.target_run_id,
                )
                if failure is None:
                    raise PlatformError(
                        f"Failed Worker Run facts changed for work item "
                        f"{node.work_item_id}")
                if failure.classification == "nonretryable" or failure.exhausted:
                    reason = _block_runtime_failure(
                        store, manifest, manifest_path, key, item,
                        "worker", failure)
                    failures[key] = reason
                    log.info(
                        logsetup.EVT_NODE_FAILED,
                        kind=_DAG_KIND,
                        node=key,
                        id=node.work_item_id,
                        reason=reason,
                        run_id=failure.run.id,
                    )
                    continue
                time.sleep(
                    _TRANSIENT_RUNTIME_RETRY_BACKOFF_SECONDS
                    * failure.consecutive_runs)
                retry_intent = _next_worker_handoff_attempt(
                    store, runtime, item, consume_business_bounce=False)
                store.update_work_item_metadata(
                    node.work_item_id, worker_handoff=retry_intent)
                handoff = _dispatch_worker_handoff(
                    store, runtime, manifest, key)
                if handoff.state == "complete":
                    finalized = _finalize_worker_handoff_or_defer(
                        store, manifest, key, node, handoff,
                    )
                    if finalized is None:
                        continue
                    item, worker_gate_errors = finalized
                    set_node(manifest, key, status="in_progress")
                else:
                    set_node(manifest, key, status="in_progress")
                    continue
            elif handoff.state == "finished-without-submit":
                set_node(manifest, key, status="in_progress")
                item = replace(
                    store.observe_work_item_control(
                        node.work_item_id).work_item,
                    agent_run_finished_without_submit=True,
                )
            elif handoff.state == "complete-unsealed":
                store.update_work_item_metadata(
                    node.work_item_id, worker_handoff={})
                item = handoff.projection.work_item
                set_node(manifest, key, status="in_progress")
            elif handoff.state != "complete":
                set_node(manifest, key, status="in_progress")
                continue
            else:
                finalized = _finalize_worker_handoff_or_defer(
                    store, manifest, key, node, handoff)
                if finalized is None:
                    continue
                item, worker_gate_errors = finalized
                set_node(manifest, key, status="in_progress")

        if node.status == "in_review" and item.phase == TaskPhase.AUTHORING:
            # 兼容旧版本 handoff，及 wake 成功、intent 已清但 manifest 尚未落盘
            # 的最后窗口。只有 review projection 已完全清空时才可直达 worker；
            # 否则按 stale delivery 失效旧判定并补 fresh Reviewer。
            if (
                item.status in {
                    WorkItemStatus.IN_REVIEW, WorkItemStatus.IN_PROGRESS,
                }
                and not _review_projection_present(item)
            ):
                continue
            if node.reviewer:
                store.reset_review(node.work_item_id)
                _dispatch_reviewer_for_current_subject(
                    store, runtime, manifest, key)
                set_node(manifest, key, status="in_review")
                continue

        if (
            node.status == "in_progress"
            and node.reviewer
            and item.phase == TaskPhase.REVIEW
        ):
            if not _review_subject_is_current(manifest, key, item):
                pending_review.append(
                    (key, node.work_item_id, node.reviewer))
                continue
            if item.review_verdict:
                set_node(manifest, key, status="in_review")
            else:
                pending_review.append(
                    (key, node.work_item_id, node.reviewer))
                continue

        if (
            node.status == "in_progress"
            and item.status == WorkItemStatus.IN_REVIEW
            and getattr(item, "phase", TaskPhase.AUTHORING) == TaskPhase.AUTHORING
            and item.worker_handoff is None
        ):
            continue

        # ---- in_progress: worker 阶段回收 ----
        if node.status == "in_progress":
            if item.agent_run_finished_without_submit:
                if item.worker_handoff is None:
                    retry = f"omac node retry {manifest_path} {key}"
                    reason = ui(
                        "Worker run ended without a causal handoff delivery. "
                        f"Run `{retry}` to authorize one explicit retry.",
                        "worker run 已结束但没有因果 handoff 交付。"
                        f"请运行 `{retry}` 显式授权一次重试。",
                    )
                    decision = {
                        "schema": DECISION_REQUIRED_SCHEMA,
                        "reason_code": "worker-retry-intent-required",
                        "kind": TaskKind.DEVELOP.value,
                        "phase": TaskPhase.AUTHORING.value,
                        "gate": "worker",
                        "resume_issue_id": node.work_item_id,
                        "node_id": key,
                        "next_action": retry,
                    }
                    if item.decision_required != decision:
                        store.update_work_item_metadata(
                            node.work_item_id, decision_required=decision)
                    store.update_status(
                        node.work_item_id, WorkItemStatus.BLOCKED)
                    set_node(manifest, key, status="blocked")
                    failures[key] = reason
                    continue
                worker_limit = limits.get("worker", DEFAULT_RETRY["worker"])
                cur_bounce = item.bounces.worker
                consumed = consumed_bounces(
                    manifest, key, item, "worker")
                reason = ui(
                    "Worker run ended without delivery through `omac work submit`.",
                    "worker run 已结束但未通过 omac work submit 交付")
                if worker_limit == 0 or consumed >= worker_limit:
                    store.clear_assignment(node.work_item_id)
                    store.update_work_item_metadata(
                        node.work_item_id, worker_handoff={})
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    set_node(manifest, key, status="blocked")
                    failures[key] = ui(
                        f"Worker did not deliver; retry limit {worker_limit} exhausted: {reason}",
                        f"worker 未交付(回退上界 {worker_limit} 已耗尽): {reason}")
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id,
                             reason=ui(
                                 f"Worker delivery retry limit ({worker_limit}) exhausted",
                                 f"worker 未交付回退上界({worker_limit})已耗尽"))
                else:
                    try:
                        retry_intent = _next_worker_handoff_attempt(
                            store, runtime, item)
                        store.clear_assignment(node.work_item_id)
                        store.update_work_item_metadata(
                            node.work_item_id,
                            worker_handoff=retry_intent,
                        )
                        handoff = _dispatch_worker_handoff(
                            store, runtime, manifest, key)
                        if handoff.state == "complete":
                            finalized = _finalize_worker_handoff_or_defer(
                                store, manifest, key, node, handoff,
                            )
                            if finalized is None:
                                continue
                            item, worker_gate_errors = finalized
                        else:
                            set_node(manifest, key, status="in_progress")
                            log.info(
                                logsetup.EVT_REVISION,
                                kind=_DAG_KIND,
                                node=key,
                                id=node.work_item_id,
                                gate="worker",
                                round=cur_bounce + 1,
                                max=worker_limit,
                            )
                            continue
                        set_node(manifest, key, status="in_progress")
                        log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id, gate="worker",
                                 round=cur_bounce + 1, max=worker_limit)
                    except PlatformError as exc:
                        store.update_work_item_metadata(
                            node.work_item_id, worker_bounce=cur_bounce)
                        store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                        store.add_comment(
                            node.work_item_id,
                            ui(
                                f"Failed to return delivery to worker {node.worker}; retry count rolled back: {exc}",
                                f"回退到 worker {node.worker} 继续交付失败"
                                f"(已回滚回退计数): {exc}"),
                        )
                        set_node(manifest, key, status="blocked")
                        failures[key] = ui(
                            f"Failed to return delivery to worker {node.worker}: {exc}",
                            f"回退到 worker {node.worker} 继续交付失败: {exc}")
                continue
            if item.status == WorkItemStatus.IN_PROGRESS:
                continue
            if item.status == WorkItemStatus.DONE:
                gate_errors = (
                    worker_gate_errors
                    if worker_gate_errors is not None
                    else validate_worker_evidence(node, item)
                )
                if gate_errors:
                    reason = "; ".join(gate_errors)
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(node.work_item_id, ui(
                        f"Evidence gate failed: {reason}", f"证据门未通过: {reason}"))
                    set_node(manifest, key, status="blocked")
                    failures[key] = ui(
                        f"Worker evidence gate failed: {reason}",
                        f"worker 证据门未通过: {reason}")
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason=ui(
                                 f"Worker evidence gate: {reason}",
                                 f"worker 证据门: {reason}"))
                    continue
                # worker 证据已过门 → CI 门(§7.3)。配置 ci 时运行 CI,绿才进评审;
                # 失败/超时 → 有界「回到 worker」(retry_limits["ci"])。
                # advance_delivery 已处理节点状态与平台状态切换;返回 'pass' 继续,
                # 'bounce' 已转回 worker(本 tick 不再推进), 'blocked' 则阻止后续。
                ci_action = advance_delivery(
                    config or {}, manifest, key, store, runtime, limits,
                    project_root=_project_root_from_manifest_path(manifest_path))
                if ci_action == "bounce":
                    failures[key] = ui(
                        "CI failed; returned to the worker for resubmission.",
                        "CI 未通过,已转回 worker(上界未耗尽,待重交)")
                    log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, gate="ci")
                    continue
                if ci_action == "blocked":
                    failures[key] = ui(
                        "CI failed and retry.ci is exhausted.",
                        "CI 检查未通过,回退上界(retry.ci)已耗尽")
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason=ui(
                                 "CI retry limit exhausted", "CI 回退上界已耗尽"))
                    continue
                # 只有绑定当前 worker delivery subject 的通过结论才能进入 merge。
                if _current_delivery_passed_review(item):
                    merge_action = _complete_merge_if_confirmed(
                        store, runtime, manifest, key, limits, config or {}, manifest_path)
                    if merge_action == "pass":
                        store.reset_review(node.work_item_id)
                    elif merge_action == "blocked":
                        failures[key] = ui(
                            "Merge failed and retry.merge is exhausted.",
                            "merge 失败,回退上界(retry.merge)已耗尽")
                        log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id, reason=ui(
                                     "Merge retry limit exhausted", "merge 回退上界已耗尽"))
                elif node.reviewer:
                    pending_review.append((key, node.work_item_id, node.reviewer))
                else:
                    merge_action = _complete_merge_if_confirmed(
                        store, runtime, manifest, key, limits, config or {}, manifest_path)
                    if merge_action == "blocked":
                        failures[key] = ui(
                            "Merge outcome cannot be confirmed.",
                            "无法确认 merge 结果。")
                        log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id, reason=ui(
                                     "Merge confirmation failed", "merge 确认失败"))
            elif item.status == WorkItemStatus.FAILED:
                store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                store.add_comment(node.work_item_id, ui(
                    "Worker execution failed", "worker 执行失败"))
                set_node(manifest, key, status="blocked")
                failures[key] = ui("Worker execution failed", "worker 执行失败")
                log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                         id=node.work_item_id, reason=ui(
                             "Worker execution failed", "worker 执行失败"))
            elif item.status == WorkItemStatus.BLOCKED:
                set_node(manifest, key, status="blocked")
                failures[key] = ui(
                    "Worker platform status is blocked", "worker 平台状态 blocked")
                log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                         id=node.work_item_id, reason=ui(
                             "Worker is blocked on the platform", "worker 平台 blocked"))

        # ---- in_review: reviewer 阶段回收 ----
        elif node.status == "in_review":
            if (
                node.reviewer
                and item.phase == TaskPhase.REVIEW
                and not _review_subject_is_current(manifest, key, item)
            ):
                pending_review.append(
                    (key, node.work_item_id, node.reviewer))
                continue
            verdict = item.review_verdict
            if not verdict:
                if node.reviewer:
                    reviewer_id = store.resolve_agent_id(node.reviewer)
                    baseline, baseline_error = (
                        _reviewer_run_baseline_for_observation(
                            store, runtime, item, node.reviewer, reviewer_id)
                    )
                    if baseline_error is not None:
                        failures[key] = _block_reviewer(
                            store, manifest, manifest_path, key, item,
                            "reviewer-run-baseline-unavailable", baseline_error)
                        continue
                    observed = _observe_direct_run_attempt(
                        runtime.list_runs(node.work_item_id), reviewer_id,
                        baseline_direct_run_ids=baseline.baseline_direct_run_ids,
                        cutoff_created_at=baseline.cutoff_created_at,
                        target_run_id=baseline.target_run_id,
                        attempt=baseline.attempt)
                    if observed.state == "unexpected":
                        failures[key] = _block_reviewer(
                            store, manifest, manifest_path, key, item,
                            "reviewer-run-baseline-unavailable", observed.detail)
                        continue
                    if observed.target_run_id != baseline.target_run_id:
                        baseline = replace(
                            baseline, target_run_id=observed.target_run_id)
                        store.update_work_item_metadata(
                            item.id, reviewer_run_baseline=baseline)
                    if (
                        observed.state == "missing" and baseline.attempt > 1
                        and baseline.target_run_id is None
                    ):
                        failures[key] = _block_reviewer(
                            store, manifest, manifest_path, key, item,
                            "reviewer-run-dispatch-unresolved",
                            "persisted retry generation has no target Run")
                        continue
                    reviewer_terminal = observed.terminal
                    retry_kind = None
                    if reviewer_terminal and reviewer_terminal.outcome != "finished-without-submit":
                        reviewer_failure = _RunFailure(
                            reviewer_terminal.run,
                            (
                                "transient"
                                if reviewer_terminal.outcome == "transient-failure"
                                else "nonretryable"
                            ),
                            reviewer_terminal.consecutive_runs,
                        )
                        if (
                            reviewer_failure.classification == "nonretryable"
                            or reviewer_failure.exhausted
                        ):
                            failures[key] = (
                                _block_runtime_failure(
                                    store, manifest, manifest_path, key, item,
                                    "reviewer", reviewer_failure)
                            )
                            continue
                        time.sleep(_TRANSIENT_RUNTIME_RETRY_BACKOFF_SECONDS)
                        retry_kind = "infrastructure_retry"
                    elif reviewer_terminal:
                        grace_state = _reviewer_no_submit_grace_state(
                            store, runtime, item, baseline, reviewer_terminal)
                        if grace_state in {"submitted", "waiting"}:
                            continue
                        if grace_state == "unavailable":
                            failures[key] = _block_reviewer(
                                store, manifest, manifest_path, key, item,
                                "reviewer-run-terminal-time-unavailable",
                                "terminal timestamp is unavailable",
                                reviewer_terminal.run.id)
                            continue
                        if (
                            reviewer_terminal.consecutive_runs
                            >= _TRANSIENT_RUNTIME_MAX_RUNS
                        ):
                            failures[key] = _block_reviewer(
                                store, manifest, manifest_path, key, item,
                                "reviewer-run-no-submit-retry-exhausted",
                                "reviewer exhausted no-submit attempts",
                                reviewer_terminal.run.id)
                            continue
                        retry_kind = "finished_without_submit"
                    if retry_kind:
                        retry_error = _retry_reviewer_attempt(
                            store, runtime, node, item, baseline)
                        if retry_error:
                            failures[key] = _block_reviewer(
                                store, manifest, manifest_path, key, item,
                                "reviewer-run-dispatch-unresolved", retry_error)
                            continue
                        set_node(manifest, key, status="in_review")
                        log.info(
                            logsetup.EVT_REVIEW_DISPATCH, kind=_DAG_KIND,
                            node=key, id=node.work_item_id,
                            reviewer=node.reviewer, recovered=True,
                            **{retry_kind: True})
                        continue
                if _reviewer_run_needs_resume(item):
                    try:
                        if _resume_reviewer_run(store, runtime, node):
                            set_node(manifest, key, status="in_review")
                            log.info(logsetup.EVT_REVIEW_DISPATCH, kind=_DAG_KIND,
                                     node=key, id=node.work_item_id,
                                     reviewer=node.reviewer, recovered=True)
                            continue
                    except PlatformError as exc:
                        store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                        store.add_comment(node.work_item_id, ui(
                            f"Failed to resume reviewer {node.reviewer}: {exc}",
                            f"恢复 reviewer {node.reviewer} 失败: {exc}"))
                        set_node(manifest, key, status="blocked")
                        failures[key] = ui(
                            f"Failed to resume reviewer {node.reviewer}: {exc}",
                            f"恢复 reviewer {node.reviewer} 失败: {exc}")
                        continue
                # reviewer 已落终态但缺结构化 review_verdict → blocked(无证据不予通过)
                if item.status in (WorkItemStatus.DONE, WorkItemStatus.FAILED, WorkItemStatus.BLOCKED):
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(
                        node.work_item_id,
                        ui(
                            f"Reviewer platform status is {item.status.value}, but structured review_verdict evidence is missing.",
                            f"reviewer 平台 {item.status.value} 但缺 review_verdict 结构化证据"),
                    )
                    set_node(manifest, key, status="blocked")
                    failures[key] = ui(
                        "Reviewer is missing review_verdict", "reviewer 缺 review_verdict")
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason=ui(
                                 "Reviewer is missing review_verdict", "reviewer 缺 review_verdict"))
                continue

            decision = item.decision_required
            if (
                isinstance(decision, dict)
                and decision.get("reason_code")
                == "reviewer-run-baseline-unavailable"
            ):
                reviewer_id = store.resolve_agent_id(node.reviewer)
                marker_error = _delayed_reviewer_recovery_marker_error(
                    manifest, key, item, node.reviewer, reviewer_id,
                    require_target=True,
                )
                if marker_error is not None:
                    failures[key] = _block_reviewer(
                        store, manifest, manifest_path, key, item,
                        "reviewer-run-baseline-unavailable", marker_error)
                    continue
                # The dedicated decision is the durable recovery marker. Only
                # the Runner that is about to consume this exact verdict clears
                # it. An unknown write result stops this tick; restart either
                # retries the marker clear or observes it cleared and consumes
                # the still-preserved verdict normally.
                store.update_work_item_metadata(
                    item.id, decision_required={})

            log.info(logsetup.EVT_VERDICT, kind=_DAG_KIND, node=key,
                     id=node.work_item_id, verdict=verdict)
            gate_errors = validate_review_evidence(node, item)
            configured_review_limit = limits.get(
                "review", DEFAULT_RETRY["review"])
            rework_budget = review_rework_budget(
                manifest, key, item, configured_review_limit)
            if verdict == "reject" and not gate_errors:
                boundary_conflicts = contract_boundary_conflicts(
                    manifest, node, item)
                if boundary_conflicts:
                    decision = build_contract_boundary_decision(
                        item, node, boundary_conflicts)
                    store.update_work_item_metadata(
                        node.work_item_id,
                        decision_required=decision,
                        phase=TaskPhase.REVIEW,
                    )
                    store.update_status(
                        node.work_item_id, WorkItemStatus.BLOCKED)
                    set_node(manifest, key, status="blocked")
                    failures[key] = ui(
                        "Reviewer required inputs outside the typed node contract; "
                        "operator decision is required before rework.",
                        "Reviewer 要求了 typed node contract 边界之外的输入；"
                        "继续返工前需要人工决策。",
                    )
                    log.info(
                        logsetup.EVT_NEEDS_DECISION,
                        kind=_DAG_KIND,
                        node=key,
                        id=node.work_item_id,
                        gate="review-boundary",
                    )
                    continue
                convergence = review_convergence_decision(item.review_ledger)
                if convergence is not None:
                    failures[key] = _block_review_non_convergence(
                        store, manifest, key, item, convergence)
                    continue
            if verdict == "pass-with-nits" and not gate_errors:
                if not rework_budget.allows_rework:
                    failures[key] = _block_review_rework_budget(
                        store,
                        manifest,
                        key,
                        item,
                        rework_budget,
                        gate="review-nits",
                        reason="reviewer returned pass-with-nits",
                    )
                    continue
                handoff = _dispatch_worker_handoff(
                    store, runtime, manifest, key,
                    review_bounce=rework_budget.next_round,
                    gate="review-nits",
                    projection=projection,
                )
                if handoff.state == "complete":
                    _finalize_worker_handoff_or_defer(
                        store, manifest, key, node, handoff)
                set_node(manifest, key, status="in_progress")
                log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                         id=node.work_item_id, gate="review-nits")
                continue
            if not gate_errors and verdict != "reject":
                # reviewer pass → P4.2 自动 merge 门。命令与远端观察均由引擎适配器执行。
                merge_action = _complete_merge_if_confirmed(
                    store, runtime, manifest, key, limits, config or {}, manifest_path)
                if merge_action == "blocked":
                    failures[key] = ui(
                        "Merge failed and retry.merge is exhausted.",
                        "merge 失败,回退上界(retry.merge)已耗尽")
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason=ui(
                                 "Merge retry limit exhausted", "merge 回退上界已耗尽"))
                # else "bounce": 节点已转回 in_progress,本 tick 不再推进。
            else:
                # reviewer reject 或评审证据不合格:有界「回到 worker」回退,
                # 受 retry_limits["review"] 约束。
                reason = "; ".join(gate_errors) if gate_errors else "reviewer reject"
                if not rework_budget.allows_rework:
                    failures[key] = _block_review_rework_budget(
                        store,
                        manifest,
                        key,
                        item,
                        rework_budget,
                        gate=("review-evidence" if gate_errors else "review"),
                        reason=reason,
                    )
                else:
                    # 有界「回到 worker」由持久化 intent 串起各 checkpoint；
                    # 任一步结果未知都保留 intent 与绝对 bounce，交给 restart 幂等续跑。
                    handoff = _dispatch_worker_handoff(
                        store, runtime, manifest, key,
                        review_bounce=rework_budget.next_round,
                        gate="review",
                        projection=projection,
                    )
                    if handoff.state == "complete":
                        _finalize_worker_handoff_or_defer(
                            store, manifest, key, node, handoff)
                    set_node(manifest, key, status="in_progress")
                    log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, gate="review",
                             round=rework_budget.next_round,
                             max=rework_budget.authorized_through_round)

        elif node.status == "merging":
            merge_action = _complete_merge_if_confirmed(
                store, runtime, manifest, key, limits,
                config or {}, manifest_path)
            if merge_action == "blocked":
                failures[key] = ui(
                    "Merge outcome cannot be confirmed.",
                    "无法确认 merge 结果。")
                log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                         id=node.work_item_id, reason=ui(
                             "Merge confirmation failed", "merge 确认失败"))

    # ---- reviewer 阶段过渡(遍历后执行,避免改 manifest 影响遍历)----
    for key, item_id, reviewer in pending_review:
        try:
            dispatched = _dispatch_reviewer_for_current_subject(
                store, runtime, manifest, key)
            set_node(manifest, key, status="in_review")
            if dispatched:
                log.info(logsetup.EVT_REVIEW_DISPATCH, kind=_DAG_KIND, node=key,
                         id=item_id, reviewer=reviewer)
        except _ReviewerDispatchUnresolved as exc:
            failures[key] = _block_reviewer(
                store, manifest, manifest_path, key,
                store.get_work_item(item_id),
                "reviewer-run-dispatch-unresolved", str(exc))
        except PlatformError as exc:
            failures[key] = _block_reviewer(
                store, manifest, manifest_path, key,
                store.get_work_item(item_id),
                "reviewer-run-baseline-unavailable", str(exc))

    if failures or pending_review:
        save_manifest(manifest, manifest_path)

    return failures


# ==================== 失败隔离 + 就绪计算(DECIDE) ====================

def _mark_downstream_blocked(
    manifest: Manifest, manifest_path: str, failed: Set[str],
) -> Set[str]:
    """失败隔离:将失败节点的(传递)下游标记为 blocked。返回新标记的节点集合。"""
    snapshot = _build_snapshot(manifest)
    downstream = graph.downstream_of(snapshot, failed)
    newly_blocked: Set[str] = set()
    for key in downstream:
        node = manifest.nodes[key]
        if node.status == "done" or confirmed_merge_is_closed(node):
            if node.status != "done":
                set_node(manifest, key, status="done")
            continue
        if node.status not in TERMINAL_STATUSES:
            set_node(manifest, key, status="blocked")
            newly_blocked.add(key)
    if newly_blocked:
        save_manifest(manifest, manifest_path)
        log.info(logsetup.EVT_CASCADE_BLOCKED, kind=_DAG_KIND,
                 ids=sorted(newly_blocked), cause=sorted(failed))
    return newly_blocked


# ==================== DISPATCH ====================

def _develop_dag_key(manifest: Manifest, node_key: str) -> str:
    """开发节点 dag_key 带 manifest 实例 key,避免不同 plan 流水线节点重名。"""
    dag_key = (manifest.meta.get("dag_key") or "").strip()
    return f"{dag_key}/{node_key}" if dag_key else node_key


def _develop_source_refs(manifest: Manifest, node, engine_env) -> List[dict]:
    refs = normalize_source_refs(
        manifest.meta.get("source_issues"),
        labels=[
            ui("Design", "设计方案"),
            ui("Acceptance document", "验收文档"),
            ui("Task decomposition", "任务拆解"),
        ],
        engine_env=engine_env,
    )
    dependency_refs = []
    for dependency_key in node.blocked_by:
        dependency = manifest.nodes.get(dependency_key)
        if dependency is None or not dependency.work_item_id:
            continue
        dependency_refs.append({
            "label": ui(
                f"Prerequisite implementation · {dependency.title or dependency_key}",
                f"前置开发任务 · {dependency.title or dependency_key}"),
            "issue_id": dependency.work_item_id,
        })
    refs.extend(normalize_source_refs(dependency_refs, engine_env=engine_env))
    return refs


def _refresh_develop_issue_body(
    store: WorkItemStore, manifest: Manifest, key: str, *, phase: TaskPhase,
) -> None:
    item_id, metadata = _develop_issue_body_metadata(
        store, manifest, key, phase=phase)
    store.update_work_item_metadata(item_id, **metadata)


def _develop_issue_body_metadata(
    store: WorkItemStore,
    manifest: Manifest,
    key: str,
    *,
    phase: TaskPhase,
    item=None,
) -> tuple[str, Dict[str, Any]]:
    node = manifest.nodes[key]
    item = item or store.get_work_item(node.work_item_id)
    env = _store_env(store)
    refs = _develop_source_refs(manifest, node, env)
    return item.id, {
        "description": render_issue_body(
            node, node.contract, TaskKind.DEVELOP, item.id,
            source_refs=refs, engine_env=env,
            issue_key=getattr(item, "identifier", None),
            language=current_language(), phase=phase,
        ),
        "source_refs": refs,
        "blocked_by": list(node.blocked_by),
    }


def _dispatch(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    manifest_path: str,
    ready: List[str],
    max_parallel: int,
) -> List[str]:
    """派发就绪节点(受 max_parallel - 进行中数约束)。

    无 work_item_id 时先创建并回填 manifest；新建与复用 issue 随后都先
    持久化/复用 WorkerHandoffIntent，再由统一 handoff 路径补齐
    status/assign/wake。
    """
    workspace_id = store.config.workspace_id
    running_count = sum(
        1 for n in manifest.nodes.values() if n.status in RUNNING_STATUSES
    )
    slots = max(0, max_parallel - running_count)
    to_dispatch = ready[:slots]

    dispatched: List[str] = []
    manifest_changed = False
    for key in to_dispatch:
        node = manifest.nodes[key]
        worker = node.worker

        # 建工单(若无)。显式 node retry 会保留 work_item_id;这种复用路径也必须
        # 刷新 body/source refs,否则 manifest 已修订而平台 issue 仍携带旧约束。
        is_new_item = not node.work_item_id
        if not node.work_item_id:
            item = store.create_work_item(
                workspace_id=workspace_id,
                title=node.title or key,
                description=node.description or f"Task {key}",
                dag_key=_develop_dag_key(manifest, key),
                worker=worker,
                reviewer=node.reviewer,
                blocked_by=list(node.blocked_by),
            )
            set_node(manifest, key, work_item_id=item.id)
            manifest_changed = True
        else:
            item = store.observe_work_item_control(
                node.work_item_id).work_item

        # contract 附件只在首次建单时发布,避免 retry 追加系统评论触发平行 run。
        # retry 的 scope/说明变化通过静默刷新 issue body 生效。
        if is_new_item:
            if node.contract is not None:
                store.set_node_contract(item.id, node.contract)

        try:
            handoff = _dispatch_worker_handoff(
                store,
                runtime,
                manifest,
                key,
                review_bounce=item.bounces.review,
                gate="explicit-dispatch",
            )
        except PlatformError as exc:
            store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
            store.add_comment(node.work_item_id, ui(
                f"Failed to wake worker {worker}: {exc}",
                f"唤醒 worker {worker} 失败: {exc}"))
            set_node(manifest, key, status="blocked")
            manifest_changed = True
            log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                     id=node.work_item_id, reason=ui(
                         f"Failed to wake worker {worker}", f"唤醒 worker {worker} 失败"))
            continue

        if handoff.state == "pending-initialization":
            set_node(manifest, key, status="todo")
            manifest_changed = True
            log.warning(
                "worker_handoff_initialization_pending",
                kind=_DAG_KIND,
                node=key,
                id=node.work_item_id,
                worker=worker,
            )
            continue

        if handoff.state == "pending-preparation":
            set_node(manifest, key, status="in_progress")
            manifest_changed = True
            log.warning(
                "worker_handoff_preparation_pending",
                kind=_DAG_KIND,
                node=key,
                id=node.work_item_id,
                worker=worker,
            )
            continue

        set_node(manifest, key, status="in_progress")
        manifest_changed = True
        log.info(logsetup.EVT_DISPATCH, kind=_DAG_KIND, node=key,
                 id=node.work_item_id, worker=worker, handoff=handoff.state)
        dispatched.append(key)

    if manifest_changed:
        save_manifest(manifest, manifest_path)

    return dispatched


# ==================== tick(单轮完整推进) ====================

def _maybe_unblock(manifest: Manifest, manifest_path: str) -> bool:
    """将「因上游失败而被隔离的 blocked 下游」解锁回 todo,使其可被重派。

    判断依据:
      - status == blocked
      - 所有 blocked_by 依赖均已满足(done 或 abandoned,见 graph.SATISFIED)
      - 该节点自身从未真正派发过(work_item_id 为空,说明是上游失败在 todo 阶段标 blocked)
    自身失败(work_item_id 非空)的节点不在此处自动解锁——必须经 ``omac node retry`` 显式决策,
    捍卫 §2.4「重试是显式决策,废除自动重试」的红线。
    """
    changed = False
    newly_unblocked: List[str] = []
    for key, node in list(manifest.nodes.items()):
        if node.status != "blocked" or node.work_item_id:
            continue
        deps = node.blocked_by
        if not deps:
            continue
        if all(
            b in manifest.nodes and manifest.nodes[b].status in graph.SATISFIED
            for b in deps
        ):
            set_node(manifest, key, work_item_id=None, status="todo")
            newly_unblocked.append(key)
            changed = True
    if changed:
        save_manifest(manifest, manifest_path)
        log.info(logsetup.EVT_UNBLOCK, kind=_DAG_KIND, ids=sorted(newly_unblocked))
    return changed


def _active_formal_run_nodes(
    runtime: AgentRuntime,
    manifest: Manifest,
    observations: Dict[str, WorkItemControlProjection | None],
) -> List[str]:
    """Use the reconcile snapshot to prove causally bound active Runs."""
    active = []
    for key, node in manifest.nodes.items():
        projection = observations.get(key)
        if not node.work_item_id or projection is None:
            continue
        item = projection.work_item
        if item.id != node.work_item_id or item.dag_key != key:
            continue
        attempt = None
        if item.phase == TaskPhase.AUTHORING:
            intent = item.worker_handoff
            if (
                intent is not None
                and intent.is_causally_bound()
                and intent.target_run_id
                and intent.target_worker == node.worker
            ):
                attempt = _observe_direct_run_attempt(
                    runtime.list_runs(item.id),
                    intent.target_agent_id,
                    baseline_direct_run_ids=intent.baseline_direct_run_ids,
                    target_run_id=intent.target_run_id,
                )
        elif item.phase == TaskPhase.REVIEW:
            baseline = item.reviewer_run_baseline
            if (
                baseline is not None
                and baseline.is_causally_bound()
                and baseline.target_run_id
                and baseline.subject_digest == item.review_subject_digest
                and baseline.target_reviewer == node.reviewer
            ):
                attempt = _observe_direct_run_attempt(
                    runtime.list_runs(item.id),
                    baseline.target_agent_id,
                    baseline_direct_run_ids=baseline.baseline_direct_run_ids,
                    cutoff_created_at=baseline.cutoff_created_at,
                    target_run_id=baseline.target_run_id,
                    attempt=baseline.attempt,
                )
        if attempt is not None and attempt.state == "active":
            active.append(key)
    return active


def tick(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    manifest_path: str,
    max_parallel: int = 4,
    retry_limits: dict | None = None,
    config: dict | None = None,
) -> TickResult:
    """执行单轮 tick:reconcile → collect_results → decide → dispatch。

    幂等:全部状态在 manifest + 平台,中断重跑即续跑。

    retry_limits: config.retry 解析后的 {ci, review, merge} 上界(None = 全缺省 3);
    reviewer reject 的「回到 worker」有界退回次数由此控制(见设计文档 §7.3)。
    与「自动重试」不同 —— tick 不会把已 blocked 节点重置为 todo
    (必须经 `omac node retry` 显式决策);retry_limits 是节点内的有界往返。
    """
    ensure_amendment_apply_complete(manifest, manifest_path)

    # 1. Reconcile: 平台状态同步回 manifest
    reconcile_result = reconcile_with_observations(
        store, manifest, manifest_path, max_parallel=max_parallel)

    # 2. SYNC: 回收进行中节点的结果
    new_failures = collect_results(store, runtime, manifest, manifest_path,
                                   retry_limits=retry_limits, config=config,
                                   observations=reconcile_result.observations)

    # 3. 收集全部失败节点(含本轮新失败 + 历史已 blocked/failed)
    all_failed: Set[str] = set(new_failures.keys())
    for key, node in manifest.nodes.items():
        if node.status in FAILED_STATUSES:
            all_failed.add(key)

    # 失败隔离: 下游标 blocked
    if all_failed:
        _mark_downstream_blocked(manifest, manifest_path, all_failed)

    # 3.5 失败解锁: 上游已满足(done/abandon)的「未派发 blocked 下游」解封回 todo
    #    自身失败的节点(work_item_id 非空)不经此处自活——须显式 omac node retry。
    _maybe_unblock(manifest, manifest_path)

    # 4. DECIDE: 计算就绪节点
    snapshot = _build_snapshot(manifest)
    ready = graph.ready_nodes(snapshot)

    # 5. DISPATCH: 派发就绪节点(受 max_parallel 约束)
    dispatched = _dispatch(store, runtime, manifest, manifest_path, ready, max_parallel)

    # 6. 保存 manifest（本地落盘 + 真实引擎回写 git,供跨机 resume 读到最新状态）
    save_manifest(manifest, manifest_path)
    commit_manifest(
        manifest_path, "chore(omac): manifest sync",
        engine_type=getattr(store.config, "engine_type", None))

    # 7. 构建 TickResult
    done = [k for k, n in manifest.nodes.items() if n.status == "done"]
    running = [k for k, n in manifest.nodes.items() if n.status in RUNNING_STATUSES]
    failed_keys = [k for k, n in manifest.nodes.items() if n.status in FAILED_STATUSES]

    # blocked/needs_decision remains authoritative, but it must not stop the
    # foreground controller while a causally bound formal Run is still active.
    # This read-only proof runs only on the otherwise-terminal aggregation path.
    if failed_keys and not running:
        running = _active_formal_run_nodes(
            runtime, manifest, reconcile_result.observations)

    # 状态判定:running 优先(有正式运行继续协调),其次 needs_decision(有失败),
    # 最后 converged(全部 done)
    if running:
        state = "running"
    elif failed_keys:
        state = "needs_decision"
        log.info(logsetup.EVT_NEEDS_DECISION, kind=_DAG_KIND,
                 failed=sorted(failed_keys), done=len(done),
                 total=len(manifest.nodes))
    else:
        state = "converged"
        log.info(logsetup.EVT_CONVERGED, kind=_DAG_KIND,
                 done=len(done), total=len(manifest.nodes))

    # 报告(仅 needs_decision 时使用与 /status 共享的 needs_decision schema)
    report: Dict[str, Any] = {}
    if state == "needs_decision":
        from ..pipeline.report import NEEDS_DECISION_KEYS, build_needs_decision  # 延迟导入,避免循环依赖
        report = build_needs_decision(
            store, manifest, manifest_path, set(failed_keys), evidence=new_failures)
        # 锁定 schema:P5 web / agent 消费方只依赖 NEEDS_DECISION_KEYS
        assert set(report.keys()) == set(NEEDS_DECISION_KEYS)

    return TickResult(
        state=state,
        done=done,
        failed=failed_keys,
        running=running,
        dispatched=dispatched,
        report=report,
    )

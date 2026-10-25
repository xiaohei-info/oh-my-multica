"""Platform-neutral work-item facts: statuses, observations and the WorkItem record."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, TypeAlias

from .taskmeta import (
    Bounces, DeliveryIdentity, ReviewerRunBaseline, TaskKind, TaskPhase,
    WorkerHandoffIntent, current_review_ledger,
)




class WorkItemStatus(Enum):
    """工作单元状态(业务语义)"""
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class WorkItemPayload(Enum):
    """Attachment-backed bodies that may be deferred during control observation."""

    DELIVERABLE = "deliverable"
    PROJECT_RULES = "project_rules"
    VERIFICATION = "verification"
    REVIEW_REPORT = "review_report"
    REVIEW_LEDGER = "review_ledger"
    REVIEW_OBLIGATIONS = "review_obligations"
    MACHINE_FEEDBACK = "machine_feedback"
    CONTRACT = "contract"


class PullRequestState(Enum):
    """远端 PR 的合并闭环结论。"""
    MERGED = "merged"
    OPEN = "open"
    PENDING = "pending"
    CLOSED_UNMERGED = "closed_unmerged"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MergeCommandResult:
    """一次合并请求的本地执行结果；它不是远端合入事实。"""
    succeeded: bool
    exit_code: Optional[int]
    output: str = ""
    timed_out: bool = False


@dataclass(frozen=True)
class PullRequestCheckResult:
    """一次 PR 检查命令的本地结果；由引擎适配器执行。"""
    succeeded: bool
    exit_code: Optional[int]
    output: str = ""
    timed_out: bool = False


class PullRequestReadinessFailureKind(Enum):
    MISSING_CLI = "missing_cli"
    TIMEOUT = "timeout"
    COMMAND = "command"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class PullRequestReadiness:
    """PR 是否可进入 CI/review 的远端读取结果。"""
    is_draft: bool
    state: str
    head_sha: Optional[str] = None


@dataclass(frozen=True)
class PullRequestReadinessFailure:
    """PR readiness 无法证明时的显式失败结果。"""
    category: PullRequestReadinessFailureKind
    detail: str


@dataclass(frozen=True)
class PullRequestObservation:
    """适配器从 PR 远端读取的当前事实。"""
    state: PullRequestState
    merged_at: Optional[str] = None
    detail: str = ""


@dataclass(frozen=True)
class AgentRunObservation:
    """One platform Agent Run with stable identity and trigger kind."""
    id: str
    kind: str
    status: str
    agent_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None
    retry_of_run_id: Optional[str] = None
    trigger_kind: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.status in {
            "queued", "pending", "dispatched", "running", "dispatching",
            "waiting_local_directory", "deferred",
        }

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}

    @property
    def formal(self) -> bool:
        return self.trigger_kind in {"issue_assignment", "rerun"}


@dataclass(frozen=True)
class VerificationAttachmentObservation:
    """平台权威的 verification 附件事实及实际下载内容身份。"""

    attachment_id: str
    comment_id: str
    sha256: str
    content: bytes = b""
    uploader_id: Optional[str] = None
    uploader_type: Optional[str] = None
    task_id: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class ReleaseAssetObservation:
    """Pinned GitHub identities/byte digest; release URLs are not immutable proof."""
    reference: Dict[str, Any]
    content: bytes = b""


@dataclass
class WorkItem:
    """工作单元 — 纯业务数据,不包含平台技术细节(body/labels/state)。

    issue 的范围是一个完整阶段(设计文档 §7.4):产出、评审、回退往返
    都在同一条 work item 上,当前阶段与承担者由 metadata + assignee 表达。
    """
    id: str
    workspace_id: str
    title: str
    description: str
    status: WorkItemStatus

    # 核心元数据
    dag_key: str
    identifier: Optional[str] = None  # 平台短编号,如 PROJ-762;用于 PR 自动关联
    worker: Optional[str] = None
    reviewer: Optional[str] = None
    blocked_by: List[str] = field(default_factory=list)
    wave: Optional[int] = None

    # 执行产物(由 worker 写入)
    artifacts: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None
    verification_ref: Optional[Dict[str, Any]] = None

    # 审核信息(由 reviewer 写入)
    review_verdict: Optional[str] = None
    review_comment: Optional[str] = None
    machine_feedback: Optional[Dict[str, Any]] = None
    machine_feedback_ref: Optional[Dict[str, Any]] = None
    review_report: Optional[Dict[str, Any]] = None
    review_report_ref: Optional[Dict[str, Any]] = None
    review_subject_digest: Optional[str] = None
    review_obligations: List[Dict[str, Any]] = field(default_factory=list)
    review_obligations_ref: Optional[Dict[str, Any]] = None
    review_ledger: Optional[Dict[str, Any]] = None
    review_ledger_ref: Optional[Dict[str, Any]] = None
    review_generation: Optional[str] = None
    review_ledger_generation: Optional[str] = None
    review_continuation: Optional[Dict[str, Any]] = None
    reviewer_run_baseline: Optional[ReviewerRunBaseline] = None
    worker_handoff: Optional[WorkerHandoffIntent] = None
    delivery_identity: Optional[DeliveryIdentity] = None
    decision_required: Optional[Dict[str, Any]] = None
    amendment_attempt: Optional[Dict[str, Any]] = None

    # 验收契约(编排器 dispatch 时下发):worker 读回后用同一套 validator 自校验
    contract: Optional[Dict[str, Any]] = None
    contract_ref: Optional[Dict[str, Any]] = None
    source_refs: List[Dict[str, Any]] = field(default_factory=list)

    # 任务类型×阶段模型(§7.4):issue 自描述——是哪类任务、处于哪个阶段、回退几次。
    # kind 缺省 develop(旧 issue 无 kind 字段时向后兼容);phase 缺省 authoring;
    # bounces 为三类回退计数(CI / 评审 / merge),由 pipeline 经
    # update_work_item_metadata 递增,Store 只存取。
    kind: TaskKind = TaskKind.DEVELOP
    phase: TaskPhase = TaskPhase.AUTHORING
    bounces: Bounces = field(default_factory=Bounces)
    bounce_baseline: Optional[Dict[str, int]] = None
    # 通用交付物:按 kind 承载 plan/acceptance/manifest/acceptance-results 正文
    deliverable: Optional[str] = None
    deliverable_ref: Optional[Dict[str, Any]] = None
    # plan 的第二份正式交付:经评审的项目级开发规范。
    project_rules: Optional[str] = None
    project_rules_ref: Optional[Dict[str, Any]] = None
    # 执行面信号:agent run 已终止但未通过 omac work submit 推进 issue。
    agent_run_finished_without_submit: bool = False
    # reviewer 与 worker 共用 issue 状态，但失败 run 不能一概映射为 worker 失败。
    # REVIEW 阶段由 loop 原地恢复 reviewer，避免丢弃已完成的 worker 交付。
    agent_run_failed: bool = False
    # 平台只读事实。时间戳不表示执行活动；实际 assignee 与未知持久字段则由
    # amendment pristine-shell 门统一视为潜在活动证据并失败关闭。
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    platform_assignee_id: Optional[str] = None
    unknown_persisted_fields: Dict[str, Any] = field(default_factory=dict)
    # Dedicated bounded operator fact for an accepted pass-with-nits review.
    # Kept last to preserve existing positional WorkItem construction.
    review_nits_acceptance: Optional[Dict[str, Any]] = None

    @property
    def requires_decision(self) -> bool:
        """Empty platform tombstones are the same domain fact as no decision."""
        return self.decision_required not in (None, {})

    @property
    def current_review_ledger(self) -> Optional[Dict[str, Any]]:
        """Return the ledger only when it belongs to the current control generation."""
        return current_review_ledger(self)

    def is_completed(self) -> bool:
        return self.status == WorkItemStatus.DONE

    def is_in_progress(self) -> bool:
        return self.status == WorkItemStatus.IN_PROGRESS

    def is_failed(self) -> bool:
        return self.status == WorkItemStatus.FAILED

    def is_blocked(self) -> bool:
        return self.status == WorkItemStatus.BLOCKED


@dataclass(frozen=True)
class WorkItemControlProjection:
    """Latest Issue control facts with attachment bodies explicitly deferred.

    ``work_item`` contains the complete Issue envelope, status, phase, metadata,
    payload references, identities, and unknown persisted fields. A payload in
    ``deferred_payloads`` is declared by reference but has intentionally not
    been downloaded; ``None`` therefore does not mean that the payload is absent.
    """

    work_item: WorkItem
    deferred_payloads: frozenset[WorkItemPayload] = frozenset()

    def has_payload(self, payload: WorkItemPayload) -> bool:
        return (
            payload in self.deferred_payloads
            or getattr(self.work_item, payload.value, None) is not None
        )


WorkItemHydrationPlan: TypeAlias = FrozenSet[WorkItemPayload]

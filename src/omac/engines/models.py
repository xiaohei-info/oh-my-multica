"""通用数据模型 — 引擎无关,纯业务语义。现有资产平移(去 squad 概念)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.taskmeta import Bounces, TaskKind, TaskPhase


class WorkItemStatus(Enum):
    """工作单元状态(业务语义)"""
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class DeliveryCommandOutcome(Enum):
    """CI / merge adapter 的业务结果；平台类错误继续通过异常传播。"""

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class DeliveryAction(Enum):
    """delivery 状态机单步动作。"""

    PASS = "pass"
    BOUNCE = "bounce"
    BLOCKED = "blocked"


class DeliveryBlockReason(Enum):
    """delivery 无法继续时的稳定业务原因。"""

    RETRY_EXHAUSTED = "retry_exhausted"
    ASSIGNMENT_FAILED = "assignment_failed"
    WAKE_FAILED = "wake_failed"
    MISSING_PR = "missing_pr"
    MISSING_REVISION = "missing_revision"


@dataclass(frozen=True)
class DeliveryResult:
    """CI / merge 推进一步后的结构化业务结果。"""

    action: DeliveryAction
    blocked_reason: Optional[DeliveryBlockReason] = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.action is DeliveryAction.BLOCKED and self.blocked_reason is None:
            raise ValueError("blocked delivery result requires blocked_reason")
        if self.action is not DeliveryAction.BLOCKED and self.blocked_reason is not None:
            raise ValueError("non-blocked delivery result cannot carry blocked_reason")


@dataclass(frozen=True)
class DeliveryCommandResult:
    """adapter 执行 CI / merge 命令后的结构化结果。

    ``FAILED`` 只表达真实命令失败，可消耗 retry；认证、网络、平台错误必须
    抛 ``AuthError`` / ``PlatformError``，``TIMED_OUT`` 由 pipeline 转平台错误，
    均不得伪装成 Worker 可修复失败。
    """

    outcome: DeliveryCommandOutcome
    exit_code: Optional[int]
    output: str
    summary: str

    @property
    def passed(self) -> bool:
        return self.outcome is DeliveryCommandOutcome.PASSED

    @property
    def failed(self) -> bool:
        return self.outcome is DeliveryCommandOutcome.FAILED

    @property
    def timed_out(self) -> bool:
        return self.outcome is DeliveryCommandOutcome.TIMED_OUT


@dataclass
class WorkspaceInfo:
    """工作空间信息"""
    id: str
    name: str
    description: Optional[str] = None
    member_count: int = 0


@dataclass
class ProjectInfo:
    """项目信息(Multica project:一个 omac 编排实例绑定一个 project,
    repo 同时挂 project resource 与 workspace registry,所有 issue 归入 project 下)。"""
    id: str
    title: str
    repos: List[str] = field(default_factory=list)   # 目标 repo URL


@dataclass(frozen=True)
class PullRequestSnapshot:
    """由 engine adapter 读取的当前 PR 权威状态与评审范围。"""

    url: str
    is_draft: bool
    state: str
    head_revision: str
    author_login: str = ""                  # PR author.login
    commit_authors: Tuple[str, ...] = ()    # commits[].authors[].login（去重）
    base_revision: str = ""                 # baseRefOid
    changed_files: Tuple[str, ...] = ()     # files[].path（完整 changedFiles）


@dataclass(frozen=True)
class RuntimeTarget:
    """可承载 Agent 的运行时目标。"""
    id: str
    name: str
    type: str
    status: str


@dataclass(frozen=True)
class SkillPackage:
    """待上传 Skill 的完整目录。"""
    name: str
    description: str
    path: Path
    files: Tuple[Path, ...]


@dataclass(frozen=True)
class AgentProvisionSpec:
    """创建 Agent 所需的 Harness 中立输入。"""
    name: str
    description: str
    instructions: str
    runtime_id: str
    skills: List[SkillPackage] = field(default_factory=list)


@dataclass(frozen=True)
class AgentInfo:
    id: str
    name: str


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
    identifier: Optional[str] = None  # 平台短编号,如 AITEAM-762;用于 PR 自动关联
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
    review_report: Optional[Dict[str, Any]] = None
    review_report_ref: Optional[Dict[str, Any]] = None
    decision_required: Optional[Dict[str, Any]] = None
    # 外部 merge 前持久化的恢复凭证；只有权威 PR 状态与此目标完全一致时
    # 才允许在进程重启后收敛 done。
    merge_intent: Optional[Dict[str, str]] = None

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
    # 通用交付物:按 kind 承载 plan/acceptance/manifest/acceptance-results 正文
    deliverable: Optional[str] = None
    deliverable_ref: Optional[Dict[str, Any]] = None
    # plan 的第二份正式交付:经评审的项目级开发规范。
    project_rules: Optional[str] = None
    project_rules_ref: Optional[Dict[str, Any]] = None
    # 执行面信号:agent run 已终止但未通过 omac work submit 推进 issue。
    agent_run_finished_without_submit: bool = False

    def is_completed(self) -> bool:
        return self.status == WorkItemStatus.DONE

    def is_in_progress(self) -> bool:
        return self.status == WorkItemStatus.IN_PROGRESS

    def is_failed(self) -> bool:
        return self.status == WorkItemStatus.FAILED

    def is_blocked(self) -> bool:
        return self.status == WorkItemStatus.BLOCKED


@dataclass
class EngineConfig:
    """引擎配置。角色映射在 omac 侧(config.yaml),这里只有平台定位与轮询参数。"""
    engine_type: str            # 'multica' | 'mock'
    workspace_id: str
    project_id: Optional[str] = None   # multica 必填(issue 归入该 project);mock 忽略
    polling_interval: int = 30
    polling_interval_min: int = 10
    polling_interval_max: int = 300
    extra: Dict[str, Any] = field(default_factory=dict)

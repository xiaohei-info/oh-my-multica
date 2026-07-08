"""通用数据模型 — 引擎无关,纯业务语义。现有资产平移(去 squad 概念)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..core.taskmeta import Bounces, TaskKind, TaskPhase


class WorkItemStatus(Enum):
    """工作单元状态(业务语义)"""
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


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
    worker: Optional[str] = None
    reviewer: Optional[str] = None
    blocked_by: List[str] = field(default_factory=list)
    wave: Optional[int] = None

    # 执行产物(由 worker 写入)
    artifacts: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None

    # 审核信息(由 reviewer 写入)
    review_verdict: Optional[str] = None
    review_comment: Optional[str] = None
    review_report: Optional[Dict[str, Any]] = None
    review_report_ref: Optional[Dict[str, Any]] = None
    decision_required: Optional[Dict[str, Any]] = None

    # 验收契约(编排器 dispatch 时下发):worker 读回后用同一套 validator 自校验
    contract: Optional[Dict[str, Any]] = None

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

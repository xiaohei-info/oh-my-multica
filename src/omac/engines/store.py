"""WorkItemStore — 引擎数据面抽象接口(manifest 驱动,纯业务语义)。

子类实现者须读:每个方法的 docstring 描述了编排层对该方法的**契约保证**。
最重要的契约是**写后读一致性**——任何 update_* / assign 后,
紧接着的 get_work_item 必须返回更新后的值。

措辞保持平台中立(设计文档 §12.4),平台专有说明写在各实现内部。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .models import EngineConfig, WorkItem, WorkItemStatus


class WorkItemStore(ABC):
    """数据面:工作单元的 CRUD / status / metadata / comment / assign。

    核心数据流(编排器视角):
        create_work_item -> 返回 WorkItem.id(= manifest 中的 work_item_id)
        get_work_item(work_item_id) -> 每轮轮询调用,回收进行中节点的结果
        update_status / update_work_item_metadata / assign_work_item -> 改状态
    """

    def __init__(self, config: EngineConfig):
        self.config = config

    # ==================== 成员池 ====================

    @abstractmethod
    def list_members(self, workspace_id: str) -> List[str]:
        """列出工作空间的**全量** agent 名称(不使用小队/分组等平台特有概念)。

        契约:返回的名称与 manifest 中 worker/reviewer 字段按字符串完全匹配,
        否则 lint 报 "not in agent pool"。平台若用 id 标识成员,内部做 name->id
        映射,此方法返回 name。
        """

    # ==================== 工作单元 CRUD ====================

    @abstractmethod
    def create_work_item(
        self,
        workspace_id: str,
        title: str,
        description: str,
        dag_key: str,
        worker: str,
        reviewer: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        wave: Optional[int] = None,
        initial_status: WorkItemStatus = WorkItemStatus.TODO,
    ) -> WorkItem:
        """创建工作单元,返回带**稳定且唯一** id 的 WorkItem。

        编排层把 id 回填进 manifest 的 work_item_id,之后所有查询都走
        get_work_item(id) 精准取,不再全量扫描。metadata 存法由实现决定,
        但存完后立刻 get_work_item(id) 应能读回全部字段。
        title 会由编排层加 [DAG:{dag_key}] 前缀语义,实现内负责拼接。
        """

    @abstractmethod
    def get_work_item(self, item_id: str) -> WorkItem:
        """按 id 精准取回工作单元的完整当前状态(主查询接口,O(1))。

        契约:返回全部业务字段;写后读一致;id 不存在时抛异常
        (编排层 reconcile 据此清空 work_item_id 走新建)。
        """

    @abstractmethod
    def update_work_item_metadata(
        self,
        item_id: str,
        worker: Optional[str] = None,
        reviewer: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        review_verdict: Optional[str] = None,
        review_comment: Optional[str] = None,
        verification: Optional[Dict[str, Any]] = None,
        review_report: Optional[Dict[str, Any]] = None,
    ) -> WorkItem:
        """更新业务元数据(不改 status)。None 的字段不更新;写后读一致。"""

    @abstractmethod
    def set_node_contract(self, item_id: str, contract: Any):
        """把节点 contract 下发到 work item(单一事实源)。

        contract 可为 Contract dataclass 或 dict;执行侧(work show)读回后
        用同一套 validator 自校验。
        """

    @abstractmethod
    def list_work_items(
        self,
        workspace_id: str,
        status: Optional[WorkItemStatus] = None,
    ) -> List[WorkItem]:
        """列出工作单元(进度查看/调试用,非主查询路径)。status 过滤可选。"""

    @abstractmethod
    def add_comment(self, item_id: str, comment: str):
        """追加一条评论(进度报告/回退原因)。失败不应中断编排(编排层会 catch)。"""

    # ==================== 状态和分配 ====================

    @abstractmethod
    def update_status(self, item_id: str, status: WorkItemStatus):
        """更新状态(排他,同一时刻只有一个)。写后读一致。"""

    @abstractmethod
    def assign_work_item(self, item_id: str, assignee: str, role: str):
        """将工作单元分配给成员(role: "worker" | "reviewer"),并同步 metadata。

        这是阶段交接的载体:评审/回退 = 同一 work item 转派新 assignee
        (设计文档 §7.4)。是否由 assign 触发 agent 唤醒是执行面(AgentRuntime)
        的事,本方法只保证数据面生效。
        """

    # ==================== 便捷方法(基类实现) ====================

    def check_member_exists(self, workspace_id: str, member_name: str) -> bool:
        return member_name in self.list_members(workspace_id)

    def mark_in_progress(self, item_id: str):
        self.update_status(item_id, WorkItemStatus.IN_PROGRESS)

    def mark_in_review(self, item_id: str):
        self.update_status(item_id, WorkItemStatus.IN_REVIEW)

    def mark_done(self, item_id: str):
        self.update_status(item_id, WorkItemStatus.DONE)

    def mark_failed(self, item_id: str):
        self.update_status(item_id, WorkItemStatus.FAILED)

    def mark_blocked(self, item_id: str):
        self.update_status(item_id, WorkItemStatus.BLOCKED)

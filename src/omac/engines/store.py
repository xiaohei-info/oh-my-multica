"""WorkItemStore — 引擎数据面抽象接口(manifest 驱动,纯业务语义)。

子类实现者须读:每个方法的 docstring 描述了编排层对该方法的**契约保证**。
最重要的契约是**写后读一致性**——任何 update_* / assign 后,
紧接着的 get_work_item 必须返回更新后的值。

措辞保持平台中立(设计文档 §12.4),平台专有说明写在各实现内部。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..core.taskmeta import TaskKind, TaskPhase
from ..errors import PlatformError, ValidationError
from ..i18n import ui
from .models import EngineConfig, ProjectInfo, WorkItem, WorkItemStatus, WorkspaceInfo


def is_github_pr_url(pr_url: Any) -> bool:
    return (isinstance(pr_url, str)
            and pr_url.startswith("https://github.com/") and "/pull/" in pr_url)


def check_pr_readiness_payload(pr_url: str, payload: Dict[str, Any]) -> None:
    """按 PR readiness 负载(isDraft/state)评估 worker 交付前置门(纯校验,无平台调用)。

    draft 或非 OPEN 抛 ValidationError(报错即教学)。获取负载的平台调用
    (如 gh pr view)只允许封装在各引擎适配器内(§12.4)。
    """
    if payload.get("isDraft") is True:
        raise ValidationError(ui(
            f"GitHub PR is still a draft and cannot enter CI/review/merge: {pr_url}\n"
            "Run `gh pr ready <pr-url>` or mark it ready for review on GitHub.",
            f"GitHub PR 仍是 draft,不能交付给下游 CI/review/merge: {pr_url}\n"
            "请先执行 `gh pr ready <pr-url>` 或在 GitHub 页面 Mark ready for review。"))
    state = payload.get("state")
    if state and state != "OPEN":
        raise ValidationError(ui(
            f"GitHub PR is not OPEN and cannot be delivered: {pr_url} (state={state})",
            f"GitHub PR 状态不是 OPEN,不能交付: {pr_url} (state={state})"))


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

    # ==================== 工作空间发现 ====================

    @abstractmethod
    def list_workspaces(self) -> List[WorkspaceInfo]:
        """列出当前账号可见的全部工作空间(omac init 配置 / --check 体检用)。

        契约:返回 WorkspaceInfo 列表(至少含 id 与 name),供 init 交互式选择;
        平台不可达时抛 PlatformError/AuthError,调用方据此降级为本地体检+警告。
        """

    # ==================== 项目发现 / 创建 ====================

    @abstractmethod
    def list_projects(self, workspace_id: str) -> List[ProjectInfo]:
        """列出 workspace 下的全部 project(omac init 选择已有项目用)。

        契约:返回 ProjectInfo 列表(至少含 id 与 title);平台不可达时抛
        PlatformError/AuthError。一个 omac 编排实例绑定其中一个 project。
        """

    @abstractmethod
    def create_project(
        self, workspace_id: str, title: str,
        repo_urls: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> ProjectInfo:
        """新建 project,并把目标 repo 同时挂到 project 与 workspace registry。

        契约:返回带**稳定 id** 的 ProjectInfo;repo_urls 中每个 URL 应作为
        project resource 存在,并在 workspace 级仓库注册表中存在(init 新建项目
        时默认取当前仓库的 origin),workspace 侧已存在的 URL 不重复登记。
        description 落为 project 描述,init 用它写入 omac 编排横幅,让被派单
        agent 认清入口。
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
        kind: TaskKind = TaskKind.DEVELOP,
    ) -> WorkItem:
        """创建工作单元,返回带**稳定且唯一** id 的 WorkItem。

        编排层把 id 回填进 manifest 的 work_item_id,之后所有查询都走
        get_work_item(id) 精准取,不再全量扫描。metadata 存法由实现决定,
        但存完后立刻 get_work_item(id) 应能读回全部字段。
        title 会由编排层加 [DAG:{dag_key}] 前缀语义,实现内负责拼接。

        kind 写入 metadata(§7.4),缺省 develop —— 未带 kind 的旧调用路径
        与旧 issue 读回均走缺省,向后兼容。phase 流转不在此处,由 pipeline
        经 update_work_item_metadata 推进。
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
        machine_feedback: Optional[Dict[str, Any]] = None,
        machine_feedback_source: Optional[str] = None,
        verification: Optional[Dict[str, Any]] = None,
        verification_source: Optional[str] = None,
        review_report: Optional[Dict[str, Any]] = None,
        review_report_source: Optional[str] = None,
        review_subject_digest: Optional[str] = None,
        review_obligations: Optional[List[Dict[str, Any]]] = None,
        review_ledger: Optional[Dict[str, Any]] = None,
        review_ledger_source: Optional[str] = None,
        review_continuation: Optional[Dict[str, Any]] = None,
        decision_required: Optional[Dict[str, Any]] = None,
        phase: Optional[TaskPhase] = None,
        worker_bounce: Optional[int] = None,
        ci_bounce: Optional[int] = None,
        review_bounce: Optional[int] = None,
        merge_bounce: Optional[int] = None,
        deliverable: Optional[str] = None,
        project_rules: Optional[str] = None,
        source_refs: Optional[List[Dict[str, Any]]] = None,
        description: Optional[str] = None,
    ) -> WorkItem:
        """更新业务元数据(不改 status)。None 的字段不更新;写后读一致。

        phase 流转与回退计数递增由 pipeline 经此方法写入(§7.4):
        - phase:产出(authoring)↔ 评审(review)的阶段切换;
        - worker_bounce/ci_bounce/review_bounce/merge_bounce:回退的绝对值
          (pipeline 读当前值、+1、写回;Store 只存取不做状态机);
        - review_continuation:operator 明确授权的绝对 review round 上限；
          不清零 review_bounce，也不写项目配置。
        - deliverable:按 kind 承载 plan/acceptance/manifest 等交付正文。
        - project_rules:plan 的项目级开发规范交付正文。
        - description:回填 Human-first issue 正文(顶部单一 bootstrap 嵌入真实 id)。
        """

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
        """追加系统说明(进度报告/回退原因)，不得把它解释为新的 agent 输入。

        平台若会通过评论唤醒当前 assignee，适配器必须先解除分配；真正的执行
        交接只允许通过 assign_work_item + AgentRuntime.wake 发生。
        """

    # ==================== 状态和分配 ====================

    @abstractmethod
    def update_status(self, item_id: str, status: WorkItemStatus):
        """更新状态(排他,同一时刻只有一个)。写后读一致。"""

    @abstractmethod
    def reset_review(self, item_id: str):
        """回退到 worker 时清除 reviewer 侧判定(verdict/comment/report)并重置为 authoring。

        让重新提交后的节点再次接受评审,避免旧 verdict 立即再次触发 reject。
        """

    @abstractmethod
    def prepare_review_cycle(self, item_id: str, subject_digest: str) -> WorkItem:
        """绑定当前评审对象；对象变化时清除旧 verdict/report，保持 review 阶段。"""

    @abstractmethod
    def assign_work_item(self, item_id: str, assignee: str, role: str):
        """将工作单元分配给成员(role: "worker" | "reviewer"),并同步 metadata。

        这是阶段交接的载体:评审/回退 = 同一 work item 转派新 assignee
        (设计文档 §7.4)。是否由 assign 触发 agent 唤醒是执行面(AgentRuntime)
        的事,本方法只保证数据面生效。
        """

    @abstractmethod
    def clear_assignment(self, item_id: str) -> None:
        """解除当前 Agent assignment，但保留 worker/review 交付和判定证据。"""

    # ==================== 便捷方法(基类实现) ====================

    def publish_draft_pr(self, item_id: str, *, branch: str, tip_sha: str) -> str:
        """数据面:为评审已通过的 branch + 精确 tip 发布 draft PR,返回 PR URL。

        review-before-PR 适配(delivery.review_before_pr)的评审后发布原语。
        平台 CLI 调用(如 gh pr create --draft)只允许封装在各引擎适配器内
        (§12.4);不支持该能力的引擎保持本基类实现 —— 抛 PlatformError,
        由 pipeline.delivery.run_pr_publish 转为 blocked(报错即教学)。
        """
        raise PlatformError(ui(
            f"Engine {self.config.engine_type!r} does not support draft PR "
            "publication (publish_draft_pr)",
            f"引擎 {self.config.engine_type!r} 不支持 draft PR 发布"
            "(publish_draft_pr)"))

    def validate_pr_ready_for_handoff(self, pr_url: str) -> None:
        """数据面:worker 交付前置门 —— GitHub PR 必须 ready(非 draft 且 OPEN),
        否则不进入 CI/review/merge。

        非 GitHub URL 无可校验,直接放行。GitHub URL 而引擎不支持该检查时
        fail-closed(抛 ValidationError,报错即教学);支持的平台在适配器内
        覆盖本方法,gh 等平台 CLI 调用只允许封装在适配器内(§12.4 红线)。
        """
        if not is_github_pr_url(pr_url):
            return
        raise ValidationError(ui(
            f"Engine {self.config.engine_type!r} cannot verify GitHub PR "
            f"readiness for {pr_url} (validate_pr_ready_for_handoff). "
            "Use an engine adapter that supports GitHub PR readiness checks.",
            f"引擎 {self.config.engine_type!r} 无法校验 GitHub PR ready 状态"
            f"({pr_url}) —— 请使用支持 GitHub PR ready 检查的引擎适配器。"))

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

    def cancel_work_item(self, item_id: str) -> None:
        """取消/作废工作单元(清理扫尾用)—— 从活跃视图移除。

        数据面清理原语:测试跑完扫尾自身创建的 work item、node abandon 均可复用,
        保证不留垃圾(幂等)。缺省退化为置 BLOCKED(可移植到无原生 cancelled 的
        平台);平台有原生「cancelled」态时应覆盖为精确语义。
        """
        self.update_status(item_id, WorkItemStatus.BLOCKED)

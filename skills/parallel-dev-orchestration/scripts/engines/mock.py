"""
Mock 引擎实现 - 用于测试（自动完成任务）
"""
import time
from typing import List, Dict, Any, Optional
from .base import CollaborationEngine
from .models import WorkspaceInfo, WorkItem, WorkItemStatus, EngineConfig


class MockEngine(CollaborationEngine):
    """Mock 引擎 - 内存模拟，用于测试

    特性：
    - 所有数据存储在内存中，不依赖外部服务
    - 自动模拟任务执行（分配后自动完成）
    """

    def __init__(self, config: EngineConfig):
        super().__init__(config)

        # 内存存储
        self._workspaces: Dict[str, WorkspaceInfo] = {}
        self._members: Dict[str, List[str]] = {}  # workspace_id -> [member_names]
        self._work_items: Dict[str, WorkItem] = {}  # item_id -> WorkItem
        self._next_id = 1

        # 自动完成模拟
        self._auto_complete_enabled = config.extra.get('MOCK_AUTO_COMPLETE', 'true').lower() == 'true'
        self._auto_complete_delay = int(config.extra.get('MOCK_AUTO_COMPLETE_DELAY', '2'))  # 秒
        self._assigned_items: Dict[str, float] = {}  # item_id -> assign_time

        # 失败注入：dag_key 集合中的节点会被模拟为失败（而不是自动完成）
        self._fail_keys: set = set()

        # 派发日志：记录每次 assign_work_item 的 (item_id, dag_key, role, timestamp)
        # 测试用于验证并发派发（同一批 ready 节点的 assign 时间戳接近）
        self.assign_log: list = []

        # 初始化默认工作空间
        self._init_default_workspace()

    def _init_default_workspace(self):
        """初始化默认工作空间和成员"""
        workspace_id = self.config.workspace_id or "mock-workspace"

        self._workspaces[workspace_id] = WorkspaceInfo(
            id=workspace_id,
            name="Mock Workspace",
            description="测试用工作空间",
            member_count=3
        )

        self._members[workspace_id] = ["alice", "bob", "charlie"]

    def _auto_complete_check(self, item_id: str):
        """检查是否应该自动完成任务（或模拟失败）"""
        if not self._auto_complete_enabled:
            return

        if item_id not in self._assigned_items:
            return

        item = self._work_items.get(item_id)
        if not item:
            return

        # 检查是否到了自动完成时间
        elapsed = time.time() - self._assigned_items[item_id]
        if elapsed >= self._auto_complete_delay:
            # 自动完成
            if item.status == WorkItemStatus.IN_PROGRESS:
                if item.dag_key in self._fail_keys:
                    print(f"[Mock] 模拟失败 {item_id} (dag_key={item.dag_key})")
                    item.status = WorkItemStatus.FAILED
                else:
                    print(f"[Mock] 自动完成任务 {item_id}")
                    item.status = WorkItemStatus.DONE
                    item.artifacts = {"pr": f"https://mock.example.com/pr/{item_id}"}
                del self._assigned_items[item_id]

            elif item.status == WorkItemStatus.IN_REVIEW:
                print(f"[Mock] 🤖 自动审核通过任务 {item_id}")
                item.review_verdict = "pass"
                item.review_comment = "Mock: LGTM"
                del self._assigned_items[item_id]

    def set_fail_keys(self, keys: set):
        """设置应模拟失败的 dag_key 集合（测试用）。"""
        self._fail_keys = set(keys)

    # ==================== 环境变量管理 ====================

    @classmethod
    def get_required_env_vars(cls) -> List[Dict[str, str]]:
        """Mock 引擎不需要额外环境变量"""
        return [
            {
                'name': 'MOCK_WORKSPACE_ID',
                'description': 'Mock 工作空间 ID（任意字符串）',
                'prompt': '请输入工作空间 ID',
                'default': 'mock-workspace',
                'validator': lambda x: len(x) > 0
            },
            {
                'name': 'MOCK_AUTO_COMPLETE',
                'description': '是否自动完成任务（true/false）',
                'prompt': '是否启用自动完成？',
                'default': 'true',
                'choices': ['true', 'false']
            },
            {
                'name': 'MOCK_AUTO_COMPLETE_DELAY',
                'description': '自动完成延迟（秒）',
                'prompt': '请输入自动完成延迟（秒）',
                'default': '2',
                'validator': lambda x: x.isdigit() and int(x) > 0
            }
        ]

    @classmethod
    def get_recommended_polling_interval(cls) -> int:
        # Mock 引擎可以很频繁
        return 1  # 1 秒轮询

    # ==================== 第一组：工作空间 ====================

    def list_members(self, workspace_id: str) -> List[str]:
        """列出工作空间成员"""
        return self._members.get(workspace_id, [])

    # ==================== 第二组：工作单元 CRUD ====================

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
        initial_status: WorkItemStatus = WorkItemStatus.TODO
    ) -> WorkItem:
        """创建工作单元"""
        item_id = str(self._next_id)
        self._next_id += 1

        work_item = WorkItem(
            id=item_id,
            workspace_id=workspace_id,
            title=f"[DAG:{dag_key}] {title}",
            description=description,
            status=initial_status,
            dag_key=dag_key,
            worker=worker,
            reviewer=reviewer,
            blocked_by=blocked_by or [],
            wave=wave
        )

        self._work_items[item_id] = work_item
        print(f"[Mock] 创建任务 {item_id}: {title}")
        return work_item

    def get_work_item(self, item_id: str) -> WorkItem:
        """获取工作单元详情"""
        if item_id not in self._work_items:
            raise RuntimeError(f"工作单元不存在: {item_id}")

        # 检查自动完成
        self._auto_complete_check(item_id)

        return self._work_items[item_id]

    def update_work_item_metadata(
        self,
        item_id: str,
        worker: Optional[str] = None,
        reviewer: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        artifacts: Optional[Dict[str, str]] = None,
        review_verdict: Optional[str] = None,
        review_comment: Optional[str] = None
    ) -> WorkItem:
        """更新工作单元的元数据"""
        item = self.get_work_item(item_id)

        if worker is not None:
            item.worker = worker
        if reviewer is not None:
            item.reviewer = reviewer
        if blocked_by is not None:
            item.blocked_by = blocked_by
        if artifacts is not None:
            item.artifacts = artifacts
            print(f"[Mock] 任务 {item_id} 产物: {artifacts}")
        if review_verdict is not None:
            item.review_verdict = review_verdict
            print(f"[Mock] 任务 {item_id} 审核结果: {review_verdict}")
        if review_comment is not None:
            item.review_comment = review_comment

        return item

    def list_work_items(
        self,
        workspace_id: str,
        status: Optional[WorkItemStatus] = None
    ) -> List[WorkItem]:
        """列出工作单元"""
        items = [
            item for item in self._work_items.values()
            if item.workspace_id == workspace_id
        ]

        # 检查所有任务的自动完成
        for item in items:
            self._auto_complete_check(item.id)

        # 过滤 status
        if status:
            items = [item for item in items if item.status == status]

        return items

    def add_comment(self, item_id: str, comment: str):
        """添加评论（Mock 只打印）"""
        print(f"[Mock] 任务 {item_id} 评论: {comment[:80]}...")

    # ==================== 第三组：状态和分配 ====================

    def update_status(self, item_id: str, status: WorkItemStatus):
        """更新工作单元状态"""
        item = self.get_work_item(item_id)
        old_status = item.status
        item.status = status
        print(f"[Mock] 任务 {item_id} 状态: {old_status.value} -> {status.value}")

    def assign_work_item(
        self,
        item_id: str,
        assignee: str,
        role: str
    ):
        """分配任务给协作者"""
        item = self.get_work_item(item_id)
        if role == "worker":
            item.worker = assignee
        elif role == "reviewer":
            item.reviewer = assignee
        print(f"[Mock] 任务 {item_id} 分配给 {assignee} (role: {role})")

        # 记录分配时间（用于自动完成 + 并发追踪）
        self.assign_log.append((item_id, item.dag_key, role, time.time()))
        self._assigned_items[item_id] = time.time()

    # ==================== 第四组：查询 ====================
    # (find_work_item_by_dag_key 已删除——manifest.work_item_id + get_work_item 精准取代)

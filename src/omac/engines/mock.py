"""Mock 引擎 — 内存模拟,撑起全部测试与 CI(现有资产平移,按双接口重组)。

特性:数据全在内存;assign 后按延迟自动模拟完成/失败/评审通过,
并按注册的 contract 生成能通过证据校验的 verification / review_report。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..core.taskmeta import Bounces, TaskKind, TaskPhase
from .models import EngineConfig, WorkItem, WorkItemStatus, WorkspaceInfo
from .runtime import AgentRuntime
from .store import WorkItemStore


class MockStore(WorkItemStore):
    """数据面的内存实现 + 任务执行模拟(自动完成)。"""

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._workspaces: Dict[str, WorkspaceInfo] = {}
        self._members: Dict[str, List[str]] = {}
        self._work_items: Dict[str, WorkItem] = {}
        self._comments: Dict[str, List[str]] = {}
        self._next_id = 1

        self._auto_complete_enabled = str(
            config.extra.get("MOCK_AUTO_COMPLETE", "true")).lower() == "true"
        self._auto_complete_delay = int(config.extra.get("MOCK_AUTO_COMPLETE_DELAY", "2"))
        self._assigned_items: Dict[str, float] = {}

        # 失败注入:dag_key 集合中的节点模拟失败(测试用)
        self._fail_keys: set = set()
        self._contracts_by_item_id: Dict[str, Any] = {}

        # 派发日志:(item_id, dag_key, role, timestamp),测试验证并发派发
        self.assign_log: list = []

        self._init_default_workspace()

    def _init_default_workspace(self):
        # 固定发现集(独立于 config.workspace_id),便于 init/体检测试断言
        self._workspaces = {
            "mock-workspace": WorkspaceInfo(
                id="mock-workspace", name="Mock Workspace",
                description="测试用工作空间", member_count=3),
            "mock-team-b": WorkspaceInfo(
                id="mock-team-b", name="Mock Team B",
                description="副工作空间", member_count=2),
        }
        self._members = {
            "mock-workspace": ["alice", "bob", "charlie"],
            "mock-team-b": ["alice", "bob"],
        }

    # ==================== 模拟执行 ====================

    def set_fail_keys(self, keys: set):
        """设置应模拟失败的 dag_key 集合(测试用)。"""
        self._fail_keys = set(keys)

    def _auto_complete_check(self, item_id: str):
        if not self._auto_complete_enabled or item_id not in self._assigned_items:
            return
        item = self._work_items.get(item_id)
        if not item:
            return
        if time.time() - self._assigned_items[item_id] < self._auto_complete_delay:
            return

        if item.status == WorkItemStatus.IN_PROGRESS:
            if item.dag_key in self._fail_keys:
                item.status = WorkItemStatus.FAILED
            else:
                item.status = WorkItemStatus.DONE
                item.artifacts = {"pr_url": f"https://mock.example.com/pr/{item_id}"}
                verification = self._mock_verification(item_id)
                if verification is not None:
                    item.verification = verification
            del self._assigned_items[item_id]
        elif item.status == WorkItemStatus.IN_REVIEW:
            item.review_verdict = "pass"
            item.review_comment = "Mock: LGTM"
            item.review_report = self._mock_review_report(item_id) or {
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": True,
                "acceptance_mapping": [],
                "blockers": [],
                "nits": [],
            }
            del self._assigned_items[item_id]

    def _mock_verification(self, item_id: str) -> Optional[Dict[str, Any]]:
        contract = self._contracts_by_item_id.get(item_id)
        if contract is None:
            return None
        return {
            "commands": [
                {"cmd": cmd, "exit_code": 0, "summary": "Mock: passed"}
                for cmd in contract.verification_commands
            ],
            "integration_gates": [
                {
                    "name": gate.get("name"),
                    "commands": [
                        {"cmd": cmd, "exit_code": 0, "summary": "Mock: integration passed"}
                        for cmd in gate.get("commands", [])
                    ],
                    "metrics": dict(gate.get("required_metrics", {})),
                    "artifacts": list(gate.get("artifacts", [])),
                    "covers": list(gate.get("covers", [])),
                    "source_of_truth": list(gate.get("source_of_truth", [])),
                    "delivery_goal": gate.get("delivery_goal"),
                }
                for gate in contract.integration_gates
            ],
            "pr_base": contract.pr_base,
            "ci_status": "passed",
            "coverage": contract.coverage_gate,
            "env_setup": [
                f"Mock env: {gate.get('name')}" for gate in contract.integration_gates
            ] if contract.integration_gates else [],
        }

    def _mock_review_report(self, item_id: str) -> Optional[Dict[str, Any]]:
        contract = self._contracts_by_item_id.get(item_id)
        if contract is None:
            return None
        return {
            "review_goals": ["Mock review goal"],
            "diff_reviewed": True,
            "tests_rerun": True,
            "integration_tests_rerun": True,
            "coverage_checked": True,
            "integration_gate_mapping": [
                {
                    "gate": gate.get("name"),
                    "source_of_truth": list(gate.get("source_of_truth", [])),
                    "delivery_goal": gate.get("delivery_goal"),
                    "evidence": f"Mock auto-review integration gate: {gate.get('name')}",
                    "commands": [
                        {"cmd": cmd, "exit_code": 0, "summary": "Mock: integration rerun passed"}
                        for cmd in gate.get("commands", [])
                    ],
                    "metrics": dict(gate.get("required_metrics", {})),
                    "artifacts": list(gate.get("artifacts", [])),
                    "status": "pass",
                }
                for gate in contract.integration_gates
            ],
            "acceptance_mapping": [
                {"acceptance": acceptance,
                 "evidence": f"Mock auto-review for {acceptance}",
                 "status": "pass"}
                for acceptance in contract.acceptance
            ],
            "blockers": [],
            "nits": [],
        }

    # ==================== 成员池 ====================

    def list_members(self, workspace_id: str) -> List[str]:
        return self._members.get(workspace_id, ["alice", "bob", "charlie"])

    # ==================== 工作空间发现 ====================

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """mock 固定值:返回已注册的工作空间(默认含配置 workspace_id 那一个)。"""
        return list(self._workspaces.values())

    # ==================== 工作单元 CRUD ====================

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
            wave=wave,
            kind=kind,
        )
        self._work_items[item_id] = work_item
        return work_item

    def get_work_item(self, item_id: str) -> WorkItem:
        if item_id not in self._work_items:
            raise RuntimeError(f"工作单元不存在: {item_id}")
        self._auto_complete_check(item_id)
        return self._work_items[item_id]

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
        phase: Optional[TaskPhase] = None,
        ci_bounce: Optional[int] = None,
        review_bounce: Optional[int] = None,
        merge_bounce: Optional[int] = None,
        deliverable: Optional[str] = None,
    ) -> WorkItem:
        item = self.get_work_item(item_id)
        if worker is not None:
            item.worker = worker
        if reviewer is not None:
            item.reviewer = reviewer
        if blocked_by is not None:
            item.blocked_by = blocked_by
        if artifacts is not None:
            item.artifacts = artifacts
        if review_verdict is not None:
            item.review_verdict = review_verdict
        if review_comment is not None:
            item.review_comment = review_comment
        if verification is not None:
            item.verification = verification
        if review_report is not None:
            item.review_report = review_report
        if phase is not None:
            item.phase = phase
        if ci_bounce is not None:
            item.bounces.ci = ci_bounce
        if review_bounce is not None:
            item.bounces.review = review_bounce
        if merge_bounce is not None:
            item.bounces.merge = merge_bounce
        if deliverable is not None:
            item.deliverable = deliverable
        return item

    def set_node_contract(self, item_id: str, contract: Any):
        """注册 contract,使自动完成能生成可过证据校验的 verification。"""
        self._contracts_by_item_id[item_id] = contract

    def list_work_items(
        self,
        workspace_id: str,
        status: Optional[WorkItemStatus] = None,
    ) -> List[WorkItem]:
        items = [i for i in self._work_items.values() if i.workspace_id == workspace_id]
        for item in items:
            self._auto_complete_check(item.id)
        if status:
            items = [i for i in items if i.status == status]
        return items

    def add_comment(self, item_id: str, comment: str):
        self._comments.setdefault(item_id, []).append(comment)

    def get_comments(self, item_id: str) -> List[str]:
        """测试辅助:读回评论。"""
        return list(self._comments.get(item_id, []))

    # ==================== 状态和分配 ====================

    def update_status(self, item_id: str, status: WorkItemStatus):
        item = self.get_work_item(item_id)
        item.status = status

    def assign_work_item(self, item_id: str, assignee: str, role: str):
        item = self.get_work_item(item_id)
        if role == "worker":
            item.worker = assignee
        elif role == "reviewer":
            item.reviewer = assignee
        self.assign_log.append((item_id, item.dag_key, role, time.time()))
        self._assigned_items[item_id] = time.time()


class MockRuntime(AgentRuntime):
    """执行面的内存实现:assign 即启动模拟计时(在 MockStore 内),wake 为确认性 no-op。"""

    def __init__(self, store: MockStore):
        self._store = store

    def wake(self, item_id: str, agent: str, role: str) -> None:
        # MockStore.assign_work_item 已启动自动完成计时,这里只确认 item 存在。
        self._store.get_work_item(item_id)

    def describe(self) -> str:
        return "mock: assign 即启动自动完成模拟,wake 为确认性 no-op"

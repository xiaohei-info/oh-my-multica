"""Mock 引擎 — 内存模拟,撑起全部测试与 CI(现有资产平移,按双接口重组)。

特性:数据全在内存(模块级共享,CLI 与测试共用同一份);
assign 后按延迟自动模拟完成/失败/评审通过,
并按注册的 contract 生成能通过证据校验的 verification / review_report。
"""
from __future__ import annotations

import os
import tempfile
import time
import json
from typing import Any, Dict, List, Optional

import yaml
from ..core.taskmeta import Bounces, TaskKind, TaskPhase
from .models import EngineConfig, WorkItem, WorkItemStatus, WorkspaceInfo
from .runtime import AgentRuntime
from .store import WorkItemStore


# 模块级共享状态:所有 MockStore 实例共用,CLI 与测试读写同一份
_shared_workspaces: Dict[str, WorkspaceInfo] = {}
_shared_members: Dict[str, List[str]] = {}
_shared_work_items: Dict[str, WorkItem] = {}
_shared_comments: Dict[str, List[str]] = {}
_shared_next_id: int = 1
_shared_contracts_by_item_id: Dict[str, Any] = {}
_shared_assigned_items: Dict[str, float] = {}
_shared_fail_keys: set = set()
_shared_assign_log: list = []
# 默认行为(可在实例创建时覆盖)
_shared_auto_complete_enabled: bool = True
_shared_auto_complete_delay: int = 2
_shared_kind_deliverables: Dict[str, Dict[str, Any]] = {}
_shared_review_rejects_remaining: int = 0
# 总控验收/增量拆解的行为注册(final-acceptance / decompose 任务完成时落 deliverable,测试用)
_accepted_results: dict[str, object] = {}   # dag_key -> acceptance_results dict
_increments: dict[str, object] = {}        # dag_key -> Manifest(增量 fix 节点)
_shared_kind_delivery_sequences: Dict[str, list] = {}


def _init_default_workspace():
    global _shared_workspaces, _shared_members
    _shared_workspaces = {
        "mock-workspace": WorkspaceInfo(
            id="mock-workspace", name="Mock Workspace",
            description="测试用工作空间", member_count=3),
        "mock-team-b": WorkspaceInfo(
            id="mock-team-b", name="Mock Team B",
            description="副工作空间", member_count=2),
    }
    _shared_members = {
        "mock-workspace": ["alice", "bob", "charlie"],
        "mock-team-b": ["alice", "bob"],
    }


def _write_tmp_json(data) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def _write_tmp_manifest(manifest) -> str:
    """把增量 Manifest 序列化为符合 manifest schema 的 YAML 临时文件。"""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    data = {
        "meta": dict(manifest.meta or {}),
        "nodes": [
            {
                "id": n.id, "worker": n.worker,
                "blocked_by": list(n.blocked_by or []),
                "status": n.status or "todo",
            }
            for n in manifest.nodes.values()
        ],
    }
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path


def _parse_base_manifest_from_description(description: str):
    """从 item.description(payload YAML)解析既有 manifest,失败返回 None。"""
    if not description:
        return None
    try:
        payload = yaml.safe_load(description)
    except yaml.YAMLError:
        return None
    if not isinstance(payload, dict):
        return None
    manifest_raw = payload.get("manifest")
    if not manifest_raw:
        return None
    try:
        data = yaml.safe_load(manifest_raw)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    from ..core.manifest import Manifest, Node
    nodes = {}
    for n in data.get("nodes", []):
        if not isinstance(n, dict) or "id" not in n:
            continue
        nodes[n["id"]] = Node(
            id=n["id"], worker=n.get("worker", ""),
            blocked_by=list(n.get("blocked_by", []) or []),
            status=n.get("status", "todo"),
        )
    return Manifest(meta=data.get("meta") or {}, nodes=nodes)


class MockStore(WorkItemStore):
    """数据面的内存实现 + 任务执行模拟(自动完成)。

    模块级共享状态:同一进程内所有实例共用,CLI 与测试读写同一份。
    """

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        _init_default_workspace()
        # 实例创建时刷新全局行为设置(以最后一次创建为准)。
        # config.extra 可能为 None:见于 dag.py 在无额外 OMAC_* env 时传 None,
        # 此时沿用模块默认值(与 EngineConfig.extra 默认 factory 一致)。
        cfg_extra = config.extra or {}
        global _shared_auto_complete_enabled, _shared_auto_complete_delay
        _shared_auto_complete_enabled = str(
            cfg_extra.get("MOCK_AUTO_COMPLETE", "true")).lower() == "true"
        _shared_auto_complete_delay = int(
            cfg_extra.get("MOCK_AUTO_COMPLETE_DELAY", "2"))

    # ==================== 测试辅助(类级) ====================

    @classmethod
    def reset(cls):
        """清空全部共享状态(测试隔离用)。"""
        global _shared_workspaces, _shared_members, _shared_work_items
        global _shared_comments, _shared_next_id, _shared_contracts_by_item_id
        global _shared_assigned_items, _shared_fail_keys, _shared_assign_log
        global _shared_auto_complete_enabled, _shared_auto_complete_delay
        global _shared_kind_deliverables, _shared_review_rejects_remaining
        _shared_workspaces = {}
        _shared_members = {}
        _shared_work_items = {}
        _shared_comments = {}
        _shared_next_id = 1
        _shared_contracts_by_item_id = {}
        _shared_assigned_items = {}
        _shared_fail_keys = set()
        _shared_assign_log = []
        _shared_auto_complete_enabled = True
        _shared_auto_complete_delay = 2
        _shared_kind_deliverables = {}
        _shared_kind_delivery_sequences = {}
        _shared_review_rejects_remaining = 0
        global _accepted_results, _increments
        _accepted_results = {}
        _increments = {}
        _init_default_workspace()

    @classmethod
    def set_fail_keys(cls, keys: set):
        """设置应模拟失败的 dag_key 集合(测试用)。"""
        global _shared_fail_keys
        _shared_fail_keys = set(keys)

    @classmethod
    def set_kind_delivery(cls, kind: str, deliverable: Dict[str, Any]):
        """注册 kind 的交付物,done 时 auto-complete 产出(测试用)。"""
        global _shared_kind_deliverables
        _shared_kind_deliverables[kind] = deliverable

    @classmethod
    def set_review_rejects(cls, n: int):
        """注入:接下来 n 次 review 自动 verdict=reject,用于测修订循环。"""
        global _shared_review_rejects_remaining
        _shared_review_rejects_remaining = max(0, int(n))

    @classmethod
    def set_acceptance_behaviors(cls, accepted: dict, increments: dict):
        """注册 final-acceptance / decompose 年完成行为(测试用)。

        accepted: {dag_key -> acceptance_results dict(list of {id,status,notes})}
        increments: {dag_key -> Manifest(增量 fix 节点)}
        """
        global _accepted_results, _increments
        _accepted_results = dict(accepted or {})
        _increments = dict(increments or {})

    @classmethod
    def set_kind_delivery_sequence(cls, kind: str, sequence: list):
        """注册 kind 的交付品序列(按次产出,用于测 lint 修订循环「坏→好」)。

        sequence 为空列表时回退到 set_kind_delivery 的单值语义。
        """
        global _shared_kind_delivery_sequences
        _shared_kind_delivery_sequences[kind] = list(sequence)

    @classmethod
    def set_auto_complete(cls, enabled: bool = True, delay: int = 0):
        """配置自动完成开关与延迟(测试用)。"""
        global _shared_auto_complete_enabled, _shared_auto_complete_delay
        _shared_auto_complete_enabled = bool(enabled)
        _shared_auto_complete_delay = max(0, int(delay))

    # ==================== 模拟执行 ====================

    def _auto_complete_check(self, item_id: str):
        global _shared_review_rejects_remaining
        if not _shared_auto_complete_enabled or item_id not in _shared_assigned_items:
            return
        item = _shared_work_items.get(item_id)
        if not item:
            return
        if time.time() - _shared_assigned_items[item_id] < _shared_auto_complete_delay:
            return

        if item.status == WorkItemStatus.IN_PROGRESS:
            if item.dag_key in _shared_fail_keys:
                item.status = WorkItemStatus.FAILED
                del _shared_assigned_items[item_id]
                return

            # 真实 work submit 路径:仅在已为当前 dag_key 注册行为时走,否则
            # 回落到通用 deliverable 路径(plan_create happy path 依赖此后者)。
            # FINAL_ACCEPTANCE / DECOMPOSE 的特殊分支若找不到注册行为就
            # 直接 return 会把节点永远卡在 IN_PROGRESS,导致 run_task 轮询 hung。
            final_acceptance_registered = (
                getattr(item, "kind", None) == TaskKind.FINAL_ACCEPTANCE
                and item.dag_key in _accepted_results)
            decompose_registered = (
                getattr(item, "kind", None) == TaskKind.DECOMPOSE
                and item.dag_key in _increments)

            if final_acceptance_registered:
                # 走真实 work submit 路径:写 acceptance-results 文件,
                # 调 dispatch.submit(acceptance_results_file=...) 经左移校验。
                # contract.acceptance_doc 已由 acceptance._dispatch_and_wait 挂载。
                # 先移除 auto-complete 标记,防止 dispatch.submit 内 get_work_item 二次触发。
                del _shared_assigned_items[item_id]
                results = _accepted_results.get(item.dag_key)
                tmp = _write_tmp_json(results)
                try:
                    from ..pipeline.dispatch import submit as dispatch_submit
                    dispatch_submit(self, item.id, acceptance_results_file=tmp)
                finally:
                    _shared_work_items[item.id] = self.get_work_item(item.id)
                    os.unlink(tmp)
                return

            if decompose_registered:
                # 走真实 work submit 路径:把增量 Manifest 序列化为 manifest YAML,
                # 调 dispatch.submit(manifest_file=...) 经结构校验+lint,状态进 IN_REVIEW。
                # 先移除 auto-complete 标记,防止 dispatch.submit 内 get_work_item 二次触发。
                del _shared_assigned_items[item_id]
                increment = _increments[item.dag_key]
                base = _parse_base_manifest_from_description(item.description)
                tmp = _write_tmp_manifest(increment)
                try:
                    from ..pipeline.dispatch import submit as dispatch_submit
                    pool = set(self.list_members(self.config.workspace_id))
                    dispatch_submit(
                        self, item.id, manifest_file=tmp,
                        agent_pool=pool, base_manifest=base,
                    )
                finally:
                    _shared_work_items[item.id] = self.get_work_item(item.id)
                    os.unlink(tmp)
                return

            item.status = WorkItemStatus.DONE
            seq = _shared_kind_delivery_sequences.get(item.dag_key)
            if seq:
                deliverable = seq.pop(0)
            else:
                deliverable = _shared_kind_deliverables.get(
                    item.dag_key,
                    {"pr_url": f"https://mock.example.com/pr/{item_id}"})
            item.artifacts = dict(deliverable)
            verification = self._mock_verification(item_id)
            if verification is not None:
                item.verification = verification
            del _shared_assigned_items[item_id]
        elif item.status == WorkItemStatus.IN_REVIEW:
            if _shared_review_rejects_remaining > 0:
                item.review_verdict = "reject"
                item.review_comment = "Mock: needs revision"
                _shared_review_rejects_remaining -= 1
            else:
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
            del _shared_assigned_items[item_id]

    def _mock_verification(self, item_id: str) -> Optional[Dict[str, Any]]:
        from ..core.manifest import Contract as _Contract

        contract = _shared_contracts_by_item_id.get(item_id)
        if contract is None or not isinstance(contract, _Contract):
            # 非 develop 节点(final-acceptance/decompose)的 contract 是 dict,
            # 不产生 verification 证据(该节点类型本身不经 worker 证据门)。
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
                        {"cmd": cmd, "exit_code": 0,
                         "summary": "Mock: integration passed"}
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
        from ..core.manifest import Contract as _Contract

        contract = _shared_contracts_by_item_id.get(item_id)
        if contract is None or not isinstance(contract, _Contract):
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
                        {"cmd": cmd, "exit_code": 0,
                         "summary": "Mock: integration rerun passed"}
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
        return _shared_members.get(workspace_id, ["alice", "bob", "charlie"])

    # ==================== 工作空间发现 ====================

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """mock 固定值:返回已注册的工作空间(默认含配置 workspace_id 那一个)。"""
        return list(_shared_workspaces.values())

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
        global _shared_next_id
        item_id = str(_shared_next_id)
        _shared_next_id += 1
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
        _shared_work_items[item_id] = work_item
        return work_item

    def get_work_item(self, item_id: str) -> WorkItem:
        if item_id not in _shared_work_items:
            raise RuntimeError(f"工作单元不存在: {item_id}")
        self._auto_complete_check(item_id)
        return _shared_work_items[item_id]

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
        description: Optional[str] = None,
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
        if description is not None:
            item.description = description
        return item

    def set_node_contract(self, item_id: str, contract: Any):
        """注册 contract,使自动完成能生成可过证据校验的 verification。

        同时同步到 WorkItem.contract,保证 work show 能读回完整上下文
        (与 MulticaStore 读回语义一致)。
        """
        _shared_contracts_by_item_id[item_id] = contract
        item = _shared_work_items.get(item_id)
        if item is not None:
            item.contract = contract

    def list_work_items(
        self,
        workspace_id: str,
        status: Optional[WorkItemStatus] = None,
    ) -> List[WorkItem]:
        items = [i for i in _shared_work_items.values()
                 if i.workspace_id == workspace_id]
        for item in items:
            self._auto_complete_check(item.id)
        if status:
            items = [i for i in items if i.status == status]
        return items

    def add_comment(self, item_id: str, comment: str):
        _shared_comments.setdefault(item_id, []).append(comment)

    def get_comments(self, item_id: str) -> List[str]:
        """测试辅助:读回评论。"""
        return list(_shared_comments.get(item_id, []))

    # ==================== 状态和分配 ====================

    def update_status(self, item_id: str, status: WorkItemStatus):
        item = self.get_work_item(item_id)
        item.status = status

    def reset_review(self, item_id: str):
        item = self.get_work_item(item_id)
        item.review_verdict = None
        item.review_comment = None
        item.review_report = None
        item.phase = TaskPhase.AUTHORING

    def assign_work_item(self, item_id: str, assignee: str, role: str):
        item = self.get_work_item(item_id)
        if role == "worker":
            item.worker = assignee
        elif role == "reviewer":
            item.reviewer = assignee
        _shared_assign_log.append((item_id, item.dag_key, role, time.time()))
        _shared_assigned_items[item_id] = time.time()

    @property
    def assign_log(self):
        return _shared_assign_log


class MockRuntime(AgentRuntime):
    """执行面的内存实现:assign 即启动模拟计时(在 MockStore 内),wake 为确认性 no-op。"""

    def __init__(self, store: MockStore):
        self._store = store

    def wake(self, item_id: str, agent: str, role: str) -> None:
        # MockStore.assign_work_item 已启动自动完成计时,这里只确认 item 存在。
        self._store.get_work_item(item_id)

    def describe(self) -> str:
        return "mock: assign 即启动自动完成模拟,wake 为确认性 no-op"

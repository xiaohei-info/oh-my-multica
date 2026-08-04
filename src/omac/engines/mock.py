"""Mock 引擎 — 内存模拟,撑起全部测试与 CI(现有资产平移,按双接口重组)。

特性:数据全在内存(模块级共享,CLI 与测试共用同一份);
assign 后按延迟自动模拟完成/失败/评审通过,
并按注册的 contract 生成能通过证据校验的 verification / review_report。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml
from ..core.machine_feedback import (
    dump_machine_feedback, parse_machine_feedback,
)
from ..core.taskmeta import (
    DELIVERY_CONTENT_KEY, DeliveryIdentity, ReviewerRunBaseline, TaskKind,
    TaskPhase, WorkerHandoffIntent, parse_delivery_identity,
    parse_reviewer_run_baseline, parse_worker_handoff, current_review_ledger,
)
from ..errors import PlatformError, ValidationError, WorkItemNotFoundError
from ..i18n import ui
from .models import (
    AgentInfo, AgentProvisionSpec, AgentRunObservation, EngineConfig, ProjectInfo, RuntimeTarget,
    MergeCommandResult, PullRequestCheckResult, PullRequestObservation,
    PullRequestReadiness, PullRequestState, RuntimeCapabilities,
    VerificationAttachmentObservation, WorkItem, WorkItemStatus, WorkspaceInfo,
)
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
_shared_projects: Dict[str, ProjectInfo] = {}
_shared_provisioned_members: Dict[str, List[str]] = {}
# 默认行为(可在实例创建时覆盖)
_shared_auto_complete_enabled: bool = True
_shared_auto_complete_delay: int = 2
_shared_auto_merge_on_success: bool = False
_shared_kind_deliverables: Dict[str, Dict[str, Any]] = {}
_shared_review_rejects_remaining: int = 0
_shared_review_verdict: str = "pass"
_shared_review_verdict_sequence: list[str] = []
# 总控验收/增量拆解的行为注册(final-acceptance / decompose 任务完成时落 deliverable,测试用)
_accepted_results: dict[str, object] = {}   # dag_key -> acceptance_results dict
_increments: dict[str, object] = {}        # dag_key -> Manifest(增量 fix 节点)
_shared_kind_delivery_sequences: Dict[str, list] = {}
# 人机门自动确认开关(测试模拟人工把产出 issue 流转到 DONE);默认关。
_shared_auto_confirm: bool = False
# 已自动确认过的 item(人工只确认一次,避免评审阶段翻回 IN_REVIEW 时被误重复确认)。
_shared_human_confirmed: set = set()
_shared_pull_requests: Dict[str, PullRequestObservation] = {}
_shared_runs: Dict[str, List[AgentRunObservation]] = {}
_shared_next_run_id: int = 1
_shared_active_assignments: Dict[str, tuple[str, str]] = {}
_shared_assignment_wake_pending: set[str] = set()
_shared_attachment_bodies: Dict[str, bytes] = {}
_shared_next_attachment_id: int = 1

# 产出后进入评审阶段(而非直接 DONE)的 kind:与真实 work submit 的产出终态一致。
# develop 走 pr_url→DONE;final-acceptance 有独立的 _accepted_results 真实 submit 分支。
_AUTHORING_TO_REVIEW = (
    TaskKind.PLAN, TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE, TaskKind.AMENDMENT,
)


def _finish_mock_run(item_id: str, status: str = "completed") -> None:
    runs = _shared_runs.get(item_id) or []
    if not runs:
        return
    latest = runs[-1]
    runs[-1] = AgentRunObservation(
        id=latest.id, kind=latest.kind, status=status,
        agent_id=latest.agent_id, created_at=latest.created_at,
        updated_at=datetime.now(timezone.utc).isoformat(), error=latest.error,
        trigger_kind=latest.trigger_kind)


def _init_default_workspace():
    global _shared_workspaces, _shared_members
    _shared_workspaces = {
        "mock-workspace": WorkspaceInfo(
            id="mock-workspace", name="Mock Workspace",
            description=ui("Test workspace", "测试用工作空间"), member_count=3),
        "mock-team-b": WorkspaceInfo(
            id="mock-team-b", name="Mock Team B",
            description=ui("Secondary workspace", "副工作空间"), member_count=2),
    }
    _shared_members = {
        "mock-workspace": ["alice", "bob", "charlie"],
        "mock-team-b": ["alice", "bob"],
    }
    for workspace_id, members in _shared_provisioned_members.items():
        pool = _shared_members.setdefault(workspace_id, ["alice", "bob", "charlie"])
        for member in members:
            if member not in pool:
                pool.append(member)


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


def _parse_base_manifest(item):
    """优先从结构化 contract 解析既有 manifest，兼容旧正文 YAML。"""
    contract = getattr(item, "contract", None)
    manifest_raw = contract.get("manifest") if isinstance(contract, dict) else None
    if manifest_raw is None and item.description:
        try:
            payload = yaml.safe_load(item.description)
        except yaml.YAMLError:
            payload = None
        if isinstance(payload, dict):
            manifest_raw = payload.get("manifest")
    if not manifest_raw:
        return None
    if isinstance(manifest_raw, str):
        try:
            data = yaml.safe_load(manifest_raw)
        except yaml.YAMLError:
            return None
    else:
        data = manifest_raw
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
        global _shared_auto_merge_on_success
        global _shared_provisioned_members
        _shared_auto_complete_enabled = str(
            cfg_extra.get("MOCK_AUTO_COMPLETE", "true")).lower() == "true"
        _shared_auto_complete_delay = int(
            cfg_extra.get("MOCK_AUTO_COMPLETE_DELAY", "2"))
        _shared_auto_merge_on_success = str(
            cfg_extra.get("MOCK_AUTO_MERGE_ON_SUCCESS", "false")).lower() == "true"

    # ==================== 测试辅助(类级) ====================

    @classmethod
    def reset(cls):
        """清空全部共享状态(测试隔离用)。"""
        global _shared_workspaces, _shared_members, _shared_work_items
        global _shared_comments, _shared_next_id, _shared_contracts_by_item_id
        global _shared_assigned_items, _shared_fail_keys, _shared_assign_log
        global _shared_auto_complete_enabled, _shared_auto_complete_delay
        global _shared_auto_merge_on_success
        global _shared_kind_deliverables, _shared_review_rejects_remaining
        global _shared_review_verdict, _shared_review_verdict_sequence
        global _shared_provisioned_members
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
        _shared_auto_merge_on_success = False
        _shared_provisioned_members = {}
        _shared_kind_deliverables = {}
        _shared_kind_delivery_sequences = {}
        _shared_review_rejects_remaining = 0
        _shared_review_verdict = "pass"
        _shared_review_verdict_sequence = []
        global _shared_auto_confirm, _shared_human_confirmed
        _shared_auto_confirm = False
        _shared_human_confirmed = set()
        global _accepted_results, _increments, _shared_projects
        global _shared_pull_requests
        global _shared_runs, _shared_next_run_id, _shared_active_assignments
        global _shared_assignment_wake_pending
        global _shared_attachment_bodies, _shared_next_attachment_id
        _accepted_results = {}
        _increments = {}
        _shared_projects = {}
        _shared_pull_requests = {}
        _shared_runs = {}
        _shared_next_run_id = 1
        _shared_active_assignments = {}
        _shared_assignment_wake_pending = set()
        _shared_attachment_bodies = {}
        _shared_next_attachment_id = 1
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
    def set_review_verdict(cls, verdict: str):
        """注入:无 reject 剩余时 review 自动给出的 verdict(测试用)。"""
        global _shared_review_verdict
        _shared_review_verdict = verdict

    @classmethod
    def set_review_verdict_sequence(cls, sequence: list[str]):
        """注册 reviewer verdict 序列(按次评审消耗,测试用)。"""
        global _shared_review_verdict_sequence
        _shared_review_verdict_sequence = list(sequence)

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

    @classmethod
    def set_auto_confirm(cls, enabled: bool = True):
        """配置人机门自动确认(测试模拟人工确认:把等待中的产出流转到 DONE)。"""
        global _shared_auto_confirm
        _shared_auto_confirm = bool(enabled)

    # ==================== 模拟执行 ====================

    def _auto_complete_check(self, item_id: str):
        global _shared_review_rejects_remaining
        item = _shared_work_items.get(item_id)
        if not item:
            return
        # 人机门自动确认(测试模拟人工):Reviewer 已通过并进入 confirmation
        # → 人工把它流转到 DONE(approval 信号)。与 auto_complete 独立开关。
        # 每个 item 只确认一次，review 阶段绝不允许提前确认。
        if (_shared_auto_confirm and item_id not in _shared_assigned_items
                and item_id not in _shared_human_confirmed
                and item.status == WorkItemStatus.IN_REVIEW
                and item.phase == TaskPhase.CONFIRMATION):
            item.status = WorkItemStatus.DONE
            _shared_human_confirmed.add(item_id)
            return
        if not _shared_auto_complete_enabled or item_id not in _shared_assigned_items:
            return
        if time.time() - _shared_assigned_items[item_id] < _shared_auto_complete_delay:
            return

        if item.status == WorkItemStatus.IN_PROGRESS:
            if item.dag_key in _shared_fail_keys:
                item.status = WorkItemStatus.FAILED
                _finish_mock_run(item_id, "failed")
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
                _finish_mock_run(item_id)
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
                _finish_mock_run(item_id)
                del _shared_assigned_items[item_id]
                increment = _increments[item.dag_key]
                base = _parse_base_manifest(item)
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

            kind_key = getattr(item.kind, "value", item.dag_key)
            seq = _shared_kind_delivery_sequences.get(
                item.dag_key) or _shared_kind_delivery_sequences.get(kind_key)
            if seq:
                deliverable = seq.pop(0)
            else:
                deliverable = _shared_kind_deliverables.get(
                    item.dag_key,
                    _shared_kind_deliverables.get(
                        kind_key,
                        {"pr_url": f"https://mock.example.com/pr/{item_id}"}))

            if getattr(item, "kind", None) in _AUTHORING_TO_REVIEW:
                # plan/acceptance/decompose:忠实真实 work submit 的产出终态——
                # 交付正文落 deliverable、phase 进 REVIEW、状态 IN_REVIEW,评审往返
                # 交由上层原语(run_task)接管,不在此直接 DONE。
                key = DELIVERY_CONTENT_KEY[item.kind]
                content = deliverable.get(key)
                if content is None:
                    content = next(iter(deliverable.values()), "")
                item.deliverable = content
                if item.kind == TaskKind.PLAN:
                    item.project_rules = deliverable.get("project_rules")
                # 对齐 dispatch.submit 的 authoring → review 原子推进：新交付
                # 不能继续携带上一轮 verdict/subject 或机器反馈。
                item.review_verdict = None
                item.review_comment = None
                item.machine_feedback = None
                item.machine_feedback_ref = None
                item.review_subject_digest = None
                item.decision_required = {}
                item.phase = TaskPhase.REVIEW
                item.status = WorkItemStatus.IN_REVIEW
            else:
                # develop 及未知类型:直接 DONE + artifacts(pr_url 证据通道)。
                item.status = WorkItemStatus.DONE
                item.artifacts = dict(deliverable)
                pr_url = item.artifacts.get("pr_url")
                if pr_url:
                    item.artifacts["head_sha"] = hashlib.sha256(
                        pr_url.encode("utf-8")).hexdigest()
                verification = self._mock_verification(item_id)
                if verification is not None:
                    item.verification = verification
                    verification_source = yaml.safe_dump(
                        verification, allow_unicode=True, sort_keys=False)
                    global _shared_next_attachment_id
                    attachment_id = (
                        f"mock-attachment-{_shared_next_attachment_id}")
                    _shared_next_attachment_id += 1
                    body = verification_source.encode("utf-8")
                    _shared_attachment_bodies[attachment_id] = body
                    latest_run = (_shared_runs.get(item_id) or [None])[-1]
                    assignment = _shared_active_assignments.get(item_id)
                    uploader_name = assignment[0] if assignment else None
                    item.verification_ref = {
                        "comment_id": f"mock-comment-{attachment_id}",
                        "attachment_id": attachment_id,
                        "filename": "omac-verification.yaml",
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "uploader_type": "agent" if uploader_name else "system",
                        "uploader_id": (
                            self.resolve_agent_id(uploader_name)
                            if uploader_name else None
                        ),
                        "task_id": (
                            latest_run.id if latest_run is not None else None
                        ),
                        "created_at": "2026-01-01T00:00:01Z",
                    }
            _finish_mock_run(item_id)
            del _shared_assigned_items[item_id]
        elif item.status == WorkItemStatus.IN_REVIEW:
            if _shared_review_rejects_remaining > 0:
                item.review_verdict = "reject"
                item.review_comment = "Mock: needs revision"
                _shared_review_rejects_remaining -= 1
            elif _shared_review_verdict_sequence:
                item.review_verdict = _shared_review_verdict_sequence.pop(0)
                item.review_comment = "Mock: LGTM"
            else:
                item.review_verdict = _shared_review_verdict
                item.review_comment = "Mock: LGTM"
            item.review_report = self._mock_review_report(
                item_id, item.review_verdict) or {
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": True,
                "full_review_completed": True,
                "acceptance_mapping": [],
                "blockers": [],
                "nits": (
                    ["Mock: apply the non-blocking follow-up"]
                    if item.review_verdict == "pass-with-nits" else []
                ),
            }
            if item.review_obligations:
                from ..core.review_convergence import advance_review_ledger
                item.review_ledger = advance_review_ledger(
                    current_review_ledger(item),
                    item.review_report,
                    verdict=item.review_verdict,
                    subject_digest=item.review_subject_digest or "mock-subject",
                    round_index=max(1, item.bounces.review + 1),
                )
                item.review_ledger_ref = {
                    "filename": "omac-review-ledger.yaml",
                    "bytes": len(yaml.safe_dump(
                        item.review_ledger, allow_unicode=True).encode("utf-8")),
                }
                item.review_ledger_generation = item.review_generation
            _finish_mock_run(item_id)
            del _shared_assigned_items[item_id]

    def _mock_verification(self, item_id: str) -> Optional[Dict[str, Any]]:
        from ..core.manifest import Contract as _Contract
        from ..core.acceptance_responsibility import evidence_targets

        contract = _shared_contracts_by_item_id.get(item_id)
        if contract is None or not isinstance(contract, _Contract):
            # 非 develop 节点(final-acceptance/decompose)的 contract 是 dict,
            # 不产生 verification 证据(该节点类型本身不经 worker 证据门)。
            return None
        dag_key = _shared_work_items[item_id].dag_key
        commands = [
            {"cmd": cmd, "exit_code": 0, "summary": "Mock: passed"}
            for cmd in contract.verification_commands
        ]
        if commands:
            commands[0]["business_tests"] = [
                {
                    "acceptance": acceptance,
                    "test": f"mock://{dag_key}/acceptance/{acceptance}",
                }
                for acceptance in evidence_targets(contract)
            ]
        return {
            "commands": commands,
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

    def _mock_review_report(
        self, item_id: str, verdict: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        from ..core.acceptance_responsibility import evidence_targets
        from ..core.manifest import Contract as _Contract
        from ..core.review_convergence import (
            REVIEW_PROTOCOL_VERSION, open_blockers)

        contract = _shared_contracts_by_item_id.get(item_id)
        item = _shared_work_items[item_id]
        nits = (
            ["Mock: apply the non-blocking follow-up"]
            if verdict == "pass-with-nits" else []
        )
        obligations = list(item.review_obligations or [])
        if contract is None or not isinstance(contract, _Contract):
            if not obligations:
                return None
            failed_id = "dimension:structure" if verdict == "reject" else None
            return {
                "review_protocol": REVIEW_PROTOCOL_VERSION,
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": True,
                "full_review_completed": True,
                "obligation_results": [
                    {
                        "obligation_id": obligation["obligation_id"],
                        "status": (
                            "fail" if obligation["obligation_id"] == failed_id
                            else "pass"),
                        "evidence": "Mock finite review coverage",
                    }
                    for obligation in obligations
                ],
                "prior_blocker_results": [
                    {
                        "blocker_id": blocker["blocker_id"],
                        "status": "fixed",
                        "evidence": "Mock regression check passed",
                    }
                    for blocker in open_blockers(current_review_ledger(item))
                ],
                "blockers": ([{
                    "root_cause_key": f"mock-review-{item.bounces.review + 1}",
                    "obligation_id": failed_id,
                    "classification": "new",
                    "summary": "Mock: needs revision",
                    "evidence": "Mock finite review found a blocker",
                    "required_fix": "Mock author must revise the deliverable",
                }] if failed_id else []),
                "nits": nits,
            }
        if not obligations:
            obligations = []
        failed_id = "dimension:structure" if verdict == "reject" else None
        report = {
            "review_protocol": REVIEW_PROTOCOL_VERSION,
            "obligation_results": [
                {
                    "obligation_id": obligation["obligation_id"],
                    "status": (
                        "fail" if obligation["obligation_id"] == failed_id
                        else "pass"),
                    "evidence": "Mock finite review coverage",
                }
                for obligation in obligations
            ],
            "prior_blocker_results": [
                {
                    "blocker_id": blocker["blocker_id"],
                    "status": "fixed",
                    "evidence": "Mock regression check passed",
                }
                for blocker in open_blockers(current_review_ledger(item))
            ],
        }
        report.update({
            "review_goals": ["Mock review goal"],
            "diff_reviewed": True,
            "tests_rerun": True,
            "integration_tests_rerun": True,
            "coverage_checked": True,
            "full_review_completed": True,
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
                 "status": "fail" if verdict == "reject" else "pass"}
                for acceptance in evidence_targets(contract)
            ],
            "blockers": ([{
                "root_cause_key": f"mock-review-{item.bounces.review + 1}",
                "obligation_id": failed_id,
                "classification": "new",
                "summary": "Mock: needs revision",
                "evidence": "Mock finite review found a blocker",
                "required_fix": "Mock author must revise the deliverable",
            }] if failed_id else []),
            "nits": nits,
        })
        return report

    # ==================== 成员池 ====================

    def list_members(self, workspace_id: str) -> List[str]:
        return _shared_members.get(workspace_id, ["alice", "bob", "charlie"])

    def resolve_agent_id(self, agent_name: str) -> str:
        if agent_name not in self.list_members(self.config.workspace_id):
            raise ValidationError(f"agent not found: {agent_name}")
        return f"mock-agent-{agent_name}"

    # ==================== 工作空间发现 ====================

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """mock 固定值:返回已注册的工作空间(默认含配置 workspace_id 那一个)。"""
        return list(_shared_workspaces.values())

    # ==================== 项目发现 / 创建 ====================

    def list_projects(self, workspace_id: str) -> List[ProjectInfo]:
        return list(_shared_projects.values())

    def create_project(
        self, workspace_id: str, title: str,
        repo_urls: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> ProjectInfo:
        global _shared_next_id
        pid = f"proj-{_shared_next_id}"
        _shared_next_id += 1
        info = ProjectInfo(id=pid, title=title, repos=list(repo_urls or []))
        _shared_projects[pid] = info
        return info

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
        # parity:真实 multica issue create 拒收空 --description-file,mock 须对等,
        # 否则 run_task 空壳建 issue 在 mock 上悄悄通过、只在真机炸(见 tasks.py 两段式)。
        if not description:
            raise ValidationError(ui(
                "Issue description cannot be empty (--description-file is empty)",
                "issue create 的 description 不能为空(--description-file 空内容)"))
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
            raise WorkItemNotFoundError(ui(
                f"Work item not found: {item_id}",
                f"工作单元不存在: {item_id}"))
        self._auto_complete_check(item_id)
        return _shared_work_items[item_id]

    def set_authoring_identity(
        self, item_id: str, *, dag_key: str, kind: TaskKind,
    ) -> WorkItem:
        item = self.get_work_item(item_id)
        item.dag_key = dag_key
        item.kind = kind
        return item

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
        review_generation: Optional[str] = None,
        review_ledger_generation: Optional[str] = None,
        bounce_baseline: Optional[Dict[str, int]] = None,
        review_continuation: Optional[Dict[str, Any]] = None,
        reviewer_run_baseline: Optional[
            ReviewerRunBaseline | Dict[str, Any]
        ] = None,
        worker_handoff: Optional[WorkerHandoffIntent | Dict[str, Any]] = None,
        delivery_identity: Optional[DeliveryIdentity | Dict[str, Any]] = None,
        decision_required: Optional[Dict[str, Any]] = None,
        amendment_attempt: Optional[Dict[str, Any]] = None,
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
            if review_verdict:
                self._complete_assigned_run(item_id, "reviewer")
        if review_comment is not None:
            item.review_comment = review_comment
        if machine_feedback is not None or machine_feedback_source is not None:
            if machine_feedback_source is None and machine_feedback:
                machine_feedback_source = dump_machine_feedback(machine_feedback)
            if not machine_feedback_source:
                item.machine_feedback = None
                item.machine_feedback_ref = None
            else:
                parsed = parse_machine_feedback(machine_feedback_source)
                if parsed is None or (
                    machine_feedback is not None and machine_feedback != parsed
                ):
                    raise ValidationError(ui(
                        "Machine feedback must use schema omac.machine-feedback/v1",
                        "machine feedback 必须使用 omac.machine-feedback/v1 schema"))
                item.machine_feedback = parsed
                item.machine_feedback_ref = {
                    "filename": "omac-machine-feedback.json",
                    "bytes": len(machine_feedback_source.encode("utf-8")),
                    "sha256": hashlib.sha256(
                        machine_feedback_source.encode("utf-8")).hexdigest(),
                }
        if verification is not None:
            item.verification = verification
        if verification_source is not None:
            global _shared_next_attachment_id
            attachment_id = f"mock-attachment-{_shared_next_attachment_id}"
            _shared_next_attachment_id += 1
            body = verification_source.encode("utf-8")
            _shared_attachment_bodies[attachment_id] = body
            runs = [
                run for run in _shared_runs.get(item_id, [])
                if run.kind == "direct"
            ]
            active_assignment = _shared_active_assignments.get(item_id)
            uploader_name = active_assignment[0] if active_assignment else None
            item.verification_ref = {
                "comment_id": f"mock-comment-{attachment_id}",
                "attachment_id": attachment_id,
                "filename": "omac-verification.yaml",
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "uploader_type": "agent" if uploader_name else "system",
                "uploader_id": (
                    self.resolve_agent_id(uploader_name) if uploader_name else None
                ),
                "task_id": runs[-1].id if runs and uploader_name else None,
                "created_at": "2026-01-01T00:00:01Z",
            }
        if review_report is not None:
            item.review_report = review_report
        if review_report_source is not None:
            attachment_id = f"mock-attachment-{_shared_next_attachment_id}"
            _shared_next_attachment_id += 1
            body = review_report_source.encode("utf-8")
            _shared_attachment_bodies[attachment_id] = body
            item.review_report_ref = {
                "comment_id": f"mock-comment-{attachment_id}",
                "attachment_id": attachment_id,
                "filename": "omac-review-report.yaml",
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        if review_subject_digest is not None:
            item.review_subject_digest = review_subject_digest or None
        if review_obligations is not None:
            item.review_obligations = list(review_obligations)
            item.review_obligations_ref = {
                "filename": "omac-review-obligations.yaml",
                "bytes": len(yaml.safe_dump(
                    item.review_obligations, allow_unicode=True,
                    sort_keys=False).encode("utf-8")),
            }
        if review_ledger is not None:
            item.review_ledger = review_ledger
        if review_ledger_source is not None:
            item.review_ledger_ref = {
                "filename": "omac-review-ledger.yaml",
                "bytes": len(review_ledger_source.encode("utf-8")),
            }
        if review_generation is not None:
            item.review_generation = review_generation or None
        if review_ledger_generation is not None:
            item.review_ledger_generation = review_ledger_generation or None
        if bounce_baseline is not None:
            item.bounce_baseline = dict(bounce_baseline) or None
        if review_continuation is not None:
            item.review_continuation = review_continuation or None
        if reviewer_run_baseline is not None:
            item.reviewer_run_baseline = parse_reviewer_run_baseline(
                reviewer_run_baseline)
        if worker_handoff is not None:
            item.worker_handoff = parse_worker_handoff(worker_handoff)
        if delivery_identity is not None:
            item.delivery_identity = parse_delivery_identity(delivery_identity)
        if decision_required is not None:
            item.decision_required = decision_required
        if amendment_attempt is not None:
            item.amendment_attempt = dict(amendment_attempt)
        if phase is not None:
            item.phase = phase
        if worker_bounce is not None:
            item.bounces.worker = worker_bounce
        if ci_bounce is not None:
            item.bounces.ci = ci_bounce
        if review_bounce is not None:
            item.bounces.review = review_bounce
        if merge_bounce is not None:
            item.bounces.merge = merge_bounce
        if deliverable is not None:
            item.deliverable = deliverable
        if project_rules is not None:
            item.project_rules = project_rules
        if source_refs is not None:
            item.source_refs = source_refs
        if description is not None:
            if not description:
                raise ValidationError(ui(
                    "Issue description cannot be empty (--description-file is empty)",
                    "issue update 的 description 不能为空(--description-file 空内容)"))
            item.description = description
        return item

    def restore_authoring_generation(
        self,
        item_id: str,
        contract: Any,
        review_generation: str,
        bounce_baseline: Optional[Dict[str, int]] = None,
    ) -> WorkItem:
        item = self.get_work_item(item_id)
        item.contract = contract
        from ..core.manifest import _dump_contract
        payload = _dump_contract(contract) if not isinstance(contract, dict) else contract
        source = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        item.contract_ref = {
            "filename": "omac-contract.yaml",
            "bytes": len(source.encode("utf-8")),
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }
        item.review_generation = review_generation
        item.bounce_baseline = (
            dict(bounce_baseline) if bounce_baseline else None)
        item.review_verdict = None
        item.review_comment = None
        item.machine_feedback = None
        item.machine_feedback_ref = None
        item.review_report = None
        item.review_report_ref = None
        item.review_subject_digest = None
        item.review_obligations = []
        item.review_obligations_ref = None
        item.review_continuation = None
        item.reviewer_run_baseline = None
        item.worker_handoff = None
        item.delivery_identity = None
        item.decision_required = None
        item.phase = TaskPhase.AUTHORING
        item.status = WorkItemStatus.TODO
        item.reviewer = None
        item.platform_assignee_id = None
        _shared_assigned_items.pop(item_id, None)
        _shared_active_assignments.pop(item_id, None)
        _shared_assignment_wake_pending.discard(item_id)
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
            from ..core.manifest import _dump_contract
            payload = _dump_contract(contract) if not isinstance(contract, dict) else contract
            source = yaml.safe_dump(
                payload, sort_keys=False, allow_unicode=True)
            item.contract_ref = {
                "filename": "omac-contract.yaml",
                "bytes": len(source.encode("utf-8")),
                "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            }

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
        if status == WorkItemStatus.DONE:
            self._complete_assigned_run(item_id, "worker")

    @staticmethod
    def _complete_assigned_run(item_id: str, role: str) -> None:
        assignment = _shared_active_assignments.get(item_id)
        if not assignment or assignment[1] != role:
            return
        runs = _shared_runs.get(item_id, [])
        for index in range(len(runs) - 1, -1, -1):
            run = runs[index]
            if run.kind == "direct" and run.active:
                runs[index] = replace(run, status="completed")
                return

    def cancel_work_item(self, item_id: str) -> None:
        """内存态直接移除,模拟平台侧作废(get 后即不存在)。"""
        _shared_work_items.pop(item_id, None)
        _shared_assigned_items.pop(item_id, None)
        _shared_active_assignments.pop(item_id, None)
        _shared_assignment_wake_pending.discard(item_id)

    def reset_review(self, item_id: str):
        item = self.get_work_item(item_id)
        item.review_verdict = None
        item.review_comment = None
        item.machine_feedback = None
        item.machine_feedback_ref = None
        item.review_report = None
        item.review_report_ref = None
        item.decision_required = None
        item.review_subject_digest = None
        item.reviewer_run_baseline = None
        item.phase = TaskPhase.AUTHORING

    def prepare_review_cycle(self, item_id: str, subject_digest: str) -> WorkItem:
        item = self.get_work_item(item_id)
        if item.review_subject_digest == subject_digest:
            return item
        item.review_verdict = None
        item.review_comment = None
        item.machine_feedback = None
        item.machine_feedback_ref = None
        item.review_report = None
        item.review_report_ref = None
        item.decision_required = None
        item.review_subject_digest = subject_digest
        item.reviewer_run_baseline = None
        item.phase = TaskPhase.REVIEW
        return item

    def assign_work_item(
        self,
        item_id: str,
        assignee: str,
        role: str,
        *,
        start_run: bool = True,
    ):
        item = self.get_work_item(item_id)
        agent_id = self.resolve_agent_id(assignee)
        if role == "worker":
            item.worker = assignee
        elif role == "reviewer":
            item.reviewer = assignee
        item.platform_assignee_id = agent_id
        assignment = (assignee, role)
        same_active_assignment = (
            item_id in _shared_assigned_items
            and _shared_active_assignments.get(item_id) == assignment
        )
        _shared_assign_log.append((item_id, item.dag_key, role, time.time()))
        _shared_assigned_items[item_id] = time.time()
        _shared_active_assignments[item_id] = assignment
        if start_run and not same_active_assignment:
            self._append_assigned_run(item_id, agent_id, "issue_assignment")
            _shared_assignment_wake_pending.add(item_id)
        elif not start_run:
            _shared_assignment_wake_pending.discard(item_id)

    @staticmethod
    def _append_assigned_run(
        item_id: str, agent_id: str, trigger_kind: str,
    ) -> None:
        global _shared_next_run_id
        _shared_runs.setdefault(item_id, []).append(AgentRunObservation(
            id=f"mock-run-{_shared_next_run_id}",
            kind="direct",
            status="running",
            agent_id=agent_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            trigger_kind=trigger_kind,
        ))
        _shared_next_run_id += 1

    def _start_assigned_run(
        self, item_id: str, agent_id: str, trigger_kind: str,
    ) -> None:
        runs = _shared_runs.setdefault(item_id, [])
        if any(run.active for run in runs):
            return
        self._append_assigned_run(item_id, agent_id, trigger_kind)

    def clear_assignment(self, item_id: str) -> None:
        item = self.get_work_item(item_id)
        item.reviewer = None
        item.platform_assignee_id = None
        _shared_assigned_items.pop(item_id, None)
        _shared_active_assignments.pop(item_id, None)
        _shared_assignment_wake_pending.discard(item_id)

    def normalize_confirmed_merge(self, item_id: str) -> None:
        """One in-memory mutation mirrors Multica's atomic issue update."""
        item = self.get_work_item(item_id)
        item.status = WorkItemStatus.DONE
        item.platform_assignee_id = None
        _shared_assigned_items.pop(item_id, None)
        _shared_active_assignments.pop(item_id, None)
        _shared_assignment_wake_pending.discard(item_id)

    def request_pull_request_merge(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> MergeCommandResult:
        """mock 仍执行测试提供的命令，但仅由随后观察到的状态确认合入。"""
        try:
            proc = subprocess.run(
                command.replace("{pr_url}", pr_url), shell=True,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            output = "".join(
                stream.decode("utf-8", errors="replace")
                if isinstance(stream, bytes) else stream or ""
                for stream in (exc.stdout, exc.stderr))
            return MergeCommandResult(False, None, output, timed_out=True)
        except FileNotFoundError as exc:
            return MergeCommandResult(False, None, str(exc))
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0 and _shared_auto_merge_on_success:
            _shared_pull_requests[pr_url] = PullRequestObservation(
                PullRequestState.MERGED,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        return MergeCommandResult(
            proc.returncode == 0, proc.returncode, output)

    def observe_pull_request(self, pr_url: str) -> PullRequestObservation:
        return _shared_pull_requests.get(
            pr_url, PullRequestObservation(PullRequestState.OPEN))

    def check_pull_request(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> PullRequestCheckResult:
        try:
            proc = subprocess.run(
                command.replace("{pr_url}", pr_url), shell=True,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            output = "".join(
                stream.decode("utf-8", errors="replace")
                if isinstance(stream, bytes) else stream or ""
                for stream in (exc.stdout, exc.stderr))
            return PullRequestCheckResult(False, None, output, timed_out=True)
        except FileNotFoundError as exc:
            return PullRequestCheckResult(False, None, str(exc))
        output = (proc.stdout or "") + (proc.stderr or "")
        return PullRequestCheckResult(
            proc.returncode == 0, proc.returncode, output)

    def read_pull_request_readiness(self, pr_url: str) -> PullRequestReadiness:
        return PullRequestReadiness(
            is_draft=False,
            state="OPEN",
            head_sha=hashlib.sha256(pr_url.encode("utf-8")).hexdigest(),
        )

    def observe_verification_attachment(
        self, item_id: str, ref: Dict[str, Any],
    ) -> VerificationAttachmentObservation:
        self.get_work_item(item_id)
        attachment_id = str(ref.get("attachment_id") or "")
        comment_id = str(ref.get("comment_id") or "")
        body = _shared_attachment_bodies.get(attachment_id)
        if not attachment_id or not comment_id or body is None:
            raise PlatformError("verification attachment observation is unavailable")
        actual_sha = hashlib.sha256(body).hexdigest()
        if ref.get("sha256") and ref.get("sha256") != actual_sha:
            raise PlatformError("verification attachment digest mismatch")
        return VerificationAttachmentObservation(
            attachment_id=attachment_id,
            comment_id=comment_id,
            sha256=actual_sha,
            content=body,
            uploader_id=ref.get("uploader_id"),
            uploader_type=ref.get("uploader_type"),
            task_id=ref.get("task_id"),
            created_at=ref.get("created_at"),
        )

    @property
    def assign_log(self):
        return _shared_assign_log


class MockRuntime(AgentRuntime):
    """执行面的内存实现:默认 assign 启动，静默 assign 由 wake 启动。"""

    def __init__(self, store: MockStore):
        self._store = store

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(stable_direct_run_identity=True)

    def wake(self, item_id: str, agent: str, role: str) -> None:
        item = self._store.get_work_item(item_id)
        agent_id = self._store.resolve_agent_id(agent)
        if item_id in _shared_assignment_wake_pending:
            _shared_assignment_wake_pending.discard(item_id)
            return
        if (
            item.platform_assignee_id is not None
            and item.platform_assignee_id != agent_id
        ):
            raise PlatformError("mock wake target does not match current assignment")
        if item.platform_assignee_id is None:
            item.platform_assignee_id = agent_id
            _shared_active_assignments[item_id] = (agent, role)
        self._store._start_assigned_run(item_id, agent_id, "rerun")

    def cancel(self, item_id: str) -> bool:
        self._store.get_work_item(item_id)
        return False

    def is_active(self, item_id: str) -> bool:
        return any(run.active for run in self.list_runs(item_id))

    def list_runs(self, item_id: str) -> List[AgentRunObservation]:
        self._store.get_work_item(item_id)
        return list(_shared_runs.get(item_id, []))

    def list_targets(self) -> List[RuntimeTarget]:
        return [RuntimeTarget(
            id="mock-runtime", name="Mock Runtime", type="mock", status="online")]

    def provision_agent(self, spec: AgentProvisionSpec) -> AgentInfo:
        if not spec.name.strip():
            raise ValidationError(ui("Agent name cannot be empty", "Agent 名称不能为空"))
        workspace_id = self._store.config.workspace_id
        members = self._store.list_members(workspace_id)
        if spec.name in members:
            raise ValidationError(ui(
                f"Agent '{spec.name}' already exists. Choose it or use another name.",
                f"Agent '{spec.name}' 已存在 —— 请选择已有 Agent 或换一个名称"))
        if spec.runtime_id != "mock-runtime":
            raise ValidationError(ui(
                f"Runtime '{spec.runtime_id}' does not exist. Available: mock-runtime",
                f"Runtime '{spec.runtime_id}' 不存在,可选:mock-runtime"))
        global _shared_provisioned_members, _shared_members
        _shared_provisioned_members.setdefault(workspace_id, []).append(spec.name)
        _shared_members.setdefault(workspace_id, members).append(spec.name)
        return AgentInfo(id=f"mock-agent-{spec.name}", name=spec.name)

    def describe(self) -> str:
        return ui(
            "mock: assign starts automatic completion; wake is a confirming no-op",
            "mock: assign 即启动自动完成模拟,wake 为确认性 no-op")

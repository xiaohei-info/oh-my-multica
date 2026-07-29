"""pipeline/loop — 确定性单轮 tick(结果回收 → 就绪计算 → 派发)。

设计文档 §7.3:sync → decide → dispatch,状态全在 manifest + 平台,幂等。
硬性约束(§2.4):无自动重试——blocked 节点在后续 tick 保持 blocked,
重试只经 `omac node retry` 显式决策。abandoned 上游视同依赖已满足(P1.4)。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from ..core import graph, logsetup
from ..core.amendment import ensure_amendment_apply_complete
from ..core.config import DEFAULT_RETRY
from ..core.contract_boundaries import (
    build_contract_boundary_decision,
    contract_boundary_conflicts,
)
from ..core.evidence import validate_review_evidence, validate_worker_evidence
from ..core.review_convergence import (
    build_review_obligations, review_subject_digest)
from ..core.retry_budget import consumed_bounces
from ..core.stage_recovery import stage_recovery_subject
from ..core.gitsync import commit_manifest
from ..core.manifest import Manifest, save_manifest, set_node
from ..pipeline.delivery import (
    _resume_merge_bounce,
    advance_delivery, block_unproven_merge_request,
    merge_bounce_attempt, merge_request_state_is_valid, run_merge_delivery,
)
from ..engines.models import PullRequestState, WorkItemStatus
from ..engines.runtime import AgentRuntime
from ..engines.store import WorkItemStore
from ..errors import AuthError, PlatformError, WorkItemNotFoundError
from ..i18n import current_language, ui
from ..pipeline.dispatch import normalize_source_refs, render_issue_body
from ..core.taskmeta import TaskKind, TaskPhase

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

    state: converged(全部 done) | running(有进行中节点) | needs_decision(有失败且无进行中)
    report: 仅 needs_decision 时有内容——失败节点 + 证据摘要 + 受阻下游
    """
    state: str
    done: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    running: List[str] = field(default_factory=list)
    dispatched: List[str] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)


def _build_snapshot(manifest: Manifest) -> dict:
    """从 manifest 构建 graph 模块所需的 snapshot dict。"""
    return {
        key: {"status": node.status, "blocked_by": list(node.blocked_by)}
        for key, node in manifest.nodes.items()
    }


def _has_unreviewed_worker_delivery(node, item) -> bool:
    """识别 worker 已交付、但 manifest 仍残留 terminal 状态的节点。"""
    return bool(
        node.reviewer
        and not node.merged
        and item.status == WorkItemStatus.DONE
        and item.phase == TaskPhase.AUTHORING
        and item.artifacts
        and item.verification
        and not _current_delivery_passed_review(item)
    )


def _current_review_subject(item) -> str:
    return review_subject_digest(item, max(1, item.bounces.review + 1))


def _review_subject_for_current_delivery(
    manifest: Manifest, key: str, item,
) -> str:
    """返回当前 delivery 的合法 subject，兼容已接受 amendment 的 review recovery。"""
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
    return _current_review_subject(item)


def _review_subject_is_current(
    manifest: Manifest, key: str, item,
) -> bool:
    return (
        item.review_subject_digest
        == _review_subject_for_current_delivery(manifest, key, item)
    )


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


def _has_confirmed_merge(node) -> bool:
    return bool(node.merged and node.merged_at)


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
        _has_confirmed_merge(node) and node.merge_request_state is None
    )


def _observe_reconcile_inputs(
    store: WorkItemStore, manifest: Manifest,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """先完整读取一轮所需事实；此阶段禁止任何 manifest/平台写入。"""
    items: Dict[str, Any] = {}
    pull_requests: Dict[str, Any] = {}
    for key, node in manifest.nodes.items():
        if not node.work_item_id:
            continue
        try:
            item = store.get_work_item(node.work_item_id)
        except WorkItemNotFoundError:
            items[key] = _MISSING_WORK_ITEM
            continue
        items[key] = item
        if not _requires_pull_request_observation(node, item):
            continue
        pull_requests[key] = store.observe_pull_request(
            _pull_request_url(item))
    return items, pull_requests


def _resume_reviewer_run(store, runtime, node) -> bool:
    """在同一 issue 恢复 reviewer，不重置既有 worker/评审对象事实。"""
    if not node.reviewer:
        return False
    item_id = node.work_item_id
    store.update_status(item_id, WorkItemStatus.IN_REVIEW)
    store.assign_work_item(item_id, node.reviewer, "reviewer")
    runtime.wake(item_id, node.reviewer, "reviewer")
    return True


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

    if (
        not subject_changed
        and current.phase == TaskPhase.REVIEW
        and current.status == WorkItemStatus.IN_REVIEW
        and current.reviewer == node.reviewer
        and runtime.is_active(item_id)
    ):
        return False

    store.update_status(item_id, WorkItemStatus.IN_REVIEW)
    store.assign_work_item(item_id, node.reviewer, "reviewer")
    runtime.wake(item_id, node.reviewer, "reviewer")
    return True


def _dispatch_worker_handoff(
    store: WorkItemStore,
    runtime: AgentRuntime,
    node,
    *,
    review_bounce: int | None = None,
) -> None:
    """幂等完成 review→worker handoff；assign 可能立即启动 Run。"""
    item_id = node.work_item_id
    current = store.get_work_item(item_id)
    if review_bounce is not None and current.bounces.review < review_bounce:
        store.update_work_item_metadata(
            item_id, review_bounce=review_bounce)
        current = store.get_work_item(item_id)

    # reset_review 的接口契约负责一次性清除当前 review projection 并回 AUTHORING。
    # handoff 不制造瞬时 review cycle，避免新增可崩溃的持久化中间态。
    if (
        current.phase != TaskPhase.AUTHORING
        or current.review_verdict is not None
        or current.review_comment not in {None, ""}
        or current.machine_feedback not in (None, {})
        or current.review_report is not None
        or current.review_subject_digest is not None
        or current.decision_required is not None
    ):
        store.reset_review(item_id)
        current = store.get_work_item(item_id)

    if current.status != WorkItemStatus.IN_PROGRESS:
        store.update_status(item_id, WorkItemStatus.IN_PROGRESS)
        current = store.get_work_item(item_id)
    if (
        current.phase != TaskPhase.AUTHORING
        or current.status != WorkItemStatus.IN_PROGRESS
        or current.review_verdict is not None
        or current.review_report is not None
        or current.review_subject_digest is not None
    ):
        raise PlatformError(
            f"Worker handoff preparation did not persist for work item {item_id}")

    # assign_work_item 自身负责观察当前 assignee 并幂等修复；同一 worker 的
    # 重复 assign 不得创建第二个 Run，不同 assignee 则在正确 phase/status 下接棒。
    store.assign_work_item(item_id, node.worker, "worker")
    runtime.wake(item_id, node.worker, "worker")


def _complete_merge_if_confirmed(
    store: WorkItemStore, runtime: AgentRuntime, manifest: Manifest, key: str,
    retry_limits: dict, config: dict, manifest_path: str,
) -> str:
    node = manifest.nodes[key]
    item = store.get_work_item(node.work_item_id)
    if (
        node.reviewer
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

def reconcile(store: WorkItemStore, manifest: Manifest, manifest_path: str) -> bool:
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
    items, pull_requests = _observe_reconcile_inputs(store, manifest)
    candidate = copy.deepcopy(manifest)
    changed = _reconcile_candidate(
        store, candidate, manifest_path, items, pull_requests)
    if not changed:
        return False
    save_manifest(candidate, manifest_path)
    manifest.meta = candidate.meta
    manifest.nodes = candidate.nodes
    return True


def _reconcile_candidate(
    store: WorkItemStore, manifest: Manifest, manifest_path: str,
    items: Dict[str, Any], pull_requests: Dict[str, Any],
) -> bool:
    """用已完整观察的事实计算候选；此阶段不得再执行平台读取。"""
    changed = False
    for key, node in manifest.nodes.items():
        if not node.work_item_id:
            if node.status == "done":
                set_node(manifest, key, status="blocked")
                changed = True
            continue
        item = items[key]
        if item is _MISSING_WORK_ITEM:
            if node.status == "done":
                set_node(manifest, key, status="blocked")
                changed = True
            elif node.status not in {"done", "abandoned"}:
                set_node(manifest, key, work_item_id=None, status="todo")
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
                if _has_confirmed_merge(node) and node.merge_request_state is None:
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
            # manifest==todo 是一个显式意图(首次派发 或 node retry 写回)。
            # 若平台工单仍是失败态,不自作主张把 todo 拉回 blocked/failed:
            #   - 首次派发时 work_item_id 本为空,这里不会触发(前一分支已清空)
            #   - node retry 显式把 todo 写回并保留 work_item_id,此时应让 dispatch
            #     经 assign_work_item 把工单重新 IN_PROGRESS 派活,而非被平台旧态覆盖
            if node.status == "todo" and manifest_status in {"blocked", "failed"}:
                continue
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

        try:
            item = store.get_work_item(node.work_item_id)
        except Exception:
            continue

        # worker handoff 已落平台、但 Runner 尚未来得及保存 manifest 时，
        # 以 Store 的 authoring phase 恢复 worker 路径；新 submit 仍要重过 review。
        recovering_worker_handoff = (
            node.status == "in_review"
            and item.phase == TaskPhase.AUTHORING
            and item.status in {
                WorkItemStatus.IN_REVIEW, WorkItemStatus.IN_PROGRESS,
            }
        )
        if node.status == "in_review" and item.phase == TaskPhase.AUTHORING:
            set_node(manifest, key, status="in_progress")
        if recovering_worker_handoff:
            try:
                _dispatch_worker_handoff(store, runtime, node)
            except PlatformError as exc:
                store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                store.add_comment(node.work_item_id, ui(
                    f"Failed to resume worker handoff to {node.worker}: {exc}",
                    f"恢复到 worker {node.worker} 的交接失败: {exc}"))
                set_node(manifest, key, status="blocked")
                failures[key] = ui(
                    f"Failed to resume worker handoff to {node.worker}: {exc}",
                    f"恢复到 worker {node.worker} 的交接失败: {exc}")
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
        ):
            _dispatch_worker_handoff(store, runtime, node)
            continue

        # ---- in_progress: worker 阶段回收 ----
        if node.status == "in_progress":
            if item.agent_run_finished_without_submit:
                worker_limit = limits.get("worker", DEFAULT_RETRY["worker"])
                cur_bounce = item.bounces.worker
                consumed = consumed_bounces(
                    manifest, key, item, "worker")
                reason = ui(
                    "Worker run ended without delivery through `omac work submit`.",
                    "worker run 已结束但未通过 omac work submit 交付")
                if worker_limit == 0 or consumed >= worker_limit:
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
                    store.update_work_item_metadata(
                        node.work_item_id,
                        phase=TaskPhase.AUTHORING,
                        worker_bounce=cur_bounce + 1,
                    )
                    try:
                        store.assign_work_item(node.work_item_id, node.worker, "worker")
                        store.update_status(node.work_item_id, WorkItemStatus.IN_PROGRESS)
                        set_node(manifest, key, status="in_progress")
                        log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id, gate="worker",
                                 round=cur_bounce + 1, max=worker_limit)
                        runtime.wake(node.work_item_id, node.worker, "worker")
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
                runtime.wake(node.work_item_id, node.worker, "worker")
                continue
            if item.status == WorkItemStatus.DONE:
                gate_errors = validate_worker_evidence(node, item)
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

            log.info(logsetup.EVT_VERDICT, kind=_DAG_KIND, node=key,
                     id=node.work_item_id, verdict=verdict)
            gate_errors = validate_review_evidence(node, item)
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
            if verdict == "pass-with-nits" and not gate_errors:
                cur_bounce = item.bounces.review
                try:
                    _dispatch_worker_handoff(
                        store, runtime, node,
                        review_bounce=cur_bounce + 1,
                    )
                    set_node(manifest, key, status="in_progress")
                    log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, gate="review-nits")
                except PlatformError as exc:
                    store.update_work_item_metadata(
                        node.work_item_id, review_bounce=cur_bounce)
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(
                        node.work_item_id,
                        ui(
                            f"Failed to return nits to worker {node.worker}: {exc}",
                            f"回退到 worker {node.worker} 处理 nits 失败: {exc}"))
                    set_node(manifest, key, status="blocked")
                    failures[key] = ui(
                        f"Failed to return nits to worker {node.worker}: {exc}",
                        f"回退到 worker {node.worker} 处理 nits 失败: {exc}")
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
                review_limit = limits.get("review", DEFAULT_RETRY["review"])
                cur_bounce = item.bounces.review
                consumed = consumed_bounces(
                    manifest, key, item, "review")
                reason = "; ".join(gate_errors) if gate_errors else "reviewer reject"
                if review_limit == 0 or consumed >= review_limit:
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(node.work_item_id, ui(
                        f"Review evidence retry limit ({review_limit}) exhausted: {reason}",
                        f"评审证据门上界({review_limit})已耗尽: {reason}"))
                    set_node(manifest, key, status="blocked")
                    failures[key] = ui(
                        f"Review evidence gate failed; retry limit {review_limit} exhausted: {reason}",
                        f"评审证据门未通过(回退上界 {review_limit} 已耗尽): {reason}")
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id,
                             reason=ui(
                                 f"Review retry limit ({review_limit}) exhausted",
                                 f"评审回退上界({review_limit})已耗尽"))
                else:
                    # 有界「回到 worker」:先记回退计数并清除旧评审判定,再重新派发 worker。
                    # 派发失败时回滚回退计数并把节点标 blocked,避免卡在「已清判定/未派发」中间态。
                    # 派发失败时回滚 review_bounce,避免把「未成功的回退」计为消耗;
                    # 这与 CI 回退路径(delivery.advance_delivery)的语义对称 ——
                    # 两者都是「计数只在派发成功时才真正消耗」。
                    try:
                        _dispatch_worker_handoff(
                            store, runtime, node,
                            review_bounce=cur_bounce + 1,
                        )
                        set_node(manifest, key, status="in_progress")
                        log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id, gate="review",
                                 round=cur_bounce + 1, max=review_limit)
                    except PlatformError as exc:
                        store.update_work_item_metadata(node.work_item_id, review_bounce=cur_bounce)
                        store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                        store.add_comment(
                            node.work_item_id,
                            ui(
                                f"Failed to return to worker {node.worker}; retry count rolled back: {exc}",
                                f"回退到 worker {node.worker} 失败(已回滚回退计数): {exc}"))
                        set_node(manifest, key, status="blocked")
                        failures[key] = ui(
                            f"Failed to return to worker {node.worker}: {exc}",
                            f"回退到 worker {node.worker} 失败: {exc}")

        elif node.status == "merging":
            pending_attempt = merge_bounce_attempt(node.merge_request_state)
            if pending_attempt is not None:
                merge_action = _resume_merge_bounce(
                    node,
                    item,
                    store,
                    runtime,
                    limits,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    attempt=pending_attempt,
                )
            else:
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
        except PlatformError as exc:
            store.update_status(item_id, WorkItemStatus.BLOCKED)
            store.add_comment(item_id, ui(
                f"Failed to wake reviewer {reviewer}: {exc}",
                f"唤醒 reviewer {reviewer} 失败: {exc}"))
            set_node(manifest, key, status="blocked")
            failures[key] = ui(
                f"Failed to wake reviewer {reviewer}", f"唤醒 reviewer {reviewer} 失败")
            log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                     id=item_id, reason=ui(
                         f"Failed to wake reviewer {reviewer}", f"唤醒 reviewer {reviewer} 失败"))

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
        if node.status == "done" or (node.merged and node.merged_at):
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


def _dispatch(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    manifest_path: str,
    ready: List[str],
    max_parallel: int,
) -> List[str]:
    """派发就绪节点(受 max_parallel - 进行中数约束)。

    无 work_item_id → store.create_work_item + set_node_contract;
    然后 assign worker + update_status(IN_PROGRESS) + runtime.wake;
    work_item_id 回填 manifest。
    """
    workspace_id = store.config.workspace_id
    running_count = sum(
        1 for n in manifest.nodes.values() if n.status in RUNNING_STATUSES
    )
    slots = max(0, max_parallel - running_count)
    to_dispatch = ready[:slots]

    dispatched: List[str] = []
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
        else:
            item = store.get_work_item(node.work_item_id)

        # contract 附件只在首次建单时发布,避免 retry 追加系统评论触发平行 run。
        # retry 的 scope/说明变化通过静默刷新 issue body 生效。
        if is_new_item:
            if node.contract is not None:
                store.set_node_contract(item.id, node.contract)

        env = _store_env(store)
        source_refs = _develop_source_refs(manifest, node, env)
        body = render_issue_body(
            node, node.contract, TaskKind.DEVELOP, item.id,
            source_refs=source_refs,
            engine_env=env,
            issue_key=getattr(item, "identifier", None),
            language=current_language(),
        )
        metadata = {
            "description": body,
            "source_refs": source_refs,
        }
        if not is_new_item:
            metadata["blocked_by"] = list(node.blocked_by)
        store.update_work_item_metadata(item.id, **metadata)

        # fire-and-forget: assign worker + 标 in_progress + wake
        store.assign_work_item(node.work_item_id, worker, "worker")
        store.update_status(node.work_item_id, WorkItemStatus.IN_PROGRESS)
        set_node(manifest, key, status="in_progress")

        try:
            runtime.wake(node.work_item_id, worker, "worker")
        except PlatformError as exc:
            store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
            store.add_comment(node.work_item_id, ui(
                f"Failed to wake worker {worker}: {exc}",
                f"唤醒 worker {worker} 失败: {exc}"))
            set_node(manifest, key, status="blocked")
            log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                     id=node.work_item_id, reason=ui(
                         f"Failed to wake worker {worker}", f"唤醒 worker {worker} 失败"))
            continue

        log.info(logsetup.EVT_DISPATCH, kind=_DAG_KIND, node=key,
                 id=node.work_item_id, worker=worker)
        dispatched.append(key)

    if dispatched:
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
    reconcile(store, manifest, manifest_path)

    # 2. SYNC: 回收进行中节点的结果
    new_failures = collect_results(store, runtime, manifest, manifest_path,
                                   retry_limits=retry_limits, config=config)

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

    # 状态判定:running 优先(有在飞节点继续推进),其次 needs_decision(有失败),
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

"""pipeline/loop — 确定性单轮 tick(结果回收 → 就绪计算 → 派发)。

设计文档 §7.3:sync → decide → dispatch,状态全在 manifest + 平台,幂等。
硬性约束(§2.4):无自动重试——blocked 节点在后续 tick 保持 blocked,
重试只经 `omac node retry` 显式决策。abandoned 上游视同依赖已满足(P1.4)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from ..core import graph
from ..core.evidence import validate_review_evidence, validate_worker_evidence
from ..core.manifest import Manifest, save_manifest, set_node
from ..engines.models import WorkItemStatus
from ..engines.runtime import AgentRuntime
from ..engines.store import WorkItemStore
from ..errors import PlatformError

# manifest status 字符串常量
RUNNING_STATUSES = {"in_progress", "in_review"}
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


# ==================== reconcile ====================

def reconcile(store: WorkItemStore, manifest: Manifest, manifest_path: str) -> bool:
    """逐节点拿 work_item_id 去平台核对真实状态,同步回 manifest。

    - work_item_id 指向的 item 平台不存在 → 清空 work_item_id,标 todo 走新建
    - 平台状态与 manifest 不一致 → 以平台为准写回 manifest
    """
    changed = False
    for key, node in manifest.nodes.items():
        if not node.work_item_id:
            continue
        try:
            item = store.get_work_item(node.work_item_id)
        except Exception:
            # work item 不存在:非终态节点清空走新建,终态节点保持
            if node.status not in TERMINAL_STATUSES:
                set_node(manifest, key, work_item_id=None, status="todo")
                changed = True
            continue

        platform_status = item.status.value if hasattr(item.status, "value") else str(item.status)
        manifest_status = _PLATFORM_TO_MANIFEST.get(platform_status, platform_status)
        if manifest_status != node.status:
            set_node(manifest, key, status=manifest_status)
            changed = True

    if changed:
        save_manifest(manifest, manifest_path)
    return changed


# ==================== collect_results(SYNC) ====================

def collect_results(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    manifest_path: str,
) -> Dict[str, str]:
    """SYNC:回收进行中节点的结果。

    返回 {node_key: failure_reason} —— 空 dict 表示无新失败。

    in_progress 节点:
      worker DONE + 证据门过 → 有 reviewer: 转 in_review + assign reviewer + wake
                               无 reviewer: 标 done
      worker DONE + 证据门不过 → blocked,失败原因经 add_comment 回贴
      worker FAILED / BLOCKED → blocked
    in_review 节点:
      reviewer pass → done;reject → blocked(P4 前先 blocked)
    """
    failures: Dict[str, str] = {}
    pending_review: List[Tuple[str, str, str]] = []  # (key, item_id, reviewer)

    for key, node in manifest.nodes.items():
        if node.status not in RUNNING_STATUSES or not node.work_item_id:
            continue

        try:
            item = store.get_work_item(node.work_item_id)
        except Exception:
            continue

        # ---- in_progress: worker 阶段回收 ----
        if node.status == "in_progress":
            if item.status == WorkItemStatus.DONE:
                gate_errors = validate_worker_evidence(node, item)
                if gate_errors:
                    reason = "; ".join(gate_errors)
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(node.work_item_id, f"证据门未通过: {reason}")
                    set_node(manifest, key, status="blocked")
                    failures[key] = f"worker 证据门未通过: {reason}"
                elif node.reviewer:
                    pending_review.append((key, node.work_item_id, node.reviewer))
                else:
                    set_node(manifest, key, status="done")
            elif item.status == WorkItemStatus.FAILED:
                store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                store.add_comment(node.work_item_id, "worker 执行失败")
                set_node(manifest, key, status="blocked")
                failures[key] = "worker 执行失败"
            elif item.status == WorkItemStatus.BLOCKED:
                set_node(manifest, key, status="blocked")
                failures[key] = "worker 平台状态 blocked"

        # ---- in_review: reviewer 阶段回收 ----
        elif node.status == "in_review":
            verdict = item.review_verdict
            if not verdict:
                # reviewer 已落终态但缺结构化 review_verdict → blocked(无证据不予通过)
                if item.status in (WorkItemStatus.DONE, WorkItemStatus.FAILED, WorkItemStatus.BLOCKED):
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(
                        node.work_item_id,
                        f"reviewer 平台 {item.status.value} 但缺 review_verdict 结构化证据",
                    )
                    set_node(manifest, key, status="blocked")
                    failures[key] = "reviewer 缺 review_verdict"
                continue

            gate_errors = validate_review_evidence(node, item)
            if not gate_errors:
                store.update_status(node.work_item_id, WorkItemStatus.DONE)
                set_node(manifest, key, status="done")
            else:
                reason = "; ".join(gate_errors)
                store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                store.add_comment(node.work_item_id, f"评审证据门未通过: {reason}")
                set_node(manifest, key, status="blocked")
                failures[key] = f"评审证据门未通过: {reason}"

    # ---- reviewer 阶段过渡(遍历后执行,避免改 manifest 影响遍历)----
    for key, item_id, reviewer in pending_review:
        store.assign_work_item(item_id, reviewer, "reviewer")
        store.update_status(item_id, WorkItemStatus.IN_REVIEW)
        set_node(manifest, key, status="in_review")
        try:
            runtime.wake(item_id, reviewer, "reviewer")
        except PlatformError as exc:
            store.update_status(item_id, WorkItemStatus.BLOCKED)
            store.add_comment(item_id, f"唤醒 reviewer {reviewer} 失败: {exc}")
            set_node(manifest, key, status="blocked")
            failures[key] = f"唤醒 reviewer {reviewer} 失败"

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
        if manifest.nodes[key].status not in TERMINAL_STATUSES:
            set_node(manifest, key, status="blocked")
            newly_blocked.add(key)
    if newly_blocked:
        save_manifest(manifest, manifest_path)
    return newly_blocked


# ==================== DISPATCH ====================

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

        # 建工单(若无)
        if not node.work_item_id:
            item = store.create_work_item(
                workspace_id=workspace_id,
                title=node.title or key,
                description=node.description or f"Task {key}",
                dag_key=key,
                worker=worker,
                reviewer=node.reviewer,
                blocked_by=list(node.blocked_by),
            )
            if node.contract is not None:
                store.set_node_contract(item.id, node.contract)
            set_node(manifest, key, work_item_id=item.id)

        # fire-and-forget: assign worker + 标 in_progress + wake
        store.assign_work_item(node.work_item_id, worker, "worker")
        store.update_status(node.work_item_id, WorkItemStatus.IN_PROGRESS)
        set_node(manifest, key, status="in_progress")

        try:
            runtime.wake(node.work_item_id, worker, "worker")
        except PlatformError as exc:
            store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
            store.add_comment(node.work_item_id, f"唤醒 worker {worker} 失败: {exc}")
            set_node(manifest, key, status="blocked")
            continue

        dispatched.append(key)

    if dispatched:
        save_manifest(manifest, manifest_path)

    return dispatched


# ==================== tick(单轮完整推进) ====================

def tick(
    store: WorkItemStore,
    runtime: AgentRuntime,
    manifest: Manifest,
    manifest_path: str,
    max_parallel: int = 4,
) -> TickResult:
    """执行单轮 tick:reconcile → collect_results → decide → dispatch。

    幂等:全部状态在 manifest + 平台,中断重跑即续跑。无自动重试。
    """
    # 1. Reconcile: 平台状态同步回 manifest
    reconcile(store, manifest, manifest_path)

    # 2. SYNC: 回收进行中节点的结果
    new_failures = collect_results(store, runtime, manifest, manifest_path)

    # 3. 收集全部失败节点(含本轮新失败 + 历史已 blocked/failed)
    all_failed: Set[str] = set(new_failures.keys())
    for key, node in manifest.nodes.items():
        if node.status in FAILED_STATUSES:
            all_failed.add(key)

    # 失败隔离: 下游标 blocked
    if all_failed:
        _mark_downstream_blocked(manifest, manifest_path, all_failed)

    # 4. DECIDE: 计算就绪节点
    snapshot = _build_snapshot(manifest)
    ready = graph.ready_nodes(snapshot)

    # 5. DISPATCH: 派发就绪节点(受 max_parallel 约束)
    dispatched = _dispatch(store, runtime, manifest, manifest_path, ready, max_parallel)

    # 6. 保存 manifest
    save_manifest(manifest, manifest_path)

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
    else:
        state = "converged"

    # 报告(仅 needs_decision 时有内容)
    report: Dict[str, Any] = {}
    if state == "needs_decision":
        snapshot = _build_snapshot(manifest)
        downstream = graph.downstream_of(snapshot, set(failed_keys))
        report = {
            "failed_nodes": sorted(failed_keys),
            "evidence_summary": {
                k: new_failures.get(k, "历史 blocked/failed 节点")
                for k in sorted(failed_keys)
            },
            "blocked_downstream": sorted(set(downstream) & set(failed_keys)),
        }

    return TickResult(
        state=state,
        done=done,
        failed=failed_keys,
        running=running,
        dispatched=dispatched,
        report=report,
    )

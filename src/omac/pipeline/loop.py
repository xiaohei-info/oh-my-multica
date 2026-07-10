"""pipeline/loop — 确定性单轮 tick(结果回收 → 就绪计算 → 派发)。

设计文档 §7.3:sync → decide → dispatch,状态全在 manifest + 平台,幂等。
硬性约束(§2.4):无自动重试——blocked 节点在后续 tick 保持 blocked,
重试只经 `omac node retry` 显式决策。abandoned 上游视同依赖已满足(P1.4)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from ..core import graph, logsetup
from ..core.config import DEFAULT_RETRY
from ..core.evidence import validate_review_evidence, validate_worker_evidence
from ..core.gitsync import commit_manifest
from ..core.manifest import Manifest, save_manifest, set_node
from ..pipeline.delivery import advance_delivery, run_merge_delivery
from ..engines.models import WorkItemStatus
from ..engines.runtime import AgentRuntime
from ..engines.store import WorkItemStore
from ..errors import PlatformError
from ..pipeline.dispatch import normalize_source_refs, render_issue_body
from ..core.taskmeta import TaskKind, TaskPhase

log = logsetup.get_logger(__name__)

# dag 节点统一 kind(事件字段;与 run_task 的 plan/decompose/acceptance 区分)
_DAG_KIND = "develop"

# manifest status 字符串常量
RUNNING_STATUSES = {"in_progress", "ci_check", "in_review"}
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


# ==================== reconcile ====================

def reconcile(store: WorkItemStore, manifest: Manifest, manifest_path: str) -> bool:
    """逐节点拿 work_item_id 去平台核对真实状态,同步回 manifest。

    - work_item_id 指向的 item 平台不存在 → 清空 work_item_id,标 todo 走新建
    - 平台状态与 manifest 不一致 → 以平台为准写回 manifest

    运行中节点(in_progress/in_review)的终态回收由 collect_results 统一处理
    (证据门 + 阶段交接),reconcile 不同步其状态,避免把平台 DONE 直接写成
    manifest done 而短路证据门和 reviewer 交接。
    """
    changed = False
    for key, node in manifest.nodes.items():
        if not node.work_item_id:
            continue
        try:
            item = store.get_work_item(node.work_item_id)
        except Exception:
            # work item 不存在:调用者明确接受的终态(done/abandoned)保持;
            # blocked/failed/cancelled 可能是用户删掉平台 issue 后的恢复路径,
            # 应清空旧 id 走新建。
            if node.status not in {"done", "abandoned"}:
                set_node(manifest, key, work_item_id=None, status="todo")
                changed = True
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

    if changed:
        save_manifest(manifest, manifest_path)
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

        if (
            node.status == "in_progress"
            and item.status == WorkItemStatus.IN_REVIEW
            and getattr(item, "phase", TaskPhase.AUTHORING) == TaskPhase.AUTHORING
        ):
            store.assign_work_item(node.work_item_id, node.worker, "worker")
            store.update_status(node.work_item_id, WorkItemStatus.IN_PROGRESS)
            continue

        # ---- in_progress: worker 阶段回收 ----
        if node.status == "in_progress":
            if item.agent_run_finished_without_submit:
                worker_limit = limits.get("worker", DEFAULT_RETRY["worker"])
                cur_bounce = item.bounces.worker
                reason = "worker run 已结束但未通过 omac work submit 交付"
                if worker_limit == 0 or cur_bounce >= worker_limit:
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    set_node(manifest, key, status="blocked")
                    failures[key] = (
                        f"worker 未交付(回退上界 {worker_limit} 已耗尽): {reason}"
                    )
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id,
                             reason=f"worker 未交付回退上界({worker_limit})已耗尽")
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
                            f"回退到 worker {node.worker} 继续交付失败"
                            f"(已回滚回退计数): {exc}",
                        )
                        set_node(manifest, key, status="blocked")
                        failures[key] = f"回退到 worker {node.worker} 继续交付失败: {exc}"
                continue
            if item.status == WorkItemStatus.IN_PROGRESS:
                runtime.wake(node.work_item_id, node.worker, "worker")
                continue
            if item.status == WorkItemStatus.DONE:
                gate_errors = validate_worker_evidence(node, item)
                if gate_errors:
                    reason = "; ".join(gate_errors)
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(node.work_item_id, f"证据门未通过: {reason}")
                    set_node(manifest, key, status="blocked")
                    failures[key] = f"worker 证据门未通过: {reason}"
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason=f"worker 证据门: {reason}")
                    continue
                # worker 证据已过门 → CI 门(§7.3)。配置 ci 时运行 CI,绿才进评审;
                # 失败/超时 → 有界「回到 worker」(retry_limits["ci"])。
                # advance_delivery 已处理节点状态与平台状态切换;返回 'pass' 继续,
                # 'bounce' 已转回 worker(本 tick 不再推进), 'blocked' 则阻止后续。
                ci_action = advance_delivery(
                    config or {}, manifest, key, store, runtime, limits,
                    project_root=_project_root_from_manifest_path(manifest_path))
                if ci_action == "bounce":
                    failures[key] = "CI 未通过,已转回 worker(上界未耗尽,待重交)"
                    log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, gate="ci")
                    continue
                if ci_action == "blocked":
                    failures[key] = "CI 检查未通过,回退上界(retry.ci)已耗尽"
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason="CI 回退上界已耗尽")
                    continue
                # CI 绿(或无可用 CI 而跳过):nits follow-up 已经由上一轮 reviewer 接受,
                # worker 修完后直接进入 merge/done,不再浪费第二轮 reviewer。
                if item.review_verdict == "pass-with-nits":
                    merge_action = run_merge_delivery(
                        config or {}, manifest, key, store, runtime, limits)
                    if merge_action == "pass":
                        store.reset_review(node.work_item_id)
                        store.update_status(node.work_item_id, WorkItemStatus.DONE)
                        set_node(manifest, key, status="done")
                        log.info(logsetup.EVT_NODE_DONE, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id)
                    elif merge_action == "blocked":
                        failures[key] = "merge 失败,回退上界(retry.merge)已耗尽"
                        log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id, reason="merge 回退上界已耗尽")
                elif node.reviewer:
                    pending_review.append((key, node.work_item_id, node.reviewer))
                else:
                    # 无 reviewer 时 CI 绿即可直接 done;同步把平台工单置 DONE,
                    # 避免 advance_delivery 把工单倒回 IN_PROGRESS 后,
                    # reconcile 下轮把节点从 done 拉回 in_progress 形成永久循环。
                    store.update_status(node.work_item_id, WorkItemStatus.DONE)
                    set_node(manifest, key, status="done")
                    log.info(logsetup.EVT_NODE_DONE, kind=_DAG_KIND, node=key,
                             id=node.work_item_id)
            elif item.status == WorkItemStatus.FAILED:
                store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                store.add_comment(node.work_item_id, "worker 执行失败")
                set_node(manifest, key, status="blocked")
                failures[key] = "worker 执行失败"
                log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                         id=node.work_item_id, reason="worker 执行失败")
            elif item.status == WorkItemStatus.BLOCKED:
                set_node(manifest, key, status="blocked")
                failures[key] = "worker 平台状态 blocked"
                log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                         id=node.work_item_id, reason="worker 平台 blocked")

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
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason="reviewer 缺 review_verdict")
                continue

            log.info(logsetup.EVT_VERDICT, kind=_DAG_KIND, node=key,
                     id=node.work_item_id, verdict=verdict)
            if verdict == "pass-with-nits":
                store.update_work_item_metadata(
                    node.work_item_id, phase=TaskPhase.AUTHORING,
                    review_comment="")
                try:
                    store.assign_work_item(node.work_item_id, node.worker, "worker")
                    store.update_status(node.work_item_id, WorkItemStatus.IN_PROGRESS)
                    set_node(manifest, key, status="in_progress")
                    log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, gate="review-nits")
                    runtime.wake(node.work_item_id, node.worker, "worker")
                except PlatformError as exc:
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(
                        node.work_item_id,
                        f"回退到 worker {node.worker} 处理 nits 失败: {exc}")
                    set_node(manifest, key, status="blocked")
                    failures[key] = f"回退到 worker {node.worker} 处理 nits 失败: {exc}"
                continue
            gate_errors = validate_review_evidence(node, item)
            if not gate_errors and verdict != "reject":
                # reviewer pass → P4.2 自动 merge 门。未显式配置 merge.command 时
                # 使用默认 gh pr merge 命令。
                merge_action = run_merge_delivery(
                    config or {}, manifest, key, store, runtime, limits)
                if merge_action == "pass":
                    store.update_status(node.work_item_id, WorkItemStatus.DONE)
                    set_node(manifest, key, status="done")
                    log.info(logsetup.EVT_NODE_DONE, kind=_DAG_KIND, node=key,
                             id=node.work_item_id)
                elif merge_action == "blocked":
                    failures[key] = "merge 失败,回退上界(retry.merge)已耗尽"
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id, reason="merge 回退上界已耗尽")
                # else "bounce": 节点已转回 in_progress,本 tick 不再推进。
            else:
                # reviewer reject 或评审证据不合格:有界「回到 worker」回退,
                # 受 retry_limits["review"] 约束。
                review_limit = limits.get("review", DEFAULT_RETRY["review"])
                cur_bounce = item.bounces.review
                reason = "; ".join(gate_errors) if gate_errors else "reviewer reject"
                if review_limit == 0 or cur_bounce >= review_limit:
                    store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    store.add_comment(node.work_item_id, f"评审证据门上界({review_limit})已耗尽: {reason}")
                    set_node(manifest, key, status="blocked")
                    failures[key] = f"评审证据门未通过(回退上界 {review_limit} 已耗尽): {reason}"
                    log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                             id=node.work_item_id,
                             reason=f"评审回退上界({review_limit})已耗尽")
                else:
                    # 有界「回到 worker」:先记回退计数并清除旧评审判定,再重新派发 worker。
                    # 派发失败时回滚回退计数并把节点标 blocked,避免卡在「已清判定/未派发」中间态。
                    store.update_work_item_metadata(node.work_item_id, review_bounce=cur_bounce + 1)
                    store.reset_review(node.work_item_id)
                    # 派发失败时回滚 review_bounce,避免把「未成功的回退」计为消耗;
                    # 这与 CI 回退路径(delivery.advance_delivery)的语义对称 ——
                    # 两者都是「计数只在派发成功时才真正消耗」。
                    try:
                        store.assign_work_item(node.work_item_id, node.worker, "worker")
                        store.update_status(node.work_item_id, WorkItemStatus.IN_PROGRESS)
                        set_node(manifest, key, status="in_progress")
                        log.info(logsetup.EVT_REVISION, kind=_DAG_KIND, node=key,
                                 id=node.work_item_id, gate="review",
                                 round=cur_bounce + 1, max=review_limit)
                        runtime.wake(node.work_item_id, node.worker, "worker")
                    except PlatformError as exc:
                        store.update_work_item_metadata(node.work_item_id, review_bounce=cur_bounce)
                        store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                        store.add_comment(
                            node.work_item_id,
                            f"回退到 worker {node.worker} 失败(已回滚回退计数): {exc}")
                        set_node(manifest, key, status="blocked")
                        failures[key] = f"回退到 worker {node.worker} 失败: {exc}"

    # ---- reviewer 阶段过渡(遍历后执行,避免改 manifest 影响遍历)----
    for key, item_id, reviewer in pending_review:
        nd = manifest.nodes[key]
        # 先把工单标 IN_REVIEW 再派发 reviewer,否则 mock 下 assign 内
        # get_work_item 触发的 auto_complete 会先在 IN_PROGRESS(刚从
        # CI 回落)走 deliverable 路径把 assigned 槽位清空,后续
        # wake 的 auto_complete 找不到已派发项而无法置评审判定。
        store.update_status(item_id, WorkItemStatus.IN_REVIEW)
        store.update_work_item_metadata(item_id, phase=TaskPhase.REVIEW)
        store.assign_work_item(item_id, reviewer, "reviewer")
        set_node(manifest, key, status="in_review")
        log.info(logsetup.EVT_REVIEW_DISPATCH, kind=_DAG_KIND, node=key,
                 id=item_id, reviewer=reviewer)
        try:
            runtime.wake(item_id, reviewer, "reviewer")
        except PlatformError as exc:
            store.update_status(item_id, WorkItemStatus.BLOCKED)
            store.add_comment(item_id, f"唤醒 reviewer {reviewer} 失败: {exc}")
            set_node(manifest, key, status="blocked")
            failures[key] = f"唤醒 reviewer {reviewer} 失败"
            log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                     id=item_id, reason=f"唤醒 reviewer {reviewer} 失败")

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
        labels=["设计方案", "验收文档", "任务拆解"],
        engine_env=engine_env,
    )
    dependency_refs = []
    for dependency_key in node.blocked_by:
        dependency = manifest.nodes.get(dependency_key)
        if dependency is None or not dependency.work_item_id:
            continue
        dependency_refs.append({
            "label": f"前置开发任务 · {dependency.title or dependency_key}",
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

        # 建工单(若无)
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
            if node.contract is not None:
                store.set_node_contract(item.id, node.contract)
            env = _store_env(store)
            source_refs = _develop_source_refs(manifest, node, env)
            body = render_issue_body(
                node, node.contract, TaskKind.DEVELOP, item.id,
                source_refs=source_refs,
                engine_env=env,
                issue_key=getattr(item, "identifier", None),
            )
            store.update_work_item_metadata(
                item.id,
                description=body,
                source_refs=source_refs,
            )
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
            log.info(logsetup.EVT_NODE_FAILED, kind=_DAG_KIND, node=key,
                     id=node.work_item_id, reason=f"唤醒 worker {worker} 失败")
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

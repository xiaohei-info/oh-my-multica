"""delivery — CI 监控与 ci_check 有界回退引擎(设计文档 §7.3)。

本模块承载 loop 收割顺序「worker 证据过门 → ci_check →(绿)in_review」中
worker 证据过门之后、进评审之前的 CI 门:

    worker 证据过门 ─► ci_check ─ 绿 ──► in_review 转派 reviewer
                              │
                              └ 失败/超时 ──► 有界转回 worker(ci_bounce+1,
                                               ≥ 上界 → blocked)

评审 reject 的回退(P4 任务 §7.3)由 ``loop.collect_results`` 内联实现
(读/写 ``WorkItem.bounces.review``),本模块不重复——避免与主线状态机分歧。

回退计数统一存放在平台侧 ``WorkItem.bounces`` 的 ``.ci`` 字段,
由 ``WorkItemStore.update_work_item_metadata(ci_bounce=...)`` 写入,Store 只存取;
manifest 的 ``Node`` 不携带回退计数(单一事实源)。上界由 ``config.retry.ci``
(缺省 3)经 ``resolve_retry`` 解析后注入 ``loop.tick`` 的 ``retry_limits``。

本模块还承载 reviewer pass 之后的 P4.2 自动 merge 门(§7.3):

    reviewer pass ─► merging ─ merge.command ─ 成功 ──► done(已合入)
                                         │
                                         └ 冲突/失败 ──► 有界转回 worker
                                                        (merge_bounce+1,
                                                         无剩余返工次数 → blocked)

纪律(§12.4):CI / merge 只调用 WorkItemStore adapter，pipeline 不直接执行平台命令;
CI 在显式配置 ``config.ci.check_command`` 或检测到 ``.github/workflows``
时启用;否则跳过。命令模板只作为 adapter 输入，平台错误由 adapter 分类传播。
考试点:退出码契约不可破(§5.1),
术语 §10.2 用「进行中节点」「就绪节点」,禁止 harvest/在飞 等硬译行话。
"""
from __future__ import annotations

import time

from ..core.config import DEFAULT_RETRY, get_ci_config, get_merge_config
from ..core.evidence import delivered_revision_of
from ..engines.models import (
    DeliveryAction,
    DeliveryBlockReason,
    DeliveryCommandOutcome,
    DeliveryResult,
    WorkItemStatus,
)
from ..engines.runtime import AgentRuntime
from ..core.manifest import Manifest
from ..errors import AuthError, PlatformError, ValidationError
from ..i18n import ui

# ── manifest 侧细分态 ↔ 平台 WorkItemStatus 映射表 ──────────────────────────
# ci_check 是 manifest 侧细分态;平台侧仍映射到 in_progress,保证平台状态机不被
# 细分态污染(设计文档 §7.3)。映射表写清并测试;未知状态报错即教学。
MANIFEST_TO_PLATFORM_STATUS: dict[str, WorkItemStatus] = {
    "todo": WorkItemStatus.TODO,
    "in_progress": WorkItemStatus.IN_PROGRESS,
    "ci_check": WorkItemStatus.IN_PROGRESS,
    "in_review": WorkItemStatus.IN_REVIEW,
    "merging": WorkItemStatus.IN_REVIEW,
    "done": WorkItemStatus.DONE,
    "blocked": WorkItemStatus.BLOCKED,
}

VALID_MANIFEST_STATUSES = set(MANIFEST_TO_PLATFORM_STATUS)


def to_platform_status(manifest_status: str) -> WorkItemStatus:
    """manifest 侧状态 → 平台 WorkItemStatus。未知状态报错即教学。"""
    if manifest_status not in MANIFEST_TO_PLATFORM_STATUS:
        raise ValueError(ui(
            f"Unknown manifest node status {manifest_status!r}. Valid values: "
            f"{sorted(VALID_MANIFEST_STATUSES)}",
            f"未知的 manifest 节点状态 {manifest_status!r} —— 合法值: "
            f"{sorted(VALID_MANIFEST_STATUSES)}"))
    return MANIFEST_TO_PLATFORM_STATUS[manifest_status]


# ── worker 证据过门后的 CI 门推进 ─────────────────────────────────────────────

def advance_delivery(
    config: dict,
    manifest: Manifest,
    node_key: str,
    store: object,
    runtime: AgentRuntime,
    retry_limits: dict,
    project_root: str = ".",
) -> DeliveryResult:
    """worker 证据已过门后推进 CI 门(§7.3)。

    - 未配置 ci 且未检测到 GitHub workflow → 环节整体跳过，返回 PASS。
    - 配置了 ci 或检测到 GitHub workflow → 进入 ``ci_check``(manifest 细分态,平台仍 in_progress),执行
      ``ci.check_command``:
        * 绿 → 回到 ``in_progress``，返回 PASS
        * 失败/超时 → 失败摘要(命令输出尾部) add_comment + 转回 worker +
          wake + ``ci_bounce``+1;
          - 已完成返工次数达到 ``retry_limits['ci']`` → BLOCKED(RETRY_EXHAUSTED)
          - 否则返回 BOUNCE(节点已置回 in_progress，loop 本 tick 不动它)。

    回退计数(读/写)经平台 ``WorkItem.bounces.ci``(单一事实源,Store 只存取);
    manifest Node 不持计数。
    BLOCKED 结果携带稳定原因，供 caller 报告真实修复动作。
    """
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    ci = get_ci_config(config, root=project_root)
    if ci is None:
        return DeliveryResult(DeliveryAction.PASS)

    item = store.get_work_item(item_id)
    pr_url = ""
    if isinstance(item.artifacts, dict):
        pr_url = item.artifacts.get("pr_url") or ""

    if not pr_url:
        # CI 已配置但 worker 未提交 pr_url —— 证据门本应挡住,此处防御性阻断并教化。
        store.add_comment(item_id, ui(
            "⚠️ CI is configured, but the worker did not submit pr_url, so the CI check cannot run.\n"
            "Resubmit with `omac work submit <id> --pr-url <url> --verification-file <ev.yaml>`.",
            "⚠️ CI 已配置(check_command)但 worker 未提交 pr_url,无法运行 CI 检查。\\n"
            "请用 `omac work submit <id> --pr-url <url> --verification-file <ev.yaml>` "
            "补交 PR 地址后重新提交。"))
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return DeliveryResult(
            DeliveryAction.BLOCKED, DeliveryBlockReason.MISSING_PR)

    # 进入 ci_check(manifest 细分态;平台仍 in_progress)
    node.status = "ci_check"
    store.update_status(item_id, to_platform_status("ci_check"))

    try:
        result = store.run_ci_check(
            pr_url, ci["check_command"], ci.get("timeout_minutes", 30))
    except (AuthError, PlatformError):
        node.status = "in_progress"
        raise
    if result.outcome is DeliveryCommandOutcome.TIMED_OUT:
        node.status = "in_progress"
        raise PlatformError(ui(
            f"CI check timed out for {pr_url}. Retry after platform connectivity recovers.",
            f"CI 检查超时: {pr_url}。请在平台连接恢复后重试。"))
    if result.passed:
        node.status = "in_progress"
        store.update_status(item_id, to_platform_status("in_progress"))
        return DeliveryResult(DeliveryAction.PASS)
    if result.outcome is not DeliveryCommandOutcome.FAILED:
        node.status = "in_progress"
        raise PlatformError(ui(
            f"CI adapter returned unsupported outcome: {result.outcome}",
            f"CI adapter 返回未知结果: {result.outcome}"))

    cur_bounce = item.bounces.ci
    ci_limit = retry_limits.get("ci", DEFAULT_RETRY["ci"])
    label = ui(
        f"CI check failed (exit code {result.exit_code})",
        f"CI 检查失败(退出码 {result.exit_code})")
    if ci_limit == 0 or cur_bounce >= ci_limit:
        store.add_comment(item_id, ui(
            f"⚠️ {label}. Worker retry limit is exhausted.\n\n"
            f"--- Command output tail ---\n{result.summary}",
            f"⚠️ {label} —— Worker 返工次数已耗尽。\n\n"
            f"--- 命令输出尾部 ---\n{result.summary}"))
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return DeliveryResult(
            DeliveryAction.BLOCKED, DeliveryBlockReason.RETRY_EXHAUSTED)

    next_bounce = cur_bounce + 1
    store.add_comment(item_id, ui(
        f"⚠️ {label}. Returning to the worker for repair and resubmission.\n\n"
        f"--- Command output tail ---\n{result.summary}",
        f"⚠️ {label} —— CI 未通过,转回 worker 修复后重新提交。\n\n"
        f"--- 命令输出尾部 ---\n{result.summary}"))
    store.update_work_item_metadata(item_id, ci_bounce=next_bounce)
    node.status = "in_progress"
    store.update_status(item_id, to_platform_status("in_progress"))
    handoff_failure = _handoff_worker(
        node,
        store,
        runtime,
        bounce_field="ci_bounce",
        previous_bounce=cur_bounce,
        stage="CI",
    )
    if handoff_failure is not None:
        return handoff_failure
    return DeliveryResult(DeliveryAction.BOUNCE)


# ── P4.2 自动 merge 与冲突回退 ──────────────────────────────────────────────

def run_merge_delivery(
    config: dict,
    manifest: Manifest,
    node_key: str,
    store: object,
    runtime: AgentRuntime,
    retry_limits: dict,
) -> DeliveryResult:
    """reviewer pass 后、进 done 之前的自动 merge 门(§7.3)。

    - 未配置 merge → 默认执行带 ``--match-head-commit {delivered_revision}`` 的 GitHub merge。
    - 配置了 merge 但节点无 pr_url → 防御性 BLOCKED(MISSING_PR) + 报错即教学。
    - 配置了 merge → command 必须同时包含 ``{pr_url}`` 和
      ``{delivered_revision}``;进入 ``merging``(manifest 细分态,平台仍 in_review),执行:
        * 成功 → 回到 ``in_progress`` 语义即「已合入」;manifest ``Node`` 记录
          ``merged: true`` / ``merged_at``；返回 PASS(loop 随即 ``done``)。
        * 冲突/失败 → 失败摘要(命令输出尾部) add_comment + reset_review + 转回
          worker + wake + ``merge_bounce``+1;
          - 已完成返工次数达到 ``retry_limits['merge']`` → BLOCKED(RETRY_EXHAUSTED)
          - 否则返回 BOUNCE(节点已置回 in_progress，loop 本 tick 不动它)。

    回退计数(读/写)经平台 ``WorkItem.bounces.merge``(单一事实源,Store 只存取);
    manifest Node 不持计数。上界由 ``config.retry.merge``(缺省 3)经
    ``resolve_retry`` 解析后注入 ``loop.tick`` 的 ``retry_limits``。
    """
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    runtime_config = dict(config or {})
    engine_type = getattr(getattr(store, "config", None), "engine_type", None)
    if engine_type:
        runtime_config["engine"] = engine_type
    merge = get_merge_config(runtime_config)

    item = store.get_work_item(item_id)
    pr_url = ""
    if isinstance(item.artifacts, dict):
        pr_url = item.artifacts.get("pr_url") or ""

    if not pr_url:
        # merge 已配置但 reviewer pass 后无 pr_url —— 防御性阻断并教化。
        store.add_comment(item_id, ui(
            "⚠️ Merge is configured, but the node has no pr_url, so automatic merge cannot run.\n"
            "Confirm the worker submitted a PR URL with `omac work submit <id> --pr-url <url> ...`.",
            "⚠️ merge 已配置(command)但节点无 pr_url,无法执行自动合并。\n"
            "请确认 worker 已用 `omac work submit <id> --pr-url <url> ...` 提交 PR 地址。"))
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return DeliveryResult(
            DeliveryAction.BLOCKED, DeliveryBlockReason.MISSING_PR)

    delivered_revision = delivered_revision_of(item.verification)
    if delivered_revision is None:
        store.add_comment(item_id, ui(
            "⚠️ Merge is blocked because Worker delivered_revision is missing. "
            "Submit fresh Worker evidence bound to the current PR head.",
            "⚠️ merge 已阻断：Worker delivered_revision 缺失。"
            "请提交绑定当前 PR head 的新 Worker 证据。",
        ))
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return DeliveryResult(
            DeliveryAction.BLOCKED, DeliveryBlockReason.MISSING_REVISION)

    # 先读取权威 PR 快照。MERGED 只用于恢复：必须由持久化 intent 证明本次
    # OMAC merge 的目标 URL 与 delivered revision 完全一致，不能仅凭外部状态
    # 或 manifest 的 merged 标志收口。
    from .dispatch import inspect_ready_pull_request
    snapshot = inspect_ready_pull_request(store, pr_url, allow_merged=True)
    if snapshot.state == "MERGED":
        return _recover_merged_delivery(
            node, item, snapshot, delivered_revision, store)

    merge_intent = {
        "pr_url": snapshot.url.rstrip("/"),
        "delivered_revision": delivered_revision,
    }
    # durable intent 必须先于任何外部 merge 副作用落盘。之后即使 merge 成功、
    # 平台状态或 manifest 持久化失败，重启仍有可核验的恢复依据。
    store.update_work_item_metadata(item_id, merge_intent=merge_intent)

    # 进入 merging(manifest 细分态;平台仍 in_review)
    node.status = "merging"
    store.update_status(item_id, to_platform_status("merging"))

    try:
        result = store.merge_pull_request(
            pr_url,
            delivered_revision,
            merge["command"],
            merge.get("timeout_minutes", 30),
        )
    except (AuthError, PlatformError):
        node.status = "in_review"
        raise
    if result.outcome is DeliveryCommandOutcome.TIMED_OUT:
        node.status = "in_review"
        raise PlatformError(ui(
            f"Merge command timed out for {pr_url}. Retry after platform connectivity recovers.",
            f"merge 命令超时: {pr_url}。请在平台连接恢复后重试。"))
    if result.passed:
        # 合入成功 → done = 已合入集成分支
        node.status = "in_progress"
        node.merged = True
        node.merged_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        store.update_status(item_id, to_platform_status("in_progress"))
        return DeliveryResult(DeliveryAction.PASS)
    if result.outcome is not DeliveryCommandOutcome.FAILED:
        node.status = "in_review"
        raise PlatformError(ui(
            f"Merge adapter returned unsupported outcome: {result.outcome}",
            f"merge adapter 返回未知结果: {result.outcome}"))

    # adapter 已明确确认 merge 未发生，清除本次 intent 后才允许回退。
    # 超时/平台异常结果不确定，intent 必须保留供下一轮读取权威 PR 状态恢复。
    store.update_work_item_metadata(item_id, merge_intent={})
    return _bounce_or_block_merge(
        node, item, store, runtime, retry_limits,
        label=ui(
            f"Merge command failed (exit code {result.exit_code})",
            f"merge 命令失败(退出码 {result.exit_code})"),
        summary=result.summary)


def _recover_merged_delivery(
    node,
    item,
    snapshot,
    delivered_revision: str,
    store,
) -> DeliveryResult:
    """仅凭同一 PR + 同一 delivered revision 的 durable intent 恢复 merge。"""
    intent = getattr(item, "merge_intent", None)
    intent_url = intent.get("pr_url") if isinstance(intent, dict) else None
    intent_revision = (
        intent.get("delivered_revision") if isinstance(intent, dict) else None
    )
    canonical_snapshot_url = snapshot.url.rstrip("/")
    valid_intent = bool(
        isinstance(intent_url, str)
        and intent_url.strip()
        and isinstance(intent_revision, str)
        and intent_revision.strip()
        and intent_url.rstrip("/") == canonical_snapshot_url
        and intent_revision == delivered_revision
        and snapshot.head_revision == delivered_revision
    )
    if not valid_intent:
        raise ValidationError(ui(
            "The PR is already MERGED, but OMAC cannot prove that it is the merge "
            "started for this delivery revision. Refusing to return the node to the "
            "Worker or mark it done.\n"
            f"Expected PR/revision: {snapshot.url} @ {delivered_revision}; "
            f"stored merge_intent: {intent!r}; current PR head: {snapshot.head_revision}.\n"
            f"Inspect with `omac node show <manifest> {node.id}`. If the merge is "
            "intentionally accepted, run `omac node accept <manifest> "
            f"{node.id}`; otherwise repair the stored delivery metadata and rerun.",
            "PR 已是 MERGED，但 OMAC 无法证明它就是针对当前交付 revision 发起的 "
            "merge。为避免误完成或把已合并代码退回 Worker，当前拒绝继续。\n"
            f"期望 PR/revision: {snapshot.url} @ {delivered_revision}；"
            f"已存 merge_intent: {intent!r}；当前 PR head: {snapshot.head_revision}。\n"
            f"请先执行 `omac node show <manifest> {node.id}` 检查。若确认接受该 "
            "merge，执行 `omac node accept <manifest> "
            f"{node.id}`；否则修复交付元数据后重跑。",
        ))

    node.status = "in_progress"
    node.merged = True
    node.merged_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.update_status(node.work_item_id, to_platform_status("in_progress"))
    return DeliveryResult(DeliveryAction.PASS)


def _bounce_or_block_merge(node, item, store, runtime, retry_limits, *,
                           label: str, summary: str) -> DeliveryResult:
    """merge 失败后的有界「回到 worker」回退(CI 路径的对称实现)。

    复用与 CI 回退相同的单一事实源与封顶语义:
    - 失败摘要(输出尾部)评论回 issue 让 worker 知道解什么;
    - reset_review 使旧评审结论失效,强制重走完整 ci→review→merge 链;
    - 读/写 WorkItem.bounces.merge,封顶即 blocked,否则转回 worker + wake。
    """
    item_id = node.work_item_id
    cur_bounce = item.bounces.merge
    merge_limit = retry_limits.get("merge", DEFAULT_RETRY["merge"])
    if merge_limit == 0 or cur_bounce >= merge_limit:
        store.add_comment(item_id, ui(
            f"⚠️ {label}. Worker retry limit is exhausted.\n\n"
            f"--- Command output tail ---\n{summary}",
            f"⚠️ {label} —— Worker 返工次数已耗尽。\n\n"
            f"--- 命令输出尾部 ---\n{summary}"))
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return DeliveryResult(
            DeliveryAction.BLOCKED, DeliveryBlockReason.RETRY_EXHAUSTED)

    next_bounce = cur_bounce + 1
    store.add_comment(item_id, ui(
        f"⚠️ {label}. Returning to the worker to resolve the merge and rerun ci→review→merge.\n\n"
        f"--- Command output tail ---\n{summary}",
        f"⚠️ {label} —— merge 冲突,转回 worker 解决后重新走 ci→review→merge。\n\n"
        f"--- 命令输出尾部 ---\n{summary}"))
    store.update_work_item_metadata(item_id, merge_bounce=next_bounce)
    node.status = "in_progress"
    store.update_status(item_id, to_platform_status("in_progress"))
    store.reset_review(item_id)
    handoff_failure = _handoff_worker(
        node,
        store,
        runtime,
        bounce_field="merge_bounce",
        previous_bounce=cur_bounce,
        stage="merge",
    )
    if handoff_failure is not None:
        return handoff_failure
    return DeliveryResult(DeliveryAction.BOUNCE)


def _handoff_worker(
    node,
    store,
    runtime,
    *,
    bounce_field: str,
    previous_bounce: int,
    stage: str,
) -> DeliveryResult | None:
    """把 assignment + wake 作为一个可补偿的 worker handoff 边界。"""
    reason = DeliveryBlockReason.ASSIGNMENT_FAILED
    try:
        store.assign_work_item(node.work_item_id, node.worker, "worker")
        reason = DeliveryBlockReason.WAKE_FAILED
        runtime.wake(node.work_item_id, node.worker, "worker")
        return None
    except (AuthError, PlatformError) as exc:
        store.update_work_item_metadata(
            node.work_item_id, **{bounce_field: previous_bounce})
        node.status = "blocked"
        store.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
        operation = "assign" if reason is DeliveryBlockReason.ASSIGNMENT_FAILED else "wake"
        store.add_comment(node.work_item_id, ui(
            f"Failed to {operation} worker {node.worker} during {stage} handoff; "
            f"retry count rolled back: {exc}",
            f"{stage} 回派期间无法{('分配' if operation == 'assign' else '唤醒')} "
            f"worker {node.worker}，已回滚返工计数: {exc}"))
        return DeliveryResult(
            DeliveryAction.BLOCKED,
            reason,
            str(exc),
        )

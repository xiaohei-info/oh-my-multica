"""pipeline/report — dag status / exit 20 共享的结构化报告(单一 schema 模块)。

设计文档 §5.2/§13.3:dag status --output json 与 dag run exit 20 报告共用同一 schema,
P5 web 与 agent 都消费它。schema 用本模块的 *_KEYS 常量锁定,测试断言字段不变。

退出码约定(§5.1):dag status 正常观测退出 0；平台/网络错误退出 2，
认证错误退出 3。未知结果不得伪装成业务状态。
dag run/tick 在 needs_decision 非空时 exit 20,report 结构完全相同。
"""
from __future__ import annotations

from ..core.manifest import Manifest
from ..engines.models import WorkItemPayload
from ..engines.store import WorkItemStore
from ..core import graph


# ==================== schema 常量(测试锁定) ====================

STATUS_REPORT_KEYS = ("manifest", "progress", "nodes", "needs_decision")
PROGRESS_KEYS = (
    "total", "done", "running", "todo", "blocked",
    "failed", "abandoned", "converged",
)
NODE_KEYS = (
    "key", "status", "worker", "reviewer", "work_item_id",
    "pr_url", "blocked_by",
)
NEEDS_DECISION_KEYS = ("failed_nodes", "blocked_downstream", "next_actions")

# 全终态集合(含 abandoned)
TERMINAL_ALL = {"done", "cancelled", "abandoned", "failed", "blocked"}
FAILED_STATUSES = {"failed", "blocked"}


# ==================== 构建 ====================

def _classify(status: str) -> str:
    """manifest 状态 → progress 桶名。"""
    if status == "done":
        return "done"
    if status in ("in_progress", "ci_check", "in_review", "merging"):
        return "running"
    if status == "todo":
        return "todo"
    if status == "blocked":
        return "blocked"
    if status == "failed":
        return "failed"
    if status == "abandoned":
        return "abandoned"
    return "todo"


def _graph_snapshot(manifest: Manifest) -> dict:
    """graph 算法消费的快照(仅 status + blocked_by)。"""
    return {
        key: {"status": node.status, "blocked_by": list(node.blocked_by)}
        for key, node in manifest.nodes.items()
    }


def _observations_to_items_and_payloads(
    observations: dict,
) -> tuple[dict, dict]:
    """reconcile observations → (items, deferred_payloads)。

    observations: 节点 key → WorkItemControlProjection | None。None 观察
    (节点无 work_item_id、平台已删除即 _MISSING_WORK_ITEM、或读取失败)
    映射为 None item——与旧 _fetch_items 的失败宽容行为等价。
    """
    items: dict = {}
    deferred_payloads: dict = {}
    for key, observation in observations.items():
        if observation is None:
            items[key] = None
            continue
        items[key] = observation.work_item
        deferred_payloads[key] = observation.deferred_payloads
    return items, deferred_payloads


def _node_row(node, item) -> dict:
    pr_url = None
    if item is not None and item.artifacts:
        pr_url = item.artifacts.get("pr_url")
    return {
        "key": node.id,
        "status": node.status,
        "worker": node.worker,
        "reviewer": node.reviewer,
        "work_item_id": node.work_item_id,
        "pr_url": pr_url,
        "blocked_by": list(node.blocked_by),
    }


def _build_failed_node(
    node,
    item,
    reason: str | None = None,
    deferred_payloads=frozenset(),
) -> dict:
    """单个失败/受阻节点详情(锁定结构)。reason 优先使用调用方传入的精确原因。"""
    pr_url = None
    evidence_summary = None

    if item is not None:
        if item.artifacts:
            pr_url = item.artifacts.get("pr_url")
        evidence_summary = {
            "review_verdict": item.review_verdict,
            "review_comment": item.review_comment,
            "has_verification": (
                item.verification is not None
                or WorkItemPayload.VERIFICATION in deferred_payloads
            ),
            "has_review": (
                item.review_report is not None
                or WorkItemPayload.REVIEW_REPORT in deferred_payloads
            ),
        }

    if reason is None:
        # 无精确原因时,从 item 状态推导(仅供 status 观测回退)
        if item is not None:
            if item.review_verdict and item.review_verdict not in ("pass", "pass-with-nits"):
                reason = f"review rejected: {item.review_verdict}"
            elif item.status.value == "failed":
                reason = "worker failed"
            elif item.status.value == "blocked":
                reason = "blocked on platform"
        if reason is None:
            reason = node.status

    return {
        "key": node.id,
        "status": node.status,
        "reason": reason,
        "work_item_id": node.work_item_id,
        "pr_url": pr_url,
        "evidence_summary": evidence_summary,
    }


def _next_actions(failed_nodes: list, manifest_path: str) -> list:
    """为每个失败节点给出可执行的下一步命令(§5.2:精确到完整命令行)。"""
    actions = []
    for fn in failed_nodes:
        key = fn["key"]
        actions.append(f"omac node retry {manifest_path} {key}")
        actions.append(f"omac node abandon {manifest_path} {key}")
    return actions


def build_needs_decision(
    manifest: Manifest,
    manifest_path: str,
    failed_keys: set[str],
    observations: dict,
    evidence: dict[str, str] | None = None,
) -> dict:
    """构建 needs-decision 段(锁定 NEEDS_DECISION_KEYS)。

    observations 复用本轮 reconcile 的观察快照(key → WorkItemControlProjection
    | None),不再二次读取平台——tick 刚完成全量 reconcile,observations 是
    最新鲜的事实。
    evidence: 节点 key → 精确失败原因(由 tick 的 collect_results 提供);
             未传入时从 item 状态推导。
    """
    items, deferred_payloads = _observations_to_items_and_payloads(observations)
    return _build_needs_decision_from_items(
        manifest, manifest_path, failed_keys,
        items, evidence, deferred_payloads)


def _build_needs_decision_from_items(
    manifest: Manifest,
    manifest_path: str,
    failed_keys: set[str],
    items: dict,
    evidence: dict[str, str] | None = None,
    deferred_payloads: dict | None = None,
) -> dict:
    """从调用方提供的 WorkItem 快照构建决策段，避免重复平台读取。"""
    evidence = evidence or {}
    deferred_payloads = deferred_payloads or {}
    snapshot = _graph_snapshot(manifest)
    downstream = graph.downstream_of(snapshot, failed_keys)
    blocked_downstream = sorted(downstream)
    failed_nodes = [
        _build_failed_node(
            manifest.nodes[key],
            items.get(key),
            reason=evidence.get(key),
            deferred_payloads=deferred_payloads.get(key, frozenset()),
        )
        for key in sorted(failed_keys)
    ]
    next_actions = _next_actions(failed_nodes, manifest_path)
    return {
        "failed_nodes": failed_nodes,
        "blocked_downstream": blocked_downstream,
        "next_actions": next_actions,
    }


def _build_report_from_items(
    manifest: Manifest,
    manifest_path: str,
    items: dict,
    deferred_payloads: dict | None = None,
) -> dict:
    """用已经取得的 item 快照构建稳定 schema；不拥有任何外部读取。"""
    total = len(manifest.nodes)
    counts = {k: 0 for k in (
        "done", "running", "todo", "blocked", "failed", "abandoned")}
    for node in manifest.nodes.values():
        counts[_classify(node.status)] += 1
    converged = total > 0 and counts["done"] + counts["abandoned"] == total

    nodes = [_node_row(manifest.nodes[key], items.get(key)) for key in manifest.nodes]
    failed_keys = {
        key for key, node in manifest.nodes.items()
        if node.status in FAILED_STATUSES
    }
    needs_decision = (
        _build_needs_decision_from_items(
            manifest,
            manifest_path,
            failed_keys,
            items,
            evidence=None,
            deferred_payloads=deferred_payloads,
        )
        if failed_keys else None
    )
    return {
        "manifest": manifest_path,
        "progress": {
            "total": total,
            "done": counts["done"],
            "running": counts["running"],
            "todo": counts["todo"],
            "blocked": counts["blocked"],
            "failed": counts["failed"],
            "abandoned": counts["abandoned"],
            "converged": converged,
        },
        "nodes": nodes,
        "needs_decision": needs_decision,
    }


def build_manifest_status_report(
    manifest: Manifest,
    manifest_path: str,
) -> dict:
    """只从 manifest 构建状态快照，不调用 Engine、不 reconcile、不写文件。"""
    return _build_report_from_items(manifest, manifest_path, {})


def build_status_report(
    manifest: Manifest,
    store: WorkItemStore,
    manifest_path: str,
) -> dict:
    """reconcile + 快照 → 结构化报告 dict(schema 由 *_KEYS 常量锁定)。

    1. reconcile:平台真实状态同步回 manifest(写回文件)
    2. 复用同轮 control/evidence observation(pr_url / 证据摘要)
    3. 构建 progress / nodes / needs_decision，不再次读取平台
    """
    from .loop import reconcile_with_observations  # 延迟导入,避免循环依赖
    # ``dag status`` is the user-facing platform inspection command. It must
    # not inherit the normal tick/run active-set scope.
    result = reconcile_with_observations(
        store, manifest, manifest_path, full_scan=True)
    items, deferred_payloads = _observations_to_items_and_payloads(
        result.observations)

    return _build_report_from_items(
        manifest, manifest_path, items, deferred_payloads)


# ==================== table 渲染(给人看) ====================

def render_table(report: dict) -> str:
    """进度统计 + 节点表 → 纯文本(对标 §5.2 stdout 数据流)。"""
    lines: list[str] = []
    p = report["progress"]
    lines.append(
        f"Progress: {p['done']}/{p['total']} done"
        f"  (running {p['running']}, todo {p['todo']},"
        f" blocked {p['blocked']}, failed {p['failed']},"
        f" abandoned {p['abandoned']})"
    )
    lines.append("")

    headers = ("KEY", "STATUS", "WORKER", "REVIEWER", "WORK_ITEM_ID", "PR_URL")
    rows = []
    for n in report["nodes"]:
        rows.append((
            n["key"], n["status"], n["worker"] or "-",
            n["reviewer"] or "-", n["work_item_id"] or "-",
            n["pr_url"] or "-",
        ))

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines.append(fmt.format(*headers).rstrip())
    for row in rows:
        lines.append(fmt.format(*row).rstrip())

    nd = report["needs_decision"]
    if nd:
        lines.append("")
        lines.append("Needs decision:")
        for fn in nd["failed_nodes"]:
            lines.append(f"  [{fn['status']}] {fn['key']}: {fn['reason']}")
        if nd["blocked_downstream"]:
            lines.append(f"  Blocked downstream: {', '.join(nd['blocked_downstream'])}")
        lines.append("  Next actions:")
        for action in nd["next_actions"]:
            lines.append(f"    {action}")

    return "\n".join(lines) + "\n"

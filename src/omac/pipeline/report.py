"""pipeline/report — dag status / exit 20 共享的结构化报告(单一 schema 模块)。

设计文档 §5.2/§13.3:dag status --output json 与 dag run exit 20 报告共用同一 schema,
P5 web 与 agent 都消费它。schema 用本模块的 *_KEYS 常量锁定,测试断言字段不变。

退出码约定(§5.1):dag status 退出码恒为 0(观测,不是判定);
dag run/tick 在 needs_decision 非空时 exit 20,report 结构完全相同。
"""
from __future__ import annotations

from ..core.manifest import Manifest
from ..engines.store import WorkItemStore
from ..core import graph
from .loop import reconcile


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
    if status in ("in_progress", "in_review"):
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


def _fetch_items(store: WorkItemStore, manifest: Manifest) -> dict:
    """按 work_item_id 精准取回 WorkItem 缓存。查找失败 → None。"""
    cache: dict = {}
    for key, node in manifest.nodes.items():
        if not node.work_item_id:
            cache[key] = None
            continue
        try:
            cache[key] = store.get_work_item(node.work_item_id)
        except Exception:
            cache[key] = None
    return cache


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


def _build_failed_node(node, item) -> dict:
    pr_url = None
    reason = None
    evidence_summary = None

    if item is not None:
        if item.artifacts:
            pr_url = item.artifacts.get("pr_url")
        if item.review_verdict and item.review_verdict not in ("pass", "pass-with-nits"):
            reason = f"review rejected: {item.review_verdict}"
        elif item.status.value == "failed":
            reason = "worker failed"
        elif item.status.value == "blocked":
            reason = "blocked on platform"
        evidence_summary = {
            "review_verdict": item.review_verdict,
            "review_comment": item.review_comment,
            "has_verification": item.verification is not None,
        }

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


def _next_actions(failed_nodes: list, blocked_downstream: list,
                  manifest_path: str) -> list:
    """为每个失败节点给出可执行的下一步命令(§5.2:精确到完整命令行)。"""
    actions = []
    for fn in failed_nodes:
        key = fn["key"]
        actions.append(f"omac node retry {manifest_path} {key}")
        actions.append(f"omac node abandon {manifest_path} {key}")
    return actions


def build_status_report(
    manifest: Manifest,
    store: WorkItemStore,
    manifest_path: str,
) -> dict:
    """reconcile + 快照 → 结构化报告 dict(schema 由 *_KEYS 常量锁定)。

    1. reconcile:平台真实状态同步回 manifest(写回文件)
    2. 精准取回 work item 缓存(pr_url / 证据摘要)
    3. 构建 progress / nodes / needs_decision
    """
    reconcile(store, manifest, manifest_path)
    items = _fetch_items(store, manifest)

    total = len(manifest.nodes)
    counts = {k: 0 for k in ("done", "running", "todo", "blocked", "failed", "abandoned")}
    for key, node in manifest.nodes.items():
        counts[_classify(node.status)] += 1
    converged = total > 0 and counts["done"] + counts["abandoned"] == total

    nodes = [_node_row(manifest.nodes[key], items.get(key)) for key in manifest.nodes]

    failed_keys = {key for key, node in manifest.nodes.items()
                   if node.status in FAILED_STATUSES}
    if failed_keys:
        snapshot = _graph_snapshot(manifest)
        downstream = graph.downstream_of(snapshot, failed_keys)
        blocked_downstream = sorted(
            k for k in downstream
            if manifest.nodes[k].status not in TERMINAL_ALL
        )
        failed_nodes = [
            _build_failed_node(manifest.nodes[key], items.get(key))
            for key in sorted(failed_keys)
        ]
        needs_decision = {
            "failed_nodes": failed_nodes,
            "blocked_downstream": blocked_downstream,
            "next_actions": _next_actions(failed_nodes, blocked_downstream, manifest_path),
        }
    else:
        needs_decision = None

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

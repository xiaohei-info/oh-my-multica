"""Pure decisions of the CI gate between Worker evidence and review: facts in, a Plan of effects out.

No I/O and no platform access (enforced by tests/test_architecture.py). ``delivery.advance_delivery`` reads the pull
request URL and bounce count, runs the check command (a local command, so it stays in the shell), and applies the plan.
"""
from __future__ import annotations

from ..core.workitem import WorkItemStatus
from ..i18n import ui
from .decide_effects import AddComment, Assign, Plan, SetNode, UpdateMetadata, UpdateStatus, Wake


def plan_ci_start(*, node_key: str, item_id: str, pr_url: str) -> Plan:
    """No PR URL: block with a teaching comment. Otherwise enter `ci_check` (platform stays in_progress)."""
    if not pr_url:
        return Plan((
            AddComment(item_id, ui(
                "⚠️ CI is configured, but the worker did not submit pr_url, so the CI check cannot run.\n"
                "Resubmit with `omac work submit <id> --pr-url <url> --verification-file <ev.yaml>`.",
                "⚠️ CI 已配置(check_command)但 worker 未提交 pr_url,无法运行 CI 检查。\\n"
                "请用 `omac work submit <id> --pr-url <url> --verification-file <ev.yaml>` "
                "补交 PR 地址后重新提交。")),
            SetNode(node_key, {"status": "blocked"}),
            UpdateStatus(item_id, WorkItemStatus.BLOCKED),
        ), "blocked")
    return Plan((SetNode(node_key, {"status": "ci_check"}), UpdateStatus(item_id, WorkItemStatus.IN_PROGRESS)))


def plan_ci_result(
    *, node_key: str, item_id: str, worker: str, passed: bool, label: str, summary: str,
    bounces: int, limit: int,
) -> Plan:
    """Green: back to in_progress, continue. Red: comment, count, then block at the limit or return to the Worker."""
    back = (SetNode(node_key, {"status": "in_progress"}), UpdateStatus(item_id, WorkItemStatus.IN_PROGRESS))
    if passed:
        return Plan(back, "pass")
    next_bounce = bounces + 1
    head = (
        AddComment(item_id, ui(
            f"⚠️ {label}. Returning to the worker for repair and resubmission.\n\n"
            f"--- Command output tail ---\n{summary}",
            f"⚠️ {label} —— CI 未通过,转回 worker 修复后重新提交。\\n\\n"
            f"--- 命令输出尾部 ---\\n{summary}")),
        UpdateMetadata(item_id, {"ci_bounce": next_bounce}),
    )
    if limit == 0 or next_bounce >= limit:
        return Plan(head + (SetNode(node_key, {"status": "blocked"}),
                            UpdateStatus(item_id, WorkItemStatus.BLOCKED)), "blocked")
    return Plan(head + back + (Assign(item_id, worker, "worker"), Wake(item_id, worker, "worker")), "bounce")

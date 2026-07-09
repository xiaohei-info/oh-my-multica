"""人工决策 metadata 的小型结构化摘要。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..core.taskmeta import TaskPhase


def review_decision_required(
    *,
    kind: str,
    verdict: str,
    review_report: Optional[Dict[str, Any]],
    review_report_ref: Optional[Dict[str, Any]] = None,
    round_index: Optional[int] = None,
) -> Dict[str, Any]:
    """构造可放进平台 metadata 的人工决策摘要。

    完整 review_report 由 review_report_ref/附件承载;decision_required 只保留
    人工判断需要的短字段,避免把大报告复制进 metadata。
    """
    report = review_report if isinstance(review_report, dict) else {}
    blockers = report.get("blockers") if isinstance(report.get("blockers"), list) else []
    nits = report.get("nits") if isinstance(report.get("nits"), list) else []
    decision: Dict[str, Any] = {
        "kind": kind,
        "phase": TaskPhase.REVIEW.value,
        "verdict": verdict,
        "blocker_count": len(blockers),
        "nit_count": len(nits),
    }
    if round_index is not None:
        decision["round"] = round_index
    if review_report_ref:
        decision["review_report_ref"] = review_report_ref
    return decision

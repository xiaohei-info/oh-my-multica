"""Accepting a pass-with-nits verdict: the marker, the checks on an existing marker, and the verified write sequence.

Shared by `omac node accept-nits` (develop nodes) and `omac plan accept-nits` (plan stages). What differs per caller is
only which issue is eligible and how its stop is identified; the marker and its persistence are one mechanism.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, REVIEW_NITS_ACCEPTANCE_SCHEMA, TaskPhase, review_nits_acceptance_is_valid,
)
from ..core.workitem import WorkItem, WorkItemStatus
from ..errors import ValidationError
from ..i18n import ui
from .apply import Ports, apply, apply_one
from .decide_effects import UpdateMetadata, UpdateStatus
from .decide_runs import active_direct_runs

NITS_STOP_REASON = "review-nits-acceptance-required"


def decision_matches(decision: Any, item: WorkItem, *, node_id: str | None = None) -> bool:
    """Whether ``decision`` is the acceptance stop of exactly this verdict on this issue."""
    if not isinstance(decision, Mapping):
        return False
    return bool(
        decision.get("schema") == DECISION_REQUIRED_SCHEMA
        and decision.get("reason_code") == NITS_STOP_REASON
        and decision.get("kind") == item.kind.value
        and decision.get("phase") == TaskPhase.REVIEW.value
        and decision.get("gate") == "review-nits"
        and decision.get("resume_issue_id") == item.id
        and decision.get("node_id") == node_id
        and decision.get("review_subject_digest") == item.review_subject_digest
        and decision.get("review_report_ref") == item.review_report_ref
        and decision.get("verdict") == "pass-with-nits"
    )


def nits_marker(item: WorkItem, *, node_id: str | None = None, retry_command: str) -> dict:
    """The marker for the current sealed review; an existing marker or decision that is not this review is refused."""
    marker = {
        "schema": REVIEW_NITS_ACCEPTANCE_SCHEMA,
        "review_subject_digest": item.review_subject_digest,
        "review_report_ref": dict(item.review_report_ref),
        "verdict": "pass-with-nits",
    }
    existing = item.review_nits_acceptance
    if existing not in (None, {}) and (not review_nits_acceptance_is_valid(existing) or existing != marker):
        raise ValidationError(ui(
            "The existing review-nits acceptance marker does not match the current sealed review; "
            f"refusing to overwrite it. Run `{retry_command}` for a fresh decision.",
            "已有 review-nits acceptance marker 与当前 sealed review 不匹配；拒绝覆盖。请运行 "
            f"`{retry_command}` 重新决策。"))
    if item.decision_required not in (None, {}) and not decision_matches(item.decision_required, item, node_id=node_id):
        raise ValidationError(ui(
            "The existing decision_required does not identify this exact pass-with-nits review; "
            "no state was changed.",
            "现有 decision_required 没有绑定当前 pass-with-nits review；未修改状态。"))
    return marker


def persist_nits_acceptance(engine, item: WorkItem, marker: dict, *, rerun_command: str) -> None:
    """Persist the marker, clear decision_required, return to in_review; every write is read back."""
    def ensure_no_active_direct_run() -> None:
        active = [run.id for run in active_direct_runs(engine.runtime.list_runs(item.id))]
        if active:
            raise ValidationError(ui(
                f"An active direct Run still exists ({', '.join(active)}); wait for it to finish before "
                f"rerunning `{rerun_command}`.",
                f"仍有 active direct Run（{', '.join(active)}）；请等待其结束后重新运行 `{rerun_command}`。"))

    # Write the marker before clearing decision_required. Each write is read-after-write
    # verified, so a restart can safely resume this sequence.
    if item.review_nits_acceptance in (None, {}):
        ensure_no_active_direct_run()
        updated = apply_one(UpdateMetadata(item.id, {'review_nits_acceptance': marker}), Ports(store=engine.store))
        if updated.review_nits_acceptance != marker:
            raise ValidationError(ui(
                "The operator acceptance marker was not persisted; refusing to clear decision_required.",
                "operator acceptance marker 未成功持久化；拒绝清除 decision_required。"))
        item = updated

    if item.decision_required not in (None, {}):
        ensure_no_active_direct_run()
        updated = apply_one(UpdateMetadata(item.id, {'decision_required': {}}), Ports(store=engine.store))
        if updated.decision_required not in (None, {}):
            raise ValidationError(ui(
                "decision_required was not cleared; the WorkItem remains blocked.",
                "decision_required 未清除；WorkItem 仍保持 blocked。"))
        item = updated

    ensure_no_active_direct_run()
    if item.status != WorkItemStatus.IN_REVIEW:
        apply([UpdateStatus(item.id, WorkItemStatus.IN_REVIEW)], Ports(store=engine.store))
    final = engine.store.get_work_item(item.id)
    if not (
        final.status == WorkItemStatus.IN_REVIEW
        and final.phase == TaskPhase.REVIEW
        and final.review_verdict == "pass-with-nits"
        and final.review_nits_acceptance == marker
        and final.decision_required in (None, {})
    ):
        raise ValidationError(ui(
            "The WorkItem did not converge to review/in_review with its acceptance marker; "
            "no merge or Reviewer fact was assumed.",
            "WorkItem 未收敛到带 acceptance marker 的 review/in_review；未假设 merge 成功，Reviewer 事实未修改。"))

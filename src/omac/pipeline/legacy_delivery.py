"""Fail-closed upgrade of pre-delivery-identity Worker rework submissions."""

from __future__ import annotations

import hashlib

from ..core.review_convergence import review_subject_digest
from ..core.taskmeta import DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity, TaskPhase
from ..engines.models import WorkItemStatus
from ..errors import PlatformError
from .delivery_identity import (
    attachment_is_causal_for_run,
    attachment_time_matches_run,
    delivery_identity,
    observe_delivery_projection,
    parse_platform_time,
    validate_controller_sealed_delivery,
)

_ACTIVE_STAGES = {"in_progress", "ci_check", "in_review"}
_ITEM_STATES = {
    WorkItemStatus.DONE, WorkItemStatus.IN_PROGRESS, WorkItemStatus.IN_REVIEW,
}


def _fail(item, reason: str) -> None:
    raise PlatformError(
        f"Cannot safely upgrade legacy Worker delivery for work item {item.id}: "
        f"{reason}. Safe next action: keep the Runner stopped and explicitly "
        "rerun the Worker to submit fresh verification with the current OMAC "
        "protocol; do not reuse the current remote PR HEAD as submitted evidence.")


def _is_candidate(node, item) -> bool:
    has_delivery = any((
        isinstance(item.artifacts, dict) and item.artifacts,
        isinstance(item.verification, dict) and item.verification,
        isinstance(item.verification_ref, dict) and item.verification_ref,
    ))
    return bool(
        node.status in _ACTIVE_STAGES
        and item.status in _ITEM_STATES
        and item.phase == TaskPhase.AUTHORING
        and item.worker_handoff is None
        and delivery_identity(item) is None
        and item.bounces.review > 0
        and item.platform_assignee_id is None
        and not item.agent_run_finished_without_submit
        and has_delivery
    )


def _is_sealed(item) -> bool:
    identity = delivery_identity(item)
    return bool(
        identity
        and (identity.handoff_generation or "").startswith("legacy-")
        and item.worker_handoff is None
        and item.status in _ITEM_STATES
        and item.phase == TaskPhase.AUTHORING
        and item.platform_assignee_id is None
    )


def _proof(store, runtime, node, item):
    if not runtime.capabilities.stable_direct_run_identity:
        _fail(item, "the Runtime does not expose stable direct Run identities")
    review_values = (
        item.review_verdict, item.review_comment, item.machine_feedback,
        item.machine_feedback_ref, item.review_report, item.review_report_ref,
        item.review_subject_digest, item.review_continuation,
        item.decision_required,
    )
    if any(value not in (None, "", {}) for value in review_values):
        _fail(item, "the current review projection is inconsistent")

    ledger = item.review_ledger if isinstance(item.review_ledger, dict) else {}
    cycles = ledger.get("cycles") if isinstance(ledger.get("cycles"), list) else []
    cycle = cycles[-1] if cycles and isinstance(cycles[-1], dict) else {}
    round_index = cycle.get("round")
    subject = cycle.get("subject_digest")
    if not (
        type(round_index) is int
        and round_index == item.bounces.review
        and cycle.get("verdict") in {"reject", "pass-with-nits"}
        and isinstance(subject, str) and subject
    ):
        _fail(item, "the latest reject/rework review ledger cycle is inconsistent")
    if review_subject_digest(item, round_index) == subject:
        _fail(item, "the candidate delivery is identical to the latest rejected subject")

    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    if not artifacts.get("head_sha"):
        _fail(item, "immutable submitted PR head evidence is missing")
    if not isinstance(item.verification, dict) or not item.verification_ref:
        _fail(item, "verification evidence is incomplete")
    attachment, pr_url, submitted_head = observe_delivery_projection(store, item)

    worker_id = store.resolve_agent_id(node.worker)
    reviewer_id = store.resolve_agent_id(node.reviewer)
    if attachment.uploader_type != "agent" or attachment.uploader_id != worker_id:
        _fail(item, "the verification attachment uploader does not match the Worker")
    runs = runtime.list_runs(item.id)
    if any(run.active for run in runs):
        _fail(item, "an Agent Run is still active")
    reviewer_runs = [
        run for run in runs
        if run.kind == "direct" and run.status == "completed"
        and run.agent_id == reviewer_id and parse_platform_time(run.updated_at)
    ]
    if not reviewer_runs:
        _fail(item, "no completed Reviewer Run proves the latest rework handoff")
    reviewer_ended = max(
        parse_platform_time(run.updated_at) for run in reviewer_runs)
    worker_runs = [
        run for run in runs
        if run.kind == "direct" and run.status == "completed"
        and run.agent_id == worker_id
        and attachment_is_causal_for_run(attachment, run, worker_id)
        and attachment_time_matches_run(attachment, run)
        and parse_platform_time(run.created_at)
        and reviewer_ended < parse_platform_time(run.created_at)
    ]
    if len(worker_runs) != 1:
        _fail(item, f"expected one post-review causal Worker Run, observed {len(worker_runs)}")
    target = worker_runs[0]
    generation = "\n".join((
        str(item.id), target.id, attachment.attachment_id,
        attachment.sha256, submitted_head,
    ))
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation="legacy-" + hashlib.sha256(
            generation.encode("utf-8")).hexdigest(),
        worker=node.worker,
        agent_id=worker_id,
        run_id=target.id,
        pr_url=pr_url,
        pr_head_sha=submitted_head,
        verification_sha256=attachment.sha256,
        verification_attachment_id=attachment.attachment_id,
        verification_comment_id=attachment.comment_id,
        verification_uploader_id=attachment.uploader_id,
        verification_uploader_type=attachment.uploader_type,
        verification_task_id=attachment.task_id,
        verification_created_at=attachment.created_at,
    )


def _seal(store, runtime, node, item):
    sealed = _proof(store, runtime, node, item)
    store.update_work_item_metadata(item.id, delivery_identity=sealed)
    persisted = store.get_work_item(item.id)
    identity = delivery_identity(persisted)
    if identity is None or identity.as_dict() != sealed.as_dict():
        _fail(item, "the Controller-sealed identity did not persist")
    validate_controller_sealed_delivery(store, persisted)
    if persisted.status != WorkItemStatus.DONE:
        store.update_status(item.id, WorkItemStatus.DONE)
        persisted = store.get_work_item(item.id)
    return persisted


def normalize_legacy_completed_delivery(store, runtime, node, item):
    """Return normalized item, or None when the item is not legacy data."""
    if _is_sealed(item):
        validate_controller_sealed_delivery(store, item)
        if item.status != WorkItemStatus.DONE:
            store.update_status(item.id, WorkItemStatus.DONE)
            return store.get_work_item(item.id)
        return item
    return _seal(store, runtime, node, item) if _is_candidate(node, item) else None

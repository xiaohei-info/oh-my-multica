"""Controller-sealed Worker delivery identity proofs."""

from __future__ import annotations

from datetime import datetime

import yaml

from ..core.taskmeta import (
    DELIVERY_IDENTITY_SCHEMA,
    DeliveryIdentity,
    WorkerHandoffIntent,
    parse_delivery_identity,
)
from ..engines.models import PullRequestReadiness, PullRequestReadinessFailure
from ..engines.store import WorkItemStore
from ..errors import PlatformError


def delivery_identity(item) -> DeliveryIdentity | None:
    return parse_delivery_identity(getattr(item, "delivery_identity", None))


def parse_platform_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def attachment_is_causal_for_run(observation, run, agent_id: str) -> bool:
    if observation.task_id:
        return bool(
            observation.task_id == run.id
            and (
                not observation.uploader_id
                or observation.uploader_id == agent_id
            )
        )
    created = parse_platform_time(observation.created_at)
    run_started = parse_platform_time(run.created_at)
    run_ended = parse_platform_time(run.updated_at)
    return bool(
        observation.uploader_type == "agent"
        and observation.uploader_id == agent_id
        and created is not None
        and run_started is not None
        and run_ended is not None
        and run_started <= created <= run_ended
    )


def attachment_time_matches_run(observation, run) -> bool:
    """Run 暴露时间边界时，附件创建时间必须落在该执行窗口内。"""
    if not run.created_at and not run.updated_at:
        return True
    created = parse_platform_time(observation.created_at)
    run_started = parse_platform_time(run.created_at)
    run_ended = parse_platform_time(run.updated_at)
    return bool(
        created is not None
        and run_started is not None
        and run_ended is not None
        and run_started <= created <= run_ended
    )


def observe_delivery_projection(store: WorkItemStore, item):
    """读取附件实字节和远端 PR，并验证提交时 HEAD 未漂移。"""
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    verification_ref = (
        item.verification_ref if isinstance(item.verification_ref, dict) else {}
    )
    pr_url = str(artifacts.get("pr_url") or artifacts.get("pr") or "").strip()
    submitted_head = str(artifacts.get("head_sha") or "").strip()
    if not pr_url or not submitted_head or not verification_ref:
        raise PlatformError(f"Worker delivery is incomplete for work item {item.id}")

    attachment = store.observe_verification_attachment(item.id, verification_ref)
    try:
        attachment_verification = yaml.safe_load(
            attachment.content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PlatformError(
            f"Verification attachment cannot be parsed for work item {item.id}") from exc
    if attachment_verification != item.verification:
        raise PlatformError(
            f"Parsed verification projection does not match downloaded attachment "
            f"for work item {item.id}")

    readiness = store.read_pull_request_readiness(pr_url)
    if isinstance(readiness, PullRequestReadinessFailure):
        raise PlatformError(
            f"Remote PR HEAD observation failed for {pr_url}: {readiness.detail}")
    if not isinstance(readiness, PullRequestReadiness) or not readiness.head_sha:
        raise PlatformError(f"Remote PR HEAD is unavailable for {pr_url}")
    if readiness.head_sha != submitted_head:
        raise PlatformError(
            f"Remote PR HEAD changed after Worker submit for {pr_url}: "
            f"submitted={submitted_head}, current={readiness.head_sha}")
    return attachment, pr_url, readiness.head_sha


def seal_worker_delivery(
    store: WorkItemStore,
    current,
    intent: WorkerHandoffIntent,
    target_run,
) -> DeliveryIdentity:
    attachment, pr_url, remote_head = observe_delivery_projection(store, current)
    if (
        intent.baseline_verification_attachment_id
        and attachment.attachment_id
        == intent.baseline_verification_attachment_id
    ):
        raise PlatformError(
            f"Worker handoff {intent.generation} did not create a new verification attachment")
    if not attachment_is_causal_for_run(
        attachment, target_run, str(intent.target_agent_id),
    ):
        raise PlatformError(
            f"Verification attachment is not causally bound to Worker Run "
            f"{target_run.id}")
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation=intent.generation,
        worker=intent.target_worker,
        agent_id=intent.target_agent_id,
        run_id=target_run.id,
        pr_url=pr_url,
        pr_head_sha=remote_head,
        verification_sha256=attachment.sha256,
        verification_attachment_id=attachment.attachment_id,
        verification_comment_id=attachment.comment_id,
        verification_uploader_id=attachment.uploader_id,
        verification_uploader_type=attachment.uploader_type,
        verification_task_id=attachment.task_id,
        verification_created_at=attachment.created_at,
    )


def validate_controller_sealed_delivery(
    store: WorkItemStore, item,
) -> None:
    identity = delivery_identity(item)
    if identity is None:
        return
    if not identity.is_complete():
        raise PlatformError(
            f"Controller-sealed delivery identity is incomplete for work item {item.id}")
    attachment, pr_url, remote_head = observe_delivery_projection(store, item)
    if (
        identity.pr_url != pr_url
        or identity.pr_head_sha != remote_head
        or identity.verification_attachment_id != attachment.attachment_id
        or identity.verification_comment_id != attachment.comment_id
    ):
        raise PlatformError(
            f"Current delivery projection does not match sealed identity for "
            f"work item {item.id}")
    if (
        attachment.sha256 != identity.verification_sha256
        or attachment.attachment_id != identity.verification_attachment_id
        or attachment.comment_id != identity.verification_comment_id
        or attachment.uploader_id != identity.verification_uploader_id
        or attachment.uploader_type != identity.verification_uploader_type
        or attachment.task_id != identity.verification_task_id
        or attachment.created_at != identity.verification_created_at
    ):
        raise PlatformError(
            f"Verification attachment no longer matches sealed identity for "
            f"work item {item.id}")

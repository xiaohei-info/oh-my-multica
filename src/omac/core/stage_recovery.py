"""共享的 DAG 阶段恢复准备与 restart-safe 观察规则。

这里只准备 review/authoring 的 Store 状态；merging 仅校验前置并返回委托标记，
真正的 PR 请求和远端观察始终由 pipeline.delivery.run_merge_delivery 负责。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime

from .manifest import _dump_contract
from .taskmeta import TaskPhase, review_context_binding, WorkerHandoffIntent
from ..engines.models import WorkItemStatus
from ..errors import PlatformError


def _stable_digest(value) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recovery_control_snapshot(item) -> dict:
    """阶段恢复 ledger 使用的稳定 Store 控制面快照。"""
    contract = getattr(item, "contract", None)
    if isinstance(contract, dict):
        contract_value = contract
    elif contract is None:
        contract_value = None
    else:
        contract_value = _dump_contract(contract)
    status = getattr(item, "status", None)
    phase = getattr(item, "phase", None)
    return {
        "status": getattr(status, "value", status),
        "phase": getattr(phase, "value", phase),
        "review_verdict": getattr(item, "review_verdict", None),
        "review_subject_digest": getattr(item, "review_subject_digest", None),
        "review_generation": getattr(item, "review_generation", None),
        "review_ledger_generation": getattr(
            item, "review_ledger_generation", None),
        "review_ledger_current": (
            getattr(item, "current_review_ledger", None) is not None),
        "bounce_baseline": getattr(item, "bounce_baseline", None),
        "decision_required_pending": bool(
            getattr(item, "decision_required", None)),
        "review_report_pending": bool(
            getattr(item, "review_report", None)
            or getattr(item, "review_report_ref", None)),
        "review_continuation_pending": bool(
            getattr(item, "review_continuation", None)),
        "reviewer_run_baseline_pending": (
            getattr(item, "reviewer_run_baseline", None) is not None),
        "delivery_identity_pending": (
            getattr(item, "delivery_identity", None) is not None),
        "contract_sha256": _stable_digest(contract_value),
        "worker_handoff_pending": (
            getattr(item, "worker_handoff", None) is not None),
    }


def recovery_evidence_digest(item) -> str:
    """绑定不可由阶段恢复重写的既有代码交付证据。"""
    delivery_identity = getattr(item, "delivery_identity", None)
    return _stable_digest({
        "artifacts": getattr(item, "artifacts", None),
        "verification": getattr(item, "verification", None),
        "delivery_identity": (
            delivery_identity.as_dict()
            if hasattr(delivery_identity, "as_dict")
            else delivery_identity
        ),
    })


def stage_recovery_subject(node, item) -> str:
    """把 contract 与既有 worker 交付绑定为 review 恢复对象。"""
    return _stable_digest({
        "contract": _dump_contract(node.contract) if node.contract else None,
        "evidence": recovery_evidence_digest(item),
    })


def validate_stage_recovery(item, stage: str) -> None:
    """验证阶段恢复前置；merging 只验证，不在此观察或发起 merge。"""
    if stage not in {"review", "authoring", "merging"}:
        raise ValueError(f"unknown recovery stage: {stage}")
    identity = getattr(item, "delivery_identity", None)
    if stage == "review" and identity is None:
        raise ValueError(
            "review recovery requires a valid controller-sealed delivery identity; "
            "use omac node retry <manifest> <node> --stage authoring for a fresh submission")
    if stage in {"review", "merging"} and identity is not None:
        artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
        verification_ref = (
            item.verification_ref
            if isinstance(item.verification_ref, dict) else {}
        )
        if not identity.is_complete() or (
            identity.pr_url != (artifacts.get("pr_url") or artifacts.get("pr"))
            or identity.pr_head_sha != artifacts.get("head_sha")
            or identity.verification_attachment_id
            != verification_ref.get("attachment_id")
            or identity.verification_comment_id
            != verification_ref.get("comment_id")
        ):
            raise ValueError(
                f"{stage} recovery requires a valid controller-sealed delivery identity")
    if stage == "review":
        try:
            cutoff = datetime.fromisoformat(identity.verification_created_at.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            cutoff = None
        if cutoff is None or cutoff.tzinfo is None:
            raise ValueError(
                "review recovery requires a timezone-aware sealed verification time; "
                "use omac node retry <manifest> <node> --stage authoring for a fresh submission")
    if stage != "merging":
        return
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    if item.review_verdict not in {"pass", "pass-with-nits"} or not (
        artifacts.get("pr_url") or artifacts.get("pr")
    ):
        raise ValueError("merge-only recovery requires a passed review and PR")


def _authoring_generation(node, item) -> str:
    return "authoring-" + _stable_digest({
        "node": node.id,
        "contract": _dump_contract(node.contract) if node.contract else None,
        "evidence": recovery_evidence_digest(item),
    })[:24]


def _authoring_retry_source(item) -> dict:
    # Only freshly observed control facts; never a reconstructed historical seal.
    return {
        "binding": review_context_binding(item),
        "status": item.status.value,
        "phase": item.phase.value,
        "assignee": item.platform_assignee_id,
        "delivery": _stable_digest({"artifacts": item.artifacts,
                                    "verification_ref": item.verification_ref}),
        "counters": asdict(item.bounces),
        "identity": _stable_digest(item.delivery_identity.as_dict())
        if item.delivery_identity is not None else None,
        "subject": item.review_subject_digest,
        "verdict": item.review_verdict,
        "ledger_generation": item.review_ledger_generation,
        "report_ref": item.review_report_ref,
        "ledger_ref": item.review_ledger_ref,
    }


def _prepare_authoring_retry(node, store, item, intent):
    """Persist a recovery intent before the first destructive stage write."""
    def observe_write(write, expected):
        try:
            write()
        except PlatformError:
            # A timeout is not permission to replay. Read the durable result first.
            observed = store.get_work_item(node.work_item_id)
            if not expected(observed):
                raise
            return observed
        observed = store.get_work_item(node.work_item_id)
        if not expected(observed):
            raise PlatformError("Authoring recovery write did not converge; repeat node retry after inspecting work show")
        return observed

    node_binding = review_context_binding(replace(item, contract=node.contract))
    if intent.authoring_recovery is None:
        if (intent.is_causally_bound()
                and item.phase == TaskPhase.AUTHORING and item.status == WorkItemStatus.TODO
                and intent == item.worker_handoff and intent.target_run_id is None
                and intent.target_worker == node.worker
                and not item.review_report_ref and not item.review_subject_digest
                and not item.review_verdict and not item.delivery_identity
                and intent.review_context_binding == review_context_binding(item)
                and node_binding == review_context_binding(item)):
            return "todo"
        feedback = intent.source_review_feedback or {}
        if (not intent.is_causally_bound()
                or node_binding != review_context_binding(item)
                or intent.review_context_binding != review_context_binding(item)
                or intent.target_worker != node.worker
                or intent.target_worker_bounce != item.bounces.worker
                or ((item.artifacts or {}).get("head_sha") and intent.baseline_pr_head_sha != item.artifacts["head_sha"])
                or intent.baseline_verification_attachment_id != (item.verification_ref or {}).get("attachment_id")
                or (item.review_subject_digest and item.review_subject_digest != intent.source_review_subject_digest)
                or (item.review_verdict and item.review_verdict != intent.source_review_verdict)
                or any(getattr(item, field) and getattr(item, field) != feedback.get(key)
                       for field, key in (("review_report_ref", "report_ref"), ("review_ledger_ref", "ledger_ref")))):
            raise PlatformError("Authoring retry source changed before intent publication; inspect work show before retry")
        plan = {"generation": _authoring_generation(node, item),
                "source": _authoring_retry_source(item)}
        intent = replace(intent, state="recovering", authoring_recovery=plan)
        item = observe_write(
            lambda: store.update_work_item_metadata(node.work_item_id, worker_handoff=intent),
            lambda current: current.worker_handoff == intent)
    plan = intent.authoring_recovery
    if (intent.state != "recovering"
            or not replace(intent, state="pending", authoring_recovery=None).is_causally_bound()
            or not isinstance(plan, dict) or set(plan) != {"generation", "source"}):
        raise PlatformError("Invalid authoring recovery intent; inspect work show before retry")
    generation, source = plan["generation"], plan["source"]
    current = _authoring_retry_source(item)
    if (not isinstance(generation, str) or not generation or not isinstance(source, dict)
            or set(source) != set(current)
            or not isinstance(source["binding"], dict)
            or set(source["binding"]) != {"generation", "contract_sha256"}
            or not (source["binding"]["generation"] is None or isinstance(source["binding"]["generation"], str))
            or intent.review_context_binding != source["binding"]
            or item.worker_handoff != intent
            or current["status"] not in (source["status"], WorkItemStatus.TODO.value)
            or current["phase"] not in (source["phase"], TaskPhase.AUTHORING.value)
            or current["assignee"] not in (None, source["assignee"])
            or node.worker != intent.target_worker
            or review_context_binding(replace(item, contract=node.contract))["contract_sha256"] != source["binding"]["contract_sha256"]
            or current["binding"]["contract_sha256"] != source["binding"]["contract_sha256"]
            or current["binding"]["generation"] not in {source["binding"]["generation"], generation}
            or any(current[k] != source[k] for k in ("delivery", "counters", "ledger_ref", "ledger_generation"))
            or any(current[k] not in (None, source[k]) for k in ("identity", "subject", "report_ref", "verdict"))):
        raise PlatformError("Authoring recovery source changed; inspect work show before retry")

    def reached(current):
        return (current.worker_handoff == intent
                and current.phase == TaskPhase.AUTHORING
                and current.status == WorkItemStatus.TODO
                and current.review_generation == generation
                and not current.review_verdict and not current.review_report
                and not current.review_report_ref and not current.review_subject_digest
                and not current.delivery_identity and not current.decision_required
                and not current.review_obligations and not current.review_continuation
                and not current.reviewer_run_baseline and not current.machine_feedback
                and current.bounce_baseline is None
                and current.platform_assignee_id is None
                and review_context_binding(current)["contract_sha256"] == source["binding"]["contract_sha256"]
                and _authoring_retry_source(current)["delivery"] == source["delivery"]
                and asdict(current.bounces) == source["counters"])

    if not reached(item):
        item = observe_write(
            lambda: store.restore_authoring_generation(
                node.work_item_id, node.contract, generation, worker_handoff=intent),
            reached)
    completed = replace(intent, state="pending", authoring_recovery=None,
                        review_context_binding=review_context_binding(item))
    observe_write(
        lambda: store.update_work_item_metadata(node.work_item_id, worker_handoff=completed),
        lambda current: current.worker_handoff == completed and current.review_generation == generation)
    return "todo"


def prepare_stage_recovery(
    node,
    store,
    stage: str,
    *,
    expected_review_subject: str | None = None,
    expected_review_generation: str | None = None,
    expected_bounce_baseline: dict[str, int] | None = None,
    sync_contract: bool = False,
    worker_handoff: WorkerHandoffIntent | None = None,
) -> str:
    """共享的 review/authoring 阶段准备；merge 交给 run_merge_delivery。

    本函数不派发 Agent，也不执行/观察 merge。调用者先持久化 manifest 意图，
    然后用自己的 restart-safe ledger 调用本原语；后续 dag run 负责真正流转。
    """
    if not node.work_item_id:
        return "no-work-item"
    item = store.get_work_item(node.work_item_id)
    validate_stage_recovery(item, stage)
    if stage == "authoring":
        if worker_handoff is not None:
            return _prepare_authoring_retry(node, store, item, worker_handoff)
        generation = expected_review_generation or _authoring_generation(node, item)
        store.restore_authoring_generation(
            node.work_item_id, node.contract, generation,
            expected_bounce_baseline)
        return "todo"
    # 显式 stage recovery 开启新的执行世代。旧 review→worker handoff 只属于
    # 被 operator/amendment 取代的阶段，必须在任何可被 apply ledger 判定为
    # reached 的 contract/phase/status 写入前先退役。clear 是幂等 metadata
    # 写；若响应未知，重放 prepare_stage_recovery 仍会安全地再次清除。
    if item.worker_handoff is not None:
        store.update_work_item_metadata(
            node.work_item_id, worker_handoff={})
    if sync_contract and node.contract is not None:
        store.set_node_contract(node.work_item_id, node.contract)
    if stage == "merging":
        return "delegated-to-run-merge-delivery"
    store.clear_assignment(node.work_item_id)
    store.reset_review(node.work_item_id)
    if stage == "review":
        subject = expected_review_subject or stage_recovery_subject(
            node, store.get_work_item(node.work_item_id))
        store.prepare_review_cycle(node.work_item_id, subject)
        store.update_work_item_metadata(
            node.work_item_id, phase=TaskPhase.REVIEW)
        store.update_status(node.work_item_id, WorkItemStatus.IN_REVIEW)
        return "in_review"
    raise AssertionError(f"unhandled recovery stage: {stage}")


def classify_stage_recovery_observation(
    stage: str,
    baseline: dict,
    current: dict,
    *,
    expected_contract_sha256: str,
    expected_review_subject: str | None = None,
    expected_review_generation: str | None = None,
    expected_bounce_baseline: dict[str, int] | None = None,
) -> str:
    """返回 reached/safe/progressed，供 restart-safe 补偿决定是否写 Store。"""
    contract_matches = current.get("contract_sha256") == expected_contract_sha256
    recovery_independent = {
        key: value for key, value in current.items()
        if key not in {"contract_sha256", "worker_handoff_pending"}
    }
    baseline_recovery_independent = {
        key: value for key, value in baseline.items()
        if key not in {"contract_sha256", "worker_handoff_pending"}
    }
    handoff_retired = not bool(current.get("worker_handoff_pending", False))
    merging_target = (
        contract_matches
        and recovery_independent == baseline_recovery_independent
    )
    if stage == "merging" and merging_target:
        return "reached" if handoff_retired else "safe"
    review_target = (
        contract_matches
        and current.get("status") == WorkItemStatus.IN_REVIEW.value
        and current.get("phase") == TaskPhase.REVIEW.value
        and current.get("review_subject_digest") == expected_review_subject
    )
    if stage == "review" and review_target:
        return "reached" if handoff_retired else "safe"
    authoring_target = (
        contract_matches
        and current.get("status") == WorkItemStatus.TODO.value
        and current.get("phase") == TaskPhase.AUTHORING.value
        and current.get("review_generation") == expected_review_generation
        and current.get("bounce_baseline") == expected_bounce_baseline
        and current.get("review_ledger_current") is False
        and current.get("review_verdict") in {None, ""}
        and current.get("review_subject_digest") in {None, ""}
        and not current.get("decision_required_pending")
        and not current.get("review_report_pending")
        and not current.get("review_continuation_pending")
        and not current.get("reviewer_run_baseline_pending")
        and not current.get("delivery_identity_pending")
    )
    if stage == "authoring" and authoring_target:
        return "reached" if handoff_retired else "safe"
    if current == baseline:
        return "safe"
    if recovery_independent == baseline_recovery_independent:
        return "safe"
    if stage == "review" and (
        current.get("status") == baseline.get("status")
        and current.get("review_verdict") in {None, ""}
        and current.get("phase") in {
            TaskPhase.AUTHORING.value, TaskPhase.REVIEW.value,
        }
        and current.get("review_subject_digest") in {
            None, "", expected_review_subject,
        }
    ):
        return "safe"
    if stage == "authoring" and (
        current.get("status") == baseline.get("status")
        and current.get("phase") == TaskPhase.AUTHORING.value
        and current.get("review_verdict") in {None, ""}
    ):
        return "safe"
    return "progressed"

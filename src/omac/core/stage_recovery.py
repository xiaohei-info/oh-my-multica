"""共享的 DAG 阶段恢复准备与 restart-safe 观察规则。

这里只准备 review/authoring 的 Store 状态；merging 仅校验前置并返回委托标记，
真正的 PR 请求和远端观察始终由 pipeline.delivery.run_merge_delivery 负责。
"""
from __future__ import annotations

import hashlib
import json
import yaml

from .manifest import _dump_contract
from .taskmeta import TaskPhase
from ..engines.models import (
    WorkItemControlProjection, WorkItemPayload, WorkItemStatus,
)


def _stable_digest(value) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _work_item(observation):
    return (
        observation.work_item
        if isinstance(observation, WorkItemControlProjection)
        else observation
    )


def _payload_present(observation, payload: WorkItemPayload, ref_name: str) -> bool:
    item = _work_item(observation)
    if isinstance(observation, WorkItemControlProjection):
        if payload in observation.deferred_payloads:
            return True
    return bool(
        getattr(item, payload.value, None)
        or getattr(item, ref_name, None)
    )


def _contract_ref_digest(contract) -> str:
    payload = _dump_contract(contract) if contract is not None else None
    source = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _identity_value(value):
    return value.as_dict() if hasattr(value, "as_dict") else value


def _opaque_payload_identity(observation, payload: WorkItemPayload, ref_name: str):
    """Bind an attachment-backed fact without parsing its historical body."""
    item = _work_item(observation)
    ref = getattr(item, ref_name, None)
    body = getattr(item, payload.value, None)
    if ref is not None:
        return _stable_digest({"kind": "ref", "value": ref})
    if body is not None:
        return _stable_digest({"kind": "body", "value": body})
    return None


def _absolute_bounces(item) -> dict[str, int]:
    bounces = getattr(item, "bounces", None)
    return {
        stage: int(getattr(bounces, stage, 0))
        for stage in ("worker", "review", "merge")
    }


def recovery_control_snapshot(observation) -> dict:
    """唯一的阶段恢复控制投影；附件正文可延迟但存在性不可丢失。"""
    item = _work_item(observation)
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
        "review_comment_pending": bool(getattr(item, "review_comment", None)),
        "machine_feedback_pending": _payload_present(
            observation, WorkItemPayload.MACHINE_FEEDBACK,
            "machine_feedback_ref"),
        "review_subject_digest": getattr(item, "review_subject_digest", None),
        "review_obligations_pending": _payload_present(
            observation, WorkItemPayload.REVIEW_OBLIGATIONS,
            "review_obligations_ref"),
        "review_generation": getattr(item, "review_generation", None),
        "review_ledger_generation": getattr(
            item, "review_ledger_generation", None),
        "review_ledger_present": _payload_present(
            observation, WorkItemPayload.REVIEW_LEDGER, "review_ledger_ref"),
        "review_ledger_identity_sha256": _opaque_payload_identity(
            observation, WorkItemPayload.REVIEW_LEDGER, "review_ledger_ref"),
        "review_ledger_current": _review_ledger_is_current(observation),
        "bounce_baseline": getattr(item, "bounce_baseline", None),
        "absolute_bounces": _absolute_bounces(item),
        "decision_required_pending": bool(
            getattr(item, "decision_required", None)),
        "review_report_pending": _payload_present(
            observation, WorkItemPayload.REVIEW_REPORT, "review_report_ref"),
        "review_continuation_pending": bool(
            getattr(item, "review_continuation", None)),
        "reviewer_run_baseline_pending": (
            getattr(item, "reviewer_run_baseline", None) is not None),
        "delivery_identity_pending": (
            getattr(item, "delivery_identity", None) is not None),
        "delivery_identity_sha256": (
            _stable_digest(_identity_value(getattr(item, "delivery_identity", None)))
            if getattr(item, "delivery_identity", None) is not None
            else None
        ),
        "contract_sha256": _stable_digest(contract_value),
        "contract_ref_sha256": (
            (getattr(item, "contract_ref", None) or {}).get("sha256")
        ),
        "worker_handoff_pending": (
            getattr(item, "worker_handoff", None) is not None),
        "assignment_pending": bool(
            getattr(item, "platform_assignee_id", None)),
    }


def _review_ledger_is_current(observation) -> bool:
    item = _work_item(observation)
    ledger_present = _payload_present(
        observation, WorkItemPayload.REVIEW_LEDGER, "review_ledger_ref")
    current = getattr(item, "review_generation", None)
    ledger_generation = getattr(item, "review_ledger_generation", None)
    if current in {None, ""} and ledger_generation in {None, ""}:
        return ledger_present
    return bool(ledger_present and current == ledger_generation)


def observe_recovery_control(store, item_id: str):
    """Read recovery control facts without hydrating historical attachments."""
    observe = getattr(store, "observe_work_item_control", None)
    if observe is None:
        return store.get_work_item(item_id)
    return observe(item_id)


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
    if stage != "merging":
        return
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    if item.review_verdict not in {"pass", "pass-with-nits"} or not (
        artifacts.get("pr_url") or artifacts.get("pr")
    ):
        raise ValueError("merge-only recovery requires a passed review and PR")


def prepare_stage_recovery(
    node,
    store,
    stage: str,
    *,
    expected_review_subject: str | None = None,
    expected_review_generation: str | None = None,
    expected_bounce_baseline: dict[str, int] | None = None,
    sync_contract: bool = False,
) -> str:
    """共享的 review/authoring 阶段准备；merge 交给 run_merge_delivery。

    本函数不派发 Agent，也不执行/观察 merge。调用者先持久化 manifest 意图，
    然后用自己的 restart-safe ledger 调用本原语；后续 dag run 负责真正流转。
    """
    if not node.work_item_id:
        return "no-work-item"
    item = _work_item(observe_recovery_control(store, node.work_item_id))
    validate_stage_recovery(item, stage)
    if stage == "authoring":
        generation = expected_review_generation or (
            "authoring-" + _stable_digest({
                "node": node.id,
                "contract": _dump_contract(node.contract) if node.contract else None,
                "evidence": recovery_evidence_digest(item),
            })[:24]
        )
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
    store.reset_review(node.work_item_id, retire_current=True)
    if stage == "review":
        subject = expected_review_subject or stage_recovery_subject(
            node, store.get_work_item(node.work_item_id))
        generation = expected_review_generation or (
            "review-" + _stable_digest({
                "node": node.id,
                "subject": subject,
            })[:24]
        )
        store.prepare_review_cycle(node.work_item_id, subject)
        store.update_work_item_metadata(
            node.work_item_id,
            phase=TaskPhase.REVIEW,
            review_generation=generation,
        )
        store.update_status(node.work_item_id, WorkItemStatus.IN_REVIEW)
        return "in_review"
    raise AssertionError(f"unhandled recovery stage: {stage}")


def classify_stage_recovery_observation(
    stage: str,
    baseline: dict,
    current: dict,
    *,
    expected_contract_sha256: str,
    expected_contract_ref_sha256: str | None = None,
    expected_review_subject: str | None = None,
    expected_review_generation: str | None = None,
    expected_bounce_baseline: dict[str, int] | None = None,
) -> str:
    """返回 reached/safe/progressed，供 restart-safe 补偿决定是否写 Store。"""
    preservation_matches = _stage_preservation_matches(
        stage,
        baseline,
        current,
        expected_bounce_baseline=expected_bounce_baseline,
    )
    contract_matches = (
        current.get("contract_sha256") == expected_contract_sha256
        or (
            expected_contract_ref_sha256 is not None
            and current.get("contract_ref_sha256")
            == expected_contract_ref_sha256
        )
    )
    mutable_recovery_keys = {
        "contract_sha256", "contract_ref_sha256",
        "worker_handoff_pending", "assignment_pending",
    }
    recovery_independent = {
        key: value for key, value in current.items()
        if key not in mutable_recovery_keys
    }
    baseline_recovery_independent = {
        key: value for key, value in baseline.items()
        if key not in mutable_recovery_keys
    }
    handoff_retired = not bool(current.get("worker_handoff_pending", False))
    assignment_retired = not bool(current.get("assignment_pending", False))
    merging_target = (
        contract_matches
        and preservation_matches
        and recovery_independent == baseline_recovery_independent
    )
    if stage == "merging" and merging_target:
        return "reached" if handoff_retired and assignment_retired else "safe"
    review_target = (
        contract_matches
        and preservation_matches
        and current.get("status") == WorkItemStatus.IN_REVIEW.value
        and current.get("phase") == TaskPhase.REVIEW.value
        and current.get("review_subject_digest") == expected_review_subject
        and current.get("review_generation") == expected_review_generation
        and current.get("review_ledger_current") is False
        and current.get("review_verdict") in {None, ""}
        and not current.get("review_comment_pending")
        and not current.get("machine_feedback_pending")
        and not current.get("review_obligations_pending")
        and not current.get("decision_required_pending")
        and not current.get("review_report_pending")
        and not current.get("review_continuation_pending")
        and not current.get("reviewer_run_baseline_pending")
    )
    if stage == "review" and review_target:
        return "reached" if handoff_retired and assignment_retired else "safe"
    authoring_target = (
        contract_matches
        and preservation_matches
        and current.get("status") == WorkItemStatus.TODO.value
        and current.get("phase") == TaskPhase.AUTHORING.value
        and current.get("review_generation") == expected_review_generation
        and current.get("bounce_baseline") == expected_bounce_baseline
        and current.get("review_ledger_current") is False
        and current.get("review_verdict") in {None, ""}
        and not current.get("review_comment_pending")
        and not current.get("machine_feedback_pending")
        and not current.get("review_obligations_pending")
        and current.get("review_subject_digest") in {None, ""}
        and not current.get("decision_required_pending")
        and not current.get("review_report_pending")
        and not current.get("review_continuation_pending")
        and not current.get("reviewer_run_baseline_pending")
        and not current.get("delivery_identity_pending")
    )
    if stage == "authoring" and authoring_target:
        return "reached" if handoff_retired and assignment_retired else "safe"
    if not preservation_matches:
        return "progressed"
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
        and all(
            current.get(key) == baseline.get(key)
            for key in {
                "review_subject_digest",
                "review_generation",
                "review_ledger_generation",
                "review_ledger_current",
                "bounce_baseline",
                "decision_required_pending",
                "review_report_pending",
                "review_continuation_pending",
                "reviewer_run_baseline_pending",
                "delivery_identity_pending",
            }
        )
    ):
        return "safe"
    return "progressed"


def _stage_preservation_matches(
    stage: str,
    baseline: dict,
    current: dict,
    *,
    expected_bounce_baseline: dict[str, int] | None,
) -> bool:
    expected_bounces = baseline.get("absolute_bounces")
    if expected_bounces is None:
        expected_bounces = expected_bounce_baseline
    if expected_bounces is not None:
        current_bounces = current.get("absolute_bounces")
        if not isinstance(current_bounces, dict) or any(
            current_bounces.get(stage, -1) < expected_bounces.get(stage, 0)
            for stage in ("worker", "review", "merge")
        ):
            return False
    if stage in {"review", "merging"}:
        for key in (
            "delivery_identity_pending", "delivery_identity_sha256",
        ):
            if key in baseline and current.get(key) != baseline.get(key):
                return False
    if stage == "authoring":
        for key in (
            "review_ledger_present", "review_ledger_identity_sha256",
        ):
            if key in baseline and current.get(key) != baseline.get(key):
                return False
    return True

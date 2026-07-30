"""共享的 DAG 阶段恢复准备与 restart-safe 观察规则。

这里只准备 review/authoring 的 Store 状态；merging 仅校验前置并返回委托标记，
真正的 PR 请求和远端观察始终由 pipeline.delivery.run_merge_delivery 负责。
"""
from __future__ import annotations

import hashlib
import json

from .manifest import _dump_contract
from .taskmeta import TaskPhase
from ..engines.models import WorkItemStatus


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
    sync_contract: bool = False,
) -> str:
    """共享的 review/authoring 阶段准备；merge 交给 run_merge_delivery。

    本函数不派发 Agent，也不执行/观察 merge。调用者先持久化 manifest 意图，
    然后用自己的 restart-safe ledger 调用本原语；后续 dag run 负责真正流转。
    """
    if not node.work_item_id:
        return "no-work-item"
    item = store.get_work_item(node.work_item_id)
    validate_stage_recovery(item, stage)
    # 显式 stage recovery 开启新的执行世代。旧 review→worker handoff 只属于
    # 被 operator/amendment 取代的阶段，必须在任何可被 apply ledger 判定为
    # reached 的 contract/phase/status 写入前先退役。clear 是幂等 metadata
    # 写；若响应未知，重放 prepare_stage_recovery 仍会安全地再次清除。
    if item.worker_handoff is not None:
        store.update_work_item_metadata(
            node.work_item_id, worker_handoff={})
    if getattr(item, "delivery_identity", None) is not None:
        store.update_work_item_metadata(
            node.work_item_id, delivery_identity={})
    if sync_contract and node.contract is not None:
        store.set_node_contract(node.work_item_id, node.contract)
    if stage == "merging":
        return "delegated-to-run-merge-delivery"
    store.reset_review(node.work_item_id)
    if stage == "review":
        subject = expected_review_subject or stage_recovery_subject(
            node, store.get_work_item(node.work_item_id))
        store.prepare_review_cycle(node.work_item_id, subject)
        store.update_work_item_metadata(
            node.work_item_id, phase=TaskPhase.REVIEW)
        store.update_status(node.work_item_id, WorkItemStatus.IN_REVIEW)
        return "in_review"
    store.update_status(node.work_item_id, WorkItemStatus.TODO)
    return "todo"


def classify_stage_recovery_observation(
    stage: str,
    baseline: dict,
    current: dict,
    *,
    expected_contract_sha256: str,
    expected_review_subject: str | None = None,
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
        and current.get("review_verdict") in {None, ""}
        and current.get("review_subject_digest") in {None, ""}
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

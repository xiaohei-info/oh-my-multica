"""受控的运行中 DAG amendment。

Amendment 只描述 manifest 定义变更；work_item_id、状态、PR/verification/review
事实始终来自当前 manifest 与 WorkItemStore。应用时允许纯运行态漂移，但拒绝把
已评审 patch 套到定义已经变化的 DAG 上。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import yaml

from .lint import lint
from .manifest import (
    Manifest, Node, _dump_contract, _load_contract, load_manifest, save_manifest,
)
from .stage_recovery import (
    classify_stage_recovery_observation,
    prepare_stage_recovery,
    recovery_control_snapshot,
    recovery_evidence_digest,
    stage_recovery_subject,
    validate_stage_recovery,
)
from ..errors import ValidationError
from ..i18n import ui


SCHEMA = "omac.dag-amendment/v1"
APPLY_LEDGER_SCHEMA = "omac.amendment-apply/v1"
_RUNTIME_FIELDS = {
    "work_item_id", "status", "merged", "merged_at", "merge_request_state",
}
_NODE_FIELDS = {
    "worker", "reviewer", "blocked_by", "title", "description", "risk", "gate",
    "contract",
}
_REVIEW_SAFE_CONTRACT_FIELDS = {
    "acceptance", "integration_gates",
}


def _node_dict(node: Node, *, include_runtime: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": node.id,
        "worker": node.worker,
        "blocked_by": list(node.blocked_by),
    }
    for key in ("title", "description", "reviewer", "risk", "gate"):
        value = getattr(node, key)
        if value is not None:
            data[key] = copy.deepcopy(value)
    if node.contract is not None:
        data["contract"] = _dump_contract(node.contract)
    if include_runtime:
        data.update({
            "work_item_id": node.work_item_id,
            "status": node.status,
            "merged": node.merged,
            "merged_at": node.merged_at,
            "merge_request_state": node.merge_request_state,
        })
    return data


def _manifest_payload(manifest: Manifest, *, include_runtime: bool) -> dict[str, Any]:
    meta = copy.deepcopy(manifest.meta)
    if not include_runtime:
        meta.pop("amendment_apply", None)
    return {
        "meta": meta,
        "nodes": [
            _node_dict(node, include_runtime=include_runtime)
            for node in manifest.nodes.values()
        ],
    }


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manifest_digest(manifest: Manifest) -> str:
    return _digest(_manifest_payload(manifest, include_runtime=True))


def manifest_definition_digest(manifest: Manifest) -> str:
    return _digest(_manifest_payload(manifest, include_runtime=False))


def work_item_evidence_digest(item: Any) -> str:
    """绑定现有代码交付，不把易变 status/assignee 混入。"""
    return recovery_evidence_digest(item)


def parse_proposal(source: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, str):
        try:
            raw = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ValidationError(ui(
                f"Amendment YAML is invalid: {exc}",
                f"amendment YAML 不合法: {exc}")) from exc
    else:
        raw = copy.deepcopy(source)
    if not isinstance(raw, dict):
        raise ValidationError(ui(
            "Amendment must be a YAML object.", "amendment 必须是 YAML object。"))
    return raw


def _node_from_mapping(raw: dict[str, Any]) -> Node:
    if not isinstance(raw, dict):
        raise ValueError("node must be an object")
    node_id = str(raw.get("id") or "").strip()
    worker = str(raw.get("worker") or "").strip()
    if not node_id or not worker:
        raise ValueError("added node requires id and worker")
    return Node(
        id=node_id,
        worker=worker,
        blocked_by=list(raw.get("blocked_by") or []),
        title=raw.get("title"),
        description=raw.get("description"),
        reviewer=raw.get("reviewer"),
        risk=raw.get("risk"),
        gate=copy.deepcopy(raw.get("gate")),
        contract=_load_contract(raw.get("contract")),
    )


def _contract_changes(before: Node, raw: dict[str, Any]) -> set[str]:
    previous = _dump_contract(before.contract) if before.contract is not None else {}
    current = raw or {}
    return {
        key for key in set(previous) | set(current)
        if previous.get(key) != current.get(key)
    }


def _requires_ownership_migration(node: Node, changes: dict[str, Any]) -> bool:
    if "worker" in changes and changes["worker"] != node.worker:
        return True
    if "contract" not in changes:
        return False
    return "scope_paths" in _contract_changes(node, changes["contract"])


def _operation_stage(node: Node | None, operation: dict[str, Any]) -> str:
    op = operation.get("op")
    if op == "resume":
        return str(operation.get("stage") or "")
    if op in {"add", "remove"}:
        return "authoring"
    changes = operation.get("set") or {}
    non_contract = set(changes) - {"contract", "reviewer", "description", "title", "risk", "gate"}
    if non_contract:
        return "authoring"
    if "contract" in changes and node is not None:
        contract_changes = _contract_changes(node, changes["contract"])
        if contract_changes - _REVIEW_SAFE_CONTRACT_FIELDS:
            return "authoring"
    return "review"


def _apply_definition(manifest: Manifest, proposal: dict[str, Any]) -> Manifest:
    amended = copy.deepcopy(manifest)
    for operation in proposal.get("operations") or []:
        op = operation.get("op")
        node_id = operation.get("node")
        if op == "add":
            node = _node_from_mapping(operation.get("value") or {})
            amended.nodes[node.id] = node
            continue
        if op == "remove":
            amended.nodes.pop(node_id, None)
            continue
        if op == "resume":
            continue
        node = amended.nodes[node_id]
        for key, value in (operation.get("set") or {}).items():
            if key == "contract":
                node.contract = _load_contract(value)
            elif key == "blocked_by":
                node.blocked_by = list(value or [])
            else:
                setattr(node, key, copy.deepcopy(value))
    return amended


def validate_proposal(
    manifest: Manifest,
    proposal_source: str | dict[str, Any],
    agent_pool: set[str],
) -> list[str]:
    proposal = parse_proposal(proposal_source)
    errors: list[str] = []
    if proposal.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if not str(proposal.get("reason") or "").strip():
        errors.append("reason is required")
    operations = proposal.get("operations")
    if not isinstance(operations, list) or not operations:
        return errors + ["operations must be a non-empty list"]

    seen: set[str] = set()
    for index, operation in enumerate(operations):
        prefix = f"operations[{index}]"
        if not isinstance(operation, dict):
            errors.append(f"{prefix} must be an object")
            continue
        op = operation.get("op")
        if op not in {"update", "add", "remove", "resume"}:
            errors.append(f"{prefix}.op must be update, add, remove, or resume")
            continue
        if op == "add":
            raw_node = operation.get("value") or {}
            runtime_fields = set(raw_node) & _RUNTIME_FIELDS
            if runtime_fields:
                errors.append(
                    f"{prefix}.value contains runtime fields: "
                    + ", ".join(sorted(runtime_fields)))
            try:
                added = _node_from_mapping(raw_node)
            except ValueError as exc:
                errors.append(f"{prefix}: {exc}")
                continue
            if added.id in manifest.nodes or added.id in seen:
                errors.append(f"{prefix}: node {added.id!r} already exists")
            if added.contract is None:
                errors.append(f"{prefix}: added node requires a complete contract")
            if not isinstance(raw_node.get("blocked_by", []), list):
                errors.append(f"{prefix}.value.blocked_by must be a list")
            seen.add(added.id)
            continue

        node_id = str(operation.get("node") or "").strip()
        node = manifest.nodes.get(node_id)
        if node is None:
            errors.append(f"{prefix}: unknown node {node_id!r}")
            continue
        if node_id in seen:
            errors.append(f"{prefix}: node {node_id!r} has multiple operations")
        seen.add(node_id)
        if node.status == "done" or node.merged:
            errors.append(f"{prefix}: done/merged node {node_id!r} is immutable")
            continue
        if op == "remove":
            if node.work_item_id or node.status != "todo":
                errors.append(f"{prefix}: only untouched todo nodes may be removed")
            continue
        if op == "resume":
            if operation.get("stage") not in {"review", "authoring", "merging"}:
                errors.append(f"{prefix}.stage must be review, authoring, or merging")
            continue
        changes = operation.get("set")
        if not isinstance(changes, dict) or not changes:
            errors.append(f"{prefix}.set must be a non-empty object")
            continue
        unknown = set(changes) - _NODE_FIELDS
        if unknown:
            errors.append(f"{prefix}.set contains unsupported fields: {', '.join(sorted(unknown))}")
        if any(field in changes for field in _RUNTIME_FIELDS):
            errors.append(f"{prefix}: runtime fields cannot be patched")
        if "blocked_by" in changes and not isinstance(changes["blocked_by"], list):
            errors.append(f"{prefix}.set.blocked_by must be a list")
        if "contract" in changes and not isinstance(changes["contract"], dict):
            errors.append(f"{prefix}.set.contract must be a complete object")
        if node.work_item_id and _requires_ownership_migration(node, changes):
            migration = operation.get("migration") or {}
            if migration.get("ownership_transfer") is not True or not migration.get("reason"):
                errors.append(
                    f"{prefix}: executed node ownership migration requires "
                    "migration.ownership_transfer=true and a reason")

    if errors:
        return errors
    try:
        amended = _apply_definition(manifest, proposal)
    except (KeyError, TypeError, ValueError) as exc:
        return [f"could not apply proposal: {exc}"]
    errors = lint(amended, agent_pool)
    _, _, immutable = _minimal_rerun(manifest, proposal)
    if immutable:
        errors.append(
            "implementation-affecting change reaches done/merged downstream nodes: "
            + ", ".join(immutable)
            + "; add an explicit compensating node instead of rewriting completed facts")
    return errors


def _changed_node_ids(proposal: dict[str, Any]) -> list[str]:
    ids = []
    for operation in proposal.get("operations") or []:
        if operation.get("op") == "add":
            node_id = (operation.get("value") or {}).get("id")
        else:
            node_id = operation.get("node")
        if node_id:
            ids.append(str(node_id))
    return ids


def _classify(
    manifest: Manifest, proposal: dict[str, Any],
) -> dict[str, list[str]]:
    result = {"review": [], "authoring": [], "merging": []}
    for operation in proposal.get("operations") or []:
        if operation.get("op") == "add":
            node_id = str((operation.get("value") or {}).get("id"))
            result["authoring"].append(node_id)
            continue
        node_id = str(operation.get("node"))
        node = manifest.nodes.get(node_id)
        stage = _operation_stage(node, operation)
        if stage == "review" and (node is None or not node.work_item_id):
            stage = "authoring"
        if operation.get("op") == "remove":
            continue
        result[stage].append(node_id)
    return result


def _implementation_affecting(node: Node | None, operation: dict[str, Any]) -> bool:
    op = operation.get("op")
    if op in {"add", "remove"}:
        return True
    if op != "update" or node is None:
        return False
    changes = operation.get("set") or {}
    if set(changes) & {"worker", "blocked_by", "gate"}:
        return True
    if "contract" not in changes:
        return False
    return bool(
        _contract_changes(node, changes["contract"])
        - _REVIEW_SAFE_CONTRACT_FIELDS
    )


def _downstream_union(
    before: Manifest, after: Manifest, roots: set[str],
) -> set[str]:
    reverse: dict[str, set[str]] = {
        node_id: set() for node_id in set(before.nodes) | set(after.nodes)
    }
    for manifest in (before, after):
        for node_id, node in manifest.nodes.items():
            for blocker in node.blocked_by:
                reverse.setdefault(blocker, set()).add(node_id)
    downstream: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        for dependent in reverse.get(current, set()):
            if dependent in downstream or dependent in roots:
                continue
            downstream.add(dependent)
            stack.append(dependent)
    return downstream


def _node_started(node: Node) -> bool:
    return bool(node.work_item_id) or node.status not in {"todo", "blocked"}


def _minimal_rerun(
    manifest: Manifest, proposal: dict[str, Any],
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    minimal = _classify(manifest, proposal)
    amended = _apply_definition(manifest, proposal)
    roots = {
        str(
            (operation.get("value") or {}).get("id")
            if operation.get("op") == "add" else operation.get("node")
        )
        for operation in proposal.get("operations") or []
        if _implementation_affecting(
            manifest.nodes.get(str(operation.get("node") or "")), operation)
    }
    roots.discard("None")
    downstream = _downstream_union(manifest, amended, roots)
    explicit_removed = {
        str(operation.get("node"))
        for operation in proposal.get("operations") or []
        if operation.get("op") == "remove"
    }
    derived: list[str] = []
    immutable: list[str] = []
    ordered_ids = list(manifest.nodes) + [
        node_id for node_id in amended.nodes if node_id not in manifest.nodes
    ]
    for node_id in ordered_ids:
        if node_id not in downstream or node_id in explicit_removed:
            continue
        node = manifest.nodes.get(node_id) or amended.nodes.get(node_id)
        if node is None or not _node_started(node):
            continue
        if node.status == "done" or node.merged:
            immutable.append(node_id)
            continue
        for stage in minimal:
            if node_id in minimal[stage]:
                minimal[stage].remove(node_id)
        minimal["authoring"].append(node_id)
        derived.append(node_id)
    return minimal, derived, immutable


def _proposal_core(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": proposal.get("schema"),
        "reason": proposal.get("reason"),
        "operations": copy.deepcopy(proposal.get("operations") or []),
    }


def _amendment_id(
    definition_digest: str,
    proposal: dict[str, Any],
    minimal: dict[str, list[str]],
) -> str:
    return f"amend-{_digest([definition_digest, _proposal_core(proposal), minimal])[:12]}"


def build_reviewed_amendment(
    manifest: Manifest,
    proposal_source: str | dict[str, Any],
    store: Any,
    *,
    issue_id: str,
    reviewer_verdict: str,
    agent_pool: set[str] | None = None,
) -> dict[str, Any]:
    proposal = parse_proposal(proposal_source)
    pool = agent_pool or set(store.list_members(store.config.workspace_id))
    errors = validate_proposal(manifest, proposal, pool)
    if errors:
        raise ValidationError("Amendment validation failed:\n  - " + "\n  - ".join(errors))
    if reviewer_verdict != "pass":
        raise ValidationError("Only a reviewer pass can enter human confirmation")

    minimal, derived, immutable = _minimal_rerun(manifest, proposal)
    if immutable:
        raise ValidationError(
            "Amendment affects immutable downstream nodes: " + ", ".join(immutable))
    evidence: dict[str, str] = {}
    affected_ids = {
        node_id for node_ids in minimal.values() for node_id in node_ids
    }
    for node_id in affected_ids:
        node = manifest.nodes.get(node_id)
        if node and node.work_item_id:
            evidence[node_id] = work_item_evidence_digest(
                store.get_work_item(node.work_item_id))
    definition_digest = manifest_definition_digest(manifest)
    return {
        **proposal,
        "amendment_id": _amendment_id(definition_digest, proposal, minimal),
        "base": {
            "manifest_sha256": manifest_digest(manifest),
            "definition_sha256": definition_digest,
            "evidence_sha256": evidence,
        },
        "review": {"issue_id": issue_id, "verdict": reviewer_verdict},
        "human_confirmation": "pending",
        "analysis": {
            "changed_nodes": _changed_node_ids(proposal),
            "derived_started_downstream": derived,
            "minimal_rerun": minimal,
            "risk": (
                "definition changes are CAS-protected; runtime-only drift is rebased "
                "only when it does not change the minimum recovery set"
            ),
        },
    }


def _verify_base(current: Manifest, amendment: dict[str, Any]) -> bool:
    base = amendment.get("base") or {}
    current_full = manifest_digest(current)
    if current_full == base.get("manifest_sha256"):
        return False
    if manifest_definition_digest(current) != base.get("definition_sha256"):
        raise ValidationError(ui(
            "The manifest definition changed after amendment review. Generate and review a new amendment.",
            "amendment 评审后 manifest definition changed；请重新生成并评审 amendment。"))
    return True


def _verify_evidence(current: Manifest, amendment: dict[str, Any], store: Any) -> None:
    expected = (amendment.get("base") or {}).get("evidence_sha256") or {}
    for node_id, digest in expected.items():
        node = current.nodes.get(node_id)
        if node is None or not node.work_item_id:
            raise ValidationError(f"node {node_id}: work item disappeared after review")
        if work_item_evidence_digest(store.get_work_item(node.work_item_id)) != digest:
            raise ValidationError(ui(
                f"Node {node_id} delivery evidence changed after amendment review. Review a rebased amendment.",
                f"节点 {node_id} 的交付证据在 amendment 评审后发生变化；请 rebase 后重新评审。"))


def _prepare_apply_ledger(
    manifest: Manifest,
    amendment_id: str,
    minimal: dict[str, list[str]],
    store: Any,
) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for stage, node_ids in minimal.items():
        for node_id in node_ids:
            node = manifest.nodes.get(node_id)
            if node is None or not node.work_item_id:
                entries[node_id] = {
                    "stage": stage,
                    "state": "synced",
                    "reason": "no existing work item side effect",
                }
                continue
            item = store.get_work_item(node.work_item_id)
            entry = {
                "stage": stage,
                "state": "pending",
                "baseline": recovery_control_snapshot(item),
                "expected_contract_sha256": _digest(
                    _dump_contract(node.contract) if node.contract else None),
            }
            if stage == "review":
                entry["expected_review_subject"] = stage_recovery_subject(node, item)
            entries[node_id] = entry
    return {
        "schema": APPLY_LEDGER_SCHEMA,
        "amendment_id": amendment_id,
        "nodes": entries,
    }


def _save_ledger(manifest: Manifest, manifest_path: str, ledger: dict[str, Any]) -> None:
    manifest.meta["amendment_apply"] = ledger
    save_manifest(manifest, manifest_path)


def _resume_apply_ledger(
    manifest: Manifest,
    manifest_path: str,
    store: Any,
) -> dict[str, list[str]]:
    ledger = manifest.meta.get("amendment_apply")
    if not isinstance(ledger, dict) or ledger.get("schema") != APPLY_LEDGER_SCHEMA:
        raise ValidationError(
            "Applied amendment is missing a valid per-node apply ledger")
    summary = {"synced": [], "observed_progress": [], "already_complete": []}
    for node_id, entry in ledger.get("nodes", {}).items():
        state = entry.get("state")
        if state in {"synced", "observed_progress"}:
            summary["already_complete"].append(node_id)
            continue
        node = manifest.nodes.get(node_id)
        if node is None or not node.work_item_id:
            entry["state"] = "synced"
            entry["reason"] = "no existing work item side effect"
            _save_ledger(manifest, manifest_path, ledger)
            summary["synced"].append(node_id)
            continue
        item = store.get_work_item(node.work_item_id)
        current = recovery_control_snapshot(item)
        observation = classify_stage_recovery_observation(
            entry["stage"],
            entry.get("baseline") or {},
            current,
            expected_contract_sha256=entry.get("expected_contract_sha256") or "",
            expected_review_subject=entry.get("expected_review_subject"),
        )
        if observation == "reached":
            entry["state"] = "synced"
            entry["observed"] = current
            _save_ledger(manifest, manifest_path, ledger)
            summary["synced"].append(node_id)
            continue
        if observation == "progressed":
            entry["state"] = "observed_progress"
            entry["observed"] = current
            entry["reason"] = (
                "work item changed after definition apply; skipped to prevent rollback"
            )
            _save_ledger(manifest, manifest_path, ledger)
            summary["observed_progress"].append(node_id)
            continue
        entry["state"] = "syncing"
        entry["attempt_baseline"] = current
        _save_ledger(manifest, manifest_path, ledger)
        prepare_stage_recovery(
            node,
            store,
            entry["stage"],
            expected_review_subject=entry.get("expected_review_subject"),
            sync_contract=True,
        )
        entry["state"] = "synced"
        entry["observed"] = recovery_control_snapshot(
            store.get_work_item(node.work_item_id))
        _save_ledger(manifest, manifest_path, ledger)
        summary["synced"].append(node_id)
    return summary


def _validate_stage_preconditions(
    manifest: Manifest, minimal: dict[str, list[str]], store: Any,
) -> None:
    for node_id in minimal.get("review", []):
        node = manifest.nodes.get(node_id)
        if node is None or not node.work_item_id:
            raise ValidationError(
                f"node {node_id}: review recovery requires an existing work item")
    for node_id in minimal.get("merging", []):
        node = manifest.nodes.get(node_id)
        if node is None or not node.work_item_id:
            raise ValidationError(
                f"node {node_id}: merge-only recovery requires an existing work item")
        item = store.get_work_item(node.work_item_id)
        try:
            validate_stage_recovery(item, "merging")
        except ValueError as exc:
            raise ValidationError(
                f"node {node_id}: {exc}") from exc


def apply_amendment(
    manifest_path: str,
    amendment_source: str | dict[str, Any],
    store: Any,
    agent_pool: set[str],
) -> dict[str, Any]:
    amendment = parse_proposal(amendment_source)
    if amendment.get("review", {}).get("verdict") != "pass":
        raise ValidationError("Amendment has not passed Reviewer review")
    if amendment.get("human_confirmation") not in {"pending", "accepted", "applied"}:
        raise ValidationError("Amendment is not waiting for human confirmation")
    expected_id = _amendment_id(
        (amendment.get("base") or {}).get("definition_sha256") or "",
        amendment,
        (amendment.get("analysis") or {}).get("minimal_rerun") or {},
    )
    if amendment.get("amendment_id") != expected_id:
        raise ValidationError(
            "Amendment identity does not match its reviewed proposal and analysis")

    current = load_manifest(manifest_path)
    already_applied = current.meta.get("last_amendment_id") == amendment.get("amendment_id")
    runtime_rebased = False
    if not already_applied:
        runtime_rebased = _verify_base(current, amendment)
        errors = validate_proposal(current, amendment, agent_pool)
        if errors:
            raise ValidationError("Amendment validation failed:\n  - " + "\n  - ".join(errors))
        recomputed_minimal, _, immutable = _minimal_rerun(current, amendment)
        if immutable:
            raise ValidationError(
                "Amendment affects immutable downstream nodes: "
                + ", ".join(immutable))
        reviewed_minimal = (amendment.get("analysis") or {}).get("minimal_rerun") or {}
        if recomputed_minimal != reviewed_minimal:
            raise ValidationError(ui(
                "The minimum recovery set changed after amendment review. Rebase and review again.",
                "amendment 评审后最小恢复集合已变化；请基于最新事实 rebase 并重新评审。"))
        _verify_evidence(current, amendment, store)
        amended = _apply_definition(current, amendment)
        minimal = recomputed_minimal
        _validate_stage_preconditions(amended, minimal, store)
        for stage, node_ids in minimal.items():
            for node_id in node_ids:
                if node_id in amended.nodes:
                    amended.nodes[node_id].status = {
                        "review": "in_review",
                        "authoring": "todo",
                        "merging": "merging",
                    }[stage]
                    if stage != "merging":
                        amended.nodes[node_id].merge_request_state = None
        amended.meta["amendment_revision"] = int(
            amended.meta.get("amendment_revision") or 0) + 1
        amended.meta["last_amendment_id"] = amendment.get("amendment_id")
        amended.meta["amendment_apply"] = _prepare_apply_ledger(
            amended, amendment["amendment_id"], minimal, store)
        save_manifest(amended, manifest_path)
        current = amended
    else:
        runtime_rebased = True
        minimal = amendment.get("analysis", {}).get("minimal_rerun") or {}
        ledger = current.meta.get("amendment_apply") or {}
        if ledger.get("amendment_id") != amendment.get("amendment_id"):
            raise ValidationError(
                "Manifest amendment apply ledger does not match the accepted amendment")

    sync_summary = _resume_apply_ledger(
        current, manifest_path, store)

    return {
        "amendment_id": amendment.get("amendment_id"),
        "manifest": manifest_path,
        "minimal_rerun": minimal,
        "runtime_rebased": runtime_rebased,
        "apply_semantics": "manifest-atomic-with-restart-safe-store-compensation",
        "sync": sync_summary,
    }

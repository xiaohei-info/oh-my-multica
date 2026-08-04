"""受控的运行中 DAG amendment。

Amendment 只描述 manifest 定义变更；work_item_id、状态、PR/verification/review
事实始终来自当前 manifest 与 WorkItemStore。应用时允许纯运行态漂移，但拒绝把
已评审 patch 套到定义已经变化的 DAG 上。
"""
from __future__ import annotations

import copy
from dataclasses import asdict, is_dataclass
import hashlib
import json
import shlex
from typing import Any

import yaml

from .lint import lint
from .manifest import (
    Manifest, Node, _dump_contract, _load_contract, load_manifest, save_manifest,
)
from .retry_budget import amendment_bounce_baseline
from .stage_recovery import (
    classify_stage_recovery_observation,
    observe_recovery_control,
    prepare_stage_recovery,
    recovery_control_snapshot,
    recovery_evidence_digest,
    stage_recovery_subject,
    validate_stage_recovery,
)
from .taskmeta import TaskPhase
from ..engines.models import WorkItemStatus
from ..errors import NeedsDecision, ValidationError
from ..i18n import ui


SCHEMA = "omac.dag-amendment/v1"
APPLY_LEDGER_SCHEMA = "omac.amendment-apply/v1"
_COMPLETE_APPLY_STATES = {"synced", "observed_progress"}
_RUNTIME_FIELDS = {
    "work_item_id", "status", "merged", "merged_at", "merge_request_state",
}
_NODE_FIELDS = {
    "worker", "reviewer", "blocked_by", "title", "description", "risk", "gate",
    "contract",
}
_REVIEW_SAFE_CONTRACT_FIELDS = {
    "acceptance", "acceptance_claims", "acceptance_contributions",
    "acceptance_refs", "integration_gates",
}
_RESPONSIBILITY_OPERATION = "update-responsibility"
_RESPONSIBILITY_OPERATION_FIELDS = {
    "op", "node", "acceptance_claims", "acceptance_contributions",
    "acceptance_refs", "clear_legacy_acceptance",
    "integration_gate_responsibility_patches",
    "historical_contract_correction", "reason", "resume_stage",
}
_CONTRACT_BOUNDARY_FIELDS = {"evidence_mode", "produces", "consumes"}


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


def historical_work_item_evidence_digest(item: Any) -> str:
    """绑定 historical correction 从 Reviewer 到 apply 不得改变的 Store 事实。"""
    status = getattr(item, "status", None)
    phase = getattr(item, "phase", None)
    return _digest({
        "status": getattr(status, "value", status),
        "phase": getattr(phase, "value", phase),
        "worker": getattr(item, "worker", None),
        "reviewer": getattr(item, "reviewer", None),
        "artifacts": getattr(item, "artifacts", None),
        "verification": getattr(item, "verification", None),
        "verification_ref": getattr(item, "verification_ref", None),
        "review_verdict": getattr(item, "review_verdict", None),
        "review_report": getattr(item, "review_report", None),
        "review_report_ref": getattr(item, "review_report_ref", None),
        "review_subject_digest": getattr(item, "review_subject_digest", None),
        "review_ledger": getattr(item, "review_ledger", None),
        "review_ledger_ref": getattr(item, "review_ledger_ref", None),
    })


def _node_runtime_digest(node: Node) -> str:
    return _digest({
        "work_item_id": node.work_item_id,
        "status": node.status,
        "merged": node.merged,
        "merged_at": node.merged_at,
        "merge_request_state": node.merge_request_state,
    })


def _acceptance_digest(acceptance: Any) -> str | None:
    if acceptance is None:
        return None
    value = asdict(acceptance) if is_dataclass(acceptance) else acceptance
    return _digest(value)


def amendment_apply_blocker(
    manifest: Manifest, manifest_path: str,
) -> dict[str, Any] | None:
    """返回未完成 amendment apply 的 fail-closed 证据；完成时返回 None。"""
    ledger = manifest.meta.get("amendment_apply")
    if ledger is None:
        return None
    if not isinstance(ledger, dict):
        ledger = {}
    nodes = ledger.get("nodes")
    node_entries = nodes if isinstance(nodes, dict) else {}
    incomplete = []
    for node_id, entry in node_entries.items():
        value = entry if isinstance(entry, dict) else {}
        state = value.get("state")
        if state in _COMPLETE_APPLY_STATES:
            continue
        incomplete.append({
            "node_id": str(node_id),
            "stage": value.get("stage"),
            "state": state or "invalid",
        })
    incomplete.sort(key=lambda item: item["node_id"])

    amendment_id = str(ledger.get("amendment_id") or "")
    identity_matches = bool(
        amendment_id
        and amendment_id == manifest.meta.get("last_amendment_id")
    )
    schema_matches = ledger.get("schema") == APPLY_LEDGER_SCHEMA
    structure_matches = isinstance(nodes, dict)
    if not incomplete and identity_matches and schema_matches and structure_matches:
        return None

    amendment_file = str(
        ledger.get("amendment_file") or "<reviewed-amendment.yaml>")
    resume_command = " ".join((
        "omac dag amend accept",
        shlex.quote(manifest_path),
        shlex.quote(amendment_file),
    ))
    return {
        "reason": "amendment_apply_incomplete",
        "amendment_id": amendment_id or None,
        "last_amendment_id": manifest.meta.get("last_amendment_id"),
        "ledger_schema": ledger.get("schema"),
        "identity_matches": identity_matches,
        "incomplete_nodes": incomplete,
        "resume_command": resume_command,
        "required_terminal_states": sorted(_COMPLETE_APPLY_STATES),
    }


def ensure_amendment_apply_complete(
    manifest: Manifest, manifest_path: str,
) -> None:
    """阻止 Runner 消费只写入 manifest、尚未补偿 Store 的 amendment。"""
    report = amendment_apply_blocker(manifest, manifest_path)
    if report is None:
        return
    node_ids = [entry["node_id"] for entry in report["incomplete_nodes"]]
    raise NeedsDecision(ui(
        "DAG advancement is blocked because amendment "
        f"{report['amendment_id'] or '<unknown>'} has unfinished apply state "
        f"for nodes {node_ids}. Resume the same human-confirmed amendment with "
        f"`{report['resume_command']}`; do not run, tick, reconcile, dispatch, or merge "
        "until every ledger entry is synced or observed_progress.",
        "DAG 推进已阻断：amendment "
        f"{report['amendment_id'] or '<unknown>'} 的节点 {node_ids} 尚未完成 apply。"
        f"请使用 `{report['resume_command']}` 续接同一个已人工确认的 amendment；"
        "所有 ledger 条目达到 synced 或 observed_progress 前，不得 run、tick、"
        "reconcile、派发或 merge。",
    ), report=report)


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


def _contract_boundary_replacement_errors(
    node: Node, operation: dict[str, Any], prefix: str,
) -> list[str]:
    changes = operation.get("set") or {}
    replacement = changes.get("contract")
    clear_boundary = operation.get("clear_contract_boundary")
    if clear_boundary is not None and clear_boundary is not True:
        return [f"{prefix}.clear_contract_boundary must be true when present"]
    if not isinstance(replacement, dict):
        if clear_boundary is True:
            return [
                f"{prefix}.clear_contract_boundary requires a complete contract replacement"
            ]
        return []

    replacement_fields = _CONTRACT_BOUNDARY_FIELDS.intersection(replacement)
    if clear_boundary is True:
        if replacement_fields:
            return [
                f"{prefix}: clear_contract_boundary=true cannot be combined with "
                "evidence_mode, produces, or consumes"
            ]
        return []

    previous = _dump_contract(node.contract) if node.contract is not None else {}
    previous_fields = _CONTRACT_BOUNDARY_FIELDS.intersection(previous)
    if not previous_fields:
        return []
    if not previous_fields.issubset(replacement_fields):
        ordered = [
            field for field in ("evidence_mode", "produces", "consumes")
            if field in previous_fields
        ]
        required = ", ".join(ordered[:-1])
        if len(ordered) > 1:
            required += f", and {ordered[-1]}"
        elif ordered:
            required = ordered[0]
        return [
            f"{prefix}.set.contract must explicitly preserve {required}, or set "
            "clear_contract_boundary=true"
        ]
    return []


def _is_responsibility_operation(operation: dict[str, Any]) -> bool:
    return operation.get("op") == _RESPONSIBILITY_OPERATION


def _responsibility_digest(contract: Any) -> str:
    dumped = _dump_contract(contract) if contract is not None else {}
    gates = dumped.get("integration_gates") or []
    return _digest({
        "acceptance": dumped.get("acceptance", []),
        "acceptance_claims": dumped.get("acceptance_claims", []),
        "acceptance_contributions": dumped.get("acceptance_contributions", []),
        "acceptance_refs": dumped.get("acceptance_refs", []),
        "integration_gate_acceptance_refs": [{
            "name": gate.get("name"),
            "acceptance_refs": gate.get("acceptance_refs", []),
        } for gate in gates if isinstance(gate, dict)],
    })


def _responsibility_contract(node: Node, operation: dict[str, Any]):
    if node.contract is None:
        raise ValueError("responsibility update requires an existing contract")
    raw = _dump_contract(node.contract)
    raw.pop("acceptance", None)
    for field in (
        "acceptance_claims", "acceptance_contributions", "acceptance_refs",
    ):
        raw[field] = copy.deepcopy(operation[field])
    patches = operation.get("integration_gate_responsibility_patches") or []
    if patches:
        gates = copy.deepcopy(raw.get("integration_gates") or [])
        patch_by_name = {patch["name"]: patch for patch in patches}
        for gate in gates:
            if isinstance(gate, dict) and gate.get("name") in patch_by_name:
                gate["acceptance_refs"] = copy.deepcopy(
                    patch_by_name[gate["name"]]["acceptance_refs"])
        raw["integration_gates"] = gates
    return _load_contract(raw)


def _responsibility_allowed_diff(before: Node, after: Node) -> list[str]:
    previous = _dump_contract(before.contract) if before.contract else {}
    current = _dump_contract(after.contract) if after.contract else {}
    changed = [
        f"contract.{field}" for field in (
            "acceptance", "acceptance_claims", "acceptance_contributions",
            "acceptance_refs",
        ) if previous.get(field, []) != current.get(field, [])
    ]
    previous_gates = {
        gate.get("name"): gate for gate in previous.get("integration_gates", [])
        if isinstance(gate, dict) and gate.get("name")
    }
    for gate in current.get("integration_gates", []):
        if not isinstance(gate, dict) or not gate.get("name"):
            continue
        old = previous_gates.get(gate["name"], {})
        if old.get("acceptance_refs", []) != gate.get("acceptance_refs", []):
            changed.append(
                f"contract.integration_gates[{gate['name']}].acceptance_refs")
    return changed


def _historical_contract_corrections(
    manifest: Manifest,
    proposal: dict[str, Any],
    evidence: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    corrections = []
    for operation in proposal.get("operations") or []:
        if not (
            _is_responsibility_operation(operation)
            and operation.get("historical_contract_correction") is True
        ):
            continue
        node = manifest.nodes[str(operation.get("node") or "")]
        after = copy.deepcopy(node)
        after.contract = _responsibility_contract(node, operation)
        correction = {
            "node": node.id,
            "runtime_facts_sha256": _node_runtime_digest(node),
            "before_contract_sha256": _digest(_dump_contract(node.contract)),
            "after_contract_sha256": _digest(_dump_contract(after.contract)),
            "before_responsibility_sha256": _responsibility_digest(node.contract),
            "after_responsibility_sha256": _responsibility_digest(after.contract),
            "allowed_field_diff": _responsibility_allowed_diff(node, after),
            "reason": operation["reason"],
        }
        if evidence is not None:
            correction["evidence_sha256"] = evidence.get(node.id)
        corrections.append(correction)
    return corrections


def _validate_responsibility_operation(
    node: Node, operation: dict[str, Any], prefix: str,
) -> list[str]:
    errors = []
    unknown = set(operation) - _RESPONSIBILITY_OPERATION_FIELDS
    if unknown:
        errors.append(
            f"{prefix} contains unsupported fields: {', '.join(sorted(unknown))}")
    for field in (
        "acceptance_claims", "acceptance_contributions", "acceptance_refs",
    ):
        if field not in operation:
            errors.append(f"{prefix}.{field} is required")
        elif not isinstance(operation[field], list):
            errors.append(f"{prefix}.{field} must be a list")
    if operation.get("clear_legacy_acceptance") is not True:
        errors.append(f"{prefix}.clear_legacy_acceptance must be true")
    patches = operation.get("integration_gate_responsibility_patches", [])
    if not isinstance(patches, list):
        errors.append(f"{prefix}.integration_gate_responsibility_patches must be a list")
        patches = []
    gate_names = {
        gate.get("name") for gate in (
            _dump_contract(node.contract).get("integration_gates", [])
            if node.contract else [])
        if isinstance(gate, dict) and isinstance(gate.get("name"), str)
    }
    seen = set()
    for index, patch in enumerate(patches):
        patch_prefix = f"{prefix}.integration_gate_responsibility_patches[{index}]"
        if not isinstance(patch, dict):
            errors.append(f"{patch_prefix} must be an object")
            continue
        extra = set(patch) - {"name", "acceptance_refs"}
        if extra:
            errors.append(
                f"{patch_prefix} contains unsupported fields: {', '.join(sorted(extra))}")
        name = patch.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{patch_prefix}.name must be a non-empty string")
        elif name not in gate_names:
            errors.append(f"{patch_prefix}.name does not identify an existing integration gate")
        elif name in seen:
            errors.append(f"{patch_prefix}.name is duplicated: {name}")
        seen.add(name)
        if not isinstance(patch.get("acceptance_refs"), list):
            errors.append(f"{patch_prefix}.acceptance_refs must be a list")
        else:
            refs = patch["acceptance_refs"]
            valid_refs = [
                ref for ref in refs
                if isinstance(ref, str) and ref.strip()
            ]
            if len(valid_refs) != len(refs):
                errors.append(
                    f"{patch_prefix}.acceptance_refs entries must be non-empty strings")
            if len(valid_refs) != len(set(valid_refs)):
                errors.append(f"{patch_prefix}.acceptance_refs must not contain duplicates")
    historical = operation.get("historical_contract_correction") is True
    resume_stage = operation.get("resume_stage")
    if resume_stage is not None and resume_stage not in {
        "review", "authoring", "merging",
    }:
        errors.append(
            f"{prefix}.resume_stage must be review, authoring, or merging")
    if node.status == "done" or node.merged:
        if not historical:
            errors.append(
                f"{prefix}: done/merged node {node.id!r} requires "
                "historical_contract_correction=true")
        if not isinstance(operation.get("reason"), str) or not operation["reason"].strip():
            errors.append(f"{prefix}.reason is required for a historical contract correction")
        if not node.work_item_id:
            errors.append(
                f"{prefix}: historical contract correction requires an existing work item for evidence CAS")
    elif historical:
        errors.append(
            f"{prefix}.historical_contract_correction is only valid for done/merged nodes")
    if historical and "resume_stage" in operation:
        errors.append(f"{prefix}: historical contract correction cannot set resume_stage")
    if resume_stage in {"review", "authoring", "merging"} and not node.work_item_id:
        errors.append(
            f"{prefix}: explicit resume_stage requires an existing work item")
    if node.contract is None:
        errors.append(f"{prefix}: responsibility update requires an existing contract")
    if historical and not errors:
        after = copy.deepcopy(node)
        after.contract = _responsibility_contract(node, operation)
        if not _responsibility_allowed_diff(node, after):
            errors.append(
                f"{prefix}: historical contract correction must change at least one "
                "acceptance responsibility field")
    return errors


def _requires_ownership_migration(node: Node, changes: dict[str, Any]) -> bool:
    if "worker" in changes and changes["worker"] != node.worker:
        return True
    if "contract" not in changes:
        return False
    return "scope_paths" in _contract_changes(node, changes["contract"])


def _operation_stage(node: Node | None, operation: dict[str, Any]) -> str:
    op = operation.get("op")
    if op == _RESPONSIBILITY_OPERATION:
        return str(operation.get("resume_stage") or "review")
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
        if op == _RESPONSIBILITY_OPERATION:
            node.contract = _responsibility_contract(node, operation)
            continue
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
    *,
    acceptance: Any = None,
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
        if op not in {"update", "add", "remove", "resume", _RESPONSIBILITY_OPERATION}:
            errors.append(
                f"{prefix}.op must be update, add, remove, resume, or "
                f"{_RESPONSIBILITY_OPERATION}")
            continue
        if "clear_contract_boundary" in operation and op != "update":
            errors.append(
                f"{prefix}.clear_contract_boundary is valid only for update operations")
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
        if op == _RESPONSIBILITY_OPERATION:
            errors.extend(_validate_responsibility_operation(node, operation, prefix))
            continue
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
        errors.extend(_contract_boundary_replacement_errors(
            node, operation, prefix))
        if (
            "contract" in changes
            and isinstance(changes["contract"], dict)
            and _contract_changes(node, changes["contract"])
            & {"acceptance_claims", "acceptance_contributions", "acceptance_refs"}
        ):
            errors.append(
                f"{prefix}: responsibility migration must use "
                f"{_RESPONSIBILITY_OPERATION}")
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
    errors = lint(amended, agent_pool, acceptance=acceptance)
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
        if (
            _is_responsibility_operation(operation)
            and operation.get("historical_contract_correction") is True
        ):
            continue
        if _is_responsibility_operation(operation) and (node is None or not node.work_item_id):
            continue
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
    if op == _RESPONSIBILITY_OPERATION:
        return False
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
    historical_corrections: list[dict[str, Any]],
    evidence: dict[str, str],
) -> str:
    identity = _digest([
        definition_digest, _proposal_core(proposal), minimal,
        historical_corrections, evidence,
    ])[:12]
    return "amend-" + identity


def build_reviewed_amendment(
    manifest: Manifest,
    proposal_source: str | dict[str, Any],
    store: Any,
    *,
    issue_id: str,
    reviewer_verdict: str,
    agent_pool: set[str] | None = None,
    acceptance: Any = None,
) -> dict[str, Any]:
    proposal = parse_proposal(proposal_source)
    pool = agent_pool or set(store.list_members(store.config.workspace_id))
    errors = validate_proposal(
        manifest, proposal, pool, acceptance=acceptance)
    if errors:
        raise ValidationError("Amendment validation failed:\n  - " + "\n  - ".join(errors))
    if reviewer_verdict != "pass":
        raise ValidationError("Only a reviewer pass can enter human confirmation")

    minimal, derived, immutable = _minimal_rerun(manifest, proposal)
    if immutable:
        raise ValidationError(
            "Amendment affects immutable downstream nodes: " + ", ".join(immutable))
    evidence: dict[str, str] = {}
    historical_node_ids = {
        str(operation.get("node"))
        for operation in proposal.get("operations") or []
        if _is_responsibility_operation(operation)
        and operation.get("historical_contract_correction") is True
    }
    affected_ids = {
        node_id for node_ids in minimal.values() for node_id in node_ids
    }
    affected_ids.update(historical_node_ids)
    for node_id in affected_ids:
        node = manifest.nodes.get(node_id)
        if node_id in historical_node_ids:
            if node is None or not node.work_item_id:
                raise ValidationError(
                    f"historical contract correction node {node_id} requires a work item for evidence CAS")
        if node and node.work_item_id:
            item = store.get_work_item(node.work_item_id)
            evidence[node_id] = (
                historical_work_item_evidence_digest(item)
                if node_id in historical_node_ids
                else work_item_evidence_digest(item)
            )
    historical_corrections = _historical_contract_corrections(
        manifest, proposal, evidence)
    definition_digest = manifest_definition_digest(manifest)
    base = {
        "manifest_sha256": manifest_digest(manifest),
        "definition_sha256": definition_digest,
        "evidence_sha256": evidence,
    }
    acceptance_sha256 = _acceptance_digest(acceptance)
    if acceptance_sha256:
        base["acceptance_sha256"] = acceptance_sha256
    return {
        **proposal,
        "amendment_id": _amendment_id(
            definition_digest, proposal, minimal, historical_corrections, evidence),
        "base": base,
        "review": {"issue_id": issue_id, "verdict": reviewer_verdict},
        "human_confirmation": "pending",
        "analysis": {
            "changed_nodes": _changed_node_ids(proposal),
            "derived_started_downstream": derived,
            "minimal_rerun": minimal,
            "historical_contract_corrections": historical_corrections,
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
    historical_node_ids = {
        correction.get("node")
        for correction in (
            (amendment.get("analysis") or {}).get(
                "historical_contract_corrections") or [])
        if isinstance(correction, dict)
    }
    for node_id, digest in expected.items():
        node = current.nodes.get(node_id)
        if node is None or not node.work_item_id:
            raise ValidationError(f"node {node_id}: work item disappeared after review")
        item = store.get_work_item(node.work_item_id)
        current_digest = (
            historical_work_item_evidence_digest(item)
            if node_id in historical_node_ids
            else work_item_evidence_digest(item)
        )
        if current_digest != digest:
            raise ValidationError(ui(
                f"Node {node_id} delivery evidence changed after amendment review. Review a rebased amendment.",
                f"节点 {node_id} 的交付证据在 amendment 评审后发生变化；请 rebase 后重新评审。"))


def _prepare_apply_ledger(
    manifest: Manifest,
    amendment_id: str,
    minimal: dict[str, list[str]],
    historical_corrections: list[dict[str, Any]],
    store: Any,
    amendment_file: str | None,
    responsibility_merge_sync_nodes: set[str],
) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for correction in historical_corrections:
        entries[correction["node"]] = {
            "stage": "historical_contract_correction",
            "state": "synced",
            "store_side_effect": "none",
            "runtime_facts_sha256": correction["runtime_facts_sha256"],
            "before_contract_sha256": correction["before_contract_sha256"],
            "after_contract_sha256": correction["after_contract_sha256"],
            "before_responsibility_sha256": correction[
                "before_responsibility_sha256"],
            "after_responsibility_sha256": correction[
                "after_responsibility_sha256"],
            "evidence_sha256": correction.get("evidence_sha256"),
            "allowed_field_diff": correction["allowed_field_diff"],
            "reason": correction["reason"],
        }
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
                "bounce_baseline": amendment_bounce_baseline(item),
                "expected_contract_sha256": _digest(
                    _dump_contract(node.contract) if node.contract else None),
            }
            if stage == "review":
                entry["expected_review_subject"] = stage_recovery_subject(node, item)
            if stage == "authoring":
                entry["expected_review_generation"] = (
                    _authoring_review_generation(amendment_id, node_id)
                )
            if stage == "merging":
                entry["sync_contract"] = node_id in responsibility_merge_sync_nodes
            entries[node_id] = entry
    return {
        "schema": APPLY_LEDGER_SCHEMA,
        "amendment_id": amendment_id,
        "amendment_file": amendment_file,
        "nodes": entries,
    }


def _authoring_review_generation(amendment_id: str, node_id: str) -> str:
    return "amendment-" + _digest({
        "amendment_id": amendment_id,
        "node_id": node_id,
    })[:24]


def _classify_apply_entry(
    entry: dict[str, Any], current: dict[str, Any], *, baseline: dict | None = None,
) -> str:
    return classify_stage_recovery_observation(
        entry["stage"],
        entry.get("baseline") or {} if baseline is None else baseline,
        current,
        expected_contract_sha256=entry.get("expected_contract_sha256") or "",
        expected_review_subject=entry.get("expected_review_subject"),
        expected_review_generation=entry.get("expected_review_generation"),
        expected_bounce_baseline=entry.get("bounce_baseline"),
    )


def authoring_recovery_node_ids(
    manifest: Manifest,
    amendment: dict[str, Any],
    store: Any,
) -> list[str]:
    """Return nodes whose current authoring projection may be rewritten."""
    if manifest.meta.get("last_amendment_id") != amendment.get("amendment_id"):
        minimal = (amendment.get("analysis") or {}).get("minimal_rerun") or {}
        return list(minimal.get("authoring", []))
    ledger = manifest.meta.get("amendment_apply")
    entries = ledger.get("nodes") if isinstance(ledger, dict) else None
    if not isinstance(entries, dict):
        return []
    selected = []
    for node_id, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("stage") != "authoring":
            continue
        if entry.get("state") not in _COMPLETE_APPLY_STATES:
            selected.append(node_id)
            continue
        if entry.get("state") != "synced":
            continue
        node = manifest.nodes.get(node_id)
        if node is None or not node.work_item_id:
            continue
        current = recovery_control_snapshot(
            observe_recovery_control(store, node.work_item_id))
        observed = entry.get("observed") or {}
        if (
            _classify_apply_entry(entry, observed) != "reached"
            or _classify_apply_entry(entry, current) != "reached"
        ):
            selected.append(node_id)
    return selected


def _save_ledger(manifest: Manifest, manifest_path: str, ledger: dict[str, Any]) -> None:
    manifest.meta["amendment_apply"] = ledger
    save_manifest(manifest, manifest_path)


def _legacy_authoring_projection_is_repairable(current: dict[str, Any]) -> bool:
    """Return whether a missing-generation legacy projection is still authoring."""
    return (
        current.get("phase") == TaskPhase.AUTHORING.value
        and current.get("status") in {
            WorkItemStatus.TODO.value,
            WorkItemStatus.IN_PROGRESS.value,
            WorkItemStatus.BLOCKED.value,
        }
        and current.get("review_generation") in {None, ""}
        and current.get("review_ledger_generation") in {None, ""}
        and not current.get("delivery_identity_pending")
    )


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
    failures = []
    for node_id, entry in ledger.get("nodes", {}).items():
        state = entry.get("state")
        persisted_synced = state == "synced"
        started_repair = False
        if state == "repairing" and not isinstance(
            entry.get("attempt_baseline"), dict
        ):
            failures.append(
                f"node {node_id}: repairing entry is missing attempt_baseline; "
                "refusing to infer a causal boundary from current Store facts")
            continue
        missing_authoring_generation = (
            entry.get("stage") == "authoring"
            and not entry.get("expected_review_generation")
        )
        legacy_synced_authoring = (
            entry.get("stage") == "authoring"
            and (
                state == "repairing"
                or (state == "synced" and missing_authoring_generation)
            )
        )
        if missing_authoring_generation:
            entry["expected_review_generation"] = _authoring_review_generation(
                str(ledger.get("amendment_id") or ""), node_id)
            if legacy_synced_authoring:
                entry["state"] = "repairing"
                state = "repairing"
                started_repair = True
        if state == "observed_progress" or (
            state == "synced"
            and entry.get("stage") == "historical_contract_correction"
        ):
            summary["already_complete"].append(node_id)
            continue
        node = manifest.nodes.get(node_id)
        if node is None or not node.work_item_id:
            entry["state"] = "synced"
            entry["reason"] = "no existing work item side effect"
            _save_ledger(manifest, manifest_path, ledger)
            summary["synced"].append(node_id)
            continue
        item = observe_recovery_control(store, node.work_item_id)
        current = recovery_control_snapshot(item)
        observation = _classify_apply_entry(entry, current)
        if observation == "reached":
            if state == "synced":
                entry_observation = _classify_apply_entry(
                    entry, entry.get("observed") or {})
                if entry_observation == "reached":
                    summary["already_complete"].append(node_id)
                    continue
            entry["state"] = "synced"
            entry["observed"] = current
            _save_ledger(manifest, manifest_path, ledger)
            summary["synced"].append(node_id)
            continue
        if state == "synced" and entry.get("stage") == "authoring":
            entry["state"] = "repairing"
            state = "repairing"
            legacy_synced_authoring = True
            started_repair = True
        attempt_baseline = entry.get("attempt_baseline")
        if started_repair:
            entry["attempt_baseline"] = current
            attempt_baseline = current
            _save_ledger(manifest, manifest_path, ledger)
        if state == "repairing" and isinstance(attempt_baseline, dict):
            if current != attempt_baseline:
                attempt_observation = _classify_apply_entry(
                    entry, current, baseline=attempt_baseline)
                if attempt_observation == "progressed":
                    entry["state"] = "observed_progress"
                    entry["observed"] = current
                    entry["reason"] = (
                        "work item advanced after authoring recovery began; "
                        "preserved to prevent rollback"
                    )
                    _save_ledger(manifest, manifest_path, ledger)
                    failures.append(
                        f"node {node_id}: authoring recovery observed progress; "
                        "preserved Store facts and stopped to prevent rollback")
                    summary["observed_progress"].append(node_id)
                    continue
        if observation == "progressed" and not (
            legacy_synced_authoring
            and _legacy_authoring_projection_is_repairable(current)
        ):
            entry["state"] = "observed_progress"
            entry["observed"] = current
            entry["reason"] = (
                "work item changed after definition apply; skipped to prevent rollback"
            )
            _save_ledger(manifest, manifest_path, ledger)
            summary["observed_progress"].append(node_id)
            failures.append(
                f"node {node_id}: recovery observed progress; preserved Store "
                "facts and stopped to prevent rollback")
            continue
        entry["state"] = (
            "repairing" if legacy_synced_authoring else "syncing")
        if persisted_synced and entry["state"] == "syncing":
            entry["attempt_baseline"] = current
        elif not isinstance(entry.get("attempt_baseline"), dict):
            entry["attempt_baseline"] = current
        _save_ledger(manifest, manifest_path, ledger)
        prepare_stage_recovery(
            node,
            store,
            entry["stage"],
            expected_review_subject=entry.get("expected_review_subject"),
            expected_review_generation=entry.get(
                "expected_review_generation"),
            expected_bounce_baseline=entry.get("bounce_baseline"),
            sync_contract=entry.get(
                "sync_contract", entry["stage"] != "merging"),
        )
        observed = recovery_control_snapshot(
            observe_recovery_control(store, node.work_item_id))
        after = _classify_apply_entry(
            entry, observed, baseline=entry.get("attempt_baseline") or {})
        entry["observed"] = observed
        if after == "reached":
            entry["state"] = "synced"
            _save_ledger(manifest, manifest_path, ledger)
            summary["synced"].append(node_id)
            continue
        if after == "progressed":
            entry["state"] = "observed_progress"
            entry["reason"] = (
                "work item advanced during recovery; preserved to prevent rollback"
            )
            _save_ledger(manifest, manifest_path, ledger)
            failures.append(
                f"node {node_id}: recovery observed progress; preserved Store "
                "facts and stopped to prevent rollback")
            summary["observed_progress"].append(node_id)
            continue
        _save_ledger(manifest, manifest_path, ledger)
        failures.append(
            f"node {node_id}: {entry['stage']} recovery did not reach its target; "
            "repeat the same amendment accept command")
    if failures:
        raise ValidationError("; ".join(failures))
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
    *,
    amendment_file: str | None = None,
    acceptance: Any = None,
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
        (amendment.get("analysis") or {}).get("historical_contract_corrections") or [],
        (amendment.get("base") or {}).get("evidence_sha256") or {},
    )
    if amendment.get("amendment_id") != expected_id:
        raise ValidationError(
            "Amendment identity does not match its reviewed proposal and analysis")

    current = load_manifest(manifest_path)
    already_applied = current.meta.get("last_amendment_id") == amendment.get("amendment_id")
    runtime_rebased = False
    if not already_applied:
        runtime_rebased = _verify_base(current, amendment)
        expected_acceptance = (amendment.get("base") or {}).get(
            "acceptance_sha256")
        if expected_acceptance and _acceptance_digest(acceptance) != expected_acceptance:
            raise ValidationError(ui(
                "The authoritative acceptance document changed after amendment review. "
                "Generate and review a new amendment.",
                "amendment 评审后权威 acceptance 文档已变化；请重新生成并评审 amendment。"))
        errors = validate_proposal(
            current, amendment, agent_pool, acceptance=acceptance)
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
        corrections = _historical_contract_corrections(
            current, amendment,
            (amendment.get("base") or {}).get("evidence_sha256") or {})
        if corrections != (amendment.get("analysis") or {}).get(
                "historical_contract_corrections", []):
            raise ValidationError(
                "Historical contract correction audit changed after amendment review. "
                "Generate and review a new amendment.")
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
            amended, amendment["amendment_id"], minimal, corrections, store,
            amendment_file,
            {
                str(operation.get("node"))
                for operation in amendment.get("operations") or []
                if (
                    _is_responsibility_operation(operation)
                    and operation.get("resume_stage") == "merging"
                )
            },
        )
        save_manifest(amended, manifest_path)
        current = amended
    else:
        runtime_rebased = True
        minimal = amendment.get("analysis", {}).get("minimal_rerun") or {}
        ledger = current.meta.get("amendment_apply") or {}
        if ledger.get("amendment_id") != amendment.get("amendment_id"):
            raise ValidationError(
                "Manifest amendment apply ledger does not match the accepted amendment")
        if amendment_file and ledger.get("amendment_file") != amendment_file:
            ledger["amendment_file"] = amendment_file
            _save_ledger(current, manifest_path, ledger)

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

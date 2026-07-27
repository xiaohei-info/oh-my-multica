"""Acceptance responsibility semantics shared by lint, evidence, and review."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _value(contract: Any, name: str, default):
    if isinstance(contract, dict):
        return contract.get(name, default)
    return getattr(contract, name, default) if contract is not None else default


def full_claims(contract: Any) -> list[str]:
    explicit = _value(contract, "acceptance_claims", []) or []
    if explicit:
        return list(explicit)
    return list(_value(contract, "acceptance", []) or [])


def contributions(contract: Any) -> list[dict]:
    values = _value(contract, "acceptance_contributions", []) or []
    return [value for value in values if isinstance(value, dict)]


def trace_refs(contract: Any) -> list[str]:
    return list(_value(contract, "acceptance_refs", []) or [])


def uses_explicit_responsibility(contract: Any) -> bool:
    return bool(
        (_value(contract, "acceptance_claims", []) or [])
        or (_value(contract, "acceptance_contributions", []) or [])
        or (_value(contract, "acceptance_refs", []) or [])
    )


def evidence_targets(contract: Any) -> list[str]:
    targets = [
        value for value in full_claims(contract)
        if isinstance(value, str) and value.strip()
    ]
    for contribution in contributions(contract):
        targets.extend(contribution.get("action_ids", []) or [])
    return list(dict.fromkeys(targets))


def _matrix_row(rows: dict[str, dict], flow_id: str) -> dict:
    return rows.setdefault(flow_id, {
        "flow_id": flow_id,
        "full_claim_owners": [],
        "action_contributors": defaultdict(list),
        "trace_nodes": [],
    })


def responsibility_matrix(manifest: Any) -> list[dict]:
    rows: dict[str, dict] = {}
    for node_id, node in manifest.nodes.items():
        contract = getattr(node, "contract", None)
        for flow_id in full_claims(contract):
            if isinstance(flow_id, str) and flow_id.strip():
                _matrix_row(rows, flow_id)["full_claim_owners"].append(node_id)
        for contribution in contributions(contract):
            flow_id = contribution.get("flow_id")
            if not isinstance(flow_id, str) or not flow_id:
                continue
            row = _matrix_row(rows, flow_id)
            for action_id in contribution.get("action_ids", []) or []:
                if isinstance(action_id, str) and action_id:
                    row["action_contributors"][action_id].append(node_id)
        for flow_id in trace_refs(contract):
            if isinstance(flow_id, str) and flow_id.strip():
                _matrix_row(rows, flow_id)["trace_nodes"].append(node_id)
    return [{
        "flow_id": flow_id,
        "full_claim_owners": sorted(set(row["full_claim_owners"])),
        "action_contributors": {
            action_id: sorted(set(node_ids))
            for action_id, node_ids in sorted(row["action_contributors"].items())
        },
        "trace_nodes": sorted(set(row["trace_nodes"])),
    } for flow_id, row in sorted(rows.items())]


def contract_shape_errors(node: Any) -> list[str]:
    contract = getattr(node, "contract", None)
    if contract is None:
        return []
    errors = []
    legacy = _value(contract, "acceptance", []) or []
    explicit = (
        (_value(contract, "acceptance_claims", []) or [])
        or (_value(contract, "acceptance_contributions", []) or [])
        or (_value(contract, "acceptance_refs", []) or [])
    )
    if legacy and explicit:
        errors.append(
            f"node {node.id}: legacy contract.acceptance cannot be mixed with "
            "acceptance_claims, acceptance_contributions, or acceptance_refs; "
            "migrate the full-flow claims to acceptance_claims explicitly")

    for field in ("acceptance", "acceptance_claims", "acceptance_refs"):
        values = _value(contract, field, []) or []
        if not isinstance(values, list):
            errors.append(f"node {node.id}: contract.{field} must be a list")
            continue
        for value in values:
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    f"node {node.id}: contract.{field} entries must be non-empty strings")

    for index, contribution in enumerate(
            _value(contract, "acceptance_contributions", []) or []):
        prefix = f"node {node.id}: contract.acceptance_contributions[{index}]"
        if not isinstance(contribution, dict):
            errors.append(f"{prefix} must be an object")
            continue
        flow_id = contribution.get("flow_id")
        if not isinstance(flow_id, str) or not flow_id.strip():
            errors.append(f"{prefix}.flow_id must be a non-empty string")
        action_ids = contribution.get("action_ids")
        if not isinstance(action_ids, list) or not action_ids:
            errors.append(f"{prefix}.action_ids must be non-empty")
            continue
        for action_id in action_ids:
            if not isinstance(action_id, str) or not action_id.strip():
                errors.append(f"{prefix}.action_ids entries must be non-empty strings")
    return errors


def matrix_errors(manifest: Any, acceptance_doc: Any) -> list[str]:
    """Validate exact owners and action closure without node-name heuristics."""
    errors: list[str] = []
    flow_actions = acceptance_doc.action_ids_by_flow
    rows = {row["flow_id"]: row for row in responsibility_matrix(manifest)}

    for node in manifest.nodes.values():
        contract = getattr(node, "contract", None)
        for flow_id in full_claims(contract) + trace_refs(contract):
            if not isinstance(flow_id, str) or not flow_id.strip():
                continue
            if flow_id not in flow_actions:
                errors.append(
                    f"node {node.id}: acceptance responsibility references unknown flow '{flow_id}'")
        node_contributions: dict[str, set[str]] = defaultdict(set)
        for contribution in contributions(contract):
            flow_id = contribution.get("flow_id")
            if not isinstance(flow_id, str) or not flow_id.strip():
                continue
            if flow_id not in flow_actions:
                errors.append(
                    f"node {node.id}: acceptance contribution references unknown flow '{flow_id}'")
                continue
            known_actions = set(flow_actions[flow_id])
            for action_id in contribution.get("action_ids", []) or []:
                if not isinstance(action_id, str) or not action_id.strip():
                    continue
                if action_id not in known_actions:
                    errors.append(
                        f"node {node.id}: acceptance contribution references unknown action "
                        f"'{action_id}' in flow {flow_id}")
                else:
                    node_contributions[flow_id].add(action_id)
        for flow_id in full_claims(contract):
            if not isinstance(flow_id, str) or flow_id not in flow_actions:
                continue
            missing = set(flow_actions.get(flow_id, [])) - node_contributions[flow_id]
            if missing:
                errors.append(
                    f"node {node.id}: full claim {flow_id} does not cover every action; "
                    f"missing: {', '.join(sorted(missing))}")

    for flow_id, action_ids in flow_actions.items():
        row = rows.get(flow_id, {})
        owners = row.get("full_claim_owners", [])
        if not owners:
            errors.append(f"acceptance flow has no full claim owner: {flow_id}")
        elif len(owners) > 1:
            errors.append(
                f"acceptance flow full claim has multiple owners: {flow_id}: "
                + ", ".join(owners))
        contributors = row.get("action_contributors", {})
        for action_id in action_ids:
            if not contributors.get(action_id):
                errors.append(
                    f"acceptance action has no contribution owner: {flow_id}/{action_id}")
    return errors

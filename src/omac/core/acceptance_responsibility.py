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
        targets.extend(
            action_id for action_id in contribution.get("action_ids", []) or []
            if isinstance(action_id, str) and action_id.strip()
        )
    return list(dict.fromkeys(targets))


def dependency_closure(manifest: Any, node_id: str) -> set[str]:
    """Return every direct or transitive prerequisite of a manifest node."""
    if node_id not in manifest.nodes:
        return set()
    closure: set[str] = set()
    stack = list(manifest.nodes[node_id].blocked_by)
    while stack:
        dependency = stack.pop()
        if dependency in closure or dependency not in manifest.nodes:
            continue
        closure.add(dependency)
        stack.extend(manifest.nodes[dependency].blocked_by)
    return closure


def _matrix_row(rows: dict[str, dict], flow_id: str) -> dict:
    return rows.setdefault(flow_id, {
        "full_claim_owners": [],
        "action_contributors": defaultdict(list),
        "trace_nodes": [],
    })


def _matrix_rows(manifest: Any) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for node_id, node in manifest.nodes.items():
        contract = getattr(node, "contract", None)
        for flow_id in full_claims(contract):
            if isinstance(flow_id, str) and flow_id.strip():
                _matrix_row(rows, flow_id)["full_claim_owners"].append(node_id)
        for contribution in contributions(contract):
            flow_id = contribution.get("flow_id")
            if not isinstance(flow_id, str) or not flow_id.strip():
                continue
            row = _matrix_row(rows, flow_id)
            for action_id in contribution.get("action_ids", []) or []:
                if isinstance(action_id, str) and action_id.strip():
                    row["action_contributors"][action_id].append(node_id)
        for flow_id in trace_refs(contract):
            if isinstance(flow_id, str) and flow_id.strip():
                _matrix_row(rows, flow_id)["trace_nodes"].append(node_id)
    return rows


def responsibility_matrix(manifest: Any, acceptance_doc: Any = None) -> list[dict]:
    """Return a compact global matrix with dependency closure and only gaps.

    The matrix intentionally does not repeat every Action-to-node mapping. Exact
    mappings remain canonical in node contracts; the Reviewer receives counts,
    contributing nodes, dependency closure, and exceptional IDs only.
    """
    rows = _matrix_rows(manifest)
    expected_by_flow = (
        acceptance_doc.business_action_ids_by_flow
        if acceptance_doc is not None else {}
    )
    flow_ids = sorted(set(rows) | set(expected_by_flow))
    matrix = []
    for flow_id in flow_ids:
        row = rows.get(flow_id, {})
        owners = sorted(set(row.get("full_claim_owners", [])))
        action_contributors = row.get("action_contributors", {})
        declared_action_ids = set(action_contributors)
        expected_action_ids = set(
            expected_by_flow.get(flow_id, declared_action_ids))
        missing = sorted(expected_action_ids - declared_action_ids)
        unknown = sorted(declared_action_ids - expected_action_ids)
        valid_action_ids = declared_action_ids & expected_action_ids
        contribution_owners = sorted({
            node_id
            for action_id in valid_action_ids
            for node_id in action_contributors.get(action_id, [])
        })
        closure = dependency_closure(manifest, owners[0]) if len(owners) == 1 else set()
        unreachable = sorted(
            set(contribution_owners) - set(owners) - closure)
        matrix.append({
            "flow_id": flow_id,
            "full_claim_owners": owners,
            "business_action_count": len(expected_action_ids),
            "contributed_business_action_count": len(valid_action_ids),
            "contribution_owners": contribution_owners,
            "full_owner_dependency_closure": sorted(closure),
            "missing_business_action_ids": missing,
            "unknown_business_action_ids": unknown,
            "unreachable_contribution_owners": unreachable,
            "trace_nodes": sorted(set(row.get("trace_nodes", []))),
        })
    return matrix


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
        if len(action_ids) != len(set(
                action_id for action_id in action_ids if isinstance(action_id, str))):
            errors.append(f"{prefix}.action_ids must not contain duplicates")
    return errors


def matrix_errors(manifest: Any, acceptance_doc: Any) -> list[str]:
    """Validate canonical flow ownership and business-Action dependency closure."""
    errors: list[str] = []
    flow_actions = acceptance_doc.business_action_ids_by_flow

    for node in manifest.nodes.values():
        contract = getattr(node, "contract", None)
        for flow_id in full_claims(contract) + trace_refs(contract):
            if not isinstance(flow_id, str) or not flow_id.strip():
                continue
            if flow_id not in flow_actions:
                errors.append(
                    f"node {node.id}: acceptance responsibility references "
                    f"unknown flow '{flow_id}'")
        for contribution in contributions(contract):
            flow_id = contribution.get("flow_id")
            if not isinstance(flow_id, str) or not flow_id.strip():
                continue
            if flow_id not in flow_actions:
                errors.append(
                    f"node {node.id}: acceptance contribution references "
                    f"unknown flow '{flow_id}'")
                continue
            known_actions = set(flow_actions[flow_id])
            for action_id in contribution.get("action_ids", []) or []:
                if not isinstance(action_id, str) or not action_id.strip():
                    continue
                if action_id not in known_actions:
                    errors.append(
                        f"node {node.id}: acceptance contribution references "
                        f"unknown business action '{action_id}' in flow {flow_id}")

    for row in responsibility_matrix(manifest, acceptance_doc):
        flow_id = row["flow_id"]
        owners = row["full_claim_owners"]
        if not owners:
            errors.append(f"acceptance flow has no full claim owner: {flow_id}")
        elif len(owners) > 1:
            errors.append(
                f"acceptance flow full claim has multiple owners: {flow_id}: "
                + ", ".join(owners))
        if row["missing_business_action_ids"]:
            errors.append(
                f"acceptance business action has no contribution owner: {flow_id}/"
                + ", ".join(row["missing_business_action_ids"]))
        if len(owners) == 1 and row["unreachable_contribution_owners"]:
            errors.append(
                f"full claim owner {owners[0]} must depend on all contribution "
                f"owners for {flow_id}; unreachable: "
                + ", ".join(row["unreachable_contribution_owners"]))
    return errors

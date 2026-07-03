# lint.py
import os
from .manifest import Manifest

def _has_cycle(nodes):
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in nodes}
    def dfs(u):
        color[u] = GREY
        for v in nodes[u].blocked_by:
            if v not in nodes:        # 未知引用，交给别的规则报
                continue
            if color[v] == GREY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False
    return any(color[k] == WHITE and dfs(k) for k in nodes)

def _integration_gate_errors(prefix: str, gate, index: int) -> list:
    errs = []
    gate_prefix = f"{prefix}.integration_gates[{index}]"
    if not isinstance(gate, dict):
        return [f"{gate_prefix} must be an object"]

    for field in ("name", "layer", "delivery_goal"):
        if not gate.get(field):
            errs.append(f"{gate_prefix}.{field} is required")

    for field in ("source_of_truth", "covers", "acceptance_refs", "commands"):
        value = gate.get(field)
        if not isinstance(value, list) or not value:
            errs.append(f"{gate_prefix}.{field} must be non-empty")

    metrics = gate.get("required_metrics", {})
    if metrics is not None and not isinstance(metrics, dict):
        errs.append(f"{gate_prefix}.required_metrics must be an object")

    artifacts = gate.get("artifacts", [])
    if artifacts is not None and not isinstance(artifacts, list):
        errs.append(f"{gate_prefix}.artifacts must be a list")

    return errs


def _contract_errors(node) -> list:
    contract = getattr(node, "contract", None)
    if contract is None:
        return []

    errs = []
    prefix = f"node {node.id}: contract"
    if not contract.objective:
        errs.append(f"{prefix}.objective is required")
    if not contract.acceptance:
        errs.append(f"{prefix}.acceptance must be non-empty")
    if not contract.non_goals:
        errs.append(f"{prefix}.non_goals must be non-empty")
    if not contract.verification_commands:
        errs.append(f"{prefix}.verification_commands must be non-empty")
    if not contract.integration_gates:
        errs.append(f"{prefix}.integration_gates must be non-empty")
    else:
        for index, gate in enumerate(contract.integration_gates):
            errs.extend(_integration_gate_errors(prefix, gate, index))
    if not contract.pr_base:
        errs.append(f"{prefix}.pr_base is required")

    coverage_gate = contract.coverage_gate
    if not isinstance(coverage_gate, (int, float)) or isinstance(coverage_gate, bool) or not 0 <= coverage_gate <= 100:
        errs.append(f"{prefix}.coverage_gate must be a 0-100 number")

    for required_path in contract.required_contracts:
        if not os.path.exists(required_path):
            errs.append(f"{prefix}.required_contracts path does not exist: {required_path}")
    return errs


def lint(m: Manifest, pool: set) -> list:
    errs = []
    for n in m.nodes.values():
        if n.worker not in pool:
            errs.append(f"node {n.id}: worker '{n.worker}' not in agent pool")
        for b in n.blocked_by:
            if b not in m.nodes:
                errs.append(f"node {n.id}: blocked_by references unknown node '{b}'")
        if n.reviewer is not None:
            if n.reviewer == n.worker:
                errs.append(f"node {n.id}: reviewer must differ from worker")
            if n.reviewer not in pool:
                errs.append(f"node {n.id}: reviewer '{n.reviewer}' not in agent pool")
        errs.extend(_contract_errors(n))
    if _has_cycle(m.nodes):
        errs.append("manifest DAG has a cycle")
    return errs

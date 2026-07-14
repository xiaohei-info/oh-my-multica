# lint.py
import os
from .manifest import Manifest
from ..i18n import ui

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
    if not contract.source_of_truth:
        errs.append(f"{prefix}.source_of_truth must be non-empty")
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


def lint(m: Manifest, pool: set, *, acceptance=None) -> list:
    """schema 校验 manifest。

    acceptance(AcceptanceDoc|None):有验收文档时,每个节点的 contract.acceptance
    条目须为验收文档 flow.id 之一(锚定,否则提示未锚定)。缺省 None = 不做锚定校验。
    """
    errs = []
    closeout_node = m.meta.get("closeout_node")
    if closeout_node and closeout_node not in m.nodes:
        errs.append(
            f"manifest meta.closeout_node references unknown node '{closeout_node}'")
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
    if acceptance is not None:
        flow_ids = set(getattr(acceptance, "flow_ids", None) or [])
        for n in m.nodes.values():
            contract = getattr(n, "contract", None)
            if not contract:
                continue
            for a in contract.acceptance:
                if a not in flow_ids:
                    errs.append(ui(
                        f"node {n.id}: contract.acceptance '{a}' is not anchored to an acceptance flow",
                        f"node {n.id}: contract.acceptance '{a}' 未锚定验收文档 flow"))
    if _has_cycle(m.nodes):
        errs.append("manifest DAG has a cycle")
    return errs

def lint_increment(increment: Manifest, existing: Manifest, pool: set) -> list:
    """校验增量 fix 节点(§7.6 并入前的 lint 门)。

    检查项:
    - id 与已有节点冲突(冲突则报错,由调用方决定)
    - blocked_by 引用有效(对「已有 + 增量」全集) 
    - worker/reviewer 在 agent 池内
    - 自身 contract 硬门(复用 _contract_errors)
    - 并入后整图不引入环

    注意:不重复检查已有节点的 worker/contract(它们已过门);只检查增量节点
    以及「增量节点依赖的集合」。
    """
    errs = []
    combined_keys = set(existing.nodes) | set(increment.nodes)

    for n in increment.nodes.values():
        if n.id in existing.nodes:
            errs.append(f"node {n.id}: id conflicts with existing node")
        if n.worker not in pool:
            errs.append(f"node {n.id}: worker {n.worker!r} not in agent pool")
        for b in n.blocked_by:
            if b not in combined_keys:
                errs.append(f"node {n.id}: blocked_by references unknown node {b!r}")
        if n.reviewer is not None:
            if n.reviewer == n.worker:
                errs.append(f"node {n.id}: reviewer must differ from worker")
            if n.reviewer not in pool:
                errs.append(f"node {n.id}: reviewer {n.reviewer!r} not in agent pool")
        errs.extend(_contract_errors(n))

    combined_nodes = dict(existing.nodes)
    combined_nodes.update(increment.nodes)
    if _has_cycle(combined_nodes):
        errs.append("increment introduces a cycle in the manifest DAG")

    return errs

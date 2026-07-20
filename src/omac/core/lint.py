# lint.py
import os
from .manifest import Manifest
from ..i18n import ui


def _non_empty_string_list(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(entry, str) and entry.strip() for entry in value)
    )

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
        value = gate.get(field)
        if not isinstance(value, str) or not value.strip():
            errs.append(f"{gate_prefix}.{field} must be a non-empty string")

    for field in ("source_of_truth", "covers", "acceptance_refs", "commands"):
        value = gate.get(field)
        if not _non_empty_string_list(value):
            errs.append(f"{gate_prefix}.{field} must be non-empty strings")

    metrics = gate.get("required_metrics", {})
    if metrics is not None and not isinstance(metrics, dict):
        errs.append(f"{gate_prefix}.required_metrics must be an object")

    artifacts = gate.get("artifacts", [])
    if artifacts is not None and not isinstance(artifacts, list):
        errs.append(f"{gate_prefix}.artifacts must be a list")

    return errs


def contract_errors(node) -> list:
    contract = getattr(node, "contract", None)
    if contract is None:
        return [f"node {node.id}: contract is required"]

    errs = []
    prefix = f"node {node.id}: contract"
    if not contract.objective:
        errs.append(f"{prefix}.objective is required")
    for field in (
        "acceptance", "source_of_truth", "non_goals", "verification_commands",
    ):
        if not _non_empty_string_list(getattr(contract, field, None)):
            errs.append(f"{prefix}.{field} must be non-empty strings")
    if not isinstance(contract.integration_gates, list) or not contract.integration_gates:
        errs.append(f"{prefix}.integration_gates must be non-empty")
    else:
        for index, gate in enumerate(contract.integration_gates):
            errs.extend(_integration_gate_errors(prefix, gate, index))
    if not contract.pr_base:
        errs.append(f"{prefix}.pr_base is required")
    errs.extend(_quality_errors(prefix, contract))

    coverage_gate = contract.coverage_gate
    if not isinstance(coverage_gate, (int, float)) or isinstance(coverage_gate, bool) or not 0 <= coverage_gate <= 100:
        errs.append(f"{prefix}.coverage_gate must be a 0-100 number")

    required_contracts = contract.required_contracts
    if not isinstance(required_contracts, list) or not all(
        isinstance(path, str) and path.strip() for path in required_contracts
    ):
        errs.append(f"{prefix}.required_contracts must be strings")
        required_contracts = []
    for required_path in required_contracts:
        if not os.path.exists(required_path):
            errs.append(f"{prefix}.required_contracts path does not exist: {required_path}")
    return errs


def authoring_runtime_field_errors(manifest: Manifest) -> list:
    """Reject runtime state smuggled into newly authored manifest nodes."""
    errs = []
    for node in manifest.nodes.values():
        if node.status != "todo":
            errs.append(
                f"node {node.id}: runtime field status must be omitted or todo")
        if node.work_item_id is not None:
            errs.append(
                f"node {node.id}: runtime field work_item_id is forbidden in authoring")
        if node.merged:
            errs.append(
                f"node {node.id}: runtime field merged is forbidden in authoring")
        if node.merged_at is not None:
            errs.append(
                f"node {node.id}: runtime field merged_at is forbidden in authoring")
    return errs


def _quality_errors(prefix: str, contract) -> list:
    quality = getattr(contract, "quality", None)
    if quality is None:
        return [f"{prefix}.quality is required"]

    errs = []
    outcomes = quality.required_outcomes
    tests = quality.business_tests
    if not isinstance(outcomes, list) or not outcomes:
        errs.append(f"{prefix}.quality.required_outcomes must be non-empty")
        outcomes = []
    if not isinstance(tests, list) or not tests:
        errs.append(f"{prefix}.quality.business_tests must be non-empty")
        tests = []
    if quality.runtime_data_policy != "real-or-error":
        errs.append(f"{prefix}.quality.runtime_data_policy must be real-or-error")

    outcome_ids = set()
    for index, outcome in enumerate(outcomes):
        item_prefix = f"{prefix}.quality.required_outcomes[{index}]"
        if not isinstance(outcome, dict):
            errs.append(f"{item_prefix} must be an object")
            continue
        outcome_id = outcome.get("id")
        if not isinstance(outcome_id, str) or not outcome_id.strip():
            errs.append(f"{item_prefix}.id is required")
        elif outcome_id in outcome_ids:
            errs.append(f"{prefix}.quality duplicate outcome id: {outcome_id}")
        else:
            outcome_ids.add(outcome_id)
        source_ref = outcome.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref.strip():
            errs.append(f"{item_prefix}.source_ref is required")
        elif not source_ref.startswith("acceptance#") or "." not in source_ref.removeprefix("acceptance#"):
            errs.append(f"{item_prefix}.source_ref must use acceptance#flow.action")

    declared_commands = {
        command for command in contract.verification_commands
        if isinstance(command, str) and command.strip()
    } if isinstance(contract.verification_commands, list) else set()
    for gate in contract.integration_gates:
        if isinstance(gate, dict):
            commands = gate.get("commands")
            if isinstance(commands, list):
                declared_commands.update(
                    command for command in commands
                    if isinstance(command, str) and command.strip()
                )

    covered_outcomes = set()
    test_ids = set()
    for index, business_test in enumerate(tests):
        item_prefix = f"{prefix}.quality.business_tests[{index}]"
        if not isinstance(business_test, dict):
            errs.append(f"{item_prefix} must be an object")
            continue
        test_id = business_test.get("id")
        if not isinstance(test_id, str) or not test_id.strip():
            errs.append(f"{item_prefix}.id is required")
        elif test_id in test_ids:
            errs.append(f"{prefix}.quality duplicate business test id: {test_id}")
        else:
            test_ids.add(test_id)
        refs = business_test.get("outcome_refs")
        if not _non_empty_string_list(refs):
            errs.append(f"{item_prefix}.outcome_refs must be non-empty strings")
        else:
            for outcome_ref in refs:
                if outcome_ref not in outcome_ids:
                    errs.append(f"{item_prefix} references unknown outcome: {outcome_ref}")
                else:
                    covered_outcomes.add(outcome_ref)
        command = business_test.get("command")
        if not isinstance(command, str) or not command.strip():
            errs.append(f"{item_prefix}.command is required")
        elif command not in declared_commands:
            errs.append(f"{item_prefix}.command is not declared: {command}")
        if business_test.get("level") not in {"integration", "e2e"}:
            errs.append(f"{item_prefix}.level must be integration|e2e")
        dependencies = business_test.get("real_dependencies")
        if not _non_empty_string_list(dependencies):
            errs.append(f"{item_prefix}.real_dependencies must be non-empty strings")
        if not isinstance(business_test.get("must_fail_on_base"), bool):
            errs.append(f"{item_prefix}.must_fail_on_base must be boolean")

    for outcome_id in sorted(outcome_ids - covered_outcomes):
        errs.append(f"{prefix}.quality required outcome has no business test: {outcome_id}")
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
        if n.reviewer is None:
            errs.append(f"node {n.id}: reviewer is required")
        elif n.reviewer == n.worker:
            errs.append(f"node {n.id}: reviewer must differ from worker")
        elif n.reviewer not in pool:
            errs.append(f"node {n.id}: reviewer '{n.reviewer}' not in agent pool")
        errs.extend(contract_errors(n))
    if acceptance is not None:
        flow_ids = set(getattr(acceptance, "flow_ids", None) or [])
        action_ids = set(getattr(acceptance, "action_ids", None) or [])
        for n in m.nodes.values():
            contract = getattr(n, "contract", None)
            if not contract:
                continue
            for a in contract.acceptance:
                if a not in flow_ids:
                    errs.append(ui(
                        f"node {n.id}: contract.acceptance '{a}' is not anchored to an acceptance flow",
                        f"node {n.id}: contract.acceptance '{a}' 未锚定验收文档 flow"))
            quality = getattr(contract, "quality", None)
            if quality is None:
                continue
            for outcome in quality.required_outcomes:
                if not isinstance(outcome, dict):
                    continue
                source_ref = outcome.get("source_ref")
                if not isinstance(source_ref, str) or not source_ref.startswith("acceptance#"):
                    continue
                action_ref = source_ref.removeprefix("acceptance#")
                if action_ref not in action_ids:
                    errs.append(
                        f"node {n.id}: quality outcome source_ref is not anchored "
                        f"to an acceptance action: {source_ref}"
                    )
                if not any(
                    action_ref.startswith(f"{flow_id}.")
                    for flow_id in contract.acceptance
                ):
                    errs.append(
                        f"node {n.id}: quality outcome source_ref flow is not "
                        f"declared in contract.acceptance: {source_ref}"
                    )
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
    errs = authoring_runtime_field_errors(increment)
    combined_keys = set(existing.nodes) | set(increment.nodes)

    for n in increment.nodes.values():
        if n.id in existing.nodes:
            errs.append(f"node {n.id}: id conflicts with existing node")
        if n.worker not in pool:
            errs.append(f"node {n.id}: worker {n.worker!r} not in agent pool")
        for b in n.blocked_by:
            if b not in combined_keys:
                errs.append(f"node {n.id}: blocked_by references unknown node {b!r}")
        if n.reviewer is None:
            errs.append(f"node {n.id}: reviewer is required")
        elif n.reviewer == n.worker:
            errs.append(f"node {n.id}: reviewer must differ from worker")
        elif n.reviewer not in pool:
            errs.append(f"node {n.id}: reviewer {n.reviewer!r} not in agent pool")
        errs.extend(contract_errors(n))

    combined_nodes = dict(existing.nodes)
    combined_nodes.update(increment.nodes)
    if _has_cycle(combined_nodes):
        errs.append("increment introduces a cycle in the manifest DAG")

    return errs

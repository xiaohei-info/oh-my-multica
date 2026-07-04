"""Structured evidence validators —— 左移门(worker submit)与权威门(结果回收)共用同一套 schema。

三类新证据字段(均左移门强制):
  1. verification.env_setup        contract 声明 integration_gates 或 env 依赖时必填
  2. review_report.review_goals    review 阶段必填
  3. acceptance_results            final-acceptance 必填,逐项按 id 对齐验收文档条目
"""

from .acceptance import AcceptanceDoc, load_acceptance_doc

REVIEW_APPROVE = {"pass", "pass-with-nits"}

ACCEPTANCE_STATUS = {"pass", "fail"}


def _commands_by_text(commands):
    if not isinstance(commands, list) or not commands:
        return None
    return {
        command.get("cmd"): command
        for command in commands
        if isinstance(command, dict) and command.get("cmd")
    }


def _validate_expected_commands(command_by_text, expected_commands, *, missing_prefix, failed_prefix):
    errors = []
    for expected_cmd in expected_commands:
        actual = command_by_text.get(expected_cmd) if command_by_text is not None else None
        if actual is None:
            errors.append(f"{missing_prefix}: {expected_cmd}")
            continue
        if actual.get("exit_code") != 0:
            errors.append(f"{failed_prefix}: {expected_cmd}")
    return errors


def _gate_by_name(gates):
    if not isinstance(gates, list) or not gates:
        return None
    return {
        gate.get("name"): gate
        for gate in gates
        if isinstance(gate, dict) and gate.get("name")
    }


def _metric_satisfies(actual, expected) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual >= expected
    return actual == expected


def _validate_integration_gate_evidence(expected_gate, actual_gate, *, prefix):
    errors = []
    gate_name = expected_gate.get("name")
    if actual_gate is None:
        return [f"{prefix} missing integration gate: {gate_name}"]

    command_by_text = _commands_by_text(actual_gate.get("commands"))
    if command_by_text is None:
        errors.append(f"{prefix} integration gate commands must be non-empty: {gate_name}")
    else:
        errors.extend(
            _validate_expected_commands(
                command_by_text,
                expected_gate.get("commands", []),
                missing_prefix=f"{prefix} missing integration command for {gate_name}",
                failed_prefix=f"{prefix} integration command failed for {gate_name}",
            )
        )

    actual_metrics = actual_gate.get("metrics", {})
    if not isinstance(actual_metrics, dict):
        errors.append(f"{prefix} integration gate metrics must be an object: {gate_name}")
        actual_metrics = {}
    for metric, expected_value in expected_gate.get("required_metrics", {}).items():
        if metric not in actual_metrics:
            errors.append(f"{prefix} missing integration metric for {gate_name}: {metric}")
        elif not _metric_satisfies(actual_metrics.get(metric), expected_value):
            errors.append(f"{prefix} integration metric below gate for {gate_name}: {metric}")

    expected_artifacts = expected_gate.get("artifacts", [])
    if expected_artifacts:
        actual_artifacts = actual_gate.get("artifacts")
        if not isinstance(actual_artifacts, list):
            actual_artifacts = []
        for artifact in expected_artifacts:
            if artifact not in actual_artifacts:
                errors.append(f"{prefix} missing integration artifact for {gate_name}: {artifact}")

    for field in ("source_of_truth", "delivery_goal"):
        if actual_gate.get(field) != expected_gate.get(field):
            errors.append(f"{prefix} integration gate {field} must match contract for {gate_name}")

    return errors


def _has_pr_url(artifacts) -> bool:
    if not isinstance(artifacts, dict):
        return False
    return bool(artifacts.get("pr_url") or artifacts.get("pr"))


def _requires_env_setup(contract) -> bool:
    """contract 声明 integration_gates(或后续 env 依赖标记)时,env_setup 必填。"""
    if not contract:
        return False
    return bool(getattr(contract, "integration_gates", None))


def validate_worker_evidence(node, item) -> list:
    """Return gate failure messages for worker artifacts + verification."""
    errors = []
    contract = getattr(node, "contract", None)

    if not _has_pr_url(getattr(item, "artifacts", None)):
        errors.append("artifacts.pr_url is required")

    if contract is None:
        return errors

    verification = getattr(item, "verification", None)
    if not isinstance(verification, dict):
        errors.append("verification is required")
        return errors

    command_by_text = _commands_by_text(verification.get("commands"))
    if command_by_text is None:
        errors.append("verification.commands must be non-empty")
    else:
        errors.extend(
            _validate_expected_commands(
                command_by_text,
                contract.verification_commands,
                missing_prefix="verification missing command",
                failed_prefix="verification command failed",
            )
        )

    integration_gate_by_name = _gate_by_name(verification.get("integration_gates"))
    if integration_gate_by_name is None:
        errors.append("verification.integration_gates must be non-empty")
    else:
        for expected_gate in contract.integration_gates:
            errors.extend(
                _validate_integration_gate_evidence(
                    expected_gate,
                    integration_gate_by_name.get(expected_gate.get("name")),
                    prefix="verification",
                )
            )

    if _requires_env_setup(contract):
        env_setup = verification.get("env_setup")
        if not isinstance(env_setup, list) or not env_setup:
            errors.append("verification.env_setup is required when contract declares integration_gates")
        else:
            for step in env_setup:
                if not isinstance(step, str) or not step.strip():
                    errors.append("verification.env_setup entries must be non-empty strings")

    if verification.get("pr_base") != contract.pr_base:
        errors.append("verification.pr_base must match contract.pr_base")

    coverage = verification.get("coverage")
    if not isinstance(coverage, (int, float)) or isinstance(coverage, bool):
        errors.append("verification.coverage must be numeric")
    elif coverage < contract.coverage_gate:
        errors.append(
            f"verification.coverage {coverage} below gate {contract.coverage_gate}"
        )

    return errors


def validate_review_evidence(node, item) -> list:
    """Return gate failure messages for structured reviewer verdict/report."""
    errors = []
    verdict = getattr(item, "review_verdict", None)
    report = getattr(item, "review_report", None)
    contract = getattr(node, "contract", None)

    if verdict not in REVIEW_APPROVE:
        return [f"review_verdict {verdict!r} is not approvable"]

    if contract is None:
        return []

    if not isinstance(report, dict):
        return ["review_report is required"]

    review_goals = report.get("review_goals")
    if not isinstance(review_goals, list) or not review_goals:
        errors.append("review_report.review_goals must be non-empty")
    else:
        for goal in review_goals:
            if not isinstance(goal, str) or not goal.strip():
                errors.append("review_report.review_goals entries must be non-empty strings")

    review_flags = ["diff_reviewed", "tests_rerun", "coverage_checked"]
    if contract.integration_gates:
        review_flags.append("integration_tests_rerun")

    for flag in review_flags:
        if report.get(flag) is not True:
            errors.append(f"review_report.{flag} must be true")

    blockers = report.get("blockers", [])
    if blockers:
        errors.append("review_report.blockers must be empty for pass verdicts")

    mappings = report.get("acceptance_mapping")
    if not isinstance(mappings, list) or not mappings:
        errors.append("review_report.acceptance_mapping must be non-empty")
        return errors

    mapped_acceptance = {
        mapping.get("acceptance")
        for mapping in mappings
        if isinstance(mapping, dict) and mapping.get("status") == "pass"
    }
    if contract is not None:
        for acceptance in contract.acceptance:
            if acceptance not in mapped_acceptance:
                errors.append(f"review_report missing acceptance mapping: {acceptance}")

    if contract.integration_gates:
        integration_mappings = report.get("integration_gate_mapping")
        if not isinstance(integration_mappings, list) or not integration_mappings:
            errors.append("review_report.integration_gate_mapping must be non-empty")
        else:
            mapping_by_gate = {
                mapping.get("gate"): mapping
                for mapping in integration_mappings
                if isinstance(mapping, dict) and mapping.get("status") == "pass"
            }
            for expected_gate in contract.integration_gates:
                errors.extend(
                    _validate_integration_gate_evidence(
                        expected_gate,
                        mapping_by_gate.get(expected_gate.get("name")),
                        prefix="review_report",
                    )
                )

    return errors


def validate_acceptance_results(acceptance_doc, results) -> list:
    """逐项校验总控验收结果:results 必须按 id 对齐覆盖验收文档全部条目。

    每项 status ∈ pass|fail;fail 须有 notes;漏项/多项均报错。
    acceptance_doc 可为 AcceptanceDoc 或原始 dict(内部 load)。
    """
    errors = []

    if isinstance(acceptance_doc, dict):
        try:
            acceptance_doc = load_acceptance_doc(acceptance_doc)
        except ValueError as exc:
            return [f"acceptance doc invalid: {exc}"]
    if not isinstance(acceptance_doc, AcceptanceDoc):
        return [f"acceptance doc must be an AcceptanceDoc or dict, got {type(acceptance_doc).__name__}"]

    expected_ids = acceptance_doc.flow_ids

    if not isinstance(results, list):
        return ["acceptance_results must be a list"]

    result_by_id = {}
    for entry in results:
        if not isinstance(entry, dict):
            errors.append("each acceptance_result must be an object")
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            errors.append("acceptance_result.id is required")
            continue
        if entry_id in result_by_id:
            errors.append(f"duplicate acceptance_result id: {entry_id}")
            continue
        result_by_id[entry_id] = entry

    for flow_id in expected_ids:
        if flow_id not in result_by_id:
            errors.append(f"acceptance_results missing acceptance flow: {flow_id}")

    for entry_id, entry in result_by_id.items():
        if entry_id not in expected_ids:
            errors.append(f"acceptance_results has extra flow not in acceptance doc: {entry_id}")
            continue
        status = entry.get("status")
        if status not in ACCEPTANCE_STATUS:
            errors.append(f"acceptance_result {entry_id} status must be pass|fail, got {status!r}")
            continue
        if status == "fail":
            notes = entry.get("notes")
            if not isinstance(notes, str) or not notes.strip():
                errors.append(f"acceptance_result {entry_id} failed but has no notes")

    return errors

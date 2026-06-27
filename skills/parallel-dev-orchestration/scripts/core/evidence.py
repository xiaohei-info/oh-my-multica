"""Structured evidence validators used by DAG harvest."""

REVIEW_APPROVE = {"pass", "pass-with-nits"}


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

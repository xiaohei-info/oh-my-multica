"""Structured evidence validators used by DAG harvest."""

REVIEW_APPROVE = {"pass", "pass-with-nits"}


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

    commands = verification.get("commands")
    if not isinstance(commands, list) or not commands:
        errors.append("verification.commands must be non-empty")
        command_by_text = {}
    else:
        command_by_text = {
            command.get("cmd"): command
            for command in commands
            if isinstance(command, dict) and command.get("cmd")
        }

    for expected_cmd in contract.verification_commands:
        actual = command_by_text.get(expected_cmd)
        if actual is None:
            errors.append(f"verification missing command: {expected_cmd}")
            continue
        if actual.get("exit_code") != 0:
            errors.append(f"verification command failed: {expected_cmd}")

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

    for flag in ("diff_reviewed", "tests_rerun", "coverage_checked"):
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

    return errors

"""Structured evidence validators —— 左移门(worker submit)与权威门(结果回收)共用同一套 schema。

三类新证据字段(均左移门强制):
  1. verification.env_setup        contract 声明 integration_gates 或 env 依赖时必填
  2. review_report.review_goals    review 阶段必填
  3. acceptance_results            final-acceptance 必填,逐项按 id 对齐验收文档条目
"""

from .acceptance import AcceptanceDoc, load_acceptance_doc

REVIEW_APPROVE = {"pass", "pass-with-nits"}
REVIEW_VERDICTS = REVIEW_APPROVE | {"reject"}

ACCEPTANCE_STATUS = {"pass", "fail"}


def _commands_by_text(commands, *, prefix):
    errors = []
    if not isinstance(commands, list) or not commands:
        return None, errors
    result = {}
    for index, command in enumerate(commands):
        item_prefix = f"{prefix}[{index}]"
        if not isinstance(command, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        cmd = command.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            errors.append(f"{item_prefix}.command cmd must be a non-empty string")
            continue
        if cmd in result:
            errors.append(f"{prefix} duplicate command: {cmd}")
        result[cmd] = command
    return result, errors


def _validate_expected_commands(command_by_text, expected_commands, *, missing_prefix, failed_prefix):
    errors = []
    for expected_cmd in expected_commands:
        if not isinstance(expected_cmd, str) or not expected_cmd.strip():
            errors.append(f"{missing_prefix}: contract command must be a non-empty string")
            continue
        actual = command_by_text.get(expected_cmd) if command_by_text is not None else None
        if actual is None:
            errors.append(f"{missing_prefix}: {expected_cmd}")
            continue
        if actual.get("exit_code") != 0:
            errors.append(f"{failed_prefix}: {expected_cmd}")
    return errors


def _gate_by_name(gates, *, expected_names: set[str], prefix: str):
    errors = []
    if not isinstance(gates, list) or not gates:
        return None, errors
    result = {}
    for index, gate in enumerate(gates):
        item_prefix = f"{prefix}[{index}]"
        if not isinstance(gate, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        name = gate.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{item_prefix}.name must be a non-empty string")
            continue
        canonical_name = name.strip()
        has_surrounding_whitespace = name != canonical_name
        if has_surrounding_whitespace:
            errors.append(
                f"{item_prefix}.name must not have surrounding whitespace")
        if canonical_name in result:
            errors.append(f"duplicate integration gate: {canonical_name}")
            continue
        if has_surrounding_whitespace:
            continue
        result[canonical_name] = gate
        if canonical_name not in expected_names:
            errors.append(f"unknown integration gate: {canonical_name}")
    return result, errors


def _contract_integration_gates(contract):
    errors = []
    gates = getattr(contract, "integration_gates", None)
    if not isinstance(gates, list):
        return [], set(), ["contract.integration_gates must be a list"]

    valid_gates = []
    gate_names = set()
    for gate in gates:
        if not isinstance(gate, dict):
            errors.append("contract integration gate must be an object")
            continue
        gate_name = gate.get("name")
        if not isinstance(gate_name, str) or not gate_name.strip():
            errors.append(
                "contract integration gate name must be a non-empty string")
            continue
        canonical_name = gate_name.strip()
        has_surrounding_whitespace = gate_name != canonical_name
        if has_surrounding_whitespace:
            errors.append(
                "contract integration gate name must not have surrounding whitespace")
        if canonical_name in gate_names:
            errors.append(
                f"contract duplicate integration gate name: {canonical_name}")
            continue
        gate_names.add(canonical_name)
        if has_surrounding_whitespace:
            continue
        valid_gates.append(gate)
    return valid_gates, gate_names, errors


def _metric_satisfies(actual, expected) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual >= expected
    return actual == expected


def _validate_integration_gate_evidence(expected_gate, actual_gate, *, prefix):
    errors = []
    if not isinstance(expected_gate, dict):
        return [f"{prefix} contract integration gate must be an object"]
    gate_name = expected_gate.get("name")
    if not isinstance(actual_gate, dict):
        return [f"{prefix} missing integration gate: {gate_name}"]

    command_by_text, command_errors = _commands_by_text(
        actual_gate.get("commands"),
        prefix=f"{prefix}.integration_gates[{gate_name}].commands",
    )
    errors.extend(command_errors)
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
    expected_metrics = expected_gate.get("required_metrics", {})
    if not isinstance(expected_metrics, dict):
        errors.append(f"{prefix} contract integration metrics must be an object: {gate_name}")
        expected_metrics = {}
    for metric, expected_value in expected_metrics.items():
        if metric not in actual_metrics:
            errors.append(f"{prefix} missing integration metric for {gate_name}: {metric}")
        elif not _metric_satisfies(actual_metrics.get(metric), expected_value):
            errors.append(f"{prefix} integration metric below gate for {gate_name}: {metric}")

    expected_artifacts = expected_gate.get("artifacts", [])
    if not isinstance(expected_artifacts, list):
        errors.append(f"{prefix} contract integration artifacts must be a list: {gate_name}")
        expected_artifacts = []
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
    pr_url = artifacts.get("pr_url")
    return isinstance(pr_url, str) and bool(pr_url.strip())


def _requires_env_setup(contract) -> bool:
    """contract 声明 integration_gates(或后续 env 依赖标记)时,env_setup 必填。"""
    if not contract:
        return False
    return bool(getattr(contract, "integration_gates", None))


def _non_empty_string_list(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(entry, str) and entry.strip() for entry in value)
    )


def _matches_finding_ids(value, expected_ids: set[str]) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(entry, str) and entry.strip() for entry in value)
        and len(value) == len(set(value))
        and set(value) == expected_ids
    )


def _strict_mapping_by_key(
    mappings,
    *,
    key_field: str,
    expected_keys: set[str],
    allowed_statuses: set[str],
    prefix: str,
    label: str,
    canonicalize_keys: bool = False,
):
    errors = []
    mapping_by_key = {}
    for index, mapping in enumerate(mappings):
        item_prefix = f"{prefix}[{index}]"
        if not isinstance(mapping, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        key = mapping.get(key_field)
        if not isinstance(key, str) or not key.strip():
            errors.append(f"{item_prefix}.{key_field} is required")
            continue
        canonical_key = key.strip() if canonicalize_keys else key
        has_surrounding_whitespace = key != canonical_key
        if has_surrounding_whitespace:
            errors.append(
                f"{item_prefix}.{key_field} must not have surrounding whitespace")
        if canonical_key in mapping_by_key:
            errors.append(f"duplicate {label} mapping: {canonical_key}")
        else:
            if not has_surrounding_whitespace:
                mapping_by_key[canonical_key] = mapping
        if canonical_key not in expected_keys:
            errors.append(f"unknown {label} mapping: {canonical_key}")
        if mapping.get("status") not in allowed_statuses:
            errors.append(f"{item_prefix}.status is invalid")
    return mapping_by_key, errors


def delivered_revision_of(verification):
    if not isinstance(verification, dict):
        return None
    quality = verification.get("quality")
    if not isinstance(quality, dict):
        return None
    revision = quality.get("delivered_revision")
    return revision.strip() if isinstance(revision, str) and revision.strip() else None


def _validate_worker_quality(
    contract,
    verification,
    *,
    allow_mock_evidence=False,
    expected_revision=None,
) -> list:
    errors = []
    expected_quality = getattr(contract, "quality", None)
    actual_quality = verification.get("quality")
    if expected_quality is None:
        return ["contract.quality is required"]
    if not isinstance(actual_quality, dict):
        return ["verification.quality is required"]

    delivered_revision = delivered_revision_of(verification)
    if delivered_revision is None:
        errors.append("verification.quality.delivered_revision is required")
    elif expected_revision and delivered_revision != expected_revision:
        errors.append(
            "verification.quality.delivered_revision must match current PR head: "
            f"expected {expected_revision}, got {delivered_revision}"
        )

    expected_outcomes = set()
    required_outcomes = expected_quality.required_outcomes
    if not isinstance(required_outcomes, list):
        errors.append("contract quality required_outcomes must be a list")
        required_outcomes = []
    for outcome in required_outcomes:
        outcome_id = outcome.get("id") if isinstance(outcome, dict) else None
        if not isinstance(outcome_id, str) or not outcome_id.strip():
            errors.append("contract quality outcome id must be a non-empty string")
            continue
        expected_outcomes.add(outcome_id)
    mappings = actual_quality.get("outcome_mapping")
    mapping_by_outcome = {}
    if not isinstance(mappings, list):
        errors.append("verification.quality.outcome_mapping must be a list")
        mappings = []
    for index, mapping in enumerate(mappings):
        prefix = f"verification.quality.outcome_mapping[{index}]"
        if not isinstance(mapping, dict):
            errors.append(f"{prefix} must be an object")
            continue
        outcome = mapping.get("outcome")
        if not isinstance(outcome, str) or not outcome.strip():
            errors.append(f"{prefix}.outcome is required")
            continue
        if outcome in mapping_by_outcome:
            errors.append(f"verification.quality duplicate outcome mapping: {outcome}")
        mapping_by_outcome[outcome] = mapping
        if outcome not in expected_outcomes:
            errors.append(f"verification.quality unknown outcome mapping: {outcome}")
        if not _non_empty_string_list(mapping.get("implementation")):
            errors.append(f"{prefix}.implementation must be non-empty strings")
        if not _non_empty_string_list(mapping.get("tests")):
            errors.append(f"{prefix}.tests must be non-empty strings")
    for outcome_id in sorted(expected_outcomes - set(mapping_by_outcome)):
        errors.append(f"verification.quality missing outcome mapping: {outcome_id}")

    expected_tests = {}
    business_tests = expected_quality.business_tests
    if not isinstance(business_tests, list):
        errors.append("contract quality business_tests must be a list")
        business_tests = []
    for business_test in business_tests:
        test_id = business_test.get("id") if isinstance(business_test, dict) else None
        if not isinstance(test_id, str) or not test_id.strip():
            errors.append("contract quality business test id must be a non-empty string")
            continue
        expected_tests[test_id] = business_test
    proofs = actual_quality.get("regression_proof")
    proof_by_test = {}
    if not isinstance(proofs, list):
        errors.append("verification.quality.regression_proof must be a list")
        proofs = []
    for index, proof in enumerate(proofs):
        prefix = f"verification.quality.regression_proof[{index}]"
        if not isinstance(proof, dict):
            errors.append(f"{prefix} must be an object")
            continue
        test_id = proof.get("test_id")
        if not isinstance(test_id, str) or not test_id.strip():
            errors.append(f"{prefix}.test_id is required")
            continue
        if test_id in proof_by_test:
            errors.append(f"verification.quality duplicate regression proof: {test_id}")
        proof_by_test[test_id] = proof
        business_test = expected_tests.get(test_id)
        if business_test is None:
            errors.append(f"verification.quality unknown regression proof: {test_id}")
            continue
        base_ref = proof.get("base_ref")
        head_ref = proof.get("head_ref")
        if not isinstance(base_ref, str) or not base_ref.strip():
            errors.append(f"{prefix}.base_ref is required")
        if not isinstance(head_ref, str) or not head_ref.strip():
            errors.append(f"{prefix}.head_ref is required")
        elif delivered_revision and head_ref != delivered_revision:
            errors.append(
                f"{prefix}.head_ref must match delivered_revision: "
                f"expected {delivered_revision}, got {head_ref}"
            )
        if base_ref and head_ref and base_ref == head_ref:
            errors.append(f"{prefix}.base_ref and head_ref must differ")
        base_exit_code = proof.get("base_exit_code")
        if not isinstance(base_exit_code, int) or isinstance(base_exit_code, bool):
            errors.append(f"{prefix}.base_exit_code must be an integer")
        elif business_test.get("must_fail_on_base") is True and base_exit_code == 0:
            errors.append(f"{prefix} must fail on base: {test_id}")
        head_exit_code = proof.get("head_exit_code")
        if not isinstance(head_exit_code, int) or isinstance(head_exit_code, bool):
            errors.append(f"{prefix}.head_exit_code must be an integer")
        elif head_exit_code != 0:
            errors.append(f"{prefix} must pass on head: {test_id}")
    for test_id in sorted(set(expected_tests) - set(proof_by_test)):
        errors.append(f"verification.quality missing regression proof: {test_id}")

    runtime_fallbacks = actual_quality.get("runtime_fallbacks")
    if runtime_fallbacks != []:
        errors.append("verification.quality.runtime_fallbacks must be empty")
    known_gaps = actual_quality.get("known_gaps")
    if known_gaps != []:
        errors.append("verification.quality.known_gaps must be empty")
    allowed_origins = {"real", "mock"} if allow_mock_evidence else {"real"}
    if actual_quality.get("evidence_origin") not in allowed_origins:
        expected = "real or mock" if allow_mock_evidence else "real"
        errors.append(f"verification.quality.evidence_origin must be {expected}")
    return errors


def _validate_review_batch(contract, verdict, report) -> list:
    errors = []
    reviewed_revision = report.get("reviewed_revision")
    if not isinstance(reviewed_revision, str) or not reviewed_revision.strip():
        errors.append("review_report.reviewed_revision is required")

    scope = report.get("review_scope")
    if not isinstance(scope, dict):
        errors.append("review_report.review_scope is required")
    else:
        if not _non_empty_string_list(scope.get("changed_files")):
            errors.append("review_report.review_scope.changed_files must be non-empty strings")
        for flag in (
            "all_changed_files_reviewed",
            "all_outcomes_reviewed",
            "all_business_tests_rerun",
            "runtime_fallback_audit_completed",
        ):
            if scope.get(flag) is not True:
                errors.append(f"review_report.review_scope.{flag} must be true")

    findings = report.get("findings")
    if not isinstance(findings, list):
        errors.append("review_report.findings must be a list")
        findings = []
    finding_ids = set()
    blocker_ids = set()
    nit_ids = set()
    for index, finding in enumerate(findings):
        prefix = f"review_report.findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix} must be an object")
            continue
        finding_id = finding.get("id")
        valid_finding_id = isinstance(finding_id, str) and bool(finding_id.strip())
        if not valid_finding_id:
            errors.append(f"{prefix}.finding.id is required")
        elif finding_id in finding_ids:
            errors.append(f"review_report duplicate finding id: {finding_id}")
        else:
            finding_ids.add(finding_id)
        severity = finding.get("severity")
        if severity not in {"blocker", "nit"}:
            errors.append(f"{prefix}.finding.severity must be blocker|nit")
        elif valid_finding_id:
            (blocker_ids if severity == "blocker" else nit_ids).add(finding_id)
        for field in ("category", "location", "evidence", "impact", "required_fix"):
            value = finding.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.finding.{field} is required")

    blockers = report.get("blockers")
    if not _matches_finding_ids(blockers, blocker_ids):
        errors.append("review_report.blockers must match blocker finding ids")
    nits = report.get("nits")
    if not _matches_finding_ids(nits, nit_ids):
        errors.append("review_report.nits must match nit finding ids")

    if verdict == "pass" and findings:
        errors.append("pass verdict requires no findings")
    elif verdict == "pass-with-nits":
        if blocker_ids:
            errors.append("pass-with-nits must not contain blocker findings")
        if not nit_ids:
            errors.append("pass-with-nits requires nit findings")
    elif verdict == "reject" and not blocker_ids:
        errors.append("reject requires blocker findings")

    quality = getattr(contract, "quality", None)
    if quality is None:
        errors.append("contract.quality is required")
        expected_outcomes = set()
    else:
        expected_outcomes = set()
        required_outcomes = quality.required_outcomes
        if not isinstance(required_outcomes, list):
            errors.append("contract quality required_outcomes must be a list")
            required_outcomes = []
        for outcome in required_outcomes:
            outcome_id = outcome.get("id") if isinstance(outcome, dict) else None
            if not isinstance(outcome_id, str) or not outcome_id.strip():
                errors.append("contract quality outcome id must be a non-empty string")
                continue
            expected_outcomes.add(outcome_id)
    outcome_mappings = report.get("outcome_mapping")
    mapping_by_outcome = {}
    if not isinstance(outcome_mappings, list):
        errors.append("review_report.outcome_mapping must be a list")
        outcome_mappings = []
    allowed_statuses = {"pass"} if verdict in REVIEW_APPROVE else {"pass", "fail"}
    for index, mapping in enumerate(outcome_mappings):
        prefix = f"review_report.outcome_mapping[{index}]"
        if not isinstance(mapping, dict):
            errors.append(f"{prefix} must be an object")
            continue
        outcome = mapping.get("outcome")
        if not isinstance(outcome, str) or not outcome.strip():
            errors.append(f"{prefix}.outcome is required")
            continue
        if outcome in mapping_by_outcome:
            errors.append(f"review_report duplicate outcome mapping: {outcome}")
        mapping_by_outcome[outcome] = mapping
        if outcome not in expected_outcomes:
            errors.append(f"{prefix} references unknown outcome: {outcome}")
        if mapping.get("status") not in allowed_statuses:
            errors.append(f"{prefix}.status is invalid for verdict {verdict}")
    for outcome_id in sorted(expected_outcomes - set(mapping_by_outcome)):
        errors.append(f"review_report missing outcome mapping: {outcome_id}")
    return errors


def validate_worker_evidence(
    node,
    item,
    *,
    allow_mock_evidence: bool = False,
    expected_revision: str | None = None,
) -> list:
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

    command_by_text, command_errors = _commands_by_text(
        verification.get("commands"), prefix="verification.commands")
    errors.extend(command_errors)
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

    valid_expected_gates, expected_gate_names, contract_gate_errors = (
        _contract_integration_gates(contract)
    )
    errors.extend(contract_gate_errors)
    integration_gate_by_name, gate_errors = _gate_by_name(
        verification.get("integration_gates"),
        expected_names=expected_gate_names,
        prefix="verification.integration_gates",
    )
    errors.extend(gate_errors)
    if integration_gate_by_name is None:
        errors.append("verification.integration_gates must be non-empty")
        integration_gate_by_name = {}
    for expected_gate in valid_expected_gates:
        gate_name = expected_gate["name"]
        errors.extend(
            _validate_integration_gate_evidence(
                expected_gate,
                integration_gate_by_name.get(gate_name),
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

    errors.extend(_validate_worker_quality(
        contract,
        verification,
        allow_mock_evidence=allow_mock_evidence,
        expected_revision=expected_revision,
    ))

    previous_review = getattr(item, "review_report", None)
    if (
        getattr(item, "review_verdict", None) == "pass-with-nits"
        and isinstance(previous_review, dict)
        and previous_review.get("reviewed_revision")
        == delivered_revision_of(verification)
    ):
        errors.append(
            "pass-with-nits follow-up must submit a new revision with fresh evidence"
        )

    return errors


def validate_review_evidence(
    node,
    item,
    *,
    expected_revision: str | None = None,
) -> list:
    """Return gate failure messages for structured reviewer verdict/report."""
    errors = []
    verdict = getattr(item, "review_verdict", None)
    report = getattr(item, "review_report", None)
    contract = getattr(node, "contract", None)

    if verdict not in REVIEW_VERDICTS:
        return [f"review_verdict {verdict!r} is unknown"]

    if not isinstance(report, dict):
        return ["review_report is required"]

    if contract is None:
        return []

    expected_gates, expected_gate_names, contract_gate_errors = (
        _contract_integration_gates(contract)
    )
    errors.extend(contract_gate_errors)

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

    errors.extend(_validate_review_batch(contract, verdict, report))

    reviewed_revision = report.get("reviewed_revision")
    delivered_revision = delivered_revision_of(
        getattr(item, "verification", None))
    if expected_revision and delivered_revision is None:
        errors.append("review_report Worker delivered_revision is required")
    elif delivered_revision and reviewed_revision != delivered_revision:
        errors.append(
            "review_report.reviewed_revision must match Worker delivered_revision: "
            f"expected {delivered_revision}, got {reviewed_revision}"
        )
    if expected_revision and reviewed_revision != expected_revision:
        errors.append(
            "review_report.reviewed_revision must match current PR head: "
            f"expected {expected_revision}, got {reviewed_revision}"
        )

    mappings = report.get("acceptance_mapping")
    if not isinstance(mappings, list) or not mappings:
        errors.append("review_report.acceptance_mapping must be non-empty")
        mappings = []

    required_statuses = {"pass"} if verdict in REVIEW_APPROVE else {"pass", "fail"}
    expected_acceptance = set()
    contract_acceptance = contract.acceptance
    if not isinstance(contract_acceptance, list):
        errors.append("contract acceptance must be a list")
        contract_acceptance = []
    for acceptance in contract_acceptance:
        if not isinstance(acceptance, str) or not acceptance.strip():
            errors.append("contract acceptance must contain non-empty strings")
            continue
        expected_acceptance.add(acceptance)
    mapped_acceptance, mapping_errors = _strict_mapping_by_key(
        mappings,
        key_field="acceptance",
        expected_keys=expected_acceptance,
        allowed_statuses=required_statuses,
        prefix="review_report.acceptance_mapping",
        label="acceptance",
    )
    errors.extend(mapping_errors)
    for acceptance in sorted(expected_acceptance):
        if acceptance not in mapped_acceptance:
            errors.append(f"review_report missing acceptance mapping: {acceptance}")

    if contract.integration_gates:
        integration_mappings = report.get("integration_gate_mapping")
        if not isinstance(integration_mappings, list) or not integration_mappings:
            errors.append("review_report.integration_gate_mapping must be non-empty")
        else:
            mapping_by_gate, gate_mapping_errors = _strict_mapping_by_key(
                integration_mappings,
                key_field="gate",
                expected_keys=expected_gate_names,
                allowed_statuses={"pass"},
                prefix="review_report.integration_gate_mapping",
                label="integration gate",
                canonicalize_keys=True,
            )
            errors.extend(gate_mapping_errors)
            for expected_gate in expected_gates:
                gate_name = expected_gate.get("name") if isinstance(expected_gate, dict) else None
                if not isinstance(gate_name, str) or not gate_name.strip():
                    continue
                errors.extend(
                    _validate_integration_gate_evidence(
                        expected_gate,
                        mapping_by_gate.get(gate_name),
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

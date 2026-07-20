"""core.evidence:worker 证据门、review 证据门、acceptance 验收门(三道门共用同一套 schema)。"""
from copy import deepcopy
import pytest

from omac.core.evidence import (
    validate_acceptance_results,
    validate_review_evidence,
    validate_worker_evidence,
)
from omac.core.acceptance import load_acceptance_doc, load_acceptance_doc_file
from omac.core.manifest import Contract, Node

CONTRACT = Contract(
    objective="do it",
    acceptance=["works"],
    non_goals=["no creep"],
    verification_commands=["pytest -q"],
    integration_gates=[{
        "name": "gate-1", "layer": "L1", "delivery_goal": "delivers",
        "source_of_truth": ["docs/d.md"], "covers": ["route"],
        "acceptance_refs": ["works"], "commands": ["pytest tests/int"],
        "required_metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
    }],
    quality={
        "required_outcomes": [{
            "id": "outcome-works", "source_ref": "acceptance#works.action",
        }],
        "business_tests": [{
            "id": "business-works",
            "outcome_refs": ["outcome-works"],
            "command": "pytest tests/int",
            "level": "integration",
            "real_dependencies": ["postgres"],
            "must_fail_on_base": True,
        }],
        "runtime_data_policy": "real-or-error",
    },
    pr_base="feature/v1",
    coverage_gate=90,
)
NODE = Node(id="a", worker="alice", contract=CONTRACT)

CONTRACT_NO_GATES = Contract(
    objective="do it",
    acceptance=["works"],
    non_goals=["no creep"],
    verification_commands=["pytest -q"],
    quality={
        "required_outcomes": [{
            "id": "outcome-works", "source_ref": "acceptance#works.action",
        }],
        "business_tests": [{
            "id": "business-works",
            "outcome_refs": ["outcome-works"],
            "command": "pytest -q",
            "level": "integration",
            "real_dependencies": ["none"],
            "must_fail_on_base": True,
        }],
        "runtime_data_policy": "real-or-error",
    },
    pr_base="feature/v1",
    coverage_gate=90,
)
NODE_NO_GATES = Node(id="b", worker="alice", contract=CONTRACT_NO_GATES)


class Item:
    def __init__(self, **kw):
        self.artifacts = kw.get("artifacts")
        self.verification = kw.get("verification")
        self.review_verdict = kw.get("review_verdict")
        self.review_report = kw.get("review_report")


def _good_verification():
    return {
        "commands": [{"cmd": "pytest -q", "exit_code": 0}],
        "integration_gates": [{
            "name": "gate-1",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"],
            "delivery_goal": "delivers",
        }],
        "env_setup": ["pip install -r requirements.txt", "docker compose up -d db"],
        "pr_base": "feature/v1",
        "coverage": 95,
        "quality": {
            "delivered_revision": "head-sha",
            "outcome_mapping": [{
                "outcome": "outcome-works",
                "implementation": ["src/feature.py"],
                "tests": ["tests/int/test_feature.py"],
            }],
            "regression_proof": [{
                "test_id": "business-works",
                "base_ref": "base-sha",
                "base_exit_code": 1,
                "head_ref": "head-sha",
                "head_exit_code": 0,
            }],
            "runtime_fallbacks": [],
            "known_gaps": [],
            "evidence_origin": "real",
        },
    }


def _good_report():
    return {
        "reviewed_revision": "head-sha",
        "review_goals": ["验收映射覆盖 contract.acceptance", "集成门 route_coverage 达标"],
        "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "integration_tests_rerun": True,
        "review_scope": {
            "changed_files": ["src/feature.py", "tests/int/test_feature.py"],
            "all_changed_files_reviewed": True,
            "all_outcomes_reviewed": True,
            "all_business_tests_rerun": True,
            "runtime_fallback_audit_completed": True,
        },
        "findings": [],
        "blockers": [],
        "nits": [],
        "outcome_mapping": [{"outcome": "outcome-works", "status": "pass"}],
        "acceptance_mapping": [{"acceptance": "works", "status": "pass"}],
        "integration_gate_mapping": [{
            "gate": "gate-1", "status": "pass",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers",
        }],
    }


def _reject_report():
    report = _good_report()
    report["findings"] = [{
        "id": "REV-001",
        "severity": "blocker",
        "category": "integration",
        "location": "tests/int/test_feature.py:1",
        "evidence": "declared integration command exits non-zero",
        "impact": "the integration delivery goal is not met",
        "required_fix": "fix the integration failure and rerun the declared command",
    }]
    report["blockers"] = ["REV-001"]
    return report


# ---------- worker evidence ----------

def test_worker_evidence_passes():
    item = Item(artifacts={"pr_url": "https://x/pr/1"}, verification=_good_verification())
    assert validate_worker_evidence(NODE, item) == []


def test_worker_evidence_rejects_duplicate_contract_integration_gate_names():
    contract = deepcopy(CONTRACT)
    contract.integration_gates.append(deepcopy(contract.integration_gates[0]))
    node = Node(id="duplicate-worker-gate", worker="alice", contract=contract)

    errors = validate_worker_evidence(
        node,
        Item(
            artifacts={"pr_url": "https://x/pr/1"},
            verification=_good_verification(),
        ),
    )

    assert any(
        "duplicate integration gate name: gate-1" in error
        for error in errors
    )


def test_worker_evidence_rejects_duplicate_integration_gate_results():
    verification = _good_verification()
    failed = deepcopy(verification["integration_gates"][0])
    failed["commands"][0]["exit_code"] = 1
    verification["integration_gates"] = [failed, verification["integration_gates"][0]]

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "https://x/pr/1"}, verification=verification))

    assert any("duplicate integration gate" in error for error in errors)
    assert any("integration command failed" in error for error in errors)


def test_worker_evidence_whitespace_variant_cannot_bypass_duplicate_gate_detection():
    verification = _good_verification()
    duplicate = deepcopy(verification["integration_gates"][0])
    duplicate["name"] = "gate-1 "
    verification["integration_gates"].append(duplicate)

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "https://x/pr/1"}, verification=verification))

    assert any("must not have surrounding whitespace" in error for error in errors)
    assert any("duplicate integration gate: gate-1" in error for error in errors)


def test_worker_evidence_detects_duplicate_when_whitespace_variant_comes_first():
    verification = _good_verification()
    canonical = verification["integration_gates"][0]
    whitespace_variant = deepcopy(canonical)
    whitespace_variant["name"] = "gate-1 "
    verification["integration_gates"] = [whitespace_variant, canonical]

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "https://x/pr/1"}, verification=verification))

    assert any("must not have surrounding whitespace" in error for error in errors)
    assert any("duplicate integration gate: gate-1" in error for error in errors)


def test_worker_evidence_rejects_unknown_integration_gate_result():
    verification = _good_verification()
    unknown = deepcopy(verification["integration_gates"][0])
    unknown["name"] = "unknown-gate"
    verification["integration_gates"].append(unknown)

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "https://x/pr/1"}, verification=verification))

    assert any("unknown integration gate" in error for error in errors)


def test_worker_evidence_rejects_malformed_integration_gate_result():
    verification = _good_verification()
    verification["integration_gates"].append({"name": ["bad"]})

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "https://x/pr/1"}, verification=verification))

    assert any("integration_gates[1].name" in error for error in errors)


def test_worker_evidence_malformed_contract_gate_name_does_not_crash():
    contract = deepcopy(CONTRACT)
    contract.integration_gates[0]["name"] = ["bad"]
    node = Node(id="bad-gate", worker="alice", contract=contract)
    item = Item(
        artifacts={"pr_url": "https://x/pr/1"},
        verification=_good_verification(),
    )

    errors = validate_worker_evidence(node, item)

    assert any("gate name" in error for error in errors)


def test_worker_evidence_requires_pr():
    item = Item(artifacts={}, verification=_good_verification())
    assert any("pr_url" in e for e in validate_worker_evidence(NODE, item))


def test_worker_evidence_coverage_gate():
    v = _good_verification()
    v["coverage"] = 50
    errs = validate_worker_evidence(NODE, Item(artifacts={"pr_url": "u"}, verification=v))
    assert any("below gate" in e for e in errs)


@pytest.mark.parametrize("exit_code", [False, 0.0], ids=["bool", "float-zero"])
def test_worker_evidence_requires_integer_command_exit_code(exit_code):
    verification = _good_verification()
    verification["commands"][0]["exit_code"] = exit_code

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))

    assert any("command exit_code must be an integer" in error for error in errors)


@pytest.mark.parametrize("exit_code", [False, 0.0], ids=["bool", "float-zero"])
def test_worker_evidence_requires_integer_integration_command_exit_code(exit_code):
    verification = _good_verification()
    verification["integration_gates"][0]["commands"][0]["exit_code"] = exit_code

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))

    assert any("command exit_code must be an integer" in error for error in errors)


@pytest.mark.parametrize("coverage", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_worker_evidence_requires_finite_coverage(coverage):
    verification = _good_verification()
    verification["coverage"] = coverage

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))

    assert any("coverage must be a finite number" in error for error in errors)


@pytest.mark.parametrize("metric", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_worker_evidence_requires_finite_integration_metric(metric):
    verification = _good_verification()
    verification["integration_gates"][0]["metrics"]["route_coverage"] = metric

    errors = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))

    assert any("integration metric must be a finite number" in error for error in errors)


def test_worker_evidence_missing_command():
    v = _good_verification()
    v["commands"] = [{"cmd": "other", "exit_code": 0}]
    errs = validate_worker_evidence(NODE, Item(artifacts={"pr_url": "u"}, verification=v))
    assert any("missing command" in e for e in errs)


def test_worker_evidence_rejects_non_string_command_without_crashing():
    verification = _good_verification()
    verification["commands"] = [{"cmd": {"nested": "pytest -q"}, "exit_code": 0}]
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("command cmd must be a non-empty string" in error for error in errs)


def test_worker_evidence_rejects_malformed_contract_quality_ids_without_crashing():
    contract = deepcopy(CONTRACT)
    contract.quality.required_outcomes[0]["id"] = {"nested": "outcome-works"}
    node = Node(id="malformed", worker="alice", contract=contract)
    errs = validate_worker_evidence(
        node,
        Item(artifacts={"pr_url": "u"}, verification=_good_verification()),
    )
    assert any("contract quality outcome id must be a non-empty string" in error for error in errs)


def test_worker_evidence_rejects_non_string_regression_test_id_without_crashing():
    verification = _good_verification()
    verification["quality"]["regression_proof"][0]["test_id"] = {"id": "business-works"}
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("test_id is required" in error for error in errs)


def test_worker_evidence_env_setup_required_when_integration_gates():
    v = _good_verification()
    del v["env_setup"]
    errs = validate_worker_evidence(NODE, Item(artifacts={"pr_url": "u"}, verification=v))
    assert any("env_setup is required" in e for e in errs)


def test_worker_evidence_env_setup_empty_list_rejected():
    v = _good_verification()
    v["env_setup"] = []
    errs = validate_worker_evidence(NODE, Item(artifacts={"pr_url": "u"}, verification=v))
    assert any("env_setup is required" in e for e in errs)


def test_worker_evidence_env_setup_blank_entry_rejected():
    v = _good_verification()
    v["env_setup"] = ["pip install -r requirements.txt", "   "]
    errs = validate_worker_evidence(NODE, Item(artifacts={"pr_url": "u"}, verification=v))
    assert any("non-empty strings" in e for e in errs)


def test_worker_evidence_env_setup_not_required_without_integration_gates():
    v = {
        "commands": [{"cmd": "pytest -q", "exit_code": 0}],
        "pr_base": "feature/v1",
        "coverage": 95,
        "quality": {
            "outcome_mapping": [{
                "outcome": "outcome-works",
                "implementation": ["src/feature.py"],
                "tests": ["tests/int/test_feature.py"],
            }],
            "regression_proof": [{
                "test_id": "business-works",
                "base_ref": "base-sha",
                "base_exit_code": 1,
                "head_ref": "head-sha",
                "head_exit_code": 0,
            }],
            "runtime_fallbacks": [],
            "known_gaps": [],
            "evidence_origin": "real",
        },
    }
    errs = validate_worker_evidence(NODE_NO_GATES, Item(artifacts={"pr_url": "u"}, verification=v))
    assert not any("env_setup" in e for e in errs)


def test_worker_evidence_requires_all_outcome_mappings():
    verification = _good_verification()
    verification["quality"]["outcome_mapping"] = []
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("missing outcome mapping: outcome-works" in e for e in errs)


def test_worker_evidence_requires_canonical_delivered_revision():
    verification = _good_verification()
    del verification["quality"]["delivered_revision"]

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))

    assert any("delivered_revision is required" in error for error in errs)


def test_worker_evidence_requires_every_business_test_on_delivered_revision():
    verification = _good_verification()
    verification["quality"]["regression_proof"][0]["head_ref"] = "other-head"

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))

    assert any("head_ref must match delivered_revision" in error for error in errs)


def test_worker_evidence_delivered_revision_must_match_current_pr_head():
    verification = _good_verification()

    errs = validate_worker_evidence(
        NODE,
        Item(artifacts={"pr_url": "u"}, verification=verification),
        expected_revision="current-pr-head",
    )

    assert any("delivered_revision must match current PR head" in error for error in errs)


def test_pass_with_nits_worker_followup_requires_new_delivered_revision():
    errs = validate_worker_evidence(
        NODE,
        Item(
            artifacts={"pr_url": "u"},
            verification=_good_verification(),
            review_verdict="pass-with-nits",
            review_report={"reviewed_revision": "head-sha"},
        ),
    )

    assert any("pass-with-nits follow-up must submit a new revision" in error for error in errs)


def test_worker_evidence_requires_base_failure_for_business_test():
    verification = _good_verification()
    verification["quality"]["regression_proof"][0]["base_exit_code"] = 0
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("must fail on base" in e for e in errs)


def test_worker_evidence_requires_base_exit_code_for_regression_proof():
    verification = _good_verification()
    del verification["quality"]["regression_proof"][0]["base_exit_code"]
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("base_exit_code must be an integer" in e for e in errs)


def test_worker_evidence_requires_head_success_for_business_test():
    verification = _good_verification()
    verification["quality"]["regression_proof"][0]["head_exit_code"] = 1
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("must pass on head" in e for e in errs)


def test_worker_evidence_rejects_same_base_and_head_ref():
    verification = _good_verification()
    verification["quality"]["regression_proof"][0]["head_ref"] = "base-sha"
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("base_ref and head_ref must differ" in e for e in errs)


def test_worker_evidence_rejects_runtime_fallbacks():
    verification = _good_verification()
    verification["quality"]["runtime_fallbacks"] = ["fake user on timeout"]
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("runtime_fallbacks must be empty" in e for e in errs)


def test_worker_evidence_rejects_known_gaps():
    verification = _good_verification()
    verification["quality"]["known_gaps"] = ["payment path not implemented"]
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("known_gaps must be empty" in e for e in errs)


def test_worker_evidence_rejects_mock_origin():
    verification = _good_verification()
    verification["quality"]["evidence_origin"] = "mock"
    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=verification))
    assert any("evidence_origin must be real" in e for e in errs)


def test_mock_engine_mode_accepts_only_explicit_mock_origin():
    verification = _good_verification()
    verification["quality"]["evidence_origin"] = "mock"
    assert validate_worker_evidence(
        NODE,
        Item(artifacts={"pr_url": "u"}, verification=verification),
        allow_mock_evidence=True,
    ) == []

    del verification["quality"]["evidence_origin"]
    errs = validate_worker_evidence(
        NODE,
        Item(artifacts={"pr_url": "u"}, verification=verification),
        allow_mock_evidence=True,
    )
    assert any("evidence_origin must be real or mock" in e for e in errs)


# ---------- review evidence ----------

def test_review_evidence_reject_requires_blockers():
    report = _good_report()
    report["acceptance_mapping"][0]["status"] = "fail"
    report["outcome_mapping"][0]["status"] = "fail"
    errs = validate_review_evidence(NODE, Item(review_verdict="reject", review_report=report))
    assert any("reject requires blocker findings" in e for e in errs)


def test_review_evidence_reject_passes_with_blockers():
    report = _good_report()
    report["findings"] = [{
        "id": "REV-001",
        "severity": "blocker",
        "category": "business-behavior",
        "location": "src/feature.py:10",
        "evidence": "业务结果不符合验收",
        "impact": "核心流程失败",
        "required_fix": "返回验收要求的结果",
    }]
    report["blockers"] = ["REV-001"]
    report["acceptance_mapping"][0]["status"] = "fail"
    report["outcome_mapping"][0]["status"] = "fail"
    assert validate_review_evidence(
        NODE, Item(review_verdict="reject", review_report=report)
    ) == []


@pytest.mark.parametrize(
    ("exit_code", "route_coverage"),
    [(1, 100), (0, 92)],
    ids=["command-failure", "metric-failure"],
)
def test_review_evidence_reject_accepts_real_failed_integration_gate(
    exit_code, route_coverage,
):
    report = _reject_report()
    gate = report["integration_gate_mapping"][0]
    gate["status"] = "fail"
    gate["commands"][0]["exit_code"] = exit_code
    gate["metrics"]["route_coverage"] = route_coverage

    assert validate_review_evidence(
        NODE, Item(review_verdict="reject", review_report=report)
    ) == []


def test_review_evidence_reject_cannot_falsely_mark_passing_gate_as_failed():
    report = _reject_report()
    report["integration_gate_mapping"][0]["status"] = "fail"

    errors = validate_review_evidence(
        NODE, Item(review_verdict="reject", review_report=report))

    assert any(
        "status fail requires failing command or metric evidence" in error
        for error in errors
    )


def test_review_evidence_reject_still_requires_every_declared_integration_gate():
    contract = deepcopy(CONTRACT)
    second_gate = deepcopy(contract.integration_gates[0])
    second_gate["name"] = "gate-2"
    second_gate["commands"] = ["pytest tests/second"]
    contract.integration_gates.append(second_gate)
    node = Node(id="missing-reject-gate", worker="alice", contract=contract)
    report = _reject_report()
    report["integration_gate_mapping"][0]["status"] = "fail"
    report["integration_gate_mapping"][0]["commands"][0]["exit_code"] = 1

    errors = validate_review_evidence(
        node, Item(review_verdict="reject", review_report=report))

    assert any("missing integration gate: gate-2" in error for error in errors)


@pytest.mark.parametrize("verdict", ["pass", "pass-with-nits"])
def test_review_approve_verdicts_require_integration_gates_to_pass(verdict):
    report = _good_report()
    if verdict == "pass-with-nits":
        report["findings"] = [{
            "id": "REV-001",
            "severity": "nit",
            "category": "maintainability",
            "location": "src/feature.py:10",
            "evidence": "local name is unclear",
            "impact": "increases maintenance cost",
            "required_fix": "rename the local variable",
        }]
        report["nits"] = ["REV-001"]
    gate = report["integration_gate_mapping"][0]
    gate["status"] = "fail"
    gate["commands"][0]["exit_code"] = 1

    errors = validate_review_evidence(
        NODE, Item(review_verdict=verdict, review_report=report))

    assert any(
        f"integration_gate_mapping[0].status is invalid" in error
        for error in errors
    )


@pytest.mark.parametrize("verdict", ["pass", "pass-with-nits"])
def test_review_approve_verdicts_cannot_hide_failed_command_behind_pass_status(verdict):
    report = _good_report()
    if verdict == "pass-with-nits":
        report["findings"] = [{
            "id": "REV-001",
            "severity": "nit",
            "category": "maintainability",
            "location": "src/feature.py:10",
            "evidence": "local name is unclear",
            "impact": "increases maintenance cost",
            "required_fix": "rename the local variable",
        }]
        report["nits"] = ["REV-001"]
    report["integration_gate_mapping"][0]["commands"][0]["exit_code"] = 1

    errors = validate_review_evidence(
        NODE, Item(review_verdict=verdict, review_report=report))

    assert any("integration command failed" in error for error in errors)


@pytest.mark.parametrize("exit_code", [False, 0.0], ids=["bool", "float-zero"])
def test_review_evidence_requires_integer_integration_command_exit_code(exit_code):
    report = _good_report()
    report["integration_gate_mapping"][0]["commands"][0]["exit_code"] = exit_code

    errors = validate_review_evidence(
        NODE, Item(review_verdict="pass", review_report=report))

    assert any("command exit_code must be an integer" in error for error in errors)


@pytest.mark.parametrize("metric", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_review_evidence_requires_finite_integration_metric(metric):
    report = _good_report()
    report["integration_gate_mapping"][0]["metrics"]["route_coverage"] = metric

    errors = validate_review_evidence(
        NODE, Item(review_verdict="pass", review_report=report))

    assert any("integration metric must be a finite number" in error for error in errors)


def test_review_evidence_rejects_unknown_verdict():
    errs = validate_review_evidence(NODE, Item(review_verdict="maybe"))
    assert errs and "unknown" in errs[0]


def test_review_evidence_requires_acceptance_mapping():
    report = _good_report()
    report["acceptance_mapping"] = []
    errs = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))
    assert any("acceptance_mapping must be non-empty" in e for e in errs)


def test_review_evidence_passes_with_goals():
    assert validate_review_evidence(
        NODE, Item(review_verdict="pass", review_report=_good_report())
    ) == []


def test_review_evidence_rejects_duplicate_contract_integration_gate_names():
    contract = deepcopy(CONTRACT)
    contract.integration_gates.append(deepcopy(contract.integration_gates[0]))
    node = Node(id="duplicate-review-gate", worker="alice", contract=contract)
    report = _good_report()
    report["integration_gate_mapping"] = []

    errors = validate_review_evidence(
        node,
        Item(
            verification=_good_verification(),
            review_verdict="pass",
            review_report=report,
        ),
    )

    assert any(
        "duplicate integration gate name: gate-1" in error
        for error in errors
    )


def test_review_evidence_review_goals_required():
    report = _good_report()
    del report["review_goals"]
    errs = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))
    assert any("review_goals must be non-empty" in e for e in errs)


def test_review_evidence_review_goals_empty_list_rejected():
    report = _good_report()
    report["review_goals"] = []
    errs = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))
    assert any("review_goals must be non-empty" in e for e in errs)


def test_review_evidence_review_goals_blank_entry_rejected():
    report = _good_report()
    report["review_goals"] = ["valid goal", ""]
    errs = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))
    assert any("non-empty strings" in e for e in errs)


def test_review_evidence_requires_reviewed_revision():
    report = _good_report()
    report["reviewed_revision"] = ""
    errs = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))
    assert any("reviewed_revision is required" in e for e in errs)


def test_review_evidence_malformed_contract_acceptance_does_not_crash():
    contract = deepcopy(CONTRACT)
    contract.acceptance = [["bad"]]
    node = Node(id="bad-acceptance", worker="alice", contract=contract)
    item = Item(
        verification=_good_verification(),
        review_verdict="pass",
        review_report=_good_report(),
    )

    errors = validate_review_evidence(node, item, expected_revision="head-sha")

    assert any("contract acceptance" in error for error in errors)


def test_review_evidence_reports_all_mapping_sections_in_one_pass():
    report = _good_report()
    report["acceptance_mapping"] = []
    report["integration_gate_mapping"] = []
    item = Item(
        verification=_good_verification(),
        review_verdict="pass",
        review_report=report,
    )

    errors = validate_review_evidence(NODE, item, expected_revision="head-sha")

    assert any("acceptance_mapping must be non-empty" in error for error in errors)
    assert any("integration_gate_mapping must be non-empty" in error for error in errors)


def test_review_evidence_revision_must_match_worker_delivery():
    report = _good_report()
    report["reviewed_revision"] = "stale-head"

    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass",
            review_report=report,
            verification=_good_verification(),
        ),
    )

    assert any("reviewed_revision must match Worker delivered_revision" in error for error in errs)


def test_review_evidence_revision_must_match_current_pr_head():
    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass",
            review_report=_good_report(),
            verification=_good_verification(),
        ),
        expected_revision="current-pr-head",
    )

    assert any("reviewed_revision must match current PR head" in error for error in errs)


def test_review_evidence_requires_worker_revision_when_current_head_is_checked():
    verification = _good_verification()
    del verification["quality"]["delivered_revision"]

    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass",
            review_report=_good_report(),
            verification=verification,
        ),
        expected_revision="head-sha",
    )

    assert any("Worker delivered_revision is required" in error for error in errs)


def test_review_evidence_rejects_conflicting_duplicate_acceptance_mapping():
    report = _good_report()
    report["acceptance_mapping"] = [
        {"acceptance": "works", "status": "fail"},
        {"acceptance": "works", "status": "pass"},
    ]

    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass", review_report=report,
            verification=_good_verification(),
        ),
    )

    assert any("duplicate acceptance mapping: works" in error for error in errs)
    assert any("acceptance_mapping[0].status is invalid" in error for error in errs)


def test_review_evidence_rejects_unknown_acceptance_mapping():
    report = _good_report()
    report["acceptance_mapping"].append(
        {"acceptance": "unknown", "status": "pass"})

    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass", review_report=report,
            verification=_good_verification(),
        ),
    )

    assert any("unknown acceptance mapping: unknown" in error for error in errs)


def test_review_evidence_rejects_duplicate_integration_gate_mapping():
    report = _good_report()
    report["integration_gate_mapping"].append(
        deepcopy(report["integration_gate_mapping"][0]))

    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass", review_report=report,
            verification=_good_verification(),
        ),
    )

    assert any("duplicate integration gate mapping: gate-1" in error for error in errs)


def test_review_evidence_whitespace_variant_cannot_bypass_duplicate_gate_mapping():
    report = _good_report()
    duplicate = deepcopy(report["integration_gate_mapping"][0])
    duplicate["gate"] = "gate-1 "
    report["integration_gate_mapping"].append(duplicate)

    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass",
            review_report=report,
            verification=_good_verification(),
        ),
    )

    assert any("must not have surrounding whitespace" in error for error in errs)
    assert any("duplicate integration gate mapping: gate-1" in error for error in errs)


def test_review_evidence_detects_duplicate_when_whitespace_variant_comes_first():
    report = _good_report()
    canonical = report["integration_gate_mapping"][0]
    whitespace_variant = deepcopy(canonical)
    whitespace_variant["gate"] = "gate-1 "
    report["integration_gate_mapping"] = [whitespace_variant, canonical]

    errs = validate_review_evidence(
        NODE,
        Item(
            review_verdict="pass",
            review_report=report,
            verification=_good_verification(),
        ),
    )

    assert any("must not have surrounding whitespace" in error for error in errs)
    assert any("duplicate integration gate mapping: gate-1" in error for error in errs)


def test_review_evidence_rejects_malformed_contract_gate_name_without_crashing():
    contract = deepcopy(CONTRACT)
    contract.integration_gates[0]["name"] = ["gate-1"]
    node = Node(id="malformed", worker="alice", contract=contract)

    errs = validate_review_evidence(
        node,
        Item(
            review_verdict="pass",
            review_report=_good_report(),
            verification=_good_verification(),
        ),
    )

    assert any("contract integration gate name must be a non-empty string" in error for error in errs)


def test_review_evidence_requires_complete_review_scope():
    report = _good_report()
    report["review_scope"]["all_changed_files_reviewed"] = False
    errs = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))
    assert any("all_changed_files_reviewed must be true" in e for e in errs)


def test_review_evidence_requires_all_outcomes():
    report = _good_report()
    report["outcome_mapping"] = []
    errs = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))
    assert any("missing outcome mapping: outcome-works" in e for e in errs)


def test_review_evidence_rejects_free_form_blocker_without_finding():
    report = _good_report()
    report["blockers"] = ["验收不满足"]
    errs = validate_review_evidence(NODE, Item(review_verdict="reject", review_report=report))
    assert any("blockers must match blocker finding ids" in e for e in errs)


def test_review_evidence_rejects_non_string_finding_references_without_crashing():
    report = _good_report()
    report["blockers"] = [{"id": "REV-001"}]
    errs = validate_review_evidence(
        NODE, Item(review_verdict="reject", review_report=report))
    assert any("blockers must match blocker finding ids" in e for e in errs)


def test_review_evidence_rejects_malformed_finding():
    report = _good_report()
    report["findings"] = [{"id": "REV-001", "severity": "blocker"}]
    report["blockers"] = ["REV-001"]
    errs = validate_review_evidence(NODE, Item(review_verdict="reject", review_report=report))
    assert any("finding.category is required" in e for e in errs)


def test_review_evidence_pass_with_nits_requires_nit_finding():
    report = _good_report()
    errs = validate_review_evidence(
        NODE, Item(review_verdict="pass-with-nits", review_report=report))
    assert any("pass-with-nits requires nit findings" in e for e in errs)


def test_review_evidence_pass_with_nits_accepts_only_nits():
    report = _good_report()
    report["findings"] = [{
        "id": "REV-001",
        "severity": "nit",
        "category": "maintainability",
        "location": "src/feature.py:10",
        "evidence": "局部命名不清晰",
        "impact": "增加后续维护成本",
        "required_fix": "重命名局部变量",
    }]
    report["nits"] = ["REV-001"]
    assert validate_review_evidence(
        NODE, Item(review_verdict="pass-with-nits", review_report=report)
    ) == []


# ---------- acceptance results ----------

ACCEPTANCE_DOC = load_acceptance_doc({
    "flows": [
        {
            "id": "login",
            "name": "用户登录",
            "actions": [
                {"id": "open", "step": "打开登录页", "how": "访问 /login", "expected": "渲染表单"},
                {"id": "submit", "step": "提交凭证", "how": "POST /login", "expected": "跳转首页"},
            ],
        },
        {
            "id": "checkout",
            "name": "下单结算",
            "actions": [
                {"id": "add", "step": "加入购物车", "how": "点击加入", "expected": "数量+1"},
            ],
        },
    ],
})


def _results(entries):
    return list(entries)


def test_acceptance_results_passes_full_cover():
    results = [
        {"id": "login", "status": "pass"},
        {"id": "checkout", "status": "pass"},
    ]
    assert validate_acceptance_results(ACCEPTANCE_DOC, results) == []


def test_acceptance_results_fail_requires_notes():
    results = [
        {"id": "login", "status": "fail"},
        {"id": "checkout", "status": "pass"},
    ]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert any("failed but has no notes" in e for e in errs)


def test_acceptance_results_fail_with_notes_ok():
    results = [
        {"id": "login", "status": "fail", "notes": "跳转未发生,返回 500"},
        {"id": "checkout", "status": "pass"},
    ]
    assert validate_acceptance_results(ACCEPTANCE_DOC, results) == []


def test_acceptance_results_fail_blank_notes_rejected():
    results = [
        {"id": "login", "status": "fail", "notes": "   "},
        {"id": "checkout", "status": "pass"},
    ]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert any("failed but has no notes" in e for e in errs)


def test_acceptance_results_missing_flow_rejected():
    results = [{"id": "login", "status": "pass"}]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert any("missing acceptance flow: checkout" in e for e in errs)


def test_acceptance_results_extra_flow_rejected():
    results = [
        {"id": "login", "status": "pass"},
        {"id": "checkout", "status": "pass"},
        {"id": "ghost", "status": "pass"},
    ]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert any("extra flow not in acceptance doc: ghost" in e for e in errs)


def test_acceptance_results_invalid_status_rejected():
    results = [
        {"id": "login", "status": "ok"},
        {"id": "checkout", "status": "pass"},
    ]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert any("status must be pass|fail" in e for e in errs)


def test_acceptance_results_not_a_list_rejected():
    errs = validate_acceptance_results(ACCEPTANCE_DOC, {"login": "pass"})
    assert any("must be a list" in e for e in errs)


def test_acceptance_results_non_object_entry_rejected():
    results = ["login", "checkout"]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert any("must be an object" in e for e in errs)


def test_acceptance_results_missing_id_rejected():
    results = [{"status": "pass"}]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert errs  # id required + missing flows


def test_acceptance_results_duplicate_id_rejected():
    results = [
        {"id": "login", "status": "pass"},
        {"id": "login", "status": "fail", "notes": "dup"},
        {"id": "checkout", "status": "pass"},
    ]
    errs = validate_acceptance_results(ACCEPTANCE_DOC, results)
    assert any("duplicate acceptance_result id: login" in e for e in errs)


def test_acceptance_results_accepts_raw_dict():
    raw_doc = {
        "flows": [
            {"id": "only", "name": "only", "actions": [
                {"id": "act", "step": "s", "how": "h", "expected": "e"},
            ]},
        ],
    }
    errs = validate_acceptance_results(raw_doc, [{"id": "only", "status": "pass"}])
    assert errs == []


# ---------- acceptance doc schema (load_acceptance_doc) ----------

def _flow(flow_id="f1", name="flow one", step="s", action_id="act"):
    return {"id": flow_id, "name": name, "actions": [
        {"id": action_id, "step": step, "how": "h", "expected": "e"},
    ]}


def test_load_acceptance_doc_requires_name():
    raw = {"flows": [{"id": "f1", "actions": [
        {"id": "act", "step": "s", "how": "h", "expected": "e"},
    ]}]}
    with pytest.raises(ValueError, match="name is required"):
        load_acceptance_doc(raw)


def test_load_acceptance_doc_blank_name_rejected():
    raw = {"flows": [_flow(name="   ")]}
    with pytest.raises(ValueError, match="name is required"):
        load_acceptance_doc(raw)


def test_load_acceptance_doc_non_string_name_rejected():
    raw = {"flows": [_flow(name=123)]}
    with pytest.raises(ValueError, match="name is required"):
        load_acceptance_doc(raw)


def test_load_acceptance_doc_name_preserved_when_valid():
    doc = load_acceptance_doc({"flows": [_flow(name="my flow")]})
    assert doc.flows[0].name == "my flow"


def test_load_acceptance_doc_requires_action_id():
    raw = {"flows": [{"id": "f1", "name": "flow", "actions": [
        {"step": "s", "how": "h", "expected": "e"},
    ]}]}
    with pytest.raises(ValueError, match="action.id is required"):
        load_acceptance_doc(raw)


def test_load_acceptance_doc_rejects_duplicate_action_ids_within_flow():
    raw = {"flows": [{"id": "f1", "name": "flow", "actions": [
        {"id": "same", "step": "s1", "how": "h", "expected": "e"},
        {"id": "same", "step": "s2", "how": "h", "expected": "e"},
    ]}]}
    with pytest.raises(ValueError, match="duplicate action id in flow f1: same"):
        load_acceptance_doc(raw)


def test_acceptance_doc_exposes_qualified_action_ids():
    doc = load_acceptance_doc({"flows": [_flow(flow_id="login", action_id="submit")]})
    assert doc.action_ids == ["login.submit"]


@pytest.mark.parametrize("flow_id", ["account.login", "account#login", "account login"])
def test_acceptance_flow_id_rejects_ambiguous_segment_characters(flow_id):
    with pytest.raises(ValueError, match="flow.id must match"):
        load_acceptance_doc({"flows": [_flow(flow_id=flow_id)]})


@pytest.mark.parametrize("action_id", ["form.submit", "form#submit", "form submit"])
def test_acceptance_action_id_rejects_ambiguous_segment_characters(action_id):
    with pytest.raises(ValueError, match="action.id must match"):
        load_acceptance_doc({"flows": [_flow(action_id=action_id)]})

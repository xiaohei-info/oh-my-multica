"""Reject admission preserves complete failed integration results, not pass claims."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omac.core.evidence import validate_review_evidence, validate_worker_evidence
from omac.core.manifest import Contract


@pytest.fixture
def actual_review():
    folder = Path(__file__).parent / "fixtures/reject_integration_evidence"
    raw = (folder / "actual-review-report.json").read_bytes()
    assert len(raw) == 54070
    assert (
        hashlib.sha256(raw).hexdigest()
        == "2926a59aadfc4beb96015b213ed542271975306d585526be27f3fd610a9bd4fa"
    )
    control = json.loads((folder / "actual-fixture-control.json").read_text())
    item = SimpleNamespace(**control)
    item.review_report = json.loads(raw)
    item.review_verdict = "reject"
    node = SimpleNamespace(contract=SimpleNamespace(**control["contract"]))
    return node, item


def test_actual_unsubmitted_reject_retains_only_real_blocker_format_errors(
    actual_review,
):
    node, item = actual_review
    before = deepcopy(item)
    assert validate_review_evidence(node, item) == [
        "review_report failed obligation has no blocker: dimension:execution",
        "review_report failed obligation has no blocker: dimension:regression",
        "review_report failed obligation has no blocker: dimension:structure",
    ]
    assert vars(item) == vars(before)


@pytest.fixture
def failed_gate():
    contract = Contract(
        acceptance=["works"],
        verification_commands=["check"],
        coverage_gate=90,
        pr_base="main",
        integration_gates=[
            {
                "name": "gate",
                "commands": ["check"],
                "required_metrics": {"coverage": 90, "semantic_pass": True},
                "artifacts": ["failure.json"],
                "source_of_truth": ["spec.md"],
                "delivery_goal": "real integration",
            }
        ],
    )
    gate = {
        "gate": "gate",
        "status": "fail",
        "commands": [{"cmd": "check", "exit_code": 1}],
        "metrics": {"coverage": 89.9, "semantic_pass": False},
        "artifacts": ["failure.json"],
        "source_of_truth": ["spec.md"],
        "delivery_goal": "real integration",
    }
    report = {
        "full_review_completed": True,
        "review_goals": ["complete scope"],
        "diff_reviewed": True,
        "tests_rerun": True,
        "coverage_checked": True,
        "integration_tests_rerun": True,
        "blockers": ["actual failed gate"],
        "acceptance_mapping": [{"acceptance": "works", "status": "fail"}],
        "integration_gate_mapping": [gate],
    }
    return (
        SimpleNamespace(contract=contract),
        SimpleNamespace(review_verdict="reject", review_report=report),
        gate,
    )


def test_complete_reject_admits_failed_commands_and_metrics_without_mutation(
    failed_gate,
):
    node, item, gate = failed_gate
    before = deepcopy(gate)
    assert validate_review_evidence(node, item) == []
    assert gate == before


@pytest.mark.parametrize(
    "change",
    [
        "missing-gate",
        "invalid-status",
        "missing-command",
        "boolean-exit",
        "string-exit",
        "missing-exit",
        "missing-metric",
        "string-metric",
        "boolean-numeric-metric",
        "non-object-metrics",
        "non-boolean-metric",
        "missing-artifact",
        "wrong-source",
        "wrong-goal",
        "incomplete-review",
    ],
)
def test_failed_reject_still_requires_complete_bound_evidence(failed_gate, change):
    node, item, gate = failed_gate
    if change == "missing-gate":
        gate["gate"] = "other"
    elif change == "invalid-status":
        gate["status"] = "unknown"
    elif change == "missing-command":
        gate["commands"] = [{"cmd": "other", "exit_code": 1}]
    elif change == "boolean-exit":
        gate["commands"][0]["exit_code"] = False
    elif change == "string-exit":
        gate["commands"][0]["exit_code"] = "1"
    elif change == "missing-exit":
        gate["commands"][0].pop("exit_code")
    elif change == "missing-metric":
        gate["metrics"].pop("coverage")
    elif change == "string-metric":
        gate["metrics"]["coverage"] = "89.9"
    elif change == "boolean-numeric-metric":
        gate["metrics"]["coverage"] = True
    elif change == "non-object-metrics":
        gate["metrics"] = []
    elif change == "non-boolean-metric":
        gate["metrics"]["semantic_pass"] = "false"
    elif change == "missing-artifact":
        gate["artifacts"] = []
    elif change == "wrong-source":
        gate["source_of_truth"] = ["different.md"]
    elif change == "wrong-goal":
        gate["delivery_goal"] = "different"
    else:
        item.review_report["full_review_completed"] = False
    assert validate_review_evidence(node, item)


@pytest.mark.parametrize("verdict", ["pass", "pass-with-nits"])
@pytest.mark.parametrize("status", ["fail", "pass"])
def test_approval_verdicts_do_not_admit_failed_integration_evidence(
    failed_gate, verdict, status
):
    node, item, gate = failed_gate
    item.review_verdict = verdict
    item.review_report["blockers"] = []
    item.review_report["acceptance_mapping"][0]["status"] = "pass"
    gate["status"] = status
    assert validate_review_evidence(node, item)


def test_reject_cannot_label_unsuccessful_gate_pass(failed_gate):
    node, item, gate = failed_gate
    gate["status"] = "pass"
    errors = validate_review_evidence(node, item)
    assert any("integration command failed" in e for e in errors)
    assert any("integration metric below gate" in e for e in errors)


def test_worker_integration_gate_remains_success_only(failed_gate):
    node, _, gate = failed_gate
    gate["name"] = gate["gate"]
    worker = SimpleNamespace(
        artifacts={"pr_url": "https://example.test/pr/1"},
        verification={
            "commands": [
                {
                    "cmd": "check",
                    "exit_code": 0,
                    "business_tests": [{"acceptance": "works", "test": "real-test"}],
                }
            ],
            "integration_gates": [gate],
            "coverage": 90,
            "pr_base": "main",
            "env_setup": ["real environment"],
        },
    )
    errors = validate_worker_evidence(node, worker)
    assert any("integration command failed" in e for e in errors)
    assert any("integration metric below gate" in e for e in errors)


def test_report_dimension_association_and_string_shape_are_not_waived(actual_review):
    node, item = actual_review
    # Prose mentions of another root are not an existing structured association.
    item.review_report["blockers"][0]["obligation_ids"] = [
        "dimension:execution",
        "dimension:regression",
        "dimension:structure",
    ]
    item.review_report["blockers"][0]["evidence"] = ["not a string"]
    errors = validate_review_evidence(node, item)
    assert "review_report.blockers[0].evidence must be a non-empty string" in errors
    assert all(
        f"review_report failed obligation has no blocker: dimension:{name}" in errors
        for name in ["execution", "regression", "structure"]
    )


def test_duplicate_root_cannot_cover_failed_dimensions(actual_review):
    node, item = actual_review
    duplicate = deepcopy(item.review_report["blockers"][0])
    duplicate["obligation_id"] = "dimension:execution"
    item.review_report["blockers"].append(duplicate)
    errors = validate_review_evidence(node, item)
    assert any("duplicate root_cause_key" in e for e in errors)


@pytest.mark.parametrize("field", ["status", "gate", "cmd"])
@pytest.mark.parametrize("value", [[], {}, ["fail"]])
def test_malformed_failed_gate_identity_returns_validation_errors(
    failed_gate, field, value
):
    node, item, gate = failed_gate
    if field == "cmd":
        gate["commands"][0]["cmd"] = value
    else:
        gate[field] = value
    assert validate_review_evidence(node, item)

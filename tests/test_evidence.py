"""core.evidence:worker 证据门与 review 证据门(两道门共用的同一套 schema)。"""
from omac.core.evidence import validate_review_evidence, validate_worker_evidence
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
    pr_base="feature/v1",
    coverage_gate=90,
)
NODE = Node(id="a", worker="alice", contract=CONTRACT)


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
        "pr_base": "feature/v1",
        "coverage": 95,
    }


def test_worker_evidence_passes():
    item = Item(artifacts={"pr_url": "https://x/pr/1"}, verification=_good_verification())
    assert validate_worker_evidence(NODE, item) == []


def test_worker_evidence_requires_pr():
    item = Item(artifacts={}, verification=_good_verification())
    assert any("pr_url" in e for e in validate_worker_evidence(NODE, item))


def test_worker_evidence_coverage_gate():
    v = _good_verification()
    v["coverage"] = 50
    errs = validate_worker_evidence(NODE, Item(artifacts={"pr_url": "u"}, verification=v))
    assert any("below gate" in e for e in errs)


def test_worker_evidence_missing_command():
    v = _good_verification()
    v["commands"] = [{"cmd": "other", "exit_code": 0}]
    errs = validate_worker_evidence(NODE, Item(artifacts={"pr_url": "u"}, verification=v))
    assert any("missing command" in e for e in errs)


def test_review_evidence_rejects_bad_verdict():
    errs = validate_review_evidence(NODE, Item(review_verdict="reject"))
    assert errs and "not approvable" in errs[0]


def test_review_evidence_requires_acceptance_mapping():
    report = {
        "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "integration_tests_rerun": True, "blockers": [],
        "acceptance_mapping": [{"acceptance": "works", "status": "pass"}],
        "integration_gate_mapping": [{
            "gate": "gate-1", "status": "pass",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers",
        }],
    }
    assert validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report)) == []

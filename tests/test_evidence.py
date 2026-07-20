"""core.evidence:worker 证据门、review 证据门、acceptance 验收门(三道门共用同一套 schema)。"""
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
    pr_base="feature/v1",
    coverage_gate=90,
)
NODE = Node(id="a", worker="alice", contract=CONTRACT)

CONTRACT_NO_GATES = Contract(
    objective="do it",
    acceptance=["works"],
    non_goals=["no creep"],
    verification_commands=["pytest -q"],
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
        "commands": [{
            "cmd": "pytest -q",
            "exit_code": 0,
            "business_tests": [{
                "acceptance": "works",
                "test": "tests/test_feature.py::test_feature_works",
            }],
        }],
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
    }


def _good_report():
    return {
        "review_goals": ["验收映射覆盖 contract.acceptance", "集成门 route_coverage 达标"],
        "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "integration_tests_rerun": True, "full_review_completed": True,
        "blockers": [],
        "acceptance_mapping": [{"acceptance": "works", "status": "pass"}],
        "integration_gate_mapping": [{
            "gate": "gate-1", "status": "pass",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers",
        }],
    }


# ---------- worker evidence ----------

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
        "commands": [{
            "cmd": "pytest -q",
            "exit_code": 0,
            "business_tests": [{
                "acceptance": "works",
                "test": "tests/test_feature.py::test_feature_works",
            }],
        }],
        "pr_base": "feature/v1",
        "coverage": 95,
    }
    errs = validate_worker_evidence(NODE_NO_GATES, Item(artifacts={"pr_url": "u"}, verification=v))
    assert not any("env_setup" in e for e in errs)


def test_worker_evidence_requires_business_test_for_every_acceptance():
    v = _good_verification()
    del v["commands"][0]["business_tests"]

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert "verification missing business test for acceptance: works" in errs


def test_worker_evidence_accepts_business_test_from_integration_command():
    v = _good_verification()
    del v["commands"][0]["business_tests"]
    v["integration_gates"][0]["commands"][0]["business_tests"] = [{
        "acceptance": "works",
        "test": "tests/integration/test_route.py::test_route_delivers",
    }]

    assert validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v)) == []


def test_worker_evidence_rejects_unknown_business_test_acceptance():
    v = _good_verification()
    v["commands"][0]["business_tests"][0]["acceptance"] = "unknown"

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert "verification business test references unknown acceptance: unknown" in errs
    assert "verification missing business test for acceptance: works" in errs


def test_worker_evidence_rejects_malformed_business_tests_and_collects_all_errors():
    v = _good_verification()
    v["commands"][0]["business_tests"] = [
        "not-an-object",
        {"acceptance": "", "test": ""},
    ]
    v["integration_gates"][0]["commands"][0]["business_tests"] = "not-a-list"

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert "verification.business_tests entries must be objects for command: pytest -q" in errs
    assert "verification.business_tests acceptance must be a non-empty string for command: pytest -q" in errs
    assert "verification.business_tests test must be a non-empty string for command: pytest -q" in errs
    assert "verification.business_tests must be a list for command: pytest tests/int" in errs
    assert "verification missing business test for acceptance: works" in errs


def test_worker_evidence_rejects_business_test_on_failed_command():
    v = _good_verification()
    v["commands"][0]["exit_code"] = 1

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert "verification business test command failed: pytest -q" in errs
    assert "verification missing business test for acceptance: works" in errs


# ---------- review evidence ----------

def test_review_evidence_reject_requires_blockers():
    report = _good_report()
    report["acceptance_mapping"][0]["status"] = "fail"
    errs = validate_review_evidence(NODE, Item(review_verdict="reject", review_report=report))
    assert any("blockers must be non-empty" in e for e in errs)


def test_review_evidence_reject_passes_with_blockers():
    report = _good_report()
    report["blockers"] = ["验收不满足"]
    report["acceptance_mapping"][0]["status"] = "fail"
    assert validate_review_evidence(
        NODE, Item(review_verdict="reject", review_report=report)
    ) == []


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


def test_review_evidence_requires_full_review_completed():
    report = _good_report()
    del report["full_review_completed"]

    errs = validate_review_evidence(
        NODE, Item(review_verdict="pass", review_report=report))

    assert "review_report.full_review_completed must be true" in errs


def test_review_evidence_requires_full_review_completed_without_contract():
    node = Node(id="plan", worker="alice")
    report = {"full_review_completed": False}

    errs = validate_review_evidence(
        node, Item(review_verdict="pass", review_report=report))

    assert errs == ["review_report.full_review_completed must be true"]


def test_review_evidence_collects_errors_after_missing_acceptance_mapping():
    report = _good_report()
    report["acceptance_mapping"] = []
    report["full_review_completed"] = False
    report["coverage_checked"] = False

    errs = validate_review_evidence(
        NODE, Item(review_verdict="pass", review_report=report))

    assert "review_report.acceptance_mapping must be non-empty" in errs
    assert "review_report.full_review_completed must be true" in errs
    assert "review_report.coverage_checked must be true" in errs


# ---------- acceptance results ----------

ACCEPTANCE_DOC = load_acceptance_doc({
    "flows": [
        {
            "id": "login",
            "name": "用户登录",
            "actions": [
                {"step": "打开登录页", "how": "访问 /login", "expected": "渲染表单"},
                {"step": "提交凭证", "how": "POST /login", "expected": "跳转首页"},
            ],
        },
        {
            "id": "checkout",
            "name": "下单结算",
            "actions": [
                {"step": "加入购物车", "how": "点击加入", "expected": "数量+1"},
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
                {"step": "s", "how": "h", "expected": "e"},
            ]},
        ],
    }
    errs = validate_acceptance_results(raw_doc, [{"id": "only", "status": "pass"}])
    assert errs == []


# ---------- acceptance doc schema (load_acceptance_doc) ----------

def _flow(flow_id="f1", name="flow one", step="s"):
    return {"id": flow_id, "name": name, "actions": [
        {"step": step, "how": "h", "expected": "e"},
    ]}


def test_load_acceptance_doc_requires_name():
    raw = {"flows": [{"id": "f1", "actions": [{"step": "s", "how": "h", "expected": "e"}]}]}
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

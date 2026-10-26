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


@pytest.mark.parametrize("invalid_exit_code", [False, 0.0, None])
def test_worker_evidence_rejects_non_integer_zero_for_required_command(
        invalid_exit_code):
    v = _good_verification()
    business_tests = v["commands"][0].pop("business_tests")
    v["commands"].append({
        "cmd": "pytest tests/business",
        "exit_code": 0,
        "business_tests": business_tests,
    })
    if invalid_exit_code is None:
        del v["commands"][0]["exit_code"]
    else:
        v["commands"][0]["exit_code"] = invalid_exit_code

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert "verification command failed: pytest -q" in errs


@pytest.mark.parametrize("invalid_exit_code", [False, 0.0, None])
def test_worker_evidence_rejects_non_integer_zero_for_integration_command(
        invalid_exit_code):
    v = _good_verification()
    command = v["integration_gates"][0]["commands"][0]
    if invalid_exit_code is None:
        del command["exit_code"]
    else:
        command["exit_code"] = invalid_exit_code

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert (
        "verification integration command failed for gate-1: pytest tests/int"
        in errs
    )


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


@pytest.mark.parametrize("cmd", [None, "   "])
def test_worker_evidence_rejects_business_test_without_real_command(cmd):
    v = _good_verification()
    if cmd is None:
        del v["commands"][0]["cmd"]
    else:
        v["commands"][0]["cmd"] = cmd

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert "verification business test command must have a non-empty cmd" in errs
    assert "verification missing business test for acceptance: works" in errs


def test_worker_evidence_rejects_boolean_business_test_exit_code():
    v = _good_verification()
    v["commands"][0]["exit_code"] = False

    errs = validate_worker_evidence(
        NODE, Item(artifacts={"pr_url": "u"}, verification=v))

    assert (
        "verification business test command exit_code must be integer 0: pytest -q"
        in errs
    )
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


@pytest.mark.parametrize(("goals", "message"), [
    (None, "review_goals must be non-empty"),                   # missing
    ([], "review_goals must be non-empty"),
    (["valid goal", ""], "non-empty strings"),
])
def test_review_evidence_needs_non_empty_review_goals(goals, message):
    report = _good_report()
    if goals is None:
        del report["review_goals"]
    else:
        report["review_goals"] = goals

    errors = validate_review_evidence(NODE, Item(review_verdict="pass", review_report=report))

    assert any(message in error for error in errors)


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


PASS_LOGIN, PASS_CHECKOUT = {"id": "login", "status": "pass"}, {"id": "checkout", "status": "pass"}


@pytest.mark.parametrize("results", [
    [PASS_LOGIN, PASS_CHECKOUT],                                                                 # every flow passes
    [{"id": "login", "status": "fail", "notes": "跳转未发生,返回 500"}, PASS_CHECKOUT],            # a failure with notes is a valid result
])
def test_acceptance_results_that_cover_every_flow_are_accepted(results):
    assert validate_acceptance_results(ACCEPTANCE_DOC, results) == []


@pytest.mark.parametrize(("results", "message"), [
    ([{"id": "login", "status": "fail"}, PASS_CHECKOUT], "failed but has no notes"),
    ([{"id": "login", "status": "fail", "notes": "   "}, PASS_CHECKOUT], "failed but has no notes"),
    ([PASS_LOGIN], "missing acceptance flow: checkout"),
    ([PASS_LOGIN, PASS_CHECKOUT, {"id": "ghost", "status": "pass"}], "extra flow not in acceptance doc: ghost"),
    ([{"id": "login", "status": "ok"}, PASS_CHECKOUT], "status must be pass|fail"),
    ({"login": "pass"}, "must be a list"),
    (["login", "checkout"], "must be an object"),
    ([PASS_LOGIN, {"id": "login", "status": "fail", "notes": "dup"}, PASS_CHECKOUT], "duplicate acceptance_result id: login"),
    ([{"status": "pass"}], "id"),                                                               # an entry needs an id (and the flows are missing)
])
def test_acceptance_results_are_refused_with_the_reason(results, message):
    errors = validate_acceptance_results(ACCEPTANCE_DOC, results)

    assert any(message in error for error in errors), errors


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


@pytest.mark.parametrize("name", [None, "   ", 123])            # missing, blank, not a string
def test_load_acceptance_doc_refuses_a_flow_without_a_usable_name(name):
    flow = _flow(name=name)
    if name is None:
        del flow["name"]
    with pytest.raises(ValueError, match="name is required"):
        load_acceptance_doc({"flows": [flow]})


def test_load_acceptance_doc_name_preserved_when_valid():
    doc = load_acceptance_doc({"flows": [_flow(name="my flow")]})
    assert doc.flows[0].name == "my flow"

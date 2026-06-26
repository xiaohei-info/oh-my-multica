# tests/test_lint.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from core import Manifest, lint
from core.manifest import Node

POOL = {"agent-be", "agent-fe", "agent-rev"}

def mk(nodes):  # nodes: list[Node]
    return Manifest(meta={}, nodes={n.id: n for n in nodes})

def test_clean_manifest_no_errors():
    m = mk([Node("M0", "agent-be"), Node("M1", "agent-fe", ["M0"], reviewer="agent-rev")])
    assert lint(m, POOL) == []

def test_worker_not_in_pool():
    m = mk([Node("M0", "ghost")])
    errs = lint(m, POOL)
    assert any("ghost" in e and "pool" in e for e in errs)

def test_blocked_by_unknown_node():
    m = mk([Node("M0", "agent-be", ["NOPE"])])
    assert any("NOPE" in e for e in lint(m, POOL))

def test_cycle_detected():
    m = mk([Node("A", "agent-be", ["B"]), Node("B", "agent-fe", ["A"])])
    assert any("cycle" in e.lower() for e in lint(m, POOL))

def test_reviewer_equals_worker():
    m = mk([Node("M0", "agent-be", reviewer="agent-be")])
    assert any("reviewer" in e and "worker" in e for e in lint(m, POOL))

def test_reviewer_not_in_pool():
    m = mk([Node("M0", "agent-be", reviewer="ghost-reviewer")])
    errs = lint(m, POOL)
    assert any("ghost-reviewer" in e and "pool" in e for e in errs)


def contract(**overrides):
    data = {
        "objective": "Implement user API",
        "acceptance": ["GET /users/:id returns 200"],
        "non_goals": ["Do not modify auth flow"],
        "verification_commands": ["pytest tests/user_api"],
        "pr_base": "feature/v1",
    }
    data.update(overrides)
    return data


def test_contract_missing_required_fields_reported():
    m = mk([
        Node(
            "M0",
            "agent-be",
            contract={
                "objective": "Implement user API",
                "acceptance": [],
                "non_goals": [],
                "verification_commands": [],
            },
        )
    ])

    errs = lint(m, POOL)

    assert any("contract.acceptance" in e for e in errs)
    assert any("contract.non_goals" in e for e in errs)
    assert any("contract.verification_commands" in e for e in errs)
    assert any("contract.pr_base" in e for e in errs)


def test_contract_coverage_gate_must_be_0_to_100():
    m = mk([Node("M0", "agent-be", contract=contract(coverage_gate=101))])

    errs = lint(m, POOL)

    assert any("coverage_gate" in e and "0-100" in e for e in errs)


def test_contract_required_contract_path_must_exist():
    m = mk([Node("M0", "agent-be", contract=contract(required_contracts=["missing/file.py"]))])

    errs = lint(m, POOL)

    assert any("required_contracts" in e and "missing/file.py" in e for e in errs)


def test_valid_contract_has_no_contract_lint_errors():
    m = mk([
        Node(
            "M0",
            "agent-be",
            contract=contract(
                required_contracts=["skills/parallel-dev-orchestration/scripts/tests/test_lint.py"],
                coverage_gate=95,
            ),
        )
    ])

    assert lint(m, POOL) == []

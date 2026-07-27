from types import SimpleNamespace

from omac.core.acceptance import load_acceptance_doc
from omac.core.evidence import validate_review_evidence, validate_worker_evidence
from omac.core.lint import lint
from omac.core.manifest import Contract, Manifest, Node, _dump_contract, _load_contract
from omac.core.review_convergence import build_review_obligations
from omac.core.taskmeta import TaskKind


POOL = {"alice", "bob"}


def _acceptance_doc():
    return load_acceptance_doc({
        "schema": "omac.acceptance/v1",
        "flows": [{
            "id": "UJ-LOGIN-001",
            "name": "login",
            "actions": [
                {"id": "ACT-LOGIN-01", "step": "open", "how": "/login", "expected": "form"},
                {"id": "ACT-LOGIN-02", "step": "submit", "how": "submit", "expected": "home"},
            ],
        }],
    })


def _gate():
    return [{
        "name": "login-gate",
        "layer": "L1",
        "delivery_goal": "login works",
        "source_of_truth": ["docs/design.md#login"],
        "covers": ["login"],
        "acceptance_refs": ["UJ-LOGIN-001"],
        "commands": ["pytest tests/login"],
    }]


def _contract(**overrides):
    values = {
        "objective": "deliver login",
        "source_of_truth": ["docs/design.md#login"],
        "acceptance_claims": [],
        "acceptance_contributions": [],
        "acceptance_refs": ["UJ-LOGIN-001"],
        "non_goals": ["no signup"],
        "verification_commands": ["pytest -q"],
        "integration_gates": _gate(),
        "pr_base": "main",
    }
    values.update(overrides)
    return Contract(**values)


def _manifest(*nodes):
    return Manifest(meta={}, nodes={node.id: node for node in nodes})


def test_legacy_acceptance_field_roundtrips_without_semantic_reinterpretation():
    contract = _load_contract({"objective": "legacy", "acceptance": ["UJ-LOGIN-001"]})

    assert contract.acceptance == ["UJ-LOGIN-001"]
    assert contract.acceptance_claims == []
    assert _dump_contract(contract)["acceptance"] == ["UJ-LOGIN-001"]


def test_acceptance_v1_derives_stable_action_identity_but_v2_requires_it():
    legacy = load_acceptance_doc({
        "schema": "omac.acceptance/v1",
        "flows": [{
            "id": "UJ-LEGACY-001", "name": "legacy",
            "actions": [{"step": "one", "how": "do", "expected": "done"}],
        }],
    })
    assert legacy.action_ids_by_flow == {
        "UJ-LEGACY-001": ["UJ-LEGACY-001/STEP-01"]}

    import pytest
    with pytest.raises(ValueError, match="id is required by omac.acceptance/v2"):
        load_acceptance_doc({
            "schema": "omac.acceptance/v2",
            "flows": [{
                "id": "UJ-NEW-001", "name": "new",
                "actions": [{"step": "one", "how": "do", "expected": "done"}],
            }],
        })


def test_lint_accepts_one_full_owner_and_complete_action_contribution_closure():
    owner = Node(
        id="login-e2e", worker="alice", reviewer="bob",
        contract=_contract(
            acceptance_claims=["UJ-LOGIN-001"],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": ["ACT-LOGIN-01", "ACT-LOGIN-02"],
            }],
        ),
    )

    assert lint(_manifest(owner), POOL, acceptance=_acceptance_doc()) == []


def test_lint_rejects_full_claim_that_only_has_local_action_evidence():
    bootstrap = Node(
        id="console-bootstrap", worker="alice", reviewer="bob",
        contract=_contract(
            acceptance_claims=["UJ-LOGIN-001"],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": ["ACT-LOGIN-01"],
            }],
        ),
    )

    errors = lint(_manifest(bootstrap), POOL, acceptance=_acceptance_doc())

    assert any(
        "full claim UJ-LOGIN-001 does not cover every action" in error
        and "ACT-LOGIN-02" in error
        for error in errors
    )


def test_lint_rejects_missing_duplicate_and_unknown_responsibilities_together():
    first = Node(
        id="first", worker="alice", reviewer="bob",
        contract=_contract(
            acceptance_claims=["UJ-LOGIN-001"],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001", "action_ids": ["ACT-LOGIN-01", "ACT-UNKNOWN"]}],
        ),
    )
    second = Node(
        id="second", worker="bob", reviewer="alice",
        contract=_contract(acceptance_claims=["UJ-LOGIN-001"]),
    )

    errors = lint(_manifest(first, second), POOL, acceptance=_acceptance_doc())
    joined = "\n".join(errors)

    assert "unknown action 'ACT-UNKNOWN'" in joined
    assert "full claim has multiple owners" in joined
    assert "action has no contribution owner: UJ-LOGIN-001/ACT-LOGIN-02" in joined


def test_lint_rejects_mixing_legacy_and_explicit_responsibility_fields():
    node = Node(
        id="mixed", worker="alice", reviewer="bob",
        contract=_contract(
            acceptance=["UJ-LOGIN-001"],
            acceptance_claims=["UJ-LOGIN-001"],
        ),
    )

    errors = lint(_manifest(node), POOL, acceptance=_acceptance_doc())

    assert any("legacy contract.acceptance cannot be mixed" in error for error in errors)


def test_lint_collects_malformed_responsibility_types_without_crashing():
    malformed = Node(
        id="malformed", worker="alice", reviewer="bob",
        contract=_contract(
            acceptance_claims=[{"not": "a string"}],
            acceptance_refs=[""],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001", "action_ids": [None]}],
        ),
    )

    errors = lint(_manifest(malformed), POOL, acceptance=_acceptance_doc())
    joined = "\n".join(errors)

    assert "acceptance_claims entries must be non-empty strings" in joined
    assert "acceptance_refs entries must be non-empty strings" in joined
    assert "action_ids entries must be non-empty strings" in joined


def test_new_decomposition_rejects_legacy_acceptance_with_migration_error():
    legacy = Node(
        id="legacy", worker="alice", reviewer="bob",
        contract=Contract(
            objective="legacy", source_of_truth=["docs/design.md"],
            acceptance=["UJ-LOGIN-001"], non_goals=["none"],
            verification_commands=["pytest -q"], integration_gates=_gate(),
            pr_base="main",
        ),
    )

    errors = lint(
        _manifest(legacy), POOL, acceptance=_acceptance_doc(),
        require_explicit_responsibility=True)

    assert any(
        "legacy contract.acceptance is read-compatible" in error
        and "migrate" in error
        for error in errors
    )


def test_decompose_reviewer_receives_global_responsibility_matrix():
    import yaml

    manifest = _manifest(Node(
        id="login-e2e", worker="alice", reviewer="bob",
        contract=_contract(
            acceptance_claims=["UJ-LOGIN-001"],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": ["ACT-LOGIN-01", "ACT-LOGIN-02"],
            }],
        ),
    ))
    raw = {
        "meta": manifest.meta,
        "nodes": [{
            "id": node.id,
            "worker": node.worker,
            "reviewer": node.reviewer,
            "blocked_by": [],
            "contract": _dump_contract(node.contract),
        } for node in manifest.nodes.values()],
    }
    item = SimpleNamespace(
        kind=TaskKind.DECOMPOSE,
        deliverable=yaml.safe_dump(raw, sort_keys=False),
        contract=None,
    )

    obligations = build_review_obligations(item)
    matrix = next(
        obligation for obligation in obligations
        if obligation["obligation_id"] == "acceptance-responsibility:matrix"
    )

    assert matrix["responsibility_matrix"] == [{
        "flow_id": "UJ-LOGIN-001",
        "full_claim_owners": ["login-e2e"],
        "action_contributors": {
            "ACT-LOGIN-01": ["login-e2e"],
            "ACT-LOGIN-02": ["login-e2e"],
        },
        "trace_nodes": ["login-e2e"],
    }]


def test_develop_review_obligations_distinguish_claims_contributions_and_refs():
    item = SimpleNamespace(
        kind=TaskKind.DEVELOP,
        contract=_contract(
            acceptance_claims=["UJ-LOGIN-001"],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001", "action_ids": ["ACT-LOGIN-01"]}],
            acceptance_refs=["UJ-TRACE-ONLY"],
        ),
        deliverable=None,
    )

    ids = [entry["obligation_id"] for entry in build_review_obligations(item)]

    assert "acceptance:UJ-LOGIN-001" in ids
    assert "acceptance-action:UJ-LOGIN-001:ACT-LOGIN-01" in ids
    assert all("UJ-TRACE-ONLY" not in obligation_id for obligation_id in ids)


def test_worker_and_reviewer_evidence_require_claim_and_action_but_not_trace_ref():
    contract = _contract(
        acceptance_claims=["UJ-LOGIN-001"],
        acceptance_contributions=[{
            "flow_id": "UJ-LOGIN-001", "action_ids": ["ACT-LOGIN-01"]}],
        acceptance_refs=["UJ-TRACE-ONLY"],
    )
    node = Node(id="login", worker="alice", contract=contract)
    verification = {
        "commands": [{
            "cmd": "pytest -q", "exit_code": 0,
            "business_tests": [
                {"acceptance": "UJ-LOGIN-001", "test": "test_flow"},
                {"acceptance": "ACT-LOGIN-01", "test": "test_action"},
            ],
        }],
        "integration_gates": [{
            "name": "login-gate",
            "commands": [{"cmd": "pytest tests/login", "exit_code": 0}],
            "metrics": {}, "artifacts": [],
            "source_of_truth": ["docs/design.md#login"],
            "delivery_goal": "login works",
        }],
        "env_setup": ["python -m venv .venv"],
        "pr_base": "main",
        "coverage": 100,
    }
    worker = SimpleNamespace(artifacts={"pr_url": "https://example/pr/1"}, verification=verification)

    assert validate_worker_evidence(node, worker) == []

    report = {
        "review_goals": ["review exact responsibility"],
        "diff_reviewed": True,
        "tests_rerun": True,
        "coverage_checked": True,
        "integration_tests_rerun": True,
        "full_review_completed": True,
        "blockers": [],
        "acceptance_mapping": [
            {"acceptance": "UJ-LOGIN-001", "status": "pass"},
            {"acceptance": "ACT-LOGIN-01", "status": "pass"},
        ],
        "integration_gate_mapping": [{
            "gate": "login-gate", "status": "pass",
            "commands": [{"cmd": "pytest tests/login", "exit_code": 0}],
            "metrics": {}, "artifacts": [],
            "source_of_truth": ["docs/design.md#login"],
            "delivery_goal": "login works",
        }],
    }
    reviewer = SimpleNamespace(
        review_verdict="pass", review_report=report, review_obligations=[])

    assert validate_review_evidence(node, reviewer) == []

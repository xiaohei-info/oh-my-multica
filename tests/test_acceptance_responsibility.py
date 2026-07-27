from types import SimpleNamespace

import pytest
import yaml

from omac.core.acceptance import load_acceptance_doc
from omac.core.evidence import validate_review_evidence, validate_worker_evidence
from omac.core.lint import lint
from omac.core.manifest import Contract, Manifest, Node, _dump_contract, _load_contract
from omac.core.review_convergence import build_review_obligations
from omac.core.taskmeta import TaskKind


POOL = {"alice", "bob"}


def _acceptance_doc():
    return load_acceptance_doc({
        "schema": "omac.acceptance/v2",
        "flows": [{
            "id": "UJ-LOGIN-001",
            "name": "login",
            "actions": [
                {
                    "id": "UJ-LOGIN-001/STEP-01",
                    "kind": "flow-step",
                    "step": "lock authority",
                    "how": "read spec",
                    "expected": "pinned",
                },
                {
                    "id": "ACT-LOGIN-01",
                    "kind": "business-action",
                    "step": "open",
                    "how": "/login",
                    "expected": "form",
                },
                {
                    "id": "ACT-LOGIN-02",
                    "kind": "business-action",
                    "step": "submit",
                    "how": "submit",
                    "expected": "home",
                },
                {
                    "id": "UJ-LOGIN-001/STEP-04",
                    "kind": "flow-step",
                    "step": "collect evidence",
                    "how": "archive",
                    "expected": "linked",
                },
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


def _decompose_item(manifest):
    raw = {
        "meta": manifest.meta,
        "nodes": [{
            "id": node.id,
            "worker": node.worker,
            "reviewer": node.reviewer,
            "blocked_by": list(node.blocked_by),
            "contract": _dump_contract(node.contract),
        } for node in manifest.nodes.values()],
    }
    return SimpleNamespace(
        kind=TaskKind.DECOMPOSE,
        deliverable=yaml.safe_dump(raw, sort_keys=False),
        contract=None,
    )


def test_legacy_acceptance_field_roundtrips_without_semantic_reinterpretation():
    contract = _load_contract({"objective": "legacy", "acceptance": ["UJ-LOGIN-001"]})

    assert contract.acceptance == ["UJ-LOGIN-001"]
    assert contract.acceptance_claims == []
    assert _dump_contract(contract)["acceptance"] == ["UJ-LOGIN-001"]


def test_acceptance_v1_preserves_explicit_business_action_identity():
    legacy = load_acceptance_doc({
        "schema": "omac.acceptance/v1",
        "flows": [{
            "id": "UJ-LEGACY-001",
            "name": "legacy",
            "actions": [
                {"step": "setup", "how": "prepare", "expected": "ready"},
                {
                    "step": "do business",
                    "how": "Action ID=`ACT-LEGACY-01`. execute",
                    "expected": "Action ID=`ACT-LEGACY-01`. done",
                },
            ],
        }],
    })

    assert legacy.action_ids_by_flow == {
        "UJ-LEGACY-001": ["UJ-LEGACY-001/STEP-01", "ACT-LEGACY-01"]}
    assert legacy.business_action_ids_by_flow == {
        "UJ-LEGACY-001": ["ACT-LEGACY-01"]}
    assert legacy.step_count == 2
    assert legacy.business_action_count == 1


def test_legacy_oac_scale_counts_922_steps_and_495_business_actions():
    actions = []
    for index in range(1, 923):
        business = index <= 495
        action_id = f"ACT-SCALE-{index:03d}"
        marker = f"Action ID=`{action_id}`. " if business else ""
        actions.append({
            "step": f"step {index}",
            "how": f"{marker}execute step {index}",
            "expected": f"{marker}observable result {index}",
        })
    doc = load_acceptance_doc({
        "schema": "omac.acceptance/v1",
        "flows": [{"id": "UJ-SCALE-001", "name": "scale", "actions": actions}],
    })

    assert doc.step_count == 922
    assert doc.business_action_count == 495


def test_acceptance_v2_requires_stable_id_and_explicit_kind():
    with pytest.raises(ValueError, match="id is required by omac.acceptance/v2"):
        load_acceptance_doc({
            "schema": "omac.acceptance/v2",
            "flows": [{
                "id": "UJ-NEW-001",
                "name": "new",
                "actions": [{"step": "one", "how": "do", "expected": "done"}],
            }],
        })

    with pytest.raises(ValueError, match="kind is required by omac.acceptance/v2"):
        load_acceptance_doc({
            "schema": "omac.acceptance/v2",
            "flows": [{
                "id": "UJ-NEW-001",
                "name": "new",
                "actions": [{
                    "id": "STEP-1",
                    "step": "one",
                    "how": "do",
                    "expected": "done",
                }],
            }],
        })


def test_lint_accepts_full_owner_downstream_of_all_business_contributors():
    ui = Node(
        id="login-ui",
        worker="alice",
        reviewer="bob",
        contract=_contract(acceptance_contributions=[{
            "flow_id": "UJ-LOGIN-001",
            "action_ids": ["ACT-LOGIN-01"],
        }]),
    )
    api = Node(
        id="login-api",
        worker="bob",
        reviewer="alice",
        blocked_by=["login-ui"],
        contract=_contract(acceptance_contributions=[{
            "flow_id": "UJ-LOGIN-001",
            "action_ids": ["ACT-LOGIN-02"],
        }]),
    )
    owner = Node(
        id="login-e2e",
        worker="alice",
        reviewer="bob",
        blocked_by=["login-api"],
        contract=_contract(acceptance_claims=["UJ-LOGIN-001"]),
    )

    assert lint(_manifest(ui, api, owner), POOL, acceptance=_acceptance_doc()) == []
    assert "acceptance_contributions" not in _dump_contract(owner.contract)


def test_lint_rejects_bootstrap_full_claim_upstream_of_contribution_owners():
    bootstrap = Node(
        id="console-bootstrap",
        worker="alice",
        reviewer="bob",
        contract=_contract(acceptance_claims=["UJ-LOGIN-001"]),
    )
    ui = Node(
        id="login-ui",
        worker="bob",
        reviewer="alice",
        blocked_by=["console-bootstrap"],
        contract=_contract(acceptance_contributions=[{
            "flow_id": "UJ-LOGIN-001",
            "action_ids": ["ACT-LOGIN-01"],
        }]),
    )
    api = Node(
        id="login-api",
        worker="bob",
        reviewer="alice",
        blocked_by=["console-bootstrap"],
        contract=_contract(acceptance_contributions=[{
            "flow_id": "UJ-LOGIN-001",
            "action_ids": ["ACT-LOGIN-02"],
        }]),
    )

    errors = lint(_manifest(bootstrap, ui, api), POOL, acceptance=_acceptance_doc())

    assert any(
        "full claim owner console-bootstrap must depend on all contribution owners"
        in error and "login-api" in error and "login-ui" in error
        for error in errors
    )


def test_lint_rejects_missing_duplicate_and_unknown_responsibilities_together():
    first = Node(
        id="first",
        worker="alice",
        reviewer="bob",
        contract=_contract(
            acceptance_claims=["UJ-LOGIN-001"],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": ["ACT-LOGIN-01", "ACT-UNKNOWN"],
            }],
        ),
    )
    second = Node(
        id="second",
        worker="bob",
        reviewer="alice",
        contract=_contract(acceptance_claims=["UJ-LOGIN-001"]),
    )

    errors = lint(_manifest(first, second), POOL, acceptance=_acceptance_doc())
    joined = "\n".join(errors)

    assert "unknown business action 'ACT-UNKNOWN'" in joined
    assert "full claim has multiple owners" in joined
    assert "business action has no contribution owner: UJ-LOGIN-001/ACT-LOGIN-02" in joined


def test_lint_rejects_mixing_legacy_and_explicit_responsibility_fields():
    node = Node(
        id="mixed",
        worker="alice",
        reviewer="bob",
        contract=_contract(
            acceptance=["UJ-LOGIN-001"],
            acceptance_claims=["UJ-LOGIN-001"],
        ),
    )

    errors = lint(_manifest(node), POOL, acceptance=_acceptance_doc())

    assert any("legacy contract.acceptance cannot be mixed" in error for error in errors)


def test_lint_collects_malformed_responsibility_types_without_crashing():
    malformed = Node(
        id="malformed",
        worker="alice",
        reviewer="bob",
        contract=_contract(
            acceptance_claims=[{"not": "a string"}],
            acceptance_refs=[""],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": [None],
            }],
        ),
    )

    errors = lint(_manifest(malformed), POOL, acceptance=_acceptance_doc())
    joined = "\n".join(errors)

    assert "acceptance_claims entries must be non-empty strings" in joined
    assert "acceptance_refs entries must be non-empty strings" in joined
    assert "action_ids entries must be non-empty strings" in joined


def test_new_decomposition_rejects_legacy_acceptance_with_migration_error():
    legacy = Node(
        id="legacy",
        worker="alice",
        reviewer="bob",
        contract=Contract(
            objective="legacy",
            source_of_truth=["docs/design.md"],
            acceptance=["UJ-LOGIN-001"],
            non_goals=["none"],
            verification_commands=["pytest -q"],
            integration_gates=_gate(),
            pr_base="main",
        ),
    )

    errors = lint(
        _manifest(legacy),
        POOL,
        acceptance=_acceptance_doc(),
        require_explicit_responsibility=True,
    )

    assert any(
        "legacy contract.acceptance is read-compatible" in error
        and "migrate" in error
        for error in errors
    )


def test_decompose_reviewer_receives_dependency_closure_and_gaps():
    manifest = _manifest(
        Node(
            id="login-ui",
            worker="alice",
            reviewer="bob",
            contract=_contract(acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": ["ACT-LOGIN-01"],
            }]),
        ),
        Node(
            id="login-api",
            worker="bob",
            reviewer="alice",
            blocked_by=["login-ui"],
            contract=_contract(acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": ["ACT-LOGIN-02"],
            }]),
        ),
        Node(
            id="login-e2e",
            worker="alice",
            reviewer="bob",
            blocked_by=["login-api"],
            contract=_contract(acceptance_claims=["UJ-LOGIN-001"]),
        ),
    )
    item = _decompose_item(manifest)

    obligations = build_review_obligations(item, acceptance_doc=_acceptance_doc())
    matrix = next(
        obligation for obligation in obligations
        if obligation["obligation_id"] == "acceptance-responsibility:matrix"
    )

    assert matrix["responsibility_matrix"] == [{
        "flow_id": "UJ-LOGIN-001",
        "full_claim_owners": ["login-e2e"],
        "business_action_count": 2,
        "contributed_business_action_count": 2,
        "contribution_owners": ["login-api", "login-ui"],
        "full_owner_dependency_closure": ["login-api", "login-ui"],
        "missing_business_action_ids": [],
        "unknown_business_action_ids": [],
        "unreachable_contribution_owners": [],
        "trace_nodes": ["login-api", "login-e2e", "login-ui"],
    }]
    assert "ACT-LOGIN" not in yaml.safe_dump(matrix["responsibility_matrix"])


def test_develop_review_obligations_distinguish_claims_contributions_and_refs():
    item = SimpleNamespace(
        kind=TaskKind.DEVELOP,
        contract=_contract(
            acceptance_claims=["UJ-LOGIN-001"],
            acceptance_contributions=[{
                "flow_id": "UJ-LOGIN-001",
                "action_ids": ["ACT-LOGIN-01"],
            }],
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
            "flow_id": "UJ-LOGIN-001",
            "action_ids": ["ACT-LOGIN-01"],
        }],
        acceptance_refs=["UJ-TRACE-ONLY"],
    )
    node = Node(id="login", worker="alice", contract=contract)
    verification = {
        "commands": [{
            "cmd": "pytest -q",
            "exit_code": 0,
            "business_tests": [
                {"acceptance": "UJ-LOGIN-001", "test": "test_flow"},
                {"acceptance": "ACT-LOGIN-01", "test": "test_action"},
            ],
        }],
        "integration_gates": [{
            "name": "login-gate",
            "commands": [{"cmd": "pytest tests/login", "exit_code": 0}],
            "metrics": {},
            "artifacts": [],
            "source_of_truth": ["docs/design.md#login"],
            "delivery_goal": "login works",
        }],
        "env_setup": ["python -m venv .venv"],
        "pr_base": "main",
        "coverage": 100,
    }
    worker = SimpleNamespace(
        artifacts={"pr_url": "https://example/pr/1"},
        verification=verification,
    )

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
            "gate": "login-gate",
            "status": "pass",
            "commands": [{"cmd": "pytest tests/login", "exit_code": 0}],
            "metrics": {},
            "artifacts": [],
            "source_of_truth": ["docs/design.md#login"],
            "delivery_goal": "login works",
        }],
    }
    reviewer = SimpleNamespace(
        review_verdict="pass",
        review_report=report,
        review_obligations=[],
    )

    assert validate_review_evidence(node, reviewer) == []

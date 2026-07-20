"""core.lint:成员池、依赖引用、reviewer 规则、contract 硬门、环检测。"""
import pytest

from omac.core.lint import lint
from omac.core.acceptance import load_acceptance_doc
from omac.core.manifest import Contract, Manifest, Node, loads_manifest

POOL = {"alice", "bob"}


_DEFAULT = object()


def _node(id, worker="alice", reviewer=_DEFAULT, contract=_DEFAULT, **kw):
    if reviewer is _DEFAULT:
        reviewer = "bob" if worker == "alice" else "alice"
    if contract is _DEFAULT:
        contract = _valid_contract()
    return Node(
        id=id, worker=worker, reviewer=reviewer, contract=contract, **kw)


def _manifest(*nodes):
    return Manifest(meta={}, nodes={n.id: n for n in nodes})


def test_clean_manifest_passes():
    errs = lint(_manifest(_node("a"), _node("b", worker="bob", blocked_by=["a"])), POOL)
    assert errs == []


def test_worker_not_in_pool():
    errs = lint(_manifest(_node("a", worker="ghost")), POOL)
    assert any("not in agent pool" in e for e in errs)


def test_unknown_dependency():
    errs = lint(_manifest(_node("a", blocked_by=["nope"])), POOL)
    assert any("unknown node" in e for e in errs)


def test_reviewer_must_differ():
    errs = lint(_manifest(_node("a", reviewer="alice")), POOL)
    assert any("reviewer must differ" in e for e in errs)


def test_cycle_detected():
    errs = lint(_manifest(_node("a", blocked_by=["b"]), _node("b", worker="bob", blocked_by=["a"])), POOL)
    assert any("cycle" in e for e in errs)


def test_declared_closeout_node_must_exist():
    manifest = _manifest(_node("a"))
    manifest.meta["closeout_node"] = "closeout"

    errs = lint(manifest, POOL)

    assert any("closeout_node" in e and "closeout" in e for e in errs)


def test_authoring_lint_rejects_explicit_runtime_fields_even_at_default_values():
    manifest = loads_manifest("""\
meta: {}
nodes:
  - id: a
    worker: alice
    reviewer: bob
    status: todo
    work_item_id: null
    merged: false
    merged_at: null
""")

    errors = lint(manifest, POOL)

    for field in ("status", "work_item_id", "merged", "merged_at"):
        assert any(f"runtime field {field}" in error for error in errors)


def test_contract_hard_gates():
    contract = Contract(objective=None, acceptance=[], non_goals=[],
                        verification_commands=[], integration_gates=[], pr_base=None,
                        quality=None)
    errs = lint(_manifest(_node("a", contract=contract)), POOL)
    joined = "\n".join(errs)
    for needle in ("objective", "acceptance", "non_goals",
                   "verification_commands", "integration_gates", "pr_base",
                   "source_of_truth", "quality"):
        assert needle in joined


def test_develop_node_requires_contract():
    errs = lint(_manifest(_node("a", reviewer="bob", contract=None)), POOL)
    assert any("contract is required" in error for error in errs)


def test_develop_node_requires_independent_reviewer():
    errs = lint(_manifest(_node(
        "a", reviewer=None, contract=_valid_contract())), POOL)
    assert any("reviewer is required" in error for error in errs)


def test_increment_requires_contract_and_independent_reviewer():
    from omac.core.lint import lint_increment

    existing = _manifest(_node(
        "existing", reviewer="bob", contract=_valid_contract()))
    increment = _manifest(_node("fix", reviewer=None, contract=None))

    errs = lint_increment(increment, existing, POOL)

    assert any("contract is required" in error for error in errs)
    assert any("reviewer is required" in error for error in errs)


def _valid_contract(**over):
    """过 lint 的最小合法契约(每个硬门都满足)。"""
    base = dict(
        objective="实现 X", acceptance=["A 工作"], non_goals=["不做 Y"],
        source_of_truth=["docs/design.md#x"],
        verification_commands=["pytest -q"],
        integration_gates=[{
            "name": "g1", "layer": "L1", "delivery_goal": "d",
            "source_of_truth": ["docs/design.md#x"], "covers": ["route"],
            "acceptance_refs": ["A 工作"], "commands": ["pytest tests/int"],
        }],
        quality={
            "required_outcomes": [{
                "id": "outcome-x",
                "source_ref": "acceptance#flow-x.action-x",
            }],
            "business_tests": [{
                "id": "business-x",
                "outcome_refs": ["outcome-x"],
                "command": "pytest tests/int",
                "level": "integration",
                "real_dependencies": ["none"],
                "must_fail_on_base": True,
            }],
            "runtime_data_policy": "real-or-error",
        },
        pr_base="feature/v1")
    base.update(over)
    return Contract(**base)


def test_source_of_truth_required_for_contract():
    """契约必须带实现层设计指针(source_of_truth),否则 worker 只能脑补设计。"""
    contract = _valid_contract(source_of_truth=[])
    errs = lint(_manifest(_node("a", contract=contract)), POOL)
    assert any("source_of_truth" in e for e in errs)


def test_valid_contract_passes_all_gates():
    """回归:补全 source_of_truth 的完整契约应零报错(硬门不误伤合法节点)。"""
    errs = lint(_manifest(_node("a", contract=_valid_contract())), POOL)
    assert errs == []


def test_contract_rejects_duplicate_integration_gate_names():
    contract = _valid_contract()
    contract.integration_gates.append(dict(contract.integration_gates[0]))

    errs = lint(_manifest(_node("a", contract=contract)), POOL)

    assert any("duplicate integration gate name: g1" in error for error in errs)


@pytest.mark.parametrize("gate_name", [" g1", "g1 "])
def test_contract_rejects_integration_gate_name_surrounding_whitespace(gate_name):
    contract = _valid_contract()
    contract.integration_gates[0]["name"] = gate_name

    errs = lint(_manifest(_node("a", contract=contract)), POOL)

    assert any(
        "integration_gates[0].name must not have surrounding whitespace" in error
        for error in errs
    )


def test_contract_whitespace_variant_cannot_bypass_duplicate_gate_detection():
    contract = _valid_contract()
    duplicate = dict(contract.integration_gates[0])
    duplicate["name"] = "g1 "
    contract.integration_gates.append(duplicate)

    errs = lint(_manifest(_node("a", contract=contract)), POOL)

    assert any("duplicate integration gate name: g1" in error for error in errs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("objective", ["实现 X"]),
        ("objective", "   "),
        ("pr_base", ["feature/v1"]),
        ("pr_base", ""),
    ],
)
def test_contract_scalar_fields_must_be_non_empty_strings(field, value):
    contract = _valid_contract(**{field: value})

    errs = lint(_manifest(_node("a", contract=contract)), POOL)

    assert any(
        f"contract.{field} must be a non-empty string" in error
        for error in errs
    )


def test_required_contracts_resolve_from_manifest_project_root(
    tmp_path, monkeypatch,
):
    project_root = tmp_path / "project"
    required_contract = project_root / "contracts" / "shared.md"
    required_contract.parent.mkdir(parents=True)
    required_contract.write_text("# shared contract\n")
    unrelated_cwd = tmp_path / "elsewhere"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    contract = _valid_contract(required_contracts=["contracts/shared.md"])
    manifest = Manifest(
        meta={},
        nodes={"a": _node("a", contract=contract)},
        project_root=str(project_root),
    )

    assert lint(manifest, POOL) == []


def test_quality_requires_every_outcome_to_have_business_test():
    quality = _valid_contract().quality
    quality.required_outcomes.append({
        "id": "uncovered",
        "source_ref": "acceptance#flow-x.uncovered",
    })
    errs = lint(_manifest(_node("a", contract=_valid_contract(quality=quality))), POOL)
    assert any("required outcome has no business test: uncovered" in e for e in errs)


def test_quality_rejects_unit_only_business_test():
    quality = _valid_contract().quality
    quality.business_tests[0]["level"] = "unit"
    errs = lint(_manifest(_node("a", contract=_valid_contract(quality=quality))), POOL)
    assert any("level must be integration|e2e" in e for e in errs)


def test_quality_business_test_command_must_be_declared_gate_command():
    quality = _valid_contract().quality
    quality.business_tests[0]["command"] = "pytest tests/unknown"
    errs = lint(_manifest(_node("a", contract=_valid_contract(quality=quality))), POOL)
    assert any("command is not declared" in e for e in errs)


def test_quality_source_ref_must_anchor_real_acceptance_action():
    acceptance = load_acceptance_doc({
        "flows": [{
            "id": "flow-x",
            "name": "X flow",
            "actions": [{
                "id": "action-x", "step": "run", "how": "call /x",
                "expected": "x works",
            }],
        }],
    })
    assert lint(
        _manifest(_node("a", contract=_valid_contract(acceptance=["flow-x"]))),
        POOL,
        acceptance=acceptance,
    ) == []

    quality = _valid_contract().quality
    quality.required_outcomes[0]["source_ref"] = "acceptance#flow-x.missing"
    errs = lint(
        _manifest(_node("a", contract=_valid_contract(
            acceptance=["flow-x"], quality=quality))),
        POOL,
        acceptance=acceptance,
    )
    assert any("source_ref is not anchored to an acceptance action" in e for e in errs)


def test_quality_source_ref_requires_acceptance_anchor_shape():
    quality = _valid_contract().quality
    quality.required_outcomes[0]["source_ref"] = "docs/design.md#x"
    errs = lint(_manifest(_node("a", contract=_valid_contract(quality=quality))), POOL)
    assert any("source_ref must use acceptance#flow.action" in e for e in errs)


def test_quality_source_ref_flow_must_belong_to_node_acceptance():
    acceptance = load_acceptance_doc({
        "flows": [
            {"id": "flow-x", "name": "X", "actions": [
                {"id": "action-x", "step": "x", "how": "run x", "expected": "x"},
            ]},
            {"id": "flow-y", "name": "Y", "actions": [
                {"id": "action-y", "step": "y", "how": "run y", "expected": "y"},
            ]},
        ],
    })
    quality = _valid_contract().quality
    quality.required_outcomes[0]["source_ref"] = "acceptance#flow-y.action-y"
    errs = lint(
        _manifest(_node("a", contract=_valid_contract(
            acceptance=["flow-x"], quality=quality))),
        POOL,
        acceptance=acceptance,
    )
    assert any("source_ref flow is not declared in contract.acceptance" in e for e in errs)


def test_contract_rejects_non_string_verification_command_without_crashing():
    contract = _valid_contract(verification_commands=[{"cmd": "pytest -q"}])
    errs = lint(_manifest(_node("a", reviewer="bob", contract=contract)), POOL)
    assert any("verification_commands must be non-empty strings" in error for error in errs)


def test_integration_gate_rejects_non_string_command_without_crashing():
    contract = _valid_contract()
    contract.integration_gates[0]["commands"] = [{"cmd": "pytest tests/int"}]
    errs = lint(_manifest(_node("a", reviewer="bob", contract=contract)), POOL)
    assert any("commands must be non-empty strings" in error for error in errs)


def test_integration_gate_rejects_non_string_name_without_crashing():
    contract = _valid_contract()
    contract.integration_gates[0]["name"] = ["gate-1"]

    errs = lint(_manifest(_node("a", reviewer="bob", contract=contract)), POOL)

    assert any("name must be a non-empty string" in error for error in errs)


def test_quality_rejects_non_string_outcome_ref_without_crashing():
    contract = _valid_contract()
    contract.quality.business_tests[0]["outcome_refs"] = [{"id": "outcome-x"}]
    errs = lint(_manifest(_node("a", reviewer="bob", contract=contract)), POOL)
    assert any("outcome_refs must be non-empty strings" in error for error in errs)


def test_quality_rejects_non_string_real_dependency_without_crashing():
    contract = _valid_contract()
    contract.quality.business_tests[0]["real_dependencies"] = [{"name": "postgres"}]
    errs = lint(_manifest(_node("a", reviewer="bob", contract=contract)), POOL)
    assert any("real_dependencies must be non-empty strings" in error for error in errs)

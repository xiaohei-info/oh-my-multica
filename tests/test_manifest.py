"""core.manifest:load/save 往返、env 展开、set_node、contract 解析。"""
import os

import pytest

from omac.core import manifest as manifest_mod
from omac.core.manifest import (
    load_manifest,
    loads_manifest,
    manifest_write_lock,
    save_manifest,
    set_node,
)
from omac.errors import ValidationError

BASIC = """\
meta:
  name: demo
nodes:
  - id: a
    worker: alice
    blocked_by: []
  - id: b
    worker: bob
    reviewer: alice
    blocked_by: [a]
    contract:
      objective: do b
      acceptance: ["b works"]
      non_goals: ["no scope creep"]
      verification_commands: ["pytest tests/b"]
      integration_gates:
        - name: b-gate
          layer: L1
          delivery_goal: b delivers
          source_of_truth: ["docs/design.md#b"]
          covers: [route]
          acceptance_refs: ["b works"]
          commands: ["pytest tests/integration/b"]
      pr_base: feature/v1
"""


def _write(tmp_path, content, name="m.yaml"):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_load_basic(tmp_path):
    m = load_manifest(_write(tmp_path, BASIC))
    assert set(m.nodes) == {"a", "b"}
    assert m.nodes["b"].blocked_by == ["a"]
    assert m.nodes["b"].contract.objective == "do b"
    assert m.nodes["b"].contract.coverage_gate == 90  # 缺省


def test_roundtrip_preserves_state(tmp_path):
    path = _write(tmp_path, BASIC)
    m = load_manifest(path)
    set_node(m, "a", work_item_id="42", status="done")
    save_manifest(m, path)
    m2 = load_manifest(path)
    assert m2.nodes["a"].work_item_id == "42"
    assert m2.nodes["a"].status == "done"
    assert m2.nodes["b"].contract.pr_base == "feature/v1"


def test_save_manifest_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = _write(tmp_path, BASIC)
    original = open(path, encoding="utf-8").read()
    manifest = load_manifest(path)
    manifest.nodes["a"].status = "done"

    def fail_after_partial_write(data, stream, **kwargs):
        stream.write("meta:\n  name: truncated\nnodes:\n")
        raise OSError("simulated interrupted dump")

    monkeypatch.setattr(manifest_mod.yaml, "dump", fail_after_partial_write)

    with pytest.raises(OSError, match="interrupted dump"):
        save_manifest(manifest, path)

    assert open(path, encoding="utf-8").read() == original


def test_manifest_write_lock_rejects_second_writer(tmp_path):
    path = _write(tmp_path, BASIC)

    with manifest_write_lock(path):
        with pytest.raises(ValidationError, match="Another `omac dag run`"):
            with manifest_write_lock(path):
                pass


def test_scope_paths_optional_roundtrip():
    """scope_paths 可选:填了则往返保留,没填则 dump 不出现(适配无结构的新项目)。"""
    from omac.core.manifest import Contract, _dump_contract, _load_contract
    c = Contract(objective="o", scope_paths=["src/auth/**", "tests/auth/**"])
    dumped = _dump_contract(c)
    assert dumped["scope_paths"] == ["src/auth/**", "tests/auth/**"]
    assert _load_contract(dumped).scope_paths == ["src/auth/**", "tests/auth/**"]
    # 没填时 dump 里不出现该键(向后兼容,不硬塞空字段)
    assert "scope_paths" not in _dump_contract(Contract(objective="o"))


def test_quality_contract_roundtrip():
    from omac.core.manifest import Contract, QualityContract, _dump_contract, _load_contract

    quality = QualityContract(
        required_outcomes=[{"id": "outcome-x", "source_ref": "acceptance#flow.action"}],
        business_tests=[{
            "id": "test-x",
            "outcome_refs": ["outcome-x"],
            "command": "pytest tests/int",
            "level": "integration",
            "real_dependencies": ["postgres"],
            "must_fail_on_base": True,
        }],
        runtime_data_policy="real-or-error",
    )
    dumped = _dump_contract(Contract(objective="o", quality=quality))
    loaded = _load_contract(dumped)

    assert loaded.quality == quality
    assert dumped["quality"]["runtime_data_policy"] == "real-or-error"


@pytest.mark.parametrize(
    ("raw_quality", "message"),
    [
        ("invalid", "contract.quality must be an object"),
        ({"required_outcomes": None}, "quality.required_outcomes must be a list"),
        ({"business_tests": None}, "quality.business_tests must be a list"),
    ],
)
def test_quality_contract_rejects_malformed_shape(raw_quality, message):
    from omac.core.manifest import _load_contract

    with pytest.raises(ValueError, match=message):
        _load_contract({"quality": raw_quality})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_of_truth", "docs/design.md"),
        ("required_contracts", "docs/shared.md"),
        ("acceptance", "flow-x"),
        ("non_goals", "none"),
        ("verification_commands", "pytest -q"),
        ("integration_gates", {"name": "gate-1"}),
        ("scope_paths", "src/**"),
    ],
)
def test_contract_list_fields_reject_non_list_raw_values(field, value):
    from omac.core.manifest import _load_contract

    with pytest.raises(ValueError, match=rf"contract\.{field} must be a list"):
        _load_contract({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("objective", []),
        ("pr_base", 42),
    ],
)
def test_contract_scalar_fields_reject_non_string_raw_values(field, value):
    from omac.core.manifest import _load_contract

    with pytest.raises(
        ValueError, match=rf"contract\.{field} must be a string"
    ):
        _load_contract({field: value})


def test_load_manifest_records_dot_omac_project_root(tmp_path):
    project_root = tmp_path / "project"
    manifest_dir = project_root / ".omac"
    manifest_dir.mkdir(parents=True)

    manifest = load_manifest(_write(manifest_dir, "meta: {}\nnodes: []\n"))

    assert manifest.project_root == str(project_root.resolve())


@pytest.mark.parametrize("content", ["", "null\n", "[]\n"])
def test_load_manifest_rejects_non_mapping_top_level(tmp_path, content):
    path = _write(tmp_path, content)

    with pytest.raises(ValueError, match="manifest must be an object"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("meta: invalid\nnodes: []\n", "manifest.meta must be an object"),
        ("meta: {}\nnodes: {id: x}\n", "manifest.nodes must be a list"),
        (
            "meta: {}\nnodes:\n  - invalid\n",
            r"manifest\.nodes\[0\] must be an object",
        ),
    ],
)
def test_loads_manifest_rejects_malformed_nested_shapes(content, message):
    with pytest.raises(ValueError, match=message):
        loads_manifest(content)


def test_env_expansion(tmp_path, monkeypatch):
    content = "meta:\n  ws: \"${OMAC_TEST_WS:-fallback}\"\nnodes: []\n"
    path = _write(tmp_path, content)
    assert load_manifest(path).meta["ws"] == "fallback"
    monkeypatch.setenv("OMAC_TEST_WS", "real-ws")
    assert load_manifest(path).meta["ws"] == "real-ws"


def test_set_node_unknown_key(tmp_path):
    m = load_manifest(_write(tmp_path, BASIC))
    try:
        set_node(m, "nope", status="done")
        assert False, "should raise"
    except KeyError:
        pass


def test_missing_worker_rejected(tmp_path):
    bad = "meta: {}\nnodes:\n  - id: x\n"
    try:
        load_manifest(_write(tmp_path, bad))
        assert False, "should raise"
    except ValueError:
        pass


def test_duplicate_node_ids_are_rejected_instead_of_overwriting_business_work():
    content = """\
meta: {}
nodes:
  - id: checkout
    worker: alice
  - id: checkout
    worker: bob
"""

    with pytest.raises(ValueError, match="duplicate node id: checkout"):
        loads_manifest(content)


@pytest.mark.parametrize(
    ("field", "yaml_value", "message"),
    [
        ("id", "[checkout]", r"manifest\.nodes\[0\]\.id must be a non-empty string"),
        ("worker", "[alice]", r"manifest\.nodes\[0\]\.worker must be a non-empty string"),
        ("blocked_by", "checkout", r"manifest\.nodes\[0\]\.blocked_by must be a list"),
        ("blocked_by", "[checkout, 42]", r"manifest\.nodes\[0\]\.blocked_by must contain non-empty strings"),
        ("reviewer", "[bob]", r"manifest\.nodes\[0\]\.reviewer must be a string or null"),
        ("work_item_id", "42", r"manifest\.nodes\[0\]\.work_item_id must be a string or null"),
        ("status", "[todo]", r"manifest\.nodes\[0\]\.status must be a non-empty string"),
        ("status", "completed", r"manifest\.nodes\[0\]\.status must be one of"),
        ("merged", "\"false\"", r"manifest\.nodes\[0\]\.merged must be boolean"),
        ("merged_at", "[now]", r"manifest\.nodes\[0\]\.merged_at must be a string or null"),
    ],
)
def test_manifest_node_fields_reject_malformed_raw_types(field, yaml_value, message):
    content = f"""\
meta: {{}}
nodes:
  - id: checkout
    worker: alice
    {field}: {yaml_value}
"""

    with pytest.raises(ValueError, match=message):
        loads_manifest(content)


@pytest.mark.parametrize(
    ("field", "yaml_value", "message"),
    [
        ("closeout_node", "[closeout]", "manifest.meta.closeout_node must be a string or null"),
        ("acceptance_file", "[acceptance.yaml]", "manifest.meta.acceptance_file must be a string or null"),
        ("acceptance_required", "\"yes\"", "manifest.meta.acceptance_required must be boolean"),
    ],
)
def test_manifest_meta_fields_reject_malformed_raw_types(field, yaml_value, message):
    content = f"meta:\n  {field}: {yaml_value}\nnodes: []\n"

    with pytest.raises(ValueError, match=message):
        loads_manifest(content)

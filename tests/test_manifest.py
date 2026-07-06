"""core.manifest:load/save 往返、env 展开、set_node、contract 解析。"""
import os

from omac.core.manifest import load_manifest, save_manifest, set_node

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


def test_scope_paths_optional_roundtrip():
    """scope_paths 可选:填了则往返保留,没填则 dump 不出现(适配无结构的新项目)。"""
    from omac.core.manifest import Contract, _dump_contract, _load_contract
    c = Contract(objective="o", scope_paths=["src/auth/**", "tests/auth/**"])
    dumped = _dump_contract(c)
    assert dumped["scope_paths"] == ["src/auth/**", "tests/auth/**"]
    assert _load_contract(dumped).scope_paths == ["src/auth/**", "tests/auth/**"]
    # 没填时 dump 里不出现该键(向后兼容,不硬塞空字段)
    assert "scope_paths" not in _dump_contract(Contract(objective="o"))


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

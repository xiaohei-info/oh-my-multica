"""core.manifest:load/save 往返、env 展开、set_node、contract 解析。"""
import pytest

from omac.core import manifest as manifest_mod
from omac.core.manifest import (
    load_manifest,
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


def test_manifest_write_lock_uses_real_path_identity(tmp_path):
    path = _write(tmp_path, BASIC)
    alias = tmp_path / "manifest-alias.yaml"
    alias.symlink_to(path)

    with manifest_write_lock(path):
        with pytest.raises(ValidationError, match="dag amend propose"):
            with manifest_write_lock(alias):
                pass


@pytest.mark.parametrize("lock_path", ["target", "alias"])
def test_manifest_save_and_lock_share_canonical_symlink_target(
    tmp_path, monkeypatch, lock_path,
):
    target = tmp_path / "manifest.yaml"
    target.write_text(BASIC)
    target.chmod(0o640)
    alias = tmp_path / "manifest-alias.yaml"
    alias.symlink_to(target)
    original_link = alias.readlink()
    manifest = load_manifest(str(alias))
    set_node(manifest, "a", work_item_id="42", status="done")
    monkeypatch.chdir(tmp_path)
    held_path = target if lock_path == "target" else "manifest-alias.yaml"

    with manifest_write_lock(str(held_path)):
        save_manifest(manifest, "manifest-alias.yaml")

        assert alias.is_symlink()
        assert alias.readlink() == original_link
        assert load_manifest(str(target)).nodes["a"].status == "done"
        assert target.stat().st_mode & 0o777 == 0o640
        for second_path in (target, "manifest-alias.yaml"):
            with pytest.raises(ValidationError, match="dag amend propose"):
                with manifest_write_lock(str(second_path)):
                    pass


def test_manifest_alias_save_failure_preserves_link_target_and_cleans_temp(
    tmp_path, monkeypatch,
):
    target = tmp_path / "manifest.yaml"
    target.write_text(BASIC)
    alias = tmp_path / "manifest-alias.yaml"
    alias.symlink_to(target)
    original = target.read_text()
    manifest = load_manifest(str(alias))
    set_node(manifest, "a", status="done")

    def fail_after_partial_write(data, stream, **kwargs):
        stream.write("meta:\n  name: partial\n")
        raise OSError("alias dump failed")

    monkeypatch.setattr(manifest_mod.yaml, "dump", fail_after_partial_write)

    with pytest.raises(OSError, match="alias dump failed"):
        save_manifest(manifest, str(alias))

    assert alias.is_symlink()
    assert target.read_text() == original
    assert list(tmp_path.glob(".manifest.yaml.*.tmp")) == []


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


# ==================== recovery_marker(reconcile 按需读取的轻量恢复标记) ====================

def test_recovery_marker_defaults_false_and_is_omitted_when_false(tmp_path):
    """新字段缺省 False;False 不落盘(旧 manifest 向后兼容,不新增噪声字段)。"""
    path = _write(tmp_path, BASIC)
    m = load_manifest(path)
    assert m.nodes["a"].recovery_marker is False
    assert m.nodes["b"].recovery_marker is False

    save_manifest(m, path)
    import yaml as _yaml
    raw = _yaml.safe_load(open(path, encoding="utf-8").read())
    assert all("recovery_marker" not in node for node in raw["nodes"])


def test_recovery_marker_roundtrip_true(tmp_path):
    """True 往返保留:save 落盘、load 读回。"""
    path = _write(tmp_path, BASIC)
    m = load_manifest(path)
    m.nodes["a"].recovery_marker = True
    save_manifest(m, path)

    m2 = load_manifest(path)
    assert m2.nodes["a"].recovery_marker is True
    assert m2.nodes["b"].recovery_marker is False


def test_recovery_marker_loads_legacy_manifest_without_field(tmp_path):
    """旧 manifest(无 recovery_marker 字段)加载为 False,不报错。"""
    legacy = (
        "meta: {}\nnodes:\n"
        "  - id: a\n    worker: alice\n    work_item_id: \"7\"\n"
        "    status: blocked\n"
    )
    m = load_manifest(_write(tmp_path, legacy))
    assert m.nodes["a"].recovery_marker is False


def test_recovery_marker_clear_roundtrip(tmp_path):
    """标记可被清除:True → False 后再落盘不再携带该字段。"""
    path = _write(tmp_path, BASIC)
    m = load_manifest(path)
    m.nodes["a"].recovery_marker = True
    save_manifest(m, path)
    m.nodes["a"].recovery_marker = False
    save_manifest(m, path)

    m2 = load_manifest(path)
    assert m2.nodes["a"].recovery_marker is False
    import yaml as _yaml
    raw = _yaml.safe_load(open(path, encoding="utf-8").read())
    assert all("recovery_marker" not in node for node in raw["nodes"])

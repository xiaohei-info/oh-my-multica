"""core primitives for P4.3: merge_increment + lint_increment."""
import pytest

from omac.core.lint import lint_increment
from omac.core.manifest import (
    Contract, Manifest, Node, load_manifest, merge_increment, save_manifest,
)

POOL = {"alice", "bob", "charlie"}


def _node(id, worker="alice", **kw):
    return Node(id=id, worker=worker, **kw)


def _manifest(*nodes):
    return Manifest(meta={}, nodes={n.id: n for n in nodes})


# ── merge_increment ──────────────────────────────────────────────

def test_merge_adds_new_nodes():
    m = _manifest(_node("a", status="done"))
    inc = _manifest(_node("fix-1", worker="bob", blocked_by=["a"]))
    merge_increment(m, inc)
    assert "fix-1" in m.nodes
    assert m.nodes["fix-1"].blocked_by == ["a"]
    assert m.nodes["a"].status == "done"  # original untouched


def test_merge_id_conflict():
    m = _manifest(_node("a"))
    inc = _manifest(_node("a", worker="bob"))
    with pytest.raises(ValueError, match="conflict"):
        merge_increment(m, inc)


def test_merge_preserves_done_and_order(tmp_path):
    path = str(tmp_path / "m.yaml")
    m = Manifest(meta={"name": "fx"}, nodes={
        "a": Node(id="a", worker="alice", status="done"),
        "b": Node(id="b", worker="bob", blocked_by=["a"], status="done"),
    })
    save_manifest(m, path)
    inc = _manifest(_node("fix-b", worker="alice", blocked_by=["b"]))
    m2 = load_manifest(path)
    merge_increment(m2, inc)
    save_manifest(m2, path)
    m3 = load_manifest(path)
    assert list(m3.nodes) == ["a", "b", "fix-b"]
    assert m3.nodes["a"].status == "done"
    assert m3.nodes["fix-b"].blocked_by == ["b"]


# ── lint_increment ───────────────────────────────────────────────

def test_lint_increment_valid_dep_on_existing():
    existing = _manifest(_node("a"))
    inc = _manifest(_node("fix-1", worker="bob", blocked_by=["a"]))
    assert lint_increment(inc, existing, POOL) == []


def test_lint_increment_unknown_dep():
    existing = _manifest(_node("a"))
    inc = _manifest(_node("fix-1", blocked_by=["ghost"]))
    errs = lint_increment(inc, existing, POOL)
    assert any("unknown node" in e for e in errs)


def test_lint_increment_id_conflict():
    existing = _manifest(_node("a"))
    inc = _manifest(_node("a"))
    errs = lint_increment(inc, existing, POOL)
    assert any("conflict" in e for e in errs)


def test_lint_increment_cycle():
    existing = _manifest(_node("a"))
    inc = _manifest(
        _node("x", worker="bob", blocked_by=["y"]),
        _node("y", worker="alice", blocked_by=["x"]),
    )
    errs = lint_increment(inc, existing, POOL)
    assert any("cycle" in e for e in errs)


def test_lint_increment_preserves_existing_done():
    """increment should pass even if existing has a done node not in pool lint."""
    existing = Manifest(meta={}, nodes={
        "a": Node(id="a", worker="alice", status="done", work_item_id="1"),
    })
    inc = _manifest(_node("fix-1", worker="bob", blocked_by=["a"]))
    # worker bob is in pool; should be clean
    assert lint_increment(inc, existing, POOL) == []

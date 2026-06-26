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

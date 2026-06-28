# tests/test_graph.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from core import frontier, downstream_of
from core.graph import is_done, all_terminal

def iss(key, status, blocked_by=None):
    return {"key": key, "id": key, "status": status,
            "blocked_by": blocked_by or [], "worker": "w", "reviewer": None,
            "review_verdict": None}

def test_frontier_root_ready_when_todo():
    issues = {"M0": iss("M0", "todo")}
    assert frontier(issues) == ["M0"]

def test_frontier_blocked_until_dep_done():
    issues = {"M0": iss("M0", "in_progress"), "M1": iss("M1", "todo", ["M0"])}
    assert frontier(issues) == []           # M0 未 done，M1 不 ready；M0 在飞不算 frontier
    issues["M0"]["status"] = "done"
    assert frontier(issues) == ["M1"]       # M0 done → M1 解锁

def test_inflight_and_done_excluded_from_frontier():
    issues = {"A": iss("A", "in_review"), "B": iss("B", "done"), "C": iss("C", "todo")}
    assert frontier(issues) == ["C"]

def test_all_terminal_done_or_cancelled():
    assert all_terminal({"A": iss("A", "done"), "B": iss("B", "cancelled")})
    assert not all_terminal({"A": iss("A", "done"), "B": iss("B", "todo")})

def test_is_done():
    assert is_done(iss("A", "done"))
    assert not is_done(iss("B", "in_review"))

def test_downstream_of_blocks_dependents_only():
    issues = {
        "A": iss("A", "blocked"),            # 失败
        "B": iss("B", "todo", ["A"]),        # 依赖失败 → 下游
        "C": iss("C", "todo", ["B"]),        # 传递下游
        "D": iss("D", "done"),               # 无关健康
        "E": iss("E", "todo", ["D"]),        # 健康分支，应仍 ready
    }
    assert downstream_of(issues, {"A"}) == {"B", "C"}
    # 健康分支 E 不受影响，仍在 frontier
    assert "E" in frontier(issues)

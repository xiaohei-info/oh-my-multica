"""core.graph:就绪节点、失败下游、终态判定。"""
from omac.core.graph import all_terminal, downstream_of, ready_nodes


def _issues(**kw):
    return {k: {"status": v[0], "blocked_by": v[1]} for k, v in kw.items()}


def test_ready_nodes_respects_dependencies():
    issues = _issues(a=("done", []), b=("todo", ["a"]), c=("todo", ["b"]))
    assert ready_nodes(issues) == ["b"]


def test_ready_nodes_skips_non_todo():
    issues = _issues(a=("in_progress", []), b=("todo", ["a"]))
    assert ready_nodes(issues) == []


def test_downstream_of_transitive():
    issues = _issues(a=("todo", []), b=("todo", ["a"]), c=("todo", ["b"]), d=("todo", []))
    assert downstream_of(issues, {"a"}) == {"b", "c"}


def test_all_terminal():
    assert all_terminal(_issues(a=("done", []), b=("abandoned", [])))
    assert not all_terminal(_issues(a=("done", []), b=("in_review", [])))


def test_ready_nodes_abandoned_satisfies_dep():
    """abandoned 上游视同依赖已满足(§2.4 P1.4)。"""
    issues = _issues(a=("abandoned", []), b=("todo", ["a"]))
    assert ready_nodes(issues) == ["b"]


def test_downstream_of_excludes_independent():
    issues = _issues(a=("todo", []), b=("todo", ["a"]), c=("todo", []))
    assert downstream_of(issues, {"a"}) == {"b"}

"""core.graph:就绪节点、失败下游、终态判定。"""
from types import SimpleNamespace

from omac.core.graph import downstream_of, held_nodes, node_waves, ready_nodes, running_nodes


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


def test_running_nodes_include_the_manifest_sub_states_and_nothing_waiting_for_a_decision():
    nodes = {k: SimpleNamespace(status=k) for k in
             ("todo", "in_progress", "ci_check", "in_review", "merging", "done", "blocked", "failed", "abandoned")}
    assert running_nodes(nodes) == ["in_progress", "ci_check", "in_review", "merging"]


def test_ready_nodes_abandoned_satisfies_dep():
    """abandoned 上游视同依赖已满足(§2.4 P1.4)。"""
    issues = _issues(a=("abandoned", []), b=("todo", ["a"]))
    assert ready_nodes(issues) == ["b"]


def test_downstream_of_excludes_independent():
    issues = _issues(a=("todo", []), b=("todo", ["a"]), c=("todo", []))
    assert downstream_of(issues, {"a"}) == {"b"}


class _N:
    def __init__(self, blocked_by):
        self.blocked_by = blocked_by


def test_node_waves_roots_are_zero():
    nodes = {"a": _N([]), "b": _N([]), "c": _N(["a", "b"])}
    assert node_waves(nodes) == {"a": 0, "b": 0, "c": 1}


def test_node_waves_chain():
    nodes = {"a": _N([]), "b": _N(["a"]), "c": _N(["b"])}
    assert node_waves(nodes) == {"a": 0, "b": 1, "c": 2}


def test_a_held_node_is_not_ready_its_downstream_stays_unsatisfied_and_an_unrelated_branch_is_ready():
    issues = _issues(a=("done", []), held=("todo", ["a"]), after=("todo", ["held"]), other=("todo", ["a"]))
    issues["held"]["held"] = True

    assert ready_nodes(issues) == ["other"]
    assert held_nodes(issues) == ["held"]


def test_releasing_a_held_node_makes_it_ready_again():
    issues = _issues(a=("done", []), held=("todo", ["a"]))
    issues["held"]["held"] = False

    assert ready_nodes(issues) == ["held"] and held_nodes(issues) == []


def test_only_a_todo_node_counts_as_held():
    issues = _issues(a=("done", []), b=("abandoned", []))
    issues["a"]["held"] = issues["b"]["held"] = True

    assert held_nodes(issues) == []

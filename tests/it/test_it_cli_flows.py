"""The CLI flows a person drives (run, inspect, retry, abandon, resume) against a real server with scripted agents."""
import json

from _support.it_project import contract
from omac.core.manifest import load_manifest


def _nodes(*names, blocked_by=None):
    return [{"id": name, "worker": "alice", "title": name, "description": "d", "contract": contract(),
             **({"blocked_by": blocked_by[name]} if blocked_by and name in blocked_by else {})} for name in names]


def _stuck_first(stack, project, name):
    """`first` finishes its Run without submitting and has no automatic retry left: the DAG needs a decision."""
    project.configure(retry={"worker": 0})
    stack.set_scenario("alice", [[{"exit": 1}]])
    first, second = f"{name}-first", f"{name}-second"
    manifest = project.manifest(name, _nodes(first, second, blocked_by={second: [first]}))
    result = project.omac("dag", "run", manifest, "--max-minutes", "2", "--output", "json")
    assert result.returncode == 20, result.stdout + result.stderr
    return manifest, json.loads(result.stdout), first, second


def test_table_output_lists_every_node(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest("table", _nodes("tbl-a", "tbl-b", blocked_by={"tbl-b": ["tbl-a"]}))
    done = project.omac("dag", "run", manifest, "--max-minutes", "3")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "tbl-a" in done.stdout and "tbl-b" in done.stdout


def test_a_worker_that_never_submits_needs_a_decision_and_retry_converges(project, stack):
    manifest, data, first, second = _stuck_first(stack, project, "retry-flow")
    report = data["report"]
    assert set(report) == {"failed_nodes", "blocked_downstream", "next_actions"}
    assert first in [n["key"] for n in report["failed_nodes"]]
    assert report["blocked_downstream"] == [second]
    assert any("node retry" in a and first in a for a in report["next_actions"])

    shown = json.loads(project.omac("node", "show", manifest, first, "--output", "json").stdout)
    assert (shown["node_key"], shown["status"]) == (first, "blocked")

    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    retried = json.loads(project.omac("node", "retry", manifest, first).stdout)
    assert (retried["status"], retried["node_key"]) == ("todo", first)
    assert retried["work_item_id"] == shown["work_item_id"]  # the same issue is reused

    resumed = project.omac("dag", "run", manifest, "--max-minutes", "3", "--output", "json")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert sorted(json.loads(resumed.stdout)["done"]) == sorted([first, second])


def test_abandoning_a_blocked_node_unlocks_its_downstream(project, stack):
    manifest, _, first, second = _stuck_first(stack, project, "abandon-flow")
    abandoned = json.loads(project.omac("node", "abandon", manifest, first).stdout)
    assert abandoned["status"] == "abandoned" and second in abandoned["affected_downstream"]

    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    resumed = project.omac("dag", "run", manifest, "--max-minutes", "3", "--output", "json")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert json.loads(resumed.stdout)["done"] == [second]
    nodes = load_manifest(manifest).nodes
    assert (nodes[first].status, nodes[second].status) == ("abandoned", "done")


def test_one_round_at_a_time_never_creates_an_issue_twice(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest("bounded", _nodes("rnd-a", "rnd-b", blocked_by={"rnd-b": ["rnd-a"]}))
    seen = {}
    for _ in range(40):
        done = project.omac("dag", "run", manifest, "--max-rounds", "1", "--output", "json")
        assert done.returncode in (0, 10), done.stdout + done.stderr
        for key, node in load_manifest(manifest).nodes.items():
            if node.work_item_id:
                assert seen.setdefault(key, node.work_item_id) == node.work_item_id, "a node must keep its issue"
        if done.returncode == 0:
            break
    else:
        raise AssertionError("the DAG did not converge in 40 single-round runs")
    assert sorted(seen) == ["rnd-a", "rnd-b"]


def test_ci_check_and_merge_each_run_once_per_node(project, stack, tmp_path):
    log = tmp_path / "ci.log"
    project.configure(ci={"check_command": f"sh -c 'echo {{pr_url}} >> {log}'", "timeout_minutes": 5})
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    stack.set_scenario("bob", [[stack.review("pass")]])
    merges_before = len(stack.gh_calls("merge"))
    manifest = project.manifest("delivery", [{"id": "dlv", "worker": "alice", "reviewer": "bob", "title": "dlv",
                                              "description": "d", "contract": contract()}])
    done = project.omac("dag", "run", manifest, "--max-minutes", "3", "--output", "json")
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(log.read_text().splitlines()) == 1, "the CI check ran once"
    assert len(stack.gh_calls("merge")) - merges_before == 1, "the merge ran once"
    assert load_manifest(manifest).nodes["dlv"].merged is True

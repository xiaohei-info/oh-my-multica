"""OMAC driving a real Multica server with scripted agents: the dispatch -> Run -> submit -> converge loop."""
import json

from _support.it_project import contract
from omac.core.manifest import load_manifest


def test_single_node_dag_converges_with_a_scripted_worker(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest("solo", [
        {"id": "solo", "worker": "alice", "title": "Solo", "description": "d", "contract": contract()}])
    done = project.omac("dag", "run", manifest, "--max-minutes", "2", "--output", "json")
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout)
    assert (result["state"], result["done"]) == ("converged", ["solo"])
    item_id = load_manifest(manifest).nodes["solo"].work_item_id
    assert len(json.loads(stack.multica("issue", "runs", item_id, "--output", "json"))) == 1  # dispatched once


def _node(name, **extra):
    return {"id": name, "worker": "alice", "reviewer": "bob", "title": name, "description": "d",
            "contract": contract(), **extra}


def test_a_rejected_delivery_is_reworked_then_merged_exactly_once(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")], [stack.tool("worker-submit")]])
    stack.set_scenario("bob", [[stack.review("reject")], [stack.review("pass")]])
    merges_before = len(stack.gh_calls("merge"))
    manifest = project.manifest("reviewed", [_node("reviewed")])
    done = project.omac("dag", "run", manifest, "--max-minutes", "3", "--output", "json")
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["done"] == ["reviewed"]
    assert len(stack.gh_calls("merge")) - merges_before == 1, "reject must not merge; the pass merges once"
    item_id = load_manifest(manifest).nodes["reviewed"].work_item_id
    runs = json.loads(stack.multica("issue", "runs", item_id, "--output", "json"))
    assert len(runs) == 4  # worker, reviewer(reject), worker(rework), reviewer(pass)


def test_a_dependent_node_starts_only_after_its_blocker_is_merged(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    stack.set_scenario("bob", [[stack.review("pass")]])
    manifest = project.manifest("chain", [_node("first"), _node("second", blocked_by=["first"])])
    done = project.omac("dag", "run", manifest, "--max-minutes", "4", "--output", "json")
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(json.loads(done.stdout)["done"]) == ["first", "second"]
    nodes = load_manifest(manifest).nodes
    created = {key: json.loads(stack.multica("issue", "get", node.work_item_id, "--output", "json"))["created_at"]
               for key, node in nodes.items()}
    assert created["first"] <= created["second"]

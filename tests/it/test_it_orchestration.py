"""OMAC driving a real Multica server with scripted agents: the dispatch -> Run -> submit -> converge loop."""
import json

from _support.it_project import contract


def test_single_node_dag_converges_with_a_scripted_worker(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest("solo", [
        {"id": "solo", "worker": "alice", "title": "Solo", "description": "d", "contract": contract()}])
    done = project.omac("dag", "run", manifest, "--max-minutes", "2", "--output", "json")
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout)
    assert (result["state"], result["done"]) == ("converged", ["solo"])
    assert len(stack.fake_runs()) == 1  # one Run, one submit: nothing was dispatched twice

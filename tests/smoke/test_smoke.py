"""Smoke run: minimal DAGs on a private Docker Multica, executed by a real Coding Agent.

    OMAC_SMOKE_AGENT=real      the daemon launches the Agent CLI named by OMAC_SMOKE_RUNTIME (codex, pi, claude)
    OMAC_SMOKE_AGENT=scripted  dry run of the same harness with the scripted agent (checks the plumbing)

Marked `live`, so `pytest tests/ -m "not live"` never starts it. The workflow is .github/workflows/smoke.yml.
"""
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ -> _support

from _support.it_project import Project, contract  # noqa: E402
from _support.it_stack import Stack, require_docker  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.docker]
REAL = os.environ.get("OMAC_SMOKE_AGENT", "scripted") == "real"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    require_docker()
    instance = Stack(tmp_path_factory.mktemp("smoke"), real_agent=REAL)
    try:
        instance.up()
        for name in ("alice", "bob"):
            instance.agent(name)
        yield instance
    finally:
        instance.down()


@pytest.fixture()
def project(stack, tmp_path):
    return Project(tmp_path, stack)


def _run(project, manifest, minutes):
    done = project.omac("dag", "run", manifest, "--max-minutes", str(minutes), "--output", "json",
                          timeout=minutes * 60 + 120)
    if done.returncode:  # a real Reviewer's verdict and comment explain a failure that the DAG state alone does not
        nodes = yaml.safe_load(Path(manifest).read_text())["nodes"]
        evidence = [project.omac("node", "show", manifest, node["id"], "--output", "json").stdout for node in nodes]
        pytest.fail(done.stdout + done.stderr + "\n".join(evidence), pytrace=False)
    return json.loads(done.stdout)


def test_single_node_development_task(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest("smoke-solo", [{
        "id": "smoke-solo", "worker": "alice", "title": "Add a greeting file",
        "description": "Create hello.txt containing the word hello, push it on a branch, open a pull request.",
        "contract": contract(acceptance=["hello.txt exists and contains hello"])}])
    assert _run(project, manifest, 25)["done"] == ["smoke-solo"]


def test_two_nodes_with_review(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    stack.set_scenario("bob", [[stack.review("pass")]])
    nodes = [{"id": "smoke-a", "worker": "alice", "reviewer": "bob", "title": "A", "description": "Create a.txt.",
              "contract": contract(acceptance=["a.txt exists"])},
             {"id": "smoke-b", "worker": "alice", "reviewer": "bob", "title": "B", "description": "Create b.txt.",
              "blocked_by": ["smoke-a"], "contract": contract(acceptance=["b.txt exists"])}]
    manifest = project.manifest("smoke-pair", nodes)
    assert sorted(_run(project, manifest, 40)["done"]) == ["smoke-a", "smoke-b"]

"""Weekly smoke run: minimal DAGs on a private Docker Multica, executed by a real Coding Agent.

    OMAC_SMOKE_AGENT=real      the daemon launches the `claude` CLI on PATH (costs money; see ledger.py)
    OMAC_SMOKE_AGENT=scripted  dry run of the same harness with the scripted agent (free; checks the plumbing)

Marked `live`, so `pytest tests/ -m "not live"` never starts it. The workflow is .github/workflows/smoke.yml.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ -> _support

from _support.it_project import Project, contract  # noqa: E402
from _support.it_stack import Stack, require_docker  # noqa: E402
from omac.core.manifest import load_manifest  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.docker]
REAL = os.environ.get("OMAC_SMOKE_AGENT", "scripted") == "real"
FALLBACK_USD_PER_RUN = float(os.environ.get("OMAC_SMOKE_FALLBACK_USD_PER_RUN", "1.0"))


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


def _costs(stack, manifest):
    """(usd, estimated) over every Run of every node: the Agent's own `total_cost_usd`, else a flat estimate."""
    usd, estimated = 0.0, False
    for node in load_manifest(manifest).nodes.values():
        for run in json.loads(stack.multica("issue", "runs", node.work_item_id, "--output", "json")):
            messages = stack.multica("issue", "run-messages", run["id"], "--issue", node.work_item_id, "--output", "json")
            found = [float(v) for v in re.findall(r'"total_cost_usd"\s*:\s*([0-9.]+)', messages.replace('\\"', '"'))]
            if found:
                usd += max(found)
            else:
                usd, estimated = usd + FALLBACK_USD_PER_RUN, True
    return usd, estimated


def _record(stack, manifest):
    usd, estimated = _costs(stack, manifest)
    ledger = Path(__file__).parent / "ledger.py"
    subprocess.run([sys.executable, str(ledger), "record", "--usd", f"{usd:.4f}", *(["--estimated"] if estimated else [])],
                   check=True)


def _run(project, manifest, minutes):
    done = project.omac("dag", "run", manifest, "--max-minutes", str(minutes), "--output", "json")
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def test_single_node_development_task(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest("smoke-solo", [{
        "id": "smoke-solo", "worker": "alice", "title": "Add a greeting file",
        "description": "Create hello.txt containing the word hello, push it on a branch, open a pull request.",
        "contract": contract(acceptance=["hello.txt exists and contains hello"])}])
    try:
        assert _run(project, manifest, 25)["done"] == ["smoke-solo"]
    finally:
        _record(stack, manifest)


def test_two_nodes_with_review(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    stack.set_scenario("bob", [[stack.review("pass")]])
    nodes = [{"id": "smoke-a", "worker": "alice", "reviewer": "bob", "title": "A", "description": "Create a.txt.",
              "contract": contract(acceptance=["a.txt exists"])},
             {"id": "smoke-b", "worker": "alice", "reviewer": "bob", "title": "B", "description": "Create b.txt.",
              "blocked_by": ["smoke-a"], "contract": contract(acceptance=["b.txt exists"])}]
    manifest = project.manifest("smoke-pair", nodes)
    try:
        assert sorted(_run(project, manifest, 40)["done"]) == ["smoke-a", "smoke-b"]
    finally:
        _record(stack, manifest)

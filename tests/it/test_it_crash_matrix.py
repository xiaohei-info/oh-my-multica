"""Fail `omac` right after each write it makes (PLAN 6.3.3), restart, and check nothing is done twice.

Two faults per write: ``kill_after`` (the server applied the write, then the process died) and ``drop_after``
(the server applied the write, the response never arrived, the process carries on without knowing).

The write requests of a one-node DAG are enumerated from a baseline run, so a new write added to the dispatch
path makes ``test_the_matrix_covers_every_write`` fail until the matrix grows with it.
"""
import json
import os
import re
import shlex
import subprocess
import sys

import pytest

from _support.it_project import contract
from _support.it_proxy import Proxy, Rule

WRITES = 19  # write requests omac makes for a one-node develop DAG, from the baseline in the test below


def _node(name):
    return [{"id": name, "worker": "alice", "title": "Solo", "description": "d", "contract": contract()}]


def _issues(stack, name):
    out = json.loads(stack.multica("issue", "list", "--output", "json", "--limit", "100"))
    rows = out["issues"] if isinstance(out, dict) else out
    return [r for r in rows if f"[DAG:{name}]" in r["title"]]


def test_the_matrix_covers_every_write(project, stack):
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest("baseline", _node("baseline"))
    proxy = Proxy(stack.url).start()
    try:
        done = project.omac("dag", "run", manifest, "--max-minutes", "2", "--output", "json", server_url=proxy.url)
        assert done.returncode == 0, done.stdout + done.stderr
        assert len(proxy.writes()) == WRITES
    finally:
        proxy.stop()


@pytest.mark.parametrize("fault", ["kill_after", "drop_after"])
@pytest.mark.parametrize("k", range(1, WRITES + 1))
def test_a_fault_after_write_k_never_duplicates_an_effect(project, stack, k, fault):
    name = f"{fault.split('_')[0]}{k}"
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    manifest = project.manifest(name, _node(name))
    proxy = Proxy(stack.url).start()
    proxy.rules.append(Rule("WRITE", "", fault, nth=k))
    env = {**os.environ, **stack.cli_env(proxy.url), **stack.omac_env()}
    first = subprocess.Popen(
        [sys.executable, "-m", "omac", "dag", "run", manifest, "--max-minutes", "2", "--output", "json"],
        cwd=project.work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proxy.kill_pid = first.pid
    try:
        _out, first_err = first.communicate(timeout=170)
    finally:
        proxy.stop()
    if fault == "kill_after":
        assert first.returncode == -9, "the kill rule should have fired"

    resumed = project.omac("dag", "run", manifest, "--max-minutes", "2", "--output", "json")
    resumes = [("resume", resumed.returncode, resumed.stderr[-2500:])]
    orphan = re.search(r"multica issue status (\S+) cancelled", resumed.stdout + resumed.stderr)
    if resumed.returncode == 2:
        # Crashed between creating the issue and recording it: fail closed, say how to clear it, then resume.
        assert orphan, resumed.stdout + resumed.stderr
        stack.multica("issue", "status", orphan.group(1), "cancelled")
        resumed = project.omac("dag", "run", manifest, "--max-minutes", "2", "--output", "json")
    retried = resumed.returncode == 20
    resumes.append(("after-orphan", resumed.returncode, resumed.stderr[-2500:]))
    if retried:
        # An unknown merge outcome is never replayed: the node stops and the report names the explicit decision.
        retry = next(a for a in json.loads(resumed.stdout)["report"]["next_actions"] if a.startswith("omac node retry"))
        assert project.omac(*shlex.split(retry)[1:]).returncode == 0
        resumed = project.omac("dag", "run", manifest, "--max-minutes", "2", "--output", "json")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert json.loads(resumed.stdout)["done"] == [name]

    live = [i for i in _issues(stack, name) if i["status"] != "cancelled"]
    assert len(live) == 1, "exactly one live issue for the node"
    runs = json.loads(stack.multica("issue", "runs", live[0]["id"], "--output", "json"))
    assert 1 <= len(runs) <= 1 + retried, (
        "the Worker is dispatched once, plus at most once per explicit retry; OMAC's writes in the faulted process: "
        f"{[(m, p.split('/', 4)[-1], b[:90]) for m, p, b in proxy.bodies]}; first exit {first.returncode}, resumed {resumes}; "
        f"Runs: {[(r.get('id'), r.get('status'), r.get('created_at'), r.get('completed_at'), (r.get('attribution') or {}).get('evidence')) for r in runs]}; "
        f"first stderr: {first_err[-1500:]}")

"""Run ``run_task`` (authoring -> review -> revisions) against the private Docker Multica with scripted agents."""
from __future__ import annotations

import sys
import time

import yaml

from _support.it_project import contract
from omac.core.taskmeta import TaskKind
from omac.pipeline import tasks


def submit_step(stack, *flags: str) -> dict:
    """A scenario step that runs ``omac work submit <issue> <flags>`` the way a real Agent does."""
    return {"run": [sys.executable, "-m", "omac", "work", "submit", "{issue}", *flags]}


def decompose_manifest(*commands: str, worker: str = "alice") -> str:
    """A one-node manifest that passes lint, whose node verifies with ``commands`` (the first one is what a test varies)."""
    node_contract = contract(
        verification_commands=list(commands), scope_paths=["src/login/**"], evidence_mode="fixture", produces=[], consumes=[],
        source_of_truth=["docs/login.md"])
    node_contract["integration_gates"][0]["commands"] = list(commands)
    return yaml.safe_dump({"meta": {"name": "demo"}, "nodes": [{
        "id": "node-a", "worker": worker, "blocked_by": [], "description": "Implement the login behavior.",
        "contract": node_contract}]}, sort_keys=False)


def run(kit, kind: TaskKind, payload: dict, assignee: str, *, timeout: float = 90.0, **kwargs):
    """``tasks.run_task`` with a real wait between observations that gives up after ``timeout`` seconds."""
    deadline = time.time() + timeout

    def poll():
        if time.time() > deadline:
            raise AssertionError(f"run_task still waiting after {timeout}s")
        time.sleep(0.1)

    kwargs.setdefault("poll", poll)
    return tasks.run_task(kit.engine, kind, payload, assignee, **kwargs)

"""Drive the Loop (`tick`, `collect_results`, the dispatch helpers) in-process against the private Docker Multica.

The store, runtime and code host are the real adapters; agents are the scripted ones of tests/it/agent; the code host
is the `gh` shim. A test arranges the world through the real store API, runs Loop code, and may wrap *single methods*
of the real adapters to inject a fault or to observe the order of writes (the port boundary is the only place a test
touches). There is no stand-in platform.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

import yaml

from omac.core.manifest import Contract, Manifest, Node, save_manifest
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop

from _support.it_project import Project

AGENTS = ("alice", "bob", "carol")
HANG = [[{"hang": 600}]]          # the Run stays active until the test cancels it
IDLE = [[]]                       # the Run completes at once without submitting anything


def contract(**overrides) -> Contract:
    base = dict(
        objective="do it", acceptance=["works"], non_goals=["no creep"], verification_commands=["pytest -q"],
        integration_gates=[{
            "name": "gate-1", "layer": "L1", "delivery_goal": "delivers", "source_of_truth": ["docs/d.md"],
            "covers": ["route"], "acceptance_refs": ["works"], "commands": ["pytest tests/int"],
            "required_metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"]}],
        pr_base="feature/v1", coverage_gate=90)
    return Contract(**{**base, **overrides})


class Kit:
    def __init__(self, stack, engine, tmp_path, monkeypatch):
        self.stack, self.engine, self.tmp_path = stack, engine, tmp_path
        self.store, self.runtime, self.code_host = engine.store, engine.runtime, engine.code_host
        self.workspace = stack.workspace_id
        monkeypatch.setenv("PATH", f"{stack.bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("OMAC_GH_STATE", str(stack.gh_state))
        # The clock is an external boundary: no retry backoff. Observation intervals stay real: a Run takes a moment to show up.
        monkeypatch.setattr(loop, "_TRANSIENT_RUNTIME_RETRY_BACKOFF_SECONDS", 0.0)
        monkeypatch.setattr(loop, "_HANDOFF_TERMINAL_GRACE_SECONDS", 0)  # tests that need the window set it
        self.monkeypatch = monkeypatch
        # The Loop commits its manifest to the repository it runs in: give it a throwaway one with a bare remote.
        self.project = Project(tmp_path, stack)
        monkeypatch.chdir(self.project.work)
        self.agents("hang")
        self._seq = 0

    # ---- scripted agents
    def agents(self, mode: str | list, *, names=AGENTS) -> None:
        """"hang" keeps Runs active, "idle" ends them without a submit, or pass explicit scenario attempts."""
        attempts = {"hang": HANG, "idle": IDLE}.get(mode, mode) if isinstance(mode, str) else mode
        for name in names:
            self.stack.set_scenario(name, attempts)

    # ---- manifest
    def node(self, key, worker="alice", blocked_by=None, reviewer=None, contract=None, title=None) -> Node:
        return Node(id=key, worker=worker, blocked_by=blocked_by or [], reviewer=reviewer, contract=contract,
                    title=title or key, description=f"Task {key}")

    def manifest(self, nodes, meta=None) -> Manifest:
        merged = {"workspace_id": self.workspace, "project_id": self.stack.project_id, **(meta or {})}
        return Manifest(meta=merged, nodes={n.id: n for n in nodes})

    def save(self, manifest: Manifest, name="dag.yaml") -> str:
        path = str(self.project.work / ".omac" / name)
        save_manifest(manifest, path)
        return path

    # ---- loop
    def tick(self, manifest, path, **kwargs):
        kwargs.setdefault("max_parallel", 4)
        return loop.tick(self.store, self.runtime, self.code_host, manifest, path, **kwargs)

    def settle(self, manifest, path, *, timeout=90.0, **kwargs):
        """Tick until the DAG is no longer running; real Runs finish asynchronously, so poll."""
        deadline, result = time.time() + timeout, None
        while time.time() < deadline:
            result = self.tick(manifest, path, **kwargs)
            if result.state != "running":
                return result
            time.sleep(0.4)
        raise AssertionError(f"DAG still running after {timeout}s: {result}")

    def tick_until(self, manifest, path, predicate, *, timeout=60.0, what="condition", **kwargs):
        """Tick (idempotent) until ``predicate()`` holds; real Runs and the clock advance between ticks."""
        return self.wait_for(lambda: (self.tick(manifest, path, **kwargs), predicate())[1], timeout=timeout, what=what)

    def wait_for(self, predicate: Callable[[], Any], *, timeout=30.0, what="condition"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.3)
        raise AssertionError(f"timed out waiting for {what}")

    # ---- arrangement through the real store
    def item(self, key, *, worker="alice", reviewer=None, contract=None, status=WorkItemStatus.TODO,
             assign=False, dag_key=None, **metadata):
        item = self.store.create_work_item(
            self.workspace, key, f"Task {key}", dag_key=dag_key or key, worker=worker, reviewer=reviewer)
        if contract is not None:
            self.store.set_node_contract(item.id, contract)
        if metadata:
            self.store.update_work_item_metadata(item.id, **metadata)
        if assign:
            self.assign(item.id, worker)
        if status is not WorkItemStatus.TODO:
            self.store.update_status(item.id, status)
        return self.store.get_work_item(item.id)

    def comments(self, item_id: str) -> list[str]:
        """Bodies of the comments on an issue, oldest first (read through the platform CLI, as an operator would)."""
        rows = json.loads(self.stack.multica("issue", "comment", "list", item_id, "--output", "json") or "[]")
        rows = rows.get("comments", rows) if isinstance(rows, dict) else rows
        return [row.get("content", row.get("body", "")) for row in rows]

    def assign(self, item_id: str, worker: str) -> None:
        """Assign as an earlier process would have: this process keeps no memory of the assignment having started a Run."""
        self.store.assign_work_item(item_id, worker, "worker")
        self.store._pending_assignment_wakes.discard(item_id)

    def done_dependency(self, key, **extra):
        """A merged upstream node and its Done issue, as the Loop leaves them."""
        item = self.item(key, artifacts={"pr_url": f"https://example.test/pr/{key}"}, status=WorkItemStatus.DONE)
        node = self.node(key, title=key)
        node.status, node.merged, node.merged_at, node.work_item_id = "done", True, "2026-07-26T08:00:00Z", item.id
        return node, item

    # ---- code host (gh shim)
    def pr(self, url: str, *, pushes: int = 1, merged: bool = False) -> None:
        path = self.stack.gh_state / "prs.json"
        prs = json.loads(path.read_text()) if path.exists() else {}
        prs[url] = {"pushes": pushes, **({"merged": True, "merged_at": "2026-07-26T08:00:00Z"} if merged else {})}
        path.write_text(json.dumps(prs))

    def merges(self) -> list[list[str]]:
        return self.stack.gh_calls("merge")

    # ---- wrapping single port methods (fault injection / write order)
    def record(self, target, *names) -> list[tuple]:
        """Log (name, args) of each call to the real ``target.<name>`` while still performing it."""
        events: list[tuple] = []
        depth = [0]  # an adapter method that calls another wrapped method internally is one write for the Loop
        for name in names:
            original = getattr(target, name)

            def wrapper(*args, _name=name, _original=original, **kw):
                if not depth[0]:
                    events.append((_name, args, kw))
                depth[0] += 1
                try:
                    return _original(*args, **kw)
                finally:
                    depth[0] -= 1

            self.monkeypatch.setattr(target, name, wrapper)
        return events

    def fault(self, target, name, *, nth=1, when="after", error: Exception | None = None, only: Callable | None = None):
        """Make the nth call to the real ``target.<name>`` fail: ``when="before"`` never reaches the platform
        (the write did not happen), ``when="after"`` performs it and then raises (the write happened, the caller
        never learns it). Returns a dict with the call count; set ``state["armed"] = False`` to stop injecting."""
        original, state = getattr(target, name), {"calls": 0, "fired": False, "armed": True}

        def wrapper(*args, **kw):
            if not state["armed"] or (only is not None and not only(*args, **kw)):
                return original(*args, **kw)
            state["calls"] += 1
            if state["calls"] != nth:
                return original(*args, **kw)
            state["fired"], state["args"], state["kwargs"] = True, args, kw
            failure = error or PlatformError(f"injected timeout in {name}")
            if when == "before":
                raise failure
            original(*args, **kw)
            raise failure

        self.monkeypatch.setattr(target, name, wrapper)
        return state

    def forbid(self, target, *names) -> None:
        import pytest

        for name in names:
            self.monkeypatch.setattr(
                target, name, lambda *a, _n=name, **k: pytest.fail(f"{_n} must not be called"))

    def run_ended(self, item_id: str) -> bool:
        runs = self.runtime.list_runs(item_id)
        return bool(runs) and all(run.terminal for run in runs)

    def wait_runs_ended(self, item_id: str, **kw) -> None:
        self.wait_for(lambda: self.run_ended(item_id), what=f"the Runs of {item_id} to end", **kw)

"""dag status/snapshot pure rules: the locked report schema, rendering, the needs-decision report built from given observations.

Reports built from a real platform, `omac dag status` and the tick report: tests/it/test_it_cli_dag.py.
"""
import json
from types import SimpleNamespace

import pytest

from _support.state import save_literal
from omac.cli import exit_codes
from omac.cli.commands.dag import _assemble_engine
from omac.cli.main import main
from omac.core.manifest import load_manifest
from omac.core.workitem import WorkItem, WorkItemControlProjection, WorkItemPayload, WorkItemStatus
from omac.pipeline.report import (
    HELD_KEYS, NEEDS_DECISION_KEYS, NODE_KEYS, PROGRESS_KEYS, STATUS_REPORT_KEYS, _next_actions, build_held_report,
    build_manifest_status_report, build_needs_decision, render_table,
)


def _manifest_yaml(tmp_path, nodes):
    return save_literal(tmp_path / "dag.yaml", {"meta": {"name": "test-dag"}, "nodes": nodes})


def test_dag_engine_maps_config_project_to_multica_batch_capability(
        tmp_path):
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(
        "engine: multica\nworkspace: ws\nproject: project-1\n")
    manifest = omac_dir / "dag.yaml"
    manifest.write_text("meta: {}\nnodes: []\n")

    engine, _ = _assemble_engine(SimpleNamespace(manifest=str(manifest)))

    assert engine.store.config.project_id == "project-1"
    assert engine.store.control_batch_observation_supported() is True


def test_needs_decision_report_prioritizes_convergence_amendment_action():
    actions = _next_actions([{
        "key": "resource-runs",
        "decision_required": {
            "reason_code": "review-convergence-scope-expanding",
            "next_action": (
                "omac dag amend propose .omac/dag.yaml --report-file report.md "
                "--docs docs --blocked-node resource-runs --output json"
            ),
        },
    }], ".omac/dag.yaml")

    assert actions == [
        "omac dag amend propose .omac/dag.yaml --report-file report.md "
        "--docs docs --blocked-node resource-runs --output json"
    ]


def test_the_report_keeps_the_stage_a_recorded_retry_names():
    """A Reviewer-side stop is retried with `--stage review`; the report must not weaken it into a retry of authoring."""
    recorded = "omac node retry .omac/dag.yaml a --stage review"

    actions = _next_actions([{"key": "a", "decision_required": {"reason_code": "reviewer-run-dispatch-unresolved", "next_action": recorded}},
                             {"key": "b", "decision_required": {"reason_code": "x", "next_action": "inspect the facts first"}},
                             {"key": "c", "decision_required": None}], ".omac/dag.yaml")

    assert actions == [recorded, "omac node abandon .omac/dag.yaml a",
                       "omac node retry .omac/dag.yaml b", "omac node abandon .omac/dag.yaml b",
                       "omac node retry .omac/dag.yaml c", "omac node abandon .omac/dag.yaml c"]


def test_the_report_schema_is_locked():
    assert STATUS_REPORT_KEYS == ("manifest", "progress", "nodes", "needs_decision")
    assert PROGRESS_KEYS == ("total", "done", "running", "todo", "blocked", "failed", "abandoned", "converged")
    assert NODE_KEYS == ("key", "status", "worker", "reviewer", "work_item_id", "pr_url", "blocked_by", "held", "hold_reason")
    assert NEEDS_DECISION_KEYS == ("failed_nodes", "blocked_downstream", "next_actions")
    assert HELD_KEYS == ("held_nodes", "blocked_downstream", "next_actions")


def test_the_table_lists_the_nodes_and_the_progress():
    report = {"manifest": "m.yaml", "progress": {"total": 2, "done": 1, "running": 0, "todo": 1, "blocked": 0, "failed": 0, "abandoned": 0,
                                                   "converged": False},
              "nodes": [{"key": "a", "status": "done", "worker": "alice", "reviewer": None, "work_item_id": "1", "pr_url": "https://pr/1",
                         "blocked_by": [], "held": False, "hold_reason": None},
                        {"key": "b", "status": "todo", "worker": "bob", "reviewer": None, "work_item_id": None, "pr_url": None, "blocked_by": ["a"],
                         "held": True, "hold_reason": "needs the test account"}],
              "needs_decision": None}

    table = render_table(report)

    assert all(header in table for header in ("KEY", "STATUS", "WORKER")) and "alice" in table and "bob" in table and "done" in table.lower()
    assert "todo (held)" in table and "Held: b - needs the test account" in table


def test_the_status_snapshot_shows_a_held_node_and_its_reason(tmp_path):
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "todo", "held": True, "hold_reason": "needs the test account"},
        {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]}])

    rows = {row["key"]: row for row in build_manifest_status_report(load_manifest(path), path)["nodes"]}

    assert (rows["a"]["held"], rows["a"]["hold_reason"]) == (True, "needs the test account")
    assert (rows["b"]["held"], rows["b"]["hold_reason"]) == (False, None)


def test_the_held_report_lists_held_nodes_their_downstream_and_the_release_commands(tmp_path):
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "todo", "held": True, "hold_reason": "needs the test account"},
        {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]},
        {"id": "c", "worker": "bob", "status": "todo", "blocked_by": ["b"]},
        {"id": "d", "worker": "bob", "status": "done"}])

    report = build_held_report(load_manifest(path), path, ["a"])

    assert tuple(report) == HELD_KEYS
    assert report["held_nodes"] == [{"key": "a", "reason": "needs the test account"}]
    assert report["blocked_downstream"] == ["b", "c"]
    assert report["next_actions"] == [f"omac node release {path} a"]


def test_a_missing_manifest_is_a_validation_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert main(["dag", "status", str(tmp_path / "nope.yaml")]) == exit_codes.VALIDATION


def test_the_engine_assembly_keeps_the_workspace_slug_of_the_config(tmp_path):
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text("engine: multica\nworkspace: ws\nproject: project-1\nworkspace_slug: acme-team\n")
    path = _manifest_yaml(omac_dir, [{"id": "a", "worker": "alice", "status": "todo"}])

    engine, _ = _assemble_engine(SimpleNamespace(manifest=path, engine=None, workspace=None, project=None))

    assert engine.store.config.extra["workspace_slug"] == "acme-team"



def test_snapshot_needs_no_engine_and_preserves_manifest(tmp_path, monkeypatch, capsys):
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "done"},
        {"id": "b", "worker": "bob", "status": "blocked",
         "blocked_by": ["a"]},
    ])
    before = open(path, "rb").read()

    import omac.cli.commands.dag as dag_mod
    monkeypatch.setattr(
        dag_mod, "_assemble_engine",
        lambda _args: pytest.fail("dag snapshot must not assemble an engine"),
    )

    assert main(["dag", "snapshot", path, "--output", "json"]) == exit_codes.OK

    data = json.loads(capsys.readouterr().out)
    assert set(data) == set(STATUS_REPORT_KEYS)
    assert data["progress"] == {
        "total": 2, "done": 1, "running": 0, "todo": 0,
        "blocked": 1, "failed": 0, "abandoned": 0,
        "converged": False,
    }
    assert [node["status"] for node in data["nodes"]] == ["done", "blocked"]
    assert data["needs_decision"]["failed_nodes"][0]["reason"] == "blocked"
    assert open(path, "rb").read() == before


def test_build_needs_decision_consumes_observations_only(tmp_path):
    """报告仅由传入的观察快照构建:字段映射与 deferred payload 语义与
    build_status_report 一致(同一 _build_needs_decision_from_items)。"""
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "blocked", "work_item_id": "1"},
        {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]},
    ])
    manifest = load_manifest(path)
    item = WorkItem(
        id="1", workspace_id="ws", title="A", description="d",
        status=WorkItemStatus.BLOCKED,
        dag_key="a",
        artifacts={"pr_url": "https://pr/1"},
        review_verdict="reject",
        review_comment="tests missing",
    )
    projection = WorkItemControlProjection(
        item, frozenset({WorkItemPayload.VERIFICATION}))

    report = build_needs_decision(
        manifest, path, {"a"}, {"a": projection, "b": None},
        evidence={"a": "ci failed"})

    assert set(report.keys()) == set(NEEDS_DECISION_KEYS)
    assert len(report["failed_nodes"]) == 1
    node = report["failed_nodes"][0]
    assert node["key"] == "a"
    assert node["status"] == "blocked"
    assert node["reason"] == "ci failed"
    assert node["work_item_id"] == "1"
    assert node["pr_url"] == "https://pr/1"
    # deferred payload 按「引用已声明」计存在,与 build_status_report 相同
    assert node["evidence_summary"] == {
        "review_verdict": "reject",
        "review_comment": "tests missing",
        "has_verification": True,
        "has_review": False,
    }
    assert report["blocked_downstream"] == ["b"]
    assert report["next_actions"] == [
        f"omac node retry {path} a",
        f"omac node abandon {path} a",
    ]


def test_build_needs_decision_none_observation_equals_legacy_tolerance(tmp_path):
    """None 观察(≙ 旧 _fetch_items 查找失败 / _MISSING_WORK_ITEM)→ None item。"""
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "failed", "work_item_id": "1"},
    ])
    manifest = load_manifest(path)

    report = build_needs_decision(manifest, path, {"a"}, {"a": None})

    assert set(report.keys()) == set(NEEDS_DECISION_KEYS)
    node = report["failed_nodes"][0]
    assert node["pr_url"] is None
    assert node["evidence_summary"] is None
    # 无精确原因且无 item 时回退 node.status,与旧 _fetch_items 宽容路径一致
    assert node["reason"] == "failed"


# ---- dag run --wait-decisions (S20) and dag decisions ------------------------------------------------------------------

class Events:
    def __init__(self):
        self.seen = []

    def info(self, event, **fields):
        self.seen.append((event, fields))

    def named(self, event):
        return [fields for name, fields in self.seen if name == event]


class WaitingRun:
    """`dag run --wait-decisions` over a two-node DAG whose first node is blocked; the platform is a scripted tick."""

    def __init__(self, tmp_path, monkeypatch, **args):
        from omac.cli.commands import dag as dag_cmd
        from omac.core.manifest import manifest_write_lock
        from omac.core.manifest import save_manifest
        from omac.pipeline.loop import TickResult

        monkeypatch.chdir(tmp_path)
        self.dag_cmd, self.lock = dag_cmd, manifest_write_lock
        self.path = _manifest_yaml(tmp_path, [
            {"id": "a", "worker": "alice", "status": "blocked", "work_item_id": "wi-a"},
            {"id": "b", "worker": "bob", "status": "todo", "blocked_by": ["a"]}])
        self.events, self.sleeps, self.ticks, self.acts = Events(), [], 0, {}
        self.now = 0.0
        self.args = SimpleNamespace(
            manifest=self.path, engine=None, workspace=None, project=None, max_parallel=None, max_rounds=None,
            max_minutes=None, wait_decisions=True, output="json", **args)

        def tick(_engine, manifest, path, **_kw):
            self.ticks += 1
            waiting = [key for key, node in manifest.nodes.items() if node.status in ("blocked", "failed")]
            if waiting:
                return TickResult(state="needs_decision", failed=waiting, report={"next_actions": []})
            for node in manifest.nodes.values():
                node.status = "done"
            save_manifest(manifest, path)                 # like the real tick
            return TickResult(state="converged", done=list(manifest.nodes))

        def sleep(seconds):
            self.sleeps.append(seconds)
            with self.lock(self.path):                       # the write lock is free while the run waits
                pass
            if len(self.sleeps) in self.acts:
                self.acts[len(self.sleeps)](self)
            self.now += 60

        engine = SimpleNamespace(store=SimpleNamespace(config=SimpleNamespace(engine_type="multica")))
        monkeypatch.setattr(dag_cmd, "_assemble_engine", lambda _args: (engine, None))
        monkeypatch.setattr(dag_cmd, "ensure_config_synced", lambda *_a, **_k: None)
        monkeypatch.setattr(dag_cmd, "_scheduled_tick", tick)
        monkeypatch.setattr(dag_cmd, "_emit", lambda *_a, **_k: None)
        monkeypatch.setattr(dag_cmd, "log", self.events)
        monkeypatch.setattr(dag_cmd, "time", SimpleNamespace(sleep=sleep, monotonic=lambda: self.now))

    def person_retries(self, _self=None):
        from omac.core.manifest import save_manifest

        manifest = load_manifest(self.path)
        manifest.nodes["a"].status = "todo"
        save_manifest(manifest, self.path)

    def run(self):
        return self.dag_cmd._loop_or_single(self.args, single_round=False)


def test_s20_a_waiting_run_announces_each_decision_once_resumes_after_the_person_acts_and_converges(tmp_path, monkeypatch):
    run = WaitingRun(tmp_path, monkeypatch)
    run.acts[3] = WaitingRun.person_retries           # nobody does anything for two polls, then `node retry`

    assert run.run() == exit_codes.OK

    announced = run.events.named("decision_pending")
    assert [(e["decision_key"], e["node"], e["kind"]) for e in announced] == [("a:blocked:wi-a", "a", "blocked")]
    assert announced[0]["next_actions"] == [f"omac node retry {run.path} a", f"omac node abandon {run.path} a"]
    assert len(run.sleeps) == 3 and run.ticks == 2    # one tick that found the decision, one after the retry
    assert {node.status for node in load_manifest(run.path).nodes.values()} == {"done"}


def test_s20_a_decision_that_comes_back_after_it_was_resolved_is_announced_again(tmp_path, monkeypatch):
    run = WaitingRun(tmp_path, monkeypatch)
    seen_ticks = []
    original = run.dag_cmd._scheduled_tick

    def block_the_first_retry_again(engine, manifest, path, **kw):
        seen_ticks.append(1)
        if len(seen_ticks) == 2:                       # the retried node stops again on the same work item
            manifest.nodes["a"].status = "blocked"
        return original(engine, manifest, path, **kw)

    monkeypatch.setattr(run.dag_cmd, "_scheduled_tick", block_the_first_retry_again)
    run.acts[1] = WaitingRun.person_retries
    run.acts[2] = WaitingRun.person_retries

    assert run.run() == exit_codes.OK

    assert [e["decision_key"] for e in run.events.named("decision_pending")] == ["a:blocked:wi-a", "a:blocked:wi-a"]


def test_s20_the_wall_clock_ends_the_wait_with_exit_10_not_20(tmp_path, monkeypatch):
    run = WaitingRun(tmp_path, monkeypatch)
    run.args.max_minutes = 3

    assert run.run() == exit_codes.IN_PROGRESS

    assert len(run.events.named("decision_pending")) == 1 and run.ticks == 1 and len(run.sleeps) == 3


def test_without_wait_decisions_a_pending_decision_still_exits_20(tmp_path, monkeypatch):
    from omac.errors import NeedsDecision

    run = WaitingRun(tmp_path, monkeypatch)
    run.args.wait_decisions = False

    with pytest.raises(NeedsDecision):
        run.run()
    assert not run.sleeps and not run.events.named("decision_pending")


def test_dag_decisions_lists_each_stop_once_with_a_stable_key_and_the_commands(tmp_path, capsys):
    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "status": "blocked", "work_item_id": "wi-a"},
        {"id": "b", "worker": "bob", "status": "blocked", "blocked_by": ["a"]},        # only waits for a
        {"id": "c", "worker": "bob", "status": "todo", "held": True, "hold_reason": "needs the test account"},
        {"id": "d", "worker": "bob", "status": "done", "work_item_id": "wi-d"}])

    assert main(["dag", "decisions", path, "--output", "json"]) == exit_codes.OK

    rows = json.loads(capsys.readouterr().out)["decisions"]
    assert [(r["decision_key"], r["kind"], r["next_actions"][0]) for r in rows] == [
        ("a:blocked:wi-a", "blocked", f"omac node retry {path} a"),
        ("c:held:-", "held", f"omac node release {path} c")]
    assert rows[1]["reason"] == "needs the test account"


def test_s17_with_hold_live_a_ready_live_node_is_held_once_and_the_report_names_the_release_command(tmp_path):
    from omac.core import graph
    from omac.pipeline import loop

    path = _manifest_yaml(tmp_path, [
        {"id": "a", "worker": "alice", "contract": {"objective": "check against the real service", "evidence_mode": "live"}},
        {"id": "b", "worker": "bob", "blocked_by": ["a"]},
        {"id": "c", "worker": "bob"}])
    manifest = load_manifest(path)

    assert loop._auto_hold_live(manifest, path, graph.ready_nodes(loop._build_snapshot(manifest))) == ["c"]

    reloaded = load_manifest(path)
    assert (reloaded.nodes["a"].held, reloaded.dag_state["auto_held"]) == (True, ["a"])
    report = build_held_report(reloaded, path, graph.held_nodes(loop._build_snapshot(reloaded)))
    assert report["next_actions"] == [f"omac node release {path} a"] and report["blocked_downstream"] == ["b"]
    assert "live" in report["held_nodes"][0]["reason"]

    assert main(["node", "release", path, "a"]) == exit_codes.OK
    released = load_manifest(path)
    assert loop._auto_hold_live(released, path, graph.ready_nodes(loop._build_snapshot(released))) == ["a", "c"]   # not held again


def test_node_retry_during_a_live_dag_run_is_refused_and_the_loop_keeps_the_state(tmp_path, monkeypatch, capsys):
    """The retry arrives while the Loop holds the write lock (inside a tick): refused, so the Loop cannot overwrite it later."""
    from omac.cli.main import main

    run = WaitingRun(tmp_path, monkeypatch)
    original, attempts = run.dag_cmd._scheduled_tick, []

    def retry_mid_tick(engine, manifest, path, **kw):
        if not attempts:
            attempts.append(main(["node", "retry", path, "a"]))
            assert load_manifest(path).nodes["a"].status == "blocked"      # nothing was written behind the Loop's back
        return original(engine, manifest, path, **kw)

    monkeypatch.setattr(run.dag_cmd, "_scheduled_tick", retry_mid_tick)
    run.acts[1] = WaitingRun.person_retries                                 # after the Loop parks (lock free) the retry goes through

    assert run.run() == exit_codes.OK
    assert attempts == [exit_codes.VALIDATION]
    assert "Stop the running `omac dag run`" in capsys.readouterr().err
    assert {node.status for node in load_manifest(run.path).nodes.values()} == {"done"}


@pytest.mark.parametrize("sig, ignored_at_start", [("SIGINT", False), ("SIGINT", True), ("SIGTERM", False)])
def test_sigint_and_sigterm_stop_dag_run_after_the_current_tick(tmp_path, monkeypatch, sig, ignored_at_start):
    """A background job starts with SIGINT ignored; the Loop must still stop on it exactly like on SIGTERM."""
    import os
    import signal

    from omac.pipeline.loop import TickResult

    number = getattr(signal, sig)
    run = WaitingRun(tmp_path, monkeypatch)
    run.args.wait_decisions = False
    ticks = []

    def tick(_engine, manifest, path, **_kw):
        ticks.append(1)
        os.kill(os.getpid(), number)                       # delivered while the tick is running
        manifest.nodes["a"].status = "in_progress"         # the tick still finishes and saves its state
        from omac.core.manifest import save_manifest
        save_manifest(manifest, path)
        return TickResult(state="in_progress")

    monkeypatch.setattr(run.dag_cmd, "_scheduled_tick", tick)
    before = signal.getsignal(signal.SIGINT)
    if ignored_at_start:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    previous = signal.getsignal(signal.SIGINT)
    try:
        assert run.run() == exit_codes.IN_PROGRESS
        assert signal.getsignal(signal.SIGINT) is previous           # the caller's disposition is put back
    finally:
        signal.signal(signal.SIGINT, before)
    assert ticks == [1]
    assert load_manifest(run.path).nodes["a"].status == "in_progress"
    with run.lock(run.path):                                         # the write lock was released on exit
        pass


def _scripted_run(tmp_path, monkeypatch, steps):
    """`dag run` whose ticks follow ``steps`` ('idle' = nothing changed, 'moves' = a node advanced, 'done' = converged)."""
    from omac.core.manifest import save_manifest
    from omac.pipeline.loop import TickResult

    run = WaitingRun(tmp_path, monkeypatch)
    run.args.wait_decisions = False
    script = iter(steps)

    def tick(_engine, manifest, path, **_kw):
        run.ticks += 1
        step = next(script)
        if step == "done":
            return TickResult(state="converged")
        if step == "moves":
            manifest.nodes["a"].status = "in_progress" if manifest.nodes["a"].status == "todo" else "todo"
            save_manifest(manifest, path)
        return TickResult(state="running")

    monkeypatch.setattr(run.dag_cmd, "_scheduled_tick", tick)
    monkeypatch.setattr(run.dag_cmd, "time", SimpleNamespace(sleep=run.sleeps.append, monotonic=lambda: 0.0))   # lock stays held
    manifest = load_manifest(run.path)
    manifest.nodes["a"].status = "todo"
    save_manifest(manifest, run.path)
    return run


def test_ticks_that_change_nothing_wait_the_poll_interval_but_a_tick_that_moved_something_is_followed_at_once(tmp_path, monkeypatch):
    run = _scripted_run(tmp_path, monkeypatch, ["idle", "idle", "moves", "done"])

    assert run.run() == exit_codes.OK

    assert run.ticks == 4 and run.sleeps == [30, 30]          # defaults.poll_interval; none after the tick that moved a node


def test_the_wait_between_ticks_follows_the_configured_poll_interval(tmp_path, monkeypatch):
    run = _scripted_run(tmp_path, monkeypatch, ["idle", "done"])
    (tmp_path / ".omac").mkdir(exist_ok=True)
    (tmp_path / ".omac" / "config.yaml").write_text("defaults:\n  poll_interval: 7\n", encoding="utf-8")

    assert run.run() == exit_codes.OK and run.sleeps == [7]


def test_the_final_acceptance_loop_waits_the_default_poll_interval_when_none_is_configured():
    from omac.cli.commands.dag import _poll_interval

    assert _poll_interval({}) == 30 and _poll_interval({"defaults": {}}) == 30 and _poll_interval({"defaults": {"poll_interval": 5}}) == 5

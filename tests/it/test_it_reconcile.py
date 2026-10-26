"""Reconcile on a real Multica: what it reads, what it keeps when a read fails, and how the audit schedule advances.

Every read failure is injected at one method of the real adapter (`kit.reads_fail`); the issues are real.
"""
import contextlib
import uuid

import pytest

from _support.loopkit import contract
from omac.cli import exit_codes
from omac.cli.commands import dag
from omac.cli.main import main
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.core.workitem import WorkItemStatus
from omac.errors import AuthError, PlatformError
from omac.pipeline import loop, reconcile_audit
from omac.pipeline.loop import tick
from omac.pipeline.report import build_status_report

READS = ("observe_work_item_control", "observe_work_item_controls", "get_work_item")


@contextlib.contextmanager
def reads_fail(store, error, *, only=None):
    """Reads of the real Store raise ``error`` (for the issues in ``only``, or for all) until the block ends."""
    originals = {name: getattr(store, name) for name in READS}

    def wrap(name):
        def guarded(target, *args, **kwargs):
            ids = [target] if isinstance(target, str) else list(target)
            if only is None or set(ids) & set(only):
                raise error
            return originals[name](target, *args, **kwargs)
        return guarded

    for name in READS:
        setattr(store, name, wrap(name))
    try:
        yield
    finally:
        for name, original in originals.items():
            setattr(store, name, original)


def linked(key, item, status, **fields):
    return Node(id=key, worker="alice", work_item_id=item.id, status=status, **fields)


def saved(kit, nodes, tmp_name="manifest.yaml", meta=None):
    manifest = Manifest(meta=meta or {}, nodes={n.id: n for n in nodes})
    path = str(kit.tmp_path / tmp_name)
    save_manifest(manifest, path)
    return manifest, path


def pr_item(kit, key, status):
    return kit.item(key, status=status, artifacts={"pr_url": f"https://example.test/{key}"})


# ---- a read that fails leaves the manifest exactly as it was --------------------------------------------------------

def test_an_unreadable_work_item_keeps_every_manifest_business_state(kit):
    item = pr_item(kit, "a", WorkItemStatus.DONE)
    for error in (PlatformError("platform timeout"), AuthError("authentication expired")):
        for status in ("done", "in_review", "todo", "blocked"):
            merged = {"merged": True, "merged_at": "2026-07-27T00:00:00Z"} if status == "done" else {}
            manifest, path = saved(kit, [linked("a", item, status, **merged)])
            before = open(path, "rb").read()

            with reads_fail(kit.store, error), pytest.raises(type(error), match=str(error)):
                loop.reconcile(kit.store, kit.code_host, manifest, path, full_scan=True)

            node = manifest.nodes["a"]
            assert (node.status, node.work_item_id, node.merged) == (status, item.id, status == "done")
            assert open(path, "rb").read() == before


def test_one_failing_read_among_several_discards_every_partial_result(kit):
    items = {key: pr_item(kit, key, status) for key, status in (
        ("done", WorkItemStatus.DONE), ("review", WorkItemStatus.IN_REVIEW),
        ("todo", WorkItemStatus.IN_PROGRESS), ("blocked", WorkItemStatus.BLOCKED))}
    manifest, path = saved(kit, [
        linked("done", items["done"], "done", merged=True, merged_at="2026-07-27T00:00:00Z"),
        linked("review", items["review"], "in_review"), linked("todo", items["todo"], "todo"),
        linked("blocked", items["blocked"], "blocked")])
    before = open(path, "rb").read()

    with reads_fail(kit.store, PlatformError("fourth read timed out"), only=[items["blocked"].id]), \
            pytest.raises(PlatformError, match="fourth read timed out"):
        loop.reconcile(kit.store, kit.code_host, manifest, path, full_scan=True)

    assert [n.status for n in manifest.nodes.values()] == ["done", "in_review", "todo", "blocked"]
    assert load_manifest(path).nodes["todo"].status == "todo" and open(path, "rb").read() == before


def test_a_failing_read_stops_the_platform_writes_an_earlier_node_would_have_caused(kit):
    first, second = pr_item(kit, "first", WorkItemStatus.DONE), pr_item(kit, "second", WorkItemStatus.IN_PROGRESS)
    manifest, path = saved(kit, [linked("first", first, "done"), linked("second", second, "todo")])
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "add_comment", "assign_work_item")

    with reads_fail(kit.store, PlatformError("second read timed out"), only=[second.id]), \
            pytest.raises(PlatformError, match="second read timed out"):
        loop.reconcile(kit.store, kit.code_host, manifest, path)

    assert not writes and kit.store.get_work_item(first.id).status is WorkItemStatus.DONE
    assert kit.comments(first.id) == [] and load_manifest(path).nodes["first"].status == "done"


@pytest.mark.parametrize("error", [PlatformError("pull request observation timed out"),
                                   AuthError("pull request authentication expired")])
def test_an_unknown_pull_request_observation_keeps_the_manifest(kit, error):
    item = pr_item(kit, "a", WorkItemStatus.DONE)
    manifest, path = saved(kit, [linked("a", item, "done", merge_request_state="requested")])
    before = open(path, "rb").read()
    kit.fault(kit.code_host, "observe_pull_request", when="before", error=error)

    with pytest.raises(type(error), match=str(error)):
        loop.reconcile(kit.store, kit.code_host, manifest, path)

    node = manifest.nodes["a"]
    assert (node.status, node.merge_request_state) == ("done", "requested")
    assert kit.store.get_work_item(item.id).status is WorkItemStatus.DONE and open(path, "rb").read() == before


def test_a_work_item_the_platform_does_not_have_resets_the_node_for_dispatch(kit):
    manifest, path = saved(kit, [Node(id="a", worker="alice", work_item_id=str(uuid.uuid4()), status="in_progress")])

    assert loop.reconcile(kit.store, kit.code_host, manifest, path) is True

    assert (manifest.nodes["a"].status, manifest.nodes["a"].work_item_id) == ("todo", None)
    persisted = load_manifest(path).nodes["a"]
    assert (persisted.status, persisted.work_item_id) == ("todo", None)


@pytest.mark.parametrize("error,code", [(PlatformError("platform timeout"), exit_codes.PLATFORM),
                                        (AuthError("authentication expired"), exit_codes.AUTH)])
@pytest.mark.parametrize("action", ["tick", "status"])
def test_the_online_dag_commands_map_a_reconcile_error_to_its_exit_code_without_a_sync_commit(kit, error, code, action):
    item = kit.item("a", status=WorkItemStatus.IN_PROGRESS)
    manifest, path = saved(kit, [linked("a", item, "todo")])
    before = open(path, "rb").read()
    kit.monkeypatch.setattr(dag, "_assemble_engine", lambda _args: (kit.engine, kit.store.config))
    kit.monkeypatch.setattr(dag, "ensure_config_synced", lambda *a, **k: None)
    kit.monkeypatch.setattr(dag, "load_config", lambda _path: {})
    kit.monkeypatch.setattr(dag, "_load_config_for_manifest", lambda _path: {})
    commits = []
    kit.monkeypatch.setattr(loop, "commit_manifest", lambda *a, **k: commits.append(a))

    with reads_fail(kit.store, error):
        assert main(["dag", action, path, "--output", "json"]) == code

    assert not commits and open(path, "rb").read() == before and manifest.nodes["a"].status == "todo"


# ---- which issues an interval reconcile reads ----------------------------------------------------------------------

def control_reads(kit):
    return kit.record(kit.store, "observe_work_item_control", "observe_work_item_controls")


def read_ids(reads):
    return {i for name, args, _ in reads for i in ([args[0]] if name == "observe_work_item_control" else args[0])}


def test_an_interval_reconcile_reads_only_the_active_work_items(kit):
    active = kit.item("active")
    marked, static, closed = (kit.item(k, status=s) for k, s in (
        ("marked", WorkItemStatus.BLOCKED), ("static", WorkItemStatus.BLOCKED), ("closed", WorkItemStatus.DONE)))
    manifest, _ = saved(kit, [linked("active", active, "todo"), linked("marked", marked, "blocked", recovery_marker=True),
                              linked("static", static, "blocked"),
                              linked("closed", closed, "done", merged=True, merged_at="2026-01-01T00:00:00Z")])
    reads = control_reads(kit)

    observations, _ = loop._observe_reconcile_inputs(kit.store, kit.code_host, manifest)

    assert read_ids(reads) == {active.id, marked.id} and set(observations) == {"active", "marked"}
    assert all(name == "observe_work_item_control" for name, _, _ in reads)   # never the batch read


def project_scoped(kit):
    """The configuration `omac init` writes always names a project; only then does the Store batch-read static nodes."""
    kit.monkeypatch.setattr(kit.store.config, "project_id", kit.stack.project_id)


def test_a_full_scan_also_reads_the_static_work_items(kit):
    project_scoped(kit)
    static = kit.item("static", status=WorkItemStatus.BLOCKED)
    manifest, _ = saved(kit, [linked("static", static, "blocked")])
    reads = control_reads(kit)

    observations, _ = loop._observe_reconcile_inputs(kit.store, kit.code_host, manifest, full_scan=True)

    assert read_ids(reads) == {static.id} and observations["static"].work_item.id == static.id


def test_static_nodes_and_their_markers_are_untouched_when_nothing_observes_them(kit):
    blocked, failed, abandoned = (kit.item(k, status=WorkItemStatus.DONE) for k in ("blocked", "failed", "abandoned"))
    manifest, path = saved(kit, [linked("blocked", blocked, "blocked"), linked("failed", failed, "failed"),
                                 linked("abandoned", abandoned, "abandoned", recovery_marker=True)])   # abandoned is never read
    reads = control_reads(kit)

    assert loop.reconcile(kit.store, kit.code_host, manifest, path) is False

    assert not reads and [n.status for n in manifest.nodes.values()] == ["blocked", "failed", "abandoned"]
    assert manifest.nodes["abandoned"].recovery_marker is True


def test_a_marker_whose_recovery_fact_was_observed_clear_is_cleared(kit):
    item = kit.item("marked", status=WorkItemStatus.BLOCKED)
    manifest, path = saved(kit, [linked("marked", item, "blocked", recovery_marker=True)])

    assert loop.reconcile(kit.store, kit.code_host, manifest, path) is True

    assert (manifest.nodes["marked"].status, manifest.nodes["marked"].recovery_marker) == ("blocked", False)


def test_the_marker_is_persisted_before_the_recovery_fact_is_written(kit):
    item = kit.item("marked", status=WorkItemStatus.IN_PROGRESS)
    manifest, path = saved(kit, [linked("marked", item, "in_progress")])
    markers = []
    kit.before(kit.store, "update_work_item_metadata",
               lambda *a, **k: markers.append(load_manifest(path).nodes["marked"].recovery_marker))

    loop._block_reviewer(kit.store, manifest, path, "marked", item, "reviewer-run-baseline-unavailable", "test")

    assert markers[0] is True and load_manifest(path).nodes["marked"].recovery_marker is True


def test_a_restarted_marker_stays_active_until_the_recovery_fact_is_observed_cleared(kit):
    item = kit.item("marked", status=WorkItemStatus.BLOCKED)
    manifest, path = saved(kit, [linked("marked", item, "blocked")])
    manifest._recovery_manifest_path = path
    loop._mark_recovery_pending(manifest, "marked")
    kit.store.update_work_item_metadata(item.id, decision_required={"reason_code": "restart-test"})

    restarted = load_manifest(path)
    assert loop.reconcile_active_keys(restarted) == {"marked"}
    assert loop.reconcile(kit.store, kit.code_host, restarted, path) is False
    assert restarted.nodes["marked"].recovery_marker is True

    kit.store.update_work_item_metadata(item.id, decision_required={})
    assert loop.reconcile(kit.store, kit.code_host, restarted, path) is True
    assert restarted.nodes["marked"].recovery_marker is False and loop.reconcile_active_keys(load_manifest(path)) == set()


def test_clearing_one_recovery_fact_keeps_the_marker_while_another_is_pending(kit):
    from _support.taskmeta_samples import worker_handoff_intent

    item = kit.item("marked", status=WorkItemStatus.BLOCKED)
    manifest, path = saved(kit, [linked("marked", item, "blocked", recovery_marker=True)])
    manifest._recovery_manifest_path = path
    kit.store.update_work_item_metadata(item.id, worker_handoff=worker_handoff_intent(),
                                        decision_required={"reason_code": "still-pending"})
    cleared = kit.store.update_work_item_metadata(item.id, decision_required={})

    loop._clear_recovery_pending(manifest, "marked", cleared)

    assert cleared.worker_handoff is not None
    assert manifest.nodes["marked"].recovery_marker is True and load_manifest(path).nodes["marked"].recovery_marker is True


def test_the_status_report_asks_for_a_full_scan_and_leaves_the_audit_state_alone(kit):
    project_scoped(kit)
    static = kit.item("static", status=WorkItemStatus.BLOCKED)
    audit = {"schema": "omac.reconcile-audit/v1", "last_full_scan_at": "2026-08-11T12:00:00Z", "completed_interval_ticks": 1}
    manifest, path = saved(kit, [linked("static", static, "blocked")], meta={"reconcile_audit": audit})
    reads = control_reads(kit)

    build_status_report(manifest, kit.store, kit.code_host, path)

    assert read_ids(reads) == {static.id} and load_manifest(path).meta["reconcile_audit"] == audit


# ---- the audit schedule only advances after a tick has fully succeeded ---------------------------------------------

NOW = reconcile_audit.datetime(2026, 8, 11, 12, 0, tzinfo=reconcile_audit.timezone.utc)


def audit_case(kit, status="blocked"):
    project_scoped(kit)
    item = kit.item("node", status=WorkItemStatus.BLOCKED)
    manifest, path = saved(kit, [linked("node", item, status)])
    state = {"recorded": False}

    def record():
        state["recorded"] = True
        reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)
    return manifest, path, state, record


def run_tick(kit, manifest, path, record):
    return tick(kit.store, kit.runtime, kit.code_host, manifest, path, full_scan=True, after_successful_tick=record)


def test_a_failed_full_reconcile_does_not_advance_the_audit_state(kit):
    manifest, path, state, record = audit_case(kit, status="todo")   # an active node: its read must not be skipped

    with reads_fail(kit.store, PlatformError("simulated reconcile failure")), pytest.raises(PlatformError, match="simulated reconcile"):
        run_tick(kit, manifest, path, record)

    assert state["recorded"] is False and "reconcile_audit" not in load_manifest(path).meta


def test_a_full_tick_that_fails_after_reconcile_does_not_advance_the_audit_state(kit):
    manifest, path, state, record = audit_case(kit)

    def fail_collect(*args, **kwargs):
        raise PlatformError("simulated collect failure")

    kit.monkeypatch.setattr(loop, "collect_results", fail_collect)
    with pytest.raises(PlatformError, match="simulated collect failure"):
        run_tick(kit, manifest, path, record)

    assert state["recorded"] is False and "reconcile_audit" not in load_manifest(path).meta


def test_a_full_scan_whose_static_reads_fail_is_incomplete_and_stays_scheduled(kit):
    manifest, path, state, record = audit_case(kit)

    with reads_fail(kit.store, PlatformError("static batch timed out"), only=[manifest.nodes["node"].work_item_id]):
        result = run_tick(kit, manifest, path, record)

    assert result.audit_complete is False and state["recorded"] is False
    assert "reconcile_audit" not in load_manifest(path).meta
    assert reconcile_audit.should_full_scan(manifest, {"full_scan_interval_ticks": 2, "full_scan_max_age_seconds": 1800}, now=NOW)


def test_a_failed_audit_state_write_does_not_advance_the_audit_state(kit):
    manifest, path, _, _ = audit_case(kit)

    def fail_after_success():
        raise PlatformError("simulated audit state write failure")

    with pytest.raises(PlatformError, match="simulated audit"):
        run_tick(kit, manifest, path, fail_after_success)

    assert "reconcile_audit" not in load_manifest(path).meta


def test_the_audit_state_is_recorded_after_the_needs_decision_report_and_before_the_save(kit):
    from omac.pipeline import report as report_mod

    manifest, path, _, _ = audit_case(kit)
    order = []
    build = report_mod.build_needs_decision
    kit.monkeypatch.setattr(report_mod, "build_needs_decision", lambda *a, **k: (order.append("report"), build(*a, **k))[1])

    def record():
        order.append("hook")
        assert order == ["report", "hook"] and "reconcile_audit" not in load_manifest(path).meta
        reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)

    result = run_tick(kit, manifest, path, record)

    assert result.state == "needs_decision" and order == ["report", "hook"]
    assert load_manifest(path).meta["reconcile_audit"] == {
        "schema": "omac.reconcile-audit/v1", "last_full_scan_at": "2026-08-11T12:00:00Z", "completed_interval_ticks": 0}

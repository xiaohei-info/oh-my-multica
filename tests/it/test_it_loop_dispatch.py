"""Dispatch and convergence of the Loop against a real Multica: what a tick creates, links, assigns and wakes."""
import pytest

from _support.loopkit import contract
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase, WorkerHandoffIntent
from omac.core.workitem import WorkItemStatus
from omac.pipeline.dispatch import build_show_output


def test_dag_with_linear_and_parallel_dependencies_converges(kit, stack):
    """a, b independent; c needs both; d needs c — every node ends done with its own work item."""
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    stack.set_scenario("bob", [[stack.tool("worker-submit")]])
    nodes = [kit.node("a", contract=contract()), kit.node("b", worker="bob", contract=contract()),
             kit.node("c", blocked_by=["a", "b"], contract=contract()),
             kit.node("d", worker="bob", blocked_by=["c"], contract=contract())]
    manifest = kit.manifest(nodes)
    path = kit.save(manifest)

    result = kit.settle(manifest, path)

    assert result.state == "converged"
    assert sorted(result.done) == ["a", "b", "c", "d"]
    assert result.failed == [] and result.running == []
    assert all(node.work_item_id for node in manifest.nodes.values())
    created = {key: kit.store.get_work_item(node.work_item_id).created_at for key, node in manifest.nodes.items()}
    assert created["c"] >= max(created["a"], created["b"]) and created["d"] >= created["c"]


@pytest.mark.parametrize(("max_parallel", "expected"), [(4, 3), (1, 1)])
def test_first_tick_dispatches_independent_nodes_up_to_max_parallel(kit, max_parallel, expected):
    manifest = kit.manifest([kit.node("a"), kit.node("b"), kit.node("c")])
    path = kit.save(manifest)

    result = kit.tick(manifest, path, max_parallel=max_parallel)

    assert result.state == "running"
    assert len(result.dispatched) == expected and sorted(result.running) == sorted(result.dispatched)


def test_dispatch_inherits_manifest_source_issues(kit):
    manifest = kit.manifest([kit.node("a", contract=contract())],
                            meta={"source_issues": ["plan-1", "acc-1", "dec-1"]})
    kit.tick(manifest, kit.save(manifest))
    item = kit.store.get_work_item(manifest.nodes["a"].work_item_id)

    assert item.source_refs == [
        {"label": "Design", "issue_id": "plan-1"},
        {"label": "Acceptance document", "issue_id": "acc-1"},
        {"label": "Task decomposition", "issue_id": "dec-1"},
    ]
    assert "## Upstream issues (stay on target)" in item.description
    assert "- Design: `plan-1`" in item.description
    assert "omac work show plan-1" not in item.description


def test_dispatch_appends_direct_dependency_issue_refs(kit):
    foundation, foundation_item = kit.done_dependency("foundation")
    foundation.title = "Shared contract foundation"
    data, data_item = kit.done_dependency("data")
    data.title = "Persistence layer"
    missing = kit.node("missing", title="Abandoned setup")
    missing.status = "abandoned"
    feature = kit.node("feature", blocked_by=["foundation", "data", "missing"], contract=contract())
    manifest = kit.manifest([foundation, data, missing, feature],
                            meta={"source_issues": ["plan-1", "acc-1", "dec-1"]})

    kit.tick(manifest, kit.save(manifest))
    item = kit.store.get_work_item(manifest.nodes["feature"].work_item_id)

    assert item.source_refs[-2:] == [
        {"label": "Prerequisite implementation · Shared contract foundation", "issue_id": foundation_item.id},
        {"label": "Prerequisite implementation · Persistence layer", "issue_id": data_item.id},
    ]
    assert item.blocked_by == ["foundation", "data", "missing"]
    assert f"- Prerequisite implementation · Shared contract foundation: `{foundation_item.id}`" in item.description
    assert f"omac work show {foundation_item.id}" not in item.description
    assert f"omac work show {data_item.id}" not in item.description
    assert "Abandoned setup" not in item.description


def test_reused_item_refreshes_manifest_dependencies_before_dispatch(kit):
    old = ["bootstrap-go", "bootstrap-console", "release-workspace-contract"]
    new = "system-release-tooling-ownership-contract"
    keys = [*old, new]
    dependency_nodes, dependency_items = [], {}
    for key in keys:
        node, item = kit.done_dependency(key)
        dependency_nodes.append(node)
        dependency_items[key] = item
    shared = contract()
    target = kit.node("release-artifact-tooling", reviewer="bob", blocked_by=keys, contract=shared,
                      title="Release artifact tooling")
    reused = kit.store.create_work_item(
        kit.workspace, target.title, "stale issue body", dag_key="demo-release/release-artifact-tooling",
        worker="alice", reviewer="bob", blocked_by=old)
    kit.store.set_node_contract(reused.id, shared)
    kit.store.update_work_item_metadata(
        reused.id,
        source_refs=[{"label": f"Prerequisite implementation · {key}", "issue_id": dependency_items[key].id}
                     for key in old],
        review_report={"review_goals": ["preserve prior history"]}, review_comment="prior review history")
    kit.store.add_comment(reused.id, "existing audit comment")
    target.work_item_id = reused.id
    manifest = kit.manifest([*dependency_nodes, target], meta={"dag_key": "demo-release"})
    path = kit.save(manifest)
    writes = kit.record(kit.store, "update_work_item_metadata", "assign_work_item", "update_status")
    kit.forbid(kit.store, "set_node_contract")
    kit.forbid(kit.runtime, "wake")
    comments_before = kit.comments(reused.id)

    result = kit.tick(manifest, path)

    current = kit.store.get_work_item(reused.id)
    summary = build_show_output(current, "worker:alice")
    mine = [(name, sorted(kw)) for name, args, kw in writes if args and args[0] == reused.id]
    assert result.dispatched == [target.id]
    assert [name for name, _ in mine] == [
        "update_work_item_metadata", "update_status", "update_work_item_metadata", "assign_work_item",
        "update_work_item_metadata"]
    assert mine[0][1] == ["worker_handoff"]
    assert mine[2][1] == ["blocked_by", "description", "source_refs"]
    assert mine[4][1] == ["worker_handoff"]
    assert current.worker_handoff is not None and current.worker_handoff.gate == "explicit-dispatch"
    assert current.worker_handoff.target_run_id is not None
    assert (current.worker, current.reviewer) == ("alice", "bob")
    assert current.review_report is None and current.review_comment is None
    assert summary["task"]["blocked_by"] == keys
    assert summary["context"]["source_issues"][-1] == {
        "label": f"Prerequisite implementation · {new}", "issue_id": dependency_items[new].id}
    assert "existing audit comment" in comments_before
    assert kit.comments(reused.id) == comments_before, "dispatch must not add or drop comments"
    assert len(kit.runtime.list_runs(reused.id)) == 1


def test_reused_todo_dispatch_recovers_same_intent_after_crash_before_wake(kit):
    """The same assignee yields no new Run on assignment, so only the persisted intent can complete the first wake."""
    kit.agents("idle")
    item = kit.item("a", assign=True, status=WorkItemStatus.DONE)
    kit.wait_runs_ended(item.id)
    kit.store.update_status(item.id, WorkItemStatus.TODO)
    kit.agents("hang")
    old_runs = kit.runtime.list_runs(item.id)
    assert len(old_runs) == 1 and old_runs[0].terminal
    node = kit.node("a")
    node.work_item_id = item.id
    manifest = kit.manifest([node])
    path = kit.save(manifest)
    wake = kit.fault(kit.runtime, "wake", when="before", error=RuntimeError("crash before first wake"))

    with pytest.raises(RuntimeError, match="crash before first wake"):
        kit.tick(manifest, path, max_parallel=1)

    interrupted = kit.store.get_work_item(item.id)
    assert interrupted.status is WorkItemStatus.IN_PROGRESS
    assert interrupted.worker_handoff is not None
    assert interrupted.worker_handoff.gate == "explicit-dispatch"
    assert interrupted.worker_handoff.target_worker_bounce == 0
    generation = interrupted.worker_handoff.generation
    assert len(kit.runtime.list_runs(item.id)) == 1

    wake["armed"] = False
    restarted = load_manifest(path)
    recovered = kit.tick(restarted, path, max_parallel=1)

    current = kit.store.get_work_item(item.id)
    runs = kit.runtime.list_runs(item.id)
    assert recovered.state == "running" and restarted.nodes["a"].status == "in_progress"
    assert current.worker_handoff.generation == generation
    assert len(runs) == 2
    assert current.worker_handoff.target_run_id == next(r.id for r in runs if r.id != old_runs[0].id)

    kit.tick(restarted, path, max_parallel=1)
    assert len(kit.runtime.list_runs(item.id)) == 2, "a settled handoff is not woken again"


def test_dispatch_develop_dag_key_includes_manifest_dag_suffix(kit):
    manifest = kit.manifest([kit.node("foundation-contract-skeleton")], meta={"dag_key": "decompose-p-aaade213"})

    kit.tick(manifest, kit.save(manifest), max_parallel=4)
    item = kit.store.get_work_item(manifest.nodes["foundation-contract-skeleton"].work_item_id)

    assert item.dag_key == "decompose-p-aaade213/foundation-contract-skeleton"
    assert item.title.startswith("[DAG:decompose-p-aaade213/foundation-contract-skeleton] ")


def test_resume_tick_does_not_redispatch_existing_in_progress_worker(kit):
    """Without a persisted handoff identity an in-progress worker is only observed, never woken again."""
    manifest = kit.manifest([kit.node("a")])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)
    item_id = manifest.nodes["a"].work_item_id
    wakes = kit.record(kit.runtime, "wake")
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path, max_parallel=1)

    assert result.state == "running"
    assert wakes == [] and assigns == []
    assert len(kit.runtime.list_runs(item_id)) == 1


def test_issue_body_is_refreshed_once_at_dispatch_and_never_while_the_worker_runs(kit):
    manifest = kit.manifest([kit.node("a")])
    path = kit.save(manifest)
    writes = kit.record(kit.store, "update_work_item_metadata")

    kit.tick(manifest, path, max_parallel=1)
    assert sum("description" in kw for _, _, kw in writes) == 1, "first dispatch refreshes the body exactly once"
    item_id = manifest.nodes["a"].work_item_id
    assert len(kit.runtime.list_runs(item_id)) == 1

    writes.clear()
    kit.tick(manifest, path, max_parallel=1)
    kit.tick(manifest, path, max_parallel=1)
    assert sum("description" in kw for _, _, kw in writes) == 0, "an active worker handoff never rewrites the body"


def test_delayed_visible_worker_run_does_not_refresh_issue_body(kit):
    """The Run exists but is not listed yet: the handoff waits for it instead of assigning, waking or rewriting."""
    node = kit.node("a", contract=contract())
    item = kit.item("a", contract=node.contract, status=WorkItemStatus.IN_PROGRESS, assign=True)
    node.work_item_id, node.status = item.id, "in_progress"
    manifest = kit.manifest([node])
    agent_id = kit.store.resolve_agent_id("alice")
    kit.store.update_work_item_metadata(item.id, worker_handoff=WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="alice", gate="explicit-dispatch",
        source_review_subject_digest="authoring-subject", source_review_round=1, target_review_bounce=0,
        generation="handoff-delayed-visible", target_agent_id=agent_id, target_worker_bounce=0))
    real_list_runs, calls = kit.runtime.list_runs, [0]

    def delayed(item_id):  # the platform shows the Run only from the second observation on
        calls[0] += 1
        return [] if calls[0] == 1 else real_list_runs(item_id)

    kit.monkeypatch.setattr(kit.runtime, "list_runs", delayed)
    writes = kit.record(kit.store, "update_work_item_metadata")
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    from omac.pipeline import loop
    result = loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")

    assert result.state == "waiting"
    assert sum("description" in kw for _, _, kw in writes) == 0


@pytest.mark.parametrize(("manifest_status", "item_status"), [
    ("in_review", WorkItemStatus.IN_PROGRESS), ("in_progress", WorkItemStatus.IN_REVIEW)])
def test_ambiguous_authoring_projection_without_handoff_never_dispatches(kit, manifest_status, item_status):
    manifest = kit.manifest([kit.node("a")])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)
    item_id = manifest.nodes["a"].work_item_id
    kit.store.update_work_item_metadata(item_id, phase=TaskPhase.AUTHORING, worker_handoff={})
    kit.store.update_status(item_id, item_status)
    manifest.nodes["a"].status = manifest_status
    kit.save(manifest)
    assigns = kit.record(kit.store, "assign_work_item")
    wakes = kit.record(kit.runtime, "wake")

    from omac.pipeline import loop
    failures = loop.collect_results(kit.store, kit.runtime, kit.code_host, manifest, path)

    assert failures == {}
    assert manifest.nodes["a"].status == manifest_status
    assert assigns == [] and wakes == []


def test_ended_run_without_a_handoff_identity_is_never_redispatched_automatically(kit):
    """No persisted handoff identity and a Run that ended without a submit: block with the migration decision."""
    kit.agents("idle")
    manifest = kit.manifest([kit.node("a")])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)
    item_id = manifest.nodes["a"].work_item_id
    kit.wait_runs_ended(item_id)
    kit.store.update_work_item_metadata(item_id, worker_handoff={})
    assigns = kit.record(kit.store, "assign_work_item")
    wakes = kit.record(kit.runtime, "wake")

    result = kit.tick(manifest, path, max_parallel=1)
    item = kit.store.get_work_item(item_id)

    assert item.status == WorkItemStatus.BLOCKED and manifest.nodes["a"].status == "blocked"
    assert result.state == "needs_decision"
    assert item.bounces.worker == 0
    assert item.decision_required["reason_code"] == "legacy-worker-handoff-migration-unproven"
    assert item.decision_required["next_action"].endswith(" a")
    assert assigns == [] and wakes == []


def test_worker_completed_without_submit_exhaustion_does_not_comment(kit):
    """Exhausting the worker budget must not post a platform comment (a comment can wake the agent again)."""
    kit.agents("idle")
    manifest = kit.manifest([kit.node("a")])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)
    item_id = manifest.nodes["a"].work_item_id
    kit.wait_runs_ended(item_id)
    comments_before = kit.comments(item_id)

    result = kit.tick(manifest, path, max_parallel=1, retry_limits={"worker": 0})

    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert kit.comments(item_id) == comments_before

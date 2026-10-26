"""Work that predates the causal handoff and delivery identity: the Loop must stop and ask, never guess."""
import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from _support.loopkit import contract
from _support.samples import legacy_convergence_snapshot
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, PlatformError
from omac.pipeline import loop

VERIFICATION = {
    "commands": [{"cmd": "pytest -q", "exit_code": 0,
                  "business_tests": [{"acceptance": "works", "test": "tests/test_feature.py::test_feature_works"}]}],
    "integration_gates": [{"name": "gate-1", "commands": []}], "pr_base": "feature/v1", "coverage": 100,
}


def convergence_case(kit):
    """A develop item stuck mid-rework whose review ledger cannot be verified (a three-round legacy snapshot)."""
    snapshot = legacy_convergence_snapshot()["work_item"]
    agent_id = kit.store.resolve_agent_id("alice")
    snapshot["worker_handoff"].update(target_worker="alice", target_agent_id=agent_id)
    node = kit.node(snapshot["dag_key"], reviewer="bob", contract=contract())
    item = kit.item(node.id, reviewer="bob", contract=node.contract, status=WorkItemStatus(snapshot["status"]))
    kit.store.update_work_item_metadata(
        item.id, phase=TaskPhase(snapshot["phase"]), worker_bounce=snapshot["bounces"]["worker"],
        review_bounce=snapshot["bounces"]["review"], review_ledger=snapshot["review_ledger"],
        worker_handoff=snapshot["worker_handoff"])
    node.work_item_id, node.status = item.id, "in_progress"
    manifest = kit.manifest([node])
    return manifest, kit.save(manifest), node, item


def test_pending_legacy_review_handoff_is_blocked_before_assign_or_wake(kit):
    manifest, _path, node, _item = convergence_case(kit)
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item", "reset_review")
    writes += kit.record(kit.runtime, "wake")

    result = loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, node.id)

    assert result.state == "needs-decision"
    assert result.decision["reason_code"] == "review-convergence-ledger-unverifiable"
    assert writes == []


def test_runner_persists_the_legacy_convergence_decision_once_without_dispatch(kit):
    manifest, path, node, item = convergence_case(kit)
    decisions = []
    metadata = kit.record(kit.store, "update_work_item_metadata")
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    first = kit.tick(manifest, path, max_parallel=1)
    persisted = kit.store.get_work_item(item.id).decision_required
    second = kit.tick(manifest, path, max_parallel=1)

    from omac.cli.commands import dag as dag_cmd
    kit.monkeypatch.setattr(dag_cmd, "_assemble_engine", lambda _args: (kit.engine, None))
    kit.monkeypatch.setattr(dag_cmd, "ensure_config_synced", lambda *_a, **_k: None)
    kit.monkeypatch.setattr(dag_cmd, "load_config", lambda _path: {})
    kit.monkeypatch.setattr(dag_cmd, "_emit", lambda *_a, **_k: None)
    args = SimpleNamespace(manifest=path, max_parallel=1, max_rounds=None, max_minutes=None, output="json")
    with pytest.raises(NeedsDecision) as runner_exit:
        dag_cmd._loop_or_single_locked(args, single_round=False)

    decisions = [kw["decision_required"] for _, _, kw in metadata if "decision_required" in kw]
    assert (first.state, second.state) == ("needs_decision", "needs_decision")
    assert runner_exit.value.exit_code == 20
    assert persisted["reason_code"] == "review-convergence-ledger-unverifiable"
    assert persisted["next_action"].startswith("omac dag amend propose ")
    assert len(decisions) == 1 and decisions[0] == persisted, "the decision is written once, not per tick"
    assert kit.runtime.list_runs(item.id) == []


def legacy_delivery(kit, *, ended=True):
    """A reworked node whose delivery has no immutable identity: back in progress, nobody assigned."""
    kit.agents("idle" if ended else "hang")
    node = kit.node("platform-release-evidence-contract", reviewer="bob", contract=contract())
    manifest = kit.manifest([node])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)
    item = kit.store.get_work_item(node.work_item_id)
    if ended:
        kit.wait_runs_ended(item.id)
    kit.store.set_node_contract(item.id, node.contract)
    kit.store.update_work_item_metadata(
        item.id, artifacts={"pr_url": "https://github.com/acme/repo/pull/24"}, verification=VERIFICATION,
        verification_source=yaml.safe_dump(VERIFICATION), phase=TaskPhase.AUTHORING, review_bounce=1,
        review_ledger={"schema": "omac.review-ledger/v1", "blockers": [], "cycles": [
            {"round": 1, "subject_digest": "rejected-subject", "verdict": "reject"}]},
        worker_handoff={})
    kit.store.update_status(item.id, WorkItemStatus.DONE)
    kit.store.clear_assignment(item.id)
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    node.recovery_marker, node.status = False, "in_review"  # cleared out of band, not by the OMAC completion path
    kit.save(manifest)
    return manifest, path, node, item


def test_legacy_delivery_requires_explicit_node_retry(kit):
    """A rework without an immutable delivery identity guesses no evidence and starts no Run."""
    manifest, path, node, item = legacy_delivery(kit)
    kit.forbid(kit.runtime, "wake")
    kit.monkeypatch.setattr(loop, "run_merge_delivery", lambda *_a, **_k: pytest.fail("legacy delivery must not merge"))

    result = kit.tick(manifest, path, max_parallel=1)

    current = kit.store.get_work_item(item.id)
    retry = f"omac node retry {path} {node.id}"
    assert result.state == "needs_decision" and result.failed == [node.id]
    assert manifest.nodes[node.id].status == "blocked" and current.status is WorkItemStatus.BLOCKED
    assert current.delivery_identity is None
    assert current.decision_required["reason_code"] == "legacy-delivery-retry-required"
    assert current.decision_required["next_action"] == retry
    assert retry in result.report["next_actions"]


def test_legacy_detection_waits_for_an_active_direct_run_without_writes(kit):
    manifest, path, node, item = legacy_delivery(kit, ended=False)
    kit.store.assign_work_item(item.id, node.worker, "worker")
    runs = [r.id for r in kit.runtime.list_runs(item.id)]
    assert any(not r.terminal for r in kit.runtime.list_runs(item.id))
    source = Path(path).read_text()
    for name in ("update_work_item_metadata", "update_status", "add_comment", "assign_work_item", "clear_assignment"):
        kit.forbid(kit.store, name)
    kit.forbid(kit.runtime, "wake")
    comments = kit.comments(item.id)

    result = kit.tick(manifest, path, max_parallel=1)

    assert result.state == "running" and result.failed == [] and result.dispatched == []
    assert manifest.nodes[node.id].status == "in_review"
    assert kit.store.get_work_item(item.id).decision_required is None
    assert [r.id for r in kit.runtime.list_runs(item.id)] == runs and kit.comments(item.id) == comments
    assert Path(path).read_text() == source


def test_legacy_detection_propagates_run_observation_failure(kit):
    manifest, path, _node, item = legacy_delivery(kit)
    kit.monkeypatch.setattr(kit.runtime, "list_runs", lambda _id: (_ for _ in ()).throw(PlatformError("runs unavailable")))
    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata",
                            lambda *_a, **_k: pytest.fail("run observation failure must precede decision writes"))

    with pytest.raises(PlatformError, match="runs unavailable"):
        kit.tick(manifest, path, max_parallel=1)

    assert kit.store.get_work_item(item.id).decision_required is None


def test_legacy_decision_restart_does_not_duplicate_comment_or_dispatch(kit):
    manifest, path, node, item = legacy_delivery(kit)
    baseline = kit.comments(item.id)
    original_save, crashed = loop.save_manifest, []

    def crash_before_manifest_save(current, current_path):
        # The recovery marker is persisted before the platform decision; crash at the later manifest write.
        if not crashed and kit.comments(item.id) != baseline:
            crashed.append(True)
            raise RuntimeError("crash before legacy decision manifest save")
        return original_save(current, current_path)

    kit.monkeypatch.setattr(loop, "save_manifest", crash_before_manifest_save)
    with pytest.raises(RuntimeError, match="legacy decision manifest save"):
        kit.tick(manifest, path, max_parallel=1)

    assert len(kit.comments(item.id)) == len(baseline) + 1
    assert kit.store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
    assert load_manifest(path).nodes[node.id].status == "in_review"

    kit.monkeypatch.setattr(loop, "save_manifest", original_save)
    for name in ("update_work_item_metadata", "update_status", "add_comment", "assign_work_item"):
        kit.forbid(kit.store, name)
    kit.forbid(kit.runtime, "wake")
    restarted = load_manifest(path)

    result = kit.tick(restarted, path, max_parallel=1)

    assert result.state == "needs_decision" and restarted.nodes[node.id].status == "blocked"
    assert len(kit.comments(item.id)) == len(baseline) + 1


def test_a_worker_that_submits_between_the_item_read_and_the_run_read_is_not_a_legacy_case(kit):
    """The Loop read the item while the Worker ran; by the time it listed the Runs the Worker had submitted and ended."""
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")]])
    manifest = kit.manifest([kit.node("a", contract=contract())])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)
    item_id = manifest.nodes["a"].work_item_id
    # what the Loop read: in progress, and with no handoff intent (a bounce re-wakes the Worker without one)
    stale = replace(kit.store.get_work_item(item_id), status=WorkItemStatus.IN_PROGRESS, worker_handoff=None)
    kit.wait_runs_ended(item_id)
    assert kit.store.get_work_item(item_id).status is WorkItemStatus.DONE
    decisions = kit.record(kit.store, "update_work_item_metadata")

    recovered, failure = loop._recover_legacy_initial_worker(kit.store, kit.runtime, manifest, "a", stale, path)

    assert failure is None and recovered is stale and not decisions
    assert kit.store.get_work_item(item_id).decision_required is None

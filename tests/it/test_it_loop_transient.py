"""Transient provider failures of a Worker Run on a real Multica: one bounded rerun that costs no business bounce.

The scripted agent ends a Run with the provider's error text, so the platform records a genuinely failed Run.
"""
import copy
import time

import pytest
import yaml

from _support.loopkit import HANG, contract
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemControlProjection, WorkItemStatus
from omac.engines.models import RuntimeCapabilities
from omac.errors import PlatformError
from omac.pipeline import loop

CAPACITY = "Selected model is at capacity. Please try a different model."
CODEX_503 = ("unexpected status 503 Service Unavailable: system cpu overloaded (current: 94.9%, threshold: 90%), "
             "url: https://api.example.test/v1/responses, cf-ray: a27dc25009ec1f54")


def fails(message):
    return [{"fail": message}]


def dispatched(kit, *errors, reviewer="bob", delivery=None):
    """Dispatch node `a`; the Worker's Runs fail with ``errors`` in order, then hang.
    ``delivery``: verification metadata the issue already carries before the handoff starts."""
    kit.agents([*[fails(e) for e in errors], *HANG], names=("alice",))
    node = kit.node("a", reviewer=reviewer, contract=contract())
    if delivery:
        item = kit.item("a", reviewer=reviewer, contract=node.contract, verification=delivery,
                        verification_source=yaml.safe_dump(delivery))
        node.work_item_id = item.id
    manifest = kit.manifest([node])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)
    item = kit.store.get_work_item(manifest.nodes["a"].work_item_id)
    kit.wait_runs_ended(item.id)
    return manifest, path, item


def collect(kit, manifest, path, **kw):
    return loop.collect_results(kit.store, kit.runtime, kit.code_host, manifest, path, **kw)


def run_ids(kit, item_id):
    """Run ids, newest first (the platform lists the latest Run first)."""
    return [r.id for r in kit.runtime.list_runs(item_id)]


def test_codex_status_wrapper_reruns_without_consuming_a_business_bounce(kit):
    manifest, path, item = dispatched(kit, CODEX_503, delivery={"marker": "preserve-existing-delivery"})
    before = kit.store.get_work_item(item.id)
    wakes = kit.record(kit.runtime, "wake")

    assert collect(kit, manifest, path, retry_limits={"worker": 1, "review": 1}) == {}

    current = kit.store.get_work_item(item.id)
    assert [w[1][2] for w in wakes] == ["worker"]
    assert current.bounces.worker == 0 and current.bounces.review == 0
    assert current.phase is TaskPhase.AUTHORING
    assert current.verification == before.verification and current.verification_ref == before.verification_ref
    assert current.worker_handoff.target_run_id == run_ids(kit, item.id)[0] != before.worker_handoff.target_run_id
    assert current.decision_required is None and manifest.nodes["a"].status == "in_progress"


def test_initial_worker_capacity_failure_uses_the_causal_handoff_and_restarts_once(kit):
    """The first dispatch also persists the Run's causal identity before any transient recovery."""
    manifest, path, item = dispatched(kit, CAPACITY)
    intent = item.worker_handoff
    assert intent is not None and intent.gate == "explicit-dispatch" and intent.target_run_id is not None

    assert collect(kit, manifest, path) == {}
    persisted = load_manifest(path)
    assert collect(kit, persisted, path) == {}

    recovered = kit.store.get_work_item(item.id)
    assert len(run_ids(kit, item.id)) == 2, "one rerun, not one per collect"
    assert recovered.worker_handoff.target_run_id == run_ids(kit, item.id)[0] != intent.target_run_id
    assert recovered.bounces.worker == recovered.bounces.review == 0
    assert persisted.nodes["a"].status == "in_progress"


def legacy_item(kit, *errors):
    """An in-progress develop issue from before handoff identities: a failed Run, no persisted intent."""
    kit.agents([*[fails(e) for e in errors], *HANG], names=("alice",))
    node = kit.node("a", reviewer="bob", contract=contract())
    item = kit.item("a", reviewer="bob", contract=node.contract)
    time.sleep(1.2)  # legacy Runs are told apart from the issue by creation time, at one-second resolution
    kit.assign(item.id, "alice")
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    node.status, node.work_item_id = "in_progress", item.id
    manifest = kit.manifest([node])
    kit.wait_runs_ended(item.id) if errors else kit.wait_for(lambda: kit.runtime.list_runs(item.id), what="a Run")
    return manifest, kit.save(manifest), item


def test_legacy_initial_worker_capacity_failure_migrates_and_restarts_once(kit):
    """Without a handoff, only the single provable transient Worker Run is migrated."""
    manifest, path, item = legacy_item(kit, CAPACITY)
    failed = run_ids(kit, item.id)

    assert collect(kit, manifest, path) == {}
    assert collect(kit, load_manifest(path), path) == {}

    recovered = kit.store.get_work_item(item.id)
    assert len(run_ids(kit, item.id)) == len(failed) + 1
    assert recovered.worker_handoff.gate == "explicit-dispatch"
    assert tuple(recovered.worker_handoff.baseline_direct_run_ids) == tuple(failed)
    assert recovered.worker_handoff.target_run_id == run_ids(kit, item.id)[0]
    assert recovered.bounces.worker == recovered.bounces.review == 0 and recovered.decision_required is None


def test_legacy_migration_continues_with_a_fresh_item_not_the_stale_control_snapshot(kit):
    manifest, path, item = legacy_item(kit, CAPACITY)
    failed = run_ids(kit, item.id)
    stale = WorkItemControlProjection(copy.deepcopy(item))

    assert collect(kit, manifest, path, observations={"a": stale}) == {}
    assert collect(kit, manifest, path, observations={
        "a": WorkItemControlProjection(copy.deepcopy(kit.store.get_work_item(item.id)))}) == {}

    recovered = kit.store.get_work_item(item.id)
    assert len(run_ids(kit, item.id)) == len(failed) + 1
    assert tuple(recovered.worker_handoff.baseline_direct_run_ids) == tuple(failed)
    assert recovered.worker_handoff.target_run_id == run_ids(kit, item.id)[0]
    assert recovered.bounces.worker == recovered.bounces.review == 0 and recovered.decision_required is None


def without_stable_run_identity(kit):
    kit.monkeypatch.setattr(type(kit.runtime), "capabilities", property(lambda _self: RuntimeCapabilities()))


def test_legacy_initial_worker_active_run_keeps_waiting_without_migration(kit):
    """A legacy Worker still running during a deployment keeps its state: no migration, no block, no rerun."""
    manifest, path, item = legacy_item(kit)
    without_stable_run_identity(kit)
    kit.forbid(kit.runtime, "wake")

    assert collect(kit, manifest, path) == {}

    current = kit.store.get_work_item(item.id)
    assert current.status is WorkItemStatus.IN_PROGRESS and current.worker_handoff is None
    assert current.decision_required is None and manifest.nodes["a"].status == "in_progress"


def test_legacy_initial_worker_without_stable_run_identity_fails_closed(kit):
    manifest, path, item = legacy_item(kit, CAPACITY)
    without_stable_run_identity(kit)

    failures = collect(kit, manifest, path)

    current = kit.store.get_work_item(item.id)
    assert "a" in failures and current.worker_handoff is None and current.status is WorkItemStatus.BLOCKED
    assert current.decision_required["reason_code"] == "legacy-worker-handoff-migration-unproven"


def test_rerun_response_unknown_observes_the_created_run(kit):
    manifest, path, item = dispatched(kit, "Our servers are currently overloaded")
    rerun = kit.fault(kit.runtime, "wake", when="after", error=PlatformError("rerun response unavailable"))

    assert collect(kit, manifest, path) == {}

    assert rerun["calls"] == 1 and len(run_ids(kit, item.id)) == 2
    assert kit.store.get_work_item(item.id).worker_handoff.target_run_id == run_ids(kit, item.id)[0]


def test_transient_retry_is_restart_safe_and_does_not_duplicate_an_active_run(kit):
    manifest, path, item = dispatched(kit, "runtime status: HTTP 503 Service Unavailable")

    assert collect(kit, manifest, path) == {}
    assert collect(kit, manifest, path) == {}

    assert len(run_ids(kit, item.id)) == 2
    assert kit.store.get_work_item(item.id).bounces.worker == 0


def test_consecutive_transient_failures_stop_at_the_infrastructure_limit(kit):
    manifest, path, item = dispatched(kit, "Our servers are currently overloaded",
                                      "provider error: HTTP 429 Too Many Requests")
    assert collect(kit, manifest, path) == {}  # first rerun, which fails again
    kit.wait_runs_ended(item.id)
    kit.forbid(kit.runtime, "wake")

    result = kit.tick(manifest, path, max_parallel=1)

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED and blocked.phase is TaskPhase.AUTHORING
    assert blocked.bounces.worker == blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == "transient-runtime-retry-exhausted"
    assert len(run_ids(kit, item.id)) == 2


def test_transient_count_excludes_runs_of_earlier_handoffs(kit):
    """The first transient failure of the current handoff is not exhausted by older failures of the same agent."""
    kit.agents([fails("Our servers are currently overloaded"), fails("provider error: HTTP 503 Service Unavailable"),
                *HANG], names=("alice",))
    node = kit.node("a", reviewer="bob", contract=contract())
    item = kit.item("a", reviewer="bob", contract=node.contract, assign=True)
    kit.wait_runs_ended(item.id)
    history = run_ids(kit, item.id)
    kit.store.update_status(item.id, WorkItemStatus.DONE)
    kit.store.update_status(item.id, WorkItemStatus.TODO)
    node.work_item_id = item.id
    manifest = kit.manifest([node])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=1)  # a new handoff: its Run is the second scripted failure
    kit.wait_runs_ended(item.id)
    current_run = run_ids(kit, item.id)[0]

    assert collect(kit, manifest, path) == {}

    recovered = kit.store.get_work_item(item.id)
    assert recovered.status is WorkItemStatus.IN_PROGRESS and recovered.decision_required is None
    assert set(recovered.worker_handoff.baseline_direct_run_ids) == {*history, current_run}
    assert recovered.worker_handoff.target_run_id == run_ids(kit, item.id)[0] != current_run

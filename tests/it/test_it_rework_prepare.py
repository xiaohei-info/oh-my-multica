"""Preparing the Worker handoff: what must be read, cleared and made durable before the Worker is assigned.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
from dataclasses import replace

import pytest

from _support.loopkit import HANG
from _support.rework import AssignmentReached, rejected, residual_reviewer_baseline, unassigned_handoff
from omac.core.review_convergence import review_subject_digest
from omac.core.workitem import WorkItemControlProjection, WorkItemPayload
from omac.errors import PlatformError
from omac.pipeline import loop


def test_the_handoff_clears_a_residual_reviewer_baseline_before_it_assigns_the_worker(kit):
    manifest, path, item = unassigned_handoff(kit)
    kit.store.update_work_item_metadata(item.id, reviewer_run_baseline=residual_reviewer_baseline(kit, item))
    resets = kit.record(kit.store, "reset_review")

    def baseline_is_gone(*_args, **_kw):
        assert kit.store.get_work_item(item.id).reviewer_run_baseline is None
        raise AssignmentReached

    kit.before(kit.store, "assign_work_item", baseline_is_gone)

    with pytest.raises(AssignmentReached):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")

    assert len(resets) == 1


def test_a_reviewer_baseline_that_appears_during_preparation_stops_the_assignment(kit):
    manifest, path, item = unassigned_handoff(kit)
    drift = kit.after(kit.store, "update_work_item_metadata", lambda *_, **__: kit.store.update_work_item_metadata(
        item.id, reviewer_run_baseline=residual_reviewer_baseline(kit, item)), only=lambda _id, **m: "description" in m)
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    result = loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")

    assert drift["calls"] == 1 and result.state == "pending-preparation"
    assert kit.store.get_work_item(item.id).reviewer_run_baseline is not None


@pytest.mark.parametrize("field", ["machine_feedback_ref", "review_report_ref"])
@pytest.mark.parametrize("raw", [[], ["fact"]], ids=["empty-list", "list"])
def test_a_review_reference_that_turns_invalid_during_preparation_stops_the_assignment(kit, field, raw):
    manifest, path, item = unassigned_handoff(kit)
    shown = []

    def arm(*_args, **_kw):
        for name, transform in (
            ("get_work_item", lambda current: replace(current, **{field: raw})),
            ("observe_work_item_control", lambda projection: replace(projection, work_item=replace(projection.work_item, **{field: raw}))),
        ):
            shown.append(kit.distort(kit.store, name, transform, times=100))

    kit.after(kit.store, "update_work_item_metadata", arm, only=lambda _id, **m: "description" in m)
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    result = loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")

    assert shown and result.state == "pending-preparation"


@pytest.mark.parametrize("payload", [WorkItemPayload.VERIFICATION, WorkItemPayload.DELIVERABLE, WorkItemPayload.PROJECT_RULES])
@pytest.mark.parametrize("source", ["current", "head-drift", "unreadable"])
def test_a_new_review_handoff_reads_the_fresh_subject_before_it_writes_anything(kit, payload, source):
    manifest, path, item = rejected(kit, *HANG)
    kit.store.update_work_item_metadata(item.id, deliverable="exact delivery", project_rules="exact rules")
    current = kit.store.get_work_item(item.id)
    kit.store.update_work_item_metadata(item.id, review_subject_digest=review_subject_digest(current, current.bounces.review + 1))
    current = kit.store.get_work_item(item.id)
    stale_head = replace(current, artifacts={**current.artifacts, "head_sha": "c" * 40}) if source == "head-drift" else current
    deferred = WorkItemControlProjection(replace(stale_head, **{payload.value: None}), deferred_payloads=frozenset({payload}))
    plans = []

    def hydrate(shown, plan):
        plans.append(plan)
        return replace(shown.work_item, **{payload.value: None if source == "unreadable" else getattr(current, payload.value)})

    kit.monkeypatch.setattr(kit.store, "hydrate_work_item_evidence", hydrate)
    kit.distort(kit.store, "observe_work_item_control", lambda _fresh: deferred, times=1)
    writes = []

    def stop_before_write(_id, **metadata):
        writes.append(metadata)
        raise RuntimeError("source accepted before the first write")

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", stop_before_write)
    kit.forbid(kit.store, "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    error, message = (RuntimeError, "source accepted") if source == "current" else (PlatformError, "source is stale")
    with pytest.raises(error, match=message):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=current.bounces.review + 1,
                                      gate="review", projection=WorkItemControlProjection(current))

    assert plans == [frozenset({payload})] and bool(writes) is (source == "current")


def test_a_handoff_intent_that_cannot_be_written_never_retires_the_delivery(kit):
    manifest, path, item = rejected(kit, *HANG)
    before = kit.store.get_work_item(item.id)
    assert before.delivery_identity is not None
    kit.fault(kit.store, "update_work_item_metadata", when="before", error=PlatformError("metadata exceeds the 8KB size limit"),
              only=lambda _id, **m: bool(m.get("worker_handoff")))

    with pytest.raises(PlatformError, match="8KB"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=before.bounces.review + 1, gate="review")

    current = kit.store.get_work_item(item.id)
    assert current.delivery_identity == before.delivery_identity and current.worker_handoff is None
    assert current.bounces == before.bounces and current.review_verdict == before.review_verdict


@pytest.mark.parametrize("drift", [False, True])
def test_the_intent_is_durable_before_the_delivery_is_retired_and_resumes_strictly(kit, drift):
    manifest, path, item = rejected(kit, *HANG)
    before = kit.store.get_work_item(item.id)
    kit.monkeypatch.setattr(kit.store, "is_transient_transport_error", lambda exc: "connection reset by peer" in str(exc))
    state = {"lost": False, "crashed": False}
    original = kit.store.update_work_item_metadata

    def interrupt(item_id, **metadata):
        if metadata.get("worker_handoff") and not state["lost"]:
            state["lost"] = True
            original(item_id, **metadata)
            raise PlatformError("connection reset by peer")     # written, but the answer is lost
        if metadata.get("delivery_identity") == {} and not state["crashed"]:
            assert kit.store.get_work_item(item_id).worker_handoff is not None, "the intent must be durable first"
            state["crashed"] = True
            raise RuntimeError("crash before the source delivery is retired")
        return original(item_id, **metadata)

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", interrupt)
    with pytest.raises(RuntimeError, match="before the source delivery is retired"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=before.bounces.review + 1, gate="review")
    staged = kit.store.get_work_item(item.id)
    generation = staged.worker_handoff.generation
    assert staged.delivery_identity == before.delivery_identity and staged.bounces == before.bounces
    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", original)

    if drift:
        kit.distort(kit.store, "observe_work_item_control", lambda projection: replace(projection, work_item=replace(
            projection.work_item, artifacts={**projection.work_item.artifacts, "head_sha": "foreign-head"})), times=100)
        with pytest.raises(PlatformError, match="source.*changed|source.*stale"):
            loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")
    else:
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a")
        current = kit.store.get_work_item(item.id)
        assert current.delivery_identity is None and current.worker_handoff.generation == generation
        assert current.bounces.worker == before.bounces.worker and current.bounces.review == before.bounces.review + 1


def test_a_source_that_stays_stale_fails_the_handoff_before_any_write(kit):
    manifest, path, item = rejected(kit, *HANG)
    current = kit.store.get_work_item(item.id)
    stale = WorkItemControlProjection(replace(current, bounces=replace(current.bounces, review=current.bounces.review + 1)))
    kit.distort(kit.store, "observe_work_item_control", lambda _fresh: stale, times=100)
    kit.forbid(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(PlatformError, match="source is stale"):
        loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, manifest, "a", review_bounce=current.bounces.review + 1,
                                      gate="review", projection=stale)

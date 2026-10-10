"""Real scheduled/reconcile route; only native reads/effects use offline transport."""

from copy import deepcopy
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from omac.cli.commands.dag import _scheduled_tick
from omac.core.manifest import load_manifest, save_manifest
from omac.engines.models import WorkItem, WorkItemControlProjection, WorkItemPayload
from omac.errors import PlatformError
from omac.pipeline.operator_review_recovery import DISPATCH_JOURNAL, JOURNAL
from omac.pipeline.reconcile_audit import META_KEY, SCHEMA
from test_operator_reservation_dispatch import held as held, reservation as reservation


pytestmark = pytest.mark.integration


@pytest.fixture(params=["deferred", "complete"])
def scheduled(reservation, request, monkeypatch):
    c = reservation
    full = load_manifest(
        str(
            Path(__file__).parent
            / "fixtures/normal_reservation_hydration/current-wholemanifest.yaml"
        )
    )
    assert len(full.nodes) == 183
    assert sum(n.status == "done" for n in full.nodes.values()) == 75
    c.manifest = deepcopy(full)
    c.manifest.nodes = {
        k: n for k, n in full.nodes.items() if k == c.key or n.status == "done"
    }
    # A valid offline interval tick: do not invent native controls for 75
    # unrelated closed DONE nodes. Their complete objects remain protected.
    c.manifest.meta[META_KEY] = {
        "schema": SCHEMA,
        "last_full_scan_at": datetime.now(timezone.utc).isoformat(),
        "completed_interval_ticks": 0,
    }
    save_manifest(c.manifest, c.path)
    c.mode = request.param
    c.hydrations = []
    c.effects = []
    c.read_fault = None
    c.required_refs = frozenset(
        p for p in WorkItemPayload if getattr(c.item, p.value + "_ref", None)
    )
    assert WorkItemPayload.VERIFICATION in c.required_refs
    assert WorkItemPayload.CONTRACT in c.required_refs

    def observe(item_id):
        assert item_id == c.item.id
        deferred = c.required_refs if c.mode == "deferred" else frozenset()
        item = replace(c.item, **{p.value: None for p in deferred})
        return WorkItemControlProjection(item, deferred)

    def hydrate(projection, plan):
        requested = plan & projection.deferred_payloads
        c.hydrations.append((projection, requested))
        if c.read_fault == "unknown":
            raise PlatformError("hydration transport outcome unknown")
        if c.read_fault in {"missing", "malformed"}:
            # The native body read and subsequent full read agree on the
            # unavailable/corrupt evidence; immutable receipt source remains.
            c.item.verification = None if c.read_fault == "missing" else "invalid"
        return replace(
            projection.work_item,
            **{p.value: deepcopy(getattr(c.item, p.value)) for p in requested},
        )

    monkeypatch.setattr(c.engine.store, "observe_work_item_control", observe)
    monkeypatch.setattr(c.engine.store, "hydrate_work_item_evidence", hydrate)
    for name in ["update_status", "update_work_item_metadata", "assign_work_item"]:
        original = getattr(c.engine.store, name)

        def tracked(*args, _name=name, _original=original, **kwargs):
            c.effects.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(c.engine.store, name, tracked)
    return c


def run(c):
    return _scheduled_tick(
        c.engine,
        c.manifest,
        c.path,
        max_parallel=1,
        retry_limits=c.config["retry"],
        config=c.config,
    )


def protected(c):
    return (
        deepcopy({k: n for k, n in c.manifest.nodes.items() if n.status == "done"}),
        deepcopy(c.manifest.meta["amendment_apply"]),
        deepcopy(c.manifest.meta[JOURNAL]),
    )


def test_actual_scheduled_reserved_collection_preserves_complete_authority(scheduled):
    c = scheduled
    before = protected(c)
    item = deepcopy(c.item)
    result = run(c)
    assert result.state == "running"
    assert len(c.calls) == 1
    assert protected(c) == before
    assert c.item.reviewer_run_baseline == replace(
        item.reviewer_run_baseline, target_run_id=c.runs[-1].id
    )
    assert c.item.reviewer_run_baseline.attempt == 2
    for f in fields(WorkItem):
        if f.name not in {
            "status",
            "reviewer",
            "platform_assignee_id",
            "decision_required",
            "reviewer_run_baseline",
            "updated_at",
        }:
            assert getattr(c.item, f.name) == getattr(item, f.name), f.name
    if c.mode == "deferred":
        assert c.hydrations
        assert set().union(*(plan for _, plan in c.hydrations)) == c.required_refs
    else:
        assert c.hydrations == []
    assert c.manifest.meta[DISPATCH_JOURNAL][c.token]["step"] == "complete"
    run(c)
    assert len(c.calls) == 1 and protected(c) == before


@pytest.mark.parametrize("fault", ["missing", "malformed"])
def test_observed_invalid_payload_cannot_be_hidden_by_fresh_full_read(
    scheduled, monkeypatch, fault
):
    c = scheduled
    observe = c.engine.store.observe_work_item_control
    hydrate = c.engine.store.hydrate_work_item_evidence
    invalid = None if fault == "missing" else "invalid"

    def bad_observation(item_id):
        projection = observe(item_id)
        if c.mode == "complete":
            return replace(
                projection,
                work_item=replace(projection.work_item, verification=invalid),
            )
        return projection

    def bad_hydration(projection, plan):
        return replace(hydrate(projection, plan), verification=invalid)

    monkeypatch.setattr(c.engine.store, "observe_work_item_control", bad_observation)
    monkeypatch.setattr(c.engine.store, "hydrate_work_item_evidence", bad_hydration)
    before = protected(c)
    # get_work_item still returns the full valid original. The already observed
    # referenced-body failure must abort the read barrier, not be overwritten.
    with pytest.raises(PlatformError, match="Referenced verification attachment"):
        run(c)
    assert c.calls == [] and c.effects == []
    assert c.item.reviewer_run_baseline.target_run_id is None
    assert protected(c) == before


@pytest.mark.parametrize("fault", ["missing", "malformed", "unknown"])
def test_scheduled_hydration_failure_never_authorizes_effect(scheduled, fault):
    c = scheduled
    if c.mode == "complete":
        # Complete projections already contain observed bodies; no hydration
        # call may hide a malformed or absent referenced payload.
        if fault == "unknown":
            c.mode = "deferred"
        else:
            c.item.verification = None if fault == "missing" else "invalid"
    c.read_fault = fault
    before = protected(c)
    with pytest.raises(
        PlatformError, match="hydration transport|Referenced verification attachment"
    ):
        run(c)
    assert c.calls == [] and c.effects == []
    assert c.item.reviewer_run_baseline.attempt == 2
    assert c.item.reviewer_run_baseline.target_run_id is None
    assert protected(c) == before
    assert c.token not in c.manifest.meta.get(DISPATCH_JOURNAL, {})


@pytest.mark.parametrize(
    "effect", ["status", "decision", "assignment", "wake", "binding"]
)
def test_scheduled_unknown_accepted_continuation_observes_once(
    scheduled, monkeypatch, effect
):
    c = scheduled
    before = protected(c)
    if effect == "status":
        method = "update_status"
        target = c.engine.store
    elif effect == "decision":
        method = "update_work_item_metadata"
        target = c.engine.store
    elif effect == "assignment":
        method = "assign_work_item"
        target = c.engine.store
    elif effect == "wake":
        method = "wake"
        target = c.engine.runtime
    else:
        method = "update_work_item_metadata"
        target = c.engine.store
    original = getattr(target, method)
    accepted = []

    def unknown(*args, **kwargs):
        result = original(*args, **kwargs)
        baseline = kwargs.get("reviewer_run_baseline")
        if effect == "decision":
            matches = kwargs.get("decision_required") == {}
        else:
            matches = effect != "binding" or bool(baseline and baseline.target_run_id)
        if matches and not accepted:
            accepted.append(effect)
            raise PlatformError("accepted outcome unknown")
        return result

    monkeypatch.setattr(target, method, unknown)
    run(c)
    assert accepted == [effect]
    c.manifest = load_manifest(c.path)
    run(c)
    c.manifest = load_manifest(c.path)
    run(c)
    assert len(c.calls) == 1
    assert c.item.reviewer_run_baseline.attempt == 2
    assert c.item.reviewer_run_baseline.target_run_id == c.runs[-1].id
    assert c.manifest.meta[DISPATCH_JOURNAL][c.token]["step"] == "complete"
    assert protected(c) == before

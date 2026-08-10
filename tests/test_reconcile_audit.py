"""P3 persisted full-reconcile audit scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from omac.core import config as config_mod
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.errors import PlatformError, ValidationError
from omac.cli.commands import dag as dag_cmd
from omac.pipeline import loop, reconcile_audit
from omac.pipeline.loop import TickResult, tick
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import EngineConfig, WorkItemStatus


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _config(**overrides):
    return {
        "full_scan_interval_ticks": 2,
        "full_scan_max_age_seconds": 1800,
        **overrides,
    }


def _manifest():
    return Manifest(meta={"name": "audit"}, nodes={})


def test_resolve_reconcile_defaults_and_partial_override():
    assert config_mod.resolve_reconcile({}) == {
        "full_scan_interval_ticks": 20,
        "full_scan_max_age_seconds": 1800,
    }
    assert config_mod.resolve_reconcile({
        "reconcile": {"full_scan_interval_ticks": 3},
    }) == {
        "full_scan_interval_ticks": 3,
        "full_scan_max_age_seconds": 1800,
    }


@pytest.mark.parametrize("value", [0, -1, "20", 1.5, True, None])
def test_resolve_reconcile_rejects_invalid_interval(value):
    with pytest.raises(ValidationError, match="full_scan_interval_ticks"):
        config_mod.resolve_reconcile({
            "reconcile": {"full_scan_interval_ticks": value},
        })


@pytest.mark.parametrize("value", [0, -1, "1800", 1.5, True, None])
def test_resolve_reconcile_rejects_invalid_maximum_age(value):
    with pytest.raises(ValidationError, match="full_scan_max_age_seconds"):
        config_mod.resolve_reconcile({
            "reconcile": {"full_scan_max_age_seconds": value},
        })


def test_old_or_invalid_audit_meta_requires_first_full_scan():
    config = _config()
    cases = [
        {},
        {"reconcile_audit": {}},
        {"reconcile_audit": {"schema": "omac.reconcile-audit/v1"}},
        {"reconcile_audit": {
            "schema": "omac.reconcile-audit/v1",
            "last_full_scan_at": "not-a-timestamp",
            "completed_interval_ticks": 0,
        }},
        {"reconcile_audit": {
            "schema": "omac.reconcile-audit/v1",
            "last_full_scan_at": "2026-08-11T11:00:00Z",
            "completed_interval_ticks": -1,
        }},
    ]

    for meta in cases:
        assert reconcile_audit.should_full_scan(
            Manifest(meta=meta, nodes={}), config, now=NOW)


def test_successful_full_scan_resets_persisted_audit_state(tmp_path):
    manifest = _manifest()
    reconcile_audit.record_successful_tick(
        manifest, full_scan=True, now=NOW)
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)

    reloaded = load_manifest(path)
    assert reloaded.meta["reconcile_audit"] == {
        "schema": "omac.reconcile-audit/v1",
        "last_full_scan_at": "2026-08-11T12:00:00Z",
        "completed_interval_ticks": 0,
    }
    assert not reconcile_audit.should_full_scan(reloaded, _config(), now=NOW)


def test_successful_interval_tick_persists_counter_across_reload(tmp_path):
    path = str(tmp_path / "manifest.yaml")
    manifest = _manifest()
    reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)
    save_manifest(manifest, path)

    restarted = load_manifest(path)
    reconcile_audit.record_successful_tick(
        restarted, full_scan=False, now=NOW + timedelta(seconds=1))
    save_manifest(restarted, path)

    reloaded = load_manifest(path)
    assert reloaded.meta["reconcile_audit"]["completed_interval_ticks"] == 1
    assert not reconcile_audit.should_full_scan(
        reloaded, _config(), now=NOW + timedelta(seconds=1))


def test_cli_schedule_persists_first_full_scan_and_skips_static_after_reload(
        tmp_path, monkeypatch):
    store = MockStore(EngineConfig(engine_type="mock", workspace_id="audit"))
    item = store.create_work_item("audit", "node", "test", dag_key="node", worker="worker")
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    path = str(tmp_path / "manifest.yaml")
    manifest = Manifest(meta={}, nodes={
        "node": Node("node", "worker", work_item_id=item.id, status="blocked"),
    })
    save_manifest(manifest, path)
    engine = SimpleNamespace(store=store, runtime=MockRuntime(store))
    scans = []

    def fake_tick(store, runtime, manifest, manifest_path, *, full_scan,
                  after_successful_tick, **kwargs):
        scans.append(full_scan)
        after_successful_tick()
        save_manifest(manifest, manifest_path)
        return TickResult(state="converged")

    monkeypatch.setattr(dag_cmd, "tick", fake_tick)
    dag_cmd._scheduled_tick(
        engine, manifest, path, max_parallel=1, retry_limits={},
        config={"reconcile": _config()})

    restarted = load_manifest(path)
    dag_cmd._scheduled_tick(
        engine, restarted, path, max_parallel=1, retry_limits={},
        config={"reconcile": _config()})

    assert scans == [True, False]
    assert load_manifest(path).meta["reconcile_audit"]["completed_interval_ticks"] == 1


def test_interval_and_age_thresholds_each_force_full_scan():
    manifest = _manifest()
    reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)
    reconcile_audit.record_successful_tick(manifest, full_scan=False, now=NOW)
    reconcile_audit.record_successful_tick(manifest, full_scan=False, now=NOW)
    assert reconcile_audit.should_full_scan(manifest, _config(), now=NOW)

    manifest = _manifest()
    reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)
    assert reconcile_audit.should_full_scan(
        manifest, _config(), now=NOW + timedelta(seconds=1800))


def test_failed_full_reconcile_does_not_advance_audit_state(tmp_path):
    store = MockStore(EngineConfig(engine_type="mock", workspace_id="audit"))
    item = store.create_work_item("audit", "node", "test", dag_key="node", worker="worker")
    manifest = Manifest(meta={}, nodes={
        "node": Node("node", "worker", work_item_id=item.id, status="blocked"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    recorded = False

    def fail_read(item_id):
        raise PlatformError("simulated reconcile failure")

    def record():
        nonlocal recorded
        recorded = True
        reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)

    store.observe_work_item_control = fail_read
    with pytest.raises(PlatformError, match="simulated reconcile failure"):
        tick(
            store, MockRuntime(store), manifest, path, full_scan=True,
            after_successful_tick=record)

    assert recorded is False
    assert "reconcile_audit" not in load_manifest(path).meta


def test_failed_full_tick_after_reconcile_does_not_advance_audit_state(
        tmp_path, monkeypatch):
    store = MockStore(EngineConfig(engine_type="mock", workspace_id="audit"))
    item = store.create_work_item("audit", "node", "test", dag_key="node", worker="worker")
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest = Manifest(meta={}, nodes={
        "node": Node("node", "worker", work_item_id=item.id, status="blocked"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    recorded = False

    def fail_collect(*args, **kwargs):
        raise PlatformError("simulated collect failure")

    def record():
        nonlocal recorded
        recorded = True
        reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)

    monkeypatch.setattr(loop, "collect_results", fail_collect)
    with pytest.raises(PlatformError, match="simulated collect failure"):
        tick(
            store, MockRuntime(store), manifest, path, full_scan=True,
            after_successful_tick=record)

    assert recorded is False
    assert "reconcile_audit" not in load_manifest(path).meta


def test_failed_audit_state_write_does_not_advance_audit_state(tmp_path):
    store = MockStore(EngineConfig(engine_type="mock", workspace_id="audit"))
    item = store.create_work_item("audit", "node", "test", dag_key="node", worker="worker")
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest = Manifest(meta={}, nodes={
        "node": Node("node", "worker", work_item_id=item.id, status="blocked"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)

    def fail_after_success():
        raise PlatformError("simulated audit state write failure")

    with pytest.raises(PlatformError, match="simulated audit"):
        tick(
            store, MockRuntime(store), manifest, path, full_scan=True,
            after_successful_tick=fail_after_success)

    assert "reconcile_audit" not in load_manifest(path).meta


@pytest.mark.parametrize(
    "audit_state",
    [
        {
            "schema": "omac.reconcile-audit/v1",
            "last_full_scan_at": "2026-08-11T12:00:01Z",
            "completed_interval_ticks": 0,
        },
        {
            "schema": "omac.reconcile-audit/v2",
            "last_full_scan_at": "2026-08-11T11:00:00Z",
            "completed_interval_ticks": 0,
        },
    ],
)
def test_future_or_unsupported_audit_state_forces_full_without_mutation(
        audit_state):
    manifest = Manifest(
        meta={"reconcile_audit": dict(audit_state)}, nodes={})
    before = {"reconcile_audit": dict(audit_state)}

    assert reconcile_audit.should_full_scan(manifest, _config(), now=NOW)
    assert manifest.meta == before


def test_successful_needs_decision_records_audit_after_report_before_save(
        tmp_path, monkeypatch):
    store = MockStore(EngineConfig(engine_type="mock", workspace_id="audit"))
    item = store.create_work_item(
        "audit", "node", "test", dag_key="node", worker="worker")
    store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest = Manifest(meta={}, nodes={
        "node": Node("node", "worker", work_item_id=item.id, status="blocked"),
    })
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)

    from omac.pipeline import report as report_mod

    order = []
    build_report = report_mod.build_needs_decision

    def capture_report(*args, **kwargs):
        order.append("report")
        return build_report(*args, **kwargs)

    monkeypatch.setattr(report_mod, "build_needs_decision", capture_report)

    def record():
        order.append("hook")
        assert order == ["report", "hook"]
        assert "reconcile_audit" not in load_manifest(path).meta
        reconcile_audit.record_successful_tick(
            manifest, full_scan=True, now=NOW)

    result = tick(
        store, MockRuntime(store), manifest, path, full_scan=True,
        after_successful_tick=record)

    assert result.state == "needs_decision"
    assert order == ["report", "hook"]
    assert load_manifest(path).meta["reconcile_audit"] == {
        "schema": "omac.reconcile-audit/v1",
        "last_full_scan_at": "2026-08-11T12:00:00Z",
        "completed_interval_ticks": 0,
    }

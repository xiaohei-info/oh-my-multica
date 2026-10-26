"""P3 persisted full-reconcile audit scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from omac.core import config as config_mod
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.errors import ValidationError
from omac.cli.commands import dag as dag_cmd
from omac.pipeline import reconcile_audit
from omac.pipeline.loop import TickResult


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
    path = str(tmp_path / "manifest.yaml")
    manifest = Manifest(meta={}, nodes={
        "node": Node("node", "worker", work_item_id="item-1", status="blocked"),
    })
    save_manifest(manifest, path)
    engine = SimpleNamespace(store=None, runtime=None, code_host=None)   # the tick is replaced below: no port is touched
    scans = []

    def fake_tick(store, runtime, _code_host, manifest, manifest_path, *, full_scan,
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



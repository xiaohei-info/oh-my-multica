"""P3 full-reconcile audit scheduling, kept host-local outside git."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from omac.core import config as config_mod
from omac.core.manifest import (
    Manifest, Node, load_manifest, read_local_runtime, save_manifest, write_local_runtime)
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


def _write_audit(path, state):
    write_local_runtime(path, reconcile_audit.RUNTIME_NAME, state)


def test_missing_or_invalid_audit_state_requires_first_full_scan(tmp_path):
    path = str(tmp_path / "manifest.yaml")
    assert reconcile_audit.should_full_scan(path, _config(), now=NOW)   # no host-local file yet (fresh host, reboot)
    cases = [
        {},
        {"schema": "omac.reconcile-audit/v1"},
        {"schema": "omac.reconcile-audit/v1", "last_full_scan_at": "not-a-timestamp", "completed_interval_ticks": 0},
        {"schema": "omac.reconcile-audit/v1", "last_full_scan_at": "2026-08-11T11:00:00Z", "completed_interval_ticks": -1},
    ]
    for state in cases:
        _write_audit(path, state)
        assert reconcile_audit.should_full_scan(path, _config(), now=NOW)


def test_audit_state_is_host_local_and_never_enters_the_manifest(tmp_path):
    path = str(tmp_path / "manifest.yaml")
    save_manifest(_manifest(), path)
    before = sorted(p.name for p in tmp_path.rglob("*"))

    reconcile_audit.record_successful_tick(path, full_scan=True, now=NOW)
    reconcile_audit.record_successful_tick(path, full_scan=False, now=NOW + timedelta(seconds=1))

    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    assert "reconcile_audit" not in load_manifest(path).dag_state
    assert not reconcile_audit.should_full_scan(path, _config(), now=NOW + timedelta(seconds=1))


def test_cli_schedule_records_first_full_scan_and_skips_static_on_the_next_tick(
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
    for _ in range(3):   # a restarted process (a new `dag tick`) reads the same host-local schedule
        dag_cmd._scheduled_tick(
            engine, load_manifest(path), path, max_parallel=1, retry_limits={},
            config={"reconcile": _config()})

    assert scans == [True, False, False]
    assert reconcile_audit.should_full_scan(path, _config())   # two interval ticks reached the interval of 2


def test_interval_and_age_thresholds_each_force_full_scan(tmp_path):
    path = str(tmp_path / "a.yaml")
    reconcile_audit.record_successful_tick(path, full_scan=True, now=NOW)
    reconcile_audit.record_successful_tick(path, full_scan=False, now=NOW)
    reconcile_audit.record_successful_tick(path, full_scan=False, now=NOW)
    assert reconcile_audit.should_full_scan(path, _config(), now=NOW)

    path = str(tmp_path / "b.yaml")
    reconcile_audit.record_successful_tick(path, full_scan=True, now=NOW)
    assert reconcile_audit.should_full_scan(
        path, _config(), now=NOW + timedelta(seconds=1800))


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
        tmp_path, audit_state):
    path = str(tmp_path / "manifest.yaml")
    _write_audit(path, audit_state)

    assert reconcile_audit.should_full_scan(path, _config(), now=NOW)
    assert read_local_runtime(path, reconcile_audit.RUNTIME_NAME) == audit_state

"""DAG-level runtime facts live in ``<name>.state/_dag.yaml``, never in the definition file (design §4)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from _support.ports import UnusedCodeHost, UnusedStore
from omac.cli.commands import dag as dag_cmd
from omac.core.amendment import _verify_base, manifest_definition_digest, manifest_digest
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.errors import ValidationError
from omac.pipeline import loop, reconcile_audit

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
AUDIT = {"schema": "omac.reconcile-audit/v1", "last_full_scan_at": "2026-08-11T12:00:00Z", "completed_interval_ticks": 0}


def _dag(path, **meta):
    manifest = Manifest(meta={"name": "demo", **meta}, nodes={
        key: Node(id=key, worker="alice", blocked_by=["a"] if key == "b" else [], status="done", merged=True,
                  merged_at="2026-01-01T00:00:00Z") for key in ("a", "b")})
    save_manifest(manifest, str(path))
    return str(path)


def _tick(manifest, path):
    """One real tick over an all-done DAG (no platform read is needed), scheduled as `dag run` schedules it."""
    engine = SimpleNamespace(
        store=SimpleNamespace(config=SimpleNamespace(engine_type=None, workspace_id="ws")), runtime=UnusedStore(), code_host=UnusedCodeHost())
    return dag_cmd._scheduled_tick(
        engine, manifest, path, max_parallel=1, retry_limits={},
        config={"reconcile": {"full_scan_interval_ticks": 20, "full_scan_max_age_seconds": 1800}})


def test_ticks_never_change_the_definition_file_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _dag(tmp_path / "dag.yaml")
    before = (tmp_path / "dag.yaml").read_bytes()
    manifest = load_manifest(path)
    for _ in range(3):
        _tick(manifest, path)
    assert (tmp_path / "dag.yaml").read_bytes() == before
    assert load_manifest(path).dag_state["reconcile_audit"]["completed_interval_ticks"] == 2


def test_s9_an_amendment_reviewed_before_a_tick_is_still_applicable_after_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _dag(tmp_path / "dag.yaml")
    reviewed = load_manifest(path)
    amendment = {"base": {"manifest_sha256": manifest_digest(reviewed),
                          "definition_sha256": manifest_definition_digest(reviewed)}}

    _tick(load_manifest(path), path)                       # a tick between propose and accept

    assert _verify_base(load_manifest(path), amendment) in (True, False)   # 2.4 raised "definition changed" here
    assert manifest_definition_digest(load_manifest(path)) == amendment["base"]["definition_sha256"]


def test_s10_a_worker_changed_on_disk_is_not_overwritten_by_a_long_running_process(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _dag(tmp_path / "dag.yaml")
    long_running = load_manifest(path)

    other = load_manifest(path)                            # `omac node retry --worker bob` in another process
    other.nodes["b"].worker = "bob"
    save_manifest(other, path)

    _tick(long_running, path)

    assert long_running.nodes["b"].worker == "bob"
    assert load_manifest(path).nodes["b"].worker == "bob"
    assert yaml.safe_load((tmp_path / "dag.yaml").read_text())["nodes"][1]["worker"] == "bob"


def test_a_legacy_manifest_migrates_the_audit_out_of_meta_once(tmp_path):
    path = _dag(tmp_path / "dag.yaml", reconcile_audit=AUDIT)
    assert not (tmp_path / "dag.state" / "_dag.yaml").exists()

    manifest = load_manifest(path)
    assert "reconcile_audit" not in manifest.meta and manifest.dag_state["reconcile_audit"] == AUDIT
    save_manifest(manifest, path)

    assert "reconcile_audit" not in yaml.safe_load((tmp_path / "dag.yaml").read_text())["meta"]
    assert load_manifest(path).dag_state["reconcile_audit"] == AUDIT
    settled = (tmp_path / "dag.yaml").read_bytes()
    save_manifest(load_manifest(path), path)
    assert (tmp_path / "dag.yaml").read_bytes() == settled


def test_dag_state_is_written_only_when_present_and_removed_when_emptied(tmp_path):
    path = _dag(tmp_path / "dag.yaml")
    assert not (tmp_path / "dag.state" / "_dag.yaml").exists()
    manifest = load_manifest(path)
    reconcile_audit.record_successful_tick(manifest, full_scan=True, now=NOW)
    save_manifest(manifest, path)
    assert (tmp_path / "dag.state" / "_dag.yaml").read_text().startswith("# sha256: ")
    manifest.dag_state.clear()
    save_manifest(manifest, path)
    assert not (tmp_path / "dag.state" / "_dag.yaml").exists()


def test_a_manifest_from_a_newer_schema_is_refused_with_a_repair_hint(tmp_path):
    path = _dag(tmp_path / "dag.yaml", omac_schema=4)
    with pytest.raises(ValidationError, match="omac_schema"):
        load_manifest(path)
    assert load_manifest(_dag(tmp_path / "ok.yaml", omac_schema=3)).meta["omac_schema"] == 3

"""Paths and branch names that reach committed content (manifest, state files, commit messages)."""
import subprocess

import pytest
import yaml

from omac.core import gitsync
from omac.core.pathrefs import persist_path, resolve_path
from omac.pipeline.amendment import _manifest_branch


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".omac").mkdir(parents=True)
    manifest = root / ".omac" / "dag.yaml"
    manifest.write_text("meta: {}\n")
    return root, str(manifest)


def test_persist_path_is_relative_inside_the_repo_and_a_bare_name_outside(project, tmp_path):
    root, manifest = project
    inside = root / ".omac" / "dag.amendment.yaml"
    outside = tmp_path / "elsewhere" / "x.amendment.yaml"
    assert persist_path(str(inside), manifest) == ".omac/dag.amendment.yaml"
    assert persist_path(".omac/dag.amendment.yaml", manifest) == ".omac/dag.amendment.yaml"
    assert persist_path(str(outside), manifest) == "x.amendment.yaml"
    assert str(tmp_path) not in persist_path(str(outside), manifest)


def test_resolve_path_reads_relative_and_legacy_absolute_records(project, tmp_path):
    root, manifest = project
    target = root / ".omac" / "dag.amendment.yaml"
    target.write_text("a: 1\n")
    assert resolve_path(".omac/dag.amendment.yaml", manifest).endswith(".omac/dag.amendment.yaml")
    assert resolve_path(str(target), manifest) == str(target)                  # legacy absolute, still there
    moved = "/old/machine/proj/.omac/dag.amendment.yaml"                       # legacy absolute from another machine
    assert resolve_path(moved, manifest).endswith(".omac/dag.amendment.yaml")
    assert resolve_path("", manifest) == ""


@pytest.mark.parametrize("legacy_absolute", [False, True])
def test_a_pending_human_amendment_marker_is_found_in_both_forms(project, legacy_absolute):
    from omac.core.manifest import load_manifest, save_manifest
    from omac.pipeline.defects import _human_amendment_pending
    root, manifest_path = project
    target = root / ".omac" / "dag.amendment.yaml"
    target.write_text(yaml.safe_dump({"amendment_id": "amend-x", "human_confirmation": "pending"}))
    manifest = load_manifest(manifest_path)
    manifest.dag_state["pending_human_amendment"] = {
        "amendment_id": "amend-x", "file": str(target) if legacy_absolute else ".omac/dag.amendment.yaml"}
    save_manifest(manifest, manifest_path)
    assert _human_amendment_pending(load_manifest(manifest_path)) is True


def test_manifest_branch_follows_the_manifest_not_a_fixed_name(tmp_path):
    def branch(meta, node_bases):
        nodes = [{"id": f"n{i}", "worker": "w", "contract": {"pr_base": b}} for i, b in enumerate(node_bases)]
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump({"meta": {"name": "m", **meta}, "nodes": nodes}))
        return _manifest_branch(str(path))
    assert branch({"pr_base": "release"}, ["x"]) == "release"
    assert branch({}, ["feature/a", "feature/a"]) == "feature/a"
    assert branch({}, []) == "main"
    assert branch({}, ["a", "b"]) == "main"


def test_config_sync_commit_message_and_log_carry_no_machine_path(tmp_path):
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "config", k, v], cwd=work, check=True)
    (work / ".omac").mkdir()
    cfg = work / ".omac" / "config.yaml"
    cfg.write_text("engine: multica\n")
    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "none.git")], cwd=work, check=True)
    with pytest.raises(Exception):                                             # push to the missing remote fails; the commit is made first
        gitsync.ensure_config_synced(str(cfg), repo_root=str(work), engine_type="multica")
    subject = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=work, capture_output=True, text=True).stdout.strip()
    assert subject == "chore(omac): sync .omac/config.yaml"


def test_resuming_an_applied_amendment_rewrites_a_legacy_absolute_ledger_path_to_relative(project, monkeypatch):
    from omac.core import amendment as core
    from omac.core.manifest import load_manifest, save_manifest
    root, manifest_path = project
    target = root / ".omac" / "dag.amendment.yaml"
    manifest = load_manifest(manifest_path)
    manifest.meta["last_amendment_id"] = "amend-x"
    manifest.meta["amendment_apply"] = {"amendment_id": "amend-x", "amendment_file": str(target)}
    save_manifest(manifest, manifest_path)
    monkeypatch.setattr(core, "_amendment_identity", lambda a: (None, None, {}, None))
    for stub in ("_verify_amendment_identity", "_verify_nits_acceptance", "_preflight_authoring_repair_ledger"):
        monkeypatch.setattr(core, stub, lambda *a, **k: None)
    monkeypatch.setattr(core, "_resume_apply_ledger", lambda *a, **k: {})
    core.apply_amendment(manifest_path, {"amendment_id": "amend-x"}, None, set(),
                         amendment_file=str(target), recover=lambda *a, **k: "")
    ledger = load_manifest(manifest_path).meta["amendment_apply"]
    assert ledger["amendment_file"] == ".omac/dag.amendment.yaml"
    assert str(root) not in open(manifest_path).read()

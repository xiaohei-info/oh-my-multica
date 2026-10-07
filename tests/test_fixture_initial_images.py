"""Immutable preparation reuse must never share mutable per-case state."""
from pathlib import Path
from omac.core.manifest import load_manifest, save_manifest
from fixture_initial_images import seed_manifest


def test_full_capture_images_have_independent_files_and_models(tmp_path):
    source = Path(__file__).parent / "fixtures/evidence_handoff/current79.yaml"
    first, second = tmp_path / "first.yaml", tmp_path / "second.yaml"
    original = load_manifest(str(source))
    reference = tmp_path / "reference.yaml"
    save_manifest(original, str(reference))
    seed_manifest(source, first)
    seed_manifest(source, second)
    assert first.read_bytes() == second.read_bytes() == reference.read_bytes()
    assert first.stat().st_ino != second.stat().st_ino
    assert load_manifest(str(first)) == load_manifest(str(second)) == original
    changed = load_manifest(str(first))
    changed.meta["test-only-isolation"] = {"mutable": ["changed"]}
    save_manifest(changed, str(first))
    assert load_manifest(str(second)) == original
    third = tmp_path / "third.yaml"
    seed_manifest(source, third)
    assert load_manifest(str(third)) == original
    assert len(original.nodes) == 183


def test_initial_image_key_preserves_referenced_environment(tmp_path, monkeypatch):
    source = tmp_path / "source.yaml"
    source.write_text('meta: {value: "${OMAC_IMAGE_ENV}"}\nnodes: []\n')
    first, second = tmp_path / "first.yaml", tmp_path / "second.yaml"
    monkeypatch.setenv("OMAC_IMAGE_ENV", "first")
    seed_manifest(source, first)
    monkeypatch.setenv("OMAC_IMAGE_ENV", "second")
    seed_manifest(source, second)
    assert load_manifest(str(first)).meta["value"] == "first"
    assert load_manifest(str(second)).meta["value"] == "second"


def test_private_git_seed_never_shares_mutable_refs_index_or_lock(tmp_path):
    from test_full_state_git_recovery import private_case, git

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a, apath, aremote, alock = private_case(first)
    b, bpath, bremote, block = private_case(second)
    original_head = git(b, "rev-parse", "HEAD")
    assert apath.read_bytes() == bpath.read_bytes()
    assert alock.stat().st_ino != block.stat().st_ino
    assert (a / ".git/index").stat().st_ino != (b / ".git/index").stat().st_ino
    assert git(a, "remote", "get-url", "origin").decode().strip() == str(aremote)
    assert git(b, "remote", "get-url", "origin").decode().strip() == str(bremote)
    git(a, "update-ref", "refs/heads/main", git(a, "rev-parse", "HEAD^").decode().strip())
    apath.write_bytes(b"independent change")
    assert git(b, "rev-parse", "HEAD") == original_head
    assert len(bpath.read_bytes()) == 105650411

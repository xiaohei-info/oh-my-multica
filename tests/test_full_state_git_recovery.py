"""Private repositories only; original f9 object identities remain separate."""
import subprocess
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
import json
import pytest

from omac.errors import ValidationError, NeedsDecision

from test_full_state_transport import captured_bytes


pytestmark = pytest.mark.integration


def git(repo, *args, data=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], input=data)


def _build_private_case(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.name", "Offline captured-state qualification")
    git(repo, "config", "user.email", "offline@example.invalid")
    git(repo, "checkout", "-b", "main")
    (repo / "business.txt").write_text("unrelated complete bytes\n")
    git(repo, "add", "business.txt")
    git(repo, "commit", "-m", "Private published base; not original0dae")
    git(repo, "push", "-u", "origin", "main")
    manifest = repo / ".omac/state.yaml"
    manifest.parent.mkdir()
    manifest.write_bytes(captured_bytes())
    git(repo, "add", ".omac/state.yaml")
    git(repo, "commit", "-m", "Private GH001 ancestor with complete original payload")
    lock = tmp_path / "writer.lock"
    lock.touch()
    return repo, manifest, remote, lock


@lru_cache(maxsize=1)
def _private_seed():
    # Only the immutable initialized fixture is reused. Both Git databases,
    # worktree/index/config and canonical lock are independent per test.
    temporary = tempfile.TemporaryDirectory(prefix="omac-git-fixture-seed-")
    _build_private_case(Path(temporary.name))
    return temporary


def private_case(tmp_path):
    seed = Path(_private_seed().name)
    remote, repo = tmp_path / "remote.git", tmp_path / "work"
    shutil.copytree(seed / "remote.git", remote)
    shutil.copytree(seed / "work", repo)
    git(repo, "remote", "set-url", "origin", str(remote))
    # Copied index stat entries still describe the seed's inodes. Initialize
    # this independent worktree's index before public prepare pins its CAS.
    git(repo, "update-index", "--refresh")
    git(repo, "write-tree")
    lock = tmp_path / "writer.lock"
    lock.touch()
    return repo, repo / ".omac/state.yaml", remote, lock


def test_public_exact_prepare_does_not_migrate_or_change_refs(tmp_path):
    repo, manifest, remote, lock = private_case(tmp_path)
    from omac.core.state_sync_recovery import prepare
    before = git(repo, "rev-parse", "HEAD")
    raw = manifest.read_bytes()
    request = prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    assert request["schema"] == "omac.full-state-sync-recovery/v1"
    assert manifest.read_bytes() == raw
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "status", "--porcelain") == b""


def test_public_resolve_preserves_full_state_and_original_ancestry_archive(tmp_path, capsys):
    from omac.core.state_sync_recovery import prepare, resolve, digest
    from omac.core.state_transport import decode, is_transport
    from omac.core.manifest import load_manifest

    repo, manifest, remote, lock = private_case(tmp_path)
    original = manifest.read_bytes()
    old_head = git(repo, "rev-parse", "HEAD").decode().strip()
    request = prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    from omac.cli.main import main
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    assert main(["dag", "recover-sync", str(manifest), "--repo", str(repo),
                 "--writer-lock", str(lock), "--resolve", str(request_path),
                 "--request-sha256", digest(request), "--authority", "offline exact Root",
                 "--reason", "Full original case without new grant"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "complete-remote-observed"
    assert is_transport(manifest.read_bytes())
    assert decode(manifest.read_bytes()) == original
    assert len(load_manifest(str(manifest)).meta) == 23
    assert git(repo, "rev-parse", "HEAD").decode().strip() == request["target_commit"]
    assert git(repo, "cat-file", "blob", request["source"]["original_blob"]) == original
    assert git(repo, "rev-parse", "refs/omac/full-state-recovery/" + digest(request) + "/original").decode().strip() == old_head
    new_closure = git(repo, "rev-list", "--objects", request["source"]["base"] + "..HEAD").decode()
    assert request["source"]["original_blob"] not in new_closure
    assert (repo / "business.txt").read_bytes() == b"unrelated complete bytes\n"
    assert git(repo, "status", "--porcelain") == b""
    assert resolve(request, request_sha256=digest(request), authority="offline exact Root", reason="Full original case without new grant") == result
    restored = tmp_path / "restored.git"
    subprocess.run(["git", "init", "--bare", str(restored)], check=True, capture_output=True)
    subprocess.run(["git", "--git-dir", str(restored), "fetch", str(remote), request["source"]["base"]], check=True, capture_output=True)
    subprocess.run(["git", "--git-dir", str(restored), "bundle", "unbundle", request["archive"]["path"]], check=True, capture_output=True)
    assert subprocess.check_output(["git", "--git-dir", str(restored), "cat-file", "blob", request["source"]["original_blob"]]) == original


@pytest.mark.parametrize("body", ["{", "[]", "{}", '{"source": null}'])
def test_public_malformed_request_is_validation_failure(tmp_path, body):
    from omac.cli.main import main
    request = tmp_path / "bad.json"
    request.write_text(body)
    assert main(["dag", "recover-sync", str(tmp_path / "state.yaml"), "--repo", str(tmp_path),
                 "--writer-lock", str(tmp_path / "lock"), "--resolve", str(request),
                 "--request-sha256", "0" * 64, "--authority", "offline Root", "--reason", "negative"]) == 5


def test_full_request_integrity_and_source_CAS_before_any_ref(tmp_path):
    import omac.core.state_sync_recovery as r
    repo, manifest, remote, lock = private_case(tmp_path)
    request = r.prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    original = manifest.read_bytes()
    before = git(repo, "show-ref")
    archive = Path(request["archive"]["path"])
    receipt = Path(request["archive_receipt"]["path"])
    for target in [archive, receipt, manifest, lock]:
        body = target.read_bytes()
        if target == lock:
            target.rename(tmp_path / "old-lock")
            target.touch()
        else:
            target.write_bytes(body + b"\n")
        with pytest.raises(ValidationError):
            r.resolve(request, request_sha256=r.digest(request), authority="offline Root", reason="negative CAS")
        assert git(repo, "show-ref") == before
        if target == lock:
            target.unlink()
            (tmp_path / "old-lock").rename(target)
        else:
            target.write_bytes(body)
    assert manifest.read_bytes() == original


def test_restart_observes_objects_encoding_index_and_branch_without_replay(tmp_path, monkeypatch):
    import omac.core.state_sync_recovery as r
    repo, manifest, remote, lock = private_case(tmp_path)
    request = r.prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    actual_durable = r._durable
    pending = ["object-" + request["target_objects"][0]["oid"], "state-encoding", "index", "branch-ref"]
    missed = []

    def crash(path, value):
        if pending and value.get("effects", {}).get(pending[0]) == "observed":
            missed.append(pending.pop(0))
            raise TimeoutError("offline effect durable; journal acknowledgement lost")
        return actual_durable(path, value)

    monkeypatch.setattr(r, "_durable", crash)
    for _ in range(4):
        with pytest.raises(TimeoutError):
            r.resolve(request, request_sha256=r.digest(request), authority="offline Root", reason="restart observation")
    result = r.resolve(request, request_sha256=r.digest(request), authority="offline Root", reason="restart observation")
    assert len(missed) == 4 and not pending
    assert result["state"] == "complete-remote-observed"
    assert set(result["effects"].values()) == {"observed"}


@pytest.mark.parametrize("case", ["business", "merge", "detached", "foreign-branch", "dirty-business", "dirty-state", "index", "managed-untracked", "advanced-remote", "symlink"])
def test_full_capture_source_scope_refuses_before_archive_or_migration(tmp_path, case):
    from omac.core.state_sync_recovery import prepare
    repo, manifest, remote, lock = private_case(tmp_path)
    original = manifest.read_bytes()
    if case == "business":
        (repo / "business.txt").write_text("user change")
        git(repo, "add", "business.txt")
        git(repo, "commit", "-m", "user business")
    elif case == "merge":
        old = git(repo, "rev-parse", "HEAD").decode().strip()
        base = git(repo, "rev-parse", "origin/main").decode().strip()
        tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
        merge = git(repo, "commit-tree", tree, "-p", old, "-p", base, data=b"private merge\n").decode().strip()
        git(repo, "update-ref", "refs/heads/main", merge, old)
    elif case == "detached":
        git(repo, "checkout", "--detach")
    elif case == "foreign-branch":
        git(repo, "checkout", "-b", "kernel-user-history")
    elif case == "dirty-business":
        (repo / "business.txt").write_text("unstaged user change")
    elif case == "dirty-state":
        manifest.write_bytes(original + b"\n")
    elif case == "index":
        (repo / "business.txt").write_text("staged user change")
        git(repo, "add", "business.txt")
    elif case == "managed-untracked":
        (repo / ".omac/unknown.json").write_text("{}")
    elif case == "advanced-remote":
        head = git(repo, "rev-parse", "HEAD").decode().strip()
        subprocess.run(["git", "--git-dir", str(remote), "fetch", str(repo), head], check=True, capture_output=True)
        subprocess.run(["git", "--git-dir", str(remote), "update-ref", "refs/heads/main", head], check=True, capture_output=True)
    else:
        real = tmp_path / "state-original.yaml"
        manifest.rename(real)
        manifest.symlink_to(real)
    before = git(repo, "rev-parse", "HEAD")
    physical = manifest.read_bytes()
    with pytest.raises(ValidationError):
        prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    assert not (tmp_path / "archive.bundle").exists()
    assert manifest.read_bytes() == physical
    assert git(repo, "rev-parse", "HEAD") == before


def test_original_real_four_raw_objects_are_authenticated():
    import hashlib
    import gzip
    fixture = Path(__file__).parent / "fixtures/full_state_transport"
    rows = [(p, p.name.split(".")[0], p.name.split(".")[1]) for p in fixture.iterdir() if ".raw" in p.name]
    assert len(rows) == 4
    for path, wanted, kind in rows:
        raw = gzip.decompress(path.read_bytes()) if path.name.endswith(".gz") else path.read_bytes()
        assert hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == wanted


def test_smaller_tip_still_reaches_actual_oversized_ancestor(tmp_path):
    repo, manifest, remote, lock = private_case(tmp_path)
    original_blob = git(repo, "rev-parse", "HEAD:.omac/state.yaml").decode().strip()
    manifest.write_text("meta: {name: misleading smaller tip}\nnodes: []\n")
    git(repo, "add", ".omac/state.yaml")
    git(repo, "commit", "-m", "A smaller tip is not ancestry recovery")
    assert original_blob in git(repo, "rev-list", "--objects", "origin/main..HEAD").decode()
    assert int(git(repo, "cat-file", "-s", original_blob)) == 105650411


def test_unknown_durable_original_ref_is_observed_not_replayed(tmp_path, monkeypatch):
    import omac.core.state_sync_recovery as recovery
    repo, manifest, remote, lock = private_case(tmp_path)
    request = recovery.prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    actual_git = recovery._git
    writes = []

    def unknown(repo, *args, **kw):
        result = actual_git(repo, *args, **kw)
        if args[:1] == ("update-ref",) and args[1].startswith("refs/omac/"):
            writes.append(args)
            raise TimeoutError("offline durable ref write, outcome initially unknown")
        return result

    monkeypatch.setattr(recovery, "_git", unknown)
    with pytest.raises(TimeoutError):
        recovery.resolve(request, request_sha256=recovery.digest(request), authority="offline Root", reason="preserve complete state")
    assert len(writes) == 1
    monkeypatch.setattr(recovery, "_git", actual_git)
    result = recovery.resolve(request, request_sha256=recovery.digest(request), authority="offline Root", reason="preserve complete state")
    assert result["state"] == "complete-remote-observed"
    assert len(writes) == 1


def test_public_cli_exact_prepare_preserves_capture_and_reports_SHA(tmp_path, capsys):
    from omac.cli.main import main
    from omac.core.state_sync_recovery import digest
    repo, manifest, remote, lock = private_case(tmp_path)
    raw = manifest.read_bytes()
    head = git(repo, "rev-parse", "HEAD")
    target = tmp_path / "public-request.json"
    code = main(["dag", "recover-sync", str(manifest), "--repo", str(repo),
                 "--writer-lock", str(lock), "--prepare", str(target),
                 "--archive", str(tmp_path / "archive.bundle"), "--output", "json"])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    request = json.loads(target.read_bytes())
    assert output["request"] == request
    assert output["request_sha256"] == digest(request)
    assert manifest.read_bytes() == raw and git(repo, "rev-parse", "HEAD") == head
    assert main(["dag", "recover-sync", str(manifest), "--repo", str(repo),
                 "--writer-lock", str(lock), "--resolve", str(target),
                 "--request-sha256", "0" * 64, "--authority", "offline Root", "--reason", "negative"]) == 5
    assert manifest.read_bytes() == raw and git(repo, "rev-parse", "HEAD") == head


def test_unknown_push_not_observed_is_not_replayed(tmp_path, monkeypatch):
    import omac.core.state_sync_recovery as recovery
    repo, manifest, remote, lock = private_case(tmp_path)
    request = recovery.prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    actual_git = recovery._git
    pushes = []

    def unknown(repo, *args, **kw):
        if args[:1] == ("push",):
            pushes.append(args)
            raise TimeoutError("offline transport outcome unobserved, no request sent")
        return actual_git(repo, *args, **kw)

    monkeypatch.setattr(recovery, "_git", unknown)
    with pytest.raises(TimeoutError):
        recovery.resolve(request, request_sha256=recovery.digest(request), authority="offline Root", reason="no blind replay")
    assert len(pushes) == 1
    with pytest.raises(NeedsDecision, match="unknown"):
        recovery.resolve(request, request_sha256=recovery.digest(request), authority="offline Root", reason="no blind replay")
    assert len(pushes) == 1


def test_unknown_push_already_accepted_observed_without_second_push(tmp_path, monkeypatch):
    import omac.core.state_sync_recovery as recovery
    repo, manifest, remote, lock = private_case(tmp_path)
    request = recovery.prepare(str(manifest), str(repo), str(tmp_path / "archive.bundle"), str(lock))
    actual_git = recovery._git
    pushes = []

    def unknown(repo, *args, **kw):
        result = actual_git(repo, *args, **kw)
        if args[:1] == ("push",):
            pushes.append(args)
            raise TimeoutError("offline remote accepted, acknowledgement lost")
        return result

    monkeypatch.setattr(recovery, "_git", unknown)
    with pytest.raises(TimeoutError):
        recovery.resolve(request, request_sha256=recovery.digest(request), authority="offline Root", reason="observe exact remote")
    assert len(pushes) == 1
    result = recovery.resolve(request, request_sha256=recovery.digest(request), authority="offline Root", reason="observe exact remote")
    assert result["state"] == "complete-remote-observed" and len(pushes) == 1


def test_archive_created_before_lost_ack_can_be_observed(tmp_path, monkeypatch):
    import omac.core.state_sync_recovery as r
    repo, manifest, remote, lock = private_case(tmp_path)
    before = git(repo, "show-ref")
    original = manifest.read_bytes()
    archive = tmp_path / "archive.bundle"
    actual = r._git
    writes = []
    def unknown(repo, *args, **kwargs):
        result = actual(repo, *args, **kwargs)
        if args[:2] == ("bundle", "create"):
            writes.append(args)
            raise TimeoutError("offline original archive created; acknowledgement lost")
        return result
    monkeypatch.setattr(r, "_git", unknown)
    with pytest.raises(TimeoutError):
        r.prepare(str(manifest), str(repo), str(archive), str(lock))
    assert archive.exists() and len(writes) == 1
    request = r.prepare(str(manifest), str(repo), str(archive), str(lock))
    assert request["archive"]["path"] == str(archive)
    assert len(writes) == 1 and git(repo, "show-ref") == before
    assert manifest.read_bytes() == original


def test_source_environment_uses_existing_manifest_unicode_variable_rules(monkeypatch):
    monkeypatch.setenv("预算", "Root-qualified")
    from omac.core.state_sync_recovery import _environment
    import hashlib
    assert _environment("meta: {budget: '${预算:-default}'}\nnodes: []\n".encode()) == {"预算": hashlib.sha256(b"Root-qualified").hexdigest()}

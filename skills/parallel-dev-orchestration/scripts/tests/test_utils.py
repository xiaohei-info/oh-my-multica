# commit_manifest real-git-path tests via tmp_path, no mock.
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils import commit_manifest


def _git(args, cwd):
    subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _init_repo(tmp_path):
    """Init a temp git repo with an initial commit."""
    _git(["git", "init"], str(tmp_path))
    _git(["git", "config", "user.email", "test@test.com"], str(tmp_path))
    _git(["git", "config", "user.name", "Test"], str(tmp_path))
    f = tmp_path / "manifest.yaml"
    f.write_text("initial\n")
    _git(["git", "add", "."], str(tmp_path))
    _git(["git", "commit", "-m", "init"], str(tmp_path))


def test_commit_success(tmp_path):
    """Changes -> add + commit + push succeed -> True."""
    _init_repo(tmp_path)
    f = tmp_path / "manifest.yaml"
    f.write_text("changed\n")
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
        capture_output=True, text=True
    ).stdout.strip()

    result = commit_manifest("manifest.yaml", "test commit", str(tmp_path))
    after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
        capture_output=True, text=True
    ).stdout.strip()

    assert result is True
    assert after != before, "should have a new commit"


def test_no_changes_skipped(tmp_path):
    """No changes -> skip -> False."""
    _init_repo(tmp_path)
    result = commit_manifest("manifest.yaml", "no-op", str(tmp_path))
    assert result is False


def test_push_failure_does_not_interrupt(tmp_path):
    """Push fails -> warn but no interrupt -> True."""
    _init_repo(tmp_path)
    _git(["git", "remote", "add", "origin", "/nonexistent/path"], str(tmp_path))
    f = tmp_path / "manifest.yaml"
    f.write_text("changed\n")
    result = commit_manifest("manifest.yaml", "push-fail", str(tmp_path))
    assert result is True, "commit succeeded even though push failed"


def test_add_failure(tmp_path):
    """git add nonexistent path -> False."""
    _init_repo(tmp_path)
    result = commit_manifest("nonexistent.yaml", "fail-add", str(tmp_path))
    assert result is False

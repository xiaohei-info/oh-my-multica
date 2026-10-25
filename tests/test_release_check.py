from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from omac import __version__


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_release.py"


def run_check(tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", tag],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_check_accepts_the_tag_matching_the_project_version() -> None:
    result = run_check(f"v{__version__}")

    assert result.returncode == 0, result.stderr
    assert f"release metadata is consistent for v{__version__}" in result.stdout


def test_release_check_rejects_mismatched_tag() -> None:
    major, minor, patch = __version__.split(".")
    other = f"{major}.{minor}.{int(patch) + 1}"

    result = run_check(f"v{other}")

    assert result.returncode == 1
    assert f"tag version {other} does not match project version {__version__}" in result.stderr

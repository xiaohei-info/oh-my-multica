from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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


def test_release_check_accepts_matching_v1_tag() -> None:
    result = run_check("v1.0.0")

    assert result.returncode == 0, result.stderr
    assert "release metadata is consistent for v1.0.0" in result.stdout


def test_release_check_rejects_mismatched_tag() -> None:
    result = run_check("v1.0.1")

    assert result.returncode == 1
    assert "tag version 1.0.1 does not match project version 1.0.0" in result.stderr

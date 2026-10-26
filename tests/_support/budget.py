"""Per-test resource budgets (PLAN 6.2): wall time, temp-dir size, process peak RSS.

Pure functions so ``tests/test_budget.py`` can unit-test them; ``conftest.py`` wires them to pytest.
"""
from __future__ import annotations

import resource
import sys
from pathlib import Path

MB = 1024 * 1024
UNIT_SECONDS = 0.2
INTEGRATION_SECONDS = 5.0  # real git / local HTTP servers / subprocesses (marker `integration`)
DOCKER_SECONDS = 30.0
UNIT_RSS = 300 * MB
DOCKER_RSS = 500 * MB
TMP_PER_TEST = 10 * MB
TMP_PER_SESSION = 200 * MB

# Files exempt from the per-test budgets. Empty since Phase 8; adding a file here needs a reason in the PR.
EXEMPT_FILES: set[str] = set()


def kind_of(markers) -> str:
    """The budget class of a test from its marker names: docker > integration > unit."""
    names = set(markers)
    return "docker" if "docker" in names else "integration" if "integration" in names else "unit"


def seconds_limit(kind: str) -> float:
    return {"docker": DOCKER_SECONDS, "integration": INTEGRATION_SECONDS}.get(kind, UNIT_SECONDS)


def rss_limit(kind: str) -> int:
    return DOCKER_RSS if kind == "docker" else UNIT_RSS


def peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # macOS reports bytes, Linux KiB


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def check_call(*, seconds: float, tmp_bytes: int, session_tmp_bytes: int, rss_before: int, rss_after: int,
               kind: str = "unit") -> list[str]:
    """Return budget violations for one finished test call."""
    out = []
    if seconds > seconds_limit(kind):
        out.append(f"took {seconds:.2f}s, budget {seconds_limit(kind):.2f}s")
    if tmp_bytes > TMP_PER_TEST:
        out.append(f"wrote {tmp_bytes / MB:.1f}MB to tmp_path, budget {TMP_PER_TEST // MB}MB")
    if session_tmp_bytes > TMP_PER_SESSION:
        out.append(f"session temp files reached {session_tmp_bytes / MB:.0f}MB, budget {TMP_PER_SESSION // MB}MB")
    limit = rss_limit(kind)
    if rss_before <= limit < rss_after:  # blame only the test that pushes the process peak over the limit
        out.append(f"process peak RSS grew to {rss_after / MB:.0f}MB, budget {limit // MB}MB")
    return out

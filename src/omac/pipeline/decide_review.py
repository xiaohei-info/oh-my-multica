"""Pure decision of the Reviewer stage in result collection: which path a submitted verdict takes.

No I/O and no platform access (enforced by tests/test_architecture.py). Boundary, convergence and
nits-acceptance checks read the platform, so the shell runs them only on the path that needs them.
"""
from __future__ import annotations

from enum import Enum


class VerdictPath(Enum):
    REJECT_CHECKS = "reject-checks"  # clean reject: boundary and convergence checks, then rework
    NITS_CHECK = "nits-check"        # clean pass-with-nits: operator acceptance check, then merge
    MERGE = "merge"                  # clean pass
    REWORK = "rework"                # reject with invalid evidence, or any verdict with evidence errors


def decide_verdict_path(*, verdict: str, has_evidence_errors: bool) -> VerdictPath:
    if has_evidence_errors:
        return VerdictPath.REWORK
    if verdict == "reject":
        return VerdictPath.REJECT_CHECKS
    return VerdictPath.NITS_CHECK if verdict == "pass-with-nits" else VerdictPath.MERGE

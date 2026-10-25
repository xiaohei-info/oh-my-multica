"""Pure decision of the Reviewer stage in result collection: which path a submitted verdict takes.

No I/O and no platform access (enforced by tests/test_architecture.py). Boundary, convergence and
nits-acceptance checks read the platform, so the shell runs them only on the path that needs them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .decide_effects import Observation


class VerdictPath(Enum):
    REJECT_CHECKS = "reject-checks"  # clean reject: boundary and convergence checks, then rework
    NITS_CHECK = "nits-check"        # clean pass-with-nits: operator acceptance check, then merge
    MERGE = "merge"                  # clean pass
    REWORK = "rework"                # reject with invalid evidence, or any verdict with evidence errors


@dataclass(frozen=True)
class VerdictObservation(Observation):
    verdict: str
    has_evidence_errors: bool


def decide_verdict_path(obs: VerdictObservation) -> VerdictPath:
    if obs.has_evidence_errors:
        return VerdictPath.REWORK
    if obs.verdict == "reject":
        return VerdictPath.REJECT_CHECKS
    return VerdictPath.NITS_CHECK if obs.verdict == "pass-with-nits" else VerdictPath.MERGE

"""Review ledgers for tests: built with the product's own `advance_review_ledger`, plus the ways one can be forged."""
from __future__ import annotations

from copy import deepcopy

from omac.core.review_convergence import advance_review_ledger


def _report(root: str, evidence: str, classification: str, prior: list[dict], *, failing: bool = True) -> dict:
    return {
        "obligation_results": [{"obligation_id": "dimension:structure", "status": "fail" if failing else "pass", "evidence": evidence}],
        "prior_blocker_results": prior,
        "blockers": ([{
            "root_cause_key": root, "obligation_id": "dimension:structure", "summary": "core contract is incomplete",
            "evidence": evidence, "required_fix": "close the contract root", "classification": classification}] if failing else []),
    }


def stalled(cycle_count: int = 3, *, classification: str = "unchanged") -> dict:
    """One blocker that stays open (``classification`` in every cycle after the first)."""
    ledger, blocker_id = None, None
    for index in range(1, cycle_count + 1):
        current = "new" if index == 1 else classification
        prior = [] if blocker_id is None else [{"blocker_id": blocker_id, "status": current, "evidence": "the same invariant still fails"}]
        ledger = advance_review_ledger(
            ledger, _report("core-acceptance", "the same invariant still fails", current, prior), verdict="reject",
            subject_digest=f"subject-{index}", round_index=index)
        blocker_id = ledger["blockers"][0]["blocker_id"]
    return ledger


def late_scope() -> dict:
    """Five clean passes, then a new blocker root in cycle six."""
    ledger = None
    for index in range(1, 7):
        last = index == 6
        report = _report("late-scope", "cycle six introduced a new root", "new", [], failing=last)
        ledger = advance_review_ledger(
            ledger, report, verdict="reject" if last else "pass", subject_digest=f"subject-{index}", round_index=index)
    return ledger


def forged(name: str) -> tuple[int, dict]:
    """(handoff round, ledger) for one way of rewriting canonical cycle facts through the blocker fields."""
    if name == "first-seen-round":
        ledger = late_scope()
        ledger["blockers"][0]["first_seen_round"] = 1
        return 6, ledger
    ledger = stalled()
    blocker, cycles = ledger["blockers"][0], ledger["cycles"]
    if name == "current-status":
        blocker.update(status="fixed", classification="fixed")
    elif name == "current-classification-fixed":
        blocker["classification"] = "fixed"
    elif name == "current-classification-deeper":
        blocker["classification"] = "deeper"
    elif name == "cycle-id-underreport":
        cycles[1].update(open_blocker_ids=[], reported_blocker_ids=[])
        blocker["seen_count"] = 2
    elif name == "latest-reported-not-open":
        cycles[-1]["open_blocker_ids"] = []
        blocker.update(status="fixed", classification="fixed")
    elif name == "impossible-seen-count":
        blocker["seen_count"] = 2
    elif name == "truncated-history":
        ledger = stalled(1)
        ledger["cycles"][0]["round"] = 10
        ledger["blockers"][0].update(classification="deeper", last_seen_round=10, seen_count=1)
        return 10, ledger
    else:
        raise KeyError(name)
    return 3, ledger


FORGERIES = ("first-seen-round", "current-status", "current-classification-fixed", "current-classification-deeper",
             "cycle-id-underreport", "latest-reported-not-open", "impossible-seen-count", "truncated-history")

# Parseable but not canonical: each must be refused before it can steer a handoff.
UNTRUSTED = {
    "empty": {},
    "future-schema": {"schema": "future.review-ledger/v9", "cycles": [], "blockers": []},
    "cycles-not-a-list": {"schema": "omac.review-ledger/v1", "cycles": {}, "blockers": []},
    "blockers-not-a-list": {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": {}},
    "cycle-not-a-mapping": {"schema": "omac.review-ledger/v1", "cycles": ["bad"], "blockers": []},
    "blocker-not-a-mapping": {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": ["bad"]},
    "cycle-without-counts": {"schema": "omac.review-ledger/v1", "cycles": [{"round": 1}], "blockers": []},
    "blocker-from-nowhere": {
        "schema": "omac.review-ledger/v1", "cycles": [{"round": 3, "open_count": 1}],
        "blockers": [{"blocker_id": "BLK-1", "root_cause_key": "root-1", "obligation_id": "dimension:structure",
                      "status": "open", "classification": "deeper", "first_seen_round": 1}]},
}


def copy_of(ledger: dict) -> dict:
    return deepcopy(ledger)

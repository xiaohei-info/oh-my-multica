"""Review ledgers for tests: built with the product's own `advance_review_ledger`, plus the ways one can be forged."""
from __future__ import annotations

from copy import deepcopy

from omac.core import review_convergence as review_mod
from omac.core.review_convergence import REVIEW_PROTOCOL_VERSION, advance_review_ledger


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


def _with_cycle_blocker_facts(ledger):
    records = {
        record["blocker_id"]: record for record in ledger["blockers"]
    }
    current = {}
    prior_open_ids = set()
    for cycle in ledger["cycles"]:
        open_ids = set(cycle["open_blocker_ids"])
        cycle.setdefault("subject_digest", f"subject-{cycle['round']}")
        cycle.setdefault("verdict", "reject" if open_ids else "pass")
        facts = []
        for blocker_id in sorted(prior_open_ids - open_ids):
            previous = current[blocker_id]
            facts.append({
                field: previous[field]
                for field in review_mod._BLOCKER_FACT_FIELDS[:6]
            } | {
                "status": "fixed",
                "classification": "fixed",
                "last_evidence": f"fixed in round {cycle['round']}",
            })
        for blocker_id in sorted(open_ids):
            source = records.setdefault(blocker_id, {
                "blocker_id": blocker_id,
                "root_cause_key": f"root-{blocker_id.removeprefix('BLK-')}",
                "obligation_id": "dimension:structure",
                "status": "open",
                "classification": "new",
                "owner": f"owner-{len(records) % 3}",
            })
            previous = current.get(blocker_id)
            if previous is None:
                classification = "new"
            elif previous["status"] == "fixed":
                classification = "regressed"
            else:
                classification = (
                    "unchanged"
                    if source.get("classification") == "unchanged"
                    else "deeper"
                )
            fact = {
                "blocker_id": blocker_id,
                "root_cause_key": source["root_cause_key"],
                "obligation_id": source["obligation_id"],
                "summary": source.get("summary", blocker_id),
                **({"owner": source["owner"]} if source.get("owner") else {}),
                "evidence": source.get("evidence", f"evidence for {blocker_id}"),
                "required_fix": source.get("required_fix", f"fix {blocker_id}"),
                "status": "open",
                "classification": classification,
            }
            if previous is not None and previous["status"] == "open":
                fact["last_evidence"] = f"still open in round {cycle['round']}"
            facts.append(fact)
        cycle["blocker_facts_schema"] = (
            review_mod.REVIEW_CYCLE_BLOCKER_FACTS_SCHEMA
        )
        cycle["blocker_facts"] = facts
        cycle["prior_open_blocker_ids"] = sorted(prior_open_ids)
        cycle["open_blocker_ids"] = sorted(open_ids)
        cycle["reported_blocker_ids"] = sorted(open_ids)
        cycle["new_count"] = sum(
            fact["classification"] == "new" for fact in facts)
        cycle["fixed_count"] = sum(
            fact["status"] == "fixed" for fact in facts)
        cycle["regressed_count"] = sum(
            fact["classification"] == "regressed" for fact in facts)
        cycle["unchanged_count"] = sum(
            fact["classification"] == "unchanged" for fact in facts)
        cycle["open_count"] = len(open_ids)
        cycle["obligation_results"] = {
            fact["obligation_id"]: "fail"
            for fact in facts if fact["status"] == "open"
        }
        current_ledger = {
            "schema": "omac.review-ledger/v1",
            "cycles": ledger["cycles"][:cycle["round"]],
            "blockers": [],
        }
        current = {
            blocker["blocker_id"]: blocker
            for blocker in review_mod._canonical_cycle_projection(
                current_ledger["cycles"])
        }
        prior_open_ids = open_ids
    ledger["blockers"] = sorted(current.values(), key=lambda item: item["blocker_id"])
    return ledger


def decision_ledger(
    open_counts, *, new_counts=None, obligation_ids=("dimension:structure",),
    first_seen_rounds=None, classifications=None,
):
    del new_counts, first_seen_rounds
    final_classifications = classifications or ["unchanged"]
    max_open_count = max(open_counts)
    blockers = [
        {
            "blocker_id": f"BLK-{index}",
            "root_cause_key": f"root-{index}",
            "obligation_id": obligation_ids[(index - 1) % len(obligation_ids)],
            "status": "open",
            "classification": final_classifications[
                (index - 1) % len(final_classifications)
            ],
            "first_seen_round": 1,
            "last_seen_round": len(open_counts),
            "seen_count": len(open_counts),
            "owner": f"owner-{(index - 1) % 3}",
        }
        for index in range(1, max_open_count + 1)
    ]
    cycles = []
    previous_ids = set()
    next_id = 1
    active_ids = set()
    for round_index, open_count in enumerate(open_counts, start=1):
        while len(active_ids) < open_count:
            active_ids.add(f"BLK-{next_id}")
            next_id += 1
        while len(active_ids) > open_count:
            active_ids.remove(sorted(active_ids)[-1])
        cycles.append({
            "round": round_index,
            "new_count": len(active_ids - previous_ids),
            "fixed_count": len(previous_ids - active_ids),
            "regressed_count": 0,
            "unchanged_count": 0,
            "open_count": open_count,
            "prior_open_blocker_ids": sorted(previous_ids),
            "open_blocker_ids": sorted(active_ids),
            "reported_blocker_ids": sorted(active_ids),
        })
        previous_ids = set(active_ids)
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": cycles,
        "blockers": blockers,
    }
    return _with_cycle_blocker_facts(ledger)


def review_results(obligations, *, failed=()):
    failed = set(failed)
    return [
        {
            "obligation_id": obligation["obligation_id"],
            "status": "fail" if obligation["obligation_id"] in failed else "pass",
            "evidence": f"checked {obligation['obligation_id']}",
        }
        for obligation in obligations
    ]


def review_report(obligations, *, blockers=None, prior=None, failed=()):
    return {
        "review_protocol": REVIEW_PROTOCOL_VERSION,
        "full_review_completed": True,
        "obligation_results": review_results(obligations, failed=failed),
        "prior_blocker_results": prior or [],
        "blockers": blockers or [],
        "nits": [],
    }

"""Pure decision: can a Worker delivery that predates handoff identities be proven, so it needs no person (设计 批量修复 2026-11-13).

A legacy initial Worker item has Runs but no persisted ``worker_handoff``, so the Loop cannot tell from the Runs alone which
one delivered. When the delivery itself carries the proof, nothing is left for a person to check: the verification attachment
was uploaded by the node's Worker during exactly one completed Run, it is the very content the item projects, and the PR still
has the head the Worker submitted. Anything short of that stays an explicit decision, and the decision names what is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .decide_runs import BASELINE_MAX_DIRECT_RUN_IDS

# Proof items a person is told about; stable identifiers, shown in the decision report.
FOREIGN_RUN = "foreign-direct-run"                          # another agent also ran directly on the item
PRIOR_REVIEW_FACTS = "prior-review-or-rework-facts"         # a rework/review history exists: the delivery may be stale
OPERATOR_NOTE = "operator-retry-note"                        # a person asked for different work with `node retry --note`
RUN_HISTORY_TOO_LONG = "run-history-exceeds-baseline-cap"   # the Run baseline cannot be written completely
NO_STABLE_RUN_IDENTITY = "platform-run-identity-not-stable"  # the platform cannot tell Runs apart reliably
NO_DELIVERY = "no-complete-delivery"                        # artifacts / verification are missing or unreadable
PROJECTION_MISMATCH = "verification-projection-mismatch"    # the attachment is not what the item projects
PR_HEAD_MOVED = "pr-head-moved-after-submit"                # the PR no longer has the submitted head
NO_CAUSAL_RUN = "no-completed-run-produced-the-verification"
AMBIGUOUS_CAUSAL_RUN = "several-runs-could-have-produced-the-verification"


@dataclass(frozen=True)
class LegacyDeliveryFacts:
    stable_run_identity: bool
    foreign_run: bool
    prior_review_facts: bool
    operator_note: bool
    direct_run_count: int
    delivery_problem: str | None      # None: the delivery projection was read and matches the remote PR head
    causal_completed_run_ids: Tuple[str, ...]   # completed Worker Runs the verification attachment is causally bound to


@dataclass(frozen=True)
class LegacyAdoption:
    run_id: str | None                # the Run whose delivery is adopted; None: not provable
    missing: Tuple[str, ...] = ()

    @property
    def adopt(self) -> bool:
        return self.run_id is not None


def decide_legacy_delivery(facts: LegacyDeliveryFacts) -> LegacyAdoption:
    """Adopt the delivery only when every proof item holds; otherwise list every item that does not."""
    missing = [
        code for code, failed in (
            (NO_STABLE_RUN_IDENTITY, not facts.stable_run_identity),
            (FOREIGN_RUN, facts.foreign_run),
            (PRIOR_REVIEW_FACTS, facts.prior_review_facts),
            (OPERATOR_NOTE, facts.operator_note),
            (RUN_HISTORY_TOO_LONG, facts.direct_run_count > BASELINE_MAX_DIRECT_RUN_IDS),
        ) if failed
    ]
    if facts.delivery_problem:
        missing.append(facts.delivery_problem)
    elif len(facts.causal_completed_run_ids) == 0:
        missing.append(NO_CAUSAL_RUN)
    elif len(facts.causal_completed_run_ids) > 1:
        missing.append(AMBIGUOUS_CAUSAL_RUN)
    if missing:
        return LegacyAdoption(None, tuple(missing))
    return LegacyAdoption(facts.causal_completed_run_ids[0])

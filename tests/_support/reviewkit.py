"""Helpers shared by several test modules (moved out of the test file that happened to define them)."""
from __future__ import annotations

from _support.ledgers import review_report as _report
from types import SimpleNamespace
from omac.core.review_convergence import advance_review_ledger, build_review_obligations, validate_convergence_review
from omac.core.taskmeta import TaskKind


def _item(*, contract=None, ledger=None):
    return SimpleNamespace(
        kind=TaskKind.DEVELOP,
        contract=contract,
        review_ledger=ledger,
        review_obligations=None,
    )



def _production_multi_blocker_ledger(
    classifications_by_round,
    *,
    obligation_ids=(
        "dimension:structure",
        "dimension:execution",
        "dimension:ownership",
    ),
):
    item = _item()
    obligations = build_review_obligations(item)
    roots = ("root-a", "root-b", "root-c")
    ledger = None
    for round_index, classifications in enumerate(
        classifications_by_round, start=1,
    ):
        prior = []
        if ledger is not None:
            records = {
                record["root_cause_key"]: record
                for record in ledger["blockers"]
            }
            prior = [
                {
                    "blocker_id": records[root]["blocker_id"],
                    "status": classification,
                    "evidence": f"round {round_index} still shows {root}",
                }
                for root, classification in zip(roots, classifications)
            ]
        blockers = [
            {
                "root_cause_key": root,
                "obligation_id": obligation_id,
                "classification": classification,
                "owner": f"owner-{root[-1]}",
                "summary": f"{root} remains open",
                "evidence": f"round {round_index} evidence for {root}",
                "required_fix": f"fix {root}",
            }
            for root, obligation_id, classification in zip(
                roots, obligation_ids, classifications,
            )
        ]
        report = _report(
            obligations,
            prior=prior,
            failed=set(obligation_ids),
            blockers=blockers,
        )
        item.review_ledger = ledger
        item.review_obligations = obligations
        assert validate_convergence_review(item, "reject", report) == []
        ledger = advance_review_ledger(
            ledger,
            report,
            verdict="reject",
            subject_digest=f"v{round_index}",
            round_index=round_index,
        )
    return ledger


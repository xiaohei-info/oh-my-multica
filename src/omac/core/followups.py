"""Follow-ups: the non-blocking nits that nodes passed with, gathered for the closeout node to dispose of.

A follow-up is one ``review_report.nits`` entry of a node whose verdict was ``pass-with-nits`` (the default
``review.node_nits: accept`` merges such a node and leaves the nits only in its review report). Nothing here fixes
anything: the closeout review requires every follow-up to be disposed of as ``fix``, ``defer`` or ``drop``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

TRIAGE_OBLIGATION_ID = "followups:triage"
TRIAGE_CATEGORY = "followups-triage"
DISPOSITIONS = ("fix", "defer", "drop")
_DISPOSITION = re.compile(r"\b(fix|fixed|defer|deferred|drop|dropped)\b", re.IGNORECASE)


def followups_of(entries: Iterable[tuple[str, Any]]) -> list[dict]:
    """``(node_key, work_item)`` pairs -> one record per nit, ids ``<node>#<n>`` (n counts from 1 within the node)."""
    records = []
    for node_key, item in entries:
        report = getattr(item, "review_report", None)
        nits = report.get("nits") if isinstance(report, dict) else None
        if getattr(item, "review_verdict", None) != "pass-with-nits" or not isinstance(nits, list):
            continue
        for index, nit in enumerate((n for n in nits if isinstance(n, str) and n.strip()), start=1):
            records.append({
                "followup_id": f"{node_key}#{index}", "node": node_key,
                "work_item_id": getattr(item, "id", None), "nit": nit.strip()})
    return records


def triage_obligation(followups: Iterable[dict]) -> dict | None:
    """The one closeout review obligation covering every follow-up; None when there is nothing to dispose of."""
    ids = [record["followup_id"] for record in followups]
    if not ids:
        return None
    return {
        "obligation_id": TRIAGE_OBLIGATION_ID,
        "category": TRIAGE_CATEGORY,
        "requirement": (
            f"Run `omac dag followups <manifest>`: each of the {len(ids)} follow-ups must be disposed of in this "
            "delivery as fix, defer or drop. In the evidence, name every follow-up id with its disposition "
            "(one `<id>: fix|defer|drop - <where>` line each)."),
        "subject": ids,
    }


def triage_gaps(followup_ids: Iterable[str], evidence: str) -> list[str]:
    """The follow-up ids the evidence does not dispose of: no line names the id together with fix, defer or drop."""
    lines = evidence.splitlines()
    return [
        followup_id for followup_id in followup_ids
        if not any(
            re.search(rf"(?<![\w.#-]){re.escape(followup_id)}(?![\w#])", line) and _DISPOSITION.search(line)
            for line in lines)]

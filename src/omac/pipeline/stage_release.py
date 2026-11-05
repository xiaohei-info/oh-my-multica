"""Releasing a stopped plan stage by an explicit operator command: read the facts, ask ``decide_stop``, apply the plan."""
from __future__ import annotations

from typing import Any, Dict

from ..core.review_continuation import authorized_review_limit
from ..core.taskmeta import TaskKind, exact_review_report_ref
from ..core.workitem import WorkItem
from ..errors import ValidationError
from .apply import Ports, apply
from .decide_runs import active_direct_runs
from .nits_acceptance import nits_marker, persist_nits_acceptance
from .decide_stop import Refusal, Release, ReleaseFacts, decide_release
from .tasks import _review_subject_digest


def release_facts(engine, item: WorkItem, configured_limit: int) -> ReleaseFacts:
    round_index = max(1, item.bounces.review + 1)
    bound = bool(
        item.review_subject_digest
        and item.review_subject_digest == _review_subject_digest(item.kind, item, round_index)
        and isinstance(item.review_report, dict) and item.review_report
        and exact_review_report_ref(item.review_report_ref))
    return ReleaseFacts(
        item_id=item.id, status=item.status, phase=item.phase, verdict=item.review_verdict or "",
        has_deliverable=bool(item.deliverable), decision=item.decision_required if item.requires_decision else None,
        bounce=max(0, item.bounces.review), limit=authorized_review_limit(item, configured_limit),
        continuation=item.review_continuation,
        active_runs=tuple(run.id for run in active_direct_runs(engine.runtime.list_runs(item.id))),
        delivery_bound=bound)


def release_stage(
    engine, item: WorkItem, release: Release, *, configured_limit: int, reason: str = "",
) -> Dict[str, Any]:
    """Apply one release to ``item``; a refusal is a ValidationError that changed nothing."""
    kind: TaskKind = item.kind
    decided = decide_release(
        release, release_facts(engine, item, configured_limit), stage=kind.value, reason=reason)
    if isinstance(decided, Refusal):
        raise ValidationError(decided.message)
    apply(decided.effects, Ports(store=engine.store))
    current = engine.store.get_work_item(item.id)
    continuation = current.review_continuation or {}
    return {
        "mode": decided.outcome,
        "authorized_through_round": authorized_review_limit(current, configured_limit),
        "decision_count": continuation.get("decision_count", 0),
    }


def accept_nits(engine, item: WorkItem, *, configured_limit: int, retry_command: str) -> None:
    """`plan accept-nits`: the operator accepts the nits of the review the stage stands on; resume then finishes the stage."""
    decided = decide_release(Release.ACCEPT_NITS, release_facts(engine, item, configured_limit), stage=item.kind.value)
    if isinstance(decided, Refusal):
        raise ValidationError(decided.message)
    marker = nits_marker(item, retry_command=retry_command)
    persist_nits_acceptance(engine, item, marker, rerun_command=retry_command)

"""What an explicit ``omac node retry`` carries over from the review it follows: facts in, a Worker handoff (or a refusal) out.

Which retry history counts, whether the review feedback is still current, which PR head is the baseline for the Worker's
delta, and which feedback fields must exist are functions of the node and the item's control facts. No I/O (enforced by
tests/test_architecture.py); the platform reads they need are passed in.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from ..core.stage_recovery import stage_recovery_subject
from ..core.taskmeta import (
    WORKER_HANDOFF_SCHEMA, TaskPhase, WorkerHandoffIntent, build_worker_rework_feedback, retained_review_subject,
    review_context_binding, review_feedback_is_current,
)
from ..core.workitem import WorkItemStatus
from .decide_runs import bounded_direct_run_baseline


class RetryRefused(Exception):
    """The retry cannot carry the review over; ``code`` is ``rework-context-unavailable`` or ``retained-source-conflict``."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class RetryFacts:
    """Facts of the current WorkItem that decide how a retry may resume authoring."""
    resuming_authoring: bool
    handoff: Any
    prior_handoff: Any
    has_delivery: bool
    prior_rework: bool
    review_current: bool
    has_retry_history: bool
    prior_feedback: Any


def retry_facts(node, current, stage: str) -> RetryFacts:
    resuming_authoring = bool(
        stage == "authoring" and current.worker_handoff is not None
        and (current.worker_handoff.authoring_recovery is not None
             or (current.phase == TaskPhase.AUTHORING
                 and current.status == WorkItemStatus.TODO
                 and current.worker_handoff.gate == "operator-retry"
                 and current.worker_handoff.target_worker == node.worker
                 and current.worker_handoff.target_run_id is None
                 and review_feedback_is_current(current, current.worker_handoff))))
    prior_handoff = current.worker_handoff
    if prior_handoff is not None and not review_feedback_is_current(current, prior_handoff):
        prior_handoff = None
    contract_matches = (
        review_context_binding(current)["contract_sha256"]
        == review_context_binding(replace(current, contract=node.contract))["contract_sha256"]
    )
    if not contract_matches:
        prior_handoff = None
    prior_rework = bool(
        prior_handoff is not None
        and (prior_handoff.source_review_verdict in {"reject", "pass-with-nits"}
             or prior_handoff.baseline_pr_head_sha))
    prior_feedback = (
        prior_handoff.source_review_feedback
        if prior_handoff is not None and isinstance(prior_handoff.source_review_feedback, dict)
        else None)
    return RetryFacts(
        resuming_authoring=resuming_authoring,
        handoff=current.worker_handoff if resuming_authoring else None,
        prior_handoff=prior_handoff,
        has_delivery=bool(current.artifacts or current.verification),
        prior_rework=prior_rework,
        # An explicit authoring handoff proves this execution's identity,
        # not that a retired review ledger is current again.
        review_current=contract_matches and (review_feedback_is_current(current) or prior_rework),
        has_retry_history=bool(
            current.bounces.review > 0 or current.review_verdict == "reject" or prior_rework),
        prior_feedback=prior_feedback,
    )


def needs_review_context(node, current, stage: str, facts: RetryFacts) -> bool:
    """Whether the review feedback must be recovered from the platform's comments before it can be carried over."""
    return bool(
        stage == "authoring" and node.reviewer and facts.has_delivery
        and facts.has_retry_history and facts.review_current
        and not current.review_report and not current.review_report_ref
        and not facts.prior_feedback)


def wants_rework_handoff(node, stage: str, facts: RetryFacts) -> bool:
    return bool(stage == "authoring" and not facts.resuming_authoring and node.reviewer
                and facts.has_retry_history and facts.has_delivery and facts.review_current)


def retry_baseline_head(current, prior_handoff):
    """The PR head the Worker's next delivery is measured against."""
    identity_head = getattr(current.delivery_identity, "pr_head_sha", None)
    artifact_head = (
        str(current.artifacts.get("head_sha") or "")
        if isinstance(current.artifacts, dict) else "")
    return (
        identity_head or artifact_head
        or (prior_handoff.baseline_pr_head_sha if prior_handoff is not None else None)
        or None)


def plan_rework_handoff(
    node, current, facts: RetryFacts, recovered_context: dict, *, store, list_runs: Callable[[], list],
    agent_id_of: Callable[[str], str], generation: str,
) -> WorkerHandoffIntent:
    """The operator-retry handoff that carries the prior review feedback into authoring.

    ``store`` is only used to read the attachments that prove a retained review subject; ``list_runs`` and ``agent_id_of``
    are read after the refusals, as the platform reads of an ordinary retry.
    """
    prior_handoff = facts.prior_handoff
    source_review_feedback = build_worker_rework_feedback(current, prior_handoff, recovered_context)
    try:
        retained_subject = retained_review_subject(current, prior_handoff, source_review_feedback, store=store)
    except ValueError as exc:
        raise RetryRefused("retained-source-conflict", str(exc)) from exc
    source_subject = (
        retained_subject or current.review_subject_digest
        or recovered_context.get("subject_digest")
        or stage_recovery_subject(node, current))
    baseline_direct_run_ids, baseline_cutoff_created_at = bounded_direct_run_baseline(list_runs())
    source_review_verdict = (
        current.review_verdict if current.review_verdict in {"reject", "pass-with-nits"}
        else (prior_handoff.source_review_verdict if facts.prior_rework else None))
    if source_review_verdict is None and source_review_feedback:
        source_review_verdict = source_review_feedback.get("verdict")
    feedback_fields = ("report_ref", "ledger_ref", "blockers", "comment")
    if facts.has_retry_history and (
            source_review_feedback is None
            or not any(source_review_feedback.get(f) for f in feedback_fields)):
        raise RetryRefused("rework-context-unavailable")
    return WorkerHandoffIntent(
        schema=WORKER_HANDOFF_SCHEMA,
        state="pending",
        target_worker=node.worker,
        gate="operator-retry",
        source_review_feedback=source_review_feedback,
        source_review_subject_digest=source_subject,
        source_review_round=max(1, current.bounces.review),
        source_review_verdict=source_review_verdict,
        target_review_bounce=max(1, current.bounces.review),
        generation=generation,
        target_agent_id=agent_id_of(node.worker),
        baseline_direct_run_ids=baseline_direct_run_ids,
        baseline_cutoff_created_at=baseline_cutoff_created_at,
        baseline_verification_attachment_id=str(
            (current.verification_ref or {}).get("attachment_id") or "") or None,
        baseline_pr_head_sha=retry_baseline_head(current, prior_handoff),
        target_worker_bounce=current.bounces.worker,
        review_context_binding=review_context_binding(current),
    )

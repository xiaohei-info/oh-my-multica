"""Control records of the task model, as one valid example each (shared by the unit tests and tests/it)."""
from omac.core.taskmeta import (
    DELIVERY_IDENTITY_SCHEMA, REVIEWER_RUN_BASELINE_SCHEMA, DeliveryIdentity, ReviewerRunBaseline, WorkerHandoffIntent,
)


def reviewer_run_baseline():
    return ReviewerRunBaseline(
        schema=REVIEWER_RUN_BASELINE_SCHEMA,
        subject_digest="subject-1",
        target_reviewer="bob",
        target_agent_id="agent-bob",
        baseline_direct_run_ids=("run-worker", "run-old-reviewer"),
    )


def worker_handoff_intent():
    return WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="alice",
        gate="review",
        source_review_subject_digest="subject-1",
        source_review_round=1,
        target_review_bounce=1,
        generation="handoff-generation-1",
        target_agent_id="agent-alice",
        baseline_direct_run_ids=("run-old",),
        baseline_pr_head_sha="head-old",
        target_run_id="run-new",
        target_worker_bounce=2,
        terminal_observed_at="2026-07-31T06:30:00+00:00",
    )


def delivery_identity():
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation="handoff-generation-1",
        worker="alice",
        agent_id="agent-alice",
        run_id="run-new",
        pr_url="https://github.com/acme/repo/pull/1",
        pr_head_sha="head-1",
        verification_sha256="verification-1",
    )


def review_nits_handoff_payload(**feedback_updates):
    feedback = {
        "verdict": "pass-with-nits",
        "nits": ["name the exact compatibility fixture"],
        "report_ref": {
            "attachment_id": "review-1",
            "sha256": "a" * 64,
        },
    }
    feedback.update(feedback_updates)
    return {
        **worker_handoff_intent().as_dict(),
        "gate": "review-nits",
        "source_review_verdict": "pass-with-nits",
        "source_review_feedback": feedback,
    }

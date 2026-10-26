"""What `omac node retry` carries from the review it follows into the Worker's handoff. Item and node facts in, a handoff or a refusal out."""
from dataclasses import replace

import pytest

from _support.recoverykit import KEY, REVIEWER, delivered_item, manifest
from omac.core.manifest import Contract
from omac.core.taskmeta import WORKER_REWORK_FEEDBACK_SCHEMA, Bounces, WorkerHandoffIntent
from omac.core.workitem import AgentRunObservation
from omac.pipeline.decide_retry import (
    RetryRefused, needs_review_context, plan_rework_handoff, retry_baseline_head, retry_facts, wants_rework_handoff,
)

HEAD = "h"
REPORT_REF = {"attachment_id": "report-1", "comment_id": "comment-r", "sha256": "b" * 64}
REPORT = {"blockers": [{"summary": "restore the context", "required_fix": "provide the next change"}]}


def rejected(**fields):
    """Delivered, then rejected in the first review round and blocked."""
    item = replace(delivered_item(), review_verdict="reject", review_report=REPORT, review_report_ref=REPORT_REF,
                   bounces=Bounces(review=1), decision_required=None, reviewer_run_baseline=None)
    return replace(item, **fields)


def prior_handoff(**fields):
    return WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="alice", gate="operator-retry",
        source_review_verdict="reject", generation="prior-retry", target_agent_id="agent-alice", baseline_pr_head_sha=HEAD, **fields)


def plan(item, recovered=None, runs=()):
    node = manifest().nodes[KEY]
    facts = retry_facts(node, item, "authoring")
    assert wants_rework_handoff(node, "authoring", facts)
    return plan_rework_handoff(node, item, facts, recovered or {}, store=None, list_runs=lambda: list(runs),
                               agent_id_of=lambda name: f"agent-{name}", generation="handoff-test")


@pytest.mark.parametrize(("item", "verdict", "head"), [
    pytest.param(rejected(), "reject", HEAD, id="a-live-reject"),
    pytest.param(rejected(review_verdict=""), None, HEAD, id="no-live-verdict-still-records-the-head"),
    pytest.param(rejected(bounces=Bounces(review=0)), "reject", HEAD, id="reject-before-the-first-bounce-was-counted"),
    pytest.param(rejected(delivery_identity=replace(delivered_item().delivery_identity, pr_head_sha=None)), "reject", HEAD,
                 id="an-incomplete-identity-falls-back-to-the-artifact-head"),
])
def test_a_plain_retry_measures_the_workers_next_delivery_against_the_head_that_was_judged(item, verdict, head):
    handoff = plan(item)

    assert (handoff.gate, handoff.state, handoff.target_worker) == ("operator-retry", "pending", "alice")
    assert handoff.source_review_verdict == verdict and handoff.baseline_pr_head_sha == head
    assert handoff.source_review_feedback["schema"] == WORKER_REWORK_FEEDBACK_SCHEMA
    assert handoff.source_review_feedback["report_ref"] == REPORT_REF
    assert handoff.generation == "handoff-test" and handoff.target_agent_id == "agent-alice"
    assert handoff.baseline_verification_attachment_id == "att"


def test_a_retry_keeps_the_head_of_the_prior_handoff_when_the_item_no_longer_shows_one():
    item = rejected(review_verdict="", bounces=Bounces(review=0), artifacts={"pr_url": "https://example.test/pr/1"},
                    delivery_identity=replace(delivered_item().delivery_identity, pr_head_sha=None),
                    worker_handoff=prior_handoff())

    handoff = plan(item)

    assert handoff.source_review_verdict == "reject" and handoff.baseline_pr_head_sha == HEAD
    assert retry_baseline_head(item, None) is None


def test_feedback_that_is_gone_from_the_item_is_recovered_from_the_platforms_comments():
    item = rejected(review_verdict=None, review_report=None, review_report_ref=None, review_subject_digest=None)
    node, facts = manifest().nodes[KEY], retry_facts(manifest().nodes[KEY], item, "authoring")
    recovered = {"verdict": "reject", "subject_digest": "recovered-subject", "report_ref": REPORT_REF,
                 "blockers": [{"root_cause_key": "auth-boundary", "summary": "add the missing auth method",
                               "required_fix": "implement auth method"}]}

    assert needs_review_context(node, item, "authoring", facts)
    handoff = plan(item, recovered)

    assert (handoff.source_review_verdict, handoff.source_review_subject_digest) == ("reject", "recovered-subject")
    feedback = handoff.source_review_feedback
    assert feedback["verdict"] == "reject" and feedback["report_ref"] == REPORT_REF
    assert feedback["blockers"][0]["required_fix"] == "implement auth method"


def test_a_retry_refuses_when_the_rejection_left_no_context_to_carry():
    item = rejected(review_report=None, review_report_ref=None, review_subject_digest=None, bounces=Bounces(review=0))

    with pytest.raises(RetryRefused) as refusal:
        plan(item)

    assert refusal.value.code == "rework-context-unavailable"


@pytest.mark.parametrize("item", [
    pytest.param(rejected(review_generation="sdk-only", review_ledger_generation="retired-host", bounces=Bounces(review=8)),
                 id="a-retired-review-generation"),
    pytest.param(replace(rejected(), contract={**rejected().contract, "objective": "changed"}), id="the-contract-of-the-review-changed"),
])
def test_feedback_of_another_generation_or_contract_is_never_rebound_to_the_new_authoring(item):
    node = manifest().nodes[KEY]
    facts = retry_facts(node, item, "authoring")

    assert not facts.review_current
    assert not wants_rework_handoff(node, "authoring", facts) and not needs_review_context(node, item, "authoring", facts)


def test_the_baseline_keeps_only_the_latest_twenty_direct_runs_and_their_cutoff():
    runs = [AgentRunObservation(id=f"run-{index:02d}", kind="direct", status="completed", created_at=f"2026-08-01T00:{index:02d}:00Z")
            for index in range(21)]

    handoff = plan(rejected(), runs=runs)

    assert handoff.baseline_direct_run_ids == tuple(f"run-{index:02d}" for index in range(1, 21))
    assert handoff.baseline_cutoff_created_at == "2026-08-01T00:20:00Z"


@pytest.mark.parametrize(("identity", "artifact", "prior", "expected"), [
    pytest.param("h-identity", "h-artifact", "h-prior", "h-identity", id="the-sealed-identity-first"),
    pytest.param(None, "h-artifact", "h-prior", "h-artifact", id="then-the-artifact"),
    pytest.param(None, None, "h-prior", "h-prior", id="then-the-prior-handoff"),
    pytest.param(None, None, None, None, id="nothing-known"),
])
def test_the_head_to_measure_against_comes_from_the_most_authoritative_source_left(identity, artifact, prior, expected):
    item = rejected(artifacts={"pr_url": "https://example.test/pr/1", **({"head_sha": artifact} if artifact else {})},
                    delivery_identity=replace(delivered_item().delivery_identity, pr_head_sha=identity))
    handoff = replace(prior_handoff(), baseline_pr_head_sha=prior) if prior else None

    assert retry_baseline_head(item, handoff) == expected

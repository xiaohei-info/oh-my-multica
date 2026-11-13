"""When do control facts prove a Worker handoff produced a new delivery? Item and intent in, a verdict out. No platform."""
from dataclasses import replace

import pytest

from omac.core.taskmeta import DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity, TaskPhase, WorkerHandoffIntent
from omac.core.workitem import WorkItem, WorkItemStatus
from omac.pipeline.decide_worker import (
    EnvironmentFailure, EnvironmentFailureObservation, control_matches_handoff_candidate, decide_environment_failure,
    handoff_has_new_delivery, head_proves_new_delivery, next_worker_retry_command,
)

PR = "https://example.test/pr/1"


def delivered(head="head-old", attachment="attachment-new", status=WorkItemStatus.DONE, **fields):
    artifacts = {"pr_url": PR, **({"head_sha": head} if head else {})}
    return WorkItem(
        id="item-1", workspace_id="ws", title="t", description="", status=status, dag_key="a", artifacts=artifacts,
        verification_ref={"attachment_id": attachment}, **fields)


def intent(gate, verdict=None, head="head-old", attachment="attachment-old", **fields):
    return WorkerHandoffIntent(
        gate=gate, source_review_verdict=verdict, baseline_verification_attachment_id=attachment,
        baseline_pr_head_sha=head, generation="g1", **fields)


@pytest.mark.parametrize(("item", "handoff", "expected"), [
    # a rejected delivery needs a changed, known head
    pytest.param(delivered(), intent("review", "reject"), False, id="reject-same-head"),
    pytest.param(delivered(head="head-new"), intent("review", "reject"), True, id="reject-changed-head"),
    pytest.param(delivered(head=None), intent("review", "reject"), False, id="reject-head-unknown-now"),
    pytest.param(delivered(), intent("review", "reject", head=None), False, id="reject-legacy-without-baseline-head"),
    pytest.param(delivered(head="head-new"), intent("operator-retry", "reject"), True, id="operator-retry-of-reject-changed-head"),
    pytest.param(delivered(), intent("operator-retry", "reject"), False, id="operator-retry-of-reject-same-head"),
    pytest.param(delivered(), intent("explicit-dispatch", "reject"), False, id="explicit-dispatch-of-reject-same-head"),
    pytest.param(delivered(), intent("explicit-dispatch", "reject", head=None), False, id="explicit-dispatch-of-reject-no-baseline-head"),
    # an operator retry cannot prove a change without a captured baseline, except after pass-with-nits
    pytest.param(delivered(), intent("operator-retry"), False, id="operator-retry-same-head"),
    pytest.param(delivered(), intent("operator-retry", head=None), False, id="operator-retry-unknown-baseline"),
    pytest.param(delivered(head=None), intent("operator-retry", head="head-old"), False, id="operator-retry-head-missing-now"),
    pytest.param(delivered(), intent("operator-retry", "pass-with-nits", head=None), True, id="operator-retry-of-nits"),
    pytest.param(delivered(head=None), intent("operator-retry", "pass-with-nits", head=None), False, id="operator-retry-of-nits-head-missing"),
    # pass-with-nits may reuse the head with a new verification
    pytest.param(delivered(), intent("review-nits", "pass-with-nits"), True, id="nits-same-head"),
    # an explicit authoring dispatch has no rejected code: only the verification attachment must be new
    pytest.param(delivered(), intent("explicit-dispatch", head=None), True, id="explicit-dispatch-baseline-head-unknown"),
    pytest.param(delivered(), intent("explicit-dispatch", head="head-old"), True, id="explicit-dispatch-baseline-head-same"),
    pytest.param(delivered(attachment="attachment-old"), intent("explicit-dispatch"), False, id="explicit-dispatch-old-verification"),
    pytest.param(delivered(head=None), intent("explicit-dispatch"), False, id="explicit-dispatch-without-head"),
    pytest.param(delivered(attachment=""), intent("explicit-dispatch"), False, id="explicit-dispatch-without-verification"),
    # which item states can hold the delivery
    pytest.param(delivered(status=WorkItemStatus.IN_PROGRESS), intent("explicit-dispatch"), False, id="still-in-progress"),
    pytest.param(delivered(status=WorkItemStatus.BLOCKED, phase=TaskPhase.AUTHORING, worker_handoff=intent("explicit-dispatch")),
                 intent("explicit-dispatch"), True, id="blocked-in-the-same-handoff"),
    pytest.param(delivered(status=WorkItemStatus.BLOCKED, phase=TaskPhase.AUTHORING,
                           worker_handoff=replace(intent("explicit-dispatch"), generation="other")),
                 intent("explicit-dispatch"), False, id="blocked-in-another-handoff"),
    pytest.param(delivered(status=WorkItemStatus.BLOCKED, phase=TaskPhase.AUTHORING, worker_handoff=intent("explicit-dispatch"),
                           decision_required={"reason_code": "x"}), intent("explicit-dispatch"), False, id="blocked-awaiting-a-decision"),
    pytest.param(delivered(status=WorkItemStatus.BLOCKED, phase=TaskPhase.REVIEW, worker_handoff=intent("explicit-dispatch")),
                 intent("explicit-dispatch"), False, id="blocked-in-review"),
])
def test_a_handoff_has_a_new_delivery_only_when_the_control_facts_prove_it(item, handoff, expected):
    assert handoff_has_new_delivery(item, handoff) is expected


# ---- is the delivery that was sealed still the one the item shows? ------------------------------------------------------

def sealed(**fields):
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA, handoff_generation="g1", worker="alice", agent_id="agent-alice", run_id="run-1",
        pr_url=PR, pr_head_sha="head-new", verification_sha256="sha-new", verification_attachment_id="attachment-new",
        verification_comment_id="comment-new", verification_task_id="run-1", verification_created_at="2026-08-03T00:00:00Z", **fields)


def shown(**fields):
    base = delivered(head="head-new", attachment="attachment-new", worker_handoff=intent("explicit-dispatch"))
    ref = {"attachment_id": "attachment-new", "comment_id": "comment-new", "sha256": "sha-new"}
    return replace(base, **{"verification_ref": ref, **fields})


@pytest.mark.parametrize(("change", "expected"), [
    pytest.param({}, True, id="same-delivery"),
    pytest.param({"artifacts": {"pr_url": PR, "head_sha": "head-new"}}, True, id="artifacts-spelled-the-same"),
    pytest.param({"artifacts": {"pr_url": "https://example.test/pr/2", "head_sha": "head-new"}}, False, id="other-pull-request"),
    pytest.param({"artifacts": {"pr_url": PR, "head_sha": "head-newer"}}, False, id="head-moved"),
    pytest.param({"verification_ref": {"attachment_id": "attachment-newer", "comment_id": "comment-new", "sha256": "sha-new"}}, False,
                 id="other-attachment"),
    pytest.param({"verification_ref": {"attachment_id": "attachment-new", "comment_id": "comment-newer", "sha256": "sha-new"}}, False,
                 id="other-comment"),
    pytest.param({"verification_ref": {"attachment_id": "attachment-new", "comment_id": "comment-new", "sha256": "sha-newer"}}, False,
                 id="other-digest"),
    pytest.param({"worker_handoff": replace(intent("explicit-dispatch"), generation="g2")}, False, id="another-handoff-generation"),
    pytest.param({"worker_handoff": replace(intent("explicit-dispatch"), terminal_observed_at="2026-08-03T00:00:00+00:00")}, True,
                 id="observing-the-end-of-the-run-does-not-change-the-handoff"),
    pytest.param({"worker_handoff": None}, False, id="handoff-already-retired"),
    pytest.param({"verification_ref": {"attachment_id": "attachment-old", "comment_id": "comment-new", "sha256": "sha-new"}}, False,
                 id="not-new-relative-to-the-baseline"),
])
def test_the_sealed_delivery_is_still_the_one_the_item_shows(change, expected):
    assert control_matches_handoff_candidate(shown(**change), intent("explicit-dispatch"), sealed()) is expected


@pytest.mark.parametrize(("item", "handoff", "expected"), [
    pytest.param(delivered(), replace(intent("review"), baseline_pr_head_sha=None), True, id="legacy-review-gate-without-a-baseline-head"),
    pytest.param(delivered(), intent("review", "pass-with-nits", head=None), True, id="legacy-nits-gate-without-a-baseline-head"),
])
def test_handoffs_from_before_the_baseline_head_existed_are_judged_by_their_verdict(item, handoff, expected):
    assert handoff_has_new_delivery(item, handoff) is expected


# ---- environment failure (S8): one automatic retry of the same Worker, then a person with the next Worker's command ---------

@pytest.mark.parametrize(("consumed", "limit", "expected"), [
    (0, 1, EnvironmentFailure.RETRY),        # the first report is retried
    (1, 1, EnvironmentFailure.ESCALATE),     # the second report goes to a person
    (0, 0, EnvironmentFailure.ESCALATE),     # retry.environment: 0 turns the automatic retry off
    (1, 2, EnvironmentFailure.RETRY),
    (5, 2, EnvironmentFailure.ESCALATE),
])
def test_s8_an_environment_failure_is_retried_only_while_the_budget_lasts(consumed, limit, expected):
    assert decide_environment_failure(EnvironmentFailureObservation(consumed=consumed, limit=limit)) is expected


@pytest.mark.parametrize(("current", "workers", "command"), [
    ("alice", ["alice", "bob", "carol"], "omac node retry m.yaml n --worker bob"),
    ("carol", ["alice", "bob", "carol"], "omac node retry m.yaml n --worker alice"),      # wraps around
    ("dave", ["alice", "bob"], "omac node retry m.yaml n --worker alice"),               # not in the pool: the first one
    ("alice", ["alice"], "omac node retry m.yaml n"),                                    # nobody else: the operator chooses
    ("alice", [], "omac node retry m.yaml n"),
])
def test_s8_the_escalation_names_the_next_worker_in_a_command_that_can_be_copied(current, workers, command):
    assert next_worker_retry_command("m.yaml", "n", current, workers) == command


# ---- a verdict that judged an earlier delivery does not demand a new head --------------------------------------------------

@pytest.mark.parametrize(("item", "handoff", "expected"), [
    pytest.param(delivered(), replace(intent("operator-retry", "reject"), source_review_superseded=True), True,
                 id="the-head-was-never-reviewed-so-an-unchanged-head-is-fine"),
    pytest.param(delivered(attachment="attachment-old"), replace(intent("operator-retry", "reject"), source_review_superseded=True),
                 False, id="but-the-submission-must-still-be-fresh"),
    pytest.param(delivered(head=None), replace(intent("operator-retry", "reject"), source_review_superseded=True), False,
                 id="and-the-head-must-be-known"),
    pytest.param(delivered(), intent("operator-retry", "reject"), False, id="a-live-reject-still-needs-a-moved-head"),
])
def test_a_superseded_verdict_only_demands_a_fresh_submission(item, handoff, expected):
    assert handoff_has_new_delivery(item, handoff) is expected


@pytest.mark.parametrize(("handoff", "head", "expected"), [
    pytest.param(intent("review", "reject"), "head-old", False, id="reject-resubmitted-unmoved-head"),
    pytest.param(intent("review", "reject"), "head-new", True, id="reject-moved-head"),
    pytest.param(intent("operator-retry"), "head-old", False, id="operator-retry-unmoved-head"),
    pytest.param(intent("review-nits", "pass-with-nits"), "head-old", True, id="nits-may-reuse-head"),
    pytest.param(intent("explicit-dispatch", head=None), "head-old", True, id="no-rejected-code-obligation"),
    pytest.param(replace(intent("explicit-dispatch"), source_review_superseded=True), "head-old", True,
                 id="verdict-superseded-by-a-later-delivery"),
])
def test_the_head_alone_decides_whether_a_submission_can_count_as_delivery(handoff, head, expected):
    assert head_proves_new_delivery(handoff, head) is expected

"""When may a persisted Reviewer decision be cleared by a Run? Item, manifest and Run facts in, a verdict out. No platform."""
from dataclasses import replace

import pytest

from omac.core.manifest import _dump_contract
from omac.core.taskmeta import TaskPhase, WorkerHandoffIntent
from omac.core.workitem import AgentRunObservation, WorkItemControlProjection, WorkItemPayload
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.decide_recovery import (
    classify_reviewer_recovery_decision as classify, delayed_reviewer_recovery_marker_error as marker_error,
    plan_delayed_reviewer_recovery, validate_structured_recovery_payloads,
)
from omac.pipeline.decide_runs import is_active_formal_run
from _support.recoverykit import AGENT, CUTOFF, KEY, NODE_CONTRACT, PATH, REVIEWER, delivered_item, manifest


def decision_with(**changes):
    def edit(item):
        decision = dict(item.decision_required)
        for name, value in changes.items():
            decision.pop(name, None) if value is None else decision.__setitem__(name, value)
        return replace(item, decision_required=decision)
    return edit


def baseline_with(**changes):
    return lambda item: replace(item, reviewer_run_baseline=replace(item.reviewer_run_baseline, **changes))


def stale_everywhere(item):
    return replace(baseline_with(subject_digest="stale-subject")(item), review_subject_digest="stale-subject")


def other_cutoff(item):
    return replace(item, delivery_identity=replace(item.delivery_identity, verification_created_at="2026-08-03T00:00:02Z"))


NOT_THIS_REVIEW = "does not identify this review"


@pytest.mark.parametrize(("edit", "error"), [
    pytest.param(lambda item: item, None, id="canonical-marker"),
    pytest.param(decision_with(reason_code="reviewer-run-dispatch-unresolved"), None, id="dispatch-unresolved-marker"),
    pytest.param(decision_with(next_action=f"omac node retry {PATH} {KEY}"), None, id="legacy-next-action-without-stage"),
    pytest.param(lambda item: replace(item, decision_required=["not-an-object"]), NOT_THIS_REVIEW, id="malformed-decision"),
    pytest.param(decision_with(schema="omac.decision-required/v2"), NOT_THIS_REVIEW, id="wrong-schema"),
    pytest.param(decision_with(reason_code="operator-product-decision"), NOT_THIS_REVIEW, id="unknown-reason"),
    pytest.param(decision_with(kind="acceptance"), NOT_THIS_REVIEW, id="wrong-kind"),
    pytest.param(decision_with(phase="authoring"), NOT_THIS_REVIEW, id="wrong-phase"),
    pytest.param(decision_with(node_id="other"), NOT_THIS_REVIEW, id="wrong-node"),
    pytest.param(decision_with(resume_issue_id="other-issue"), NOT_THIS_REVIEW, id="wrong-resume-issue"),
    pytest.param(decision_with(gate="human-confirmation"), NOT_THIS_REVIEW, id="wrong-gate"),
    pytest.param(decision_with(gate=None), NOT_THIS_REVIEW, id="missing-gate"),
    pytest.param(decision_with(failure_class="operator-product-decision"), NOT_THIS_REVIEW, id="wrong-failure-class"),
    pytest.param(decision_with(failure_class=None), NOT_THIS_REVIEW, id="missing-failure-class"),
    pytest.param(decision_with(next_action="wait for product owner"), NOT_THIS_REVIEW, id="wrong-next-action"),
    pytest.param(decision_with(next_action=None), NOT_THIS_REVIEW, id="missing-next-action"),
    pytest.param(decision_with(operator_decision=True), NOT_THIS_REVIEW, id="operator-decision-extra"),
    pytest.param(lambda item: replace(item, kind=item.kind.__class__("acceptance")), NOT_THIS_REVIEW, id="not-a-develop-item"),
    pytest.param(baseline_with(subject_digest="stale-subject"), "review subject or reviewer identity", id="wrong-subject"),
    pytest.param(baseline_with(target_agent_id="agent-other"), "review subject or reviewer identity", id="wrong-reviewer-agent"),
    pytest.param(baseline_with(generation=""), "baseline is incomplete", id="baseline-without-generation"),
    pytest.param(lambda item: replace(item, reviewer_run_baseline=None), "baseline is incomplete", id="no-baseline"),
    pytest.param(stale_everywhere, "authoritative review subject does not match", id="stale-authoritative-subject"),
    pytest.param(lambda item: replace(item, contract={"schema": "wrong-contract/v1"}), "contracts do not match",
                 id="contract-mismatch"),
    pytest.param(other_cutoff, "delivery identity does not match", id="delivery-cutoff-mismatch"),
    pytest.param(lambda item: replace(item, delivery_identity=None), "controller-sealed delivery identity", id="no-sealed-delivery"),
])
def test_a_provisional_reviewer_decision_is_cleared_only_when_every_binding_holds(edit, error):
    item = edit(delivered_item())

    found = marker_error(manifest(), PATH, KEY, item, REVIEWER, AGENT, require_target=False)

    if error is None:
        assert found is None
    else:
        assert found is not None and error in found


def test_the_target_run_is_required_when_a_verdict_is_collected_not_when_a_run_is_restored():
    item = delivered_item()
    assert marker_error(manifest(), PATH, KEY, item, REVIEWER, AGENT, require_target=False) is None
    assert "no target Run" in marker_error(manifest(), PATH, KEY, item, REVIEWER, AGENT, require_target=True)


def test_the_explicit_retry_decisions_are_classified_apart_from_the_provisional_one():
    item = delivered_item()
    assert classify(PATH, KEY, item) == "canonical-baseline-unavailable"
    assert classify(PATH, KEY, replace(item, decision_required={})) == "absent"
    explicit = decision_with(
        reason_code="nonretryable-runtime-failure", failure_class="nonretryable-agent-run", run_id="run-1")(item)
    assert classify(PATH, KEY, explicit) == "explicit-review-retry"
    assert marker_error(manifest(), PATH, KEY, explicit, REVIEWER, AGENT, require_target=False) is not None
    assert classify(PATH, KEY, decision_with(run_id="")(explicit)) == "other"


def run(rid="run-bob", status="running", created="2026-08-03T00:00:01Z", trigger="issue_assignment", agent=AGENT):
    return AgentRunObservation(id=rid, kind="direct", status=status, agent_id=agent, created_at=created, trigger_kind=trigger)


def active(runs, **baseline):
    return is_active_formal_run(runs, AGENT, cutoff_created_at=CUTOFF, **baseline)


@pytest.mark.parametrize(("runs", "expected"), [
    pytest.param([run()], True, id="assignment-dispatch"),
    pytest.param([run(trigger="rerun")], True, id="rerun-dispatch"),
    pytest.param([run(trigger="comment")], False, id="comment-run"),
    pytest.param([run(trigger="manual")], False, id="manual-run"),
    pytest.param([run(trigger=None)], False, id="missing-trigger"),
    pytest.param([run(agent="foreign-agent")], False, id="other-agents-run"),
    pytest.param([run(created="2026-08-02T23:59:59Z")], False, id="created-before-the-cutoff"),
    pytest.param([run(created=CUTOFF)], False, id="created-at-the-cutoff"),
    pytest.param([run(), run("run-bob-2", created="2026-08-03T00:00:02Z")], False, id="two-post-baseline-runs-are-ambiguous"),
    pytest.param([run(status="failed")], False, id="terminal-failure"),
    pytest.param([run(status="completed")], False, id="terminal-success"),
    pytest.param([], False, id="no-run-yet"),
])
def test_only_a_unique_formal_active_run_keeps_a_blocked_stage_alive(runs, expected):
    assert active(runs) is expected


def test_a_target_run_pins_which_run_counts():
    assert active([run("run-bob")], target_run_id="run-bob") is True
    assert active([run("run-bob")], target_run_id="run-missing") is False
    unrelated_newer = run("run-new", status="completed", created="2026-08-03T00:00:02Z")
    assert active([unrelated_newer, run("run-bob")], target_run_id="run-bob") is False  # not a retry of the target
    retry = replace(unrelated_newer, retry_of_run_id="run-bob", status="running")
    assert active([retry, run("run-bob", status="failed")], target_run_id="run-bob") is True


# ---- `omac node retry --stage review` binds a Run that became visible late --------------------------------------------

SUBMITTED = dict(review_verdict="reject", review_report={"blockers": ["repair the rejected delivery"]},
                 review_report_ref={"attachment_id": "report-1", "sha256": "b" * 64})


def submitted(**fields):
    return replace(delivered_item(), **SUBMITTED, **fields)


def unresolved_dispatch(item):
    """A continuation dispatch whose Run outcome was never proven: no verdict, the second attempt, no target Run."""
    decision = {**item.decision_required, "reason_code": "reviewer-run-dispatch-unresolved"}
    return replace(item, review_verdict=None, review_report=None, review_report_ref=None, decision_required=decision,
                   reviewer_run_baseline=replace(item.reviewer_run_baseline, attempt=2))


def plan(item, runs=(), *, stable=True, node_manifest=None):
    def unexpected():
        raise AssertionError("an ordinary retry must not look at the Runs")

    return plan_delayed_reviewer_recovery(
        node_manifest or manifest(), PATH, KEY, item, REVIEWER, agent_id_of=lambda name: AGENT,
        list_runs=(lambda: list(runs)) if runs is not None else unexpected, stable_run_identity=stable)


def drifted_delivery(item):
    """The pull request head moved after the review subject was bound."""
    return replace(item, artifacts={**item.artifacts, "head_sha": "h2"}, delivery_identity=replace(item.delivery_identity, pr_head_sha="h2"))


def drifted_verification(item):
    return replace(item, verification={"coverage": 99}, delivery_identity=replace(item.delivery_identity, verification_sha256="s2"))


def drifted_contract(item):
    return replace(item, contract=_dump_contract(replace(NODE_CONTRACT, objective="changed contract")))


@pytest.mark.parametrize("trigger", ["issue_assignment", "rerun"])
@pytest.mark.parametrize("status", ["completed", "running"])
def test_a_unique_formal_run_after_the_cutoff_is_adopted_for_a_submitted_review(trigger, status):
    found = plan(submitted(), [run("run-late", status=status, trigger=trigger)])

    assert (found.action, found.target_run_id) == ("adopt", "run-late")


@pytest.mark.parametrize(("runs", "detail"), [
    pytest.param([], "no uniquely observable target Run", id="no-run"),
    pytest.param([run("run-1"), run("run-2", created="2026-08-03T00:00:02Z")], "", id="ambiguous"),
    pytest.param([run(agent="agent-foreign")], "", id="another-agents-run"),
    pytest.param([run(created="2026-08-03T00:00:00Z")], "", id="not-after-the-cutoff"),
    pytest.param([run(created=None)], "no usable creation time", id="no-creation-time"),
    pytest.param([run(trigger="issue_assignment"), run("run-2", created="2026-08-03T00:00:02Z", trigger="issue_assignment")], "",
                 id="two-assignment-runs"),
    pytest.param([run(trigger="comment")], "not a formal", id="comment-run"),
    pytest.param([run(trigger="manual")], "not a formal", id="manual-run"),
    pytest.param([run(trigger=None)], "not a formal", id="no-trigger"),
])
def test_a_submitted_review_is_never_replayed_without_a_unique_formal_run(runs, detail):
    found = plan(submitted(), runs)

    assert found.action == "refuse" and detail in found.detail


@pytest.mark.parametrize(("edit", "detail"), [
    pytest.param(baseline_with(subject_digest="different-subject"), "review subject or reviewer identity", id="subject-mismatch"),
    pytest.param(other_cutoff, "delivery identity does not match", id="identity-mismatch"),
    pytest.param(drifted_delivery, "authoritative review subject", id="pr-head-drift"),
    pytest.param(drifted_verification, "authoritative review subject", id="verification-drift"),
    pytest.param(drifted_contract, "contracts do not match", id="contract-drift"),
])
def test_a_submitted_review_is_not_adopted_when_the_delivery_it_judged_changed(edit, detail):
    found = plan(edit(submitted()), [run()])

    assert found.action == "refuse" and detail in found.detail


def test_nothing_is_adopted_when_the_runtime_cannot_identify_a_direct_run():
    assert plan(submitted(), [run()], stable=False).detail == "stable direct Run identity is unavailable"


UNKNOWN_DECISIONS = {
    "missing-reason-code": lambda d: {k: v for k, v in d.items() if k != "reason_code"},
    "tampered-reason-code": lambda d: {**d, "reason_code": "reviewer-run-baseline-unavailable-tampered"},
    "not-an-object": lambda d: ["reviewer-run-baseline-unavailable"],
    "unknown-existing-decision": lambda d: {"schema": "omac.decision-required/v1", "reason_code": "guard-budget-exhausted"},
}


@pytest.mark.parametrize("name", UNKNOWN_DECISIONS)
@pytest.mark.parametrize("shape", [submitted, lambda **f: unresolved_dispatch(submitted(**f))], ids=["submitted", "dispatch-unresolved"])
def test_a_decision_that_is_not_the_canonical_marker_is_kept_as_it_is(name, shape):
    item = shape()
    item = replace(item, decision_required=UNKNOWN_DECISIONS[name](item.decision_required))

    found = plan(item, None)       # classifying the decision comes before any look at evidence or Runs

    assert found.action == "refuse" and "does not identify this review" in found.detail


def test_the_decision_is_classified_before_the_submitted_evidence_is_judged():
    incomplete = replace(submitted(), review_report=None, review_report_ref=None,
                         decision_required={"schema": "omac.decision-required/v1", "reason_code": "guard-budget-exhausted"})

    assert plan(incomplete, None).action == "refuse"


@pytest.mark.parametrize("item", [
    pytest.param(replace(submitted(), decision_required={}, reviewer_run_baseline=baseline_with(target_run_id="old-run")(submitted())
                         .reviewer_run_baseline), id="completed-review-without-a-decision"),
    pytest.param(replace(submitted(), review_report=None, review_report_ref=None), id="no-structured-report"),
    pytest.param(replace(submitted(), decision_required=decision_with(
        reason_code="nonretryable-runtime-failure", failure_class="nonretryable-agent-run", run_id="run-1")(delivered_item()).decision_required),
                 id="explicit-reviewer-retry-decision"),
])
def test_every_other_review_retry_is_the_ordinary_stage_reset_that_looks_at_no_run(item):
    assert plan(item, None).action == "ordinary"


@pytest.mark.parametrize("trigger", ["issue_assignment", "rerun"])
def test_an_unresolved_continuation_adopts_the_run_that_became_visible_late(trigger):
    found = plan(unresolved_dispatch(submitted()), [run("run-continuation", trigger=trigger, created="2026-08-03T00:10:00Z")])

    assert (found.action, found.target_run_id) == ("adopt", "run-continuation")


def test_an_unresolved_continuation_without_any_run_is_reset_so_it_is_dispatched_again():
    assert plan(unresolved_dispatch(submitted()), []).action == "ordinary"


# ---- structured evidence that recovery relies on -------------------------------------------------------------------

REF = {"attachment_id": "att-1", "sha256": "a" * 64}


@pytest.mark.parametrize(("item_fields", "requested", "fails"), [
    pytest.param(dict(contract_ref=REF, contract={"objective": "x"}), {WorkItemPayload.CONTRACT}, False, id="parsed-mapping"),
    pytest.param(dict(contract_ref=REF, contract=None), {WorkItemPayload.CONTRACT}, True, id="reference-did-not-parse"),
    pytest.param(dict(contract_ref=REF, contract={}), {WorkItemPayload.CONTRACT}, True, id="empty-mapping"),
    pytest.param(dict(contract_ref=REF, contract=["not", "a", "mapping"]), {WorkItemPayload.CONTRACT}, True, id="not-a-mapping"),
    pytest.param(dict(contract_ref=REF, contract=None), {WorkItemPayload.REVIEW_REPORT}, False, id="payload-not-requested"),
    pytest.param(dict(contract_ref=None, contract=None), {WorkItemPayload.CONTRACT}, False, id="no-reference"),
    pytest.param(dict(review_ledger_ref=REF, review_ledger=None), {WorkItemPayload.REVIEW_LEDGER}, True, id="ledger-did-not-parse"),
])
def test_recovery_fails_closed_when_referenced_evidence_did_not_parse(item_fields, requested, fails):
    item = replace(delivered_item(), **item_fields)
    check = lambda: validate_structured_recovery_payloads(WorkItemControlProjection(item), frozenset(requested))
    if fails:
        with pytest.raises(PlatformError, match="did not parse as a non-empty"):
            check()
    else:
        check()


def cycle(index, **fields):
    return {"round": index, "open_count": 1, "open_blocker_ids": ["BLK-core"], "reported_blocker_ids": ["BLK-core"], **fields}


def blocker(**fields):
    return {"blocker_id": "BLK-core", "root_cause_key": "core-acceptance", "obligation_id": "dimension:structure",
            "status": "open", "classification": "unchanged", "first_seen_round": 1, "last_seen_round": 3, "seen_count": 2, **fields}


INVALID_LEDGERS = {
    "wrong-schema": {"schema": "future.review-ledger/v9", "cycles": [{"round": 3, "open_count": 1}], "blockers": []},
    "cycle-id-underreport": {
        "schema": "omac.review-ledger/v1",
        "cycles": [cycle(1), cycle(2, open_blocker_ids=[], reported_blocker_ids=[]), cycle(3)], "blockers": [blocker()]},
    "open-fixed": {
        "schema": "omac.review-ledger/v1",
        "cycles": [cycle(i, new_count=int(i == 1), fixed_count=0, regressed_count=0, unchanged_count=int(i != 1),
                         prior_open_blocker_ids=[] if i == 1 else ["BLK-core"]) for i in range(1, 4)],
        "blockers": [blocker(classification="fixed", seen_count=3)]},
}


@pytest.mark.parametrize("name", INVALID_LEDGERS)
def test_a_restored_worker_stage_rejects_an_invalid_deferred_review_ledger(name):
    intent = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="alice", gate="review",
        source_review_subject_digest="subject", source_review_round=3, target_review_bounce=3, generation="worker-1",
        target_agent_id="agent-alice", target_worker_bounce=0)
    item = replace(delivered_item(), phase=TaskPhase.AUTHORING, worker_handoff=intent, review_ledger=INVALID_LEDGERS[name],
                   review_ledger_ref=REF)

    with pytest.raises(PlatformError, match="review ledger"):
        loop._resolve_handoff_review_convergence(item, intent, KEY)

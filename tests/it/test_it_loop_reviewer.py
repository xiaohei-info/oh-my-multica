"""The Reviewer Run on a real Multica: provider failures, runs that end without a verdict, and recovery after a crash.

The Worker is a scripted agent that submits a valid delivery; the Reviewer's Runs follow the scenario each test sets.
"""
from dataclasses import replace

import pytest

from _support.loopkit import HANG, contract
from _support.rework import delayed_verdict
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError, ValidationError
from omac.pipeline import loop

HTTP_503 = "hermes provider error: ⚠️  API call failed (attempt 3/8): InternalServerError [HTTP 503]"
IDLE = []   # a Run that ends without submitting a verdict








def test_provider_failure_of_the_reviewer_reruns_without_a_review_bounce(kit):
    manifest, path, item = kit.in_review([{"fail": HTTP_503}], *HANG)
    kit.wait_runs_ended(item.id)
    before = kit.store.get_work_item(item.id)
    wakes = kit.record(kit.runtime, "wake")

    assert kit.collect(manifest, path) == {}
    assert kit.collect(manifest, path) == {}

    retried = kit.store.get_work_item(item.id)
    assert [w[1][2] for w in wakes] == ["reviewer"]
    assert retried.phase is TaskPhase.REVIEW and retried.bounces.review == before.bounces.review == 0
    assert retried.verification == before.verification and manifest.nodes["a"].status == "in_review"
    assert len(kit.reviewer_runs(item.id)) == 2


def test_a_run_that_ended_without_a_verdict_is_restarted_once_from_runtime_facts(kit):
    manifest, path, item = kit.in_review(IDLE, *HANG)
    kit.wait_runs_ended(item.id)
    wakes = kit.record(kit.runtime, "wake")

    assert kit.collect(manifest, path) == {}
    persisted = load_manifest(path)
    assert kit.collect(persisted, path) == {}

    recovered = kit.store.get_work_item(item.id)
    assert len(wakes) == 1 and len(kit.reviewer_runs(item.id)) == 2
    assert recovered.phase is TaskPhase.REVIEW and recovered.status is WorkItemStatus.IN_REVIEW
    assert recovered.review_verdict is None and recovered.bounces.review == 0
    assert recovered.reviewer_run_baseline.attempt == 2 and persisted.nodes["a"].status == "in_review"


def test_a_late_verdict_within_the_grace_window_is_awaited(kit):
    manifest, path, item = kit.in_review(IDLE)
    kit.wait_runs_ended(item.id)
    kit.monkeypatch.setattr(loop, "_HANDOFF_TERMINAL_GRACE_SECONDS", 300)
    kit.forbid(kit.runtime, "wake")

    assert kit.collect(manifest, path) == {}

    current = kit.store.get_work_item(item.id)
    assert current.bounces.review == 0 and current.decision_required is None
    assert manifest.nodes["a"].status == "in_review" and len(kit.reviewer_runs(item.id)) == 1


def test_a_retry_whose_run_stays_invisible_fails_closed(kit):
    manifest, path, item = kit.in_review(IDLE, *HANG)
    kit.wait_runs_ended(item.id)
    seen = {r.id for r in kit.runtime.list_runs(item.id)}
    real_list = kit.runtime.list_runs
    kit.monkeypatch.setattr(kit.runtime, "list_runs", lambda item_id: [r for r in real_list(item_id) if r.id in seen])
    wakes = kit.record(kit.runtime, "wake")
    kit.monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)

    failures = kit.collect(manifest, path)
    restarted = load_manifest(path)
    kit.collect(restarted, path)

    recovered = kit.store.get_work_item(item.id)
    assert "a" in failures and len(wakes) == 1
    assert recovered.reviewer_run_baseline.attempt == 2 and recovered.reviewer_run_baseline.target_run_id is None
    assert recovered.status is WorkItemStatus.BLOCKED
    assert recovered.decision_required["reason_code"] == "reviewer-run-dispatch-unresolved"
    assert restarted.nodes["a"].status == "blocked"


def test_a_crash_after_the_retry_intent_fails_closed_on_restart_without_waking(kit):
    manifest, path, item = kit.in_review(IDLE, *HANG)
    kit.wait_runs_ended(item.id)
    kit.forbid(kit.runtime, "wake")
    crash = kit.fault(kit.store, "update_work_item_metadata", when="after", error=RuntimeError("crash after intent"),
                      only=lambda _id, **kw: getattr(kw.get("reviewer_run_baseline"), "attempt", 0) == 2)

    with pytest.raises(RuntimeError, match="crash after intent"):
        kit.collect(manifest, path)

    interrupted = kit.store.get_work_item(item.id)
    assert interrupted.reviewer_run_baseline.attempt == 2 and interrupted.reviewer_run_baseline.target_run_id is None

    crash["armed"] = False
    wakes = kit.record(kit.runtime, "wake")
    failures = kit.collect(load_manifest(path), path)

    recovered = kit.store.get_work_item(item.id)
    assert "a" in failures and wakes == []
    assert recovered.status is WorkItemStatus.BLOCKED
    assert recovered.decision_required["reason_code"] == "reviewer-run-dispatch-unresolved"


DECISION = {"schema": "omac.decision-required/v1", "reason_code": "review-convergence-scope-expanding",
            "kind": "develop", "phase": "review", "gate": "review-convergence", "node_id": "a"}


def prepared(kit, manifest, path, item, **metadata):
    """The prepared-but-undispatched shape: the Reviewer's Run is over, nobody is assigned, work is 'in progress'."""
    kit.runtime.cancel(item.id)
    kit.wait_runs_ended(item.id)
    kit.store.clear_assignment(item.id)
    if metadata:
        kit.store.update_work_item_metadata(item.id, **metadata)
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    manifest.nodes["a"].status = "in_progress"
    kit.save(manifest)


def hide_reviewer_runs(kit):
    """The platform lists no Reviewer Run yet (eventual consistency)."""
    bob, real = kit.store.resolve_agent_id("bob"), kit.runtime.list_runs
    kit.monkeypatch.setattr(kit.runtime, "list_runs", lambda item_id: [r for r in real(item_id) if r.agent_id != bob])


def dispatch(kit, manifest):
    return loop._dispatch_reviewer_for_current_subject(kit.store, kit.runtime, kit.code_host, manifest, "a")


def test_the_initial_reviewer_assignment_does_not_start_a_run(kit):
    assigns = kit.record(kit.store, "assign_work_item")
    manifest, path, item = kit.in_review()
    reviewer_calls = [kw for _, args, kw in assigns if args[2] == "reviewer"]
    assert reviewer_calls == [{"start_run": False}]
    assert len(kit.reviewer_runs(item.id)) == 1, "the wake after the silent assignment starts the one Run"


def test_a_silent_assignment_defers_the_run_until_wake_and_a_second_wake_adds_none(kit):
    """Port contract: after an earlier Run, assign(start_run=False) starts nothing; wake starts one; again is a no-op."""
    kit.agents("idle", names=("alice",))
    item = kit.item("review-a", reviewer="bob", assign=True)   # the Worker's earlier Run, already over
    kit.wait_runs_ended(item.id)
    before = {r.id for r in kit.runtime.list_runs(item.id)}

    kit.store.assign_work_item(item.id, "bob", "reviewer", start_run=False)
    assert {r.id for r in kit.runtime.list_runs(item.id)} == before

    kit.runtime.wake(item.id, "bob", "reviewer")
    woken = kit.wait_for(lambda: kit.reviewer_runs(item.id), what="the woken Reviewer Run")
    assert len(woken) == 1 and woken[0].active

    kit.runtime.wake(item.id, "bob", "reviewer")
    assert [r.id for r in kit.reviewer_runs(item.id)] == [woken[0].id]


def test_resuming_a_reviewer_assigns_without_resuming_the_old_session(kit):
    manifest, path, item = kit.in_review()
    assigns = kit.record(kit.store, "assign_work_item")
    assert loop._resume_reviewer_run(kit.store, kit.runtime, manifest.nodes["a"])
    assert [kw for _, args, kw in assigns if args[2] == "reviewer"] == [{"start_run": False}]


def test_a_reused_issue_is_refreshed_with_the_control_protocol_before_the_reviewer_is_dispatched(kit):
    manifest, path, item = kit.in_review()
    prepared(kit, manifest, path, item, description="stale issue body", reviewer_run_baseline={})
    hide_reviewer_runs(kit)
    kit.monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)

    try:
        dispatch(kit, manifest)
    except loop._ReviewerDispatchUnresolved:
        pass  # the hidden Run cannot be bound; the refresh happened before that

    first_screen = kit.store.get_work_item(item.id).description.split("# ", 1)[0]
    assert "omac work show" in first_screen and "multica issue comment" in first_screen
    assert "omac work submit" in first_screen
    body = kit.store.get_work_item(item.id).description
    assert "Execution role: Independent reviewer" in body or "执行角色: 独立评审者" in body


# ---- a persisted decision always wins over dispatching a Reviewer ------------------------------------------------

def decision_for(item, **extra):
    return {**DECISION, "resume_issue_id": item.id, **extra}


def test_a_pending_review_with_an_existing_decision_fails_closed(kit):
    manifest, path, item = kit.in_review()
    decision = decision_for(item, reason_code="review-convergence-stalled", rounds=3)
    prepared(kit, manifest, path, item, decision_required=decision)
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    failures = kit.collect(manifest, path)

    blocked = kit.store.get_work_item(item.id)
    assert "a" in failures and manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED and blocked.decision_required == decision


def test_direct_dispatch_cannot_bypass_a_persisted_decision(kit):
    manifest, path, item = kit.in_review()
    decision = decision_for(item, reason_code="review-convergence-stalled", rounds=3)
    prepared(kit, manifest, path, item, decision_required=decision)
    assigns = kit.record(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    assert dispatch(kit, manifest) is False

    blocked = kit.store.get_work_item(item.id)
    assert manifest.nodes["a"].status == "blocked" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision and assigns == []


@pytest.mark.parametrize("entry", ["direct", "collect"])
def test_a_decision_that_appears_during_the_handoff_blocks_before_assignment(kit, entry):
    manifest, path, item = kit.in_review()
    prepared(kit, manifest, path, item)
    decision = decision_for(item)
    refresh = loop._refresh_develop_issue_body

    def inject(*args, **kwargs):
        refresh(*args, **kwargs)
        kit.store.update_work_item_metadata(item.id, decision_required=decision)

    kit.monkeypatch.setattr(loop, "_refresh_develop_issue_body", inject)
    assigns = kit.record(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    if entry == "direct":
        assert dispatch(kit, manifest) is False
    else:
        assert "a" in kit.collect(manifest, path)

    blocked = kit.store.get_work_item(item.id)
    assert manifest.nodes["a"].status == "blocked" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision and [a for a in assigns if a[1][2] == "reviewer"] == []


def test_a_decision_seen_after_the_terminal_run_stops_the_no_submit_retry(kit):
    manifest, path, item = kit.in_review(IDLE, *HANG)
    kit.wait_runs_ended(item.id)
    attempt = kit.store.get_work_item(item.id).reviewer_run_baseline.attempt
    decision = decision_for(item)
    grace = loop._reviewer_no_submit_grace_state

    def inject_after_terminal(*args, **kwargs):
        state = grace(*args, **kwargs)
        if state == "elapsed":
            kit.store.update_work_item_metadata(item.id, decision_required=decision)
        return state

    kit.monkeypatch.setattr(loop, "_reviewer_no_submit_grace_state", inject_after_terminal)
    assigns = kit.record(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    failures = kit.collect(manifest, path)

    blocked = kit.store.get_work_item(item.id)
    assert "a" in failures and manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED and blocked.decision_required == decision
    assert blocked.reviewer_run_baseline.attempt == attempt and assigns == []


# ---- initial dispatch: a crash between assignment and wake never reruns --------------------------------------------

def test_a_new_baseline_is_dispatched_once_then_an_existing_one_is_only_observed(kit):
    manifest, path, item = kit.in_review()
    prepared(kit, manifest, path, item, reviewer="bob", reviewer_run_baseline={})
    hide_reviewer_runs(kit)
    kit.monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)
    calls = []
    kit.monkeypatch.setattr(kit.runtime, "dispatch_reviewer",
                            lambda _store, item_id, reviewer, **kw: calls.append((item_id, reviewer)) or True)

    assert dispatch(kit, manifest) is True
    assert calls == [(item.id, "bob")]
    current = kit.store.get_work_item(item.id)
    assert current.decision_required is None and current.reviewer_run_baseline.target_run_id is None

    kit.forbid(kit.runtime, "dispatch_reviewer")
    with pytest.raises(loop._ReviewerDispatchUnresolved, match="no uniquely observable target Run"):
        dispatch(kit, manifest)


def crash_before_wake(kit):
    manifest, path, item = kit.in_review()
    prepared(kit, manifest, path, item, reviewer_run_baseline={})
    kit.monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)
    crash = kit.fault(kit.runtime, "wake", when="before", error=RuntimeError("crash after suppressed assignment"))
    with pytest.raises(RuntimeError, match="suppressed assignment"):
        dispatch(kit, manifest)
    crash["armed"] = False
    return manifest, path, item


def test_a_crash_after_the_silent_assignment_fails_closed_and_never_reruns(kit):
    manifest, path, item = crash_before_wake(kit)

    interrupted = kit.store.get_work_item(item.id)
    assert interrupted.status is WorkItemStatus.IN_REVIEW and interrupted.phase is TaskPhase.REVIEW
    assert interrupted.reviewer == "bob" and interrupted.reviewer_run_baseline.target_run_id is None
    assert not kit.runtime.is_active(item.id)

    kit.forbid(kit.runtime, "wake")
    with pytest.raises(loop._ReviewerDispatchUnresolved, match="no uniquely observable target Run"):
        dispatch(kit, manifest)


def test_a_formal_run_that_appears_after_the_crash_is_bound_not_rerun(kit):
    manifest, path, item = crash_before_wake(kit)
    kit.runtime.wake(item.id, "bob", "reviewer")   # what the platform did late: a formal dispatch Run
    run = kit.wait_for(lambda: kit.reviewer_runs(item.id), what="the late Run")[0]
    kit.forbid(kit.runtime, "wake")

    assert dispatch(kit, manifest) is False

    assert kit.store.get_work_item(item.id).reviewer_run_baseline.target_run_id == run.id


# ---- budgets ---------------------------------------------------------------------------------------------------------

def test_reviewer_runs_without_a_verdict_are_bounded_and_cost_no_business_bounce(kit):
    manifest, path, item = kit.in_review(IDLE, IDLE)
    kit.wait_runs_ended(item.id)
    kit.tick_until(manifest, path, lambda: len(kit.reviewer_runs(item.id)) == 2, max_parallel=1,
                   what="the second Reviewer Run")   # the first no-submit costs one more Run
    kit.wait_runs_ended(item.id)
    kit.forbid(kit.runtime, "wake")

    result = kit.tick(manifest, path, max_parallel=1)

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED and blocked.phase is TaskPhase.REVIEW
    assert blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == "reviewer-run-no-submit-retry-exhausted"


def test_a_larger_no_submit_budget_keeps_dispatching(kit):
    manifest, path, item = kit.in_review(IDLE, IDLE, *HANG)
    config = {"retry": {"no_submit_runs": 3}}
    for expected_runs in (2, 3):
        kit.wait_runs_ended(item.id)
        kit.tick_until(manifest, path, lambda: len(kit.reviewer_runs(item.id)) == expected_runs, max_parallel=1,
                       config=config, what=f"Reviewer Run {expected_runs}")

    recovered = kit.store.get_work_item(item.id)
    assert manifest.nodes["a"].status == "in_review" and recovered.status is WorkItemStatus.IN_REVIEW
    assert not recovered.decision_required and recovered.reviewer_run_baseline.attempt == 3
    assert len(kit.reviewer_runs(item.id)) == 3


@pytest.mark.parametrize("bad", [0, -1, "6", 2.5])
def test_an_invalid_no_submit_budget_fails_closed(kit, bad):
    manifest = kit.manifest([kit.node("a", reviewer="bob", contract=contract())])
    with pytest.raises(ValidationError, match="no_submit_runs"):
        kit.tick(manifest, kit.save(manifest), config={"retry": {"no_submit_runs": bad}})


# ---- a verdict that arrived while a recovery decision was recorded ------------------------------------------------

def hold(kit, manifest, path, item, **metadata):
    """Re-arrange a blocked node holding ``metadata`` (decision, baseline) around the preserved verdict."""
    kit.store.update_work_item_metadata(item.id, **metadata)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    manifest.nodes["a"].status = "in_review"
    kit.save(manifest)


def guard(kit):
    return (kit.fault(kit.store, "assign_work_item", when="before", error=AssertionError("must not assign")),
            kit.fault(kit.runtime, "wake", when="before", error=AssertionError("must not wake")))


@pytest.mark.parametrize("verdict", ["pass", "reject"])
def test_an_unknown_decision_clear_resumes_the_same_verdict_after_restart(kit, verdict):
    manifest, path, item, run = delayed_verdict(kit, verdict)
    report, runs_before = item.review_report, [r.id for r in kit.runtime.list_runs(item.id)]
    clear = kit.fault(kit.store, "update_work_item_metadata", when="after",
                      error=PlatformError("decision clear response unknown"),
                      only=lambda _id, **kw: kw.get("decision_required") == {})
    no_assign, no_wake = guard(kit)

    with pytest.raises(PlatformError, match="decision clear response unknown"):
        kit.collect(manifest, path)

    interrupted = kit.store.get_work_item(item.id)
    assert not interrupted.decision_required and interrupted.review_verdict == verdict
    assert interrupted.review_report == report and interrupted.reviewer_run_baseline.target_run_id == run.id
    assert [r.id for r in kit.runtime.list_runs(item.id)] == runs_before

    clear["armed"] = no_assign["armed"] = no_wake["armed"] = False
    assigns = kit.record(kit.store, "assign_work_item")
    assert kit.collect(manifest, path) == {}

    recovered = kit.store.get_work_item(item.id)
    assert not recovered.decision_required
    if verdict == "reject":
        assert manifest.nodes["a"].status == "in_progress" and recovered.bounces.review == 1
    else:
        assert manifest.nodes["a"].status in {"merging", "done"}
    assert [a for a in assigns if a[1][2] == "reviewer"] == []


def preserved_cases(kit, verdict, cases):
    """Each case: (id, metadata edit). The node must stay blocked with the decision and verdict untouched."""
    manifest, path, item, _run = delayed_verdict(kit, verdict)
    original = kit.store.get_work_item(item.id)
    no_assign, no_wake = guard(kit)
    metadata = kit.record(kit.store, "update_work_item_metadata")
    for case_id, edit in cases:
        edits = edit(original)
        expected = edits.pop("expected", edits["decision_required"])   # how the platform reads it back
        hold(kit, load_manifest(path), path, item, **edits)
        manifest = load_manifest(path)
        metadata.clear()

        failures = kit.collect(manifest, path)

        blocked = kit.store.get_work_item(item.id)
        assert "a" in failures and manifest.nodes["a"].status == "blocked", case_id
        assert blocked.status is WorkItemStatus.BLOCKED and blocked.decision_required == expected, case_id
        assert blocked.review_verdict == verdict and blocked.review_report == original.review_report, case_id
        assert not [m for m in metadata if "decision_required" in m[2]], f"{case_id}: the decision must not be rewritten"
        hold(kit, manifest, path, item, reviewer_run_baseline=original.reviewer_run_baseline,
             decision_required=original.decision_required)


def test_an_invalid_delayed_reviewer_decision_is_never_cleared_or_consumed(kit):
    def edited(fix):
        def edit(item):
            decision = dict(item.decision_required)
            extra = fix(decision, item)
            return {"decision_required": decision, **extra}
        return edit

    preserved_cases(kit, "pass", [
        ("stale-subject", edited(lambda d, item: {"reviewer_run_baseline": replace(
            item.reviewer_run_baseline, subject_digest="stale-subject")})),
        ("wrong-gate", edited(lambda d, item: d.update(gate="human-confirmation") or {})),
        ("missing-failure-class", edited(lambda d, item: d.pop("failure_class") and {})),
        ("operator-decision-extra", edited(lambda d, item: d.update(operator_decision=True) or {})),
    ])


def test_only_the_exact_canonical_reviewer_recovery_decision_is_consumed(kit):
    def decision(change):
        def edit(item):
            value = dict(item.decision_required)
            return {"decision_required": change(value)}
        return edit

    preserved_cases(kit, "reject", [
        ("missing-reason-code", decision(lambda d: {k: v for k, v in d.items() if k != "reason_code"})),
        ("tampered-reason-code", decision(lambda d: {**d, "reason_code": "reviewer-run-baseline-unavailable-tampered"})),
        ("non-object", lambda item: {
            "decision_required": ["reviewer-run-baseline-unavailable"],
            "expected": {"schema": "omac.invalid-object-metadata/v1", "decoded_type": "list",
                         "value": ["reviewer-run-baseline-unavailable"]}}),
        ("unknown-existing-decision", decision(lambda d: {"schema": "omac.decision-required/v1",
                                                          "reason_code": "guard-budget-exhausted"})),
    ])


def test_an_existing_unknown_decision_wins_over_no_submit_recovery(kit):
    manifest, path, item = kit.in_review(IDLE, *HANG)
    kit.wait_runs_ended(item.id)
    baseline = kit.store.get_work_item(item.id).reviewer_run_baseline
    decision = {"schema": "omac.decision-required/v1", "reason_code": "guard-budget-exhausted"}
    kit.store.update_work_item_metadata(item.id, decision_required=decision)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    assigns = kit.record(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    failures = kit.collect(manifest, path)

    blocked = kit.store.get_work_item(item.id)
    assert "a" in failures and manifest.nodes["a"].status == "blocked" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required == decision and blocked.reviewer_run_baseline == baseline and assigns == []


# ---- an unusable cutoff blocks and stays blocked ------------------------------------------------------------------

def test_an_unusable_persisted_cutoff_stays_blocked_across_collects(kit):
    manifest, path, item = kit.in_review()
    baseline = kit.store.get_work_item(item.id).reviewer_run_baseline
    kit.store.update_work_item_metadata(
        item.id, reviewer_run_baseline=replace(baseline, cutoff_created_at="2026-08-01T01:00:01Z"))  # not the delivery's
    kit.forbid(kit.runtime, "wake")

    for attempt in range(3):
        failures = kit.collect(manifest, path)
        blocked = kit.store.get_work_item(item.id)
        if attempt == 0:
            assert "a" in failures
        assert blocked.status is WorkItemStatus.BLOCKED and manifest.nodes["a"].status == "blocked"
        assert blocked.decision_required["reason_code"] == "reviewer-run-baseline-unavailable"
        manifest = load_manifest(path)

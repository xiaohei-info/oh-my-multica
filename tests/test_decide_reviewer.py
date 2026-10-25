"""Reviewer Run decisions as tables: Run facts, baseline and clock in, an action out. No platform."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from omac.core.taskmeta import (
    DELIVERY_IDENTITY_SCHEMA, REVIEWER_RUN_BASELINE_SCHEMA, DeliveryIdentity, ReviewerRunBaseline,
)
from omac.core.workitem import AgentRunObservation
from omac.pipeline.decide_reviewer import (
    BaselineAction as B, BaselineObservation, DispatchBaselineObservation, NoSubmitAction as N, RunAction as A, RunObservation, decide_baseline,
    decide_dispatch_baseline, decide_no_submit, decide_reviewer_run, no_submit_grace,
)
from omac.pipeline.decide_runs import observe_direct_run_attempt

REVIEWER, AGENT = "bob", "agent-bob"
CUTOFF = "2026-08-01T01:00:00Z"
UTC = timezone.utc


def run(rid, status="completed", created="2026-08-01T01:01:00Z", updated=None, error=None, trigger="issue_assignment"):
    return AgentRunObservation(id=rid, kind="direct", status=status, agent_id=AGENT, created_at=created,
                               updated_at=updated, error=error, trigger_kind=trigger)


def identity(created=CUTOFF, run_id="run-1"):
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA, handoff_generation="g", worker="alice", agent_id="agent-alice", run_id=run_id,
        pr_url="https://example.test/pr/1", pr_head_sha="h", verification_sha256="s", verification_attachment_id="a",
        verification_comment_id="c", verification_task_id="t", verification_created_at=created)


def persisted(cutoff=CUTOFF, **kw):
    return ReviewerRunBaseline(
        schema=REVIEWER_RUN_BASELINE_SCHEMA, subject_digest="subject", target_reviewer=REVIEWER,
        target_agent_id=AGENT, cutoff_created_at=cutoff, generation="gen", attempt=1,
        baseline_direct_run_ids=(), **kw)


def baseline_obs(**kw):
    base = dict(persisted=None, subject_digest="subject", reviewer=REVIEWER, reviewer_id=AGENT, identity=identity(),
                stable_run_identity=True, reviewer_runs=())
    return BaselineObservation(**{**base, **kw})


# ---- baseline ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("obs", "action", "reason"), [
    pytest.param(dict(persisted=persisted()), B.USE, None, id="persisted-valid"),
    pytest.param(dict(persisted=persisted("2026-08-01T09:00:00+08:00")), B.USE, None, id="equivalent-instant"),
    pytest.param(dict(persisted=persisted(None)), B.FILL_CUTOFF, None, id="missing-cutoff-is-filled"),
    pytest.param(dict(persisted=persisted("2026-08-01T01:00:00")), B.UNAVAILABLE, "unusable or mismatched",
                 id="persisted-naive-cutoff"),
    pytest.param(dict(persisted=persisted("not-a-time")), B.UNAVAILABLE, "unusable or mismatched",
                 id="persisted-malformed-cutoff"),
    pytest.param(dict(persisted=persisted("2026-08-01T01:00:01Z")), B.UNAVAILABLE, "unusable or mismatched",
                 id="persisted-mismatched-cutoff"),
    pytest.param(dict(persisted=persisted(), identity=identity("2026-08-01T01:00:00")), B.UNAVAILABLE,
                 "verification time is unusable", id="identity-naive-cutoff"),
    pytest.param(dict(persisted=persisted(), identity=identity("not-a-time")), B.UNAVAILABLE,
                 "verification time is unusable", id="identity-malformed-cutoff"),
    pytest.param(dict(persisted=persisted(), identity=identity(run_id=None)), B.UNAVAILABLE,
                 "verification time is unusable", id="identity-incomplete"),
    pytest.param(dict(persisted=persisted(), identity=None), B.UNAVAILABLE, "verification time is unusable",
                 id="identity-missing"),
    pytest.param(dict(persisted=persisted(), subject_digest="other"), B.UNAVAILABLE, "incomplete or stale",
                 id="stale-subject"),
    pytest.param(dict(persisted=persisted(), reviewer="carol"), B.UNAVAILABLE, "incomplete or stale",
                 id="other-reviewer"),
    pytest.param(dict(persisted=persisted(), reviewer_id="agent-carol"), B.UNAVAILABLE, "incomplete or stale",
                 id="other-agent"),
    pytest.param(dict(identity=None), B.UNAVAILABLE, "missing or incomplete", id="new-without-identity"),
    pytest.param(dict(identity=identity(run_id=None)), B.UNAVAILABLE, "missing or incomplete", id="new-incomplete"),
    pytest.param(dict(stable_run_identity=False), B.UNAVAILABLE, "stable Run identity", id="no-stable-run-identity"),
    pytest.param(dict(identity=identity("2026-08-01T01:00:00")), B.UNAVAILABLE, "stable Run identity",
                 id="new-naive-cutoff"),
    pytest.param(dict(subject_digest=None), B.UNAVAILABLE, "stable Run identity", id="no-subject"),
    pytest.param(dict(reviewer_runs=(run("r", created=None),)), B.UNAVAILABLE, "no usable creation time",
                 id="run-without-creation-time"),
    pytest.param(dict(), B.CREATE, None, id="new-baseline"),
])
def test_baseline_decision(obs, action, reason):
    decided = decide_baseline(baseline_obs(**obs), generation="fresh")
    assert decided.action is action
    assert (reason is None) or reason in decided.reason


def test_a_new_baseline_keeps_only_runs_created_up_to_the_delivery_time():
    """PROJ-826: the issue's own update time is irrelevant; the sealed delivery time decides what is history."""
    history = run("run-history", created="2026-07-31T16:06:30Z")
    at_cutoff = run("run-at-cutoff", created="2026-07-31T16:07:00Z")
    current = run("run-current", created="2026-07-31T16:08:23Z")
    decided = decide_baseline(baseline_obs(
        identity=identity("2026-07-31T16:07:00Z"), reviewer_runs=(current, at_cutoff, history)), generation="fresh")
    assert decided.action is B.CREATE
    assert decided.baseline.baseline_direct_run_ids == ("run-at-cutoff", "run-history")
    assert decided.baseline.cutoff_created_at == "2026-07-31T16:07:00Z" and decided.baseline.generation == "fresh"


def test_filling_a_missing_cutoff_takes_a_new_generation():
    decided = decide_baseline(baseline_obs(persisted=persisted(None)), generation="fresh")
    assert (decided.baseline.cutoff_created_at, decided.baseline.generation) == (CUTOFF, "fresh")


def test_a_baseline_of_many_historical_runs_is_capped():
    runs = tuple(run(f"r{i:02d}", created=f"2026-07-31T10:{i:02d}:00Z") for i in range(30))
    decided = decide_baseline(baseline_obs(reviewer_runs=runs), generation="fresh")
    assert len(decided.baseline.baseline_direct_run_ids) == 20


# ---- what the observed Run requires -------------------------------------------------------------------------

def decide(runs, *, attempt=1, baseline_ids=(), target=None, unassigned=False):
    observed = observe_direct_run_attempt(
        runs, AGENT, baseline_direct_run_ids=baseline_ids, cutoff_created_at=CUTOFF, target_run_id=target,
        attempt=attempt)
    return decide_reviewer_run(RunObservation(observed=observed, attempt=attempt, in_review_unassigned=unassigned))


TRANSIENT = "hermes provider error: ⚠️  API call failed (attempt 3/8): InternalServerError [HTTP 503]"


@pytest.mark.parametrize(("runs", "kw", "action"), [
    pytest.param([], dict(), A.WAIT, id="nothing-yet"),
    pytest.param([], dict(unassigned=True), A.QUEUE_INITIAL_DISPATCH, id="prepared-subject-never-dispatched"),
    pytest.param([], dict(attempt=2), A.BLOCK_DISPATCH_UNRESOLVED, id="retry-generation-without-a-run"),
    pytest.param([run("r1", "running")], dict(), A.WAIT, id="active"),
    pytest.param([run("r1", "completed")], dict(), A.CHECK_NO_SUBMIT, id="completed-without-verdict"),
    pytest.param([run("r1", "cancelled")], dict(), A.CHECK_NO_SUBMIT, id="cancelled-counts-as-no-submit"),
    pytest.param([run("r1", "failed", error=TRANSIENT)], dict(), A.RETRY_INFRASTRUCTURE, id="first-provider-failure"),
    pytest.param([run("r2", "failed", created="2026-08-01T01:02:00Z", error=TRANSIENT),
                  run("r1", "failed", error=TRANSIENT)], dict(target="r2"), A.BLOCK_RUNTIME_FAILURE,
                 id="two-provider-failures-in-a-row"),
    pytest.param([run("r2", "failed", created="2026-08-01T01:02:00Z", error=TRANSIENT)],
                 dict(attempt=2, baseline_ids=("r1",), target="r2"), A.BLOCK_RUNTIME_FAILURE,
                 id="second-attempt-failing-again-spends-the-budget"),
    pytest.param([run("r1", "failed", error="request rejected by network security policy")], dict(),
                 A.BLOCK_RUNTIME_FAILURE, id="non-retryable"),
    pytest.param([run("r2", "running", created="2026-08-01T01:02:00Z"), run("r1", "running")], dict(),
                 A.BLOCK_BASELINE_UNAVAILABLE, id="two-unexplained-runs"),
])
def test_reviewer_run_decision(runs, kw, action):
    assert decide(runs, **kw).action is action


def test_a_provider_failure_carries_its_classification_for_the_block_reason():
    decided = decide([run("r1", "failed", error="quota exhausted for this account")])
    assert decided.action is A.BLOCK_RUNTIME_FAILURE and decided.failure.classification == "nonretryable"


# ---- ended without a verdict --------------------------------------------------------------------------------

NOW = datetime(2026, 8, 1, 1, 3, 1, tzinfo=UTC)


@pytest.mark.parametrize(("ended", "state"), [
    ("2026-08-01T01:02:50Z", "waiting"),     # 11 s ago, inside the 30 s grace
    ("2026-08-01T01:02:31Z", "elapsed"),     # exactly 30 s
    ("2026-08-01T01:00:00Z", "elapsed"),
    ("2026-08-01T01:03:02Z", "unavailable"), # one second in the future
    ("2026-08-01T01:03:30Z", "unavailable"), # in the future
    ("2026-08-01T01:00:00", "unavailable"),  # no time zone
    ("not-a-time", "unavailable"),
    (None, "unknown"),                       # not reported yet: observe again
])
def test_no_submit_grace(ended, state):
    assert no_submit_grace(ended, NOW, 30) == state


@pytest.mark.parametrize(("grace", "consecutive", "budget", "action"), [
    ("submitted", 1, 2, N.WAIT), ("waiting", 1, 2, N.WAIT), ("blocked", 1, 2, N.STOP_BLOCKED),
    ("unavailable", 1, 2, N.BLOCK_TERMINAL_TIME_UNAVAILABLE), ("unknown", 1, 2, N.BLOCK_TERMINAL_TIME_UNAVAILABLE),
    ("elapsed", 1, 2, N.RETRY), ("elapsed", 2, 2, N.BLOCK_EXHAUSTED), ("elapsed", 5, 2, N.BLOCK_EXHAUSTED),
    ("elapsed", 3, 4, N.RETRY), ("elapsed", 4, 4, N.BLOCK_EXHAUSTED),
])
def test_no_submit_decision(grace, consecutive, budget, action):
    assert decide_no_submit(grace, consecutive_runs=consecutive, budget=budget) is action


def test_the_budget_follows_the_chain_of_no_submit_runs():
    chain = [run(f"r{i}", created=f"2026-08-01T01:0{i}:00Z", updated=f"2026-08-01T01:0{i}:30Z") for i in (3, 2, 1)]
    decided = decide(chain, attempt=3, baseline_ids=("r1", "r2"), target="r3")
    assert decided.action is A.CHECK_NO_SUBMIT
    observed = observe_direct_run_attempt(chain, AGENT, baseline_direct_run_ids=("r1", "r2"), cutoff_created_at=CUTOFF,
                                          target_run_id="r3", attempt=3)
    assert observed.terminal.consecutive_runs == 3
    assert decide_no_submit("elapsed", consecutive_runs=3, budget=4) is N.RETRY
    assert decide_no_submit("elapsed", consecutive_runs=3, budget=2) is N.BLOCK_EXHAUSTED


# ---- earlier Runs of the same Reviewer never count for the current subject --------------------------------------

def test_a_run_listed_in_the_baseline_is_history_and_the_current_run_may_be_invisible():
    """Eventual consistency: the current subject's Run is not listed yet, only an old terminal one."""
    history = run("run-history", created="2026-08-01T00:00:00Z", updated="2026-08-01T00:01:00Z")
    decided = decide([history], baseline_ids=("run-history",), unassigned=False)
    assert decided.action is A.WAIT


def test_a_late_visible_run_created_before_the_delivery_cutoff_is_ignored_even_when_not_in_the_id_snapshot():
    late_history = run("run-late-history", created="2025-12-31T23:59:59Z", updated="2026-01-01T00:00:00Z")
    assert decide([late_history]).action is A.WAIT


@pytest.mark.parametrize(("trigger", "formal"), [
    ("issue_assignment", True), ("rerun", True), ("comment", False), ("manual", False), (None, False)])
def test_only_assignment_and_rerun_runs_are_a_formal_dispatch(trigger, formal):
    from omac.pipeline.decide_runs import formal_dispatch_target

    candidate = run("run-1", "running", created="2026-08-01T01:00:02Z", trigger=trigger)
    observed = observe_direct_run_attempt([candidate], AGENT, cutoff_created_at=CUTOFF)
    target, error = formal_dispatch_target([candidate], observed)
    assert (target is candidate) is formal
    assert (error is None) is formal
    assert formal or "not a formal assignment/rerun dispatch" in error


# ---- the baseline of a dispatch that is about to start ----------------------------------------------------------

@pytest.mark.parametrize(("obs", "reason"), [
    (dict(stable_run_identity=False), "stable direct Run identity"),
    (dict(identity=None), "sealed delivery verification time"),
    (dict(identity=identity(run_id=None)), "sealed delivery verification time"),
    (dict(identity=identity("2026-01-01T00:00:01")), "sealed delivery verification time"),   # no time zone
    (dict(identity=identity("not-a-time")), "sealed delivery verification time"),
    (dict(direct_runs=(*(run(f"r{i:02d}", created=f"2026-07-31T10:{i:02d}:00Z") for i in range(20)),
                       run("x", created=None))), "creation time"),   # capping needs every creation time
])
def test_a_dispatch_baseline_needs_a_sealed_time_and_stable_run_identity(obs, reason):
    base = dict(subject_digest="subject", reviewer=REVIEWER, reviewer_id=AGENT, identity=identity(),
                stable_run_identity=True, direct_runs=())
    decided = decide_dispatch_baseline(DispatchBaselineObservation(**{**base, **obs}), generation="g")
    assert decided.action is B.UNAVAILABLE and reason in decided.reason


def test_every_existing_direct_run_is_history_for_a_new_dispatch():
    runs = (run("a", created="2026-08-01T00:00:00Z"), run("b", created="2026-08-01T02:00:00Z"))   # b is after the cutoff
    decided = decide_dispatch_baseline(DispatchBaselineObservation(
        subject_digest="subject", reviewer=REVIEWER, reviewer_id=AGENT, identity=identity(), stable_run_identity=True,
        direct_runs=runs), generation="g")
    assert decided.action is B.CREATE and set(decided.baseline.baseline_direct_run_ids) == {"a", "b"}
    assert decided.baseline.attempt == 1 and decided.baseline.target_run_id is None

"""Pure decisions about the one intended Reviewer Run: which baseline binds it, and what its Run facts require.

No I/O (enforced by tests/test_architecture.py). ``loop._observe_reviewer_run`` reads the Runs and the item, calls
these, then applies the action. Time and fresh identifiers come in as arguments.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Tuple

from ..core.taskmeta import REVIEWER_RUN_BASELINE_SCHEMA, DeliveryIdentity, ReviewerRunBaseline
from ..core.workitem import AgentRunObservation
from ..errors import PlatformError
from .decide_effects import Observation
from .decide_runs import (
    DirectRunAttempt, RunFailure, bounded_direct_run_baseline, parse_platform_time,
)

NO_SUBMIT_GRACE_SECONDS_DEFAULT = 30


# ---- which baseline binds the Reviewer Run -------------------------------------------------------------------

class BaselineAction(Enum):
    USE = "use"                  # the persisted baseline is valid as it is
    FILL_CUTOFF = "fill-cutoff"  # valid, but the cutoff must be filled from the sealed delivery and persisted
    CREATE = "create"            # none persisted: derive one from the sealed delivery and persist it
    UNAVAILABLE = "unavailable"  # fail closed with ``reason``


@dataclass(frozen=True)
class BaselineObservation(Observation):
    persisted: ReviewerRunBaseline | None
    subject_digest: str | None
    reviewer: str
    reviewer_id: str
    identity: DeliveryIdentity | None
    stable_run_identity: bool
    reviewer_runs: Tuple[AgentRunObservation, ...]   # direct Runs of the Reviewer agent on this issue


@dataclass(frozen=True)
class BaselineDecision:
    action: BaselineAction
    baseline: ReviewerRunBaseline | None = None
    reason: str | None = None


def _unavailable(reason: str) -> BaselineDecision:
    return BaselineDecision(BaselineAction.UNAVAILABLE, reason=reason)


def _decide_persisted(obs: BaselineObservation, generation: str) -> BaselineDecision:
    baseline, identity = obs.persisted, obs.identity
    if not (baseline.subject_digest == obs.subject_digest and baseline.target_reviewer == obs.reviewer
            and baseline.target_agent_id == obs.reviewer_id):
        return _unavailable("the persisted reviewer Run baseline is incomplete or stale")
    identity_cutoff = (parse_platform_time(identity.verification_created_at)
                       if identity is not None and identity.is_complete() else None)
    if identity_cutoff is None or identity_cutoff.tzinfo is None:
        return _unavailable("the current delivery identity or verification time is unusable")
    action = BaselineAction.USE
    if not baseline.cutoff_created_at:
        baseline = replace(baseline, cutoff_created_at=identity.verification_created_at, generation=generation)
        action = BaselineAction.FILL_CUTOFF
    cutoff = parse_platform_time(baseline.cutoff_created_at)
    if cutoff is None or cutoff.tzinfo is None or cutoff != identity_cutoff:
        return _unavailable("the persisted reviewer Run cutoff is unusable or mismatched")
    if not baseline.is_causally_bound():
        return _unavailable("the persisted reviewer Run baseline is incomplete")
    return BaselineDecision(action, baseline)


def _decide_new(obs: BaselineObservation, generation: str) -> BaselineDecision:
    identity = obs.identity
    if identity is None or not identity.is_complete():
        return _unavailable("the current delivery identity is missing or incomplete")
    cutoff = parse_platform_time(identity.verification_created_at)
    if not obs.subject_digest or cutoff is None or cutoff.tzinfo is None or not obs.stable_run_identity:
        return _unavailable(
            "the sealed delivery verification time or stable Run identity support is unavailable")
    historical = []
    for run in obs.reviewer_runs:
        created = parse_platform_time(run.created_at)
        if created is None or created.tzinfo is None:
            return _unavailable(f"Reviewer Run {run.id} has no usable creation time")
        if created <= cutoff:
            historical.append(run)
    try:
        historical_ids, _ = bounded_direct_run_baseline(
            historical, gate_cutoff_created_at=identity.verification_created_at)
    except PlatformError as exc:
        return _unavailable(str(exc))
    return BaselineDecision(BaselineAction.CREATE, ReviewerRunBaseline(
        schema=REVIEWER_RUN_BASELINE_SCHEMA, subject_digest=obs.subject_digest, target_reviewer=obs.reviewer,
        target_agent_id=obs.reviewer_id, cutoff_created_at=identity.verification_created_at, generation=generation,
        baseline_direct_run_ids=tuple(sorted(historical_ids))))


@dataclass(frozen=True)
class DispatchBaselineObservation(Observation):
    subject_digest: str
    reviewer: str
    reviewer_id: str
    identity: DeliveryIdentity | None
    stable_run_identity: bool
    direct_runs: Tuple[AgentRunObservation, ...]   # every direct Run on the issue: all of them are history


def decide_dispatch_baseline(obs: DispatchBaselineObservation, *, generation: str) -> BaselineDecision:
    """The baseline of a Reviewer dispatch that is about to start: every existing direct Run is history."""
    if not obs.stable_run_identity:
        return _unavailable("Reviewer recovery requires stable direct Run identity support")
    identity = obs.identity
    cutoff = parse_platform_time(identity.verification_created_at) if identity is not None else None
    if identity is None or not identity.is_complete() or cutoff is None or cutoff.tzinfo is None:
        return _unavailable("Reviewer dispatch requires a sealed delivery verification time")
    try:
        ids, _ = bounded_direct_run_baseline(
            list(obs.direct_runs), gate_cutoff_created_at=identity.verification_created_at)
    except PlatformError as exc:
        return _unavailable(str(exc))
    return BaselineDecision(BaselineAction.CREATE, ReviewerRunBaseline(
        schema=REVIEWER_RUN_BASELINE_SCHEMA, subject_digest=obs.subject_digest, target_reviewer=obs.reviewer,
        target_agent_id=obs.reviewer_id, cutoff_created_at=identity.verification_created_at, generation=generation,
        baseline_direct_run_ids=ids))


def decide_baseline(obs: BaselineObservation, *, generation: str) -> BaselineDecision:
    """Load the persisted baseline, or derive one from the sealed delivery's creation time."""
    return _decide_persisted(obs, generation) if obs.persisted is not None else _decide_new(obs, generation)


# ---- what the observed Run requires --------------------------------------------------------------------------

class RunAction(Enum):
    BLOCK_BASELINE_UNAVAILABLE = "block-baseline-unavailable"   # the Runs contradict the baseline
    BLOCK_DISPATCH_UNRESOLVED = "block-dispatch-unresolved"     # a persisted retry generation never got a Run
    QUEUE_INITIAL_DISPATCH = "queue-initial-dispatch"           # a prepared subject was never dispatched
    BLOCK_RUNTIME_FAILURE = "block-runtime-failure"             # non-retryable, or the infrastructure budget is spent
    RETRY_INFRASTRUCTURE = "retry-infrastructure"               # one bounded rerun that costs no review bounce
    CHECK_NO_SUBMIT = "check-no-submit"                         # ended without a verdict: apply decide_no_submit
    WAIT = "wait"                                               # active, or nothing observable yet


@dataclass(frozen=True)
class RunObservation(Observation):
    observed: DirectRunAttempt
    attempt: int
    in_review_unassigned: bool   # IN_REVIEW, review phase, no reviewer and no platform assignee on the issue


@dataclass(frozen=True)
class RunDecision:
    action: RunAction
    detail: str = ""
    failure: RunFailure | None = None


def decide_reviewer_run(obs: RunObservation) -> RunDecision:
    observed = obs.observed
    if observed.state == "unexpected":
        return RunDecision(RunAction.BLOCK_BASELINE_UNAVAILABLE, detail=observed.detail)
    target = observed.target_run_id
    if observed.state == "missing" and obs.attempt > 1 and target is None:
        return RunDecision(RunAction.BLOCK_DISPATCH_UNRESOLVED, "persisted retry generation has no target Run")
    if observed.state == "missing" and obs.attempt == 1 and target is None and obs.in_review_unassigned:
        return RunDecision(RunAction.QUEUE_INITIAL_DISPATCH)
    terminal = observed.terminal
    if terminal is None:
        return RunDecision(RunAction.WAIT)
    if terminal.outcome == "finished-without-submit":
        return RunDecision(RunAction.CHECK_NO_SUBMIT)
    failure = RunFailure(terminal.run, "transient" if terminal.outcome == "transient-failure" else "nonretryable",
                         terminal.consecutive_runs)
    if failure.classification == "nonretryable" or failure.exhausted:
        return RunDecision(RunAction.BLOCK_RUNTIME_FAILURE, failure=failure)
    return RunDecision(RunAction.RETRY_INFRASTRUCTURE, failure=failure)


# ---- a Run that ended without a verdict ----------------------------------------------------------------------

def no_submit_grace(ended_at: str | None, now: datetime, grace_seconds: float) -> str:
    """"unavailable" (unusable or future timestamp), "waiting", "elapsed", or "unknown" (no timestamp yet)."""
    if not ended_at:
        return "unknown"
    ended = parse_platform_time(ended_at)
    if ended is None or ended.tzinfo is None:
        return "unavailable"
    age = (now - ended).total_seconds()
    if age < 0:
        return "unavailable"
    return "elapsed" if age >= grace_seconds else "waiting"


class NoSubmitAction(Enum):
    WAIT = "wait"                              # a late verdict may still arrive, or one already did
    STOP_BLOCKED = "stop-blocked"              # persisted control stopped the dispatch
    BLOCK_TERMINAL_TIME_UNAVAILABLE = "block-terminal-time-unavailable"
    BLOCK_EXHAUSTED = "block-exhausted"        # the no-submit budget is spent
    RETRY = "retry"


def decide_no_submit(grace: str, *, consecutive_runs: int, budget: int) -> NoSubmitAction:
    """``grace``: "submitted", "blocked", "waiting", "unavailable" or "elapsed" (see ``no_submit_grace``)."""
    if grace in {"submitted", "waiting"}:
        return NoSubmitAction.WAIT
    if grace == "blocked":
        return NoSubmitAction.STOP_BLOCKED
    if grace != "elapsed":
        return NoSubmitAction.BLOCK_TERMINAL_TIME_UNAVAILABLE
    if consecutive_runs >= budget:
        return NoSubmitAction.BLOCK_EXHAUSTED
    return NoSubmitAction.RETRY

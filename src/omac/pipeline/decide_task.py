"""The stage transition table of one authoring task (``tasks.run_task``): facts in, next stage and effects out.

No I/O and no platform access (enforced by tests/test_architecture.py). ``tasks._TaskRun`` runs one *handler* per stage:
the handler observes (reads the platform, waits for a Run, calls the producer) and names what it saw as a ``Fact``.
``decide_task_step(stage, fact, obs)`` looks the pair up in ``TRANSITIONS`` and returns the next stage plus the effects
to apply before entering it. A pair that is not in the table raises: every reachable combination is a row, and
``tests/test_decide_task.py`` walks every row.

Observation points between effects are stages of their own. For example a budget stop persists feedback, *then* reads the
item back (``BLOCK_BUDGET``), *then* blocks it; each arrow is one row, so a restart between any two is a state the table
describes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from ..core.taskmeta import TaskPhase
from ..core.workitem import WorkItemStatus
from .decide_effects import (
    ClearAssignment, Effect, PrepareReviewCycle, ResetReview, UpdateMetadata, UpdateStatus,
)


class Stage(Enum):
    OPEN = "open"                          # create or resume the issue
    CHECK_RESUME = "check-resume"          # validate what a resumed issue may do
    PRODUCE = "produce"                    # first delivery from the producer
    RESOLVE_INITIAL = "resolve-initial"    # an already-reviewed delivery may short-circuit
    PRODUCE_REWORK = "produce-rework"      # producer redelivers after a counted reject
    MACHINE_GATE = "machine-gate"          # guard + preflight with a bounded number of revisions
    PRODUCE_HINTED = "produce-hinted"      # producer redelivers with machine feedback
    RESUME_REVIEW = "resume-review"        # a verdict may already be on the issue
    REVIEWER_CHECK = "reviewer-check"      # no reviewers configured: finish as passed
    REVIEW_ENTER = "review-enter"          # review budget check
    REVIEW_ROUND = "review-round"          # bind the review subject for this round
    REVIEW_CHECK = "review-check"          # is there a verdict, is its evidence valid
    REVIEW_DISPATCH = "review-dispatch"    # assign + wake the Reviewer, wait for a verdict
    REVIEW_VERDICT = "review-verdict"      # branch on the verdict
    PRODUCE_NITS = "produce-nits"          # producer redelivers after pass-with-nits
    PRODUCE_REJECT = "produce-reject"      # producer redelivers after a reject
    BLOCK_BUDGET = "block-budget"          # read the item, then project the exhausted budget decision
    FINISH = "finish"                      # settle the issue after a pass
    FINISH_WAIT = "finish-wait"            # wait for the human confirmation
    STOPPED = "stopped"                    # terminal: the handler raises NeedsDecision
    DONE = "done"                          # terminal: the result is ready


class Fact(Enum):
    OPENED = "opened"
    CONFIRMATION_RESUMED = "confirmation-resumed"
    FRESH = "fresh"
    PRODUCED = "produced"
    PRODUCED_AT_LIMIT = "produced-at-limit"
    ALREADY_PASSED = "already-passed"
    COUNTED_REJECT_RETRY = "counted-reject-retry"
    PROCEED = "proceed"
    GATE_CLEAR = "gate-clear"
    GATE_RETRY = "gate-retry"
    GATE_EXHAUSTED = "gate-exhausted"
    FINISH_PASS = "finish-pass"
    FINISH_CONFIRMED = "finish-confirmed"
    STALE_CONFIRMATION = "stale-confirmation"
    CONTINUE = "continue"
    NO_REVIEWERS = "no-reviewers"
    HAS_REVIEWERS = "has-reviewers"
    BUDGET_EXHAUSTED = "budget-exhausted"
    ROUNDS_REMAIN = "rounds-remain"
    PREPARE_FRESH_OBLIGATIONS = "prepare-fresh-obligations"
    PREPARE = "prepare"
    VERDICT_READY = "verdict-ready"
    EVIDENCE_INVALID = "evidence-invalid"
    NEEDS_DISPATCH = "needs-dispatch"
    REVIEWED = "reviewed"
    PASS = "pass"
    CONVERGENCE_STOP = "convergence-stop"
    AMENDMENT_NITS = "amendment-nits"
    NITS_RETRY = "nits-retry"
    REJECT_EXHAUSTED = "reject-exhausted"
    REJECT_RETRY = "reject-retry"
    OBSERVED = "observed"
    UNCONFIRMED = "unconfirmed"
    CONFIRM_PAUSE = "confirm-pause"
    CONFIRM_WAIT = "confirm-wait"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class TaskObs:
    """Everything a row's effects may depend on. Handlers fill only the fields their stage's facts need."""
    item_id: str
    round_index: int = 0
    subject_digest: str = ""
    obligations: Mapping[str, Any] | None = None
    feedback: Mapping[str, Any] | None = None
    feedback_summary: str = ""
    decision: Mapping[str, Any] | None = None
    stale_feedback: bool = False
    prepare_confirmation: bool = True
    decision_current: bool = False    # the item already carries this exact decision in the review phase
    already_blocked: bool = False


@dataclass(frozen=True)
class Transition:
    next: Stage
    effects: tuple[Effect, ...] = ()


Builder = Callable[[TaskObs], tuple[Effect, ...]]


def _none(obs: TaskObs) -> tuple[Effect, ...]:
    return ()


def _rework_to_authoring(obs: TaskObs) -> tuple[Effect, ...]:
    return (ResetReview(obs.item_id), UpdateStatus(obs.item_id, WorkItemStatus.TODO))


def _reset_review(obs: TaskObs) -> tuple[Effect, ...]:
    return (ResetReview(obs.item_id),)


def _persist_feedback(obs: TaskObs) -> tuple[Effect, ...]:
    return plan_machine_feedback(obs.item_id, obs.feedback, obs.feedback_summary)


def _back_to_review_phase(obs: TaskObs) -> tuple[Effect, ...]:
    return (UpdateMetadata(obs.item_id, {"phase": TaskPhase.REVIEW}),)


def _prepare_cycle(obs: TaskObs) -> tuple[Effect, ...]:
    return (PrepareReviewCycle(obs.item_id, obs.subject_digest),)


def _prepare_cycle_with_obligations(obs: TaskObs) -> tuple[Effect, ...]:
    return (UpdateMetadata(obs.item_id, {"review_obligations": obs.obligations}),) + _prepare_cycle(obs)


def _restart_invalid_review(obs: TaskObs) -> tuple[Effect, ...]:
    """Clear the invalid verdict, rebind the same subject, and tell the Reviewer what was wrong."""
    return (ResetReview(obs.item_id), PrepareReviewCycle(obs.item_id, obs.subject_digest),
            *plan_machine_feedback(obs.item_id, obs.feedback, obs.feedback_summary))


def _review_in_review(obs: TaskObs) -> tuple[Effect, ...]:
    return (UpdateStatus(obs.item_id, WorkItemStatus.IN_REVIEW),)


def _nits_back_to_authoring(obs: TaskObs) -> tuple[Effect, ...]:
    return (UpdateMetadata(obs.item_id, {
        "phase": TaskPhase.AUTHORING, "review_comment": "", "machine_feedback": {},
        "review_bounce": obs.round_index}), UpdateStatus(obs.item_id, WorkItemStatus.TODO))


def _count_reject(obs: TaskObs) -> tuple[Effect, ...]:
    return (UpdateMetadata(obs.item_id, {"review_bounce": obs.round_index}),)


def _count_reject_and_rework(obs: TaskObs) -> tuple[Effect, ...]:
    return _count_reject(obs) + _rework_to_authoring(obs)


def plan_persist_decision(
    item_id: str, decision: Mapping[str, Any], *, decision_current: bool, already_blocked: bool,
) -> tuple[Effect, ...]:
    """Project a decision onto the issue, writing only what is not already true (idempotent on restart)."""
    effects: list[Effect] = []
    if not decision_current:
        effects.append(UpdateMetadata(item_id, {"decision_required": decision, "phase": TaskPhase.REVIEW}))
    if not already_blocked:
        effects.append(UpdateStatus(item_id, WorkItemStatus.BLOCKED))
    return tuple(effects)


def plan_machine_feedback(item_id: str, feedback: Mapping[str, Any], summary: str) -> tuple[Effect, ...]:
    return (UpdateMetadata(item_id, {"machine_feedback": feedback, "review_comment": summary}),)


def _convergence_stop(obs: TaskObs) -> tuple[Effect, ...]:
    return plan_persist_decision(
        obs.item_id, obs.decision, decision_current=obs.decision_current, already_blocked=obs.already_blocked)


def _project_exhausted_budget(obs: TaskObs) -> tuple[Effect, ...]:
    return (UpdateMetadata(obs.item_id, {"decision_required": obs.decision, "phase": TaskPhase.REVIEW}),
            UpdateStatus(obs.item_id, WorkItemStatus.BLOCKED))


def _clear_stale_feedback(obs: TaskObs) -> tuple[Effect, ...]:
    if not obs.stale_feedback:
        return ()
    return (UpdateMetadata(obs.item_id, {"review_comment": "", "machine_feedback": {}}),)


def _finish_unconfirmed(obs: TaskObs) -> tuple[Effect, ...]:
    return _clear_stale_feedback(obs) + (UpdateStatus(obs.item_id, WorkItemStatus.DONE),)


def _finish_confirmation(obs: TaskObs) -> tuple[Effect, ...]:
    prepare = ((ClearAssignment(obs.item_id), UpdateMetadata(obs.item_id, {"phase": TaskPhase.CONFIRMATION}))
               if obs.prepare_confirmation else ())
    return _clear_stale_feedback(obs) + prepare


def _row(next_stage: Stage, builder: Builder = _none) -> tuple[Stage, Builder]:
    return next_stage, builder


S, F = Stage, Fact
TRANSITIONS: dict[tuple[Stage, Fact], tuple[Stage, Builder]] = {
    (S.OPEN, F.OPENED): _row(S.CHECK_RESUME),
    (S.CHECK_RESUME, F.CONFIRMATION_RESUMED): _row(S.RESOLVE_INITIAL),
    (S.CHECK_RESUME, F.FRESH): _row(S.PRODUCE),
    (S.PRODUCE, F.PRODUCED): _row(S.RESOLVE_INITIAL),
    (S.RESOLVE_INITIAL, F.ALREADY_PASSED): _row(S.DONE),
    (S.RESOLVE_INITIAL, F.COUNTED_REJECT_RETRY): _row(S.PRODUCE_REWORK, _rework_to_authoring),
    (S.RESOLVE_INITIAL, F.PROCEED): _row(S.MACHINE_GATE),
    (S.PRODUCE_REWORK, F.PRODUCED): _row(S.MACHINE_GATE),
    (S.MACHINE_GATE, F.GATE_CLEAR): _row(S.RESUME_REVIEW),
    (S.MACHINE_GATE, F.GATE_RETRY): _row(S.PRODUCE_HINTED, _reset_review),
    (S.MACHINE_GATE, F.GATE_EXHAUSTED): _row(S.BLOCK_BUDGET, _persist_feedback),
    (S.PRODUCE_HINTED, F.PRODUCED): _row(S.MACHINE_GATE),
    (S.RESUME_REVIEW, F.FINISH_PASS): _row(S.FINISH),
    (S.RESUME_REVIEW, F.FINISH_CONFIRMED): _row(S.FINISH),
    (S.RESUME_REVIEW, F.STALE_CONFIRMATION): _row(S.REVIEWER_CHECK, _back_to_review_phase),
    (S.RESUME_REVIEW, F.CONTINUE): _row(S.REVIEWER_CHECK),
    (S.REVIEWER_CHECK, F.NO_REVIEWERS): _row(S.FINISH),
    (S.REVIEWER_CHECK, F.HAS_REVIEWERS): _row(S.REVIEW_ENTER),
    (S.REVIEW_ENTER, F.BUDGET_EXHAUSTED): _row(S.BLOCK_BUDGET),
    (S.REVIEW_ENTER, F.ROUNDS_REMAIN): _row(S.REVIEW_ROUND),
    (S.REVIEW_ROUND, F.PREPARE_FRESH_OBLIGATIONS): _row(S.REVIEW_CHECK, _prepare_cycle_with_obligations),
    (S.REVIEW_ROUND, F.PREPARE): _row(S.REVIEW_CHECK, _prepare_cycle),
    (S.REVIEW_CHECK, F.VERDICT_READY): _row(S.REVIEW_VERDICT),
    (S.REVIEW_CHECK, F.EVIDENCE_INVALID): _row(S.REVIEW_CHECK, _restart_invalid_review),
    (S.REVIEW_CHECK, F.NEEDS_DISPATCH): _row(S.REVIEW_DISPATCH, _review_in_review),
    (S.REVIEW_DISPATCH, F.REVIEWED): _row(S.REVIEW_CHECK),
    (S.REVIEW_VERDICT, F.PASS): _row(S.FINISH),
    (S.REVIEW_VERDICT, F.CONVERGENCE_STOP): _row(S.STOPPED, _convergence_stop),
    (S.REVIEW_VERDICT, F.AMENDMENT_NITS): _row(S.FINISH),
    (S.REVIEW_VERDICT, F.NITS_RETRY): _row(S.PRODUCE_NITS, _nits_back_to_authoring),
    (S.REVIEW_VERDICT, F.REJECT_EXHAUSTED): _row(S.BLOCK_BUDGET, _count_reject),
    (S.REVIEW_VERDICT, F.REJECT_RETRY): _row(S.PRODUCE_REJECT, _count_reject_and_rework),
    (S.PRODUCE_NITS, F.PRODUCED_AT_LIMIT): _row(S.BLOCK_BUDGET),
    (S.PRODUCE_NITS, F.PRODUCED): _row(S.REVIEW_ROUND),
    (S.PRODUCE_REJECT, F.PRODUCED): _row(S.REVIEW_ROUND),
    (S.BLOCK_BUDGET, F.OBSERVED): _row(S.STOPPED, _project_exhausted_budget),
    (S.FINISH, F.UNCONFIRMED): _row(S.DONE, _finish_unconfirmed),
    (S.FINISH, F.CONFIRM_PAUSE): _row(S.DONE, _finish_confirmation),
    (S.FINISH, F.CONFIRM_WAIT): _row(S.FINISH_WAIT, _finish_confirmation),
    (S.FINISH_WAIT, F.CONFIRMED): _row(S.DONE),
}
TERMINAL = {Stage.STOPPED, Stage.DONE}


def decide_task_step(stage: Stage, fact: Fact, obs: TaskObs) -> Transition:
    """Next stage and the effects to apply before entering it. A (stage, fact) pair outside the table is a bug."""
    try:
        next_stage, builder = TRANSITIONS[(stage, fact)]
    except KeyError:
        raise KeyError(f"no transition for stage {stage.value!r} on fact {fact.value!r}") from None
    return Transition(next_stage, builder(obs))

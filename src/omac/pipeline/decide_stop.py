"""Why an authoring task stops, which command releases the stop, and how a resumed issue is classified.

No I/O and no platform access (enforced by tests/test_architecture.py). ``tasks.py`` observes, these functions name what
was seen: ``build_stop`` makes the one decision/report of a stop, ``STOP_RELEASES`` is the only place that says which
command releases which stop, ``render_release`` the only place that spells those commands, and ``decide_release`` the
only place that decides whether a release is allowed and what it writes. ``tests/test_decide_task.py`` parses every
rendered command with the real argparse parser, so a next_action that cannot run is a failing test.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..core.machine_feedback import build_machine_feedback
from ..core.review_continuation import continuation_record
from ..core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase, plan_id_of
from ..core.workitem import WorkItemStatus
from ..i18n import ui
from .decide_effects import Effect, Plan, UpdateMetadata, UpdateStatus
from .decide_task import plan_machine_feedback
from .decide_transitions import rework_to_producer

PLAN_KINDS = frozenset({TaskKind.PLAN, TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE})
REVIEW_STOP_GATES = frozenset({"review", "review-nits", "review-convergence"})
CONVERGENCE_PREFIX = "review-convergence-"


class StopReason(str, Enum):
    GUARD_BUDGET = "guard-budget-exhausted"
    GUARD_NON_REDUCING = "guard-non-reducing"                       # two gate rounds in a row did not lower the error count
    REVIEW_BUDGET = "review-budget-exhausted"                       # a reject at the review limit
    REVIEW_NITS_BUDGET = "review-nits-budget-exhausted"             # recorded by versions before 2.1 (a delivery left unreviewed)
    REVIEW_NITS_ACCEPTANCE = "review-nits-acceptance-required"
    REVIEW_CONVERGENCE = "review-convergence"                       # reason_code is review-convergence-<mode>
    COMPLETED_WITHOUT_SUBMIT = "completed-without-submit"
    UNSAFE_AUTHORING_RESUME = "unsafe-authoring-resume"
    REVIEWER_COMPLETED_WITHOUT_VERDICT = "reviewer-completed-without-verdict"
    REVIEW_EVIDENCE_INVALID = "review-evidence-invalid"             # the Reviewer's report kept failing the evidence gate
    CONFIRMATION_REQUIRED = "confirmation-required"                 # the human gate: a passed delivery awaits a person


class Release(str, Enum):
    CONTINUE_REVIEW = "continue-review"
    ACCEPT_NITS = "accept-nits"
    RESUME = "resume"
    RESTART_ACTIVE = "restart-active"
    CONFIRM = "confirm"
    REJECT_CONFIRMATION = "reject-confirmation"


# The first release is the stop's next_action; the others are alternatives it lists.
STOP_RELEASES: dict[StopReason, tuple[Release, ...]] = {
    StopReason.GUARD_BUDGET: (Release.RESUME,),
    StopReason.GUARD_NON_REDUCING: (Release.RESUME,),
    StopReason.REVIEW_BUDGET: (Release.CONTINUE_REVIEW,),
    StopReason.REVIEW_NITS_BUDGET: (Release.CONTINUE_REVIEW,),
    StopReason.REVIEW_NITS_ACCEPTANCE: (Release.ACCEPT_NITS, Release.CONTINUE_REVIEW),
    StopReason.REVIEW_CONVERGENCE: (Release.CONTINUE_REVIEW,),
    StopReason.COMPLETED_WITHOUT_SUBMIT: (Release.RESTART_ACTIVE,),
    StopReason.UNSAFE_AUTHORING_RESUME: (Release.RESTART_ACTIVE,),
    StopReason.REVIEWER_COMPLETED_WITHOUT_VERDICT: (Release.RESUME,),
    StopReason.REVIEW_EVIDENCE_INVALID: (Release.RESUME,),
    StopReason.CONFIRMATION_REQUIRED: (Release.CONFIRM, Release.REJECT_CONFIRMATION),
}
# Releases that do not continue the pipeline themselves: the stop's report names `plan resume` as the step after them.
_CONTINUES_BY_ITSELF = frozenset({Release.RESUME, Release.RESTART_ACTIVE})


def render_release(release: Release, dag_key: str) -> str:
    """The command text of a release for the plan stage ``dag_key`` (the one place these commands are spelled)."""
    plan_id = plan_id_of(dag_key)
    return {
        Release.CONTINUE_REVIEW: f"omac plan continue-review --dag-key {dag_key}",
        Release.ACCEPT_NITS: f"omac plan accept-nits --dag-key {dag_key}",
        Release.RESUME: f"omac plan resume --plan-id {plan_id}",
        Release.RESTART_ACTIVE: f"omac plan resume --plan-id {plan_id} --restart-active",
        Release.CONFIRM: f"omac plan confirm --dag-key {dag_key}",
        Release.REJECT_CONFIRMATION: f'omac plan confirm --dag-key {dag_key} --reject --reason "<what to change>"',
    }[release]


def inspect_action(item_id: str) -> str:
    return f"omac work show {item_id} --output json"


def stop_reason_of(decision: Any) -> StopReason | None:
    code = decision.get("reason_code") if isinstance(decision, Mapping) else None
    if not isinstance(code, str):
        return None
    if code.startswith(CONVERGENCE_PREFIX):
        return StopReason.REVIEW_CONVERGENCE
    return next((reason for reason in StopReason if reason.value == code), None)


def build_stop(
    reason: StopReason, *, kind: TaskKind, item_id: str, dag_key: str, gate: str, rounds: int,
    phase: TaskPhase = TaskPhase.REVIEW, extra: Mapping[str, Any] | None = None,
) -> dict:
    """The decision a stop persists. The report of the NeedsDecision is this same dict, so issue and caller agree.

    Only the three plan stages are released by plan commands; any other kind (an amendment) names none.
    """
    decision: dict[str, Any] = {
        "schema": DECISION_REQUIRED_SCHEMA, "reason_code": reason.value, "kind": kind.value, "phase": phase.value,
        "gate": gate, "rounds": rounds, "resume_issue_id": item_id,
    }
    decision.update({key: value for key, value in (extra or {}).items() if value not in (None, "", {}, [])})
    if kind in PLAN_KINDS:
        actions = [render_release(release, dag_key) for release in STOP_RELEASES[reason]]
        decision["next_action"] = actions[0]
        if len(actions) > 1:
            decision["alternatives"] = actions[1:]
        if STOP_RELEASES[reason][0] not in _CONTINUES_BY_ITSELF:
            decision["then"] = render_release(Release.RESUME, dag_key)
    return decision


# ── releasing a stop ─────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReleaseFacts:
    """What a release decision reads about one stage issue."""
    item_id: str
    status: WorkItemStatus
    phase: TaskPhase
    verdict: str
    has_deliverable: bool
    decision: Mapping[str, Any] | None
    bounce: int                          # review bounces counted so far
    limit: int                           # the authorized review limit
    continuation: Mapping[str, Any] | None
    active_runs: tuple[str, ...] = ()
    delivery_bound: bool = False         # the verdict is bound to the current delivery and its report is sealed


@dataclass(frozen=True)
class Refusal:
    message: str


def _legacy_review_only(facts: ReleaseFacts, stop: StopReason | None) -> bool:
    """A delivery left awaiting review at the limit: it is still in review, or an older version stopped on it."""
    if facts.verdict or facts.phase != TaskPhase.REVIEW or not facts.has_deliverable:
        return False
    if facts.status == WorkItemStatus.IN_REVIEW:
        return True
    decision = facts.decision or {}
    return bool(
        facts.status == WorkItemStatus.BLOCKED
        and stop in (StopReason.REVIEW_NITS_BUDGET, StopReason.REVIEW_BUDGET)
        and decision.get("gate") in ("review", "review-nits")
        and decision.get("rounds") == facts.bounce)


def _continue_review(facts: ReleaseFacts, stage: str, reason: str) -> Plan | Refusal:
    stop = stop_reason_of(facts.decision)
    pending = facts.bounce + 1

    def record(through: int, mode: str) -> UpdateMetadata:
        return UpdateMetadata(facts.item_id, {"review_continuation": continuation_record(
            stage=stage, existing=facts.continuation, mode=mode, through=through, reason=reason)})

    if stop is StopReason.REVIEW_CONVERGENCE and facts.verdict == "reject":
        # The extra round must not meet the same convergence stop again, or the operator's decision changes nothing.
        through = pending if pending < facts.limit else pending + 1
        return Plan((record(through, "producer-rework"), *rework_to_producer(facts.item_id)), "producer-rework")
    if stop is StopReason.REVIEW_NITS_ACCEPTANCE and facts.verdict == "pass-with-nits":
        # Declining the nits is a counted return to the author, as `node retry` is for a develop node.
        counted = UpdateMetadata(facts.item_id, {"review_bounce": pending})
        extra = (record(max(pending, facts.limit) + 1, "producer-rework"),) if pending >= facts.limit else ()
        return Plan((counted, *extra, *rework_to_producer(facts.item_id)), "producer-rework")
    if facts.verdict == "reject" and facts.bounce >= facts.limit:
        return Plan((record(max(facts.bounce, facts.limit) + 1, "producer-rework"),
                     *rework_to_producer(facts.item_id)), "producer-rework")
    if facts.bounce >= facts.limit and _legacy_review_only(facts, stop):
        effects: tuple[Effect, ...] = (record(max(facts.bounce, facts.limit) + 1, "review-only"),)
        if facts.status == WorkItemStatus.BLOCKED:
            effects += (UpdateMetadata(facts.item_id, {"decision_required": {}}),
                        UpdateStatus(facts.item_id, WorkItemStatus.IN_REVIEW))
        return Plan(effects, "review-only")
    if facts.bounce < facts.limit and (facts.verdict == "reject" or _legacy_review_only(facts, stop)):
        return Refusal(ui(
            f"Review round {pending} is already authorized through round {facts.limit}. Run plan resume instead "
            "of stacking another decision.",
            f"当前已授权到 review round {facts.limit}；请运行 plan resume，不要重复叠加 decision。"))
    return Refusal(ui(
        f"Work item {facts.item_id} is not stopped on a review decision that continue-review releases "
        "(an exhausted reject, declinable nits, or a review-convergence stop).",
        f"Work item {facts.item_id} 没有停在 continue-review 可放行的 review 决策上"
        "（reject 耗尽、可拒绝的 nits 或 review-convergence 停止）。"))


def _decide_confirmation(release: Release, facts: ReleaseFacts, reason: str) -> Plan | Refusal:
    """The human gate: a delivery the Reviewer passed waits in the confirmation phase for a person."""
    if facts.phase != TaskPhase.CONFIRMATION or facts.status != WorkItemStatus.IN_REVIEW:
        return Refusal(ui(
            f"Work item {facts.item_id} is not waiting at the human gate (it needs phase confirmation and status "
            "in_review). It may already be confirmed, or not yet reviewed: run `omac plan resume --plan-id <id>`.",
            f"Work item {facts.item_id} 没有停在人机确认门（需要 phase=confirmation 且 status=in_review）。"
            "可能已确认或尚未评审通过：请运行 `omac plan resume --plan-id <id>`。"))
    if release is Release.CONFIRM:
        return Plan((UpdateStatus(facts.item_id, WorkItemStatus.DONE),), "confirm")
    if not reason.strip():
        return Refusal(ui(
            "--reject needs --reason <what to change>: the reason is the author's input for the rework.",
            "--reject 需要 --reason <要改什么>：该理由是作者返工的输入。"))
    feedback = build_machine_feedback("human-confirmation", [reason])
    summary = ui(
        f"A person returned this stage at the human gate. Reason: {reason}",
        f"人工在确认门退回了该阶段。理由：{reason}")
    # The feedback goes after the reset, which clears it; nothing is counted against the Reviewer's budget.
    return Plan((*rework_to_producer(facts.item_id), *plan_machine_feedback(facts.item_id, feedback, summary)),
                "reject-confirmation")


def decide_release(release: Release, facts: ReleaseFacts, *, stage: str = "", reason: str = "") -> Plan | Refusal:
    """Whether ``release`` may act on this stop, and the ordered effects it writes."""
    if facts.active_runs:
        return Refusal(ui(
            f"Work item {facts.item_id} still has an active Agent run ({', '.join(facts.active_runs)}). Wait for it "
            "to finish; releasing a stop never cancels runs.",
            f"Work item {facts.item_id} 仍有活跃 Agent run（{', '.join(facts.active_runs)}）。请等待其结束；"
            "放行停止绝不取消运行。"))
    if release is Release.CONTINUE_REVIEW:
        return _continue_review(facts, stage, reason)
    if release is Release.ACCEPT_NITS:
        if (facts.verdict != "pass-with-nits" or facts.phase != TaskPhase.REVIEW or not facts.delivery_bound
                or facts.status not in (WorkItemStatus.BLOCKED, WorkItemStatus.IN_REVIEW)):
            return Refusal(ui(
                f"Work item {facts.item_id} has no pass-with-nits verdict bound to its current delivery with a sealed "
                "report, so there is nothing to accept. Run plan resume, or plan continue-review to get a review.",
                f"Work item {facts.item_id} 没有绑定当前交付且报告已封存的 pass-with-nits verdict，没有可接受的内容。"
                "请运行 plan resume，或 plan continue-review 重新取得评审。"))
        return Plan((), "accept-nits")
    if release in (Release.CONFIRM, Release.REJECT_CONFIRMATION):
        return _decide_confirmation(release, facts, reason)
    return Plan((), release.value)      # resume / restart-active: the resume itself classifies and checks the issue


# ── classifying a resumed issue ──────────────────────────────────────────────────────────────────────────────

class Resume(str, Enum):
    PROCEED = "proceed"                                  # carry on from the delivery / dispatch as the stage dictates
    UNSTARTED = "unstarted"                              # nothing was dispatched: refresh the issue in place
    ATTACH_RUNNING = "attach-running"                    # the author's Run is alive: wait for it, dispatch nothing
    CLEAR_REVIEWER_STOP = "clear-reviewer-stop"          # the Reviewer's Run ended without a verdict: safe to review again
    RUN_NOT_TERMINAL = "run-not-terminal"                # a Run's end is unknown: dispatch nothing
    COMPLETED_WITHOUT_SUBMIT = "completed-without-submit"
    STOPPED_WITH_DELIVERY = "stopped-with-delivery"      # the author stopped after a delivery: only --restart-active is safe
    STOPPED_AUTHORING = "stopped-authoring"              # blocked authoring with nothing delivered


@dataclass(frozen=True)
class ResumeFacts:
    phase: TaskPhase
    status: WorkItemStatus
    has_deliverable: bool
    decision_reason: str                 # reason_code of the standing decision, "" if none
    run_flagged_stopped: bool            # the platform recorded a failed Run or a Run that ended without a submit
    produced: bool                       # the issue already holds a delivery the stage can consume
    active_runs: bool = False            # a direct Run is active
    unknown_runs: bool = False           # a direct Run is neither active nor terminal
    has_direct_runs: bool = False
    latest_run_terminal: bool = False


_REVIEWER_STOPS = frozenset({StopReason.REVIEWER_COMPLETED_WITHOUT_VERDICT.value, StopReason.REVIEW_EVIDENCE_INVALID.value})


def _reviewer_stop(facts: ResumeFacts) -> bool:
    """A review-phase stop that only needs the Reviewer asked again: resuming clears it once no Run is live."""
    return (facts.phase == TaskPhase.REVIEW and facts.status == WorkItemStatus.BLOCKED
            and facts.decision_reason in _REVIEWER_STOPS)


def _author_in_progress(facts: ResumeFacts) -> bool:
    return (facts.phase == TaskPhase.AUTHORING and facts.status == WorkItemStatus.IN_PROGRESS
            and not facts.has_deliverable and not facts.run_flagged_stopped)


def needs_run_facts(facts: ResumeFacts) -> bool:
    """Only an authoring issue the platform left in progress, or a Reviewer stop, depends on what the Runs say."""
    return _reviewer_stop(facts) or _author_in_progress(facts)


def classify_resume(facts: ResumeFacts) -> Resume:
    if _reviewer_stop(facts):
        return Resume.RUN_NOT_TERMINAL if facts.active_runs or facts.unknown_runs else Resume.CLEAR_REVIEWER_STOP
    if facts.phase != TaskPhase.AUTHORING:
        return Resume.PROCEED
    stopped = facts.status in (WorkItemStatus.FAILED, WorkItemStatus.BLOCKED) or facts.run_flagged_stopped
    if facts.has_deliverable:
        return Resume.STOPPED_WITH_DELIVERY if stopped else Resume.PROCEED
    if _author_in_progress(facts):
        if facts.unknown_runs or (facts.has_direct_runs and not facts.latest_run_terminal and not facts.active_runs):
            return Resume.RUN_NOT_TERMINAL
        if facts.active_runs:
            return Resume.ATTACH_RUNNING
        return Resume.PROCEED if facts.produced or not facts.has_direct_runs else Resume.COMPLETED_WITHOUT_SUBMIT
    if facts.status == WorkItemStatus.BLOCKED and not facts.run_flagged_stopped:
        return Resume.STOPPED_AUTHORING
    if facts.status == WorkItemStatus.TODO and not facts.run_flagged_stopped:
        return Resume.UNSTARTED
    return Resume.PROCEED

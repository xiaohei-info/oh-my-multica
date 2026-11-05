"""The run_task stage transition table: every row is pinned (next stage and effects), and the table is closed."""
import pytest

from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus as S
from omac.pipeline.decide_effects import (
    ClearAssignment, PrepareReviewCycle, ResetReview, UpdateMetadata, UpdateStatus,
)
from omac.pipeline.decide_task import (
    Fact as F, Stage as T, TERMINAL, TRANSITIONS, TaskObs, decide_task_step, plan_persist_decision,
)

OBS = TaskObs(
    item_id="i", round_index=2, subject_digest="d", obligations={"o": 1}, feedback={"f": 1},
    feedback_summary="s", decision={"reason_code": "r"})
FB = UpdateMetadata("i", {"machine_feedback": {"f": 1}, "review_comment": "s"})
TODO = UpdateStatus("i", S.TODO)
DECISION_WRITE = (UpdateMetadata("i", {"decision_required": {"reason_code": "r"}, "phase": TaskPhase.REVIEW}),
                  UpdateStatus("i", S.BLOCKED))

# (stage, fact) -> (next stage, effects) for an observation with feedback, a decision and round 2.
EXPECTED = {
    (T.OPEN, F.OPENED): (T.CHECK_RESUME, ()),
    (T.CHECK_RESUME, F.CONFIRMATION_RESUMED): (T.RESOLVE_INITIAL, ()),
    (T.CHECK_RESUME, F.FRESH): (T.PRODUCE, ()),
    (T.PRODUCE, F.PRODUCED): (T.RESOLVE_INITIAL, ()),
    (T.RESOLVE_INITIAL, F.ALREADY_PASSED): (T.DONE, ()),
    (T.RESOLVE_INITIAL, F.COUNTED_REJECT_RETRY): (T.PRODUCE_REWORK, (ResetReview("i"), TODO)),
    (T.RESOLVE_INITIAL, F.PROCEED): (T.MACHINE_GATE, ()),
    (T.PRODUCE_REWORK, F.PRODUCED): (T.MACHINE_GATE, ()),
    (T.MACHINE_GATE, F.GATE_CLEAR): (T.RESUME_REVIEW, ()),
    (T.MACHINE_GATE, F.GATE_RETRY): (T.PRODUCE_HINTED, (ResetReview("i"), TODO)),
    (T.MACHINE_GATE, F.GATE_EXHAUSTED): (T.BLOCK_BUDGET, (FB,)),
    (T.PRODUCE_HINTED, F.PRODUCED): (T.MACHINE_GATE, ()),
    (T.RESUME_REVIEW, F.FINISH_PASS): (T.FINISH, ()),
    (T.RESUME_REVIEW, F.FINISH_CONFIRMED): (T.FINISH, ()),
    (T.RESUME_REVIEW, F.STALE_CONFIRMATION): (T.REVIEWER_CHECK, (UpdateMetadata("i", {"phase": TaskPhase.REVIEW}),)),
    (T.RESUME_REVIEW, F.CONTINUE): (T.REVIEWER_CHECK, ()),
    (T.REVIEWER_CHECK, F.NO_REVIEWERS): (T.FINISH, ()),
    (T.REVIEWER_CHECK, F.HAS_REVIEWERS): (T.REVIEW_ENTER, ()),
    (T.REVIEW_ENTER, F.BUDGET_EXHAUSTED): (T.BLOCK_BUDGET, ()),
    (T.REVIEW_ENTER, F.ROUNDS_REMAIN): (T.REVIEW_ROUND, ()),
    (T.REVIEW_ROUND, F.PREPARE_FRESH_OBLIGATIONS): (
        T.REVIEW_CHECK, (UpdateMetadata("i", {"review_obligations": {"o": 1}}), PrepareReviewCycle("i", "d"))),
    (T.REVIEW_ROUND, F.PREPARE): (T.REVIEW_CHECK, (PrepareReviewCycle("i", "d"),)),
    (T.REVIEW_CHECK, F.VERDICT_READY): (T.REVIEW_VERDICT, ()),
    (T.REVIEW_CHECK, F.EVIDENCE_INVALID): (T.REVIEW_CHECK, (ResetReview("i"), PrepareReviewCycle("i", "d"), FB)),
    (T.REVIEW_CHECK, F.NEEDS_DISPATCH): (T.REVIEW_DISPATCH, (UpdateStatus("i", S.IN_REVIEW),)),
    (T.REVIEW_DISPATCH, F.REVIEWED): (T.REVIEW_CHECK, ()),
    (T.REVIEW_VERDICT, F.PASS): (T.FINISH, ()),
    (T.REVIEW_VERDICT, F.CONVERGENCE_STOP): (T.STOPPED, DECISION_WRITE),
    (T.REVIEW_VERDICT, F.AMENDMENT_NITS): (T.FINISH, ()),
    (T.REVIEW_VERDICT, F.NITS_ACCEPTED): (T.FINISH, ()),
    (T.REVIEW_VERDICT, F.NITS_NEED_DECISION): (T.STOPPED, DECISION_WRITE),
    (T.REVIEW_VERDICT, F.REJECT_EXHAUSTED): (T.BLOCK_BUDGET, (UpdateMetadata("i", {"review_bounce": 2}),)),
    (T.REVIEW_VERDICT, F.REJECT_RETRY): (T.PRODUCE_REJECT, (UpdateMetadata("i", {"review_bounce": 2}),
                                                            ResetReview("i"), TODO)),
    (T.PRODUCE_REJECT, F.PRODUCED): (T.REVIEW_ROUND, ()),
    (T.BLOCK_BUDGET, F.OBSERVED): (T.STOPPED, DECISION_WRITE),
    (T.FINISH, F.UNCONFIRMED): (T.DONE, (UpdateStatus("i", S.DONE),)),
    (T.FINISH, F.CONFIRM_PAUSE): (T.DONE, (ClearAssignment("i"), UpdateMetadata("i", {"phase": TaskPhase.CONFIRMATION}))),
    (T.FINISH, F.CONFIRM_WAIT): (T.FINISH_WAIT, (ClearAssignment("i"), UpdateMetadata("i", {"phase": TaskPhase.CONFIRMATION}))),
    (T.FINISH_WAIT, F.CONFIRMED): (T.DONE, ()),
}


def test_the_expected_table_is_the_whole_table():
    assert set(EXPECTED) == set(TRANSITIONS)


@pytest.mark.parametrize("key", sorted(EXPECTED, key=lambda k: (k[0].value, k[1].value)),
                         ids=lambda k: f"{k[0].value}--{k[1].value}")
def test_row(key):
    transition = decide_task_step(*key, OBS)
    assert (transition.next, transition.effects) == EXPECTED[key]


def test_every_stage_is_reachable_and_only_terminal_stages_have_no_rows():
    seen, todo = {T.OPEN}, [T.OPEN]
    while todo:
        stage = todo.pop()
        for (src, _fact), (nxt, _b) in TRANSITIONS.items():
            if src is stage and nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    assert seen == set(T)
    with_rows = {src for src, _ in TRANSITIONS}
    assert set(T) - with_rows == TERMINAL


def test_unknown_pair_is_an_error_not_a_default():
    with pytest.raises(KeyError, match="no transition"):
        decide_task_step(T.PRODUCE, F.REVIEWED, OBS)


@pytest.mark.parametrize("stale,prepare,expected_kinds", [
    (False, True, []),
    (True, True, ["UpdateMetadata"]),
])
def test_finish_unconfirmed_clears_stale_feedback_first(stale, prepare, expected_kinds):
    obs = TaskObs(item_id="i", stale_feedback=stale, prepare_confirmation=prepare)
    effects = decide_task_step(T.FINISH, F.UNCONFIRMED, obs).effects
    assert [type(e).__name__ for e in effects[:-1]] == expected_kinds and effects[-1] == UpdateStatus("i", S.DONE)


def test_confirmation_without_prepare_keeps_the_assignment_and_phase():
    obs = TaskObs(item_id="i", prepare_confirmation=False)
    assert decide_task_step(T.FINISH, F.CONFIRM_PAUSE, obs).effects == ()


@pytest.mark.parametrize("current,blocked,kinds", [
    (False, False, ["UpdateMetadata", "UpdateStatus"]),
    (True, False, ["UpdateStatus"]),
    (False, True, ["UpdateMetadata"]),
    (True, True, []),
])
def test_persisting_a_decision_writes_only_what_is_not_already_true(current, blocked, kinds):
    effects = plan_persist_decision("i", {"x": 1}, decision_current=current, already_blocked=blocked)
    assert [type(e).__name__ for e in effects] == kinds


def test_a_reject_that_exhausts_the_budget_counts_the_round_before_blocking():
    first = decide_task_step(T.REVIEW_VERDICT, F.REJECT_EXHAUSTED, OBS)
    second = decide_task_step(first.next, F.OBSERVED, OBS)
    assert first.effects == (UpdateMetadata("i", {"review_bounce": 2}),)
    assert second.effects == DECISION_WRITE and second.next is T.STOPPED


# ── stops, their releases and resumed issues (pipeline/decide_stop.py) ────────────────────────────────────────

import shlex

from omac.cli.main import build_parser
from omac.core.taskmeta import TaskKind
from omac.pipeline.decide_stop import (
    PLAN_KINDS, STOP_RELEASES, Refusal, Release, ReleaseFacts, Resume, ResumeFacts, StopReason, build_stop,
    classify_resume, decide_release,
)

_STOPPED_ON = {
    StopReason.GUARD_BUDGET: dict(status=S.BLOCKED, verdict=""),
    StopReason.REVIEW_BUDGET: dict(status=S.BLOCKED, verdict="reject", bounce=3, limit=3),
    StopReason.REVIEW_NITS_BUDGET: dict(status=S.BLOCKED, verdict="", bounce=3, limit=3),
    StopReason.REVIEW_NITS_ACCEPTANCE: dict(status=S.BLOCKED, verdict="pass-with-nits", delivery_bound=True),
    StopReason.REVIEW_CONVERGENCE: dict(status=S.BLOCKED, verdict="reject", bounce=2, limit=3),
    StopReason.COMPLETED_WITHOUT_SUBMIT: dict(status=S.BLOCKED, phase=TaskPhase.AUTHORING),
    StopReason.UNSAFE_AUTHORING_RESUME: dict(status=S.BLOCKED, phase=TaskPhase.AUTHORING),
    StopReason.REVIEWER_COMPLETED_WITHOUT_VERDICT: dict(status=S.BLOCKED),
}
_GATES = {StopReason.REVIEW_NITS_BUDGET: "review-nits", StopReason.REVIEW_NITS_ACCEPTANCE: "review-nits",
          StopReason.REVIEW_CONVERGENCE: "review-convergence", StopReason.GUARD_BUDGET: "guard"}


def test_every_stop_has_releases_and_every_release_is_documented_by_a_stop():
    assert set(STOP_RELEASES) == set(StopReason) == set(_STOPPED_ON)
    assert {release for releases in STOP_RELEASES.values() for release in releases} == set(Release)


@pytest.mark.parametrize("reason", list(StopReason), ids=lambda r: r.value)
@pytest.mark.parametrize("kind", [TaskKind.PLAN, TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE])
def test_every_command_a_stop_names_parses_and_is_accepted_for_that_stop(reason, kind):
    """A next_action that cannot run, or that the stop's own release refuses, is the bug class behind 2.0.1-2.0.4."""
    key = f"{kind.value}-p-1"
    stop = build_stop(reason, kind=kind, item_id="i", dag_key=key, gate=_GATES.get(reason, "review"), rounds=3)
    commands = [stop["next_action"], *stop.get("alternatives", [])]
    assert len(commands) == len(STOP_RELEASES[reason])
    for command in commands:
        argv = shlex.split(command)
        assert argv[0] == "omac"
        build_parser().parse_args(argv[1:])
    facts = ReleaseFacts(item_id="i", status=S.BLOCKED, phase=TaskPhase.REVIEW, verdict="", has_deliverable=True, decision={
        **stop, "reason_code": reason.value}, bounce=3, limit=3, continuation=None)
    facts = ReleaseFacts(**{**facts.__dict__, **_STOPPED_ON[reason]})
    for release in STOP_RELEASES[reason]:
        assert not isinstance(decide_release(release, facts, stage=kind.value), Refusal), (release, reason)


def test_a_stop_of_a_kind_no_plan_command_releases_names_no_plan_command():
    stop = build_stop(StopReason.REVIEW_BUDGET, kind=TaskKind.AMENDMENT, item_id="i", dag_key="amend-x", gate="review", rounds=3)

    assert "next_action" not in stop and TaskKind.AMENDMENT not in PLAN_KINDS


def _resume(**over):
    base = dict(phase=TaskPhase.AUTHORING, status=S.IN_PROGRESS, has_deliverable=False, decision_reason="",
                run_flagged_stopped=False, produced=False)
    return ResumeFacts(**{**base, **over})


@pytest.mark.parametrize("facts,expected", [
    (_resume(status=S.TODO), Resume.UNSTARTED),
    (_resume(status=S.TODO, has_deliverable=True), Resume.PROCEED),
    (_resume(), Resume.PROCEED),                                                       # nothing ever ran: dispatch
    (_resume(has_direct_runs=True, active_runs=True), Resume.ATTACH_RUNNING),
    (_resume(has_direct_runs=True, latest_run_terminal=True), Resume.COMPLETED_WITHOUT_SUBMIT),
    (_resume(has_direct_runs=True, latest_run_terminal=True, produced=True), Resume.PROCEED),
    (_resume(has_direct_runs=True, unknown_runs=True), Resume.RUN_NOT_TERMINAL),
    (_resume(status=S.BLOCKED), Resume.STOPPED_AUTHORING),
    (_resume(status=S.FAILED), Resume.PROCEED),                                        # a failed Run is retried by the resume
    (_resume(status=S.BLOCKED, has_deliverable=True), Resume.STOPPED_WITH_DELIVERY),
    (_resume(status=S.TODO, has_deliverable=True, run_flagged_stopped=True), Resume.STOPPED_WITH_DELIVERY),
    (_resume(phase=TaskPhase.REVIEW, status=S.BLOCKED, has_deliverable=True,
             decision_reason="reviewer-completed-without-verdict"), Resume.CLEAR_REVIEWER_STOP),
    (_resume(phase=TaskPhase.REVIEW, status=S.BLOCKED, has_deliverable=True,
             decision_reason="reviewer-completed-without-verdict", active_runs=True), Resume.RUN_NOT_TERMINAL),
    (_resume(phase=TaskPhase.REVIEW, status=S.IN_REVIEW, has_deliverable=True), Resume.PROCEED),
])
def test_a_resumed_issue_is_classified_from_its_facts(facts, expected):
    assert classify_resume(facts) is expected

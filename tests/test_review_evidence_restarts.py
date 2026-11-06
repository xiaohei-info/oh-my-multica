"""PLAN G3/O8: an invalid Reviewer report is re-dispatched on the same subject a bounded number of times."""
from _support.samples import work_item
from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind
from omac.core.workitem import WorkItemStatus
from omac.pipeline.decide_task import Fact
from omac.pipeline.tasks import _TaskRun


def _run_with_an_invalid_verdict():
    """A review stage whose Reviewer keeps filing a `pass` without any report (the evidence gate rejects it every time)."""
    run = object.__new__(_TaskRun)
    run.kind, run.item_id, run.round_index, run.review_limit = TaskKind.PLAN, "item-1", 1, 3
    run.contract = Contract(objective="x", acceptance=["y"], non_goals=["z"])
    run.subject_digest, run.invalid_restarts, run.invalid_evidence_restarts = "digest-1", {}, 2
    run.reviewed = work_item(status=WorkItemStatus.IN_REVIEW, review_verdict="pass", review_subject_digest="digest-1")
    return run


def test_an_invalid_verdict_is_restarted_at_most_twice_on_one_subject_then_the_stage_stops():
    run = _run_with_an_invalid_verdict()

    facts = [run._at_review_check()[0] for _ in range(4)]

    assert facts == [Fact.EVIDENCE_INVALID, Fact.EVIDENCE_INVALID, Fact.EVIDENCE_INVALID_EXHAUSTED,
                     Fact.EVIDENCE_INVALID_EXHAUSTED]


def test_the_stop_names_the_reason_and_the_command_that_releases_it():
    run = _run_with_an_invalid_verdict()
    for _ in range(2):
        run._at_review_check()

    fact, obs = run._at_review_check()

    assert fact is Fact.EVIDENCE_INVALID_EXHAUSTED
    assert obs.decision["reason_code"] == "review-evidence-invalid" and obs.decision["gate"] == "review-evidence"
    assert obs.decision["next_action"].startswith("omac plan resume --plan-id ")
    assert obs.decision["errors"]


def test_a_new_subject_has_its_own_restart_budget():
    run = _run_with_an_invalid_verdict()
    for _ in range(3):
        run._at_review_check()

    run.subject_digest = "digest-2"          # the author redelivered: a different review subject

    assert run._at_review_check()[0] is Fact.EVIDENCE_INVALID

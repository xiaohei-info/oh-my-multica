"""Named issue transitions: order and content of the effects each submit, stop and rework command shares."""
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus as S
from omac.pipeline.decide_effects import ResetReview, UpdateMetadata, UpdateStatus
from omac.pipeline.decide_transitions import (
    advance_to_review, block_if_needed, block_in_phase, block_with_decision, finish_clearing_decision,
    finish_with_delivery, rework_to_producer,
)


def test_a_decision_is_written_before_the_status_that_makes_it_visible():
    assert block_with_decision("i", {"d": 1}) == (
        UpdateMetadata("i", {"decision_required": {"d": 1}}), UpdateStatus("i", S.BLOCKED))
    assert block_in_phase("i", {"d": 1}, TaskPhase.AUTHORING)[0].fields == {
        "decision_required": {"d": 1}, "phase": TaskPhase.AUTHORING}


def test_blocking_is_idempotent():
    assert block_if_needed("i", S.BLOCKED) == ()
    assert block_if_needed("i", S.IN_REVIEW) == (UpdateStatus("i", S.BLOCKED),)


def test_a_new_delivery_in_review_clears_the_old_verdict_and_decision():
    metadata, status = advance_to_review("i", {"deliverable": "x"})
    assert metadata.fields["deliverable"] == "x" and metadata.fields["phase"] is TaskPhase.REVIEW
    assert {k: metadata.fields[k] for k in ("review_verdict", "review_subject_digest", "decision_required",
                                            "review_nits_acceptance")} == {
        "review_verdict": "", "review_subject_digest": "", "decision_required": {}, "review_nits_acceptance": {}}
    assert status == UpdateStatus("i", S.IN_REVIEW)


def test_delivery_fields_win_over_the_cleared_defaults():
    assert advance_to_review("i", {"phase": TaskPhase.CONFIRMATION})[0].fields["phase"] is TaskPhase.CONFIRMATION


def test_terminal_transitions():
    assert finish_with_delivery("i", {"deliverable": "r"}) == (
        UpdateMetadata("i", {"deliverable": "r"}), UpdateStatus("i", S.DONE))
    assert finish_clearing_decision("i") == (
        UpdateMetadata("i", {"decision_required": {}}), UpdateStatus("i", S.DONE))
    assert rework_to_producer("i") == (ResetReview("i"), UpdateStatus("i", S.TODO))

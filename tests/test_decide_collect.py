"""Result-collection decisions as pure facts -> action tables."""
import pytest

from omac.pipeline.decide_review import VerdictObservation, VerdictPath as V, decide_verdict_path
from omac.pipeline.decide_worker import (
    AfterCi as A, AfterCiObservation, NoSubmit as N, NoSubmitObservation, decide_after_ci, decide_worker_no_submit,
)


@pytest.mark.parametrize("has_handoff,limit,consumed,expected", [
    (False, 2, 0, N.REQUIRE_RETRY_INTENT),
    (False, 0, 0, N.REQUIRE_RETRY_INTENT),   # no handoff outranks an exhausted budget
    (True, 0, 0, N.BUDGET_EXHAUSTED),        # limit 0 means no automatic retry at all
    (True, 2, 2, N.BUDGET_EXHAUSTED),
    (True, 2, 3, N.BUDGET_EXHAUSTED),
    (True, 2, 1, N.RETRY),
])
def test_worker_no_submit(has_handoff, limit, consumed, expected):
    assert decide_worker_no_submit(NoSubmitObservation(has_handoff, limit, consumed)) is expected


@pytest.mark.parametrize("ci,passed,reviewer,expected", [
    ("bounce", True, True, A.BOUNCE),
    ("blocked", True, True, A.BLOCK),
    ("pass", True, True, A.MERGE_PASSED_REVIEW),
    ("pass", True, False, A.MERGE_PASSED_REVIEW),
    ("pass", False, True, A.QUEUE_REVIEW),
    ("pass", False, False, A.CONFIRM_MERGE),
])
def test_after_ci(ci, passed, reviewer, expected):
    assert decide_after_ci(AfterCiObservation(ci, passed, reviewer)) is expected


@pytest.mark.parametrize("verdict,errors,expected", [
    ("reject", False, V.REJECT_CHECKS),
    ("pass", False, V.MERGE),
    ("pass-with-nits", False, V.NITS_CHECK),
    ("reject", True, V.REWORK),
    ("pass", True, V.REWORK),                # a pass without valid evidence is reworked, never merged
    ("pass-with-nits", True, V.REWORK),
])
def test_verdict_path(verdict, errors, expected):
    assert decide_verdict_path(VerdictObservation(verdict, errors)) is expected

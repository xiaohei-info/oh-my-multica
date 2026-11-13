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
    ("pending", True, True, A.WAIT),     # a check still running outranks a passing verdict: nothing moves yet
    ("pass", True, True, A.MERGE_PASSED_REVIEW),
    ("pass", True, False, A.MERGE_PASSED_REVIEW),
    ("pass", False, True, A.QUEUE_REVIEW),
    ("pass", False, False, A.CONFIRM_MERGE),
])
def test_after_ci(ci, passed, reviewer, expected):
    assert decide_after_ci(AfterCiObservation(ci, passed, reviewer)) is expected


@pytest.mark.parametrize("verdict,errors,nits_accepted,expected", [
    ("reject", False, True, V.REJECT_CHECKS),
    ("reject", False, False, V.REJECT_CHECKS),
    ("pass", False, True, V.MERGE),
    ("pass", False, False, V.MERGE),
    ("pass-with-nits", False, True, V.MERGE),            # review.node_nits=accept: nits do not stop the node
    ("pass-with-nits", False, False, V.NITS_CHECK),      # review.node_nits=decide: operator acceptance first
    ("reject", True, True, V.REWORK),
    ("pass", True, True, V.REWORK),                      # a pass without valid evidence is reworked, never merged
    ("pass-with-nits", True, True, V.REWORK),
    ("pass-with-nits", True, False, V.REWORK),
])
def test_verdict_path(verdict, errors, nits_accepted, expected):
    assert decide_verdict_path(VerdictObservation(verdict, errors, nits_accepted)) is expected

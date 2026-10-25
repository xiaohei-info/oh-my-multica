"""The merge state machine as pure facts -> action tables; no platform, no store, no clock."""
import pytest

from omac.core.workitem import PullRequestState as S
from omac.pipeline.decide_merge import (
    MergeAfterRequest as After, MergePrecheck as Pre, MergeStep as Step,
    decide_after_merge_request, decide_merge_observed, decide_merge_precheck,
    merge_bounce_attempt, merge_request_state_is_valid,
)


@pytest.mark.parametrize("state,has_pr,expected", [
    ("bounce_pending:2", True, Pre.RESUME_BOUNCE),
    ("bounce_pending:2", False, Pre.RESUME_BOUNCE),   # a half-done bounce is finished before anything else
    (None, False, Pre.BLOCK_NO_PULL_REQUEST),
    ("intent", False, Pre.BLOCK_NO_PULL_REQUEST),
    ("garbage", True, Pre.BLOCK_INVALID_MARKER),
    ("bounce_pending:0", True, Pre.BLOCK_INVALID_MARKER),
    (None, True, Pre.OBSERVE),
    ("intent", True, Pre.OBSERVE),
    ("requested", True, Pre.OBSERVE),
])
def test_precheck(state, has_pr, expected):
    assert decide_merge_precheck(request_state=state, has_pull_request=has_pr) is expected


@pytest.mark.parametrize("state,pr,expected", [
    (None, S.MERGED, Step.SETTLE),
    ("intent", S.MERGED, Step.SETTLE),
    ("requested", S.MERGED, Step.SETTLE),
    (None, S.PENDING, Step.RECORD_PENDING),
    ("intent", S.PENDING, Step.RECORD_PENDING),
    (None, S.CLOSED_UNMERGED, Step.SETTLE),
    (None, S.UNKNOWN, Step.SETTLE),
    ("intent", S.UNKNOWN, Step.SETTLE),
    (None, S.OPEN, Step.REQUEST),
    ("intent", S.OPEN, Step.BLOCK_PRIOR_INTENT),
    ("requested", S.OPEN, Step.STAY_PENDING),
])
def test_observed_pull_request(state, pr, expected):
    assert decide_merge_observed(request_state=state, pr_state=pr) is expected


@pytest.mark.parametrize("state", ["intent", "requested"])
@pytest.mark.parametrize("pr", list(S))
def test_a_marked_request_is_never_sent_twice(state, pr):
    assert decide_merge_observed(request_state=state, pr_state=pr) is not Step.REQUEST


@pytest.mark.parametrize("timed_out,succeeded,pr,expected", [
    (False, True, S.MERGED, After.SETTLE),
    (True, False, S.MERGED, After.SETTLE),
    (False, True, S.PENDING, After.SETTLE),
    (True, False, S.OPEN, After.BLOCK_TIMED_OUT_OPEN),   # unknown outcome: never replay
    (False, True, S.OPEN, After.PENDING),
    (False, False, S.OPEN, After.BOUNCE_OR_BLOCK),
    (False, False, S.CLOSED_UNMERGED, After.SETTLE),
    (True, False, S.UNKNOWN, After.SETTLE),
])
def test_after_merge_request(timed_out, succeeded, pr, expected):
    assert decide_after_merge_request(timed_out=timed_out, succeeded=succeeded, pr_state=pr) is expected


def test_marker_parsing():
    assert merge_bounce_attempt("bounce_pending:3") == 3
    assert merge_bounce_attempt("bounce_pending:x") is None
    assert merge_bounce_attempt(None) is None
    assert merge_request_state_is_valid(None) and merge_request_state_is_valid("bounce_pending:1")
    assert not merge_request_state_is_valid("done")

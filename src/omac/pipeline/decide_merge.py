"""Pure decisions of the merge delivery state machine: facts in, a named action out.

No I/O and no platform access (enforced by tests/test_architecture.py). The shell in
``delivery.run_merge_delivery`` observes the platform, calls these functions, and applies the action.
The durable ``merge_request_state`` marker is what makes a restart safe:

  None --request--> "intent" --command returned--> "requested" --observed MERGED--> done

An "intent" or "requested" marker is never followed by a second merge request.
"""
from __future__ import annotations

from enum import Enum

from ..core.workitem import PullRequestState

VALID_MERGE_REQUEST_STATES = {None, "intent", "requested"}
MERGE_BOUNCE_PENDING_PREFIX = "bounce_pending:"


def merge_bounce_attempt(state: str | None) -> int | None:
    """解析 durable merge bounce marker 中的绝对回退次数。"""
    if not isinstance(state, str) or not state.startswith(MERGE_BOUNCE_PENDING_PREFIX):
        return None
    try:
        attempt = int(state[len(MERGE_BOUNCE_PENDING_PREFIX):])
    except ValueError:
        return None
    return attempt if attempt > 0 else None


def merge_request_state_is_valid(state: str | None) -> bool:
    return state in VALID_MERGE_REQUEST_STATES or merge_bounce_attempt(state) is not None


class MergePrecheck(Enum):
    RESUME_BOUNCE = "resume-bounce"          # a durable bounce is half done: finish it first
    BLOCK_NO_PULL_REQUEST = "no-pull-request"
    BLOCK_INVALID_MARKER = "invalid-marker"
    OBSERVE = "observe"                      # read the remote pull request, then decide again


def decide_merge_precheck(*, request_state: str | None, has_pull_request: bool) -> MergePrecheck:
    if merge_bounce_attempt(request_state) is not None:
        return MergePrecheck.RESUME_BOUNCE
    if not has_pull_request:
        return MergePrecheck.BLOCK_NO_PULL_REQUEST
    if not merge_request_state_is_valid(request_state):
        return MergePrecheck.BLOCK_INVALID_MARKER
    return MergePrecheck.OBSERVE


class MergeStep(Enum):
    SETTLE = "settle"                  # the observation itself decides: merged, closed, unknown...
    RECORD_PENDING = "record-pending"  # queued remotely (auto-merge): remember "requested", keep waiting
    BLOCK_PRIOR_INTENT = "block-prior-intent"  # a request may already have been sent: never send another
    STAY_PENDING = "stay-pending"      # already requested: only observe
    REQUEST = "request"                # write the intent marker, then send exactly one request


def decide_merge_observed(*, request_state: str | None, pr_state: PullRequestState) -> MergeStep:
    if pr_state == PullRequestState.MERGED:
        return MergeStep.SETTLE
    if pr_state == PullRequestState.PENDING:
        return MergeStep.RECORD_PENDING
    if pr_state != PullRequestState.OPEN:
        return MergeStep.SETTLE
    if request_state == "intent":
        return MergeStep.BLOCK_PRIOR_INTENT
    if request_state == "requested":
        return MergeStep.STAY_PENDING
    return MergeStep.REQUEST


class MergeAfterRequest(Enum):
    SETTLE = "settle"                    # merged / queued / closed / unknown: the observation decides
    BLOCK_TIMED_OUT_OPEN = "block-timed-out-open"  # the command timed out and the PR is still open: outcome unknown
    PENDING = "pending"                  # the command succeeded but the PR is not merged yet: observe next tick
    BOUNCE_OR_BLOCK = "bounce-or-block"  # the command failed: bounded return to the worker


def decide_after_merge_request(
    *, timed_out: bool, succeeded: bool, pr_state: PullRequestState,
) -> MergeAfterRequest:
    if pr_state in {PullRequestState.MERGED, PullRequestState.PENDING}:
        return MergeAfterRequest.SETTLE
    if pr_state == PullRequestState.OPEN:
        if timed_out:
            return MergeAfterRequest.BLOCK_TIMED_OUT_OPEN
        if succeeded:
            return MergeAfterRequest.PENDING
        return MergeAfterRequest.BOUNCE_OR_BLOCK
    return MergeAfterRequest.SETTLE

"""GitHubCodeHost: PR facts come from `gh`, configured commands run in the adapter, unknown stays unknown."""
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omac.core.workitem import PullRequestReadinessFailure, PullRequestState
from omac.engines.github import PR_VIEW_FIELDS, GitHubCodeHost


@pytest.mark.parametrize(
    ("payload", "expected_state", "expected_merged_at"),
    [
        ({"state": "MERGED", "mergedAt": "2026-07-26T08:45:00Z"},
         PullRequestState.MERGED, "2026-07-26T08:45:00Z"),
        ({"state": "OPEN", "mergedAt": None, "mergeStateStatus": "CLEAN"},
         PullRequestState.OPEN, None),
        ({"state": "OPEN", "mergedAt": None,
          "autoMergeRequest": {"enabledAt": "2026-07-26T08:40:00Z"},
          "mergeStateStatus": "CLEAN"},
         getattr(PullRequestState, "PENDING", "pending"), None),
        ({"state": "OPEN", "mergedAt": None,
          "autoMergeRequest": None, "mergeStateStatus": "QUEUED"},
         getattr(PullRequestState, "PENDING", "pending"), None),
        ({"state": "CLOSED", "mergedAt": None},
         PullRequestState.CLOSED_UNMERGED, None),
    ],
)
def test_github_observe_pull_request_classifies_remote_states(
    monkeypatch, payload, expected_state, expected_merged_at,
):
    host = GitHubCodeHost()
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("omac.engines.github.subprocess.run", run)

    observation = host.observe_pull_request("https://example.com/pr/1")

    assert observation.state is expected_state
    assert observation.merged_at == expected_merged_at
    assert "autoMergeRequest" in calls[0][0][-1]
    assert "mergeStateStatus" in calls[0][0][-1]
    assert "isInMergeQueue" not in calls[0][0][-1]


@pytest.mark.live  # needs the real gh CLI (unit tests put a no-op gh first on PATH)
def test_github_pr_view_fields_are_supported_by_local_gh():
    if shutil.which("gh") is None:
        pytest.skip("gh is not installed")

    result = subprocess.run(
        ["gh", "pr", "view", "--help"], capture_output=True, text=True,
        check=False,
    )
    assert result.returncode == 0
    fields = PR_VIEW_FIELDS
    for field in fields.split(","):
        assert field in result.stdout


@pytest.mark.parametrize(
    "run_result",
    [
        SimpleNamespace(returncode=1, stdout="", stderr="authentication failed"),
        subprocess.TimeoutExpired(cmd="gh", timeout=30),
        SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    ],
)
def test_github_observe_pull_request_fails_closed_for_unreadable_remote(
    monkeypatch, run_result,
):
    host = GitHubCodeHost()

    def run(*args, **kwargs):
        if isinstance(run_result, BaseException):
            raise run_result
        return run_result

    monkeypatch.setattr("omac.engines.github.subprocess.run", run)

    observation = host.observe_pull_request("https://example.com/pr/1")

    assert observation.state is PullRequestState.UNKNOWN


def test_github_pr_check_and_readiness_stay_in_adapter(monkeypatch):
    host = GitHubCodeHost()
    calls = []
    responses = iter([
        SimpleNamespace(returncode=0, stdout="checks ok", stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({
            "isDraft": False, "state": "OPEN", "headRefOid": "head-1"}),
            stderr=""),
    ])

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr("omac.engines.github.subprocess.run", run)

    check = host.check_pull_request(
        "https://github.com/acme/repo/pull/1", "gh pr checks {pr_url}", 30)
    readiness = host.read_pull_request_readiness(
        "https://github.com/acme/repo/pull/1")

    assert check.succeeded is True
    assert readiness.is_draft is False
    assert readiness.state == "OPEN"
    assert readiness.head_sha == "head-1"
    assert calls[0][0] == "gh pr checks https://github.com/acme/repo/pull/1"
    assert calls[1][0][-1] == "isDraft,state,headRefOid"


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"isDraft": None, "state": "OPEN", "headRefOid": "head"},
     {"isDraft": False},
     {"isDraft": "false", "state": "OPEN", "headRefOid": "head"},
     {"isDraft": False, "state": "OPEN"}],
)
def test_github_readiness_malformed_payload_fails_closed(monkeypatch, payload):
    host = GitHubCodeHost()
    monkeypatch.setattr(
        "omac.engines.github.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""),
    )

    result = host.read_pull_request_readiness("https://github.com/acme/repo/pull/1")

    assert isinstance(result, PullRequestReadinessFailure)

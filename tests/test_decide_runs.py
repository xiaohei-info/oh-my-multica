"""Which Run facts count as a causal attempt, and which provider failures are worth one more Run. No platform."""
from dataclasses import replace
from uuid import uuid4

import pytest

from omac.core.taskmeta import WORKER_HANDOFF_KEY, WorkerHandoffIntent
from omac.core.workitem import AgentRunObservation
from omac.engines.metadata_policy import assert_metadata_write_allowed
from omac.errors import PlatformError
from omac.pipeline import decide_runs


def test_direct_run_observation_follows_one_platform_retry_chain():
    observed = decide_runs.observe_direct_run_attempt(
        [
            AgentRunObservation(
                id="run-3", kind="direct", status="completed",
                agent_id="agent-1", retry_of_run_id="run-2",
            ),
            AgentRunObservation(
                id="run-2", kind="direct", status="failed",
                agent_id="agent-1", retry_of_run_id="run-1",
                error="read timeout while waiting for provider response",
            ),
            AgentRunObservation(
                id="run-1", kind="direct", status="failed",
                agent_id="agent-1",
                error="read timeout while waiting for provider response",
            ),
        ],
        "agent-1",
        target_run_id="run-1",
    )

    assert observed.state == "terminal"
    assert observed.target_run_id == "run-3"
    assert observed.terminal is not None
    assert observed.terminal.run.id == "run-3"


def test_direct_run_observation_rejects_retry_chain_forks():
    observed = decide_runs.observe_direct_run_attempt(
        [
            AgentRunObservation(
                id="run-3a", kind="direct", status="completed",
                agent_id="agent-1", retry_of_run_id="run-2",
            ),
            AgentRunObservation(
                id="run-3b", kind="direct", status="completed",
                agent_id="agent-1", retry_of_run_id="run-2",
            ),
            AgentRunObservation(
                id="run-2", kind="direct", status="failed",
                agent_id="agent-1", retry_of_run_id="run-1",
                error="read timeout while waiting for provider response",
            ),
            AgentRunObservation(
                id="run-1", kind="direct", status="failed",
                agent_id="agent-1",
                error="read timeout while waiting for provider response",
            ),
        ],
        "agent-1",
        target_run_id="run-1",
    )

    assert observed.state == "unexpected"
    assert observed.detail == "ambiguous target Run"


@pytest.mark.parametrize("error", [
    "Selected model is at capacity. Please try a different model.",
    "Our servers are currently overloaded",
    "Hermes provider error: Our servers are currently overloaded. Please try again later.",
    "provider error: HTTP 429 Too Many Requests",
    "transport status: HTTP 502 Bad Gateway",
    "runtime status: 503 Service Unavailable",
    "Hermes provider error: HTTP 504 Gateway Timeout",
    (
        "hermes provider error: ⚠️  API call failed (attempt 3/8): "
        "InternalServerError [HTTP 503]"
    ),
    'hermes provider error: HTTP 502: {"error":"bad gateway"}',
    'hermes provider error: HTTP 524: {"error":"upstream timed out"}',
    "connection timeout while opening provider stream",
    "read timeout while waiting for provider response",
    "provider error: connection timeout",
    "runtime status: read timeout",
    "Hermes provider error: connect timeout",
])
def test_transient_runtime_failure_allowlist(error):
    assert decide_runs.is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


@pytest.mark.parametrize("error", [
    (
        "unexpected status 503 Service Unavailable: system cpu overloaded "
        "(current: 94.9%, threshold: 90%), url: "
        "https://api.example.test/v1/responses, "
        "cf-ray: 0123456789abcdef"
    ),
    (
        "unexpected status 503 Service Unavailable: system cpu overloaded "
        "(current: 97.8%, threshold: 90%), url: "
        "https://api.example.test/v1/responses, "
        "cf-ray: 0123456789abcdef"
    ),
    (
        "unexpected status 503 Service Unavailable: system cpu overloaded "
        "(current: 93.1%, threshold: 90%), url: "
        "https://api.example.test/v1/responses, "
        "cf-ray: 0123456789abcdef"
    ),
    (
        "unexpected status 429 Too Many Requests: provider rate limited, "
        "url: https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 502 Bad Gateway: upstream unavailable, url: "
        "http://api.example.test/v1/responses"
    ),
    (
        "unexpected status 504 Gateway Timeout: upstream timed out, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 524 A Timeout Occurred: origin timed out, url: "
        "https://api.example.test/v1/responses"
    ),
])
def test_transient_runtime_failure_allows_codex_http_status_wrapper(error):
    assert decide_runs.is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


@pytest.mark.parametrize("error", [
    (
        "unexpected status 401 Unauthorized: missing authentication, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 403 Forbidden: access denied, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 500 Internal Server Error: unknown failure, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 521 Web Server Is Down: origin unavailable, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 503 Bad Gateway: mismatched reason, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 502 Service Unavailable: mismatched reason, url: "
        "https://api.example.test/v1/responses"
    ),
    "unexpected status 503 Service Unavailable: system cpu overloaded",
    (
        "unexpected status 503 Service Unavailable: system cpu overloaded, "
        "url: ftp://api.example.test/v1/responses"
    ),
    (
        "unexpected status 503 Service Unavailable: system cpu overloaded, "
        "url: api.example.test/v1/responses"
    ),
    (
        "request failed: unexpected status 503 Service Unavailable: system "
        "cpu overloaded, url: https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 400 Bad Request: invalid_request business body "
        "mentions 503 Service Unavailable, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 400 Bad Request: response missing reasoning_text "
        "after a body mentioning 503, url: "
        "https://api.example.test/v1/responses"
    ),
    (
        "unexpected status 400 Bad Request: response missing "
        "reasoning_content after a body mentioning 503, url: "
        "https://api.example.test/v1/responses"
    ),
])
def test_transient_runtime_failure_rejects_non_codex_http_status_wrapper(error):
    assert not decide_runs.is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


@pytest.mark.parametrize("error", [
    "HTTP 401 Missing Authentication header",
    "HTTP 403 Forbidden",
    'Hermes provider error: HTTP 401: {"error":"missing authentication"}',
    'Hermes provider error: HTTP 403: {"error":"forbidden"}',
    'Hermes provider error: HTTP 500: {"error":"unknown server error"}',
    (
        'Hermes provider error: HTTP 400: {"type":"invalid_request_error",'
        '"message":"expected HTTP 503"}'
    ),
    "Hermes provider error: response missing reasoning_text",
    "Hermes provider error: response missing reasoning_content",
    "Hermes provider error: invalid_request mentions HTTP 503",
    "quota exhausted for this account",
    "billing credits exhausted",
    "model does not exist",
    "invalid model identifier",
    "request rejected by network security policy",
    "request refused by safety policy",
    "business validation failed",
    "acceptance failure: required behavior is missing",
    "unknown provider failure",
    "worker exited without submitting",
    "request rejected by network security policy because the prompt contains the phrase read timeout",
    "quota exhausted after an upstream connection timeout",
    "model does not exist; request body mentioned HTTP 503 Service Unavailable",
    "HTTP 401 Missing Authentication header; diagnostic text includes 429",
    "business validation failed: the acceptance case expects a read timeout",
    "unknown provider failure while documenting overloaded behavior",
    "documentation example: provider overloaded",
    "test assertion expected HTTP 503",
    "review evidence quoted selected model is at capacity. Please try a different model.",
    "API docs describe 429 responses",
    "business text mentions provider overloaded as an example",
    "business output documents that the provider is overloaded",
    "test assertion expected HTTP 503 Service Unavailable from the provider",
    "review evidence quotes: Selected model is at capacity. Please try a different model.",
    "provider rejected the prompt because it discusses overloaded infrastructure",
    "documentation for the HTTP API mentions 429 responses",
])
def test_transient_runtime_failure_rejects_non_allowlisted_errors(error):
    assert not decide_runs.is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


@pytest.mark.parametrize("error", [
    "429",
    "503",
    "operation overloaded",
    "read timeout",
    "connection timeout",
])
def test_transient_runtime_failure_requires_provider_transport_context(error):
    assert not decide_runs.is_retryable_transient_run_failure(AgentRunObservation(
        id="run-1", kind="direct", status="failed", error=error))


def test_bounded_direct_run_baseline_keeps_small_lists_uncapped():
    runs = baseline_history_runs(5, "agent-worker", "agent-reviewer")
    ids, cutoff = decide_runs.bounded_direct_run_baseline(
        runs + [AgentRunObservation(id="run-child", kind="child", status="completed")])
    assert ids == tuple(sorted(run.id for run in runs))
    assert cutoff is None


def test_bounded_direct_run_baseline_caps_to_latest_20_with_cutoff():
    runs = baseline_history_runs(45, "agent-worker", "agent-reviewer")
    ids, cutoff = decide_runs.bounded_direct_run_baseline(runs)
    assert len(ids) == 20
    assert ids == tuple(sorted(run.id for run in runs[-20:]))
    assert cutoff == runs[-1].created_at


def test_bounded_direct_run_baseline_fails_closed_without_creation_time():
    runs = baseline_history_runs(25, "agent-worker", "agent-reviewer")
    runs[0] = replace(runs[0], created_at=None)
    with pytest.raises(PlatformError):
        decide_runs.bounded_direct_run_baseline(runs)


def test_bounded_direct_run_baseline_gate_drops_only_pre_cutoff_runs():
    runs = baseline_history_runs(45, "agent-worker", "agent-reviewer")
    # 门控 cutoff 覆盖全部被裁 Run → 安全封顶,沿用调用方自己的门控。
    ids, cutoff = decide_runs.bounded_direct_run_baseline(
        runs, gate_cutoff_created_at=runs[24].created_at)
    assert ids == tuple(sorted(run.id for run in runs[-20:]))
    assert cutoff is None
    # 被裁 Run 晚于门控 cutoff → 无法安全封顶,fail-closed。
    with pytest.raises(PlatformError):
        decide_runs.bounded_direct_run_baseline(
            runs, gate_cutoff_created_at=runs[10].created_at)


# ---- a handoff baseline that is capped to fit the platform's metadata limit -------------------------------------------------

def baseline_history_runs(count, worker_id, reviewer_id):
    """Alternating Worker/Reviewer terminal Runs with strictly increasing creation times."""
    return [
        AgentRunObservation(
            id=f"run-hist-{index:03d}", kind="direct", status="completed", agent_id=worker_id if index % 2 == 0 else reviewer_id,
            created_at=f"2026-01-{1 + index // 1440:02d}T{(index // 60) % 24:02d}:{index % 60:02d}:00Z")
        for index in range(count)
    ]


WORKER, REVIEWER = "agent-worker", "agent-reviewer"
CURRENT = AgentRunObservation(id="run-current", kind="direct", status="failed", agent_id=WORKER, created_at="2026-01-03T00:00:00Z",
                              error="provider error: HTTP 503 Service Unavailable")


def capped_handoff():
    """40 earlier Runs of two agents, the latest 20 kept as the baseline, and one current failing Run after the cutoff."""
    history = baseline_history_runs(40, WORKER, REVIEWER)
    retained, cutoff = decide_runs.bounded_direct_run_baseline(history)
    return history, retained, cutoff


def test_runs_dropped_from_a_capped_baseline_are_still_inside_it_by_time():
    history, retained, cutoff = capped_handoff()
    runs = history + [CURRENT]

    attempt = decide_runs.observe_direct_run_attempt(
        runs, WORKER, baseline_direct_run_ids=retained, cutoff_created_at=cutoff, target_run_id="run-current")

    assert len(retained) == 20 and attempt.state == "terminal" and attempt.terminal.outcome == "transient-failure"
    assert not decide_runs.foreign_unbaselined_runs(runs, WORKER, retained, decide_runs.parse_platform_time(cutoff))


def test_a_stranger_after_the_cutoff_still_breaks_a_capped_baseline():
    history, retained, cutoff = capped_handoff()
    stranger = AgentRunObservation(id="run-stranger", kind="direct", status="completed", agent_id=REVIEWER, created_at="2026-01-03T00:30:00Z")

    found = decide_runs.foreign_unbaselined_runs(history + [CURRENT, stranger], WORKER, retained, decide_runs.parse_platform_time(cutoff))

    assert [run.id for run in found] == ["run-stranger"]


def test_the_baseline_after_a_retry_is_capped_again_and_advances_its_cutoff():
    history, _retained, _cutoff = capped_handoff()

    ids, cutoff = decide_runs.bounded_direct_run_baseline(history + [CURRENT])

    assert len(ids) <= 20 and "run-current" in ids and cutoff == CURRENT.created_at


def test_a_capped_handoff_fits_the_platforms_metadata_limit():
    ids = tuple(sorted(str(uuid4()) for _ in range(20)))
    intent = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="alice", gate="review", source_review_subject_digest="s" * 64,
        source_review_round=9, target_review_bounce=9, generation="handoff-" + "g" * 16, target_agent_id=str(uuid4()),
        baseline_direct_run_ids=ids, baseline_cutoff_created_at="2026-01-03T00:00:00Z", baseline_verification_attachment_id=str(uuid4()),
        baseline_pr_head_sha="h" * 40, target_run_id=str(uuid4()), target_worker_bounce=9, source_review_verdict="reject")

    assert_metadata_write_allowed(WORKER_HANDOFF_KEY, intent.as_dict())
    assert len(intent.baseline_direct_run_ids) == 20 and replace(intent, target_run_id=None).baseline_direct_run_ids == ids

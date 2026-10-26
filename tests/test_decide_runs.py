"""Which Run facts count as a causal attempt, and which provider failures are worth one more Run. No platform."""
import pytest

from omac.core.workitem import AgentRunObservation
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

"""Pure facts about agent Runs: what the Runs of one work item say about one causal dispatch.

No I/O (enforced by tests/test_architecture.py). The Loop reads ``runtime.list_runs`` and the persisted baseline,
these functions classify them, and the decisions in ``decide_reviewer`` / ``decide_worker`` build on the result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

from ..core.workitem import AgentRunObservation
from ..errors import PlatformError



# Multica 限制单 issue metadata 为 8KB;direct Run 基线封顶保留最近 N 个,
# 避免高 bounce 节点的 handoff 写入确定性超限(gap #13)。
BASELINE_MAX_DIRECT_RUN_IDS = 20


TRANSIENT_RUNTIME_MAX_RUNS = 2


@dataclass(frozen=True)
class RunFailure:
    run: AgentRunObservation
    classification: str
    consecutive_runs: int

    @property
    def exhausted(self) -> bool:
        return (
            self.classification == "transient"
            and self.consecutive_runs >= TRANSIENT_RUNTIME_MAX_RUNS
        )


@dataclass(frozen=True)
class ExpectedTerminalRun:
    """Latest terminal direct Run for one causally bounded agent dispatch."""

    run: AgentRunObservation
    outcome: str
    consecutive_runs: int


@dataclass(frozen=True)
class DirectRunAttempt:
    state: str
    target_run_id: str | None = None
    terminal: ExpectedTerminalRun | None = None
    detail: str = ""


# Cloudflare 524 is an origin response timeout, so it shares 504 semantics.
_RETRYABLE_RUN_HTTP_STATUS_PATTERN = (
    r"(?:429(?:\s+too many requests)?|502(?:\s+bad gateway)?|"
    r"503(?:\s+service unavailable)?|504(?:\s+gateway timeout)?|"
    r"524(?:\s+a timeout occurred)?)"
)


# Codex transport errors spell out both the status and its canonical reason.
_RETRYABLE_CODEX_HTTP_STATUS_PATTERN = (
    r"(?:429 too many requests|502 bad gateway|503 service unavailable|"
    r"504 gateway timeout|524 a timeout occurred)"
)


_RETRYABLE_CODEX_PROVIDER_URL_PATTERN = (
    r"https?://"
    r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?::\d{1,5})?(?:/[^\s,\r\n]*)?"
)


_RETRYABLE_RUN_ERROR_SIGNATURES = (
    re.compile(
        r"unexpected status "
        + _RETRYABLE_CODEX_HTTP_STATUS_PATTERN
        + r":[^\r\n]+?,\s*url:\s*"
        + _RETRYABLE_CODEX_PROVIDER_URL_PATTERN
        + r"(?:,\s*[^\r\n]+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"selected model is at capacity\. please try a different model\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:hermes provider error:\s*)?our servers are currently overloaded"
        r"(?:\.|\. please try again later\.?)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:provider|runtime|transport) (?:error|status)|"
        r"hermes provider error):\s*(?:http\s+)?"
        + _RETRYABLE_RUN_HTTP_STATUS_PATTERN
        + r"(?:\.?|:\s*\{[\s\S]*\})",
        re.IGNORECASE,
    ),
    re.compile(
        r"hermes provider error:\s*(?:⚠️?\s*)?api call failed\s*"
        r"\(attempt\s+[1-9]\d*/[1-9]\d*\):\s*"
        r"[a-z][a-z0-9_]*error\s*\[http\s+"
        + _RETRYABLE_RUN_HTTP_STATUS_PATTERN
        + r"\]\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:provider|runtime|transport) (?:error|status)|"
        r"hermes provider error):\s*"
        r"(?:connection timeout(?: while opening provider stream)?|"
        r"connect timeout|read timeout(?: while waiting for provider response)?)\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:provider|runtime|transport) "
        r"(?:connection|connect|read) timeout\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"connection timeout while opening provider stream\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"read timeout while waiting for provider response\.?",
        re.IGNORECASE,
    ),
)


def is_retryable_transient_run_failure(run: AgentRunObservation) -> bool:
    """Classify only explicit provider/transport failures as retryable."""
    if run.status != "failed" or not run.error:
        return False
    error = run.error.strip()
    return any(
        signature.fullmatch(error)
        for signature in _RETRYABLE_RUN_ERROR_SIGNATURES
    )


def ordered_direct_runs(
    runs: List[AgentRunObservation],
) -> List[AgentRunObservation]:
    indexed = [
        (index, run) for index, run in enumerate(runs)
        if run.kind == "direct"
    ]
    indexed.sort(
        key=lambda pair: (
            pair[1].created_at or pair[1].updated_at or "", -pair[0]
        ),
        reverse=True,
    )
    return [run for _index, run in indexed]


def bounded_direct_run_baseline(
    runs: List[AgentRunObservation],
    *,
    gate_cutoff_created_at: str | None = None,
) -> tuple[Tuple[str, ...], str | None]:
    """Cap the direct-Run baseline at the most recent N Runs (gap #13).

    Returns ``(baseline ids, cutoff_created_at)`` keeping the existing
    sorted-by-ID order semantics. Uncapped baselines return ``None`` cutoff so
    consumers keep pure ID-membership semantics. When capped, the returned
    cutoff is the newest retained Run's creation time: consumers treat Runs
    created at or before it as baseline-internal, Runs after it stay
    fail-closed. With ``gate_cutoff_created_at`` the caller already owns a
    consumer-side timestamp gate; then only Runs at or before that gate may be
    dropped, the returned cutoff stays ``None``, and anything unsafe fails
    closed.
    """
    direct = [run for run in runs if run.kind == "direct"]
    if len(direct) <= BASELINE_MAX_DIRECT_RUN_IDS:
        return tuple(sorted(run.id for run in direct)), None
    timed = []
    for run in direct:
        created = parse_platform_time(run.created_at)
        if created is None or created.tzinfo is None:
            raise PlatformError(
                f"Run {run.id} has no usable creation time; cannot bound the "
                "direct Run baseline safely")
        timed.append((created, run))
    timed.sort(key=lambda pair: (pair[0], pair[1].id))
    keep = timed[-BASELINE_MAX_DIRECT_RUN_IDS:]
    dropped = timed[:-BASELINE_MAX_DIRECT_RUN_IDS]
    kept_ids = tuple(sorted(run.id for _created, run in keep))
    if gate_cutoff_created_at is not None:
        gate = parse_platform_time(gate_cutoff_created_at)
        if gate is None or gate.tzinfo is None:
            raise PlatformError(
                "Direct Run baseline cannot be bounded: unusable gate cutoff")
        if any(created > gate for created, _run in dropped):
            raise PlatformError(
                "Direct Run baseline cannot be bounded safely: Runs after "
                "the gate cutoff exceed the baseline limit")
        return kept_ids, None
    return kept_ids, keep[-1][1].created_at


def run_created_within_baseline(
    run: AgentRunObservation, cutoff: datetime | None,
) -> bool:
    """Whether a Run predates the baseline cutoff and is baseline-internal."""
    if cutoff is None:
        return False
    created = parse_platform_time(run.created_at)
    if created is None or created.tzinfo is None:
        return False
    return created <= cutoff


def parse_baseline_cutoff(intent) -> datetime | None:
    """Parse a persisted baseline cutoff or fail closed when unusable."""
    raw = getattr(intent, "baseline_cutoff_created_at", None)
    if not raw:
        return None
    cutoff = parse_platform_time(raw)
    if cutoff is None or cutoff.tzinfo is None:
        raise PlatformError(
            "Direct Run baseline cutoff is unusable; refusing to observe")
    return cutoff


def observe_direct_run_attempt(
    runs: List[AgentRunObservation],
    agent_id: str,
    *,
    baseline_direct_run_ids: Tuple[str, ...] = (),
    cutoff_created_at: str | None = None,
    target_run_id: str | None = None,
    attempt: int = 1,
) -> DirectRunAttempt:
    """Bind and classify one causal direct-Run attempt."""
    baseline = set(baseline_direct_run_ids)
    cutoff = parse_platform_time(cutoff_created_at)
    expected = []
    for run in ordered_direct_runs(runs):
        if run.agent_id != agent_id or run.id in baseline:
            continue
        if cutoff_created_at:
            created = parse_platform_time(run.created_at)
            if created is None or created.tzinfo is None:
                return DirectRunAttempt(
                    "unexpected", detail=f"Run {run.id} has no usable creation time")
            if cutoff is None or cutoff.tzinfo is None:
                return DirectRunAttempt("unexpected", detail="invalid Run cutoff")
            if created <= cutoff:
                continue
        expected.append(run)
    if target_run_id:
        by_id = {run.id: run for run in expected}
        if len(by_id) != len(expected):
            return DirectRunAttempt("unexpected", detail="ambiguous target Run")
        latest = by_id.get(target_run_id)
        if latest is None:
            return DirectRunAttempt("missing", target_run_id)
        target_index = expected.index(latest)
        if target_index:
            chain_ids = {latest.id}
            while True:
                children = [
                    run for run in expected[:target_index]
                    if run.retry_of_run_id == latest.id
                ]
                if not children:
                    break
                if len(children) != 1 or not latest.terminal:
                    return DirectRunAttempt(
                        "unexpected", detail="ambiguous target Run")
                latest = children[0]
                if latest.id in chain_ids:
                    return DirectRunAttempt(
                        "unexpected", detail="ambiguous target Run")
                chain_ids.add(latest.id)
            if (
                len(chain_ids) != target_index + 1
                or expected[0].id != latest.id
            ):
                return DirectRunAttempt(
                    "unexpected", detail="ambiguous target Run")
            target_run_id = latest.id
        if any(run.id != target_run_id and not run.terminal for run in expected):
            return DirectRunAttempt("unexpected", detail="ambiguous target Run")
    else:
        if len(expected) > 1:
            return DirectRunAttempt("unexpected", detail="ambiguous post-baseline Runs")
        if not expected:
            return DirectRunAttempt("missing")
        latest = expected[0]
        target_run_id = latest.id
    if latest.active:
        return DirectRunAttempt("active", target_run_id)
    if not latest.terminal:
        return DirectRunAttempt("missing", target_run_id)
    if latest.status == "failed":
        outcome = (
            "transient-failure"
            if is_retryable_transient_run_failure(latest)
            else "nonretryable-failure"
        )
    else:
        outcome = "finished-without-submit"

    consecutive = 0
    for run in expected:
        if not run.terminal:
            break
        if outcome == "transient-failure":
            matches = is_retryable_transient_run_failure(run)
        elif outcome == "nonretryable-failure":
            matches = (
                run.status == "failed"
                and not is_retryable_transient_run_failure(run)
            )
        else:
            matches = run.status in {"completed", "cancelled"}
        if not matches:
            break
        consecutive += 1
    terminal = ExpectedTerminalRun(latest, outcome, max(consecutive, attempt))
    return DirectRunAttempt("terminal", target_run_id, terminal)


def formal_dispatch_target(
    runs: List[AgentRunObservation],
    observed: DirectRunAttempt,
) -> tuple[AgentRunObservation | None, str | None]:
    """Return the one formal assignment/rerun dispatch shared by recovery paths."""
    if observed.state not in {"active", "terminal"}:
        return None, observed.detail or "no uniquely observable target Run"
    target = next(
        (run for run in runs if run.id == observed.target_run_id),
        None,
    )
    if target is None or not target.formal:
        return None, "post-baseline Run is not a formal assignment/rerun dispatch"
    return target, None


def parse_platform_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


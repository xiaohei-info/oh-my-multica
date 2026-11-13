"""Pure facts about agent Runs: what the Runs of one work item say about one causal dispatch.

No I/O (enforced by tests/test_architecture.py). The Loop reads ``runtime.list_runs`` and the persisted baseline,
these functions classify them, and the decisions in ``decide_reviewer`` / ``decide_worker`` build on the result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from ..core.workitem import AgentRunObservation
from ..errors import PlatformError



# Multica 限制单 issue metadata 为 8KB;direct Run 基线封顶保留最近 N 个,
# 避免高 bounce 节点的 handoff 写入确定性超限(gap #13)。
BASELINE_MAX_DIRECT_RUN_IDS = 20


TRANSIENT_RUNTIME_MAX_RUNS = 2

# Capacity / rate-limit failures are waited out, not counted: the wait is bounded by time (retry.capacity_wait_hours).
DEFAULT_CAPACITY_WAIT_MAX_SECONDS = 12 * 3600
CAPACITY_BACKOFF_BASE_SECONDS = 300
CAPACITY_BACKOFF_MAX_SECONDS = 1800
CAPACITY_RESET_MARGIN_SECONDS = 60

# Run failure classes (the one definition; ``DirectRunAttempt`` outcomes are "<class>-failure").
FAILURE_TRANSIENT = "transient"
FAILURE_CAPACITY = "capacity"
FAILURE_NONRETRYABLE = "nonretryable"


@dataclass(frozen=True)
class RunFailure:
    run: AgentRunObservation
    classification: str
    consecutive_runs: int
    chain_started_at: str | None = None   # creation time of the oldest Run in the consecutive same-class chain

    @property
    def exhausted(self) -> bool:
        # Capacity is bounded by wall-clock (``decide_capacity_wait``), never by a Run count.
        return (
            self.classification == FAILURE_TRANSIENT
            and self.consecutive_runs >= TRANSIENT_RUNTIME_MAX_RUNS
        )


@dataclass(frozen=True)
class ExpectedTerminalRun:
    """Latest terminal direct Run for one causally bounded agent dispatch."""

    run: AgentRunObservation
    outcome: str
    consecutive_runs: int
    chain_started_at: str | None = None


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


_CAPACITY_ERROR_TEXT = re.compile(
    r"hit your .{0,40}limit|rate.?limit|usage limit|too many requests|(?<!\d)429(?!\d)",
    re.IGNORECASE,
)
_RESET_CLOCK = re.compile(
    r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*(?:\(([^)]{1,40})\))?", re.IGNORECASE)
_RESET_ISO = re.compile(
    r"resets?\s+(?:at\s+)?(\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2}))", re.IGNORECASE)
_RETRY_AFTER = re.compile(
    r"(?:retry|try again)[^0-9]{0,24}(\d{1,6})\s*(seconds?|secs?|minutes?|mins?|hours?|s|m|h)\b", re.IGNORECASE)


def classify_run_failure(run: AgentRunObservation) -> str | None:
    """The one classification of a failed Run: capacity (wait), transient (bounded rerun) or non-retryable (decision)."""
    if run.status != "failed":
        return None
    if run.failure_class == FAILURE_CAPACITY:
        return FAILURE_CAPACITY
    if is_retryable_transient_run_failure(run):
        return FAILURE_TRANSIENT
    if run.error and _CAPACITY_ERROR_TEXT.search(run.error):
        return FAILURE_CAPACITY
    return FAILURE_NONRETRYABLE


def _zone(name: str | None):
    if not name or name.strip().upper() in {"UTC", "UTC+0", "GMT", "Z"}:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name.strip())
    except Exception:    # unknown zone name: the caller falls back to backoff
        return None


def parse_capacity_reset(error: str | None, reference: datetime) -> datetime | None:
    """When the provider says capacity returns ("resets 12:10pm (UTC)", ISO instant, "retry in 90 seconds")."""
    if not error:
        return None
    iso = _RESET_ISO.search(error)
    if iso:
        parsed = parse_platform_time(iso.group(1))
        if parsed is not None and parsed.tzinfo is not None:
            return parsed
    clock = _RESET_CLOCK.search(error)
    if clock:
        zone = _zone(clock.group(4))
        hour = int(clock.group(1))
        if zone is not None and 1 <= hour <= 12:
            hour = hour % 12 + (12 if clock.group(3).lower() == "pm" else 0)
            local = reference.astimezone(zone)
            candidate = local.replace(hour=hour, minute=int(clock.group(2) or 0), second=0, microsecond=0)
            if candidate <= local:
                candidate += timedelta(days=1)
            return candidate
    after = _RETRY_AFTER.search(error)
    if after:
        unit = after.group(2).lower()[0]
        seconds = int(after.group(1)) * {"s": 1, "m": 60, "h": 3600}[unit]
        return reference + timedelta(seconds=seconds)
    return None


@dataclass(frozen=True)
class CapacityWait:
    step: str                      # "wait" | "retry" | "exhausted" | "unavailable"
    not_before: datetime | None = None


def decide_capacity_wait(failure: RunFailure, now: datetime, max_wait_seconds: float) -> CapacityWait:
    """Pure: wait for the reset time (or a bounded backoff when none is stated), rerun after it, give up past the ceiling."""
    reference = parse_platform_time(failure.run.updated_at) or parse_platform_time(failure.run.created_at)
    started = parse_platform_time(failure.chain_started_at) or reference
    if reference is None or reference.tzinfo is None or started is None or started.tzinfo is None:
        return CapacityWait("unavailable")
    reset = parse_capacity_reset(failure.run.error, reference)
    if reset is not None:
        not_before = reset + timedelta(seconds=CAPACITY_RESET_MARGIN_SECONDS)
    else:
        backoff = min(
            CAPACITY_BACKOFF_BASE_SECONDS * 2 ** max(failure.consecutive_runs - 1, 0),
            CAPACITY_BACKOFF_MAX_SECONDS)
        not_before = reference + timedelta(seconds=backoff)
    if (not_before - started).total_seconds() > max_wait_seconds or (now - started).total_seconds() > max_wait_seconds:
        return CapacityWait("exhausted", not_before)
    return CapacityWait("retry" if now >= not_before else "wait", not_before)


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
    created before it as baseline-internal, later Runs stay fail-closed.
    With ``gate_cutoff_created_at`` the caller already owns a consumer-side
    timestamp gate; then only Runs before that gate may be dropped, the
    returned cutoff stays ``None``, and anything unsafe fails closed.
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
        if any(created >= gate for created, _run in dropped):
            raise PlatformError(
                "Direct Run baseline cannot be bounded safely: Runs after "
                "the gate cutoff exceed the baseline limit")
        return kept_ids, None
    return kept_ids, keep[-1][1].created_at


def run_created_within_baseline(
    run: AgentRunObservation, cutoff: datetime | None,
) -> bool:
    """Whether a Run predates the baseline cutoff and is baseline-internal.

    Platform creation times have whole-second precision: a Run in the cutoff's own second may be newer, so it is not.
    """
    if cutoff is None:
        return False
    created = parse_platform_time(run.created_at)
    if created is None or created.tzinfo is None:
        return False
    return created < cutoff


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
            if created < cutoff:   # same second as the cutoff: not provably history
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
    failure_class = classify_run_failure(latest) if latest.status == "failed" else None
    if failure_class is not None:
        outcome = f"{failure_class}-failure"
    else:
        outcome = "finished-without-submit"

    consecutive = 0
    chain_started = None
    for run in expected:
        if not run.terminal:
            break
        if failure_class is not None:
            matches = classify_run_failure(run) == failure_class
        else:
            matches = run.status in {"completed", "cancelled"}
        if not matches:
            break
        consecutive += 1
        chain_started = run.created_at or chain_started
    terminal = ExpectedTerminalRun(latest, outcome, max(consecutive, attempt), chain_started)
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


def foreign_unbaselined_runs(
    runs: List[AgentRunObservation], target_agent_id: str, baseline_direct_run_ids, cutoff,
) -> List[AgentRunObservation]:
    """Direct Runs of another agent that the baseline does not cover: they break the causal chain of a handoff."""
    baseline = set(baseline_direct_run_ids)
    return [
        run for run in runs
        if run.kind == "direct" and run.id not in baseline and run.agent_id != target_agent_id
        and not run_created_within_baseline(run, cutoff)
    ]


def is_active_formal_run(runs: List[AgentRunObservation], agent_id: str, **baseline) -> bool:
    """Whether the one causally bound Run is still active and was a formal assignment/rerun dispatch."""
    attempt = observe_direct_run_attempt(runs, agent_id, **baseline)
    return attempt.state == "active" and formal_dispatch_target(runs, attempt)[1] is None


def parse_platform_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def active_direct_runs(runs: List[AgentRunObservation]) -> List[AgentRunObservation]:
    """Direct Runs that are alive: the one definition of "someone is still working on this issue"."""
    return [run for run in runs if run.kind == "direct" and run.active]


def unknown_direct_runs(runs: List[AgentRunObservation]) -> List[AgentRunObservation]:
    """Direct Runs that are neither active nor terminal: their outcome cannot be proven."""
    return [run for run in runs if run.kind == "direct" and not run.active and not run.terminal]

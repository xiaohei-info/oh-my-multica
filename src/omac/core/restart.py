"""Persisted amendment-authoring restart generation and fencing data."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any


RESTART_SCHEMA = "omac.authoring-restart/v1"
RESTART_KEY = "authoring_restart"
TERMINAL_RESTART_STATES = {"confirmation", "needs_decision", "unknown_partial"}


@dataclass(frozen=True)
class RestartState:
    generation: str
    owner_nonce: str
    request_digest: str
    state: str
    base_kind: str
    base_phase: str
    base_status: str
    base_review_subject_digest: str
    base_deliverable_identity: str
    baseline_run_ids: tuple[str, ...]
    lease_expires_at: float
    reviewer_baseline_run_ids: tuple[str, ...] = ()
    worker_run_id: str | None = None
    reviewer_run_id: str | None = None
    detail: str | None = None
    schema: str = RESTART_SCHEMA

    def evolve(self, **changes: Any) -> "RestartState":
        return replace(self, **changes)


@dataclass(frozen=True)
class RestartClaimResult:
    restart: RestartState
    acquired: bool
    resumed: bool = False
    confirmation_reused: bool = False


def dump_restart_state(state: RestartState) -> str:
    payload = asdict(state)
    payload["baseline_run_ids"] = list(state.baseline_run_ids)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_restart_state(raw: Any) -> RestartState | None:
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict) or raw.get("schema") != RESTART_SCHEMA:
        return None
    try:
        return RestartState(
            generation=str(raw["generation"]),
            owner_nonce=str(raw["owner_nonce"]),
            request_digest=str(raw["request_digest"]),
            state=str(raw["state"]),
            base_kind=str(raw["base_kind"]),
            base_phase=str(raw["base_phase"]),
            base_status=str(raw["base_status"]),
            base_review_subject_digest=str(raw["base_review_subject_digest"]),
            base_deliverable_identity=str(raw["base_deliverable_identity"]),
            baseline_run_ids=tuple(str(value) for value in raw.get("baseline_run_ids", [])),
            reviewer_baseline_run_ids=tuple(
                str(value) for value in raw.get("reviewer_baseline_run_ids", [])),
            lease_expires_at=float(raw["lease_expires_at"]),
            worker_run_id=(str(raw["worker_run_id"]) if raw.get("worker_run_id") else None),
            reviewer_run_id=(str(raw["reviewer_run_id"]) if raw.get("reviewer_run_id") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )
    except (KeyError, TypeError, ValueError):
        return None


def deliverable_identity(item: Any) -> str:
    ref = getattr(item, "deliverable_ref", None)
    if isinstance(ref, dict) and ref:
        stable = {key: ref.get(key) for key in (
            "sha256", "attachment_id", "comment_id", "filename",
        ) if ref.get(key)}
        if stable:
            return hashlib.sha256(json.dumps(
                stable, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
    body = getattr(item, "deliverable", None) or ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

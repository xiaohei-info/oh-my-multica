"""Explicit current-bound infrastructure review recovery; preparation never dispatches."""

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from enum import Enum
from datetime import datetime, timezone
import hashlib
import json

from ..core.config import resolve_no_submit_runs, resolve_retry
from ..core.manifest import _dump_contract, load_manifest, save_manifest
from ..core.retry_budget import preserved_amendment_budget
from ..core.stage_recovery import validate_stage_recovery
from ..core.taskmeta import ReviewerRunBaseline, TaskKind, TaskPhase
from ..engines.models import AgentRunObservation, PullRequestReadiness, WorkItemStatus
from ..errors import PlatformError, ValidationError
from .loop import (
    _bounded_direct_run_baseline,
    _formal_dispatch_target,
    _observe_direct_run_attempt,
    _review_subject_for_current_delivery,
    _validate_controller_sealed_delivery,
)

SCHEMA = "omac.operator-infrastructure-review-recovery/v1"
JOURNAL = "operator_review_recovery"
HOLD_REASON = "omac-reject-integration-evidence-recovery-required"


def _plain(value):
    def convert(obj):
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, Enum):
            return obj.value
        raise TypeError(f"Unsupported recovery fact: {type(obj).__name__}")

    return json.loads(json.dumps(value, default=convert))


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fail(detail):
    raise ValidationError(
        f"Infrastructure review recovery refused: {detail}; inspect current facts and "
        "prepare a new request with `omac node review-infrastructure --help`"
    )


def _control(item):
    return _plain(item)


def _manifest_digest(manifest, key, token=None, original_node=None):
    value = _plain(manifest)
    entries = value["meta"].get(JOURNAL)
    if entries is not None:
        if not isinstance(entries, dict):
            _fail("authorization journal is not a mapping")
        if token is not None:
            entries.pop(token, None)
        if not entries:
            value["meta"].pop(JOURNAL, None)
    if original_node is not None:
        node = value["nodes"][key]
        if node["status"] != original_node["status"] or node["merged"]:
            _fail("node completed or changed stage")
        node["status"] = original_node["status"]
        node["recovery_marker"] = original_node["recovery_marker"]
    return _digest(value)


def _budget(manifest, key, item, config):
    value = preserved_amendment_budget(manifest, key, item)
    limits = resolve_retry(config)
    value["limits"] = limits
    value["remaining"] = {
        stage: max(0, limits[stage] - value["consumed"][stage])
        for stage in ("worker", "review", "merge")
    }
    value["ci_remaining"] = max(0, limits["ci"] - value["absolute"]["ci"])
    value["no_submit_limit"] = resolve_no_submit_runs(config)
    return value


def _verify(store, runtime, manifest, key, item, config):
    node = manifest.nodes.get(key)
    if node is None or not node.work_item_id or node.work_item_id != item.id:
        _fail("work item/node identity missing or conflicting")
    if node.status != "blocked" or node.merged or item.status != WorkItemStatus.BLOCKED:
        _fail("only a currently blocked, unmerged node can prepare recovery")
    if item.review_generation is not None and (
        type(item.review_generation) is not str or not item.review_generation
    ):
        _fail("current generation is malformed; actual None must remain None")
    if item.kind != TaskKind.DEVELOP or item.phase != TaskPhase.REVIEW:
        _fail("not a develop review-stage hold")
    native_contract = (
        _dump_contract(item.contract) if is_dataclass(item.contract) else item.contract
    )
    if node.contract is None or _plain(native_contract) != _plain(
        _dump_contract(node.contract)
    ):
        _fail("complete manifest and native contract must match")
    if (
        not node.reviewer
        or item.reviewer not in (None, node.reviewer)
        or item.platform_assignee_id
    ):
        _fail("reviewer changed or an assignment still owns this issue")
    if any(
        getattr(item, field)
        for field in (
            "review_verdict",
            "review_report",
            "review_report_ref",
            "review_continuation",
            "review_nits_acceptance",
            "worker_handoff",
            "machine_feedback",
            "machine_feedback_ref",
            "review_comment",
            "current_review_ledger",
        )
    ):
        _fail("submitted/accepted review, continuation or handoff cannot be reset here")
    try:
        validate_stage_recovery(item, "review")
    except ValueError as exc:
        _fail(str(exc))
    _validate_controller_sealed_delivery(store, item)
    identity = item.delivery_identity
    if identity.worker != node.worker or identity.agent_id != store.resolve_agent_id(
        node.worker
    ):
        _fail("sealed Worker identity does not match the current owner")
    readiness = store.read_pull_request_readiness(identity.pr_url)
    if (
        not isinstance(readiness, PullRequestReadiness)
        or readiness.state.upper() != "OPEN"
        or readiness.is_draft is not False
        or readiness.head_sha != identity.pr_head_sha
    ):
        _fail("PR is not the same open, non-draft sealed source")

    subject = _review_subject_for_current_delivery(manifest, key, item)
    baseline = item.reviewer_run_baseline
    reviewer_id = store.resolve_agent_id(node.reviewer)
    if (
        baseline is None
        or not baseline.is_causally_bound()
        or not baseline.target_run_id
        or baseline.subject_digest != subject
        or item.review_subject_digest != subject
        or baseline.target_reviewer != node.reviewer
        or baseline.target_agent_id != reviewer_id
        or baseline.cutoff_created_at != identity.verification_created_at
        or type(baseline.generation) is not str
        or not baseline.generation
        or len(set(baseline.baseline_direct_run_ids))
        != len(baseline.baseline_direct_run_ids)
    ):
        _fail(
            "current Reviewer baseline/subject/seal/cutoff is incomplete or conflicting"
        )
    hold = item.decision_required
    expected = {
        "schema": "omac.decision-required/v1",
        "kind": "develop",
        "phase": "review",
        "gate": "operator-recovery",
        "reason_code": HOLD_REASON,
        "resume_issue_id": item.id,
        "node_id": key,
        "source_reviewer_run_id": baseline.target_run_id,
        "review_context_binding": {
            "generation": item.review_generation,
            "subject_digest": subject,
        },
    }
    if not isinstance(hold, dict) or any(hold.get(k) != v for k, v in expected.items()):
        _fail("unsupported or mismatched infrastructure hold")
    provenance = [
        (hold.get("source_report_sha256"), hold.get("source_report_bytes")),
        (
            hold.get("source_final_local_report_sha256"),
            hold.get("source_final_local_report_bytes"),
        ),
    ]
    usable = [
        (sha, count)
        for sha, count in provenance
        if sha is not None or count is not None
    ]
    if (
        len(usable) != 1
        or not isinstance(usable[0][0], str)
        or len(usable[0][0]) != 64
        or any(c not in "0123456789abcdef" for c in usable[0][0])
        or type(usable[0][1]) is not int
        or usable[0][1] <= 0
    ):
        _fail("original unsubmitted source provenance is missing or conflicting")
    if not runtime.capabilities.stable_direct_run_identity:
        _fail("stable direct Run identity is unavailable")
    runs = runtime.list_runs(item.id)
    if not isinstance(runs, list) or any(
        not isinstance(run, AgentRunObservation)
        or type(run.id) is not str
        or not run.id
        or type(run.kind) is not str
        or not run.kind
        for run in runs
    ):
        _fail("Run observations have missing or malformed identity")
    if len({run.id for run in runs}) != len(runs) or any(
        not run.terminal for run in runs
    ):
        _fail("active, queued, unknown or duplicate Run prevents recovery")
    observed = _observe_direct_run_attempt(
        runs,
        reviewer_id,
        baseline_direct_run_ids=baseline.baseline_direct_run_ids,
        cutoff_created_at=baseline.cutoff_created_at,
        target_run_id=baseline.target_run_id,
        attempt=baseline.attempt,
    )
    target, error = _formal_dispatch_target(runs, observed)
    if (
        error
        or observed.state != "terminal"
        or target.status != "completed"
        or target.id != baseline.target_run_id
    ):
        _fail(error or "source Reviewer is not the uniquely completed original attempt")
    # Every post-baseline Reviewer must be this exact source; do not adopt a
    # delayed retry chain or another terminal Reviewer through this reset.
    remaining = [
        run
        for run in runs
        if run.kind == "direct"
        and run.agent_id == reviewer_id
        and run.id not in baseline.baseline_direct_run_ids
    ]
    if len(remaining) != 1 or remaining[0].id != target.id:
        _fail("another delayed-owned or ambiguous Reviewer Run exists")
    budget = _budget(manifest, key, item, config)
    attempts = observed.terminal.consecutive_runs
    if attempts >= budget["no_submit_limit"]:
        _fail("no-submit budget exhausted; this capability cannot grant another budget")
    ids, _ = _bounded_direct_run_baseline(
        runs, gate_cutoff_created_at=baseline.cutoff_created_at
    )
    next_ids = sorted(set(ids) | set(baseline.baseline_direct_run_ids))
    return budget, _plain(sorted(runs, key=lambda run: run.id)), next_ids, attempts + 1


def prepare_operator_review_recovery(store, runtime, manifest, key, reason, config):
    if not isinstance(reason, str) or not reason.strip() or len(reason.encode()) > 2048:
        _fail("an explicit bounded operator reason is required")
    node = manifest.nodes.get(key)
    if node is None or not node.work_item_id:
        _fail("existing work item required")
    item = store.get_work_item(node.work_item_id)
    budget, runs, ids, next_attempt = _verify(
        store, runtime, manifest, key, item, config
    )
    return {
        "schema": SCHEMA,
        "node_id": key,
        "reason": reason,
        "source": {
            "manifest_sha256": _manifest_digest(manifest, key),
            "node": _plain(node),
            "control": _control(item),
            "budget": budget,
            "runs": runs,
            "next_baseline_ids": ids,
            "next_attempt": next_attempt,
        },
    }


def _states(request, token):
    source = request["source"]
    if not isinstance(source.get("control"), dict) or not isinstance(
        source["control"].get("reviewer_run_baseline"), dict
    ):
        _fail("prepared control and Reviewer baseline must be mappings")
    baseline = deepcopy(source["control"]["reviewer_run_baseline"])
    baseline.update(
        generation=f"infrastructure-{token[:24]}",
        attempt=source["next_attempt"],
        baseline_direct_run_ids=source["next_baseline_ids"],
        target_run_id=None,
    )
    states = [deepcopy(source["control"])]
    for change in (
        {"reviewer": None, "platform_assignee_id": None},
        {"reviewer_run_baseline": baseline},
        {"status": "in_review"},
        {"decision_required": None},
    ):
        states.append({**deepcopy(states[-1]), **change})
    return states, baseline


def _same_control(actual, expected, *, assignment_cleared=False):
    actual, expected = deepcopy(actual), deepcopy(expected)
    # Multica clear_assignment persists reviewer="", while Mock uses None.
    # Accept this representation only when observing this receipt's cleared
    # assignment states; preparation and the original source remain exact.
    if (
        assignment_cleared
        and expected.get("reviewer") is None
        and actual.get("reviewer") == ""
    ):
        actual["reviewer"] = None
    # Retain the original timestamp in the receipt; successful owned writes
    # legitimately change only updated_at, not other protected control facts.
    actual.pop("updated_at", None)
    expected.pop("updated_at", None)
    for name in ("decision_required",):
        if actual.get(name) == {}:
            actual[name] = None
        if expected.get(name) == {}:
            expected[name] = None
    return actual == expected


def _observe_apply(
    store, runtime, manifest, key, source, states, progress, token, config
):
    if (
        _manifest_digest(manifest, key, token, source["node"])
        != source["manifest_sha256"]
    ):
        _fail("protected manifest/contract/done/budget facts changed")
    current = store.get_work_item(source["control"]["id"])
    allowed = [progress] + ([progress + 1] if progress < 4 else [])
    matches = [
        i
        for i in allowed
        if _same_control(_control(current), states[i], assignment_cleared=i > 0)
    ]
    if not matches:
        _fail("control drift outside this receipt step; do not reset a new cycle")
    original = deepcopy(current)
    original.reviewer = source["control"]["reviewer"]
    original.platform_assignee_id = source["control"]["platform_assignee_id"]
    original.reviewer_run_baseline = ReviewerRunBaseline(
        **source["control"]["reviewer_run_baseline"]
    )
    original.status = WorkItemStatus.BLOCKED
    original.decision_required = deepcopy(source["control"]["decision_required"])
    budget, runs, ids, next_attempt = _verify(
        store, runtime, manifest, key, original, config
    )
    if (
        budget != source["budget"]
        or runs != source["runs"]
        or ids != source["next_baseline_ids"]
        or next_attempt != source["next_attempt"]
    ):
        _fail("source identity, Run set or budgets changed before recovery write")
    return max(matches)


def apply_operator_review_recovery(
    store, runtime, manifest_path, key, request, approved_sha256, config
):
    token = _digest(request)
    if (
        not isinstance(request, dict)
        or request.get("schema") != SCHEMA
        or request.get("node_id") != key
        or token != approved_sha256
    ):
        _fail("exact prepared request SHA256 approval is required")
    manifest = load_manifest(manifest_path)
    entries = manifest.meta.get(JOURNAL, {})
    if not isinstance(entries, dict):
        _fail("authorization journal is not a mapping")
    entry = entries.get(token)
    if entry is not None:
        if (
            not isinstance(entry, dict)
            or entry.get("request") != request
            or entry.get("request_sha256") != token
            or type(entry.get("step")) is not int
            or entry["step"] not in range(5)
            or entry.get("state") not in {"pending", "consumed"}
            or (entry["state"] == "consumed" and entry["step"] != 4)
        ):
            _fail("authorization receipt is malformed or changed")
        if entry["state"] == "consumed":
            return {"state": "already-consumed", "authorization_sha256": token}
    if not isinstance(request.get("source"), dict):
        _fail("prepared source is missing")
    source = request["source"]
    try:
        states, baseline = _states(request, token)
        item_id = source["control"]["id"]
    except (KeyError, TypeError, ValueError):
        _fail("prepared source is incomplete")
    with store.worker_control_lock(item_id):
        if entry is None:
            if (
                prepare_operator_review_recovery(
                    store, runtime, manifest, key, request["reason"], config
                )
                != request
            ):
                _fail(
                    "full current hold/control/Run/budget facts changed since preparation"
                )
            entry = {
                "request_sha256": token,
                "request": deepcopy(request),
                "state": "pending",
                "step": 0,
            }
            manifest.meta.setdefault(JOURNAL, {})[token] = entry
            manifest.nodes[key].recovery_marker = True
            save_manifest(manifest, manifest_path)
        operations = [
            lambda: store.clear_assignment(item_id),
            lambda: store.update_work_item_metadata(
                item_id, reviewer_run_baseline=ReviewerRunBaseline(**baseline)
            ),
            lambda: store.update_status(item_id, WorkItemStatus.IN_REVIEW),
            lambda: store.update_work_item_metadata(item_id, decision_required={}),
        ]
        while entry["step"] < 4:
            progress = entry["step"]
            observed = _observe_apply(
                store, runtime, manifest, key, source, states, progress, token, config
            )
            if observed > progress:
                entry["step"] = observed
                save_manifest(manifest, manifest_path)
                continue
            operations[progress]()
            if not _same_control(
                _control(store.get_work_item(item_id)),
                states[progress + 1],
                assignment_cleared=True,
            ):
                raise PlatformError(
                    "Recovery write not yet observable; resume the identical approved request"
                )
            entry["step"] = progress + 1
            save_manifest(manifest, manifest_path)
        _observe_apply(store, runtime, manifest, key, source, states, 4, token, config)
        manifest.nodes[key].status = "in_review"
        manifest.nodes[key].recovery_marker = True
        entry["state"] = "consumed"
        save_manifest(manifest, manifest_path)
    return {
        "state": "ready-for-independent-review",
        "authorization_sha256": token,
        "issue_id": item_id,
        "verdict": None,
        "next_action": f"Inspect receipt, then use omac dag run {manifest_path} under the single controller",
    }


DISPATCH_JOURNAL = "operator_review_reservation_dispatch"


def _reservation_candidates(manifest, key):
    """Completed history is retained but cannot authorize another dispatch."""
    entries = manifest.meta.get(JOURNAL, {})
    progress = manifest.meta.get(DISPATCH_JOURNAL, {})
    if not isinstance(entries, dict) or not isinstance(progress, dict):
        return []
    return [
        (token, entry)
        for token, entry in entries.items()
        if isinstance(entry, dict)
        and isinstance(entry.get("request"), dict)
        and entry["request"].get("node_id") == key
        and entry.get("state") == "consumed"
        and (
            not isinstance(progress.get(token), dict)
            or progress[token].get("step") != "complete"
        )
    ]


def reservation_candidate(manifest, key):
    """Select only for validation; neither receipt state nor marker is authority."""
    return bool(_reservation_candidates(manifest, key))


def _reservation_source(store, runtime, manifest, key, config, record):
    """Prove full consumed request and protected current facts before each effect."""
    from .loop import _classify_reviewer_recovery_decision

    matches = _reservation_candidates(manifest, key)
    if len(matches) != 1:
        _fail("one complete consumed infrastructure reservation is required")
    token, entry = matches[0]
    request = entry["request"]
    if (
        entry.get("step") != 4
        or entry.get("request_sha256") != token
        or _digest(request) != token
        or request.get("schema") != SCHEMA
    ):
        _fail("consumed receipt/request digest or stage changed")
    try:
        source = request["source"]
        states, reserved = _states(request, token)
    except (KeyError, TypeError, ValueError):
        _fail("consumed reservation source is malformed")
    node = manifest.nodes[key]
    node_state = _plain(node)
    expected_node = deepcopy(source["node"])
    if node.status not in {"blocked", "in_review"} or node.merged:
        _fail("reserved review is done/merged or no longer review-stage")
    node_state["status"] = expected_node["status"]
    node_state["recovery_marker"] = expected_node["recovery_marker"]
    if node_state != expected_node:
        _fail("reserved node/contract/source changed")
    current = store.get_work_item(source["control"]["id"])
    expected = deepcopy(states[-1])
    observed = _control(current)
    decision_class = _classify_reviewer_recovery_decision(
        getattr(manifest, "_recovery_manifest_path", ""), key, current
    )
    if current.decision_required not in (None, {}):
        if record and record.get("step") != "release":
            _fail("new decision stopped the reserved Reviewer dispatch")
        if (
            decision_class != "canonical-baseline-unavailable"
            or current.decision_required.get("reason_code")
            != "reviewer-run-dispatch-unresolved"
        ):
            _fail("current decision is not the exact reserved dispatch guard")
        expected["decision_required"] = deepcopy(current.decision_required)
    if current.status == WorkItemStatus.BLOCKED:
        if current.decision_required in (None, {}) and not (
            record
            and record.get("step") == "release"
            and record.get("release_status_attempted")
        ):
            _fail("platform BLOCKED is not the exact reserved dispatch decision")
        if record and record.get("step") != "release":
            _fail("platform BLOCKED stopped the reserved Reviewer dispatch")
        expected["status"] = "blocked"
    elif current.status != WorkItemStatus.IN_REVIEW:
        _fail("reserved review control status changed")
    step = record.get("step") if record else None
    if step == "assign":
        # Assignment metadata can precede the suppressed platform assignment.
        if current.reviewer == node.reviewer:
            expected["reviewer"] = node.reviewer
        if current.platform_assignee_id == reserved["target_agent_id"]:
            expected["platform_assignee_id"] = reserved["target_agent_id"]
    elif step in {"assigned", "wake", "binding"}:
        expected["reviewer"] = node.reviewer
        expected["platform_assignee_id"] = reserved["target_agent_id"]
    if (
        step == "binding"
        and record.get("target_run_id")
        and current.reviewer_run_baseline.target_run_id == record["target_run_id"]
    ):
        expected["reviewer_run_baseline"]["target_run_id"] = record["target_run_id"]
    if not _same_control(observed, expected, assignment_cleared=True):
        _fail("protected reserved control/source/subject/generation/budget drift")
    if record:
        if manifest.meta.get(DISPATCH_JOURNAL, {}).get(
            token
        ) is not record or record.get("checkpoint_sha256") != _digest(
            {k: v for k, v in record.items() if k != "checkpoint_sha256"}
        ):
            _fail("dispatch checkpoint changed before effect")
        if (
            record.get("request_sha256") != token
            or record.get("reservation") != reserved
        ):
            _fail("dispatch checkpoint does not identify the complete reservation")
        if (
            _digest(manifest.meta.get("amendment_apply"))
            != record["approved_meta_sha256"]
        ):
            _fail("approved retained budgets changed during reservation dispatch")
        for done_key, sha in record["done_nodes"].items():
            if (
                done_key not in manifest.nodes
                or _digest(_plain(manifest.nodes[done_key])) != sha
            ):
                _fail("a protected complete DONE object changed")
    runs = runtime.list_runs(current.id)
    if not isinstance(runs, list) or any(
        not isinstance(r, AgentRunObservation) for r in runs
    ):
        _fail("complete stable native Run observations unavailable")
    if len({r.id for r in runs}) != len(runs):
        _fail("ambiguous native Run identities")
    original_ids = {r["id"] for r in source["runs"]}
    original_runs = [r for r in runs if r.id in original_ids]
    fresh = [r for r in runs if r.id not in original_ids]
    if _plain(sorted(original_runs, key=lambda r: r.id)) != source["runs"]:
        _fail("original native Run facts changed or disappeared")
    # Revalidate original authorization through the existing complete guard.
    original = deepcopy(current)
    original.reviewer = source["control"]["reviewer"]
    original.platform_assignee_id = source["control"]["platform_assignee_id"]
    original.status = WorkItemStatus.BLOCKED
    original.decision_required = deepcopy(source["control"]["decision_required"])
    original.reviewer_run_baseline = ReviewerRunBaseline(
        **source["control"]["reviewer_run_baseline"]
    )
    verify_manifest = deepcopy(manifest)
    verify_manifest.nodes[key].status = "blocked"

    class OriginalRuns:
        capabilities = runtime.capabilities

        def list_runs(self, _item_id):
            return original_runs

    budget, frozen_runs, ids, next_attempt = _verify(
        store, OriginalRuns(), verify_manifest, key, original, config
    )
    if (
        budget != source["budget"]
        or frozen_runs != source["runs"]
        or ids != source["next_baseline_ids"]
        or next_attempt != source["next_attempt"]
    ):
        _fail("reserved effective absolute/relative budgets or causal source changed")
    if fresh:
        if step not in {"wake", "binding"} or len(fresh) != 1:
            _fail(
                "new delayed/active/queued/unknown or ambiguous Run before owned wake"
            )
        target = fresh[0]
        from .loop import _parse_platform_time

        created = _parse_platform_time(target.created_at)
        wake_started = _parse_platform_time(record.get("wake_started_at"))
        if (
            created is None
            or created.tzinfo is None
            or wake_started is None
            or wake_started.tzinfo is None
            or created < wake_started
        ):
            _fail("delayed native Run predates this owned wake intent")
        if (
            not target.formal
            or target.agent_id != reserved["target_agent_id"]
            or target.status not in {"running", "completed", "failed", "cancelled"}
        ):
            _fail("unproven native reservation wake outcome")
        observed_run = _observe_direct_run_attempt(
            runs,
            reserved["target_agent_id"],
            baseline_direct_run_ids=reserved["baseline_direct_run_ids"],
            cutoff_created_at=reserved["cutoff_created_at"],
            attempt=reserved["attempt"],
        )
        actual, error = _formal_dispatch_target(runs, observed_run)
        if error or actual.id != target.id:
            _fail(error or "reservation target is not unique")
    else:
        target = None
    return token, current, reserved, target


def dispatch_operator_reservation(store, runtime, manifest, manifest_path, key, config):
    """Once-only assignment/wake for a fully proven consumed reservation."""
    manifest._recovery_manifest_path = manifest_path
    entries = manifest.meta.get(DISPATCH_JOURNAL, {})
    if not isinstance(entries, dict):
        _fail("reservation dispatch journal is malformed")
    candidates = _reservation_candidates(manifest, key)
    record = entries.get(candidates[0][0]) if len(candidates) == 1 else None
    if record is not None and (
        not isinstance(record, dict)
        or record.get("schema") != "omac.operator-review-reservation-dispatch/v1"
        or record.get("checkpoint_sha256")
        != _digest({k: v for k, v in record.items() if k != "checkpoint_sha256"})
        or not isinstance(record.get("done_nodes"), dict)
        or not isinstance(record.get("approved_meta_sha256"), str)
        or type(record.get("release_status_attempted")) is not bool
        or type(record.get("release_decision_attempted")) is not bool
        or record.get("step")
        not in {"release", "ready", "assign", "assigned", "wake", "binding"}
    ):
        _fail("reservation dispatch checkpoint is malformed")
    token, current, reserved, target = _reservation_source(
        store, runtime, manifest, key, config, record
    )
    if record is None:
        record = {
            "schema": "omac.operator-review-reservation-dispatch/v1",
            "release_status_attempted": False,
            "release_decision_attempted": False,
            "request_sha256": token,
            "reservation": deepcopy(reserved),
            "step": "release",
            "approved_meta_sha256": _digest(manifest.meta.get("amendment_apply")),
            "done_nodes": {
                k: _digest(_plain(n))
                for k, n in manifest.nodes.items()
                if n.status == "done"
            },
        }
        record["checkpoint_sha256"] = _digest(record)
        manifest.meta.setdefault(DISPATCH_JOURNAL, {})[token] = record
        save_manifest(manifest, manifest_path)

    def verify():
        return _reservation_source(store, runtime, manifest, key, config, record)

    def checkpoint(step):
        record["step"] = step
        record["checkpoint_sha256"] = _digest(
            {k: v for k, v in record.items() if k != "checkpoint_sha256"}
        )
        save_manifest(manifest, manifest_path)

    if record["step"] == "release":
        if current.status == WorkItemStatus.BLOCKED:
            verify()
            if record["release_status_attempted"]:
                raise PlatformError(
                    "Reserved status release outcome pending; observe before retry"
                )
            record["release_status_attempted"] = True
            checkpoint("release")
            store.update_status(current.id, WorkItemStatus.IN_REVIEW)
        _, current, _, _ = verify()
        if current.decision_required not in (None, {}):
            verify()
            if record["release_decision_attempted"]:
                raise PlatformError(
                    "Reserved decision release outcome pending; observe before retry"
                )
            record["release_decision_attempted"] = True
            checkpoint("release")
            store.update_work_item_metadata(current.id, decision_required={})
        _, current, _, _ = verify()
        if (
            current.status != WorkItemStatus.IN_REVIEW
            or current.decision_required not in (None, {})
        ):
            raise PlatformError(
                "Reserved review release not yet observable; observe the same dispatch checkpoint"
            )
        manifest.nodes[key].status = "in_review"
        checkpoint("ready")
    if record["step"] == "ready":
        verify()
        checkpoint("assign")  # Persist before an assignment can be accepted.
        store.assign_reviewer(
            current.id, manifest.nodes[key].reviewer, admission=verify
        )
    if record["step"] == "assign":
        _, current, _, _ = verify()
        if (
            current.reviewer != reserved["target_reviewer"]
            or current.platform_assignee_id != reserved["target_agent_id"]
        ):
            raise PlatformError(
                "Reserved suppressed assignment outcome pending; observe before any retry"
            )
        checkpoint("assigned")
    if record["step"] == "assigned":
        verify()
        record["wake_started_at"] = datetime.now(timezone.utc).isoformat()
        checkpoint("wake")  # A missing outcome never permits another wake.
        runtime.wake_reviewer(
            store, current.id, manifest.nodes[key].reviewer, admission=verify
        )
    _, current, reserved, target = verify()
    if target is None:
        raise PlatformError(
            "Reserved Reviewer wake outcome pending; do not dispatch another Run"
        )
    if (
        current.reviewer != reserved["target_reviewer"]
        or current.platform_assignee_id != reserved["target_agent_id"]
    ):
        _fail("owned Reviewer assignment changed before binding native target")
    if record["step"] == "wake":
        record["target_run_id"] = target.id
        checkpoint("binding")
    if current.reviewer_run_baseline.target_run_id != target.id:
        verify()
        store.update_work_item_metadata(
            current.id,
            reviewer_run_baseline=ReviewerRunBaseline(
                **{**reserved, "target_run_id": target.id}
            ),
        )
    verify()
    checkpoint("complete")
    return True

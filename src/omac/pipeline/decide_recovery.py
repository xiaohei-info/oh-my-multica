"""Pure checks on a persisted Reviewer decision marker and on the review subject it must bind.

Whether a blocked node's recovery decision is the exact marker OMAC wrote (so a unique formal Run may clear it) is a
function of the manifest, the item's control facts and the reviewer identity. No I/O (enforced by
tests/test_architecture.py); ``loop`` and ``omac node retry`` call these.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.manifest import Manifest, _dump_contract
from ..core.stage_recovery import stage_recovery_subject, validate_stage_recovery
from ..core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase
from ..core.review_convergence import review_subject_digest
from ..core.workitem import WorkItemControlProjection, WorkItemHydrationPlan, WorkItemPayload
from ..errors import PlatformError
from ..i18n import ui
from .decide_runs import formal_dispatch_target, observe_direct_run_attempt


def current_review_subject(item) -> str:
    return review_subject_digest(item, max(1, item.bounces.review + 1))


def review_subject_for_round(
    manifest: Manifest, key: str, item, round_index: int,
) -> str:
    """返回指定 round 的合法 subject，兼容已接受 amendment 的 review recovery。"""
    ledger = manifest.meta.get("amendment_apply")
    entries = ledger.get("nodes") if isinstance(ledger, dict) else None
    entry = entries.get(key) if isinstance(entries, dict) else None
    expected = (
        entry.get("expected_review_subject")
        if isinstance(entry, dict) and entry.get("stage") == "review"
        else None
    )
    if (
        isinstance(expected, str)
        and expected
        and expected == stage_recovery_subject(manifest.nodes[key], item)
    ):
        return expected
    return review_subject_digest(item, round_index)


def review_subject_for_current_delivery(
    manifest: Manifest, key: str, item,
) -> str:
    return review_subject_for_round(
        manifest, key, item, max(1, item.bounces.review + 1))


def review_subject_is_current(
    manifest: Manifest, key: str, item,
) -> bool:
    return (
        item.review_subject_digest
        == review_subject_for_current_delivery(manifest, key, item)
    )


def node_retry_action(
    manifest_path: str,
    key: str,
    phase: TaskPhase,
) -> str:
    """Route an explicit retry from the business phase that failed."""
    retry = f"omac node retry {manifest_path} {key}"
    if phase == TaskPhase.REVIEW:
        retry += " --stage review"
    return retry


def reviewer_recovery_decision(
    manifest_path: str,
    key: str,
    item,
    reason_code: str,
    *,
    run_id: str | None = None,
) -> dict:
    """Build the one canonical reviewer recovery decision marker."""
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": reason_code,
        "kind": TaskKind.DEVELOP.value,
        "phase": TaskPhase.REVIEW.value,
        "gate": "reviewer",
        "resume_issue_id": item.id,
        "node_id": key,
        "failure_class": "unproven-reviewer-run-causality",
        "next_action": node_retry_action(
            manifest_path, key, TaskPhase.REVIEW),
    }
    if run_id:
        decision["run_id"] = run_id
    return decision


def provisional_reviewer_decision_variants(
    manifest_path: str,
    key: str,
    item,
) -> tuple[dict, ...]:
    """Return exact current and documented legacy marker shapes.

    Both provisional reason codes describe the same unproven Reviewer Run
    causality family: a baseline that could not be established, and a
    continuation dispatch whose Run outcome was never observed. Either must
    be recoverable through the one explicit operator retry.
    """
    path_spellings = {manifest_path, str(Path(manifest_path).resolve())}
    try:
        path_spellings.add(str(
            Path(manifest_path).resolve().relative_to(Path.cwd().resolve())
        ))
    except ValueError:
        pass
    variants = []
    for reason_code in (
        "reviewer-run-baseline-unavailable",
        "reviewer-run-dispatch-unresolved",
    ):
        canonical = reviewer_recovery_decision(
            manifest_path,
            key,
            item,
            reason_code,
        )
        for path_value in sorted(path_spellings):
            for stage_suffix in (" --stage review", ""):
                variant = dict(canonical)
                variant["next_action"] = (
                    f"omac node retry {path_value} {key}{stage_suffix}"
                )
                variants.append(variant)
    return tuple(variants)


def reviewer_runtime_failure_decision_variants(
    manifest_path: str,
    key: str,
    item,
) -> tuple[dict, ...]:
    """Return exact current and legacy explicit Reviewer retry decisions."""
    decision = item.decision_required
    if not isinstance(decision, dict):
        return ()
    failure_classes = {
        "transient-runtime-retry-exhausted": "transient-provider-or-transport",
        "nonretryable-runtime-failure": "nonretryable-agent-run",
    }
    reason_code = decision.get("reason_code")
    failure_class = failure_classes.get(reason_code)
    run_id = decision.get("run_id")
    if failure_class is None or not isinstance(run_id, str) or not run_id:
        return ()

    expected = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": reason_code,
        "kind": TaskKind.DEVELOP.value,
        "phase": TaskPhase.REVIEW.value,
        "gate": "reviewer",
        "resume_issue_id": item.id,
        "node_id": key,
        "run_id": run_id,
        "failure_class": failure_class,
        "next_action": node_retry_action(
            manifest_path, key, TaskPhase.REVIEW),
    }
    variants = [expected]
    legacy = dict(expected)
    legacy["next_action"] = f"omac node retry {manifest_path} {key}"
    variants.append(legacy)
    return tuple(variants)


def classify_reviewer_recovery_decision(
    manifest_path: str,
    key: str,
    item,
) -> str:
    """Classify the persisted decision without trusting any marker field."""
    decision = item.decision_required
    if decision in (None, {}):
        return "absent"
    if any(
        decision == expected
        for expected in provisional_reviewer_decision_variants(
            manifest_path, key, item)
    ):
        return "canonical-baseline-unavailable"
    if any(
        decision == expected
        for expected in reviewer_runtime_failure_decision_variants(
            manifest_path, key, item)
    ):
        return "explicit-review-retry"
    return "other"


def provisional_reviewer_recovery_marker_error(
    manifest: Manifest,
    manifest_path: str,
    key: str,
    item,
    reviewer: str,
    reviewer_id: str,
) -> str | None:
    """Validate the control facts that make one reviewer decision provisional."""
    if not (
        classify_reviewer_recovery_decision(manifest_path, key, item)
        == "canonical-baseline-unavailable"
        and item.kind == TaskKind.DEVELOP
        and item.phase == TaskPhase.REVIEW
    ):
        return "the persisted recovery decision does not identify this review"

    baseline = item.reviewer_run_baseline
    if baseline is None or not baseline.is_causally_bound():
        return "the persisted reviewer Run baseline is incomplete"

    if (
        item.review_subject_digest != baseline.subject_digest
        or baseline.target_reviewer != reviewer
        or baseline.target_agent_id != reviewer_id
    ):
        return "the persisted review subject or reviewer identity does not match"

    try:
        validate_stage_recovery(item, TaskPhase.REVIEW.value)
    except ValueError as exc:
        return str(exc)
    identity = item.delivery_identity
    if (
        identity is None
        or identity.verification_created_at != baseline.cutoff_created_at
    ):
        return "the delivery identity does not match the reviewer Run cutoff"
    return None


def delayed_reviewer_recovery_marker_error(
    manifest: Manifest,
    manifest_path: str,
    key: str,
    item,
    reviewer: str,
    reviewer_id: str,
    *,
    require_target: bool,
) -> str | None:
    """Validate the dedicated delayed-Run marker against hydrated authority."""
    marker_error = provisional_reviewer_recovery_marker_error(
        manifest, manifest_path, key, item, reviewer, reviewer_id)
    if marker_error is not None:
        return marker_error

    baseline = item.reviewer_run_baseline
    if require_target and not baseline.target_run_id:
        return "the persisted reviewer Run baseline has no target Run"

    authoritative_subject = review_subject_for_current_delivery(
        manifest, key, item)
    if authoritative_subject != item.review_subject_digest:
        return "the authoritative review subject does not match"

    item_contract = item.contract
    if item_contract is not None and not isinstance(item_contract, dict):
        item_contract = _dump_contract(item_contract)
    node_contract = (
        _dump_contract(manifest.nodes[key].contract)
        if manifest.nodes[key].contract else None
    )
    if item_contract != node_contract:
        return "the manifest and persisted node contracts do not match"
    return None


@dataclass(frozen=True)
class DelayedReviewerRecovery:
    """What ``omac node retry --stage review`` does about a Reviewer Run that became visible late.

    ``ordinary``: nothing to bind, the review stage is reset as usual. ``adopt``: bind ``target_run_id`` and restore the
    stage without a new dispatch. ``refuse``: the Run causality is not unique; ``detail`` says why.
    """

    action: str
    target_run_id: str | None = None
    detail: str = ""


def plan_delayed_reviewer_recovery(
    manifest: Manifest,
    manifest_path: str,
    key: str,
    item,
    reviewer: str,
    *,
    agent_id_of: Callable[[str], str],
    list_runs: Callable[[], list],
    stable_run_identity: bool,
) -> DelayedReviewerRecovery:
    """Decide how an explicit review retry treats a Reviewer result whose Run was not visible when it was observed.

    The two callables are read lazily: an ordinary retry never looks at the Runs.
    """
    decision_class = classify_reviewer_recovery_decision(manifest_path, key, item)
    if decision_class == "other":
        return DelayedReviewerRecovery("refuse", detail="the persisted recovery decision does not identify this review")
    if decision_class != "canonical-baseline-unavailable":
        return DelayedReviewerRecovery("ordinary")

    has_submitted_review = bool(
        item.review_verdict in {"pass", "pass-with-nits", "reject"}
        and isinstance(item.review_report, dict) and item.review_report
        and isinstance(item.review_report_ref, dict) and item.review_report_ref)
    decision = item.decision_required if isinstance(item.decision_required, dict) else {}
    dispatch_unresolved = decision.get("reason_code") == "reviewer-run-dispatch-unresolved"
    # A submitted review always needs its causal Run proven before replay. An unresolved continuation dispatch may
    # additionally own a delayed Run that became visible after the observation window; adopting it avoids a duplicate
    # Reviewer dispatch. Every other unsubmitted shape stays an ordinary stage reset.
    if not has_submitted_review and not dispatch_unresolved:
        return DelayedReviewerRecovery("ordinary")

    if not stable_run_identity:
        return DelayedReviewerRecovery("refuse", detail="stable direct Run identity is unavailable")
    marker_error = delayed_reviewer_recovery_marker_error(
        manifest, manifest_path, key, item, reviewer, agent_id_of(reviewer), require_target=False)
    if marker_error is not None:
        return DelayedReviewerRecovery("refuse", detail=marker_error)

    baseline, runs = item.reviewer_run_baseline, list_runs()
    observed = observe_direct_run_attempt(
        runs, baseline.target_agent_id,
        baseline_direct_run_ids=baseline.baseline_direct_run_ids, cutoff_created_at=baseline.cutoff_created_at,
        target_run_id=baseline.target_run_id, attempt=baseline.attempt)
    _target, target_error = formal_dispatch_target(runs, observed)
    if target_error is not None:
        # An unresolved continuation that never materialised as a provable Run leaves nothing to preserve.
        return DelayedReviewerRecovery("refuse", detail=target_error) if has_submitted_review else DelayedReviewerRecovery("ordinary")
    return DelayedReviewerRecovery("adopt", target_run_id=observed.target_run_id)


STRUCTURED_RECOVERY_PAYLOADS = (
    (WorkItemPayload.VERIFICATION, "verification_ref"),
    (WorkItemPayload.REVIEW_REPORT, "review_report_ref"),
    (WorkItemPayload.REVIEW_LEDGER, "review_ledger_ref"),
    (WorkItemPayload.CONTRACT, "contract_ref"),
)


def validate_structured_recovery_payloads(
    projection: WorkItemControlProjection,
    requested: WorkItemHydrationPlan,
) -> None:
    """Fail closed when referenced collect evidence is not a non-empty mapping."""
    item = projection.work_item
    for payload, ref_name in STRUCTURED_RECOVERY_PAYLOADS:
        ref = getattr(item, ref_name, None)
        if payload not in requested or not ref:
            continue
        value = getattr(item, payload.value, None)
        if isinstance(value, dict) and value:
            continue
        attachment_id = str(ref.get("attachment_id") or "unknown")
        raise PlatformError(ui(
            f"Referenced {payload.value} attachment {attachment_id} for work "
            f"item {item.id} did not parse as a non-empty YAML/JSON mapping. "
            "Restore a valid structured attachment before retrying DAG recovery.",
            f"工作单元 {item.id} 引用的 {payload.value} 附件 {attachment_id} "
            "无法解析为非空 YAML/JSON 映射。请恢复合法的结构化附件后再重试 "
            "DAG 恢复。",
        ))

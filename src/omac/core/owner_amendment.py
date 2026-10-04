"""Exact operator authorization for an assessment, never a product verdict.

The manifest journal is a single-writer control record. Host-local flock is
not distributed CAS; callers must retain one control writer across hosts.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from enum import Enum

import yaml

from .manifest import _dump_contract
from .retry_budget import preserved_amendment_budget
from .taskmeta import TaskKind, TaskPhase, review_context_binding
from ..engines.models import WorkItemStatus
from ..errors import ValidationError, WorkItemNotFoundError

SCHEMA = "omac.owner-amendment-request/v1"
JOURNAL = "owner_amendment_resolutions"
PACKAGE_REASON = "omac-budget-preserving-amendment-recovery-required"


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def plain(value):
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=lambda value: value.value if isinstance(value, Enum) else value,
        )
    )


def _invalid(detail):
    raise ValidationError(
        detail
        + "; inspect current source and run `omac dag amend prepare-owner --help`"
    )


def manifest_source(manifest):
    # Bind ALL nodes, FULL DONE objects and the whole approved/consumed history,
    # rather than reconstructing a lossy subset or assuming a fixed DONE count.
    from .amendment import _manifest_payload

    result = _manifest_payload(manifest, include_runtime=True)
    result["meta"].pop(JOURNAL, None)
    for value, node in zip(result["nodes"], manifest.nodes.values()):
        value["recovery_marker"] = node.recovery_marker
    return result


def retry_policy(manifest_path):
    from ..cli.commands.dag import _config_path_for_manifest
    from .config import load_config, resolve_retry

    path = str(Path(_config_path_for_manifest(manifest_path)).resolve())
    return {"config_path": path, "limits": resolve_retry(load_config(path))}


def remaining_budget(source, limits):
    budget = source.get("budget")
    if budget is None:
        return None
    consumed = {**budget["consumed"], "ci": budget["absolute"]["ci"]}
    return {
        "limits": copy.deepcopy(limits),
        "remaining": {s: max(0, limits[s] - consumed[s]) for s in limits},
        "review_authorized_through_round": budget["effective_baseline"]["review"]
        + limits["review"],
    }


def terminal_runs(runtime, item_id):
    if runtime is None:
        _invalid("Source authorization requires authoritative Run observation")
    runs = runtime.list_runs(item_id)
    if len({r.id for r in runs}) != len(runs) or any(not r.terminal for r in runs):
        _invalid("Source has active, queued, unknown or ambiguous Runs")
    return sorted([asdict(r) for r in runs], key=lambda r: r["id"])


def _attachment(store, item_id, ref):
    if (
        not isinstance(ref, dict)
        or any(not ref.get(k) for k in ("attachment_id", "comment_id", "sha256"))
        or type(ref.get("bytes")) is not int
        or ref["bytes"] <= 0
    ):
        _invalid("Full failed report/ledger native reference is missing")
    observed = store.observe_verification_attachment(item_id, ref)
    content = observed.content
    if (
        observed.attachment_id != ref["attachment_id"]
        or observed.comment_id != ref["comment_id"]
        or observed.sha256 != ref["sha256"]
        or hashlib.sha256(content).hexdigest() != ref["sha256"]
        or len(content) != ref["bytes"]
    ):
        _invalid("Full failed report/ledger bytes or native identity changed")
    try:
        text = content.decode("utf-8")
        body = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValidationError("Full failed report/ledger cannot be decoded") from exc
    if not isinstance(body, dict):
        _invalid("Full failed report/ledger is malformed")
    return {
        "ref": copy.deepcopy(ref),
        "native": asdict(observed) | {"content": text},
        "body": body,
    }


def capture_source(manifest, node_id, store, runtime, *, held=False):
    node = manifest.nodes.get(node_id)
    if node is None or node.status == "done" or node.merged:
        _invalid("Authorized target must be an existing unmerged non-DONE node")
    result = {"node": node_id, "item": None, "runs": [], "budget": None}
    if not node.work_item_id:
        if held:
            _invalid("Held source has no WorkItem")
        return result
    item = store.get_work_item(node.work_item_id)
    result.update(
        item=plain(asdict(item)),
        runs=terminal_runs(runtime, item.id),
        budget=preserved_amendment_budget(manifest, node_id, item),
    )
    contract = (
        item.contract
        if isinstance(item.contract, dict)
        else _dump_contract(item.contract)
    )
    if (
        item.id != node.work_item_id
        or item.workspace_id != store.config.workspace_id
        or item.dag_key != node_id
        or contract != _dump_contract(node.contract)
    ):
        _invalid(
            "Actual affected WorkItem identity or contract disagrees with manifest authority"
        )
    if not held:
        if item.unknown_persisted_fields:
            _invalid("Actual affected source has unknown persisted control fields")
        if item.decision_required:
            from types import SimpleNamespace
            from ..pipeline.amendment import _validate_amendment_admission

            _validate_amendment_admission(
                SimpleNamespace(store=store), manifest, "<manifest>", [node_id]
            )
        return result
    decision = item.decision_required
    if (
        item.id != node.work_item_id
        or item.workspace_id != store.config.workspace_id
        or item.dag_key != node_id
        or item.kind != TaskKind.DEVELOP
        or item.phase != TaskPhase.AUTHORING
        or item.status != WorkItemStatus.BLOCKED
        or node.status != "blocked"
        or not isinstance(decision, dict)
        or decision.get("schema") != "omac.decision-required/v1"
        or decision.get("kind") != "develop"
        or decision.get("phase") != "authoring"
        or decision.get("resume_issue_id") != item.id
        or item.agent_run_failed
        or item.agent_run_finished_without_submit
        or item.review_continuation
        or item.reviewer_run_baseline
        or item.unknown_persisted_fields
    ):
        _invalid("Source is not a complete held authoring decision")
    contract = (
        item.contract
        if isinstance(item.contract, dict)
        else _dump_contract(item.contract)
    )
    if contract != _dump_contract(node.contract):
        _invalid("Source contract differs from manifest authority")
    reason = decision.get("reason_code")
    if reason == "worker-decision-required":
        # Reuse the published v2 validator; publication's running-Run gate is
        # deliberately not reused for a now-terminal retained decision.
        from ..pipeline.worker_decision import _validate, SCHEMA as BLOCKER_SCHEMA

        blocker = decision.get("blocker")
        if not isinstance(blocker, dict):
            _invalid("Worker blocker is missing")
        _validate(blocker, item.id)
        intent = item.worker_handoff
        binding = review_context_binding(item)
        target = [r for r in result["runs"] if r["id"] == blocker.get("run_id")]
        if (
            blocker.get("schema") != BLOCKER_SCHEMA
            or blocker.get("reason_code") != "owner-decision-required"
            or blocker.get("issue_id") != item.id
            or blocker.get("review_context_binding") != binding
            or decision.get("review_context_binding") != binding
            or intent is None
            or not intent.is_causally_bound()
            or intent.review_context_binding != binding
            or blocker.get("handoff_generation") != intent.generation
            or blocker.get("worker") != intent.target_worker
            or item.worker != intent.target_worker
            or blocker.get("run_id") != intent.target_run_id
            or blocker.get("contract_ref") not in (contract or {})
            or len(target) != 1
            or target[0]["kind"] != "direct"
            or target[0]["trigger_kind"] not in ("issue_assignment", "rerun")
            or target[0]["agent_id"] != intent.target_agent_id
            or target[0]["id"] in intent.baseline_direct_run_ids
        ):
            _invalid("Worker decision full causal source binding is stale or invalid")
        feedback = intent.source_review_feedback
        if (
            not isinstance(feedback, dict)
            or feedback.get("verdict") != "reject"
            or not intent.source_review_subject_digest
        ):
            _invalid("Original failed-review subject/feedback is missing")
        source_item = item
        result["subject_digest"] = intent.source_review_subject_digest
    elif reason == PACKAGE_REASON:
        if (
            decision.get("gate") != "operator-recovery"
            or decision.get("node_id") != node_id
            or not decision.get("authority")
            or not decision.get("source_amendment_issue")
            or not decision.get("source_blocker")
            or item.worker_handoff is not None
        ):
            _invalid("Distinct Package decision source authority is malformed")
        source_item = store.get_work_item(decision["source_amendment_issue"])
        feedback = store.recover_review_rework_context(source_item.id)
        if (
            source_item.kind != TaskKind.AMENDMENT
            or source_item.workspace_id != item.workspace_id
            or feedback.get("verdict") != "reject"
            or not feedback.get("subject_digest")
        ):
            _invalid(
                "Package requires the original rejected amendment source, never its replay"
            )
        result["rejected_source"] = {
            "item": plain(asdict(source_item)),
            "runs": terminal_runs(runtime, source_item.id),
            "context": copy.deepcopy(feedback),
        }
        result["subject_digest"] = feedback["subject_digest"]
    else:
        _invalid("Decision is outside the narrow owner/Package assessment capability")
    result["failed"] = {
        name: _attachment(store, source_item.id, feedback.get(name))
        for name in ("report_ref", "ledger_ref")
    }
    from .review_convergence import validate_review_ledger, _review_report_digest

    ledger = result["failed"]["ledger_ref"]["body"]
    try:
        validate_review_ledger(ledger)
    except ValueError as exc:
        raise ValidationError("Full failed ledger is malformed: " + str(exc)) from exc
    cycles = ledger["cycles"]
    report = result["failed"]["report_ref"]["body"]
    report_digest = _review_report_digest(report)
    from types import SimpleNamespace
    from .evidence import validate_review_evidence

    # Historical obligation IDs come from the actual validated last cycle,
    # never from the current authoring contract or invented old obligations.
    if not cycles:
        _invalid("Full failed ledger has no historical rejected cycle")
    historical = SimpleNamespace(
        review_verdict="reject",
        review_report=report,
        review_obligations=[
            {"obligation_id": key} for key in cycles[-1]["obligation_results"]
        ],
        review_ledger=ledger,
        review_generation=None,
        review_ledger_generation=None,
        review_subject_digest=cycles[-1]["subject_digest"],
    )
    errors = validate_review_evidence(SimpleNamespace(contract=None), historical)
    for flag in (
        "diff_reviewed",
        "tests_rerun",
        "integration_tests_rerun",
        "coverage_checked",
    ):
        if report.get(flag) is not True:
            errors.append("historical review_report." + flag + " must be true")
    for field in ("review_goals", "acceptance_mapping", "integration_gate_mapping"):
        if not isinstance(report.get(field), list) or not report[field]:
            errors.append(
                "historical review_report." + field + " must be a non-empty list"
            )
    if errors:
        _invalid("Full historical failed report is malformed: " + "; ".join(errors))
    if (
        not cycles
        or cycles[-1].get("subject_digest") != result["subject_digest"]
        or cycles[-1].get("verdict") != "reject"
        or cycles[-1].get("report_digest") != report_digest
        or (
            reason == "worker-decision-required"
            and cycles[-1].get("round") != intent.source_review_round
        )
    ):
        _invalid(
            "Full failed report/ledger do not bind the latest original rejected subject/round"
        )
    if reason == PACKAGE_REASON:
        for name, field in [
            ("report_ref", "source_report_sha256"),
            ("ledger_ref", "source_ledger_sha256"),
        ]:
            if result["failed"][name]["ref"]["sha256"] != decision.get(field):
                _invalid(
                    "Distinct Package original failed bytes differ from its decision"
                )
        if not any(
            isinstance(b, dict) and b.get("blocker_id") == decision["source_blocker"]
            for b in result["failed"]["ledger_ref"]["body"].get("blockers", [])
        ):
            _invalid(
                "Package original blocker is missing from the full retained ledger"
            )
    # Reread after native evidence/Run calls. This is an observation sandwich,
    # not a cross-platform atomic compare-and-dispatch primitive.
    if terminal_runs(runtime, item.id) != result["runs"]:
        _invalid("Source Run identity changed during full evidence observation")
    if reason == PACKAGE_REASON and (
        plain(asdict(store.get_work_item(source_item.id)))
        != result["rejected_source"]["item"]
        or terminal_runs(runtime, source_item.id) != result["rejected_source"]["runs"]
    ):
        _invalid("Original rejected Package source changed during evidence observation")
    if plain(asdict(store.get_work_item(item.id))) != result["item"]:
        _invalid("Source control changed during full evidence observation")
    return result


def verify_request(manifest, request, store, runtime):
    if (
        not isinstance(request, dict)
        or request.get("schema") != SCHEMA
        or set(request)
        != {
            "schema",
            "manifest",
            "blocked_nodes",
            "allowed_nodes",
            "sources",
            "inputs",
            "retry_policy",
            "remaining_budgets",
            "required_inputs",
        }
        or not isinstance(request.get("blocked_nodes"), list)
        or not request["blocked_nodes"]
        or not isinstance(request.get("allowed_nodes"), list)
        or not all(
            isinstance(k, str) and k.strip()
            for k in request["blocked_nodes"] + request["allowed_nodes"]
        )
        or not isinstance(request.get("sources"), dict)
        or not isinstance(request.get("inputs"), dict)
        or set(request["blocked_nodes"]) - set(request["allowed_nodes"])
        or len(set(request["allowed_nodes"])) != len(request["allowed_nodes"])
        or set(request["sources"]) != set(request["allowed_nodes"])
        or request["manifest"] != manifest_source(manifest)
    ):
        _invalid(
            "Immutable owner request or FULL manifest/DONE/history authority changed"
        )
    from .config import load_config, resolve_retry
    from ..pipeline.owner_amendment import required_inputs

    required = request["required_inputs"]
    if (
        not isinstance(required, dict)
        or not isinstance(required.get("manifest_path"), str)
        or required_inputs(manifest, required["manifest_path"]) != required
    ):
        _invalid("Full required-contract/authoritative acceptance input bytes changed")
    policy = request["retry_policy"]
    if (
        not isinstance(policy, dict)
        or set(policy) != {"config_path", "limits"}
        or not isinstance(policy["config_path"], str)
        or resolve_retry(load_config(policy["config_path"])) != policy["limits"]
        or request["remaining_budgets"]
        != {
            k: remaining_budget(v, policy["limits"])
            for k, v in request["sources"].items()
        }
    ):
        _invalid("Exact configured retry limits or derived remaining budgets changed")
    for key in request["allowed_nodes"]:
        if (
            capture_source(
                manifest, key, store, runtime, held=key in request["blocked_nodes"]
            )
            != request["sources"][key]
        ):
            _invalid(
                "Full decision/report/ledger/source/handoff/Run/budget CAS changed"
            )


def authorized_request(manifest, request_digest, store, runtime):
    if not isinstance(request_digest, str) or len(request_digest) != 64:
        _invalid("Exact owner request digest is missing or malformed")
    entries = manifest.meta.get(JOURNAL)
    entry = entries.get(request_digest) if isinstance(entries, dict) else None
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("approval"), dict)
        or digest(entry.get("request")) != request_digest
        or entry.get("approval", {}).get("request_sha256") != request_digest
        or entry.get("approval_sha256") != digest(entry.get("approval"))
        or not entry.get("approval", {}).get("authority")
        or not entry.get("approval", {}).get("reason")
        or entry.get("state") not in ("approved", "assessment_started", "reviewed")
    ):
        _invalid("Fresh exact operator resolution approval is missing or malformed")
    verify_request(manifest, entry["request"], store, runtime)
    return entry


def validate_affected(manifest, proposal, store, runtime):
    """Use derived operations/recovery, never caller blocked-node selection."""
    from .amendment import _minimal_rerun, _changed_node_ids

    minimal, _, _ = _minimal_rerun(manifest, proposal)
    affected = set(_changed_node_ids(proposal)) | {
        n for ids in minimal.values() for n in ids
    }
    request_digest = proposal.get("owner_resolution")
    if request_digest and not any(
        op.get("op") in ("update", "add", "remove", "update-responsibility")
        for op in proposal.get("operations", [])
    ):
        _invalid(
            "Owner assessment must review a changed coherent definition, not unchanged-scope resume"
        )
    entry = (
        authorized_request(manifest, request_digest, store, runtime)
        if request_digest
        else None
    )
    if (
        entry is not None
        and proposal.get("owner_resolution_approval") != entry["approval_sha256"]
    ):
        _invalid(
            "Actual proposal is not bound to the exact fresh coordinator resolution"
        )
    if entry is not None and (
        proposal.get("budget_policy") != "preserve"
        or affected - set(entry["request"]["allowed_nodes"])
    ):
        _invalid(
            "Actual affected/derived targets exceed exact authorization or budget_policy is not preserve"
        )
    for node_id in affected:
        node = manifest.nodes.get(node_id)
        if node and node.work_item_id:
            try:
                decision = store.get_work_item(node.work_item_id).decision_required
            except WorkItemNotFoundError as exc:
                raise ValidationError(
                    "Actual affected WorkItem disappeared; inspect current source before proposing or accepting"
                ) from exc
            if isinstance(decision, dict) and decision.get("reason_code") in (
                "worker-decision-required",
                PACKAGE_REASON,
            ):
                if entry is None or node_id not in entry["request"]["blocked_nodes"]:
                    _invalid(
                        "Actual affected held decision requires its exact source-bound resolution"
                    )
                capture_source(manifest, node_id, store, runtime, held=True)
    return entry


def verify_reviewed_resolution(manifest, amendment, store, runtime, manifest_path):
    entry = validate_affected(manifest, amendment, store, runtime)
    if entry is None:
        return
    if entry["request"]["required_inputs"]["manifest_path"] != str(
        Path(manifest_path).resolve()
    ):
        _invalid("Reviewed resolution belongs to a different canonical manifest path")
    from .amendment import amendment_review_binding, _proposal_core

    item = store.get_work_item((amendment.get("review") or {}).get("issue_id"))
    if (
        entry.get("state") != "reviewed"
        or entry.get("issue_id") != item.id
        or entry.get("review_binding") != amendment_review_binding(item)
        or item.kind != TaskKind.AMENDMENT
        or item.phase != TaskPhase.CONFIRMATION
        or item.review_verdict not in ("pass", "pass-with-nits")
        or _proposal_core(yaml.safe_load(item.deliverable or ""))
        != _proposal_core(amendment)
    ):
        _invalid("Assessment lacks its fresh independently reviewed exact delivery")
    checkpoint = entry.get("review_dispatches", {}).get(item.review_subject_digest)
    if not isinstance(checkpoint, dict) or checkpoint.get("item_id") != item.id:
        _invalid("Reviewed assessment dispatch checkpoint changed")
    if planner_source(store, runtime, item, entry) != entry.get("planner_source"):
        _invalid("Authorized same-Planner submitted source changed before accept")
    actual = fresh_review_source(store, runtime, item, checkpoint["reviewer"])
    expected = entry.get("review_source")
    if item.review_verdict == "pass-with-nits" and isinstance(expected, dict):
        # The existing apply gate has already verified its exact operator nits
        # marker. Only that own intentional update and its platform timestamp
        # may differ; original complete review evidence remains archived.
        actual["item"]["review_nits_acceptance"] = expected["item"][
            "review_nits_acceptance"
        ]
        actual["item"]["updated_at"] = expected["item"]["updated_at"]
    if actual != expected:
        _invalid(
            "Fresh reviewed full report/ledger/native source changed before accept"
        )


def fresh_review_source(store, runtime, item, reviewer):
    from .review_convergence import validate_review_ledger, _review_report_digest
    from ..pipeline.tasks import _review_evidence_errors
    from ..pipeline.tasks import _payload_contract

    if (
        item.kind != TaskKind.AMENDMENT
        or item.phase != TaskPhase.CONFIRMATION
        or item.review_verdict not in ("pass", "pass-with-nits")
        or not item.worker
        or not reviewer
        or item.reviewer not in (None, "", reviewer)
        or item.worker == reviewer
        or not item.review_subject_digest
        or _review_evidence_errors(_payload_contract(item.contract), item)
    ):
        _invalid("Fresh independent assessment Review is not consumable")
    snapshot = {
        "item": plain(asdict(item)),
        "runs": terminal_runs(runtime, item.id),
        "report": _attachment(store, item.id, item.review_report_ref),
        "ledger": _attachment(store, item.id, item.review_ledger_ref),
    }
    ledger = snapshot["ledger"]["body"]
    try:
        validate_review_ledger(ledger)
    except ValueError as exc:
        raise ValidationError(
            "Fresh assessment review ledger is malformed: " + str(exc)
        ) from exc
    cycles = ledger["cycles"]
    if (
        not cycles
        or cycles[-1].get("subject_digest") != item.review_subject_digest
        or cycles[-1].get("verdict") != item.review_verdict
        or cycles[-1].get("report_digest")
        != _review_report_digest(snapshot["report"]["body"])
    ):
        _invalid(
            "Fresh full assessment report/ledger do not bind its reviewed delivery"
        )
    observed = snapshot["report"]["native"]
    reviewer_id = store.resolve_agent_id(reviewer)
    matching = [
        r
        for r in snapshot["runs"]
        if r["id"] == observed.get("task_id")
        and r["kind"] == "direct"
        and r["trigger_kind"] in ("issue_assignment", "rerun")
        and r["status"] == "completed"
        and r["agent_id"] == reviewer_id
    ]
    if (
        observed.get("uploader_type") != "agent"
        or observed.get("uploader_id") != reviewer_id
        or reviewer_id == store.resolve_agent_id(item.worker)
        or len(matching) != 1
    ):
        _invalid(
            "Fresh assessment report lacks its exact completed independent Reviewer Run"
        )
    if (
        plain(asdict(store.get_work_item(item.id))) != snapshot["item"]
        or terminal_runs(runtime, item.id) != snapshot["runs"]
    ):
        _invalid(
            "Assessment control/Run changed during fresh review evidence observation"
        )
    return snapshot


def planner_source(store, runtime, item, entry):
    approved = entry.get("assessment", {})
    baselines = entry.get("authoring_dispatches", [])
    if (
        item.kind != TaskKind.AMENDMENT
        or item.workspace_id != store.config.workspace_id
        or item.dag_key != entry.get("dag_key")
        or item.worker != approved.get("orchestrator")
        or not baselines
        or baselines[-1].get("item_id") != item.id
        or not item.deliverable
        or not item.deliverable_ref
    ):
        _invalid(
            "Assessment identity/authorized same-Planner delivery source is missing or changed"
        )
    observed = _attachment(store, item.id, item.deliverable_ref)
    if (
        hashlib.sha256(item.deliverable.encode()).hexdigest()
        != observed["ref"]["sha256"]
    ):
        _invalid(
            "Planner native deliverable bytes differ from the observed submitted content"
        )
    runs = terminal_runs(runtime, item.id)
    agent_id = store.resolve_agent_id(approved["orchestrator"])
    native = observed["native"]
    matches = [
        r
        for r in runs
        if r["id"] == native.get("task_id")
        and r["agent_id"] == agent_id
        and r["kind"] == "direct"
        and r["trigger_kind"] in ("issue_assignment", "rerun")
        and r["status"] == "completed"
        and r["id"] not in baselines[-1]["baseline_run_ids"]
    ]
    if (
        native.get("uploader_id") != agent_id
        or native.get("uploader_type") != "agent"
        or len(matches) != 1
    ):
        _invalid(
            "Submitted amendment lacks its exact completed authorized same-Planner Run"
        )
    return {
        "item_id": item.id,
        "worker": item.worker,
        "workspace_id": item.workspace_id,
        "dag_key": item.dag_key,
        "contract": plain(item.contract)
        if isinstance(item.contract, dict)
        else _dump_contract(item.contract),
        "delivery": observed,
        "run": matches[0],
    }


def guard_apply_resume(manifest, amendment, store, runtime, *, before_node=None):
    """Recheck untouched full sources; observe an unknown restore, never replay."""
    request_digest = amendment.get("owner_resolution")
    if not request_digest:
        return
    entry = manifest.meta.get(JOURNAL, {}).get(request_digest)
    if (
        not isinstance(entry, dict)
        or entry.get("state") != "reviewed"
        or digest(entry.get("request")) != request_digest
        or entry.get("approval_sha256") != amendment.get("owner_resolution_approval")
        or digest(entry.get("approval")) != entry.get("approval_sha256")
    ):
        _invalid(
            "Accepted recovery lost its exact original request/resolution authority"
        )
    request = entry["request"]
    from .manifest import loads_manifest
    from .amendment import _node_dict
    from .stage_recovery import (
        recovery_control_snapshot,
        classify_stage_recovery_observation,
    )

    original = loads_manifest(json.dumps(request["manifest"]))
    # All original DONE data and consumed/operator/source histories remain
    # exact even after this owned definition/application journal advances.
    for key, node in original.nodes.items():
        if node.status == "done":
            actual = manifest.nodes.get(key)
            if (
                actual is None
                or _node_dict(actual, include_runtime=True)
                != _node_dict(node, include_runtime=True)
                or actual.recovery_marker != node.recovery_marker
            ):
                _invalid("Full DONE object changed during accepted recovery")
    for key, value in original.meta.items():
        if (
            key not in {"amendment_apply", "last_amendment_id", "amendment_revision"}
            and manifest.meta.get(key) != value
        ):
            _invalid(
                "Original consumed/operator/approved history changed during accepted recovery"
            )
    from ..pipeline.owner_amendment import required_inputs
    from .config import load_config, resolve_retry

    required = request["required_inputs"]
    if (
        required_inputs(original, required["manifest_path"]) != required
        or resolve_retry(load_config(request["retry_policy"]["config_path"]))
        != request["retry_policy"]["limits"]
    ):
        _invalid(
            "Required source/acceptance/configured limits changed during accepted recovery"
        )
    ledger = manifest.meta.get("amendment_apply", {})
    if any(
        r.get("state") not in ("synced", "observed_progress")
        for r in ledger.get("nodes", {}).values()
    ):
        review_item = store.get_work_item(entry.get("issue_id"))
        checkpoint = entry.get("review_dispatches", {}).get(
            review_item.review_subject_digest
        )
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get("item_id") != review_item.id
        ):
            _invalid(
                "Pending recovery lost its fresh independent Review dispatch authority"
            )
        if planner_source(store, runtime, review_item, entry) != entry.get(
            "planner_source"
        ):
            _invalid("Authorized same-Planner source changed during pending recovery")
        actual_review = fresh_review_source(
            store, runtime, review_item, checkpoint["reviewer"]
        )
        expected_review = entry.get("review_source")
        if review_item.review_verdict == "pass-with-nits" and isinstance(
            expected_review, dict
        ):
            actual_review["item"]["review_nits_acceptance"] = expected_review["item"][
                "review_nits_acceptance"
            ]
            actual_review["item"]["updated_at"] = expected_review["item"]["updated_at"]
        if actual_review != expected_review:
            _invalid(
                "Full independent Review evidence/control changed during pending recovery"
            )
    checkpoints = entry.setdefault("recovery_intents", {})
    for key, source in request["sources"].items():
        if source.get("item") is not None:
            terminal_runs(runtime, source["item"]["id"])
        if source.get("rejected_source"):
            terminal_runs(runtime, source["rejected_source"]["item"]["id"])
        recovery = ledger.get("nodes", {}).get(key)
        if recovery is None or recovery.get("state") == "pending":
            if (
                capture_source(
                    original, key, store, runtime, held=key in request["blocked_nodes"]
                )
                != source
            ):
                _invalid(
                    "Complete original source changed before accepted node recovery"
                )
        # Historical failed bytes are immutable and are independently re-read
        # even when the accepted generation intentionally retires their live refs.
        for name, failed in source.get("failed", {}).items():
            source_id = (source.get("rejected_source") or {}).get(
                "item", source["item"]
            )["id"]
            if _attachment(store, source_id, failed["ref"]) != failed:
                _invalid(
                    "Original full failed report/ledger changed during accepted recovery"
                )
        if recovery is None or recovery.get("state") in ("synced", "observed_progress"):
            continue
        item = store.get_work_item(recovery["work_item_id"])
        terminal_runs(runtime, item.id)
        if key in checkpoints:
            observation = classify_stage_recovery_observation(
                recovery["stage"],
                recovery["baseline"],
                recovery_control_snapshot(item),
                expected_contract_sha256=recovery["expected_contract_sha256"],
                expected_review_subject=recovery.get("expected_review_subject"),
                expected_review_generation=recovery.get("expected_review_generation"),
                expected_bounce_baseline=recovery.get("bounce_baseline"),
            )
            if observation != "reached":
                from ..errors import NeedsDecision

                raise NeedsDecision(
                    "Accepted recovery outcome is unknown or partial; observe the same target without applying again",
                    report={
                        "reason_code": "owner-amendment-recovery-pending",
                        "node": key,
                        "outcome": "unknown_partial",
                        "next_action": "Repeat the same accept only after the original recovery target is fully observable",
                    },
                )
            # Anything outside the explicitly retired control projection must
            # still equal the complete original source; no hidden source edits.
            control = plain(asdict(item))
            mutable = {
                "contract",
                "contract_ref",
                "review_generation",
                "bounce_baseline",
                "review_verdict",
                "review_comment",
                "machine_feedback",
                "machine_feedback_ref",
                "review_report",
                "review_report_ref",
                "review_subject_digest",
                "review_obligations",
                "review_obligations_ref",
                "review_continuation",
                "reviewer_run_baseline",
                "worker_handoff",
                "delivery_identity",
                "decision_required",
                "review_nits_acceptance",
                "phase",
                "status",
                "reviewer",
                "platform_assignee_id",
                "updated_at",
            }
            if {k: v for k, v in control.items() if k not in mutable} != {
                k: v for k, v in source["item"].items() if k not in mutable
            }:
                _invalid(
                    "Unknown recovery target changed non-retired full source fields"
                )
        elif recovery.get("state") != "pending":
            _invalid(
                "Accepted recovery has activity without its owned once-only intention"
            )
    if before_node is not None:
        if before_node in checkpoints:
            _invalid("Recovery is already intended; do not replay its apply operation")
        checkpoints[before_node] = {
            "amendment_id": amendment["amendment_id"],
            "state": "intended",
        }

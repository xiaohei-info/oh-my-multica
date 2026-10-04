"""Public prepare + explicit operator resolution, with a once-only journal."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from ..core.manifest import load_manifest, save_manifest
from ..core.owner_amendment import (
    SCHEMA,
    JOURNAL,
    digest,
    manifest_source,
    capture_source,
    verify_request,
    retry_policy,
    remaining_budget,
    authorized_request,
    terminal_runs,
)
from ..core.taskmeta import TaskKind, TaskPhase
from ..errors import ValidationError, NeedsDecision


def request_inputs(manifest_path, report_file, docs, blocked_nodes):
    from .amendment import _validate_inputs, _docs_snapshot

    report, paths, root = _validate_inputs(
        manifest_path, report_file, docs, blocked_nodes
    )
    return {"report": report, "docs": _docs_snapshot(paths, project_root=root)}


def required_inputs(manifest, manifest_path):
    from .amendment import _manifest_project_root, _resolve_docs_input, _docs_snapshot
    import hashlib

    root = _manifest_project_root(manifest_path)
    paths = {
        p
        for node in manifest.nodes.values()
        if node.contract
        for p in node.contract.required_contracts
    }
    acceptance_file = manifest.meta.get("acceptance_file")
    if acceptance_file:
        acceptance_path = Path(manifest_path).parent / acceptance_file
        data = acceptance_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest.meta.get("acceptance_sha256"):
            raise ValidationError(
                "Full authoritative acceptance bytes differ from captured manifest identity"
            )
        paths.add(str(acceptance_path))
    resolved = [str(_resolve_docs_input(path, root)) for path in sorted(paths)]
    return {
        "manifest_path": str(Path(manifest_path).resolve()),
        "files": _docs_snapshot(resolved, project_root=root),
    }


def prepare_owner_amendment(
    engine,
    manifest_path,
    *,
    blocked_nodes,
    allowed_nodes,
    report_file,
    docs,
    output_file,
):
    from .amendment import _write_yaml_atomic

    if not blocked_nodes or set(blocked_nodes) - set(allowed_nodes):
        raise ValidationError(
            "Explicit blocked and allowed targets are required; run `omac dag amend prepare-owner --help`"
        )
    manifest = load_manifest(manifest_path)
    request = {
        "schema": SCHEMA,
        "manifest": manifest_source(manifest),
        "blocked_nodes": sorted(set(blocked_nodes)),
        "allowed_nodes": sorted(set(allowed_nodes)),
        "sources": {
            key: capture_source(
                manifest, key, engine.store, engine.runtime, held=key in blocked_nodes
            )
            for key in sorted(set(allowed_nodes))
        },
        "inputs": request_inputs(manifest_path, report_file, docs, blocked_nodes),
    }
    request["required_inputs"] = required_inputs(manifest, manifest_path)
    request["retry_policy"] = retry_policy(manifest_path)
    request["remaining_budgets"] = {
        k: remaining_budget(v, request["retry_policy"]["limits"])
        for k, v in request["sources"].items()
    }
    verify_request(load_manifest(manifest_path), request, engine.store, engine.runtime)
    if Path(output_file).exists():
        if load_request(output_file) != request:
            raise ValidationError(
                "Immutable request already exists with different content; choose a new output path"
            )
    else:
        _write_yaml_atomic(output_file, request, as_json=True)
    return {
        "state": "pending_operator_resolution",
        "request_file": output_file,
        "request_sha256": digest(request),
        "next_action": f"omac dag amend resolve-owner {manifest_path} {output_file} --request-sha256 {digest(request)} --authority <coordinator> --reason <assessment-resolution>",
    }


def load_request(path):
    from .amendment import load_amendment_file

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return load_amendment_file(path)
    if not isinstance(value, dict):
        raise ValidationError(
            "Owner request must be an immutable object; run `omac dag amend prepare-owner --help`"
        )
    return value


def resolve_owner_amendment(
    engine, manifest_path, request_file, *, request_sha256, authority, reason
):
    if (
        not isinstance(authority, str)
        or not authority.strip()
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValidationError(
            "Explicit new coordinator authority and assessment resolution reason are required"
        )
    request = load_request(request_file)
    if request.get("required_inputs", {}).get("manifest_path") != str(
        Path(manifest_path).resolve()
    ):
        raise ValidationError(
            "Exact operator resolution belongs to a different canonical manifest path"
        )
    if digest(request) != request_sha256:
        raise ValidationError(
            "Immutable request SHA256 differs from explicit operator approval"
        )
    manifest = load_manifest(manifest_path)
    verify_request(manifest, request, engine.store, engine.runtime)
    approval = {
        "request_sha256": request_sha256,
        "authority": authority,
        "reason": reason,
    }
    entries = manifest.meta.setdefault(JOURNAL, {})
    old = entries.get(request_sha256)
    if old is not None:
        if old.get("request") != request or old.get("approval") != approval:
            raise ValidationError(
                "Existing exact operator resolution cannot be replaced"
            )
    else:
        entries[request_sha256] = {
            "request": copy.deepcopy(request),
            "approval": approval,
            "approval_sha256": digest(approval),
            "state": "approved",
        }
        save_manifest(manifest, manifest_path)
    current = load_manifest(manifest_path).meta.get(JOURNAL, {}).get(request_sha256)
    if (
        current is None
        or current.get("request") != request
        or current.get("approval") != approval
    ):
        raise ValidationError(
            "Operator resolution write was not confirmed; observe the same exact request before continuing"
        )
    return {
        "state": current["state"],
        "request_sha256": request_sha256,
        "approval_sha256": current["approval_sha256"],
    }


def begin_assessment(
    engine,
    manifest_path,
    request_file,
    *,
    report_file,
    docs,
    blocked_nodes,
    orchestrator,
    reviewers,
    max_revisions,
):
    request = load_request(request_file)
    request_digest = digest(request)
    if request.get("required_inputs", {}).get("manifest_path") != str(
        Path(manifest_path).resolve()
    ):
        raise ValidationError(
            "Assessment request belongs to a different canonical manifest path"
        )
    manifest = load_manifest(manifest_path)
    entry = authorized_request(manifest, request_digest, engine.store, engine.runtime)
    if (
        request != entry["request"]
        or sorted(blocked_nodes) != request["blocked_nodes"]
        or request_inputs(manifest_path, report_file, docs, blocked_nodes)
        != request["inputs"]
    ):
        raise ValidationError(
            "Selectors/report/docs differ from the immutable authorized request"
        )
    assessment = {
        "orchestrator": orchestrator,
        "reviewers": reviewers,
        "max_revisions": max_revisions,
    }
    if entry.get("assessment", assessment) != assessment:
        raise ValidationError("Existing assessment agents/bounds cannot be replaced")
    key = "amend-owner-" + request_digest
    if entry["state"] == "approved":
        if (
            engine.store.find_work_item_by_dag_key(
                engine.store.config.workspace_id, key
            )
            is not None
        ):
            raise ValidationError(
                "New assessment identity already exists without this journal intent"
            )
        entry["state"] = "assessment_started"
        entry["dag_key"] = key
        entry["assessment"] = assessment
        save_manifest(manifest, manifest_path)  # Intent BEFORE any external effect.
        if load_manifest(manifest_path).meta[JOURNAL][request_digest] != entry:
            raise ValidationError(
                "Assessment intent write is unconfirmed; do not dispatch"
            )
        return request_digest, key, None
    # Unknown effects are observe-only: no new create/Planner dispatch. A fully
    # observed produced delivery may enter its first independent Review; a lost
    # Review dispatch must publish its bound verdict before it can continue.
    item = engine.store.find_work_item_by_dag_key(engine.store.config.workspace_id, key)
    if item is not None:
        item = engine.store.get_work_item(item.id)
    consumable = (
        item is not None
        and item.kind == TaskKind.AMENDMENT
        and item.deliverable
        and not item.decision_required
        and not item.agent_run_failed
        and not item.agent_run_finished_without_submit
        and item.phase in (TaskPhase.REVIEW, TaskPhase.CONFIRMATION)
        and (
            not entry.get("review_dispatches")
            or item.review_verdict in ("pass", "pass-with-nits")
        )
    )
    if not consumable:
        raise NeedsDecision(
            "Assessment outcome is unknown or incomplete; observe the existing attempt without dispatching again",
            report={
                "reason_code": "owner-amendment-assessment-pending",
                "dag_key": key,
                "item_id": item.id if item else None,
                "outcome": "unknown_partial",
                "next_action": "omac work show <existing-attempt-issue-id> --output json",
            },
        )
    terminal_runs(engine.runtime, item.id)
    from ..core.owner_amendment import planner_source

    planner_source(engine.store, engine.runtime, item, entry)
    return request_digest, key, item.id


def record_review(engine, manifest_path, request_digest, item):
    from ..core.amendment import amendment_review_binding

    manifest = load_manifest(manifest_path)
    entry = authorized_request(manifest, request_digest, engine.store, engine.runtime)
    terminal_runs(engine.runtime, item.id)
    from ..core.owner_amendment import planner_source

    if planner_source(engine.store, engine.runtime, item, entry) != entry.get(
        "planner_source"
    ):
        raise ValidationError(
            "Same-Planner native source changed after independent Review"
        )
    from ..core.owner_amendment import fresh_review_source

    checkpoint = entry.get("review_dispatches", {}).get(item.review_subject_digest)
    if not isinstance(checkpoint, dict) or checkpoint.get("item_id") != item.id:
        raise ValidationError("Fresh Review lacks its source-bound dispatch checkpoint")
    review_source = fresh_review_source(
        engine.store, engine.runtime, item, checkpoint["reviewer"]
    )
    report_run = review_source["report"]["native"].get("task_id")
    if report_run in {r["id"] for r in checkpoint["baseline_runs"]}:
        raise ValidationError(
            "Independent Review report cannot consume a pre-existing Run"
        )
    binding = amendment_review_binding(item)
    if any(not binding.get(k) for k in binding):
        raise ValidationError(
            "Assessment needs a complete fresh review subject/report/ledger/delivery"
        )
    if entry.get("state") == "reviewed" and entry.get("review_source") != review_source:
        raise ValidationError("Reviewed assessment evidence cannot be replaced")
    entry.update(
        state="reviewed",
        issue_id=item.id,
        review_binding=binding,
        review_source=review_source,
    )
    save_manifest(manifest, manifest_path)
    if load_manifest(manifest_path).meta[JOURNAL][request_digest] != entry:
        raise ValidationError("Reviewed assessment checkpoint is unconfirmed")
    return load_manifest(manifest_path)


def checkpoint_review_dispatch(engine, manifest_path, request_digest, item, reviewer):
    manifest = load_manifest(manifest_path)
    entry = authorized_request(manifest, request_digest, engine.store, engine.runtime)
    from ..core.owner_amendment import planner_source

    delivery_source = planner_source(engine.store, engine.runtime, item, entry)
    previous = entry.get("planner_source")
    if previous is not None and previous != delivery_source:
        raise ValidationError(
            "Approved Planner submitted source changed before independent Review"
        )
    entry["planner_source"] = delivery_source
    subject = item.review_subject_digest
    if not isinstance(subject, str) or not subject:
        raise ValidationError(
            "Independent Review subject must be bound before dispatch"
        )
    checkpoints = entry.setdefault("review_dispatches", {})
    if subject in checkpoints:
        raise NeedsDecision(
            "Review dispatch already intended; observe the same Run/verdict without redispatch",
            report={
                "reason_code": "owner-amendment-review-pending",
                "item_id": item.id,
                "outcome": "unknown_partial",
                "next_action": f"omac work show {item.id} --output json",
            },
        )
    checkpoints[subject] = {
        "item_id": item.id,
        "reviewer": reviewer,
        "baseline_runs": terminal_runs(engine.runtime, item.id),
    }
    save_manifest(manifest, manifest_path)
    if load_manifest(manifest_path).meta[JOURNAL][request_digest] != entry:
        raise ValidationError(
            "Review dispatch intention was not confirmed; do not dispatch"
        )


def checkpoint_authoring_dispatch(
    engine, manifest_path, request_digest, item_id, stage
):
    manifest = load_manifest(manifest_path)
    entry = authorized_request(manifest, request_digest, engine.store, engine.runtime)
    item = engine.store.observe_work_item_control(item_id).work_item
    if (
        item.kind != TaskKind.AMENDMENT
        or item.workspace_id != engine.store.config.workspace_id
        or item.dag_key != entry.get("dag_key")
        or item.worker != entry["assessment"]["orchestrator"]
    ):
        raise ValidationError("Planner identity changed before authoring dispatch")
    if stage == "before":
        records = entry.setdefault("authoring_dispatches", [])
        records.append(
            {
                "item_id": item.id,
                "baseline_run_ids": [
                    r["id"] for r in terminal_runs(engine.runtime, item.id)
                ],
            }
        )
        save_manifest(manifest, manifest_path)
        if load_manifest(manifest_path).meta[JOURNAL][request_digest] != entry:
            raise ValidationError(
                "Planner dispatch intention was not confirmed; do not assign/wake"
            )

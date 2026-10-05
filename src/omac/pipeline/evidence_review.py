"""Explicit, single-use recovery of a witnessed same-HEAD evidence submission.

This prepares independent review; it never dispatches a Run or grants a verdict.
"""

from copy import deepcopy
from dataclasses import asdict, is_dataclass, replace
import hashlib
import json
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from ..core.evidence import validate_worker_evidence
from ..core.manifest import load_manifest, save_manifest, _dump_contract
from ..core.review_convergence import (
    _review_report_digest,
    build_review_obligations,
    validate_review_ledger,
)
from ..core.stage_recovery import stage_recovery_subject
from ..core.taskmeta import (
    parse_worker_handoff,
    review_context_binding,
    TaskPhase,
    TaskKind,
)
from ..engines.models import WorkItemStatus, PullRequestReadiness
from ..errors import NeedsDecision, PlatformError, ValidationError
from .loop import _seal_worker_delivery, _parse_platform_time

SCHEMA = "omac.evidence-review-request/v1"
JOURNAL = "evidence_review_authorizations"


def _plain(value):
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if is_dataclass(value):
        return asdict(value)
    return value


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_plain,
        ).encode()
    ).hexdigest()


def _fail(message):
    raise ValidationError(
        message
        + "; re-run `omac node review-evidence --help` and prepare a fresh request; no guessed identity is allowed"
    )


def _state(item):
    fields = (
        "artifacts",
        "verification_ref",
        "contract_ref",
        "worker",
        "reviewer",
        "platform_assignee_id",
        "review_generation",
        "review_ledger_generation",
        "review_ledger_ref",
        "bounces",
        "bounce_baseline",
        "worker_handoff",
        "delivery_identity",
        "decision_required",
        "review_verdict",
        "review_report_ref",
        "review_comment",
        "review_subject_digest",
        "reviewer_run_baseline",
        "review_nits_acceptance",
        "machine_feedback_ref",
    )
    result = {k: deepcopy(_plain(getattr(item, k, None))) for k in fields}
    for key in (
        "reviewer",
        "platform_assignee_id",
        "decision_required",
        "review_verdict",
        "review_report_ref",
        "review_subject_digest",
        "reviewer_run_baseline",
        "review_nits_acceptance",
        "machine_feedback_ref",
        "worker_handoff",
    ):
        result[key] = result[key] or None
    result.update(
        status=item.status.value,
        phase=item.phase.value,
        binding=review_context_binding(item),
        obligations_sha256=_digest(item.review_obligations),
    )
    return result


def _runs(runtime, issue_id):
    runs = runtime.list_runs(issue_id)
    if any(not r.terminal for r in runs):
        _fail("An active or unknown Run exists")
    if len({r.id for r in runs}) != len(runs):
        _fail("Run identities are ambiguous")
    return runs


def _time(value):
    return _parse_platform_time(value) if isinstance(value, str) else None


def _witness(path, line_number):
    if type(line_number) is not int or line_number < 1:
        _fail("Witness line must be a positive integer")
    raw = Path(path).read_bytes()
    if len(raw) > 128 * 1024 * 1024:
        _fail("Witness exceeds 128 MiB")
    lines = raw.splitlines(keepends=True)
    if line_number > len(lines):
        _fail("Witness line is missing")
    line = lines[line_number - 1]
    try:
        record = json.loads(line)
        message = record["message"]
        if (
            not isinstance(message, dict)
            or message.get("role") != "toolResult"
            or message.get("isError") is not False
        ):
            _fail("Witness must be an original toolResult, not an Agent assertion")
        texts = [
            v["text"]
            for v in message["content"]
            if isinstance(v, dict) and v.get("type") == "text"
        ]
        snapshots = []
        for text in texts:
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict) and "id" in value and "metadata" in value:
                snapshots.append(value)
        if len(snapshots) != 1:
            _fail("Witness must contain one complete original Issue JSON snapshot")
        snapshot = snapshots[0]
        calls = []
        for index, prior_line in enumerate(lines[: line_number - 1], 1):
            try:
                prior = json.loads(prior_line)
            except ValueError:
                continue
            previous = prior.get("message", {}) if isinstance(prior, dict) else {}
            if not isinstance(previous, dict) or previous.get("role") != "assistant":
                continue
            content = previous.get("content", [])
            for part in content if isinstance(content, list) else []:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "toolCall"
                    and part.get("id") == message.get("toolCallId")
                ):
                    calls.append((index, prior, part, prior_line))
        if len(calls) != 1:
            _fail("Witness needs one matching original Issue read tool call")
        call_line, call_record, call, call_bytes = calls[0]
        arguments = call.get("arguments", {})
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if (
            call.get("name") != "bash"
            or message.get("toolName") != "bash"
            or not isinstance(command, str)
            or shlex.split(command)
            != ["multica", "issue", "get", snapshot["id"], "--output", "json"]
        ):
            _fail(
                "Witness must be the original Issue read, not echoed or transformed JSON"
            )
        metadata = snapshot["metadata"]

        def decode(key):
            value = metadata.get(key)
            return json.loads(value) if isinstance(value, str) else value

        intent = parse_worker_handoff(decode("worker_handoff"))
        baseline = decode("verification_ref")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            "Malformed original session witness; use complete original JSONL bytes"
        ) from exc
    if intent is None or not intent.is_causally_bound() or not intent.target_run_id:
        _fail("Historical handoff has no exact causal Run identity")
    proof = {
        "source_path": str(Path(path).resolve()),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "line": line_number,
        "record_sha256": hashlib.sha256(line).hexdigest(),
        "timestamp": record.get("timestamp"),
        "call_line": call_line,
        "call_record_sha256": hashlib.sha256(call_bytes).hexdigest(),
        "call_timestamp": call_record.get("timestamp"),
        "command": command,
        "issue_id": snapshot["id"],
    }
    return proof, intent, baseline


def _mapping(content, label):
    try:
        value = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValidationError(
            f"{label} is not valid structured UTF-8 evidence"
        ) from exc
    if not isinstance(value, dict) or not value:
        _fail(f"{label} must be a non-empty mapping")
    return value


def _attachment(store, issue_id, ref):
    if not isinstance(ref, dict) or not ref.get("sha256"):
        _fail("An exact attachment reference is required")
    observed = store.observe_verification_attachment(issue_id, ref)
    if (
        observed.attachment_id != ref.get("attachment_id")
        or observed.comment_id != ref.get("comment_id")
        or observed.sha256 != ref["sha256"]
        or hashlib.sha256(observed.content).hexdigest() != ref["sha256"]
        or type(ref.get("bytes")) is not int
        or len(observed.content) != ref.get("bytes")
    ):
        _fail("Attachment identity or downloaded bytes changed")
    return observed


def _verify(store, runtime, manifest, key, witness_path, line_number):
    node = manifest.nodes.get(key)
    if (
        node is None
        or not node.work_item_id
        or not node.reviewer
        or node.merged
        or node.status == "done"
    ):
        _fail("An existing unmerged node with an independent Reviewer is required")
    if node.worker == node.reviewer:
        _fail("Worker and Reviewer must be independent")
    item = store.get_work_item(node.work_item_id)
    if item.kind != TaskKind.DEVELOP:
        _fail("This capability is restricted to develop evidence")
    proof, historical, baseline_ref = _witness(witness_path, line_number)
    binding = review_context_binding(item)
    if (
        proof["issue_id"] != item.id
        or historical.review_context_binding != binding
        or binding["contract_sha256"] != _digest(_dump_contract(node.contract))
        or historical.target_worker != node.worker
        or item.worker != node.worker
        or store.resolve_agent_id(node.worker) != historical.target_agent_id
        or store.resolve_agent_id(node.reviewer) == historical.target_agent_id
        or historical.source_review_verdict != "reject"
        or not historical.baseline_pr_head_sha
        or not isinstance(baseline_ref, dict)
        or baseline_ref.get("attachment_id")
        != historical.baseline_verification_attachment_id
    ):
        _fail(
            "Witness issue, contract, Worker, reject or baseline binding does not match"
        )
    runs = _runs(runtime, item.id)
    target = next((r for r in runs if r.id == historical.target_run_id), None)
    if (
        target is None
        or target.kind != "direct"
        or target.status != "completed"
        or not target.formal
        or target.agent_id != historical.target_agent_id
        or target.id in historical.baseline_direct_run_ids
    ):
        _fail("The original completed direct Worker Run cannot be verified")
    start, end, recorded = (
        _time(v) for v in (target.created_at, target.updated_at, proof["timestamp"])
    )
    called = _time(proof["call_timestamp"])
    if (
        any(v is None or v.tzinfo is None for v in (start, end, recorded, called))
        or not start <= called <= recorded <= end
    ):
        _fail("Witness timestamp is outside the original Run")
    old = _attachment(store, item.id, baseline_ref)
    old_time = _time(old.created_at)
    if old_time is None or old_time.tzinfo is None or old_time >= start:
        _fail("Historical baseline was not present before the original Run")
    candidate = _attachment(store, item.id, item.verification_ref)
    if (
        candidate.task_id != target.id
        or candidate.uploader_id != target.agent_id
        or candidate.uploader_type != "agent"
    ):
        _fail(
            "New verification is not attributed to the exact original Worker Run and Agent"
        )
    created = _time(candidate.created_at)
    if created is None or created.tzinfo is None or not start <= created <= end:
        _fail("New verification is outside the original Run window")
    if candidate.sha256 == old.sha256:
        _fail("Verification bytes are unchanged from the historical baseline")
    verification = _mapping(candidate.content, "Verification")
    if verification != item.verification or not isinstance(verification, dict):
        _fail("Verification projection differs from freshly downloaded bytes")
    feedback = historical.source_review_feedback or {}
    report_ref, ledger_ref = feedback.get("report_ref"), feedback.get("ledger_ref")
    report_blob = _attachment(store, item.id, report_ref)
    ledger_blob = _attachment(store, item.id, ledger_ref)
    if (
        report_blob.uploader_type != "agent"
        or not report_blob.uploader_id
        or report_blob.uploader_id == historical.target_agent_id
    ):
        _fail("Original reject must be uploaded by an independent Reviewer Agent")
    report, ledger = (
        _mapping(report_blob.content, "Reject report"),
        _mapping(ledger_blob.content, "Reject ledger"),
    )
    try:
        validate_review_ledger(ledger)
    except ValueError as exc:
        raise ValidationError(
            "Original review ledger is invalid; preserve it and stop"
        ) from exc
    if not isinstance(report, dict):
        _fail("Original reject report must be structured")
    latest = ledger["cycles"][-1] if ledger["cycles"] else {}
    if (
        latest.get("verdict") != "reject"
        or latest.get("subject_digest") != historical.source_review_subject_digest
        or latest.get("report_digest") != _review_report_digest(report)
        or item.review_ledger_ref != ledger_ref
        or item.review_ledger != ledger
    ):
        _fail("Original reject report, subject and preserved ledger do not match")
    artifacts = verification.get("retrievable_artifacts")
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 32:
        _fail("Verification must identify a bounded immutable published artifact set")
    rows = []
    total_bytes = 0
    pr = (item.artifacts or {}).get("pr_url", "")
    parsed_pr = urlsplit(pr)
    if (
        parsed_pr.scheme != "https"
        or parsed_pr.netloc != "github.com"
        or not re.fullmatch(r"/[^/]+/[^/]+/pull/[0-9]+", parsed_pr.path)
        or parsed_pr.query
        or parsed_pr.fragment
    ):
        _fail("This recovery profile requires an exact GitHub PR URL")
    repo = parsed_pr.path.split("/")[1:3]
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("url"), str):
            _fail("Every published artifact must have a URL and SHA256")
        url = artifact["url"]
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/[^/]+/[^/]+/blob/[0-9a-f]{40}/.+", parsed.path)
        ):
            _fail("Only immutable GitHub commit artifact URLs are supported")
        if parsed.path.split("/")[1:3] != repo:
            _fail("Published artifacts must belong to the PR repository")
        body = store.read_immutable_artifact(url)
        total_bytes += len(body)
        if total_bytes > 64 * 1024 * 1024:
            _fail("Artifact set exceeds 64 MiB")
        digest = hashlib.sha256(body).hexdigest()
        if digest != artifact.get("sha256") or len(body) > 16 * 1024 * 1024:
            _fail("Published artifact digest or size is invalid")
        rows.append({"url": url, "sha256": digest, "bytes": len(body)})
    if len({r["url"] for r in rows}) != len(rows):
        _fail("Duplicate artifact URLs are not permitted")
    rows.sort(key=lambda r: r["url"])
    baseline_verification = _mapping(old.content, "Historical baseline")
    old_artifacts = (
        baseline_verification.get("retrievable_artifacts")
        if isinstance(baseline_verification, dict)
        else None
    )
    if isinstance(old_artifacts, list) and {
        (a.get("url"), a.get("sha256")) for a in old_artifacts if isinstance(a, dict)
    } == {(r["url"], r["sha256"]) for r in rows}:
        _fail("Published artifact set is unchanged from the historical baseline")
    readiness = store.read_pull_request_readiness(pr)
    if (
        not isinstance(readiness, PullRequestReadiness)
        or readiness.is_draft
        or readiness.state.upper() != "OPEN"
    ):
        _fail("PR must remain open and non-draft")
    errors = validate_worker_evidence(node, item)
    if errors:
        _fail("Normal Worker evidence validation failed: " + "; ".join(errors))
    identity = _seal_worker_delivery(
        store, manifest, key, item, historical, target, attachment=candidate
    )
    if identity.pr_head_sha != historical.baseline_pr_head_sha:
        _fail("This entry point only permits the exact rejected HEAD")
    immutable = {
        "issue_id": item.id,
        "node_id": key,
        "binding": binding,
        "pr_url": identity.pr_url,
        "head_sha": identity.pr_head_sha,
        "run": _plain(target),
        "historical_handoff": historical.as_dict(),
        "baseline_ref": baseline_ref,
        "verification_ref": item.verification_ref,
        "reject_report_ref": report_ref,
        "reject_ledger_ref": ledger_ref,
        "artifacts": rows,
        "artifact_set_sha256": _digest(rows),
        "witness": proof,
        "manifest_authority_sha256": _digest(
            {k: manifest.meta.get(k) for k in ("last_amendment_id", "amendment_apply")}
        ),
        "node_definition_sha256": _digest(
            {
                "worker": node.worker,
                "reviewer": node.reviewer,
                "contract": _dump_contract(node.contract),
                "blocked_by": node.blocked_by,
            }
        ),
    }
    return item, immutable, identity, runs


def _initial_control(item, immutable):
    handoff = item.worker_handoff
    source = immutable["historical_handoff"]
    if (
        item.phase != TaskPhase.AUTHORING
        or item.status != WorkItemStatus.BLOCKED
        or item.platform_assignee_id
        or item.reviewer
        or item.delivery_identity
        or any(
            getattr(item, field, None)
            for field in (
                "review_verdict",
                "review_report_ref",
                "review_report",
                "review_subject_digest",
                "reviewer_run_baseline",
                "review_nits_acceptance",
                "machine_feedback_ref",
                "review_comment",
            )
        )
        or (item.decision_required or {}).get("reason_code")
        != "evidence-only-rework-head-policy"
        or handoff is None
        or not handoff.is_causally_bound()
        or handoff.review_context_binding != immutable["binding"]
        or handoff.source_review_subject_digest
        != source["source_review_subject_digest"]
        or handoff.source_review_verdict != "reject"
        or (handoff.source_review_feedback or {}).get("report_ref")
        != immutable["reject_report_ref"]
    ):
        _fail(
            "Current blocked control no longer identifies this same-HEAD rejected recovery"
        )


def preview_evidence_review(
    store,
    runtime,
    manifest,
    key,
    witness_path,
    line_number,
    reason,
    *,
    expected_witness_sha256,
):
    if not isinstance(reason, str) or not reason.strip() or len(reason.encode()) > 2048:
        _fail("An explicit bounded operator reason is required")
    item, immutable, identity, runs = _verify(
        store, runtime, manifest, key, witness_path, line_number
    )
    if immutable["witness"]["file_sha256"] != expected_witness_sha256:
        _fail(
            "Original session bytes do not match the explicitly approved witness SHA256"
        )
    _initial_control(item, immutable)
    return {
        "schema": SCHEMA,
        "reason": reason,
        "tuple": immutable,
        "expected_control": _state(item),
        "runs_sha256": _digest(
            sorted((_plain(r) for r in runs), key=lambda r: r["id"])
        ),
    }


def _apply_review_request(store, runtime, manifest_path, key, request, token, *,
                          schema, journal, verifier, initial_control,
                          obligation_transform=None, subject_builder=None,
                          pre_write_verify=None, checkpoint_confirm=None):
    """Shared one-time Controller seal/review transition; no Agent dispatch or verdict."""
    manifest = load_manifest(manifest_path)
    def persist_checkpoint():
        save_manifest(manifest, manifest_path)
        if checkpoint_confirm is not None:
            checkpoint_confirm(manifest)
    value = request["tuple"]
    entries = manifest.meta.get(journal, {})
    if not isinstance(entries, dict):
        _fail("Authorization journal is not a mapping")
    existing = entries.get(token)
    if token in entries and (
        not isinstance(existing, dict)
        or existing.get("request") != request
        or type(existing.get("step")) is not int
        or not 0 <= existing["step"] <= 8
        or existing.get("state") not in {"pending", "consumed"}
        or (existing.get("state") == "consumed" and existing.get("step") != 8)
    ):
        _fail("Invalid authorization journal")
    if existing and existing.get("request_sha256") != _digest(request):
        _fail("Authorization changed after preparation")
    if existing and existing.get("state") == "consumed":
        raise NeedsDecision(
            "Authorization already consumed; inspect the independent review, do not replay it"
        )
    with store.worker_control_lock(value.get("issue_id", "")):
        item, actual, identity, runs = verifier(manifest)
        if (
            actual != value
            or _digest(sorted((_plain(r) for r in runs), key=lambda r: r["id"]))
            != request["runs_sha256"]
        ):
            _fail("Prepared tuple or Run set changed")
        if existing is None:
            initial_control(item, actual)
            prepared = {
                "schema": schema,
                "reason": request["reason"],
                "tuple": actual,
                "expected_control": _state(item),
                "runs_sha256": _digest(
                    sorted((_plain(r) for r in runs), key=lambda r: r["id"])
                ),
            }
            if prepared != request:
                _fail("Current control changed since preview")
        bound = replace(
            item,
            delivery_identity=identity,
            review_ledger_generation=item.review_generation,
        )
        obligations = build_review_obligations(bound)
        if obligation_transform is not None:
            obligations = obligation_transform(obligations, actual)
        subject = (subject_builder(manifest, key, bound) if subject_builder is not None
                   else stage_recovery_subject(manifest.nodes[key], bound))
        states = [deepcopy(request["expected_control"])]
        changes = [
            {"delivery_identity": identity.as_dict()},
            {"worker_handoff": None},
            {"review_ledger_generation": item.review_generation},
            {"obligations_sha256": _digest(obligations)},
            {"review_subject_digest": subject},
            {"phase": "review"},
            {"decision_required": None},
            {"status": "in_review"},
        ]
        for change in changes:
            states.append({**deepcopy(states[-1]), **change})
        current = _state(store.get_work_item(item.id))
        progress = int(existing.get("step", 0)) if existing else 0
        allowed = [progress] + ([progress + 1] if progress < len(changes) else [])
        matches = [i for i in allowed if current == states[i]]
        if not matches:
            _fail("Control changed outside the recorded recovery step")
        progress = max(matches)
        if existing is None:
            entries = manifest.meta.setdefault(journal, {})
            existing = entries[token] = {
                "request_sha256": _digest(request),
                "request": deepcopy(request),
                "state": "pending",
                "step": 0,
            }
            persist_checkpoint()
        elif progress > existing["step"]:
            # Observe an accepted unknown write durably before another write;
            # otherwise two lost replies can outrun the one-step resume window.
            existing["step"] = progress
            persist_checkpoint()
        operations = [
            lambda: store.update_work_item_metadata(
                item.id, delivery_identity=identity
            ),
            lambda: store.update_work_item_metadata(item.id, worker_handoff={}),
            lambda: store.update_work_item_metadata(
                item.id, review_ledger_generation=item.review_generation
            ),
            lambda: store.update_work_item_metadata(
                item.id, review_obligations=obligations
            ),
            lambda: store.update_work_item_metadata(
                item.id, review_subject_digest=subject
            ),
            lambda: store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW),
            lambda: store.update_work_item_metadata(item.id, decision_required={}),
            lambda: store.update_status(item.id, WorkItemStatus.IN_REVIEW),
        ]
        while progress < len(operations):
            if (
                _digest(
                    sorted(
                        (_plain(r) for r in _runs(runtime, item.id)),
                        key=lambda r: r["id"],
                    )
                )
                != request["runs_sha256"]
            ):
                _fail("Run set changed before recovery write")
            readiness = store.read_pull_request_readiness(value["pr_url"])
            if (
                not isinstance(readiness, PullRequestReadiness)
                or readiness.is_draft
                or readiness.state.upper() != "OPEN"
                or readiness.head_sha != value["head_sha"]
            ):
                _fail("Remote PR HEAD or state changed before recovery write")
            if _state(store.get_work_item(item.id)) != states[progress]:
                _fail("Control changed before recovery write")
            if pre_write_verify is not None:
                pre_write_verify(manifest, actual)
            operations[progress]()
            if _state(store.get_work_item(item.id)) != states[progress + 1]:
                raise PlatformError(
                    "Recovery write is not yet observable; resume the identical request"
                )
            progress += 1
            existing["step"] = progress
            persist_checkpoint()
        manifest.nodes[key].status = "in_review"
        manifest.nodes[key].recovery_marker = False
        existing["step"] = len(operations)
        existing["state"] = "consumed"
        persist_checkpoint()
        return {
            "state": "ready-for-independent-review",
            "authorization_sha256": token,
            "issue_id": item.id,
            "run_id": identity.run_id,
            "verdict": None,
            "next_action": f"Inspect facts, then use omac dag run {manifest_path} under the existing single controller",
        }

def apply_evidence_review(store, runtime, manifest_path, key, witness_path, request):
    if (
        not isinstance(request, dict)
        or set(request)
        != {"schema", "reason", "tuple", "expected_control", "runs_sha256"}
        or request["schema"] != SCHEMA
    ):
        _fail(
            "Use the exact prepared request; supplied identities and extra fields are forbidden"
        )
    value = request["tuple"]
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("witness"), dict)
        or type(value["witness"].get("line")) is not int
        or not isinstance(request["expected_control"], dict)
        or not isinstance(request["reason"], str)
        or not request["reason"].strip()
        or len(request["reason"].encode()) > 2048
    ):
        _fail("Malformed prepared request")
    try:
        token = _digest(
            {
                k: value[k]
                for k in (
                    "issue_id",
                    "node_id",
                    "binding",
                    "pr_url",
                    "head_sha",
                    "run",
                    "historical_handoff",
                    "verification_ref",
                    "reject_report_ref",
                    "artifact_set_sha256",
                )
            }
        )
    except KeyError as exc:
        raise ValidationError(
            "Prepared tuple is incomplete; prepare a fresh request"
        ) from exc
    return _apply_review_request(
        store, runtime, manifest_path, key, request, token, schema=SCHEMA, journal=JOURNAL,
        verifier=lambda current: _verify(store, runtime, current, key, witness_path, value["witness"]["line"]),
        initial_control=_initial_control,
    )

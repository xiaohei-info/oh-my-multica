"""运行中 DAG amendment 的 Orchestrator → Reviewer → Human 流水线。"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from ..core.acceptance import load_acceptance_doc_file
from ..core.amendment import (
    apply_amendment, build_reviewed_amendment, parse_proposal, validate_proposal,
)
from ..core.manifest import Contract, load_manifest
from ..core.taskmeta import TaskKind, TaskPhase
from ..engines.models import WorkItemStatus
from ..errors import NeedsDecision, PlatformError, ValidationError
from ..i18n import ui
from .tasks import run_task


def default_amendment_path(manifest_path: str) -> str:
    path = Path(manifest_path)
    return str(path.with_name(f"{path.stem}.amendment.yaml"))


def _new_attempt_command(
    manifest_path: str,
    *,
    report_file: str,
    docs: list[str],
    blocked_nodes: list[str],
    supersedes_issue_id: str,
    output_file: str | None,
) -> str:
    args = [
        "omac", "dag", "amend", "propose", manifest_path,
        "--report-file", report_file,
    ]
    for path in docs:
        args.extend(["--docs", path])
    for node_id in blocked_nodes:
        args.extend(["--blocked-node", node_id])
    args.extend([
        "--new-attempt", "--supersedes-issue-id", supersedes_issue_id,
    ])
    if output_file:
        args.extend(["--output-file", output_file])
    args.extend(["--output", "json"])
    return shlex.join(args)


def _attempt_context(
    manifest_path: str,
    *,
    report: str,
    docs_snapshot: dict[str, Any],
    blocked_nodes: list[str],
    superseded_issue,
) -> dict[str, Any]:
    report_digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    manifest_digest = hashlib.sha256(
        Path(manifest_path).read_bytes()).hexdigest()
    request_digest = hashlib.sha256(json.dumps({
        "manifest_sha256": manifest_digest,
        "report_sha256": report_digest,
        "docs_sha256": docs_snapshot["docs_sha256"],
        "blocked_nodes": sorted(blocked_nodes),
        "supersedes_issue_id": superseded_issue.id,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema": "omac.amendment-attempt/v1",
        "attempt_id": request_digest[:16],
        "request_digest": request_digest,
        "manifest_sha256": manifest_digest,
        "report_sha256": report_digest,
        "docs_sha256": docs_snapshot["docs_sha256"],
        "docs_file_count": len(docs_snapshot["docs_files"]),
        "supersedes_issue_id": superseded_issue.id,
        "supersedes_issue_key": superseded_issue.identifier or "",
    }


def _read_document_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError(
            f"Could not read authoritative docs input {path}: {exc}") from exc


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise ValidationError(
                f"Authoritative docs input must not traverse a symlink: {path}")


def _manifest_project_root(manifest_path: str) -> Path:
    try:
        manifest = Path(manifest_path).resolve(strict=True)
    except OSError as exc:
        raise ValidationError(f"Could not resolve manifest {manifest_path}: {exc}") from exc
    return manifest.parent.parent if manifest.parent.name == ".omac" else manifest.parent


def _resolve_docs_input(raw_path: str, project_root: Path) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    _reject_symlink_components(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValidationError(
            f"Could not resolve authoritative docs input {raw_path}: {exc}") from exc
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValidationError(
            "Authoritative docs input is outside the manifest project: "
            f"{raw_path}") from exc
    return resolved


def _project_logical_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(project_root).as_posix()
    except (OSError, ValueError) as exc:
        raise ValidationError(
            f"Input is outside the manifest project: {path}") from exc


def _docs_snapshot(
    paths: list[str], *, project_root: Path,
) -> dict[str, Any]:
    project_root = project_root.resolve(strict=True)
    entries: dict[str, bytes] = {}
    for raw_path in sorted(set(paths)):
        resolved = _resolve_docs_input(raw_path, project_root)
        if resolved.is_file():
            entries[_project_logical_path(resolved, project_root)] = (
                _read_document_bytes(resolved))
            continue
        if not resolved.is_dir():
            raise ValidationError(
                f"Authoritative docs input is not a regular file or directory: {raw_path}")
        try:
            descendants = sorted(resolved.rglob("*"), key=lambda path: path.as_posix())
        except OSError as exc:
            raise ValidationError(
                f"Could not enumerate authoritative docs input {raw_path}: {exc}") from exc
        for candidate in descendants:
            if candidate.is_symlink():
                raise ValidationError(
                    f"Authoritative docs input contains a symlink: {candidate}")
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise ValidationError(
                    f"Authoritative docs input contains a non-regular file: {candidate}")
            logical_path = _project_logical_path(candidate, project_root)
            entries[logical_path] = _read_document_bytes(candidate)
    if not entries:
        raise ValidationError("Authoritative docs inputs contain no readable files")
    digest = hashlib.sha256()
    for logical_path, content in sorted(entries.items()):
        encoded_path = logical_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return {
        "docs_sha256": digest.hexdigest(),
        "docs_files": sorted(entries),
    }


def _write_yaml_atomic(path: str, payload: dict[str, Any]) -> None:
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_amendment_file(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except FileNotFoundError as exc:
        raise ValidationError(ui(
            f"Amendment file not found: {path}", f"amendment 文件不存在: {path}")) from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(ui(
            f"Could not read amendment file {path}: {exc}",
            f"无法读取 amendment 文件 {path}: {exc}")) from exc
    return parse_proposal(value)


def _acceptance_for_manifest(manifest, manifest_path: str):
    configured = manifest.meta.get("acceptance_file")
    if not configured:
        return None
    path = os.path.join(os.path.dirname(manifest_path), configured)
    try:
        return load_acceptance_doc_file(path)
    except (OSError, ValueError) as exc:
        raise ValidationError(ui(
            f"Could not load the authoritative acceptance document {path}: {exc}",
            f"无法读取权威 acceptance 文档 {path}: {exc}")) from exc


def _validate_inputs(
    manifest_path: str, report_file: str, docs: list[str], blocked_nodes: list[str],
) -> tuple[str, list[str], Path]:
    if not os.path.exists(manifest_path):
        raise ValidationError(f"Manifest file not found: {manifest_path}")
    project_root = _manifest_project_root(manifest_path)
    try:
        report = Path(report_file).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Could not read Reviewer report {report_file}: {exc}") from exc
    if not report.strip():
        raise ValidationError("Reviewer report must not be empty")
    resolved_docs = [
        str(_resolve_docs_input(path, project_root)) for path in docs]
    manifest = load_manifest(manifest_path)
    unknown = [node for node in blocked_nodes if node not in manifest.nodes]
    if unknown:
        raise ValidationError("Blocked node not found in manifest: " + ", ".join(unknown))
    return report, resolved_docs, project_root


def _contract(
    manifest_path: str,
    docs: list[str],
    blocked_nodes: list[str],
    report_file: str,
    *,
    project_root: Path,
) -> Contract:
    manifest_source = _project_logical_path(Path(manifest_path), project_root)
    sources = [
        manifest_source,
        *(_project_logical_path(Path(path), project_root) for path in docs),
    ]
    return Contract(
        objective=(
            "Produce the smallest structured running-DAG amendment that resolves the "
            "reported contract or topology defect without rewriting runtime facts."
        ),
        source_of_truth=sources,
        acceptance=[
            "The amendment is schema-valid and changes only the minimum required nodes",
            "Done/merged facts and unaffected node runtime state remain immutable",
            "The output states why each changed node resumes at review, authoring, or merging",
        ],
        non_goals=[
            "Do not implement project code",
            "Do not edit the live manifest directly",
            "Do not regenerate the whole plan or reset unaffected nodes",
        ],
        verification_commands=[
            f"omac dag check {manifest_source} --no-review",
        ],
        integration_gates=[{
            "name": "running-dag-amendment",
            "layer": "control-plane",
            "delivery_goal": "reviewable minimal manifest diff",
            "source_of_truth": sources,
            "covers": blocked_nodes or ["reported blocker"],
            "acceptance_refs": ["amendment schema and invariant validation"],
            "commands": [f"omac dag show {manifest_source} --output json"],
        }],
        pr_base="manifest-runtime",
        coverage_gate=0,
    )


def propose_amendment(
    engine: Any,
    manifest_path: str,
    *,
    report_file: str,
    docs: list[str],
    blocked_nodes: list[str],
    orchestrator: str,
    reviewers: list[str],
    max_revisions: int,
    output_file: str | None = None,
    resume_issue_id: str | None = None,
    restart_authoring: bool = False,
    new_attempt: bool = False,
    supersedes_issue_id: str | None = None,
    poll=None,
) -> dict[str, Any]:
    requested_docs = list(docs)
    if restart_authoring and not resume_issue_id:
        raise ValidationError(ui(
            "--restart-authoring requires --resume-issue-id",
            "--restart-authoring 必须与 --resume-issue-id 一起使用"))
    if new_attempt and not supersedes_issue_id:
        raise ValidationError(ui(
            "--new-attempt requires --supersedes-issue-id",
            "--new-attempt 必须与 --supersedes-issue-id 一起使用"))
    if supersedes_issue_id and not new_attempt:
        raise ValidationError(ui(
            "--supersedes-issue-id requires --new-attempt",
            "--supersedes-issue-id 必须与 --new-attempt 一起使用"))
    if new_attempt and (resume_issue_id or restart_authoring):
        raise ValidationError(ui(
            "--new-attempt cannot be combined with --resume-issue-id or --restart-authoring",
            "--new-attempt 不能与 --resume-issue-id 或 --restart-authoring 同用"))
    if restart_authoring:
        next_action = _new_attempt_command(
            manifest_path,
            report_file=report_file,
            docs=docs,
            blocked_nodes=blocked_nodes,
            supersedes_issue_id=resume_issue_id,
            output_file=output_file,
        )
        raise NeedsDecision(
            ui(
                "The selected engine cannot safely restart this confirmation in place; create a new amendment attempt",
                "当前引擎无法安全原地重开该 confirmation；请创建新的 amendment attempt"),
            report={
                "reason_code": "atomic-restart-unsupported",
                "engine": engine.store.config.engine_type,
                "resume_issue_id": resume_issue_id,
                "next_action": next_action,
            },
        )
    report, docs, project_root = _validate_inputs(
        manifest_path, report_file, docs, blocked_nodes)
    docs_snapshot = _docs_snapshot(docs, project_root=project_root)
    if not orchestrator:
        raise ValidationError("An Orchestrator agent is required")
    if not reviewers:
        raise ValidationError("At least one Reviewer agent is required")

    manifest = load_manifest(manifest_path)
    acceptance = _acceptance_for_manifest(manifest, manifest_path)
    pool = set(engine.store.list_members(engine.store.config.workspace_id))
    missing_agents = [
        agent for agent in [orchestrator, *reviewers] if agent not in pool
    ]
    if missing_agents:
        raise ValidationError(
            "Amendment agents are not in the workspace pool: "
            + ", ".join(sorted(set(missing_agents))))

    description = (
        "A DAG already approved and running has exposed a contract/topology defect. "
        "Read every design document under each supplied docs path, the current manifest, "
        "and the Reviewer report. Submit one YAML object using schema "
        "omac.dag-amendment/v1 with reason and operations. Supported operations are "
        "update/add/remove/resume/update-responsibility. Never patch runtime fields. "
        "Done/merged nodes are immutable except update-responsibility with explicit "
        "historical_contract_correction=true and an operation reason. Use "
        "update-responsibility for every acceptance responsibility migration: carry only "
        "acceptance_claims, acceptance_contributions, acceptance_refs, explicit legacy "
        "acceptance clearing, and optional named integration-gate acceptance_refs patches; "
        "never copy a complete contract. Use migration.ownership_transfer=true plus a reason when an "
        "executed node changes worker or scope_paths. For an acceptance responsibility migration that needs recovery, set its optional resume_stage "
        "to review, authoring, or merging on that same update-responsibility operation; "
        "never add a second resume operation for the same node. "
        "A contract update is a complete "
        "replacement: preserve every intended acceptance_claims, "
        "acceptance_contributions, and acceptance_refs responsibility field and use only "
        "flow/action identities from the authoritative acceptance document. If the "
        "existing contract has typed boundary fields, preserve only the boundary fields "
        "actually present. An omitted consumes must remain omitted unless the amendment "
        "explicitly changes the input policy. To clear the whole boundary, set top-level "
        "clear_contract_boundary: true and omit every boundary field. "
        "Do not edit "
        "the live manifest.\n\n"
        f"Current manifest: "
        f"{_project_logical_path(Path(manifest_path), project_root)}\n"
        f"Authoritative docs paths (read every design document under each path): "
        f"{', '.join(_project_logical_path(Path(path), project_root) for path in docs)}\n"
        f"Blocked nodes: {', '.join(blocked_nodes) or '(derive from report)'}\n\n"
        "Reviewer report:\n\n" + report
    )
    payload = {
        "title": f"Running DAG amendment: {Path(manifest_path).name}",
        "description": description,
        "contract": _contract(
            manifest_path, docs, blocked_nodes, report_file,
            project_root=project_root),
    }

    attempt = None
    source_refs = None
    dag_key = f"amend-{Path(manifest_path).stem}"
    effective_resume_issue_id = resume_issue_id
    if new_attempt:
        superseded = engine.store.get_work_item(supersedes_issue_id)
        if superseded.kind != TaskKind.AMENDMENT:
            raise ValidationError("--supersedes-issue-id must reference an amendment issue")
        if (
            superseded.phase != TaskPhase.CONFIRMATION
            or superseded.review_verdict != "pass"
        ):
            raise ValidationError(
                "--supersedes-issue-id must reference a Reviewer-pass amendment "
                "in human confirmation")
        attempt = _attempt_context(
            manifest_path,
            report=report,
            docs_snapshot=docs_snapshot,
            blocked_nodes=blocked_nodes,
            superseded_issue=superseded,
        )
        suffix = attempt["attempt_id"]
        dag_key = f"amend-{Path(manifest_path).stem}-attempt-{suffix}"
        payload["title"] += f" [attempt {suffix}]"
        superseded_ref = {
            "issue_id": superseded.id,
            "label": (
                f"superseded amendment {superseded.identifier}"
                if superseded.identifier else "superseded amendment"
            ),
            "kind": "amendment",
            "relation": "supersedes",
            "report_sha256": attempt["report_sha256"],
            "docs_sha256": attempt["docs_sha256"],
        }
        if superseded.identifier:
            superseded_ref["issue_key"] = superseded.identifier
        source_refs = [superseded_ref]
    elif resume_issue_id is None:
        existing = engine.store.find_work_item_by_dag_key(
            engine.store.config.workspace_id, dag_key)
        if existing is not None:
            next_action = _new_attempt_command(
                manifest_path,
                report_file=report_file,
                docs=requested_docs,
                blocked_nodes=blocked_nodes,
                supersedes_issue_id=existing.id,
                output_file=output_file,
            )
            raise NeedsDecision(
                ui(
                    "The amendment issue identity already exists; create an explicit new attempt",
                    "amendment issue 身份已存在；请显式创建新的 attempt"),
                report={
                    "reason_code": "amendment-identity-conflict",
                    "existing_issue_id": existing.id,
                    "existing_issue_key": existing.identifier,
                    "next_action": next_action,
                },
            )

    def guard(item) -> list[str]:
        if not item.deliverable:
            return ["amendment deliverable is empty"]
        return validate_proposal(
            manifest, item.deliverable, pool, acceptance=acceptance)

    try:
        outcome = run_task(
            engine,
            TaskKind.AMENDMENT,
            payload,
            orchestrator,
            reviewers=reviewers,
            max_revisions=max_revisions,
            poll=poll or (lambda: time.sleep(engine.store.config.polling_interval)),
            guard=guard,
            confirm=True,
            pause_at_confirmation=True,
            source_refs=source_refs,
            dag_key=dag_key,
            resume_item_id=effective_resume_issue_id,
            amendment_attempt=attempt,
            reuse_dag_key=new_attempt,
            review_acceptance_doc=acceptance,
            review_amendment_manifest=manifest,
        )
    except PlatformError as exc:
        if resume_issue_id or new_attempt or "conflict" not in str(exc).lower():
            raise
        conflict_item = engine.store.find_work_item_by_dag_key(
            engine.store.config.workspace_id, dag_key)
        supersedes = (
            conflict_item.id if conflict_item is not None
            else "<existing-amendment-issue-id>"
        )
        raise NeedsDecision(
            "Amendment issue creation conflicted with an existing identity",
            report={
                "reason_code": "amendment-identity-conflict",
                "next_action": _new_attempt_command(
                    manifest_path,
                    report_file=report_file,
                    docs=requested_docs,
                    blocked_nodes=blocked_nodes,
                    supersedes_issue_id=supersedes,
                    output_file=output_file,
                ),
            },
        ) from exc
    issue = engine.store.get_work_item(outcome["item_id"])
    if issue.phase != TaskPhase.CONFIRMATION or issue.review_verdict != "pass":
        raise ValidationError("Amendment did not reach Reviewer-pass confirmation")

    reviewed = build_reviewed_amendment(
        manifest,
        outcome["delivery"]["amendment"],
        engine.store,
        issue_id=issue.id,
        reviewer_verdict=issue.review_verdict,
        agent_pool=pool,
        acceptance=acceptance,
    )
    target = output_file or default_amendment_path(manifest_path)
    _write_yaml_atomic(target, reviewed)
    return {
        "state": "pending_human_confirmation",
        "manifest": manifest_path,
        "amendment_file": target,
        "amendment_id": reviewed["amendment_id"],
        "issue_id": issue.id,
        "reviewer_verdict": issue.review_verdict,
        "analysis": reviewed["analysis"],
        "next_action": f"omac dag amend accept {manifest_path} {target}",
    }


def accept_amendment(
    engine: Any,
    manifest_path: str,
    amendment_file: str,
    *,
    reason: str,
    agent_pool: set[str],
) -> dict[str, Any]:
    amendment = load_amendment_file(amendment_file)
    review = amendment.get("review") or {}
    issue_id = review.get("issue_id")
    if not issue_id:
        raise ValidationError("Amendment review.issue_id is missing")
    issue = engine.store.get_work_item(issue_id)
    current_manifest = load_manifest(manifest_path)
    already_applied = (
        current_manifest.meta.get("last_amendment_id")
        == amendment.get("amendment_id")
    )
    acceptance = (
        None if already_applied
        else _acceptance_for_manifest(current_manifest, manifest_path)
    )
    if not already_applied and (
        issue.review_verdict != "pass" or issue.phase != TaskPhase.CONFIRMATION
    ):
        raise ValidationError(ui(
            "Human acceptance is allowed only after Reviewer pass and confirmation phase.",
            "只有 Reviewer pass 且进入 confirmation 后才能人工 accept。"))

    result = apply_amendment(
        manifest_path, amendment, engine.store, agent_pool,
        amendment_file=amendment_file, acceptance=acceptance)
    amendment["human_confirmation"] = "applied"
    amendment["human_reason"] = reason
    amendment["apply_result"] = result
    _write_yaml_atomic(amendment_file, amendment)
    engine.store.update_work_item_metadata(
        issue_id,
        decision_required={},
    )
    engine.store.update_status(issue_id, WorkItemStatus.DONE)
    return {**result, "issue_id": issue_id, "state": "applied"}

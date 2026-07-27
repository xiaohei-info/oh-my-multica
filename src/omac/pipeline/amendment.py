"""运行中 DAG amendment 的 Orchestrator → Reviewer → Human 流水线。"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from ..core.amendment import (
    apply_amendment, build_reviewed_amendment, parse_proposal, validate_proposal,
)
from ..core.manifest import Contract, load_manifest
from ..core.taskmeta import TaskKind, TaskPhase
from ..engines.models import WorkItemStatus
from ..errors import ValidationError
from ..i18n import ui
from .tasks import run_task


def default_amendment_path(manifest_path: str) -> str:
    path = Path(manifest_path)
    return str(path.with_name(f"{path.stem}.amendment.yaml"))


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


def _validate_inputs(
    manifest_path: str, report_file: str, docs: list[str], blocked_nodes: list[str],
) -> tuple[str, list[str]]:
    if not os.path.exists(manifest_path):
        raise ValidationError(f"Manifest file not found: {manifest_path}")
    try:
        report = Path(report_file).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Could not read Reviewer report {report_file}: {exc}") from exc
    if not report.strip():
        raise ValidationError("Reviewer report must not be empty")
    missing = [path for path in docs if not os.path.exists(path)]
    if missing:
        raise ValidationError("Authoritative docs path not found: " + ", ".join(missing))
    manifest = load_manifest(manifest_path)
    unknown = [node for node in blocked_nodes if node not in manifest.nodes]
    if unknown:
        raise ValidationError("Blocked node not found in manifest: " + ", ".join(unknown))
    return report, docs


def _portable_path(path: str) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _contract(
    manifest_path: str, docs: list[str], blocked_nodes: list[str], report_file: str,
) -> Contract:
    sources = [_portable_path(manifest_path), *map(_portable_path, docs)]
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
            f"omac dag check {manifest_path} --no-review",
        ],
        integration_gates=[{
            "name": "running-dag-amendment",
            "layer": "control-plane",
            "delivery_goal": "reviewable minimal manifest diff",
            "source_of_truth": sources,
            "covers": blocked_nodes or ["reported blocker"],
            "acceptance_refs": ["amendment schema and invariant validation"],
            "commands": [f"omac dag show {manifest_path} --output json"],
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
    poll=None,
) -> dict[str, Any]:
    report, docs = _validate_inputs(
        manifest_path, report_file, docs, blocked_nodes)
    if not orchestrator:
        raise ValidationError("An Orchestrator agent is required")
    if not reviewers:
        raise ValidationError("At least one Reviewer agent is required")

    manifest = load_manifest(manifest_path)
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
        "update/add/remove/resume. Never patch runtime fields. Never delete or rewrite "
        "done/merged nodes. Use migration.ownership_transfer=true plus a reason when an "
        "executed node changes worker or scope_paths. Do not edit the live manifest.\n\n"
        f"Current manifest: {_portable_path(manifest_path)}\n"
        f"Authoritative docs paths (read every design document under each path): "
        f"{', '.join(map(_portable_path, docs))}\n"
        f"Blocked nodes: {', '.join(blocked_nodes) or '(derive from report)'}\n\n"
        "Reviewer report:\n\n" + report
    )
    payload = {
        "title": f"Running DAG amendment: {Path(manifest_path).name}",
        "description": description,
        "contract": _contract(manifest_path, docs, blocked_nodes, report_file),
    }

    def guard(item) -> list[str]:
        if not item.deliverable:
            return ["amendment deliverable is empty"]
        return validate_proposal(manifest, item.deliverable, pool)

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
        dag_key=f"amend-{Path(manifest_path).stem}",
        resume_item_id=resume_issue_id,
    )
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
    already_applied = (
        load_manifest(manifest_path).meta.get("last_amendment_id")
        == amendment.get("amendment_id")
    )
    if not already_applied and (
        issue.review_verdict != "pass" or issue.phase != TaskPhase.CONFIRMATION
    ):
        raise ValidationError(ui(
            "Human acceptance is allowed only after Reviewer pass and confirmation phase.",
            "只有 Reviewer pass 且进入 confirmation 后才能人工 accept。"))

    result = apply_amendment(
        manifest_path, amendment, engine.store, agent_pool)
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

"""Helpers shared by several test modules (moved out of the test file that happened to define them)."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import yaml
from omac.core.manifest import Manifest, Node, save_manifest
from omac.core.taskmeta import DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity
from omac.core.workitem import PullRequestObservation, PullRequestState
from omac.errors import PlatformError


class _RemoteFixture:
    def __init__(self, issues: dict[str, dict], attachments: dict[str, tuple[str, bytes]]):
        self.issues = issues
        self.attachments = attachments
        self.issue_gets = 0
        self.issue_lists = 0
        self.attachment_downloads = 0
        self.pr_observations = 0
        self.calls: list[tuple[str, str]] = []
        self.fail_attachment_id: str | None = None
        self.attachment_error: Exception | None = None
        self.issue_get_hook = None

    def run(self, args, capture=True):
        if args[:2] == ["issue", "list"]:
            self.issue_lists += 1
            self.calls.append(("issue-list", str(self.issue_lists)))
            assert args[args.index("--project") + 1] == "project-1"
            assert args[args.index("--limit") + 1] == "10"
            offset = int(args[args.index("--offset") + 1])
            return copy.deepcopy(list(self.issues.values())[offset:offset + 10])
        if args[:2] == ["issue", "get"]:
            item_id = args[2]
            self.issue_gets += 1
            self.calls.append(("issue", item_id))
            if self.issue_get_hook is not None:
                self.issue_get_hook(item_id, self.issue_gets)
            return copy.deepcopy(self.issues[item_id])
        if args[:2] == ["attachment", "download"]:
            attachment_id = args[2]
            self.attachment_downloads += 1
            self.calls.append(("attachment", attachment_id))
            if attachment_id == self.fail_attachment_id:
                raise self.attachment_error or PlatformError(
                    f"attachment read failed: {attachment_id}")
            filename, body = self.attachments[attachment_id]
            output_dir = Path(args[args.index("--output-dir") + 1])
            (output_dir / filename).write_bytes(body)
            return None
        if args[:3] == ["issue", "comment", "list"]:
            item_id = args[3]
            comment_id = args[args.index("--thread") + 1]
            issue = self.issues[item_id]
            verification_ref = issue["metadata"]["verification_ref"]
            return [{
                "id": comment_id,
                "attachments": [{
                    "id": verification_ref["attachment_id"],
                    "filename": verification_ref["filename"],
                    "uploader_type": "agent",
                    "uploader_id": "agent-worker",
                    "task_id": "run-worker",
                    "created_at": "2026-07-30T01:00:00Z",
                }],
            }]
        if args[:2] == ["issue", "update"] and "--status" in args:
            item_id = args[2]
            self.issues[item_id]["status"] = args[args.index("--status") + 1]
            return None
        raise AssertionError(f"unexpected command: {args}")

    def observe_pull_request(self, pr_url: str) -> PullRequestObservation:
        self.pr_observations += 1
        self.calls.append(("pr", pr_url))
        return PullRequestObservation(PullRequestState.UNKNOWN)



def _ref(item_id: str, label: str, filename: str, body: bytes) -> tuple[dict, tuple[str, bytes]]:
    attachment_id = f"{item_id}-{label}"
    return ({
        "comment_id": f"comment-{attachment_id}",
        "attachment_id": attachment_id,
        "filename": filename,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }, (filename, body))



def _issue(
    item_id: str,
    *,
    status: str,
    phase: str,
    review_verdict: str | None = None,
    review_subject: str | None = None,
    delivery_identity: dict | None = None,
    worker_handoff: dict | None = None,
    unknown: bool = False,
) -> tuple[dict, dict[str, tuple[str, bytes]]]:
    verification_body = yaml.safe_dump({
        "commands": [{"cmd": "pytest -q", "exit_code": 0}],
        "integration_gates": [],
        "coverage": 100,
    }, sort_keys=False).encode()
    report_body = yaml.safe_dump({
        "full_review_completed": True,
        "review_goals": ["verify current delivery"],
        "blockers": [],
    }, sort_keys=False).encode()
    ledger_body = yaml.safe_dump({
        "schema": "omac.review-ledger/v1", "cycles": [], "blockers": [],
    }, sort_keys=False).encode()
    contract_body = yaml.safe_dump({
        "objective": "deliver", "verification_commands": ["pytest -q"],
        "integration_gates": [], "pr_base": "main",
    }, sort_keys=False).encode()
    refs = {}
    attachments = {}
    for label, filename, body in (
        ("verification", "verification.yaml", verification_body),
        ("review_report", "review-report.yaml", report_body),
        ("review_ledger", "review-ledger.yaml", ledger_body),
        ("contract", "contract.yaml", contract_body),
    ):
        refs[label], attachment = _ref(item_id, label, filename, body)
        attachments[refs[label]["attachment_id"]] = attachment

    metadata = {
        "dag_key": item_id,
        "worker": "worker",
        "reviewer": "reviewer",
        "kind": "develop",
        "phase": phase,
        "artifacts": {
            "pr_url": f"https://github.com/acme/repo/pull/{item_id}",
            "head_sha": f"head-{item_id}",
        },
        "verification_ref": refs["verification"],
        "review_report_ref": refs["review_report"],
        "review_ledger_ref": refs["review_ledger"],
        "contract_ref": refs["contract"],
    }
    if review_verdict is not None:
        metadata["review_verdict"] = review_verdict
    if review_subject is not None:
        metadata["review_subject_digest"] = review_subject
    if delivery_identity is not None:
        metadata["delivery_identity"] = delivery_identity
    if worker_handoff is not None:
        metadata["worker_handoff"] = worker_handoff
    if unknown:
        metadata["future_execution_fact"] = {"generation": 2}
    issue = {
        "id": item_id,
        "title": item_id,
        "description": item_id,
        "status": status,
        "metadata": metadata,
    }
    return issue, attachments



def _manifest_path(tmp_path, nodes: dict[str, Node]) -> tuple[Manifest, str]:
    manifest = Manifest(meta={"name": "large-dag"}, nodes=nodes)
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    return manifest, path



def _delivery_identity(issue: dict) -> dict:
    metadata = issue["metadata"]
    verification_ref = metadata["verification_ref"]
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation=f"handoff-{issue['id']}",
        worker="worker",
        agent_id="agent-worker",
        run_id=f"run-{issue['id']}",
        pr_url=metadata["artifacts"]["pr_url"],
        pr_head_sha=metadata["artifacts"]["head_sha"],
        verification_sha256=verification_ref["sha256"],
        verification_attachment_id=verification_ref["attachment_id"],
        verification_comment_id=verification_ref["comment_id"],
        verification_uploader_id="agent-worker",
        verification_uploader_type="agent",
        verification_task_id=f"run-{issue['id']}",
        verification_created_at="2026-07-30T01:00:00Z",
    ).as_dict()


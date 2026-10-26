"""A delivered, reviewed node as pure facts: the manifest, the WorkItem with its sealed delivery identity and Reviewer baseline."""
from dataclasses import replace

from _support.loopkit import contract
from omac.core.manifest import Manifest, Node, _dump_contract
from omac.core.taskmeta import (
    DELIVERY_IDENTITY_SCHEMA, REVIEWER_RUN_BASELINE_SCHEMA, DeliveryIdentity, ReviewerRunBaseline, TaskPhase,
)
from omac.core.workitem import WorkItem, WorkItemStatus
from omac.pipeline.decide_recovery import review_subject_for_current_delivery

PATH, KEY, REVIEWER, AGENT = "dag.yaml", "a", "bob", "agent-bob"
CUTOFF = "2026-08-03T00:00:00Z"
NODE_CONTRACT = contract()


def manifest():
    return Manifest(meta={"workspace_id": "ws"}, nodes={KEY: Node(
        id=KEY, worker="alice", reviewer=REVIEWER, contract=NODE_CONTRACT, title=KEY, description="")})


def delivered_item():
    identity = DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA, handoff_generation="g", worker="alice", agent_id="agent-alice", run_id="run-1",
        pr_url="https://example.test/pr/1", pr_head_sha="h", verification_sha256="s", verification_attachment_id="att",
        verification_comment_id="com", verification_task_id="t", verification_created_at=CUTOFF)
    item = WorkItem(
        id="item-1", workspace_id="ws", title="t", description="", status=WorkItemStatus.BLOCKED, dag_key=KEY,
        worker="alice", reviewer=REVIEWER, phase=TaskPhase.REVIEW, delivery_identity=identity,
        artifacts={"pr_url": identity.pr_url, "head_sha": "h"},
        verification_ref={"attachment_id": "att", "comment_id": "com", "sha256": "s"},
        contract=_dump_contract(NODE_CONTRACT))
    subject = review_subject_for_current_delivery(manifest(), KEY, item)
    baseline = ReviewerRunBaseline(
        schema=REVIEWER_RUN_BASELINE_SCHEMA, subject_digest=subject, target_reviewer=REVIEWER, target_agent_id=AGENT,
        cutoff_created_at=CUTOFF, generation="review-1", attempt=1, baseline_direct_run_ids=())
    decision = {
        "schema": "omac.decision-required/v1", "reason_code": "reviewer-run-baseline-unavailable", "kind": "develop",
        "phase": "review", "gate": "reviewer", "resume_issue_id": item.id, "node_id": KEY,
        "failure_class": "unproven-reviewer-run-causality", "next_action": f"omac node retry {PATH} {KEY} --stage review"}
    return replace(item, review_subject_digest=subject, reviewer_run_baseline=baseline, decision_required=decision)

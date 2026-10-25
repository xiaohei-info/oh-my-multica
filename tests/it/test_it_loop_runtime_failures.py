"""Runtime failures that retrying cannot fix: the node blocks with a decision whose command really recovers it."""
import shlex

import pytest

from _support.loopkit import fails
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus

POLICY = "request rejected by network security policy"


def run_command(kit, command: str):
    """Run a copyable `omac ...` command from a decision, as the operator would."""
    words = shlex.split(command)
    assert words[0] == "omac"
    return kit.project.omac(*words[1:])


def test_a_nonretryable_worker_failure_blocks_without_a_business_bounce(kit):
    manifest, path, item = kit.dispatched(POLICY, delivery={"marker": "preserve-existing-delivery"})
    kit.forbid(kit.runtime, "wake")

    result = kit.tick(manifest, path, max_parallel=1)

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert blocked.status is WorkItemStatus.BLOCKED and blocked.phase is TaskPhase.AUTHORING
    assert blocked.verification == item.verification and blocked.verification_ref == item.verification_ref
    assert blocked.bounces.worker == blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == "nonretryable-runtime-failure"


def test_the_worker_decision_command_recovers_authoring_not_review(kit):
    manifest, path, item = kit.dispatched(POLICY)
    kit.forbid(kit.runtime, "wake")
    assert kit.tick(manifest, path, max_parallel=1).state == "needs_decision"
    blocked = kit.store.get_work_item(item.id)
    next_action = blocked.decision_required["next_action"]
    assert blocked.phase is TaskPhase.AUTHORING and blocked.worker_handoff is not None
    assert "--stage review" not in next_action

    done = run_command(kit, next_action)

    assert done.returncode == 0, done.stdout + done.stderr
    recovered = kit.store.get_work_item(item.id)
    assert recovered.phase is TaskPhase.AUTHORING and recovered.status is WorkItemStatus.TODO
    assert recovered.worker_handoff is None, "a stale handoff is not reused after an operator retry"
    resumed = load_manifest(path)
    assert resumed.nodes["a"].status == "todo"

    assert kit.tick(resumed, path, max_parallel=1).state == "running"
    fresh = kit.store.get_work_item(item.id).worker_handoff
    assert fresh is not None and fresh.generation != blocked.worker_handoff.generation


def blocked_reviewer(kit, *reviewer_runs):
    manifest, path, item = kit.in_review(*reviewer_runs)
    kit.wait_runs_ended(item.id)
    return manifest, path, item


def test_a_nonretryable_reviewer_failure_blocks_without_a_review_bounce(kit):
    manifest, path, item = blocked_reviewer(kit, fails("HTTP 403 forbidden by provider safety policy"))
    before = kit.store.get_work_item(item.id)
    kit.forbid(kit.runtime, "wake")

    result = kit.tick(manifest, path, max_parallel=1)

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and blocked.phase is TaskPhase.REVIEW
    assert blocked.verification == before.verification and blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == "nonretryable-runtime-failure"


def test_the_reviewer_decision_command_recovers_review_and_keeps_the_delivery(kit):
    manifest, path, item = blocked_reviewer(kit, fails("HTTP 403 forbidden by provider safety policy"))
    kit.forbid(kit.runtime, "wake")
    assert kit.tick(manifest, path, max_parallel=1).state == "needs_decision"
    blocked = kit.store.get_work_item(item.id)
    next_action = blocked.decision_required["next_action"]
    assert blocked.phase is TaskPhase.REVIEW and next_action.endswith(" --stage review")

    done = run_command(kit, next_action)

    assert done.returncode == 0, done.stdout + done.stderr
    recovered = kit.store.get_work_item(item.id)
    assert recovered.phase is TaskPhase.REVIEW and recovered.status is WorkItemStatus.IN_REVIEW
    assert recovered.review_subject_digest is not None and recovered.reviewer_run_baseline is None
    assert recovered.verification == blocked.verification
    assert load_manifest(path).nodes["a"].status == "in_review"


def test_reviewer_transient_failures_stop_at_the_infrastructure_limit(kit):
    manifest, path, item = blocked_reviewer(
        kit, fails("Our servers are currently overloaded"), fails("provider error: HTTP 503 Service Unavailable"))
    kit.tick_until(manifest, path, lambda: len(kit.reviewer_runs(item.id)) == 2, max_parallel=1,
                   what="the one allowed rerun")
    kit.wait_runs_ended(item.id)
    kit.forbid(kit.runtime, "wake")

    result = kit.tick(manifest, path, max_parallel=1)

    blocked = kit.store.get_work_item(item.id)
    assert result.state == "needs_decision" and blocked.phase is TaskPhase.REVIEW and blocked.bounces.review == 0
    assert blocked.decision_required["reason_code"] == "transient-runtime-retry-exhausted"
    assert len(kit.reviewer_runs(item.id)) == 2

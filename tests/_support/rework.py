"""Scenarios shared by the rejected-delivery tests (tests/it/test_it_rework_*.py): node `a` delivered by alice and rejected by bob."""
import json
from dataclasses import replace

from _support.loopkit import HANG, contract
from omac.core.manifest import EvidenceMode, ProducedArtifact
from omac.core.review_convergence import review_subject_digest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.pipeline import loop


IDLE = []   # a Run that ends at once without submitting anything


def rejected(kit, *rework, reviews=None, **node):
    """``reviews``: the Reviewer's attempts (default: reject the first delivery, hang on the next); ``node``: see Kit.in_review."""
    manifest, path, item = kit.in_review(*(reviews or ([{"hang": 3}, kit.stack.review("reject")], *HANG)), rework=rework, **node)
    kit.wait_for(lambda: kit.store.get_work_item(item.id).review_verdict == "reject", what="the rejection")
    kit.wait_runs_ended(item.id)
    return manifest, path, item


def workers(assigns):
    return [a for a in assigns if a[1][2] == "worker"]


def reviewers(assigns):
    return [a for a in assigns if a[1][2] == "reviewer"]


CRASH = RuntimeError("crash")


CHECKPOINTS = {
    "intent": lambda kit: kit.fault(kit.store, "update_work_item_metadata", error=CRASH,
                                    only=lambda _id, **m: m.get("worker_handoff") is not None),
    "bounce": lambda kit: kit.fault(kit.store, "update_work_item_metadata", error=CRASH,
                                    only=lambda _id, **m: m.get("review_bounce") == 1),
    "reset_review": lambda kit: kit.fault(kit.store, "reset_review", error=CRASH),
    "status": lambda kit: kit.fault(kit.store, "update_status", error=CRASH,
                                    only=lambda _id, status: status is WorkItemStatus.IN_PROGRESS),
    "assignment": lambda kit: kit.fault(kit.store, "assign_work_item", error=CRASH, only=lambda _id, _who, role, **k: role == "worker"),
}


def worker_assignment_sees_authoring(kit, item_id):
    """The Worker may only be assigned once the item is back in authoring, in progress, with the review cleared."""
    def check(_id, _assignee, role, **_kw):
        current = kit.store.get_work_item(item_id)
        if role == "worker":
            assert current.phase is TaskPhase.AUTHORING and current.status is WorkItemStatus.IN_PROGRESS
            assert current.review_verdict is None and current.review_report is None and current.review_subject_digest is None

    kit.before(kit.store, "assign_work_item", check)


def delivered_again(kit):
    """The rework Run submitted a new delivery and ended; the Loop has not looked yet."""
    manifest, path, item = rejected(kit)
    kit.tick(manifest, path)
    assert kit.store.get_work_item(item.id).worker_handoff is not None
    kit.wait_runs_ended(item.id)
    return manifest, path, item


def pushes(kit, item):
    """Move the PR head as a push would."""
    prs = json.loads((kit.stack.gh_state / "prs.json").read_text())
    url = item.artifacts["pr_url"]
    kit.pr(url, pushes=prs.get(url, {}).get("pushes", 0) + 1)


def stale_status(item):
    return replace(item, status=WorkItemStatus.TODO)


def stale_projection(projection):
    return replace(projection, work_item=stale_status(projection.work_item))


def causal_intent(item, **fields):
    return {"schema": "omac.worker-handoff/v1", "state": "pending", "target_worker": "alice", "gate": "review",
            "source_review_subject_digest": item.review_subject_digest, "source_review_round": 1, "target_review_bounce": 1, **fields}


def slow_submit(kit):
    return [{"hang": 3}, kit.stack.tool("worker-submit")]


def with_handoff_round(kit, item, round_index, **fields):
    """The pending handoff as if it answered the ``round_index``-th review."""
    intent = kit.store.get_work_item(item.id).worker_handoff
    feedback = {key: value for key, value in (intent.source_review_feedback or {}).items() if key != "ledger_ref"}   # the ledger is replaced
    kit.store.update_work_item_metadata(item.id, worker_handoff=replace(
        intent, source_review_round=round_index, target_review_bounce=round_index, source_review_feedback=feedback or None, **fields))
    return kit.store.get_work_item(item.id).worker_handoff


def nits_review(kit, *, bounces=0, continuation=None):
    """Node `a` reviewed with pass-with-nits as its ``bounces + 1``-th review; the Loop has not collected the verdict."""
    manifest, path, item = kit.in_review([{"hang": 4}, kit.stack.review("pass-with-nits")], *HANG)
    kit.wait_for(lambda: kit.store.get_work_item(item.id).review_verdict == "pass-with-nits", what="the verdict")
    kit.wait_runs_ended(item.id)
    if bounces:
        current = kit.store.get_work_item(item.id)
        kit.store.update_work_item_metadata(item.id, review_bounce=bounces, **({"review_continuation": continuation} if continuation else {}))
        subject = review_subject_digest(kit.store.get_work_item(item.id), bounces + 1)
        kit.store.update_work_item_metadata(item.id, review_subject_digest=subject,
                                            reviewer_run_baseline=replace(current.reviewer_run_baseline, subject_digest=subject))
    return manifest, path, item


CONTINUATION = {"schema": "omac.review-continuation/v1", "stage": "develop", "mode": "producer-rework", "authorized_through_round": 10,
                "decision_count": 1, "reason": "operator approved one additional review round"}


class AssignmentReached(Exception):
    pass


def unassigned_handoff(kit):
    """Node `a` with a Worker handoff that has not started a Run yet (everything before the assignment is done)."""
    kit.monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)
    manifest, path, item = kit.dispatched(wait=False)
    current = kit.store.get_work_item(item.id)
    baseline = tuple(sorted(run.id for run in kit.runtime.list_runs(item.id) if run.kind == "direct"))
    kit.store.update_work_item_metadata(item.id, worker_handoff=replace(
        current.worker_handoff, target_run_id=None, baseline_direct_run_ids=baseline))
    return manifest, path, item


def residual_reviewer_baseline(kit, item):
    from omac.core.taskmeta import ReviewerRunBaseline

    return ReviewerRunBaseline(
        schema="omac.reviewer-run-baseline/v1", subject_digest="old-subject", target_reviewer="bob",
        target_agent_id=kit.store.resolve_agent_id("bob"), cutoff_created_at="2026-08-01T00:00:00Z", generation="review-old", attempt=1)


def boundary_setup(kit, required_fix, **blocker_fields):
    """Node `a` (fixture evidence, produces a tooling package) rejected by a Reviewer who asks for the downstream node's artifact."""
    assembly = kit.node("assembly", worker="carol", blocked_by=["a"], contract=contract(
        evidence_mode=EvidenceMode.LIVE, produces=[ProducedArtifact("production-bundle")]))
    manifest, path, item = rejected(kit, *HANG, others=(assembly,), node_contract=contract(
        evidence_mode=EvidenceMode.FIXTURE, produces=[ProducedArtifact("tooling-package")]))
    report = kit.store.get_work_item(item.id).review_report
    report["blockers"][0].update({"required_fix": required_fix, **blocker_fields})
    kit.store.update_work_item_metadata(item.id, review_report=report)
    return manifest, path, item

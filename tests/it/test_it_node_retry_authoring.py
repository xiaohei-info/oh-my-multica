"""`omac node retry` back to authoring on a real Multica: the rejected feedback is never retired before a durable successor exists.

Node `b` is a delivery rejected in its first review round and blocked; no Agent has run (the facts are written through the store).
The retry persists a recovery intent, restores the authoring generation and then completes the intent; a crash or a lost reply
at any of those writes is completed by the next retry, and a source that changed meanwhile is refused.
"""
import uuid
from copy import deepcopy
from dataclasses import replace

import pytest
import yaml

from omac.cli import exit_codes
from omac.core.manifest import Contract, load_manifest, save_manifest
from omac.core.retry_budget import consumed_bounces
from omac.core.taskmeta import TaskPhase, WorkerHandoffIntent, review_context_binding
from omac.core.workitem import WorkItemControlProjection, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline.dispatch import _previous_review_context, build_show_output
from omac.pipeline.loop import _dispatch_worker_handoff_locked

CRASH = RuntimeError("offline crash")


def rejected_node(kit, *, bounces=1, **metadata):
    """Node `b` (Worker bob, Reviewer alice): a delivery with its verification and a reject report, blocked. Returns (item id, manifest path)."""
    verification = {"commands": [], "integration_gates": [], "pr_base": "main", "coverage": 100}
    report = {"full_review_completed": True, "blockers": [{"summary": "restore the context", "required_fix": "provide the next change"}],
              "nits": []}
    item = kit.item(
        "rejected", worker="bob", reviewer="alice", status=WorkItemStatus.BLOCKED, phase=TaskPhase.REVIEW,
        artifacts={"pr_url": f"https://example.test/pr/{uuid.uuid4().hex[:8]}", "head_sha": "head-1"}, verification=verification,
        verification_source=yaml.safe_dump(verification), review_verdict="reject", review_report=report,
        review_report_source=yaml.safe_dump(report), review_subject_digest="rejected-subject", review_bounce=bounces, **metadata)
    node = kit.node("b", worker="bob", reviewer="alice")
    node.status, node.work_item_id = "blocked", item.id
    return item.id, kit.save(kit.manifest([node]), "retry.yaml")


def no_dispatch(kit):
    """A retry only prepares; it assigns nobody and starts nothing."""
    kit.fault(kit.store, "assign_work_item", when="before", error=AssertionError("a retry must not assign"))
    kit.fault(kit.runtime, "wake", when="before", error=AssertionError("a retry must not wake"))


def is_intent(state):
    return lambda _id, **meta: getattr(meta.get("worker_handoff"), "state", None) == state


def partial_restore(kit):
    """The platform applied only part of the generation switch before the connection dropped (once)."""
    original, state = kit.store.restore_authoring_generation, {"armed": True, "fired": False}

    def partial(item_id, *args, **kwargs):
        if not state["armed"]:
            return original(item_id, *args, **kwargs)
        state["fired"] = True
        kit.store.reset_review(item_id)
        kit.store.update_work_item_metadata(item_id, phase=TaskPhase.AUTHORING)
        kit.store.update_status(item_id, WorkItemStatus.TODO)
        raise PlatformError("offline partial restore")

    kit.monkeypatch.setattr(kit.store, "restore_authoring_generation", partial)
    return state


def restore_follows_the_intent(kit, item_id, report_ref):
    """The generation switch may only start once the recovery intent that carries the rejected report is durable."""
    def check(*_args, **_kwargs):
        handoff = kit.store.get_work_item(item_id).worker_handoff
        assert handoff.state == "recovering" and handoff.source_review_feedback["report_ref"] == report_ref

    kit.before(kit.store, "restore_authoring_generation", check)


# ---- every write of the retry may fail or be lost -----------------------------------------------------------------------

FAULTS = {          # checkpoint: (arm, exit code of the first retry or the exception it raises)
    "intent-rejected": (lambda kit: kit.fault(kit.store, "update_work_item_metadata", when="before", only=is_intent("recovering")),
                        exit_codes.PLATFORM),
    "intent-reply-lost": (lambda kit: kit.fault(kit.store, "update_work_item_metadata", only=is_intent("recovering")), exit_codes.OK),
    "restore-reply-lost": (lambda kit: kit.fault(kit.store, "restore_authoring_generation"), exit_codes.OK),
    "crash-after-intent": (lambda kit: kit.fault(kit.store, "update_work_item_metadata", error=CRASH, only=is_intent("recovering")), CRASH),
    "crash-after-restore": (lambda kit: kit.fault(kit.store, "restore_authoring_generation", error=CRASH), CRASH),
    "finish-reply-lost": (lambda kit: kit.fault(kit.store, "update_work_item_metadata", only=is_intent("pending")), exit_codes.OK),
    "finish-rejected": (lambda kit: kit.fault(kit.store, "update_work_item_metadata", when="before", only=is_intent("pending")),
                        exit_codes.PLATFORM),
    "partial-restore": (partial_restore, exit_codes.PLATFORM),
}


@pytest.mark.parametrize("checkpoint", FAULTS)
def test_a_crash_or_a_lost_reply_at_any_write_of_the_retry_keeps_the_rejected_source_and_the_next_retry_completes_it(kit, node, checkpoint):
    item_id, path = rejected_node(kit, worker_bounce=20, review_generation="old-generation", review_ledger_generation="old-generation")
    old = kit.store.get_work_item(item_id)
    no_dispatch(kit)
    restore_follows_the_intent(kit, item_id, old.review_report_ref)
    arm, outcome = FAULTS[checkpoint]
    fault = arm(kit)
    meta_before = deepcopy(load_manifest(path).meta)

    if isinstance(outcome, Exception):
        with pytest.raises(RuntimeError, match="offline crash"):
            node("retry", path, "b")
    else:
        assert node("retry", path, "b")[0] == outcome, checkpoint

    interrupted = kit.store.get_work_item(item_id)
    assert fault["fired"]
    if checkpoint == "intent-rejected":
        assert (interrupted.review_report_ref, interrupted.review_subject_digest, interrupted.review_generation) == (
            old.review_report_ref, old.review_subject_digest, "old-generation")
    else:
        assert interrupted.worker_handoff.source_review_feedback["report_ref"] == old.review_report_ref
    fault["armed"] = False

    assert node("retry", path, "b")[0] == exit_codes.OK

    completed = kit.store.get_work_item(item_id)
    handoff = completed.worker_handoff
    assert handoff.state == "pending" and handoff.authoring_recovery is None
    assert handoff.review_context_binding == review_context_binding(completed)
    assert handoff.source_review_subject_digest == old.review_subject_digest
    assert _previous_review_context(completed)["report_ref"] == old.review_report_ref
    assert (completed.bounces.worker, completed.bounces.review) == (20, 1)
    assert completed.phase is TaskPhase.AUTHORING and completed.status is WorkItemStatus.TODO

    assert node("retry", path, "b")[0] == exit_codes.OK                      # once more: nothing is rewritten
    assert kit.store.get_work_item(item_id).worker_handoff == handoff and load_manifest(path).meta == meta_before


# ---- a pending recovery is refused when the source it was prepared from changed --------------------------------------------

DRIFTS = {
    "contract": lambda kit, item_id: kit.store.set_node_contract(item_id, Contract(objective="changed contract")),
    "generation": lambda kit, item_id: kit.store.update_work_item_metadata(item_id, review_generation="foreign"),
    "head": lambda kit, item_id: kit.store.update_work_item_metadata(
        item_id, artifacts={**kit.store.get_work_item(item_id).artifacts, "head_sha": "foreign"}),
    "verification": lambda kit, item_id: kit.store.update_work_item_metadata(
        item_id, verification={"commands": [], "integration_gates": [], "pr_base": "main", "coverage": 50},
        verification_source=yaml.safe_dump({"coverage": 50})),
    "counter": lambda kit, item_id: kit.store.update_work_item_metadata(item_id, worker_bounce=1),
    "subject": lambda kit, item_id: kit.store.update_work_item_metadata(item_id, review_subject_digest="foreign"),
    "status": lambda kit, item_id: kit.store.update_status(item_id, WorkItemStatus.IN_PROGRESS),
    "assignee": lambda kit, item_id: kit.store.assign_work_item(item_id, "carol", "worker"),
}


def interrupted_retry(kit, node, **kwargs):
    """A first retry that crashed at the generation switch: the recovery intent is durable, nothing else was restored."""
    item_id, path = rejected_node(kit, **kwargs)
    crash = crash_at_restore(kit)
    with pytest.raises(RuntimeError, match="offline crash"):
        node("retry", path, "b")
    crash["armed"] = False
    return item_id, path


def crash_at_restore(kit):
    return kit.fault(kit.store, "restore_authoring_generation", when="before", error=CRASH)


@pytest.mark.parametrize("drift", DRIFTS)
def test_a_pending_authoring_retry_fails_closed_when_the_source_it_was_prepared_from_changed(kit, node, drift):
    item_id, path = interrupted_retry(kit, node)
    assert kit.store.get_work_item(item_id).worker_handoff.state == "recovering"
    DRIFTS[drift](kit, item_id)
    before = kit.store.get_work_item(item_id)
    writes = kit.record(kit.store, "update_work_item_metadata", "restore_authoring_generation", "reset_review", "update_status")

    code, _, err = node("retry", path, "b")

    assert code == exit_codes.PLATFORM and "changed" in err and writes == [], drift
    assert kit.store.get_work_item(item_id) == before


def test_an_incomplete_recovery_intent_blocks_the_dispatch_of_the_worker(kit, node):
    item_id, path = interrupted_retry(kit, node)
    item = kit.store.get_work_item(item_id)
    assert not item.worker_handoff.is_causally_bound()
    kit.fault(kit.runtime, "wake", when="before", error=AssertionError("incomplete recovery cannot dispatch"))

    with pytest.raises(PlatformError, match="Authoring recovery is incomplete"):
        _dispatch_worker_handoff_locked(kit.store, kit.runtime, kit.code_host, load_manifest(path), "b", projection=WorkItemControlProjection(item))


def test_a_repeated_retry_does_not_accept_a_pending_intent_that_lost_its_target_agent(kit, node):
    item_id, path = rejected_node(kit)
    assert node("retry", path, "b")[0] == exit_codes.OK
    pending = kit.store.get_work_item(item_id)
    kit.store.update_work_item_metadata(item_id, worker_handoff=replace(pending.worker_handoff, target_agent_id=None))
    before = kit.store.get_work_item(item_id)

    code, _, _ = node("retry", path, "b")

    assert code == exit_codes.PLATFORM and kit.store.get_work_item(item_id) == before


# ---- which review a retry may keep carrying ---------------------------------------------------------------------------------

def test_a_later_authoring_retry_keeps_the_originally_rejected_subject(kit, node):
    item_id, path = rejected_node(kit, bounces=2)
    subject = kit.store.get_work_item(item_id).review_subject_digest
    kit.fault(kit.runtime, "wake", when="before", error=AssertionError("a retry cannot dispatch"))

    assert node("retry", path, "b", "--stage", "authoring")[0] == exit_codes.OK

    first = kit.store.get_work_item(item_id)
    prior = deepcopy(first.worker_handoff)
    assert prior.source_review_subject_digest == subject and first.review_subject_digest is None
    kit.store.update_status(item_id, WorkItemStatus.BLOCKED)
    counters = deepcopy(kit.store.get_work_item(item_id).bounces)

    assert node("retry", path, "b", "--stage", "authoring")[0] == exit_codes.OK

    again = kit.store.get_work_item(item_id)
    assert again.worker_handoff.source_review_subject_digest == subject
    assert again.worker_handoff.source_review_feedback == prior.source_review_feedback
    assert again.worker_handoff.source_review_round == prior.source_review_round
    assert again.bounces == counters and again.phase is TaskPhase.AUTHORING


@pytest.mark.parametrize(("ledger_current", "prior_reject"), [(False, False), (True, False), (False, True)])
def test_a_current_worker_handoff_does_not_renew_an_older_review_generation(kit, node, ledger_current, prior_reject):
    item_id, path = rejected_node(kit)
    kit.store.reset_review(item_id)
    kit.store.update_work_item_metadata(
        item_id, review_generation="amendment-current", review_ledger_generation="amendment-current" if ledger_current else "retired-review",
        review_bounce=1, worker_bounce=88, bounce_baseline={"worker": 88, "review": 1, "merge": 0})
    item = kit.store.get_work_item(item_id)
    intent = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="bob", gate="operator-retry" if prior_reject else "explicit-dispatch",
        source_review_subject_digest="subject", source_review_round=1, source_review_verdict="reject" if prior_reject else None,
        target_review_bounce=1, generation="current-handoff", target_agent_id=kit.store.resolve_agent_id("bob"),
        review_context_binding=review_context_binding(item), target_worker_bounce=88)
    decision = {"schema": "omac.decision-required/v1", "reason_code": "worker-decision-required", "kind": "develop", "phase": "authoring",
                "gate": "worker", "resume_issue_id": item_id, "review_context_binding": review_context_binding(item),
                "blocker": {"schema": "omac.worker-blocker/v2", "reason_code": "quality-gate-failed"}}
    kit.store.update_work_item_metadata(item_id, worker_handoff=intent, decision_required=decision)
    kit.store.update_status(item_id, WorkItemStatus.BLOCKED)
    before = kit.store.get_work_item(item_id)
    manifest = load_manifest(path)
    manifest.meta.update({"last_amendment_id": "amend-budget", "amendment_apply": {
        "schema": "omac.amendment-apply/v1", "amendment_id": "amend-budget",
        "nodes": {"b": {"stage": "authoring", "state": "synced", "work_item_id": item_id, "bounce_baseline": {"worker": 88, "review": 1, "merge": 0}}}}})
    save_manifest(manifest, path)
    budget_authority = deepcopy(manifest.meta)
    recovered = kit.record(kit.store, "recover_review_rework_context")
    writes = kit.record(kit.store, "update_work_item_metadata")

    code, _, _ = node("retry", path, "b", "--stage", "authoring")

    after = kit.store.get_work_item(item_id)
    if ledger_current or prior_reject:
        assert code == exit_codes.NEEDS_DECISION
        assert after.decision_required == before.decision_required and after.worker_handoff == before.worker_handoff
        assert writes == [] and [call[1] for call in recovered] == [(item_id,)]
    else:
        assert code == exit_codes.OK and recovered == []
        assert after.worker_handoff is None and not after.decision_required and after.status is WorkItemStatus.TODO
        assert load_manifest(path).nodes["b"].status == "todo"
        assert "previous_review" not in build_show_output(after, "worker:bob")["context"]
    assert after.bounces == before.bounces
    restored = load_manifest(path)
    assert restored.meta == budget_authority and consumed_bounces(restored, "b", after, "worker") == 0
    assert consumed_bounces(manifest, "b", before, "worker") == 0
    assert after.bounce_baseline == (before.bounce_baseline if code == exit_codes.NEEDS_DECISION else None)    # the Store projection, not the manifest
    assert after.review_ledger == before.review_ledger and after.artifacts == before.artifacts

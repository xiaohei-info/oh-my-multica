"""A Worker handoff prepared on a real Multica while one write or read is lost, delayed or stale.

Each case drives ``loop._dispatch`` on a node whose issue still carries a stale review, injects one fault at a single
adapter method, then restarts the Loop and checks that exactly one Run results and nothing is written twice.
"""
from dataclasses import replace

import pytest

from _support.loopkit import contract
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase
from omac.core.workitem import WorkItemControlProjection, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop

TIMEOUT = PlatformError("Request timed out: server did not respond")


@pytest.fixture()
def prepared(kit):
    """An issue left in review with a stale reject verdict; its node is `todo` and must be handed to the Worker."""
    node = kit.node("handoff-preparation", contract=contract())
    item = kit.item(node.id, contract=node.contract)
    kit.store.update_work_item_metadata(
        item.id, phase=TaskPhase.REVIEW, review_verdict="reject", review_comment="stale blocker",
        review_subject_digest="stale-review-subject")
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    node.work_item_id = item.id
    manifest = kit.manifest([node])
    kit.transient = lambda value=True: kit.monkeypatch.setattr(
        kit.store, "is_transient_transport_error", lambda _error: value)
    kit.fast_observation = lambda: kit.monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)
    return manifest, kit.save(manifest), node, item, kit.comments(item.id)


def dispatch(kit, manifest, path, node):
    return loop._dispatch(kit.store, kit.runtime, kit.code_host, manifest, path, [node.id], 1)


def restart(kit, path):
    return kit.tick(load_manifest(path), path, max_parallel=1)


def test_reset_timeout_after_apply_observes_and_dispatches(kit, prepared):
    """A timed-out idempotent write is accepted only after the authority confirms it."""
    manifest, path, node, item, comments = prepared
    reset = kit.fault(kit.store, "reset_review", when="after", error=TIMEOUT)
    kit.transient()

    assert dispatch(kit, manifest, path, node) == [node.id]

    current = kit.store.get_work_item(item.id)
    assert reset["calls"] == 1
    assert manifest.nodes[node.id].status == "in_progress"
    assert current.status is WorkItemStatus.IN_PROGRESS and current.phase is TaskPhase.AUTHORING
    assert current.review_verdict is None and current.worker_handoff is not None
    assert len(kit.runtime.list_runs(item.id)) == 1
    assert kit.comments(item.id) == comments


def test_reset_timeout_before_apply_stays_pending_and_restarts_with_one_run(kit, prepared):
    """An unconfirmed preparation keeps its intent and creates no duplicate Run."""
    manifest, path, node, item, comments = prepared
    bounces = kit.store.get_work_item(item.id).bounces
    reset = kit.fault(kit.store, "reset_review", when="before", error=TIMEOUT)
    kit.transient()

    assert dispatch(kit, manifest, path, node) == []

    interrupted = kit.store.get_work_item(item.id)
    assert manifest.nodes[node.id].status == "in_progress"
    assert interrupted.status is WorkItemStatus.IN_REVIEW and interrupted.phase is TaskPhase.REVIEW
    assert interrupted.worker_handoff is not None and interrupted.bounces == bounces
    assert kit.runtime.list_runs(item.id) == [] and kit.comments(item.id) == comments

    reset["armed"] = False
    assigns = kit.record(kit.store, "assign_work_item")
    restarted = load_manifest(path)
    assert kit.tick(restarted, path, max_parallel=1).state == "running"
    assert kit.tick(restarted, path, max_parallel=1).state == "running"
    recovered = kit.store.get_work_item(item.id)
    assert recovered.status is WorkItemStatus.IN_PROGRESS and recovered.phase is TaskPhase.AUTHORING
    assert len(kit.runtime.list_runs(item.id)) == 1
    assert len([e for e in assigns if e[1][2] == "worker"]) == 1


def test_intent_timeout_after_apply_confirms_the_same_generation(kit, prepared):
    manifest, path, node, item, comments = prepared
    write = kit.fault(kit.store, "update_work_item_metadata", when="after", error=TIMEOUT,
                      only=lambda _id, **kw: kw.get("worker_handoff") is not None)
    kit.transient()

    assert dispatch(kit, manifest, path, node) == [node.id]

    current = kit.store.get_work_item(item.id)
    assert current.worker_handoff.generation == write["kwargs"]["worker_handoff"].generation
    assert len(kit.runtime.list_runs(item.id)) == 1 and kit.comments(item.id) == comments


def test_intent_timeout_before_apply_keeps_manifest_retryable(kit, prepared):
    manifest, path, node, item, comments = prepared
    bounces = kit.store.get_work_item(item.id).bounces
    write = kit.fault(kit.store, "update_work_item_metadata", when="before", error=TIMEOUT,
                      only=lambda _id, **kw: kw.get("worker_handoff") is not None)
    kit.transient()

    assert dispatch(kit, manifest, path, node) == []

    interrupted = kit.store.get_work_item(item.id)
    assert manifest.nodes[node.id].status == "todo"
    assert interrupted.worker_handoff is None and interrupted.bounces == bounces
    assert kit.runtime.list_runs(item.id) == [] and kit.comments(item.id) == comments

    write["armed"] = False
    assert restart(kit, path).state == "running"
    recovered = kit.store.get_work_item(item.id)
    assert recovered.worker_handoff.generation != write["kwargs"]["worker_handoff"].generation, "a fresh intent"
    assert len(kit.runtime.list_runs(item.id)) == 1


def test_intent_read_failure_keeps_todo_and_reuses_the_persisted_intent(kit, prepared):
    manifest, path, node, item, comments = prepared
    kit.fast_observation()
    kit.transient()
    original_update, original_observe = kit.store.update_work_item_metadata, kit.store.observe_work_item_control
    written = []

    def persist(item_id, **metadata):
        result = original_update(item_id, **metadata)
        if metadata.get("worker_handoff") is not None and not written:
            written.append(metadata["worker_handoff"])
        return result

    def unreadable(item_id):
        if written:
            raise TIMEOUT
        return original_observe(item_id)

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", persist)
    kit.monkeypatch.setattr(kit.store, "observe_work_item_control", unreadable)

    assert dispatch(kit, manifest, path, node) == []
    assert manifest.nodes[node.id].status == "todo"
    assert kit.runtime.list_runs(item.id) == [] and kit.comments(item.id) == comments

    kit.monkeypatch.setattr(kit.store, "observe_work_item_control", original_observe)
    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", original_update)
    assert kit.store.get_work_item(item.id).worker_handoff.generation == written[0].generation

    assert restart(kit, path).state == "running"
    assert kit.store.get_work_item(item.id).worker_handoff.generation == written[0].generation
    assert len(kit.runtime.list_runs(item.id)) == 1


def test_hard_platform_error_during_preparation_still_blocks(kit, prepared):
    manifest, path, node, item, comments = prepared
    kit.fault(kit.store, "reset_review", when="before", error=PlatformError("validation rejected: invalid metadata"))
    kit.transient(False)

    assert dispatch(kit, manifest, path, node) == []

    current = kit.store.get_work_item(item.id)
    assert manifest.nodes[node.id].status == "blocked" and current.status is WorkItemStatus.BLOCKED
    assert current.worker_handoff is not None
    assert kit.runtime.list_runs(item.id) == []
    assert len(kit.comments(item.id)) == len(comments) + 1, "the block is explained by exactly one comment"


@pytest.mark.parametrize("converges", [True, False])
def test_successful_write_with_stale_projection(kit, prepared, converges):
    """Reads that still show the old projection after the write: wait while bounded, else stay pending."""
    manifest, path, node, item, comments = prepared
    kit.fast_observation()
    original_reset, original_observe = kit.store.reset_review, kit.store.observe_work_item_control
    stale = WorkItemControlProjection(replace(original_observe(item.id).work_item))
    state = {"applied": False, "stale_reads": 0}

    def reset(item_id):
        original_reset(item_id)
        state["applied"] = True

    def observe(item_id):
        if state["applied"] and (not converges or state["stale_reads"] < 2):
            state["stale_reads"] += 1
            return stale
        return original_observe(item_id)

    kit.monkeypatch.setattr(kit.store, "reset_review", reset)
    kit.monkeypatch.setattr(kit.store, "observe_work_item_control", observe)

    first = dispatch(kit, manifest, path, node)

    if converges:
        assert first == [node.id] and state["stale_reads"] == 2
        assert len(kit.runtime.list_runs(item.id)) == 1
    else:
        assert first == [] and manifest.nodes[node.id].status == "in_progress"
        assert kit.runtime.list_runs(item.id) == []
        kit.monkeypatch.setattr(kit.store, "observe_work_item_control", original_observe)
        assert restart(kit, path).state == "running"
        assert len(kit.runtime.list_runs(item.id)) == 1
    assert kit.comments(item.id) == comments


def stale_body(kit, manifest, item):
    manifest.meta["source_issues"] = ["plan-current"]
    kit.store.update_work_item_metadata(item.id, source_refs=[{"label": "stale", "issue_id": "old"}],
                                        blocked_by=["stale-dependency"])
    return kit.comments(item.id)


def test_body_partial_apply_stays_pending_and_restarts(kit, prepared):
    manifest, path, node, item, _ = prepared
    comments = stale_body(kit, manifest, item)
    kit.fast_observation()
    kit.transient()
    original = kit.store.update_work_item_metadata
    attempted = []

    def partial(item_id, **metadata):
        if "description" in metadata and not attempted:
            attempted.append(True)
            original(item_id, description=metadata["description"])  # only the body lands
            raise TIMEOUT
        return original(item_id, **metadata)

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", partial)

    assert dispatch(kit, manifest, path, node) == []

    interrupted = kit.store.get_work_item(item.id)
    assert manifest.nodes[node.id].status == "in_progress" and interrupted.worker_handoff is not None
    assert interrupted.source_refs == [{"label": "stale", "issue_id": "old"}]
    assert interrupted.blocked_by == ["stale-dependency"]
    assert kit.runtime.list_runs(item.id) == [] and kit.comments(item.id) == comments

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", original)
    assert restart(kit, path).state == "running"
    recovered = kit.store.get_work_item(item.id)
    assert recovered.source_refs != [{"label": "stale", "issue_id": "old"}] and recovered.blocked_by == []
    assert len(kit.runtime.list_runs(item.id)) == 1


def test_body_timeout_after_full_apply_observes_and_dispatches(kit, prepared):
    manifest, path, node, item, _ = prepared
    stale_body(kit, manifest, item)
    kit.fault(kit.store, "update_work_item_metadata", when="after", error=TIMEOUT,
              only=lambda _id, **kw: "description" in kw)
    kit.transient()

    assert dispatch(kit, manifest, path, node) == [node.id]

    current = kit.store.get_work_item(item.id)
    assert current.source_refs != [{"label": "stale", "issue_id": "old"}] and current.blocked_by == []
    assert len(kit.runtime.list_runs(item.id)) == 1


def test_body_read_failure_stays_pending_and_restarts(kit, prepared):
    manifest, path, node, item, _ = prepared
    comments = stale_body(kit, manifest, item)
    kit.fast_observation()
    kit.transient()
    original_update, original_observe = kit.store.update_work_item_metadata, kit.store.observe_work_item_control
    applied = []

    def apply_body(item_id, **metadata):
        result = original_update(item_id, **metadata)
        if "description" in metadata:
            applied.append(True)
        return result

    def unreadable(item_id):
        if applied:
            raise TIMEOUT
        return original_observe(item_id)

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", apply_body)
    kit.monkeypatch.setattr(kit.store, "observe_work_item_control", unreadable)

    assert dispatch(kit, manifest, path, node) == []
    assert manifest.nodes[node.id].status == "in_progress"
    assert kit.runtime.list_runs(item.id) == [] and kit.comments(item.id) == comments

    kit.monkeypatch.setattr(kit.store, "observe_work_item_control", original_observe)
    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", original_update)
    assert restart(kit, path).state == "running"
    assert len(kit.runtime.list_runs(item.id)) == 1

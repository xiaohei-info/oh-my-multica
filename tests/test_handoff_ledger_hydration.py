"""A fresh control observation defers a referenced ledger; it does not delete it."""

from copy import deepcopy
from dataclasses import replace
from omac.engines.mock import MockCodeHost
import json
from unittest.mock import Mock

import pytest
import yaml

from omac.core.manifest import Manifest, Node
from omac.core.taskmeta import current_review_ledger
from omac.engines.models import EngineConfig
from omac.core.workitem import AgentRunObservation, WorkItemPayload
from omac.engines.multica import MulticaStore
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.convergence import ResolutionState
from test_lightweight_reconcile import _RemoteFixture


def _round3_issue():
    """A mid-authoring node after three rejected reviews whose handoff and ledger are externalized."""
    from omac.core.taskmeta import WorkerHandoffIntent
    from test_lightweight_reconcile import _issue, _ref
    from test_review_convergence import _production_multi_blocker_ledger

    item_id = "item-round3"
    raw, attachments = _issue(item_id, status="in_progress", phase="authoring")
    metadata = raw["metadata"]
    ledger_ref, ledger_attachment = _ref(
        item_id, "review_ledger", "omac-review-ledger-3.yaml",
        yaml.safe_dump(
            # Two cycles after a ledger reset; the handoff carries the absolute round 3.
            _production_multi_blocker_ledger([("new", "new", "new"), ("unchanged", "deeper", "deeper")]),
            sort_keys=False).encode())
    report_ref = metadata["review_report_ref"]
    attachments[ledger_ref["attachment_id"]] = ledger_attachment
    generation = "authoring-gen-1"
    metadata.update({
        "dag_key": "harness-node", "worker": "worker-a", "reviewer": "reviewer-a",
        "review_ledger_ref": ledger_ref, "review_bounce": 3, "worker_bounce": 88,
        "review_generation": generation, "review_ledger_generation": generation,
        "review_verdict": "", "blocked_by": [], "bounce_baseline": {}, "decision_required": {},
        "delivery_identity": {}, "review_continuation": {}, "reviewer_run_baseline": {},
    })
    raw["assignee_id"], raw["assignee_type"] = "agent-a", "agent"
    intent = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="worker-a", gate="review",
        source_review_subject_digest="v2", source_review_round=3, source_review_verdict="reject",
        source_review_feedback={
            "schema": "omac.worker-rework-feedback/v1", "verdict": "reject",
            "report_ref": report_ref, "ledger_ref": ledger_ref,
            "blockers": [{"root_cause_key": "root-core", "summary": "core invariant",
                          "required_fix": "bind the fact to its subject"}]},
        review_context_binding={"generation": generation, "contract_sha256": "contract-sha"},
        target_review_bounce=3, generation="handoff-3", target_agent_id="agent-a",
        baseline_direct_run_ids=tuple(f"run-{i}" for i in range(20)),
        baseline_verification_attachment_id=metadata["verification_ref"]["attachment_id"],
        target_run_id="run-target", target_worker_bounce=88,
    ).as_dict()
    body = json.dumps(intent, ensure_ascii=False).encode()
    handoff_ref, handoff_attachment = _ref(item_id, "worker_handoff", "omac-worker-handoff-3.json", body)
    attachments[handoff_ref["attachment_id"]] = handoff_attachment
    metadata["worker_handoff"] = json.dumps({
        "schema": "omac.control-payload/v1", "key": "worker_handoff", "issue_id": item_id,
        "ref": handoff_ref, "overlay": {}})
    return raw, attachments


@pytest.fixture
def case(monkeypatch):
    raw, attachments = _round3_issue()
    remote = _RemoteFixture({raw["id"]: raw}, attachments)
    store = MulticaStore(EngineConfig("multica", "ws"))
    monkeypatch.setattr(store, "_run_multica", remote.run)
    for name in [
        "update_work_item_metadata",
        "assign_work_item",
        "clear_assignment",
        "reset_review",
        "update_status",
    ]:
        monkeypatch.setattr(
            store,
            name,
            lambda *a, **kw: pytest.fail(
                "Read-only active handoff cannot mutate Store"
            ),
        )
    projection = store.observe_work_item_control(raw["id"])
    node = Node(
        id="harness-node",
        worker=projection.work_item.worker,
        work_item_id=raw["id"],
        status="in_progress",
    )
    manifest = Manifest(meta={}, nodes={node.id: node})
    runtime = Mock()
    intent = projection.work_item.worker_handoff
    # Synthetic Runtime view only: test an already-bound formal active Run.
    runtime.list_runs.return_value = [
        AgentRunObservation(
            id=intent.target_run_id,
            kind="direct",
            status="running",
            agent_id=intent.target_agent_id,
            created_at="2099-01-01T00:00:00Z",
            trigger_kind="issue_assignment",
        )
    ]
    runtime.wake.side_effect = lambda *a, **kw: pytest.fail(
        "Active Worker must not be dispatched again"
    )
    return store, remote, projection, intent, manifest, runtime


def test_fresh_locked_dispatch_hydrates_original_round3_ledger(case):
    store, remote, projection, intent, manifest, runtime = case
    before = deepcopy(remote.issues)
    assert current_review_ledger(projection.work_item) is None
    assert WorkItemPayload.REVIEW_LEDGER in projection.deferred_payloads
    result = loop._dispatch_worker_handoff_locked(
        store, runtime, MockCodeHost(), manifest, "harness-node", projection=projection
    )
    assert result.state == "waiting" and result.intent == intent
    assert remote.issues == before
    assert (
        remote.attachment_downloads == 2
    )  # full handoff + exact ledger, no other payload
    assert runtime.wake.call_count == 0


def test_shared_resolution_hydrates_without_changing_control_facts(case):
    store, remote, projection, intent, _, _ = case
    before = deepcopy(projection.work_item)
    result = loop._resolve_handoff_review_convergence(
        projection.work_item, intent, "harness-node", store=store
    )
    assert result.state is ResolutionState.VALID
    assert projection.work_item == before
    assert remote.attachment_downloads == 2


@pytest.mark.parametrize(
    "failure",
    [
        "missing-ref",
        "tampered",
        "unavailable",
        "byte-length",
        "stale-generation",
        "ref-drift",
        "round-mismatch-subject",
    ],
)
def test_missing_or_changed_primary_facts_fail_closed(case, failure):
    store, remote, projection, intent, _, _ = case
    item = projection.work_item
    if failure == "missing-ref":
        item = replace(item, review_ledger_ref=None)
    if failure == "tampered":
        ref = item.review_ledger_ref
        remote.attachments[ref["attachment_id"]] = (ref["filename"], b"{}")
    if failure == "unavailable":
        remote.fail_attachment_id = item.review_ledger_ref["attachment_id"]
    if failure == "byte-length":
        ref = {**item.review_ledger_ref, "bytes": item.review_ledger_ref["bytes"] + 1}
        item = replace(item, review_ledger_ref=ref)
        intent = replace(
            intent,
            source_review_feedback={**intent.source_review_feedback, "ledger_ref": ref},
        )
        remote.issues[item.id]["metadata"]["review_ledger_ref"] = json.dumps(ref)
    if failure == "stale-generation":
        item = replace(item, review_generation="foreign")
        remote.issues[item.id]["metadata"]["review_generation"] = "foreign"
    if failure == "ref-drift":
        item = replace(
            item,
            review_ledger_ref={**item.review_ledger_ref, "attachment_id": "foreign"},
        )
    if failure == "round-mismatch-subject":
        intent = replace(intent, source_review_subject_digest="foreign-subject")
    with pytest.raises(PlatformError):
        loop._resolve_handoff_review_convergence(
            item, intent, "harness-node", store=store
        )


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "review_generation",
        "review_ledger_ref",
        "worker_handoff",
        "contract_ref",
        "bounce_baseline",
    ],
)
def test_hydration_cannot_overwrite_new_control_observation(case, field):
    store, remote, projection, intent, _, _ = case
    original = remote.run

    def change(args, **kw):
        result = original(args, **kw)
        if (
            args[:2] == ["attachment", "download"]
            and args[2] == projection.work_item.review_ledger_ref["attachment_id"]
        ):
            raw = remote.issues[projection.work_item.id]
            if field == "status":
                raw["status"] = "done"
            elif field == "bounce_baseline":
                raw["metadata"][field] = json.dumps(
                    {"worker": 88, "review": 3, "merge": 0}
                )
            else:
                raw["metadata"][field] = (
                    "foreign" if field == "review_generation" else "{}"
                )
        return result

    store._run_multica = change
    with pytest.raises(PlatformError, match="changed"):
        loop._resolve_handoff_review_convergence(
            projection.work_item, intent, "harness-node", store=store
        )
    assert remote.issues[projection.work_item.id]["status"] == (
        "done" if field == "status" else "in_progress"
    )


def test_low_round_retains_zero_ledger_download_path(case):
    store, remote, projection, intent, _, _ = case
    intent = replace(intent, source_review_round=2, target_review_bounce=2)
    before = remote.attachment_downloads
    before_gets = remote.issue_gets
    assert (
        loop._resolve_handoff_review_convergence(
            projection.work_item, intent, "harness-node", store=store
        ).state
        is ResolutionState.VALID
    )
    assert remote.attachment_downloads == before
    assert remote.issue_gets == before_gets


def test_already_hydrated_ledger_is_not_downloaded_again(case):
    store, remote, projection, intent, _, _ = case
    item = store.hydrate_work_item_evidence(
        projection, frozenset({WorkItemPayload.REVIEW_LEDGER})
    )
    before = remote.attachment_downloads
    assert (
        loop._resolve_handoff_review_convergence(
            item, intent, "harness-node", store=store
        ).state
        is ResolutionState.VALID
    )
    assert remote.attachment_downloads == before


@pytest.mark.parametrize("field", ["status", "worker_handoff"])
def test_loaded_body_does_not_hide_new_control_facts(case, field):
    store, remote, projection, intent, _, _ = case
    item = store.hydrate_work_item_evidence(
        projection, frozenset({WorkItemPayload.REVIEW_LEDGER})
    )
    raw = remote.issues[item.id]
    if field == "status":
        raw["status"] = "done"
    else:
        raw["metadata"]["worker_handoff"] = "{}"
    before = remote.attachment_downloads
    with pytest.raises(PlatformError, match="changed"):
        loop._resolve_handoff_review_convergence(
            item, intent, "harness-node", store=store
        )
    assert remote.attachment_downloads == before

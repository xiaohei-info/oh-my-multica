"""Regression: a deferred contract is not the JSON null contract."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from omac.core.manifest import Manifest, Node, _load_contract
from omac.core.taskmeta import review_context_binding
from omac.engines.models import WorkItemPayload
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.dispatch import build_show_output
from test_lightweight_reconcile import _issue, _RemoteFixture, _store


class IntentCaptured(Exception):
    pass


def fixture(monkeypatch):
    raw, attachments = _issue("worker", status="in_progress", phase="authoring")
    remote = _RemoteFixture({"worker": raw}, attachments)
    store = _store(remote)
    current = store.get_work_item("worker")
    store = _store(
        remote
    )  # Cold adapter: avoid the earlier inspection's payload cache.
    node = Node(
        id="worker",
        worker=current.worker,
        work_item_id="worker",
        contract=_load_contract(current.contract),
        status="in_progress",
    )
    manifest = Manifest(meta={}, nodes={"worker": node})
    runtime = Mock()
    runtime.capabilities.stable_direct_run_identity = True
    runtime.list_runs.return_value = []
    monkeypatch.setattr(store, "resolve_agent_id", lambda _: "worker-agent")
    captured = []

    def save(_, **metadata):
        if "worker_handoff" in metadata:
            captured.append(metadata["worker_handoff"])
            raise IntentCaptured()
        raise AssertionError(f"Unexpected pre-binding write: {metadata}")

    monkeypatch.setattr(store, "update_work_item_metadata", save)
    remote.attachment_downloads = 0
    return store, runtime, manifest, remote, captured, current


def test_explicit_dispatch_binds_materialized_contract_not_null(monkeypatch):
    store, runtime, manifest, remote, captured, current = fixture(monkeypatch)
    projection = store.observe_work_item_control("worker")
    assert projection.work_item.contract is None
    assert WorkItemPayload.CONTRACT in projection.deferred_payloads
    null_hash = "74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b"
    assert review_context_binding(projection.work_item)["contract_sha256"] == null_hash
    with pytest.raises(IntentCaptured):
        loop._dispatch_worker_handoff(
            store,
            runtime,
            manifest,
            "worker",
            gate="explicit-dispatch",
            review_bounce=0,
        )
    intent = captured[0]
    assert intent.review_context_binding == review_context_binding(current)
    assert intent.review_context_binding["contract_sha256"] != null_hash
    shown = build_show_output(replace(current, worker_handoff=intent), "worker:worker")
    assert (
        shown["control"]["blocker_report_template"]["review_context_binding"]
        == intent.review_context_binding
    )
    assert (
        remote.attachment_downloads == 1
    )  # Contract only; not stale verification/report.
    runtime.wake.assert_not_called()


@pytest.mark.parametrize("failure", ["transport", "unmaterialized"])
def test_contract_hydration_failure_does_not_persist_null_intent(monkeypatch, failure):
    store, runtime, manifest, remote, captured, current = fixture(monkeypatch)
    if failure == "transport":
        remote.fail_attachment_id = current.contract_ref["attachment_id"]
    else:
        monkeypatch.setattr(
            store,
            "hydrate_work_item_evidence",
            lambda projection, plan: projection.work_item,
        )
    with pytest.raises(PlatformError):
        loop._dispatch_worker_handoff(
            store,
            runtime,
            manifest,
            "worker",
            gate="explicit-dispatch",
            review_bounce=0,
        )
    assert captured == []
    runtime.wake.assert_not_called()


@pytest.mark.parametrize("stale_null_binding", [False, True])
def test_materialized_handoff_can_block_but_null_binding_stays_rejected(
    monkeypatch, stale_null_binding
):
    from omac.engines.models import AgentRunObservation
    from omac.errors import ValidationError
    from omac.pipeline.worker_decision import report_decision

    store, runtime, manifest, remote, captured, current = fixture(monkeypatch)
    with pytest.raises(IntentCaptured):
        loop._dispatch_worker_handoff(
            store,
            runtime,
            manifest,
            "worker",
            gate="explicit-dispatch",
            review_bounce=0,
        )
    intent = replace(captured[0], target_run_id="new-fixture-run")
    if stale_null_binding:
        intent = replace(
            intent,
            review_context_binding=review_context_binding(
                replace(current, contract=None)
            ),
        )
    remote.issues["worker"]["metadata"]["worker_handoff"] = intent.as_dict()
    current = store.get_work_item("worker")
    report = build_show_output(current, "worker:worker")["control"][
        "blocker_report_template"
    ]
    report.update(
        summary="Contract requires a decision",
        decision_needed="Choose next action",
        contract_ref="objective",
        evidence=[
            {"ref": "local/failed.log", "observation": "Failure retained, not a pass"}
        ],
    )
    runtime.list_runs.return_value = [
        AgentRunObservation(
            "new-fixture-run",
            "direct",
            "running",
            agent_id="worker-agent",
            trigger_kind="issue_assignment",
        )
    ]
    writes = []

    def persist(item_id, **metadata):
        writes.append(metadata)
        remote.issues[item_id]["metadata"].update(metadata)

    monkeypatch.setattr(store, "update_work_item_metadata", persist)
    if stale_null_binding:
        with pytest.raises(ValidationError, match="Stale worker decision binding"):
            report_decision(store, runtime, "worker", report)
        assert writes == []
    else:
        result = report_decision(store, runtime, "worker", report)
        assert result["exit_code"] == 20 and result["terminal"]
        assert remote.issues["worker"]["status"] == "blocked"
        assert store.get_work_item("worker").bounces == current.bounces

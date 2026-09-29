"""Offline regressions for OAC recovery; no platform access."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from omac.core.taskmeta import TaskPhase, WorkerHandoffIntent
from omac.core.stage_recovery import validate_stage_recovery
from omac.core.review_convergence import validate_review_ledger
from omac.engines.models import EngineConfig, WorkItem, WorkItemStatus
from omac.engines.multica import MulticaStore, MulticaRuntime
from omac.errors import PlatformError
from omac.pipeline.dispatch import build_show_output, _previous_review_context


def item(**kwargs):
    return WorkItem(id="issue", workspace_id="ws", title="task", description="", dag_key="node", **kwargs)


@pytest.mark.parametrize("path", sorted((Path(__file__).parent / "fixtures/oac_legacy").glob("*.yaml")))
def test_completed_legacy_history_is_readable_but_not_active_evidence(path):
    ledger = yaml.safe_load(path.read_text())
    before = deepcopy(ledger)
    work = item(status=WorkItemStatus.DONE, review_ledger=ledger, artifacts={"pr_url": "https://example/pr/1"})
    output = build_show_output(work, "worker:reader")
    assert output["context"]["review_history"]["ledger"] == before
    assert output["context"]["review_history"]["validation"] == "unverified-history"
    assert "required_closures" not in output["context"]
    assert ledger == before
    with pytest.raises(ValueError):
        validate_review_ledger(ledger)


@pytest.mark.parametrize("message", ["TLS handshake timed out", "TLS 握手超时"])
def test_run_read_retries_handshake_timeout(monkeypatch, message):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"), sleeper=lambda _: None)
    call = Mock(side_effect=[PlatformError(message), []])
    monkeypatch.setattr(store, "_run_multica", call)
    assert MulticaRuntime(store).list_runs("issue") == []
    assert call.call_count == 2


@pytest.mark.parametrize("message", ["certificate verify failed", "HTTP 401 unauthorized", "HTTP 403 forbidden", "unknown failure", "certificate expired: TLS handshake timed out"])
def test_run_read_does_not_retry_hard_failures(monkeypatch, message):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"), sleeper=lambda _: None)
    call = Mock(side_effect=PlatformError(message))
    monkeypatch.setattr(store, "_run_multica", call)
    with pytest.raises(PlatformError, match=message):
        MulticaRuntime(store).list_runs("issue")
    assert call.call_count == 1


def test_review_recovery_requires_sealed_identity():
    work = item(status=WorkItemStatus.BLOCKED)
    with pytest.raises(ValueError, match="sealed delivery identity"):
        validate_stage_recovery(work, "review")
    validate_stage_recovery(work, "authoring")


def test_retired_review_cannot_be_recovered_or_exposed(monkeypatch):
    work = item(status=WorkItemStatus.BLOCKED, review_generation="sdk", review_ledger_generation="host", review_ledger={"cycles": []})
    work.worker_handoff = SimpleNamespace(gate="operator-retry", is_causally_bound=lambda: True, source_review_feedback={"comment": "require host positive"})
    assert _previous_review_context(work) is None
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "get_work_item", lambda _: work)
    call = Mock(return_value=[])
    monkeypatch.setattr(store, "_run_multica", call)
    assert store.recover_review_rework_context("issue") == {}
    call.assert_not_called()


def test_handoff_feedback_is_bound_to_contract_and_generation():
    from omac.core.taskmeta import review_context_binding, review_feedback_is_current
    work = item(status=WorkItemStatus.IN_PROGRESS, review_generation="sdk", contract={"goal": "SDK only"})
    handoff = WorkerHandoffIntent(review_context_binding=review_context_binding(work))
    assert review_feedback_is_current(work, handoff)
    assert not review_feedback_is_current(replace(work, review_generation="new"), handoff)
    assert not review_feedback_is_current(replace(work, contract={"goal": "Host"}), handoff)


def test_worker_blocker_stops_collection_without_run_or_budget(tmp_path):
    from omac.pipeline.dispatch import report_worker_blocker
    from omac.pipeline.loop import collect_results
    from omac.core.manifest import Manifest, Node
    work = item(status=WorkItemStatus.IN_PROGRESS, phase=TaskPhase.AUTHORING)
    store = Mock()
    store.get_work_item.return_value = work
    store.update_work_item_metadata.side_effect = lambda _, **kw: [setattr(work, k, v) for k, v in kw.items()]
    report = tmp_path / "blocker.yaml"
    report.write_text(yaml.safe_dump({"schema": "omac.worker-blocker/v1", "reason_code": "upstream-unreadable", "upstream_issue_id": "upstream", "operation": "work-show", "exit_code": 5}))
    report_worker_blocker(store, "issue", str(report))
    runtime = Mock()
    node = Node(id="node", worker="worker", work_item_id="issue", status="in_progress")
    manifest = Manifest(meta={}, nodes={"node": node})
    for _ in range(2):
        collect_results(store, runtime, manifest, str(tmp_path / "manifest.yaml"))
    assert node.status == "blocked"
    assert work.bounces.worker == 0
    assert not runtime.mock_calls


def test_amendment_review_preflight_rejects_missing_identity():
    from omac.core.amendment import _validate_stage_preconditions
    from omac.core.manifest import Manifest, Node
    from omac.errors import ValidationError
    store = Mock()
    store.get_work_item.return_value = item(status=WorkItemStatus.BLOCKED)
    manifest = Manifest(meta={}, nodes={"node": Node(id="node", worker="alice", work_item_id="issue")})
    with pytest.raises(ValidationError, match="sealed delivery identity"):
        _validate_stage_preconditions(manifest, {"review": ["node"]}, store)
    assert [call[0] for call in store.mock_calls] == ["get_work_item"]


def test_run_read_retry_exhaustion_is_bounded(monkeypatch):
    from omac.engines.multica import _MULTICA_READ_MAX_ATTEMPTS
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"), sleeper=lambda _: None)
    call = Mock(side_effect=PlatformError("TLS handshake timed out"))
    monkeypatch.setattr(store, "_run_multica", call)
    with pytest.raises(PlatformError) as exc:
        MulticaRuntime(store).list_runs("issue")
    assert exc.value.exit_code == 2
    assert call.call_count == _MULTICA_READ_MAX_ATTEMPTS


@pytest.mark.parametrize("change", [{"exit_code": 2}, {"reason_code": "guess from prose"}, {"extra": "unknown"}])
def test_worker_blocker_rejects_untyped_input_without_writes(tmp_path, change):
    from omac.pipeline.dispatch import report_worker_blocker
    from omac.errors import ValidationError
    report = {"schema": "omac.worker-blocker/v1", "reason_code": "upstream-unreadable", "upstream_issue_id": "upstream", "operation": "work-show", "exit_code": 5, **change}
    path = tmp_path / "blocker.yaml"
    path.write_text(yaml.safe_dump(report))
    store = Mock()
    with pytest.raises(ValidationError):
        report_worker_blocker(store, "issue", str(path))
    assert not store.mock_calls


@pytest.mark.parametrize("timestamp", [None, "not-a-time", "2026-09-26T12:00:00"])
def test_review_recovery_rejects_unusable_observation_cutoff(timestamp):
    from omac.engines import create_engine
    from conftest import seal_mock_delivery
    engine = create_engine("mock", EngineConfig(engine_type="mock", workspace_id="ws"))
    work = engine.store.create_work_item("ws", "task", "description", "node", "alice")
    work = seal_mock_delivery(engine.store, work.id, "https://example.test/pr/1", {"commands": []})
    work.delivery_identity = replace(work.delivery_identity, verification_created_at=timestamp)
    with pytest.raises(ValueError, match="verification time"):
        validate_stage_recovery(work, "review")


def test_persisted_worker_decision_prevents_dispatch_even_with_stale_manifest():
    from omac.core.manifest import Manifest, Node
    from omac.engines.models import WorkItemControlProjection
    from omac.pipeline.loop import _dispatch_worker_handoff
    work = item(status=WorkItemStatus.IN_PROGRESS, phase=TaskPhase.AUTHORING,
                decision_required={"reason_code": "worker-precondition-blocked"})
    store, runtime = Mock(), Mock()
    from contextlib import nullcontext
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.observe_work_item_control.return_value = WorkItemControlProjection(work)
    manifest = Manifest(meta={}, nodes={"node": Node(id="node", worker="alice", work_item_id="issue")})
    result = _dispatch_worker_handoff(store, runtime, manifest, "node", review_bounce=0, gate="explicit-dispatch")
    assert result.state == "needs-decision"
    assert not runtime.mock_calls
    store.update_work_item_metadata.assert_not_called()

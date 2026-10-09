from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omac.core.taskmeta import TaskKind, TaskPhase, review_context_binding
from omac.engines.models import WorkItem, WorkItemStatus, AgentRunObservation
from omac.errors import ValidationError


def test_scope_gap_is_terminal_without_fake_proposal_or_reviewer(tmp_path):
    from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template
    item = WorkItem(id="assessment", workspace_id="ws", title="assessment", description="",
                    dag_key="amend-owner-" + "a" * 64, kind=TaskKind.AMENDMENT,
                    phase=TaskPhase.AUTHORING, status=WorkItemStatus.IN_PROGRESS,
                    worker="planner", contract={"objective": "assessment only"})
    store = Mock()
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    store.resolve_agent_id.return_value = "planner-id"
    def update(_, **values):
        for key, value in values.items():
            setattr(item, key, value)
    store.update_work_item_metadata.side_effect = update
    store.update_status.side_effect = lambda _, status: setattr(item, "status", status)
    runtime = Mock()
    runtime.list_runs.return_value = [AgentRunObservation("run", "direct", "running", agent_id="planner-id", trigger_kind="issue_assignment")]
    report = scope_gap_template(item)
    report.update(run_id="run", reason_code="source-gap", summary="Current source unavailable",
                  decision_needed="Publish the exact current owner Source")
    result = report_scope_gap(store, runtime, item.id, report)
    assert result["exit_code"] == 20 and result["terminal"]
    assert item.deliverable is None and item.review_verdict is None
    assert item.status == WorkItemStatus.BLOCKED
    calls = store.update_work_item_metadata.call_count
    status_calls = store.update_status.call_count
    runtime.list_runs.return_value[0] = AgentRunObservation("run", "direct", "completed", agent_id="planner-id", trigger_kind="issue_assignment")
    assert report_scope_gap(store, runtime, item.id, report) == result
    assert store.update_work_item_metadata.call_count == calls
    assert store.update_status.call_count == status_calls
    store.assign_reviewer.assert_not_called()
    runtime.wake.assert_not_called()



@pytest.mark.parametrize("lost", ["metadata", "status"])
def test_scope_gap_lost_ack_is_observed_without_replay(tmp_path, lost):
    from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template
    from omac.errors import PlatformError
    item = WorkItem(id="assessment", workspace_id="ws", title="assessment", description="", dag_key="amend-owner-"+"a"*64,
                    kind=TaskKind.AMENDMENT, phase=TaskPhase.AUTHORING, status=WorkItemStatus.IN_PROGRESS,
                    worker="planner", contract={"objective": "Source scope"})
    store, runtime = Mock(), Mock()
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    store.resolve_agent_id.return_value = "planner-id"
    def metadata(_, **values):
        for key, value in values.items():
            setattr(item, key, value)
        if lost == "metadata":
            raise PlatformError("committed metadata, ACK lost")
    def status(_, value):
        item.status = value
        if lost == "status":
            raise PlatformError("committed status, ACK lost")
    store.update_work_item_metadata.side_effect = metadata
    store.update_status.side_effect = status
    runtime.list_runs.return_value = [AgentRunObservation("run", "direct", "running", agent_id="planner-id", trigger_kind="issue_assignment")]
    report = scope_gap_template(item)
    report.update(run_id="run", reason_code="source-gap", summary="Source unavailable", decision_needed="Publish complete Source")
    assert report_scope_gap(store, runtime, item.id, report)["exit_code"] == 20
    assert report_scope_gap(store, runtime, item.id, report)["exit_code"] == 20
    assert store.update_work_item_metadata.call_count == 1
    assert store.update_status.call_count == 1



def test_scope_gap_public_command_returns_twenty_and_blocks_submit(tmp_path, monkeypatch, capsys):
    from omac.pipeline.amendment_scope_gap import scope_gap_template
    from omac.cli.main import main
    import omac.cli.commands.work as command
    import json
    item = WorkItem(id="assessment", workspace_id="ws", title="assessment", description="",
                    dag_key="amend-owner-" + "a"*64, kind=TaskKind.AMENDMENT,
                    phase=TaskPhase.AUTHORING, status=WorkItemStatus.IN_PROGRESS,
                    worker="planner", contract={"objective": "scope"})
    store = Mock()
    store.config = SimpleNamespace(engine_type="mock", workspace_id="ws")
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    def update(_, **kw):
        for key, value in kw.items():
            setattr(item, key, value)
    store.update_work_item_metadata.side_effect = update
    store.update_status.side_effect = lambda _, status: setattr(item, "status", status)
    store.resolve_agent_id.return_value = "planner-id"
    runtime = Mock()
    runtime.list_runs.return_value = [AgentRunObservation("run", "direct", "running", agent_id="planner-id", trigger_kind="issue_assignment")]
    monkeypatch.setattr(command, "_resolve_store", lambda: store)
    monkeypatch.setattr(command, "create_engine", lambda *a: SimpleNamespace(runtime=runtime))
    report = scope_gap_template(item)
    report.update(run_id="run", reason_code="source-gap", summary="Missing published Source", decision_needed="Publish exact complete current Source")
    file = tmp_path / "gap.json"
    file.write_text(json.dumps(report))
    assert main(["work", "block", item.id, "--report-file", str(file)]) == 20
    capsys.readouterr()
    assert item.decision_required["blocker"] == report
    assert item.deliverable is None and item.review_verdict is None


@pytest.mark.parametrize("field,value", [("run_id", "foreign"), ("worker", "other"), ("owner_resolution", "forged"), ("owner_source_sha256", "forged")])
def test_scope_gap_rejects_forged_binding(field, value):
    from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template
    item = WorkItem(id="assessment", workspace_id="ws", title="assessment", description="", dag_key="amend-owner-" + "a"*64,
                    kind=TaskKind.AMENDMENT, phase=TaskPhase.AUTHORING, status=WorkItemStatus.IN_PROGRESS, worker="planner", contract={"objective": "only"})
    store = Mock()
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.get_work_item.return_value = item
    store.resolve_agent_id.return_value = "planner-id"
    runtime = Mock()
    runtime.list_runs.return_value = [AgentRunObservation("run", "direct", "running", agent_id="planner-id", trigger_kind="issue_assignment")]
    report = scope_gap_template(item)
    report.update(run_id="run", reason_code="source-gap", summary="Missing source", decision_needed="Publish it")
    report[field] = value
    with pytest.raises(ValidationError):
        report_scope_gap(store, runtime, item.id, report)
    store.update_work_item_metadata.assert_not_called()
    store.update_status.assert_not_called()

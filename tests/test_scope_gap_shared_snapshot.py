from unittest.mock import Mock

import pytest

from omac.core.taskmeta import TaskKind
from omac.engines import create_engine
from omac.engines.models import AgentRunObservation, EngineConfig, WorkItemStatus
from omac.errors import ValidationError


@pytest.mark.parametrize("during_run_read", [1, 4])
def test_scope_gap_rejects_mutation_of_shared_store_object(during_run_read):
    """The Store interface may return a mutable cached WorkItem."""
    from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template

    workspace = "scope-gap-shared-snapshot"
    engine = create_engine("mock", EngineConfig("mock", workspace, extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item(
        workspace, "assessment", "Offline Source assessment", "amend-owner-" + "a" * 64,
        "alice", kind=TaskKind.AMENDMENT,
    )
    item.status = WorkItemStatus.IN_PROGRESS
    item.contract = {"objective": "approved"}
    assert engine.store.get_work_item(item.id) is item

    agent_id = engine.store.resolve_agent_id("alice")
    runtime = Mock()
    calls = 0

    def list_runs(_):
        nonlocal calls
        calls += 1
        if calls == during_run_read:
            item.contract = {"objective": "foreign during native observation"}
        return [AgentRunObservation("run", "direct", "running", agent_id=agent_id,
                                    trigger_kind="issue_assignment")]

    runtime.list_runs.side_effect = list_runs
    report = scope_gap_template(item)
    report.update(run_id="run", summary="missing Source", decision_needed="publish exact Source")
    engine.store.update_status = Mock(wraps=engine.store.update_status)
    engine.store.update_work_item_metadata = Mock(wraps=engine.store.update_work_item_metadata)

    with pytest.raises(ValidationError, match="control|binding"):
        report_scope_gap(engine.store, runtime, item.id, report)

    if during_run_read == 1:
        engine.store.update_work_item_metadata.assert_not_called()
    engine.store.update_status.assert_not_called()

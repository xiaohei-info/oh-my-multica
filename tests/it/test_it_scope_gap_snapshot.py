"""A scope-gap report is refused when the item's control changed while the Runs were being read (real Multica, one wrapped port method)."""
import pytest

from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind
from omac.core.workitem import WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template
from omac.pipeline.tasks import AuthoringTaskSpec, create_authoring_task


@pytest.mark.parametrize("during_run_read", [1, 4])
def test_a_scope_gap_report_is_refused_when_the_contract_changes_during_the_native_observation(kit, during_run_read):
    item = create_authoring_task(kit.engine, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT, title="assessment", dag_key=f"amend-assessment-{during_run_read}", assignee="alice",
        contract=Contract(objective="approved")))
    kit.stack.set_scenario("alice", [[{"hang": 120}]])
    kit.assign(item.id, "alice")
    kit.wait_for(lambda: kit.runtime.is_active(item.id), what="the Run")
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    run = kit.runtime.list_runs(item.id)[0]
    report = scope_gap_template(kit.store.get_work_item(item.id))
    report.update(run_id=run.id, summary="missing Source", decision_needed="publish exact Source")
    calls = []

    def foreign_write(*_a, **_k):
        calls.append(1)
        if len(calls) == during_run_read:
            kit.store.set_node_contract(item.id, Contract(objective="foreign during native observation"))

    kit.before(kit.runtime, "list_runs", foreign_write, only=lambda item_id: item_id == item.id)
    statuses, metadata = kit.record(kit.store, "update_status"), kit.record(kit.store, "update_work_item_metadata")

    with pytest.raises(ValidationError, match="control|binding"):
        report_scope_gap(kit.store, kit.runtime, item.id, report)

    assert not statuses and (during_run_read != 1 or not [m for m in metadata])

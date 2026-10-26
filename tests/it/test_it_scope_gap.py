"""A Planner's scope-gap report on a real Multica: terminal, written once, observed after a lost acknowledgement, refused when forged or when the
control changed while the Runs were read (one wrapped port method where a fault or a foreign write is injected)."""
import json
from types import SimpleNamespace

import pytest

import omac.cli.commands.work as work_cmd
from omac.cli.main import main

from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError, ValidationError
from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template
from omac.pipeline.tasks import AuthoringTaskSpec, create_authoring_task


@pytest.mark.parametrize("during_run_read", [1, 4])
def test_a_scope_gap_report_is_refused_when_the_contract_changes_during_the_native_observation(kit, during_run_read):
    item, report = gap(kit, f"snapshot-{during_run_read}")
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


def gap(kit, key, *, hang=120, objective="approved"):
    """An amendment assessment the Planner is working on (a Run is live, the issue is in progress) and the report template for it."""
    item = create_authoring_task(kit.engine, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT, title="assessment", dag_key=f"amend-assessment-{key}", assignee="alice", contract=Contract(objective=objective)))
    kit.stack.set_scenario("alice", [[{"hang": hang}]])
    kit.assign(item.id, "alice")
    kit.wait_for(lambda: kit.runtime.is_active(item.id), what="the Run")
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    run = kit.runtime.list_runs(item.id)[0]
    report = scope_gap_template(kit.store.get_work_item(item.id))
    report.update(run_id=run.id, reason_code="source-gap", summary="Current source unavailable", decision_needed="Publish the exact current Source")
    return item, report


def test_a_scope_gap_is_terminal_and_a_second_report_after_the_run_ended_changes_nothing(kit):
    item, report = gap(kit, "terminal", hang=3)
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_reviewer")
    wakes = kit.record(kit.runtime, "wake")

    result = report_scope_gap(kit.store, kit.runtime, item.id, report)

    current = kit.store.get_work_item(item.id)
    assert result["exit_code"] == 20 and result["terminal"]
    assert current.deliverable is None and current.review_verdict is None and current.status is WorkItemStatus.BLOCKED
    written = len(writes)
    kit.wait_runs_ended(item.id)

    assert report_scope_gap(kit.store, kit.runtime, item.id, report) == result
    assert len(writes) == written and wakes == [] and not [w for w in writes if w[0] == "assign_reviewer"]


@pytest.mark.parametrize("lost", ["update_work_item_metadata", "update_status"])
def test_a_scope_gap_whose_acknowledgement_was_lost_is_observed_and_never_replayed(kit, lost):
    item, report = gap(kit, f"lost-{lost}")
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status")
    kit.fault(kit.store, lost, when="after", error=PlatformError("committed, ACK lost"), only=lambda item_id, *a, **k: item_id == item.id)

    assert report_scope_gap(kit.store, kit.runtime, item.id, report)["exit_code"] == 20
    assert report_scope_gap(kit.store, kit.runtime, item.id, report)["exit_code"] == 20

    assert [w[0] for w in writes].count("update_work_item_metadata") == 1 and [w[0] for w in writes].count("update_status") == 1
    assert kit.store.get_work_item(item.id).decision_required["blocker"] == report


def test_the_command_line_returns_20_and_blocks_the_issue(kit, tmp_path, monkeypatch, capsys):
    item, report = gap(kit, "command")
    monkeypatch.setattr(work_cmd, "_resolve_engine", lambda: SimpleNamespace(store=kit.store, runtime=kit.runtime))
    path = tmp_path / "gap.json"
    path.write_text(json.dumps(report))
    capsys.readouterr()

    assert main(["work", "block", item.id, "--report-file", str(path)]) == 20

    current = kit.store.get_work_item(item.id)
    assert current.decision_required["blocker"] == report and current.deliverable is None and current.review_verdict is None


@pytest.mark.parametrize(("field", "value"), [("run_id", "foreign"), ("worker", "other")])
def test_a_forged_binding_is_refused_before_any_write(kit, field, value):
    item, report = gap(kit, f"forged-{field}")
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status")
    report[field] = value

    with pytest.raises(ValidationError):
        report_scope_gap(kit.store, kit.runtime, item.id, report)

    assert writes == []

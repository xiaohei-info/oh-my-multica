"""A terminal Run must not turn a stale read into a no-submit decision."""

from copy import deepcopy

import pytest

from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines.models import AgentRunObservation, WorkItemStatus
from omac.pipeline.tasks import (
    run_task,
    create_authoring_task,
    AuthoringTaskSpec,
    _guard_resume_authoring_terminal_run,
)
from test_tasks import _engine, _payload, _poll


@pytest.mark.parametrize("kind", [TaskKind.PLAN, TaskKind.AMENDMENT])
def test_submit_between_item_read_and_terminal_run_read_is_not_overwritten(
    monkeypatch, kind
):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    original_get = eng.store.get_work_item
    state = {"awake": False, "snapshotted": False, "submitted": False}

    def wake(*args):
        state["awake"] = True

    def get(item_id):
        item = original_get(item_id)
        if state["awake"] and not state["snapshotted"]:
            state["snapshotted"] = True
            return deepcopy(item)
        return item

    def runs(item_id):
        if not state["awake"]:
            return []
        if not state["submitted"]:
            assert state["snapshotted"]
            eng.store.update_work_item_metadata(
                item_id,
                deliverable="original report",
                project_rules="existing rules",
                phase=TaskPhase.REVIEW,
            )
            eng.store.update_status(item_id, WorkItemStatus.IN_REVIEW)
            state["submitted"] = True
        return [AgentRunObservation("worker-run", "direct", "completed")]

    monkeypatch.setattr(eng.runtime, "wake", wake)
    monkeypatch.setattr(eng.store, "get_work_item", get)
    monkeypatch.setattr(eng.runtime, "list_runs", runs)
    result = run_task(eng, kind, _payload(), "alice", poll=_poll)
    item = original_get(result["item_id"])
    assert item.deliverable == "original report"
    assert not item.decision_required
    assert item.status != WorkItemStatus.BLOCKED


def test_resume_guard_rechecks_after_terminal_run_observation(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(
        eng,
        AuthoringTaskSpec(
            kind=TaskKind.PLAN, title="plan", dag_key="plan-race", assignee="alice"
        ),
    )
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    stale = deepcopy(eng.store.get_work_item(item.id))

    def runs(_):
        eng.store.update_work_item_metadata(
            item.id,
            deliverable="original report",
            project_rules="rules",
            phase=TaskPhase.REVIEW,
        )
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        return [AgentRunObservation("worker-run", "direct", "completed")]

    monkeypatch.setattr(eng.runtime, "list_runs", runs)
    resumed = _guard_resume_authoring_terminal_run(eng, stale, TaskKind.PLAN)
    assert resumed.phase == TaskPhase.REVIEW
    assert not resumed.decision_required


def test_reviewer_verdict_arriving_during_terminal_observation_is_preserved(
    monkeypatch,
):
    import yaml
    from test_tasks import _review_report

    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(
        eng,
        AuthoringTaskSpec(
            kind=TaskKind.PLAN,
            title="plan",
            dag_key="review-race",
            assignee="alice",
            contract=_payload()["contract"],
        ),
    )
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="original report",
        project_rules="rules",
        phase=TaskPhase.REVIEW,
    )
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    original_get = eng.store.get_work_item
    state = {"awake": False, "snapshot": False, "submitted": False}

    def dispatch(*args):
        state["awake"] = True
        return True

    def get(item_id):
        current = original_get(item_id)
        if state["awake"] and not state["snapshot"]:
            state["snapshot"] = True
            return deepcopy(current)
        return current

    def runs(item_id):
        if not state["awake"]:
            return []
        if not state["submitted"]:
            assert state["snapshot"]
            report = _review_report("pass", original_get(item_id))
            eng.store.update_work_item_metadata(
                item_id,
                review_verdict="pass",
                review_report=report,
                review_report_source=yaml.safe_dump(report),
            )
            state["submitted"] = True
        return [AgentRunObservation("reviewer-run", "direct", "completed")]

    monkeypatch.setattr(eng.runtime, "dispatch_reviewer", dispatch)
    monkeypatch.setattr(eng.runtime, "list_runs", runs)
    monkeypatch.setattr(eng.store, "get_work_item", get)
    result = run_task(
        eng,
        TaskKind.PLAN,
        _payload(),
        "alice",
        reviewers=["bob"],
        poll=_poll,
        resume_item_id=item.id,
        confirm=True,
        pause_at_confirmation=True,
    )
    assert result["verdict"] == "pass" and result["pending_confirmation"]
    assert not original_get(item.id).decision_required


def test_resume_terminal_recheck_failure_does_not_write_decision(monkeypatch):
    from unittest.mock import Mock
    from omac.errors import PlatformError

    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(
        eng,
        AuthoringTaskSpec(
            kind=TaskKind.PLAN, title="plan", dag_key="read-failure", assignee="alice"
        ),
    )
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    stale = deepcopy(eng.store.get_work_item(item.id))
    monkeypatch.setattr(
        eng.runtime,
        "list_runs",
        lambda _: [AgentRunObservation("worker-run", "direct", "completed")],
    )
    monkeypatch.setattr(
        eng.store,
        "get_work_item",
        Mock(side_effect=PlatformError("fresh read unavailable")),
    )
    writes = Mock()
    monkeypatch.setattr(eng.store, "update_work_item_metadata", writes)
    with pytest.raises(PlatformError, match="fresh read unavailable"):
        _guard_resume_authoring_terminal_run(eng, stale, TaskKind.PLAN)
    writes.assert_not_called()


def test_terminal_without_submission_still_requires_decision(monkeypatch):
    from omac.errors import NeedsDecision

    eng = _engine(MOCK_AUTO_COMPLETE="false")
    state = {"awake": False}

    def wake(*_):
        state["awake"] = True

    monkeypatch.setattr(eng.runtime, "wake", wake)
    monkeypatch.setattr(
        eng.runtime,
        "list_runs",
        lambda _: (
            [AgentRunObservation("worker-run", "direct", "completed")]
            if state["awake"]
            else []
        ),
    )
    with pytest.raises(NeedsDecision) as exc:
        run_task(eng, TaskKind.PLAN, _payload(), "alice", poll=_poll)
    assert exc.value.report["reason_code"] == "completed-without-submit"

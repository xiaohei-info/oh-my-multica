"""A terminal historical amendment issue makes the next proposal its attempt; a live one stays an identity conflict."""
from types import SimpleNamespace

import pytest

import omac.pipeline.amendment as amendment_pipeline
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import AgentRunObservation, WorkItemStatus

DECISION = {
    "schema": amendment_pipeline.DECISION_REQUIRED_SCHEMA, "reason_code": "budget", "kind": "amendment",
    "phase": "authoring", "resume_issue_id": "old"}


def _item(**change):
    base = dict(id="old", kind=TaskKind.AMENDMENT, phase=TaskPhase.CONFIRMATION, review_verdict="pass",
                status=WorkItemStatus.DONE, decision_required=None)
    return SimpleNamespace(**{**base, **change})


def _run(status):
    return AgentRunObservation(id="r", kind="direct", status=status)


@pytest.mark.parametrize("item, runs, terminal", [
    (_item(), [], True),
    (_item(), [_run("completed")], True),
    (_item(phase=TaskPhase.AUTHORING, review_verdict=None, status=WorkItemStatus.BLOCKED,
           decision_required=DECISION), [_run("failed")], True),
    (_item(review_verdict="reject"), [], False),                                 # still in review rounds
    (_item(phase=TaskPhase.AUTHORING, review_verdict=None, status=WorkItemStatus.IN_PROGRESS), [], False),
    (_item(), [_run("running")], False),                                         # a live Run
    (_item(), [_run("")], False),                                                # outcome unknown
])
def test_superseded_amendment_problem_names_only_non_terminal_issues(item, runs, terminal):
    assert (amendment_pipeline.superseded_amendment_problem(item, runs) is None) is terminal


@pytest.mark.parametrize("existing, runs, supersedes", [
    (_item(), [], "old"),
    (_item(review_verdict="reject"), [], None),
    (None, [], None),
])
def test_a_historical_identity_is_superseded_only_when_terminal(existing, runs, supersedes):
    store = SimpleNamespace(
        config=SimpleNamespace(workspace_id="ws"), find_work_item_by_dag_key=lambda _ws, _key: existing)
    engine = SimpleNamespace(store=store, runtime=SimpleNamespace(list_runs=lambda _id: runs))

    found = amendment_pipeline._historical_identity_to_supersede(engine, "amend-x")

    assert (found.id if found else None) == supersedes

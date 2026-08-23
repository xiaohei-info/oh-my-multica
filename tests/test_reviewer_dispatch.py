from __future__ import annotations

from copy import deepcopy

import pytest

from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItemStatus


@pytest.fixture
def reviewer_dispatch_fixture():
    MockStore.reset()
    engine = create_engine(
        "mock",
        EngineConfig(
            engine_type="mock",
            workspace_id="reviewer-dispatch-tests",
            extra={"MOCK_AUTO_COMPLETE": "false"},
        ),
    )
    item = engine.store.create_work_item(
        "reviewer-dispatch-tests",
        "review subject",
        "subject",
        "reviewer-dispatch",
        "alice",
        reviewer="bob",
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    return engine, item


def _decision(item_id: str) -> dict:
    return {
        "schema": "omac.decision-required/v1",
        "reason_code": "review-convergence-scope-expanding",
        "kind": "develop",
        "phase": "review",
        "gate": "review-convergence",
        "resume_issue_id": item_id,
    }


@pytest.mark.parametrize(
    "interleaving",
    ["after-first-guard", "after-second-guard", "before-assign", "before-wake"],
)
def test_reviewer_dispatch_interleavings_fail_closed(
    reviewer_dispatch_fixture, monkeypatch, interleaving,
):
    engine, item = reviewer_dispatch_fixture
    decision = _decision(item.id)
    store = engine.store
    runtime = engine.runtime

    def persist_decision():
        store.update_work_item_metadata(
            item.id, decision_required=deepcopy(decision))

    if interleaving == "after-first-guard":
        original_observe = store.observe_work_item_control
        observed = 0

        def observe(item_id):
            nonlocal observed
            projection = original_observe(item_id)
            observed += 1
            if observed == 1:
                persist_decision()
            return projection

        monkeypatch.setattr(store, "observe_work_item_control", observe)
    elif interleaving == "after-second-guard":
        original_assign_reviewer = store.assign_reviewer

        def assign_reviewer(item_id, assignee):
            assigned = original_assign_reviewer(item_id, assignee)
            persist_decision()
            return assigned

        monkeypatch.setattr(store, "assign_reviewer", assign_reviewer)
    elif interleaving == "before-assign":
        original_assign = store.assign_work_item

        def assign(item_id, assignee, role, **kwargs):
            if role == "reviewer":
                persist_decision()
            return original_assign(item_id, assignee, role, **kwargs)

        monkeypatch.setattr(store, "assign_work_item", assign)
    else:
        original_wake = runtime.wake

        def wake(item_id, agent, role):
            persist_decision()
            return original_wake(item_id, agent, role)

        monkeypatch.setattr(runtime, "wake", wake)

    assert runtime.dispatch_reviewer(store, item.id, "bob") is False
    current = store.get_work_item(item.id)
    assert current.decision_required == decision
    assert not [
        run for run in runtime.list_runs(item.id)
        if run.agent_id == "mock-agent-bob"
    ]


def test_reviewer_dispatch_without_decision_starts_one_run(reviewer_dispatch_fixture):
    engine, item = reviewer_dispatch_fixture

    assert engine.runtime.dispatch_reviewer(engine.store, item.id, "bob") is True

    reviewer_assignments = [
        entry for entry in engine.store.assign_log if entry[2] == "reviewer"
    ]
    reviewer_runs = [
        run for run in engine.runtime.list_runs(item.id)
        if run.agent_id == "mock-agent-bob"
    ]
    assert len(reviewer_assignments) == 1
    assert len(reviewer_runs) == 1
    assert engine.store.get_work_item(item.id).decision_required is None

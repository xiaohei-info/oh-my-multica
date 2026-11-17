"""Authoring tasks (final-acceptance, decompose) whose Run ends without a submission are resumed, then escalated."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from omac.core.taskmeta import TaskKind
from omac.core.workitem import AgentRunObservation, WorkItemStatus
from omac.errors import NeedsDecision
from omac.pipeline import acceptance
from omac.pipeline.decide_effects import Wake
from omac.pipeline.tasks import AuthoringTaskSpec

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ENDED = (NOW - timedelta(minutes=5)).isoformat()


def _run(rid, status, age_seconds, agent="agent-1"):
    stamp = (NOW - timedelta(seconds=age_seconds)).isoformat()
    return AgentRunObservation(id=rid, kind="direct", status=status, agent_id=agent, created_at=stamp, updated_at=stamp)


class World:
    """Each Wake (the initial dispatch included) starts one Run with the next status of `statuses` and `age` seconds old.

    `submit_on_wake` = the Wake (1-based) whose Run submits the work item.
    """

    def __init__(self, statuses, age=300, submit_on_wake=None):
        self.statuses, self.age, self.submit_on_wake = list(statuses), age, submit_on_wake
        self.runs, self.wakes, self.status = [], 0, WorkItemStatus.IN_PROGRESS
        self.engine = SimpleNamespace(
            store=SimpleNamespace(
                resolve_agent_id=lambda name: "agent-1",
                get_work_item=lambda item_id: SimpleNamespace(id=item_id, status=self.status)),
            runtime=SimpleNamespace(list_runs=lambda item_id: list(self.runs)))

    def apply(self, effects, ports):
        for effect in effects:
            if isinstance(effect, Wake):
                self.wakes += 1
                self.runs.append(_run(f"r{self.wakes}", self.statuses[self.wakes - 1], self.age + 10 - self.wakes))
                if self.wakes == self.submit_on_wake:
                    self.status = WorkItemStatus.IN_REVIEW


@pytest.fixture
def world(monkeypatch):
    def build(*args, **kwargs):
        w = World(*args, **kwargs)
        monkeypatch.setattr(acceptance, "apply", w.apply)
        monkeypatch.setattr(acceptance, "create_authoring_task", lambda engine, spec: SimpleNamespace(id="GT-1"))
        return w
    return build


def _spec(kind):
    return AuthoringTaskSpec(kind=kind, title="t", dag_key="k", assignee="acceptor", description="d", contract={})


def _wait(w, kind, budget=2, ticks=20, polls=None):
    return acceptance._dispatch_and_wait(
        w.engine, _spec(kind), poll=lambda: polls.append(1) if polls is not None else None, max_ticks=ticks,
        no_submit_runs=budget, now=lambda: NOW)


KINDS = [TaskKind.FINAL_ACCEPTANCE, TaskKind.DECOMPOSE]


@pytest.mark.parametrize("kind", KINDS)
def test_ended_run_without_submit_is_resumed_once_then_finishes(world, kind):
    w = world(["completed", "completed"], submit_on_wake=2)
    assert _wait(w, kind) == "GT-1"
    assert w.wakes == 2          # the initial dispatch + one resume
    assert w.status is WorkItemStatus.IN_REVIEW


@pytest.mark.parametrize("kind", KINDS)
def test_exhausted_budget_needs_decision_naming_item_and_run(world, kind):
    w = world(["completed", "failed", "completed"])
    with pytest.raises(NeedsDecision) as caught:
        _wait(w, kind, budget=2)
    assert "GT-1" in str(caught.value) and caught.value.report["run_id"] == "r2"
    assert w.wakes == 2          # one resume was spent; the second ended Run exhausted the budget, no third wake


def test_running_run_is_never_resumed(world):
    w = world(["running"])
    polls = []
    _wait(w, TaskKind.FINAL_ACCEPTANCE, ticks=5, polls=polls)
    assert w.wakes == 1 and len(polls) == 5


def test_run_inside_grace_period_is_waited_for(world):
    w = world(["completed"], age=3)
    _wait(w, TaskKind.FINAL_ACCEPTANCE, ticks=3)
    assert w.wakes == 1

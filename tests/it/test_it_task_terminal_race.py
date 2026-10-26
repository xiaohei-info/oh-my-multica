"""A terminal Run must not turn a stale read into a no-submit decision (real Multica, scripted agents that end at once).

The race: the controller read the item before a delivery or verdict landed, then observes the Run as terminal. The delivery
that landed in between is written when the Runs are listed (`submit_when_runs_are_terminal`).
"""
import pytest
import yaml

import omac.pipeline.tasks as tasks_module
from _support.tasksworld import World, payload, review_report
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, PlatformError


@pytest.fixture()
def world(kit, tmp_path):
    return World(kit, tmp_path)


def submit_when_runs_are_terminal(kit, write, agent="alice"):
    """The first time a Run of ``agent`` is seen terminal (and none is active), ``write(item_id)`` lands first: a submit that raced the observation."""
    original, state, agent_id = kit.runtime.list_runs, {"done": False}, kit.store.resolve_agent_id(agent)

    def listed(item_id):
        runs = original(item_id)
        if not state["done"] and any(run.agent_id == agent_id for run in runs) and all(run.terminal for run in runs):
            state["done"] = True
            write(item_id)
        return runs

    kit.monkeypatch.setattr(kit.runtime, "list_runs", listed)
    return state


def deliver(kit):
    def write(item_id):
        kit.store.update_work_item_metadata(item_id, deliverable="original report", project_rules="existing rules", phase=TaskPhase.REVIEW)
        kit.store.update_status(item_id, WorkItemStatus.IN_REVIEW)

    return write


def ended_author(world, key):
    """Plan issue `key` whose author's Run has ended without submitting; the controller still holds the read from before."""
    kit = world.kit
    item = world.issue(TaskKind.PLAN, key)
    kit.stack.set_scenario("alice", [[]])
    kit.assign(item.id, "alice")
    kit.wait_runs_ended(item.id)
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    return item, kit.store.get_work_item(item.id)


@pytest.mark.parametrize("kind", [TaskKind.PLAN, TaskKind.AMENDMENT])
def test_a_submit_between_the_item_read_and_the_terminal_run_read_is_not_overwritten(world, kind):
    kit = world.kit
    kit.stack.set_scenario("alice", [[]])
    seen = submit_when_runs_are_terminal(kit, deliver(kit))

    result = world.run(kind, payload(), "alice")

    item = kit.store.get_work_item(result["item_id"])
    assert seen["done"] and item.deliverable == "original report"
    assert not item.decision_required and item.status is not WorkItemStatus.BLOCKED


def test_the_resume_guard_rereads_the_item_after_the_terminal_run_was_observed(world):
    kit = world.kit
    item, stale = ended_author(world, "plan-race")
    submit_when_runs_are_terminal(kit, deliver(kit))

    resumed = tasks_module._guard_resume_authoring_terminal_run(kit.engine, stale, TaskKind.PLAN)

    assert resumed.phase is TaskPhase.REVIEW and not resumed.decision_required


def test_a_verdict_that_arrives_while_the_terminal_reviewer_run_is_observed_is_preserved(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "review-race", contract=payload()["contract"])
    world.stage(item, phase=TaskPhase.REVIEW, deliverable="original report", project_rules="rules", status=WorkItemStatus.IN_REVIEW)
    kit.stack.set_scenario("alice", [[]])
    kit.assign(item.id, "alice")                  # a Reviewer is assigned after the author was: that assignment starts its Run
    kit.wait_runs_ended(item.id)
    kit.stack.set_scenario("bob", [[]])

    def verdict(item_id):
        report = review_report("pass", kit.store.get_work_item(item_id))
        kit.store.update_work_item_metadata(item_id, review_verdict="pass", review_report=report, review_report_source=yaml.safe_dump(report))

    submit_when_runs_are_terminal(kit, verdict, agent="bob")

    result = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"], resume_item_id=item.id, confirm=True, pause_at_confirmation=True)

    assert result["verdict"] == "pass" and result["pending_confirmation"]
    assert not kit.store.get_work_item(item.id).decision_required


def test_a_failed_fresh_read_after_a_terminal_run_writes_no_decision(world):
    kit = world.kit
    item, stale = ended_author(world, "read-failure")
    kit.fault(kit.store, "get_work_item", when="before", error=PlatformError("fresh read unavailable"))
    writes = kit.record(kit.store, "update_work_item_metadata")

    with pytest.raises(PlatformError, match="fresh read unavailable"):
        tasks_module._guard_resume_authoring_terminal_run(kit.engine, stale, TaskKind.PLAN)

    assert writes == []


def test_a_terminal_author_run_without_a_submission_still_asks_for_a_decision(world):
    world.kit.stack.set_scenario("alice", [[]])

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.PLAN, payload(), "alice")

    assert raised.value.report["reason_code"] == "completed-without-submit"

"""Handing a delivery to the Reviewer on a real Multica: a decision that lands between the guards and the writes stops it."""
from copy import deepcopy

import pytest

from omac.core.workitem import WorkItemStatus


def decision(item_id):
    return {"schema": "omac.decision-required/v1", "reason_code": "review-convergence-scope-expanding", "kind": "develop",
            "phase": "review", "gate": "review-convergence", "resume_issue_id": item_id}


def reviewer_runs(kit, item_id):
    bob = kit.store.resolve_agent_id("bob")
    return [run for run in kit.runtime.list_runs(item_id) if run.agent_id == bob]


@pytest.fixture()
def subject(kit):
    """A delivered issue: the Worker's Run has ended, as it has before any Reviewer is assigned."""
    kit.agents("idle", names=("alice",))
    item = kit.item("reviewer-dispatch", worker="alice", reviewer="bob", assign=True)
    kit.wait_runs_ended(item.id)
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    kit.agents("hang", names=("bob",))
    return kit.store.get_work_item(item.id)


@pytest.mark.parametrize("interleaving", ["after-first-guard", "after-second-guard", "before-assign", "before-wake"])
def test_a_decision_landing_between_the_guards_and_the_writes_stops_the_reviewer_dispatch(kit, subject, interleaving):
    store, runtime, item_id = kit.store, kit.runtime, subject.id
    persist = lambda *_a, **_k: store.update_work_item_metadata(item_id, decision_required=deepcopy(decision(item_id)))
    if interleaving == "after-first-guard":
        kit.after(store, "observe_work_item_control", lambda *_a: persist(), only=lambda target: target == item_id)
    elif interleaving == "after-second-guard":
        kit.after(store, "assign_reviewer", lambda *_a, **_k: persist())
    elif interleaving == "before-assign":
        kit.before(store, "assign_work_item", persist, only=lambda target, assignee, role, **kw: role == "reviewer")
    else:
        kit.before(runtime, "wake", persist)

    assert runtime.dispatch_reviewer(store, item_id, "bob") is False

    assert store.get_work_item(item_id).decision_required == decision(item_id)
    # A decision that lands after the last guard and before the wake cannot stop a wake that is already in flight: the platform has no
    # compare-and-set. The dispatch still reports the stop (False) so the caller never counts the Run as handed over.
    assert interleaving == "before-wake" or not reviewer_runs(kit, item_id)


def test_without_a_decision_the_dispatch_assigns_the_reviewer_once_and_starts_one_run(kit, subject):
    assigns = kit.record(kit.store, "assign_work_item")

    assert kit.runtime.dispatch_reviewer(kit.store, subject.id, "bob") is True

    kit.wait_for(lambda: reviewer_runs(kit, subject.id), what="the Reviewer Run")
    assert len([a for a in assigns if a[1][2] == "reviewer"]) == 1 and len(reviewer_runs(kit, subject.id)) == 1
    assert kit.store.get_work_item(subject.id).decision_required is None

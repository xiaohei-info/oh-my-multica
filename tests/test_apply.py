"""apply(): effects run strictly in order, results come back in order, and a non-replayable request needs a
durable intent earlier in the same plan."""
import pytest

from omac.core.workitem import WorkItemStatus
from omac.pipeline.apply import ManifestState, Ports, apply
from omac.pipeline.decide_effects import (
    AcknowledgeAssignment, AddComment, Assign, CancelRun, ClearAssignment, CreateWorkItem, DispatchReviewer, NormalizeConfirmedMerge,
    Plan, PlanError, PrepareReviewCycle, RequestMerge, ResetReview, RestoreAuthoringGeneration, SaveState, SetAuthoringIdentity, SetNode,
    SetNodeContract, UpdateMetadata, UpdateStatus, Wake, validate_plan,
)


class Recorder:
    """Records port calls in order; every method returns its own name so results can be checked."""

    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.log.append((name, args, kwargs))
            return name
        return call


class Node:
    status = "todo"
    merge_request_state = None


class Manifest:
    def __init__(self):
        self.nodes = {"a": Node()}


def _ports(log):
    return Ports(store=Recorder(log), runtime=Recorder(log), code_host=Recorder(log))


EFFECT_CALLS = [
    (UpdateStatus("i", WorkItemStatus.BLOCKED), ("update_status", ("i", WorkItemStatus.BLOCKED), {})),
    (UpdateMetadata("i", {"ci_bounce": 2}), ("update_work_item_metadata", ("i",), {"ci_bounce": 2})),
    (AddComment("i", "t"), ("add_comment", ("i", "t"), {})),
    (Assign("i", "w", "worker"), ("assign_work_item", ("i", "w", "worker"), {})),
    (Assign("i", "w", "worker", start_run=False), ("assign_work_item", ("i", "w", "worker"), {"start_run": False})),
    (ClearAssignment("i"), ("clear_assignment", ("i",), {})),
    (ResetReview("i"), ("reset_review", ("i",), {})),
    (PrepareReviewCycle("i", "d"), ("prepare_review_cycle", ("i", "d"), {})),
    (CreateWorkItem({"title": "t"}), ("create_work_item", (), {"title": "t"})),
    (SetNodeContract("i", "c"), ("set_node_contract", ("i", "c"), {})),
    (SetAuthoringIdentity("i", "k", "develop"), ("set_authoring_identity", ("i",), {"dag_key": "k", "kind": "develop"})),
    (NormalizeConfirmedMerge("i"), ("normalize_confirmed_merge", ("i",), {})),
    (RestoreAuthoringGeneration("i", "c", "g"), ("restore_authoring_generation", ("i", "c", "g", None), {})),
    (RestoreAuthoringGeneration("i", "c", "g", {"review": 1}, worker_handoff="h"),
     ("restore_authoring_generation", ("i", "c", "g", {"review": 1}), {"worker_handoff": "h"})),
    (Wake("i", "w", "worker"), ("wake", ("i", "w", "worker"), {})),
    (CancelRun("i"), ("cancel", ("i",), {})),
    (AcknowledgeAssignment("i"), ("acknowledge_assignment", ("i",), {})),
    (DispatchReviewer("i", "r"), ("dispatch_reviewer", ("STORE", "i", "r"), {})),
]


@pytest.mark.parametrize("effect,call", EFFECT_CALLS, ids=lambda x: type(x).__name__ if not isinstance(x, tuple) else "")
def test_each_effect_is_exactly_one_port_call(effect, call):
    log = []
    ports = Ports(store=Recorder(log), runtime=Recorder(log))
    results = apply([effect], ports)
    (name, args, kwargs), = log
    args = tuple("STORE" if isinstance(a, Recorder) else a for a in args)
    assert (name, args, kwargs) == call
    assert results == [name]


def test_effects_run_in_plan_order_and_results_follow_order():
    log = []
    plan = Plan((UpdateStatus("i", WorkItemStatus.BLOCKED), AddComment("i", "x"), Wake("i", "w", "worker")))
    results = apply(plan, _ports(log))
    assert [c[0] for c in log] == ["update_status", "add_comment", "wake"]
    assert results == ["update_status", "add_comment", "wake"]


def test_a_failing_effect_stops_the_rest_and_is_not_swallowed():
    log = []

    class Boom(Recorder):
        def add_comment(self, *a):
            raise RuntimeError("platform")

    ports = Ports(store=Boom(log), runtime=Recorder(log))
    with pytest.raises(RuntimeError):
        apply([UpdateStatus("i", WorkItemStatus.BLOCKED), AddComment("i", "x"), Wake("i", "w", "worker")], ports)
    assert [c[0] for c in log] == ["update_status"]


def test_set_node_and_save_state_go_through_the_state_sink(monkeypatch):
    saved = []
    monkeypatch.setattr("omac.pipeline.apply.save_manifest", lambda m, p: saved.append((m, p)))
    manifest = Manifest()
    apply([SetNode("a", {"status": "blocked"}), SaveState()], Ports(), ManifestState(manifest, "p.yaml"))
    assert manifest.nodes["a"].status == "blocked" and saved == [(manifest, "p.yaml")]


INTENT = SetNode("a", {"merge_request_state": "intent"})
REQUEST = RequestMerge("u", "cmd", 60)


@pytest.mark.parametrize("effects,ok", [
    ([REQUEST], False),
    ([INTENT, REQUEST], False),                  # intent set in memory but not yet durable
    ([SaveState(), INTENT, REQUEST], False),     # a save before the intent does not count
    ([INTENT, SaveState(), REQUEST], True),
    ([INTENT, SaveState(), UpdateStatus("i", WorkItemStatus.IN_REVIEW), REQUEST], True),
    ([UpdateStatus("i", WorkItemStatus.IN_REVIEW)], True),
])
def test_request_merge_needs_a_durable_intent_first(effects, ok):
    if ok:
        validate_plan(effects)
    else:
        with pytest.raises(PlanError):
            validate_plan(effects)


def test_an_invalid_plan_runs_nothing():
    log = []
    with pytest.raises(PlanError):
        apply([UpdateStatus("i", WorkItemStatus.BLOCKED), REQUEST], _ports(log))
    assert log == []

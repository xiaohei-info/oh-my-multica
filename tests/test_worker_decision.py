"""A Worker decision request is a causal stop, never a delivery: which reports and Runs are accepted (pure facts -> verdict).

The persistence side (idempotent stop, interrupted writes, stale projections, collection) runs on Docker Multica:
tests/it/test_it_worker_decision.py.
"""
from dataclasses import replace

import pytest

from omac.core.review_convergence import bounded_decision_required
from omac.core.taskmeta import TaskKind, TaskPhase, WorkerHandoffIntent, review_context_binding
from omac.core.workitem import AgentRunObservation, WorkItem, WorkItemStatus
from omac.engines.metadata_policy import assert_metadata_write_allowed
from omac.errors import ValidationError
from omac.pipeline import worker_decision
from omac.pipeline.dispatch import build_show_output


def make_item():
    item = WorkItem(id="issue", workspace_id="ws", title="task", description="", dag_key="node", kind=TaskKind.DEVELOP,
                    phase=TaskPhase.AUTHORING, status=WorkItemStatus.IN_PROGRESS, worker="worker", contract={"objective": "scope"})
    item.worker_handoff = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", gate="explicit-dispatch", target_worker="worker",
        source_review_subject_digest="subject", source_review_round=1, target_review_bounce=0, generation="generation",
        target_agent_id="agent", target_run_id="run", review_context_binding=review_context_binding(item))
    return item


def make_report(item):
    return {
        "schema": "omac.worker-blocker/v2", "reason_code": "quality-gate-failed", "issue_id": item.id,
        "review_context_binding": review_context_binding(item), "handoff_generation": "generation", "worker": "worker", "run_id": "run",
        "summary": "Required gate failed; contract requires an owner decision", "decision_needed": "Choose an authorized next step",
        "contract_ref": "objective",
        "evidence": [{"ref": "artifacts/failed.log", "command": "test coverage", "exit_code": 1,
                      "observation": "82% below required 85%; retained uncommitted work"}],
    }


def running(**over):
    return AgentRunObservation(**{"id": "run", "kind": "direct", "status": "running", "agent_id": "agent",
                                  "trigger_kind": "issue_assignment", **over})


@pytest.fixture
def case():
    item = make_item()
    return item, make_report(item)


def stale(item, report):
    return worker_decision.binding_is_stale(report, item, review_context_binding(item))


def test_a_complete_report_for_the_bound_running_run_is_accepted(case):
    item, report = case

    worker_decision._validate(report, item.id)
    assert not stale(item, report) and worker_decision.known_contract_ref(report, item.contract)
    assert worker_decision.target_run_problem([running()], item.worker_handoff, "run") is None


@pytest.mark.parametrize("field,value", [("reason_code", "reject"), ("extra", "unexpected"), ("evidence", []), ("reason_code", [])])
def test_a_malformed_report_is_refused_before_anything_is_read(case, field, value):
    item, report = case
    report[field] = value

    with pytest.raises(ValidationError):
        worker_decision._validate(report, item.id)


@pytest.mark.parametrize("field,value", [("issue_id", "other"), ("handoff_generation", "old"), ("worker", "other"), ("review_context_binding", {})])
def test_a_report_bound_to_another_issue_generation_worker_or_context_is_stale(case, field, value):
    item, report = case
    report[field] = value

    assert stale(item, report)


@pytest.mark.parametrize("change", [{"phase": TaskPhase.REVIEW}, {"phase": TaskPhase.CONFIRMATION}, {"status": WorkItemStatus.DONE},
                                    {"worker": "somebody-else"}])
def test_an_item_outside_active_worker_authoring_is_stale(case, change):
    item, report = case
    for name, value in change.items():
        setattr(item, name, value)

    assert stale(item, report)


def test_a_handoff_without_a_causal_run_is_stale(case):
    item, report = case
    item.worker_handoff = replace(item.worker_handoff, target_agent_id=None, target_run_id=None)

    assert stale(item, report)


@pytest.mark.parametrize("run_id,runs,problem", [
    ("old", [running()], "not-current"),                                   # the report names a Run that is not the current one
    ("run", [running(kind="child")], "not-current"),
    ("run", [running(status="completed")], "not-current"),
    ("run", [running(trigger_kind="comment")], "not-current"),
    ("run", [running(agent_id="other")], "ambiguous"),                     # another agent's Run is the active one
    ("run", [running(), running(id="second")], "not-current"),             # two Runs of the Worker: which one is meant?
    ("run", [running(), running(id="second", agent_id="foreign")], "ambiguous"),
])
def test_only_the_single_running_direct_run_of_the_bound_worker_is_accepted(case, run_id, runs, problem):
    item, _ = case

    assert worker_decision.target_run_problem(runs, item.worker_handoff, run_id) == problem


def test_a_run_from_before_the_handoff_baseline_is_not_the_target(case):
    item, _ = case
    item.worker_handoff = replace(item.worker_handoff, baseline_direct_run_ids=("run",))

    assert worker_decision.target_run_problem([running()], item.worker_handoff, "run") == "not-current"


def test_a_target_not_yet_persisted_resolves_to_the_unique_causal_run(case):
    item, _ = case
    intent = replace(item.worker_handoff, target_run_id=None)

    assert worker_decision.target_run_problem([running()], intent, "run") is None


def test_a_contract_ref_must_name_a_field_of_the_contract(case):
    item, report = case

    assert worker_decision.known_contract_ref({**report, "contract_ref": "nonexistent"}, item.contract) is False


def test_an_owner_decision_may_rest_on_partial_evidence_without_a_success_verification(case):
    item, report = case
    report["reason_code"] = "owner-decision-required"
    report["evidence"] = [{"ref": "local/notes.md", "observation": "Owner scope decision needed; no validation executed"}]

    worker_decision._validate(report, item.id)


def test_a_quality_gate_stop_needs_a_failing_command_among_its_evidence(case):
    item, report = case
    report["evidence"] = [{"ref": "artifacts/ok.log", "command": "test coverage", "exit_code": 0, "observation": "passed"}]

    with pytest.raises(ValidationError):
        worker_decision._validate(report, item.id)


def test_an_oversized_report_is_refused_before_the_store_is_touched(case):
    item, report = case
    report["summary"] = "x" * 2048

    with pytest.raises(ValidationError):
        worker_decision.report_decision(None, None, item.id, report)       # no store and no runtime: nothing could have been read or written


def test_the_persisted_decision_is_the_exact_bounded_report(case):
    item, report = case
    decision = worker_decision.decision_record(item, review_context_binding(item), report)

    assert decision["blocker"] == report and decision["resume_issue_id"] == item.id and decision["gate"] == "worker"
    assert bounded_decision_required(decision) == decision
    assert_metadata_write_allowed("decision_required", decision)
    assert report == make_report(item)       # building the record does not alias or alter the report


def test_work_show_exposes_the_exact_binding_without_claiming_to_authenticate_the_run(case):
    item, _ = case
    template = build_show_output(item, "worker:worker")["control"]["blocker_report_template"]

    assert template["issue_id"] == item.id and template["handoff_generation"] == "generation" and template["run_id"] == "run"
    assert template["review_context_binding"] == review_context_binding(item)

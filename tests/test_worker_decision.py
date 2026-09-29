"""Worker decision requests are causal stops, never successful deliveries."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock
from contextlib import nullcontext

import pytest
import yaml

from omac.core.taskmeta import (
    TaskKind,
    TaskPhase,
    WorkerHandoffIntent,
    review_context_binding,
)
from omac.engines.models import WorkItem, WorkItemStatus, AgentRunObservation
from omac.errors import ValidationError, PlatformError
from omac.pipeline.dispatch import report_worker_blocker, build_show_output


@pytest.fixture
def setup_request(tmp_path):
    item = WorkItem(
        id="issue",
        workspace_id="ws",
        title="task",
        description="",
        dag_key="node",
        kind=TaskKind.DEVELOP,
        phase=TaskPhase.AUTHORING,
        status=WorkItemStatus.IN_PROGRESS,
        worker="worker",
        contract={"objective": "scope"},
    )
    item.worker_handoff = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        gate="explicit-dispatch",
        target_worker="worker",
        source_review_subject_digest="subject",
        source_review_round=1,
        target_review_bounce=0,
        generation="generation",
        target_agent_id="agent",
        target_run_id="run",
        review_context_binding=review_context_binding(item),
    )
    store = Mock()
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    store.hydrate_work_item_evidence.side_effect = lambda projection, plan: deepcopy(
        projection.work_item
    )
    from omac.engines.models import WorkItemControlProjection

    store.observe_work_item_control.side_effect = lambda _: WorkItemControlProjection(
        deepcopy(item)
    )

    def update(_, **kw):
        for k, v in kw.items():
            setattr(item, k, v)
        return deepcopy(item)

    store.update_work_item_metadata.side_effect = update
    store.update_status.side_effect = lambda _, status: setattr(item, "status", status)
    runtime = Mock()
    runtime.list_runs.return_value = [
        AgentRunObservation(
            "run",
            "direct",
            "running",
            agent_id="agent",
            trigger_kind="issue_assignment",
        )
    ]
    report = {
        "schema": "omac.worker-blocker/v2",
        "reason_code": "quality-gate-failed",
        "issue_id": "issue",
        "review_context_binding": review_context_binding(item),
        "handoff_generation": "generation",
        "worker": "worker",
        "run_id": "run",
        "summary": "Required gate failed; contract requires an owner decision",
        "decision_needed": "Choose an authorized next step",
        "contract_ref": "objective",
        "evidence": [
            {
                "ref": "artifacts/failed.log",
                "command": "test coverage",
                "exit_code": 1,
                "observation": "82% below required 85%; retained uncommitted work",
            }
        ],
    }
    path = tmp_path / "block.yaml"
    return item, store, runtime, report, path


def send(case):
    item, store, runtime, report, path = case
    path.write_text(yaml.safe_dump(report))
    return report_worker_blocker(store, item.id, str(path), runtime=runtime)


def test_valid_stop_is_idempotent_and_preserves_audit(setup_request):
    item, store, runtime, report, path = setup_request
    before = deepcopy(item)
    result = send(setup_request)
    assert result["exit_code"] == 20 and result["terminal"] and result["ok"] is False
    assert item.status == WorkItemStatus.BLOCKED
    assert (
        item.bounces == before.bounces and item.worker_handoff == before.worker_handoff
    )
    assert item.decision_required["blocker"] == report
    runtime.list_runs.return_value = [
        replace(runtime.list_runs.return_value[0], status="completed")
    ]
    assert send(setup_request) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("issue_id", "other"),
        ("handoff_generation", "old"),
        ("worker", "other"),
        ("run_id", "old"),
        ("review_context_binding", {}),
        ("reason_code", "reject"),
        ("extra", "unexpected"),
    ],
)
def test_wrong_report_never_writes(setup_request, field, value):
    setup_request[3][field] = value
    with pytest.raises(ValidationError):
        send(setup_request)
    setup_request[1].update_work_item_metadata.assert_not_called()
    setup_request[1].update_status.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "child"},
        {"agent_id": "other"},
        {"status": "completed"},
        {"trigger_kind": "comment"},
    ],
)
def test_wrong_run_never_writes(setup_request, change):
    runtime = setup_request[2]
    runtime.list_runs.return_value = [
        replace(runtime.list_runs.return_value[0], **change)
    ]
    with pytest.raises(ValidationError):
        send(setup_request)
    setup_request[1].update_work_item_metadata.assert_not_called()


@pytest.mark.parametrize("phase", [TaskPhase.REVIEW, TaskPhase.CONFIRMATION])
def test_non_authoring_never_writes(setup_request, phase):
    setup_request[0].phase = phase
    with pytest.raises(ValidationError):
        send(setup_request)
    setup_request[1].update_work_item_metadata.assert_not_called()


def test_target_not_yet_persisted_uses_unique_causal_run(setup_request):
    item = setup_request[0]
    item.worker_handoff = replace(item.worker_handoff, target_run_id=None)
    assert send(setup_request)["exit_code"] == 20


def test_interrupted_status_write_leaves_stop_and_retry_repairs(setup_request):
    item, store, runtime, _, _ = setup_request
    store.update_status.side_effect = PlatformError("offline")
    with pytest.raises(PlatformError):
        send(setup_request)
    assert item.decision_required
    store.update_status.side_effect = lambda _, status: setattr(item, "status", status)
    assert send(setup_request)["exit_code"] == 20


def test_show_exposes_exact_binding_without_claiming_run_authentication(setup_request):
    item = setup_request[0]
    template = build_show_output(item, "worker:worker")["control"][
        "blocker_report_template"
    ]
    assert template["issue_id"] == item.id
    assert template["handoff_generation"] == "generation"
    assert template["run_id"] == "run"
    assert template["review_context_binding"] == review_context_binding(item)


def test_retry_rereads_decision_before_replacing_generation(setup_request):
    from omac.pipeline.loop import _retry_worker_handoff
    from omac.core.manifest import Manifest, Node
    from omac.engines.models import WorkItemControlProjection

    item, store, runtime, _, _ = setup_request
    stale = deepcopy(item)
    send(setup_request)
    store.reset_mock()
    store.observe_work_item_control.side_effect = lambda _: WorkItemControlProjection(
        deepcopy(item)
    )
    manifest = Manifest(
        meta={},
        nodes={
            "node": Node(
                id="node", worker="worker", work_item_id="issue", status="in_progress"
            )
        },
    )
    result = _retry_worker_handoff(store, runtime, manifest, "node", stale)
    assert result.state == "needs-decision"
    store.clear_assignment.assert_not_called()
    store.update_work_item_metadata.assert_not_called()
    store.assign_work_item.assert_not_called()
    runtime.wake.assert_not_called()


def test_dispatch_ignores_stale_projection_when_decision_arrived(setup_request):
    from omac.pipeline.loop import _dispatch_worker_handoff
    from omac.core.manifest import Manifest, Node
    from omac.engines.models import WorkItemControlProjection

    item, store, runtime, _, _ = setup_request
    stale = WorkItemControlProjection(deepcopy(item))
    send(setup_request)
    store.reset_mock()
    store.observe_work_item_control.side_effect = lambda _: WorkItemControlProjection(
        deepcopy(item)
    )
    manifest = Manifest(
        meta={},
        nodes={
            "node": Node(
                id="node", worker="worker", work_item_id="issue", status="in_progress"
            )
        },
    )
    result = _dispatch_worker_handoff(
        store, runtime, manifest, "node", projection=stale
    )
    assert result.state == "needs-decision"
    store.update_work_item_metadata.assert_not_called()
    store.assign_work_item.assert_not_called()


def test_owner_decision_accepts_partial_evidence_without_success_verification(
    setup_request,
):
    report = setup_request[3]
    report["reason_code"] = "owner-decision-required"
    report["evidence"] = [
        {
            "ref": "local/notes.md",
            "observation": "Owner scope decision needed; no validation executed",
        }
    ]
    assert send(setup_request)["exit_code"] == 20


@pytest.mark.parametrize(
    "mutation",
    [
        "ambiguous",
        "foreign",
        "old-baseline",
        "generation-during-read",
        "status-done",
        "missing-evidence",
        "invalid-reason",
        "unknown-contract-ref",
    ],
)
def test_fail_closed_boundaries(setup_request, mutation):
    item, store, runtime, report, _ = setup_request
    if mutation == "ambiguous":
        runtime.list_runs.return_value.append(
            AgentRunObservation("second", "direct", "running", agent_id="agent")
        )
    if mutation == "foreign":
        runtime.list_runs.return_value.append(
            AgentRunObservation("second", "direct", "running", agent_id="foreign")
        )
    if mutation == "old-baseline":
        item.worker_handoff = replace(
            item.worker_handoff, baseline_direct_run_ids=("run",)
        )
    if mutation == "generation-during-read":

        def runs(_):
            item.worker_handoff = replace(item.worker_handoff, generation="new")
            return [
                AgentRunObservation(
                    "run",
                    "direct",
                    "running",
                    agent_id="agent",
                    trigger_kind="issue_assignment",
                )
            ]

        runtime.list_runs.side_effect = runs
    if mutation == "status-done":
        item.status = WorkItemStatus.DONE
    if mutation == "missing-evidence":
        report["evidence"] = []
    if mutation == "invalid-reason":
        report["reason_code"] = []
    if mutation == "unknown-contract-ref":
        report["contract_ref"] = "nonexistent"
    with pytest.raises(ValidationError):
        send(setup_request)
    store.update_work_item_metadata.assert_not_called()


def test_submit_cannot_override_persisted_worker_decision(setup_request, monkeypatch):
    from omac.pipeline import dispatch
    from omac.errors import NeedsDecision

    item, store, _, _, _ = setup_request
    send(setup_request)
    monkeypatch.setattr(
        dispatch,
        "load_submit_context",
        lambda *a: (deepcopy(item), item.kind, item.phase),
    )
    with pytest.raises(NeedsDecision):
        dispatch.submit(
            store,
            item.id,
            pr_url="https://example/pr/1",
            verification_file="absent.yaml",
        )


def test_metadata_policy_accepts_exact_bounded_report(setup_request):
    from omac.engines.metadata_policy import assert_metadata_write_allowed
    from omac.core.review_convergence import bounded_decision_required

    decision = send(setup_request)["decision_required"]
    assert bounded_decision_required(decision) == decision
    assert_metadata_write_allowed("decision_required", decision)


def test_large_report_is_rejected_before_any_write(setup_request):
    setup_request[3]["summary"] = "x" * 2048
    with pytest.raises(ValidationError):
        send(setup_request)
    setup_request[1].update_work_item_metadata.assert_not_called()


def test_blocked_node_does_not_run_or_consume_budget_on_repeated_collection(
    setup_request, tmp_path
):
    from omac.core.manifest import Manifest, Node
    from omac.pipeline.loop import collect_results

    item, store, runtime, _, _ = setup_request
    send(setup_request)
    runtime.reset_mock()
    before = deepcopy(item)
    manifest = Manifest(
        meta={},
        nodes={
            "node": Node(
                id="node", worker="worker", work_item_id="issue", status="in_progress"
            )
        },
    )
    for _ in range(2):
        manifest.nodes["node"].status = "in_progress"  # Restart with stale local state.
        collect_results(store, runtime, manifest, str(tmp_path / "manifest.yaml"))
    assert manifest.nodes["node"].status == "blocked"
    assert item.bounces == before.bounces
    assert item.worker_handoff == before.worker_handoff
    assert not runtime.mock_calls


def test_cli_v2_returns_twenty(setup_request, monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    from omac.cli.commands import work
    from omac.cli.main import main

    item, store, runtime, report, path = setup_request
    path.write_text(yaml.safe_dump(report))
    monkeypatch.setattr(work, "_resolve_store", lambda: store)
    monkeypatch.setattr(
        work, "create_engine", lambda *a: SimpleNamespace(runtime=runtime)
    )
    assert main(["work", "block", item.id, "--report-file", str(path)]) == 20
    output = json.loads(capsys.readouterr().out)
    assert output["terminal"] and output["next_action"] == "stop" and not output["ok"]


def test_decision_arriving_during_runtime_read_prevents_retry_writes(setup_request):
    from omac.core.manifest import Manifest, Node
    from omac.pipeline.loop import _retry_worker_handoff

    item, store, runtime, _, _ = setup_request
    stale = deepcopy(item)
    runtime.list_runs.return_value = [
        replace(runtime.list_runs.return_value[0], status="completed")
    ]

    def observe_runs(_):
        item.decision_required = {"reason_code": "worker-decision-required"}
        return runtime.list_runs.return_value

    runtime.list_runs.side_effect = observe_runs
    manifest = Manifest(
        meta={}, nodes={"node": Node(id="node", worker="worker", work_item_id="issue")}
    )
    result = _retry_worker_handoff(store, runtime, manifest, "node", stale)
    assert result.state == "needs-decision"
    store.clear_assignment.assert_not_called()
    store.update_work_item_metadata.assert_not_called()
    runtime.wake.assert_not_called()


def test_report_does_not_hydrate_success_evidence(setup_request):
    from omac.engines.models import WorkItemPayload

    _, store, _, _, _ = setup_request
    store.get_work_item.side_effect = AssertionError(
        "Do not load unrelated attachments"
    )
    send(setup_request)
    assert all(
        call.args[1] == frozenset({WorkItemPayload.CONTRACT})
        for call in store.hydrate_work_item_evidence.call_args_list
    )

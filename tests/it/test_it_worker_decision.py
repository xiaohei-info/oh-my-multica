"""A Worker's request for a decision, persisted on a real Multica: one causal stop, never replayed, never a delivery.
(Which reports and Runs are accepted is decided by pure functions: tests/test_worker_decision.py.)"""
import json
import subprocess
import sys
from dataclasses import replace

import pytest
import yaml

from _support.loopkit import contract
from omac.core.taskmeta import TaskPhase, review_context_binding
from omac.core.workitem import WorkItemPayload, WorkItemStatus
from omac.errors import NeedsDecision, PlatformError, ValidationError
from omac.pipeline import dispatch, loop
from omac.pipeline.dispatch import build_show_output, report_worker_blocker


class Case:
    """Node ``a`` dispatched to alice, whose Run is still running when the test starts."""

    def __init__(self, kit, tmp_path):
        self.kit, self.tmp_path = kit, tmp_path
        self.manifest = kit.manifest([kit.node("a", contract=contract())])
        self.path = kit.save(self.manifest)
        kit.tick(self.manifest, self.path, max_parallel=1)
        self.item_id = self.manifest.nodes["a"].work_item_id
        kit.wait_for(lambda: kit.runtime.is_active(self.item_id), what="the Worker Run")
        self.report_file = tmp_path / "block.yaml"

    @property
    def item(self):
        return self.kit.store.get_work_item(self.item_id)

    def report(self, **over):
        item = self.item
        run = next(run for run in self.kit.runtime.list_runs(self.item_id) if run.kind == "direct")
        template = build_show_output(item, "worker:alice")["control"]["blocker_report_template"]
        return {**template, "reason_code": "quality-gate-failed", "run_id": run.id,
                "summary": "Required gate failed; contract requires an owner decision", "decision_needed": "Choose an authorized next step",
                "contract_ref": "objective",
                "evidence": [{"ref": "artifacts/failed.log", "command": "test coverage", "exit_code": 1,
                              "observation": "82% below required 85%; retained uncommitted work"}], **over}

    def send(self, report=None):
        self.report_file.write_text(yaml.safe_dump(report or self.report()))
        return report_worker_blocker(self.kit.store, self.item_id, str(self.report_file), runtime=self.kit.runtime)


@pytest.fixture()
def case(kit, tmp_path):
    kit.agents("hang")
    return Case(kit, tmp_path)


def test_a_valid_stop_blocks_the_issue_keeps_the_audit_and_is_idempotent_after_the_run_ends(case):
    kit = case.kit
    before = case.item
    report = case.report()
    reads = kit.record(kit.store, "hydrate_work_item_evidence", "update_work_item_metadata")     # a read inside a write is the adapter's own

    result = case.send(report)

    read_for_the_stop = [event for event in reads if event[0] == "hydrate_work_item_evidence"]
    blocked = case.item
    assert result["exit_code"] == 20 and result["terminal"] and result["ok"] is False
    assert blocked.status == WorkItemStatus.BLOCKED and blocked.decision_required["blocker"] == report
    assert (blocked.bounces, blocked.worker_handoff) == (before.bounces, before.worker_handoff)
    assert read_for_the_stop and all(call[1][1] == frozenset({WorkItemPayload.CONTRACT}) for call in read_for_the_stop)      # no success evidence is read for a stop
    kit.runtime.cancel(case.item_id)
    kit.wait_runs_ended(case.item_id)
    assert case.send(report) == result


def test_an_interrupted_status_write_leaves_the_stop_and_the_identical_report_repairs_it(case):
    kit = case.kit
    state = kit.fault(kit.store, "update_status", when="before", error=PlatformError("offline"))

    with pytest.raises(PlatformError):
        case.send()

    assert state["fired"] and case.item.decision_required and case.item.status != WorkItemStatus.BLOCKED
    state["armed"] = False
    assert case.send()["exit_code"] == 20 and case.item.status == WorkItemStatus.BLOCKED


def test_a_refused_report_writes_nothing_and_a_valid_one_still_goes_through(case):
    kit = case.kit
    report = case.report()
    writes = kit.record(kit.store, "update_work_item_metadata")
    statuses = kit.record(kit.store, "update_status")
    for phase in (TaskPhase.REVIEW, TaskPhase.CONFIRMATION):
        kit.store.update_work_item_metadata(case.item_id, phase=phase)
        del writes[:]
        with pytest.raises(ValidationError):
            case.send(report)
        assert not writes and not statuses, phase
    kit.store.update_work_item_metadata(case.item_id, phase=TaskPhase.AUTHORING)
    del writes[:]
    with pytest.raises(ValidationError):
        case.send({**report, "run_id": "old"})                         # not the Run that is running
    with pytest.raises(ValidationError):
        case.send({**report, "contract_ref": "nonexistent"})
    assert not writes and not statuses and case.item.decision_required is None

    assert case.send(report)["exit_code"] == 20


def test_a_generation_that_changes_while_the_runs_are_read_invalidates_the_report(case):
    kit = case.kit
    intent = case.item.worker_handoff
    report = case.report()

    def a_new_generation(runs, *_args):
        kit.store.update_work_item_metadata(case.item_id, worker_handoff=replace(intent, generation="new"))

    kit.after(kit.runtime, "list_runs", a_new_generation, only=lambda item_id: item_id == case.item_id)
    statuses = kit.record(kit.store, "update_status")

    with pytest.raises(ValidationError, match="changed while reporting"):
        case.send(report)

    assert case.item.decision_required is None and not statuses


def test_a_retry_with_a_stale_snapshot_sees_the_decision_and_writes_nothing(case):
    kit = case.kit
    stale = case.item
    case.send()
    writes = {name: kit.record(kit.store, name) for name in ("clear_assignment", "update_work_item_metadata", "assign_work_item")}
    wakes = kit.record(kit.runtime, "wake")

    result = loop._retry_worker_handoff(kit.store, kit.runtime, kit.code_host, case.manifest, "a", stale)

    assert result.state == "needs-decision" and not any(writes.values()) and not wakes


def test_a_dispatch_with_a_stale_projection_sees_the_decision_that_arrived(case):
    kit = case.kit
    stale = kit.store.observe_work_item_control(case.item_id)
    case.send()
    metadata, assigns = kit.record(kit.store, "update_work_item_metadata"), kit.record(kit.store, "assign_work_item")

    result = loop._dispatch_worker_handoff(kit.store, kit.runtime, kit.code_host, case.manifest, "a", projection=stale)

    assert result.state == "needs-decision" and not metadata and not assigns


def test_a_decision_that_arrives_while_the_runs_are_read_prevents_every_retry_write(case):
    kit = case.kit
    stale = case.item
    kit.runtime.cancel(case.item_id)
    kit.wait_runs_ended(case.item_id)
    kit.after(kit.runtime, "list_runs", lambda runs, *_a: kit.store.update_work_item_metadata(
        case.item_id, decision_required={"schema": "omac.decision-required/v1", "reason_code": "worker-decision-required"}),
        only=lambda item_id: item_id == case.item_id)
    writes = {name: kit.record(kit.store, name) for name in ("clear_assignment", "assign_work_item")}
    wakes = kit.record(kit.runtime, "wake")
    metadata = kit.record(kit.store, "update_work_item_metadata")

    result = loop._retry_worker_handoff(kit.store, kit.runtime, kit.code_host, case.manifest, "a", stale)

    own = [event for event in metadata if "decision_required" not in event[2]]      # the injected decision is the test's own write
    assert result.state == "needs-decision" and not any(writes.values()) and not wakes and not own


def test_a_submit_cannot_override_a_persisted_worker_decision(case):
    kit = case.kit
    case.send()

    with pytest.raises(NeedsDecision):
        dispatch.submit(kit.store, case.item_id, code_host=kit.code_host, pr_url="https://github.com/example/repo/pull/1",
                        verification_file="absent.yaml")


def test_collection_leaves_a_blocked_node_alone_and_spends_no_budget(case):
    kit = case.kit
    case.send()
    before = case.item
    wakes = kit.record(kit.runtime, "wake")
    assigns = kit.record(kit.store, "assign_work_item")

    for _ in range(2):
        case.manifest.nodes["a"].status = "in_progress"       # a restart with stale local state
        loop.collect_results(kit.store, kit.runtime, kit.code_host, case.manifest, case.path)

    after = case.item
    assert case.manifest.nodes["a"].status == "blocked"
    assert (after.bounces, after.worker_handoff) == (before.bounces, before.worker_handoff) and not wakes and not assigns


def test_the_work_block_command_exits_twenty_with_a_terminal_stop(case):
    kit = case.kit
    case.report_file.write_text(yaml.safe_dump(case.report()))
    env = {**__import__("os").environ, **kit.stack.cli_env(), **kit.stack.omac_env()}

    done = subprocess.run([sys.executable, "-m", "omac", "work", "block", case.item_id, "--report-file", str(case.report_file)],
                          env=env, capture_output=True, text=True)

    output = json.loads(done.stdout)
    assert done.returncode == 20 and output["terminal"] and output["next_action"] == "stop" and not output["ok"]


# ---- the context binding of a Worker handoff: a deferred contract is not the JSON null contract ------------------------------

def deferred_contract_node(kit):
    """Node ``w`` whose issue keeps its contract as an attachment (the store defers it) and is waiting for its first dispatch."""
    item = kit.item("w", worker="alice", contract=contract(), status=WorkItemStatus.IN_PROGRESS)
    node = kit.node("w", contract=contract())
    node.work_item_id, node.status = item.id, "in_progress"
    manifest = kit.manifest([node])
    return manifest, item


def null_binding(item):
    return review_context_binding(replace(item, contract=None))


def cold_engine(kit):
    """A second adapter with empty caches: the contract has to come over the wire again."""
    from omac.engines import create_engine
    from omac.engines.models import EngineConfig

    return create_engine("multica", EngineConfig(engine_type="multica", workspace_id=kit.workspace))


def test_an_explicit_dispatch_binds_the_materialized_contract_and_not_null(kit):
    manifest, item = deferred_contract_node(kit)
    cold = cold_engine(kit)
    kit.proxy                                                  # record the wire from here on
    projection = cold.store.observe_work_item_control(item.id)
    assert projection.work_item.contract is None and WorkItemPayload.CONTRACT in projection.deferred_payloads
    assert review_context_binding(projection.work_item) == null_binding(item)
    contract_gets = lambda: [row for row in kit.proxy.log if row[0] == "GET" and "signed-download" in row[1]
                             and item.contract_ref["attachment_id"] in row[1]]
    before = len(contract_gets())

    loop._dispatch_worker_handoff(cold.store, cold.runtime, kit.code_host, manifest, "w", gate="explicit-dispatch", review_bounce=0)

    current = kit.store.get_work_item(item.id)
    intent = current.worker_handoff
    assert intent.review_context_binding == review_context_binding(current) != null_binding(current)
    shown = build_show_output(current, "worker:alice")
    assert shown["control"]["blocker_report_template"]["review_context_binding"] == intent.review_context_binding
    assert len(contract_gets()) - before == 1               # the contract only, not stale verification or review reports


@pytest.mark.parametrize("failure", ["transport", "unmaterialized"])
def test_a_contract_that_cannot_be_hydrated_never_persists_a_null_bound_intent(kit, failure):
    from _support.it_proxy import Rule

    manifest, item = deferred_contract_node(kit)
    cold = cold_engine(kit)
    if failure == "transport":
        kit.proxy.rules.extend(Rule("GET", item.contract_ref["attachment_id"], "fail_before") for _ in range(4))   # every read attempt fails
    else:
        kit.distort(cold.store, "hydrate_work_item_evidence", lambda hydrated: replace(hydrated, contract=None), times=5)
    metadata = kit.record(cold.store, "update_work_item_metadata")
    wakes, assigns = kit.record(cold.runtime, "wake"), kit.record(cold.store, "assign_work_item")

    with pytest.raises(PlatformError):
        loop._dispatch_worker_handoff(cold.store, cold.runtime, kit.code_host, manifest, "w", gate="explicit-dispatch", review_bounce=0)

    assert not any("worker_handoff" in event[2] for event in metadata) and not wakes and not assigns


def test_a_report_bound_to_the_null_contract_is_stale_while_the_materialized_one_is_accepted(case):
    kit = case.kit
    intent = case.item.worker_handoff
    report = case.report()
    kit.store.update_work_item_metadata(case.item_id, worker_handoff=replace(intent, review_context_binding=null_binding(case.item)))
    writes = kit.record(kit.store, "update_work_item_metadata")

    with pytest.raises(ValidationError, match="Stale worker decision binding"):
        case.send(report)

    assert not writes
    kit.store.update_work_item_metadata(case.item_id, worker_handoff=intent)
    assert case.send(report)["exit_code"] == 20 and case.item.status == WorkItemStatus.BLOCKED

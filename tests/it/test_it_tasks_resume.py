"""run_task resumed on a real Multica: what current facts allow, what they refuse, and that a refusal touches nothing."""
import copy
import time
from dataclasses import replace

import pytest
import yaml
from structlog.testing import capture_logs

import omac.pipeline.tasks as tasks_module
from omac.core import logsetup
from _support.tasksworld import World, payload, review_report
from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, PlatformError, WorkItemNotFoundError
from omac.pipeline.tasks import run_task


@pytest.fixture()
def world(kit, tmp_path):
    return World(kit, tmp_path)


def authored(world, item):
    """The author's Run has run and ended, as it has by the time any review is dispatched (a Reviewer is assigned after a Worker was)."""
    world.kit.stack.set_scenario("alice", [[]])
    world.kit.assign(item.id, "alice")
    world.kit.wait_runs_ended(item.id)


def no_poll(reason):
    return lambda: pytest.fail(reason)


def untouched(world):
    """Record what a resume that must refuse would write or start; the returned dict is empty when nothing happened."""
    kit = world.kit
    return {"assign": kit.record(kit.store, "assign_work_item"), "reset": kit.record(kit.store, "reset_review"),
            "wake": kit.record(kit.runtime, "wake", "dispatch_reviewer")}


def nothing_happened(log):
    return not any(log.values())


def test_a_resume_refreshes_nothing_when_a_delivery_is_on_record_and_reruns_the_author(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-no-refresh-delivery")
    kit.store.update_work_item_metadata(item.id, deliverable="an earlier delivery")
    world.author(world.plan("fresh plan"))
    kit.monkeypatch.setattr(tasks_module, "refresh_authoring_task", lambda *_a, **_k: pytest.fail("current facts do not permit a refresh"))

    result = world.run(TaskKind.PLAN, payload(title="plan"), "alice", resume_item_id=item.id)

    assert result["delivery"]["plan"] == "fresh plan"


def test_a_stale_confirmation_is_refused_without_a_new_reviewer(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-stale-confirmation", title="feature-x")
    world.stage(item, phase=TaskPhase.CONFIRMATION, deliverable="计划正文-v2", verdict="pass-with-nits", bounce=1, bound=False,
                review_subject_digest="stale-v1-review-subject", status=WorkItemStatus.IN_REVIEW)
    log = untouched(world)

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.PLAN, payload(), "alice", reviewers=["bob"], poll=no_poll("a confirmation must not poll a Reviewer"),
                 resume_item_id=item.id)

    assert raised.value.report["reason_code"] == "confirmation-not-consumable"
    assert raised.value.report["next_action"] == f"omac work show {item.id} --output json" and nothing_happened(log)


@pytest.mark.parametrize("state", ["blocked", "failed"])
def test_an_unsafe_authoring_resume_with_a_delivery_fails_before_the_runtime_is_touched(world, state):
    kit = world.kit
    item = world.issue(TaskKind.AMENDMENT, f"amend-resume-stopped-{state}", title="running DAG amendment")
    kit.store.update_work_item_metadata(item.id, deliverable="existing amendment proposal", phase=TaskPhase.AUTHORING)
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED if state == "blocked" else WorkItemStatus.FAILED)
    kit.forbid(kit.runtime, "is_active", "wake")
    assigns = kit.record(kit.store, "assign_work_item")

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.AMENDMENT, payload(title="running DAG amendment"), "alice", poll=no_poll("an unsafe resume must not poll"),
                 resume_item_id=item.id)

    report = raised.value.report
    assert raised.value.exit_code == 20 and report["reason_code"] == "unsafe-authoring-resume" and report["phase"] == "authoring"
    assert "--new-attempt" in report["next_action"] and f"--supersedes-issue-id {item.id}" in report["next_action"]
    assert not assigns


@pytest.mark.parametrize("invalid_fact", ["stale-subject", "reject-verdict", "missing-report", "invalid-evidence"])
def test_a_confirmation_with_invalid_current_facts_fails_before_any_agent_path(world, invalid_fact):
    kit = world.kit
    item = world.issue(TaskKind.AMENDMENT, f"amend-confirmation-{invalid_fact}", title="running DAG amendment")
    report = review_report()
    if invalid_fact == "invalid-evidence":
        report = {k: v for k, v in report.items() if k != "full_review_completed"}
    world.stage(item, phase=TaskPhase.CONFIRMATION, deliverable="current amendment proposal",
                verdict="reject" if invalid_fact == "reject-verdict" else "pass", report=None if invalid_fact == "missing-report" else report)
    if invalid_fact == "stale-subject":
        kit.store.update_work_item_metadata(item.id, review_subject_digest="stale-subject")
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    kit.forbid(kit.runtime, "is_active")
    log = untouched(world)

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.AMENDMENT, payload(title="running DAG amendment"), "alice", reviewers=["bob"], confirm=True,
                 poll=no_poll("a confirmation must not poll"), resume_item_id=item.id)

    assert raised.value.exit_code == 20 and raised.value.report["reason_code"] == "confirmation-not-consumable"
    assert "--new-attempt" in raised.value.report["next_action"] and f"--supersedes-issue-id {item.id}" in raised.value.report["next_action"]
    assert nothing_happened(log)


def amendment_in_review_with_nits(world):
    contract = payload()["contract"]
    contract.source_of_truth = ["docs/design.md"]
    item = world.issue(TaskKind.AMENDMENT, "amend-resume-final-nits", title="running DAG amendment", contract=contract)
    report = review_report()
    report["nits"] = ["operator guide wording"]
    return world.stage(item, phase=TaskPhase.REVIEW,
                       deliverable="schema: omac.dag-amendment/v1\nreason: fixed\noperations: []\n", verdict="pass-with-nits", report=report,
                       status=WorkItemStatus.IN_REVIEW)


def test_an_in_review_amendment_with_nits_enters_confirmation_without_dispatching_anyone(world):
    kit = world.kit
    item = amendment_in_review_with_nits(world)
    log = untouched(world)

    with capture_logs() as events:
        result = run_task(kit.engine, TaskKind.AMENDMENT, payload(contract=amendment_contract(item)), "alice", reviewers=["bob"], confirm=True,
                          pause_at_confirmation=True, poll=no_poll("a resume must not poll a new Run"), resume_item_id=item.id)

    names = [event["event"] for event in events]
    assert logsetup.EVT_DISPATCH not in names and logsetup.EVT_HUMAN_GATE_WAIT in names     # resuming a confirmation dispatches nothing
    resumed = kit.store.get_work_item(item.id)
    assert result["item_id"] == item.id and result["verdict"] == "pass-with-nits" and result["pending_confirmation"] is True
    assert (resumed.phase, resumed.review_verdict) == (TaskPhase.CONFIRMATION, "pass-with-nits") and nothing_happened(log)


def amendment_contract(item):
    from omac.core.manifest import _load_contract

    return _load_contract(item.contract) if isinstance(item.contract, dict) else item.contract


def test_an_amendment_resumed_with_other_docs_than_its_persisted_contract_is_refused_before_dispatch(world):
    kit = world.kit
    item = amendment_in_review_with_nits(world)
    persisted = amendment_contract(item)
    requested = Contract(objective=persisted.objective, acceptance=list(persisted.acceptance),
                         source_of_truth=["docs/design.md", "docs/review-report.md"])
    log = untouched(world)

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.AMENDMENT, payload(contract=requested), "alice", reviewers=["bob"], confirm=True,
                 poll=no_poll("contract drift must stop before polling"), resume_item_id=item.id)

    current = kit.store.get_work_item(item.id)
    assert raised.value.report["reason_code"] == "amendment-resume-contract-drift"
    assert raised.value.report["persisted_source_of_truth"] == ["docs/design.md"]
    assert (current.phase, current.review_verdict) == (TaskPhase.REVIEW, "pass-with-nits") and nothing_happened(log)


@pytest.mark.parametrize("status", [WorkItemStatus.FAILED, WorkItemStatus.BLOCKED])
def test_a_valid_confirmation_is_consumed_whatever_the_platform_status_projection_says(world, status):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, f"plan-confirmation-{status.value}", contract=payload()["contract"])
    world.stage(item, phase=TaskPhase.CONFIRMATION, deliverable="current plan", verdict="pass-with-nits", report=review_report(), status=status)
    kit.forbid(kit.runtime, "is_active", "wake")
    assigns = kit.record(kit.store, "assign_work_item")

    result = run_task(kit.engine, TaskKind.PLAN, payload(contract=Contract(objective="调用参数中的陈旧 contract", acceptance=["不应覆盖 Store contract"])),
                      "alice", reviewers=["bob"], poll=no_poll("a valid confirmation must not poll"), resume_item_id=item.id)

    assert result["verdict"] == "pass-with-nits" and result["delivery"]["plan"] == "current plan" and not assigns


def test_the_current_store_facts_win_over_a_stale_snapshot(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-current-over-snapshot")
    stale = copy.deepcopy(item)
    world.stage(item, phase=TaskPhase.CONFIRMATION, deliverable="current plan", verdict="pass", report=review_report(), status=WorkItemStatus.IN_REVIEW)
    kit.monkeypatch.setattr(tasks_module, "refresh_authoring_task", lambda *_a, **_k: pytest.fail("a stale snapshot must not overwrite Store facts"))
    kit.forbid(kit.runtime, "is_active")

    result = run_task(kit.engine, TaskKind.PLAN, payload(), "alice", reviewers=["bob"], poll=no_poll("a valid confirmation must not poll"),
                      resume_item_id=item.id, resume_item_snapshot=stale)

    assert result["verdict"] == "pass" and result["delivery"]["plan"] == "current plan"


def test_an_issue_the_platform_no_longer_has_is_never_resumed_from_a_snapshot(world):
    kit = world.kit
    snapshot = kit.store.create_work_item(kit.workspace, "plan", "small body", dag_key="plan-missing", worker="alice", kind=TaskKind.PLAN)
    kit.monkeypatch.setattr(kit.store, "get_work_item", lambda _id: (_ for _ in ()).throw(WorkItemNotFoundError("item no longer exists")))
    kit.monkeypatch.setattr(tasks_module, "refresh_authoring_task", lambda *_a, **_k: pytest.fail("a missing item must not be refreshed"))

    with pytest.raises(WorkItemNotFoundError, match="no longer exists"):
        run_task(kit.engine, TaskKind.PLAN, payload(), "alice", poll=no_poll("a missing item must not poll"), resume_item_id=snapshot.id,
                 resume_item_snapshot=snapshot)


def test_a_confirmation_is_consumed_for_the_round_after_the_earlier_bounces(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-confirmation-after-bounces")
    world.stage(item, phase=TaskPhase.CONFIRMATION, deliverable="计划正文-v3", verdict="pass", report=review_report(), bounce=2, round_index=3,
                status=WorkItemStatus.IN_REVIEW)
    assigns = kit.record(kit.store, "assign_work_item")

    result = run_task(kit.engine, TaskKind.PLAN, payload(), "alice", reviewers=["bob"], poll=no_poll("a current confirmation must not redispatch"),
                      resume_item_id=item.id)

    assert (result["verdict"], result["rounds"]) == ("pass", 3) and not assigns


def test_a_confirmation_with_invalid_stored_review_evidence_fails_in_place(world):
    kit = world.kit
    item = world.issue(TaskKind.ACCEPTANCE, "acceptance-invalid-evidence", title="acceptance document")
    world.stage(item, phase=TaskPhase.CONFIRMATION, deliverable="当前验收正文", verdict="pass",
                report={k: v for k, v in review_report().items() if k != "full_review_completed"}, status=WorkItemStatus.IN_REVIEW)
    log = untouched(world)

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.ACCEPTANCE, payload(title="acceptance document"), "alice", reviewers=["bob"], confirm=True,
                 poll=no_poll("a confirmation must not poll a Reviewer"), resume_item_id=item.id)

    assert raised.value.report["reason_code"] == "confirmation-not-consumable" and nothing_happened(log)


def test_a_finished_pass_with_nits_item_is_terminal(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-done-with-nits")
    kit.store.update_work_item_metadata(item.id, deliverable="计划正文-v2", phase=TaskPhase.REVIEW, review_verdict="pass-with-nits")
    kit.store.update_status(item.id, WorkItemStatus.DONE)

    result = run_task(kit.engine, TaskKind.PLAN, payload(), "alice", reviewers=["bob"], poll=no_poll("a completed item must not be polled"),
                      resume_item_id=item.id)

    assert (result["verdict"], result["rounds"], result["delivery"]["plan"]) == ("pass-with-nits", 0, "计划正文-v2")
    assert kit.store.get_work_item(item.id).status == WorkItemStatus.DONE


def test_a_blank_review_verdict_is_an_undecided_state_not_a_reject(world):
    kit = world.kit
    item = kit.store.create_work_item(kit.workspace, "feature-x", "feature-x", dag_key="plan-blank-verdict", worker="alice", kind=TaskKind.PLAN)
    authored(world, item)
    kit.store.update_work_item_metadata(item.id, deliverable="计划正文", phase=TaskPhase.REVIEW, review_verdict="")
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    calls = []

    def poll_until_valid_verdict():
        time.sleep(0.1)   # a real Run needs a moment to appear
        calls.append(1)
        if len(calls) == 1:
            kit.store.update_work_item_metadata(item.id, review_verdict="pass", review_report=review_report(item=kit.store.get_work_item(item.id)))
        if len(calls) > 3:
            raise TimeoutError("blank verdict was treated as terminal")

    res = run_task(kit.engine, TaskKind.PLAN, payload(), "alice", reviewers=["bob"], poll=poll_until_valid_verdict, resume_item_id=item.id)

    assert res["verdict"] == "pass" and "verdict=reject" not in "\n".join(kit.comments(item.id))


def test_the_persisted_review_bounce_limit_is_honoured_even_when_the_platform_status_lags(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-bounce-limit")
    # a Reviewer Run that finished while the platform still shows the issue in progress
    world.stage(item, phase=TaskPhase.REVIEW, deliverable="计划正文-v3", verdict="reject", report=review_report("reject"), bounce=2, round_index=3,
                review_comment="第三轮仍有 blocker", status=WorkItemStatus.IN_PROGRESS)

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.PLAN, payload(), "alice", reviewers=["bob", "charlie"], max_revisions=3,
                 poll=no_poll("a final reject must not start a producer"), resume_item_id=item.id)

    got = kit.store.get_work_item(item.id)
    assert raised.value.report["rounds"] == 3 and (got.bounces.review, got.review_verdict, got.phase) == (3, "reject", TaskPhase.REVIEW)


def test_a_verdict_not_bound_to_the_current_review_subject_is_invalidated(world):
    kit = world.kit
    item = world.issue(TaskKind.ACCEPTANCE, "acceptance-unbound-verdict", title="acceptance document")
    authored(world, item)
    kit.store.update_work_item_metadata(item.id, deliverable="当前验收正文", phase=TaskPhase.REVIEW, review_verdict="pass-with-nits",
                                        review_comment="旧交付的建议")
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    assigns = kit.record(kit.store, "assign_work_item")

    def submit_fresh_review():
        time.sleep(0.1)
        current = kit.store.get_work_item(item.id)
        assert current.phase == TaskPhase.REVIEW and current.review_verdict is None and current.review_subject_digest
        kit.store.update_work_item_metadata(item.id, review_verdict="pass", review_report=review_report(item=current))

    result = run_task(kit.engine, TaskKind.ACCEPTANCE, payload(title="acceptance document"), "alice", reviewers=["bob"], poll=submit_fresh_review,
                      resume_item_id=item.id)

    assert result["verdict"] == "pass" and [a[1][2] for a in assigns] == ["reviewer"]


def test_a_verdict_bound_to_the_current_subject_is_consumed_without_a_second_reviewer(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-bound-verdict")
    world.stage(item, phase=TaskPhase.REVIEW, deliverable="计划正文", verdict="pass", report=review_report(), status=WorkItemStatus.IN_REVIEW)
    assigns = kit.record(kit.store, "assign_work_item")

    result = run_task(kit.engine, TaskKind.PLAN, payload(), "alice", reviewers=["bob"], poll=no_poll("a bound verdict must not be redispatched"),
                      resume_item_id=item.id)

    assert result["verdict"] == "pass" and not assigns


def test_a_bound_verdict_whose_stored_report_is_invalid_is_not_consumed(world):
    kit = world.kit
    item = world.issue(TaskKind.ACCEPTANCE, "acceptance-invalid-bound-report", title="acceptance document")
    authored(world, item)
    world.stage(item, phase=TaskPhase.REVIEW, deliverable="当前验收正文", verdict="pass", report={}, status=WorkItemStatus.IN_REVIEW)
    assigns = kit.record(kit.store, "assign_work_item")

    def submit_valid_review():
        time.sleep(0.1)
        current = kit.store.get_work_item(item.id)
        if current.phase == TaskPhase.REVIEW and current.review_verdict is None:
            kit.store.update_work_item_metadata(item.id, review_verdict="pass", review_report=review_report(item=current))

    result = run_task(kit.engine, TaskKind.ACCEPTANCE, payload(title="acceptance document"), "alice", reviewers=["bob"], poll=submit_valid_review,
                      resume_item_id=item.id)

    assert result["verdict"] == "pass" and [a[1][2] for a in assigns] == ["reviewer"]


# ---- explicit resume of an authoring issue whose Run stopped ----------------------------------------------------------------

def amendment_issue(world, key, **spec):
    return world.issue(TaskKind.AMENDMENT, key, title="running DAG amendment", contract=payload()["contract"], **spec)


def runs_of(world, item):
    return world.kit.runtime.list_runs(item.id)


def test_resuming_a_stopped_authoring_issue_starts_no_run_and_leaves_its_contract_and_bounces_alone(world):
    kit = world.kit
    item = amendment_issue(world, "amend-resume-stopped")
    kit.store.update_work_item_metadata(item.id, review_bounce=2)
    kit.store.update_status(item.id, WorkItemStatus.FAILED)       # Multica has no "failed": the platform keeps it blocked
    before = kit.store.get_work_item(item.id)
    assert before.status is WorkItemStatus.BLOCKED
    kit.forbid(kit.runtime, "wake")
    kit.forbid(kit.store, "assign_work_item")

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.AMENDMENT, payload(title="running DAG amendment"), "alice", poll=no_poll("a stopped issue must not poll"),
                 resume_item_id=item.id)

    resumed = kit.store.get_work_item(item.id)
    assert raised.value.report["last_opinion"] == "producer blocked" and not runs_of(world, item)
    assert (resumed.contract, resumed.contract_ref, resumed.bounces.review) == (before.contract, before.contract_ref, 2)


def test_resuming_an_issue_with_an_active_run_waits_for_it_instead_of_starting_another(world):
    kit = world.kit
    item = amendment_issue(world, "amend-resume-active")
    kit.stack.set_scenario("alice", [[{"hang": 120}]])
    kit.assign(item.id, "alice")
    kit.wait_for(lambda: kit.runtime.is_active(item.id), what="the Run to be active")
    assigns, wakes = kit.record(kit.store, "assign_work_item"), kit.record(kit.runtime, "wake")

    def the_running_run_delivers():
        kit.store.update_work_item_metadata(item.id, deliverable=world.amendment_text(), phase=TaskPhase.REVIEW)
        kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    result = run_task(kit.engine, TaskKind.AMENDMENT, payload(title="running DAG amendment"), "alice", poll=the_running_run_delivers,
                      resume_item_id=item.id)

    assert result["item_id"] == item.id and not assigns and not wakes and len(runs_of(world, item)) == 1


def test_resuming_waits_for_an_active_run_that_then_fails_and_does_not_start_a_second_one(world):
    kit = world.kit
    item = amendment_issue(world, "amend-resume-active-failure")
    kit.stack.set_scenario("alice", [[{"hang": 120}], [world.amendment(world.amendment_text())]])
    kit.assign(item.id, "alice")
    kit.wait_for(lambda: kit.runtime.is_active(item.id), what="the first Run to be active")
    polls = []

    def the_running_run_fails():
        polls.append(1)
        if len(polls) == 1:
            kit.runtime.cancel(item.id)
            kit.store.update_status(item.id, WorkItemStatus.BLOCKED)    # what the platform does to the issue of a failed Run
        time.sleep(0.2)

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.AMENDMENT, payload(title="running DAG amendment"), "alice", poll=the_running_run_fails,
                 resume_item_id=item.id)

    assert raised.value.report["last_opinion"] == "producer blocked" and len(runs_of(world, item)) == 1


def test_a_failed_review_state_is_resumed_with_the_reviewer_and_never_the_author(world):
    kit = world.kit
    item = world.issue(TaskKind.PLAN, "plan-review-resume", contract=payload()["contract"])
    world.author(world.plan("plan body"))
    kit.assign(item.id, "alice")
    kit.wait_runs_ended(item.id)
    authors = len(runs_of(world, item))
    kit.store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW, review_verdict="", review_subject_digest="")
    kit.store.update_status(item.id, WorkItemStatus.FAILED)
    world.review("pass")

    result = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"], resume_item_id=item.id)

    assert result["verdict"] == "pass" and len(runs_of(world, item)) == authors + 1 and len(kit.reviewer_runs(item.id)) == 1


def test_a_confirmation_whose_run_failed_never_reruns_an_agent(world):
    kit = world.kit
    item = amendment_issue(world, "amend-confirmation")
    kit.store.update_work_item_metadata(item.id, deliverable="amendment body", phase=TaskPhase.CONFIRMATION)
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    kit.forbid(kit.runtime, "wake", "dispatch_reviewer")
    kit.forbid(kit.store, "assign_work_item")

    with pytest.raises(NeedsDecision) as raised:
        run_task(kit.engine, TaskKind.AMENDMENT, payload(title="running DAG amendment"), "alice", reviewers=["bob"], confirm=True,
                 pause_at_confirmation=True, poll=no_poll("a confirmation must not poll"), resume_item_id=item.id)

    assert raised.value.report["phase"] == "confirmation"


def test_a_reviewer_that_finishes_without_a_verdict_blocks_the_review_instead_of_looping(world):
    kit = world.kit
    world.author(world.amendment(world.amendment_text()))
    kit.stack.set_scenario("bob", [[]])

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.AMENDMENT, payload(), "alice", reviewers=["bob"])

    current = kit.store.get_work_item(raised.value.report["item_id"])
    assert raised.value.report["reason_code"] == "reviewer-completed-without-verdict"
    assert (current.phase, current.status) == (TaskPhase.REVIEW, WorkItemStatus.BLOCKED) and len(kit.reviewer_runs(current.id)) == 1


def test_resuming_after_a_reviewer_without_verdict_clears_only_the_decision_it_knows(world):
    kit = world.kit

    def blocked_review(key, scenario):
        item = amendment_issue(world, key)
        kit.stack.set_scenario("bob", scenario)
        kit.store.assign_work_item(item.id, "bob", "reviewer")
        decision = {"schema": "omac.decision-required/v1", "reason_code": "reviewer-completed-without-verdict", "kind": "amendment",
                    "phase": "review", "resume_issue_id": item.id}
        kit.store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW, deliverable="amendment", decision_required=decision)
        kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
        return item, decision

    item, _ = blocked_review("amend-resume-reviewer-no-verdict", [[]])
    kit.wait_runs_ended(item.id)
    resumed = tasks_module._resume_reviewer_completed_without_verdict(kit.engine, kit.store.get_work_item(item.id))
    assert (resumed.status, resumed.phase) == (WorkItemStatus.IN_REVIEW, TaskPhase.REVIEW) and resumed.decision_required in (None, {})

    item, decision = blocked_review("amend-resume-reviewer-active", [[{"hang": 120}]])
    kit.wait_for(lambda: kit.runtime.is_active(item.id), what="the Reviewer Run to be active")
    with pytest.raises(NeedsDecision, match="still active"):
        tasks_module._resume_reviewer_completed_without_verdict(kit.engine, kit.store.get_work_item(item.id))
    assert kit.store.get_work_item(item.id).decision_required == decision

    item, decision = blocked_review("amend-resume-reviewer-unknown", [[]])
    kit.wait_runs_ended(item.id)
    kit.distort(kit.runtime, "list_runs", lambda runs: [replace(run, status="unknown") for run in runs])
    with pytest.raises(NeedsDecision, match="active or its terminal state is unknown"):
        tasks_module._resume_reviewer_completed_without_verdict(kit.engine, kit.store.get_work_item(item.id))
    assert kit.store.get_work_item(item.id).decision_required == decision


def test_a_persisted_reviewer_pass_on_an_amendment_enters_confirmation_without_redispatch(world):
    kit = world.kit
    item = amendment_issue(world, "amend-resume-passed-review")
    report = review_report("pass", item)
    world.stage(item, phase=TaskPhase.REVIEW, deliverable="amendment: existing reviewed delivery", verdict="pass", report=report,
                review_report_source=yaml.safe_dump(report), status=WorkItemStatus.IN_REVIEW)
    kit.forbid(kit.runtime, "dispatch_reviewer", "wake")

    result = run_task(kit.engine, TaskKind.AMENDMENT, payload(title="resume passed amendment"), "alice", reviewers=["bob"], confirm=True,
                      pause_at_confirmation=True, poll=no_poll("a persisted pass must not poll"), resume_item_id=item.id)

    resumed = kit.store.get_work_item(item.id)
    assert result["pending_confirmation"] is True and result["verdict"] == "pass"
    assert (resumed.phase, resumed.review_verdict) == (TaskPhase.CONFIRMATION, "pass")


def test_a_resumed_authoring_issue_whose_last_run_ended_without_submit_blocks_with_the_run_named(world):
    kit = world.kit
    item = amendment_issue(world, "amend-resume-terminal-authoring")
    kit.stack.set_scenario("alice", [[]])
    kit.assign(item.id, "alice")
    kit.wait_runs_ended(item.id)
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    assigns = kit.record(kit.store, "assign_work_item")

    with pytest.raises(NeedsDecision, match="ended without submitting") as raised:
        tasks_module._guard_resume_authoring_terminal_run(kit.engine, kit.store.get_work_item(item.id), TaskKind.AMENDMENT)

    blocked = kit.store.get_work_item(item.id)
    assert raised.value.report["reason_code"] == "completed-without-submit" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required["run_id"] == kit.run_ids(item.id)[0] and not assigns


def test_a_resumed_authoring_issue_whose_runs_cannot_be_listed_is_left_unchanged(world):
    kit = world.kit
    item = amendment_issue(world, "amend-resume-unreadable-authoring")
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    kit.fault(kit.runtime, "list_runs", when="before", error=PlatformError("run list unavailable"))

    with pytest.raises(NeedsDecision, match="no new Run was started"):
        tasks_module._guard_resume_authoring_terminal_run(kit.engine, kit.store.get_work_item(item.id), TaskKind.AMENDMENT)

    unchanged = kit.store.get_work_item(item.id)
    assert unchanged.status is WorkItemStatus.IN_PROGRESS and unchanged.decision_required is None


def test_a_blocked_producer_short_circuits_the_resume(world):
    kit = world.kit
    item = world.issue(TaskKind.ACCEPTANCE, "acceptance-p1", title="acceptance document")
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.ACCEPTANCE, payload(title="acceptance document"), "alice", resume_item_id=item.id,
                  poll=no_poll("a blocked item must not be polled"))

    assert raised.value.report["rounds"] == 0 and "producer blocked" in raised.value.report["last_opinion"]

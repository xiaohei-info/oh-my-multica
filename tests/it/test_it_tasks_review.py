"""run_task review loops on a real Multica: rejects and rework, budgets and continuations, the human gate, and amendment tasks."""
import pytest
import yaml
from structlog.testing import capture_logs

from _support import ledgers
from _support.amendkit import contract_update, proposal, responsibility_acceptance_doc
from _support.taskkit import decompose_manifest
from _support.tasksworld import World, payload, review_report
from omac.core import logsetup
from omac.core.amendment import build_reviewed_amendment, historical_work_item_evidence_digest
from omac.core.manifest import Contract, Manifest, Node
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision
from omac.pipeline.dispatch import build_show_output
from omac.pipeline.plan import plan_continue_review
from omac.pipeline.tasks import run_task


@pytest.fixture()
def world(kit, tmp_path):
    return World(kit, tmp_path)


def handoff_comments(world, item_id):
    """Comments that are not just the carrier of an attachment: a stage handoff must not need one."""
    return [c for c in world.kit.comments(item_id) if not c.startswith("## omac ")]


def roles(assigns):
    return [a[1][2] for a in assigns]


def authored(world, item):
    """The author's Run has run and ended (a Reviewer is only ever assigned after a Worker was)."""
    world.kit.stack.set_scenario("alice", [[]])
    world.kit.assign(item.id, "alice")
    world.kit.wait_runs_ended(item.id)


def human_confirms(world, item_id):
    """The poll hook of a test that acts as the human: it marks the issue Done once the review has passed."""
    def poll():
        import time

        time.sleep(0.1)
        current = world.store.get_work_item(item_id) if item_id else None
        if current is not None and current.phase is TaskPhase.CONFIRMATION and current.status is not WorkItemStatus.DONE:
            world.store.update_status(current.id, WorkItemStatus.DONE)
    return poll


# ---- reviewing --------------------------------------------------------------------------------------------------------------

def test_a_decision_that_lands_while_the_review_starts_prevents_the_reviewer_assignment(world):
    kit = world.kit
    world.author(world.plan())
    decision = {"schema": "omac.decision-required/v1", "reason_code": "review-convergence-scope-expanding", "kind": "plan", "phase": "review",
                "gate": "review-convergence"}
    kit.after(kit.store, "update_status", lambda _r, item_id, status: kit.store.update_work_item_metadata(item_id, decision_required=decision),
              only=lambda item_id, status: status is WorkItemStatus.IN_REVIEW)
    assigns = kit.record(kit.store, "assign_work_item")

    with pytest.raises(NeedsDecision, match="Reviewer dispatch is blocked"):
        world.run(TaskKind.PLAN, payload(title="late-decision"), "alice", reviewers=["bob"])

    item = next(i for i in kit.store.list_work_items(kit.workspace) if i.title.endswith("late-decision"))
    assert item.status is WorkItemStatus.BLOCKED and item.decision_required == decision and roles(assigns) == ["worker"]


def test_handing_over_to_the_reviewer_posts_no_trigger_comment(world):
    world.author(world.plan())
    world.review("pass")

    res = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"])

    assert not any("阶段变更" in c and "omac work submit" in c for c in world.kit.comments(res["item_id"]))
    assert handoff_comments(world, res["item_id"]) == []


def test_a_changed_worker_follow_up_after_pass_with_nits_is_reviewed_again(world):
    world.author(world.plan("计划正文-v1"), world.plan("计划正文-v2"))
    world.review("pass-with-nits", "pass")

    res = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"])

    item = world.store.get_work_item(res["item_id"])
    assert res["verdict"] == "pass" and res["rounds"] == 2 and res["delivery"]["plan"] == "计划正文-v2"
    assert item.status == WorkItemStatus.DONE and item.bounces.review == 1 and item.decision_required is None
    assert handoff_comments(world, item.id) == []


def test_a_reject_goes_back_to_the_producer_through_metadata_and_not_a_comment(world):
    world.author(world.plan(), world.plan())
    world.review("reject", "pass")

    res = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"], max_revisions=3)

    assert res["verdict"] == "pass" and res["rounds"] == 2 and handoff_comments(world, res["item_id"]) == []


def test_two_rejects_then_a_pass_end_on_the_same_issue(world):
    kit = world.kit
    world.author(world.plan())
    world.review("reject", "reject", "pass")
    before = {i.id for i in kit.store.list_work_items(kit.workspace)}

    with capture_logs() as events:
        res = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"], max_revisions=3)

    names = [event["event"] for event in events]
    assert names.count(logsetup.EVT_VERDICT) >= 3 and logsetup.EVT_NODE_DONE in names       # two rejects and the pass
    assert any(event["event"] == logsetup.EVT_REVISION and event.get("gate") == "review" for event in events)
    item = kit.store.get_work_item(res["item_id"])
    assert (res["rounds"], res["verdict"]) == (3, "pass") and item.status == WorkItemStatus.DONE and item.bounces.review == 2
    assert {i.id for i in kit.store.list_work_items(kit.workspace)} - before == {item.id}   # one new issue for the whole task
    assert handoff_comments(world, item.id) == []


def test_the_reviewer_is_never_the_producer_when_another_one_is_available(world):
    world.author(world.plan())
    world.review("pass", agent="bob")
    world.review("pass", agent="charlie")

    res = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["alice", "bob", "charlie"])

    assert world.store.get_work_item(res["item_id"]).reviewer in ("bob", "charlie")


def test_a_failed_production_stops_the_task_at_once(world):
    from _support.loopkit import fails

    world.kit.stack.set_scenario("alice", [[fails("provider outage")]])

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.PLAN, payload(), "alice", dag_key="plan-producer-fails")

    assert raised.value.report["rounds"] == 0 and "without omac work submit" in raised.value.report["last_opinion"]


# ---- the human gate ---------------------------------------------------------------------------------------------------------

def test_the_human_gate_waits_until_somebody_confirms(world):
    world.author(world.plan())
    world.review("pass")
    polls = []

    def bounded_poll():
        import time

        time.sleep(0.1)
        polls.append(1)
        if len(polls) > 12:
            raise TimeoutError("not confirmed")

    with capture_logs() as events, pytest.raises(TimeoutError):
        world.run(TaskKind.PLAN, payload(title="human-gate-waits"), "alice", reviewers=["bob"], confirm=True, poll=bounded_poll)

    assert logsetup.EVT_HUMAN_GATE_WAIT in [event["event"] for event in events]

    item = next(i for i in world.store.list_work_items(world.kit.workspace) if i.title.endswith("human-gate-waits"))
    assert (item.review_verdict, item.phase.value) == ("pass", "confirmation") and not item.reviewer


def test_a_human_confirmation_moves_a_reviewed_issue_to_done(world):
    world.author(world.plan())
    world.review("pass")
    hook = []

    def poll():
        import time

        time.sleep(0.1)
        for item in world.store.list_work_items(world.kit.workspace):
            if item.title.endswith("human-confirms") and item.phase is TaskPhase.CONFIRMATION and item.status is not WorkItemStatus.DONE:
                world.store.update_status(item.id, WorkItemStatus.DONE)
                hook.append(item.id)

    res = world.run(TaskKind.PLAN, payload(title="human-confirms"), "alice", reviewers=["bob"], confirm=True, poll=poll)

    item = world.store.get_work_item(res["item_id"])
    assert res["verdict"] == "pass" and item.status == WorkItemStatus.DONE and (item.review_verdict, item.phase.value) == ("pass", "confirmation")


def test_the_confirmation_phase_is_the_last_write_after_the_assignment_is_cleared(world):
    kit = world.kit
    world.author(world.plan())
    world.review("pass")
    events = []
    original = kit.store.update_work_item_metadata

    def tracking_update(item_id, **fields):
        if fields.get("phase") == TaskPhase.CONFIRMATION:
            events.append("phase")
        return original(item_id, **fields)

    original_clear = kit.store.clear_assignment
    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", tracking_update)
    kit.monkeypatch.setattr(kit.store, "clear_assignment", lambda item_id: (events.append("clear"), original_clear(item_id))[1])

    def poll():
        import time

        time.sleep(0.1)
        for item in world.store.list_work_items(kit.workspace):
            if item.title.endswith("confirm-last") and item.phase is TaskPhase.CONFIRMATION and item.status is not WorkItemStatus.DONE:
                world.store.update_status(item.id, WorkItemStatus.DONE)

    world.run(TaskKind.PLAN, payload(title="confirm-last"), "alice", reviewers=["bob"], confirm=True, poll=poll)

    assert events[-2:] == ["clear", "phase"]


def test_without_reviewers_the_human_confirmation_is_the_end(world):
    world.author(world.plan())

    def poll():
        import time

        time.sleep(0.1)
        for item in world.store.list_work_items(world.kit.workspace):
            if item.title.endswith("human-only") and item.phase is TaskPhase.CONFIRMATION and item.status is not WorkItemStatus.DONE:
                world.store.update_status(item.id, WorkItemStatus.DONE)

    res = world.run(TaskKind.PLAN, payload(title="human-only"), "alice", confirm=True, poll=poll)

    assert res["verdict"] == "pass" and world.store.get_work_item(res["item_id"]).status == WorkItemStatus.DONE


def test_without_the_human_gate_a_delivery_goes_straight_to_review(world):
    world.author(world.plan())
    world.review("pass")

    assert world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"], confirm=False)["verdict"] == "pass"


# ---- budgets ----------------------------------------------------------------------------------------------------------------

def amendment_text():
    return yaml.safe_dump(proposal(contract_update()), sort_keys=False)


def test_an_exhausted_amendment_review_budget_projects_a_decision_and_the_same_issue_resumes(world):
    kit = world.kit
    world.author(world.amendment(amendment_text()))
    world.review("reject")

    with capture_logs() as events, pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.AMENDMENT, payload(title="budget-exhaustion"), "alice", reviewers=["bob"], max_revisions=3)

    assert logsetup.EVT_NEEDS_DECISION in [event["event"] for event in events]
    report = raised.value.report
    item = kit.store.get_work_item(report["item_id"])
    assert report["rounds"] == 3 and report["last_opinion"] and (report["kind"], report["phase"], report["gate"]) == ("amendment", "review", "review")
    assert report["resume_issue_id"] == item.id
    assert item.status == WorkItemStatus.BLOCKED and item.phase == TaskPhase.REVIEW
    assert item.decision_required == {
        "schema": "omac.decision-required/v1", "reason_code": "review-budget-exhausted", "kind": "amendment", "phase": "review",
        "gate": "review", "rounds": 3, "resume_issue_id": item.id, "review_ledger_ref": item.review_ledger_ref,
        "review_report_ref": item.review_report_ref}
    assert build_show_output(item, "orchestrator:alice")["context"]["decision_required"] == item.decision_required

    world.review("pass")
    resumed = world.run(TaskKind.AMENDMENT, payload(title="budget-exhaustion"), "alice", reviewers=["bob"], max_revisions=4, resume_item_id=item.id)
    assert resumed["item_id"] == item.id and resumed["verdict"] == "pass"
    assert kit.store.get_work_item(item.id).decision_required is None


def test_an_exhausted_machine_gate_budget_projects_a_blocked_decision(world):
    world.author(world.amendment(amendment_text()))
    world.review("pass")

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.AMENDMENT, payload(title="guard-exhaustion"), "alice", reviewers=["bob"], max_revisions=1,
                  guard=lambda _item: ["proposal is not executable"])

    item = world.store.get_work_item(raised.value.report["item_id"])
    assert item.status == WorkItemStatus.BLOCKED and item.phase == TaskPhase.REVIEW
    assert item.decision_required == {
        "schema": "omac.decision-required/v1", "reason_code": "guard-budget-exhausted", "kind": "amendment", "phase": "review",
        "gate": "guard", "rounds": 1, "resume_issue_id": item.id, "machine_feedback_ref": item.machine_feedback_ref}


def exhausted_decompose(world, key, *, verdict="reject", bounce=8, continuation=None):
    item = world.issue(TaskKind.DECOMPOSE, key, title="decompose review")
    authored(world, item)
    world.kit.store.update_work_item_metadata(
        item.id, deliverable="meta:\n  name: review\nnodes: []\n", phase=TaskPhase.REVIEW, review_verdict=verdict or "",
        review_comment="budget exhausted", review_bounce=bounce, **({"review_report": review_report(verdict)} if verdict else {}),
        **({"review_continuation": continuation} if continuation else {}))
    if verdict == "reject" and bounce > 0:
        import omac.pipeline.tasks as tasks_module

        world.kit.store.update_work_item_metadata(item.id, review_subject_digest=tasks_module._review_subject_digest(
            TaskKind.DECOMPOSE, world.kit.store.get_work_item(item.id), bounce))
    world.kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    return world.kit.store.get_work_item(item.id)


CONTINUATION = {"schema": "omac.review-continuation/v1", "stage": "decompose", "mode": "producer-rework", "authorized_through_round": 9,
                "decision_count": 1, "reason": "operator approved one more round"}


def revised_manifest(world):
    return world.manifest(decompose_manifest("go test ./cmd/...", worker="alice"))


def test_a_legacy_config_budget_increase_reworks_a_rejected_delivery_before_it_is_reviewed(world):
    item = exhausted_decompose(world, "decompose-legacy-budget")
    world.author(revised_manifest(world))
    world.review("pass")
    assigns = world.kit.record(world.store, "assign_work_item")

    result = world.run(TaskKind.DECOMPOSE, {"title": "decompose review"}, "alice", reviewers=["bob"], max_revisions=9, resume_item_id=item.id)

    assert result["verdict"] == "pass" and result["rounds"] == 9 and roles(assigns) == ["worker", "reviewer"]


def test_a_persisted_review_continuation_survives_a_process_restart_for_a_reject(world):
    item = exhausted_decompose(world, "decompose-continuation-reject", continuation=CONTINUATION)
    world.store.reset_review(item.id)
    world.store.update_status(item.id, WorkItemStatus.TODO)
    world.author(revised_manifest(world))
    world.review("pass")
    assigns = world.kit.record(world.store, "assign_work_item")

    result = world.run(TaskKind.DECOMPOSE, {"title": "decompose review"}, "alice", reviewers=["bob"], max_revisions=8, resume_item_id=item.id)

    assert result["verdict"] == "pass" and result["rounds"] == 9 and roles(assigns) == ["worker", "reviewer"]


def test_a_review_only_continuation_reviews_the_final_nits_delivery_once(world):
    item = exhausted_decompose(world, "decompose-continuation-nits", verdict=None,
                               continuation={**CONTINUATION, "mode": "review-only"})
    world.review("pass")
    assigns = world.kit.record(world.store, "assign_work_item")

    result = world.run(TaskKind.DECOMPOSE, {"title": "decompose review"}, "alice", reviewers=["bob"], max_revisions=8, resume_item_id=item.id)

    assert result["verdict"] == "pass" and result["rounds"] == 9 and roles(assigns) == ["reviewer"]


def test_the_final_pass_with_nits_can_be_continued_for_one_more_review_round(world):
    kit = world.kit
    world.author(revised_manifest(world), revised_manifest(world))
    world.review("pass-with-nits", "pass")

    with pytest.raises(NeedsDecision):
        world.run(TaskKind.DECOMPOSE, {"title": "decompose review"}, "alice", reviewers=["bob"], max_revisions=1, dag_key="decompose-p-final-nits")

    item = next(i for i in kit.store.list_work_items(kit.workspace) if i.dag_key == "decompose-p-final-nits")
    assert item.review_verdict is None and item.bounces.review == 1

    decision = plan_continue_review(kit.engine, 1, dag_key=item.dag_key, reason="operator approved final nits recheck")
    assert decision["mode"] == "review-only"
    authorized = kit.store.get_work_item(item.id)
    assert authorized.status == WorkItemStatus.IN_REVIEW and authorized.decision_required in (None, {})
    assigns = kit.record(kit.store, "assign_work_item")

    result = world.run(TaskKind.DECOMPOSE, {"title": "decompose review"}, "alice", reviewers=["bob"], max_revisions=1, resume_item_id=item.id)

    assert result["verdict"] == "pass" and result["rounds"] == 2 and roles(assigns)[-1] == "reviewer"


def test_a_consumed_review_continuation_does_not_reset_the_budget_again(world):
    item = exhausted_decompose(world, "decompose-continuation-consumed", bounce=9, continuation=CONTINUATION)
    assigns = world.kit.record(world.store, "assign_work_item")

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.DECOMPOSE, {"title": "decompose review"}, "alice", reviewers=["bob"], max_revisions=8,
                  poll=lambda: pytest.fail("a consumed authorization must not dispatch"), resume_item_id=item.id)

    assert raised.value.report["rounds"] == 9 and not assigns
    assert f"omac plan continue-review --dag-key {item.dag_key}" in str(raised.value)


def test_a_final_reject_does_not_start_an_unreviewed_producer_revision(world):
    kit = world.kit
    world.author(world.plan("计划正文-v1"), world.plan("计划正文-v2"))
    world.review("reject")

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"], max_revisions=1)

    item = kit.store.get_work_item(raised.value.report["item_id"])
    assert (item.deliverable, item.review_verdict, item.bounces.review) == ("计划正文-v1", "reject", 1)


def test_a_review_that_does_not_converge_stops_before_a_twenty_round_limit(world):
    kit = world.kit
    # nine rejected rounds are on record, each with a different blocker: the tenth Reviewer rejection exhausts the policy
    item = world.issue(TaskKind.PLAN, "plan-non-converging", contract=payload()["contract"])
    world.author(world.plan("计划正文"))
    kit.assign(item.id, "alice")
    kit.wait_runs_ended(item.id)
    kit.store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW, review_bounce=9, review_ledger=ledgers.churning(9),
                                        review_verdict="")
    kit.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    world.review("reject")

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"], max_revisions=20, resume_item_id=item.id)

    got = kit.store.get_work_item(item.id)
    assert raised.value.report["reason_code"] == "review-convergence-exhausted" and raised.value.report["rounds"] == 10
    assert got.status is WorkItemStatus.BLOCKED and got.bounces.review == 9
    assert got.decision_required["recommended_action"] == "reconsider-task-boundary" and len(got.review_ledger["cycles"]) == 10


def test_a_historical_amendment_review_binds_the_reviewed_apply_evidence(world):
    kit = world.kit
    historical = kit.store.create_work_item(kit.workspace, "historical", "completed delivery", "historical-node", "alice", reviewer="bob")
    contract = Contract(
        objective="historical delivery", source_of_truth=["docs/design.md"], acceptance=["UJ-HISTORICAL-001"],
        non_goals=["do not replay the completed delivery"], verification_commands=["pytest -q"],
        integration_gates=[{"name": "historical-gate", "layer": "L1", "delivery_goal": "preserve the completed delivery",
                            "source_of_truth": ["docs/design.md"], "covers": ["historical-node"], "acceptance_refs": ["UJ-HISTORICAL-001"],
                            "commands": ["pytest -q"]}], pr_base="main")
    kit.store.set_node_contract(historical.id, contract)
    kit.store.update_work_item_metadata(historical.id, artifacts={"pr_url": "https://example.test/pr/1"}, verification={"subject_digest": "verification-1"},
                                        review_verdict="pass", review_report={"blockers": []}, review_subject_digest="review-subject-1",
                                        review_ledger={"schema": "omac.review-ledger/v1", "rounds": []})
    kit.store.update_status(historical.id, WorkItemStatus.DONE)
    manifest = Manifest(meta={}, nodes={"historical-node": Node(id="historical-node", worker="alice", reviewer="bob", contract=contract,
                                                                  work_item_id=historical.id, status="done", merged=True)})
    amendment = {"schema": "omac.dag-amendment/v1", "reason": "correct historical acceptance responsibility", "operations": [{
        "op": "update-responsibility", "node": "historical-node", "acceptance_claims": ["UJ-HISTORICAL-001"],
        "acceptance_contributions": [{"flow_id": "UJ-HISTORICAL-001", "action_ids": ["ACT-HISTORICAL-001"]}],
        "acceptance_refs": ["UJ-HISTORICAL-001"], "clear_legacy_acceptance": True,
        "integration_gate_responsibility_patches": [{"name": "historical-gate", "acceptance_refs": ["UJ-HISTORICAL-001"]}],
        "historical_contract_correction": True, "reason": "correct metadata without replaying the completed delivery"}]}
    from omac.core.acceptance import load_acceptance_doc

    acceptance = load_acceptance_doc({"schema": "omac.acceptance/v2", "flows": [{"id": "UJ-HISTORICAL-001", "name": "historical flow", "actions": [{
        "id": "ACT-HISTORICAL-001", "kind": "business-action", "step": "complete the historical action", "how": "use the existing delivery",
        "expected": "the responsibility metadata is corrected"}]}]})
    world.kit.stack.set_scenario("charlie", [[world.amendment(yaml.safe_dump(amendment, sort_keys=False))]])
    world.review("pass")

    result = world.run(TaskKind.AMENDMENT, payload(title="historical amendment"), "charlie", reviewers=["bob"], review_acceptance_doc=acceptance,
                       review_amendment_manifest=manifest)

    amendment_item = kit.store.get_work_item(result["item_id"])
    obligation = next(e for e in amendment_item.review_obligations if e["obligation_id"] == "acceptance-responsibility:amendment-matrix")
    expected = historical_work_item_evidence_digest(kit.store.get_work_item(historical.id))
    reviewed = build_reviewed_amendment(manifest, amendment, kit.store, issue_id=amendment_item.id, reviewer_verdict="pass", acceptance=acceptance)
    assert obligation["historical_contract_corrections"][0]["evidence_sha256"] == expected
    assert reviewed["base"]["evidence_sha256"]["historical-node"] == expected
    assert reviewed["analysis"]["historical_contract_corrections"][0]["evidence_sha256"] == expected

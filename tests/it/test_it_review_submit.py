"""``omac work submit`` of a review on a real Multica: what a verdict does to the issue, and what is refused before any write."""
import itertools
import json

import pytest
import yaml

from _support.ledgers import decision_ledger, review_report
from _support.taskkit import submit_step
from omac.cli import exit_codes
from omac.cli.commands import work as work_cmd
from omac.cli.main import main
from omac.core.review_convergence import build_review_obligations
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import dispatch

_keys = itertools.count(1)


def in_review(kit, *, digest="subject-v1", obligations=True, **metadata):
    """A decompose issue the Reviewer is working on: delivered, in review, with the obligations of the review contract."""
    item = kit.store.create_work_item(
        kit.workspace, "review", "review", dag_key=f"submit-{next(_keys)}", worker="alice", reviewer="bob",
        kind=TaskKind.DECOMPOSE, initial_status=WorkItemStatus.IN_REVIEW)
    kit.store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW, deliverable="nodes: []",
                                        **({"review_subject_digest": digest} if digest else {}), **metadata)
    if obligations:
        kit.store.update_work_item_metadata(item.id, review_obligations=build_review_obligations(kit.store.get_work_item(item.id)))
    return kit.store.get_work_item(item.id)


def report_file(tmp_path, content, name="review.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(content))
    return str(path)


def submit(kit, item, verdict, path):
    return dispatch.submit(kit.store, item.id, code_host=kit.code_host, verdict=verdict, report_file=path)


def test_a_pass_with_nits_verdict_without_a_nit_is_refused_and_nothing_changes(kit, tmp_path):
    item = in_review(kit)
    path = report_file(tmp_path, review_report(item.review_obligations))
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")

    with pytest.raises(ValidationError, match="pass-with-nits verdict requires at least one non-empty nit"):
        submit(kit, item, "pass-with-nits", path)

    current = kit.store.get_work_item(item.id)
    assert not writes
    assert (current.review_verdict, current.review_report, current.worker_handoff, current.bounces.review) == (None, None, None, 0)


def test_a_review_without_the_review_protocol_is_refused_before_any_write(kit, tmp_path):
    item = in_review(kit)
    path = report_file(tmp_path, {"full_review_completed": True, "blockers": [], "nits": []})
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status")

    with pytest.raises(ValidationError, match="review_protocol"):
        submit(kit, item, "pass", path)

    current = kit.store.get_work_item(item.id)
    assert not writes and (current.review_verdict, current.review_report, current.review_ledger) == (None, None, None)


def test_a_reject_verdict_is_recorded_in_the_ledger_with_its_blocker(kit, tmp_path):
    item = in_review(kit)
    path = report_file(tmp_path, review_report(
        item.review_obligations, failed={"dimension:execution"},
        blockers=[{"root_cause_key": "command-target", "obligation_id": "dimension:execution", "classification": "new",
                   "summary": "command target is empty", "evidence": "target matches nothing",
                   "required_fix": "declare a non-empty target"}]))

    submit(kit, item, "reject", path)

    current = kit.store.get_work_item(item.id)
    assert current.review_verdict == "reject"
    assert current.review_ledger["cycles"][0]["subject_digest"] == "subject-v1" and current.review_ledger["cycles"][0]["new_count"] == 1
    assert current.review_ledger["blockers"][0]["root_cause_key"] == "command-target"


def test_a_review_of_an_issue_that_has_no_obligations_is_accepted_without_a_ledger(kit, tmp_path):
    item = in_review(kit, digest=None, obligations=False)
    path = report_file(tmp_path, {"full_review_completed": True, "blockers": [], "nits": []})

    submit(kit, item, "pass", path)

    current = kit.store.get_work_item(item.id)
    assert current.review_verdict == "pass" and current.review_ledger is None


def legacy_two_cycle_review(kit, tmp_path):
    """Two rejected cycles recorded before cycle facts existed, and a third reject repeating the same blocker."""
    ledger = decision_ledger([1, 1])
    for cycle in ledger["cycles"]:
        cycle.pop("blocker_facts_schema")
        cycle.pop("blocker_facts")
    item = in_review(kit, digest="subject-v3", review_ledger=ledger, review_bounce=2)
    blocker = ledger["blockers"][0]
    path = report_file(tmp_path, review_report(
        item.review_obligations, failed={blocker["obligation_id"]},
        prior=[{"blocker_id": blocker["blocker_id"], "status": "unchanged",
                "evidence": "the same task boundary still cannot close the blocker"}],
        blockers=[{"root_cause_key": blocker["root_cause_key"], "obligation_id": blocker["obligation_id"],
                   "classification": "unchanged", "summary": "the same blocker remains open",
                   "evidence": "the current node boundary is still insufficient",
                   "required_fix": "split the work into independently reviewable nodes"}]))
    return item, path


def snapshot(item):
    return (item.review_ledger, item.review_report, item.review_verdict, item.decision_required, item.status)


def test_a_third_submit_on_a_legacy_two_cycle_ledger_gets_the_same_decision_every_time_and_writes_nothing(kit, tmp_path):
    item, path = legacy_two_cycle_review(kit, tmp_path)
    before = snapshot(kit.store.get_work_item(item.id))
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")

    decisions = []
    for _ in range(2):
        with pytest.raises(NeedsDecision) as exc:
            submit(kit, item, "reject", path)
        decisions.append(exc.value.report)

    assert decisions[0] == decisions[1] and not writes
    assert decisions[0]["reason_code"] == "review-convergence-ledger-unverifiable"
    assert decisions[0]["convergence"]["mode"] == "unverifiable-legacy-ledger"
    assert decisions[0]["next_action"].startswith("omac dag amend propose ")
    assert snapshot(kit.store.get_work_item(item.id)) == before


def test_the_command_line_reports_the_same_decision_as_exit_20_and_writes_nothing(kit, tmp_path, capsys):
    item, path = legacy_two_cycle_review(kit, tmp_path)
    before = snapshot(kit.store.get_work_item(item.id))
    kit.monkeypatch.setattr(work_cmd, "_resolve_store", lambda: kit.store)
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")

    decisions = []
    for _ in range(2):
        assert main(["work", "submit", item.id, "--verdict", "reject", "--report-file", path]) == exit_codes.NEEDS_DECISION
        captured = capsys.readouterr()
        decisions.append(json.loads(captured.out))
        assert "omac dag amend propose" in captured.err

    assert decisions[0] == decisions[1] and decisions[0]["reason_code"] == "review-convergence-ledger-unverifiable"
    assert not writes and snapshot(kit.store.get_work_item(item.id)) == before


def test_review_cycles_of_a_plan_reach_the_ledger_through_the_real_submit(kit, tmp_path):
    from _support.taskkit import run

    plan, rules = tmp_path / "plan.md", tmp_path / "rules.md"
    plan.write_text("# Plan\n")
    rules.write_text("## Rules\n")
    deliver = submit_step(kit.stack, "--plan-file", str(plan), "--project-rules-file", str(rules))
    kit.stack.set_scenario("alice", [[deliver], [deliver]])
    kit.stack.set_scenario("bob", [[kit.stack.review("reject")], [kit.stack.review("pass")]])

    result = run(kit, TaskKind.PLAN, {"title": "plan"}, "alice", reviewers=["bob"], max_revisions=3)

    ledger = kit.store.get_work_item(result["item_id"]).review_ledger
    assert [cycle["verdict"] for cycle in ledger["cycles"]] == ["reject", "pass"]
    assert ledger["blockers"][0]["status"] == "fixed"

"""`omac work submit|block` on a real Multica: what each kind × phase accepts, what it refuses before any write, and what a submit leaves behind.

Parameter validation, the hydration table and the CLI's missing-configuration exit need no platform: tests/test_cli_work.py.
"""
import hashlib
import itertools
import json
from types import SimpleNamespace

import pytest
import yaml

from _support.taskkit import decompose_manifest
from omac.cli import exit_codes
from omac.cli.commands import work as work_cmd
from omac.cli.main import main
from omac.core.manifest import Contract, _dump_contract
from omac.core.review_convergence import REVIEW_PROTOCOL_VERSION, advance_review_ledger, build_review_obligations
from omac.core.taskmeta import TaskKind, TaskPhase, WorkerHandoffIntent
from omac.core.workitem import WorkItemPayload, WorkItemStatus
from omac.errors import AuthError, PlatformError, ValidationError
from omac.pipeline import dispatch
from omac.pipeline.dispatch import build_show_output

_keys = itertools.count(1)
PR = "https://github.com/acme/snake/pull/1"
CONTRACT = Contract(
    objective="do it", acceptance=["works"], non_goals=["no creep"], verification_commands=["pytest -q"],
    integration_gates=[{
        "name": "gate-1", "layer": "L1", "delivery_goal": "delivers", "source_of_truth": ["docs/d.md"], "covers": ["route"],
        "acceptance_refs": ["works"], "commands": ["pytest tests/int"], "required_metrics": {"route_coverage": 100},
        "artifacts": ["coverage.xml"]}],
    pr_base="feature/v1", coverage_gate=90)


def verification(pr_base="feature/v1", coverage=95):
    return {
        "commands": [{"cmd": "pytest -q", "exit_code": 0, "business_tests": [{"acceptance": "works", "test": "tests/test_feature.py::test_feature_works"}]}],
        "integration_gates": [{"name": "gate-1", "commands": [{"cmd": "pytest tests/int", "exit_code": 0}], "metrics": {"route_coverage": 100},
                               "artifacts": ["coverage.xml"], "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers"}],
        "env_setup": ["pip install -r requirements.txt", "docker compose up -d db"], "pr_base": pr_base, "coverage": coverage}


def review_report(integration_gates=True):
    report = {"review_goals": ["验收映射覆盖 contract.acceptance"], "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
              "full_review_completed": True, "acceptance_mapping": [{"acceptance": "works", "status": "pass"}], "blockers": [], "nits": []}
    if integration_gates:
        report["integration_tests_rerun"] = True
        report["integration_gate_mapping"] = [{
            "gate": "gate-1", "status": "pass", "commands": [{"cmd": "pytest tests/int", "exit_code": 0}], "metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"], "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers"}]
    return report


def make(kit, kind, *, contract=None, reviewer=None, status=WorkItemStatus.TODO, **metadata):
    item = kit.store.create_work_item(kit.workspace, "t", "d", dag_key=f"submit-{next(_keys)}", worker="alice", reviewer=reviewer, kind=kind)
    if contract is not None:
        kit.store.set_node_contract(item.id, contract)
    if metadata:
        kit.store.update_work_item_metadata(item.id, **metadata)
    if status is not WorkItemStatus.TODO:
        kit.store.update_status(item.id, status)
    return kit.store.get_work_item(item.id)


def reviewable(kit, kind=TaskKind.DEVELOP, *, contract=CONTRACT, **metadata):
    return make(kit, kind, contract=contract, reviewer="bob", status=WorkItemStatus.IN_REVIEW, phase=TaskPhase.REVIEW, **metadata)


def write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else yaml.safe_dump(content))
    return str(path)


def submit(kit, item, **params):
    return dispatch.submit(kit.store, item.id, code_host=kit.code_host, **params)


def untouched(kit, item, *fields):
    current = kit.store.get_work_item(item.id)
    return all(getattr(current, field) in (None, "", {}) for field in fields)


# ---- develop ---------------------------------------------------------------------------------------------------------------------

def test_a_develop_delivery_is_refused_whole_until_it_is_valid_and_then_lands(kit, tmp_path):
    item = make(kit, TaskKind.DEVELOP, contract=CONTRACT)
    no_business_test = verification()
    del no_business_test["commands"][0]["business_tests"]
    refusals = [
        ("missing business test for acceptance", PR, no_business_test),
        ("gate|coverage", PR, verification(coverage=50)),
    ]
    for match, url, doc in refusals:
        with pytest.raises(ValidationError, match=match):
            submit(kit, item, pr_url=url, verification_file=write(tmp_path, "v.yaml", doc))
        assert untouched(kit, item, "artifacts", "verification") and kit.store.get_work_item(item.id).status is WorkItemStatus.TODO

    kit.pr(PR, draft=True)
    with pytest.raises(ValidationError, match="(?i)draft"):
        submit(kit, item, pr_url=PR, verification_file=write(tmp_path, "v.yaml", verification()))
    assert untouched(kit, item, "artifacts", "verification")

    kit.pr(PR)
    kit.stack.gh_behavior(view="fail")
    with pytest.raises(ValidationError, match="readiness"):
        submit(kit, item, pr_url=PR, verification_file=write(tmp_path, "v.yaml", verification()))
    assert untouched(kit, item, "artifacts", "verification")
    kit.stack.gh_behavior()

    result = submit(kit, item, pr_url=PR, verification_file=write(tmp_path, "v.yaml", verification()))

    current = kit.store.get_work_item(item.id)
    assert (result.kind, result.phase, result.advanced_to) == (TaskKind.DEVELOP, TaskPhase.AUTHORING, WorkItemStatus.DONE)
    assert current.artifacts["pr_url"] == PR and current.verification["pr_base"] == "feature/v1"
    assert current.verification_ref["filename"].startswith("omac-verification") and current.status is WorkItemStatus.DONE


@pytest.mark.parametrize("actor_hints", [False, True])
def test_a_rework_submit_records_the_candidate_and_never_seals_an_identity_from_the_environment(kit, tmp_path, monkeypatch, actor_hints):
    item = make(kit, TaskKind.DEVELOP, contract=CONTRACT, reviewer="bob")
    kit.assign(item.id, "alice")
    kit.wait_for(lambda: any(run.active for run in kit.runtime.list_runs(item.id)), what="the Run")
    run = kit.runtime.list_runs(item.id)[-1]
    kit.store.update_work_item_metadata(item.id, phase=TaskPhase.AUTHORING, worker_handoff=WorkerHandoffIntent(
        schema="omac.worker-handoff/v1", state="pending", target_worker="alice", gate="review", source_review_subject_digest="subject-1",
        source_review_round=1, target_review_bounce=1, generation="handoff-generation-1", target_agent_id=kit.store.resolve_agent_id("alice"),
        baseline_direct_run_ids=(), target_run_id=run.id))
    kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    kit.pr(PR, pushes=2)
    if actor_hints:                                   # an Agent can set these itself: they prove nothing
        monkeypatch.setenv("MULTICA_AGENT_NAME", "bob")

    submit(kit, item, pr_url=PR, verification_file=write(tmp_path, "v.yaml", verification()))

    current = kit.store.get_work_item(item.id)
    assert current.delivery_identity is None
    assert current.artifacts["head_sha"] == hashlib.sha1(f"{PR}#2".encode()).hexdigest()


def test_submit_reads_only_what_the_gate_needs_before_its_first_write_through_the_library_and_the_command_line(kit, tmp_path, monkeypatch, capsys):
    item = make(kit, TaskKind.DEVELOP, contract=CONTRACT)
    events = kit.record(kit.store, "get_work_item", "update_work_item_metadata", "update_status")
    plans = []

    def unrelated_payloads_would_fail(_projection, plan):
        plans.append(frozenset(plan))
        if not [e for e in events if e[0] != "get_work_item"] and set(plan) - {WorkItemPayload.CONTRACT}:
            raise PlatformError(f"attachment download timeout: {sorted(p.value for p in plan)}")    # the gate must not need these

    kit.before(kit.store, "hydrate_work_item_evidence", unrelated_payloads_would_fail)
    path = write(tmp_path, "v.yaml", verification())

    result = submit(kit, item, pr_url="https://x/pr/1", verification_file=path)

    assert result.advanced_to is WorkItemStatus.DONE and plans[0] == frozenset({WorkItemPayload.CONTRACT})
    assert [e for e in events if e[0] == "get_work_item" and e[1][0] != item.id] == []

    second = make(kit, TaskKind.DEVELOP, contract=CONTRACT)
    monkeypatch.setattr(work_cmd, "_resolve_store", lambda: kit.store)
    del events[:], plans[:]
    capsys.readouterr()

    code = main(["work", "submit", second.id, "--pr-url", "https://x/pr/2", "--verification-file", path])

    assert code == exit_codes.OK, capsys.readouterr()
    assert plans[0] == frozenset({WorkItemPayload.CONTRACT})


@pytest.mark.parametrize("read_error", [PlatformError("attachment service unavailable"), AuthError("multica authentication expired")])
def test_a_failed_read_of_the_issue_is_a_validation_error_naming_the_cause(kit, tmp_path, monkeypatch, capsys, read_error):
    monkeypatch.setattr(work_cmd, "_resolve_store", lambda: kit.store)
    kit.fault(kit.store, "observe_work_item_control", when="before", error=read_error)
    capsys.readouterr()

    code = main(["work", "submit", "issue-1", "--pr-url", "https://x/pr/1", "--verification-file", str(tmp_path / "v.yaml")])

    error = json.loads(capsys.readouterr().err)
    assert code == exit_codes.VALIDATION and error["error"]["exit_code"] == exit_codes.VALIDATION
    assert error["error"]["message"] == f"Could not read work item 'issue-1': {read_error}"


def test_a_review_whose_target_cannot_be_read_fails_closed(kit, tmp_path):
    item = reviewable(kit, TaskKind.PLAN, contract=None, deliverable="# Plan\n", project_rules="## Rules\n")

    armed = [True]

    def deliverable_unavailable(_projection, plan):
        if armed[0] and WorkItemPayload.DELIVERABLE in plan:
            raise PlatformError("attachment download timeout: deliverable")

    kit.before(kit.store, "hydrate_work_item_evidence", deliverable_unavailable)

    with pytest.raises(PlatformError, match="deliverable"):
        submit(kit, item, verdict="pass", report_file=write(tmp_path, "r.yaml", review_report()))
    armed[0] = False

    assert kit.store.get_work_item(item.id).review_verdict in (None, "")


def test_an_issue_with_no_review_obligations_or_ledger_hydrates_empty(kit):
    item = reviewable(kit)

    hydrated, kind, phase = dispatch.load_submit_context(kit.store, item.id)

    assert (kind, phase) == (TaskKind.DEVELOP, TaskPhase.REVIEW) and not hydrated.review_obligations and not hydrated.review_ledger


def test_the_command_line_names_the_missing_pr_url(kit, tmp_path, capsys):
    item = make(kit, TaskKind.DEVELOP, contract=CONTRACT)
    capsys.readouterr()

    code = main(["work", "submit", item.id, "--verification-file", write(tmp_path, "v.yaml", "commands: []")])

    error = json.loads(capsys.readouterr().err)
    assert code == exit_codes.VALIDATION and error["ok"] is False and error["issue_id"] == item.id and "pr-url" in error["error"]["message"]


# ---- review --------------------------------------------------------------------------------------------------------------------

def test_a_review_report_must_say_the_review_was_completed_and_a_pass_records_the_report(kit, tmp_path):
    item = reviewable(kit)
    incomplete = review_report()
    del incomplete["full_review_completed"]
    with pytest.raises(ValidationError, match="full_review_completed"):
        submit(kit, item, verdict="pass", report_file=write(tmp_path, "r.yaml", incomplete))
    assert untouched(kit, item, "review_report", "review_verdict")

    submit(kit, item, verdict="pass", report_file=write(tmp_path, "r.yaml", review_report()))

    current = kit.store.get_work_item(item.id)
    assert current.review_verdict == "pass" and current.review_report["acceptance_mapping"][0]["acceptance"] == "works"
    assert current.review_report_ref["filename"].startswith("omac-review-report")


def test_the_command_line_tells_a_reviewer_the_submission_is_final_and_clears_the_old_machine_comment(kit, tmp_path, capsys):
    item = reviewable(kit, review_comment="旧机器门错误")
    capsys.readouterr()

    code = main(["work", "submit", item.id, "--verdict", "pass", "--report-file", write(tmp_path, "r.yaml", review_report())])

    out = json.loads(capsys.readouterr().out)
    assert code == exit_codes.OK and (out["ok"], out["submitted_phase"], out["next_phase"], out["deliverable_key"]) == (True, "review", None, "review_report")
    assert (out["advanced_to"], out["verdict"], out["terminal"], out["next_action"]) == ("in_review", "pass", True, "stop")
    assert "Review submission is complete" in out["message"] and "Do not add an issue comment" in out["message"]
    assert not kit.store.get_work_item(item.id).review_comment


def test_a_reject_is_a_structured_verdict_with_its_blockers(kit, tmp_path):
    item = reviewable(kit, contract=None)
    report = review_report()
    report["blockers"], report["acceptance_mapping"][0]["status"] = ["验收映射不满足"], "fail"

    result = submit(kit, item, verdict="reject", report_file=write(tmp_path, "r.yaml", report))

    current = kit.store.get_work_item(item.id)
    assert result.advanced_to is WorkItemStatus.IN_REVIEW and current.review_verdict == "reject"
    assert current.review_report["blockers"] == ["验收映射不满足"] and current.review_report_ref["filename"].startswith("omac-review-report")


def test_a_plan_review_needs_the_deliverable_and_the_project_rules_it_reviews(kit, tmp_path):
    report = write(tmp_path, "r.yaml", review_report())
    bare = reviewable(kit, TaskKind.PLAN, contract=None)
    with pytest.raises(ValidationError, match="Review target is missing"):
        submit(kit, bare, verdict="pass", report_file=report)
    without_rules = reviewable(kit, TaskKind.PLAN, contract=None, deliverable="# Design\n")
    with pytest.raises(ValidationError, match="project rules"):
        submit(kit, without_rules, verdict="pass", report_file=report)

    assert untouched(kit, bare, "review_verdict") and untouched(kit, without_rules, "review_verdict")


def test_a_reviewer_submit_is_routed_as_a_review_even_when_the_phase_lags_behind_the_status(kit, tmp_path):
    item = make(kit, TaskKind.PLAN, contract=CONTRACT)
    first = submit(kit, item, plan_file=write(tmp_path, "plan.md", "# Plan"), project_rules_file=write(tmp_path, "rules.md", "## Project rules\n"))
    assert (first.phase, first.next_phase) == (TaskPhase.AUTHORING, TaskPhase.REVIEW)
    assert kit.store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW
    kit.store.update_work_item_metadata(item.id, phase=TaskPhase.AUTHORING)       # the old loop moved status without the phase

    second = submit(kit, item, verdict="pass", report_file=write(tmp_path, "r.yaml", review_report()))

    assert second.phase is TaskPhase.REVIEW and kit.store.get_work_item(item.id).review_verdict == "pass"


# ---- review ledger generations ---------------------------------------------------------------------------------------------------

def test_a_review_never_advances_the_raw_ledger_of_a_previous_generation(kit, tmp_path, monkeypatch):
    item = reviewable(kit)
    old = {"schema": "omac.review-ledger/v1", "cycles": [{"round": 1}], "blockers": []}
    kit.store.update_work_item_metadata(
        item.id, review_generation="amendment-proj-850", review_ledger_generation="review-legacy-convergence", review_ledger=old,
        review_subject_digest="subject-new-generation", review_obligations=[{"obligation_id": "dimension:authority"}])
    observed = []
    monkeypatch.setattr(dispatch, "_validate_review", lambda *_a, **_k: {"verdict": "pass"})
    monkeypatch.setattr(dispatch, "advance_review_ledger", lambda previous, *_a, **_k: (
        observed.append(previous) or {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": []}))

    submit(kit, item, verdict="pass", report_file=write(tmp_path, "r.yaml", "verdict: pass\n"))

    assert observed == [None] and kit.store.get_work_item(item.id).review_ledger_generation == "amendment-proj-850"


def rejected(obligations, evidence):
    return {
        "review_protocol": REVIEW_PROTOCOL_VERSION, "full_review_completed": True,
        "obligation_results": [{"obligation_id": o["obligation_id"], "status": "fail" if o["obligation_id"] == "dimension:structure" else "pass",
                               "evidence": evidence} for o in obligations],
        "prior_blocker_results": [],
        "blockers": [{"root_cause_key": "same-root", "obligation_id": "dimension:structure", "classification": "new",
                      "summary": "generation-local blocker", "evidence": evidence, "required_fix": "fix this generation"}],
        "nits": [], "review_goals": ["review the current generation"], "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "acceptance_mapping": [{"acceptance": "works", "status": "fail"}]}


def test_the_first_review_of_a_new_generation_starts_a_fresh_ledger(kit, tmp_path):
    plain = Contract(objective="do it", acceptance=["works"], verification_commands=["pytest -q"], integration_gates=[], pr_base="main")
    item = make(kit, TaskKind.DEVELOP, contract=plain, reviewer="bob", status=WorkItemStatus.IN_REVIEW)
    obligations = build_review_obligations(kit.store.get_work_item(item.id))
    old_ledger = advance_review_ledger(None, rejected(obligations, "old generation evidence"), verdict="reject", subject_digest="old-subject", round_index=1)
    kit.store.update_work_item_metadata(
        item.id, phase=TaskPhase.REVIEW, review_obligations=obligations, review_ledger=old_ledger, review_generation="review-old",
        review_ledger_generation="review-old", review_subject_digest="new-subject")
    kit.store.update_work_item_metadata(
        item.id, review_generation="amendment-new", review_ledger_generation="review-old", review_verdict="", review_report={},
        decision_required={}, phase=TaskPhase.REVIEW)

    submit(kit, item, verdict="reject", report_file=write(tmp_path, "r.yaml", rejected(obligations, "new generation evidence")))

    current = kit.store.get_work_item(item.id)
    assert current.review_ledger_generation == "amendment-new" and len(current.review_ledger["cycles"]) == 1
    assert current.review_ledger["cycles"][0]["subject_digest"] == "new-subject"
    assert current.review_ledger["blockers"][0]["classification"] == "new"


def test_after_a_reset_the_worker_reads_the_persisted_obligations_from_the_ledger(kit):
    report = {"verdict": "reject", "blockers": ["缺少积分体系的持久化方案"], "nits": ["补充排行榜刷新策略"]}
    ledger = {"schema": "omac.review-ledger/v1", "cycles": [{"round": 1, "subject_digest": "review-subject-1", "verdict": "reject"}],
              "blockers": [{"blocker_id": "BLK-persistence", "obligation_id": "dimension:structure", "root_cause_key": "missing-persistence",
                            "summary": "缺少积分体系的持久化方案", "required_fix": "补充积分持久化设计", "status": "open"}]}
    item = make(kit, TaskKind.PLAN, contract=CONTRACT, phase=TaskPhase.REVIEW, review_verdict="reject", review_report=report,
                review_ledger=ledger, review_generation="review-generation-1", review_ledger_generation="review-generation-1")
    kit.store.reset_review(item.id)

    out = build_show_output(kit.store.get_work_item(item.id), "worker:alice")

    assert "previous_review" not in out["context"]
    assert out["context"]["required_closures"] == [{
        "blocker_id": "BLK-persistence", "obligation_id": "dimension:structure", "root_cause_key": "missing-persistence",
        "summary": "缺少积分体系的持久化方案", "required_fix": "补充积分持久化设计"}]
    assert out["context"]["review_ledger_ref"]["filename"].startswith("omac-review-ledger")


# ---- plan, acceptance, decompose, final acceptance --------------------------------------------------------------------------------

def test_a_plan_needs_its_project_rules_and_a_body_and_is_refused_whole(kit, tmp_path):
    item = make(kit, TaskKind.PLAN)
    plan, rules = write(tmp_path, "plan.md", "# Plan\n"), write(tmp_path, "rules.md", "## Project rules\n\n- Keep APIs backward compatible.\n")
    markers = write(tmp_path, "markers.md", "<!-- OMAC:PROJECT_RULES:START -->\ninvalid\n<!-- OMAC:PROJECT_RULES:END -->\n")
    for params, match in [({"plan_file": plan}, "project-rules"), ({"plan_file": plan, "project_rules_file": markers}, "must not contain OMAC markers"),
                          ({"plan_file": write(tmp_path, "empty.md", "   \n"), "project_rules_file": rules}, "(?i)empty|plan")]:
        with pytest.raises(ValidationError, match=match):
            submit(kit, item, **params)
        assert untouched(kit, item, "deliverable", "project_rules")
        assert kit.store.get_work_item(item.id).status is WorkItemStatus.TODO and kit.store.get_work_item(item.id).phase is TaskPhase.AUTHORING

    result = submit(kit, item, plan_file=plan, project_rules_file=rules)

    current = kit.store.get_work_item(item.id)
    assert (result.phase, result.next_phase) == (TaskPhase.AUTHORING, TaskPhase.REVIEW) and current.status is WorkItemStatus.IN_REVIEW
    assert current.deliverable.startswith("# Plan") and current.project_rules.startswith("## Project rules")


def test_the_command_line_tells_a_producer_to_stop_after_authoring(kit, tmp_path, capsys):
    item = make(kit, TaskKind.PLAN)
    capsys.readouterr()

    code = main(["work", "submit", item.id, "--plan-file", write(tmp_path, "plan.md", "# Plan\n"),
                 "--project-rules-file", write(tmp_path, "rules.md", "## Project rules\n\n- Preserve compatibility.\n")])

    out = json.loads(capsys.readouterr().out)
    assert code == exit_codes.OK and (out["submitted_phase"], out["next_phase"], out["deliverable_keys"]) == ("authoring", "review", ["plan", "project_rules"])
    assert "verdict" not in out and out["terminal"] is True and out["next_action"] == "stop"
    assert "Authoring is complete" in out["message"] and "Do not submit a verdict" in out["message"] and "wait for the OMAC loop" in out["message"]


def test_an_acceptance_document_must_have_its_schema_and_a_resubmission_clears_the_stale_review_facts(kit, tmp_path):
    item = make(kit, TaskKind.ACCEPTANCE, review_verdict="reject", review_comment="stale blocker", decision_required={"decision": "revise"},
                machine_feedback={"schema": "omac.machine-feedback/v1", "gate": "machine-gate", "error_count": 1, "errors": ["stale machine finding"]})
    with pytest.raises(ValidationError):
        submit(kit, item, acceptance_file=write(tmp_path, "bad.yaml", {"flows": [{"id": "x"}]}))     # no name or actions

    result = submit(kit, item, acceptance_file=write(tmp_path, "a.yaml", {"flows": [{"id": "login", "name": "登录", "actions": [
        {"step": "open", "how": "GET /login", "expected": "表单"}]}]}))

    current = kit.store.get_work_item(item.id)
    assert (result.phase, result.next_phase) == (TaskPhase.AUTHORING, TaskPhase.REVIEW) and "flows" in current.deliverable
    assert current.status is WorkItemStatus.IN_REVIEW and not current.review_verdict and not current.review_comment
    assert current.machine_feedback is None and current.machine_feedback_ref is None and not current.decision_required


def test_a_manifest_is_linted_against_the_workspace_agent_pool_the_command_line_supplies(kit, tmp_path, capsys):
    item = make(kit, TaskKind.DECOMPOSE)
    bad = yaml.safe_load(decompose_manifest("pytest -q"))
    bad["nodes"][0]["reviewer"] = bad["nodes"][0]["worker"]                       # a reviewer who is the worker breaks lint
    pool = set(kit.store.list_members(kit.workspace))
    with pytest.raises(ValidationError, match="lint|reviewer"):
        submit(kit, item, manifest_file=write(tmp_path, "bad.yaml", bad), agent_pool=pool)
    assert untouched(kit, item, "deliverable")

    good = yaml.safe_load(decompose_manifest("pytest -q"))
    good["nodes"][0]["reviewer"] = "bob"
    capsys.readouterr()
    code = main(["work", "submit", item.id, "--manifest-file", write(tmp_path, "good.yaml", good)])

    current = kit.store.get_work_item(item.id)
    assert code == exit_codes.OK, capsys.readouterr()
    assert current.status is WorkItemStatus.IN_REVIEW and current.deliverable is not None


def final_acceptance(kit):
    doc = {"flows": [{"id": "login", "name": "登录", "actions": [{"step": "open", "how": "GET /login", "expected": "表单"}]}]}
    gates = [{"name": "g", "layer": "L1", "delivery_goal": "d", "source_of_truth": ["s"], "covers": ["c"], "acceptance_refs": ["login"],
              "commands": ["c1"], "required_metrics": {}, "artifacts": []}]
    contract = Contract(objective="accept", non_goals=["x"], acceptance=["login"], verification_commands=["pytest -q"], integration_gates=gates,
                        pr_base="feature/v1", coverage_gate=90)
    return make(kit, TaskKind.FINAL_ACCEPTANCE, contract={**_dump_contract(contract), "acceptance_doc": doc})


def test_final_acceptance_results_must_cover_every_flow(kit, tmp_path):
    item = final_acceptance(kit)
    with pytest.raises(ValidationError, match="login"):
        submit(kit, item, acceptance_results_file=write(tmp_path, "none.yaml", []))        # the login flow is missing

    result = submit(kit, item, acceptance_results_file=write(tmp_path, "r.yaml", [{"id": "login", "status": "pass"}]))

    assert result.advanced_to is WorkItemStatus.DONE


# ---- work block ----------------------------------------------------------------------------------------------------------------

def test_a_worker_blocker_is_a_typed_decision_returned_as_exit_20_every_time_it_is_reported(kit, tmp_path, monkeypatch, capsys):
    item = make(kit, TaskKind.DEVELOP, contract=CONTRACT)
    monkeypatch.setattr(work_cmd, "_resolve_engine", lambda: SimpleNamespace(store=kit.store, runtime=kit.runtime))
    report = write(tmp_path, "blocker.yaml", {"schema": "omac.worker-blocker/v1", "reason_code": "upstream-unreadable",
                                              "upstream_issue_id": "upstream", "operation": "work-show", "exit_code": 5})
    for _ in range(2):
        assert main(["work", "block", item.id, "--report-file", report]) == 20
        result = json.loads(capsys.readouterr().out)
        assert result["terminal"] is True and result["next_action"] == "stop"

    current = kit.store.get_work_item(item.id)
    assert current.status is WorkItemStatus.BLOCKED and current.bounces.worker == 0
    assert current.decision_required["reason_code"] == "worker-precondition-blocked"

"""Proposing a DAG amendment against a real Multica: admission, resuming a reviewed amendment, and new attempts."""
import copy
import uuid
from pathlib import Path

import pytest
import yaml

import omac.pipeline.amendment as amendment_pipeline
import omac.pipeline.tasks as tasks_module
from _support.amendkit import bootstrap, contract_update, proposal, responsibility_update
from _support.deliveries import review_report_for
from _support.state import save_literal
from omac.core.amendment import build_reviewed_amendment
from omac.pipeline.amendment import apply_amendment
from omac.core.manifest import _dump_contract, load_manifest
from omac.core.review_convergence import REVIEW_PROTOCOL_VERSION, build_review_obligations
from omac.core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, ValidationError


def case(kit, tmp_path):
    """A manifest whose `bootstrap` node is blocked, a trigger report and a docs directory."""
    path, target = bootstrap(kit, tmp_path, name=f"dag-{uuid.uuid4().hex[:8]}")   # the amendment's dag key derives from the file name
    report = tmp_path / "report.md"
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    return path, target, report, docs


def propose(kit, path, report, docs, **kwargs):
    kwargs.setdefault("max_revisions", 1)
    return amendment_pipeline.propose_amendment(
        kit.engine, str(path), report_file=str(report), docs=[str(docs)], blocked_nodes=["bootstrap"],
        orchestrator="alice", reviewers=["bob"], **kwargs)


def amendment_issue(kit, title="amendment", key="amendment", **metadata):
    issue = kit.store.create_work_item(kit.workspace, title, "desc", key, "alice", reviewer="bob", kind=TaskKind.AMENDMENT)
    if metadata:
        kit.store.update_work_item_metadata(issue.id, **metadata)
    return issue


def confirmed(kit, **kwargs):
    issue = amendment_issue(kit, **kwargs)
    kit.store.update_work_item_metadata(issue.id, review_verdict="pass", phase=TaskPhase.CONFIRMATION)
    kit.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
    return issue


def failed_authoring(kit, status, key="amend-old"):
    decision = {"schema": DECISION_REQUIRED_SCHEMA, "reason_code": "completed-without-submit", "kind": "amendment",
                "phase": "authoring"}
    old = amendment_issue(kit, "failed amendment", key)
    kit.store.update_work_item_metadata(old.id, phase=TaskPhase.AUTHORING, decision_required={**decision, "resume_issue_id": old.id})
    kit.store.update_status(old.id, status)
    return old


# ---- the done node's correction needs nothing but facts ---------------------------------------------------------------------

def test_a_historical_correction_of_a_done_node_changes_only_the_responsibility_and_never_recovers_a_stage(kit, tmp_path):
    from _support.amendkit import dag_manifest

    path = tmp_path / "done.yaml"
    item = kit.store.create_work_item(kit.workspace, "baseline", "historical delivery", dag_key="baseline", worker="alice", reviewer="bob")
    kit.store.update_status(item.id, WorkItemStatus.DONE)
    save_literal(path, {"meta": {"name": "done-demo"}, "nodes": [{
        "id": "baseline", "worker": "alice", "reviewer": "bob", "blocked_by": [], "work_item_id": item.id, "status": "done",
        "merged": True, "merged_at": "2026-01-01T00:00:00Z", "merge_request_state": "merged",
        "contract": {"objective": "baseline", "source_of_truth": ["docs/design.md"], "acceptance": ["UJ-LEGACY-1"],
                     "non_goals": ["feature work"], "verification_commands": ["pytest -q"],
                     "integration_gates": [{"name": "baseline-gate", "layer": "L1", "delivery_goal": "baseline holds",
                                            "source_of_truth": ["docs/design.md"], "covers": ["baseline"],
                                            "acceptance_refs": ["UJ-LEGACY-1"], "commands": ["pytest -q"]}],
                     "pr_base": "main", "scope_paths": ["src"]}}]})
    manifest = load_manifest(str(path))
    node = manifest.nodes["baseline"]
    kit.store.set_node_contract(item.id, node.contract)
    facts_before = {f: getattr(node, f) for f in ("work_item_id", "status", "merged", "merged_at", "merge_request_state")}
    refs = ["UJ-NEW-1", "UJ-NEW-2"]
    operation = {"op": "update-responsibility", "node": node.id, "acceptance_claims": refs, "acceptance_contributions": [],
                 "acceptance_refs": refs, "clear_legacy_acceptance": True,
                 "integration_gate_responsibility_patches": [{"name": "baseline-gate", "acceptance_refs": refs}],
                 "historical_contract_correction": True, "reason": "Migrate the legacy acceptance ownership contract"}
    reviewed = build_reviewed_amendment(manifest, proposal(operation), kit.store, issue_id="amendment-issue", reviewer_verdict="pass")
    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery", lambda *_a, **_k: pytest.fail("a historical correction must not recover a Store stage"))

    first = apply_amendment(str(path), reviewed, kit.store, {node.worker, node.reviewer})

    updated = load_manifest(str(path)).nodes[node.id]
    assert {f: getattr(updated, f) for f in facts_before} == facts_before
    assert updated.contract.acceptance == [] and updated.contract.acceptance_claims == refs
    assert first["minimal_rerun"] == {"review": [], "authoring": [], "merging": []}
    assert load_manifest(str(path)).meta["amendment_apply"]["nodes"][node.id]["stage"] == "historical_contract_correction"
    second = apply_amendment(str(path), reviewed, kit.store, {node.worker, node.reviewer})
    assert second["sync"]["already_complete"] == [node.id]


# ---- admission ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("decision,message", [
    ({"reason_code": "review-convergence-scope-expanding", "convergence": {"cross_owner": False, "non_reducing_streak": 2}},
     "scope-expanding decision"),
    ({"reason_code": "reviewer-completed-without-verdict"}, "Amendment admission"),
], ids=["unqualified-scope-decision", "non-convergence-decision"])
def test_an_amendment_is_refused_before_any_issue_is_created_when_the_decision_does_not_admit_it(kit, tmp_path, decision, message):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("scope expansion")
    kit.store.update_work_item_metadata(
        target.id, decision_required={"schema": DECISION_REQUIRED_SCHEMA, "resume_issue_id": target.id, **decision})
    before = [i.id for i in kit.store.list_work_items(kit.workspace)]

    with pytest.raises(ValidationError, match=message):
        propose(kit, path, report, docs)

    assert [i.id for i in kit.store.list_work_items(kit.workspace)] == before


# ---- proposing -------------------------------------------------------------------------------------------------------------

def test_propose_hands_the_authoritative_acceptance_to_the_reviewer_obligations(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    manifest = load_manifest(str(path))
    manifest.meta["acceptance_file"] = "acceptance.yaml"
    from omac.core.manifest import save_manifest
    save_manifest(manifest, str(path))
    (tmp_path / "acceptance.yaml").write_text(yaml.safe_dump({"schema": "omac.acceptance/v2", "flows": [{
        "id": "UJ-BOOTSTRAP", "name": "bootstrap", "actions": [{
            "id": "ACT-BOOT-01", "kind": "business-action", "step": "bootstrap", "how": "run bootstrap", "expected": "workspace is ready"}]}]},
        sort_keys=False))
    report.write_text("migrate legacy acceptance ownership")
    issue, observed = confirmed(kit), {}

    def fake_run_task(*args, **kwargs):
        observed.update(description=args[2]["description"], acceptance_doc=kwargs["review_acceptance_doc"],
                        manifest=kwargs["review_amendment_manifest"])
        return {"item_id": issue.id, "delivery": {"amendment": proposal(responsibility_update())}}

    kit.monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)

    result = propose(kit, path, report, docs)

    assert observed["acceptance_doc"].business_action_ids_by_flow == {"UJ-BOOTSTRAP": ["ACT-BOOT-01"]}
    assert observed["manifest"].nodes["bootstrap"].id == "bootstrap"
    for sentence in ("clear_contract_boundary: true", "preserve only the boundary fields actually present", "omitted consumes must remain omitted"):
        assert sentence in observed["description"]
    assert Path(result["amendment_file"]).exists()


def test_resuming_a_pass_with_nits_amendment_materializes_the_reviewed_amendment_without_dispatching(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("existing amendment review report")
    persisted_contract = amendment_pipeline._contract(str(path), [str(docs)], ["bootstrap"], str(report), project_root=tmp_path)
    issue = amendment_issue(kit, key="amend-resume-nits")
    kit.store.set_node_contract(issue.id, persisted_contract)
    kit.store.update_work_item_metadata(issue.id, deliverable=yaml.safe_dump(proposal(contract_update()), sort_keys=False), phase=TaskPhase.REVIEW)
    obligations = build_review_obligations(kit.store.get_work_item(issue.id))
    review = review_report_for({"contract": _dump_contract(persisted_contract), "review_protocol": REVIEW_PROTOCOL_VERSION,
                                "review_obligations": obligations}, "pass-with-nits")
    review["nits"] = ["operator guide wording"]
    kit.store.update_work_item_metadata(issue.id, review_obligations=obligations, review_report=review,
                                        review_report_source=yaml.safe_dump(review, sort_keys=False), review_verdict="pass-with-nits")
    current = kit.store.get_work_item(issue.id)
    kit.store.update_work_item_metadata(issue.id, review_subject_digest=tasks_module._review_subject_digest(TaskKind.AMENDMENT, current, 1))
    kit.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
    assigns = kit.record(kit.store, "assign_work_item")
    output = tmp_path / "resume.amendment.yaml"

    result = propose(kit, path, report, docs, output_file=str(output), resume_issue_id=issue.id)

    reviewed = yaml.safe_load(output.read_text())
    assert result["next_action"].startswith("omac dag amend accept-nits ")
    assert (reviewed["review"]["issue_id"], reviewed["review"]["verdict"]) == (issue.id, "pass-with-nits")
    final = kit.store.get_work_item(issue.id)
    assert not assigns and (final.phase, final.review_verdict) == (TaskPhase.CONFIRMATION, "pass-with-nits")


def test_propose_forwards_the_explicit_resume_issue_id(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("resume the existing amendment issue")
    issue, observed = confirmed(kit), {}

    def fake_run_task(*_args, **kwargs):
        observed["resume_item_id"] = kwargs["resume_item_id"]
        return {"item_id": issue.id, "delivery": {"amendment": proposal(contract_update())}}

    kit.monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)

    propose(kit, path, report, docs, resume_issue_id=issue.id)

    assert observed["resume_item_id"] == issue.id


def test_restarting_authoring_requires_the_issue_to_resume(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("replace the reviewed amendment")

    with pytest.raises(ValidationError, match="--resume-issue-id"):
        propose(kit, path, report, docs, restart_authoring=True)


# ---- new attempts ----------------------------------------------------------------------------------------------------------

def fake_attempt_task(kit, *, reviewer="bob", store_attempt=True):
    """A run_task replacement that creates (or finds) the attempt's issue the way the real task would, with a passed review."""
    def fake_run_task(_engine, _kind, payload, _assignee, **kwargs):
        issue = kit.store.get_work_item(kwargs["resume_item_id"]) if kwargs.get("resume_item_id") else None
        if issue is None and kwargs.get("reuse_dag_key"):
            issue = kit.store.find_work_item_by_dag_key(kit.workspace, kwargs["dag_key"])
        if issue is None:
            issue = kit.store.create_work_item(kit.workspace, payload["title"], payload["description"], kwargs["dag_key"], "alice",
                                               reviewer=reviewer, kind=TaskKind.AMENDMENT)
            if store_attempt:
                kit.store.update_work_item_metadata(issue.id, amendment_attempt=kwargs["amendment_attempt"], source_refs=kwargs["source_refs"])
        kit.store.update_work_item_metadata(issue.id, deliverable=yaml.safe_dump(proposal(contract_update())), review_verdict="pass", phase=TaskPhase.CONFIRMATION)
        kit.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
        fake_run_task.calls.append(kwargs)
        return {"item_id": issue.id, "delivery": {"amendment": proposal(contract_update())}}
    fake_run_task.calls = []
    return fake_run_task


def test_a_new_attempt_names_the_issue_it_supersedes(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("new attempt")

    with pytest.raises(ValidationError, match="--supersedes-issue-id"):
        propose(kit, path, report, docs, new_attempt=True)


def test_a_new_attempt_is_auditable_idempotent_and_leaves_the_old_issue_alone(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("first corrected report")
    old = kit.store.create_work_item(kit.workspace, "old amendment", "old confirmation", f"amend-{path.stem}", "alice",
                                     reviewer="bob", kind=TaskKind.AMENDMENT)
    kit.store.update_work_item_metadata(old.id, deliverable="old proposal", review_verdict="pass", phase=TaskPhase.CONFIRMATION)
    kit.store.update_status(old.id, WorkItemStatus.IN_REVIEW)
    old_before = copy.deepcopy(kit.store.get_work_item(old.id))
    fake = fake_attempt_task(kit)
    kit.monkeypatch.setattr(amendment_pipeline, "run_task", fake)
    kwargs = dict(new_attempt=True, supersedes_issue_id=old.id)

    first = propose(kit, path, report, docs, **kwargs)
    second = propose(kit, path, report, docs, **kwargs)

    attempt = kit.store.get_work_item(first["issue_id"])
    assert second["issue_id"] == first["issue_id"]
    assert [c["resume_item_id"] for c in fake.calls] == [None, None] and "-attempt-" in fake.calls[0]["dag_key"]
    assert attempt.amendment_attempt["supersedes_issue_id"] == old.id
    assert attempt.amendment_attempt["report_sha256"] and attempt.amendment_attempt["docs_sha256"]
    assert attempt.source_refs == [{
        "issue_id": old.id, "issue_key": old_before.identifier, "label": f"superseded amendment {old_before.identifier}",
        "kind": "amendment", "relation": "supersedes", "report_sha256": attempt.amendment_attempt["report_sha256"],
        "docs_sha256": attempt.amendment_attempt["docs_sha256"]}]
    old_after = kit.store.get_work_item(old.id)
    assert (old_after.phase, old_after.deliverable, old_after.review_verdict) == (TaskPhase.CONFIRMATION, old_before.deliverable, old_before.review_verdict)


def test_a_new_attempt_may_not_supersede_an_amendment_that_is_still_in_progress(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("new attempt")
    old = amendment_issue(kit, "old amendment", "amend-old")

    with pytest.raises(ValidationError, match="terminal"):
        propose(kit, path, report, docs, new_attempt=True, supersedes_issue_id=old.id)


@pytest.mark.parametrize("status", [WorkItemStatus.BLOCKED, WorkItemStatus.DONE], ids=["blocked", "platform-finalized"])
def test_a_new_attempt_may_supersede_a_failed_authoring_decision_without_an_active_run(kit, tmp_path, status):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("replacement attempt")
    old = failed_authoring(kit, status)
    kit.monkeypatch.setattr(amendment_pipeline, "run_task", fake_attempt_task(kit))

    result = propose(kit, path, report, docs, new_attempt=True, supersedes_issue_id=old.id)

    old_after = kit.store.get_work_item(old.id)
    assert result["issue_id"] != old.id
    assert old_after.status == status and old_after.decision_required["reason_code"] == "completed-without-submit"


def test_a_new_attempt_is_refused_while_the_failed_attempt_still_has_an_active_run(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("replacement attempt")
    old = failed_authoring(kit, WorkItemStatus.BLOCKED)
    kit.assign(old.id, "alice")   # a Run is active on the superseded issue

    with pytest.raises(ValidationError, match="active Agent Run"):
        propose(kit, path, report, docs, new_attempt=True, supersedes_issue_id=old.id)


def test_a_superseded_amendment_needs_a_complete_decision_or_a_finished_run(kit):
    incomplete = amendment_issue(kit, "failed amendment", "amend-incomplete")
    kit.store.update_work_item_metadata(incomplete.id, phase=TaskPhase.AUTHORING, decision_required={"schema": DECISION_REQUIRED_SCHEMA})
    kit.store.update_status(incomplete.id, WorkItemStatus.BLOCKED)
    with pytest.raises(ValidationError, match="terminal amendment"):
        amendment_pipeline._validate_superseded_amendment(kit.engine, kit.store.get_work_item(incomplete.id))

    completed = amendment_issue(kit, "completed amendment", "amend-completed")
    kit.store.update_status(completed.id, WorkItemStatus.DONE)
    with pytest.raises(ValidationError, match="terminal amendment"):
        amendment_pipeline._validate_superseded_amendment(kit.engine, kit.store.get_work_item(completed.id))


def test_a_run_in_an_unknown_status_is_not_taken_for_a_finished_one(kit):
    from omac.core.workitem import AgentRunObservation

    old = failed_authoring(kit, WorkItemStatus.BLOCKED)
    kit.monkeypatch.setattr(kit.runtime, "list_runs", lambda _id: [AgentRunObservation(id="run-unknown", kind="direct", status="mystery")])

    with pytest.raises(ValidationError, match="explicitly terminal"):
        amendment_pipeline._validate_superseded_amendment(kit.engine, kit.store.get_work_item(old.id))


def test_a_different_report_gives_a_different_attempt_identity(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    old = amendment_issue(kit, "old amendment", "amend-old")
    kit.store.update_work_item_metadata(old.id, deliverable="old", review_verdict="pass", phase=TaskPhase.CONFIRMATION)
    kit.store.update_status(old.id, WorkItemStatus.IN_REVIEW)
    fake = fake_attempt_task(kit, reviewer=None)
    kit.monkeypatch.setattr(amendment_pipeline, "run_task", fake)

    for body in ("report one", "report two"):
        report.write_text(body)
        propose(kit, path, report, docs, new_attempt=True, supersedes_issue_id=old.id)

    assert fake.calls[0]["dag_key"] != fake.calls[1]["dag_key"]
    assert fake.calls[0]["amendment_attempt"]["report_sha256"] != fake.calls[1]["amendment_attempt"]["report_sha256"]


def test_an_existing_amendment_with_the_fixed_identity_teaches_the_new_attempt_option(kit, tmp_path):
    path, target, report, docs = case(kit, tmp_path)
    report.write_text("resource conflict")
    old = kit.store.create_work_item(kit.workspace, "Running DAG amendment", "old confirmation", f"amend-{path.stem}", "alice", kind=TaskKind.AMENDMENT)

    with pytest.raises(NeedsDecision) as exc:
        propose(kit, path, report, docs)

    assert exc.value.report["reason_code"] == "amendment-identity-conflict" and exc.value.report["existing_issue_id"] == old.id
    assert "--new-attempt" in exc.value.report["next_action"]


def test_responsibility_operations_stay_compact_for_145_large_contracts(kit, tmp_path):
    huge_objective = "immutable contract " + ("x" * 2048)
    raw = {
        "meta": {"name": "scale"},
        "nodes": [{
            "id": f"node-{index}",
            "worker": "alice",
            "reviewer": "bob",
            "blocked_by": [],
            "contract": {
                "objective": huge_objective,
                "source_of_truth": ["docs/design.md"],
                "acceptance": [f"UJ-{index}"],
                "non_goals": ["preserve delivery"],
                "verification_commands": ["pytest -q"],
                "integration_gates": [{
                    "name": f"gate-{index}",
                    "layer": "L1",
                    "delivery_goal": "preserve responsibility",
                    "source_of_truth": ["docs/design.md"],
                    "covers": [f"node-{index}"],
                    "acceptance_refs": [f"UJ-{index}"],
                    "commands": ["pytest -q"],
                }],
                "pr_base": "main",
            },
        } for index in range(145)],
    }
    path = tmp_path / "scale.yaml"
    save_literal(path, raw)
    operations = [{
        "op": "update-responsibility",
        "node": f"node-{index}",
        "acceptance_claims": [f"UJ-{index}"],
        "acceptance_contributions": [],
        "acceptance_refs": [f"UJ-{index}"],
        "clear_legacy_acceptance": True,
        "integration_gate_responsibility_patches": [{
            "name": f"gate-{index}", "acceptance_refs": [f"UJ-{index}"],
        }],
    } for index in range(145)]
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal(*operations), kit.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    rendered = yaml.safe_dump(reviewed, sort_keys=False)

    assert all("contract" not in operation for operation in reviewed["operations"])
    assert huge_objective not in rendered
    assert len(rendered.encode("utf-8")) < 150_000

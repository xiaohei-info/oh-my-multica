"""Applying amendments on a real Multica: runtime facts and the minimal rerun, topology changes, and the pending-apply gate."""
import copy
import json

import pytest
import yaml

import omac.core.amendment as amendment_mod
import omac.pipeline.amendment as amendment_pipeline
from _support.amendkit import (
    POOL, bootstrap, contract_update, dag_manifest, proposal, responsibility_acceptance_doc, seal_review_fixture, seed_legacy_convergence_review_control,
    stage_amendment_with_handoff, topology_manifest,
)
from omac.cli import exit_codes
from omac.cli.commands import dag as dag_cmd
from omac.cli.main import main
from omac.pipeline.amendment import apply_amendment
from omac.core.amendment import (
    build_reviewed_amendment, manifest_definition_digest, manifest_digest, validate_proposal,
)
from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import TaskKind
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import loop
from omac.pipeline.dispatch import submit


def reviewed_for(kit, path, *operations, **kwargs):
    kwargs.setdefault("reviewer_verdict", "pass")
    return build_reviewed_amendment(load_manifest(str(path)), proposal(*operations), kit.store, issue_id="amendment-issue", **kwargs)


def topology(kit, tmp_path):
    first = kit.store.create_work_item(kit.workspace, "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    kit.store.update_status(first.id, WorkItemStatus.BLOCKED)
    second = kit.store.create_work_item(kit.workspace, "dependent", "desc", "started-dependent", "charlie", reviewer="bob")
    kit.store.update_status(second.id, WorkItemStatus.IN_REVIEW)
    return topology_manifest(tmp_path, {"bootstrap": first.id, "started-dependent": second.id}), first, second


TOPOLOGY_CHANGE = {"op": "update", "node": "bootstrap", "set": {"blocked_by": ["foundation"]}}


# ---- what apply does to an issue that carries delivery facts ------------------------------------------------------------------

def test_a_contract_only_amendment_keeps_the_runtime_facts_and_resumes_review(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
                                        verification={"subject_digest": "verify-1", "commands": []}, review_verdict="reject",
                                        review_report={"blockers": ["bad mapping"]}, review_bounce=4)
    seal_review_fixture(kit.store, item.id)
    reviewed = reviewed_for(kit, path, contract_update())

    result = apply_amendment(str(path), reviewed, kit.store, POOL)

    node, got = load_manifest(str(path)).nodes["bootstrap"], kit.store.get_work_item(item.id)
    assert result["minimal_rerun"] == {"review": ["bootstrap"], "authoring": [], "merging": []}
    assert (node.work_item_id, node.status) == (item.id, "in_review") and node.contract.acceptance == ["bootstrap workspace is valid"]
    assert got.artifacts == {"pr_url": "https://example.test/pr/1", "head_sha": "abc"}
    assert got.verification == {"subject_digest": "verify-1", "commands": []}
    assert amendment_mod._load_contract(got.contract).acceptance == ["bootstrap workspace is valid"]
    assert (got.bounces.review, got.status) == (4, WorkItemStatus.IN_REVIEW)


@pytest.mark.parametrize("checkpoint", ("before_switch", "after_switch"))
def test_a_legacy_convergence_authoring_generation_is_applied_restart_safely_and_idempotently(
        kit, tmp_path, legacy_convergence_snapshot, checkpoint):
    snapshot = legacy_convergence_snapshot["work_item"]
    path, item, reviewed = stage_amendment_with_handoff(kit, tmp_path, "authoring")
    seed_legacy_convergence_review_control(kit.store, item, snapshot)
    kit.fault(kit.store, "restore_authoring_generation", when="before" if checkpoint == "before_switch" else "after",
              error=RuntimeError("crash around authoring generation switch"))

    with pytest.raises(RuntimeError, match="authoring generation switch"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    entry = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing" and entry["expected_review_generation"].startswith("amendment-")

    recovered = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    repeated = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())

    current = kit.store.get_work_item(item.id)
    entry = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]
    assert recovered["sync"]["synced"] == ["bootstrap"] and repeated["sync"]["already_complete"] == ["bootstrap"]
    assert current.review_generation == entry["expected_review_generation"]
    assert current.review_ledger == snapshot["review_ledger"] and current.review_ledger_ref is not None
    assert current.current_review_ledger is None
    assert (current.decision_required, current.review_continuation, current.worker_handoff) == (None, None, None)
    assert (current.bounces.worker, current.bounces.review) == (15, 3)
    observed = entry["observed"]
    assert observed["review_generation"] == entry["expected_review_generation"]
    assert not any(observed[key] for key in ("review_ledger_current", "decision_required_pending", "review_report_pending",
                                             "review_continuation_pending"))


def test_a_runner_restarted_after_a_legacy_convergence_amendment_spends_a_fresh_worker_budget(kit, tmp_path, legacy_convergence_snapshot):
    snapshot = legacy_convergence_snapshot["work_item"]
    path, item, reviewed = stage_amendment_with_handoff(kit, tmp_path, "authoring")
    seed_legacy_convergence_review_control(kit.store, item, snapshot)
    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))

    first = loop.tick(kit.store, kit.runtime, kit.code_host, manifest, str(path), max_parallel=1)
    second = loop.tick(kit.store, kit.runtime, kit.code_host, manifest, str(path), max_parallel=1)

    current = kit.store.get_work_item(item.id)
    assert (first.state, second.state) == ("running", "running")
    assert current.bounces.worker == 15 and current.decision_required is None
    assert current.current_review_ledger is None and current.worker_handoff is not None


def test_a_resume_that_crashed_after_the_manifest_apply_still_refuses_while_a_formal_run_is_active(kit, tmp_path):
    from _support.amendkit import accept, reviewed_resume

    path, target, amendment_file = reviewed_resume(kit, tmp_path, "amend-active-resume")
    original = amendment_mod._resume_apply_ledger
    kit.monkeypatch.setattr(amendment_mod, "_resume_apply_ledger",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("crash after manifest apply")))
    with pytest.raises(RuntimeError, match="crash after manifest apply"):
        accept(kit, path, amendment_file)
    kit.monkeypatch.setattr(amendment_mod, "_resume_apply_ledger", original)
    kit.assign(target.id, "alice")

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        accept(kit, path, amendment_file)

    assert load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "pending"
    assert kit.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


# ---- scope, drift and topology -------------------------------------------------------------------------------------------------

def test_an_implementation_scope_change_needs_authoring_and_an_explicit_ownership_migration(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    operation = contract_update()
    operation["set"]["contract"]["scope_paths"] = ["src/**"]

    errors = validate_proposal(load_manifest(str(path)), proposal(operation), POOL)
    assert any("ownership migration" in error for error in errors)

    operation["migration"] = {"ownership_transfer": True, "reason": "bootstrap now owns the source skeleton"}
    reviewed = reviewed_for(kit, path, operation)
    result = apply_amendment(str(path), reviewed, kit.store, POOL)
    manifest = load_manifest(str(path))
    assert result["minimal_rerun"]["authoring"] == ["bootstrap"] and manifest.nodes["bootstrap"].status == "todo"
    assert kit.store.get_work_item(item.id).worker_handoff is None

    dispatched = loop.tick(kit.store, kit.runtime, kit.code_host, manifest, str(path), max_parallel=1)

    handoff = kit.store.get_work_item(item.id).worker_handoff
    assert dispatched.dispatched == ["bootstrap"] and handoff is not None
    assert (handoff.gate, handoff.target_worker_bounce) == ("explicit-dispatch", 0)


def test_runtime_drift_is_rebased_but_definition_drift_fails_the_cas(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    base = load_manifest(str(path))
    seal_review_fixture(kit.store, item.id)
    reviewed = build_reviewed_amendment(base, proposal(contract_update()), kit.store, issue_id="amendment-issue", reviewer_verdict="pass")
    assert reviewed["base"]["manifest_sha256"] == manifest_digest(base)
    assert reviewed["base"]["definition_sha256"] == manifest_definition_digest(base)

    drifted = load_manifest(str(path))
    list(drifted.nodes.values())[1].status = "blocked"
    save_manifest(drifted, str(path))
    apply_amendment(str(path), copy.deepcopy(reviewed), kit.store, POOL)

    second_path = dag_manifest(tmp_path, {"bootstrap": item.id}, name="dag-definition-drift")
    drifted = load_manifest(str(second_path))
    list(drifted.nodes.values())[1].blocked_by = []
    save_manifest(drifted, str(second_path))
    with pytest.raises(ValidationError, match="definition changed"):
        apply_amendment(str(second_path), reviewed, kit.store, POOL)


def test_a_topology_change_recovers_a_started_downstream_node_but_not_an_unstarted_closure(kit, tmp_path):
    path, first, second = topology(kit, tmp_path)

    reviewed = reviewed_for(kit, path, TOPOLOGY_CHANGE)

    assert reviewed["analysis"]["derived_started_downstream"] == ["started-dependent"]
    assert reviewed["analysis"]["minimal_rerun"] == {"review": [], "authoring": ["bootstrap", "started-dependent"], "merging": []}
    result = apply_amendment(str(path), reviewed, kit.store, POOL)
    updated = load_manifest(str(path))
    assert result["minimal_rerun"]["authoring"] == ["bootstrap", "started-dependent"]
    assert [updated.nodes[n].status for n in ("bootstrap", "started-dependent", "future-dependent")] == ["todo"] * 3
    assert "future-dependent" not in result["minimal_rerun"]["authoring"]


def test_the_apply_ledger_retries_only_the_node_side_effects_that_did_not_finish(kit, tmp_path):
    path, first, second = topology(kit, tmp_path)
    reviewed = reviewed_for(kit, path, TOPOLOGY_CHANGE)
    original, failed = amendment_pipeline.prepare_stage_recovery, []

    def fail_second(node, store, stage, **kwargs):
        if node.id == "started-dependent" and not failed:
            failed.append(True)
            raise RuntimeError("simulated Store interruption")
        return original(node, store, stage, **kwargs)

    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery", fail_second)
    with pytest.raises(RuntimeError, match="Store interruption"):
        apply_amendment(str(path), reviewed, kit.store, POOL)
    interrupted = load_manifest(str(path))
    ledger = interrupted.meta["amendment_apply"]["nodes"]
    assert (ledger["bootstrap"]["state"], ledger["started-dependent"]["state"]) == ("synced", "syncing")

    kit.store.update_status(first.id, WorkItemStatus.DONE)   # the first node progressed while the apply was interrupted
    interrupted.nodes["bootstrap"].status = "done"
    save_manifest(interrupted, str(path))
    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery", original)

    result = apply_amendment(str(path), reviewed, kit.store, POOL)

    assert kit.store.get_work_item(first.id).status == WorkItemStatus.DONE and kit.store.get_work_item(second.id).status == WorkItemStatus.TODO
    assert result["sync"]["observed_progress"] == ["bootstrap"] and result["sync"]["synced"] == ["started-dependent"]
    completed = load_manifest(str(path)).meta["amendment_apply"]["nodes"]
    assert (completed["bootstrap"]["state"], completed["started-dependent"]["state"]) == ("observed_progress", "synced")


def test_a_pending_apply_blocks_all_dag_progress_until_the_same_accept_resumes(kit, tmp_path, capsys):
    path, item = bootstrap(kit, tmp_path)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
                                        verification={"subject_digest": "verify-1", "commands": []}, review_verdict="reject",
                                        review_report={"blockers": ["bad mapping"]})
    seal_review_fixture(kit.store, item.id)
    reviewed = reviewed_for(kit, path, contract_update())
    amendment_file = tmp_path / "reviewed-amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    original = amendment_pipeline.prepare_stage_recovery
    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("crash after manifest before Store compensation")))
    with pytest.raises(RuntimeError, match="before Store compensation"):
        apply_amendment(str(path), reviewed, kit.store, POOL, amendment_file=str(amendment_file))
    partial = load_manifest(str(path))
    ledger = partial.meta["amendment_apply"]
    assert ledger["amendment_id"] == reviewed["amendment_id"] and ledger["amendment_file"] == str(amendment_file)
    assert ledger["nodes"]["bootstrap"]["state"] == "syncing"

    reads = kit.record(kit.store, "get_work_item", "observe_work_item_control", "observe_work_item_controls")
    for advance in (lambda: loop.reconcile(kit.store, kit.code_host, partial, str(path)),
                    lambda: loop.tick(kit.store, kit.runtime, kit.code_host, partial, str(path))):
        with pytest.raises(NeedsDecision) as raised:
            advance()
        report = raised.value.report
        assert report["reason"] == "amendment_apply_incomplete" and report["amendment_id"] == reviewed["amendment_id"]
        assert report["incomplete_nodes"] == [{"node_id": "bootstrap", "stage": "review", "state": "syncing"}]
        assert report["resume_command"] == f"omac dag amend accept {path} {amendment_file}"
    assert not reads   # the gate runs before the Store is read

    kit.monkeypatch.setattr(dag_cmd, "_assemble_engine", lambda _args: pytest.fail("the CLI must block before assembling an engine"))
    assert main(["dag", "run", str(path), "--output", "json"]) == exit_codes.NEEDS_DECISION
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["amendment_id"] == reviewed["amendment_id"] and cli_report["resume_command"].endswith(str(amendment_file))

    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery", original)
    result = apply_amendment(str(path), reviewed, kit.store, POOL, amendment_file=str(amendment_file))

    assert result["sync"]["synced"] == ["bootstrap"]
    completed = load_manifest(str(path))
    assert all(e["state"] in {"synced", "observed_progress"} for e in completed.meta["amendment_apply"]["nodes"].values())
    loop.reconcile(kit.store, kit.code_host, completed, str(path))


def test_work_submit_accepts_a_structured_amendment_delivery(kit, tmp_path):
    item = kit.store.create_work_item(kit.workspace, "amend", "desc", "amend-demo", "alice", reviewer="bob", kind=TaskKind.AMENDMENT)
    amendment_file = tmp_path / "amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(proposal(contract_update())))

    result = submit(kit.store, item.id, code_host=kit.code_host, amendment_file=str(amendment_file), agent_pool=POOL)

    got = kit.store.get_work_item(item.id)
    assert result.deliverable_key == "amendment" and got.deliverable
    assert (got.phase.value, got.status) == ("review", WorkItemStatus.IN_REVIEW)


def test_a_tampered_minimal_rerun_is_rejected(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    reviewed = reviewed_for(kit, path, contract_update())
    reviewed["analysis"]["minimal_rerun"] = {"review": [], "authoring": [], "merging": ["bootstrap"]}

    with pytest.raises(ValidationError, match="identity does not match"):
        apply_amendment(str(path), reviewed, kit.store, POOL)

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import omac.cli.commands.dag as dag_cmd
import omac.core.amendment as amendment_mod
import omac.pipeline.amendment as amendment_pipeline
from omac.core.amendment import (
    apply_amendment,
    build_reviewed_amendment,
    manifest_definition_digest,
    manifest_digest,
    validate_proposal,
)
from omac.core.acceptance import load_acceptance_doc
from omac.core.manifest import (
    ConsumedArtifact,
    Contract,
    EvidenceMode,
    MISSING_CONSUMES,
    Node,
    ProducedArtifact,
    load_manifest,
    save_manifest,
)
from omac.core.review_convergence import review_subject_digest
from omac.core.retry_budget import bounce_log_fields, consumed_bounces
from omac.core.stage_recovery import (
    prepare_stage_recovery, recovery_control_snapshot,
)
from omac.core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity,
    TaskKind, TaskPhase,
)
from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import (
    AgentRunObservation, EngineConfig, WorkItem, WorkItemStatus,
)
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import loop
from omac.pipeline.delivery import run_merge_delivery
from omac.pipeline.dispatch import build_show_output, submit
from omac.pipeline.tasks import run_task


@pytest.mark.parametrize("stage", ["review", "merging"])
def test_review_and_merge_stage_recovery_preserve_delivery_identity(stage):
    """不启动新 Worker generation 的恢复必须保留因果交付身份。"""
    engine = create_engine(
        "mock",
        EngineConfig(engine_type="mock", workspace_id="mock-workspace"),
    )
    node = Node(id="a", worker="alice", reviewer="bob", contract=Contract())
    item = engine.store.create_work_item(
        "mock-workspace", "a", "a", dag_key="a", worker="alice",
        reviewer="bob", kind=TaskKind.DEVELOP,
    )
    node.work_item_id = item.id
    identity = DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation="generation-1",
        worker="alice",
        agent_id="agent-alice",
        run_id="run-1",
        pr_url="https://github.com/acme/repo/pull/1",
        pr_head_sha="head-1",
        verification_sha256="verification-1",
        verification_attachment_id="attachment-1",
        verification_comment_id="comment-1",
        verification_uploader_id="agent-alice",
        verification_uploader_type="agent",
        verification_task_id="run-1",
        verification_created_at="2026-07-30T01:00:00Z",
    )
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": identity.pr_url, "head_sha": identity.pr_head_sha},
        verification={"commands": []},
        verification_source="commands: []\n",
        delivery_identity=identity,
        review_verdict="pass" if stage == "merging" else "",
    )
    current = engine.store.get_work_item(item.id)
    current.verification_ref.update({
        "attachment_id": identity.verification_attachment_id,
        "comment_id": identity.verification_comment_id,
        "uploader_id": identity.verification_uploader_id,
        "uploader_type": identity.verification_uploader_type,
        "task_id": identity.verification_task_id,
        "created_at": identity.verification_created_at,
    })

    prepare_stage_recovery(node, engine.store, stage)

    assert engine.store.get_work_item(item.id).delivery_identity == identity


@pytest.mark.parametrize("stage", ["authoring", "review"])
def test_stage_recovery_retires_the_previous_assignment(stage):
    engine = create_engine(
        "mock",
        EngineConfig(engine_type="mock", workspace_id="mock-workspace"),
    )
    node = Node(id="a", worker="alice", reviewer="bob", contract=Contract())
    item = engine.store.create_work_item(
        "mock-workspace", "a", "a", dag_key="a", worker="alice",
        reviewer="bob", kind=TaskKind.DEVELOP,
    )
    node.work_item_id = item.id
    current = engine.store.get_work_item(item.id)
    current.platform_assignee_id = "agent-from-previous-stage"

    prepare_stage_recovery(node, engine.store, stage)

    recovered = engine.store.get_work_item(item.id)
    assert recovered.platform_assignee_id is None
    assert recovered.reviewer is None


def test_authoring_recovery_hides_aiteam_849_review_control_but_keeps_audit(
    aiteam_849_legacy_snapshot,
):
    """新 authoring 世代不得继续消费 amendment 前的 convergence 控制事实。"""
    snapshot = aiteam_849_legacy_snapshot["work_item"]
    engine = _engine()
    item = engine.store.create_work_item(
        "ws",
        "AITEAM-849",
        "redacted production snapshot",
        dag_key=snapshot["dag_key"],
        worker=snapshot["worker_handoff"]["target_worker"],
        reviewer="reviewer-redacted",
        kind=TaskKind(snapshot["kind"]),
        initial_status=WorkItemStatus.BLOCKED,
    )
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": "review-convergence-ledger-unverifiable",
        "kind": "develop",
        "phase": "review",
        "gate": "review-convergence",
        "rounds": 3,
        "resume_issue_id": item.id,
        "node_id": snapshot["dag_key"],
        "verdict": "reject",
        "recommended_action": "dag-amendment",
        "convergence": {
            "schema": "omac.review-convergence-decision/v1",
            "mode": "unverifiable-legacy-ledger",
            "reason_code": "review-convergence-ledger-unverifiable",
            "cycle_count": 3,
        },
    }
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        worker_bounce=15,
        review_bounce=snapshot["bounces"]["review"],
        review_verdict="reject",
        review_report={"blockers": ["legacy convergence audit"]},
        review_report_source="blockers:\n  - legacy convergence audit\n",
        review_subject_digest="subject-round-3",
        review_ledger=snapshot["review_ledger"],
        review_ledger_source=yaml.safe_dump(
            snapshot["review_ledger"], sort_keys=False),
        review_continuation={
            "schema": "omac.review-continuation/v1",
            "authorized_through_round": 6,
        },
        decision_required=decision,
        worker_handoff=snapshot["worker_handoff"],
    )
    node = Node(
        id=snapshot["dag_key"],
        worker=snapshot["worker_handoff"]["target_worker"],
        reviewer="reviewer-redacted",
        contract=Contract(objective="amended authoring contract"),
        work_item_id=item.id,
    )

    prepare_stage_recovery(node, engine.store, "authoring", sync_contract=True)

    recovered = engine.store.get_work_item(item.id)
    assert recovered.phase is TaskPhase.AUTHORING
    assert recovered.status is WorkItemStatus.TODO
    assert recovered.decision_required is None
    assert recovered.review_verdict is None
    assert recovered.review_report is None
    assert recovered.review_subject_digest is None
    assert recovered.worker_handoff is None
    assert recovered.review_ledger == snapshot["review_ledger"]
    assert recovered.review_ledger_ref is not None
    output = build_show_output(recovered, "worker:worker-redacted")
    assert output.get("ok", True) is True
    assert "decision_required" not in output["context"]
    assert "review_state" not in output["context"]
    assert "required_closures" not in output["context"]


def _engine():
    return create_engine("mock", EngineConfig(
        engine_type="mock",
        workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false"},
    ))


def _manifest(tmp_path):
    path = tmp_path / "dag.yaml"
    path.write_text(yaml.safe_dump({
        "meta": {"name": "demo", "closeout_node": "closeout"},
        "nodes": [
            {
                "id": "bootstrap",
                "worker": "alice",
                "reviewer": "bob",
                "blocked_by": [],
                "work_item_id": "1",
                "status": "blocked",
                "contract": {
                    "objective": "bootstrap",
                    "source_of_truth": ["docs/design.md"],
                    "acceptance": ["UJ-WHOLE-FLOW"],
                    "non_goals": ["feature work"],
                    "verification_commands": ["pytest -q"],
                    "integration_gates": [{
                        "name": "bootstrap",
                        "layer": "L1",
                        "delivery_goal": "workspace boots",
                        "source_of_truth": ["docs/design.md"],
                        "covers": ["bootstrap"],
                        "acceptance_refs": ["UJ-WHOLE-FLOW"],
                        "commands": ["pytest -q"],
                    }],
                    "pr_base": "main",
                    "scope_paths": ["pyproject.toml"],
                },
            },
            {
                "id": "closeout",
                "worker": "charlie",
                "reviewer": "bob",
                "blocked_by": ["bootstrap"],
                "status": "todo",
            },
        ],
    }, allow_unicode=True, sort_keys=False))
    return path


def _proposal(*operations):
    return {
        "schema": "omac.dag-amendment/v1",
        "reason": "Reviewer found an invalid acceptance mapping",
        "operations": list(operations),
    }


def _old_worker_handoff(worker="alice"):
    return {
        "schema": "omac.worker-handoff/v1",
        "state": "pending",
        "target_worker": worker,
        "gate": "review",
        "source_review_subject_digest": "old-review-subject",
        "source_review_round": 1,
        "target_review_bounce": 1,
    }


def _topology_manifest(tmp_path):
    path = tmp_path / "topology.yaml"
    path.write_text(yaml.safe_dump({
        "meta": {"name": "topology"},
        "nodes": [
            {
                "id": "foundation",
                "worker": "charlie",
                "blocked_by": [],
                "status": "done",
            },
            {
                "id": "bootstrap",
                "worker": "alice",
                "reviewer": "bob",
                "blocked_by": [],
                "work_item_id": "1",
                "status": "blocked",
                "contract": _contract_update()["set"]["contract"],
            },
            {
                "id": "started-dependent",
                "worker": "charlie",
                "reviewer": "bob",
                "blocked_by": ["bootstrap"],
                "work_item_id": "2",
                "status": "in_review",
            },
            {
                "id": "future-dependent",
                "worker": "alice",
                "reviewer": "bob",
                "blocked_by": ["started-dependent"],
                "status": "todo",
            },
        ],
    }, allow_unicode=True, sort_keys=False))
    return path


def _contract_update():
    return {
        "op": "update",
        "node": "bootstrap",
        "set": {
            "contract": {
                "objective": "bootstrap",
                "source_of_truth": ["docs/design.md"],
                "acceptance": ["bootstrap workspace is valid"],
                "non_goals": ["feature work"],
                "verification_commands": ["pytest -q"],
                "integration_gates": [{
                    "name": "bootstrap",
                    "layer": "L1",
                    "delivery_goal": "workspace boots",
                    "source_of_truth": ["docs/design.md"],
                    "covers": ["bootstrap"],
                    "acceptance_refs": ["bootstrap workspace is valid"],
                    "commands": ["pytest -q"],
                }],
                "pr_base": "main",
                "scope_paths": ["pyproject.toml"],
            },
        },
    }


def _typed_boundary_contract_update():
    operation = _contract_update()
    operation["set"]["contract"].update({
        "evidence_mode": "fixture",
        "produces": [{"artifact_id": "tooling-package"}],
        "consumes": [],
    })
    return operation


def _transitional_boundary_contract_update():
    operation = _typed_boundary_contract_update()
    operation["set"]["contract"].pop("consumes")
    return operation


def _add_typed_boundary(path):
    manifest = load_manifest(str(path))
    contract = manifest.nodes["bootstrap"].contract
    contract.evidence_mode = EvidenceMode.FIXTURE
    contract.produces = [ProducedArtifact("tooling-package")]
    contract.consumes = []
    save_manifest(manifest, str(path))


def _add_transitional_boundary(path):
    manifest = load_manifest(str(path))
    contract = manifest.nodes["bootstrap"].contract
    contract.evidence_mode = EvidenceMode.FIXTURE
    contract.produces = [ProducedArtifact("tooling-package")]
    contract.consumes = MISSING_CONSUMES
    save_manifest(manifest, str(path))


def _explicit_responsibility_update(action_id="ACT-BOOT-01"):
    operation = _contract_update()
    contract = operation["set"]["contract"]
    contract.pop("acceptance")
    contract.update({
        "acceptance_claims": ["UJ-BOOTSTRAP"],
        "acceptance_contributions": [{
            "flow_id": "UJ-BOOTSTRAP",
            "action_ids": [action_id],
        }],
        "acceptance_refs": ["UJ-BOOTSTRAP"],
    })
    return operation


def _responsibility_update(
    *, historical=False, action_id="ACT-BOOT-01", resume_stage=None,
):
    operation = {
        "op": "update-responsibility",
        "node": "bootstrap",
        "acceptance_claims": ["UJ-BOOTSTRAP"],
        "acceptance_contributions": [{
            "flow_id": "UJ-BOOTSTRAP",
            "action_ids": [action_id],
        }],
        "acceptance_refs": ["UJ-BOOTSTRAP"],
        "clear_legacy_acceptance": True,
        "integration_gate_responsibility_patches": [{
            "name": "bootstrap",
            "acceptance_refs": ["UJ-BOOTSTRAP"],
        }],
    }
    if historical:
        operation.update({
            "historical_contract_correction": True,
            "reason": "Correct legacy acceptance ownership without changing delivery facts",
        })
    if resume_stage is not None:
        operation["resume_stage"] = resume_stage
    return operation


def _responsibility_acceptance_doc():
    return load_acceptance_doc({
        "schema": "omac.acceptance/v2",
        "flows": [
            {
                "id": "UJ-BOOTSTRAP",
                "name": "bootstrap",
                "actions": [{
                    "id": "ACT-BOOT-01",
                    "kind": "business-action",
                    "step": "bootstrap",
                    "how": "run bootstrap",
                    "expected": "workspace is ready",
                }],
            },
        ],
    })


def test_complete_contract_update_preserves_and_validates_responsibility_fields(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    acceptance = _responsibility_acceptance_doc()
    proposal = _proposal(_responsibility_update())

    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=acceptance,
    )
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=acceptance,
    )

    assert result["minimal_rerun"] == {
        "review": ["bootstrap"], "authoring": [], "merging": [],
    }
    manifest_contract = load_manifest(str(path)).nodes["bootstrap"].contract
    store_contract = engine.store.get_work_item(item.id).contract
    for contract in (manifest_contract, store_contract):
        assert contract.acceptance == []
        assert contract.acceptance_claims == ["UJ-BOOTSTRAP"]
        assert contract.acceptance_contributions == [{
            "flow_id": "UJ-BOOTSTRAP", "action_ids": ["ACT-BOOT-01"],
        }]
        assert contract.acceptance_refs == ["UJ-BOOTSTRAP"]

    invalid = _proposal(_responsibility_update(action_id="UNKNOWN-ACTION"))
    errors = validate_proposal(
        load_manifest(str(_manifest(tmp_path))), invalid,
        {"alice", "bob", "charlie"}, acceptance=acceptance,
    )
    assert any("unknown business action 'UNKNOWN-ACTION'" in error for error in errors)


def test_complete_contract_replacement_requires_boundary_preservation_or_clear(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    manifest = load_manifest(str(path))

    missing = validate_proposal(
        manifest, _proposal(_contract_update()), {"alice", "bob", "charlie"})
    preserved = validate_proposal(
        manifest, _proposal(_typed_boundary_contract_update()),
        {"alice", "bob", "charlie"})

    assert any(
        "must explicitly preserve evidence_mode, produces, and consumes" in error
        for error in missing
    )
    assert preserved == []


def test_complete_replacement_preserves_actual_transitional_boundary_fields(tmp_path):
    path = _manifest(tmp_path)
    _add_transitional_boundary(path)
    manifest = load_manifest(str(path))
    preserved = validate_proposal(
        manifest, _proposal(_transitional_boundary_contract_update()),
        {"alice", "bob", "charlie"})
    missing_produces = _transitional_boundary_contract_update()
    missing_produces["set"]["contract"].pop("produces")
    errors = validate_proposal(
        manifest, _proposal(missing_produces), {"alice", "bob", "charlie"})

    assert preserved == []
    assert any(
        "preserve evidence_mode, and produces" in error
        and "consumes" not in error
        for error in errors
    )


def test_complete_replacement_rejects_explicit_null_consumes(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    operation = _typed_boundary_contract_update()
    operation["set"]["contract"]["consumes"] = None

    errors = validate_proposal(
        load_manifest(str(path)), _proposal(operation),
        {"alice", "bob", "charlie"})

    assert any("contract.consumes must be a list" in error for error in errors)


def test_apply_and_restart_fail_closed_on_tampered_null_consumes(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_typed_boundary_contract_update()),
        engine.store, issue_id="amendment-issue", reviewer_verdict="pass")
    reviewed["operations"][0]["set"]["contract"]["consumes"] = None
    reviewed["amendment_id"] = amendment_mod._amendment_id(
        reviewed["base"]["definition_sha256"], reviewed,
        reviewed["analysis"]["minimal_rerun"],
        reviewed["analysis"]["historical_contract_corrections"],
        reviewed["base"]["evidence_sha256"],
    )
    original = path.read_bytes()

    for _attempt in range(2):
        with pytest.raises(ValidationError, match="contract.consumes must be a list"):
            apply_amendment(
                str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
        assert path.read_bytes() == original
        assert load_manifest(str(path)).nodes["bootstrap"].contract.consumes == []


def test_contract_boundary_clear_expression_is_explicit_and_unambiguous(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    manifest = load_manifest(str(path))
    false_clear = _contract_update()
    false_clear["clear_contract_boundary"] = False
    mixed_clear = _typed_boundary_contract_update()
    mixed_clear["clear_contract_boundary"] = True
    resume_clear = {
        "op": "resume",
        "node": "bootstrap",
        "stage": "authoring",
        "clear_contract_boundary": True,
    }

    false_errors = validate_proposal(
        manifest, _proposal(false_clear), {"alice", "bob", "charlie"})
    mixed_errors = validate_proposal(
        manifest, _proposal(mixed_clear), {"alice", "bob", "charlie"})
    resume_errors = validate_proposal(
        manifest, _proposal(resume_clear), {"alice", "bob", "charlie"})

    assert any("must be true when present" in error for error in false_errors)
    assert any("cannot be combined" in error for error in mixed_errors)
    assert any("valid only for update operations" in error for error in resume_errors)


def test_explicit_contract_boundary_clear_survives_apply_reload_and_restart(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    operation = _contract_update()
    operation["clear_contract_boundary"] = True
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(operation), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    first = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    reloaded = load_manifest(str(path))
    second = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    contract = reloaded.nodes["bootstrap"].contract
    assert first["sync"]["synced"] == ["bootstrap"]
    assert contract.evidence_mode is None
    assert contract.produces == []
    assert contract.consumes is MISSING_CONSUMES
    assert second["sync"]["already_complete"] == ["bootstrap"]


def test_typed_contract_boundary_preservation_survives_apply_and_reload(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_typed_boundary_contract_update()),
        engine.store, issue_id="amendment-issue", reviewer_verdict="pass")

    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    contract = load_manifest(str(path)).nodes["bootstrap"].contract
    assert contract.evidence_mode is EvidenceMode.FIXTURE
    assert contract.produces == [ProducedArtifact("tooling-package")]
    assert contract.consumes == []


def test_transitional_consumes_survives_amendment_apply_reload_and_restart(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_transitional_boundary_contract_update()),
        engine.store, issue_id="amendment-issue", reviewer_verdict="pass")

    first = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    reloaded = load_manifest(str(path))
    second = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert first["sync"]["synced"] == ["bootstrap"]
    assert reloaded.nodes["bootstrap"].contract.consumes is MISSING_CONSUMES
    assert "consumes" not in amendment_mod._dump_contract(
        reloaded.nodes["bootstrap"].contract)
    assert second["sync"]["already_complete"] == ["bootstrap"]


def test_acceptance_drift_after_amendment_review_fails_closed(tmp_path):
    path = _manifest(tmp_path)
    original = path.read_bytes()
    engine = _engine()
    engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    acceptance = _responsibility_acceptance_doc()
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_responsibility_update()),
        engine.store, issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=acceptance,
    )
    changed_acceptance = copy.deepcopy(acceptance)
    changed_acceptance.flows[0].actions[0].expected = "a different authority"

    with pytest.raises(ValidationError, match="acceptance document changed"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=changed_acceptance,
        )

    assert path.read_bytes() == original


def test_done_merged_historical_responsibility_correction_preserves_facts_and_never_recovers(tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="pass",
        review_report={"blockers": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    manifest = load_manifest(str(path))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    node.merged_at = "2026-07-26T23:03:59Z"
    engine.store.set_node_contract(item.id, node.contract)
    store_before = copy.deepcopy(engine.store.get_work_item(item.id))
    runtime_before = {
        "work_item_id": node.work_item_id,
        "status": node.status,
        "merged": node.merged,
        "merged_at": node.merged_at,
        "merge_request_state": node.merge_request_state,
    }
    save_manifest(manifest, str(path))
    acceptance = _responsibility_acceptance_doc()
    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update(historical=True)), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=acceptance)

    assert reviewed["analysis"]["minimal_rerun"] == {
        "review": [], "authoring": [], "merging": [],
    }
    assert reviewed["base"]["evidence_sha256"] == {
        "bootstrap": amendment_mod.historical_work_item_evidence_digest(
            store_before),
    }
    correction = reviewed["analysis"]["historical_contract_corrections"][0]
    assert correction["node"] == "bootstrap"
    assert correction["before_contract_sha256"] != correction["after_contract_sha256"]
    assert correction["evidence_sha256"] == reviewed["base"]["evidence_sha256"]["bootstrap"]
    assert correction["allowed_field_diff"] == [
        "contract.acceptance",
        "contract.acceptance_claims",
        "contract.acceptance_contributions",
        "contract.acceptance_refs",
        "contract.integration_gates[bootstrap].acceptance_refs",
    ]
    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery",
        lambda *_args, **_kwargs: pytest.fail("historical correction must not recover Store stages"),
    )
    def reject_store_write(*_args, **_kwargs):
        pytest.fail("historical correction must not write or merge through Store")

    for method in (
        "set_node_contract", "reset_review", "prepare_review_cycle",
        "update_work_item_metadata", "update_status", "assign_work_item",
        "request_pull_request_merge", "observe_pull_request",
    ):
        monkeypatch.setattr(engine.store, method, reject_store_write)

    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=acceptance)

    updated = load_manifest(str(path)).nodes["bootstrap"]
    assert result["sync"]["already_complete"] == ["bootstrap"]
    assert {key: getattr(updated, key) for key in runtime_before} == runtime_before
    assert updated.contract.acceptance == []
    assert updated.contract.acceptance_claims == ["UJ-BOOTSTRAP"]
    assert updated.contract.acceptance_contributions == [{
        "flow_id": "UJ-BOOTSTRAP", "action_ids": ["ACT-BOOT-01"],
    }]
    assert updated.contract.acceptance_refs == ["UJ-BOOTSTRAP"]
    ledger = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]
    assert ledger["stage"] == "historical_contract_correction"
    assert ledger["state"] == "synced"
    assert ledger["store_side_effect"] == "none"
    assert ledger["before_contract_sha256"] == correction["before_contract_sha256"]
    assert ledger["after_contract_sha256"] == correction["after_contract_sha256"]
    assert ledger["before_responsibility_sha256"] == correction[
        "before_responsibility_sha256"]
    assert ledger["after_responsibility_sha256"] == correction[
        "after_responsibility_sha256"]
    assert ledger["runtime_facts_sha256"] == correction["runtime_facts_sha256"]
    assert ledger["evidence_sha256"] == correction["evidence_sha256"]
    assert ledger["allowed_field_diff"] == correction["allowed_field_diff"]
    assert ledger["reason"] == correction["reason"]
    store_after = engine.store.get_work_item(item.id)
    for field in (
        "status", "phase", "worker", "reviewer", "artifacts", "verification",
        "verification_ref", "review_verdict", "review_report", "review_report_ref",
        "review_subject_digest", "review_ledger", "review_ledger_ref", "contract",
        "contract_ref",
    ):
        assert getattr(store_after, field) == getattr(store_before, field)

    monkeypatch.setattr(
        engine.store, "get_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "repeated historical accept must not read Store"),
    )
    repeated = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    assert repeated["sync"]["already_complete"] == ["bootstrap"]


@pytest.mark.parametrize(("field", "value"), [
    ("review_verdict", "reject"),
    ("review_report", {"blockers": ["late review drift"]}),
    ("review_report_ref", {"sha256": "changed-review-report-ref"}),
    ("review_subject_digest", "changed-review-subject"),
    ("review_ledger", {"schema": "omac.review-ledger/v1", "rounds": [1]}),
    ("review_ledger_ref", {"sha256": "changed-review-ledger-ref"}),
    ("verification_ref", {"sha256": "changed-verification-ref"}),
])
def test_historical_apply_fails_closed_when_review_or_reference_evidence_drifts(
    tmp_path, field, value,
):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        verification={"subject_digest": "verification-1"},
        review_verdict="pass",
        review_report={"blockers": []},
        review_subject_digest="review-subject-1",
        review_ledger={"schema": "omac.review-ledger/v1", "rounds": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    manifest = load_manifest(str(path))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    engine.store.set_node_contract(item.id, node.contract)
    save_manifest(manifest, str(path))
    reviewed = build_reviewed_amendment(
        manifest,
        _proposal(_responsibility_update(historical=True)),
        engine.store,
        issue_id="amendment-issue",
        reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )

    setattr(engine.store.get_work_item(item.id), field, value)

    with pytest.raises(ValidationError, match="delivery evidence changed"):
        apply_amendment(
            str(path),
            reviewed,
            engine.store,
            {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    unchanged = load_manifest(str(path))
    assert unchanged.meta.get("amendment_apply") is None
    assert unchanged.meta.get("last_amendment_id") is None


def test_historical_apply_fails_closed_when_manifest_runtime_facts_drift(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    manifest = load_manifest(str(path))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    node.merged_at = "2026-07-26T23:03:59Z"
    save_manifest(manifest, str(path))
    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update(historical=True)), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc())

    drifted = load_manifest(str(path))
    drifted.nodes["bootstrap"].merged_at = "2026-07-27T00:00:00Z"
    save_manifest(drifted, str(path))

    with pytest.raises(ValidationError, match="audit changed"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc())

    unchanged = load_manifest(str(path))
    assert unchanged.meta.get("amendment_apply") is None
    assert unchanged.meta.get("last_amendment_id") is None


def test_historical_correction_audit_is_bound_into_amendment_identity(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "done"
    manifest.nodes["bootstrap"].merged = True
    save_manifest(manifest, str(path))
    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update(historical=True)), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc())
    reviewed["analysis"]["historical_contract_corrections"][0]["reason"] = "tampered"

    with pytest.raises(ValidationError, match="identity does not match"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc())


@pytest.mark.parametrize("field, value", [
    ("objective", "smuggled objective"),
    ("scope_paths", ["src/**"]),
    ("commands", ["rm -rf /"]),
    ("worker", "charlie"),
    ("blocked_by", ["closeout"]),
    ("status", "todo"),
])
def test_historical_responsibility_correction_rejects_every_non_whitelisted_field(
    tmp_path, field, value,
):
    manifest = load_manifest(str(_manifest(tmp_path)))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    operation = _responsibility_update(historical=True)
    operation[field] = value

    errors = validate_proposal(
        manifest, _proposal(operation), {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc())

    assert any("unsupported fields" in error for error in errors)


@pytest.mark.parametrize(("refs", "messages"), [
    ([{"bad": "value"}], ["entries must be non-empty strings"]),
    ([["nested"]], ["entries must be non-empty strings"]),
    ([42], ["entries must be non-empty strings"]),
    (["UJ-BOOTSTRAP", {"bad": "value"}, ["nested"]], [
        "entries must be non-empty strings",
    ]),
    (["  "], ["entries must be non-empty strings"]),
    (["UJ-BOOTSTRAP", "UJ-BOOTSTRAP"], ["must not contain duplicates"]),
    (["UJ-BOOTSTRAP", {"bad": "value"}, "UJ-BOOTSTRAP"], [
        "entries must be non-empty strings", "must not contain duplicates",
    ]),
])
def test_historical_correction_rejects_invalid_gate_refs(
    tmp_path, refs, messages,
):
    manifest = load_manifest(str(_manifest(tmp_path)))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    operation = _responsibility_update(historical=True)
    operation["integration_gate_responsibility_patches"][0]["acceptance_refs"] = refs

    errors = validate_proposal(
        manifest, _proposal(operation), {"alice", "bob", "charlie"})

    for message in messages:
        assert any(message in error for error in errors)


def test_run_task_machine_guard_bounds_invalid_gate_ref_feedback(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    operation = _responsibility_update(historical=True)
    operation["integration_gate_responsibility_patches"][0]["acceptance_refs"] = [
        "UJ-BOOTSTRAP", {"bad": "value"}, "UJ-BOOTSTRAP",
    ]
    proposal = _proposal(operation)
    MockStore.reset()
    engine = create_engine("mock", EngineConfig(
        engine_type="mock",
        workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"},
    ))
    MockStore.set_kind_delivery("amendment", {
        "amendment": yaml.safe_dump(proposal, sort_keys=False),
    })

    with pytest.raises(NeedsDecision) as exc_info:
        run_task(
            engine,
            TaskKind.AMENDMENT,
            {"title": "invalid gate refs"},
            "alice",
            max_revisions=1,
            poll=lambda: None,
            guard=lambda item: validate_proposal(
                manifest,
                item.deliverable or "",
                {"alice", "bob", "charlie"},
                acceptance=_responsibility_acceptance_doc(),
            ),
        )

    report = exc_info.value.report
    assert report["phase"] == "guard"
    assert report["rounds"] == 1
    assert "entries must be non-empty strings" in report["last_opinion"]
    assert "must not contain duplicates" in report["last_opinion"]
    item = engine.store.get_work_item(report["item_id"])
    assert item.status == WorkItemStatus.BLOCKED
    assert item.decision_required["reason_code"] == "guard-budget-exhausted"


def test_historical_responsibility_correction_rejects_noop(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    node.contract.acceptance = []
    node.contract.acceptance_claims = ["UJ-BOOTSTRAP"]
    node.contract.acceptance_contributions = [{
        "flow_id": "UJ-BOOTSTRAP", "action_ids": ["ACT-BOOT-01"],
    }]
    node.contract.acceptance_refs = ["UJ-BOOTSTRAP"]
    node.contract.integration_gates[0]["acceptance_refs"] = ["UJ-BOOTSTRAP"]

    errors = validate_proposal(
        manifest, _proposal(_responsibility_update(historical=True)),
        {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc())

    assert any("must change at least one acceptance responsibility field" in error
               for error in errors)


@pytest.mark.parametrize("consumes_policy", ("omitted", "empty", "allowlist"))
def test_historical_correction_preserves_consumes_policy(
    tmp_path, monkeypatch, consumes_policy,
):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    manifest = load_manifest(str(path))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    node.contract.evidence_mode = EvidenceMode.FIXTURE
    node.contract.produces = [ProducedArtifact("bootstrap-output")]
    if consumes_policy == "omitted":
        node.contract.consumes = MISSING_CONSUMES
    elif consumes_policy == "empty":
        node.contract.consumes = []
    else:
        manifest.nodes["producer"] = Node(
            id="producer",
            worker="charlie",
            blocked_by=[],
            status="done",
            merged=True,
            contract=Contract(
                objective="produce bootstrap input",
                source_of_truth=["docs/design.md"],
                acceptance_refs=["UJ-BOOTSTRAP"],
                non_goals=["do not own bootstrap acceptance"],
                verification_commands=["pytest -q"],
                integration_gates=[{
                    "name": "producer",
                    "layer": "L1",
                    "delivery_goal": "produce bootstrap input",
                    "source_of_truth": ["docs/design.md"],
                    "covers": ["producer"],
                    "acceptance_refs": ["UJ-BOOTSTRAP"],
                    "commands": ["pytest -q"],
                }],
                pr_base="main",
                evidence_mode=EvidenceMode.FIXTURE,
                produces=[ProducedArtifact("bootstrap-input")],
                consumes=[],
            ),
        )
        node.blocked_by = ["producer"]
        node.contract.consumes = [ConsumedArtifact(
            artifact_id="bootstrap-input",
            producer="producer",
            evidence_mode=EvidenceMode.FIXTURE,
        )]
    engine.store.set_node_contract(item.id, node.contract)
    store_before = copy.deepcopy(engine.store.get_work_item(item.id))
    save_manifest(manifest, str(path))
    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update(historical=True)), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc())
    monkeypatch.setattr(
        engine.store, "set_node_contract",
        lambda *_args, **_kwargs: pytest.fail(
            "historical correction must not publish typed contract"),
    )

    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc())

    updated = load_manifest(str(path)).nodes["bootstrap"].contract
    dumped = amendment_mod._dump_contract(updated)
    if consumes_policy == "omitted":
        assert updated.consumes is MISSING_CONSUMES
        assert "consumes" not in dumped
    elif consumes_policy == "empty":
        assert updated.consumes == []
        assert dumped["consumes"] == []
    else:
        assert updated.consumes == [ConsumedArtifact(
            artifact_id="bootstrap-input",
            producer="producer",
            evidence_mode=EvidenceMode.FIXTURE,
        )]
        assert dumped["consumes"] == [{
            "artifact_id": "bootstrap-input",
            "producer": "producer",
            "evidence_mode": "fixture",
        }]
    store_after = engine.store.get_work_item(item.id)
    assert store_after.contract == store_before.contract
    assert store_after.contract_ref == store_before.contract_ref


def test_historical_correction_requires_marker_and_evidence_cas(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "done"
    manifest.nodes["bootstrap"].merged = True
    save_manifest(manifest, str(path))
    acceptance = _responsibility_acceptance_doc()

    errors = validate_proposal(
        manifest, _proposal(_responsibility_update()), {"alice", "bob", "charlie"},
        acceptance=acceptance)
    assert any("historical_contract_correction" in error for error in errors)

    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update(historical=True)), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=acceptance)
    engine.store.update_work_item_metadata(item.id, artifacts={"head_sha": "changed"})

    with pytest.raises(ValidationError, match="delivery evidence changed"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=acceptance)


def test_responsibility_update_without_work_item_is_definition_only_but_existing_delivery_reopens_review(tmp_path):
    path = _manifest(tmp_path)
    acceptance = _responsibility_acceptance_doc()
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].work_item_id = None
    manifest.nodes["bootstrap"].status = "todo"
    save_manifest(manifest, str(path))
    engine = _engine()

    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=acceptance)
    assert reviewed["analysis"]["minimal_rerun"] == {
        "review": [], "authoring": [], "merging": [],
    }
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=acceptance)
    assert load_manifest(str(path)).nodes["bootstrap"].status == "todo"

    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_responsibility_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=acceptance)
    assert reviewed["analysis"]["minimal_rerun"]["review"] == ["bootstrap"]


def test_responsibility_resume_stage_validation_preserves_single_operation_invariant(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    acceptance = _responsibility_acceptance_doc()

    invalid = validate_proposal(
        manifest, _proposal(_responsibility_update(resume_stage="dispatch")),
        {"alice", "bob", "charlie"}, acceptance=acceptance,
    )
    assert any("resume_stage must be review, authoring, or merging" in error
               for error in invalid)

    manifest.nodes["bootstrap"].status = "done"
    manifest.nodes["bootstrap"].merged = True
    historical = validate_proposal(
        manifest, _proposal(_responsibility_update(
            historical=True, resume_stage="merging")),
        {"alice", "bob", "charlie"}, acceptance=acceptance,
    )
    assert any("historical contract correction cannot set resume_stage" in error
               for error in historical)

    duplicate = validate_proposal(
        load_manifest(str(_manifest(tmp_path))),
        _proposal(
            _responsibility_update(resume_stage="merging"),
            {"op": "resume", "node": "bootstrap", "stage": "merging"},
        ),
        {"alice", "bob", "charlie"}, acceptance=acceptance,
    )
    assert any("has multiple operations" in error for error in duplicate)


@pytest.mark.parametrize("stage", ("review", "authoring", "merging"))
def test_explicit_responsibility_resume_stage_requires_work_item_and_matches_minimal_rerun(
    tmp_path, stage,
):
    path = _manifest(tmp_path)
    acceptance = _responsibility_acceptance_doc()
    no_item = load_manifest(str(path))
    no_item.nodes["bootstrap"].work_item_id = None
    errors = validate_proposal(
        no_item,
        _proposal(_responsibility_update(resume_stage=stage)),
        {"alice", "bob", "charlie"},
        acceptance=acceptance,
    )
    assert any("explicit resume_stage requires an existing work item" in error
               for error in errors)

    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        verification={"subject_digest": "verify-1"},
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage=stage)),
        engine.store, issue_id=f"amendment-{stage}", reviewer_verdict="pass",
        acceptance=acceptance,
    )
    assert reviewed["analysis"]["minimal_rerun"] == {
        "review": ["bootstrap"] if stage == "review" else [],
        "authoring": ["bootstrap"] if stage == "authoring" else [],
        "merging": ["bootstrap"] if stage == "merging" else [],
    }


def test_responsibility_merging_rejects_missing_merge_preconditions(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        review_verdict="reject",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage="merging")),
        engine.store, issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )

    with pytest.raises(ValidationError, match="passed review and PR"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )


def _merge_ready_responsibility_amendment(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": ["pytest -q"]},
        review_verdict="pass",
        review_report={"blockers": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    old_contract = load_manifest(str(path)).nodes["bootstrap"].contract
    engine.store.set_node_contract(item.id, old_contract)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage="merging")),
        engine.store, issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )
    return path, engine, item, reviewed


def _presynced_merging_amendment_with_handoff(tmp_path):
    """Store contract 已提前同步，但仍残留真实旧 handoff 的 pure merging。"""
    path, engine, item, reviewed = _merge_ready_responsibility_amendment(
        tmp_path)
    amended = amendment_mod._apply_definition(
        load_manifest(str(path)), reviewed)
    engine.store.set_node_contract(
        item.id, amended.nodes["bootstrap"].contract)
    current = engine.store.get_work_item(item.id)
    engine.store.prepare_review_cycle(
        item.id,
        review_subject_digest(
            current, max(1, current.bounces.review + 1)),
    )
    engine.store.update_work_item_metadata(
        item.id,
        review_verdict="pass",
        review_report={"blockers": []},
        worker_handoff=_old_worker_handoff(),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    return path, engine, item, reviewed


def test_presynced_pure_merging_retires_handoff_before_reached_and_tick(
    tmp_path, monkeypatch,
):
    """预同步 contract 不能绕过 intent retirement，后续 tick 不得补 Reviewer。"""
    path, engine, item, reviewed = _presynced_merging_amendment_with_handoff(
        tmp_path)

    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    applied = load_manifest(str(path))
    entry = applied.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert result["sync"]["synced"] == ["bootstrap"]
    assert entry["state"] == "synced"
    assert entry["observed"]["worker_handoff_pending"] is False
    assert engine.store.get_work_item(item.id).worker_handoff is None

    applied.nodes["closeout"].status = "abandoned"
    save_manifest(applied, str(path))
    monkeypatch.setattr(
        loop,
        "_dispatch_reviewer_for_current_subject",
        lambda *_args, **_kwargs: pytest.fail(
            "retired handoff must not turn pure merging back to Reviewer"),
    )
    monkeypatch.setattr(
        loop, "run_merge_delivery", lambda *_args, **_kwargs: "pass")

    tick_result = loop.tick(
        engine.store, engine.runtime, applied, str(path), max_parallel=1)

    assert tick_result.state == "converged"
    assert applied.nodes["bootstrap"].status == "done"
    assert engine.store.get_work_item(item.id).status is WorkItemStatus.DONE


@pytest.mark.parametrize("checkpoint", ("before_clear", "after_clear"))
def test_presynced_merging_handoff_retirement_crash_is_idempotent(
    tmp_path, monkeypatch, checkpoint,
):
    """预同步 merging 在 clear 前/后崩溃都能重入，重复 apply 不回放副作用。"""
    path, engine, item, reviewed = _presynced_merging_amendment_with_handoff(
        tmp_path)
    original_update = engine.store.update_work_item_metadata
    crashed = False

    def crash_at_clear(item_id, **metadata):
        nonlocal crashed
        if metadata.get("worker_handoff") == {} and not crashed:
            crashed = True
            if checkpoint == "before_clear":
                raise RuntimeError("crash before presynced handoff retirement")
            original_update(item_id, **metadata)
            raise RuntimeError("crash after presynced handoff retirement")
        return original_update(item_id, **metadata)

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", crash_at_clear)
    with pytest.raises(RuntimeError, match="presynced handoff retirement"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    interrupted = load_manifest(str(path))
    assert interrupted.meta[
        "amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    assert (engine.store.get_work_item(item.id).worker_handoff is None) is (
        checkpoint == "after_clear")

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update)
    recovered = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    repeated = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    assert recovered["sync"]["synced"] == ["bootstrap"]
    assert repeated["sync"]["already_complete"] == ["bootstrap"]
    assert engine.store.get_work_item(item.id).worker_handoff is None


def test_responsibility_merging_syncs_contract_without_replaying_delivery(tmp_path, monkeypatch):
    path, engine, item, reviewed = _merge_ready_responsibility_amendment(tmp_path)
    before = copy.deepcopy(engine.store.get_work_item(item.id))
    old_contract_ref = before.contract_ref["sha256"]
    for method in (
        "reset_review", "prepare_review_cycle", "assign_work_item",
        "observe_pull_request", "request_pull_request_merge",
    ):
        monkeypatch.setattr(
            engine.store, method,
            lambda *_args, **_kwargs: pytest.fail(
                "merge-stage responsibility recovery must not replay delivery"),
        )

    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    updated = load_manifest(str(path)).nodes["bootstrap"]
    store_after = engine.store.get_work_item(item.id)
    assert result["minimal_rerun"] == {
        "review": [], "authoring": [], "merging": ["bootstrap"],
    }
    assert updated.status == "merging"
    assert store_after.contract.acceptance == []
    assert store_after.contract.acceptance_claims == ["UJ-BOOTSTRAP"]
    assert store_after.contract_ref["sha256"] != old_contract_ref
    for field in (
        "status", "phase", "worker", "reviewer", "artifacts", "verification",
        "review_verdict", "review_report", "review_subject_digest",
    ):
        assert getattr(store_after, field) == getattr(before, field)
    ledger = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]
    assert ledger["stage"] == "merging"
    assert ledger["state"] == "synced"


def test_responsibility_merging_resumes_after_manifest_write_interruption(tmp_path, monkeypatch):
    path, engine, item, reviewed = _merge_ready_responsibility_amendment(tmp_path)
    original_prepare = amendment_mod.prepare_stage_recovery
    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after manifest write")),
    )
    with pytest.raises(RuntimeError, match="after manifest write"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    partial = load_manifest(str(path))
    assert partial.nodes["bootstrap"].status == "merging"
    assert partial.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", original_prepare)

    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert result["sync"]["synced"] == ["bootstrap"]
    assert engine.store.get_work_item(item.id).contract.acceptance_claims == [
        "UJ-BOOTSTRAP"]


def test_responsibility_merging_restart_observes_published_contract_once(tmp_path, monkeypatch):
    path, engine, item, reviewed = _merge_ready_responsibility_amendment(tmp_path)
    before = copy.deepcopy(engine.store.get_work_item(item.id))
    original_set = engine.store.set_node_contract
    calls = 0

    def write_then_crash(item_id, contract):
        nonlocal calls
        calls += 1
        original_set(item_id, contract)
        raise RuntimeError("crash after Store contract publish")

    monkeypatch.setattr(engine.store, "set_node_contract", write_then_crash)
    with pytest.raises(RuntimeError, match="after Store contract publish"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    partial = load_manifest(str(path))
    assert partial.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    monkeypatch.setattr(engine.store, "set_node_contract", original_set)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    store_after = engine.store.get_work_item(item.id)
    assert calls == 1
    assert result["sync"]["synced"] == ["bootstrap"]
    assert store_after.contract.acceptance_claims == ["UJ-BOOTSTRAP"]
    assert store_after.contract_ref["sha256"] != before.contract_ref["sha256"]
    for field in (
        "status", "phase", "worker", "reviewer", "artifacts", "verification",
        "review_verdict", "review_report", "review_subject_digest",
    ):
        assert getattr(store_after, field) == getattr(before, field)
    ledger = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]
    assert ledger["state"] == "synced"


def test_responsibility_operation_stays_compact_for_145_large_contracts(tmp_path):
    huge_objective = "immutable contract " + ("x" * 8192)
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
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
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
    engine = _engine()
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(*operations), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    rendered = yaml.safe_dump(reviewed, sort_keys=False)

    assert all("contract" not in operation for operation in reviewed["operations"])
    assert huge_objective not in rendered
    assert len(rendered.encode("utf-8")) < 150_000


_OAC_MANIFEST = Path(
    "/Volumes/SSD1T/code/ai/open-agent-cluster/.omac/open-agent-cluster.yaml")


@pytest.mark.skipif(not _OAC_MANIFEST.exists(), reason="local OAC regression input is unavailable")
def test_real_oac_done_node_historical_responsibility_correction_is_facts_only(tmp_path, monkeypatch):
    raw = yaml.safe_load(_OAC_MANIFEST.read_text())
    source = next(
        node for node in raw["nodes"]
        if node["id"] == "source-ownership-baseline-contract")
    path = tmp_path / "oac.yaml"
    path.write_text(yaml.safe_dump({"meta": {"name": "oac"}, "nodes": [source]}, sort_keys=False))
    monkeypatch.chdir(_OAC_MANIFEST.parent.parent)
    manifest = load_manifest(str(path))
    node = manifest.nodes["source-ownership-baseline-contract"]
    facts_before = {
        field: getattr(node, field)
        for field in (
            "work_item_id", "status", "merged", "merged_at", "merge_request_state",
        )
    }
    item = WorkItem(
        id=node.work_item_id,
        workspace_id="ws",
        title="source ownership",
        description="historical delivery",
        status=WorkItemStatus.DONE,
        dag_key="source-ownership-baseline-contract",
    )
    item.contract = copy.deepcopy(node.contract)
    item.contract_ref = {"sha256": "historical-contract"}

    class HistoricalStore:
        config = EngineConfig(engine_type="mock", workspace_id="ws")

        def list_members(self, _workspace_id):
            return [node.worker, node.reviewer]

        def get_work_item(self, item_id):
            assert item_id == item.id
            return item

    operation = {
        "op": "update-responsibility",
        "node": node.id,
        "acceptance_claims": [
            "UJ-PLATFORM-INSTALL-001", "UJ-OAC-UPGRADE-001",
        ],
        "acceptance_contributions": [],
        "acceptance_refs": [
            "UJ-PLATFORM-INSTALL-001", "UJ-OAC-UPGRADE-001",
        ],
        "clear_legacy_acceptance": True,
        "integration_gate_responsibility_patches": [{
            "name": "source-ownership-baseline-contract-gate",
            "acceptance_refs": [
                "UJ-PLATFORM-INSTALL-001", "UJ-OAC-UPGRADE-001",
            ],
        }],
        "historical_contract_correction": True,
        "reason": "Migrate the actual OAC legacy acceptance ownership contract",
    }
    reviewed = build_reviewed_amendment(
        manifest, _proposal(operation), HistoricalStore(),
        issue_id="oac-amendment", reviewer_verdict="pass")
    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery",
        lambda *_args, **_kwargs: pytest.fail("actual OAC historical correction cannot recover a Store stage"),
    )

    first = apply_amendment(str(path), reviewed, HistoricalStore(), {node.worker, node.reviewer})
    updated = load_manifest(str(path)).nodes[node.id]
    assert {field: getattr(updated, field) for field in facts_before} == facts_before
    assert updated.contract.acceptance == []
    assert updated.contract.acceptance_claims == operation["acceptance_claims"]
    assert first["minimal_rerun"] == {"review": [], "authoring": [], "merging": []}
    assert load_manifest(str(path)).meta["amendment_apply"]["nodes"][node.id]["stage"] == (
        "historical_contract_correction")

    second = apply_amendment(str(path), reviewed, HistoricalStore(), {node.worker, node.reviewer})
    assert second["sync"]["already_complete"] == [node.id]


def test_propose_amendment_passes_authoritative_acceptance_to_reviewer_obligations(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["meta"]["acceptance_file"] = "acceptance.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    (tmp_path / "acceptance.yaml").write_text(yaml.safe_dump({
        "schema": "omac.acceptance/v2",
        "flows": [{
            "id": "UJ-BOOTSTRAP",
            "name": "bootstrap",
            "actions": [{
                "id": "ACT-BOOT-01",
                "kind": "business-action",
                "step": "bootstrap",
                "how": "run bootstrap",
                "expected": "workspace is ready",
            }],
        }],
    }, sort_keys=False))
    report = tmp_path / "report.md"
    report.write_text("migrate legacy acceptance ownership")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amendment", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        issue.id, review_verdict="pass", phase=TaskPhase.CONFIRMATION)
    engine.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
    observed = {}

    def fake_run_task(*_args, **kwargs):
        observed["description"] = _args[2]["description"]
        observed["acceptance_doc"] = kwargs["review_acceptance_doc"]
        observed["manifest"] = kwargs["review_amendment_manifest"]
        return {
            "item_id": issue.id,
            "delivery": {"amendment": _proposal(_responsibility_update())},
        }

    monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)
    result = amendment_pipeline.propose_amendment(
        engine, str(path), report_file=str(report), docs=[str(docs)],
        blocked_nodes=["bootstrap"], orchestrator="alice", reviewers=["bob"],
        max_revisions=1)

    assert observed["acceptance_doc"].business_action_ids_by_flow == {
        "UJ-BOOTSTRAP": ["ACT-BOOT-01"]}
    assert observed["manifest"].nodes["bootstrap"].id == "bootstrap"
    assert "clear_contract_boundary: true" in observed["description"]
    assert "preserve only the boundary fields actually present" in observed["description"]
    assert "omitted consumes must remain omitted" in observed["description"]
    assert Path(result["amendment_file"]).exists()


def test_propose_amendment_forwards_explicit_resume_issue_id(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("resume the existing amendment issue")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amendment", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        issue.id, review_verdict="pass", phase=TaskPhase.CONFIRMATION)
    engine.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
    observed = {}

    def fake_run_task(*_args, **kwargs):
        observed["resume_item_id"] = kwargs["resume_item_id"]
        return {
            "item_id": issue.id,
            "delivery": {"amendment": _proposal(_contract_update())},
        }

    monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)

    amendment_pipeline.propose_amendment(
        engine,
        str(path),
        report_file=str(report),
        docs=[str(docs)],
        blocked_nodes=["bootstrap"],
        orchestrator="alice",
        reviewers=["bob"],
        max_revisions=1,
        resume_issue_id=issue.id,
    )

    assert observed["resume_item_id"] == issue.id


def test_propose_amendment_restart_requires_resume_issue(tmp_path):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("replace the reviewed amendment")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")

    with pytest.raises(ValidationError, match="--resume-issue-id"):
        amendment_pipeline.propose_amendment(
            _engine(),
            str(path),
            report_file=str(report),
            docs=[str(docs)],
            blocked_nodes=["bootstrap"],
            orchestrator="alice",
            reviewers=["bob"],
            max_revisions=1,
            restart_authoring=True,
        )


def test_restart_authoring_rejects_before_any_multica_access(tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("replace the reviewed amendment")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    from omac.engines.multica import MulticaRuntime, MulticaStore
    store = MulticaStore(EngineConfig(
        engine_type="multica", workspace_id="ws", project_id="project-1"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(
        store, "_run_multica",
        lambda *_args, **_kwargs: pytest.fail("unsupported restart must not read/write Multica"),
    )
    monkeypatch.setattr(
        runtime, "wake",
        lambda *_args, **_kwargs: pytest.fail("unsupported restart must not create a Run"),
    )
    engine = SimpleNamespace(store=store, runtime=runtime)

    with pytest.raises(NeedsDecision) as exc:
        amendment_pipeline.propose_amendment(
            engine,
            str(path),
            report_file=str(report),
            docs=[str(docs)],
            blocked_nodes=["bootstrap"],
            orchestrator="alice",
            reviewers=["bob"],
            max_revisions=1,
            resume_issue_id="old-issue-id",
            restart_authoring=True,
        )

    report_payload = exc.value.report
    assert report_payload["reason_code"] == "atomic-restart-unsupported"
    assert "--new-attempt" in report_payload["next_action"]
    assert "--supersedes-issue-id old-issue-id" in report_payload["next_action"]
    assert "--resume-issue-id" not in report_payload["next_action"]


def test_new_attempt_requires_superseded_issue(tmp_path):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("new attempt")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")

    with pytest.raises(ValidationError, match="--supersedes-issue-id"):
        amendment_pipeline.propose_amendment(
            _engine(), str(path), report_file=str(report), docs=[str(docs)],
            blocked_nodes=["bootstrap"], orchestrator="alice",
            reviewers=["bob"], max_revisions=1, new_attempt=True,
        )


def test_new_attempt_is_auditable_idempotent_and_preserves_old_issue(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("first corrected report")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "old amendment", "old confirmation", f"amend-{path.stem}", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    old.identifier = "AITEAM-811"
    engine.store.update_work_item_metadata(
        old.id, deliverable="old proposal", review_verdict="pass",
        phase=TaskPhase.CONFIRMATION)
    engine.store.update_status(old.id, WorkItemStatus.IN_REVIEW)
    old_before = copy.deepcopy(engine.store.get_work_item(old.id))
    observed = []

    def fake_run_task(_engine, _kind, payload, _assignee, **kwargs):
        observed.append(kwargs)
        issue = (
            engine.store.get_work_item(kwargs["resume_item_id"])
            if kwargs["resume_item_id"] else None
        )
        if issue is None and kwargs["reuse_dag_key"]:
            issue = engine.store.find_work_item_by_dag_key(
                "ws", kwargs["dag_key"])
        if issue is None:
            issue = engine.store.create_work_item(
                "ws", payload["title"], payload["description"],
                kwargs["dag_key"], "alice", reviewer="bob",
                kind=TaskKind.AMENDMENT)
            engine.store.update_work_item_metadata(
                issue.id,
                amendment_attempt=kwargs["amendment_attempt"],
                source_refs=kwargs["source_refs"],
            )
        engine.store.update_work_item_metadata(
            issue.id, deliverable=_proposal(_contract_update()),
            review_verdict="pass", phase=TaskPhase.CONFIRMATION)
        engine.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
        return {
            "item_id": issue.id,
            "delivery": {"amendment": _proposal(_contract_update())},
        }

    monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)
    kwargs = dict(
        report_file=str(report), docs=[str(docs)],
        blocked_nodes=["bootstrap"], orchestrator="alice",
        reviewers=["bob"], max_revisions=1, new_attempt=True,
        supersedes_issue_id=old.id,
    )

    first = amendment_pipeline.propose_amendment(engine, str(path), **kwargs)
    second = amendment_pipeline.propose_amendment(engine, str(path), **kwargs)
    attempt_issue = engine.store.get_work_item(first["issue_id"])

    assert second["issue_id"] == first["issue_id"]
    assert observed[0]["resume_item_id"] is None
    assert observed[1]["resume_item_id"] is None
    assert "-attempt-" in observed[0]["dag_key"]
    assert attempt_issue.amendment_attempt["supersedes_issue_id"] == old.id
    assert attempt_issue.amendment_attempt["report_sha256"]
    assert attempt_issue.amendment_attempt["docs_sha256"]
    assert attempt_issue.source_refs == [{
        "issue_id": old.id,
        "issue_key": "AITEAM-811",
        "label": "superseded amendment AITEAM-811",
        "kind": "amendment",
        "relation": "supersedes",
        "report_sha256": attempt_issue.amendment_attempt["report_sha256"],
        "docs_sha256": attempt_issue.amendment_attempt["docs_sha256"],
    }]
    old_after = engine.store.get_work_item(old.id)
    assert old_after.phase == old_before.phase == TaskPhase.CONFIRMATION
    assert old_after.deliverable == old_before.deliverable
    assert old_after.review_verdict == old_before.review_verdict


def test_new_attempt_rejects_non_terminal_superseded_amendment(tmp_path):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("new attempt")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "old amendment", "still authoring", "amend-old", "alice",
        kind=TaskKind.AMENDMENT)

    with pytest.raises(ValidationError, match="terminal"):
        amendment_pipeline.propose_amendment(
            engine, str(path), report_file=str(report), docs=[str(docs)],
            blocked_nodes=["bootstrap"], orchestrator="alice",
            reviewers=["bob"], max_revisions=1, new_attempt=True,
            supersedes_issue_id=old.id)


def test_new_attempt_allows_blocked_decision_required_without_active_run(
        tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("replacement attempt")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "failed amendment", "authority conflict", "amend-old", "alice",
        kind=TaskKind.AMENDMENT)
    old.identifier = "AITEAM-843"
    engine.store.update_work_item_metadata(
        old.id,
        phase=TaskPhase.AUTHORING,
        decision_required={
            "schema": DECISION_REQUIRED_SCHEMA,
            "reason_code": "completed-without-submit",
            "kind": "amendment",
            "phase": "authoring",
            "resume_issue_id": old.id,
        },
    )
    engine.store.update_status(old.id, WorkItemStatus.BLOCKED)

    def fake_run_task(_engine, _kind, payload, _assignee, **kwargs):
        issue = engine.store.create_work_item(
            "ws", payload["title"], payload["description"], kwargs["dag_key"],
            "alice", reviewer="bob", kind=TaskKind.AMENDMENT)
        engine.store.update_work_item_metadata(
            issue.id,
            amendment_attempt=kwargs["amendment_attempt"],
            source_refs=kwargs["source_refs"],
            deliverable=_proposal(_contract_update()),
            review_verdict="pass",
            phase=TaskPhase.CONFIRMATION,
        )
        engine.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
        return {"item_id": issue.id, "delivery": {
            "amendment": _proposal(_contract_update())}}

    monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)

    result = amendment_pipeline.propose_amendment(
        engine, str(path), report_file=str(report), docs=[str(docs)],
        blocked_nodes=["bootstrap"], orchestrator="alice",
        reviewers=["bob"], max_revisions=1, new_attempt=True,
        supersedes_issue_id=old.id)

    assert result["issue_id"] != old.id
    old_after = engine.store.get_work_item(old.id)
    assert old_after.status == WorkItemStatus.BLOCKED
    assert old_after.decision_required["reason_code"] == "completed-without-submit"


def test_new_attempt_allows_platform_finalized_decision_required_without_active_run(
        tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("replacement attempt")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "failed amendment", "authority conflict", "amend-old", "alice",
        kind=TaskKind.AMENDMENT)
    old.identifier = "AITEAM-843"
    engine.store.update_work_item_metadata(
        old.id,
        phase=TaskPhase.AUTHORING,
        decision_required={
            "schema": DECISION_REQUIRED_SCHEMA,
            "reason_code": "completed-without-submit",
            "kind": "amendment",
            "phase": "authoring",
            "resume_issue_id": old.id,
        },
    )
    engine.store.update_status(old.id, WorkItemStatus.DONE)

    def fake_run_task(_engine, _kind, payload, _assignee, **kwargs):
        issue = engine.store.create_work_item(
            "ws", payload["title"], payload["description"], kwargs["dag_key"],
            "alice", reviewer="bob", kind=TaskKind.AMENDMENT)
        engine.store.update_work_item_metadata(
            issue.id,
            amendment_attempt=kwargs["amendment_attempt"],
            source_refs=kwargs["source_refs"],
            deliverable=_proposal(_contract_update()),
            review_verdict="pass",
            phase=TaskPhase.CONFIRMATION,
        )
        engine.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
        return {"item_id": issue.id, "delivery": {
            "amendment": _proposal(_contract_update())}}

    monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)

    result = amendment_pipeline.propose_amendment(
        engine, str(path), report_file=str(report), docs=[str(docs)],
        blocked_nodes=["bootstrap"], orchestrator="alice",
        reviewers=["bob"], max_revisions=1, new_attempt=True,
        supersedes_issue_id=old.id)

    assert result["issue_id"] != old.id
    old_after = engine.store.get_work_item(old.id)
    assert old_after.status == WorkItemStatus.DONE
    assert old_after.decision_required["reason_code"] == "completed-without-submit"


def test_new_attempt_rejects_blocked_attempt_with_active_run(tmp_path):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("replacement attempt")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "failed amendment", "authority conflict", "amend-old", "alice",
        kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        old.id,
        phase=TaskPhase.AUTHORING,
        decision_required={
            "schema": DECISION_REQUIRED_SCHEMA,
            "reason_code": "completed-without-submit",
            "kind": "amendment",
            "phase": "authoring",
            "resume_issue_id": old.id,
        },
    )
    engine.store.update_status(old.id, WorkItemStatus.BLOCKED)
    engine.store.assign_work_item(old.id, "alice", "worker")

    with pytest.raises(ValidationError, match="active Agent Run"):
        amendment_pipeline.propose_amendment(
            engine, str(path), report_file=str(report), docs=[str(docs)],
            blocked_nodes=["bootstrap"], orchestrator="alice",
            reviewers=["bob"], max_revisions=1, new_attempt=True,
            supersedes_issue_id=old.id)


def test_new_attempt_rejects_incomplete_decision_required(tmp_path):
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "failed amendment", "authority conflict", "amend-old", "alice",
        kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        old.id,
        phase=TaskPhase.AUTHORING,
        decision_required={"schema": DECISION_REQUIRED_SCHEMA},
    )
    engine.store.update_status(old.id, WorkItemStatus.BLOCKED)

    with pytest.raises(ValidationError, match="terminal amendment"):
        amendment_pipeline._validate_superseded_amendment(
            engine, engine.store.get_work_item(old.id))


def test_new_attempt_rejects_done_without_decision_required():
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "completed amendment", "no failed-closed decision",
        "amend-old", "alice", kind=TaskKind.AMENDMENT)
    engine.store.update_status(old.id, WorkItemStatus.DONE)

    with pytest.raises(ValidationError, match="terminal amendment"):
        amendment_pipeline._validate_superseded_amendment(
            engine, engine.store.get_work_item(old.id))


def test_new_attempt_rejects_unknown_run_status(tmp_path, monkeypatch):
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "failed amendment", "authority conflict", "amend-old", "alice",
        kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        old.id,
        phase=TaskPhase.AUTHORING,
        decision_required={
            "schema": DECISION_REQUIRED_SCHEMA,
            "reason_code": "completed-without-submit",
            "kind": "amendment",
            "phase": "authoring",
            "resume_issue_id": old.id,
        },
    )
    engine.store.update_status(old.id, WorkItemStatus.BLOCKED)
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [
        AgentRunObservation(
            id="run-unknown", kind="direct", status="mystery"),
    ])

    with pytest.raises(ValidationError, match="explicitly terminal"):
        amendment_pipeline._validate_superseded_amendment(
            engine, engine.store.get_work_item(old.id))


def test_different_report_digest_creates_different_attempt_identity(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "old amendment", "old confirmation", "amend-old", "alice",
        kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        old.id, deliverable="old", review_verdict="pass",
        phase=TaskPhase.CONFIRMATION)
    engine.store.update_status(old.id, WorkItemStatus.IN_REVIEW)
    identities = []

    def fake_run_task(_engine, _kind, payload, _assignee, **kwargs):
        identities.append((kwargs["dag_key"], kwargs["amendment_attempt"]))
        issue = engine.store.create_work_item(
            "ws", payload["title"], payload["description"], kwargs["dag_key"],
            "alice", kind=TaskKind.AMENDMENT)
        engine.store.update_work_item_metadata(
            issue.id, deliverable=_proposal(_contract_update()),
            review_verdict="pass", phase=TaskPhase.CONFIRMATION,
            amendment_attempt=kwargs["amendment_attempt"])
        engine.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
        return {"item_id": issue.id, "delivery": {
            "amendment": _proposal(_contract_update())}}

    monkeypatch.setattr(amendment_pipeline, "run_task", fake_run_task)
    for body in ("report one", "report two"):
        report.write_text(body)
        amendment_pipeline.propose_amendment(
            engine, str(path), report_file=str(report), docs=[str(docs)],
            blocked_nodes=["bootstrap"], orchestrator="alice",
            reviewers=["bob"], max_revisions=1, new_attempt=True,
            supersedes_issue_id=old.id)

    assert identities[0][0] != identities[1][0]
    assert identities[0][1]["report_sha256"] != identities[1][1]["report_sha256"]


def test_docs_digest_is_recursive_order_independent_and_content_bound(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    docs_a = project_root / "docs-a"
    nested = docs_a / "nested"
    nested.mkdir(parents=True)
    (docs_a / "overview.md").write_text("overview")
    detail = nested / "detail.md"
    detail.write_text("detail v1")
    docs_b = project_root / "single.md"
    docs_b.write_text("single")

    first = amendment_pipeline._docs_snapshot(
        [str(docs_a), str(docs_b)], project_root=project_root)
    reordered = amendment_pipeline._docs_snapshot(
        [str(docs_b), str(docs_a)], project_root=project_root)

    assert reordered == first
    assert any(path.endswith("nested/detail.md") for path in first["docs_files"])

    issue = SimpleNamespace(id="old", identifier="AITEAM-811")
    manifest = _manifest(project_root)
    first_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=first,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    reordered_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=reordered,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    assert reordered_attempt["attempt_id"] == first_attempt["attempt_id"]

    detail.write_text("detail v2")
    changed = amendment_pipeline._docs_snapshot(
        [str(docs_a), str(docs_b)], project_root=project_root)
    changed_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=changed,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    assert changed["docs_sha256"] != first["docs_sha256"]
    assert changed_attempt["attempt_id"] != first_attempt["attempt_id"]


def test_docs_digest_uses_only_git_tracked_files_for_revision_snapshot(tmp_path):
    project_root = tmp_path / "project"
    docs = project_root / "docs"
    docs.mkdir(parents=True)
    (docs / "design.md").write_text("authoritative design")
    (docs / ".DS_Store").write_bytes(b"local desktop metadata")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project_root, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project_root, check=True)
    subprocess.run(
        ["git", "add", "docs/design.md"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "authoritative docs"],
        cwd=project_root, check=True)

    snapshot = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=project_root)

    assert snapshot["docs_files"] == ["docs/design.md"]


def test_docs_digest_treats_git_directory_path_as_literal(tmp_path):
    project_root = tmp_path / "project"
    selected = project_root / "docs*"
    leaked = project_root / "docs-other"
    selected.mkdir(parents=True)
    leaked.mkdir()
    (selected / "design.md").write_text("selected")
    (leaked / "leak.md").write_text("must not leak")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "."], cwd=project_root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "commit", "-qm", "authoritative docs"],
        cwd=project_root, check=True)

    snapshot = amendment_pipeline._docs_snapshot(
        [str(selected)], project_root=project_root)

    assert snapshot["docs_files"] == ["docs*/design.md"]


def test_docs_digest_reads_revision_blob_instead_of_dirty_worktree(tmp_path):
    project_root = tmp_path / "project"
    docs = project_root / "docs"
    docs.mkdir(parents=True)
    design = docs / "design.md"
    design.write_text("revision v1")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "docs/design.md"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "commit", "-qm", "authoritative docs"],
        cwd=project_root, check=True)
    expected = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=project_root)

    design.write_text("dirty worktree v2")
    actual = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=project_root)

    assert actual == expected


@pytest.mark.parametrize("link_at_root", [False, True])
def test_docs_digest_rejects_symlinks_and_path_escape(tmp_path, link_at_root):
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    docs = tmp_path / "docs"
    docs.mkdir()
    if link_at_root:
        root = tmp_path / "docs-link"
        root.symlink_to(docs, target_is_directory=True)
    else:
        (docs / "escape.md").symlink_to(outside)
        root = docs

    with pytest.raises(ValidationError, match="symlink"):
        amendment_pipeline._docs_snapshot([str(root)], project_root=tmp_path)


def test_docs_digest_rejects_symlinked_parent_component(tmp_path):
    real = tmp_path / "real"
    nested = real / "nested"
    nested.mkdir(parents=True)
    (nested / "design.md").write_text("design")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValidationError, match="symlink"):
        amendment_pipeline._docs_snapshot(
            [str(alias / "nested")], project_root=tmp_path)


def test_docs_digest_rejects_unreadable_file(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    blocked = docs / "blocked.md"
    blocked.write_text("secret")
    original = Path.read_bytes

    def fail_blocked(path):
        if path == blocked:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_blocked)

    with pytest.raises(ValidationError, match="Could not read"):
        amendment_pipeline._docs_snapshot([str(docs)], project_root=tmp_path)


def test_docs_digest_is_stable_across_cwd_and_relative_absolute_inputs(
    tmp_path, monkeypatch,
):
    project_root = tmp_path / "project"
    manifest_dir = project_root / ".omac"
    manifest_dir.mkdir(parents=True)
    manifest = _manifest(manifest_dir)
    docs = project_root / "docs"
    nested = docs / "nested"
    nested.mkdir(parents=True)
    (docs / "overview.md").write_text("overview")
    (nested / "detail.md").write_text("detail")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    expected_root = amendment_pipeline._manifest_project_root(str(manifest))
    assert expected_root == project_root.resolve()

    monkeypatch.chdir(project_root)
    from_root = amendment_pipeline._docs_snapshot(
        ["docs"], project_root=expected_root)
    monkeypatch.chdir(nested)
    from_subdir = amendment_pipeline._docs_snapshot(
        ["docs"], project_root=expected_root)
    monkeypatch.chdir(elsewhere)
    from_elsewhere = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=expected_root)
    absolute_file = amendment_pipeline._docs_snapshot(
        [str(docs / "overview.md")], project_root=expected_root)
    relative_file = amendment_pipeline._docs_snapshot(
        ["docs/overview.md"], project_root=expected_root)

    assert from_root == from_subdir == from_elsewhere
    assert relative_file == absolute_file
    assert from_root["docs_files"] == [
        "docs/nested/detail.md", "docs/overview.md"]

    superseded = SimpleNamespace(id="old", identifier="AITEAM-811")
    attempts = [
        amendment_pipeline._attempt_context(
            str(manifest), report="report", docs_snapshot=snapshot,
            blocked_nodes=["bootstrap"], superseded_issue=superseded)
        for snapshot in (from_root, from_subdir, from_elsewhere)
    ]
    assert {attempt["docs_sha256"] for attempt in attempts} == {
        from_root["docs_sha256"]}
    assert len({attempt["request_digest"] for attempt in attempts}) == 1
    assert len({attempt["attempt_id"] for attempt in attempts}) == 1


def test_docs_digest_rejects_inputs_outside_manifest_project(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside")

    with pytest.raises(ValidationError, match="outside the manifest project"):
        amendment_pipeline._docs_snapshot(
            [str(outside)], project_root=project_root)


def test_existing_fixed_amendment_identity_teaches_new_attempt(tmp_path):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("resource conflict")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    engine = _engine()
    old = engine.store.create_work_item(
        "ws", "Running DAG amendment", "old confirmation",
        f"amend-{path.stem}", "alice", kind=TaskKind.AMENDMENT)

    with pytest.raises(NeedsDecision) as exc:
        amendment_pipeline.propose_amendment(
            engine, str(path), report_file=str(report), docs=[str(docs)],
            blocked_nodes=["bootstrap"], orchestrator="alice",
            reviewers=["bob"], max_revisions=1)

    assert exc.value.report["reason_code"] == "amendment-identity-conflict"
    assert exc.value.report["existing_issue_id"] == old.id
    assert "--new-attempt" in exc.value.report["next_action"]


def test_contract_only_amendment_preserves_runtime_facts_and_resumes_review(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
        review_bounce=4,
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    base = load_manifest(str(path))
    reviewed = build_reviewed_amendment(
        base, _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    result = apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    updated = load_manifest(str(path))
    node = updated.nodes["bootstrap"]
    got = engine.store.get_work_item("1")
    assert result["minimal_rerun"] == {"review": ["bootstrap"], "authoring": [], "merging": []}
    assert node.work_item_id == "1"
    assert node.status == "in_review"
    assert node.contract.acceptance == ["bootstrap workspace is valid"]
    assert got.artifacts == {"pr_url": "https://example.test/pr/1", "head_sha": "abc"}
    assert got.verification == {"subject_digest": "verify-1", "commands": []}
    assert got.contract.acceptance == ["bootstrap workspace is valid"]
    assert got.bounces.review == 4
    assert got.status == WorkItemStatus.IN_REVIEW


def _stage_amendment_with_handoff(tmp_path, stage):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="pass",
        worker_bounce=3,
        review_bounce=4,
        merge_bounce=5,
        worker_handoff=_old_worker_handoff(),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage=stage)),
        engine.store,
        issue_id=f"amendment-{stage}",
        reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )
    return path, engine, item, reviewed


def _apply_exhausted_stage_amendment(tmp_path, stage):
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, stage)
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    return path, engine, item


def _seed_aiteam_849_review_control(engine, item, snapshot):
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": "review-convergence-ledger-unverifiable",
        "kind": "develop",
        "phase": "review",
        "gate": "review-convergence",
        "rounds": 3,
        "resume_issue_id": item.id,
        "node_id": snapshot["dag_key"],
        "verdict": "reject",
        "recommended_action": "dag-amendment",
        "convergence": {
            "schema": "omac.review-convergence-decision/v1",
            "mode": "unverifiable-legacy-ledger",
            "reason_code": "review-convergence-ledger-unverifiable",
            "cycle_count": 3,
        },
    }
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        worker_bounce=15,
        review_bounce=3,
        review_verdict="reject",
        review_report={"blockers": ["legacy convergence audit"]},
        review_report_source="blockers:\n  - legacy convergence audit\n",
        review_subject_digest="subject-round-3",
        review_ledger=snapshot["review_ledger"],
        review_ledger_source=yaml.safe_dump(
            snapshot["review_ledger"], sort_keys=False),
        review_continuation={
            "schema": "omac.review-continuation/v1",
            "authorized_through_round": 6,
        },
        decision_required=decision,
        worker_handoff=snapshot["worker_handoff"],
    )


@pytest.mark.parametrize("checkpoint", ("before_switch", "after_switch"))
def test_aiteam_849_authoring_generation_apply_is_restart_safe_and_idempotent(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot, checkpoint,
):
    snapshot = aiteam_849_legacy_snapshot["work_item"]
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, "authoring")
    _seed_aiteam_849_review_control(engine, item, snapshot)
    original_restore = engine.store.restore_authoring_generation
    crashed = False

    def crash_at_switch(
        item_id, contract, generation, bounce_baseline=None,
    ):
        nonlocal crashed
        if not crashed:
            crashed = True
            if checkpoint == "before_switch":
                raise RuntimeError("crash before authoring generation switch")
            result = original_restore(
                item_id, contract, generation, bounce_baseline)
            raise RuntimeError("crash after authoring generation switch")
        return original_restore(item_id, contract, generation, bounce_baseline)

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", crash_at_switch)
    with pytest.raises(RuntimeError, match="authoring generation switch"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    partial = load_manifest(str(path))
    entry = partial.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing"
    assert entry["expected_review_generation"].startswith("amendment-")

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", original_restore)
    recovered = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    repeated = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    current = engine.store.get_work_item(item.id)
    applied = load_manifest(str(path))
    entry = applied.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert recovered["sync"]["synced"] == ["bootstrap"]
    assert repeated["sync"]["already_complete"] == ["bootstrap"]
    assert current.review_generation == entry["expected_review_generation"]
    assert current.review_ledger == snapshot["review_ledger"]
    assert current.review_ledger_ref is not None
    assert current.current_review_ledger is None
    assert current.decision_required is None
    assert current.review_continuation is None
    assert current.worker_handoff is None
    assert current.bounces.worker == 15
    assert current.bounces.review == 3
    assert entry["observed"]["review_generation"] == (
        entry["expected_review_generation"])
    assert entry["observed"]["review_ledger_current"] is False
    assert entry["observed"]["decision_required_pending"] is False
    assert entry["observed"]["review_report_pending"] is False
    assert entry["observed"]["review_continuation_pending"] is False


def test_runner_restart_after_aiteam_849_amendment_uses_fresh_worker_budget(
    tmp_path, aiteam_849_legacy_snapshot,
):
    snapshot = aiteam_849_legacy_snapshot["work_item"]
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, "authoring")
    _seed_aiteam_849_review_control(engine, item, snapshot)
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))

    first = loop.tick(
        engine.store, engine.runtime, manifest, str(path), max_parallel=1)
    second = loop.tick(
        engine.store, engine.runtime, manifest, str(path), max_parallel=1)

    current = engine.store.get_work_item(item.id)
    assert first.state == "running"
    assert second.state == "running"
    assert current.bounces.worker == 15
    assert current.decision_required is None
    assert current.current_review_ledger is None
    assert current.worker_handoff is not None


def test_accept_authoring_amendment_fails_before_manifest_write_for_active_formal_run(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-active-run", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"}),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
    )
    amendment_file = tmp_path / "active-run.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    before = path.read_bytes()
    active = AgentRunObservation(
        id="formal-run-1",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="issue_assignment",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert path.read_bytes() == before
    assert engine.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


def test_already_applied_authoring_crash_resume_blocks_active_formal_run(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-crash-resume", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"}),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
    )
    amendment_file = tmp_path / "crash-resume.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    original_prepare = amendment_mod.prepare_stage_recovery
    monkeypatch.setattr(
        amendment_mod,
        "prepare_stage_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after manifest write")),
    )

    with pytest.raises(RuntimeError, match="after manifest write"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    interrupted = load_manifest(str(path))
    entry = interrupted.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing"
    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery", original_prepare)
    active = AgentRunObservation(
        id="formal-crash-resume-run",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="issue_assignment",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )
    before = path.read_bytes()

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert path.read_bytes() == before
    assert engine.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


def _legacy_synced_authoring_accept_fixture(
    tmp_path, aiteam_849_legacy_snapshot,
):
    snapshot = aiteam_849_legacy_snapshot["work_item"]
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.set_node_contract(
        target.id, load_manifest(str(path)).nodes["bootstrap"].contract)
    _seed_aiteam_849_review_control(engine, target, snapshot)
    engine.store.update_work_item_metadata(target.id, worker_bounce=14)
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-aiteam-850", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage="authoring")),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    legacy = load_manifest(str(path))
    entry = legacy.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry.pop("expected_review_generation", None)
    entry.pop("observed", None)
    entry["state"] = "synced"
    save_manifest(legacy, str(path))

    _seed_aiteam_849_review_control(engine, target, snapshot)
    current = engine.store.get_work_item(target.id)
    current.review_generation = None
    current.review_ledger_generation = None
    current.bounce_baseline = None
    current.phase = TaskPhase.AUTHORING
    current.status = WorkItemStatus.IN_PROGRESS
    current.bounces.worker = 17

    reviewed["human_confirmation"] = "applied"
    reviewed["apply_result"] = {"legacy": True}
    amendment_file = tmp_path / "aiteam-850.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    engine.store.update_status(amendment_issue.id, WorkItemStatus.DONE)
    return path, amendment_file, engine, target, reviewed


def test_same_legacy_synced_accept_repairs_missing_authoring_projection(
    tmp_path, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))

    result = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    current = engine.store.get_work_item(target.id)
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert result["sync"]["synced"] == ["bootstrap"]
    assert current.status is WorkItemStatus.TODO
    assert current.phase is TaskPhase.AUTHORING
    assert current.bounces.worker == 17
    assert current.bounce_baseline == {
        "worker": 14, "review": 3, "merge": 0}
    assert consumed_bounces(manifest, "bootstrap", current, "worker") == 3
    assert current.review_generation == entry["expected_review_generation"]
    assert current.current_review_ledger is None
    assert current.decision_required is None
    assert current.review_subject_digest is None
    assert current.review_verdict is None
    assert current.review_report is None
    assert current.worker_handoff is None
    assert build_show_output(current, "worker:alice").get("ok", True) is True
    assert entry["observed"]["contract_sha256"] == (
        entry["expected_contract_sha256"])
    assert entry["observed"]["review_generation"] == (
        entry["expected_review_generation"])
    assert entry["observed"]["review_ledger_current"] is False
    assert yaml.safe_load(amendment_file.read_text())["human_confirmation"] == "applied"
    repeated = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    assert repeated["sync"]["already_complete"] == ["bootstrap"]


def test_authoring_repair_noop_restore_does_not_mark_synced(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    before = copy.deepcopy(engine.store.get_work_item(target.id))
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *_args, **_kwargs: before,
    )

    with pytest.raises(
        ValidationError, match="authoring recovery did not reach its target",
    ):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    entry = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "repairing"
    assert entry["observed"]["review_generation"] is None


def test_fake_synced_authoring_projection_is_reselected_and_repaired(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["expected_review_generation"] = "expected-repair-generation"
    entry["state"] = "synced"
    entry["observed"] = recovery_control_snapshot(
        engine.store.get_work_item(target.id))
    save_manifest(manifest, str(path))
    inspected = []
    monkeypatch.setattr(
        engine.runtime,
        "list_runs",
        lambda item_id: inspected.append(item_id) or [],
    )

    result = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    current = engine.store.get_work_item(target.id)
    assert inspected == [target.id]
    assert result["sync"]["synced"] == ["bootstrap"]
    assert current.review_generation == "expected-repair-generation"
    assert current.current_review_ledger is None


@pytest.mark.parametrize("checkpoint", ("before_switch", "after_switch"))
def test_legacy_synced_authoring_repair_is_restart_safe(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot, checkpoint,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    original_restore = engine.store.restore_authoring_generation
    crashed = False

    def crash_at_switch(
        item_id, contract, generation, bounce_baseline=None,
    ):
        nonlocal crashed
        if not crashed:
            crashed = True
            if checkpoint == "before_switch":
                raise RuntimeError("crash before legacy repair switch")
            result = original_restore(
                item_id, contract, generation, bounce_baseline)
            raise RuntimeError("crash after legacy repair switch")
        return original_restore(item_id, contract, generation, bounce_baseline)

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", crash_at_switch)
    with pytest.raises(RuntimeError, match="legacy repair switch"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    interrupted = load_manifest(str(path))
    assert interrupted.meta[
        "amendment_apply"]["nodes"]["bootstrap"]["state"] == "repairing"
    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", original_restore)
    recovered = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    repeated = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    assert recovered["sync"]["synced"] == ["bootstrap"]
    assert repeated["sync"]["already_complete"] == ["bootstrap"]
    current = engine.store.get_work_item(target.id)
    assert current.review_generation is not None
    assert current.current_review_ledger is None


def test_repairing_without_attempt_baseline_fails_closed_after_crash(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    engine.store.update_work_item_metadata(target.id, decision_required={})
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry.pop("attempt_baseline", None)
    save_manifest(manifest, str(path))
    original_save = amendment_mod._save_ledger
    crashed = False

    def crash_after_repairing_save(manifest, manifest_path, ledger):
        nonlocal crashed
        original_save(manifest, manifest_path, ledger)
        saved = ledger["nodes"]["bootstrap"]
        if not crashed and saved["state"] == "repairing":
            crashed = True
            raise RuntimeError("crash after repairing state save")

    monkeypatch.setattr(
        amendment_mod, "_save_ledger", crash_after_repairing_save)
    with pytest.raises(RuntimeError, match="repairing state save"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    interrupted = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert interrupted["state"] == "repairing"
    assert interrupted["attempt_baseline"]
    engine.store.update_work_item_metadata(
        target.id, decision_required={"reason_code": "new-after-crash"})
    assert interrupted["attempt_baseline"]["decision_required_pending"] is False
    assert recovery_control_snapshot(
        engine.store.get_work_item(target.id)
    )["decision_required_pending"] is True
    monkeypatch.setattr(amendment_mod, "_save_ledger", original_save)

    with pytest.raises(
        ValidationError, match="observed progress",
    ):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert engine.store.get_work_item(target.id).decision_required == {
        "reason_code": "new-after-crash"}
    repaired = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert repaired["state"] == "observed_progress"


def test_existing_repairing_without_attempt_baseline_fails_closed(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["state"] = "repairing"
    entry["expected_review_generation"] = "repair-generation"
    entry.pop("attempt_baseline", None)
    save_manifest(manifest, str(path))
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *_args, **_kwargs: pytest.fail(
            "missing causal baseline must not restore Store control"),
    )

    with pytest.raises(
        ValidationError, match="repairing entry is missing attempt_baseline",
    ):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]["state"] == "repairing"
    assert engine.store.get_work_item(target.id).review_generation is None


def test_repairing_without_attempt_baseline_fails_even_when_store_reached(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["state"] = "repairing"
    entry.pop("attempt_baseline", None)
    save_manifest(manifest, str(path))

    with pytest.raises(
        ValidationError, match="repairing entry is missing attempt_baseline",
    ):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    malformed = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert malformed["state"] == "repairing"
    assert "attempt_baseline" not in malformed
    assert engine.store.get_work_item(target.id).review_generation == (
        malformed["expected_review_generation"])


def test_legacy_synced_repair_fails_closed_for_active_formal_run(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    before_manifest = path.read_bytes()
    before_item = copy.deepcopy(engine.store.get_work_item(target.id))
    active = AgentRunObservation(
        id="formal-repair-run",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="rerun",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert path.read_bytes() == before_manifest
    current = engine.store.get_work_item(target.id)
    assert current.review_generation == before_item.review_generation
    assert current.decision_required == before_item.decision_required
    assert current.worker_handoff == before_item.worker_handoff


@pytest.mark.parametrize("progress", ("delivery", "review", "generation"))
@pytest.mark.parametrize("repair_state", ("synced", "repairing"))
def test_legacy_synced_repair_does_not_rollback_progressed_work_item(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot, progress, repair_state,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    if repair_state == "repairing":
        entry["state"] = "repairing"
        entry["expected_review_generation"] = "legacy-repair-generation"
        entry["attempt_baseline"] = recovery_control_snapshot(
            engine.store.get_work_item(target.id))
        save_manifest(manifest, str(path))

    current = engine.store.get_work_item(target.id)
    if progress == "delivery":
        current.delivery_identity = DeliveryIdentity(
            schema=DELIVERY_IDENTITY_SCHEMA,
            handoff_generation="progressed-handoff",
            worker="alice",
            agent_id="agent-alice",
            run_id="completed-worker-run",
            pr_url="https://github.com/acme/repo/pull/146",
            pr_head_sha="progressed-head",
            verification_sha256="progressed-verification",
            verification_attachment_id="progressed-attachment",
            verification_comment_id="progressed-comment",
            verification_uploader_id="agent-alice",
            verification_uploader_type="agent",
        )
    elif progress == "review":
        current.phase = TaskPhase.REVIEW
        current.status = WorkItemStatus.IN_REVIEW
        current.review_generation = "progressed-review-generation"
        current.review_ledger_generation = "progressed-review-generation"
    else:
        current.review_generation = "new-authoring-generation"

    before_item = copy.deepcopy(current)
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("progressed work item must not be restored")),
    )

    with pytest.raises(ValidationError, match="observed progress"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert engine.store.get_work_item(target.id) == before_item
    repaired = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert repaired["state"] == "observed_progress"
    assert repaired["observed"]["delivery_identity_pending"] is (
        progress == "delivery")
    assert repaired["observed"]["phase"] == before_item.phase.value


def test_legacy_synced_repair_first_reconcile_dispatches_one_worker(
    tmp_path, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    before = list(engine.runtime.list_runs(target.id))

    loop.tick(engine.store, engine.runtime, manifest, str(path), max_parallel=1)
    loop.tick(engine.store, engine.runtime, manifest, str(path), max_parallel=1)

    after = list(engine.runtime.list_runs(target.id))
    new_formal = [run for run in after if run not in before and run.formal]
    assert len(new_formal) == 1
    assert new_formal[0].agent_id == engine.store.resolve_agent_id("alice")


def test_worker_retry_log_distinguishes_absolute_and_generation_consumption(
    tmp_path, aiteam_849_legacy_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    current = engine.store.get_work_item(target.id)
    worker_retry = bounce_log_fields(
        current, "worker", absolute_count=18, limit=5)
    assert worker_retry["absolute_audit_round"] == 18
    assert worker_retry["current_generation_consumed"] == 4
    assert worker_retry["current_generation_limit"] == 5


def test_accept_authoring_amendment_resume_rechecks_active_formal_run(
    tmp_path, monkeypatch,
):
    """Crash resume must not bypass the formal Run boundary after manifest apply."""
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-active-resume", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"}),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
    )
    amendment_file = tmp_path / "active-resume.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    original_resume = amendment_mod._resume_apply_ledger
    monkeypatch.setattr(
        amendment_mod,
        "_resume_apply_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after manifest apply")),
    )

    with pytest.raises(RuntimeError, match="crash after manifest apply"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    monkeypatch.setattr(amendment_mod, "_resume_apply_ledger", original_resume)
    active = AgentRunObservation(
        id="formal-run-resume",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="issue_assignment",
    )
    monkeypatch.setattr(
        engine.runtime,
        "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="resume accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    interrupted = load_manifest(str(path))
    assert interrupted.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == (
        "pending"
    )
    assert engine.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


@pytest.mark.parametrize("stage", ("authoring", "review", "merging"))
def test_amendment_recovery_preserves_absolute_bounce_audit_and_records_fresh_budget(
    tmp_path, stage,
):
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, stage)

    got = engine.store.get_work_item(item.id)
    ledger = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]

    assert got.bounces.worker == 3
    assert got.bounces.review == 4
    assert got.bounces.merge == 5
    assert got.worker_handoff is None
    assert ledger["bounce_baseline"] == {
        "worker": 3,
        "review": 4,
        "merge": 5,
    }


@pytest.mark.parametrize("stage", ("review", "merging"))
@pytest.mark.parametrize("checkpoint", ("before_clear", "after_clear"))
def test_amendment_stage_recovery_retires_handoff_restart_safely(
    tmp_path, monkeypatch, stage, checkpoint,
):
    """apply ledger 在 intent clear 写入前后崩溃都可重入并幂等收口。"""
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, stage)
    original_update = engine.store.update_work_item_metadata
    crashed = False

    def crash_at_clear(item_id, **metadata):
        nonlocal crashed
        if metadata.get("worker_handoff") == {} and not crashed:
            crashed = True
            if checkpoint == "before_clear":
                raise RuntimeError("crash before stage handoff retirement")
            result = original_update(item_id, **metadata)
            raise RuntimeError("crash after stage handoff retirement")
        return original_update(item_id, **metadata)

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", crash_at_clear)
    with pytest.raises(RuntimeError, match="stage handoff retirement"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    interrupted = load_manifest(str(path))
    entry = interrupted.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing"
    assert (engine.store.get_work_item(item.id).worker_handoff is None) is (
        checkpoint == "after_clear")

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    assert result["sync"]["synced"] == ["bootstrap"]
    recovered = engine.store.get_work_item(item.id)
    assert recovered.worker_handoff is None
    assert load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]["state"] == "synced"

    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    assert engine.store.get_work_item(item.id).worker_handoff is None


def test_merging_stage_retires_handoff_before_contract_sync(
    tmp_path, monkeypatch,
):
    """merging 不能先同步 contract 再遗留旧 handoff。"""
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, "merging")
    original_set_contract = engine.store.set_node_contract
    observed = []

    def assert_retired_before_contract(item_id, contract):
        observed.append(engine.store.get_work_item(item_id).worker_handoff)
        return original_set_contract(item_id, contract)

    monkeypatch.setattr(
        engine.store, "set_node_contract", assert_retired_before_contract)
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    assert observed == [None]
    assert engine.store.get_work_item(item.id).worker_handoff is None


def test_review_recovery_uses_fresh_budget_without_erasing_absolute_history(
    tmp_path, monkeypatch,
):
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, "review")
    manifest = load_manifest(str(path))
    engine.store.update_work_item_metadata(item.id, review_verdict="reject")
    monkeypatch.setattr(loop, "validate_review_evidence", lambda *_args: [])

    failures = loop.collect_results(
        engine.store, engine.runtime, manifest, str(path),
        retry_limits={"worker": 3, "ci": 3, "review": 3, "merge": 3},
    )

    got = engine.store.get_work_item(item.id)
    assert failures == {}
    assert manifest.nodes["bootstrap"].status == "in_progress"
    assert got.bounces.review == 5


def test_authoring_recovery_uses_fresh_worker_budget_without_erasing_history(tmp_path):
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, "authoring")
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "in_progress"
    engine.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    engine.store.get_work_item(item.id).agent_run_finished_without_submit = True

    failures = loop.collect_results(
        engine.store, engine.runtime, manifest, str(path),
        retry_limits={"worker": 3, "ci": 3, "review": 3, "merge": 3},
    )

    got = engine.store.get_work_item(item.id)
    assert "bootstrap" in failures
    assert manifest.nodes["bootstrap"].status == "blocked"
    assert got.bounces.worker == 3
    assert got.decision_required["reason_code"] == "worker-retry-intent-required"


def test_merging_recovery_uses_fresh_merge_budget_without_erasing_history(tmp_path):
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, "merging")
    manifest = load_manifest(str(path))
    engine.store.observe_pull_request = lambda _url: type("Observation", (), {
        "state": "closed_unmerged",
        "merged_at": None,
        "detail": "closed without merge",
    })()

    result = run_merge_delivery(
        {}, manifest, "bootstrap", engine.store, engine.runtime,
        {"worker": 3, "ci": 3, "review": 3, "merge": 3}, str(path),
    )

    got = engine.store.get_work_item(item.id)
    assert result == "bounce"
    assert manifest.nodes["bootstrap"].status == "in_progress"
    assert got.bounces.merge == 6


def test_repeated_accept_after_node_progress_never_rolls_it_back(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    first = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    assert first["sync"]["synced"] == ["bootstrap"]

    engine.store.update_work_item_metadata(
        item.id,
        review_verdict="pass",
        review_report={"blockers": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    progressed = load_manifest(str(path))
    progressed.nodes["bootstrap"].status = "done"
    progressed.nodes["bootstrap"].merged = True
    progressed.nodes["bootstrap"].merged_at = "2026-07-27T01:00:00Z"
    save_manifest(progressed, str(path))

    with pytest.raises(ValidationError, match="observed progress"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    got = engine.store.get_work_item(item.id)
    reloaded = load_manifest(str(path))
    assert got.status == WorkItemStatus.DONE
    assert got.review_verdict == "pass"
    assert reloaded.nodes["bootstrap"].status == "done"
    assert reloaded.nodes["bootstrap"].merged is True
    assert reloaded.meta["amendment_apply"]["nodes"]["bootstrap"][
        "state"] == "observed_progress"


def test_apply_ledger_completes_after_contract_write_but_before_review_reset(tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        verification={"subject_digest": "verify-1"},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    original_reset = engine.store.reset_review
    failed = False

    def fail_reset(item_id):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("simulated reset interruption")
        return original_reset(item_id)

    monkeypatch.setattr(engine.store, "reset_review", fail_reset)
    with pytest.raises(RuntimeError, match="reset interruption"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    partial = engine.store.get_work_item(item.id)
    assert partial.contract.acceptance == ["bootstrap workspace is valid"]
    assert partial.review_verdict == "reject"

    monkeypatch.setattr(engine.store, "reset_review", original_reset)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    completed = engine.store.get_work_item(item.id)
    assert result["sync"]["synced"] == ["bootstrap"]
    assert completed.status == WorkItemStatus.IN_REVIEW
    assert completed.review_verdict is None


def test_merge_only_recovery_keeps_pass_verdict_and_enters_merging(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        review_verdict="pass",
        review_report={"blockers": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    engine.store.set_node_contract(
        item.id, load_manifest(str(path)).nodes["bootstrap"].contract)
    proposal = _proposal({"op": "resume", "node": "bootstrap", "stage": "merging"})
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    engine.store.observe_pull_request = lambda *_args, **_kwargs: pytest.fail(
        "amendment accept must delegate PR observation to dag run")
    engine.store.request_pull_request_merge = lambda *_args, **_kwargs: pytest.fail(
        "amendment accept must delegate merge requests to dag run")
    engine.store.set_node_contract = lambda *_args, **_kwargs: pytest.fail(
        "pure merge resume must not republish the unchanged contract")

    result = apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert result["minimal_rerun"]["merging"] == ["bootstrap"]
    assert load_manifest(str(path)).nodes["bootstrap"].status == "merging"
    assert engine.store.get_work_item("1").review_verdict == "pass"


@pytest.mark.parametrize("stage", ("review", "merging"))
def test_non_authoring_recovery_noop_does_not_mark_synced(
    tmp_path, monkeypatch, stage,
):
    path, engine, _item, reviewed = _stage_amendment_with_handoff(
        tmp_path, stage)
    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ValidationError, match="did not reach its target"):
        apply_amendment(
            str(path),
            reviewed,
            engine.store,
            {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    entry = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing"


@pytest.mark.parametrize("stage", ("review", "merging"))
@pytest.mark.parametrize("drift", ("safe", "progressed"))
def test_persisted_synced_non_authoring_reobserves_store(
    tmp_path, monkeypatch, stage, drift,
):
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, stage)
    before = copy.deepcopy(engine.store.get_work_item(item.id))
    apply_amendment(
        str(path),
        reviewed,
        engine.store,
        {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    current = engine.store.get_work_item(item.id)
    if drift == "safe":
        current.__dict__.clear()
        current.__dict__.update(copy.deepcopy(before.__dict__))
    else:
        current.status = WorkItemStatus.DONE
    calls = []
    monkeypatch.setattr(
        amendment_mod,
        "prepare_stage_recovery",
        lambda *_args, **_kwargs: calls.append(stage),
    )

    match = "did not reach its target" if drift == "safe" else "observed progress"
    with pytest.raises(ValidationError, match=match):
        apply_amendment(
            str(path),
            reviewed,
            engine.store,
            {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    entry = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    if drift == "safe":
        assert entry["state"] == "syncing"
        assert calls == [stage]
    else:
        assert entry["state"] == "observed_progress"
        assert calls == []
        assert engine.store.get_work_item(item.id).status is WorkItemStatus.DONE


def test_merge_only_precondition_failure_does_not_modify_manifest(tmp_path):
    path = _manifest(tmp_path)
    original = path.read_bytes()
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        review_verdict="reject",
        review_report={"blockers": ["not ready"]},
        worker_handoff=_old_worker_handoff(),
    )
    proposal = _proposal({"op": "resume", "node": "bootstrap", "stage": "merging"})
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    with pytest.raises(ValidationError, match="passed review and PR"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert path.read_bytes() == original
    assert engine.store.get_work_item(item.id).worker_handoff is not None


def test_implementation_scope_change_requires_authoring_and_explicit_migration(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item("ws", "bootstrap", "desc", "bootstrap", "alice")
    operation = _contract_update()
    operation["set"]["contract"]["scope_paths"] = ["src/**"]

    errors = validate_proposal(
        load_manifest(str(path)), _proposal(operation), {"alice", "bob", "charlie"})
    assert any("ownership migration" in error for error in errors)

    operation["migration"] = {
        "ownership_transfer": True,
        "reason": "bootstrap now owns the source skeleton",
    }
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(operation), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    result = apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert result["minimal_rerun"]["authoring"] == ["bootstrap"]
    manifest = load_manifest(str(path))
    assert manifest.nodes["bootstrap"].status == "todo"
    assert engine.store.get_work_item("1").worker_handoff is None

    dispatched = loop.tick(
        engine.store, engine.runtime, manifest, str(path), max_parallel=1)

    handoff = engine.store.get_work_item("1").worker_handoff
    assert dispatched.dispatched == ["bootstrap"]
    assert handoff is not None
    assert handoff.gate == "explicit-dispatch"
    assert handoff.target_worker_bounce == 0


def test_done_or_merged_node_cannot_be_rewritten_or_removed(tmp_path):
    path = _manifest(tmp_path)
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "done"
    manifest.nodes["bootstrap"].merged = True
    manifest.nodes["bootstrap"].merged_at = "2026-07-27T00:00:00Z"

    update_errors = validate_proposal(
        manifest, _proposal(_contract_update()), {"alice", "bob", "charlie"})
    remove_errors = validate_proposal(
        manifest, _proposal({"op": "remove", "node": "bootstrap"}),
        {"alice", "bob", "charlie"})

    assert any("done/merged" in error for error in update_errors)
    assert any("done/merged" in error for error in remove_errors)


def test_runtime_drift_is_rebased_but_definition_drift_fails_cas(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item("ws", "bootstrap", "desc", "bootstrap", "alice")
    base = load_manifest(str(path))
    reviewed = build_reviewed_amendment(
        base, _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    assert reviewed["base"]["manifest_sha256"] == manifest_digest(base)
    assert reviewed["base"]["definition_sha256"] == manifest_definition_digest(base)

    raw = yaml.safe_load(path.read_text())
    raw["nodes"][1]["status"] = "blocked"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    apply_amendment(str(path), copy.deepcopy(reviewed), engine.store, {"alice", "bob", "charlie"})

    path = _manifest(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["nodes"][1]["blocked_by"] = []
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    with pytest.raises(ValidationError, match="definition changed"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})


def test_amendment_rejects_cycles_and_reports_changed_nodes(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["closeout"]},
    })

    errors = validate_proposal(manifest, proposal, {"alice", "bob", "charlie"})

    assert "manifest DAG has a cycle" in errors


def test_topology_change_recovers_started_downstream_but_not_unstarted_closure(tmp_path):
    path = _topology_manifest(tmp_path)
    engine = _engine()
    first = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(first.id, WorkItemStatus.BLOCKED)
    second = engine.store.create_work_item(
        "ws", "dependent", "desc", "started-dependent", "charlie", reviewer="bob")
    engine.store.update_status(second.id, WorkItemStatus.IN_REVIEW)
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["foundation"]},
    })

    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    assert reviewed["analysis"]["derived_started_downstream"] == ["started-dependent"]
    assert reviewed["analysis"]["minimal_rerun"] == {
        "review": [],
        "authoring": ["bootstrap", "started-dependent"],
        "merging": [],
    }
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    updated = load_manifest(str(path))
    assert result["minimal_rerun"]["authoring"] == [
        "bootstrap", "started-dependent"]
    assert updated.nodes["bootstrap"].status == "todo"
    assert updated.nodes["started-dependent"].status == "todo"
    assert updated.nodes["future-dependent"].status == "todo"
    assert "future-dependent" not in result["minimal_rerun"]["authoring"]


def test_apply_ledger_retries_only_unfinished_node_side_effects(tmp_path, monkeypatch):
    path = _topology_manifest(tmp_path)
    engine = _engine()
    first = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(first.id, WorkItemStatus.BLOCKED)
    second = engine.store.create_work_item(
        "ws", "dependent", "desc", "started-dependent", "charlie", reviewer="bob")
    engine.store.update_status(second.id, WorkItemStatus.IN_REVIEW)
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["foundation"]},
    })
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    original_sync = amendment_mod.prepare_stage_recovery
    failed = False

    def fail_second(
        node, store, stage, *, expected_review_subject=None,
        expected_review_generation=None, expected_bounce_baseline=None,
        sync_contract=False,
    ):
        nonlocal failed
        if node.id == "started-dependent" and not failed:
            failed = True
            raise RuntimeError("simulated Store interruption")
        return original_sync(
            node, store, stage,
            expected_review_subject=expected_review_subject,
            expected_review_generation=expected_review_generation,
            expected_bounce_baseline=expected_bounce_baseline,
            sync_contract=sync_contract)

    monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", fail_second)
    with pytest.raises(RuntimeError, match="Store interruption"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    interrupted = load_manifest(str(path))
    ledger = interrupted.meta["amendment_apply"]["nodes"]
    assert ledger["bootstrap"]["state"] == "synced"
    assert ledger["started-dependent"]["state"] == "syncing"

    engine.store.update_status(first.id, WorkItemStatus.DONE)
    interrupted.nodes["bootstrap"].status = "done"
    save_manifest(interrupted, str(path))
    monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", original_sync)

    with pytest.raises(ValidationError, match="observed progress"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert engine.store.get_work_item(first.id).status == WorkItemStatus.DONE
    assert engine.store.get_work_item(second.id).status == WorkItemStatus.TODO
    completed = load_manifest(str(path)).meta["amendment_apply"]["nodes"]
    assert completed["bootstrap"]["state"] == "observed_progress"
    assert completed["started-dependent"]["state"] == "synced"


def test_invalid_historical_ledger_remains_raw_history_during_authoring_recovery(
    tmp_path, monkeypatch, aiteam_849_legacy_snapshot,
    contracts_platform_resource_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, aiteam_849_legacy_snapshot))
    invalid_ledger = copy.deepcopy(
        contracts_platform_resource_snapshot["work_item"]["review_ledger"])
    current = engine.store.get_work_item(target.id)
    current.review_ledger = invalid_ledger
    current.review_generation = None
    current.review_ledger_generation = None
    before = copy.deepcopy(current.review_ledger)

    with pytest.raises(
        ValidationError, match=r"cycles\[10\]\.round must be 11",
    ):
        build_show_output(current, "worker:alice")

    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    result = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    recovered = engine.store.get_work_item(target.id)
    assert result["sync"]["synced"] == ["bootstrap"]
    assert recovered.review_ledger == before
    assert recovered.current_review_ledger is None
    assert build_show_output(recovered, "worker:alice").get("ok", True) is True


def test_pending_apply_blocks_all_dag_progress_until_same_accept_resumes(
    tmp_path, monkeypatch, capsys,
):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    amendment_file = tmp_path / "reviewed-amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    original_prepare = amendment_mod.prepare_stage_recovery

    def crash_before_store(*_args, **_kwargs):
        raise RuntimeError("crash after manifest before Store compensation")

    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery", crash_before_store)
    with pytest.raises(RuntimeError, match="before Store compensation"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            amendment_file=str(amendment_file),
        )

    partial = load_manifest(str(path))
    ledger = partial.meta["amendment_apply"]
    assert ledger["amendment_id"] == reviewed["amendment_id"]
    assert ledger["amendment_file"] == str(amendment_file)
    assert ledger["nodes"]["bootstrap"]["state"] == "syncing"

    original_get = engine.store.get_work_item
    monkeypatch.setattr(
        engine.store, "get_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "pending amendment gate must run before reading Store"),
    )
    for advance in (
        lambda: loop.reconcile(engine.store, partial, str(path)),
        lambda: loop.tick(engine.store, engine.runtime, partial, str(path)),
    ):
        with pytest.raises(NeedsDecision) as exc_info:
            advance()
        report = exc_info.value.report
        assert report["reason"] == "amendment_apply_incomplete"
        assert report["amendment_id"] == reviewed["amendment_id"]
        assert report["incomplete_nodes"] == [{
            "node_id": "bootstrap", "stage": "review", "state": "syncing",
        }]
        assert report["resume_command"] == (
            f"omac dag amend accept {path} {amendment_file}")

    monkeypatch.setattr(
        dag_cmd, "_assemble_engine",
        lambda _args: pytest.fail(
            "CLI run must block before engine assembly or dispatch"),
    )
    assert main(["dag", "run", str(path), "--output", "json"]) == exit_codes.NEEDS_DECISION
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["amendment_id"] == reviewed["amendment_id"]
    assert cli_report["resume_command"].endswith(str(amendment_file))

    monkeypatch.setattr(engine.store, "get_work_item", original_get)
    monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", original_prepare)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        amendment_file=str(amendment_file),
    )

    assert result["sync"]["synced"] == ["bootstrap"]
    completed = load_manifest(str(path))
    assert all(
        entry["state"] in {"synced", "observed_progress"}
        for entry in completed.meta["amendment_apply"]["nodes"].values()
    )
    loop.reconcile(engine.store, completed, str(path))


def test_topology_change_fails_closed_for_completed_downstream(tmp_path):
    path = _topology_manifest(tmp_path)
    manifest = load_manifest(str(path))
    manifest.nodes["started-dependent"].status = "done"
    manifest.nodes["started-dependent"].merged = True
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["foundation"]},
    })

    errors = validate_proposal(
        manifest, proposal, {"alice", "bob", "charlie"})

    assert any("done/merged downstream" in error for error in errors)


def test_added_node_cannot_smuggle_runtime_facts(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    proposal = _proposal({
        "op": "add",
        "value": {
            "id": "new-node",
            "worker": "alice",
            "blocked_by": [],
            "status": "done",
            "work_item_id": "forged",
        },
    })

    errors = validate_proposal(manifest, proposal, {"alice", "bob", "charlie"})

    assert any("runtime fields" in error for error in errors)


def test_work_submit_accepts_structured_amendment_delivery(tmp_path):
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "amend", "desc", "amend-demo", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    amendment_file = tmp_path / "amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(_proposal(_contract_update())))

    result = submit(
        engine.store, item.id, amendment_file=str(amendment_file),
        agent_pool={"alice", "bob", "charlie"})

    got = engine.store.get_work_item(item.id)
    assert result.deliverable_key == "amendment"
    assert got.deliverable
    assert got.phase.value == "review"
    assert got.status == WorkItemStatus.IN_REVIEW


def test_tampered_minimal_rerun_is_rejected(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item("ws", "bootstrap", "desc", "bootstrap", "alice")
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    reviewed["analysis"]["minimal_rerun"] = {
        "review": [], "authoring": [], "merging": ["bootstrap"],
    }

    with pytest.raises(ValidationError, match="identity does not match"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})


def test_cli_amendment_stops_after_reviewer_then_human_accepts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {
            "workers": ["alice", "charlie"],
            "orchestrator": "alice",
            "reviewers": ["bob"],
        },
        "retry": {"review": 2, "ci": 2, "merge": 2, "worker": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("bootstrap wrongly claims the whole user journey")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("bootstrap contributes only a local gate")

    engine = _engine()
    bootstrap = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        bootstrap.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(bootstrap.id, WorkItemStatus.BLOCKED)
    MockStore.set_kind_delivery("amendment", {
        "amendment": yaml.safe_dump(_proposal(_contract_update()), sort_keys=False),
    })
    MockStore.set_review_verdict("pass")

    amendment_path = omac_dir / "dag.amendment.yaml"
    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--blocked-node", "bootstrap",
        "--output-file", str(amendment_path),
    ])

    assert code == exit_codes.NEEDS_DECISION
    reviewed = yaml.safe_load(amendment_path.read_text())
    amendment_issue = engine.store.get_work_item(reviewed["review"]["issue_id"])
    assert amendment_issue.phase.value == "confirmation"
    assert amendment_issue.review_verdict == "pass"
    assert load_manifest(str(manifest_path)).nodes["bootstrap"].status == "blocked"

    code = main([
        "dag", "amend", "accept", str(manifest_path), str(amendment_path),
        "--reason", "reviewed by operator",
    ])

    assert code == exit_codes.OK
    updated = load_manifest(str(manifest_path))
    assert updated.nodes["bootstrap"].status == "in_review"
    assert updated.nodes["bootstrap"].work_item_id == "1"
    assert engine.store.get_work_item(reviewed["review"]["issue_id"]).status == WorkItemStatus.DONE
    assert yaml.safe_load(amendment_path.read_text())["human_confirmation"] == "applied"


def test_cli_amendment_resume_failed_authoring_reuses_issue(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {
            "workers": ["alice", "charlie"],
            "orchestrator": "alice",
            "reviewers": ["bob"],
        },
        "retry": {"review": 2, "ci": 2, "merge": 2, "worker": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("resume failed amendment authoring")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")

    resume_issue_id = "e66f44e4-bd0f-4a9c-8bfa-2f60a7641d84"
    observed = {}

    def fake_propose(*_args, **kwargs):
        observed["resume_issue_id"] = kwargs["resume_issue_id"]
        return {
            "state": "pending_human_confirmation",
            "manifest": str(manifest_path),
            "amendment_file": str(omac_dir / "dag.amendment.yaml"),
            "amendment_id": "amendment-1",
            "issue_id": resume_issue_id,
            "reviewer_verdict": "pass",
        }

    monkeypatch.setattr(amendment_pipeline, "propose_amendment", fake_propose)

    amendment_path = omac_dir / "dag.amendment.yaml"
    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--blocked-node", "bootstrap",
        "--resume-issue-id", resume_issue_id,
        "--output-file", str(amendment_path),
    ])

    assert code == exit_codes.NEEDS_DECISION
    assert observed["resume_issue_id"] == resume_issue_id


def test_cli_amendment_forwards_restart_authoring(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("replace confirmation")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    observed = {}

    def fake_propose(*_args, **kwargs):
        observed.update(kwargs)
        return {
            "state": "pending_human_confirmation",
            "manifest": str(manifest_path),
            "amendment_file": str(omac_dir / "dag.amendment.yaml"),
            "amendment_id": "amendment-2",
            "issue_id": "issue-1",
            "reviewer_verdict": "pass",
        }

    monkeypatch.setattr(amendment_pipeline, "propose_amendment", fake_propose)

    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--resume-issue-id", "issue-1",
        "--restart-authoring",
    ])

    assert code == exit_codes.NEEDS_DECISION
    assert observed["resume_issue_id"] == "issue-1"
    assert observed["restart_authoring"] is True


def test_cli_concurrent_amend_propose_fails_before_engine_or_pipeline_side_effects(
    tmp_path, monkeypatch, capsys,
):
    project = tmp_path / "project"
    project.mkdir()
    omac_dir = project / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(project)
    report = project / "review.md"
    report.write_text("concurrent amendment")
    docs = project / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    output_file = omac_dir / "dag.concurrent.amendment.yaml"
    ready = tmp_path / "first-propose-ready"
    release = tmp_path / "release-first-propose"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(repo_root / "src"), env.get("PYTHONPATH", ""),
    ]))
    script = """
import sys
import time
from pathlib import Path

import omac.pipeline.amendment as amendment_pipeline
from omac.cli.main import main

ready = Path(sys.argv[1])
release = Path(sys.argv[2])
manifest, report, docs, output_file = sys.argv[3:7]

def hold_propose(*_args, **_kwargs):
    ready.write_text("locked")
    while not release.exists():
        time.sleep(0.01)
    return {
        "state": "pending_human_confirmation",
        "manifest": manifest,
        "amendment_file": output_file,
        "amendment_id": "held-amendment",
        "issue_id": "held-issue",
        "reviewer_verdict": "pass",
    }

amendment_pipeline.propose_amendment = hold_propose
raise SystemExit(main([
    "dag", "amend", "propose", manifest,
    "--report-file", report,
    "--docs", docs,
    "--output-file", output_file,
    "--output", "json",
]))
"""
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(ready), str(release),
         str(manifest_path), str(report), str(docs), str(output_file)],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if first.poll() is not None:
                stdout, stderr = first.communicate()
                pytest.fail(
                    f"first propose exited before holding lock: {stdout}\n{stderr}")
            time.sleep(0.01)
        assert ready.exists(), "first propose did not reach the locked pipeline"

        monkeypatch.setattr(
            dag_cmd, "_assemble_engine",
            lambda _args: pytest.fail(
                "second propose must fail before engine assembly"))
        monkeypatch.setattr(
            amendment_pipeline, "propose_amendment",
            lambda *_args, **_kwargs: pytest.fail(
                "second propose must not enter the pipeline"))

        code = main([
            "dag", "amend", "propose", str(manifest_path),
            "--report-file", str(report),
            "--docs", str(docs),
            "--output-file", str(output_file),
            "--output", "json",
        ])

        assert code == exit_codes.VALIDATION
        assert "dag amend propose" in capsys.readouterr().err
    finally:
        release.write_text("release")
        stdout, stderr = first.communicate(timeout=10)
        assert first.returncode == exit_codes.NEEDS_DECISION, (stdout, stderr)


def test_cli_restart_authoring_fails_closed_without_multica_access(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "multica",
        "workspace": "ws",
        "project": "project-1",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("replace confirmation")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    from omac.engines.multica import MulticaStore
    monkeypatch.setattr(
        MulticaStore, "_run_multica",
        lambda *_args, **_kwargs: pytest.fail("restart must not access Multica"))

    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--resume-issue-id", "old-issue-id",
        "--restart-authoring",
        "--output", "json",
    ])

    output = capsys.readouterr()
    assert code == exit_codes.NEEDS_DECISION
    structured = json.loads(output.out)
    assert "--new-attempt" in structured["next_action"]
    assert "--supersedes-issue-id old-issue-id" in structured["next_action"]


def test_cli_amendment_forwards_new_attempt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("new amendment attempt")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    observed = {}

    def fake_propose(*_args, **kwargs):
        observed.update(kwargs)
        return {
            "state": "pending_human_confirmation",
            "manifest": str(manifest_path),
            "amendment_file": str(omac_dir / "dag.amendment.yaml"),
            "amendment_id": "amendment-attempt",
            "issue_id": "issue-new",
            "reviewer_verdict": "pass",
        }

    monkeypatch.setattr(amendment_pipeline, "propose_amendment", fake_propose)

    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--new-attempt",
        "--supersedes-issue-id", "issue-old",
    ])

    assert code == exit_codes.NEEDS_DECISION
    assert observed["new_attempt"] is True
    assert observed["supersedes_issue_id"] == "issue-old"

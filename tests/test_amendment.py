import copy
import json
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
    EvidenceMode,
    MISSING_CONSUMES,
    ProducedArtifact,
    load_manifest,
    save_manifest,
)
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItem, WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import loop
from omac.pipeline.delivery import run_merge_delivery
from omac.pipeline.dispatch import build_show_output, submit


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

    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=acceptance)

    updated = load_manifest(str(path)).nodes["bootstrap"]
    assert result["sync"]["synced"] == ["bootstrap"]
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
    assert ledger["store_side_effect"] == "set_node_contract"
    assert ledger["before_contract_sha256"] == correction["before_contract_sha256"]
    assert ledger["evidence_sha256"] == reviewed["base"]["evidence_sha256"][
        "bootstrap"
    ]
    store_after = engine.store.get_work_item(item.id)
    assert store_after.contract.acceptance == []
    assert store_after.contract.acceptance_claims == ["UJ-BOOTSTRAP"]
    assert store_after.contract_ref["sha256"]
    assert store_after.contract_ref["sha256"] != store_before.contract_ref["sha256"]
    show = build_show_output(store_after, "reviewer:bob")
    assert show["context"]["contract"]["acceptance_claims"] == ["UJ-BOOTSTRAP"]
    assert show["context"]["contract_ref"] == store_after.contract_ref
    for field in (
        "status", "phase", "worker", "reviewer", "artifacts", "verification",
        "verification_ref", "review_verdict", "review_report", "review_report_ref",
        "review_subject_digest", "review_ledger", "review_ledger_ref",
    ):
        assert getattr(store_after, field) == getattr(store_before, field)

    repeated = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    assert repeated["sync"]["already_complete"] == ["bootstrap"]


def test_historical_contract_sync_recovers_after_store_write_without_republishing(
    tmp_path, monkeypatch,
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
    node.merged_at = "2026-07-26T23:03:59Z"
    engine.store.set_node_contract(item.id, node.contract)
    save_manifest(manifest, str(path))
    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update(historical=True)), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc())
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
            acceptance=_responsibility_acceptance_doc())

    partial = load_manifest(str(path))
    assert partial.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    monkeypatch.setattr(engine.store, "set_node_contract", original_set)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert calls == 1
    assert result["sync"]["synced"] == ["bootstrap"]
    completed = load_manifest(str(path))
    assert completed.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "synced"


def test_historical_contract_sync_fails_closed_on_unexpected_store_contract_drift(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    manifest = load_manifest(str(path))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    engine.store.set_node_contract(item.id, node.contract)
    save_manifest(manifest, str(path))
    reviewed = build_reviewed_amendment(
        manifest, _proposal(_responsibility_update(historical=True)), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc())
    drifted = copy.deepcopy(node.contract)
    drifted.objective = "unexpected Store contract drift"
    engine.store.set_node_contract(item.id, drifted)

    with pytest.raises(ValidationError, match="Store contract changed"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc())

    unchanged = load_manifest(str(path))
    assert unchanged.meta.get("amendment_apply") is None
    assert unchanged.meta.get("last_amendment_id") is None


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
    item.contract = amendment_mod._canonical_contract_value(node.contract)
    item.contract_ref = {
        "sha256": amendment_mod._contract_digest(node.contract),
    }

    class HistoricalStore:
        config = EngineConfig(engine_type="mock", workspace_id="ws")

        def list_members(self, _workspace_id):
            return [node.worker, node.reviewer]

        def get_work_item(self, item_id):
            assert item_id == item.id
            return item

        def set_node_contract(self, item_id, contract):
            assert item_id == item.id
            item.contract = amendment_mod._canonical_contract_value(contract)
            item.contract_ref = {
                "sha256": amendment_mod._contract_digest(contract),
            }

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


def test_new_attempt_requires_superseded_human_confirmation(tmp_path):
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

    with pytest.raises(ValidationError, match="human confirmation"):
        amendment_pipeline.propose_amendment(
            engine, str(path), report_file=str(report), docs=[str(docs)],
            blocked_nodes=["bootstrap"], orchestrator="alice",
            reviewers=["bob"], max_revisions=1, new_attempt=True,
            supersedes_issue_id=old.id)


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
    docs_a = tmp_path / "docs-a"
    nested = docs_a / "nested"
    nested.mkdir(parents=True)
    (docs_a / "overview.md").write_text("overview")
    detail = nested / "detail.md"
    detail.write_text("detail v1")
    docs_b = tmp_path / "single.md"
    docs_b.write_text("single")

    first = amendment_pipeline._docs_snapshot([str(docs_a), str(docs_b)])
    reordered = amendment_pipeline._docs_snapshot([str(docs_b), str(docs_a)])

    assert reordered == first
    assert any(path.endswith("nested/detail.md") for path in first["docs_files"])

    issue = SimpleNamespace(id="old", identifier="AITEAM-811")
    manifest = _manifest(tmp_path)
    first_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=first,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    reordered_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=reordered,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    assert reordered_attempt["attempt_id"] == first_attempt["attempt_id"]

    detail.write_text("detail v2")
    changed = amendment_pipeline._docs_snapshot([str(docs_a), str(docs_b)])
    changed_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=changed,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    assert changed["docs_sha256"] != first["docs_sha256"]
    assert changed_attempt["attempt_id"] != first_attempt["attempt_id"]


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
        amendment_pipeline._docs_snapshot([str(root)])


def test_docs_digest_rejects_symlinked_parent_component(tmp_path):
    real = tmp_path / "real"
    nested = real / "nested"
    nested.mkdir(parents=True)
    (nested / "design.md").write_text("design")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValidationError, match="symlink"):
        amendment_pipeline._docs_snapshot([str(alias / "nested")])


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
        amendment_pipeline._docs_snapshot([str(docs)])


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


def _apply_exhausted_stage_amendment(tmp_path, stage):
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
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    return path, engine, item


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
    assert ledger["bounce_baseline"] == {
        "worker": 3,
        "review": 4,
        "merge": 5,
    }


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
    assert failures == {}
    assert manifest.nodes["bootstrap"].status == "in_progress"
    assert got.bounces.worker == 4


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

    second = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    got = engine.store.get_work_item(item.id)
    reloaded = load_manifest(str(path))
    assert second["sync"]["already_complete"] == ["bootstrap"]
    assert got.status == WorkItemStatus.DONE
    assert got.review_verdict == "pass"
    assert reloaded.nodes["bootstrap"].status == "done"
    assert reloaded.nodes["bootstrap"].merged is True
    assert reloaded.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "synced"


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
    )
    proposal = _proposal({"op": "resume", "node": "bootstrap", "stage": "merging"})
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    with pytest.raises(ValidationError, match="passed review and PR"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert path.read_bytes() == original


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
    assert load_manifest(str(path)).nodes["bootstrap"].status == "todo"


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
        node, store, stage, *, expected_review_subject=None, sync_contract=False,
    ):
        nonlocal failed
        if node.id == "started-dependent" and not failed:
            failed = True
            raise RuntimeError("simulated Store interruption")
        return original_sync(
            node, store, stage,
            expected_review_subject=expected_review_subject,
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

    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert engine.store.get_work_item(first.id).status == WorkItemStatus.DONE
    assert engine.store.get_work_item(second.id).status == WorkItemStatus.TODO
    assert result["sync"]["already_complete"] == ["bootstrap"]
    assert result["sync"]["synced"] == ["started-dependent"]
    completed = load_manifest(str(path)).meta["amendment_apply"]["nodes"]
    assert completed["bootstrap"]["state"] == "synced"
    assert completed["started-dependent"]["state"] == "synced"


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

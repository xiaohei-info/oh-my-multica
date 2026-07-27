import copy
import json
from pathlib import Path

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
from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItem, WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import loop
from omac.pipeline.dispatch import submit


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


def _responsibility_update(*, historical=False, action_id="ACT-BOOT-01"):
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
    assert reviewed["base"]["evidence_sha256"] == {"bootstrap": amendment_mod.work_item_evidence_digest(item)}
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
    assert ledger["before_contract_sha256"] == correction["before_contract_sha256"]
    assert engine.store.get_work_item(item.id).status == WorkItemStatus.DONE

    repeated = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    assert repeated["sync"]["already_complete"] == ["bootstrap"]


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
    assert Path(result["amendment_file"]).exists()


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

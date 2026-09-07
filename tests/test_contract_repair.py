from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from omac.core.amendment import (
    AMENDMENT_IDENTITY_SCHEMA,
    _amendment_id,
    manifest_definition_digest,
    manifest_digest,
)
from omac.core.manifest import Contract, Manifest, Node, _dump_contract, load_manifest, save_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines import create_engine
from omac.engines.models import AgentRunObservation, EngineConfig, WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline.contract_repair import (
    _contract_digest,
    repair_contract_commands,
)


def _approved_contract(verification_command: str, gate_command: str) -> dict:
    return {
        "objective": "live authority",
        "acceptance": ["live checks pass"],
        "non_goals": ["no scope creep"],
        "verification_commands": [verification_command],
        "integration_gates": [{
            "name": "live-gate",
            "layer": "control-plane",
            "delivery_goal": "live authority",
            "source_of_truth": ["docs/live.md"],
            "covers": ["live"],
            "acceptance_refs": ["live checks pass"],
            "commands": [gate_command],
        }],
        "pr_base": "main",
        "coverage_gate": 0,
    }


def _setup(tmp_path: Path):
    engine = create_engine(
        "mock",
        EngineConfig("mock", "ws", extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    approved_contract = _approved_contract(
        'test -n "${OAC_WORKSPACE_LIVE_CONFIG:-}"',
        'test -n "${OAC_REVIEWED_HEAD:-}"',
    )
    damaged_contract = copy.deepcopy(approved_contract)
    damaged_contract["verification_commands"][0] = 'test -n ""'
    damaged_contract["integration_gates"][0]["commands"][0] = 'test -n ""'
    item = engine.store.create_work_item(
        "ws", "authority", "authority", dag_key="authority",
        worker="alice", kind=TaskKind.DEVELOP,
    )
    item.contract = Contract(**damaged_contract)
    item.status = WorkItemStatus.IN_PROGRESS
    manifest = Manifest(
        meta={
            "last_amendment_id": "amend-live-1",
            "amendment_apply": {
                "amendment_id": "amend-live-1",
                "nodes": {"authority": {"stage": "review", "state": "synced"}},
            },
        },
        nodes={
            "authority": Node(
                id="authority",
                worker="alice",
                work_item_id=item.id,
                status="in_progress",
                contract=item.contract,
            ),
        },
    )
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(manifest, str(manifest_path))
    amendment = {
        "schema": "omac.dag-amendment/v1",
        "reason": "restore shell placeholders",
        "human_confirmation": "applied",
        "review": {"verdict": "pass", "issue_id": "amend-review"},
        "operations": [{
            "op": "update",
            "node": "authority",
            "set": {"contract": approved_contract},
        }],
        "analysis": {
            "minimal_rerun": {},
            "historical_contract_corrections": [],
        },
        "base": {
            "manifest_sha256": manifest_digest(manifest),
            "definition_sha256": manifest_definition_digest(manifest),
            "evidence_sha256": {},
        },
        "identity_schema": AMENDMENT_IDENTITY_SCHEMA,
    }
    amendment["amendment_id"] = _amendment_id(
        amendment["base"]["definition_sha256"],
        amendment,
        amendment["analysis"]["minimal_rerun"],
        amendment["analysis"]["historical_contract_corrections"],
        amendment["base"]["evidence_sha256"],
        manifest_digest_value=amendment["base"]["manifest_sha256"],
        issue_id=amendment["review"]["issue_id"],
        reviewer_verdict="pass",
    )
    manifest.meta["last_amendment_id"] = amendment["amendment_id"]
    manifest.meta["amendment_apply"]["amendment_id"] = amendment["amendment_id"]
    manifest.meta["amendment_apply"]["nodes"]["authority"] = {
        "stage": "review",
        "state": "synced",
        "expected_contract_sha256": _contract_digest(approved_contract),
    }
    save_manifest(manifest, str(manifest_path))
    amendment_path = tmp_path / "approved.amendment.yaml"
    amendment_path.write_text(yaml.safe_dump(amendment, sort_keys=False))
    return engine, item, manifest, manifest_path, amendment_path, approved_contract


def test_repair_contract_commands_restores_exact_shell_damage_and_preserves_runtime(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    before = {
        "status": item.status,
        "phase": item.phase,
        "work_item_id": manifest.nodes["authority"].work_item_id,
        "bounces": item.bounces.as_dict(),
    }

    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    repaired = load_manifest(str(manifest_path))
    assert _dump_contract(repaired.nodes["authority"].contract) == approved
    current = engine.store.get_work_item(item.id)
    assert current.contract_ref["sha256"] == _contract_digest(approved)
    assert current.status is before["status"]
    assert current.phase is before["phase"]
    assert repaired.nodes["authority"].work_item_id == before["work_item_id"]
    assert current.bounces.as_dict() == before["bounces"]

    repeated = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))
    assert repeated["repair_id"] == result["repair_id"]


def test_repair_rejects_contract_drift_outside_known_empty_expansion(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    manifest.nodes["authority"].contract.objective = "unexpected drift"
    save_manifest(manifest, str(manifest_path))
    before = Path(manifest_path).read_bytes()

    with pytest.raises(ValidationError, match="outside verification command"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert Path(manifest_path).read_bytes() == before
    assert engine.store.get_work_item(item.id).contract_ref is None


def test_repair_rejects_malformed_receipt_fail_closed(tmp_path, monkeypatch):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    monkeypatch.setattr(
        "omac.pipeline.contract_repair._load_receipt",
        lambda _path: {
            "schema": "omac.contract-command-repair/v1",
            "repair_id": "wrong",
            "targets": [],
        },
    )

    with pytest.raises(NeedsDecision):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))
    assert item.contract_ref is None


def test_repair_rejects_assigned_work_item_before_store_side_effect(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    item.platform_assignee_id = "agent-active"

    with pytest.raises(NeedsDecision, match="platform assignment"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert item.contract_ref is None


def test_repair_rejects_active_run_before_store_side_effect(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    engine.runtime.list_runs = lambda _item_id: [AgentRunObservation(
        id="active", kind="direct", status="running")]

    with pytest.raises(NeedsDecision, match="active/unknown Runs"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert engine.store.get_work_item(item.id).contract_ref is None


def test_repair_lost_response_adopts_one_matching_publication_without_republish(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    digest = _contract_digest(approved)
    publication = {
        "comment_id": "published-comment",
        "attachment_id": "published-contract",
        "filename": "omac-contract-published.yaml",
        "sha256": digest,
    }
    calls = {"set": 0, "sync": 0}

    published = {"value": False}

    def lost_set(_item_id, contract):
        calls["set"] += 1
        item.contract = Contract(**contract)
        published["value"] = True
        raise RuntimeError("response lost after publish")

    def find(_item_id, _digest):
        return [publication] if published["value"] else []

    def sync(_item_id, ref):
        calls["sync"] += 1
        item.contract = Contract(**approved)
        item.contract_ref = dict(ref)

    engine.store.set_node_contract = lost_set
    engine.store.find_contract_publications = find
    engine.store.sync_contract_ref = sync

    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert calls == {"set": 1, "sync": 1}
    assert item.contract_ref == publication
    assert _dump_contract(load_manifest(str(manifest_path)).nodes["authority"].contract) == approved


def test_repair_manifest_failure_resumes_without_republishing_store_contract(
        tmp_path, monkeypatch):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    calls = {"set": 0}
    original_set = engine.store.set_node_contract

    def count_set(*args, **kwargs):
        calls["set"] += 1
        return original_set(*args, **kwargs)

    monkeypatch.setattr(engine.store, "set_node_contract", count_set)
    original_save = save_manifest
    failed = {"value": False}

    def fail_once(current_manifest, path):
        if not failed["value"]:
            failed["value"] = True
            raise OSError("manifest write interrupted")
        return original_save(current_manifest, path)

    monkeypatch.setattr("omac.pipeline.contract_repair.save_manifest", fail_once)
    with pytest.raises(OSError, match="manifest write interrupted"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))
    assert calls["set"] == 1
    assert load_manifest(str(manifest_path)).nodes["authority"].contract.verification_commands == [
        'test -n ""'
    ]

    monkeypatch.setattr("omac.pipeline.contract_repair.save_manifest", original_save)
    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert calls["set"] == 1
    assert _dump_contract(load_manifest(str(manifest_path)).nodes["authority"].contract) == approved
    assert item.contract_ref["sha256"] == _contract_digest(approved)


def test_repair_unknown_store_outcome_is_not_retried(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    calls = {"set": 0}

    def lost_set(*_args):
        calls["set"] += 1
        raise RuntimeError("unknown write result")

    engine.store.set_node_contract = lost_set
    engine.store.find_contract_publications = lambda *_args: []

    with pytest.raises(NeedsDecision, match="unknown"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert calls["set"] == 1
    receipt_files = list((tmp_path / "contract-repair").glob("*.json"))
    assert len(receipt_files) == 1
    receipt = json.loads(receipt_files[0].read_text())
    assert receipt["targets"]["authority"]["store_state"] == "unknown"
    assert engine.store.get_work_item(item.id).contract_ref is None

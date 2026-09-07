from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from omac.core.amendment import (
    AMENDMENT_IDENTITY_SCHEMA,
    APPLY_LEDGER_SCHEMA,
    _amendment_id,
    _digest,
    manifest_definition_digest,
    manifest_digest,
)
from omac.core.manifest import (
    Contract, Manifest, Node, _dump_contract, _load_contract, load_manifest,
    save_manifest,
)
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines import create_engine
from omac.engines.models import AgentRunObservation, EngineConfig, WorkItemStatus
from omac.errors import NeedsDecision, PlatformError, ValidationError
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


def _setup(
        tmp_path: Path, *, added: bool = False,
        extra_update_fields: bool = False):
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
                "schema": APPLY_LEDGER_SCHEMA,
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
    update_set = {"contract": approved_contract}
    if extra_update_fields:
        update_set.update({
            "description": "already applied description",
            "blocked_by": ["already-applied-upstream"],
        })
    amendment = {
        "schema": "omac.dag-amendment/v1",
        "reason": "restore shell placeholders",
        "human_confirmation": "applied",
        "review": {"verdict": "pass", "issue_id": "amend-review"},
        "operations": [
            (
                {
                    "op": "add",
                    "value": {
                        "id": "authority",
                        "worker": "alice",
                        "contract": approved_contract,
                    },
                }
                if added else
                {
                    "op": "update",
                    "node": "authority",
                    "set": update_set,
                }
            ),
        ],
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
    manifest.meta["amendment_apply"]["nodes"]["authority"] = (
        {
            "stage": "authoring",
            "state": "synced",
            "reason": "no existing work item side effect",
        }
        if added else
        {
            "stage": "review",
            "state": "synced",
            "expected_contract_sha256": _digest(
                _dump_contract(_load_contract(approved_contract))),
        }
    )
    save_manifest(manifest, str(manifest_path))
    amendment_path = tmp_path / "approved.amendment.yaml"
    amendment_path.write_text(yaml.safe_dump(amendment, sort_keys=False))
    return engine, item, manifest, manifest_path, amendment_path, approved_contract


def test_repair_does_not_republish_contract_without_command_damage(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    item.contract = Contract(**approved)
    manifest.nodes["authority"].contract = Contract(**approved)
    save_manifest(manifest, str(manifest_path))
    calls = []
    engine.store.set_node_contract = lambda *args: calls.append(args)

    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert calls == []
    assert item.contract_ref is None


def test_repair_does_not_republish_contract_without_command_damage(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    manifest.nodes["authority"].contract = Contract(**approved)
    item.contract = Contract(**approved)
    save_manifest(manifest, str(manifest_path))
    calls = []
    original_set = engine.store.set_node_contract
    engine.store.set_node_contract = lambda *args, **kwargs: (
        calls.append(args), original_set(*args, **kwargs)
    )[-1]

    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert calls == []
    assert item.contract_ref is None


def test_repair_ignores_already_applied_update_fields(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(
        tmp_path, extra_update_fields=True)

    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    repaired = load_manifest(str(manifest_path))
    assert _dump_contract(repaired.nodes["authority"].contract) == approved
    assert repaired.nodes["authority"].description is None
    assert repaired.nodes["authority"].blocked_by == []


def test_repair_restores_contract_commands_for_added_node(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(
        tmp_path, added=True)

    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert _dump_contract(load_manifest(str(manifest_path)).nodes["authority"].contract) == approved
    assert item.contract_ref["sha256"] == _contract_digest(approved)


def test_repair_restores_added_node_without_work_item_definition_only(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(
        tmp_path, added=True)
    manifest.nodes["authority"].work_item_id = None
    manifest.nodes["authority"].status = "todo"
    save_manifest(manifest, str(manifest_path))

    result = repair_contract_commands(
        engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert _dump_contract(load_manifest(str(manifest_path)).nodes["authority"].contract) == approved
    assert item.contract_ref is None


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


def test_repair_rejects_incomplete_amendment_apply_ledger(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    manifest.meta["amendment_apply"]["nodes"]["authority"]["state"] = "pending"
    save_manifest(manifest, str(manifest_path))

    with pytest.raises(NeedsDecision, match="apply"):
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
        "filename": f"omac-contract-{digest[:12]}.yaml",
        "sha256": digest,
        "bytes": 10,
        "work_item_id": item.id,
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


@pytest.mark.parametrize("tampered_state", ["bogus", "pending"])
def test_repair_rejects_tampered_receipt_state_without_republishing(
        tmp_path, tampered_state):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    calls = {"set": 0}

    def lost_set(*_args):
        calls["set"] += 1
        raise RuntimeError("unknown write result")

    engine.store.set_node_contract = lost_set
    engine.store.find_contract_publications = lambda *_args: []
    with pytest.raises(NeedsDecision):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))
    assert calls["set"] == 1

    receipt_path = next((tmp_path / "contract-repair").glob("*.json"))
    receipt = json.loads(receipt_path.read_text())
    receipt["targets"]["authority"]["store_state"] = tampered_state
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(NeedsDecision, match="receipt"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))
    assert calls["set"] == 1
    assert item.contract_ref is None


def test_repair_rejects_store_item_id_mismatch_before_write(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    original_get = engine.store.get_work_item
    calls = []

    def wrong_item(item_id):
        observed = copy.copy(original_get(item_id))
        observed.id = "different-issue"
        return observed

    engine.store.get_work_item = wrong_item
    engine.store.set_node_contract = lambda item_id, _contract: calls.append(item_id)

    with pytest.raises(NeedsDecision, match="WorkItem"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert calls == []
    assert item.contract_ref is None


def test_repair_rejects_tampered_receipt_cas_snapshots(tmp_path, monkeypatch):
    engine, item, manifest, manifest_path, amendment_path, _approved = _setup(tmp_path)
    original_save = save_manifest
    failed = {"value": False}

    def fail_once(current_manifest, path):
        if not failed["value"]:
            failed["value"] = True
            raise OSError("manifest write interrupted")
        return original_save(current_manifest, path)

    monkeypatch.setattr("omac.pipeline.contract_repair.save_manifest", fail_once)
    with pytest.raises(OSError):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))
    monkeypatch.setattr("omac.pipeline.contract_repair.save_manifest", original_save)

    receipt_path = next((tmp_path / "contract-repair").glob("*.json"))
    receipt = json.loads(receipt_path.read_text())
    target = receipt["targets"]["authority"]
    target["store_state"] = "unknown"
    target["runtime_snapshot"] = None
    target["manifest_snapshot"] = None
    receipt_path.write_text(json.dumps(receipt))
    item.status = WorkItemStatus.TODO

    with pytest.raises(NeedsDecision, match="CAS|runtime"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))


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


def test_repair_rejects_wrong_item_on_lost_write_readback(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    original_get = engine.store.get_work_item
    digest = _contract_digest(approved)
    reads = {"count": 0}

    def wrong_recovery_item(item_id):
        reads["count"] += 1
        observed = copy.copy(original_get(item_id))
        if reads["count"] >= 2:
            observed.id = "different-issue"
            observed.contract = Contract(**approved)
            observed.contract_ref = {"sha256": digest}
        return observed

    def lost_set(*_args):
        raise RuntimeError("response lost after publish")

    engine.store.get_work_item = wrong_recovery_item
    engine.store.set_node_contract = lost_set
    engine.store.find_contract_publications = lambda *_args: []

    with pytest.raises(NeedsDecision, match="WorkItem"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert _dump_contract(load_manifest(str(manifest_path)).nodes["authority"].contract) != approved
    assert reads["count"] >= 2
    assert item.contract_ref is None


def test_repair_rejects_unbound_publication_before_any_store_write(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    digest = _contract_digest(approved)
    calls = []
    engine.store.find_contract_publications = lambda *_args: [{
        "comment_id": "comment-contract",
        "attachment_id": "attachment-contract",
        "filename": f"omac-contract-{digest[:12]}.yaml",
        "sha256": digest,
        "bytes": 10,
    }]
    engine.store.set_node_contract = lambda *args: calls.append(args)

    with pytest.raises(PlatformError, match="bound"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert calls == []
    assert item.contract_ref is None


def test_repair_recovers_ref_write_failure_without_republishing_contract(tmp_path):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    calls = {"set": 0, "sync": 0}
    published = {"value": False}
    publication = {
        "comment_id": "comment-contract",
        "attachment_id": "attachment-contract",
        "filename": f"omac-contract-{_contract_digest(approved)[:12]}.yaml",
        "sha256": _contract_digest(approved),
        "bytes": 10,
        "work_item_id": item.id,
    }

    def lost_set(_item_id, contract):
        calls["set"] += 1
        item.contract = Contract(**contract)
        published["value"] = True
        raise RuntimeError("response lost after publish")

    def find(_item_id, _digest):
        return [publication] if published["value"] else []

    def sync(_item_id, ref):
        calls["sync"] += 1
        if calls["sync"] == 1:
            raise PlatformError("ref metadata write failed")
        item.contract_ref = dict(ref)

    engine.store.set_node_contract = lost_set
    engine.store.find_contract_publications = find
    engine.store.sync_contract_ref = sync
    with pytest.raises(NeedsDecision, match="unknown"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))
    assert calls == {"set": 1, "sync": 1}

    result = repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert calls == {"set": 1, "sync": 2}
    assert _dump_contract(load_manifest(str(manifest_path)).nodes["authority"].contract) == approved


def test_repair_receipt_failure_after_manifest_save_resumes_without_store_republish(
        tmp_path, monkeypatch):
    engine, item, manifest, manifest_path, amendment_path, approved = _setup(tmp_path)
    calls = {"set": 0, "writes": 0}
    original_set = engine.store.set_node_contract
    original_write = __import__(
        "omac.pipeline.contract_repair", fromlist=["_write_receipt"]
    )._write_receipt

    def count_set(*args, **kwargs):
        calls["set"] += 1
        return original_set(*args, **kwargs)

    def fail_after_manifest(path, receipt):
        calls["writes"] += 1
        if calls["writes"] == 4:
            raise OSError("receipt write interrupted")
        return original_write(path, receipt)

    monkeypatch.setattr(engine.store, "set_node_contract", count_set)
    monkeypatch.setattr("omac.pipeline.contract_repair._write_receipt", fail_after_manifest)
    with pytest.raises(OSError, match="receipt write interrupted"):
        repair_contract_commands(engine, str(manifest_path), str(amendment_path))
    assert calls["set"] == 1
    assert _dump_contract(load_manifest(str(manifest_path)).nodes["authority"].contract) == approved

    monkeypatch.setattr("omac.pipeline.contract_repair._write_receipt", original_write)
    result = repair_contract_commands(engine, str(manifest_path), str(amendment_path))

    assert result["state"] == "synced"
    assert calls["set"] == 1
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

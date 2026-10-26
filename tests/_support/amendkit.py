"""Builders for amendment tests: manifests, proposals and acceptance documents (no platform involved)."""
from __future__ import annotations

from _support.state import save_literal
from omac.core.acceptance import load_acceptance_doc
from omac.core.manifest import EvidenceMode, MISSING_CONSUMES, ProducedArtifact, load_manifest, save_manifest


def dag_manifest(tmp_path, ids=None, name="dag"):
    ids = {"bootstrap": "1", **(ids or {})}
    path = tmp_path / f"{name}.yaml"
    save_literal(path, {
        "meta": {"name": "demo", "closeout_node": "closeout"},
        "nodes": [
            {
                "id": "bootstrap",
                "worker": "alice",
                "reviewer": "bob",
                "blocked_by": [],
                "work_item_id": ids["bootstrap"],
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
    })
    return path


def proposal(*operations):
    return {
        "schema": "omac.dag-amendment/v1",
        "reason": "Reviewer found an invalid acceptance mapping",
        "operations": list(operations),
    }


def old_worker_handoff(worker="alice"):
    return {
        "schema": "omac.worker-handoff/v1",
        "state": "pending",
        "target_worker": worker,
        "gate": "review",
        "source_review_subject_digest": "old-review-subject",
        "source_review_round": 1,
        "target_review_bounce": 1,
    }


def topology_manifest(tmp_path, ids=None):
    ids = {"bootstrap": "1", "started-dependent": "2", **(ids or {})}
    path = tmp_path / "topology.yaml"
    save_literal(path, {
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
                "work_item_id": ids["bootstrap"],
                "status": "blocked",
                "contract": contract_update()["set"]["contract"],
            },
            {
                "id": "started-dependent",
                "worker": "charlie",
                "reviewer": "bob",
                "blocked_by": ["bootstrap"],
                "work_item_id": ids["started-dependent"],
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
    })
    return path


def contract_update():
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


def typed_boundary_contract_update():
    operation = contract_update()
    operation["set"]["contract"].update({
        "evidence_mode": "fixture",
        "produces": [{"artifact_id": "tooling-package"}],
        "consumes": [],
    })
    return operation


def transitional_boundary_contract_update():
    operation = typed_boundary_contract_update()
    operation["set"]["contract"].pop("consumes")
    return operation


def add_typed_boundary(path):
    manifest = load_manifest(str(path))
    contract = manifest.nodes["bootstrap"].contract
    contract.evidence_mode = EvidenceMode.FIXTURE
    contract.produces = [ProducedArtifact("tooling-package")]
    contract.consumes = []
    save_manifest(manifest, str(path))


def add_transitional_boundary(path):
    manifest = load_manifest(str(path))
    contract = manifest.nodes["bootstrap"].contract
    contract.evidence_mode = EvidenceMode.FIXTURE
    contract.produces = [ProducedArtifact("tooling-package")]
    contract.consumes = MISSING_CONSUMES
    save_manifest(manifest, str(path))


def explicit_responsibility_update(action_id="ACT-BOOT-01"):
    operation = contract_update()
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


def responsibility_update(
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


def responsibility_acceptance_doc():
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


POOL = {"alice", "bob", "charlie"}


def seal_review_fixture(store, item_id):
    """A prior controller-sealed submission, as review recovery expects to find it."""
    from dataclasses import replace

    from _support.sealing import seal_delivery

    current = store.get_work_item(item_id)
    old_head = (current.artifacts or {}).get("head_sha")
    sealed = seal_delivery(store, item_id, (current.artifacts or {}).get("pr_url", "https://example.test/pr/1"),
                           current.verification or {"commands": []})
    if old_head:
        store.update_work_item_metadata(item_id, artifacts={**sealed.artifacts, "head_sha": old_head},
                                        delivery_identity=replace(sealed.delivery_identity, pr_head_sha=old_head))


def bootstrap(kit, tmp_path, status=None, name="dag"):
    """The `bootstrap` node's issue (blocked unless ``status`` says otherwise) and the DAG manifest that points at it."""
    from omac.core.workitem import WorkItemStatus

    item = kit.store.create_work_item(kit.workspace, "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    kit.store.update_status(item.id, status or WorkItemStatus.BLOCKED)
    return dag_manifest(tmp_path, {"bootstrap": item.id}, name), kit.store.get_work_item(item.id)

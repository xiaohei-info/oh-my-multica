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


def pass_with_nits_amendment_fixture(kit, tmp_path):
    """A blocked `bootstrap` node and a reviewed amendment issue that passed with nits, plus the amendment file."""
    import yaml

    from omac.core.amendment import build_reviewed_amendment
    from omac.core.review_convergence import review_subject_digest
    from omac.core.taskmeta import TaskKind, TaskPhase
    from omac.core.workitem import WorkItemStatus

    path, target = bootstrap(kit, tmp_path)
    issue = kit.store.create_work_item(kit.workspace, "amendment", "desc", "amend-pass-with-nits", "alice", reviewer="bob",
                                       kind=TaskKind.AMENDMENT)
    amendment = proposal(contract_update())
    report = {"full_review_completed": True, "blockers": [], "nits": ["tighten the operator guide", "add one regression example"]}
    ledger = {"schema": "omac.review-ledger/v1", "rounds": [], "blockers": []}
    kit.store.update_work_item_metadata(
        issue.id, deliverable=yaml.safe_dump(amendment, sort_keys=False), review_report=report,
        review_report_source=yaml.safe_dump(report, sort_keys=False), review_ledger=ledger,
        review_ledger_source=yaml.safe_dump(ledger, sort_keys=False), review_verdict="pass-with-nits", phase=TaskPhase.CONFIRMATION)
    issue = kit.store.get_work_item(issue.id)
    kit.store.update_work_item_metadata(issue.id, review_subject_digest=review_subject_digest(issue, 1))
    kit.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
    seal_review_fixture(kit.store, target.id)
    reviewed = build_reviewed_amendment(load_manifest(str(path)), amendment, kit.store, issue_id=issue.id,
                                        reviewer_verdict="pass-with-nits")
    amendment_file = tmp_path / "pass-with-nits.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    return path, issue, amendment_file, reviewed


def seed_legacy_convergence_review_control(store, item, snapshot):
    """The control facts a node carries after a legacy convergence block: ledger, decision, continuation and Worker handoff."""
    import yaml

    from omac.core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskPhase

    decision = {
        "schema": DECISION_REQUIRED_SCHEMA, "reason_code": "review-convergence-ledger-unverifiable", "kind": "develop",
        "phase": "review", "gate": "review-convergence", "rounds": 3, "resume_issue_id": item.id, "node_id": snapshot["dag_key"],
        "verdict": "reject", "recommended_action": "dag-amendment",
        "convergence": {"schema": "omac.review-convergence-decision/v1", "mode": "unverifiable-legacy-ledger",
                        "reason_code": "review-convergence-ledger-unverifiable", "cycle_count": 3}}
    store.update_work_item_metadata(
        item.id, phase=TaskPhase.REVIEW, worker_bounce=15, review_bounce=3, review_verdict="reject",
        review_report={"blockers": ["legacy convergence audit"]}, review_report_source="blockers:\n  - legacy convergence audit\n",
        review_subject_digest="subject-round-3", review_ledger=snapshot["review_ledger"],
        review_ledger_source=yaml.safe_dump(snapshot["review_ledger"], sort_keys=False),
        review_continuation={"schema": "omac.review-continuation/v1", "authorized_through_round": 6},
        decision_required=decision, worker_handoff=snapshot["worker_handoff"])


def stage_amendment_with_handoff(kit, tmp_path, stage):
    """A blocked `bootstrap` node with an old Worker handoff and exhausted budgets, and the reviewed amendment that resumes ``stage``."""
    from omac.core.amendment import build_reviewed_amendment
    from omac.core.workitem import WorkItemStatus

    item = kit.store.create_work_item(kit.workspace, "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    path = dag_manifest(tmp_path, {"bootstrap": item.id})
    kit.store.update_work_item_metadata(
        item.id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []}, review_verdict="pass", worker_bounce=3, review_bounce=4,
        merge_bounce=5, worker_handoff=old_worker_handoff())
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    seal_review_fixture(kit.store, item.id)
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal(responsibility_update(resume_stage=stage)), kit.store,
                                        issue_id=f"amendment-{stage}", reviewer_verdict="pass",
                                        acceptance=responsibility_acceptance_doc())
    return path, item, reviewed


def apply_exhausted_stage_amendment(kit, tmp_path, stage):
    from omac.core.amendment import apply_amendment

    path, item, reviewed = stage_amendment_with_handoff(kit, tmp_path, stage)
    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    return path, item


def legacy_synced_authoring_accept_fixture(kit, tmp_path, legacy_convergence_snapshot):
    """An authoring amendment applied by an older OMAC: its ledger entry says `synced`, but the issue still has the old projection."""
    import yaml

    from omac.core.amendment import apply_amendment, build_reviewed_amendment
    from omac.core.taskmeta import TaskKind, TaskPhase
    from omac.core.workitem import WorkItemStatus

    snapshot = legacy_convergence_snapshot["work_item"]
    target = kit.store.create_work_item(kit.workspace, "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    path = dag_manifest(tmp_path, {"bootstrap": target.id})
    kit.store.set_node_contract(target.id, load_manifest(str(path)).nodes["bootstrap"].contract)
    seed_legacy_convergence_review_control(kit.store, target, snapshot)
    kit.store.update_work_item_metadata(target.id, worker_bounce=14)
    kit.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = kit.store.create_work_item(kit.workspace, "amendment", "desc", "amend-proj-850", "alice", reviewer="bob",
                                                 kind=TaskKind.AMENDMENT)
    kit.store.update_work_item_metadata(amendment_issue.id, phase=TaskPhase.CONFIRMATION, review_verdict="pass")
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal(responsibility_update(resume_stage="authoring")), kit.store,
                                        issue_id=amendment_issue.id, reviewer_verdict="pass",
                                        acceptance=responsibility_acceptance_doc())
    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    legacy = load_manifest(str(path))
    entry = legacy.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry.pop("expected_review_generation", None)
    entry.pop("observed", None)
    entry["state"] = "synced"
    save_manifest(legacy, str(path))

    seed_legacy_convergence_review_control(kit.store, target, snapshot)
    for key, empty in (("review_generation", ""), ("review_ledger_generation", ""), ("bounce_baseline", "{}")):
        kit.stack.multica("issue", "metadata", "set", target.id, "--key", key, "--value", empty)
    kit.store.update_work_item_metadata(target.id, phase=TaskPhase.AUTHORING, worker_bounce=17)
    kit.store.update_status(target.id, WorkItemStatus.IN_PROGRESS)

    reviewed["human_confirmation"] = "applied"
    reviewed["apply_result"] = {"legacy": True}
    amendment_file = tmp_path / "proj-850.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    kit.store.update_status(amendment_issue.id, WorkItemStatus.DONE)
    return path, amendment_file, target, reviewed

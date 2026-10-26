"""Reviewed amendments on a real Multica: stage recovery, what an amendment's identity binds, and how contracts survive apply."""
import copy

import pytest
import yaml

import omac.core.amendment as amendment_mod
from _support.amendkit import (
    POOL, add_typed_boundary, bootstrap, contract_update, proposal,
    responsibility_acceptance_doc, responsibility_update, seal_review_fixture, transitional_boundary_contract_update,
    typed_boundary_contract_update,
)
from omac.core.amendment import apply_amendment, build_reviewed_amendment, validate_proposal
from omac.core.manifest import Contract, EvidenceMode, MISSING_CONSUMES, Node, ProducedArtifact, _load_contract, load_manifest
from omac.core.stage_recovery import prepare_stage_recovery
from omac.core.taskmeta import DECISION_REQUIRED_SCHEMA, DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity, TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline.dispatch import build_show_output


def reviewed_for(store, path, *operations, **kwargs):
    kwargs.setdefault("reviewer_verdict", "pass")
    return build_reviewed_amendment(load_manifest(str(path)), proposal(*operations), store, issue_id="amendment-issue", **kwargs)


# ---- stage recovery ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stage", ["review", "merging"])
def test_review_and_merge_stage_recovery_preserve_delivery_identity(kit, stage):
    """Recovery that starts no new Worker generation keeps the causal delivery identity."""
    node = Node(id="a", worker="alice", reviewer="bob", contract=Contract())
    item = kit.store.create_work_item(kit.workspace, "a", "a", dag_key="a", worker="alice", reviewer="bob", kind=TaskKind.DEVELOP)
    node.work_item_id = item.id
    identity = DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA, handoff_generation="generation-1", worker="alice", agent_id="agent-alice",
        run_id="run-1", pr_url="https://github.com/acme/repo/pull/1", pr_head_sha="head-1", verification_sha256="verification-1",
        verification_attachment_id="attachment-1", verification_comment_id="comment-1", verification_uploader_id="agent-alice",
        verification_uploader_type="agent", verification_task_id="run-1", verification_created_at="2026-07-30T01:00:00Z")
    kit.store.update_work_item_metadata(
        item.id, artifacts={"pr_url": identity.pr_url, "head_sha": identity.pr_head_sha}, verification={"commands": []},
        verification_source="commands: []\n", delivery_identity=identity, review_verdict="pass" if stage == "merging" else "")
    ref = kit.store.get_work_item(item.id).verification_ref
    # the identity must name the verification attachment the platform really holds
    sealed = DeliveryIdentity(**{**identity.as_dict(), "verification_attachment_id": ref["attachment_id"],
                                 "verification_comment_id": ref["comment_id"], "verification_sha256": ref["sha256"]})
    kit.store.update_work_item_metadata(item.id, delivery_identity=sealed)

    prepare_stage_recovery(node, kit.store, stage)

    assert kit.store.get_work_item(item.id).delivery_identity == sealed


@pytest.mark.parametrize("stage", ["authoring", "review"])
def test_stage_recovery_retires_the_previous_assignment(kit, stage):
    node = Node(id="a", worker="alice", reviewer="bob", contract=Contract())
    item = kit.store.create_work_item(kit.workspace, "a", "a", dag_key="a", worker="alice", reviewer="bob", kind=TaskKind.DEVELOP)
    node.work_item_id = item.id
    kit.assign(item.id, "carol")          # the previous stage's assignee, whose Run is still the platform's fact
    if stage == "review":
        seal_review_fixture(kit.store, item.id)

    prepare_stage_recovery(node, kit.store, stage)

    recovered = kit.store.get_work_item(item.id)
    assert recovered.platform_assignee_id is None and not recovered.reviewer


def test_authoring_recovery_hides_legacy_convergence_review_control_but_keeps_audit(kit, legacy_convergence_snapshot):
    """A new authoring generation must not keep consuming the convergence control facts from before the amendment."""
    snapshot = legacy_convergence_snapshot["work_item"]
    item = kit.store.create_work_item(
        kit.workspace, "PROJ-849", "snapshot", dag_key=snapshot["dag_key"], worker=snapshot["worker_handoff"]["target_worker"],
        reviewer="reviewer-redacted", kind=TaskKind(snapshot["kind"]), initial_status=WorkItemStatus.BLOCKED)
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA, "reason_code": "review-convergence-ledger-unverifiable", "kind": "develop",
        "phase": "review", "gate": "review-convergence", "rounds": 3, "resume_issue_id": item.id, "node_id": snapshot["dag_key"],
        "verdict": "reject", "recommended_action": "dag-amendment",
        "convergence": {"schema": "omac.review-convergence-decision/v1", "mode": "unverifiable-legacy-ledger",
                        "reason_code": "review-convergence-ledger-unverifiable", "cycle_count": 3}}
    kit.store.update_work_item_metadata(
        item.id, phase=TaskPhase.REVIEW, worker_bounce=15, review_bounce=snapshot["bounces"]["review"], review_verdict="reject",
        review_report={"blockers": ["legacy convergence audit"]}, review_report_source="blockers:\n  - legacy convergence audit\n",
        review_subject_digest="subject-round-3", review_ledger=snapshot["review_ledger"],
        review_ledger_source=yaml.safe_dump(snapshot["review_ledger"], sort_keys=False),
        review_continuation={"schema": "omac.review-continuation/v1", "authorized_through_round": 6},
        decision_required=decision, worker_handoff=snapshot["worker_handoff"])
    node = Node(id=snapshot["dag_key"], worker=snapshot["worker_handoff"]["target_worker"], reviewer="reviewer-redacted",
                contract=Contract(objective="amended authoring contract"), work_item_id=item.id)

    prepare_stage_recovery(node, kit.store, "authoring", sync_contract=True)

    recovered = kit.store.get_work_item(item.id)
    assert (recovered.phase, recovered.status) == (TaskPhase.AUTHORING, WorkItemStatus.TODO)
    assert (recovered.decision_required, recovered.review_verdict, recovered.review_report,
            recovered.review_subject_digest, recovered.worker_handoff) == (None, None, None, None, None)
    assert recovered.review_ledger == snapshot["review_ledger"] and recovered.review_ledger_ref is not None
    output = build_show_output(recovered, "worker:worker-redacted")
    assert output.get("ok", True) is True
    assert not {"decision_required", "review_state", "required_closures"} & set(output["context"])


# ---- contracts and responsibility through apply ---------------------------------------------------------------------------

def test_complete_contract_update_preserves_and_validates_responsibility_fields(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    acceptance = responsibility_acceptance_doc()
    seal_review_fixture(kit.store, item.id)
    reviewed = reviewed_for(kit.store, path, responsibility_update(), acceptance=acceptance)

    result = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=acceptance)

    assert result["minimal_rerun"] == {"review": ["bootstrap"], "authoring": [], "merging": []}
    for contract in (load_manifest(str(path)).nodes["bootstrap"].contract, _load_contract(kit.store.get_work_item(item.id).contract)):
        assert contract.acceptance == [] and contract.acceptance_claims == ["UJ-BOOTSTRAP"]
        assert contract.acceptance_contributions == [{"flow_id": "UJ-BOOTSTRAP", "action_ids": ["ACT-BOOT-01"]}]
        assert contract.acceptance_refs == ["UJ-BOOTSTRAP"]
    invalid = proposal(responsibility_update(action_id="UNKNOWN-ACTION"))
    errors = validate_proposal(load_manifest(str(path)), invalid, POOL, acceptance=acceptance)
    assert any("unknown business action 'UNKNOWN-ACTION'" in error for error in errors)


def test_apply_and_restart_fail_closed_on_tampered_null_consumes(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    add_typed_boundary(path)
    reviewed = reviewed_for(kit.store, path, typed_boundary_contract_update())
    reviewed["operations"][0]["set"]["contract"]["consumes"] = None
    reviewed["amendment_id"] = amendment_mod._amendment_id(
        reviewed["base"]["definition_sha256"], reviewed, reviewed["analysis"]["minimal_rerun"],
        reviewed["analysis"]["historical_contract_corrections"], reviewed["base"]["evidence_sha256"],
        manifest_digest_value=reviewed["base"]["manifest_sha256"], acceptance_digest=reviewed["base"].get("acceptance_sha256"),
        issue_id=reviewed["review"]["issue_id"], reviewer_verdict=reviewed["review"]["verdict"])
    original = path.read_bytes()

    for _attempt in range(2):
        with pytest.raises(ValidationError, match="contract.consumes must be a list"):
            apply_amendment(str(path), reviewed, kit.store, POOL)
        assert path.read_bytes() == original
        assert load_manifest(str(path)).nodes["bootstrap"].contract.consumes == []


def test_reviewed_amendment_identity_binds_cas_and_review_envelope(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    reviewed = reviewed_for(kit.store, path, contract_update())
    original = path.read_bytes()

    for mutate in (lambda a: a["base"].update({"manifest_sha256": "forged-manifest-digest"}),
                   lambda a: a["review"].update({"issue_id": "other-amendment-issue"})):
        tampered = copy.deepcopy(reviewed)
        mutate(tampered)
        with pytest.raises(ValidationError, match="identity does not match"):
            apply_amendment(str(path), tampered, kit.store, POOL)
        assert path.read_bytes() == original


def test_legacy_amendment_identity_remains_readable(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    seal_review_fixture(kit.store, item.id)
    reviewed = reviewed_for(kit.store, path, contract_update())
    reviewed.pop("identity_schema")
    reviewed["amendment_id"] = amendment_mod._amendment_id(
        reviewed["base"]["definition_sha256"], reviewed, reviewed["analysis"]["minimal_rerun"],
        reviewed["analysis"]["historical_contract_corrections"], reviewed["base"]["evidence_sha256"])

    assert apply_amendment(str(path), reviewed, kit.store, POOL)["amendment_id"] == reviewed["amendment_id"]


def test_reviewed_amendment_identity_binds_acceptance_digest_presence(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    acceptance = responsibility_acceptance_doc()
    reviewed = reviewed_for(kit.store, path, responsibility_update(), acceptance=acceptance)
    reviewed["base"].pop("acceptance_sha256")

    with pytest.raises(ValidationError, match="identity does not match"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=acceptance)


def test_an_explicit_contract_boundary_clear_survives_apply_reload_and_restart(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    add_typed_boundary(path)
    operation = contract_update()
    operation["clear_contract_boundary"] = True
    reviewed = reviewed_for(kit.store, path, operation)

    first = apply_amendment(str(path), reviewed, kit.store, POOL)
    reloaded = load_manifest(str(path))
    second = apply_amendment(str(path), reviewed, kit.store, POOL)

    contract = reloaded.nodes["bootstrap"].contract
    assert first["sync"]["synced"] == ["bootstrap"] and second["sync"]["already_complete"] == ["bootstrap"]
    assert (contract.evidence_mode, contract.produces) == (None, []) and contract.consumes is MISSING_CONSUMES


def test_a_typed_contract_boundary_preserved_by_an_amendment_survives_apply_and_reload(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    add_typed_boundary(path)
    seal_review_fixture(kit.store, item.id)
    reviewed = reviewed_for(kit.store, path, typed_boundary_contract_update())

    apply_amendment(str(path), reviewed, kit.store, POOL)

    contract = load_manifest(str(path)).nodes["bootstrap"].contract
    assert (contract.evidence_mode, contract.produces, contract.consumes) == (
        EvidenceMode.FIXTURE, [ProducedArtifact("tooling-package")], [])


def test_transitional_consumes_survives_amendment_apply_reload_and_restart(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    reviewed = reviewed_for(kit.store, path, transitional_boundary_contract_update())

    first = apply_amendment(str(path), reviewed, kit.store, POOL)
    reloaded = load_manifest(str(path))
    second = apply_amendment(str(path), reviewed, kit.store, POOL)

    assert first["sync"]["synced"] == ["bootstrap"] and second["sync"]["already_complete"] == ["bootstrap"]
    assert reloaded.nodes["bootstrap"].contract.consumes is MISSING_CONSUMES
    assert "consumes" not in amendment_mod._dump_contract(reloaded.nodes["bootstrap"].contract)


def test_acceptance_drift_after_amendment_review_fails_closed(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    original = path.read_bytes()
    acceptance = responsibility_acceptance_doc()
    reviewed = reviewed_for(kit.store, path, responsibility_update(), acceptance=acceptance)
    changed = copy.deepcopy(acceptance)
    changed.flows[0].actions[0].expected = "a different authority"

    with pytest.raises(ValidationError, match="acceptance document changed"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=changed)

    assert path.read_bytes() == original

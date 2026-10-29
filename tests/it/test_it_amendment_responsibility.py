"""Responsibility and historical-correction amendments on a real Multica: what apply writes, what it must not touch, and how it resumes."""
import copy
import json

import pytest
import yaml

import omac.core.amendment as amendment_mod
import omac.pipeline.amendment as amendment_pipeline
from _support.amendkit import (
    POOL, bootstrap, dag_manifest, proposal, responsibility_acceptance_doc, responsibility_update, seal_review_fixture,
)
from _support.taskkit import run, submit_step
from omac.core.amendment import build_reviewed_amendment, validate_proposal
from omac.pipeline.amendment import apply_amendment
from omac.core.manifest import (
    ConsumedArtifact, Contract, EvidenceMode, MISSING_CONSUMES, Node, ProducedArtifact, load_manifest, save_manifest,
)
from omac.core.review_convergence import review_subject_digest
from omac.core.taskmeta import TaskKind
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, PlatformError, ValidationError
from omac.pipeline import loop

from _support.amendkit import old_worker_handoff

ACCEPTANCE_FIELDS = ("status", "phase", "worker", "reviewer", "artifacts", "verification", "review_verdict", "review_report",
                     "review_subject_digest")


def reviewed_for(kit, path, *operations, **kwargs):
    kwargs.setdefault("reviewer_verdict", "pass")
    return build_reviewed_amendment(load_manifest(str(path)), proposal(*operations), kit.store, issue_id="amendment-issue", **kwargs)


def mark_done_merged(path, merged_at=None):
    manifest = load_manifest(str(path))
    node = manifest.nodes["bootstrap"]
    node.status, node.merged = "done", True
    if merged_at:
        node.merged_at = merged_at
    save_manifest(manifest, str(path))
    return manifest, node


def delivered(kit, item, **extra):
    kit.store.update_work_item_metadata(
        item.id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []}, review_verdict="pass", review_report={"blockers": []}, **extra)


# ---- historical correction of a done and merged node ---------------------------------------------------------------------

def test_a_historical_responsibility_correction_of_a_merged_node_changes_no_delivery_fact_and_never_recovers(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path, status=WorkItemStatus.DONE)
    delivered(kit, item)
    manifest, node = mark_done_merged(path, "2026-07-26T23:03:59Z")
    kit.store.set_node_contract(item.id, node.contract)
    before = copy.deepcopy(kit.store.get_work_item(item.id))
    runtime_before = {k: getattr(node, k) for k in ("work_item_id", "status", "merged", "merged_at", "merge_request_state")}
    acceptance = responsibility_acceptance_doc()
    reviewed = build_reviewed_amendment(manifest, proposal(responsibility_update(historical=True)), kit.store,
                                        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=acceptance)

    assert reviewed["analysis"]["minimal_rerun"] == {"review": [], "authoring": [], "merging": []}
    assert reviewed["base"]["evidence_sha256"] == {"bootstrap": amendment_mod.historical_work_item_evidence_digest(before)}
    correction = reviewed["analysis"]["historical_contract_corrections"][0]
    assert correction["node"] == "bootstrap" and correction["before_contract_sha256"] != correction["after_contract_sha256"]
    assert correction["evidence_sha256"] == reviewed["base"]["evidence_sha256"]["bootstrap"]
    assert correction["allowed_field_diff"] == [
        "contract.acceptance", "contract.acceptance_claims", "contract.acceptance_contributions", "contract.acceptance_refs",
        "contract.integration_gates[bootstrap].acceptance_refs"]
    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery",
                            lambda *_a, **_k: pytest.fail("historical correction must not recover Store stages"))
    kit.forbid(kit.store, "set_node_contract", "reset_review", "prepare_review_cycle", "update_work_item_metadata", "update_status",
               "assign_work_item")

    result = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=acceptance)

    updated = load_manifest(str(path)).nodes["bootstrap"]
    assert result["sync"]["already_complete"] == ["bootstrap"]
    assert {k: getattr(updated, k) for k in runtime_before} == runtime_before
    assert (updated.contract.acceptance, updated.contract.acceptance_claims, updated.contract.acceptance_refs) == (
        [], ["UJ-BOOTSTRAP"], ["UJ-BOOTSTRAP"])
    assert updated.contract.acceptance_contributions == [{"flow_id": "UJ-BOOTSTRAP", "action_ids": ["ACT-BOOT-01"]}]
    ledger = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]
    assert (ledger["stage"], ledger["state"], ledger["store_side_effect"]) == ("historical_contract_correction", "synced", "none")
    for key in ("before_contract_sha256", "after_contract_sha256", "before_responsibility_sha256", "after_responsibility_sha256",
                "runtime_facts_sha256", "evidence_sha256", "allowed_field_diff", "reason"):
        assert ledger[key] == correction[key]
    after = kit.store.get_work_item(item.id)
    for field in ("status", "phase", "worker", "reviewer", "artifacts", "verification", "verification_ref", "review_verdict",
                  "review_report", "review_report_ref", "review_subject_digest", "review_ledger", "review_ledger_ref", "contract",
                  "contract_ref"):
        assert getattr(after, field) == getattr(before, field)

    kit.monkeypatch.setattr(kit.store, "get_work_item", lambda *_a, **_k: pytest.fail("a repeated historical accept must not read the Store"))
    assert apply_amendment(str(path), reviewed, kit.store, POOL)["sync"]["already_complete"] == ["bootstrap"]


def drift_review_verdict(kit, item):
    kit.store.update_work_item_metadata(item.id, review_verdict="reject")


def drift_review_report(kit, item):
    kit.store.update_work_item_metadata(item.id, review_report={"blockers": ["late review drift"]}, review_report_source="blockers:\n- late review drift\n")


def drift_review_subject(kit, item):
    kit.store.update_work_item_metadata(item.id, review_subject_digest="changed-review-subject")


def drift_review_ledger(kit, item):
    ledger = {"schema": "omac.review-ledger/v1", "rounds": [1]}
    kit.store.update_work_item_metadata(item.id, review_ledger=ledger, review_ledger_source=yaml.safe_dump(ledger))


def forge_ref(field):
    def drift(kit, item):   # somebody else rewrites the reference on the platform
        kit.stack.multica("issue", "metadata", "set", item.id, "--key", field, "--value", '{"sha256": "changed-%s"}' % field)
    return drift


EVIDENCE_CHANGED = (ValidationError, "delivery evidence changed")
UNREADABLE_LEDGER = (PlatformError, "review ledger attachment")   # a reference to a ledger that cannot be read is refused by the read itself


@pytest.mark.parametrize("drift,refusal", [
    (drift_review_verdict, EVIDENCE_CHANGED), (drift_review_report, EVIDENCE_CHANGED), (forge_ref("review_report_ref"), EVIDENCE_CHANGED),
    (drift_review_subject, EVIDENCE_CHANGED), (drift_review_ledger, EVIDENCE_CHANGED), (forge_ref("review_ledger_ref"), UNREADABLE_LEDGER),
    (forge_ref("verification_ref"), EVIDENCE_CHANGED)],
    ids=["verdict", "report", "report-ref", "subject", "ledger", "ledger-ref", "verification-ref"])
def test_a_historical_apply_fails_closed_when_review_or_reference_evidence_drifts(kit, tmp_path, drift, refusal):
    path, item = bootstrap(kit, tmp_path, status=WorkItemStatus.DONE)
    kit.store.update_work_item_metadata(
        item.id, verification={"subject_digest": "verification-1"}, review_verdict="pass", review_report={"blockers": []},
        review_subject_digest="review-subject-1", review_ledger={"schema": "omac.review-ledger/v1", "rounds": []})
    manifest, node = mark_done_merged(path)
    kit.store.set_node_contract(item.id, node.contract)
    reviewed = build_reviewed_amendment(manifest, proposal(responsibility_update(historical=True)), kit.store,
                                        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=responsibility_acceptance_doc())

    refs = {key: value for key, value in json.loads(kit.stack.multica("issue", "get", item.id, "--output", "json"))["metadata"].items()
            if key.endswith("_ref")}
    drift(kit, item)

    try:
        with pytest.raises(refusal[0], match=refusal[1]):
            apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    finally:   # the workspace is shared by the session: leave no forged reference behind for the tests that list every issue
        for key, value in refs.items():
            kit.stack.multica("issue", "metadata", "set", item.id, "--key", key, "--value", value)
    unchanged = load_manifest(str(path))
    assert unchanged.meta.get("amendment_apply") is None and unchanged.meta.get("last_amendment_id") is None


def test_a_historical_apply_fails_closed_when_manifest_runtime_facts_drift(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path, status=WorkItemStatus.DONE)
    manifest, _ = mark_done_merged(path, "2026-07-26T23:03:59Z")
    reviewed = build_reviewed_amendment(manifest, proposal(responsibility_update(historical=True)), kit.store,
                                        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=responsibility_acceptance_doc())
    drifted = load_manifest(str(path))
    drifted.nodes["bootstrap"].merged_at = "2026-07-27T00:00:00Z"
    save_manifest(drifted, str(path))

    with pytest.raises(ValidationError, match="audit changed"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    unchanged = load_manifest(str(path))
    assert unchanged.meta.get("amendment_apply") is None and unchanged.meta.get("last_amendment_id") is None


def test_the_historical_correction_audit_is_bound_into_the_amendment_identity(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    manifest, _ = mark_done_merged(path)
    reviewed = build_reviewed_amendment(manifest, proposal(responsibility_update(historical=True)), kit.store,
                                        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=responsibility_acceptance_doc())
    reviewed["analysis"]["historical_contract_corrections"][0]["reason"] = "tampered"

    with pytest.raises(ValidationError, match="identity does not match"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())


def test_the_machine_guard_bounds_feedback_about_invalid_gate_refs(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    manifest, _ = mark_done_merged(path)
    operation = responsibility_update(historical=True)
    operation["integration_gate_responsibility_patches"][0]["acceptance_refs"] = ["UJ-BOOTSTRAP", {"bad": "value"}, "UJ-BOOTSTRAP"]
    amendment = tmp_path / "amendment.yaml"
    amendment.write_text(yaml.safe_dump(proposal(operation), sort_keys=False))
    kit.stack.set_scenario("alice", [[submit_step(kit.stack, "--amendment-file", str(amendment))]])

    with pytest.raises(NeedsDecision) as raised:
        run(kit, TaskKind.AMENDMENT, {"title": "invalid gate refs"}, "alice", max_revisions=1,
            guard=lambda item: validate_proposal(manifest, item.deliverable or "", POOL, acceptance=responsibility_acceptance_doc()))

    report = raised.value.report
    assert (report["phase"], report["rounds"]) == ("guard", 1)
    assert "entries must be non-empty strings" in report["last_opinion"] and "must not contain duplicates" in report["last_opinion"]
    item = kit.store.get_work_item(report["item_id"])
    assert item.status == WorkItemStatus.BLOCKED and item.decision_required["reason_code"] == "guard-budget-exhausted"


@pytest.mark.parametrize("consumes_policy", ("omitted", "empty", "allowlist"))
def test_a_historical_correction_preserves_the_consumes_policy_and_publishes_no_contract(kit, tmp_path, consumes_policy):
    path, item = bootstrap(kit, tmp_path, status=WorkItemStatus.DONE)
    manifest = load_manifest(str(path))
    node = manifest.nodes["bootstrap"]
    node.status, node.merged = "done", True
    node.contract.evidence_mode, node.contract.produces = EvidenceMode.FIXTURE, [ProducedArtifact("bootstrap-output")]
    if consumes_policy == "omitted":
        node.contract.consumes = MISSING_CONSUMES
    elif consumes_policy == "empty":
        node.contract.consumes = []
    else:
        manifest.nodes["producer"] = Node(
            id="producer", worker="charlie", blocked_by=[], status="done", merged=True, contract=Contract(
                objective="produce bootstrap input", source_of_truth=["docs/design.md"], acceptance_refs=["UJ-BOOTSTRAP"],
                non_goals=["do not own bootstrap acceptance"], verification_commands=["pytest -q"],
                integration_gates=[{"name": "producer", "layer": "L1", "delivery_goal": "produce bootstrap input",
                                    "source_of_truth": ["docs/design.md"], "covers": ["producer"],
                                    "acceptance_refs": ["UJ-BOOTSTRAP"], "commands": ["pytest -q"]}],
                pr_base="main", evidence_mode=EvidenceMode.FIXTURE, produces=[ProducedArtifact("bootstrap-input")], consumes=[]))
        node.blocked_by = ["producer"]
        node.contract.consumes = [ConsumedArtifact(artifact_id="bootstrap-input", producer="producer", evidence_mode=EvidenceMode.FIXTURE)]
    kit.store.set_node_contract(item.id, node.contract)
    before = copy.deepcopy(kit.store.get_work_item(item.id))
    save_manifest(manifest, str(path))
    reviewed = build_reviewed_amendment(manifest, proposal(responsibility_update(historical=True)), kit.store,
                                        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=responsibility_acceptance_doc())
    kit.forbid(kit.store, "set_node_contract")

    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())

    updated = load_manifest(str(path)).nodes["bootstrap"].contract
    dumped = amendment_mod._dump_contract(updated)
    expected = ConsumedArtifact(artifact_id="bootstrap-input", producer="producer", evidence_mode=EvidenceMode.FIXTURE)
    if consumes_policy == "omitted":
        assert updated.consumes is MISSING_CONSUMES and "consumes" not in dumped
    elif consumes_policy == "empty":
        assert updated.consumes == [] and dumped["consumes"] == []
    else:
        assert updated.consumes == [expected]
        assert dumped["consumes"] == [{"artifact_id": "bootstrap-input", "producer": "producer", "evidence_mode": "fixture"}]
    after = kit.store.get_work_item(item.id)
    assert (after.contract, after.contract_ref) == (before.contract, before.contract_ref)


def test_a_historical_correction_needs_its_marker_and_the_delivery_evidence_it_was_reviewed_against(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    manifest, _ = mark_done_merged(path)
    acceptance = responsibility_acceptance_doc()

    errors = validate_proposal(manifest, proposal(responsibility_update()), POOL, acceptance=acceptance)
    assert any("historical_contract_correction" in error for error in errors)

    reviewed = build_reviewed_amendment(manifest, proposal(responsibility_update(historical=True)), kit.store,
                                        issue_id="amendment-issue", reviewer_verdict="pass", acceptance=acceptance)
    kit.store.update_work_item_metadata(item.id, artifacts={"head_sha": "changed"})

    with pytest.raises(ValidationError, match="delivery evidence changed"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=acceptance)


# ---- responsibility amendments ----------------------------------------------------------------------------------------------

def test_a_contract_only_responsibility_amendment_cannot_be_repeated_on_the_same_node(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    acceptance = responsibility_acceptance_doc()
    seal_review_fixture(kit.store, item.id)
    reviewed = reviewed_for(kit, path, responsibility_update(), acceptance=acceptance)
    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=acceptance)

    errors = validate_proposal(load_manifest(str(path)), proposal(responsibility_update()), POOL, acceptance=acceptance)

    assert any("update-responsibility must change at least one acceptance responsibility field" in error for error in errors)


def test_without_a_work_item_a_responsibility_update_is_definition_only_but_an_existing_delivery_reopens_review(kit, tmp_path):
    acceptance = responsibility_acceptance_doc()
    path = dag_manifest(tmp_path)
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].work_item_id, manifest.nodes["bootstrap"].status = None, "todo"
    save_manifest(manifest, str(path))

    reviewed = build_reviewed_amendment(manifest, proposal(responsibility_update()), kit.store, issue_id="amendment-issue",
                                        reviewer_verdict="pass", acceptance=acceptance)
    assert reviewed["analysis"]["minimal_rerun"] == {"review": [], "authoring": [], "merging": []}
    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=acceptance)
    assert load_manifest(str(path)).nodes["bootstrap"].status == "todo"

    path2, _ = bootstrap(kit, tmp_path / "second" if (tmp_path / "second").mkdir() is None else tmp_path)
    reviewed = reviewed_for(kit, path2, responsibility_update(), acceptance=acceptance)
    assert reviewed["analysis"]["minimal_rerun"]["review"] == ["bootstrap"]


@pytest.mark.parametrize("stage", ("review", "authoring", "merging"))
def test_an_explicit_resume_stage_needs_a_work_item_and_matches_the_minimal_rerun(kit, tmp_path, stage):
    path, item = bootstrap(kit, tmp_path)
    acceptance = responsibility_acceptance_doc()
    no_item = load_manifest(str(path))
    no_item.nodes["bootstrap"].work_item_id = None
    errors = validate_proposal(no_item, proposal(responsibility_update(resume_stage=stage)), POOL, acceptance=acceptance)
    assert any("explicit resume_stage requires an existing work item" in error for error in errors)

    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1"},
                                        verification={"subject_digest": "verify-1"}, review_verdict="pass")
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal(responsibility_update(resume_stage=stage)), kit.store,
                                        issue_id=f"amendment-{stage}", reviewer_verdict="pass", acceptance=acceptance)
    assert reviewed["analysis"]["minimal_rerun"] == {
        "review": ["bootstrap"] if stage == "review" else [], "authoring": ["bootstrap"] if stage == "authoring" else [],
        "merging": ["bootstrap"] if stage == "merging" else []}


# ---- merge-stage responsibility amendments --------------------------------------------------------------------------------

def merge_ready(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
                                        verification={"subject_digest": "verify-1", "commands": ["pytest -q"]},
                                        review_verdict="pass", review_report={"blockers": []})
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    kit.store.set_node_contract(item.id, load_manifest(str(path)).nodes["bootstrap"].contract)
    reviewed = reviewed_for(kit, path, responsibility_update(resume_stage="merging"), acceptance=responsibility_acceptance_doc())
    return path, item, reviewed


def presynced_with_handoff(kit, tmp_path):
    """The Store already has the new contract but still carries a real old Worker handoff (pure merging)."""
    path, item, reviewed = merge_ready(kit, tmp_path)
    amended = amendment_mod._apply_definition(load_manifest(str(path)), reviewed)
    kit.store.set_node_contract(item.id, amended.nodes["bootstrap"].contract)
    current = kit.store.get_work_item(item.id)
    kit.store.prepare_review_cycle(item.id, review_subject_digest(current, max(1, current.bounces.review + 1)))
    kit.store.update_work_item_metadata(item.id, review_verdict="pass", review_report={"blockers": []},
                                        worker_handoff=old_worker_handoff())
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    return path, item, reviewed


def test_merging_is_refused_without_a_passed_review_and_a_pull_request(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1"}, review_verdict="reject")
    reviewed = reviewed_for(kit, path, responsibility_update(resume_stage="merging"), acceptance=responsibility_acceptance_doc())

    with pytest.raises(ValidationError, match="passed review and PR"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())


def test_a_presynced_pure_merging_retires_the_handoff_before_it_is_reached_and_a_tick_adds_no_reviewer(kit, tmp_path):
    path, item, reviewed = presynced_with_handoff(kit, tmp_path)

    result = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())

    applied = load_manifest(str(path))
    entry = applied.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert result["sync"]["synced"] == ["bootstrap"] and entry["state"] == "synced"
    assert entry["observed"]["worker_handoff_pending"] is False and kit.store.get_work_item(item.id).worker_handoff is None

    applied.nodes["closeout"].status = "abandoned"
    save_manifest(applied, str(path))
    kit.monkeypatch.setattr(loop, "_dispatch_reviewer_for_current_subject",
                            lambda *_a, **_k: pytest.fail("a retired handoff must not turn pure merging back to the Reviewer"))
    kit.monkeypatch.setattr(loop, "run_merge_delivery", lambda *_a, **_k: "pass")

    tick = loop.tick(kit.store, kit.runtime, kit.code_host, applied, str(path), max_parallel=1)

    assert tick.state == "converged" and applied.nodes["bootstrap"].status == "done"
    assert kit.store.get_work_item(item.id).status is WorkItemStatus.DONE


@pytest.mark.parametrize("checkpoint", ("before_clear", "after_clear"))
def test_a_crash_around_the_handoff_retirement_of_a_presynced_merging_is_idempotent(kit, tmp_path, checkpoint):
    path, item, reviewed = presynced_with_handoff(kit, tmp_path)
    original, crashed = kit.store.update_work_item_metadata, []

    def crash_at_clear(item_id, **metadata):
        if metadata.get("worker_handoff") == {} and not crashed:
            crashed.append(True)
            if checkpoint == "before_clear":
                raise RuntimeError("crash before presynced handoff retirement")
            original(item_id, **metadata)
            raise RuntimeError("crash after presynced handoff retirement")
        return original(item_id, **metadata)

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", crash_at_clear)
    with pytest.raises(RuntimeError, match="presynced handoff retirement"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    assert load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    assert (kit.store.get_work_item(item.id).worker_handoff is None) is (checkpoint == "after_clear")

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", original)
    recovered = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    repeated = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())

    assert recovered["sync"]["synced"] == ["bootstrap"] and repeated["sync"]["already_complete"] == ["bootstrap"]
    assert kit.store.get_work_item(item.id).worker_handoff is None


def test_merge_stage_responsibility_recovery_syncs_the_contract_without_replaying_the_delivery(kit, tmp_path):
    path, item, reviewed = merge_ready(kit, tmp_path)
    before = copy.deepcopy(kit.store.get_work_item(item.id))
    kit.forbid(kit.store, "reset_review", "prepare_review_cycle", "assign_work_item")

    result = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())

    after = kit.store.get_work_item(item.id)
    contract = after.contract if hasattr(after.contract, "acceptance_claims") else amendment_mod._load_contract(after.contract)
    assert result["minimal_rerun"] == {"review": [], "authoring": [], "merging": ["bootstrap"]}
    assert load_manifest(str(path)).nodes["bootstrap"].status == "merging"
    assert contract.acceptance == [] and contract.acceptance_claims == ["UJ-BOOTSTRAP"]
    assert after.contract_ref["sha256"] != before.contract_ref["sha256"]
    assert all(getattr(after, f) == getattr(before, f) for f in ACCEPTANCE_FIELDS)
    ledger = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]
    assert (ledger["stage"], ledger["state"]) == ("merging", "synced")


def test_merge_stage_recovery_resumes_after_an_interruption_following_the_manifest_write(kit, tmp_path):
    path, item, reviewed = merge_ready(kit, tmp_path)
    original = amendment_pipeline.prepare_stage_recovery
    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("crash after manifest write")))
    with pytest.raises(RuntimeError, match="after manifest write"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    partial = load_manifest(str(path))
    assert partial.nodes["bootstrap"].status == "merging" and partial.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    kit.monkeypatch.setattr(amendment_pipeline, "prepare_stage_recovery", original)

    result = apply_amendment(str(path), reviewed, kit.store, POOL)

    assert result["sync"]["synced"] == ["bootstrap"]
    assert amendment_mod._load_contract(kit.store.get_work_item(item.id).contract).acceptance_claims == ["UJ-BOOTSTRAP"]


def test_a_restart_after_the_contract_was_published_observes_it_once(kit, tmp_path):
    path, item, reviewed = merge_ready(kit, tmp_path)
    before = copy.deepcopy(kit.store.get_work_item(item.id))
    original, calls = kit.store.set_node_contract, []

    def write_then_crash(item_id, contract):
        calls.append(item_id)
        original(item_id, contract)
        raise RuntimeError("crash after Store contract publish")

    kit.monkeypatch.setattr(kit.store, "set_node_contract", write_then_crash)
    with pytest.raises(RuntimeError, match="after Store contract publish"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    assert load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    kit.monkeypatch.setattr(kit.store, "set_node_contract", original)

    result = apply_amendment(str(path), reviewed, kit.store, POOL)

    after = kit.store.get_work_item(item.id)
    assert len(calls) == 1 and result["sync"]["synced"] == ["bootstrap"]
    assert amendment_mod._load_contract(after.contract).acceptance_claims == ["UJ-BOOTSTRAP"]
    assert after.contract_ref["sha256"] != before.contract_ref["sha256"]
    assert all(getattr(after, f) == getattr(before, f) for f in ACCEPTANCE_FIELDS)
    assert load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "synced"

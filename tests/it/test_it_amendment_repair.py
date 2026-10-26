"""Authoring amendments applied by an older OMAC, repaired on a real Multica: projection, generation, baseline and progress."""
import copy

import pytest
import yaml

import omac.core.amendment as amendment_mod
import omac.pipeline.amendment as amendment_pipeline
from _support.amendkit import POOL, legacy_synced_authoring_accept_fixture, topology_manifest, proposal
from omac.core.amendment import apply_amendment, build_reviewed_amendment
from omac.core.manifest import load_manifest, save_manifest
from omac.core.retry_budget import consumed_bounces
from omac.core.taskmeta import DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline import loop
from omac.pipeline.dispatch import build_show_output


@pytest.fixture()
def legacy(kit, tmp_path, legacy_convergence_snapshot):
    return legacy_synced_authoring_accept_fixture(kit, tmp_path, legacy_convergence_snapshot)


def accept(kit, path, amendment_file):
    return amendment_pipeline.accept_amendment(kit.engine, str(path), str(amendment_file), reason="repeat official accepted amendment",
                                               agent_pool=POOL)


def abandon_closeout(path):
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    return manifest


def ledger_entry(path):
    return load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]


def tick_twice(kit, manifest, path):
    for _ in range(2):
        loop.tick(kit.store, kit.runtime, kit.code_host, manifest, str(path), max_parallel=1)


def formal_runs_since(kit, item_id, before):
    return [run for run in kit.runtime.list_runs(item_id) if run not in before and run.formal]


def test_the_same_legacy_synced_accept_repairs_the_missing_authoring_projection(kit, legacy):
    path, amendment_file, target, reviewed = legacy

    result = accept(kit, path, amendment_file)

    current, manifest, entry = kit.store.get_work_item(target.id), load_manifest(str(path)), ledger_entry(path)
    assert result["sync"]["synced"] == ["bootstrap"]
    assert (current.status, current.phase, current.bounces.worker) == (WorkItemStatus.TODO, TaskPhase.AUTHORING, 17)
    assert current.bounce_baseline == {"worker": 14, "review": 3, "merge": 0}
    assert consumed_bounces(manifest, "bootstrap", current, "worker") == 3
    assert current.review_generation == entry["expected_review_generation"] and current.current_review_ledger is None
    assert (current.decision_required, current.review_subject_digest, current.review_verdict, current.review_report,
            current.worker_handoff) == (None, None, None, None, None)
    assert build_show_output(current, "worker:alice").get("ok", True) is True
    assert entry["observed"]["contract_sha256"] == entry["expected_contract_sha256"]
    assert entry["observed"]["review_generation"] == entry["expected_review_generation"]
    assert entry["observed"]["review_ledger_current"] is False
    assert yaml.safe_load(amendment_file.read_text())["human_confirmation"] == "applied"
    assert accept(kit, path, amendment_file)["sync"]["already_complete"] == ["bootstrap"]


def test_a_synced_entry_with_a_stale_projection_is_observed_again_against_the_expected_generation(kit, legacy):
    path, amendment_file, target, _ = legacy
    manifest = load_manifest(str(path))
    manifest.meta["amendment_apply"]["nodes"]["bootstrap"]["expected_review_generation"] = amendment_mod._authoring_review_generation(
        manifest.meta["amendment_apply"]["amendment_id"], "bootstrap")
    save_manifest(manifest, str(path))
    stale = kit.store.get_work_item(target.id)
    assert stale.review_generation is None and stale.current_review_ledger is not None and stale.worker_handoff is not None

    result = accept(kit, path, amendment_file)

    current, entry = kit.store.get_work_item(target.id), ledger_entry(path)
    assert result["sync"]["synced"] == ["bootstrap"] and result["sync"]["already_complete"] == []
    assert current.review_generation == entry["expected_review_generation"]
    assert current.current_review_ledger is None and current.worker_handoff is None
    assert entry["observed"]["review_generation"] == entry["expected_review_generation"]


def test_a_restore_that_changes_nothing_does_not_mark_the_entry_synced(kit, legacy):
    path, amendment_file, target, _ = legacy
    before = copy.deepcopy(kit.store.get_work_item(target.id))
    kit.monkeypatch.setattr(kit.store, "restore_authoring_generation", lambda *_a, **_k: before)

    with pytest.raises(ValidationError, match="authoring recovery did not reach its target"):
        accept(kit, path, amendment_file)

    entry = ledger_entry(path)
    assert entry["state"] == "repairing" and entry["observed"]["review_generation"] is None


def test_a_failed_baseline_read_in_a_legacy_repair_has_no_side_effect(kit, legacy):
    path, amendment_file, target, _ = legacy
    legacy_manifest = load_manifest(str(path))
    legacy_manifest.meta["amendment_apply"]["nodes"]["bootstrap"].pop("attempt_baseline", None)
    save_manifest(legacy_manifest, str(path))
    entry = ledger_entry(path)
    assert entry["state"] == "synced" and "expected_review_generation" not in entry and "attempt_baseline" not in entry
    before_manifest, before_amendment = path.read_bytes(), amendment_file.read_bytes()
    before_item = copy.deepcopy(kit.store.get_work_item(target.id))
    state = kit.fail_first_read(target.id, RuntimeError("legacy repair baseline read failed"))

    with pytest.raises(RuntimeError, match="legacy repair baseline read failed"):
        accept(kit, path, amendment_file)

    entry = ledger_entry(path)
    assert state["fired"] and state["reads"] == 1
    # the only thing the repeated accept records before it reads the baseline is the file it was given
    assert yaml.safe_load(path.read_text()) == {**yaml.safe_load(before_manifest), "meta": {
        **yaml.safe_load(before_manifest)["meta"], "amendment_apply": {
            **yaml.safe_load(before_manifest)["meta"]["amendment_apply"], "amendment_file": str(amendment_file)}}}
    assert amendment_file.read_bytes() == before_amendment
    assert entry["state"] == "synced" and "attempt_baseline" not in entry and "expected_review_generation" not in entry
    assert kit.store.get_work_item(target.id) == before_item


def test_authoring_preflight_rejects_a_later_malformed_entry_before_any_write(kit, tmp_path):
    first = kit.store.create_work_item(kit.workspace, "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    kit.store.update_status(first.id, WorkItemStatus.BLOCKED)
    second = kit.store.create_work_item(kit.workspace, "dependent", "desc", "started-dependent", "charlie", reviewer="bob")
    kit.store.update_status(second.id, WorkItemStatus.IN_REVIEW)
    path = topology_manifest(tmp_path, {"bootstrap": first.id, "started-dependent": second.id})
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal({"op": "update", "node": "bootstrap", "set": {"blocked_by": ["foundation"]}}), kit.store,
        issue_id="amendment-preflight", reviewer_verdict="pass")
    original_resume = amendment_mod._resume_apply_ledger
    kit.monkeypatch.setattr(amendment_mod, "_resume_apply_ledger",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stop after ledger creation")))
    with pytest.raises(RuntimeError, match="after ledger creation"):
        apply_amendment(str(path), reviewed, kit.store, POOL)
    manifest = load_manifest(str(path))
    entries = manifest.meta["amendment_apply"]["nodes"]
    entries["started-dependent"]["state"] = "repairing"
    entries["started-dependent"].pop("attempt_baseline", None)
    save_manifest(manifest, str(path))
    before_manifest = path.read_bytes()
    before_first, before_second = (copy.deepcopy(kit.store.get_work_item(i.id)) for i in (first, second))
    kit.monkeypatch.setattr(amendment_mod, "_resume_apply_ledger", original_resume)

    with pytest.raises(ValidationError, match="repairing entry is missing attempt_baseline"):
        apply_amendment(str(path), reviewed, kit.store, POOL)

    assert path.read_bytes() == before_manifest
    assert kit.store.get_work_item(first.id) == before_first and kit.store.get_work_item(second.id) == before_second


@pytest.mark.parametrize("checkpoint", ("before_switch", "after_switch"))
def test_a_legacy_synced_authoring_repair_is_restart_safe(kit, legacy, checkpoint):
    path, amendment_file, target, _ = legacy
    kit.fault(kit.store, "restore_authoring_generation", when="before" if checkpoint == "before_switch" else "after",
              error=RuntimeError("crash around legacy repair switch"))

    with pytest.raises(RuntimeError, match="legacy repair switch"):
        accept(kit, path, amendment_file)

    assert ledger_entry(path)["state"] == "repairing"
    recovered = accept(kit, path, amendment_file)
    repeated = accept(kit, path, amendment_file)
    assert recovered["sync"]["synced"] == ["bootstrap"] and repeated["sync"]["already_complete"] == ["bootstrap"]
    current = kit.store.get_work_item(target.id)
    assert current.review_generation is not None and current.current_review_ledger is None


def test_a_legacy_synced_repair_fails_closed_while_a_formal_run_is_active(kit, legacy):
    path, amendment_file, target, _ = legacy
    kit.assign(target.id, "alice")
    before_manifest, before_item = path.read_bytes(), copy.deepcopy(kit.store.get_work_item(target.id))

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        accept(kit, path, amendment_file)

    current = kit.store.get_work_item(target.id)
    assert path.read_bytes() == before_manifest
    assert (current.review_generation, current.decision_required, current.worker_handoff) == (
        before_item.review_generation, before_item.decision_required, before_item.worker_handoff)


@pytest.mark.parametrize("progress", ("delivery", "review"))
@pytest.mark.parametrize("repair_state", ("synced", "repairing"))
def test_a_legacy_synced_repair_never_rolls_a_progressed_work_item_back(kit, legacy, progress, repair_state):
    path, amendment_file, target, _ = legacy
    manifest = load_manifest(str(path))
    if repair_state == "repairing":
        entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
        entry["state"], entry["expected_review_generation"] = "repairing", "legacy-repair-generation"
        save_manifest(manifest, str(path))
    if progress == "delivery":
        kit.store.update_work_item_metadata(target.id, delivery_identity=DeliveryIdentity(
            schema=DELIVERY_IDENTITY_SCHEMA, handoff_generation="progressed-handoff", worker="alice", agent_id="agent-alice",
            run_id="completed-worker-run", pr_url="https://github.com/acme/repo/pull/146", pr_head_sha="progressed-head",
            verification_sha256="progressed-verification", verification_attachment_id="progressed-attachment",
            verification_comment_id="progressed-comment", verification_uploader_id="agent-alice", verification_uploader_type="agent"))
    else:
        kit.store.update_work_item_metadata(target.id, phase=TaskPhase.REVIEW, review_generation="progressed-review-generation",
                                            review_ledger_generation="progressed-review-generation")
        kit.store.update_status(target.id, WorkItemStatus.IN_REVIEW)
    before_item = copy.deepcopy(kit.store.get_work_item(target.id))
    kit.forbid(kit.store, "restore_authoring_generation")

    result = accept(kit, path, amendment_file)

    repaired = ledger_entry(path)
    assert result["sync"]["observed_progress"] == ["bootstrap"] and kit.store.get_work_item(target.id) == before_item
    assert repaired["state"] == "observed_progress"
    assert repaired["observed"]["delivery_identity_pending"] is (progress == "delivery")
    assert repaired["observed"]["phase"] == before_item.phase.value


def test_after_a_legacy_synced_repair_the_first_reconcile_dispatches_exactly_one_worker(kit, legacy):
    path, amendment_file, target, _ = legacy
    accept(kit, path, amendment_file)
    manifest = abandon_closeout(path)
    before = list(kit.runtime.list_runs(target.id))

    tick_twice(kit, manifest, path)

    new_formal = formal_runs_since(kit, target.id, before)
    assert len(new_formal) == 1 and new_formal[0].agent_id == kit.store.resolve_agent_id("alice")


def test_a_node_left_in_progress_by_the_old_dispatch_is_made_dispatchable_again(kit, legacy):
    """The issue is back at its authoring start; a manifest node that is still in_progress could never be dispatched again."""
    path, amendment_file, target, _ = legacy
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "in_progress"
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))

    accept(kit, path, amendment_file)

    manifest = load_manifest(str(path))
    assert manifest.nodes["bootstrap"].status == "todo" and kit.store.get_work_item(target.id).status == WorkItemStatus.TODO
    before = list(kit.runtime.list_runs(target.id))
    loop.tick(kit.store, kit.runtime, kit.code_host, manifest, str(path), max_parallel=1)
    assert len(formal_runs_since(kit, target.id, before)) == 1


def test_a_second_accept_normalizes_a_stale_node_even_when_the_entry_is_already_synced(kit, legacy):
    path, amendment_file, _, _ = legacy
    abandon_closeout(path)
    accept(kit, path, amendment_file)
    manifest = load_manifest(str(path))   # a crash left the node in_progress while the issue is still at its authoring start
    manifest.nodes["bootstrap"].status = "in_progress"
    save_manifest(manifest, str(path))

    accept(kit, path, amendment_file)

    assert load_manifest(str(path)).nodes["bootstrap"].status == "todo"


def legacy_baseline(entry, **overrides):
    return {"status": "todo", "phase": "authoring", "review_verdict": None, "review_subject_digest": None,
            "contract_sha256": entry["baseline"]["contract_sha256"], "worker_handoff_pending": False, **overrides}


def test_a_synced_entry_with_a_legacy_baseline_is_upgraded_when_the_issue_is_at_the_recovery_target(kit, legacy):
    path, amendment_file, target, _ = legacy
    abandon_closeout(path)
    accept(kit, path, amendment_file)
    manifest = load_manifest(str(path))
    manifest.meta["amendment_apply"]["nodes"]["bootstrap"]["baseline"] = legacy_baseline(ledger_entry(path))
    manifest.nodes["bootstrap"].status = "in_progress"
    save_manifest(manifest, str(path))
    before_item = kit.store.get_work_item(target.id)
    restores = kit.record(kit.store, "restore_authoring_generation")

    accept(kit, path, amendment_file)

    repaired = load_manifest(str(path))
    entry = repaired.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "synced" and amendment_mod._RECOVERY_SNAPSHOT_FIELDS.issubset(entry["baseline"])
    assert repaired.nodes["bootstrap"].status == "todo" and kit.store.get_work_item(target.id) == before_item
    assert not restores   # no redundant restore


def test_a_repair_upgrades_an_incomplete_baseline_when_it_syncs(kit, legacy):
    path, amendment_file, _, _ = legacy
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["baseline"] = legacy_baseline(entry, status="blocked", phase="review", worker_handoff_pending=True)
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))

    accept(kit, path, amendment_file)

    repaired = ledger_entry(path)
    assert repaired["state"] == "synced" and amendment_mod._RECOVERY_SNAPSHOT_FIELDS.issubset(repaired["baseline"])


def test_a_legacy_baseline_stays_fail_closed_when_the_issue_drifted(kit, legacy):
    path, amendment_file, target, _ = legacy
    abandon_closeout(path)
    accept(kit, path, amendment_file)
    manifest = load_manifest(str(path))
    manifest.meta["amendment_apply"]["nodes"]["bootstrap"]["baseline"] = legacy_baseline(ledger_entry(path))
    save_manifest(manifest, str(path))
    kit.store.update_status(target.id, WorkItemStatus.IN_PROGRESS)

    with pytest.raises(amendment_mod.ValidationError):
        accept(kit, path, amendment_file)


def test_the_worker_retry_log_tells_absolute_from_current_generation_consumption(kit, legacy):
    from omac.core.retry_budget import bounce_log_fields

    path, amendment_file, target, _ = legacy
    accept(kit, path, amendment_file)

    fields = bounce_log_fields(kit.store.get_work_item(target.id), "worker", absolute_count=18, limit=5)

    assert (fields["absolute_audit_round"], fields["current_generation_consumed"], fields["current_generation_limit"]) == (18, 4, 5)

"""Stage recovery by amendment on a real Multica: fresh budgets over untouched history, handoff retirement, and restart safety."""
import pytest

from _support.amendkit import (
    POOL, apply_exhausted_stage_amendment, bootstrap, contract_update, old_worker_handoff, proposal, responsibility_acceptance_doc,
    seal_review_fixture, stage_amendment_with_handoff,
)
from omac.core.amendment import apply_amendment, build_reviewed_amendment
from omac.core.manifest import load_manifest, save_manifest
from omac.core.workitem import PullRequestObservation, PullRequestState, WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline import loop
from omac.pipeline.decide_merge import merge_bounce_attempt
from omac.pipeline.delivery import run_merge_delivery

LIMITS = {"worker": 3, "ci": 3, "review": 3, "merge": 3}


def ledger_entry(path):
    return load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]


@pytest.mark.parametrize("stage", ("authoring", "review", "merging"))
def test_recovery_keeps_the_absolute_bounce_audit_and_records_a_fresh_budget(kit, tmp_path, stage):
    path, item = apply_exhausted_stage_amendment(kit, tmp_path, stage)

    got = kit.store.get_work_item(item.id)

    assert (got.bounces.worker, got.bounces.review, got.bounces.merge) == (3, 4, 5) and got.worker_handoff is None
    assert ledger_entry(path)["bounce_baseline"] == {"worker": 3, "review": 4, "merge": 5}


@pytest.mark.parametrize("stage", ("review", "merging"))
@pytest.mark.parametrize("checkpoint", ("before_clear", "after_clear"))
def test_stage_recovery_retires_the_handoff_and_survives_a_crash_on_either_side_of_it(kit, tmp_path, stage, checkpoint):
    path, item, reviewed = stage_amendment_with_handoff(kit, tmp_path, stage)
    original, crashed = kit.store.update_work_item_metadata, []

    def crash_at_clear(item_id, **metadata):
        if metadata.get("worker_handoff") == {} and not crashed:
            crashed.append(True)
            if checkpoint == "before_clear":
                raise RuntimeError("crash before stage handoff retirement")
            original(item_id, **metadata)
            raise RuntimeError("crash after stage handoff retirement")
        return original(item_id, **metadata)

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", crash_at_clear)
    with pytest.raises(RuntimeError, match="stage handoff retirement"):
        apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    assert ledger_entry(path)["state"] == "syncing"
    assert (kit.store.get_work_item(item.id).worker_handoff is None) is (checkpoint == "after_clear")

    kit.monkeypatch.setattr(kit.store, "update_work_item_metadata", original)
    result = apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    assert result["sync"]["synced"] == ["bootstrap"]
    assert kit.store.get_work_item(item.id).worker_handoff is None and ledger_entry(path)["state"] == "synced"
    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())
    assert kit.store.get_work_item(item.id).worker_handoff is None


def test_the_merging_stage_retires_the_handoff_before_it_syncs_the_contract(kit, tmp_path):
    path, item, reviewed = stage_amendment_with_handoff(kit, tmp_path, "merging")
    observed = []
    kit.before(kit.store, "set_node_contract", lambda item_id, contract: observed.append(kit.store.get_work_item(item_id).worker_handoff))

    apply_amendment(str(path), reviewed, kit.store, POOL, acceptance=responsibility_acceptance_doc())

    assert observed == [None] and kit.store.get_work_item(item.id).worker_handoff is None


def test_after_review_recovery_a_reject_spends_the_fresh_review_budget_not_the_absolute_history(kit, tmp_path):
    path, item = apply_exhausted_stage_amendment(kit, tmp_path, "review")
    manifest = load_manifest(str(path))
    kit.store.update_work_item_metadata(item.id, review_verdict="reject")
    kit.monkeypatch.setattr(loop, "validate_review_evidence", lambda *_a: [])

    failures = loop.collect_results(kit.store, kit.runtime, kit.code_host, manifest, str(path), retry_limits=LIMITS)

    assert failures == {} and manifest.nodes["bootstrap"].status == "in_progress"
    assert kit.store.get_work_item(item.id).bounces.review == 5


def test_after_authoring_recovery_the_worker_gets_a_fresh_retry_budget_on_top_of_the_untouched_history(kit, tmp_path):
    path, item = apply_exhausted_stage_amendment(kit, tmp_path, "authoring")   # the absolute worker count is already at the limit of 3
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    kit.agents("idle", names=("alice",))   # every Worker Run ends without submitting
    kit.tick(manifest, str(path), max_parallel=1, retry_limits=LIMITS)
    kit.wait_runs_ended(item.id)

    kit.tick(manifest, str(path), max_parallel=1, retry_limits=LIMITS)   # a retry from the fresh budget, not a block

    retrying = kit.store.get_work_item(item.id)
    assert manifest.nodes["bootstrap"].status == "in_progress" and retrying.decision_required is None
    assert retrying.bounces.worker == 4 and ledger_entry(path)["bounce_baseline"]["worker"] == 3

    blocked = kit.tick_until(manifest, str(path), lambda: manifest.nodes["bootstrap"].status == "blocked", timeout=90,
                             what="the fresh budget to run out", max_parallel=1, retry_limits=LIMITS)
    got = kit.store.get_work_item(item.id)
    assert blocked and got.bounces.worker == 6   # three retries on top of the three recorded before the amendment


def test_after_merging_recovery_a_closed_pull_request_spends_the_fresh_merge_budget(kit, tmp_path):
    path, item = apply_exhausted_stage_amendment(kit, tmp_path, "merging")
    manifest = load_manifest(str(path))
    kit.monkeypatch.setattr(kit.code_host, "observe_pull_request", lambda _url: PullRequestObservation(
        PullRequestState.CLOSED_UNMERGED, None, "closed without merge"))

    result = run_merge_delivery({}, manifest, "bootstrap", kit.store, kit.runtime, kit.code_host, LIMITS, str(path))

    assert result == "bounce" and merge_bounce_attempt(manifest.nodes["bootstrap"].merge_request_state) is not None   # the Loop hands back
    assert kit.store.get_work_item(item.id).bounces.merge == 6


def test_a_repeated_accept_after_the_node_progressed_never_rolls_it_back(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
                                        verification={"subject_digest": "verify-1", "commands": []}, review_verdict="reject",
                                        review_report={"blockers": ["bad mapping"]})
    seal_review_fixture(kit.store, item.id)
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal(contract_update()), kit.store, issue_id="amendment-issue",
                                        reviewer_verdict="pass")
    assert apply_amendment(str(path), reviewed, kit.store, POOL)["sync"]["synced"] == ["bootstrap"]

    kit.store.update_work_item_metadata(item.id, review_verdict="pass", review_report={"blockers": []})
    kit.store.update_status(item.id, WorkItemStatus.DONE)
    progressed = load_manifest(str(path))
    node = progressed.nodes["bootstrap"]
    node.status, node.merged, node.merged_at = "done", True, "2026-07-27T01:00:00Z"
    save_manifest(progressed, str(path))

    second = apply_amendment(str(path), reviewed, kit.store, POOL)

    got, reloaded = kit.store.get_work_item(item.id), load_manifest(str(path))
    assert second["sync"]["already_complete"] == ["bootstrap"]
    assert (got.status, got.review_verdict) == (WorkItemStatus.DONE, "pass")
    assert (reloaded.nodes["bootstrap"].status, reloaded.nodes["bootstrap"].merged) == ("done", True)
    assert ledger_entry(path)["state"] == "synced"


def test_the_apply_ledger_completes_after_a_crash_between_the_contract_write_and_the_review_reset(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1"},
                                        verification={"subject_digest": "verify-1"}, review_verdict="reject",
                                        review_report={"blockers": ["bad mapping"]})
    seal_review_fixture(kit.store, item.id)
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal(contract_update()), kit.store, issue_id="amendment-issue",
                                        reviewer_verdict="pass")
    fault = kit.fault(kit.store, "reset_review", when="before", error=RuntimeError("simulated reset interruption"))

    with pytest.raises(RuntimeError, match="reset interruption"):
        apply_amendment(str(path), reviewed, kit.store, POOL)
    from omac.core.manifest import _load_contract
    partial = kit.store.get_work_item(item.id)
    assert _load_contract(partial.contract).acceptance == ["bootstrap workspace is valid"] and partial.review_verdict == "reject"

    fault["armed"] = False
    result = apply_amendment(str(path), reviewed, kit.store, POOL)

    completed = kit.store.get_work_item(item.id)
    assert result["sync"]["synced"] == ["bootstrap"] and completed.status == WorkItemStatus.IN_REVIEW and completed.review_verdict is None


def test_a_merge_only_recovery_keeps_the_pass_verdict_and_enters_merging_without_touching_the_pull_request(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1"}, review_verdict="pass",
                                        review_report={"blockers": []})
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal({"op": "resume", "node": "bootstrap", "stage": "merging"}),
                                        kit.store, issue_id="amendment-issue", reviewer_verdict="pass")
    kit.forbid(kit.code_host, "observe_pull_request", "request_pull_request_merge")   # accept delegates both to `dag run`
    kit.forbid(kit.store, "set_node_contract")                                       # the contract is unchanged

    result = apply_amendment(str(path), reviewed, kit.store, POOL)

    assert result["minimal_rerun"]["merging"] == ["bootstrap"] and load_manifest(str(path)).nodes["bootstrap"].status == "merging"
    assert kit.store.get_work_item(item.id).review_verdict == "pass"


def test_a_merge_only_recovery_without_its_preconditions_modifies_nothing(kit, tmp_path):
    path, item = bootstrap(kit, tmp_path)
    original = path.read_bytes()
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1"}, review_verdict="reject",
                                        review_report={"blockers": ["not ready"]}, worker_handoff=old_worker_handoff())
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal({"op": "resume", "node": "bootstrap", "stage": "merging"}),
                                        kit.store, issue_id="amendment-issue", reviewer_verdict="pass")

    with pytest.raises(ValidationError, match="passed review and PR"):
        apply_amendment(str(path), reviewed, kit.store, POOL)

    assert path.read_bytes() == original and kit.store.get_work_item(item.id).worker_handoff is not None

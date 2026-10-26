"""A review that does not converge, and review ledgers that cannot be trusted: decided before any write.

Node `a` is delivered by alice and rejected by bob; the Loop has not collected the verdict yet when a test starts.
alice's Runs after her first follow the scenario each test passes (default: they submit again).
"""
from dataclasses import replace
from pathlib import Path

import pytest

from _support import ledgers
from _support.loopkit import HANG
from _support.rework import rejected, with_handoff_round, workers
from omac.core.manifest import load_manifest
from omac.core.review_convergence import review_subject_digest
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline import loop


def test_a_review_that_stalls_blocks_before_another_worker_handoff(kit):
    """The third review repeats the blocker of the first two: no fourth attempt."""
    manifest, path, item = kit.in_review([{"hang": 4}, kit.stack.tool("reviewer-submit", "--repeat", "reject")], *HANG)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(2), review_bounce=2)   # two reworks happened
    third = kit.store.get_work_item(item.id)
    subject = review_subject_digest(third, 3)
    kit.store.update_work_item_metadata(item.id, review_subject_digest=subject,
                                        reviewer_run_baseline=replace(third.reviewer_run_baseline, subject_digest=subject))
    kit.wait_for(lambda: kit.store.get_work_item(item.id).review_verdict == "reject", what="the third rejection")
    kit.wait_runs_ended(item.id)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.tick(manifest, path, retry_limits={"review": 20})

    blocked = kit.store.get_work_item(item.id)
    assert len(blocked.review_ledger["cycles"]) == 3
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.bounces.review == 2 and not workers(assigns)
    assert blocked.decision_required["reason_code"] == "review-convergence-stalled"
    assert blocked.decision_required["recommended_action"] == "dag-amendment" and blocked.decision_required["convergence"]["cycle_count"] == 3


@pytest.mark.parametrize("terminal_observed", [False, True])
def test_a_restart_decides_the_stall_before_it_looks_at_an_existing_handoff(kit, terminal_observed):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    intent = with_handoff_round(kit, item, 3, **({"terminal_observed_at": "2026-08-03T00:00:00+00:00"} if terminal_observed else {}))
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(3))
    runs_before = kit.run_ids(item.id)
    assigns = kit.record(kit.store, "assign_work_item")
    decisions = kit.record(kit.store, "update_work_item_metadata")
    statuses = kit.record(kit.store, "update_status")

    first = kit.tick(manifest, path)
    second = kit.tick(load_manifest(path), path)

    blocked = kit.store.get_work_item(item.id)
    assert first.state == second.state == "needs_decision" and blocked.status is WorkItemStatus.BLOCKED
    assert blocked.decision_required["reason_code"] == "review-convergence-stalled" and blocked.worker_handoff == intent
    assert len([d for d in decisions if (d[2].get("decision_required") or {}).get("reason_code") == "review-convergence-stalled"]) == 1
    assert len([s for s in statuses if s[1][1] is WorkItemStatus.BLOCKED]) == 1
    assert not workers(assigns) and kit.run_ids(item.id) == runs_before


def test_a_ledger_that_keeps_going_deeper_does_not_stop_the_handoff(kit):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    with_handoff_round(kit, item, 3)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(3, classification="deeper"))

    result = kit.tick(manifest, path)

    assert result.state == "running" and manifest.nodes["a"].status == "in_progress"


@pytest.mark.parametrize("name", [*ledgers.FORGERIES, *ledgers.UNTRUSTED])
def test_an_untrustworthy_ledger_fails_the_tick_before_any_write(kit, name):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    round_index, ledger = ledgers.forged(name) if name in ledgers.FORGERIES else (3, ledgers.copy_of(ledgers.UNTRUSTED[name]))
    with_handoff_round(kit, item, round_index)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledger)
    before = Path(path).read_bytes()
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status", "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(PlatformError, match="review ledger|seen_count"):
        kit.tick(manifest, path)

    assert not writes and manifest.nodes["a"].status == "in_progress" and Path(path).read_bytes() == before


def test_an_active_worker_is_not_restored_before_a_stalled_review_has_its_decision(kit):
    manifest, path, item = rejected(kit, *HANG)
    kit.tick(manifest, path)
    with_handoff_round(kit, item, 3)
    kit.store.update_work_item_metadata(item.id, review_ledger=ledgers.stalled(3))
    kit.store.update_status(item.id, WorkItemStatus.BLOCKED)    # the graph's blocked projection of the node
    manifest.nodes["a"].status = "blocked"
    kit.save(manifest)
    writes = kit.record(kit.store, "update_work_item_metadata", "update_status")
    saved = []
    original_save = loop.save_manifest
    kit.monkeypatch.setattr(loop, "save_manifest", lambda current, target: (saved.append(current.nodes["a"].status), original_save(current, target))[1])
    kit.fault(kit.store, "assign_work_item", when="before", error=AssertionError("must not assign"))
    kit.fault(kit.runtime, "wake", when="before", error=AssertionError("must not wake"))

    result = kit.tick(manifest, path)

    blocked = kit.store.get_work_item(item.id)
    assert "in_progress" not in saved and writes[0][2].get("decision_required", {}).get("reason_code") == "review-convergence-stalled"
    assert result.state == "needs_decision" and manifest.nodes["a"].status == "blocked"
    assert blocked.decision_required["reason_code"] == "review-convergence-stalled"

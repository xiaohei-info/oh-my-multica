"""A current Worker handoff does not renew an older review generation."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from omac.cli.main import main
from omac.core.manifest import load_manifest, save_manifest
from omac.core.retry_budget import consumed_bounces
from omac.core.taskmeta import WorkerHandoffIntent, review_context_binding
from omac.engines.models import WorkItemStatus
from omac.pipeline.dispatch import build_show_output
from test_cli_node import _pass_with_nits_fixture


@pytest.mark.parametrize(
    "ledger_current,prior_reject", [(False, False), (True, False), (False, True)]
)
def test_retry_distinguishes_current_authoring_from_current_review(
    tmp_path, monkeypatch, capsys, ledger_current, prior_reject
):
    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    engine.store.reset_review(item_id)
    engine.store.update_work_item_metadata(
        item_id,
        review_generation="amendment-current",
        review_ledger_generation="amendment-current"
        if ledger_current
        else "retired-review",
        review_bounce=1,
        worker_bounce=88,
        bounce_baseline={"worker": 88, "review": 1, "merge": 0},
        delivery_identity={},
    )
    item = engine.store.get_work_item(item_id)
    intent = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="bob",
        gate="operator-retry" if prior_reject else "explicit-dispatch",
        source_review_subject_digest="subject",
        source_review_round=1,
        source_review_verdict="reject" if prior_reject else None,
        target_review_bounce=1,
        generation="current-handoff",
        target_agent_id=engine.store.resolve_agent_id("bob"),
        review_context_binding=review_context_binding(item),
        target_worker_bounce=88,
    )
    decision = {
        "schema": "omac.decision-required/v1",
        "reason_code": "worker-decision-required",
        "kind": "develop",
        "phase": "authoring",
        "gate": "worker",
        "resume_issue_id": item_id,
        "review_context_binding": review_context_binding(item),
        "blocker": {
            "schema": "omac.worker-blocker/v2",
            "reason_code": "quality-gate-failed",
        },
    }
    engine.store.update_work_item_metadata(
        item_id, worker_handoff=intent, decision_required=decision
    )
    engine.store.update_status(item_id, WorkItemStatus.BLOCKED)
    before = deepcopy(engine.store.get_work_item(item_id))
    manifest = load_manifest(path)
    manifest.meta.update(
        {
            "last_amendment_id": "amend-budget",
            "amendment_apply": {
                "schema": "omac.amendment-apply/v1",
                "amendment_id": "amend-budget",
                "nodes": {
                    "b": {
                        "stage": "authoring",
                        "state": "synced",
                        "work_item_id": item_id,
                        "bounce_baseline": {"worker": 88, "review": 1, "merge": 0},
                    }
                },
            },
        }
    )
    save_manifest(manifest, path)
    budget_authority = deepcopy(manifest.meta)

    recover = Mock(return_value={})
    monkeypatch.setattr(engine.store, "recover_review_rework_context", recover)
    writes = Mock(side_effect=engine.store.update_work_item_metadata)
    monkeypatch.setattr(engine.store, "update_work_item_metadata", writes)
    code = main(["node", "retry", path, "b", "--stage", "authoring"])
    capsys.readouterr()
    after = engine.store.get_work_item(item_id)
    if ledger_current or prior_reject:
        assert code == 20
        assert after.decision_required == before.decision_required
        assert after.worker_handoff == before.worker_handoff
        writes.assert_not_called()
        recover.assert_called_once_with(item_id)
    else:
        assert code == 0
        recover.assert_not_called()
        assert after.worker_handoff is None
        assert not after.decision_required
        assert after.status == WorkItemStatus.TODO
        assert load_manifest(path).nodes["b"].status == "todo"
        assert (
            "previous_review" not in build_show_output(after, "worker:bob")["context"]
        )
    assert after.bounces == before.bounces
    restored_manifest = load_manifest(path)
    assert restored_manifest.meta == budget_authority
    assert consumed_bounces(restored_manifest, "b", after, "worker") == 0
    assert consumed_bounces(manifest, "b", before, "worker") == 0
    # node retry historically clears the Store projection, not manifest authority.
    assert after.bounce_baseline == (before.bounce_baseline if code == 20 else None)
    assert after.review_ledger == before.review_ledger
    assert after.artifacts == before.artifacts

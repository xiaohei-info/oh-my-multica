from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omac.core.amendment import (
    APPLY_LEDGER_SCHEMA, _prepare_apply_ledger, _resume_apply_ledger,
)
from omac.core.manifest import Contract, Manifest, Node, load_manifest, save_manifest
from omac.core.retry_budget import consumed_bounces, review_rework_budget
from omac.core.taskmeta import review_context_binding


def fixture():
    node = Node(id="dispatch", worker="worker", work_item_id="dispatch-issue", contract=Contract(objective="dispatch"), status="in_review")
    baseline = {"worker": 2, "review": 21, "merge": 0}
    entry = {"stage": "authoring", "state": "synced", "bounce_baseline": baseline,
             "expected_contract_sha256": review_context_binding(node)["contract_sha256"]}
    ledger = {"schema": APPLY_LEDGER_SCHEMA, "amendment_id": "amend-dispatch", "nodes": {"dispatch": entry}}
    manifest = Manifest(meta={"last_amendment_id": "amend-dispatch", "amendment_apply": ledger}, nodes={"dispatch": node, "harness": Node(id="harness", worker="worker")})
    item = SimpleNamespace(id="dispatch-issue", bounces=SimpleNamespace(worker=2, review=22, merge=0), bounce_baseline=None, review_continuation=None)
    return manifest, item


def advance(manifest, store, amendment_id, minimal):
    ledger = _prepare_apply_ledger(manifest, amendment_id, minimal, [], store, None, set())
    manifest.meta.update(last_amendment_id=amendment_id, amendment_apply=ledger)
    return ledger


def test_unrelated_amendments_keep_authorized_budget_without_replaying_old_nodes(tmp_path):
    manifest, item = fixture()
    original = deepcopy(manifest)
    store = Mock()
    for index in range(3):
        ledger = advance(manifest, store, f"amend-harness-{index}", {"authoring": ["harness"]})
        assert set(ledger["nodes"]) == {"harness"}
        assert consumed_bounces(manifest, "dispatch", item, "review") == 1
        budget = review_rework_budget(manifest, "dispatch", item, 20)
        assert budget.allows_rework and budget.authorized_through_round == 41
        path = tmp_path / "dag.yaml"
        save_manifest(manifest, str(path))
        manifest = load_manifest(str(path))
        summary = _resume_apply_ledger(manifest, str(path), store)
        assert sorted(node for values in summary.values() for node in values) == ["harness"]
        assert manifest.nodes["dispatch"] == original.nodes["dispatch"]
    assert not store.mock_calls
    assert item.bounces.review == 22 and item.bounce_baseline is None


def test_new_affected_node_budget_supersedes_retained_budget():
    manifest, item = fixture()
    store = Mock()
    advance(manifest, store, "amend-harness", {"authoring": ["harness"]})
    # A later accepted recovery of Dispatch owns a new baseline. It must not
    # fall back to the older baseline even if the new active entry is invalid.
    manifest.meta['amendment_apply']['nodes']['dispatch'] = {'bounce_baseline': {'review': 22}}
    assert consumed_bounces(manifest, "dispatch", item, "review") == 0
    manifest.meta['amendment_apply']['nodes']['dispatch'] = {'bounce_baseline': {'review': True}}
    assert consumed_bounces(manifest, "dispatch", item, "review") == 22


@pytest.mark.parametrize("change", ["contract", "issue", "removed", "regressed", "store-only"])
def test_retained_budget_does_not_grant_unrelated_or_unproven_authority(change):
    manifest, item = fixture()
    advance(manifest, Mock(), "amend-harness", {"authoring": ["harness"]})
    if change == "contract":
        manifest.nodes["dispatch"].contract.objective = "changed scope"
    elif change == "issue":
        manifest.nodes["dispatch"].work_item_id = item.id = "replacement-issue"
    elif change == "removed":
        del manifest.nodes["dispatch"]
    elif change == "regressed":
        item.bounces.review = 10
    else:
        manifest.meta['amendment_apply'].pop('retained_bounce_baselines', None)
        item.bounce_baseline = {"worker": 2, "review": 21, "merge": 0}
    assert consumed_bounces(manifest, "dispatch", item, "review") == item.bounces.review


@pytest.mark.parametrize("bad", [True, -1, "21"])
def test_invalid_old_baseline_never_creates_authorization(bad):
    manifest, item = fixture()
    manifest.meta['amendment_apply']['nodes']['dispatch']['bounce_baseline']['review'] = bad
    advance(manifest, Mock(), "amend-harness", {"authoring": ["harness"]})
    assert consumed_bounces(manifest, "dispatch", item, "review") == 22


def test_unsynced_old_recovery_is_not_carried_as_completed_authority():
    manifest, item = fixture()
    manifest.meta['amendment_apply']['nodes']['dispatch']['state'] = 'pending'
    advance(manifest, Mock(), "amend-harness", {"authoring": ["harness"]})
    assert consumed_bounces(manifest, "dispatch", item, "review") == 22


def test_real_apply_and_reentry_leave_unaffected_node_and_store_untouched(tmp_path):
    from test_amendment import _engine, _manifest, _proposal
    from omac.core.amendment import build_reviewed_amendment, apply_amendment
    engine = _engine()
    path = _manifest(tmp_path)
    bootstrap = engine.store.create_work_item("ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    dispatch = engine.store.create_work_item("ws", "dispatch", "desc", "dispatch", "alice", reviewer="bob")
    manifest = load_manifest(str(path))
    manifest.nodes['dispatch'] = deepcopy(manifest.nodes['bootstrap'])
    manifest.nodes['dispatch'].id = 'dispatch'
    manifest.nodes['dispatch'].work_item_id = dispatch.id
    engine.store.set_node_contract(dispatch.id, manifest.nodes['dispatch'].contract)
    engine.store.update_work_item_metadata(dispatch.id, review_bounce=22)
    manifest.meta.update(last_amendment_id='amend-old', amendment_apply={
        'schema': APPLY_LEDGER_SCHEMA, 'amendment_id': 'amend-old', 'nodes': {
            'dispatch': {'stage': 'authoring', 'state': 'synced',
                         'expected_contract_sha256': review_context_binding(manifest.nodes['dispatch'])['contract_sha256'],
                         'bounce_baseline': {'worker': 2, 'review': 21, 'merge': 0}}}})
    save_manifest(manifest, str(path))
    before = deepcopy(engine.store.get_work_item(dispatch.id))
    reviewed = build_reviewed_amendment(
        manifest, _proposal({'op': 'resume', 'node': 'bootstrap', 'stage': 'authoring'}),
        engine.store, issue_id='reviewed-new', reviewer_verdict='pass')
    for _ in range(2):
        result = apply_amendment(str(path), reviewed, engine.store, {'alice', 'bob', 'charlie'})
        updated = load_manifest(str(path))
        assert 'dispatch' not in updated.meta['amendment_apply']['nodes']
        assert all('dispatch' not in v for v in result['sync'].values())
        assert consumed_bounces(updated, 'dispatch', engine.store.get_work_item(dispatch.id), 'review') == 1
        assert engine.store.get_work_item(dispatch.id) == before
    assert bootstrap.id != dispatch.id
    engine.store.update_work_item_metadata(dispatch.id, review_bounce=23)
    latest = load_manifest(str(path))
    renewed = build_reviewed_amendment(
        latest, _proposal({'op': 'resume', 'node': 'dispatch', 'stage': 'authoring'}),
        engine.store, issue_id='reviewed-dispatch-again', reviewer_verdict='pass')
    apply_amendment(str(path), renewed, engine.store, {'alice', 'bob', 'charlie'})
    latest = load_manifest(str(path))
    assert consumed_bounces(latest, 'dispatch', engine.store.get_work_item(dispatch.id), 'review') == 0
    assert 'dispatch' not in latest.meta['amendment_apply'].get('retained_bounce_baselines', {})
    assert latest.meta['amendment_apply']['nodes']['dispatch']['work_item_id'] == dispatch.id


def test_retry_log_uses_manifest_authority_when_store_projection_is_missing():
    from omac.core.retry_budget import bounce_log_fields
    manifest, item = fixture()
    advance(manifest, Mock(), 'amend-harness', {'authoring': ['harness']})
    fields = bounce_log_fields(item, 'worker', absolute_count=3, limit=20,
                               manifest=manifest, node_id='dispatch')
    assert fields == {'absolute_audit_round': 3, 'current_generation_consumed': 1,
                      'current_generation_limit': 20}


@pytest.mark.parametrize('bad_identity', [None, 7, 'other-amendment'])
def test_retained_authority_requires_matching_accepted_ledger_identity(bad_identity):
    manifest, item = fixture()
    advance(manifest, Mock(), 'amend-harness', {'authoring': ['harness']})
    manifest.meta['amendment_apply']['amendment_id'] = bad_identity
    if bad_identity in (None, 7):
        manifest.meta['last_amendment_id'] = bad_identity
    assert consumed_bounces(manifest, 'dispatch', item, 'review') == 22

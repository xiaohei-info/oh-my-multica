"""Explicit amendment budget conservation (pure part): the budget authority is decided from a manifest ledger and an issue's facts.

The tests that apply an amendment to a real issue live in tests/it/test_it_amendment_retention.py."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from _support.amendkit import POOL, dag_manifest, proposal as _proposal
from _support.samples import work_item
from omac.core import amendment as core
from omac.core.manifest import load_manifest
from omac.core.retry_budget import consumed_bounces, preserved_amendment_budget, review_rework_budget
from omac.core.taskmeta import Bounces
from omac.errors import ValidationError

ITEM_ID = "item-bootstrap"


def case(tmp_path, **item_fields):
    """The `bootstrap` node and its issue, as `scenario` in the platform tests arranges them: worker=1 review=1 consumed."""
    path = dag_manifest(tmp_path, {"bootstrap": ITEM_ID})
    manifest = load_manifest(str(path))
    item = work_item(id=ITEM_ID, contract=manifest.nodes["bootstrap"].contract, bounces=Bounces(worker=1, review=1, merge=0),
                     bounce_baseline=None, **item_fields)
    return SimpleNamespace(path=str(path), manifest=manifest, item=item)


def seed_authority(c, baseline, *, native=True, active=False):
    record = {"amendment_id": "amend-old", "work_item_id": c.item.id,
              "contract_sha256": core._digest(core._dump_contract(c.manifest.nodes["bootstrap"].contract)),
              "bounce_baseline": deepcopy(baseline)}
    ledger = {"schema": "omac.amendment-apply/v1", "amendment_id": "amend-current", "nodes": {},
              "retained_bounce_baselines": {"bootstrap": record}}
    if active:
        ledger["nodes"]["bootstrap"] = {"stage": "authoring", "state": "synced", "work_item_id": c.item.id,
                                        "expected_contract_sha256": record["contract_sha256"], "bounce_baseline": deepcopy(baseline)}
        ledger["retained_bounce_baselines"] = {}
    c.manifest.meta.update(last_amendment_id="amend-current", amendment_apply=ledger)
    c.item.bounce_baseline = deepcopy(baseline) if native else None
    return ledger


@pytest.mark.parametrize("change", ["native-conflict", "missing-record", "wrong-contract", "wrong-item", "pending", "stale-ledger",
                                    "malformed", "regressed", "native-partial", "invalid-counter", "continuation"])
def test_missing_conflicting_or_stale_authority_is_refused_with_nothing_preserved(tmp_path, change):
    c = case(tmp_path)
    ledger = seed_authority(c, {"worker": 0, "review": 1, "merge": 0})
    record = ledger["retained_bounce_baselines"]["bootstrap"]
    if change == "native-conflict":
        c.item.bounce_baseline["review"] = 0
    elif change == "missing-record":
        ledger["retained_bounce_baselines"]["bootstrap"] = None
    elif change == "wrong-contract":
        record["contract_sha256"] = "f" * 64
    elif change == "wrong-item":
        record["work_item_id"] = "other"
    elif change == "pending":
        ledger["nodes"]["bootstrap"] = {"stage": "authoring", "state": "pending"}
    elif change == "stale-ledger":
        ledger["amendment_id"] = "not-current"
    elif change == "malformed":
        record["bounce_baseline"]["worker"] = True
    elif change == "regressed":
        c.item.bounces.review = 0
    elif change == "native-partial":
        c.item.bounce_baseline = {}
    elif change == "invalid-counter":
        c.item.bounces.worker = True   # no adapter produces this; the guard is for adapters that do not normalize counters
    else:
        c.item.review_continuation = {"authorized": True}
    before = deepcopy(c.item)

    with pytest.raises(ValidationError):
        preserved_amendment_budget(c.manifest, "bootstrap", c.item)

    assert c.item == before


@pytest.mark.parametrize("policy", [None, [], {}, True, "retain", ""])
def test_an_unknown_explicit_budget_policy_is_rejected(tmp_path, policy):
    c = case(tmp_path)
    amendment = _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"})
    amendment["budget_policy"] = policy

    with pytest.raises(ValidationError, match="budget_policy must be renew or preserve"):
        core.validate_proposal(c.manifest, amendment, POOL)


def test_preserve_fails_closed_for_a_historical_definition_only_correction(tmp_path):
    c = case(tmp_path)
    amendment = _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring", "historical_contract_correction": True})
    amendment["budget_policy"] = "preserve"

    errors = core.validate_proposal(c.manifest, amendment, POOL)

    assert any("does not support historical responsibility corrections" in error for error in errors)


def test_the_effective_baseline_is_read_from_the_manifest_authority_without_writes(tmp_path):
    c = case(tmp_path)
    c.item.bounces.review = 5
    seed_authority(c, {"worker": 0, "review": 4, "merge": 0})
    before = deepcopy(c.item)

    result = preserved_amendment_budget(c.manifest, "bootstrap", c.item)

    assert result["effective_baseline"] == {"worker": 0, "review": 4, "merge": 0}
    assert result["consumed"] == {"worker": 1, "review": 1, "merge": 0}
    assert c.item == before


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_the_manifest_authority_keeps_the_relative_consumption_and_the_remaining_budget(tmp_path, active, native):
    c = case(tmp_path)
    c.item.bounces = Bounces(worker=3, review=5, merge=1)
    baseline = {"worker": 2, "review": 4, "merge": 1}
    seed_authority(c, baseline, native=native, active=active)

    assert [consumed_bounces(c.manifest, "bootstrap", c.item, s) for s in ("worker", "review", "merge")] == [1, 1, 0]
    assert review_rework_budget(c.manifest, "bootstrap", c.item, 3).authorized_through_round is not None


def test_the_legacy_review_identity_is_byte_identical_when_no_budget_policy_is_named():
    """A golden vector: the inputs a no-policy review hashed before the budget policy existed, and the identity they produced."""
    identity = core._amendment_id(
        "7e71317d654309e74703b937d2feca94aa8be75dae3a0db3f093ad7808e28ff8",
        {"schema": "omac.dag-amendment/v1", "reason": "Reviewer found an invalid acceptance mapping",
         "operations": [{"op": "resume", "node": "bootstrap", "stage": "authoring"}]},
        {"review": [], "authoring": ["bootstrap"], "merging": []}, [],
        {"bootstrap": "5528e781766b7018571677461b163a1bb909c48ea8095f96de95eb4ecf57b4e7"},
        manifest_digest_value="e03c8d5a8c737c7cc5cb82b9ca77a86362cb96bbdcbb3366a0bfa94fe8ca0a5f", acceptance_digest=None,
        issue_id="offline-amendment", reviewer_verdict="pass", budget_bindings=None)

    assert identity == "amend-31c02b3689aa"

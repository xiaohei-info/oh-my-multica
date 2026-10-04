"""Explicit amendment budget conservation, without changing legacy renew behavior."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from omac.core import amendment as core
from omac.core.manifest import load_manifest, save_manifest
from omac.core.retry_budget import consumed_bounces
from omac.errors import ValidationError, PlatformError
from test_amendment import _engine, _manifest, _proposal
from omac.engines.models import WorkItemStatus

POOL = {"alice", "bob", "charlie"}


@pytest.fixture
def scenario(tmp_path):
    path = _manifest(tmp_path)
    manifest = load_manifest(str(path))
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob"
    )
    engine.store.set_node_contract(item.id, manifest.nodes["bootstrap"].contract)
    assert item.id == "1"
    item.bounces.worker = 1
    item.bounces.review = 1
    item.bounces.merge = 0
    item.bounce_baseline = None
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    proposal = _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"})
    proposal["budget_policy"] = "preserve"
    return SimpleNamespace(
        path=str(path),
        manifest=manifest,
        store=engine.store,
        item=item,
        proposal=proposal,
    )


def reviewed(c):
    return core.build_reviewed_amendment(
        c.manifest,
        c.proposal,
        c.store,
        issue_id="offline-amendment",
        reviewer_verdict="pass",
        agent_pool=POOL,
    )


def apply(c, request):
    return core.apply_amendment(c.path, request, c.store, POOL)


def test_preserve_does_not_refund_existing_consumption(scenario):
    c = scenario
    request = reviewed(c)
    before = deepcopy(c.item.bounces)
    apply(c, request)
    manifest = load_manifest(c.path)
    assert c.item.bounces == before
    assert c.item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}
    assert consumed_bounces(manifest, "bootstrap", c.item, "worker") == 1
    assert consumed_bounces(manifest, "bootstrap", c.item, "review") == 1


def test_default_renew_semantics_remain_unchanged(scenario):
    c = scenario
    c.proposal.pop("budget_policy")
    apply(c, reviewed(c))
    assert c.item.bounces.worker == c.item.bounces.review == 1
    assert c.item.bounce_baseline == {"worker": 1, "review": 1, "merge": 0}
    assert consumed_bounces(load_manifest(c.path), "bootstrap", c.item, "review") == 0


def test_review_identity_binds_explicit_budget_policy(scenario):
    c = scenario
    preserved = reviewed(c)
    c.proposal.pop("budget_policy")
    renewed = reviewed(c)
    assert preserved["amendment_id"] != renewed["amendment_id"]


@pytest.fixture
def actual_packet():
    folder = Path(__file__).parent / "fixtures/amend_budget_retention"
    manifest = load_manifest(str(folder / "manifest.yaml"))
    proposal = core.parse_proposal((folder / "proposal.yaml").read_text())
    items = {}
    for file in folder.glob("current-control-*.json"):
        d = json.loads(file.read_text())
        d["bounces"] = SimpleNamespace(**d["bounces"])
        items[d["id"]] = SimpleNamespace(**d)
    store = Mock()
    store.get_work_item.side_effect = lambda iid: items[iid]
    return manifest, proposal, items, store


def test_actual_seven_effective_baselines_are_retained_read_only(actual_packet):
    manifest, proposal, items, store = actual_packet
    from omac.core.retry_budget import preserved_amendment_budget

    before = deepcopy(items)
    nodes = core._minimal_rerun(manifest, proposal)[0]
    ids = sorted(set(k for values in nodes.values() for k in values))
    assert len(ids) == 7
    result = {}
    for node_id in ids:
        item = items[manifest.nodes[node_id].work_item_id]
        result[node_id] = preserved_amendment_budget(manifest, node_id, item)
    assert result["authorization"]["effective_baseline"] == {
        "worker": 0,
        "review": 0,
        "merge": 0,
    }
    assert result["authorization"]["consumed"] == {"worker": 1, "review": 1, "merge": 0}
    assert result["extension-package"]["effective_baseline"] == {
        "worker": 0,
        "review": 4,
        "merge": 0,
    }
    assert result["extension-package"]["consumed"] == {
        "worker": 0,
        "review": 0,
        "merge": 0,
    }
    assert sum(n.status == "done" for n in manifest.nodes.values()) == 68
    assert items == before
    store.update_work_item_metadata.assert_not_called()


def seed_authority(c, baseline, *, native=True, active=False):
    record = {
        "amendment_id": "amend-old",
        "work_item_id": c.item.id,
        "contract_sha256": core._digest(
            core._dump_contract(c.manifest.nodes["bootstrap"].contract)
        ),
        "bounce_baseline": deepcopy(baseline),
    }
    ledger = {
        "schema": "omac.amendment-apply/v1",
        "amendment_id": "amend-current",
        "nodes": {},
        "retained_bounce_baselines": {"bootstrap": record},
    }
    if active:
        ledger["nodes"]["bootstrap"] = {
            "stage": "authoring",
            "state": "synced",
            "work_item_id": c.item.id,
            "expected_contract_sha256": record["contract_sha256"],
            "bounce_baseline": deepcopy(baseline),
        }
        ledger["retained_bounce_baselines"] = {}
    c.manifest.meta.update(last_amendment_id="amend-current", amendment_apply=ledger)
    c.item.bounce_baseline = deepcopy(baseline) if native else None
    save_manifest(c.manifest, c.path)
    return ledger


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_manifest_authority_preserves_relative_consumption_and_remaining(
    scenario, active, native
):
    from omac.core.retry_budget import review_rework_budget

    c = scenario
    c.item.bounces.worker = 3
    c.item.bounces.review = 5
    c.item.bounces.merge = 1
    baseline = {"worker": 2, "review": 4, "merge": 1}
    seed_authority(c, baseline, native=native, active=active)
    before = review_rework_budget(c.manifest, "bootstrap", c.item, 3)
    apply(c, reviewed(c))
    after = load_manifest(c.path)
    assert c.item.bounce_baseline == baseline
    assert [
        consumed_bounces(after, "bootstrap", c.item, s)
        for s in ["worker", "review", "merge"]
    ] == [1, 1, 0]
    assert review_rework_budget(after, "bootstrap", c.item, 3) == before


@pytest.mark.parametrize(
    "change",
    [
        "native-conflict",
        "missing-record",
        "wrong-contract",
        "wrong-item",
        "pending",
        "stale-ledger",
        "malformed",
        "regressed",
        "native-partial",
        "invalid-counter",
        "continuation",
    ],
)
def test_missing_conflicting_or_stale_authority_fails_before_any_apply(
    scenario, change
):
    c = scenario
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
        c.item.bounces.worker = True
    else:
        c.item.review_continuation = {"authorized": True}
    before = deepcopy(c.item)
    content = Path(c.path).read_bytes()
    with pytest.raises(ValidationError):
        reviewed(c)
    assert c.item == before and Path(c.path).read_bytes() == content


@pytest.mark.parametrize(
    "drift",
    ["counter", "ci", "native", "manifest-authority", "identity", "binding", "policy"],
)
def test_reviewed_budget_cas_rejects_drift_before_manifest_or_store_writes(
    scenario, drift
):
    c = scenario
    r = reviewed(c)
    if drift == "counter":
        c.item.bounces.worker += 1
    elif drift == "ci":
        c.item.bounces.ci += 1
    elif drift == "native":
        c.item.bounce_baseline = {"worker": 1, "review": 1, "merge": 0}
    elif drift == "manifest-authority":
        seed_authority(c, {"worker": 1, "review": 1, "merge": 0}, native=False)
    elif drift == "identity":
        c.manifest.nodes["bootstrap"].work_item_id = "different"
        save_manifest(c.manifest, c.path)
    elif drift == "binding":
        r["base"]["budget_bindings"]["nodes"]["bootstrap"]["effective_baseline"][
            "worker"
        ] = 1
    else:
        r["budget_policy"] = "renew"
    before = deepcopy(c.item)
    content = Path(c.path).read_bytes()
    with pytest.raises(ValidationError):
        apply(c, r)
    assert c.item == before and Path(c.path).read_bytes() == content


def test_unknown_accepted_authoring_write_resumes_without_refund_or_double_write(
    scenario,
):
    c = scenario
    r = reviewed(c)
    original = c.store.restore_authoring_generation
    count = 0

    def lost(*a, **kw):
        nonlocal count
        original(*a, **kw)
        count += 1
        raise PlatformError("accepted authoring write response lost")

    c.store.restore_authoring_generation = lost
    with pytest.raises(PlatformError):
        apply(c, r)
    c.store.restore_authoring_generation = Mock(wraps=original)
    apply(c, r)
    apply(c, r)
    assert count == 1
    c.store.restore_authoring_generation.assert_not_called()
    assert c.item.bounces.worker == c.item.bounces.review == 1
    assert c.item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}


def test_tampered_pending_ledger_cannot_refund_consumed_budget(scenario):
    c = scenario
    r = reviewed(c)
    original = c.store.restore_authoring_generation
    c.store.restore_authoring_generation = Mock(
        side_effect=PlatformError("before write")
    )
    with pytest.raises(PlatformError):
        apply(c, r)
    m = load_manifest(c.path)
    m.meta["amendment_apply"]["nodes"]["bootstrap"]["bounce_baseline"]["worker"] = 1
    save_manifest(m, c.path)
    c.store.restore_authoring_generation = Mock(wraps=original)
    with pytest.raises(ValidationError):
        apply(c, r)
    c.store.restore_authoring_generation.assert_not_called()


def test_readonly_preview_preserves_every_file_and_item(scenario):
    c = scenario
    before = deepcopy(c.item)
    content = Path(c.path).read_bytes()
    result = core.preview_amendment_budgets(c.manifest, c.proposal, c.store, POOL)
    assert result["read_only"] is True
    assert result["preserve"]["nodes"]["bootstrap"]["consumed"]["worker"] == 1
    assert result["renew_baselines"]["bootstrap"]["worker"] == 1
    assert c.item == before and Path(c.path).read_bytes() == content


@pytest.mark.parametrize("policy", [None, [], {}, True, "retain", ""])
def test_unknown_explicit_budget_policy_rejected(scenario, policy):
    c = scenario
    c.proposal["budget_policy"] = policy
    with pytest.raises(ValidationError):
        reviewed(c)


def test_unknown_partial_then_accepted_write_checkpoint_and_repeat_are_budget_safe(
    scenario,
):
    c = scenario
    contract = core._dump_contract(c.manifest.nodes["bootstrap"].contract)
    contract["scope_paths"] = ["src/new-scope.py"]
    c.proposal["operations"] = [
        {
            "op": "update",
            "node": "bootstrap",
            "set": {"contract": contract},
            "migration": {
                "ownership_transfer": True,
                "reason": "fixture-only declared scope migration",
            },
        }
    ]
    r = reviewed(c)
    assert r["analysis"]["minimal_rerun"]["authoring"] == ["bootstrap"]
    original = c.store.restore_authoring_generation
    calls = 0

    def lost(item_id, next_contract, generation, bounce_baseline=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            c.store.set_node_contract(item_id, next_contract)
        else:
            original(item_id, next_contract, generation, bounce_baseline)
        raise PlatformError("partial or accepted unknown response")

    c.store.restore_authoring_generation = lost
    with pytest.raises(PlatformError):
        apply(c, r)
    with pytest.raises(PlatformError):
        apply(c, r)
    c.store.restore_authoring_generation = Mock(wraps=original)
    apply(c, r)
    apply(c, r)
    assert calls == 2
    c.store.restore_authoring_generation.assert_not_called()
    assert c.item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}
    assert [c.item.bounces.worker, c.item.bounces.review] == [1, 1]


@pytest.mark.parametrize(
    "drift", ["counter", "native", "stage", "target", "policy", "node-contract"]
)
def test_unknown_resume_refuses_changed_budget_or_recovery_target_without_more_writes(
    scenario, drift
):
    c = scenario
    r = reviewed(c)
    original = c.store.restore_authoring_generation
    c.store.restore_authoring_generation = Mock(
        side_effect=PlatformError("interrupted before restore")
    )
    with pytest.raises(PlatformError):
        apply(c, r)
    m = load_manifest(c.path)
    entry = m.meta["amendment_apply"]["nodes"]["bootstrap"]
    if drift == "counter":
        c.item.bounces.review += 1
    elif drift == "native":
        c.item.bounce_baseline = {"worker": 1, "review": 1, "merge": 0}
    elif drift == "stage":
        entry["stage"] = "review"
    elif drift == "target":
        entry["expected_contract_sha256"] = "f" * 64
    elif drift == "policy":
        m.meta["amendment_apply"].pop("budget_policy")
    else:
        m.nodes["bootstrap"].contract.objective = "changed after apply"
    save_manifest(m, c.path)
    c.store.restore_authoring_generation = Mock(wraps=original)
    before = deepcopy(c.item)
    with pytest.raises(ValidationError):
        apply(c, r)
    assert c.item == before
    c.store.restore_authoring_generation.assert_not_called()


def test_preview_cli_does_not_take_write_lock_or_mutate_inputs(
    scenario, monkeypatch, capsys, tmp_path
):
    from omac.cli import main as cli
    from omac.cli.commands import dag
    import yaml

    c = scenario
    proposal_file = tmp_path / "proposal.yaml"
    proposal_file.write_text(yaml.safe_dump(c.proposal))
    monkeypatch.setattr(
        dag, "_assemble_engine", lambda _: (SimpleNamespace(store=c.store), {})
    )
    monkeypatch.setattr(dag, "_load_config_for_manifest", lambda _: {})
    monkeypatch.setattr(
        dag,
        "manifest_write_lock",
        Mock(side_effect=AssertionError("preview must not write")),
    )
    content = Path(c.path).read_bytes()
    pcontent = proposal_file.read_bytes()
    before = deepcopy(c.item)
    assert (
        cli.main(
            [
                "dag",
                "amend",
                "budget-preview",
                c.path,
                str(proposal_file),
                "--output",
                "json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        result["read_only"]
        and result["preserve"]["nodes"]["bootstrap"]["consumed"]["review"] == 1
    )
    assert (
        c.item == before
        and Path(c.path).read_bytes() == content
        and proposal_file.read_bytes() == pcontent
    )


def test_preserve_then_legitimate_progress_never_restores_old_counters(scenario):
    c = scenario
    r = reviewed(c)
    apply(c, r)
    c.item.bounces.worker += 1
    c.store.restore_authoring_generation = Mock(
        side_effect=AssertionError("must not roll back progress")
    )
    apply(c, r)
    assert c.item.bounces.worker == 2
    assert consumed_bounces(load_manifest(c.path), "bootstrap", c.item, "worker") == 2


def test_existing_legacy_review_identity_remains_byte_identical(scenario):
    c = scenario
    c.proposal.pop("budget_policy")
    assert reviewed(c)["amendment_id"] == "amend-31c02b3689aa"


@pytest.fixture
def derived_scenario(tmp_path):
    from test_amendment import _topology_manifest

    path = _topology_manifest(tmp_path)
    manifest = load_manifest(str(path))
    engine = _engine()
    first = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob"
    )
    second = engine.store.create_work_item(
        "ws", "dependent", "desc", "started-dependent", "charlie", reviewer="bob"
    )
    engine.store.set_node_contract(first.id, manifest.nodes["bootstrap"].contract)
    for item in [first, second]:
        item.bounces.worker = 1
        item.bounces.review = 1
        engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    proposal = _proposal(
        {"op": "update", "node": "bootstrap", "set": {"blocked_by": ["foundation"]}}
    )
    proposal["budget_policy"] = "preserve"
    return SimpleNamespace(
        path=str(path),
        manifest=manifest,
        store=engine.store,
        item=first,
        second=second,
        proposal=proposal,
    )


def test_derived_recoveries_and_done_objects_journals_are_preserved(derived_scenario):
    c = derived_scenario
    c.manifest.meta["sdk_command18_checkpoint_reviews"] = {
        "old": {"state": "consumed", "step": 8}
    }
    save_manifest(c.manifest, c.path)
    done = deepcopy(c.manifest.nodes["foundation"])
    r = reviewed(c)
    assert set(r["base"]["budget_bindings"]["nodes"]) == {
        "bootstrap",
        "started-dependent",
    }
    assert r["analysis"]["minimal_rerun"]["authoring"] == [
        "bootstrap",
        "started-dependent",
    ]
    apply(c, r)
    m = load_manifest(c.path)
    assert m.nodes["foundation"] == done
    assert (
        m.meta["sdk_command18_checkpoint_reviews"]
        == c.manifest.meta["sdk_command18_checkpoint_reviews"]
    )
    for key, item in [("bootstrap", c.item), ("started-dependent", c.second)]:
        assert item.bounces.worker == item.bounces.review == 1
        assert item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}
        assert consumed_bounces(m, key, item, "review") == 1


def test_later_derived_drift_blocks_all_store_side_effects(derived_scenario):
    c = derived_scenario
    r = reviewed(c)
    c.second.bounces.worker += 1
    c.store.restore_authoring_generation = Mock(
        side_effect=AssertionError("must fail before any write")
    )
    content = Path(c.path).read_bytes()
    with pytest.raises(ValidationError):
        apply(c, r)
    assert Path(c.path).read_bytes() == content
    c.store.restore_authoring_generation.assert_not_called()


def test_counter_drift_between_initial_capture_and_ledger_preparation_fails_closed(
    scenario,
):
    c = scenario
    r = reviewed(c)
    get = c.store.get_work_item
    calls = 0

    # Inject the concurrent audit change on the ledger's second post-review read.
    def read(iid):
        nonlocal calls
        item = get(iid)
        calls += 1
        if calls == 3:
            item.bounces.worker += 1
        return item

    c.store.get_work_item = read
    content = Path(c.path).read_bytes()
    with pytest.raises(ValidationError):
        apply(c, r)
    assert Path(c.path).read_bytes() == content


def test_preserve_fails_closed_for_historical_definition_only_budget_authority(
    scenario,
):
    c = scenario
    c.proposal["operations"][0]["historical_contract_correction"] = True
    errors = core.validate_proposal(c.manifest, c.proposal, POOL)
    assert any(
        "does not support historical responsibility corrections" in error
        for error in errors
    )
    assert c.item.bounces.worker == c.item.bounces.review == 1


@pytest.mark.parametrize("stage", ["review", "merging"])
def test_preserve_review_and_merge_recovery_does_not_renew_relative_budget(
    scenario, stage
):
    from test_amendment import _seal_review_fixture

    c = scenario
    c.store.update_work_item_metadata(
        c.item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"commands": []},
        review_verdict="pass",
    )
    _seal_review_fixture(SimpleNamespace(store=c.store))
    c.proposal["operations"][0]["stage"] = stage
    before = deepcopy(c.item.bounces)
    apply(c, reviewed(c))
    m = load_manifest(c.path)
    assert c.item.bounces == before
    assert consumed_bounces(m, "bootstrap", c.item, "worker") == 1
    assert consumed_bounces(m, "bootstrap", c.item, "review") == 1

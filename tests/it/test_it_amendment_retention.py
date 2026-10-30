"""Explicit amendment budget conservation applied to real issues: refunds, drift, lost responses, previews and derived recoveries."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from _support.amendkit import POOL, bootstrap, proposal as _proposal, seal_review_fixture, topology_manifest
from omac.cli import main as cli
from omac.cli.commands import dag
from omac.core import amendment as core
from omac.pipeline.amendment import apply_amendment
from omac.core.manifest import load_manifest, save_manifest
from omac.core.retry_budget import consumed_bounces, review_rework_budget
from omac.core.workitem import WorkItemStatus
from omac.errors import PlatformError, ValidationError

RESUME = {"op": "resume", "node": "bootstrap", "stage": "authoring"}


class Scenario:
    """`bootstrap` blocked with one worker and one review bounce consumed; the proposal preserves the budget."""

    def __init__(self, kit, tmp_path):
        self.kit, self.store = kit, kit.store
        path, item = bootstrap(kit, tmp_path)
        self.path, self.item_id = str(path), item.id
        self.manifest = load_manifest(self.path)
        self.store.set_node_contract(item.id, self.manifest.nodes["bootstrap"].contract)
        self.store.update_work_item_metadata(item.id, worker_bounce=1, review_bounce=1, merge_bounce=0)
        self.proposal = _proposal(dict(RESUME))
        self.proposal["budget_policy"] = "preserve"

    @property
    def item(self):
        return self.store.get_work_item(self.item_id)

    def reviewed(self):
        return core.build_reviewed_amendment(self.manifest, self.proposal, self.store, issue_id="offline-amendment", reviewer_verdict="pass",
                                             agent_pool=POOL)

    def apply(self, request):
        return apply_amendment(self.path, request, self.store, POOL)

    def seed_authority(self, baseline, *, native=True):
        record = {"amendment_id": "amend-old", "work_item_id": self.item_id,
                  "contract_sha256": core._digest(core._dump_contract(self.manifest.nodes["bootstrap"].contract)),
                  "bounce_baseline": copy.deepcopy(baseline)}
        self.manifest.meta.update(last_amendment_id="amend-current", amendment_apply={
            "schema": "omac.amendment-apply/v1", "amendment_id": "amend-current", "nodes": {},
            "retained_bounce_baselines": {"bootstrap": record}})
        if native:
            self.store.update_work_item_metadata(self.item_id, bounce_baseline=baseline)
        save_manifest(self.manifest, self.path)


@pytest.fixture()
def scenario(kit, tmp_path):
    return Scenario(kit, tmp_path)


def counters(item):
    return (item.bounces.worker, item.bounces.review)


def test_preserve_does_not_refund_the_consumption_that_already_happened(scenario):
    c = scenario
    request, before = c.reviewed(), c.item.bounces

    c.apply(request)

    after = c.item
    assert after.bounces == before and after.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}
    manifest = load_manifest(c.path)
    assert consumed_bounces(manifest, "bootstrap", after, "worker") == 1 and consumed_bounces(manifest, "bootstrap", after, "review") == 1


def test_the_default_renew_semantics_stay_as_they_were(scenario):
    c = scenario
    c.proposal.pop("budget_policy")

    c.apply(c.reviewed())

    after = c.item
    assert after.bounces.worker == after.bounces.review == 1 and after.bounce_baseline == {"worker": 1, "review": 1, "merge": 0}
    assert consumed_bounces(load_manifest(c.path), "bootstrap", after, "review") == 0


def test_the_reviewed_identity_binds_the_explicit_budget_policy(scenario):
    c = scenario
    preserved = c.reviewed()
    c.proposal.pop("budget_policy")

    assert preserved["amendment_id"] != c.reviewed()["amendment_id"]


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_applying_over_a_manifest_authority_keeps_the_relative_consumption_and_the_remaining_budget(scenario, active, native):
    c = scenario
    c.store.update_work_item_metadata(c.item_id, worker_bounce=3, review_bounce=5, merge_bounce=1)
    baseline = {"worker": 2, "review": 4, "merge": 1}
    c.seed_authority(baseline, native=native)
    if active:   # the same authority as an active, synced ledger entry
        ledger = c.manifest.meta["amendment_apply"]
        record = ledger["retained_bounce_baselines"]["bootstrap"]
        ledger["nodes"]["bootstrap"] = {"stage": "authoring", "state": "synced", "work_item_id": c.item_id,
                                        "expected_contract_sha256": record["contract_sha256"], "bounce_baseline": copy.deepcopy(baseline)}
        ledger["retained_bounce_baselines"] = {}
        save_manifest(c.manifest, c.path)
    c.manifest = load_manifest(c.path)
    before = review_rework_budget(c.manifest, "bootstrap", c.item, 3)

    c.apply(c.reviewed())

    after = load_manifest(c.path)
    assert c.item.bounce_baseline == baseline
    assert [consumed_bounces(after, "bootstrap", c.item, s) for s in ("worker", "review", "merge")] == [1, 1, 0]
    assert review_rework_budget(after, "bootstrap", c.item, 3) == before


@pytest.mark.parametrize("reason", ["native-conflict", "continuation"])
def test_a_refused_review_leaves_every_file_and_the_issue_untouched(scenario, reason):
    c = scenario
    c.seed_authority({"worker": 0, "review": 1, "merge": 0})
    if reason == "native-conflict":
        c.store.update_work_item_metadata(c.item_id, bounce_baseline={"worker": 0, "review": 0, "merge": 0})
    else:
        c.store.update_work_item_metadata(c.item_id, review_continuation={"schema": "omac.review-continuation/v1", "authorized_through_round": 3})
    before, content = c.item, Path(c.path).read_bytes()

    with pytest.raises(ValidationError):
        c.reviewed()

    assert c.item == before and Path(c.path).read_bytes() == content


def drift_counter(c, r):
    c.store.update_work_item_metadata(c.item_id, worker_bounce=c.item.bounces.worker + 1)


def drift_ci(c, r):
    c.store.update_work_item_metadata(c.item_id, ci_bounce=c.item.bounces.ci + 1)


def drift_native(c, r):
    c.store.update_work_item_metadata(c.item_id, bounce_baseline={"worker": 1, "review": 1, "merge": 0})


def drift_manifest_authority(c, r):
    c.seed_authority({"worker": 1, "review": 1, "merge": 0}, native=False)


def drift_identity(c, r):
    other = c.store.create_work_item(c.kit.workspace, "other", "desc", "other-issue", "alice", reviewer="bob")
    c.manifest.nodes["bootstrap"].work_item_id = other.id   # the node now points at a different issue
    save_manifest(c.manifest, c.path)


def drift_binding(c, r):
    r["base"]["budget_bindings"]["nodes"]["bootstrap"]["effective_baseline"]["worker"] = 1


def drift_policy(c, r):
    r["budget_policy"] = "renew"


@pytest.mark.parametrize("drift", [drift_counter, drift_ci, drift_native, drift_manifest_authority, drift_identity, drift_binding, drift_policy],
                         ids=lambda f: f.__name__.removeprefix("drift_"))
def test_the_reviewed_budget_binding_rejects_drift_before_any_manifest_or_store_write(scenario, drift):
    c = scenario
    request = c.reviewed()
    drift(c, request)
    before, content = c.item, Path(c.path).read_bytes()

    with pytest.raises(ValidationError):
        c.apply(request)

    assert c.item == before and Path(c.path).read_bytes() == content


def test_a_lost_response_to_the_accepted_authoring_write_resumes_without_refund_or_a_second_write(scenario):
    c = scenario
    request = c.reviewed()
    fault = c.kit.fault(c.store, "restore_authoring_generation", when="after", error=PlatformError("accepted authoring write response lost"))

    with pytest.raises(PlatformError):
        c.apply(request)
    fault["armed"] = False
    restores = c.kit.record(c.store, "restore_authoring_generation")
    c.apply(request)
    c.apply(request)

    assert fault["calls"] == 1 and not restores
    assert counters(c.item) == (1, 1) and c.item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}


def test_a_tampered_pending_ledger_cannot_refund_consumed_budget(scenario):
    c = scenario
    request = c.reviewed()
    fault = c.kit.fault(c.store, "restore_authoring_generation", when="before", error=PlatformError("before write"))
    with pytest.raises(PlatformError):
        c.apply(request)
    manifest = load_manifest(c.path)
    manifest.meta["amendment_apply"]["nodes"]["bootstrap"]["bounce_baseline"]["worker"] = 1
    save_manifest(manifest, c.path)
    fault["armed"] = False
    restores = c.kit.record(c.store, "restore_authoring_generation")

    with pytest.raises(ValidationError):
        c.apply(request)

    assert not restores


def test_a_read_only_preview_leaves_every_file_and_the_issue_alone(scenario):
    c = scenario
    before, content = c.item, Path(c.path).read_bytes()

    result = core.preview_amendment_budgets(c.manifest, c.proposal, c.store, POOL)

    assert result["read_only"] is True
    assert result["preserve"]["nodes"]["bootstrap"]["consumed"]["worker"] == 1 and result["renew_baselines"]["bootstrap"]["worker"] == 1
    assert c.item == before and Path(c.path).read_bytes() == content


def test_a_partial_then_an_accepted_unknown_write_and_a_repeat_stay_budget_safe(scenario):
    c = scenario
    contract = core._dump_contract(c.manifest.nodes["bootstrap"].contract)
    contract["scope_paths"] = ["src/new-scope.py"]
    c.proposal["operations"] = [{"op": "update", "node": "bootstrap", "set": {"contract": contract},
                                 "migration": {"ownership_transfer": True, "reason": "fixture-only declared scope migration"}}]
    request = c.reviewed()
    assert request["analysis"]["minimal_rerun"]["authoring"] == ["bootstrap"]
    original, calls = c.store.restore_authoring_generation, []

    def lost(item_id, next_contract, generation, *args, **kwargs):
        calls.append(item_id)
        if len(calls) == 1:
            c.store.set_node_contract(item_id, next_contract)   # only the contract reached the platform
        else:
            original(item_id, next_contract, generation, *args, **kwargs)
        raise PlatformError("partial or accepted unknown response")

    c.kit.monkeypatch.setattr(c.store, "restore_authoring_generation", lost)
    for _ in range(2):
        with pytest.raises(PlatformError):
            c.apply(request)
    c.kit.monkeypatch.setattr(c.store, "restore_authoring_generation", original)
    restores = c.kit.record(c.store, "restore_authoring_generation")
    c.apply(request)
    c.apply(request)

    assert len(calls) == 2 and not restores
    assert c.item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0} and counters(c.item) == (1, 1)


def resume_counter(c, m, entry):
    c.store.update_work_item_metadata(c.item_id, review_bounce=c.item.bounces.review + 1)


def resume_native(c, m, entry):
    c.store.update_work_item_metadata(c.item_id, bounce_baseline={"worker": 1, "review": 1, "merge": 0})


def resume_stage(c, m, entry):
    entry["stage"] = "review"


def resume_target(c, m, entry):
    entry["expected_contract_sha256"] = "f" * 64


def resume_policy(c, m, entry):
    m.meta["amendment_apply"].pop("budget_policy")


def resume_node_contract(c, m, entry):
    m.nodes["bootstrap"].contract.objective = "changed after apply"


@pytest.mark.parametrize("drift", [resume_counter, resume_native, resume_stage, resume_target, resume_policy, resume_node_contract],
                         ids=lambda f: f.__name__.removeprefix("resume_"))
def test_resuming_an_unknown_write_refuses_a_changed_budget_or_target_without_further_writes(scenario, drift):
    c = scenario
    request = c.reviewed()
    fault = c.kit.fault(c.store, "restore_authoring_generation", when="before", error=PlatformError("interrupted before restore"))
    with pytest.raises(PlatformError):
        c.apply(request)
    manifest = load_manifest(c.path)
    drift(c, manifest, manifest.meta["amendment_apply"]["nodes"]["bootstrap"])
    save_manifest(manifest, c.path)
    fault["armed"] = False
    restores = c.kit.record(c.store, "restore_authoring_generation")
    before = c.item

    with pytest.raises(ValidationError):
        c.apply(request)

    assert c.item == before and not restores


def test_the_preview_command_takes_no_write_lock_and_changes_no_input(scenario, tmp_path, capsys):
    c = scenario
    proposal_file = tmp_path / "proposal.yaml"
    proposal_file.write_text(yaml.safe_dump(c.proposal))
    c.kit.monkeypatch.setattr(dag, "_assemble_engine", lambda _: (c.kit.engine, {}))
    c.kit.monkeypatch.setattr(dag, "_load_config_for_manifest", lambda _: {})
    c.kit.monkeypatch.setattr(dag, "manifest_write_lock", lambda *a, **k: pytest.fail("preview must not write"))
    content, pcontent, before = Path(c.path).read_bytes(), proposal_file.read_bytes(), c.item

    assert cli.main(["dag", "amend", "budget-preview", c.path, str(proposal_file), "--output", "json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["read_only"] and result["preserve"]["nodes"]["bootstrap"]["consumed"]["review"] == 1
    assert c.item == before and Path(c.path).read_bytes() == content and proposal_file.read_bytes() == pcontent


def test_progress_after_a_preserving_apply_never_restores_the_old_counters(scenario):
    c = scenario
    request = c.reviewed()
    c.apply(request)
    c.store.update_work_item_metadata(c.item_id, worker_bounce=c.item.bounces.worker + 1)
    c.kit.forbid(c.store, "restore_authoring_generation")

    c.apply(request)

    assert c.item.bounces.worker == 2
    assert consumed_bounces(load_manifest(c.path), "bootstrap", c.item, "worker") == 2


@pytest.mark.parametrize("stage", ["review", "merging"])
def test_preserving_a_review_or_merge_recovery_does_not_renew_the_relative_budget(scenario, stage):
    c = scenario
    c.store.update_work_item_metadata(c.item_id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
                                      verification={"commands": []}, review_verdict="pass")
    seal_review_fixture(c.store, c.item_id)
    c.proposal["operations"][0]["stage"] = stage
    before = c.item.bounces

    c.apply(c.reviewed())

    manifest = load_manifest(c.path)
    assert c.item.bounces == before
    assert consumed_bounces(manifest, "bootstrap", c.item, "worker") == 1 and consumed_bounces(manifest, "bootstrap", c.item, "review") == 1


# ---- derived recoveries -------------------------------------------------------------------------------------------------------

@pytest.fixture()
def derived(kit, tmp_path):
    first = kit.store.create_work_item(kit.workspace, "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    second = kit.store.create_work_item(kit.workspace, "dependent", "desc", "started-dependent", "charlie", reviewer="bob")
    path = topology_manifest(tmp_path, {"bootstrap": first.id, "started-dependent": second.id})
    manifest = load_manifest(str(path))
    kit.store.set_node_contract(first.id, manifest.nodes["bootstrap"].contract)
    for item in (first, second):
        kit.store.update_work_item_metadata(item.id, worker_bounce=1, review_bounce=1)
        kit.store.update_status(item.id, WorkItemStatus.BLOCKED)
    amendment = _proposal({"op": "update", "node": "bootstrap", "set": {"blocked_by": ["foundation"]}})
    amendment["budget_policy"] = "preserve"
    return SimpleNamespace(kit=kit, store=kit.store, path=str(path), manifest=manifest, first=first, second=second, proposal=amendment)


def derived_reviewed(c):
    return core.build_reviewed_amendment(c.manifest, c.proposal, c.store, issue_id="offline-amendment", reviewer_verdict="pass", agent_pool=POOL)


def test_derived_recoveries_keep_their_budgets_and_the_done_node_and_journals_untouched(derived):
    c = derived
    c.manifest.meta["sdk_command18_checkpoint_reviews"] = {"old": {"state": "consumed", "step": 8}}
    save_manifest(c.manifest, c.path)
    done = copy.deepcopy(c.manifest.nodes["foundation"])
    request = derived_reviewed(c)
    assert set(request["base"]["budget_bindings"]["nodes"]) == {"bootstrap", "started-dependent"}
    assert request["analysis"]["minimal_rerun"]["authoring"] == ["bootstrap", "started-dependent"]

    apply_amendment(c.path, request, c.store, POOL)

    m = load_manifest(c.path)
    assert m.nodes["foundation"] == done
    assert m.meta["sdk_command18_checkpoint_reviews"] == c.manifest.meta["sdk_command18_checkpoint_reviews"]
    for key, item_id in (("bootstrap", c.first.id), ("started-dependent", c.second.id)):
        item = c.store.get_work_item(item_id)
        assert item.bounces.worker == item.bounces.review == 1 and item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}
        assert consumed_bounces(m, key, item, "review") == 1


def test_drift_in_a_later_derived_node_blocks_every_store_side_effect(derived):
    c = derived
    request = derived_reviewed(c)
    c.store.update_work_item_metadata(c.second.id, worker_bounce=c.store.get_work_item(c.second.id).bounces.worker + 1)
    restores = c.kit.record(c.store, "restore_authoring_generation")
    content = Path(c.path).read_bytes()

    with pytest.raises(ValidationError):
        apply_amendment(c.path, request, c.store, POOL)

    assert Path(c.path).read_bytes() == content and not restores


def test_counter_drift_between_the_first_capture_and_the_ledger_preparation_fails_closed(scenario):
    c = scenario
    request = c.reviewed()
    # apply reads the issue to validate the affected set, to capture the budget, to verify the evidence and then to prepare the
    # ledger: a concurrent audit bumps the worker counter exactly on that fourth read
    c.kit.distort(c.store, "get_work_item", lambda item: replace(item, bounces=replace(item.bounces, worker=item.bounces.worker + 1)),
                  skip=3, times=1)
    content = Path(c.path).read_bytes()

    with pytest.raises(ValidationError):
        c.apply(request)

    assert Path(c.path).read_bytes() == content


# ---- an unrelated amendment must not disturb a node that holds retained budget ---------------------------------------------------

def test_a_real_apply_and_its_reentry_leave_the_unaffected_node_and_its_issue_untouched(kit, tmp_path):
    from omac.core.amendment import APPLY_LEDGER_SCHEMA, build_reviewed_amendment
    from omac.core.taskmeta import review_context_binding

    path, bootstrap_item = bootstrap(kit, tmp_path)
    dispatch = kit.store.create_work_item(kit.workspace, "dispatch", "desc", "dispatch", "alice", reviewer="bob")
    manifest = load_manifest(str(path))
    manifest.nodes["dispatch"] = copy.deepcopy(manifest.nodes["bootstrap"])
    manifest.nodes["dispatch"].id, manifest.nodes["dispatch"].work_item_id = "dispatch", dispatch.id
    kit.store.set_node_contract(dispatch.id, manifest.nodes["dispatch"].contract)
    kit.store.update_work_item_metadata(dispatch.id, review_bounce=22)
    manifest.meta.update(last_amendment_id="amend-old", amendment_apply={
        "schema": APPLY_LEDGER_SCHEMA, "amendment_id": "amend-old", "nodes": {"dispatch": {
            "stage": "authoring", "state": "synced",
            "expected_contract_sha256": review_context_binding(manifest.nodes["dispatch"])["contract_sha256"],
            "bounce_baseline": {"worker": 2, "review": 21, "merge": 0}}}})
    save_manifest(manifest, str(path))
    before = copy.deepcopy(kit.store.get_work_item(dispatch.id))
    reviewed = build_reviewed_amendment(manifest, _proposal(dict(RESUME)), kit.store, issue_id="reviewed-new", reviewer_verdict="pass")

    for _ in range(2):
        result = apply_amendment(str(path), reviewed, kit.store, POOL)
        updated = load_manifest(str(path))
        assert "dispatch" not in updated.meta["amendment_apply"]["nodes"]
        assert all("dispatch" not in v for v in result["sync"].values())
        assert consumed_bounces(updated, "dispatch", kit.store.get_work_item(dispatch.id), "review") == 1
        assert kit.store.get_work_item(dispatch.id) == before

    kit.store.update_work_item_metadata(dispatch.id, review_bounce=23)
    renewed = build_reviewed_amendment(load_manifest(str(path)), _proposal({"op": "resume", "node": "dispatch", "stage": "authoring"}),
                                       kit.store, issue_id="reviewed-dispatch-again", reviewer_verdict="pass")
    apply_amendment(str(path), renewed, kit.store, POOL)
    latest = load_manifest(str(path))
    assert consumed_bounces(latest, "dispatch", kit.store.get_work_item(dispatch.id), "review") == 0
    assert "dispatch" not in latest.meta["amendment_apply"].get("retained_bounce_baselines", {})
    assert latest.meta["amendment_apply"]["nodes"]["dispatch"]["work_item_id"] == dispatch.id


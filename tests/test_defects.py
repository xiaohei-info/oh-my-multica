"""Upstream-defect feedback: a report becomes a fix node the reporter waits for, with budgets, fingerprints and a crash-safe
system amendment (设计 §7.5). Platform and the amendment's Store compensation are replaced by in-memory fakes; the
amendment identity, whitelist and ledger code under test are the real ones."""
import copy
import json
from argparse import Namespace
from types import SimpleNamespace

import pytest
import yaml

from _support.amendkit import contract_update
from _support.state import save_literal
from omac.core import graph
from omac.core.amendment import (
    _amendment_identity, _apply_definition, _verify_amendment_identity, build_system_amendment, system_proposal_errors,
    validate_affected,
)
from omac.core.defects import (
    anchor_ids, defect_errors, fingerprint, fix_node_id, review_origin_defects, routing_problem,
)
from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase
from omac.core.workitem import WorkItem, WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import defects as shell
from omac.cli.commands import defect as defect_cli

CONTRACT = contract_update()["set"]["contract"]


def defect(**over):
    return {"class": "design-gap", "suspected_node": "lib", "anchors": ["FLOW-1/ACT-1"], "design_ref": "docs/design.md#4",
            "expected": "GET /items returns views", "observed": "views missing", "locations": ["lib/items.py:list_items"],
            "repro": {"command": "pytest -q tests/e2e", "exit_code": 1}, **over}


def blocker_decision(defects, gate="worker", reason="worker-decision-required"):
    return {"schema": DECISION_REQUIRED_SCHEMA, "reason_code": reason, "kind": "develop", "phase": "authoring",
            "gate": gate, "resume_issue_id": "w-api",
            "blocker": {"schema": "omac.worker-blocker/v3", "reason_code": "upstream-defect", "defects": defects}}


class Store:
    """The few platform reads and metadata writes the defect shell needs."""

    def __init__(self, items):
        self.items = items
        self.config = SimpleNamespace(workspace_id="ws")
        self.writes = []

    def list_members(self, workspace):
        return {"alice", "bob", "carol"}

    def get_work_item(self, item_id):
        return self.items[item_id]

    def update_status(self, item_id, status):
        self.items[item_id].status = status

    def add_comment(self, item_id, text):
        pass

    def update_work_item_metadata(self, item_id, **fields):
        self.writes.append((item_id, fields))
        for name, value in fields.items():
            setattr(self.items[item_id], name, value)


def work_item(item_id, decision=None):
    item = WorkItem(id=item_id, workspace_id="ws", title="t", description="", status=WorkItemStatus.BLOCKED, dag_key=item_id,
                    kind=TaskKind.DEVELOP, phase=TaskPhase.AUTHORING, worker="carol")
    item.decision_required = decision
    return item


def node(node_id, worker, blocked_by=(), status="todo", **extra):
    return {"id": node_id, "worker": worker, "reviewer": "bob", "blocked_by": list(blocked_by), "status": status,
            "contract": copy.deepcopy({**CONTRACT, "source_of_truth": ["docs/design.md"]}), **extra}


@pytest.fixture
def world(tmp_path):
    """lib (done) ← api (blocked, reports a defect of lib); a second blocked sibling ``web`` for grouping tests."""
    path = tmp_path / "dag.yaml"
    save_literal(path, {"meta": {"name": "demo", "closeout_node": "closeout"}, "nodes": [
        node("lib", "alice", status="done", work_item_id="w-lib"),
        node("lib2", "alice", status="done", work_item_id="w-lib2"),
        node("api", "carol", ["lib"], status="blocked", work_item_id="w-api", defect_pending=True),
        node("web", "carol", ["lib"], status="blocked", work_item_id="w-web", defect_pending=True),
        node("closeout", "alice", ["api", "web"])]})
    items = {"w-lib": work_item("w-lib"), "w-api": work_item("w-api", blocker_decision([defect()])),
             "w-web": work_item("w-web", blocker_decision([defect(anchors=["FLOW-1/ACT-1"])]))}
    state = SimpleNamespace(path=str(path), store=Store(items), applied=[], manifest=load_manifest(str(path)))
    state.engine = SimpleNamespace(store=state.store, runtime=None)
    state.config = {}
    return state


def fake_apply(applied, *, crash_after_write=False, crash_before_write=False):
    """The Store-free part of ``apply_amendment``: the real identity and whitelist checks, then the definition write."""
    def apply(path, amendment, store, pool, *, amendment_file, acceptance, runtime):
        review, base, kwargs, _ = _amendment_identity(amendment)
        _verify_amendment_identity(amendment, base, kwargs)
        current = load_manifest(path)
        if current.meta.get("last_amendment_id") != amendment["amendment_id"]:
            assert not system_proposal_errors(current, amendment, review["reporter_items"])
            if crash_before_write:
                raise RuntimeError("killed before the manifest was written")
            amended = _apply_definition(current, amendment)
            for reporter in review["reporter_items"]:
                amended.nodes[reporter].status = "todo"
            amended.meta["last_amendment_id"] = amendment["amendment_id"]
            save_manifest(amended, path)
            applied.append(amendment["amendment_id"])
            if crash_after_write:
                raise RuntimeError("killed after the manifest was written")
    return apply


def advance(world, **kwargs):
    return shell.advance(world.engine, world.manifest, world.path, world.config,
                         apply_system=kwargs.pop("apply_system", None) or fake_apply(world.applied), **kwargs)


def ledger(world):
    return load_manifest(world.path).dag_state["defects"]


def fix_nodes(world):
    return sorted(k for k in load_manifest(world.path).nodes if k.startswith("fix."))


def decision_of(world, item_id):
    return world.store.items[item_id].decision_required


# --- schema, fingerprint, routability ---------------------------------------------------------------------------------

def test_the_fingerprint_ignores_prose_and_anchor_order_but_not_the_suspected_node():
    first = fingerprint("lib", ["B", "A"])
    assert first == fingerprint("lib", ["A", "B", "A"]) and first != fingerprint("other", ["A", "B"]) and first.startswith("dfx-")


@pytest.mark.parametrize("change", [
    {"class": "bug"}, {"anchors": []}, {"expected": ""}, {"repro": {"command": "x", "exit_code": 0}},
    {"repro": {"command": "x"}}, {"extra": 1}, {"locations": "lib.py"}])
def test_a_malformed_defect_is_reported_with_the_field_to_fix(change):
    assert defect_errors([defect(**change)])


def test_a_review_origin_defect_needs_no_repro_but_a_worker_defect_does():
    no_repro = {k: v for k, v in defect().items() if k != "repro"}
    assert defect_errors([no_repro]) and not defect_errors([no_repro], repro_required=False)


def test_routability_demands_a_finished_upstream_node_with_known_anchors_and_a_design_ref_under_its_sources(world):
    manifest, anchors = world.manifest, {"FLOW-1/ACT-1"}
    assert routing_problem(defect(), "api", manifest, anchors) is None
    assert "not done" in routing_problem(defect(suspected_node="web"), "api", manifest, anchors)
    assert "not a node" in routing_problem(defect(suspected_node="nope"), "api", manifest, anchors)
    assert "anchors" in routing_problem(defect(anchors=["FLOW-9"]), "api", manifest, anchors)
    assert "design_ref" in routing_problem(defect(design_ref="docs/other.md#1"), "api", manifest, anchors)
    manifest.nodes["lib"].blocked_by = ["api"]          # a suspect that runs after the reporter can never be "upstream"
    assert "after the reporter" in routing_problem(defect(), "api", manifest, anchors)


# --- the system amendment ---------------------------------------------------------------------------------------------

def test_s3_a_worker_report_becomes_a_fix_node_the_reporter_waits_for(world):
    assert advance(world) is True

    on_disk = load_manifest(world.path)
    fix = fix_nodes(world)
    assert len(fix) == 1 and fix[0] == fix_node_id("lib", [fingerprint("lib", ["FLOW-1/ACT-1"])])
    assert on_disk.nodes[fix[0]].contract.defect_refs and on_disk.nodes[fix[0]].worker == "alice"
    assert on_disk.nodes[fix[0]].blocked_by == [] and "views missing" in on_disk.nodes[fix[0]].description
    assert fix[0] in on_disk.nodes["api"].blocked_by and fix[0] in on_disk.nodes["web"].blocked_by      # one fix, both wait
    assert not on_disk.nodes["api"].defect_pending and on_disk.nodes["api"].status == "todo"
    entry = ledger(world)["entries"][fingerprint("lib", ["FLOW-1/ACT-1"])]
    assert entry["state"] == "fixing" and entry["fix_node"] == fix[0] and entry["reporters"] == ["api", "web"]
    assert ledger(world)["used_global"] == 1 and ledger(world)["used_by_reporter"] == {"api": 1, "web": 1}
    assert "applying" not in ledger(world)


def test_s6_the_reporter_is_never_ready_before_its_fix_is_merged(world):
    advance(world)
    on_disk = load_manifest(world.path)
    snapshot = lambda: {k: {"status": n.status, "blocked_by": n.blocked_by} for k, n in on_disk.nodes.items()}  # noqa: E731
    fix = fix_nodes(world)[0]

    assert graph.ready_nodes(snapshot()) == [fix]
    on_disk.nodes[fix].status = "done"
    assert set(graph.ready_nodes(snapshot())) == {"api", "web"}


def test_a_defect_reported_after_another_fix_on_the_same_node_was_merged_reruns_the_reporter_instead_of_a_new_fix(world):
    advance(world)
    fix = fix_nodes(world)[0]
    world.manifest = load_manifest(world.path)
    world.manifest.nodes[fix].status = "done"
    world.manifest.nodes["api"].status, world.manifest.nodes["api"].defect_pending = "blocked", True
    world.store.items["w-api"].decision_required = blocker_decision([defect(anchors=["FLOW-2/ACT-9"])])
    save_manifest(world.manifest, world.path)

    advance(world)

    assert fix_nodes(world) == [fix] and load_manifest(world.path).nodes["api"].status == "todo"      # the merged fix is its retry
    assert ledger(world)["entries"][fingerprint("lib", ["FLOW-2/ACT-9"])]["retried"] is True


def test_a_second_pass_with_nothing_pending_changes_nothing(world):
    advance(world)
    before = open(world.path).read()

    assert advance(world) is False and open(world.path).read() == before


def test_s4_the_same_defect_after_its_fix_was_merged_stops_as_recurred(world):
    advance(world)
    fix = fix_nodes(world)[0]
    world.manifest = load_manifest(world.path)
    world.manifest.nodes[fix].status = "done"
    world.manifest.nodes["api"].status, world.manifest.nodes["api"].defect_pending = "blocked", True
    world.store.items["w-api"].decision_required = blocker_decision([defect()])
    save_manifest(world.manifest, world.path)

    assert advance(world) is True

    decision = decision_of(world, "w-api")
    assert decision["reason_code"] == "defect-recurred" and decision["next_action"].startswith("omac dag amend propose")
    assert decision["blocker"]["defects"] and ledger(world)["entries"][fingerprint("lib", ["FLOW-1/ACT-1"])]["state"] == "recurred"
    assert not load_manifest(world.path).nodes["api"].defect_pending and fix_nodes(world) == [fix]


def test_s5_a_reporter_over_budget_stops_and_a_grant_lets_the_next_pass_fix_it(world):
    world.config = {"defects": {"per_reporter": 1}}
    advance(world)
    world.manifest = load_manifest(world.path)
    for name in (fix_nodes(world)[0],):
        world.manifest.nodes[name].status = "done"
    api = world.manifest.nodes["api"]
    api.status, api.defect_pending = "blocked", True
    world.store.items["w-api"].decision_required = blocker_decision([defect(suspected_node="lib2", anchors=["FLOW-2/ACT-9"])])
    save_manifest(world.manifest, world.path)

    advance(world)
    stopped = decision_of(world, "w-api")
    assert stopped["reason_code"] == "defect-budget-exhausted"
    assert stopped["next_action"].startswith("omac defect grant") and "--reporter api" in stopped["next_action"]
    assert len(fix_nodes(world)) == 1

    world.manifest = load_manifest(world.path)
    shell.grant_budget(world.manifest, world.path, "api", 1)
    assert load_manifest(world.path).nodes["api"].defect_pending
    advance(world)
    assert len(fix_nodes(world)) == 2 and ledger(world)["used_by_reporter"]["api"] == 2


@pytest.mark.parametrize("report,config,reason", [
    (defect(**{"class": "tradeoff"}), {}, "defect-needs-owner"),
    (defect(**{"class": "contract-gap"}), {}, "defect-needs-owner"),
    (defect(), {"defects": {"auto_fix": "off"}}, "defect-needs-owner"),
    (defect(suspected_node="web"), {}, "defect-unroutable"),
    (defect(design_ref="docs/other.md"), {}, "defect-unroutable"),
])
def test_a_report_that_cannot_be_fixed_by_code_stops_with_a_reason_and_commands(world, report, config, reason):
    world.config = config
    world.store.items["w-api"].decision_required = blocker_decision([report])
    world.manifest.nodes["web"].defect_pending = False
    save_manifest(world.manifest, world.path)

    advance(world)

    decision = decision_of(world, "w-api")
    assert decision["reason_code"] == reason and decision["next_action"] and decision["blocker"]["defects"] == [report]
    assert fix_nodes(world) == [] and not load_manifest(world.path).nodes["api"].defect_pending
    assert load_manifest(world.path).nodes["api"].status == "blocked"


def test_a_fix_node_that_reports_a_defect_is_never_fixed_automatically(world):
    advance(world)
    fix = fix_nodes(world)[0]
    world.manifest = load_manifest(world.path)
    node_ = world.manifest.nodes[fix]
    node_.status, node_.work_item_id, node_.defect_pending = "blocked", "w-fix", True
    world.store.items["w-fix"] = work_item("w-fix", blocker_decision([defect(anchors=["FLOW-3/ACT-1"])]))
    save_manifest(world.manifest, world.path)

    advance(world)

    assert decision_of(world, "w-fix")["reason_code"] == "defect-fix-generation-cap" and fix_nodes(world) == [fix]


def test_s14_while_a_human_amendment_is_pending_the_defect_only_queues(world, tmp_path):
    pending = tmp_path / "human.amendment.yaml"
    pending.write_text(yaml.safe_dump({"amendment_id": "amend-x", "human_confirmation": "pending"}))
    world.manifest.dag_state["pending_human_amendment"] = {"amendment_id": "amend-x", "file": str(pending)}
    save_manifest(world.manifest, world.path)

    advance(world)

    assert fix_nodes(world) == [] and {e["state"] for e in ledger(world)["entries"].values()} == {"queued"}
    assert load_manifest(world.path).nodes["api"].defect_pending            # still waiting, nothing consumed

    pending.write_text(yaml.safe_dump({"amendment_id": "amend-x", "human_confirmation": "applied"}))
    advance(world)
    assert len(fix_nodes(world)) == 1 and ledger(world)["entries"][fingerprint("lib", ["FLOW-1/ACT-1"])]["state"] == "fixing"


@pytest.mark.parametrize("crash", ["before-write", "after-write"])
def test_s15_a_crash_while_applying_leaves_exactly_one_fix_node_and_a_consistent_ledger(world, crash):
    broken = fake_apply(world.applied, crash_before_write=crash == "before-write", crash_after_write=crash == "after-write")
    with pytest.raises(RuntimeError):
        advance(world, apply_system=broken)
    assert ledger(world)["applying"] and ledger(world)["used_global"] == 0          # the intent is durable, nothing is charged yet

    world.manifest = load_manifest(world.path)
    advance(world)                                                                    # a restart finishes the same amendment

    assert len(fix_nodes(world)) == 1 and "applying" not in ledger(world)
    assert ledger(world)["used_global"] == 1 and {e["state"] for e in ledger(world)["entries"].values()} == {"fixing"}
    assert len(set(world.applied)) == 1


def test_s15_a_crash_between_the_intent_and_the_amendment_file_withdraws_the_intent_and_routes_again(world, monkeypatch):
    real = shell._write_yaml_atomic
    monkeypatch.setattr(shell, "_write_yaml_atomic", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        advance(world)
    assert ledger(world)["applying"] and fix_nodes(world) == []
    monkeypatch.setattr(shell, "_write_yaml_atomic", real)

    world.manifest = load_manifest(world.path)
    advance(world)

    assert len(fix_nodes(world)) == 1 and ledger(world)["used_global"] == 1 and "applying" not in ledger(world)


def test_a_system_amendment_that_fails_its_checks_stops_every_carried_report_as_unroutable(world):
    # a pool without the suspected node's worker makes the real builder refuse before anything is applied
    world.store.list_members = lambda workspace: {"carol", "bob"}

    advance(world)

    decision = decision_of(world, "w-api")
    assert decision["reason_code"] == "defect-unroutable" and "agent pool" in decision["detail"] and fix_nodes(world) == []


def test_the_system_whitelist_allows_only_fix_nodes_and_append_only_reporter_edges(world):
    manifest = world.manifest
    add = {"op": "add", "value": {"id": "fix.x", "worker": "alice", "blocked_by": ["lib"],
                                  "contract": {"defect_refs": ["dfx-1"]}}}
    ok = {"operations": [add, {"op": "update", "node": "api", "set": {"blocked_by": ["lib", "fix.x"]}}]}
    assert system_proposal_errors(manifest, ok, ["api"]) == []
    assert system_proposal_errors(manifest, {"operations": [{"op": "resume", "node": "api", "stage": "authoring"}]}, ["api"]) == []
    for bad in (
        {"operations": [{"op": "remove", "node": "api"}]},
        {"operations": [{"op": "resume", "node": "lib", "stage": "authoring"}]},                          # not a reporter
        {"operations": [{"op": "resume", "node": "api", "stage": "review"}]},
        {"operations": [{"op": "update", "node": "lib", "set": {"blocked_by": ["x"]}}]},                  # not a reporter
        {"operations": [{"op": "update", "node": "api", "set": {"blocked_by": []}}]},                     # not append-only
        {"operations": [{"op": "update", "node": "api", "set": {"blocked_by": ["lib"], "worker": "bob"}}]},
        {"operations": [{**add, "value": {**add["value"], "contract": {}}}]},                             # no defect_refs
        {"operations": [{**add, "value": {**add["value"], "blocked_by": ["web"]}}]},                      # waits for unfinished work
        {"operations": [{**add, "value": {**add["value"], "contract": {"defect_refs": ["d"], "registers": ["r"]}}}]},
    ):
        assert system_proposal_errors(manifest, bad, ["api"]), bad


def test_a_system_amendment_file_cannot_be_edited_after_it_was_built(world):
    manifest = load_manifest(world.path)
    proposal = {"schema": "omac.dag-amendment/v1", "reason": "r", "operations": [
        {"op": "add", "value": {"id": "fix.lib.a", "worker": "alice", "reviewer": "bob", "blocked_by": ["lib"],
                                "contract": {**CONTRACT, "source_of_truth": ["docs/design.md"], "defect_refs": ["dfx-a"]}}},
        {"op": "update", "node": "api", "set": {"blocked_by": ["lib", "fix.lib.a"]}}]}
    amendment = build_system_amendment(manifest, proposal, world.store, defect_fps=["dfx-a"], reporters=["api"])

    review, base, kwargs, _ = _amendment_identity(amendment)
    _verify_amendment_identity(amendment, base, kwargs)
    tampered = copy.deepcopy(amendment)
    tampered["operations"][0]["value"]["worker"] = "bob"
    with pytest.raises(ValidationError):
        _verify_amendment_identity(tampered, base, kwargs)
    stale_policy = {**amendment, "review": {**amendment["review"], "policy_sha256": "0" * 64}}
    with pytest.raises(ValidationError):
        _amendment_identity(stale_policy)
    assert amendment["human_confirmation"] == "not-required" and review["kind"] == "system"


def test_a_worker_decision_is_consumable_by_a_system_amendment_only_when_it_is_an_upstream_defect(world):
    manifest = load_manifest(world.path)
    proposal = {"operations": [{"op": "update", "node": "api", "set": {"blocked_by": ["lib", "x"]}}]}
    with pytest.raises(ValidationError):
        validate_affected(manifest, proposal, world.store)                                                  # a person must resolve it
    validate_affected(manifest, proposal, world.store, consume_defects=True)
    world.store.items["w-api"].decision_required = {**blocker_decision([defect()]), "blocker": {"reason_code": "quality-gate-failed"}}
    with pytest.raises(ValidationError):
        validate_affected(manifest, proposal, world.store, consume_defects=True)


def recover_to_authoring(node, store, stage, *, expected_review_subject, expected_review_generation, expected_bounce_baseline,
                         sync_contract):
    """What ``prepare_stage_recovery`` leaves on the platform for the authoring stage (its writes are the Store's concern)."""
    from omac.core.manifest import _dump_contract
    item = store.get_work_item(node.work_item_id)
    item.status, item.phase, item.decision_required, item.worker_handoff = WorkItemStatus.TODO, TaskPhase.AUTHORING, None, None
    item.review_generation, item.bounce_baseline = expected_review_generation, expected_bounce_baseline
    item.contract = _dump_contract(node.contract)


def test_s3_the_real_amendment_ledger_applies_the_system_amendment_and_recovers_the_reporter(world):
    from functools import partial

    from omac.core.amendment import apply_amendment, amendment_apply_blocker

    world.manifest.nodes["web"].defect_pending = False
    save_manifest(world.manifest, world.path)
    world.manifest = load_manifest(world.path)

    advance(world, apply_system=partial(apply_amendment, recover=recover_to_authoring))

    on_disk = load_manifest(world.path)
    fix = fix_nodes(world)[0]
    assert fix in on_disk.nodes["api"].blocked_by and on_disk.nodes["api"].status == "todo"
    assert on_disk.meta["amendment_apply"]["system"] is True and amendment_apply_blocker(on_disk, world.path) is None
    assert world.store.items["w-api"].status == WorkItemStatus.TODO and not world.store.items["w-api"].decision_required
    assert ledger(world)["entries"][fingerprint("lib", ["FLOW-1/ACT-1"])]["state"] == "fixing"
    assert not on_disk.nodes["api"].defect_pending


def test_a_half_applied_system_amendment_reports_dag_run_as_its_resume_command(world):
    from omac.core.amendment import amendment_apply_blocker

    manifest = load_manifest(world.path)
    manifest.meta.update(last_amendment_id="amend-x", amendment_apply={
        "schema": "omac.amendment-apply/v1", "amendment_id": "amend-x", "amendment_file": "a.yaml", "system": True,
        "nodes": {"api": {"stage": "authoring", "state": "pending"}}})

    assert amendment_apply_blocker(manifest, "m.yaml")["resume_command"] == "omac dag run m.yaml"
    assert amendment_apply_blocker(manifest, "m.yaml")["system"] is True


# --- review-origin defects (S7) ---------------------------------------------------------------------------------------

def blocker(owner, obligation="acceptance-action:FLOW-1:ACT-1", **extra):
    return {"root_cause_key": f"rc-{obligation}", "obligation_id": obligation, "classification": "new", "summary": "views missing",
            "evidence": "lib/items.py:list_items", "required_fix": "return views", **({"owner": owner} if owner else {}), **extra}


def test_s7_blockers_all_owned_by_finished_upstream_nodes_become_one_defect_per_owner(world):
    defects = review_origin_defects([blocker("lib"), blocker("lib", "interface:items:views")], "api", world.manifest)

    assert len(defects) == 1 and defects[0]["suspected_node"] == "lib" and not defect_errors(defects, repro_required=False)
    assert defects[0]["anchors"] == ["acceptance-action:FLOW-1:ACT-1", "interface:items:views"]
    assert defects[0]["design_ref"] == "docs/design.md"


@pytest.mark.parametrize("blockers", [
    [blocker(None)],                                  # no owner: ordinary rework
    [blocker("lib"), blocker(None)],                  # one blocker is the reporter's own
    [blocker("web")],                                 # not a finished upstream node
    [blocker("api")],                                 # the reviewed node itself
])
def test_s7_any_other_blocker_set_stays_ordinary_rework(world, blockers):
    assert review_origin_defects(blockers, "api", world.manifest) is None


def test_s7_a_rejecting_review_of_upstream_work_blocks_the_node_without_spending_a_review_bounce(world):
    from omac.pipeline import loop

    item = work_item("w-api")
    item.phase, item.review_report = TaskPhase.REVIEW, {"blockers": [blocker("lib")]}
    node_ = world.manifest.nodes["api"]
    node_.status, node_.defect_pending = "in_review", False
    collect = loop._Collect(store=world.store, runtime=None, code_host=None, manifest=world.manifest, manifest_path=world.path,
                            limits={}, config={}, no_submit_runs=2, node_nits="accept")
    world.store.items["w-api"] = item

    assert loop._route_review_to_defect(collect, loop._Node("api", node_, None, item)) is True

    decision = item.decision_required
    assert decision["reason_code"] == "upstream-defect" and decision["gate"] == "review"
    assert decision["blocker"]["defects"][0]["suspected_node"] == "lib"
    assert world.manifest.nodes["api"].status == "blocked" and world.manifest.nodes["api"].defect_pending
    assert "worker_bounce" not in {name for _, fields in world.store.writes for name in fields}
    assert "api" in collect.failures

    other = loop._Collect(store=world.store, runtime=None, code_host=None, manifest=world.manifest, manifest_path=world.path,
                          limits={}, config={"defects": {"auto_fix": "off"}}, no_submit_runs=2, node_nits="accept")
    assert loop._route_review_to_defect(other, loop._Node("api", node_, None, item)) is False


def test_a_review_origin_defect_is_routed_like_a_worker_one(world):
    defects = review_origin_defects([blocker("lib")], "api", world.manifest)
    world.store.items["w-api"].decision_required = blocker_decision(defects, gate="review", reason="upstream-defect")
    world.store.items["w-web"].decision_required = None
    world.manifest.nodes["web"].defect_pending = False
    save_manifest(world.manifest, world.path)
    world.manifest = load_manifest(world.path)

    advance(world)

    assert len(fix_nodes(world)) == 1 and "acceptance-action:FLOW-1:ACT-1" in load_manifest(world.path).nodes[fix_nodes(world)[0]].description


# --- people: report / list / final acceptance (S19) --------------------------------------------------------------------

def test_a_person_can_queue_a_defect_for_a_node_that_has_not_started_and_the_next_pass_routes_it(world):
    manifest = load_manifest(world.path)
    manifest.nodes["web"].status, manifest.nodes["web"].defect_pending, manifest.nodes["web"].work_item_id = "todo", False, None
    manifest.nodes["api"].defect_pending = False
    save_manifest(manifest, world.path)
    world.manifest = load_manifest(world.path)
    report = {k: v for k, v in defect().items() if k != "repro"}

    queued = shell.report_defects(world.manifest, world.path, "web", [report])
    assert queued["queued"] == [fingerprint("lib", ["FLOW-1/ACT-1"])] and ledger(world)["inbox"]

    assert advance(world) is True
    assert fix_nodes(world) and fix_nodes(world)[0] in load_manifest(world.path).nodes["web"].blocked_by
    assert ledger(world)["inbox"] == []


@pytest.mark.parametrize("before,change", [("lib", {}), ("nope", {}), ("web", {"suspected_node": "web"}), ("web", {"class": "x"})])
def test_a_defect_report_that_cannot_be_routed_is_refused_up_front(world, before, change):
    with pytest.raises(ValidationError):
        shell.report_defects(world.manifest, world.path, before, [defect(**change)])


def test_s19_a_failed_flow_is_merged_through_a_system_amendment_and_stops_when_it_fails_again(world):
    increment = save_literal(world.path.replace("dag.yaml", "inc.yaml"), {"meta": {"name": "inc"}, "nodes": [
        node("fix-login", "alice", ["lib"])]})
    manifest = load_manifest(world.path)
    manifest.nodes["api"].status = manifest.nodes["web"].status = "done"
    manifest.nodes["api"].defect_pending = manifest.nodes["web"].defect_pending = False
    save_manifest(manifest, world.path)
    world.manifest = load_manifest(world.path)

    assert shell.recurred_flows(world.engine, world.manifest, world.path, ["login"]) == []
    shell.merge_acceptance_increment(world.engine, world.manifest, world.path, load_manifest(increment), ["login"], {},
                                     apply_system=fake_apply(world.applied))

    added = load_manifest(world.path).nodes["fix-login"]
    assert added.contract.defect_refs == [fingerprint("", ["login"])] and added.blocked_by == ["lib"]
    assert ledger(world)["entries"][fingerprint("", ["login"])]["state"] == "fixing" and ledger(world)["used_global"] == 0
    assert shell.recurred_flows(world.engine, world.manifest, world.path, ["login", "export"]) == ["login"]
    assert ledger(world)["entries"][fingerprint("", ["login"])]["state"] == "recurred"


def test_an_increment_that_waits_for_unfinished_work_is_refused_with_the_whitelist_reason(world):
    increment = save_literal(world.path.replace("dag.yaml", "inc.yaml"), {"meta": {"name": "inc"}, "nodes": [
        node("fix-login", "alice", ["web"])]})

    with pytest.raises(NeedsDecision) as raised:
        shell.merge_acceptance_increment(world.engine, world.manifest, world.path, load_manifest(increment), ["login"], {},
                                         apply_system=fake_apply(world.applied))

    assert "neither finished nor added" in raised.value.report["errors"] and "fix-login" not in load_manifest(world.path).nodes


def test_defect_list_and_grant_are_plain_commands_over_the_manifest(world, capsys):
    defect_cli.run(Namespace(action="list", manifest=world.path))
    listed = json.loads(capsys.readouterr().out)
    assert listed["pending_reporters"] == ["api", "web"] and listed["entries"] == {}

    defect_cli.run(Namespace(action="grant", manifest=world.path, reporter="api", count=2))
    assert json.loads(capsys.readouterr().out)["granted"] == 2
    assert ledger(world)["granted_by_reporter"] == {"api": 2}
    with pytest.raises(ValidationError):
        defect_cli.run(Namespace(action="grant", manifest=world.path, reporter="nope", count=1))


def test_anchor_ids_cover_flows_actions_and_provides(world):
    acceptance = SimpleNamespace(flows=[SimpleNamespace(id="F", actions=[SimpleNamespace(id="A")])], requirements=[])
    assert anchor_ids(world.manifest, acceptance) == {"F", "F/A"}


@pytest.mark.parametrize("nodes,expected", [(10, 5), (40, 6), (181, 28)])
def test_the_automatic_global_fix_budget_scales_with_the_node_count(nodes, expected):
    from omac.core.config import resolve_defects

    assert resolve_defects({}, nodes) == {"auto_fix": "design-gap", "per_reporter": 2, "max_auto_fixes": expected}


@pytest.mark.parametrize("raw", [{"auto_fix": "maybe"}, {"per_reporter": -1}, {"max_auto_fixes": "many"}, {"per_reporter": True}])
def test_a_bad_defects_setting_is_refused_instead_of_defaulted(raw):
    from omac.core.config import resolve_defects

    with pytest.raises(ValidationError):
        resolve_defects({"defects": raw}, 10)

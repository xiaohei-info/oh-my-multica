"""The interface chain: provides/requires, registries, and the review obligations that prove them (3.0)."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from _support.ledgers import review_report
from omac.core.interfaces import has_evidence_locator, interface_errors
from omac.core.lint import lint, lint_increment
from omac.core.manifest import Manifest, loads_manifest, load_manifest, save_manifest
from omac.core.review_convergence import build_review_obligations, validate_convergence_review
from omac.core.taskmeta import TaskKind

POOL = {"alice", "bob"}

# What 2.4 wrote for a manifest without any 3.0 field: loading and saving it must not change one byte.
OLD_MANIFEST = """meta:
  name: demo
  closeout_node: web
nodes:
- id: api
  worker: alice
  blocked_by: []
  reviewer: bob
  contract:
    objective: serve packs
    non_goals:
    - auth
    verification_commands:
    - pytest -q
    integration_gates:
    - name: g
      layer: L1
      delivery_goal: d
      source_of_truth:
      - docs/design.md
      covers:
      - x
      acceptance_refs:
      - FLOW-1
      commands:
      - pytest
    pr_base: main
    acceptance_claims:
    - FLOW-1
    source_of_truth:
    - docs/design.md#5
    evidence_mode: artifact
    produces:
    - artifact_id: packs
    consumes: []
- id: web
  worker: bob
  blocked_by:
  - api
  reviewer: alice
  contract:
    objective: show packs
    non_goals:
    - auth
    verification_commands:
    - pytest -q
    integration_gates: []
    pr_base: main
    acceptance_refs:
    - FLOW-1
    evidence_mode: artifact
    consumes:
    - artifact_id: packs
      producer: api
      evidence_mode: artifact
"""


def _node(node_id, *, blocked_by=(), **contract):
    return {
        "id": node_id, "worker": "alice", "reviewer": "bob", "blocked_by": list(blocked_by),
        "contract": {
            "objective": f"deliver {node_id}", "source_of_truth": ["docs/design.md#1"], "acceptance_refs": ["FLOW-1"],
            "non_goals": ["auth"], "verification_commands": ["pytest -q"], "pr_base": "main",
            "integration_gates": [{
                "name": "gate", "layer": "L1", "delivery_goal": "delivers", "source_of_truth": ["docs/design.md"],
                "covers": ["x"], "acceptance_refs": ["FLOW-1"], "commands": ["pytest"]}],
            "evidence_mode": "artifact", **contract}}


def _dag(nodes, **meta) -> Manifest:
    return loads_manifest(yaml.safe_dump({"meta": {"name": "demo", **meta}, "nodes": nodes}, sort_keys=False))


def _pair(requires, provides=("http:GET /v1/packs#views",)):
    return _dag([
        _node("api", produces=[{"artifact_id": "packs", "provides": [{"id": i, "ref": "docs/design.md#5"} for i in provides]}]),
        _node("web", blocked_by=["api"], consumes=[
            {"artifact_id": "packs", "producer": "api", "evidence_mode": "artifact", "requires": list(requires)}])])


def test_a_manifest_without_3_0_fields_round_trips_byte_for_byte(tmp_path):
    path = tmp_path / "dag.yaml"
    path.write_text(OLD_MANIFEST)
    manifest = load_manifest(str(path))
    save_manifest(manifest, str(path))
    assert path.read_text() == OLD_MANIFEST
    save_manifest(loads_manifest(OLD_MANIFEST), str(tmp_path / "again.yaml"))
    assert (tmp_path / "again.yaml").read_text() == OLD_MANIFEST
    assert interface_errors(manifest) == []


def test_provides_requires_registers_and_refs_survive_a_save_and_reload(tmp_path):
    manifest = _dag([
        _node("api", registers=["serve"], requirement_refs=["REQ-1"], produces=[
            {"artifact_id": "packs", "provides": [{"id": "a", "ref": "docs/design.md#5"}, {"id": "b"}]}]),
        _node("fix", blocked_by=["api"], defect_refs=["dfx-1a2b3c"], consumes=[
            {"artifact_id": "packs", "producer": "api", "evidence_mode": "artifact", "requires": ["a", "b"]}])],
        registries=[{"id": "serve", "ref": "docs/design.md#2", "guard": "fix"}])
    path = str(tmp_path / "dag.yaml")
    save_manifest(manifest, path)
    first = (tmp_path / "dag.yaml").read_bytes()

    loaded = load_manifest(path)
    save_manifest(loaded, str(tmp_path / "second.yaml"))
    assert (tmp_path / "second.yaml").read_bytes() == first
    api, fix = loaded.nodes["api"].contract, loaded.nodes["fix"].contract
    assert [(p.id, p.ref) for p in api.produces[0].provides] == [("a", "docs/design.md#5"), ("b", None)]
    assert fix.consumes[0].requires == ("a", "b")
    assert (api.registers, api.requirement_refs, fix.defect_refs) == (["serve"], ["REQ-1"], ["dfx-1a2b3c"])
    assert loaded.meta["registries"][0]["guard"] == "fix"
    assert interface_errors(loaded) == []


def test_s1_a_required_item_the_producer_does_not_provide_fails_lint_with_a_snippet_to_paste():
    errors = [e for e in lint(_pair(["http:GET /v1/packs#views", "http:GET /v1/packs#routes"]), POOL) if "requires" in e]
    assert len(errors) == 1
    message = errors[0]
    assert "node web: contract.consumes[0] requires 'http:GET /v1/packs#routes'" in message
    snippet = message.split(":\n", 1)[1]
    pasted = yaml.safe_load(snippet)[0]
    assert pasted["id"] == "api" and pasted["contract"]["produces"][0]["artifact_id"] == "packs"
    assert pasted["contract"]["produces"][0]["provides"][0]["id"] == "http:GET /v1/packs#routes"
    assert lint(_pair(["http:GET /v1/packs#views"]), POOL) == []


def test_requires_on_an_artifact_the_producer_does_not_produce_is_left_to_the_boundary_checks():
    manifest = _pair(["x"])
    consumed = manifest.nodes["web"].contract.consumes
    consumed[0] = replace(consumed[0], artifact_id="other")
    assert interface_errors(manifest) == []
    assert any("is not produced by" in e for e in lint(manifest, POOL))


@pytest.mark.parametrize("contract,expected", [
    ({"produces": [{"artifact_id": "p", "provides": "oops"}]}, "provides must be a list"),
    ({"produces": [{"artifact_id": "p", "provides": [{"ref": "r"}]}]}, "non-empty id"),
    ({"produces": [{"artifact_id": "p", "provides": [{"id": "a"}, {"id": "a"}]}]}, "duplicate id"),
    ({"registers": "serve"}, "registers must be a list"),
    ({"requirement_refs": ["R", "R"]}, "requirement_refs must not contain duplicates"),
    ({"defect_refs": [""]}, "defect_refs[0] must be a non-empty string"),
])
def test_malformed_interface_fields_are_reported_not_ignored(contract, expected):
    assert any(expected in e for e in interface_errors(_dag([_node("n", **contract)])))


REGISTRIES = [{"id": "serve", "ref": "docs/design.md#2", "guard": "guard"}]


def _registry_dag(*, guard_after=True, guard_status="todo", extra_registrant=False):
    nodes = [_node("a", registers=["serve"]), _node("b", registers=["serve"] if extra_registrant else [])]
    nodes.append(_node("guard", blocked_by=["a", "b"] if guard_after else ["a"]))
    manifest = _dag(nodes, registries=REGISTRIES)
    manifest.nodes["guard"].status = guard_status
    return manifest


def test_s2_a_guard_that_does_not_run_after_every_registrant_fails_lint_with_the_blocked_by_to_add():
    assert interface_errors(_registry_dag(extra_registrant=True)) == []
    (error,) = interface_errors(_registry_dag(guard_after=False, extra_registrant=True))
    assert "guard guard must run after every node that registers" in error and "b is not upstream" in error
    assert "blocked_by: [a, b]" in error


def test_s2_a_registrant_added_after_the_guard_is_done_asks_for_a_new_guard_node():
    manifest = _registry_dag(guard_after=False, guard_status="done", extra_registrant=True)
    (error,) = interface_errors(manifest)
    assert "already done" in error and "Add a new guard node" in error and "guard-after-b" in error
    assert "blocked_by: [a, b]" in error


def test_registry_declarations_are_checked_where_they_are_used():
    (unknown,) = interface_errors(_dag([_node("a", registers=["serve"])]))
    assert "unknown registry 'serve'" in unknown and "registries:" in unknown
    assert any("is not a node" in e for e in interface_errors(_dag([_node("a")], registries=[{"id": "r", "guard": "nope"}])))
    assert any("must not register into the registry it guards" in e for e in interface_errors(
        _dag([_node("g", registers=["r"])], registries=[{"id": "r", "guard": "g"}])))
    assert any("meta.registries must be a list" in e for e in interface_errors(_dag([_node("a")], registries="x")))


def test_an_amendment_increment_is_checked_against_the_guard_of_the_nodes_it_adds():
    existing = _registry_dag(extra_registrant=True)
    increment = _dag([_node("c", registers=["serve"])], registries=REGISTRIES)
    errors = lint_increment(increment, existing, POOL)
    assert any("c is not upstream" in e for e in errors)


# ---- review obligations and evidence locators (S11) -----------------------------------------------------------------

def _item(**contract):
    return SimpleNamespace(
        kind=TaskKind.DEVELOP, deliverable=None, review_ledger=None, review_obligations=None,
        contract=_dag([_node("n", **contract)]).nodes["n"].contract)


def test_s11_acceptance_refs_add_one_aggregate_trace_obligation_a_report_cannot_skip():
    item = _item(acceptance_refs=["FLOW-2", "FLOW-1"])
    obligations = build_review_obligations(item)
    (trace,) = [o for o in obligations if o["obligation_id"] == "trace:refs"]
    assert trace["subject"] == ["FLOW-1", "FLOW-2"]

    complete = review_report(obligations)
    assert validate_convergence_review(item, "pass", complete) == []
    skipped = review_report([o for o in obligations if o["obligation_id"] != "trace:refs"])
    assert "review_report missing obligation result: trace:refs" in validate_convergence_review(item, "pass", skipped)
    assert not any(o["obligation_id"] == "trace:refs" for o in build_review_obligations(_item(acceptance_refs=[])))


def test_interface_and_registry_obligations_follow_the_nodes_own_contract():
    item = _item(registers=["serve"], produces=[
        {"artifact_id": "packs", "provides": [{"id": "http:GET /v1/packs#views"}, {"id": "http:GET /v1/packs"}]}])
    ids = [o["obligation_id"] for o in build_review_obligations(item)]
    assert "registry:serve" in ids
    assert {"interface:packs:http:GET /v1/packs#views", "interface:packs:http:GET /v1/packs"} <= set(ids)


@pytest.mark.parametrize("evidence,accepted", [
    ("src/api/packs.py:build_view returns the views field", True),
    ("covered by test_pack_views", True),
    ("TestPackViews in api/packs_test.go", True),
    ("checked", False),
    ("looks right to me, version 1.2", False),
])
def test_evidence_locator_format(evidence, accepted):
    assert has_evidence_locator(evidence) is accepted


def test_a_passing_interface_obligation_without_a_locator_is_rejected_but_a_failing_one_is_not():
    item = _item(registers=["serve"])
    obligations = build_review_obligations(item)
    report = review_report(obligations)
    (error,) = validate_convergence_review(item, "pass", report)          # "checked registry:serve" names no file or test
    assert "must cite a repository path" in error and "registry:serve" in error
    for result in report["obligation_results"]:
        if result["obligation_id"] == "registry:serve":
            result["evidence"] = "src/server/routes.py registers the pack routes"
    assert validate_convergence_review(item, "pass", report) == []

    failing = review_report(obligations, failed=("registry:serve",), blockers=[{
        "root_cause_key": "serve-missing", "obligation_id": "registry:serve", "classification": "new", "owner": "n",
        "summary": "no entry", "evidence": "nothing added", "required_fix": "register the routes"}])
    assert not any("must cite a repository path" in e for e in validate_convergence_review(item, "reject", failing))

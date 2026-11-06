"""Plan profile (light/standard), acceptance v3 requirements and the plan-stage review obligations (3.0 WP2)."""
import pytest
import yaml

from omac.core.acceptance import load_acceptance_doc
from omac.core.config import resolve_plan
from omac.core.lint import lint
from omac.core.manifest import loads_manifest
from omac.core.requirements import requirement_errors
from omac.core.review_convergence import build_review_obligations
from omac.core.review_preflight import SCALE_LIGHT, SCALE_STANDARD, plan_manifest_preflight, plan_scale
from omac.core.taskmeta import TaskKind
from omac.errors import ValidationError
from omac.pipeline.plan import _acceptance_guard, _compose_guard
from types import SimpleNamespace

POOL = {"alice", "bob"}


def _node(node_id, blocked_by=(), *, consumes=(), produces=(), scope=None, **contract):
    return {
        "id": node_id, "worker": "alice", "blocked_by": list(blocked_by), "description": f"Concrete outcome of {node_id}.",
        "contract": {
            "objective": f"do {node_id}", "source_of_truth": ["docs/design.md"], "non_goals": ["nothing else"],
            "verification_commands": ["pytest -q"], "pr_base": "main", "scope_paths": [scope or f"src/{node_id}/**"],
            "evidence_mode": "fixture", "produces": list(produces), "consumes": list(consumes),
            "acceptance_claims": ["FLOW-1"], "integration_gates": [{
                "name": f"gate-{node_id}", "layer": "L1", "delivery_goal": "works", "source_of_truth": ["docs/design.md"],
                "covers": [node_id], "acceptance_refs": ["FLOW-1"], "commands": ["pytest -q"]}], **contract},
    }


def _text(nodes, **meta):
    return yaml.safe_dump({"meta": {"name": "demo", **meta}, "nodes": nodes}, sort_keys=False)


def _manifest(nodes, **meta):
    return loads_manifest(_text(nodes, **meta))


def _chain(count, *, consumes_requires=None, closeout=False, ref=None, text=False):
    """``count`` nodes in a line; each consumes the previous one's artifact (with ``requires`` when given)."""
    nodes = []
    for index in range(count):
        previous = f"n{index - 1}"
        consumes = [] if index == 0 else [{"artifact_id": f"{previous}-out", "producer": previous, "evidence_mode": "fixture",
                                           **({"requires": consumes_requires} if consumes_requires else {})}]
        provided = [{"id": item_id, **({"ref": ref} if ref else {})} for item_id in (consumes_requires or [])]
        nodes.append(_node(f"n{index}", [previous] if index else [], consumes=consumes, produces=[
            {"artifact_id": f"n{index}-out", **({"provides": provided} if provided else {})}]))
    meta = {"closeout_node": f"n{count - 1}"} if closeout else {}
    return _text(nodes, **meta) if text else _manifest(nodes, **meta)


# ── S12: a small plan without any 3.0 field stays light and passes; the same plan forced standard is told what to add ──

def test_s12_a_five_node_plan_without_3_0_fields_passes_the_strict_gate_in_light_and_is_asked_for_more_in_standard():
    manifest = _chain(5)

    assert plan_scale(manifest) == SCALE_LIGHT
    assert plan_manifest_preflight(manifest) == []

    errors = plan_manifest_preflight(manifest, scale=SCALE_STANDARD)

    assert any("n1: contract.consumes[0]" in error and "requires" in error for error in errors)
    assert any("closeout_node" in error for error in errors)


def test_the_scale_is_decided_by_code_from_the_node_count_and_the_business_action_count_and_only_a_person_overrides_it():
    actions = [{"id": f"ACT-{i}", "kind": "business-action", "step": f"s{i}", "how": f"h{i}", "expected": f"e{i}"} for i in range(21)]
    big_doc = load_acceptance_doc({"schema": "omac.acceptance/v2", "flows": [{"id": "FLOW-1", "name": "f", "actions": actions}]})
    small_doc = load_acceptance_doc({"schema": "omac.acceptance/v2", "flows": [{"id": "FLOW-1", "name": "f", "actions": actions[:20]}]})

    assert plan_scale(_chain(12), small_doc) == SCALE_LIGHT
    assert plan_scale(_chain(13), small_doc) == SCALE_STANDARD
    assert plan_scale(_chain(3), big_doc) == SCALE_STANDARD
    assert plan_scale(_chain(3), None) == SCALE_LIGHT                                   # --no-acceptance: only the node count counts
    assert plan_scale(_chain(13), small_doc, profile="light") == SCALE_LIGHT
    assert plan_scale(_chain(3), small_doc, profile="standard") == SCALE_STANDARD
    assert plan_scale(_chain(5), small_doc, light_max_nodes=4) == SCALE_STANDARD


def test_a_standard_plan_that_names_its_interfaces_anchors_them_and_ends_in_one_sink_passes():
    unanchored = _chain(4, consumes_requires=["http:GET /v1/items"], closeout=True)

    errors = plan_manifest_preflight(unanchored, scale=SCALE_STANDARD)

    assert len(errors) == 3 and all("needs a design `ref`" in error for error in errors)   # one per provider, nothing else

    anchored = _chain(4, consumes_requires=["http:GET /v1/items"], closeout=True, ref="docs/design.md#items")
    assert plan_manifest_preflight(anchored, scale=SCALE_STANDARD) == []


def test_a_standard_closeout_must_depend_on_every_other_node():
    manifest = _chain(3, consumes_requires=["x"], closeout=True)
    manifest.nodes["n2"].blocked_by = []                      # the closeout no longer closes the graph

    errors = plan_manifest_preflight(manifest, scale=SCALE_STANDARD)

    assert any("closeout node n2 must be the sink" in error and "n0, n1" in error for error in errors)


# ── S18: requirements are covered by actions, or claimed by a node when technical ──────────────────────────────

def _requirement(requirement_id, **over):
    return {"id": requirement_id, "source": "docs/design.md#4", "summary": "must do it", "disposition": "in-scope",
            "verification": "action", **over}


def _doc(requirements, action_requirements=("REQ-1",), **over):
    action = {"id": "ACT-1", "kind": "business-action", "step": "s", "how": "h", "expected": "e",
              "requirement_ids": list(action_requirements)}
    return load_acceptance_doc({"schema": "omac.acceptance/v3", "requirements": requirements,
                                "flows": [{"id": "FLOW-1", "name": "f", "actions": [action]}], **over})


def test_s18_a_requirement_no_action_references_and_that_is_not_technical_fails_the_acceptance_guard():
    doc_text = yaml.safe_dump({
        "schema": "omac.acceptance/v3", "requirements": [_requirement("REQ-1"), _requirement("REQ-2")],
        "flows": [{"id": "FLOW-1", "name": "f", "actions": [{
            "id": "ACT-1", "kind": "business-action", "step": "s", "how": "h", "expected": "e", "requirement_ids": ["REQ-1"]}]}]})

    errors = _acceptance_guard(SimpleNamespace(deliverable=doc_text))

    assert len(errors) == 1 and "REQ-2" in errors[0] and "requirement_ids: [REQ-2]" in errors[0]


def test_s18_a_technical_requirement_no_node_claims_fails_decompose_and_a_claim_passes():
    doc = _doc([_requirement("REQ-1"), _requirement("REQ-T", verification="technical")])
    manifest = _chain(2)

    errors = plan_manifest_preflight(manifest, acceptance_doc=doc)
    assert any("technical requirement REQ-T is claimed by no node" in error and "requirement_refs: [REQ-T]" in error
               for error in errors)

    manifest.nodes["n1"].contract.requirement_refs = ["REQ-T"]
    assert not [error for error in plan_manifest_preflight(manifest, acceptance_doc=doc) if "REQ-T" in error]


def test_a_requirement_ref_that_is_not_a_technical_requirement_is_a_lint_error_for_any_manifest_that_has_the_document():
    doc = _doc([_requirement("REQ-1"), _requirement("REQ-T", verification="technical")])
    manifest = _chain(2)
    manifest.nodes["n1"].contract.requirement_refs = ["REQ-1", "REQ-missing"]

    errors = lint(manifest, POOL, acceptance=doc)

    assert any("'REQ-1' is not an in-scope technical requirement" in error for error in errors)
    assert any("'REQ-missing' is not an in-scope technical requirement" in error for error in errors)


@pytest.mark.parametrize("mutate,message", [
    (lambda r: r.update(disposition="deferred"), "reason is required when disposition is deferred"),
    (lambda r: r.update(summary="x" * 201), "at most 200 characters"),
    (lambda r: r.update(verification="manual"), "verification must be one of"),
    (lambda r: r.pop("source"), "source is required"),
    (lambda r: r.update(id="bad id"), "id must match"),
])
def test_a_malformed_requirement_is_a_schema_error(mutate, message):
    entry = _requirement("REQ-1")
    mutate(entry)

    with pytest.raises(ValueError, match=message):
        _doc([entry], action_requirements=())


def test_requirement_ids_must_be_unique_known_in_scope_and_action_verified():
    with pytest.raises(ValueError, match="duplicate requirement id"):
        _doc([_requirement("REQ-1"), _requirement("REQ-1")])
    doc = _doc([_requirement("REQ-1"), _requirement("REQ-D", disposition="out-of-scope", reason="not now"),
                _requirement("REQ-T", verification="technical")], action_requirements=("REQ-1", "REQ-D", "REQ-T", "REQ-x"))

    errors = requirement_errors(doc)

    assert len(errors) == 3 and any("out-of-scope" in e for e in errors) and any("technical" in e for e in errors)


def test_requirements_need_the_v3_schema_and_old_documents_stay_readable():
    v2 = {"schema": "omac.acceptance/v2", "requirements": [], "flows": [{"id": "F", "name": "f", "actions": [{
        "id": "A", "kind": "business-action", "step": "s", "how": "h", "expected": "e"}]}]}
    with pytest.raises(ValueError, match="need schema omac.acceptance/v3"):
        load_acceptance_doc(v2)
    del v2["requirements"]
    assert load_acceptance_doc(v2).requirements == []


def test_the_acceptance_guard_demands_requirements_only_where_the_acceptance_alone_proves_the_plan_is_standard():
    def doc_with(count, schema="omac.acceptance/v2"):
        actions = [{"id": f"ACT-{i}", "kind": "business-action", "step": f"s{i}", "how": f"h{i}", "expected": f"e{i}"}
                   for i in range(count)]
        return SimpleNamespace(deliverable=yaml.safe_dump({"schema": schema, "flows": [{"id": "F", "name": "f", "actions": actions}]}))

    assert _acceptance_guard(doc_with(20)) == []                                         # may still decompose into a light plan
    [error] = _acceptance_guard(doc_with(21))
    assert "omac.acceptance/v3" in error and "requirements" in error
    assert _acceptance_guard(doc_with(21), profile="light") == []                        # a person forced light
    assert _acceptance_guard(doc_with(3), profile="standard") != []                      # a person forced standard


# ── review obligations of the plan stages ──────────────────────────────────────────────────────────────────────

def test_a_decompose_review_gets_one_interface_matrix_obligation_when_any_node_requires_something():
    item = SimpleNamespace(kind=TaskKind.DECOMPOSE, deliverable=_chain(3, consumes_requires=["a", "b"], text=True), contract=None)

    [matrix] = [o for o in build_review_obligations(item) if o["obligation_id"] == "interface:matrix"]

    assert [(row["consumer"], row["producer"], row["requires"], row["provides"]) for row in matrix["interface_matrix"]] == [
        ("n1", "n0", ["a", "b"], ["a", "b"]), ("n2", "n1", ["a", "b"], ["a", "b"])]
    plain = SimpleNamespace(kind=TaskKind.DECOMPOSE, deliverable=_chain(3, text=True), contract=None)
    assert "interface:matrix" not in {o["obligation_id"] for o in build_review_obligations(plain)}


def test_an_acceptance_review_gets_the_requirements_extraction_obligation_only_when_the_document_has_requirements():
    text = yaml.safe_dump({"schema": "omac.acceptance/v3", "requirements": [_requirement("REQ-1")], "flows": [{
        "id": "F", "name": "f", "actions": [{"id": "A", "kind": "business-action", "step": "s", "how": "h",
                                              "expected": "e", "requirement_ids": ["REQ-1"]}]}]})
    item = SimpleNamespace(kind=TaskKind.ACCEPTANCE, deliverable=text, contract=None)

    [extraction] = [o for o in build_review_obligations(item) if o["obligation_id"] == "requirements:extraction"]

    assert extraction["requirements"] == [{"id": "REQ-1", "source": "docs/design.md#4", "disposition": "in-scope",
                                           "verification": "action"}]
    v2 = yaml.safe_dump({"schema": "omac.acceptance/v2", "flows": [{"id": "F", "name": "f", "actions": [{
        "id": "A", "kind": "business-action", "step": "s", "how": "h", "expected": "e"}]}]})
    other = SimpleNamespace(kind=TaskKind.ACCEPTANCE, deliverable=v2, contract=None)
    assert "requirements:extraction" not in {o["obligation_id"] for o in build_review_obligations(other)}


def test_the_compose_guard_uses_the_profile_it_is_given_and_reports_every_standard_gap_at_once():
    item = SimpleNamespace(deliverable=_chain(5, text=True))

    light = _compose_guard(POOL, strict_plan=True)(item)
    standard = _compose_guard(POOL, strict_plan=True, profile="standard")(item)
    small_cap = _compose_guard(POOL, strict_plan=True, light_max_nodes=4)(item)

    assert light == []
    assert standard == small_cap and len(standard) >= 4                  # four consumes + the closeout, in one round


def test_plan_settings_are_validated_and_default_to_auto():
    assert resolve_plan({}) == {"profile": "auto", "light_max_nodes": 12, "light_max_business_actions": 20}
    assert resolve_plan({"plan": {"profile": "standard", "light_max_nodes": 3}})["light_max_nodes"] == 3
    for bad in ({"profile": "heavy"}, {"light_max_nodes": 0}, {"light_max_nodes": "12"}):
        with pytest.raises(ValidationError):
            resolve_plan({"plan": bad})

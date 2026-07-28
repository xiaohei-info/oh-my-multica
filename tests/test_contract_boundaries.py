from types import SimpleNamespace

import pytest

from omac.core.contract_boundaries import (
    contract_boundary_conflicts,
    responsibility_summary,
    review_boundary_report_errors,
)
from omac.core.lint import lint
from omac.core.manifest import (
    ConsumedArtifact,
    Contract,
    EvidenceMode,
    Manifest,
    MISSING_CONSUMES,
    Node,
    ProducedArtifact,
    _dump_contract,
    _load_contract,
    loads_manifest,
)


POOL = {"alice", "bob"}


def _contract(**overrides):
    values = {
        "objective": "deliver one bounded capability",
        "source_of_truth": ["docs/design.md#capability"],
        "acceptance": ["UJ-1"],
        "non_goals": ["do not consume downstream outputs"],
        "verification_commands": ["pytest -q"],
        "integration_gates": [{
            "name": "gate-1",
            "layer": "L0",
            "delivery_goal": "bounded delivery",
            "source_of_truth": ["docs/design.md#capability"],
            "covers": ["capability"],
            "acceptance_refs": ["UJ-1"],
            "commands": ["pytest -q"],
        }],
        "pr_base": "main",
    }
    values.update(overrides)
    return Contract(**values)


def _manifest(*nodes):
    return Manifest(meta={}, nodes={node.id: node for node in nodes})


def test_old_contract_remains_readable_and_omits_boundary_defaults():
    contract = _load_contract({"objective": "legacy"})

    assert contract.evidence_mode is None
    assert contract.produces == []
    assert contract.consumes is MISSING_CONSUMES
    assert "evidence_mode" not in _dump_contract(contract)
    assert "produces" not in _dump_contract(contract)
    assert "consumes" not in _dump_contract(contract)


def test_omitted_and_explicit_empty_consumes_survive_roundtrip():
    omitted = _load_contract({
        "objective": "transitional",
        "evidence_mode": "fixture",
        "produces": [{"artifact_id": "tooling-package"}],
    })
    explicit_empty = _load_contract({
        "objective": "self-contained",
        "evidence_mode": "fixture",
        "produces": [{"artifact_id": "tooling-package"}],
        "consumes": [],
    })

    assert omitted.consumes is MISSING_CONSUMES
    assert "consumes" not in _dump_contract(omitted)
    assert explicit_empty.consumes == []
    assert _dump_contract(explicit_empty)["consumes"] == []
    assert _load_contract(_dump_contract(omitted)).consumes is MISSING_CONSUMES
    assert _load_contract(_dump_contract(explicit_empty)).consumes == []


def test_explicit_null_consumes_is_preserved_and_rejected_from_yaml():
    manifest = loads_manifest("""
meta: {}
nodes:
  - id: tooling
    worker: alice
    contract:
      objective: tooling
      evidence_mode: fixture
      produces:
        - artifact_id: tooling-package
      consumes: null
""")
    contract = manifest.nodes["tooling"].contract

    assert contract.consumes is None
    assert _dump_contract(contract)["consumes"] is None
    assert responsibility_summary(contract)["input_policy"] == "invalid"
    assert responsibility_summary({
        "evidence_mode": "fixture", "consumes": None,
    })["input_policy"] == "invalid"
    assert any("contract.consumes must be a list" in error
               for error in lint(manifest, POOL))


def test_typed_boundary_roundtrip_preserves_enum_and_artifact_types():
    contract = Contract(
        objective="tooling",
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
        consumes=[ConsumedArtifact(
            artifact_id="source-contracts",
            producer="source-contracts",
            evidence_mode=EvidenceMode.ARTIFACT,
        )],
    )

    loaded = _load_contract(_dump_contract(contract))

    assert loaded.evidence_mode is EvidenceMode.FIXTURE
    assert loaded.produces == [ProducedArtifact("tooling-package")]
    assert loaded.consumes == [ConsumedArtifact(
        artifact_id="source-contracts",
        producer="source-contracts",
        evidence_mode=EvidenceMode.ARTIFACT,
    )]


def test_lint_accepts_fixture_node_with_declared_upstream_artifact():
    producer = Node(
        id="source-contracts",
        worker="alice",
        contract=_contract(
            evidence_mode=EvidenceMode.ARTIFACT,
            produces=[ProducedArtifact("source-contracts")],
        ),
    )
    consumer = Node(
        id="tooling",
        worker="bob",
        blocked_by=["source-contracts"],
        contract=_contract(
            evidence_mode=EvidenceMode.FIXTURE,
            consumes=[ConsumedArtifact(
                artifact_id="source-contracts",
                producer="source-contracts",
                evidence_mode=EvidenceMode.ARTIFACT,
            )],
            produces=[ProducedArtifact("tooling-package")],
        ),
    )

    assert lint(_manifest(producer, consumer), POOL) == []


def test_lint_rejects_unknown_and_non_upstream_producers_with_safe_direction():
    unrelated = Node(
        id="future-bundle",
        worker="alice",
        contract=_contract(produces=[ProducedArtifact("release-assembly")]),
    )
    consumer = Node(
        id="tooling",
        worker="bob",
        contract=_contract(consumes=[
            ConsumedArtifact(
                artifact_id="missing",
                producer="missing-node",
                evidence_mode=EvidenceMode.ARTIFACT,
            ),
            ConsumedArtifact(
                artifact_id="release-assembly",
                producer="future-bundle",
                evidence_mode=EvidenceMode.ARTIFACT,
            ),
        ]),
    )

    errors = lint(_manifest(unrelated, consumer), POOL)
    joined = "\n".join(errors)

    assert "producer 'missing-node' does not exist" in joined
    assert "add the producer node" in joined
    assert "producer 'future-bundle' is not a transitive upstream dependency" in joined
    assert "add it to blocked_by only if it is a real prerequisite" in joined


def test_lint_rejects_fixture_contract_that_requires_live_evidence():
    live_source = Node(
        id="live-source",
        worker="alice",
        contract=_contract(
            evidence_mode=EvidenceMode.LIVE,
            produces=[ProducedArtifact("live-release")],
        ),
    )
    fixture = Node(
        id="fixture-tooling",
        worker="bob",
        blocked_by=["live-source"],
        contract=_contract(
            evidence_mode=EvidenceMode.FIXTURE,
            consumes=[ConsumedArtifact(
                artifact_id="live-release",
                producer="live-source",
                evidence_mode=EvidenceMode.LIVE,
            )],
        ),
    )

    errors = lint(_manifest(live_source, fixture), POOL)

    assert any(
        "fixture evidence_mode cannot require live evidence" in error
        and "change the node evidence_mode" in error
        for error in errors
    )


def test_lint_rejects_artifact_not_declared_by_reachable_producer():
    producer = Node(
        id="producer",
        worker="alice",
        contract=_contract(produces=[ProducedArtifact("actual-output")]),
    )
    consumer = Node(
        id="consumer",
        worker="bob",
        blocked_by=["producer"],
        contract=_contract(consumes=[ConsumedArtifact(
            artifact_id="invented-output",
            producer="producer",
            evidence_mode=EvidenceMode.ARTIFACT,
        )]),
    )

    errors = lint(_manifest(producer, consumer), POOL)

    assert any(
        "artifact_id 'invented-output' is not produced by 'producer'" in error
        and "declare it in that producer's contract.produces" in error
        for error in errors
    )


def test_responsibility_summary_is_compact_and_explicit():
    summary = responsibility_summary(_contract(
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
        consumes=[ConsumedArtifact(
            artifact_id="source-contracts",
            producer="source-contracts",
            evidence_mode=EvidenceMode.ARTIFACT,
        )],
    ))

    assert summary == {
        "evidence_mode": "fixture",
        "input_policy": "allowlist",
        "allowed_inputs": [{
            "artifact_id": "source-contracts",
            "producer": "source-contracts",
            "evidence_mode": "artifact",
        }],
        "produces": ["tooling-package"],
        "boundary_rule": (
            "Only declared consumes are allowed external inputs; outputs from "
            "non-upstream or downstream nodes are outside this contract."
        ),
    }


def test_responsibility_summary_distinguishes_transitional_and_no_input():
    transitional = responsibility_summary(_contract(
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
    ))
    no_input = responsibility_summary(_contract(
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
        consumes=[],
    ))

    assert transitional["input_policy"] == "transitional-upstream"
    assert transitional["allowed_inputs"] is None
    assert "transitive upstream" in transitional["boundary_rule"]
    assert no_input["input_policy"] == "none"
    assert no_input["allowed_inputs"] == []
    assert "No external inputs" in no_input["boundary_rule"]


def test_transitional_consumes_allows_structured_upstream_but_rejects_downstream():
    upstream = Node(id="upstream", worker="alice")
    tooling = Node(
        id="tooling", worker="alice", blocked_by=["upstream"],
        contract=_contract(
            evidence_mode=EvidenceMode.FIXTURE,
            produces=[ProducedArtifact("tooling-package")],
        ),
    )
    downstream = Node(id="downstream", worker="bob", blocked_by=["tooling"])

    upstream_report = {"blockers": [{"required_inputs": [{
        "artifact_id": "legacy-output", "producer": "upstream",
        "evidence_mode": "artifact",
    }]}]}
    downstream_report = {"blockers": [{"required_inputs": [{
        "artifact_id": "future-output", "producer": "downstream",
        "evidence_mode": "artifact",
    }]}]}

    manifest = _manifest(upstream, tooling, downstream)
    assert contract_boundary_conflicts(
        manifest, tooling, SimpleNamespace(review_report=upstream_report)) == []
    assert contract_boundary_conflicts(
        manifest, tooling, SimpleNamespace(review_report=downstream_report)
    ) == [{
        "reason_code": "review-requires-non-upstream-artifact",
        "artifact_id": "future-output",
        "producer": "downstream",
    }]


def test_explicit_empty_consumes_rejects_upstream_input():
    upstream = Node(id="upstream", worker="alice")
    tooling = Node(
        id="tooling", worker="bob", blocked_by=["upstream"],
        contract=_contract(evidence_mode=EvidenceMode.FIXTURE, consumes=[]),
    )
    report = {"blockers": [{"required_inputs": [{
        "artifact_id": "legacy-output", "producer": "upstream",
        "evidence_mode": "artifact",
    }]}]}

    assert contract_boundary_conflicts(
        _manifest(upstream, tooling), tooling,
        SimpleNamespace(review_report=report),
    ) == [{
        "reason_code": "review-requires-undeclared-artifact",
        "artifact_id": "legacy-output",
        "producer": "upstream",
    }]


def test_lint_does_not_require_typed_producer_outputs_for_omitted_consumes():
    legacy = Node(id="legacy", worker="alice")
    consumer = Node(
        id="consumer", worker="bob", blocked_by=["legacy"],
        contract=_contract(
            evidence_mode=EvidenceMode.FIXTURE,
            produces=[ProducedArtifact("tooling-package")],
        ),
    )

    assert lint(_manifest(legacy, consumer), POOL) == []


def test_review_conflict_detects_exact_downstream_artifact_and_live_requirement():
    tooling = Node(
        id="tooling",
        worker="alice",
        contract=_contract(
            evidence_mode=EvidenceMode.FIXTURE,
            produces=[ProducedArtifact("tooling-package")],
        ),
    )
    assembly = Node(
        id="assembly",
        worker="bob",
        blocked_by=["tooling"],
        contract=_contract(
            evidence_mode=EvidenceMode.LIVE,
            produces=[ProducedArtifact("production-bundle")],
        ),
    )
    report = {
        "blockers": [{
            "required_fix": "Generate production-bundle before tooling can pass.",
            "required_evidence_mode": "live",
            "required_inputs": [{
                "artifact_id": "production-bundle",
                "producer": "assembly",
                "evidence_mode": "live",
            }],
        }],
    }

    conflicts = contract_boundary_conflicts(
        _manifest(tooling, assembly), tooling, SimpleNamespace(review_report=report))

    assert {conflict["reason_code"] for conflict in conflicts} == {
        "fixture-requires-live-evidence",
        "review-requires-non-upstream-artifact",
    }
    artifact_conflict = next(
        conflict for conflict in conflicts
        if conflict["reason_code"] == "review-requires-non-upstream-artifact")
    assert artifact_conflict["artifact_id"] == "production-bundle"
    assert artifact_conflict["producer"] == "assembly"


def test_normal_local_rework_has_no_boundary_conflict():
    tooling = Node(
        id="tooling",
        worker="alice",
        contract=_contract(
            evidence_mode=EvidenceMode.FIXTURE,
            produces=[ProducedArtifact("tooling-package")],
        ),
    )
    report = {"blockers": [{"required_fix": "Add the missing local fixture test."}]}

    assert contract_boundary_conflicts(
        _manifest(tooling), tooling, SimpleNamespace(review_report=report)) == []


@pytest.mark.parametrize("blocker", [
    {
        "required_fix": (
            "Do not generate production-bundle; only fix the local fixture."
        ),
    },
    {
        "summary": "production-bundle is explicitly out of scope for this node.",
        "required_fix": "Keep the rework inside the fixture contract.",
    },
    {
        "required_fix": (
            "Compare the local fixture naming with production-bundle, but do not "
            "consume that downstream artifact."
        ),
    },
])
def test_review_prose_never_infers_boundary_conflicts(blocker):
    tooling = Node(
        id="tooling",
        worker="alice",
        contract=_contract(evidence_mode=EvidenceMode.FIXTURE),
    )
    downstream = Node(
        id="downstream",
        worker="bob",
        blocked_by=["tooling"],
        contract=_contract(produces=[ProducedArtifact("production-bundle")]),
    )
    report = {"blockers": [blocker]}

    assert contract_boundary_conflicts(
        _manifest(tooling, downstream), tooling,
        SimpleNamespace(review_report=report)) == []


def test_reviewer_boundary_fields_are_typed_when_present():
    errors = review_boundary_report_errors({
        "blockers": [{
            "required_evidence_mode": "production-ish",
            "required_inputs": [{
                "artifact_id": "",
                "producer": "",
                "evidence_mode": "unknown",
            }],
        }],
    })

    assert "required_evidence_mode must be fixture|artifact|live" in "\n".join(errors)
    assert "required_inputs[0].artifact_id must be a non-empty string" in "\n".join(errors)
    assert "required_inputs[0].producer must be a non-empty node id" in "\n".join(errors)
    assert "required_inputs[0].evidence_mode must be fixture|artifact|live" in "\n".join(errors)

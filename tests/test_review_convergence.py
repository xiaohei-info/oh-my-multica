from types import SimpleNamespace

import pytest
import yaml

from omac.core.review_convergence import (
    REVIEW_PROTOCOL_VERSION,
    advance_review_ledger,
    build_review_obligations,
    review_convergence_decision,
    review_state,
    validate_convergence_review,
    validate_review_ledger,
)
from omac.core.taskmeta import TaskKind, TaskPhase, WorkerHandoffIntent
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline import dispatch
from omac.pipeline.dispatch import submit as submit_work
from omac.engines import create_engine
from omac.pipeline.tasks import run_task
from omac.core.acceptance import load_acceptance_doc
from omac.core.manifest import Contract, Manifest, Node, _dump_contract


def _item(*, contract=None, ledger=None):
    return SimpleNamespace(
        kind=TaskKind.DEVELOP,
        contract=contract,
        review_ledger=ledger,
        review_obligations=None,
    )


def _results(obligations, *, failed=()):
    failed = set(failed)
    return [
        {
            "obligation_id": obligation["obligation_id"],
            "status": "fail" if obligation["obligation_id"] in failed else "pass",
            "evidence": f"checked {obligation['obligation_id']}",
        }
        for obligation in obligations
    ]


def _report(obligations, *, blockers=None, prior=None, failed=()):
    return {
        "review_protocol": REVIEW_PROTOCOL_VERSION,
        "full_review_completed": True,
        "obligation_results": _results(obligations, failed=failed),
        "prior_blocker_results": prior or [],
        "blockers": blockers or [],
        "nits": [],
    }


def _amendment_acceptance_doc():
    return load_acceptance_doc({
        "schema": "omac.acceptance/v2",
        "flows": [{
            "id": "UJ-AMEND-001",
            "name": "amendment flow",
            "actions": [{
                "id": "ACT-AMEND-001",
                "kind": "business-action",
                "step": "perform correction",
                "how": "apply the accepted amendment",
                "expected": "owner is corrected",
            }],
        }],
    })


def _amendment_manifest():
    contract = Contract(
        objective="preserve current contract",
        source_of_truth=["docs/design.md"],
        acceptance=["UJ-AMEND-001"],
        non_goals=["no delivery replay"],
        verification_commands=["pytest -q"],
        integration_gates=[{
            "name": "amendment-gate",
            "layer": "L1",
            "delivery_goal": "correct ownership",
            "source_of_truth": ["docs/design.md"],
            "covers": ["node-a"],
            "acceptance_refs": ["UJ-AMEND-001"],
            "commands": ["pytest -q"],
        }],
        pr_base="main",
    )
    return Manifest(meta={}, nodes={"node-a": Node(
        id="node-a", worker="alice", reviewer="bob", contract=contract,
    )})


def _amendment_item(manifest):
    raw = {
        "schema": "omac.dag-amendment/v1",
        "reason": "move global acceptance responsibility",
        "operations": [{
            "op": "update-responsibility",
            "node": "node-a",
            "acceptance_claims": ["UJ-AMEND-001"],
            "acceptance_contributions": [{
                "flow_id": "UJ-AMEND-001", "action_ids": ["ACT-AMEND-001"],
            }],
            "acceptance_refs": ["UJ-AMEND-001"],
            "clear_legacy_acceptance": True,
            "integration_gate_responsibility_patches": [{
                "name": "amendment-gate", "acceptance_refs": ["UJ-AMEND-001"],
            }],
        }],
    }
    return SimpleNamespace(
        kind=TaskKind.AMENDMENT,
        contract=None,
        deliverable=yaml.safe_dump(raw, sort_keys=False),
        review_ledger=None,
        review_obligations=None,
    )


def test_build_review_obligations_is_stable_and_contract_aware():
    item = _item(contract={
        "acceptance": ["UJ-2", "UJ-1", "UJ-1"],
        "integration_gates": [{"name": "release"}, {"name": "api"}],
    })

    obligations = build_review_obligations(item)
    ids = [entry["obligation_id"] for entry in obligations]

    assert ids[:6] == [
        "dimension:authority",
        "dimension:structure",
        "dimension:execution",
        "dimension:ownership",
        "dimension:evidence",
        "dimension:regression",
    ]
    assert ids[6:] == [
        "acceptance:UJ-1",
        "acceptance:UJ-2",
        "integration:api",
        "integration:release",
    ]


def test_amendment_review_requires_disposition_of_compact_before_after_responsibility_matrix():
    manifest = _amendment_manifest()
    item = _amendment_item(manifest)
    obligations = build_review_obligations(
        item,
        acceptance_doc=_amendment_acceptance_doc(),
        amendment_manifest=manifest,
    )
    matrix = next(
        obligation for obligation in obligations
        if obligation["obligation_id"] == "acceptance-responsibility:amendment-matrix"
    )

    assert matrix["before"][0] == {
        "flow_id": "UJ-AMEND-001",
        "full_claim_owners": ["node-a"],
        "business_action_count": 1,
        "contributed_business_action_count": 0,
        "contribution_owners": [],
        "full_owner_dependency_closure": [],
        "missing_business_action_ids": ["ACT-AMEND-001"],
        "unknown_business_action_ids": [],
        "unreachable_contribution_owners": [],
        "trace_nodes": [],
    }
    assert matrix["after"][0]["contributed_business_action_count"] == 1
    assert matrix["after"][0]["missing_business_action_ids"] == []
    assert "ACT-AMEND-001" not in yaml.safe_dump(matrix["after"])
    assert matrix["historical_contract_corrections"] == []

    item.review_obligations = obligations
    report = _report(obligations)
    report["obligation_results"] = [
        result for result in report["obligation_results"]
        if result["obligation_id"] != "acceptance-responsibility:amendment-matrix"
    ]

    assert validate_convergence_review(item, "pass", report) == [
        "review_report missing obligation result: acceptance-responsibility:amendment-matrix"
    ]


def test_review_cannot_claim_complete_with_unreviewed_obligation():
    item = _item()
    obligations = build_review_obligations(item)
    item.review_obligations = obligations
    report = _report(obligations)
    report["obligation_results"].pop()

    errors = validate_convergence_review(item, "pass", report)

    assert errors == ["review_report missing obligation result: dimension:regression"]


def test_review_must_disposition_every_open_prior_blocker():
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [],
        "blockers": [{
            "blocker_id": "BLK-old",
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "summary": "release trust is incomplete",
            "status": "open",
            "first_seen_round": 1,
            "last_seen_round": 1,
            "seen_count": 1,
        }],
    }
    item = _item(ledger=ledger)
    obligations = build_review_obligations(item)
    item.review_obligations = obligations

    errors = validate_convergence_review(
        item, "pass", _report(obligations))

    assert errors == ["review_report missing prior blocker result: BLK-old"]


def test_unresolved_prior_blocker_requires_current_structured_blocker():
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [],
        "blockers": [{
            "blocker_id": "BLK-old",
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "summary": "release trust is incomplete",
            "status": "open",
            "seen_count": 1,
        }],
    }
    item = _item(ledger=ledger)
    obligations = build_review_obligations(item)
    item.review_obligations = obligations
    report = _report(
        obligations,
        prior=[{
            "blocker_id": "BLK-old",
            "status": "unchanged",
            "evidence": "still incomplete",
        }],
        failed={"dimension:structure"},
        blockers=[{
            "root_cause_key": "another-root",
            "obligation_id": "dimension:structure",
            "classification": "new",
            "summary": "another blocker",
            "evidence": "another finding",
            "required_fix": "fix the other blocker",
        }],
    )

    errors = validate_convergence_review(item, "reject", report)

    assert "review_report unresolved prior blocker is missing from blockers: BLK-old" in errors


def test_prior_blocker_status_and_current_classification_must_agree():
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [],
        "blockers": [{
            "blocker_id": "BLK-old",
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "summary": "release trust is incomplete",
            "status": "open",
            "seen_count": 1,
        }],
    }
    item = _item(ledger=ledger)
    obligations = build_review_obligations(item)
    item.review_obligations = obligations
    report = _report(
        obligations,
        prior=[{
            "blocker_id": "BLK-old",
            "status": "fixed",
            "evidence": "claimed fixed",
        }],
        failed={"dimension:evidence"},
        blockers=[{
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "classification": "unchanged",
            "summary": "release trust is incomplete",
            "evidence": "still incomplete",
            "required_fix": "verify the signed handoff",
        }],
    )

    errors = validate_convergence_review(item, "reject", report)

    assert "review_report prior blocker BLK-old cannot be fixed while its root remains blocked" in errors


def test_review_report_rejects_duplicate_root_cause_keys():
    item = _item()
    obligations = build_review_obligations(item)
    item.review_obligations = obligations
    blocker = {
        "root_cause_key": "duplicate-root",
        "obligation_id": "dimension:structure",
        "classification": "new",
        "summary": "duplicate root",
        "evidence": "same finding",
        "required_fix": "fix once",
    }
    report = _report(
        obligations,
        failed={"dimension:structure"},
        blockers=[blocker, dict(blocker)],
    )

    errors = validate_convergence_review(item, "reject", report)

    assert "review_report contains duplicate root_cause_key: duplicate-root" in errors


def test_review_report_blocker_must_reference_failed_obligation():
    item = _item()
    obligations = build_review_obligations(item)
    item.review_obligations = obligations
    report = _report(
        obligations,
        blockers=[{
            "root_cause_key": "contradictory-root",
            "obligation_id": "dimension:structure",
            "classification": "new",
            "summary": "claims a blocker despite passing evidence",
            "evidence": "all obligation results say pass",
            "required_fix": "make the result and blocker agree",
        }],
    )

    errors = validate_convergence_review(item, "reject", report)

    assert (
        "review_report.blockers[0].obligation_id must reference a failed obligation"
        in errors
    )
    assert "review_report reject verdict requires at least one failed obligation" in errors


def test_ledger_closes_old_blocker_and_adds_new_blocker():
    item = _item()
    obligations = build_review_obligations(item)
    first = _report(
        obligations,
        failed={"dimension:evidence"},
        blockers=[{
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "classification": "new",
            "summary": "release trust is incomplete",
            "evidence": "signature report is not consumed",
            "required_fix": "verify the signed release handoff",
        }],
    )
    first_ledger = advance_review_ledger(
        None, first, verdict="reject", subject_digest="v1", round_index=1)
    old_id = first_ledger["blockers"][0]["blocker_id"]

    item.review_ledger = first_ledger
    second_obligations = build_review_obligations(item)
    second = _report(
        second_obligations,
        prior=[{
            "blocker_id": old_id,
            "status": "fixed",
            "evidence": "signed handoff is now verified",
        }],
        failed={"dimension:execution"},
        blockers=[{
            "root_cause_key": "command-target",
            "obligation_id": "dimension:execution",
            "classification": "new",
            "summary": "command target is empty",
            "evidence": "go list matches no packages",
            "required_fix": "make the target explicit and non-empty",
        }],
    )

    second_ledger = advance_review_ledger(
        first_ledger, second, verdict="reject",
        subject_digest="v2", round_index=2)

    records = {record["root_cause_key"]: record for record in second_ledger["blockers"]}
    assert records["release-trust"]["status"] == "fixed"
    assert records["command-target"]["status"] == "open"
    assert second_ledger["cycles"][-1]["fixed_count"] == 1
    assert second_ledger["cycles"][-1]["new_count"] == 1


def test_ledger_same_subject_and_verdict_with_different_report_advances():
    item = _item()
    obligations = build_review_obligations(item)

    def rejected_report(root):
        return _report(
            obligations,
            failed={"dimension:structure"},
            blockers=[{
                "root_cause_key": root,
                "obligation_id": "dimension:structure",
                "classification": "new",
                "summary": root,
                "evidence": root,
                "required_fix": f"fix {root}",
            }],
        )

    first = rejected_report("root-a")
    second = rejected_report("root-b")
    ledger = advance_review_ledger(
        None, first, verdict="reject", subject_digest="same", round_index=1)

    ledger = advance_review_ledger(
        ledger, second, verdict="reject", subject_digest="same", round_index=1)

    assert len(ledger["cycles"]) == 2
    assert {record["root_cause_key"] for record in ledger["blockers"]} == {
        "root-a",
        "root-b",
    }
    assert ledger["cycles"][0]["report_digest"] != ledger["cycles"][1]["report_digest"]


def test_ledger_identical_retry_remains_idempotent():
    item = _item()
    obligations = build_review_obligations(item)
    report = _report(
        obligations,
        failed={"dimension:structure"},
        blockers=[{
            "root_cause_key": "same-root",
            "obligation_id": "dimension:structure",
            "classification": "new",
            "summary": "same root",
            "evidence": "same evidence",
            "required_fix": "same fix",
        }],
    )
    ledger = advance_review_ledger(
        None, report, verdict="reject", subject_digest="same", round_index=1)

    retried = advance_review_ledger(
        ledger, report, verdict="reject", subject_digest="same", round_index=1)

    assert retried == ledger
    assert len(retried["cycles"]) == 1


def test_reappearing_closed_root_cause_is_classified_as_regression():
    item = _item()
    obligations = build_review_obligations(item)
    first = _report(
        obligations,
        failed={"dimension:evidence"},
        blockers=[{
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "classification": "new",
            "summary": "release trust is incomplete",
            "evidence": "missing signature proof",
            "required_fix": "verify signatures",
        }],
    )
    ledger = advance_review_ledger(
        None, first, verdict="reject", subject_digest="v1", round_index=1)
    blocker_id = ledger["blockers"][0]["blocker_id"]
    item.review_ledger = ledger
    obligations = build_review_obligations(item)
    fixed = _report(
        obligations,
        prior=[{"blocker_id": blocker_id, "status": "fixed", "evidence": "fixed"}],
    )
    ledger = advance_review_ledger(
        ledger, fixed, verdict="pass", subject_digest="v2", round_index=2)
    reopened = _report(
        obligations,
        failed={"dimension:evidence"},
        blockers=[{
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "classification": "new",
            "summary": "release trust regressed",
            "evidence": "signature proof disappeared",
            "required_fix": "restore signature verification",
        }],
    )

    ledger = advance_review_ledger(
        ledger, reopened, verdict="reject", subject_digest="v3", round_index=3)

    record = ledger["blockers"][0]
    assert record["blocker_id"] == blocker_id
    assert record["status"] == "open"
    assert record["classification"] == "regressed"
    assert ledger["cycles"][-1]["regressed_count"] == 1
    assert review_state(ledger)["mode"] == "normal"


def test_two_early_rounds_with_new_blockers_do_not_invent_a_decision():
    item = _item()
    obligations = build_review_obligations(item)
    ledger = None
    for round_index, root in enumerate(("root-a", "root-b"), start=1):
        report = _report(
            obligations,
            failed={"dimension:structure"},
            blockers=[{
                "root_cause_key": root,
                "obligation_id": "dimension:structure",
                "classification": "new",
                "summary": root,
                "evidence": root,
                "required_fix": root,
            }],
        )
        ledger = advance_review_ledger(
            ledger, report, verdict="reject",
            subject_digest=f"v{round_index}", round_index=round_index)

    state = review_state(ledger)
    assert state == {
        "mode": "normal",
        "reason": None,
        "cycle_count": 2,
        "open_blocker_count": 2,
        "open_blocker_ids": sorted(
            blocker["blocker_id"] for blocker in ledger["blockers"]),
    }


def test_unchanged_blocker_counts_once_per_review_cycle():
    item = _item()
    obligations = build_review_obligations(item)
    report = _report(
        obligations,
        failed={"dimension:structure"},
        blockers=[{
            "root_cause_key": "same-root",
            "obligation_id": "dimension:structure",
            "classification": "new",
            "summary": "same root",
            "evidence": "first finding",
            "required_fix": "close the root",
        }],
    )
    ledger = advance_review_ledger(
        None, report, verdict="reject", subject_digest="v1", round_index=1)
    blocker_id = ledger["blockers"][0]["blocker_id"]
    item.review_ledger = ledger
    obligations = build_review_obligations(item)
    report = _report(
        obligations,
        prior=[{
            "blocker_id": blocker_id,
            "status": "unchanged",
            "evidence": "still present",
        }],
        failed={"dimension:structure"},
        blockers=[{
            "root_cause_key": "same-root",
            "obligation_id": "dimension:structure",
            "classification": "unchanged",
            "summary": "same root",
            "evidence": "still present",
            "required_fix": "close the root",
        }],
    )

    ledger = advance_review_ledger(
        ledger, report, verdict="reject", subject_digest="v2", round_index=2)

    assert ledger["blockers"][0]["seen_count"] == 2
    assert review_state(ledger)["mode"] == "normal"


def _decision_ledger(
    open_counts, *, new_counts=None, obligation_ids=("dimension:structure",),
    first_seen_rounds=None, classifications=None,
):
    new_counts = new_counts or [0] * len(open_counts)
    first_seen_rounds = first_seen_rounds or [1] * len(obligation_ids)
    classifications = classifications or ["unchanged"] * len(obligation_ids)
    blocker_ids = [
        f"BLK-{index}" for index in range(1, len(obligation_ids) + 1)
    ]
    return {
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": index,
                "new_count": new_counts[index - 1],
                "fixed_count": 0,
                "regressed_count": 0,
                "unchanged_count": (
                    len(blocker_ids) if index > 1 else 0
                ),
                "open_count": open_count,
                "open_blocker_ids": blocker_ids,
            }
            for index, open_count in enumerate(open_counts, start=1)
        ],
        "blockers": [
            {
                "blocker_id": f"BLK-{index}",
                "root_cause_key": f"root-{index}",
                "obligation_id": obligation_id,
                "status": "open",
                "classification": classifications[index - 1],
                "first_seen_round": first_seen_rounds[index - 1],
                "last_seen_round": len(open_counts),
                "seen_count": (
                    len(open_counts) - first_seen_rounds[index - 1] + 1
                ),
            }
            for index, obligation_id in enumerate(obligation_ids, start=1)
        ],
    }


def test_review_convergence_allows_healthy_progress_without_policy_signal():
    ledger = _decision_ledger(
        [4, 3, 2, 1], classifications=["deeper"])

    assert review_convergence_decision(ledger) is None
    assert review_state(ledger)["mode"] == "normal"


def test_review_convergence_stops_at_cycle_three_for_same_unchanged_blocker():
    ledger = _decision_ledger([1, 1, 1])

    decision = review_convergence_decision(ledger)

    assert decision["reason_code"] == "review-convergence-stalled"
    assert decision["mode"] == "stalled"
    assert decision["cycle_count"] == 3
    assert decision["non_reducing_streak"] == 2
    assert decision["unchanged_blocker_ids"] == ["BLK-1"]
    state = review_state(ledger)
    assert state["mode"] == "convergence-audit"
    assert state["decision"] == decision


def test_review_convergence_stops_when_late_review_expands_scope():
    ledger = _decision_ledger(
        [4, 3, 2, 1, 1, 2],
        new_counts=[0, 0, 0, 0, 0, 1],
        first_seen_rounds=[6],
        classifications=["new"],
    )

    decision = review_convergence_decision(ledger)

    assert decision["reason_code"] == "review-convergence-scope-expanding"
    assert decision["mode"] == "scope-expanding"
    assert decision["late_root_cause_keys"] == ["root-1"]


def test_review_convergence_stops_for_three_open_responsibility_dimensions():
    ledger = _decision_ledger(
        [3, 3, 3],
        obligation_ids=(
            "dimension:authority",
            "dimension:structure",
            "dimension:ownership",
        ),
        classifications=("new", "new", "new"),
    )

    decision = review_convergence_decision(ledger)

    assert decision["reason_code"] == "review-convergence-scope-expanding"
    assert decision["obligation_dimensions"] == [
        "dimension:authority",
        "dimension:ownership",
        "dimension:structure",
    ]
    assert decision["cycle_count"] == 3


def test_review_convergence_has_unconditional_ten_cycle_stop():
    ledger = _decision_ledger(
        [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
        classifications=["deeper"],
    )

    decision = review_convergence_decision(ledger)

    assert decision["reason_code"] == "review-convergence-exhausted"
    assert decision["mode"] == "exhausted"
    assert decision["cycle_count"] == 10


@pytest.mark.parametrize("ledger", [
    {},
    {"schema": "future.review-ledger/v9", "cycles": [], "blockers": []},
    {"schema": "omac.review-ledger/v1", "cycles": {}, "blockers": []},
    {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": {}},
    {"schema": "omac.review-ledger/v1", "cycles": ["bad"], "blockers": []},
    {"schema": "omac.review-ledger/v1", "cycles": [], "blockers": ["bad"]},
    {
        "schema": "omac.review-ledger/v1",
        "cycles": [{"round": "3"}],
        "blockers": [],
    },
    {
        "schema": "omac.review-ledger/v1",
        "cycles": [{
            "round": 1, "new_count": 0, "fixed_count": 0,
            "regressed_count": 0, "unchanged_count": 0, "open_count": 1,
        }],
        "blockers": [{
            "blocker_id": "BLK-1", "root_cause_key": "root-1",
            "obligation_id": "dimension:structure", "status": "open",
            "classification": "unchanged", "first_seen_round": 1,
            "last_seen_round": 1,
        }],
    },
], ids=[
    "empty", "wrong-schema", "wrong-cycles-type", "wrong-blockers-type",
    "wrong-cycle-entry", "wrong-blocker-entry", "partial-cycle",
    "partial-blocker",
])
def test_review_ledger_validation_rejects_noncanonical_persisted_facts(ledger):
    with pytest.raises(ValueError, match="review ledger"):
        validate_review_ledger(ledger)


def test_review_ledger_validation_requires_expected_source_round():
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {"round": 1, "open_count": 1},
            {"round": 2, "open_count": 1},
        ],
        "blockers": [{
            "blocker_id": "BLK-1", "root_cause_key": "root-1",
            "obligation_id": "dimension:structure", "status": "open",
            "classification": "deeper", "first_seen_round": 1,
            "seen_count": 2,
        }],
    }

    with pytest.raises(ValueError, match="latest round must be 3"):
        validate_review_ledger(ledger, expected_round=3)


def test_review_ledger_validation_rejects_truncated_cycle_history():
    ledger = _decision_ledger([1], classifications=["deeper"])
    ledger["cycles"][0]["round"] = 10

    with pytest.raises(ValueError, match="review ledger"):
        validate_review_ledger(ledger, expected_round=10)


def _interleaved_canonical_ledger():
    return {
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": 1,
                "open_count": 1,
                "open_blocker_ids": ["BLK-core"],
                "reported_blocker_ids": ["BLK-core"],
            },
            {
                "round": 2,
                "open_count": 2,
                "open_blocker_ids": ["BLK-core", "BLK-interleaved"],
                "reported_blocker_ids": ["BLK-core", "BLK-interleaved"],
            },
            {
                "round": 3,
                "open_count": 1,
                "open_blocker_ids": ["BLK-core"],
                "reported_blocker_ids": ["BLK-core"],
            },
        ],
        "blockers": [
            {
                "blocker_id": "BLK-core",
                "root_cause_key": "root-core",
                "obligation_id": "dimension:structure",
                "status": "open",
                "classification": "unchanged",
                "first_seen_round": 1,
                "last_seen_round": 3,
                "seen_count": 3,
            },
            {
                "blocker_id": "BLK-interleaved",
                "root_cause_key": "root-interleaved",
                "obligation_id": "dimension:evidence",
                "status": "fixed",
                "classification": "fixed",
                "first_seen_round": 2,
                "last_seen_round": 3,
                "seen_count": 1,
            },
        ],
    }


def _late_canonical_ledger():
    blocker_id = "BLK-late"
    return {
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": round_index,
                "open_count": 0,
                "open_blocker_ids": [],
                "reported_blocker_ids": [],
            }
            for round_index in range(1, 6)
        ] + [{
            "round": 6,
            "open_count": 1,
            "open_blocker_ids": [blocker_id],
            "reported_blocker_ids": [blocker_id],
        }],
        "blockers": [{
            "blocker_id": blocker_id,
            "root_cause_key": "root-late",
            "obligation_id": "dimension:structure",
            "status": "open",
            "classification": "new",
            "first_seen_round": 6,
            "last_seen_round": 6,
            "seen_count": 1,
        }],
    }


def _closed_reopened_canonical_ledger():
    blocker_id = "BLK-reopened"
    return {
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": 1,
                "open_count": 1,
                "open_blocker_ids": [blocker_id],
                "reported_blocker_ids": [blocker_id],
            },
            {
                "round": 2,
                "open_count": 0,
                "open_blocker_ids": [],
                "reported_blocker_ids": [],
            },
            {
                "round": 3,
                "open_count": 1,
                "open_blocker_ids": [blocker_id],
                "reported_blocker_ids": [blocker_id],
            },
        ],
        "blockers": [{
            "blocker_id": blocker_id,
            "root_cause_key": "root-reopened",
            "obligation_id": "dimension:regression",
            "status": "open",
            "classification": "regressed",
            "first_seen_round": 1,
            "last_seen_round": 3,
            "seen_count": 2,
        }],
    }


@pytest.mark.parametrize(("blocker_id", "seen_count"), [
    ("BLK-core", 2),
    ("BLK-interleaved", 2),
], ids=["underreported", "overreported-interleaved"])
def test_review_ledger_validation_requires_exact_canonical_seen_count(
    blocker_id, seen_count,
):
    ledger = _interleaved_canonical_ledger()
    blocker = next(
        record for record in ledger["blockers"]
        if record["blocker_id"] == blocker_id
    )
    blocker["seen_count"] = seen_count

    with pytest.raises(ValueError, match="seen_count"):
        validate_review_ledger(ledger, expected_round=3)


def test_review_ledger_validation_accepts_exact_interleaved_seen_counts():
    ledger = _interleaved_canonical_ledger()

    assert validate_review_ledger(ledger, expected_round=3) is ledger


def test_review_ledger_validation_rejects_cycle_id_underreport():
    ledger = _interleaved_canonical_ledger()
    cycle = ledger["cycles"][1]
    cycle["open_blocker_ids"].remove("BLK-core")
    cycle["reported_blocker_ids"].remove("BLK-core")
    ledger["blockers"][0]["seen_count"] = 2

    with pytest.raises(ValueError, match="open_count"):
        validate_review_ledger(ledger, expected_round=3)


@pytest.mark.parametrize(
    "field", ["open_blocker_ids", "reported_blocker_ids"])
def test_review_ledger_validation_rejects_duplicate_cycle_ids(field):
    ledger = _interleaved_canonical_ledger()
    ledger["cycles"][0][field].append("BLK-core")

    with pytest.raises(ValueError, match=field):
        validate_review_ledger(ledger, expected_round=3)


def test_review_ledger_validation_rejects_open_count_mismatch():
    ledger = _interleaved_canonical_ledger()
    ledger["cycles"][0]["open_count"] = 2

    with pytest.raises(ValueError, match="open_count"):
        validate_review_ledger(ledger, expected_round=3)


def test_review_ledger_validation_rejects_reported_blocker_not_open():
    ledger = _interleaved_canonical_ledger()
    latest = ledger["cycles"][-1]
    latest["open_count"] = 0
    latest["open_blocker_ids"] = []
    ledger["blockers"][0].update({
        "status": "fixed",
        "classification": "fixed",
    })

    with pytest.raises(ValueError, match="reported_blocker_ids"):
        validate_review_ledger(ledger, expected_round=3)


def test_review_ledger_validation_rejects_forged_first_seen_round():
    ledger = _late_canonical_ledger()
    ledger["blockers"][0]["first_seen_round"] = 1

    with pytest.raises(ValueError, match="first_seen_round"):
        validate_review_ledger(ledger, expected_round=6)


def test_review_ledger_validation_rejects_forged_current_status():
    ledger = _decision_ledger([1, 1, 1])
    ledger["blockers"][0].update({
        "status": "fixed",
        "classification": "fixed",
    })

    with pytest.raises(ValueError, match="status"):
        validate_review_ledger(ledger, expected_round=3)


@pytest.mark.parametrize("ledger_factory", [
    _interleaved_canonical_ledger,
    _closed_reopened_canonical_ledger,
], ids=["interleaved-open-reported", "closed-reopened"])
def test_review_ledger_validation_accepts_exact_canonical_projection(
    ledger_factory,
):
    ledger = ledger_factory()

    assert validate_review_ledger(
        ledger, expected_round=len(ledger["cycles"])
    ) is ledger


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_review_ledger_validation_requires_exact_canonical_blocker_set(mutation):
    ledger = _interleaved_canonical_ledger()
    if mutation == "missing":
        ledger["blockers"].pop()
    else:
        ledger["blockers"].append({
            "blocker_id": "BLK-extra",
            "root_cause_key": "root-extra",
            "obligation_id": "dimension:ownership",
            "status": "fixed",
            "classification": "fixed",
            "first_seen_round": 1,
            "last_seen_round": 3,
            "seen_count": 1,
        })

    with pytest.raises(ValueError, match="blocker"):
        validate_review_ledger(ledger, expected_round=3)


@pytest.mark.parametrize(("field", "value"), [
    ("seen_count", 0),
    ("seen_count", 4),
    ("first_seen_round", 0),
    ("first_seen_round", 4),
])
def test_review_ledger_validation_rejects_impossible_blocker_counts(field, value):
    ledger = _decision_ledger([1, 1, 1])
    ledger["blockers"][0][field] = value

    with pytest.raises(ValueError, match=field):
        validate_review_ledger(ledger, expected_round=3)


def _store():
    MockStore.reset()
    return MockStore(EngineConfig(engine_type="mock", workspace_id="ws"))


def test_work_show_exposes_finite_review_contract_and_open_blockers():
    store = _store()
    item = store.create_work_item(
        "ws", "review", "review", dag_key="review-1", worker="alice",
        reviewer="bob", kind=TaskKind.DECOMPOSE,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    item.phase = TaskPhase.REVIEW
    item.deliverable = "nodes: []"
    item.review_ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [],
        "blockers": [{
            "blocker_id": "BLK-old",
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "summary": "release trust is incomplete",
            "status": "open",
            "first_seen_round": 1,
            "last_seen_round": 1,
            "seen_count": 1,
            "required_fix": "verify the signed handoff",
        }],
    }
    item.review_obligations = build_review_obligations(item)

    output = dispatch.build_show_output(item, "reviewer:bob")

    assert output["context"]["review_protocol"] == REVIEW_PROTOCOL_VERSION
    assert output["context"]["review_obligations"] == item.review_obligations
    assert output["context"]["prior_open_blockers"][0]["blocker_id"] == "BLK-old"
    assert output["context"]["review_state"]["open_blocker_count"] == 1


def test_authoring_show_exposes_required_closures_and_convergence_mode():
    store = _store()
    item = store.create_work_item(
        "ws", "authoring", "authoring", dag_key="review-1", worker="alice",
        kind=TaskKind.DECOMPOSE,
    )
    item.review_ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {"round": 1, "new_count": 1},
            {"round": 2, "new_count": 1},
        ],
        "blockers": [{
            "blocker_id": "BLK-old",
            "root_cause_key": "release-trust",
            "obligation_id": "dimension:evidence",
            "summary": "release trust is incomplete",
            "status": "open",
            "first_seen_round": 1,
            "last_seen_round": 2,
            "seen_count": 2,
            "required_fix": "verify the signed handoff",
        }],
    }

    output = dispatch.build_show_output(item, "worker:alice")

    assert output["context"]["review_state"]["mode"] == "normal"
    assert output["context"]["required_closures"] == [{
        "blocker_id": "BLK-old",
        "obligation_id": "dimension:evidence",
        "root_cause_key": "release-trust",
        "summary": "release trust is incomplete",
        "required_fix": "verify the signed handoff",
    }]
    assert "previous_review" not in output["context"]


def test_authoring_show_does_not_bind_feedback_from_non_review_handoff():
    store = _store()
    item = store.create_work_item(
        "ws", "authoring", "authoring", dag_key="review-1", worker="alice",
        kind=TaskKind.DEVELOP,
    )
    item.worker_handoff = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="alice",
        gate="explicit-dispatch",
        source_review_subject_digest="stage-recovery",
        source_review_round=1,
        source_review_feedback={
            "verdict": "pass-with-nits",
            "nits": ["stale feedback"],
        },
        target_review_bounce=0,
    )

    output = dispatch.build_show_output(item, "worker:alice")

    assert "previous_review" not in output["context"]


def test_authoring_show_does_not_expose_malformed_review_nits_feedback():
    store = _store()
    item = store.create_work_item(
        "ws", "authoring", "authoring", dag_key="review-1", worker="alice",
        kind=TaskKind.DEVELOP,
    )
    item.worker_handoff = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="alice",
        gate="review-nits",
        source_review_subject_digest="subject-1",
        source_review_round=1,
        source_review_verdict="pass-with-nits",
        source_review_feedback={
            "verdict": "pass-with-nits",
            "nits": [],
        },
        target_review_bounce=1,
    )

    output = dispatch.build_show_output(item, "worker:alice")

    assert "previous_review" not in output["context"]


def test_authoring_show_does_not_expose_uncoupled_review_nits_feedback():
    store = _store()
    item = store.create_work_item(
        "ws", "authoring", "authoring", dag_key="review-1", worker="alice",
        kind=TaskKind.DEVELOP,
    )
    item.worker_handoff = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="alice",
        gate="review-nits",
        source_review_subject_digest="subject-1",
        source_review_round=1,
        source_review_verdict="pass-with-nits",
            source_review_feedback={
                "verdict": "pass-with-nits",
                "nits": ["follow up"],
                "report_ref": {
                    "attachment_id": "review-report-1",
                    "sha256": "a" * 64,
                },
            },
        target_review_bounce=1,
    )
    assert item.worker_handoff.is_complete()
    assert not item.worker_handoff.is_causally_bound()

    output = dispatch.build_show_output(item, "worker:alice")

    assert "previous_review" not in output["context"]


def test_pass_with_nits_requires_at_least_one_actionable_nit():
    item = _item()
    obligations = build_review_obligations(item)
    item.review_obligations = obligations
    report = _report(obligations)

    errors = validate_convergence_review(item, "pass-with-nits", report)

    assert errors == [
        "review_report pass-with-nits verdict requires at least one non-empty nit"
    ]


@pytest.mark.parametrize("nits", [[""], ["ok", "  "], ["ok", 1]])
def test_review_report_rejects_malformed_nits(nits):
    item = _item()
    obligations = build_review_obligations(item)
    item.review_obligations = obligations
    report = _report(obligations)
    report["nits"] = nits

    errors = validate_convergence_review(item, "pass-with-nits", report)

    assert any("review_report.nits" in error for error in errors)


def test_review_submit_rejects_empty_pass_with_nits_without_state_change(
    tmp_path,
):
    store = _store()
    item = store.create_work_item(
        "ws", "review", "review", dag_key="review-1", worker="alice",
        reviewer="bob", kind=TaskKind.DECOMPOSE,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    item.phase = TaskPhase.REVIEW
    item.deliverable = "nodes: []"
    item.review_subject_digest = "subject-v1"
    item.review_obligations = build_review_obligations(item)
    report = _report(item.review_obligations)
    report_path = tmp_path / "review.yaml"
    report_path.write_text(yaml.safe_dump(report))

    with pytest.raises(
        ValidationError,
        match="pass-with-nits verdict requires at least one non-empty nit",
    ):
        submit_work(
            store,
            item.id,
            verdict="pass-with-nits",
            report_file=str(report_path),
        )

    current = store.get_work_item(item.id)
    assert current.review_verdict is None
    assert current.review_report is None
    assert current.worker_handoff is None
    assert current.bounces.review == 0


def test_review_submit_updates_ledger_before_exposing_verdict(tmp_path):
    store = _store()
    item = store.create_work_item(
        "ws", "review", "review", dag_key="review-1", worker="alice",
        reviewer="bob", kind=TaskKind.DECOMPOSE,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    item.phase = TaskPhase.REVIEW
    item.deliverable = "nodes: []"
    item.review_subject_digest = "subject-v1"
    item.review_obligations = build_review_obligations(item)
    report = _report(
        item.review_obligations,
        failed={"dimension:execution"},
        blockers=[{
            "root_cause_key": "command-target",
            "obligation_id": "dimension:execution",
            "classification": "new",
            "summary": "command target is empty",
            "evidence": "target matches nothing",
            "required_fix": "declare a non-empty target",
        }],
    )
    path = tmp_path / "review.yaml"
    path.write_text(yaml.safe_dump(report))

    dispatch.submit(
        store, item.id, verdict="reject", report_file=str(path))

    current = store.get_work_item(item.id)
    assert current.review_verdict == "reject"
    assert current.review_ledger["cycles"][0]["subject_digest"] == "subject-v1"
    assert current.review_ledger["cycles"][0]["new_count"] == 1
    assert current.review_ledger["blockers"][0]["root_cause_key"] == "command-target"


def test_v2_review_submit_rejects_legacy_report_before_metadata_write(tmp_path):
    store = _store()
    item = store.create_work_item(
        "ws", "review", "review", dag_key="review-1", worker="alice",
        reviewer="bob", kind=TaskKind.DECOMPOSE,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    item.phase = TaskPhase.REVIEW
    item.deliverable = "nodes: []"
    item.review_subject_digest = "subject-v1"
    item.review_obligations = build_review_obligations(item)
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump({
        "full_review_completed": True,
        "blockers": [],
        "nits": [],
    }))

    with pytest.raises(ValidationError, match="review_protocol"):
        dispatch.submit(
            store, item.id, verdict="pass", report_file=str(path))

    current = store.get_work_item(item.id)
    assert current.review_verdict is None
    assert current.review_report is None
    assert current.review_ledger is None


def test_legacy_review_report_remains_accepted_without_v2_obligations(tmp_path):
    store = _store()
    item = store.create_work_item(
        "ws", "legacy", "legacy", dag_key="legacy-1", worker="alice",
        reviewer="bob", kind=TaskKind.DECOMPOSE,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    item.phase = TaskPhase.REVIEW
    item.deliverable = "nodes: []"
    report = {"full_review_completed": True, "blockers": [], "nits": []}
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump(report))

    dispatch.submit(store, item.id, verdict="pass", report_file=str(path))

    assert store.get_work_item(item.id).review_verdict == "pass"
    assert store.get_work_item(item.id).review_ledger is None


def test_mock_review_cycles_persist_same_ledger_contract_as_real_submit():
    MockStore.reset()
    engine = create_engine("mock", EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"},
    ))
    MockStore.set_kind_delivery("plan", {
        "plan": "# Plan", "project_rules": "## Rules"})
    MockStore.set_review_rejects(1)

    result = run_task(
        engine, TaskKind.PLAN, {"title": "plan"}, "alice",
        reviewers=["bob"], max_revisions=3, poll=lambda: None)

    item = engine.store.get_work_item(result["item_id"])
    assert len(item.review_ledger["cycles"]) == 2
    assert item.review_ledger["cycles"][0]["verdict"] == "reject"
    assert item.review_ledger["cycles"][1]["verdict"] == "pass"
    assert item.review_ledger["blockers"][0]["status"] == "fixed"

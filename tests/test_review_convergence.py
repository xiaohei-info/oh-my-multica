from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import yaml

from omac.cli import exit_codes
from omac.cli.commands import work as work_cmd
from omac.cli.main import main
from omac.core import review_convergence as review_mod
from omac.core.review_convergence import (
    LegacyReviewLedgerUnverifiable,
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
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import dispatch
from omac.pipeline.convergence import ResolutionState, resolve_convergence
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
    blocker_id = ledger["blockers"][0]["blocker_id"]
    second["prior_blocker_results"] = [{
        "blocker_id": blocker_id,
        "status": "fixed",
        "evidence": "root-a is fixed",
    }]

    ledger = advance_review_ledger(
        ledger, second, verdict="reject", subject_digest="same", round_index=2)

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
    prior_blocker_id = None
    for round_index, root in enumerate(("root-a", "root-b"), start=1):
        report = _report(
            obligations,
            prior=([] if prior_blocker_id is None else [{
                "blocker_id": prior_blocker_id,
                "status": "fixed",
                "evidence": "the prior root is fixed",
            }]),
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
        prior_blocker_id = next(
            blocker["blocker_id"] for blocker in ledger["blockers"]
            if blocker["status"] == "open"
        )

    state = review_state(ledger)
    assert state == {
        "mode": "normal",
        "reason": None,
        "cycle_count": 2,
        "open_blocker_count": 1,
        "open_blocker_ids": [prior_blocker_id],
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


def _production_multi_blocker_ledger(
    classifications_by_round,
    *,
    obligation_ids=(
        "dimension:structure",
        "dimension:execution",
        "dimension:ownership",
    ),
):
    item = _item()
    obligations = build_review_obligations(item)
    roots = ("root-a", "root-b", "root-c")
    ledger = None
    for round_index, classifications in enumerate(
        classifications_by_round, start=1,
    ):
        prior = []
        if ledger is not None:
            records = {
                record["root_cause_key"]: record
                for record in ledger["blockers"]
            }
            prior = [
                {
                    "blocker_id": records[root]["blocker_id"],
                    "status": classification,
                    "evidence": f"round {round_index} still shows {root}",
                }
                for root, classification in zip(roots, classifications)
            ]
        blockers = [
            {
                "root_cause_key": root,
                "obligation_id": obligation_id,
                "classification": classification,
                "summary": f"{root} remains open",
                "evidence": f"round {round_index} evidence for {root}",
                "required_fix": f"fix {root}",
            }
            for root, obligation_id, classification in zip(
                roots, obligation_ids, classifications,
            )
        ]
        report = _report(
            obligations,
            prior=prior,
            failed=set(obligation_ids),
            blockers=blockers,
        )
        item.review_ledger = ledger
        item.review_obligations = obligations
        assert validate_convergence_review(item, "reject", report) == []
        ledger = advance_review_ledger(
            ledger,
            report,
            verdict="reject",
            subject_digest=f"v{round_index}",
            round_index=round_index,
        )
    return ledger


def test_writer_and_validator_accept_mixed_unchanged_and_deeper_blockers():
    ledger = _production_multi_blocker_ledger([
        ("new", "new", "new"),
        ("unchanged", "deeper", "deeper"),
        ("unchanged", "deeper", "deeper"),
    ], obligation_ids=("dimension:structure",) * 3)

    assert validate_review_ledger(ledger, expected_round=3) is ledger
    decision = review_convergence_decision(ledger)
    assert decision["reason_code"] == "review-convergence-stalled"
    assert decision["unchanged_blocker_ids"] == [
        next(
            blocker["blocker_id"] for blocker in ledger["blockers"]
            if blocker["root_cause_key"] == "root-a"
        )
    ]


def test_obligation_id_cannot_override_cycle_blocker_facts():
    ledger = _production_multi_blocker_ledger([
        ("new", "new", "new"),
        ("deeper", "deeper", "deeper"),
        ("deeper", "deeper", "deeper"),
    ])
    assert validate_review_ledger(ledger, expected_round=3) is ledger
    assert review_convergence_decision(ledger)["reason_code"] == (
        "review-convergence-scope-expanding"
    )
    for index, blocker in enumerate(ledger["blockers"]):
        blocker["obligation_id"] = f"acceptance:forged-{index}"

    with pytest.raises(ValueError, match="obligation_id"):
        validate_review_ledger(ledger, expected_round=3)
    with pytest.raises(ValueError, match="obligation_id"):
        review_convergence_decision(ledger)


def test_legacy_cycle_without_blocker_facts_fails_closed():
    ledger = _stalled_canonical_ledger(with_facts=False)

    with pytest.raises(ValueError, match="blocker facts"):
        validate_review_ledger(ledger, expected_round=3)
    with pytest.raises(ValueError, match="blocker facts"):
        review_convergence_decision(ledger)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda ledger: ledger["cycles"][0].__setitem__(
            "prior_open_blocker_ids", "BLK-legacy"),
        lambda ledger: ledger["cycles"][0].__setitem__(
            "blocker_facts_schema", "wrong/v0"),
        lambda ledger: ledger["blockers"][0].__setitem__("root_cause_key", ""),
        lambda ledger: ledger["cycles"][-1]["open_blocker_ids"].append(
            ledger["cycles"][-1]["open_blocker_ids"][0]),
    ],
    ids=[
        "damaged-cycle-field",
        "wrong-blocker-facts-schema",
        "empty-summary-root-cause",
        "duplicate-open-blocker-id",
    ],
)
def test_corrupt_legacy_ledger_is_invalid(
    aiteam_849_legacy_snapshot, corrupt,
):
    ledger = deepcopy(aiteam_849_legacy_snapshot["work_item"]["review_ledger"])
    corrupt(ledger)

    with pytest.raises(ValueError) as exc_info:
        validate_review_ledger(ledger, expected_round=3)
    assert not isinstance(exc_info.value, LegacyReviewLedgerUnverifiable)

    resolution = resolve_convergence(_item(ledger=ledger), expected_round=3)
    assert resolution.state is ResolutionState.INVALID


@pytest.mark.parametrize("cycle_count", [1, 2])
def test_legacy_cycles_before_decision_boundary_keep_fast_path(cycle_count):
    ledger = _stalled_canonical_ledger(with_facts=False)
    ledger["cycles"] = ledger["cycles"][:cycle_count]

    resolution = resolve_convergence(_item(ledger=ledger))

    assert resolution.state is ResolutionState.VALID
    assert resolution.convergence is None


def test_new_schema_resolves_without_legacy_decision():
    ledger = _stalled_canonical_ledger(with_facts=True)
    item = _item(ledger=ledger)
    item.id = "item-1"
    item.review_verdict = "reject"

    resolution = resolve_convergence(item)

    assert resolution.state is ResolutionState.NEEDS_DECISION
    assert resolution.convergence["reason_code"] == "review-convergence-stalled"


def _with_cycle_blocker_facts(ledger):
    records = {
        record["blocker_id"]: record for record in ledger["blockers"]
    }
    current = {}
    prior_open_ids = set()
    for cycle in ledger["cycles"]:
        open_ids = set(cycle["open_blocker_ids"])
        facts = []
        for blocker_id in sorted(prior_open_ids - open_ids):
            previous = current[blocker_id]
            facts.append({
                field: previous[field]
                for field in review_mod._BLOCKER_FACT_FIELDS[:6]
            } | {
                "status": "fixed",
                "classification": "fixed",
                "last_evidence": f"fixed in round {cycle['round']}",
            })
        for blocker_id in sorted(open_ids):
            source = records.setdefault(blocker_id, {
                "blocker_id": blocker_id,
                "root_cause_key": f"root-{blocker_id.removeprefix('BLK-')}",
                "obligation_id": "dimension:structure",
                "status": "open",
                "classification": "new",
            })
            previous = current.get(blocker_id)
            if previous is None:
                classification = "new"
            elif previous["status"] == "fixed":
                classification = "regressed"
            else:
                classification = (
                    "unchanged"
                    if source.get("classification") == "unchanged"
                    else "deeper"
                )
            fact = {
                "blocker_id": blocker_id,
                "root_cause_key": source["root_cause_key"],
                "obligation_id": source["obligation_id"],
                "summary": source.get("summary", blocker_id),
                "evidence": source.get("evidence", f"evidence for {blocker_id}"),
                "required_fix": source.get("required_fix", f"fix {blocker_id}"),
                "status": "open",
                "classification": classification,
            }
            if previous is not None and previous["status"] == "open":
                fact["last_evidence"] = f"still open in round {cycle['round']}"
            facts.append(fact)
        cycle["blocker_facts_schema"] = (
            review_mod.REVIEW_CYCLE_BLOCKER_FACTS_SCHEMA
        )
        cycle["blocker_facts"] = facts
        cycle["prior_open_blocker_ids"] = sorted(prior_open_ids)
        cycle["open_blocker_ids"] = sorted(open_ids)
        cycle["reported_blocker_ids"] = sorted(open_ids)
        cycle["new_count"] = sum(
            fact["classification"] == "new" for fact in facts)
        cycle["fixed_count"] = sum(
            fact["status"] == "fixed" for fact in facts)
        cycle["regressed_count"] = sum(
            fact["classification"] == "regressed" for fact in facts)
        cycle["unchanged_count"] = sum(
            fact["classification"] == "unchanged" for fact in facts)
        cycle["open_count"] = len(open_ids)
        cycle["obligation_results"] = {
            fact["obligation_id"]: "fail"
            for fact in facts if fact["status"] == "open"
        }
        current_ledger = {
            "schema": "omac.review-ledger/v1",
            "cycles": ledger["cycles"][:cycle["round"]],
            "blockers": [],
        }
        current = {
            blocker["blocker_id"]: blocker
            for blocker in review_mod._canonical_cycle_projection(
                current_ledger["cycles"])
        }
        prior_open_ids = open_ids
    ledger["blockers"] = sorted(current.values(), key=lambda item: item["blocker_id"])
    return ledger


def _decision_ledger(
    open_counts, *, new_counts=None, obligation_ids=("dimension:structure",),
    first_seen_rounds=None, classifications=None,
):
    del new_counts, first_seen_rounds
    final_classifications = classifications or ["unchanged"]
    max_open_count = max(open_counts)
    blockers = [
        {
            "blocker_id": f"BLK-{index}",
            "root_cause_key": f"root-{index}",
            "obligation_id": obligation_ids[(index - 1) % len(obligation_ids)],
            "status": "open",
            "classification": final_classifications[
                (index - 1) % len(final_classifications)
            ],
            "first_seen_round": 1,
            "last_seen_round": len(open_counts),
            "seen_count": len(open_counts),
        }
        for index in range(1, max_open_count + 1)
    ]
    cycles = []
    previous_ids = set()
    next_id = 1
    active_ids = set()
    for round_index, open_count in enumerate(open_counts, start=1):
        while len(active_ids) < open_count:
            active_ids.add(f"BLK-{next_id}")
            next_id += 1
        while len(active_ids) > open_count:
            active_ids.remove(sorted(active_ids)[-1])
        cycles.append({
            "round": round_index,
            "new_count": len(active_ids - previous_ids),
            "fixed_count": len(previous_ids - active_ids),
            "regressed_count": 0,
            "unchanged_count": 0,
            "open_count": open_count,
            "prior_open_blocker_ids": sorted(previous_ids),
            "open_blocker_ids": sorted(active_ids),
            "reported_blocker_ids": sorted(active_ids),
        })
        previous_ids = set(active_ids)
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": cycles,
        "blockers": blockers,
    }
    return _with_cycle_blocker_facts(ledger)


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
    assert decision["late_root_cause_keys"] == ["root-5"]


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
    return _with_cycle_blocker_facts({
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": 1,
                "new_count": 1,
                "fixed_count": 0,
                "regressed_count": 0,
                "unchanged_count": 0,
                "open_count": 1,
                "prior_open_blocker_ids": [],
                "open_blocker_ids": ["BLK-core"],
                "reported_blocker_ids": ["BLK-core"],
            },
            {
                "round": 2,
                "new_count": 1,
                "fixed_count": 0,
                "regressed_count": 0,
                "unchanged_count": 1,
                "open_count": 2,
                "prior_open_blocker_ids": ["BLK-core"],
                "open_blocker_ids": ["BLK-core", "BLK-interleaved"],
                "reported_blocker_ids": ["BLK-core", "BLK-interleaved"],
            },
            {
                "round": 3,
                "new_count": 0,
                "fixed_count": 1,
                "regressed_count": 0,
                "unchanged_count": 1,
                "open_count": 1,
                "prior_open_blocker_ids": ["BLK-core", "BLK-interleaved"],
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
    })


def _stalled_canonical_ledger(*, with_facts=True):
    blocker_id = "BLK-core"
    ledger = {
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": round_index,
                "new_count": 1 if round_index == 1 else 0,
                "fixed_count": 0,
                "regressed_count": 0,
                "unchanged_count": 0 if round_index == 1 else 1,
                "open_count": 1,
                "prior_open_blocker_ids": (
                    [] if round_index == 1 else [blocker_id]
                ),
                "open_blocker_ids": [blocker_id],
                "reported_blocker_ids": [blocker_id],
            }
            for round_index in range(1, 4)
        ],
        "blockers": [{
            "blocker_id": blocker_id,
            "root_cause_key": "root-core",
            "obligation_id": "dimension:structure",
            "status": "open",
            "classification": "unchanged",
            "first_seen_round": 1,
            "last_seen_round": 3,
            "seen_count": 3,
        }],
    }
    return _with_cycle_blocker_facts(ledger) if with_facts else ledger


def _late_canonical_ledger():
    blocker_id = "BLK-late"
    return _with_cycle_blocker_facts({
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": round_index,
                "new_count": 0,
                "fixed_count": 0,
                "regressed_count": 0,
                "unchanged_count": 0,
                "open_count": 0,
                "prior_open_blocker_ids": [],
                "open_blocker_ids": [],
                "reported_blocker_ids": [],
            }
            for round_index in range(1, 6)
        ] + [{
            "round": 6,
            "new_count": 1,
            "fixed_count": 0,
            "regressed_count": 0,
            "unchanged_count": 0,
            "open_count": 1,
            "prior_open_blocker_ids": [],
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
    })


def _closed_reopened_canonical_ledger():
    blocker_id = "BLK-reopened"
    return _with_cycle_blocker_facts({
        "schema": "omac.review-ledger/v1",
        "cycles": [
            {
                "round": 1,
                "new_count": 1,
                "fixed_count": 0,
                "regressed_count": 0,
                "unchanged_count": 0,
                "open_count": 1,
                "prior_open_blocker_ids": [],
                "open_blocker_ids": [blocker_id],
                "reported_blocker_ids": [blocker_id],
            },
            {
                "round": 2,
                "new_count": 0,
                "fixed_count": 1,
                "regressed_count": 0,
                "unchanged_count": 0,
                "open_count": 0,
                "prior_open_blocker_ids": [blocker_id],
                "open_blocker_ids": [],
                "reported_blocker_ids": [],
            },
            {
                "round": 3,
                "new_count": 0,
                "fixed_count": 0,
                "regressed_count": 1,
                "unchanged_count": 0,
                "open_count": 1,
                "prior_open_blocker_ids": [],
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
    })


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

    with pytest.raises(ValueError, match="open_blocker_ids"):
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

    with pytest.raises(ValueError, match="open_count"):
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


@pytest.mark.parametrize("classification", [None, "fixed", "deeper"])
def test_review_ledger_validation_rejects_missing_or_forged_classification(
    classification,
):
    ledger = _stalled_canonical_ledger()
    ledger["blockers"][0]["classification"] = classification

    with pytest.raises(ValueError, match="classification"):
        validate_review_ledger(ledger, expected_round=3)


def test_review_ledger_validation_rejects_fixed_status_with_open_classification():
    ledger = _stalled_canonical_ledger()
    latest = ledger["cycles"][-1]
    latest.update({
        "fixed_count": 1,
        "unchanged_count": 0,
        "open_count": 0,
        "open_blocker_ids": [],
        "reported_blocker_ids": [],
    })
    ledger["blockers"][0].update({
        "status": "fixed",
        "classification": "unchanged",
    })

    with pytest.raises(ValueError, match="fixed_count"):
        validate_review_ledger(ledger, expected_round=3)


@pytest.mark.parametrize("field", [
    "prior_open_blocker_ids",
    "reported_blocker_ids",
])
def test_review_ledger_validation_requires_exact_cycle_blocker_sets(field):
    ledger = _stalled_canonical_ledger()
    ledger["cycles"][-1][field] = []

    with pytest.raises(ValueError, match=field):
        validate_review_ledger(ledger, expected_round=3)


def test_review_ledger_validation_accepts_canonical_status_classification_pairs():
    ledgers = [
        _stalled_canonical_ledger(),
        _late_canonical_ledger(),
        _closed_reopened_canonical_ledger(),
        _interleaved_canonical_ledger(),
    ]

    for ledger in ledgers:
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


def _legacy_two_cycle_review_case(tmp_path):
    store = _store()
    item = store.create_work_item(
        "ws", "review", "review", dag_key="review-1", worker="alice",
        reviewer="bob", kind=TaskKind.DECOMPOSE,
        initial_status=WorkItemStatus.IN_REVIEW,
    )
    item.phase = TaskPhase.REVIEW
    item.deliverable = "nodes: []"
    item.review_subject_digest = "subject-v3"
    item.review_obligations = build_review_obligations(item)
    ledger = _decision_ledger([1, 1])
    for cycle in ledger["cycles"]:
        cycle.pop("blocker_facts_schema")
        cycle.pop("blocker_facts")
    store.update_work_item_metadata(
        item.id,
        review_ledger=ledger,
        review_bounce=2,
    )
    blocker = ledger["blockers"][0]
    report = _report(
        item.review_obligations,
        prior=[{
            "blocker_id": blocker["blocker_id"],
            "status": "unchanged",
            "evidence": "the same task boundary still cannot close the blocker",
        }],
        failed={blocker["obligation_id"]},
        blockers=[{
            "root_cause_key": blocker["root_cause_key"],
            "obligation_id": blocker["obligation_id"],
            "classification": "unchanged",
            "summary": "the same blocker remains open",
            "evidence": "the current node boundary is still insufficient",
            "required_fix": "split the work into independently reviewable nodes",
        }],
    )
    report_path = tmp_path / "review.yaml"
    report_path.write_text(yaml.safe_dump(report))
    return store, item, report_path


def test_legacy_two_cycle_ledger_requires_stable_decision_before_third_submit(
    tmp_path, monkeypatch,
):
    store, item, report_path = _legacy_two_cycle_review_case(tmp_path)
    before = deepcopy(store.get_work_item(item.id))
    writes = []
    original_update = store.update_work_item_metadata
    original_status = store.update_status

    def record_update(*args, **kwargs):
        writes.append(("metadata", args, kwargs))
        return original_update(*args, **kwargs)

    def record_status(*args, **kwargs):
        writes.append(("status", args, kwargs))
        return original_status(*args, **kwargs)

    monkeypatch.setattr(store, "update_work_item_metadata", record_update)
    monkeypatch.setattr(store, "update_status", record_status)

    decisions = []
    for _ in range(2):
        with pytest.raises(NeedsDecision) as exc:
            dispatch.submit(
                store,
                item.id,
                verdict="reject",
                report_file=str(report_path),
            )
        decisions.append(exc.value.report)

    assert decisions[0] == decisions[1]
    assert decisions[0]["reason_code"] == (
        "review-convergence-ledger-unverifiable")
    assert decisions[0]["convergence"]["mode"] == (
        "unverifiable-legacy-ledger")
    assert decisions[0]["next_action"].startswith("omac dag amend propose ")
    assert writes == []
    after = store.get_work_item(item.id)
    assert after.review_ledger == before.review_ledger
    assert after.review_report == before.review_report
    assert after.review_verdict == before.review_verdict
    assert after.decision_required == before.decision_required
    assert after.status == before.status


def test_legacy_two_cycle_cli_returns_repeatable_exit_20_without_writes(
    tmp_path, monkeypatch, capsys,
):
    store, item, report_path = _legacy_two_cycle_review_case(tmp_path)
    before = deepcopy(store.get_work_item(item.id))
    writes = []
    monkeypatch.setattr(work_cmd, "_resolve_store", lambda: store)
    for name in ("update_work_item_metadata", "update_status", "assign_work_item"):
        monkeypatch.setattr(
            store, name,
            lambda *_args, _name=name, **_kwargs: writes.append(_name),
        )

    decisions = []
    for _ in range(2):
        assert main([
            "work", "submit", item.id, "--verdict", "reject",
            "--report-file", str(report_path),
        ]) == exit_codes.NEEDS_DECISION
        captured = capsys.readouterr()
        decisions.append(json.loads(captured.out))
        assert "omac dag amend propose" in captured.err

    assert decisions[0] == decisions[1]
    assert decisions[0]["reason_code"] == (
        "review-convergence-ledger-unverifiable")
    assert writes == []
    assert store.get_work_item(item.id) == before


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

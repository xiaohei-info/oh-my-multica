from types import SimpleNamespace

import yaml

from omac.core.review_convergence import (
    REVIEW_PROTOCOL_VERSION,
    advance_review_ledger,
    build_review_obligations,
    review_state,
    validate_convergence_review,
)
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.pipeline import dispatch
from omac.engines import create_engine
from omac.pipeline.tasks import run_task


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
    assert review_state(ledger)["mode"] == "convergence-audit"


def test_two_rounds_with_new_blockers_trigger_convergence_audit():
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
    assert state["mode"] == "convergence-audit"
    assert state["reason"] == "new blockers appeared in two consecutive review cycles"


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

    assert output["context"]["review_state"]["mode"] == "convergence-audit"
    assert output["context"]["required_closures"] == [{
        "blocker_id": "BLK-old",
        "obligation_id": "dimension:evidence",
        "root_cause_key": "release-trust",
        "summary": "release trust is incomplete",
        "required_fix": "verify the signed handoff",
    }]


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

"""Narrow corrections never authorize generic downstream recovery exceptions."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
import yaml

from omac.core.manifest import load_manifest, save_manifest, _dump_contract
from omac.core.amendment import (
    validate_proposal,
    _minimal_rerun,
    build_reviewed_amendment,
    apply_amendment,
)
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines.models import AgentRunObservation, VerificationAttachmentObservation
from omac.errors import ValidationError
from test_amendment import _manifest, _engine


@pytest.fixture
def case(tmp_path):
    path = _manifest(tmp_path)
    manifest = load_manifest(str(path))
    target = manifest.nodes["bootstrap"]
    target.status = "todo"
    target.contract.non_goals = ["Retain old-purpose rejection."]
    eng = _engine()
    item = eng.store.create_work_item(
        "ws", "target", "target", "bootstrap", "alice", reviewer="bob"
    )
    eng.store.set_node_contract(item.id, target.contract)
    child = eng.store.create_work_item(
        "ws", "child", "child", "closeout", "charlie", reviewer="bob"
    )
    manifest.nodes["closeout"].work_item_id = child.id
    source = eng.store.create_work_item(
        "ws",
        "reject",
        "reject",
        "amend-old",
        "alice",
        reviewer="bob",
        kind=TaskKind.AMENDMENT,
    )
    report_body = yaml.safe_dump(
        {"blockers": [{"required_fix": "target-only correction"}]}
    ).encode()
    report_ref = {
        "attachment_id": "reject-report",
        "comment_id": "reject-comment",
        "sha256": hashlib.sha256(report_body).hexdigest(),
    }
    ledger_body = yaml.safe_dump(
        {"cycles": [{"subject_digest": "original-reject", "verdict": "reject"}]}
    ).encode()
    ledger_ref = {
        "attachment_id": "reject-ledger",
        "comment_id": "ledger-comment",
        "sha256": hashlib.sha256(ledger_body).hexdigest(),
    }
    replacement = _dump_contract(target.contract)
    replacement["non_goals"][0] = replacement["non_goals"][0].replace(
        "old-purpose", "new-purpose"
    )
    source.deliverable = yaml.safe_dump(
        {
            "schema": "omac.dag-amendment/v1",
            "reason": "original",
            "operations": [
                {"op": "update", "node": "bootstrap", "set": {"contract": replacement}}
            ],
        }
    )
    source.review_ledger = yaml.safe_load(ledger_body)
    source.review_ledger_ref = ledger_ref
    context = {
        "verdict": "reject",
        "subject_digest": "original-reject",
        "report_ref": report_ref,
        "ledger_ref": ledger_ref,
    }
    eng.store.recover_review_rework_context = lambda _id: deepcopy(context)
    blobs = {"reject-report": report_body, "reject-ledger": ledger_body}

    def observe(_id, ref):
        data = blobs[ref["attachment_id"]]
        return VerificationAttachmentObservation(
            ref["attachment_id"],
            ref["comment_id"],
            hashlib.sha256(data).hexdigest(),
            data,
            "mock-agent-bob",
            "agent",
            "review-run",
            "2026-09-30T01:05:00Z",
        )

    eng.store.observe_verification_attachment = observe
    authority = b"Canonical purpose is new-purpose. Preserve rejection coverage."
    eng.store.read_immutable_artifact = lambda _url: authority
    eng.runtime.list_runs = lambda item_id: (
        [
            AgentRunObservation(
                "review-run",
                "direct",
                "completed",
                agent_id="mock-agent-bob",
                trigger_kind="issue_assignment",
            )
        ]
        if item_id == source.id
        else []
    )
    save_manifest(manifest, str(path))
    return SimpleNamespace(
        path=path,
        manifest=manifest,
        eng=eng,
        item=item,
        child=child,
        source=source,
        blobs=blobs,
        context=context,
        authority=authority,
    )


def prepare(c):
    from omac.core.literal_correction import prepare_literal_correction

    return prepare_literal_correction(
        c.manifest,
        c.eng.store,
        c.eng.runtime,
        node_id="bootstrap",
        index=0,
        old_token="old-purpose",
        new_token="new-purpose",
        authority_url="https://github.com/acme/repo/blob/"
        + "a" * 40
        + "/docs/design.md",
        authority_sha256=hashlib.sha256(c.authority).hexdigest(),
        authority_quote="Canonical purpose is new-purpose.",
        source_issue_id=c.source.id,
        reason="Correct proven prose literal; preserve downstream state",
    )


def test_prepare_is_readonly_and_only_explicit_operation_is_target_only(case):
    before = deepcopy(case.eng.store.get_work_item(case.item.id))
    raw = case.path.read_bytes()
    proposal = prepare(case)
    assert case.eng.store.get_work_item(case.item.id) == before
    assert case.path.read_bytes() == raw
    assert validate_proposal(case.manifest, proposal, {"alice", "bob", "charlie"}) == []
    minimal, derived, immutable = _minimal_rerun(case.manifest, proposal)
    assert minimal == {"review": [], "authoring": ["bootstrap"], "merging": []}
    assert derived == immutable == []
    generic = deepcopy(proposal)
    generic["operations"][0] = {
        "op": "update",
        "node": "bootstrap",
        "set": proposal["operations"][0]["set"],
    }
    assert _minimal_rerun(case.manifest, generic)[1] == ["closeout"]


@pytest.mark.parametrize(
    "change",
    ["scope", "quality", "two-strings", "old-contract", "token", "authority-url"],
)
def test_non_literal_contract_changes_are_not_admitted(case, change):
    proposal = prepare(case)
    op = proposal["operations"][0]
    if change == "scope":
        op["set"]["contract"]["scope_paths"] = ["other/**"]
    if change == "quality":
        op["set"]["contract"]["verification_commands"] = ["true"]
    if change == "two-strings":
        op["set"]["contract"]["objective"] = "different"
    if change == "old-contract":
        op["correction"]["old_contract"]["objective"] = "forged"
    if change == "token":
        op["correction"]["new_token"] = "allow all"
    if change == "authority-url":
        op["correction"]["authority"]["url"] = (
            "https://github.com/acme/repo/blob/main/docs/design.md"
        )
    assert validate_proposal(case.manifest, proposal, {"alice", "bob", "charlie"})


@pytest.mark.parametrize(
    "change",
    [
        "downstream-status",
        "downstream-budget",
        "downstream-run",
        "authority",
        "reject",
        "target-active",
    ],
)
def test_freeze_drift_fails_before_review_or_any_apply_write(case, change):
    from omac.core.literal_correction import verify_literal_correction

    proposal = prepare(case)
    if change == "downstream-status":
        case.manifest.nodes["closeout"].status = "in_progress"
    if change == "downstream-budget":
        case.child.bounces.worker += 1
    if change == "downstream-run":
        case.eng.runtime.list_runs = lambda _id: [
            AgentRunObservation("new", "direct", "completed")
        ]
    if change == "authority":
        case.eng.store.read_immutable_artifact = lambda _url: b"changed"
    if change == "reject":
        case.context["subject_digest"] = "changed"
    if change == "target-active":
        case.item.platform_assignee_id = "worker"
    with pytest.raises(ValidationError):
        verify_literal_correction(
            case.manifest, proposal, case.eng.store, case.eng.runtime
        )


def test_a_real_new_independent_review_is_required(case):
    proposal = prepare(case)
    with pytest.raises(ValidationError):
        build_reviewed_amendment(
            case.manifest,
            proposal,
            case.eng.store,
            issue_id=case.source.id,
            reviewer_verdict="pass",
            agent_pool={"alice", "bob", "charlie"},
            runtime=case.eng.runtime,
        )


def approve(c, proposal):
    from omac.core.literal_correction import literal_review_obligation
    from omac.core.review_convergence import REVIEW_PROTOCOL_VERSION

    review = c.eng.store.create_work_item(
        "ws",
        "new review",
        "new review",
        "amend-new",
        "alice",
        reviewer="bob",
        kind=TaskKind.AMENDMENT,
    )
    obligation = literal_review_obligation(proposal)
    body = {
        "review_protocol": REVIEW_PROTOCOL_VERSION,
        "full_review_completed": True,
        "obligation_results": [
            {
                "obligation_id": obligation["obligation_id"],
                "status": "pass",
                "evidence": "Independent authority/contract/downstream non-propagation checks passed",
            }
        ],
    }
    c.eng.store.update_work_item_metadata(
        review.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
        review_subject_digest="new-independent-subject",
        deliverable=yaml.safe_dump(proposal),
        review_obligations=[obligation],
        review_report=body,
        review_report_source=yaml.safe_dump(body),
    )
    review = c.eng.store.get_work_item(review.id)
    c.blobs[review.review_report_ref["attachment_id"]] = yaml.safe_dump(body).encode()
    c.eng.runtime.list_runs = lambda item_id: (
        [
            AgentRunObservation(
                "review-run",
                "direct",
                "completed",
                agent_id="mock-agent-bob",
                trigger_kind="issue_assignment",
            )
        ]
        if item_id in (review.id, c.source.id)
        else []
    )
    return build_reviewed_amendment(
        c.manifest,
        proposal,
        c.eng.store,
        issue_id=review.id,
        reviewer_verdict="pass",
        agent_pool={"alice", "bob", "charlie"},
        runtime=c.eng.runtime,
    )


def test_reviewed_correction_applies_only_target_once_without_budget_or_reject_loss(
    case,
):
    case.child.bounces.worker = 17
    case.child.bounces.review = 9
    proposal = prepare(case)
    reviewed = approve(case, proposal)
    child_before = deepcopy(case.child)
    source_before = deepcopy(case.source)
    target_bounces = deepcopy(case.item.bounces)
    child_node_before = deepcopy(case.manifest.nodes["closeout"])
    result = apply_amendment(
        str(case.path),
        reviewed,
        case.eng.store,
        {"alice", "bob", "charlie"},
        runtime=case.eng.runtime,
    )
    assert result["minimal_rerun"] == {
        "review": [],
        "authoring": ["bootstrap"],
        "merging": [],
    }
    assert case.eng.store.get_work_item(case.child.id) == child_before
    assert case.eng.store.get_work_item(case.source.id) == source_before
    assert case.item.bounces == target_bounces
    assert case.eng.store.get_work_item(case.item.id).contract.non_goals == [
        "Retain new-purpose rejection."
    ]
    assert load_manifest(str(case.path)).nodes["closeout"] == child_node_before
    again = apply_amendment(
        str(case.path),
        reviewed,
        case.eng.store,
        {"alice", "bob", "charlie"},
        runtime=case.eng.runtime,
    )
    assert again["sync"]["already_complete"] == ["bootstrap"]
    assert case.eng.store.get_work_item(case.child.id) == child_before
    assert case.eng.store.assign_log == []


@pytest.mark.parametrize(
    "change", ["runtime", "authority", "review-report", "source-reject", "identity"]
)
def test_reviewed_frozen_change_is_rejected_before_manifest_or_store_write(
    case, monkeypatch, change
):
    reviewed = approve(case, prepare(case))
    if change == "runtime":
        case.child.bounces.review += 1
    if change == "authority":
        case.eng.store.read_immutable_artifact = lambda _url: b"changed"
    if change == "review-report":
        case.eng.store.get_work_item(
            reviewed["review"]["issue_id"]
        ).review_subject_digest = "other-review"
    if change == "source-reject":
        case.context["subject_digest"] = "other-reject"
    if change == "identity":
        reviewed["review"]["literal_review_binding"]["review_subject_digest"] = "forged"
    before = case.path.read_bytes()
    for name in (
        "update_work_item_metadata",
        "set_node_contract",
        "reset_review",
        "update_status",
        "assign_work_item",
    ):
        monkeypatch.setattr(
            case.eng.store,
            name,
            lambda *_args, **_kwargs: pytest.fail("CAS rejection must not write"),
        )
    with pytest.raises(ValidationError):
        apply_amendment(
            str(case.path),
            reviewed,
            case.eng.store,
            {"alice", "bob", "charlie"},
            runtime=case.eng.runtime,
        )
    assert case.path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    ["generic-pass", "self-review", "wrong-run", "tampered-bytes", "failed-obligation"],
)
def test_new_review_requires_exact_independent_proof(case, change):
    proposal = prepare(case)
    reviewed = approve(case, proposal)
    item = case.eng.store.get_work_item(reviewed["review"]["issue_id"])
    if change == "generic-pass":
        item.review_obligations = []
    if change == "self-review":
        item.reviewer = item.worker
    if change == "wrong-run":
        case.eng.runtime.list_runs = lambda _id: []
    if change == "tampered-bytes":
        case.blobs[item.review_report_ref["attachment_id"]] = b"changed"
    if change == "failed-obligation":
        item.review_report["obligation_results"][0]["status"] = "fail"
    with pytest.raises(ValidationError):
        build_reviewed_amendment(
            case.manifest,
            proposal,
            case.eng.store,
            issue_id=item.id,
            reviewer_verdict="pass",
            agent_pool={"alice", "bob", "charlie"},
            runtime=case.eng.runtime,
        )


def test_cli_preparation_is_readonly_and_outputs_unapproved_proposal(
    case, monkeypatch, capsys
):
    import omac.cli.commands.dag as dag_cmd
    from omac.cli.main import main

    monkeypatch.setattr(
        dag_cmd, "_assemble_engine", lambda _args: (case.eng, case.eng.store.config)
    )
    monkeypatch.setattr(dag_cmd, "_load_config_for_manifest", lambda _path: {})
    before = case.path.read_bytes()
    child_before = deepcopy(case.child)
    result = main(
        [
            "dag",
            "amend",
            "prepare-literal-correction",
            str(case.path),
            "bootstrap",
            "--index",
            "0",
            "--old-token",
            "old-purpose",
            "--new-token",
            "new-purpose",
            "--authority-url",
            "https://github.com/acme/repo/blob/" + "a" * 40 + "/docs/design.md",
            "--authority-sha256",
            hashlib.sha256(case.authority).hexdigest(),
            "--authority-quote",
            "Canonical purpose is new-purpose.",
            "--source-reject-issue-id",
            case.source.id,
            "--reason",
            "Correct exact prose token",
        ]
    )
    assert result == 0
    proposal = json.loads(capsys.readouterr().out)
    assert proposal["operations"][0]["op"] == "correct-contract-literal"
    assert "review" not in proposal and "human_confirmation" not in proposal
    assert case.path.read_bytes() == before and case.child == child_before
    assert case.eng.store.assign_log == []


@pytest.mark.parametrize(
    "change",
    [
        "existing-budget",
        "existing-run",
        "unknown-run",
        "wrong-reject-target",
        "authority-quote",
    ],
)
def test_preparation_fails_closed_at_authority_and_activity_boundaries(case, change):
    if change == "existing-budget":
        case.item.bounces.worker = 1
    if change == "existing-run":
        case.eng.runtime.list_runs = lambda _id: [
            AgentRunObservation("old", "direct", "completed")
        ]
    if change == "unknown-run":
        case.eng.runtime.list_runs = lambda _id: [
            AgentRunObservation("unknown", "direct", "unknown")
        ]
    if change == "wrong-reject-target":
        proposal = yaml.safe_load(case.source.deliverable)
        proposal["operations"][0]["node"] = "closeout"
        case.source.deliverable = yaml.safe_dump(proposal)
    if change == "authority-quote":
        case.eng.store.read_immutable_artifact = lambda _url: b"Other purpose"
    with pytest.raises(ValidationError):
        prepare(case)


def test_unknown_extra_operation_cannot_borrow_target_only_policy(case):
    proposal = prepare(case)
    proposal["operations"].append(
        {"op": "update", "node": "closeout", "set": {"description": "changed"}}
    )
    assert validate_proposal(case.manifest, proposal, {"alice", "bob", "charlie"})


def test_ordinary_amendment_review_receives_specific_non_propagation_obligation(case):
    from omac.core.literal_correction import literal_review_obligation
    from omac.core.review_convergence import build_review_obligations

    proposal = prepare(case)
    item = deepcopy(case.source)
    item.deliverable = yaml.safe_dump(proposal)
    obligations = build_review_obligations(item, amendment_manifest=case.manifest)
    assert literal_review_obligation(proposal) in obligations


def historical_blocker(c):
    from omac.core.taskmeta import review_context_binding

    run = AgentRunObservation(
        "discovery",
        "direct",
        "completed",
        agent_id="mock-agent-alice",
        trigger_kind="issue_assignment",
        created_at="2026-09-30T01:00:00Z",
        updated_at="2026-09-30T01:03:00Z",
    )
    prior = c.eng.runtime.list_runs
    c.eng.runtime.list_runs = lambda item_id: (
        [run] if item_id == c.item.id else prior(item_id)
    )
    binding = review_context_binding(c.item)
    blocker = {
        "schema": "omac.worker-blocker/v2",
        "reason_code": "owner-decision-required",
        "issue_id": c.item.id,
        "review_context_binding": binding,
        "handoff_generation": "historical-handoff",
        "worker": "alice",
        "run_id": run.id,
        "contract_ref": "non_goals",
        "summary": "old-purpose differs from canonical new-purpose",
        "decision_needed": "Confirm canonical token",
        "evidence": [
            {"ref": "docs/design.md", "observation": "Canonical purpose is new-purpose"}
        ],
    }
    decision = {
        "schema": "omac.decision-required/v1",
        "reason_code": "worker-decision-required",
        "kind": "develop",
        "phase": "authoring",
        "gate": "worker",
        "resume_issue_id": c.item.id,
        "review_context_binding": binding,
        "blocker": blocker,
    }
    output = {
        "ok": False,
        "exit_code": 20,
        "terminal": True,
        "decision_required": decision,
        "next_action": "stop",
    }
    messages = [
        {
            "seq": 1,
            "type": "tool_use",
            "tool": "exec_command",
            "call_id": "block",
            "issue_id": c.item.id,
            "task_id": run.id,
            "created_at": "2026-09-30T01:01:00Z",
            "input": {
                "command": f"/usr/bin/zsh -lc 'OMAC_ENGINE=multica omac work block {c.item.id} --report-file blocker.yaml'"
            },
        },
        {
            "seq": 2,
            "type": "tool_result",
            "tool": "exec_command",
            "call_id": "block",
            "issue_id": c.item.id,
            "task_id": run.id,
            "created_at": "2026-09-30T01:02:00Z",
            "output_truncated": False,
            "output": json.dumps(output),
        },
    ]
    c.eng.runtime.read_run_messages = lambda _item_id, _run_id: deepcopy(messages)
    return run, messages, output


def test_only_proven_completed_blocker_discovery_is_eligible_without_erasing_history(
    case,
):
    run, messages, _ = historical_blocker(case)
    original = deepcopy(case.item)
    proposal = prepare(case)
    snapshot = proposal["operations"][0]["correction"]["snapshots"]["bootstrap"]
    assert snapshot["runs"][0]["id"] == run.id
    assert snapshot["historical_blocker"]["receipt"]["exit_code"] == 20
    assert snapshot["historical_blocker"]["messages_sha256"]
    assert case.item == original and case.eng.runtime.list_runs(case.item.id) == [run]


@pytest.mark.parametrize(
    "change",
    [
        "active",
        "unknown",
        "failed",
        "foreign-agent",
        "foreign-worker",
        "foreign-issue",
        "truncated",
        "fake-command",
        "wrong-contract",
        "wrong-run",
        "not-terminal",
        "prior-submit",
        "after-stop",
        "verification",
        "artifacts",
        "deliverable",
        "extra-run",
        "wrong-topic",
        "outside-window",
        "wrong-call",
    ],
)
def test_historical_discovery_exception_never_accepts_activity_delivery_or_fake_receipts(
    case, change
):
    from dataclasses import replace

    run, messages, output = historical_blocker(case)
    if change in ("active", "unknown", "failed"):
        run = replace(
            run,
            status={"active": "running", "unknown": "unknown", "failed": "failed"}[
                change
            ],
        )
    if change == "foreign-agent":
        run = replace(run, agent_id="other")
    if change == "foreign-worker":
        case.item.worker = "charlie"
    if change == "foreign-issue":
        messages[1]["issue_id"] = "other"
    if change == "truncated":
        messages[1]["output_truncated"] = True
    if change == "fake-command":
        messages[0]["input"]["command"] = "echo claimed blocker"
    if change == "wrong-contract":
        output["decision_required"]["blocker"]["review_context_binding"][
            "contract_sha256"
        ] = "0" * 64
    if change == "wrong-run":
        output["decision_required"]["blocker"]["run_id"] = "old"
    if change == "not-terminal":
        output["terminal"] = False
    if change == "prior-submit":
        messages.insert(
            0,
            {
                "seq": 0,
                "type": "tool_use",
                "tool": "exec_command",
                "input": {
                    "command": f"omac work submit {case.item.id} --pr-url https://example.test/pr/1"
                },
            },
        )
    if change == "after-stop":
        messages.append(
            {
                "seq": 3,
                "type": "tool_use",
                "tool": "exec_command",
                "input": {"command": "git status"},
            }
        )
    if change == "verification":
        case.item.verification_ref = {"attachment_id": "old-verification"}
    if change == "artifacts":
        case.item.artifacts = {"pr_url": "https://example.test/pr/1"}
    if change == "deliverable":
        case.item.deliverable = "produced result"
    if change == "wrong-topic":
        output["decision_required"]["blocker"]["summary"] = "Unrelated owner decision"
        messages[1]["output"] = json.dumps(output)
    if change == "outside-window":
        messages[1]["created_at"] = "2026-09-30T02:00:00Z"
    if change == "wrong-call":
        messages[1]["call_id"] = "foreign"
    if change == "extra-run":
        prior = case.eng.runtime.list_runs
        case.eng.runtime.list_runs = lambda item_id: (
            [run, replace(run, id="another")]
            if item_id == case.item.id
            else prior(item_id)
        )
    elif change in ("active", "unknown", "failed", "foreign-agent"):
        prior = case.eng.runtime.list_runs
        case.eng.runtime.list_runs = lambda item_id: (
            [run] if item_id == case.item.id else prior(item_id)
        )
    if change in ("wrong-contract", "wrong-run", "not-terminal"):
        messages[1]["output"] = json.dumps(output)
    with pytest.raises(ValidationError):
        prepare(case)


def test_historical_receipt_drift_invalidates_frozen_preparation(case):
    from omac.core.literal_correction import verify_literal_correction

    _, messages, _ = historical_blocker(case)
    proposal = prepare(case)
    messages[0]["input"]["command"] += " "
    with pytest.raises(ValidationError, match="snapshot changed"):
        verify_literal_correction(
            case.manifest, proposal, case.eng.store, case.eng.runtime
        )


def test_nonzero_budget_categories_cannot_cancel_each_other(case):
    historical_blocker(case)
    case.item.bounces.worker = 1
    case.item.bounces.review = -1
    with pytest.raises(ValidationError):
        prepare(case)

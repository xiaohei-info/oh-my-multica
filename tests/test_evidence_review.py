"""One precisely witnessed evidence-only submission can enter independent review."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from omac.core.manifest import Contract, Manifest, Node, save_manifest, load_manifest
from omac.core.review_convergence import advance_review_ledger, build_review_obligations
from omac.core.taskmeta import WorkerHandoffIntent, review_context_binding, TaskPhase
from omac.engines import create_engine
from omac.engines.models import (
    EngineConfig,
    AgentRunObservation,
    VerificationAttachmentObservation,
    WorkItemStatus,
    PullRequestReadiness,
)
from omac.errors import ValidationError, NeedsDecision, PlatformError
from test_review_convergence import _report


def sha(value):
    return hashlib.sha256(value).hexdigest()


def github_response(body):
    payload = {
        "encoding": "base64",
        "content": base64.b64encode(body).decode(),
        "size": len(body),
        "sha": hashlib.sha1(
            b"blob " + str(len(body)).encode() + b"\0" + body
        ).hexdigest(),
    }
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode())


@pytest.fixture
def case(tmp_path):
    eng = create_engine(
        "mock", EngineConfig("mock", "ws", extra={"MOCK_AUTO_COMPLETE": "false"})
    )
    store = eng.store
    store.resolve_agent_id = lambda name: (
        "worker-agent" if name == "worker" else "reviewer-agent"
    )
    item = store.create_work_item(
        "ws", "evidence", "original", "node", "worker", reviewer="reviewer"
    )
    contract = Contract(
        objective="publish evidence", verification_commands=["true"], pr_base="main"
    )
    store.set_node_contract(item.id, contract)
    node = Node(
        id="node",
        worker="worker",
        reviewer="reviewer",
        work_item_id=item.id,
        contract=contract,
        status="blocked",
    )
    manifest = Manifest(meta={}, nodes={"node": node})
    path = tmp_path / "manifest.yaml"
    save_manifest(manifest, str(path))
    head = "a" * 40
    url = f"https://github.com/acme/repo/blob/{'b' * 40}/evidence.json"
    body = b'{"source":"fixture"}'
    verification = {
        "commands": [{"cmd": "true", "exit_code": 0}],
        "coverage": 100,
        "pr_base": "main",
        "integration_gates": [
            {"name": "checks", "commands": [{"cmd": "true", "exit_code": 0}]}
        ],
        "retrievable_artifacts": [{"url": url, "sha256": sha(body)}],
    }
    blobs = {}
    refs = {}

    def attachment(label, data, task=None, created="2026-09-27T06:00:00Z"):
        content = yaml.safe_dump(data).encode() if isinstance(data, dict) else data
        ref = {
            "comment_id": label + "-comment",
            "attachment_id": label,
            "sha256": sha(content),
            "bytes": len(content),
            "filename": label + ".yaml",
        }
        blobs[label] = VerificationAttachmentObservation(
            label,
            label + "-comment",
            sha(content),
            content,
            "reviewer-agent" if label == "reject" else "worker-agent",
            "agent",
            task,
            created,
        )
        refs[label] = ref
        return ref

    old = attachment("baseline", {"old": "evidence"}, "prior-run")
    new = attachment("candidate", verification, "original-run", "2026-09-27T07:06:00Z")
    obligations = build_review_obligations(item)
    blocker = {
        "root_cause_key": "published-evidence",
        "obligation_id": "dimension:evidence",
        "summary": "Publish exact evidence",
        "required_fix": "publish files",
        "evidence": "absent",
    }
    report = _report(obligations, blockers=[blocker], failed=["dimension:evidence"])
    ledger = advance_review_ledger(
        None, report, verdict="reject", subject_digest="rejected-subject", round_index=1
    )
    report_ref = attachment("reject", report)
    ledger_ref = attachment("ledger", ledger)
    store.update_work_item_metadata(
        item.id,
        review_generation="generation",
        review_bounce=2,
        worker_bounce=2,
        artifacts={"pr_url": "https://github.com/acme/repo/pull/1", "head_sha": head},
        verification=verification,
        review_ledger=ledger,
    )
    current = store.get_work_item(item.id)
    current.verification_ref = new
    current.review_ledger_ref = ledger_ref
    historical = WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="worker",
        gate="operator-retry",
        source_review_subject_digest="rejected-subject",
        source_review_round=2,
        source_review_verdict="reject",
        source_review_feedback={
            "schema": "omac.worker-rework-feedback/v1",
            "verdict": "reject",
            "report_ref": report_ref,
            "ledger_ref": ledger_ref,
        },
        review_context_binding=review_context_binding(current),
        target_review_bounce=2,
        generation="historical",
        target_agent_id="worker-agent",
        baseline_direct_run_ids=("prior-run",),
        baseline_verification_attachment_id="baseline",
        baseline_pr_head_sha=head,
        target_run_id="original-run",
        target_worker_bounce=1,
    )
    snapshot = {
        "id": item.id,
        "metadata": {
            "worker_handoff": json.dumps(historical.as_dict()),
            "verification_ref": json.dumps(old),
        },
    }
    record = {
        "type": "message",
        "timestamp": "2026-09-27T07:02:00Z",
        "message": {
            "role": "toolResult",
            "toolCallId": "read-issue",
            "toolName": "bash",
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(snapshot)}],
        },
    }
    witness = tmp_path / "session.jsonl"
    call = {
        "timestamp": "2026-09-27T07:01:59Z",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "toolCall",
                    "id": "read-issue",
                    "name": "bash",
                    "arguments": {
                        "command": f"multica issue get {item.id} --output json"
                    },
                }
            ],
        },
    }
    witness.write_text(json.dumps(call) + "\n" + json.dumps(record) + "\n")
    current.worker_handoff = replace(
        historical,
        generation="forward",
        target_run_id="later-run",
        baseline_verification_attachment_id="candidate",
        target_worker_bounce=2,
    )
    current.status = WorkItemStatus.BLOCKED
    current.reviewer = None
    current.decision_required = {
        "schema": "omac.decision-required/v1",
        "reason_code": "evidence-only-rework-head-policy",
        "resume_issue_id": item.id,
    }
    runs = [
        AgentRunObservation(
            "original-run",
            "direct",
            "completed",
            agent_id="worker-agent",
            created_at="2026-09-27T07:01:00Z",
            updated_at="2026-09-27T07:07:00Z",
            trigger_kind="issue_assignment",
        ),
        AgentRunObservation(
            "later-run",
            "direct",
            "completed",
            agent_id="worker-agent",
            created_at="2026-09-27T07:08:00Z",
            updated_at="2026-09-27T07:10:00Z",
            trigger_kind="issue_assignment",
        ),
    ]
    runtime = Mock()
    runtime.list_runs.return_value = runs
    store.observe_verification_attachment = lambda _, ref: blobs[ref["attachment_id"]]
    store.read_pull_request_readiness = lambda _: PullRequestReadiness(
        False, "OPEN", head_sha=head
    )
    store.read_immutable_artifact = lambda u: (
        body if u == url else (_ for _ in ()).throw(AssertionError(u))
    )
    return SimpleNamespace(
        store=store,
        runtime=runtime,
        manifest=manifest,
        path=str(path),
        witness=str(witness),
        item=current,
        blobs=blobs,
        body=body,
        historical=historical,
    )


def preview(c):
    from omac.pipeline.evidence_review import preview_evidence_review

    return preview_evidence_review(
        c.store,
        c.runtime,
        c.manifest,
        "node",
        c.witness,
        2,
        "Authorized exact evidence-only review",
        expected_witness_sha256=sha(Path(c.witness).read_bytes()),
    )


def apply(c, request):
    from omac.pipeline.evidence_review import apply_evidence_review

    return apply_evidence_review(c.store, c.runtime, c.path, "node", c.witness, request)


def test_preview_is_readonly_and_apply_seals_original_run_once(case):
    before = deepcopy(case.item)
    request = preview(case)
    assert case.item == before and not load_manifest(case.path).meta
    result = apply(case, request)
    current = case.store.get_work_item(case.item.id)
    assert result["state"] == "ready-for-independent-review"
    assert current.delivery_identity.run_id == "original-run"
    assert current.delivery_identity.handoff_generation == "historical"
    assert current.bounces == before.bounces
    assert current.phase == TaskPhase.REVIEW and current.review_verdict is None
    assert current.review_ledger == before.review_ledger
    assert current.review_ledger_generation == current.review_generation
    assert not case.runtime.wake.called and not case.runtime.dispatch_reviewer.called
    with pytest.raises(NeedsDecision, match="consumed"):
        apply(case, request)


@pytest.mark.parametrize(
    "change",
    [
        "witness",
        "head",
        "contract",
        "run",
        "active",
        "unknown",
        "artifact",
        "attachment",
        "baseline",
        "reject",
        "extra-identity",
        "decision",
    ],
)
def test_stale_or_tampered_request_has_no_store_writes(case, change, monkeypatch):
    request = preview(case)
    if change == "witness":
        Path(case.witness).write_text(Path(case.witness).read_text() + "\n")
    if change == "head":
        case.item.artifacts["head_sha"] = "c" * 40
    if change == "contract":
        case.item.contract = Contract(objective="changed")
    if change == "run":
        case.runtime.list_runs.return_value[0] = replace(
            case.runtime.list_runs.return_value[0], agent_id="other"
        )
    if change in ("active", "unknown"):
        case.runtime.list_runs.return_value[1] = replace(
            case.runtime.list_runs.return_value[1],
            status="running" if change == "active" else "unknown",
        )
    if change == "artifact":
        case.store.read_immutable_artifact = lambda _: b"tampered"
    if change == "attachment":
        case.blobs["candidate"] = replace(case.blobs["candidate"], task_id="later-run")
    if change == "baseline":
        case.blobs["baseline"] = replace(
            case.blobs["baseline"], created_at="2026-09-27T08:00:00Z"
        )
    if change == "reject":
        case.blobs["reject"] = replace(
            case.blobs["reject"], content=b"{}", sha256=sha(b"{}")
        )
    if change == "extra-identity":
        request["delivery_identity"] = {"run_id": "forged"}
    if change == "decision":
        case.item.decision_required = {"reason_code": "another-decision"}
    writes = Mock(side_effect=case.store.update_work_item_metadata)
    monkeypatch.setattr(case.store, "update_work_item_metadata", writes)
    with pytest.raises((ValidationError, PlatformError)):
        apply(case, request)
    writes.assert_not_called()


@pytest.mark.parametrize("interrupt", [1, 2, 3, 4, 5, 6, 7])
def test_unknown_metadata_write_outcome_resumes_exact_request(
    case, monkeypatch, interrupt
):
    request = preview(case)
    original = case.store.update_work_item_metadata
    calls = 0

    def write(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == interrupt:
            raise PlatformError("response lost after commit")
        return result

    monkeypatch.setattr(case.store, "update_work_item_metadata", write)
    with pytest.raises(PlatformError):
        apply(case, request)
    monkeypatch.setattr(case.store, "update_work_item_metadata", original)
    assert apply(case, request)["state"] == "ready-for-independent-review"
    assert (
        case.store.get_work_item(case.item.id).delivery_identity.run_id
        == "original-run"
    )


@pytest.mark.parametrize(
    "attachment_run,comment_run,expected",
    [
        (None, "original-run", "original-run"),
        ("original-run", "original-run", "original-run"),
        ("different-run", "original-run", None),
    ],
)
def test_multica_attachment_uses_authoritative_comment_run(
    monkeypatch, attachment_run, comment_run, expected
):
    from omac.engines.multica import MulticaStore

    store = MulticaStore(EngineConfig("multica", "ws"))
    ref = {
        "comment_id": "comment",
        "attachment_id": "attachment",
        "sha256": sha(b"bytes"),
        "bytes": 5,
    }
    attachment = {
        "id": "attachment",
        "filename": "verification.yaml",
        "task_id": attachment_run,
        "uploader_id": "agent",
        "uploader_type": "agent",
    }
    monkeypatch.setattr(
        store,
        "_run_multica",
        lambda _: [
            {
                "id": "comment",
                "source_task_id": comment_run,
                "attachments": [attachment],
            }
        ],
    )
    monkeypatch.setattr(store, "_download_attachment_bytes", lambda *a, **k: b"bytes")
    if expected is None:
        with pytest.raises(PlatformError, match="Run"):
            store.observe_verification_attachment("issue", ref)
    else:
        assert store.observe_verification_attachment("issue", ref).task_id == expected


def test_authorization_does_not_relax_normal_same_head_reject(case):
    from omac.pipeline.loop import _worker_handoff_has_new_delivery
    from omac.core.taskmeta import current_review_ledger
    from omac.core.review_convergence import required_closures

    request = preview(case)
    apply(case, request)
    current = case.store.get_work_item(case.item.id)
    assert required_closures(current_review_ledger(current))
    ordinary = replace(
        current,
        status=WorkItemStatus.DONE,
        phase=TaskPhase.AUTHORING,
        worker_handoff=case.historical,
    )
    assert not _worker_handoff_has_new_delivery(ordinary, case.historical)


@pytest.mark.parametrize("stage", ["review-cycle", "status"])
def test_resume_unknown_review_transition_response(case, monkeypatch, stage):
    request = preview(case)
    name = "update_work_item_metadata" if stage == "review-cycle" else "update_status"
    original = getattr(case.store, name)

    def lost(*a, **k):
        result = original(*a, **k)
        if stage == "status" or "phase" in k:
            raise PlatformError("response lost")
        return result

    monkeypatch.setattr(case.store, name, lost)
    with pytest.raises(PlatformError):
        apply(case, request)
    monkeypatch.setattr(case.store, name, original)
    assert apply(case, request)["state"] == "ready-for-independent-review"


def test_changed_operator_reason_cannot_reconsume(case):
    request = preview(case)
    apply(case, request)
    changed = deepcopy(request)
    changed["reason"] = "Another reason for same evidence"
    with pytest.raises(ValidationError):
        apply(case, changed)


def test_cli_preview_and_apply_only_prepare_review(case, monkeypatch, capsys, tmp_path):
    from omac.cli.commands import node
    from omac.cli.main import main

    monkeypatch.setattr(
        node,
        "_build_engine",
        lambda _: SimpleNamespace(store=case.store, runtime=case.runtime),
    )
    command = [
        "node",
        "review-evidence",
        case.path,
        "node",
        "--witness-file",
        case.witness,
    ]
    assert (
        main(
            command
            + [
                "--witness-line",
                "2",
                "--witness-sha256",
                sha(Path(case.witness).read_bytes()),
                "--reason",
                "Approved exact original witness",
            ]
        )
        == 0
    )
    request = json.loads(capsys.readouterr().out)
    file = tmp_path / "request.json"
    file.write_text(json.dumps(request))
    assert main(command + ["--apply-request", str(file)]) == 0
    assert (
        json.loads(capsys.readouterr().out)["state"] == "ready-for-independent-review"
    )
    assert not case.runtime.wake.called


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/repo/blob/" + "a" * 40 + "/a",
        "https://github.com/acme/repo/blob/main/a",
        "https://evil.test/acme/repo/blob/" + "a" * 40 + "/a",
        "https://github.com/acme/repo/blob/" + "a" * 40 + "/../a",
    ],
)
def test_artifact_adapter_rejects_mutable_or_foreign_locations(url, monkeypatch):
    from omac.engines.multica import MulticaStore
    import omac.engines.multica as multica

    call = Mock()
    monkeypatch.setattr(multica.subprocess, "run", call)
    with pytest.raises(PlatformError):
        MulticaStore(EngineConfig("multica", "ws")).read_immutable_artifact(url)
    call.assert_not_called()


def test_artifact_adapter_reads_raw_commit_bytes(monkeypatch):
    from omac.engines.multica import MulticaStore
    import omac.engines.multica as multica

    call = Mock(return_value=github_response(b"raw bytes"))
    monkeypatch.setattr(multica.subprocess, "run", call)
    assert (
        MulticaStore(EngineConfig("multica", "ws")).read_immutable_artifact(
            "https://github.com/acme/repo/blob/" + "a" * 40 + "/path/file"
        )
        == b"raw bytes"
    )
    assert call.call_args.args[0][:2] == ["gh", "api"]


def test_preview_requires_preapproved_witness_sha(case):
    from omac.pipeline.evidence_review import preview_evidence_review

    with pytest.raises(ValidationError, match="approved witness"):
        preview_evidence_review(
            case.store,
            case.runtime,
            case.manifest,
            "node",
            case.witness,
            2,
            "reason",
            expected_witness_sha256="0" * 64,
        )


def test_artifact_transport_retries_only_idempotent_transient_reads(monkeypatch):
    from omac.engines.multica import MulticaStore
    import omac.engines.multica as multica

    call = Mock(
        side_effect=[
            SimpleNamespace(
                returncode=1, stdout=b"", stderr=b"net/http: TLS handshake timeout"
            ),
            github_response(b"bytes"),
        ]
    )
    monkeypatch.setattr(multica.subprocess, "run", call)
    store = MulticaStore(EngineConfig("multica", "ws"), sleeper=lambda _: None)
    assert (
        store.read_immutable_artifact(
            "https://github.com/acme/repo/blob/" + "a" * 40 + "/file"
        )
        == b"bytes"
    )
    assert call.call_count == 2


def test_apply_preserves_manifest_budget_authority(case):
    case.manifest.meta.update(
        {
            "last_amendment_id": "approved",
            "amendment_apply": {
                "schema": "omac.amendment-apply/v1",
                "amendment_id": "approved",
                "nodes": {
                    "node": {
                        "stage": "authoring",
                        "state": "synced",
                        "work_item_id": case.item.id,
                        "bounce_baseline": {"worker": 2, "review": 2, "merge": 0},
                    }
                },
            },
        }
    )
    save_manifest(case.manifest, case.path)
    before = deepcopy(case.manifest.meta)
    request = preview(case)
    apply(case, request)
    final = load_manifest(case.path)
    assert final.meta["amendment_apply"] == before["amendment_apply"]
    assert final.meta["last_amendment_id"] == before["last_amendment_id"]
    assert (
        next(iter(final.meta["evidence_review_authorizations"].values()))["step"] == 8
    )


def test_partial_recovery_rejects_new_runs_without_more_writes(case, monkeypatch):
    request = preview(case)
    original = case.store.update_work_item_metadata

    def lost(*a, **k):
        original(*a, **k)
        raise PlatformError("lost")

    monkeypatch.setattr(case.store, "update_work_item_metadata", lost)
    with pytest.raises(PlatformError):
        apply(case, request)
    writes = Mock(side_effect=original)
    monkeypatch.setattr(case.store, "update_work_item_metadata", writes)
    case.runtime.list_runs.return_value.append(
        AgentRunObservation("unrelated", "direct", "completed", agent_id="worker-agent")
    )
    with pytest.raises(ValidationError):
        apply(case, request)
    writes.assert_not_called()


def test_invalid_journal_cannot_bypass_initial_control(case):
    request = preview(case)
    from omac.pipeline.evidence_review import _digest

    keys = (
        "issue_id",
        "node_id",
        "binding",
        "pr_url",
        "head_sha",
        "run",
        "historical_handoff",
        "verification_ref",
        "reject_report_ref",
        "artifact_set_sha256",
    )
    token = _digest({k: request["tuple"][k] for k in keys})
    case.manifest.meta["evidence_review_authorizations"] = {token: {}}
    save_manifest(case.manifest, case.path)
    with pytest.raises(ValidationError, match="journal"):
        apply(case, request)


def test_cli_apply_does_not_accept_preview_flags(case, monkeypatch, tmp_path):
    from omac.cli.commands import node
    from omac.cli.main import main

    monkeypatch.setattr(
        node,
        "_build_engine",
        lambda _: SimpleNamespace(store=case.store, runtime=case.runtime),
    )
    request = tmp_path / "request.json"
    request.write_text(json.dumps(preview(case)))
    assert (
        main(
            [
                "node",
                "review-evidence",
                case.path,
                "node",
                "--witness-file",
                case.witness,
                "--apply-request",
                str(request),
                "--reason",
                "override",
            ]
        )
        == 5
    )
    assert case.item.delivery_identity is None


def test_original_reject_cannot_be_worker_authored(case):
    case.blobs["reject"] = replace(case.blobs["reject"], uploader_id="worker-agent")
    with pytest.raises(ValidationError, match="independent Reviewer"):
        preview(case)


def test_malformed_downloaded_verification_fails_closed(case):
    content = b"field: [invalid yaml"
    case.item.verification_ref["sha256"] = sha(content)
    case.item.verification_ref["bytes"] = len(content)
    case.blobs["candidate"] = replace(
        case.blobs["candidate"], content=content, sha256=sha(content)
    )
    with pytest.raises(ValidationError, match="structured"):
        preview(case)


def test_witness_requires_the_original_issue_read_not_echoed_json(case):
    lines = Path(case.witness).read_text().splitlines()
    call = json.loads(lines[0])
    call["message"]["content"][0]["arguments"]["command"] = "echo invented-snapshot"
    Path(case.witness).write_text(json.dumps(call) + "\n" + lines[1] + "\n")
    with pytest.raises(ValidationError, match="Issue read"):
        preview(case)


@pytest.mark.parametrize("observed_run", ["original-run", "foreign-run"])
def test_enriched_comment_run_preserves_only_matching_legacy_seal(case, observed_run):
    from omac.pipeline.loop import (
        _seal_worker_delivery,
        _validate_controller_sealed_delivery,
    )

    old_observation = replace(case.blobs["candidate"], task_id=None)
    identity = _seal_worker_delivery(
        case.store,
        case.manifest,
        "node",
        case.item,
        case.historical,
        case.runtime.list_runs.return_value[0],
        attachment=old_observation,
    )
    assert identity.verification_task_id is None
    case.item.delivery_identity = identity
    case.blobs["candidate"] = replace(case.blobs["candidate"], task_id=observed_run)
    if observed_run == "original-run":
        _validate_controller_sealed_delivery(case.store, case.item)
        assert case.item.delivery_identity == identity
    else:
        with pytest.raises(PlatformError):
            _validate_controller_sealed_delivery(case.store, case.item)


def test_ready_recovery_routes_one_independent_reviewer_on_normal_ticks(
    case, monkeypatch
):
    from omac.pipeline import loop

    request = preview(case)
    apply(case, request)
    manifest = load_manifest(case.path)
    dispatches = []

    def dispatch(store, item_id, agent):
        dispatches.append((item_id, agent))
        store.update_work_item_metadata(item_id, reviewer=agent)
        case.runtime.list_runs.return_value.append(
            AgentRunObservation(
                "independent-review",
                "direct",
                "running",
                agent_id="reviewer-agent",
                created_at="2026-09-29T00:00:00Z",
                trigger_kind="issue_assignment",
            )
        )
        return True

    case.runtime.dispatch_reviewer.side_effect = dispatch
    monkeypatch.setattr(loop.time, "sleep", lambda _: None)
    for _ in range(2):
        result = loop.tick(
            case.store, case.runtime, manifest, case.path, max_parallel=1, config={}
        )
        assert not result.failed
    assert dispatches == [(case.item.id, "reviewer")]
    assert case.store.get_work_item(case.item.id).review_verdict is None


def test_artifact_repository_identity_includes_pr_host(case):
    case.item.artifacts["pr_url"] = "https://other-host.example/acme/repo/pull/1"
    with pytest.raises(ValidationError, match="GitHub PR"):
        preview(case)

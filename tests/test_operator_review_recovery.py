"""Bound operator infrastructure recovery; fixture-only, never production."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from omac.cli.main import main
from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import ReviewerRunBaseline
from omac.engines.models import AgentRunObservation, TaskPhase, WorkItemStatus
from test_amendment import _engine, _manifest
from conftest import seal_mock_delivery


@pytest.fixture
def held(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMAC_ENGINE", "mock")
    monkeypatch.setenv("OMAC_WORKSPACE_ID", "ws")
    engine = _engine()
    path = _manifest(tmp_path)
    manifest = load_manifest(str(path))
    item = engine.store.create_work_item(
        "ws", "sealed", "worker delivery", "bootstrap", "alice"
    )
    node = manifest.nodes["bootstrap"]
    assert item.id == node.work_item_id
    engine.store.set_node_contract(item.id, node.contract)
    seal_mock_delivery(
        engine.store,
        item.id,
        "https://example.test/pr/1",
        {"commands": [], "integration_gates": [], "coverage": 100},
    )
    item.bounces.review = 2
    item.phase = TaskPhase.REVIEW
    item.status = WorkItemStatus.BLOCKED
    item.review_generation = "authoring-original"
    item.reviewer = "bob"
    from omac.pipeline.loop import _review_subject_for_current_delivery

    subject = _review_subject_for_current_delivery(manifest, "bootstrap", item)
    item.review_subject_digest = subject
    cutoff = item.delivery_identity.verification_created_at
    created = (
        datetime.fromisoformat(cutoff.replace("Z", "+00:00")) + timedelta(seconds=1)
    ).isoformat()
    reviewer_id = engine.store.resolve_agent_id("bob")
    item.reviewer_run_baseline = ReviewerRunBaseline(
        schema="omac.reviewer-run-baseline/v1",
        subject_digest=subject,
        target_reviewer="bob",
        target_agent_id=reviewer_id,
        cutoff_created_at=cutoff,
        generation="review-original",
        attempt=1,
        baseline_direct_run_ids=(item.delivery_identity.run_id,),
        target_run_id="reviewer-failed-submit",
    )
    item.review_ledger = {"old_raw_history": "not a current accepted report"}
    item.review_ledger_ref = {"sha256": "a" * 64, "attachment_id": "old-ledger"}
    item.decision_required = {
        "schema": "omac.decision-required/v1",
        "kind": "develop",
        "phase": "review",
        "gate": "operator-recovery",
        "reason_code": "omac-reject-integration-evidence-recovery-required",
        "resume_issue_id": item.id,
        "node_id": "bootstrap",
        "source_reviewer_run_id": "reviewer-failed-submit",
        "source_report_sha256": hashlib.sha256(b"unsubmitted local report").hexdigest(),
        "source_report_bytes": 24,
        "source_report_delivery": "unsubmitted; never accepted",
        "review_context_binding": {
            "generation": item.review_generation,
            "subject_digest": subject,
        },
        "next_action": "Supported recovery after capability deployment; preserve full raw facts",
    }
    runs = [
        AgentRunObservation(
            id="reviewer-failed-submit",
            kind="direct",
            status="completed",
            agent_id=reviewer_id,
            created_at=created,
            trigger_kind="issue_assignment",
        )
    ]
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _id: deepcopy(runs))
    import omac.cli.commands.node as node_command

    monkeypatch.setattr(node_command, "create_engine", lambda *a, **kw: engine)
    manifest.nodes["closeout"].status = "done"
    manifest.meta["protected"] = {"full_original": ["all budgets and done objects"]}
    save_manifest(manifest, str(path))
    return SimpleNamespace(
        engine=engine,
        path=str(path),
        item=item,
        runs=runs,
        manifest=manifest,
        key="bootstrap",
    )


def prepare(c, capsys):
    assert (
        main(
            [
                "node",
                "review-infrastructure",
                c.path,
                c.key,
                "--reason",
                "Explicit current-hold recovery; no product verdict",
            ]
        )
        == 0
    )
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("generation", ["authoring-original", None])
def test_public_prepare_binds_complete_hold_and_changes_nothing(
    held, capsys, generation
):
    c = held
    c.item.review_generation = generation
    c.item.decision_required["review_context_binding"]["generation"] = generation
    if generation is None:
        c.item.review_ledger = None
        c.item.review_ledger_ref = None
    before = deepcopy(c.item)
    manifest_bytes = Path(c.path).read_bytes()
    request = prepare(c, capsys)
    assert request["source"]["control"]["decision_required"] == before.decision_required
    assert request["source"]["control"]["review_generation"] == generation
    assert request["source"]["budget"]["absolute"]["review"] == 2
    assert c.item == before
    assert Path(c.path).read_bytes() == manifest_bytes


def test_ordinary_retry_still_refuses_same_infrastructure_hold(held, capsys):
    c = held
    before = deepcopy(c.item)
    assert main(["node", "retry", c.path, c.key, "--stage", "review"]) == 5
    assert "does not identify this review" in capsys.readouterr().err
    assert c.item == before


def test_explicit_approved_apply_prepares_one_review_without_actor_or_refund(
    held, capsys, monkeypatch
):
    c = held
    request = prepare(c, capsys)
    file = Path("request.json")
    file.write_text(json.dumps(request))
    token = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    before = deepcopy(c.item)
    monkeypatch.setattr(
        c.engine.runtime, "wake", lambda *a, **kw: pytest.fail("No actor in apply")
    )
    monkeypatch.setattr(
        c.engine.store,
        "assign_work_item",
        lambda *a, **kw: pytest.fail("No actor in apply"),
    )
    args = [
        "node",
        "review-infrastructure",
        c.path,
        c.key,
        "--apply-request",
        str(file),
        "--approve-request-sha256",
        token,
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ready-for-independent-review"
    assert c.item.decision_required in (None, {})
    assert c.item.status == WorkItemStatus.IN_REVIEW
    assert c.item.review_verdict is None and c.item.review_report is None
    assert c.item.bounces == before.bounces
    assert c.item.delivery_identity == before.delivery_identity
    assert c.item.review_ledger == before.review_ledger
    assert (
        c.item.reviewer_run_baseline.attempt == before.reviewer_run_baseline.attempt + 1
    )
    assert c.item.reviewer_run_baseline.target_run_id is None
    manifest = load_manifest(c.path)
    receipt = manifest.meta["operator_review_recovery"][token]
    assert (
        receipt["request"]["source"]["control"]["decision_required"]
        == before.decision_required
    )
    assert receipt["state"] == "consumed"
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "already-consumed"


def api_prepare(c, config=None):
    from omac.pipeline.operator_review_recovery import prepare_operator_review_recovery

    return prepare_operator_review_recovery(
        c.engine.store,
        c.engine.runtime,
        load_manifest(c.path),
        c.key,
        "Bound explicit infrastructure recovery",
        config or {},
    )


def api_apply(c, request, config=None, approval=None):
    from omac.pipeline.operator_review_recovery import (
        apply_operator_review_recovery,
        _digest,
    )

    return apply_operator_review_recovery(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        approval or _digest(request),
        config or {},
    )


@pytest.mark.parametrize(
    "change",
    [
        "verdict",
        "report",
        "report-ref",
        "hold-reason",
        "hold-gate",
        "hold-issue",
        "hold-node",
        "hold-run",
        "hold-generation",
        "hold-subject",
        "hold-provenance",
        "source-baseline",
        "source-agent",
        "source-cutoff",
        "source-trigger",
        "source-missing",
        "active",
        "queued",
        "unknown",
        "duplicate",
        "delayed",
        "identity",
        "contract",
        "done",
        "merged",
        "wrong-phase",
        "assignment",
        "continuation",
        "baseline-conflict",
        "budget-exhausted",
        "generation-missing",
        "current-ledger",
        "feedback",
        "feedback-ref",
        "review-comment",
        "generation-type",
        "baseline-generation-type",
        "baseline-duplicate-ids",
        "Run-id-type",
        "Run-object",
    ],
)
def test_source_guard_fails_before_any_writes(held, change):
    c = held
    if change == "verdict":
        c.item.review_verdict = "reject"
    elif change == "report":
        c.item.review_report = {"actual accepted report": True}
    elif change == "report-ref":
        c.item.review_report_ref = {"sha256": "b" * 64}
    elif change.startswith("hold-"):
        field = {
            "hold-reason": "reason_code",
            "hold-gate": "gate",
            "hold-issue": "resume_issue_id",
            "hold-node": "node_id",
            "hold-run": "source_reviewer_run_id",
        }.get(change)
        if field:
            c.item.decision_required[field] = "forged"
        elif change == "hold-provenance":
            c.item.decision_required.pop("source_report_sha256")
        else:
            c.item.decision_required["review_context_binding"][
                "generation" if change == "hold-generation" else "subject_digest"
            ] = "stale"
    elif change == "source-baseline":
        c.item.reviewer_run_baseline = None
    elif change == "source-agent":
        c.item.reviewer_run_baseline = replace(
            c.item.reviewer_run_baseline, target_agent_id="foreign"
        )
    elif change == "source-cutoff":
        c.item.reviewer_run_baseline = replace(
            c.item.reviewer_run_baseline, cutoff_created_at="2000-01-01T00:00:00Z"
        )
    elif change == "baseline-generation-type":
        c.item.reviewer_run_baseline = replace(
            c.item.reviewer_run_baseline, generation=True
        )
    elif change == "baseline-duplicate-ids":
        c.item.reviewer_run_baseline = replace(
            c.item.reviewer_run_baseline, baseline_direct_run_ids=("dup", "dup")
        )
    elif change == "Run-id-type":
        c.runs[0] = replace(c.runs[0], id=123)
    elif change == "Run-object":
        c.runs[:] = [None]
    elif change == "source-trigger":
        c.runs[0] = replace(c.runs[0], trigger_kind="comment")
    elif change == "source-missing":
        c.runs.clear()
    elif change in {"active", "queued", "unknown"}:
        c.runs[0] = replace(
            c.runs[0],
            status={"active": "running", "queued": "queued", "unknown": "unknown"}[
                change
            ],
        )
    elif change == "duplicate":
        c.runs.append(c.runs[0])
    elif change == "delayed":
        c.runs.append(replace(c.runs[0], id="another-terminal-reviewer"))
    elif change == "identity":
        c.item.delivery_identity = None
    elif change == "contract":
        c.item.contract = None
    elif change in {"done", "merged"}:
        m = load_manifest(c.path)
        if change == "done":
            m.nodes[c.key].status = "done"
        else:
            m.nodes[c.key].merged = True
        save_manifest(m, c.path)
    elif change == "wrong-phase":
        c.item.phase = TaskPhase.AUTHORING
    elif change == "assignment":
        c.item.platform_assignee_id = "still-assigned"
    elif change == "continuation":
        c.item.review_continuation = {"not a transferable grant": True}
    elif change == "baseline-conflict":
        c.item.bounce_baseline = {"worker": 0, "review": 2, "merge": 0}
    elif change == "current-ledger":
        c.item.review_ledger_generation = c.item.review_generation
    elif change in {"feedback", "feedback-ref", "review-comment"}:
        setattr(
            c.item,
            {
                "feedback": "machine_feedback",
                "feedback-ref": "machine_feedback_ref",
                "review-comment": "review_comment",
            }[change],
            {"current": "not accepted as empty"},
        )
    elif change == "generation-type":
        c.item.review_generation = False
        c.item.decision_required["review_context_binding"]["generation"] = False
    elif change == "generation-missing":
        c.item.review_generation = None
        c.item.decision_required["review_context_binding"].pop("generation")
    before = deepcopy(c.item)
    manifest_bytes = Path(c.path).read_bytes()
    from omac.errors import ValidationError, PlatformError

    with pytest.raises((ValidationError, PlatformError)):
        api_prepare(
            c, {"retry": {"no_submit_runs": 1}} if change == "budget-exhausted" else {}
        )
    assert c.item == before
    assert Path(c.path).read_bytes() == manifest_bytes


@pytest.mark.parametrize(
    "change",
    [
        "hold",
        "counter",
        "native-baseline",
        "contract",
        "subject",
        "generation",
        "seal",
        "source-run",
        "new-report",
        "limits",
        "manifest-done",
        "manifest-budget",
    ],
)
def test_current_cas_drift_blocks_every_write(held, change):
    c = held
    request = api_prepare(c)
    config = {}
    if change == "hold":
        c.item.decision_required["next_action"] = "changed full original hold"
    elif change == "counter":
        c.item.bounces.worker += 1
    elif change == "native-baseline":
        c.item.bounce_baseline = {"worker": 0, "review": 0, "merge": 0}
    elif change == "contract":
        c.item.contract = None
    elif change == "subject":
        c.item.review_subject_digest = "changed"
    elif change == "generation":
        c.item.review_generation = None
        c.item.decision_required["review_context_binding"]["generation"] = None
    elif change == "seal":
        c.item.delivery_identity = replace(
            c.item.delivery_identity, handoff_generation="changed"
        )
    elif change == "source-run":
        c.runs[0] = replace(c.runs[0], updated_at="changed")
    elif change == "new-report":
        c.item.review_report_ref = {"sha256": "accepted after prepare"}
    elif change == "limits":
        config = {"retry": {"review": 10}}
    else:
        m = load_manifest(c.path)
        if change == "manifest-done":
            m.nodes["closeout"].title = "mutated done full object"
        else:
            m.meta["protected"]["full_original"].append("budget changed")
        save_manifest(m, c.path)
    before = deepcopy(c.item)
    raw = Path(c.path).read_bytes()
    from omac.errors import ValidationError, PlatformError

    with pytest.raises((ValidationError, PlatformError)):
        api_apply(c, request, config)
    assert c.item == before
    assert Path(c.path).read_bytes() == raw


@pytest.mark.parametrize("operation", ["assignment", "baseline", "status", "hold"])
def test_unknown_accepted_write_observed_once_without_refund(
    held, monkeypatch, operation
):
    c = held
    request = api_prepare(c)
    before = deepcopy(c.item)
    from omac.errors import PlatformError

    methods = {
        "assignment": "clear_assignment",
        "baseline": "update_work_item_metadata",
        "status": "update_status",
        "hold": "update_work_item_metadata",
    }
    method = methods[operation]
    original = getattr(c.engine.store, method)
    calls = []

    def unknown(*args, **kwargs):
        matching = operation in {"assignment", "status"} or (
            "reviewer_run_baseline" in kwargs
            if operation == "baseline"
            else "decision_required" in kwargs
        )
        result = original(*args, **kwargs)
        if matching:
            calls.append(deepcopy(kwargs))
            if len(calls) == 1:
                raise PlatformError("accepted write; response lost")
        return result

    monkeypatch.setattr(c.engine.store, method, unknown)
    with pytest.raises(PlatformError, match="response lost"):
        api_apply(c, request)
    assert api_apply(c, request)["state"] == "ready-for-independent-review"
    assert len(calls) == 1
    assert c.item.bounces == before.bounces
    assert c.item.review_ledger_ref == before.review_ledger_ref
    m = load_manifest(c.path)
    assert m.nodes["closeout"] == c.manifest.nodes["closeout"]
    assert m.meta["protected"] == c.manifest.meta["protected"]
    c.item.bounces.review += 1
    c.item.status = WorkItemStatus.DONE
    assert api_apply(c, request)["state"] == "already-consumed"
    assert c.item.status == WorkItemStatus.DONE and len(calls) == 1


def test_unknown_final_write_does_not_admit_new_hidden_or_queued_run(held, monkeypatch):
    c = held
    request = api_prepare(c)
    original = c.engine.store.update_work_item_metadata
    from omac.errors import PlatformError, ValidationError

    def unknown(item_id, **kwargs):
        original(item_id, **kwargs)
        if "decision_required" in kwargs:
            raise PlatformError("last accepted clear response lost")

    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", unknown)
    with pytest.raises(PlatformError):
        api_apply(c, request)
    c.runs.append(replace(c.runs[0], id="delayed-queued-reviewer", status="queued"))
    before = deepcopy(c.item)
    with pytest.raises(ValidationError):
        api_apply(c, request)
    assert c.item == before


def test_pending_step4_cannot_reopen_done_manifest(held, monkeypatch):
    c = held
    request = api_prepare(c)
    import omac.pipeline.operator_review_recovery as recovery

    real_save = recovery.save_manifest
    lost = []

    def lost_after_save(manifest, path):
        real_save(manifest, path)
        entries = manifest.meta.get(recovery.JOURNAL, {})
        if not lost and any(
            e["step"] == 4 and e["state"] == "pending" for e in entries.values()
        ):
            lost.append(True)
            raise OSError("saved step4; acknowledgement lost")

    monkeypatch.setattr(recovery, "save_manifest", lost_after_save)
    with pytest.raises(OSError):
        api_apply(c, request)
    m = load_manifest(c.path)
    m.nodes[c.key].status = "done"
    real_save(m, c.path)
    raw = Path(c.path).read_bytes()
    from omac.errors import ValidationError

    with pytest.raises(ValidationError):
        api_apply(c, request)
    assert Path(c.path).read_bytes() == raw


def test_approval_required_and_full_hold_receipt_precedes_clear(held, monkeypatch):
    c = held
    request = api_prepare(c)
    from omac.errors import ValidationError

    with pytest.raises(ValidationError):
        api_apply(c, request, approval="0" * 64)
    original = c.engine.store.update_work_item_metadata
    seen = []

    def checked(item_id, **kwargs):
        if "decision_required" in kwargs:
            from omac.pipeline.operator_review_recovery import JOURNAL, _digest

            entry = load_manifest(c.path).meta[JOURNAL][_digest(request)]
            assert (
                entry["request"]["source"]["control"]["decision_required"]
                == c.item.decision_required
            )
            assert (
                entry["request"]["source"]["control"]["review_ledger_ref"]
                == c.item.review_ledger_ref
            )
            seen.append(True)
        return original(item_id, **kwargs)

    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", checked)
    api_apply(c, request)
    assert seen == [True]


@pytest.mark.parametrize(
    "case,key",
    [
        ("model", "model-catalog"),
        ("fixture", "agentrun-malformed-marker-fixture"),
    ],
)
@pytest.mark.parametrize(
    "manifest_file,done_count",
    [("manifest.yaml", 70), ("manifest-progress-71.yaml", 71)],
)
def test_captured_full_controls_and_manifest_preserve_exact_budgets_history_done(
    held, tmp_path, monkeypatch, case, key, manifest_file, done_count
):
    """Captured controls, with mocked transport. No live-source eligibility claim."""
    from dataclasses import fields
    from omac.core.taskmeta import DeliveryIdentity, TaskKind
    from omac.engines.models import Bounces, WorkItem, PullRequestReadiness

    folder = Path(__file__).parent / "fixtures/operator_review_recovery"
    raw = json.loads((folder / f"{case}.json").read_text())
    report_bytes = (
        folder / f"{case}-final-unsubmitted.{'yaml' if case == 'model' else 'json'}"
    ).read_bytes()
    hold = raw["decision_required"]
    report_sha = hold.get(
        "source_final_local_report_sha256", hold.get("source_report_sha256")
    )
    report_size = hold.get(
        "source_final_local_report_bytes", hold.get("source_report_bytes")
    )
    assert hashlib.sha256(report_bytes).hexdigest() == report_sha
    assert len(report_bytes) == report_size
    values = {f.name: raw[f.name] for f in fields(WorkItem) if f.name in raw}
    values.update(
        status=WorkItemStatus.BLOCKED,
        phase=TaskPhase.REVIEW,
        kind=TaskKind.DEVELOP,
        bounces=Bounces(**raw["bounces"]),
        delivery_identity=DeliveryIdentity(**raw["delivery_identity"]),
        reviewer_run_baseline=ReviewerRunBaseline(**raw["reviewer_run_baseline"]),
    )
    item = WorkItem(**values)
    import omac.engines.mock as mock

    mock._shared_work_items[item.id] = item
    engine = held.engine
    engine.store.config.workspace_id = item.workspace_id
    baseline, identity = item.reviewer_run_baseline, item.delivery_identity
    monkeypatch.setattr(
        engine.store,
        "resolve_agent_id",
        lambda name: (
            baseline.target_agent_id
            if name == baseline.target_reviewer
            else identity.agent_id
        ),
    )
    # Transport proof replay from the bound metadata + decoded verification.
    # This reserialized body is NOT asserted as the original immutable bytes.
    attachment = SimpleNamespace(
        content=yaml.safe_dump(item.verification).encode(),
        attachment_id=identity.verification_attachment_id,
        comment_id=identity.verification_comment_id,
        sha256=identity.verification_sha256,
        uploader_id=identity.verification_uploader_id,
        uploader_type=identity.verification_uploader_type,
        task_id=identity.verification_task_id,
        created_at=identity.verification_created_at,
    )
    monkeypatch.setattr(
        engine.store, "observe_verification_attachment", lambda *a: attachment
    )
    monkeypatch.setattr(
        engine.store,
        "read_pull_request_readiness",
        lambda _url: PullRequestReadiness(False, "OPEN", identity.pr_head_sha),
    )
    created = (
        datetime.fromisoformat(baseline.cutoff_created_at.replace("Z", "+00:00"))
        + timedelta(seconds=1)
    ).isoformat()
    runs = [
        AgentRunObservation(
            id=baseline.target_run_id,
            kind="direct",
            status="completed",
            agent_id=baseline.target_agent_id,
            created_at=created,
            trigger_kind="issue_assignment",
        )
    ]
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _id: deepcopy(runs))
    path = tmp_path / "captured.yaml"
    path.write_bytes((folder / manifest_file).read_bytes())
    c = SimpleNamespace(engine=engine, item=item, path=str(path), key=key, runs=runs)
    before = deepcopy(item)
    original = load_manifest(c.path)
    request = api_prepare(c)
    assert request["source"]["control"]["decision_required"] == raw["decision_required"]
    assert request["source"]["control"]["review_generation"] == raw["review_generation"]
    assert request["source"]["budget"]["absolute"] == raw["bounces"]
    assert api_apply(c, request)["state"] == "ready-for-independent-review"
    result = load_manifest(c.path)
    assert item.bounces == before.bounces
    assert item.bounce_baseline == before.bounce_baseline
    assert item.review_ledger == before.review_ledger
    assert item.review_ledger_ref == before.review_ledger_ref
    assert (
        item.artifacts == before.artifacts
        and item.verification_ref == before.verification_ref
    )
    assert item.delivery_identity == before.delivery_identity
    assert result.meta["amendment_apply"] == original.meta["amendment_apply"]
    for node_id, node in original.nodes.items():
        if node_id != key:
            assert result.nodes[node_id] == node
    original_raw = yaml.safe_load((folder / manifest_file).read_text())
    result_raw = yaml.safe_load(Path(c.path).read_text())
    previous = {n["id"]: n for n in original_raw["nodes"]}
    current = {n["id"]: n for n in result_raw["nodes"]}
    assert {k: v for k, v in current.items() if k != key} == {
        k: v for k, v in previous.items() if k != key
    }
    assert len([n for n in original.nodes.values() if n.status == "done"]) == done_count


def test_normal_runner_dispatches_exactly_once_on_reserved_attempt(held, monkeypatch):
    c = held
    before = c.item.reviewer_run_baseline
    request = api_prepare(c)
    api_apply(c, request)
    assert c.item.reviewer is None
    assert c.item.reviewer_run_baseline.attempt == before.attempt + 1
    assert set(c.item.reviewer_run_baseline.baseline_direct_run_ids) >= (
        set(before.baseline_direct_run_ids) | {before.target_run_id}
    )
    from omac.pipeline.loop import _dispatch_reviewer_for_current_subject

    dispatched = []

    def dispatch(store, item_id, reviewer):
        dispatched.append(item_id)
        assert c.item.reviewer_run_baseline.attempt == before.attempt + 1
        c.item.reviewer = reviewer
        c.item.platform_assignee_id = c.item.reviewer_run_baseline.target_agent_id
        c.item.reviewer_run_baseline = replace(
            c.item.reviewer_run_baseline, target_run_id="new-normal-reviewer"
        )
        c.runs.append(replace(c.runs[0], id="new-normal-reviewer", status="running"))
        return True

    monkeypatch.setattr(c.engine.runtime, "dispatch_reviewer", dispatch)
    monkeypatch.setattr(c.engine.runtime, "is_active", lambda _id: bool(dispatched))
    m = load_manifest(c.path)
    assert _dispatch_reviewer_for_current_subject(
        c.engine.store, c.engine.runtime, m, c.key
    )
    assert not _dispatch_reviewer_for_current_subject(
        c.engine.store, c.engine.runtime, m, c.key
    )
    assert dispatched == [c.item.id]
    assert c.item.bounces.review == 2


def test_two_consecutive_unknown_writes_checkpoint_observation_before_next_write(
    held, monkeypatch
):
    c = held
    request = api_prepare(c)
    from omac.errors import PlatformError

    original_clear = c.engine.store.clear_assignment
    original_update = c.engine.store.update_work_item_metadata
    calls = []

    def clear(item_id):
        original_clear(item_id)
        calls.append("clear")
        raise PlatformError("first accepted response lost")

    def update(item_id, **kwargs):
        if "reviewer_run_baseline" in kwargs:
            from omac.pipeline.operator_review_recovery import JOURNAL, _digest

            assert load_manifest(c.path).meta[JOURNAL][_digest(request)]["step"] == 1
            original_update(item_id, **kwargs)
            calls.append("baseline")
            raise PlatformError("second accepted response lost")
        return original_update(item_id, **kwargs)

    monkeypatch.setattr(c.engine.store, "clear_assignment", clear)
    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", update)
    with pytest.raises(PlatformError, match="first"):
        api_apply(c, request)
    with pytest.raises(PlatformError, match="second"):
        api_apply(c, request)
    assert api_apply(c, request)["state"] == "ready-for-independent-review"
    assert calls == ["clear", "baseline"]
    assert c.item.bounces.review == 2


@pytest.mark.parametrize(
    "drift", ["counter", "generation", "new-report", "Run", "done", "receipt"]
)
def test_pending_unknown_drift_never_replays_another_control_write(
    held, monkeypatch, drift
):
    c = held
    request = api_prepare(c)
    from omac.errors import PlatformError, ValidationError

    original = c.engine.store.update_work_item_metadata
    calls = []

    def unknown(item_id, **kwargs):
        calls.append(deepcopy(kwargs))
        original(item_id, **kwargs)
        if "reviewer_run_baseline" in kwargs:
            raise PlatformError("accepted baseline response lost")

    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", unknown)
    with pytest.raises(PlatformError):
        api_apply(c, request)
    if drift == "counter":
        c.item.bounces.merge += 1
    elif drift == "generation":
        c.item.review_generation = None
    elif drift == "new-report":
        c.item.review_report = {"late actual report": "not safe to reset"}
    elif drift == "Run":
        c.runs.append(replace(c.runs[0], id="delayed-visible", status="running"))
    else:
        from omac.pipeline.operator_review_recovery import JOURNAL, _digest

        m = load_manifest(c.path)
        if drift == "done":
            m.nodes[c.key].status = "done"
        else:
            m.meta[JOURNAL][_digest(request)]["request"]["source"]["control"][
                "decision_required"
            ]["next_action"] = "changed"
        save_manifest(m, c.path)
    before = deepcopy(c.item)
    count = len(calls)
    with pytest.raises(ValidationError):
        api_apply(c, request)
    assert c.item == before and len(calls) == count


def test_readonly_prepare_never_takes_write_lock_or_mutates_source(
    held, capsys, monkeypatch
):
    c = held
    import omac.core.manifest as manifest_module

    monkeypatch.setattr(
        manifest_module,
        "manifest_write_lock",
        lambda *_args: pytest.fail("prepare must not take manifest write lock"),
    )
    before = deepcopy(c.item)
    raw = Path(c.path).read_bytes()
    prepare(c, capsys)
    assert c.item == before and Path(c.path).read_bytes() == raw


@pytest.mark.parametrize("field", ["source", "control", "reviewer_run_baseline"])
@pytest.mark.parametrize("bad", [None, [], "bad"])
def test_malformed_approved_source_returns_help_without_writes(
    held, capsys, field, bad
):
    c = held
    request = prepare(c, capsys)
    if field == "source":
        request["source"] = bad
    elif field == "control":
        request["source"]["control"] = bad
    else:
        request["source"]["control"]["reviewer_run_baseline"] = bad
    file = Path("malformed-request.json")
    file.write_text(json.dumps(request))
    token = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    before = deepcopy(c.item)
    raw = Path(c.path).read_bytes()
    assert (
        main(
            [
                "node",
                "review-infrastructure",
                c.path,
                c.key,
                "--apply-request",
                str(file),
                "--approve-request-sha256",
                token,
            ]
        )
        == 5
    )
    assert "help" in capsys.readouterr().err
    assert c.item == before and Path(c.path).read_bytes() == raw

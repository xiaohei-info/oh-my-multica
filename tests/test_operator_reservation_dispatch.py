"""Captured consumed reservations through real collection; transport is offline."""

from copy import deepcopy
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import DeliveryIdentity, ReviewerRunBaseline, TaskKind
from omac.engines.models import (
    AgentRunObservation,
    Bounces,
    PullRequestReadiness,
    TaskPhase,
    WorkItem,
    WorkItemStatus,
)
from omac.pipeline.loop import collect_results
from test_operator_review_recovery import held as _held_fixture

held = _held_fixture
FOLDER = Path(__file__).parent / "fixtures/operator_reservation_dispatch"


pytestmark = pytest.mark.integration


@pytest.fixture(params=["model-catalog", "agentrun-malformed-marker-fixture"])
def reservation(held, request, tmp_path, monkeypatch):
    raw_case = next(
        x
        for x in json.loads(
            (FOLDER / "both-restored-current-controls-and-runs.json").read_text()
        )["issues"]
        if x["node"] == request.param
    )
    raw = raw_case["control"]
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
    # Decoded capture over mock transport, not proof of original attachment bytes.
    proof = SimpleNamespace(
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
        engine.store, "observe_verification_attachment", lambda *a: proof
    )
    monkeypatch.setattr(
        engine.store,
        "read_pull_request_readiness",
        lambda _url: PullRequestReadiness(False, "OPEN", identity.pr_head_sha),
    )
    runs = [AgentRunObservation(**x) for x in raw_case["runs"]]
    mock._shared_runs[item.id] = runs
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _id: deepcopy(runs))
    monkeypatch.setattr(
        engine.runtime, "is_active", lambda _id: any(r.active for r in runs)
    )
    full = load_manifest(str(FOLDER / "current-wholemanifest.yaml"))
    assert (
        len(full.nodes) == 183
        and sum(n.status == "done" for n in full.nodes.values()) == 74
    )
    # Scoped collection fixture retains ALL74 current full DONE objects and complete
    # approved meta. Unrelated healthy OAC controls are not fabricated/exercised.
    manifest = deepcopy(full)
    manifest.nodes = {
        k: n
        for k, n in manifest.nodes.items()
        if k == request.param or n.status == "done"
    }
    path = ".omac/open-agent-cluster.yaml"
    Path(".omac").mkdir(exist_ok=True)
    save_manifest(manifest, path)
    token, entry = next(
        (t, e)
        for t, e in manifest.meta["operator_review_recovery"].items()
        if e["request"]["node_id"] == request.param
    )
    assert entry["state"] == "consumed" and entry["step"] == 4
    config = {
        "retry": {
            **entry["request"]["source"]["budget"]["limits"],
            "no_submit_runs": entry["request"]["source"]["budget"]["no_submit_limit"],
        }
    }
    calls = []

    def wake(item_id, agent, role):
        calls.append((item_id, agent, role))
        runs.append(
            AgentRunObservation(
                id="fresh-" + token[:12],
                kind="direct",
                status="running",
                agent_id=baseline.target_agent_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                trigger_kind="issue_assignment",
            )
        )

    monkeypatch.setattr(engine.runtime, "wake", wake)
    monkeypatch.setattr(
        engine.store,
        "clear_assignment",
        lambda *_a: pytest.fail("Reservation must not clear again"),
    )
    monkeypatch.setattr(
        engine.store, "reset_review", lambda *_a: pytest.fail("No new cycle")
    )
    return SimpleNamespace(
        engine=engine,
        item=item,
        runs=runs,
        manifest=manifest,
        path=path,
        key=request.param,
        config=config,
        token=token,
        entry=deepcopy(entry),
        calls=calls,
    )


def collect(c):
    return collect_results(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        retry_limits=c.config["retry"],
        config=c.config,
    )


@pytest.mark.parametrize("reviewer", ["", None])
def test_real_collection_dispatches_reserved_attempt_once(reservation, reviewer):
    c = reservation
    c.item.reviewer = reviewer
    before_item, before_manifest = deepcopy(c.item), deepcopy(c.manifest)
    failures = collect(c)
    assert not failures, failures
    assert len(c.calls) == 1
    assert c.item.reviewer_run_baseline.target_run_id == c.runs[-1].id
    assert c.item.reviewer_run_baseline.attempt == 2
    assert (
        replace(c.item.reviewer_run_baseline, target_run_id=None)
        == before_item.reviewer_run_baseline
    )
    assert c.manifest.meta["operator_review_recovery"][c.token] == c.entry
    assert (
        c.item.bounces == before_item.bounces
        and c.item.bounce_baseline == before_item.bounce_baseline
    )
    assert c.item.review_generation == before_item.review_generation
    assert (
        c.item.review_ledger == before_item.review_ledger
        and c.item.review_ledger_ref == before_item.review_ledger_ref
    )
    assert c.item.delivery_identity == before_item.delivery_identity
    assert c.manifest.meta["amendment_apply"] == before_manifest.meta["amendment_apply"]
    assert all(
        c.manifest.nodes[k] == n for k, n in before_manifest.nodes.items() if k != c.key
    )
    collect(c)
    assert len(c.calls) == 1


@pytest.mark.parametrize(
    "operation", ["status", "decision", "assignment", "wake", "binding"]
)
def test_unknown_accepted_effect_is_observed_before_resume(
    reservation, monkeypatch, operation
):
    from omac.errors import PlatformError

    c = reservation
    method = {
        "status": "update_status",
        "decision": "update_work_item_metadata",
        "assignment": "assign_reviewer",
        "wake": "wake",
        "binding": "update_work_item_metadata",
    }[operation]
    owner = c.engine.runtime if operation == "wake" else c.engine.store
    original = getattr(owner, method)
    accepted = []

    def unknown(*args, **kw):
        matching = operation not in {"decision", "binding"} or (
            "decision_required" in kw
            if operation == "decision"
            else "reviewer_run_baseline" in kw
        )
        result = original(*args, **kw)
        if matching:
            accepted.append(operation)
            if len(accepted) == 1:
                raise PlatformError("accepted effect; response lost")
        return result

    monkeypatch.setattr(owner, method, unknown)
    assert collect(c)
    c.manifest = load_manifest(c.path)
    collect(c)
    assert len(accepted) == 1 and len(c.calls) == 1
    assert (
        c.item.reviewer_run_baseline.attempt == 2
        and c.item.reviewer_run_baseline.target_run_id == c.runs[-1].id
    )
    assert c.manifest.meta["operator_review_recovery"][c.token] == c.entry


def test_wake_unknown_or_unobserved_never_replays(reservation, monkeypatch):
    from omac.errors import PlatformError

    c = reservation

    def invisible(*args):
        c.calls.append(args)
        raise PlatformError("wake outcome unknown")

    monkeypatch.setattr(c.engine.runtime, "wake", invisible)
    assert collect(c)
    c.manifest = load_manifest(c.path)
    assert collect(c)
    assert len(c.calls) == 1 and c.item.reviewer_run_baseline.target_run_id is None
    c.runs.append(
        AgentRunObservation(
            id="late-owned",
            kind="direct",
            status="running",
            agent_id=c.item.reviewer_run_baseline.target_agent_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            trigger_kind="issue_assignment",
        )
    )
    c.manifest = load_manifest(c.path)
    collect(c)
    assert (
        c.item.reviewer_run_baseline.target_run_id == "late-owned" and len(c.calls) == 1
    )


@pytest.mark.parametrize(
    "change",
    [
        "no_receipt",
        "pending",
        "bad_step",
        "bad_digest",
        "request",
        "generation",
        "attempt",
        "exclusions",
        "subject",
        "review_generation",
        "contract",
        "node_contract",
        "done",
        "merged",
        "source_run",
        "missing_run",
        "active",
        "queued",
        "unknown",
        "delayed",
        "ambiguous",
        "report",
        "ledger",
        "continuation",
        "assignee",
        "reviewer",
        "arbitrary_hold",
        "no_decision_blocked",
        "budget",
        "relative",
        "limit",
    ],
)
def test_full_bound_reservation_rejects_drift_before_effects(
    reservation, monkeypatch, change
):
    c = reservation
    entries = c.manifest.meta["operator_review_recovery"]
    base = c.item.reviewer_run_baseline
    if change == "no_receipt":
        del entries[c.token]
    elif change == "pending":
        entries[c.token]["state"] = "pending"
    elif change == "bad_step":
        entries[c.token]["step"] = 3
    elif change == "bad_digest":
        entries[c.token]["request_sha256"] = "0" * 64
    elif change == "request":
        entries[c.token]["request"]["reason"] = "forged"
    elif change == "generation":
        c.item.reviewer_run_baseline = replace(base, generation="infrastructure-forged")
    elif change == "attempt":
        c.item.reviewer_run_baseline = replace(base, attempt=1)
    elif change == "exclusions":
        c.item.reviewer_run_baseline = replace(base, baseline_direct_run_ids=())
    elif change == "subject":
        c.item.review_subject_digest = "0" * 64
    elif change == "review_generation":
        c.item.review_generation = "changed-generation"
    elif change == "contract":
        c.item.contract = {"changed": True}
    elif change == "node_contract":
        c.manifest.nodes[c.key].contract.verification_commands.append("echo drift")
    elif change == "done":
        c.manifest.nodes[c.key].status = "done"
    elif change == "merged":
        c.manifest.nodes[c.key].merged = True
    elif change == "source_run":
        c.runs[0] = replace(c.runs[0], updated_at="changed")
    elif change == "missing_run":
        c.runs.pop()
    elif change in {"active", "queued", "unknown", "delayed", "ambiguous"}:
        count = 2 if change == "ambiguous" else 1
        for i in range(count):
            c.runs.append(
                AgentRunObservation(
                    id="extra-" + str(i),
                    kind="direct",
                    status={
                        "active": "running",
                        "queued": "queued",
                        "unknown": "unknown",
                    }.get(change, "completed"),
                    agent_id=base.target_agent_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    trigger_kind="issue_assignment",
                )
            )
    elif change == "report":
        c.item.review_report_ref = {"attachment_id": "accepted"}
    elif change == "ledger":
        c.item.review_ledger = {"current": True}
        c.item.review_ledger_generation = c.item.review_generation
    elif change == "continuation":
        c.item.review_continuation = {"active": True}
    elif change == "assignee":
        c.item.platform_assignee_id = "another-agent"
    elif change == "reviewer":
        c.item.reviewer = "another-reviewer"
    elif change == "arbitrary_hold":
        c.item.decision_required["gate"] = "other"
    elif change == "no_decision_blocked":
        c.item.decision_required = None
    elif change == "budget":
        c.item.bounces.review += 1
    elif change == "relative":
        c.item.bounce_baseline = {"worker": 0, "review": 0, "merge": 0}
    elif change == "limit":
        c.config["retry"]["review"] += 1
    before = deepcopy(c.item)
    monkeypatch.setattr(
        c.engine.store, "update_status", lambda *_a: pytest.fail("No status effect")
    )
    monkeypatch.setattr(
        c.engine.store,
        "update_work_item_metadata",
        lambda *_a, **_kw: pytest.fail("No metadata effect"),
    )
    monkeypatch.setattr(
        c.engine.store, "assign_reviewer", lambda *_a: pytest.fail("No assignment")
    )
    collect(c)
    assert c.item == before and c.calls == []


def test_raw_original_failure_and_consumed_receipt_remain_exact(reservation):
    c = reservation
    report = (
        Path(__file__).parent
        / "fixtures/operator_review_recovery"
        / (
            "model-final-unsubmitted.yaml"
            if c.key == "model-catalog"
            else "fixture-final-unsubmitted.json"
        )
    )
    raw = report.read_bytes()
    hold = c.entry["request"]["source"]["control"]["decision_required"]
    assert hashlib.sha256(raw).hexdigest() == hold.get(
        "source_final_local_report_sha256", hold.get("source_report_sha256")
    )
    assert len(raw) == hold.get(
        "source_final_local_report_bytes", hold.get("source_report_bytes")
    )
    snapshot = json.dumps(c.entry, sort_keys=True, separators=(",", ":"))
    collect(c)
    assert (
        json.dumps(
            c.manifest.meta["operator_review_recovery"][c.token],
            sort_keys=True,
            separators=(",", ":"),
        )
        == snapshot
    )


@pytest.mark.parametrize("stage", ["assign", "wake"])
def test_full_guard_rechecked_inside_existing_dispatch_lock(
    reservation, monkeypatch, stage
):
    from omac.pipeline.operator_review_recovery import DISPATCH_JOURNAL

    c = reservation
    read = c.engine.store.observe_work_item_control
    changed = []

    def racing(item_id):
        record = c.manifest.meta.get(DISPATCH_JOURNAL, {}).get(c.token, {})
        if record.get("step") == stage and not changed:
            changed.append(stage)
            c.item.contract = {"raced": True}
        return read(item_id)

    monkeypatch.setattr(c.engine.store, "observe_work_item_control", racing)
    assert collect(c)
    assert changed and c.calls == []
    if stage == "assign":
        assert c.item.platform_assignee_id is None


@pytest.mark.parametrize(
    "drift",
    [
        "step",
        "receipt",
        "done",
        "approved_budget",
        "assignee",
        "new_stop",
        "delayed_target",
    ],
)
def test_pending_wake_drift_never_creates_second_run(reservation, monkeypatch, drift):
    from omac.errors import PlatformError
    from omac.pipeline.operator_review_recovery import DISPATCH_JOURNAL

    c = reservation

    def invisible(*args):
        c.calls.append(args)
        raise PlatformError("accepted wake; target not visible")

    monkeypatch.setattr(c.engine.runtime, "wake", invisible)
    assert collect(c)
    assert len(c.calls) == 1
    record = c.manifest.meta[DISPATCH_JOURNAL][c.token]
    if drift == "step":
        record["step"] = "assigned"
    elif drift == "receipt":
        c.manifest.meta["operator_review_recovery"][c.token]["request"]["reason"] = (
            "tampered"
        )
    elif drift == "done":
        next(
            n for n in c.manifest.nodes.values() if n.status == "done"
        ).description += " changed"
    elif drift == "approved_budget":
        c.manifest.meta["amendment_apply"]["unexpected"] = {"budget": 999}
    elif drift == "assignee":
        c.item.platform_assignee_id = None
    elif drift == "new_stop":
        c.item.decision_required = {"operator": "stop"}
    elif drift == "delayed_target":
        created = datetime.fromisoformat(record["wake_started_at"]) - timedelta(
            seconds=2
        )
        c.runs.append(
            AgentRunObservation(
                id="old-delayed",
                kind="direct",
                status="running",
                agent_id=c.item.reviewer_run_baseline.target_agent_id,
                created_at=created.isoformat(),
                trigger_kind="issue_assignment",
            )
        )
    assert collect(c)
    assert len(c.calls) == 1 and c.item.reviewer_run_baseline.target_run_id is None


@pytest.mark.parametrize("historical_complete", [True, False])
def test_historical_receipt_selection_preserves_ambiguity_guard(
    reservation, historical_complete
):
    from omac.pipeline.operator_review_recovery import DISPATCH_JOURNAL, _digest

    c = reservation
    history = deepcopy(c.entry)
    history["request"]["reason"] += " earlier archived reservation"
    historical_token = _digest(history["request"])
    history["request_sha256"] = historical_token
    c.manifest.meta["operator_review_recovery"][historical_token] = history
    historical_record = {"step": "complete"}
    historical_record["checkpoint_sha256"] = _digest(historical_record)
    if historical_complete:
        c.manifest.meta[DISPATCH_JOURNAL] = {
            historical_token: deepcopy(historical_record)
        }
    before = deepcopy(c.manifest.meta["operator_review_recovery"])
    failures = collect(c)
    if historical_complete:
        assert not failures, failures
        assert len(c.calls) == 1
        assert c.item.reviewer_run_baseline.target_run_id == c.runs[-1].id
        assert c.manifest.meta[DISPATCH_JOURNAL][historical_token] == historical_record
    else:
        assert failures
        assert c.calls == []
        assert c.item.reviewer_run_baseline.target_run_id is None
    assert c.manifest.meta["operator_review_recovery"] == before


def test_same_second_predating_run_cannot_bind_reserved_wake(reservation, monkeypatch):
    from omac.errors import PlatformError
    import omac.pipeline.operator_review_recovery as recovery

    c = reservation
    intent = datetime(2026, 10, 4, 14, 1, 2, 900000, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return intent

    def invisible(*args):
        c.calls.append(args)
        raise PlatformError("wake outcome unknown")

    monkeypatch.setattr(recovery, "datetime", Clock)
    monkeypatch.setattr(c.engine.runtime, "wake", invisible)
    assert collect(c)
    c.runs.append(
        AgentRunObservation(
            id="delayed-before-actual-intent",
            kind="direct",
            status="running",
            agent_id=c.item.reviewer_run_baseline.target_agent_id,
            created_at=(intent - timedelta(microseconds=1)).isoformat(),
            trigger_kind="issue_assignment",
        )
    )
    c.manifest = load_manifest(c.path)
    assert collect(c)
    assert len(c.calls) == 1
    assert c.item.reviewer_run_baseline.target_run_id is None


def test_checkpoint_tamper_at_final_guard_does_not_wake(reservation, monkeypatch):
    from omac.pipeline.operator_review_recovery import DISPATCH_JOURNAL

    c = reservation
    read = c.engine.store.observe_work_item_control

    def racing(item_id):
        record = c.manifest.meta.get(DISPATCH_JOURNAL, {}).get(c.token, {})
        if record.get("step") == "wake":
            record["step"] = "assigned"
        return read(item_id)

    monkeypatch.setattr(c.engine.store, "observe_work_item_control", racing)
    assert collect(c)
    assert c.calls == [] and c.item.reviewer_run_baseline.target_run_id is None

"""Offline actual Multica clear representation and exact pending request replay."""

from copy import deepcopy
from dataclasses import fields
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
    EngineConfig,
    PullRequestReadiness,
    TaskPhase,
    WorkItem,
    WorkItemStatus,
)
from omac.engines.multica import MulticaStore
from omac.errors import PlatformError, ValidationError
from omac.pipeline.operator_review_recovery import (
    JOURNAL,
    _control,
    _digest,
    _same_control,
)
from test_operator_review_recovery import held as _held_fixture, api_apply, api_prepare

held = _held_fixture

TOKEN = "26459918f63e75ed7f8037b3dda5a255c95d7d140bf5562817b923faa91fd42e"
FOLDER = Path(__file__).parent / "fixtures/operator_assignment_observation"


def multica_clear(c, monkeypatch, unknown=False):
    """Invoke real owning adapter; stub only external transport/metadata persistence."""
    adapter = MulticaStore(
        EngineConfig(engine_type="multica", workspace_id=c.item.workspace_id)
    )
    calls = []
    monkeypatch.setattr(adapter, "_run_multica", lambda args: calls.append(args))

    def metadata(item_id, key, value):
        assert item_id == c.item.id and key == "reviewer" and value == ""
        native = {"id": item_id, "status": "blocked", "metadata": {key: value}}
        c.item.reviewer = adapter._issue_to_control_projection(
            native, c.item.workspace_id
        ).work_item.reviewer
        c.item.platform_assignee_id = None

    monkeypatch.setattr(adapter, "_set_metadata", metadata)

    def clear(item_id):
        adapter.clear_assignment(item_id)
        if unknown:
            raise PlatformError("accepted Multica clear; response lost")

    monkeypatch.setattr(c.engine.store, "clear_assignment", clear)
    return calls


@pytest.mark.parametrize("unknown", [False, True])
def test_real_multica_clear_is_observable_and_not_repeated(held, monkeypatch, unknown):
    c = held
    request = api_prepare(c)
    original = deepcopy(c.item)
    calls = multica_clear(c, monkeypatch, unknown)
    if unknown:
        with pytest.raises(PlatformError, match="response lost"):
            api_apply(c, request)
    assert api_apply(c, request)["state"] == "ready-for-independent-review"
    assert calls == [["issue", "assign", c.item.id, "--unassign"]]
    assert c.item.reviewer == "" and c.item.platform_assignee_id is None
    assert (
        c.item.bounces == original.bounces
        and c.item.review_ledger_ref == original.review_ledger_ref
    )
    assert api_apply(c, request)["state"] == "already-consumed"
    assert len(calls) == 1


def test_empty_reviewer_not_a_global_nullable_equivalence(held):
    current = _control(held.item)
    empty = deepcopy(current)
    empty["reviewer"] = ""
    absent = deepcopy(current)
    absent["reviewer"] = None
    assert not _same_control(empty, absent)
    held.item.reviewer = ""
    with pytest.raises(ValidationError):
        api_prepare(held)


@pytest.fixture
def captured_pending(held, tmp_path, monkeypatch):
    request_bytes = (FOLDER / "approved-request.json").read_bytes()
    assert len(request_bytes) == 130688
    assert (
        hashlib.sha256(request_bytes).hexdigest()
        == "5a0cbeb08ff640be8f8c64ea7a99a503838c5dfddf410f0578c7e4bc1cd077d4"
    )
    request = json.loads(request_bytes)
    assert _digest(request) == TOKEN
    raw = json.loads((FOLDER / "actual-Store-after.json").read_text())
    assert raw["reviewer"] == "" and raw["platform_assignee_id"] is None
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
    # Decoded fixture proof over mock transports, not original attachment bytes/live eligibility.
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
    runs = [AgentRunObservation(**r) for r in request["source"]["runs"]]
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _id: deepcopy(runs))
    path = tmp_path / "actual-pending.yaml"
    path.write_bytes((FOLDER / "whole-current-manifest.yaml").read_bytes())
    manifest = load_manifest(str(path))
    entry = manifest.meta[JOURNAL][TOKEN]
    assert (
        entry["state"] == "pending"
        and entry["step"] == 0
        and entry["request"] == request
    )
    config = {"retry": {**request["source"]["budget"]["limits"], "no_submit_runs": 6}}
    return SimpleNamespace(
        engine=engine,
        item=item,
        path=str(path),
        key=request["node_id"],
        runs=runs,
        request=request,
        config=config,
    )


@pytest.mark.parametrize("unknown_next", [False, True])
def test_actual_old_pending_request_checkpoints_clear_before_next_write(
    captured_pending, monkeypatch, unknown_next
):
    c = captured_pending
    before = deepcopy(c.item)
    manifest = load_manifest(c.path)
    receipt = deepcopy(manifest.meta[JOURNAL][TOKEN])
    monkeypatch.setattr(
        c.engine.store,
        "clear_assignment",
        lambda *_a: pytest.fail("Already accepted clear must not repeat"),
    )
    monkeypatch.setattr(
        c.engine.runtime, "wake", lambda *_a, **_kw: pytest.fail("No actor")
    )
    metadata = c.engine.store.update_work_item_metadata
    calls = []

    def update(item_id, **kw):
        observed = load_manifest(c.path).meta[JOURNAL][TOKEN]
        assert observed["request"] == receipt["request"] and observed["step"] >= 1
        calls.append(deepcopy(kw))
        result = metadata(item_id, **kw)
        if unknown_next and len(calls) == 1:
            raise PlatformError("accepted baseline; response lost")
        return result

    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", update)
    if unknown_next:
        with pytest.raises(PlatformError, match="response lost"):
            api_apply(c, c.request, c.config)
        assert load_manifest(c.path).meta[JOURNAL][TOKEN]["step"] == 1
    assert api_apply(c, c.request, c.config)["state"] == "ready-for-independent-review"
    assert len(calls) == 2
    result = load_manifest(c.path)
    assert result.meta[JOURNAL][TOKEN]["request"] == receipt["request"]
    assert result.meta[JOURNAL][TOKEN]["state"] == "consumed"
    assert c.item.reviewer_run_baseline.attempt == 2
    assert (
        c.item.bounces == before.bounces
        and c.item.bounce_baseline == before.bounce_baseline
    )
    assert (
        c.item.review_ledger == before.review_ledger
        and c.item.review_ledger_ref == before.review_ledger_ref
    )
    assert (
        c.item.delivery_identity == before.delivery_identity
        and c.item.review_generation == before.review_generation
    )
    assert {k: v for k, v in result.meta.items() if k != JOURNAL} == {
        k: v for k, v in manifest.meta.items() if k != JOURNAL
    }
    assert all(
        result.nodes[k] == node for k, node in manifest.nodes.items() if k != c.key
    )
    assert (
        len(manifest.nodes) == 183
        and sum(n.status == "done" for n in manifest.nodes.values()) == 73
    )
    c.item.bounces.review += 1
    c.item.status = WorkItemStatus.DONE
    assert api_apply(c, c.request, c.config)["state"] == "already-consumed"
    assert (
        c.item.status == WorkItemStatus.DONE
        and c.item.bounces.review == before.bounces.review + 1
        and len(calls) == 2
    )


@pytest.mark.parametrize(
    "drift",
    [
        "reviewer",
        "whitespace",
        "assignee",
        "generation",
        "report",
        "ledger",
        "hold",
        "budget",
        "contract",
        "done",
        "queued",
        "delayed",
        "limit",
    ],
)
def test_empty_clear_observation_still_rejects_other_drift(held, monkeypatch, drift):
    c = held
    request = api_prepare(c)
    manifest = load_manifest(c.path)
    manifest.meta[JOURNAL] = {
        _digest(request): {
            "request_sha256": _digest(request),
            "request": request,
            "state": "pending",
            "step": 0,
        }
    }
    manifest.nodes[c.key].recovery_marker = True
    c.item.reviewer = ""
    if drift == "reviewer":
        c.item.reviewer = "another-reviewer"
    elif drift == "whitespace":
        c.item.reviewer = " "
    elif drift == "assignee":
        c.item.platform_assignee_id = "another-agent"
    elif drift == "generation":
        c.item.review_generation = None
    elif drift == "report":
        c.item.review_report_ref = {"attachment_id": "accepted"}
    elif drift == "ledger":
        c.item.review_ledger_generation = c.item.review_generation
    elif drift == "hold":
        c.item.decision_required["next_action"] = "changed"
    elif drift == "budget":
        c.item.bounces.review += 1
    elif drift == "contract":
        c.item.contract = {"changed": True}
    elif drift == "done":
        manifest.nodes[c.key].status = "done"
    elif drift in {"queued", "delayed"}:
        c.runs.append(
            AgentRunObservation(
                id="late",
                kind="direct",
                status="queued" if drift == "queued" else "completed",
                agent_id=c.runs[0].agent_id,
                created_at=c.runs[0].created_at,
                retry_of_run_id=c.runs[0].id,
                trigger_kind="issue_assignment",
            )
        )
    config = {"retry": {"review": 99}} if drift == "limit" else {}
    save_manifest(manifest, c.path)
    before, raw = deepcopy(c.item), Path(c.path).read_bytes()
    monkeypatch.setattr(
        c.engine.store, "clear_assignment", lambda *_a: pytest.fail("No repeat clear")
    )
    monkeypatch.setattr(
        c.engine.store,
        "update_work_item_metadata",
        lambda *_a, **_kw: pytest.fail("No next write"),
    )
    monkeypatch.setattr(
        c.engine.store, "update_status", lambda *_a, **_kw: pytest.fail("No next write")
    )
    with pytest.raises(ValidationError):
        api_apply(c, request, config)
    assert c.item == before and Path(c.path).read_bytes() == raw

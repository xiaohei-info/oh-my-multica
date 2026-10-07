"""New typed continuation must fail closed without its rejected source proof."""

from copy import deepcopy
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from omac.core.manifest import load_manifest, save_manifest
from fixture_initial_images import seed_manifest
from omac.core.taskmeta import (
    Bounces,
    TaskKind,
    TaskPhase,
    parse_worker_handoff,
    parse_delivery_identity,
    parse_reviewer_run_baseline,
)
from omac.engines import create_engine
from omac.engines.models import (
    EngineConfig,
    WorkItem,
    WorkItemStatus,
    AgentRunObservation,
    VerificationAttachmentObservation,
    PullRequestReadiness,
)

from omac.errors import ValidationError

FIXTURES = Path(__file__).parent / "fixtures/evidence_handoff"


@pytest.fixture
def captured(tmp_path, monkeypatch):
    from omac.engines import mock

    data = json.loads((FIXTURES / "current-control.json").read_bytes())
    control = deepcopy(data["control"])
    control["kind"] = TaskKind(control["kind"])
    control["phase"] = TaskPhase(control["phase"])
    control["status"] = WorkItemStatus(control["status"])
    control["bounces"] = Bounces(**control["bounces"])
    control["worker_handoff"] = parse_worker_handoff(control["worker_handoff"])
    assert set(control) == {f.name for f in fields(WorkItem)}
    item = WorkItem(**control)
    engine = create_engine(
        "mock",
        EngineConfig("mock", item.workspace_id, extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    mock._shared_work_items[item.id] = item
    all_captures = json.loads((FIXTURES / "all-controls-runs.json").read_bytes())[
        "issues"
    ]
    for capture in all_captures:
        raw = deepcopy(capture["control"])
        if raw["id"] == item.id:
            continue
        raw.update(
            status=WorkItemStatus(raw["status"]),
            kind=TaskKind(raw["kind"]),
            phase=TaskPhase(raw["phase"]),
            bounces=Bounces(**raw["bounces"]),
            worker_handoff=parse_worker_handoff(raw.get("worker_handoff")),
            delivery_identity=parse_delivery_identity(raw.get("delivery_identity")),
            reviewer_run_baseline=parse_reviewer_run_baseline(
                raw.get("reviewer_run_baseline")
            ),
        )
        mock._shared_work_items[raw["id"]] = WorkItem(**raw)
    agent_ids = {
        item.worker: item.worker_handoff.target_agent_id,
        "hermes-reviewer": "94f847b9-c10f-4d64-88b2-e90cb5c03ffc",
    }
    monkeypatch.setattr(engine.store, "resolve_agent_id", lambda name: agent_ids[name])
    runs = [AgentRunObservation(**r) for r in data["runs"]]
    captured_runs = {
        row["control"]["id"]: [AgentRunObservation(**r) for r in row["runs"]]
        for row in all_captures
    }
    captured_runs[item.id] = runs
    monkeypatch.setattr(
        engine.runtime,
        "list_runs",
        lambda item_id: (
            deepcopy(captured_runs.get(item_id, []))
            + list(mock._shared_runs.get(item_id, []))
        ),
    )
    monkeypatch.setattr(
        engine.runtime,
        "is_active",
        lambda item_id: any(r.active for r in engine.runtime.list_runs(item_id)),
    )
    native = json.loads((FIXTURES / "current-native.json").read_bytes())
    comment = json.loads((FIXTURES / "old-native-comment.json").read_bytes())[0]
    old_attachment = comment["attachments"][0]
    old_native = {
        "attachment_id": old_attachment["id"],
        "comment_id": comment["id"],
        "sha256": hashlib.sha256(
            (FIXTURES / "old-verification.yaml").read_bytes()
        ).hexdigest(),
        "uploader_id": old_attachment["uploader_id"],
        "uploader_type": old_attachment["uploader_type"],
        "task_id": comment["source_task_id"],
        "created_at": old_attachment["created_at"],
    }
    observations = {}
    for meta, file in [
        (native, "current-verification.yaml"),
        (old_native, "old-verification.yaml"),
    ]:
        observations[meta["attachment_id"]] = VerificationAttachmentObservation(
            **{
                k: meta[k]
                for k in (
                    "attachment_id",
                    "comment_id",
                    "sha256",
                    "uploader_id",
                    "uploader_type",
                    "task_id",
                    "created_at",
                )
            },
            content=(FIXTURES / file).read_bytes(),
        )
    for meta, file in zip(
        json.loads((FIXTURES / "report-ledger-native.json").read_bytes())[
            "original_report_ledger_full_native_observations"
        ],
        ("report.yaml", "ledger.yaml"),
    ):
        observations[meta["attachment_id"]] = VerificationAttachmentObservation(
            **{
                k: meta[k]
                for k in (
                    "attachment_id",
                    "comment_id",
                    "sha256",
                    "uploader_id",
                    "uploader_type",
                    "task_id",
                    "created_at",
                )
            },
            content=(FIXTURES / file).read_bytes(),
        )
    original_attachment = engine.store.observe_verification_attachment
    monkeypatch.setattr(
        engine.store,
        "observe_verification_attachment",
        lambda _id, ref: (
            observations[ref["attachment_id"]]
            if ref["attachment_id"] in observations
            else original_attachment(_id, ref)
        ),
    )
    original_update = engine.store.update_work_item_metadata

    def publish_owned_obligations(item_id, **kwargs):
        from datetime import datetime, timezone

        uploaded_at = datetime.now(timezone.utc).isoformat()
        result = original_update(item_id, **kwargs)
        if kwargs.get("review_verdict"):
            from dataclasses import replace

            # Mock submit marks terminal without a platform end timestamp.
            # Model the real terminal observation at this OFFLINE transport seam.
            mock._shared_runs[item_id] = [
                replace(run, updated_at=datetime.now(timezone.utc).isoformat())
                if run.terminal and run.updated_at is None
                else run
                for run in mock._shared_runs[item_id]
            ]
        for field in ("review_report", "review_ledger"):
            if field + "_source" in kwargs:
                run = mock._shared_runs[item_id][-1]
                body = kwargs[field + "_source"].encode()
                ref = {
                    "attachment_id": "offline-native-"
                    + field
                    + "-"
                    + str(len(observations)),
                    "comment_id": "offline-native-comment-" + field,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                    "uploader_type": "agent",
                    "uploader_id": run.agent_id,
                    "task_id": run.id,
                    "created_at": uploaded_at,
                }
                observations[ref["attachment_id"]] = VerificationAttachmentObservation(
                    ref["attachment_id"],
                    ref["comment_id"],
                    ref["sha256"],
                    body,
                    run.agent_id,
                    "agent",
                    run.id,
                    uploaded_at,
                )
                setattr(
                    result,
                    field + "_ref",
                    {
                        k: ref[k]
                        for k in ("attachment_id", "comment_id", "sha256", "bytes")
                    },
                )
        if "review_obligations" in kwargs:
            import yaml

            body = yaml.safe_dump(
                result.review_obligations, allow_unicode=True, sort_keys=False
            ).encode()
            ref = {
                "attachment_id": "offline-owned-obligations-" + str(len(observations)),
                "comment_id": "offline-owned-obligations-comment",
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "filename": "omac-review-obligations.yaml",
            }
            observations[ref["attachment_id"]] = VerificationAttachmentObservation(
                ref["attachment_id"],
                ref["comment_id"],
                ref["sha256"],
                body,
                None,
                None,
                None,
                None,
            )
            result.review_obligations_ref = ref
        return result

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", publish_owned_obligations
    )
    uris = json.loads((FIXTURES / "uris.json").read_bytes())
    monkeypatch.setattr(
        engine.store,
        "read_immutable_artifact",
        lambda url: (FIXTURES / "blobs" / uris[url]).read_bytes(),
    )
    monkeypatch.setattr(
        engine.store,
        "read_pull_request_readiness",
        lambda _: PullRequestReadiness(False, "OPEN", item.artifacts["head_sha"]),
    )
    manifest = load_manifest(str(FIXTURES / "current79.yaml"))
    mock._shared_contracts_by_item_id[item.id] = manifest.nodes[data["node"]].contract
    shutil.copytree(FIXTURES / "required-context", tmp_path, dirs_exist_ok=True)
    (tmp_path / manifest.meta["acceptance_file"]).write_bytes(
        (FIXTURES / "acceptance.yaml").read_bytes()
    )
    path = str(tmp_path / "dag.yaml")
    seed_manifest(FIXTURES / "current79.yaml", path)
    limits = json.loads((FIXTURES / "limits.json").read_bytes())
    config = {"retry": limits["limits"] | {"no_submit_runs": limits["no_submit_limit"]}}
    return SimpleNamespace(
        engine=engine,
        item=item,
        manifest=manifest,
        path=path,
        key=data["node"],
        config=config,
        source=str(FIXTURES / "source-workshow.json"),
        history=[
            str(FIXTURES / "original-native181.json"),
            str(FIXTURES / "unsubmitted54070.json"),
        ],
        runs=runs,
        observations=observations,
        uris=uris,
    )


def test_actual_rejected41_to_current42_prepare_preserves_full_sources(captured):
    from omac.pipeline.evidence_handoff import prepare_evidence_handoff

    c = captured
    original = deepcopy(c.item)
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        config=c.config,
        history_files=c.history,
    )
    assert c.item == original
    assert load_manifest(c.path) == c.manifest
    assert len(request["tuple"]["original_publication"]["artifacts"]) == 41
    assert len(request["tuple"]["current_publication"]["artifacts"]) == 42
    assert request["tuple"]["historical_sealed_control"] is None
    assert request["tuple"]["binding"]["generation"] is None
    assert request["tuple"]["budget"]["absolute"]["worker"] == 11
    assert request["tuple"]["budget"]["absolute"]["review"] == 2


def test_exact_resolution_and_owned_eight_step_seal(captured):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
        consume_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        config=c.config,
        history_files=c.history,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline explicit coordinator",
        reason="Exact bounded evidence continuation, no product verdict",
        config=c.config,
    )
    manifest = load_manifest(c.path)
    assert consume_evidence_handoff(
        c.engine.store, c.engine.runtime, manifest, c.path, c.key, c.config
    )
    assert (
        c.item.delivery_identity.verification_sha256
        == request["tuple"]["current_native"]["sha256"]
    )
    assert c.item.delivery_identity.run_id == "01a10958-3483-750c-8635-a3d8373bd57a"
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2
    assert c.item.worker_handoff is None
    assert c.item.phase == TaskPhase.REVIEW
    assert manifest.nodes[c.key].status == "in_review"
    assert not consume_evidence_handoff(
        c.engine.store, c.engine.runtime, manifest, c.path, c.key, c.config
    )


def test_normal_collection_creates_one_fresh_independent_review(captured):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest
    from omac.pipeline.loop import collect_results

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline exact coordinator",
        reason="Independent evidence review only",
        config=c.config,
    )
    original = deepcopy(c.manifest)
    manifest = load_manifest(c.path)
    failures = collect_results(
        c.engine.store,
        c.engine.runtime,
        manifest,
        c.path,
        retry_limits=c.config["retry"],
        config=c.config,
    )
    assert failures == {}
    fresh = [
        r
        for r in c.engine.runtime.list_runs(c.item.id)
        if r.id not in {r.id for r in c.runs}
    ]
    assert len(fresh) == 1 and fresh[0].active
    assert fresh[0].agent_id == "94f847b9-c10f-4d64-88b2-e90cb5c03ffc"
    assert fresh[0].id != c.item.delivery_identity.run_id
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2
    assert c.item.review_verdict is None and c.item.review_generation is None
    assert sum(n.status == "done" for n in manifest.nodes.values()) == 79
    for key, node in original.nodes.items():
        if key != c.key:
            assert manifest.nodes[key] == node
    # A premature retired flag cannot disable the owner gate.
    disk = load_manifest(c.path)
    original_disk = deepcopy(disk)
    record = next(iter(disk.meta["rejected_evidence_handoffs"].values()))
    record["review_dispatch"]["state"] = "normal-review-handed-off"
    save_manifest(disk, c.path)
    before = deepcopy(c.item)
    with pytest.raises(ValidationError, match="retirement"):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert c.item == before
    save_manifest(original_disk, c.path)
    # Restart observes this causal Run; neither assignment nor wake repeats.
    again = load_manifest(c.path)
    assert (
        collect_results(
            c.engine.store,
            c.engine.runtime,
            again,
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
        == {}
    )
    assert (
        len(
            [
                r
                for r in c.engine.runtime.list_runs(c.item.id)
                if r.id not in {r.id for r in c.runs}
            ]
        )
        == 1
    )


def test_public_cli_prepare_and_exact_resolve(captured, monkeypatch, capsys):
    from omac.cli.main import main
    import omac.cli.commands.node as cli

    c = captured
    monkeypatch.setattr(cli, "load_config", lambda: c.config)
    monkeypatch.setattr(cli, "_build_engine", lambda _: c.engine)
    args = ["node", "continue-evidence", c.path, c.key]
    assert (
        main(
            args
            + [
                "--rejected-source-file",
                c.source,
                "--history-file",
                c.history[0],
                "--history-file",
                c.history[1],
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["verdict"] is None
    path = str(Path(c.path).parent / "request.json")
    Path(path).write_text(json.dumps(prepared))
    assert (
        main(
            args
            + [
                "--resolve-request",
                path,
                "--request-sha256",
                prepared["request_sha256"],
                "--authority",
                "offline coordinator",
                "--reason",
                "Exact independent review only",
            ]
        )
        == 0
    )
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["verdict"] is None
    assert c.item.worker_handoff is not None
    assert c.item.delivery_identity is None


@pytest.mark.parametrize(
    "drift",
    [
        "head",
        "generation",
        "control",
        "worker-budget",
        "limits",
        "DONE",
        "acceptance",
        "required-contract",
        "no-submit-limit",
        "config",
        "queued",
        "running",
        "unknown",
        "nonformal",
        "duplicate",
        "native-uploader",
        "native-task",
        "native-time",
        "native-body",
        "failed-report",
        "failed-ledger",
        "source-workshow",
        "publication",
    ],
)
def test_exact_resolution_rejects_full_source_drift_before_effect(captured, drift):
    from dataclasses import replace
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    if drift == "head":
        c.item.artifacts["head_sha"] = "0" * 40
    elif drift == "generation":
        c.item.review_generation = "forged-generation"
    elif drift == "control":
        c.item.title += " changed"
    elif drift == "worker-budget":
        c.item.bounces.worker += 1
    elif drift == "limits":
        c.config["retry"]["review"] += 1
    elif drift == "no-submit-limit":
        c.config["retry"]["no_submit_runs"] += 1
    elif drift == "config":
        c.config["ci"] = {"check_command": "changed"}
    elif drift == "DONE":
        manifest = load_manifest(c.path)
        next(
            n for n in manifest.nodes.values() if n.status == "done"
        ).recovery_marker = {"changed": True}
        save_manifest(manifest, c.path)
    elif drift == "acceptance":
        path = Path(c.path).parent / c.manifest.meta["acceptance_file"]
        path.write_bytes(path.read_bytes() + b"\n# changed\n")
    elif drift == "required-contract":
        path = (
            Path(c.path).parent
            / request["tuple"]["required_inputs"]["files"]["docs_files"][0]
        )
        path.write_bytes(path.read_bytes() + b"\n# changed\n")
    elif drift in {"queued", "running", "unknown", "nonformal", "duplicate"}:
        if drift == "duplicate":
            c.runs.append(c.runs[0])
        elif drift == "nonformal":
            c.runs[0] = replace(c.runs[0], trigger_kind="chat")
        else:
            c.runs[0] = replace(c.runs[0], status=drift)
    elif drift.startswith("native-"):
        attachment = c.item.verification_ref["attachment_id"]
        obs = c.observations[attachment]
        kwargs = {
            "native-uploader": {"uploader_id": "foreign"},
            "native-task": {"task_id": "unknown"},
            "native-time": {"created_at": "2020-01-01T00:00:00Z"},
            "native-body": {"content": obs.content + b"\n"},
        }[drift]
        c.observations[attachment] = replace(obs, **kwargs)
    elif drift in {"failed-report", "failed-ledger"}:
        field = "report_ref" if drift == "failed-report" else "ledger_ref"
        attachment = c.item.worker_handoff.source_review_feedback[field][
            "attachment_id"
        ]
        c.observations[attachment] = replace(
            c.observations[attachment], uploader_id="foreign"
        )
    elif drift == "source-workshow":
        request["tuple"]["source_file"]["sha256"] = "0" * 64
    elif drift == "publication":
        c.uris[next(iter(c.uris))] = "missing"
    original = deepcopy(c.item)
    disk = Path(c.path).read_bytes()
    with pytest.raises((ValidationError, FileNotFoundError)):
        resolve_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            c.path,
            c.key,
            request,
            request_sha256=_digest(request),
            authority="offline coordinator",
            reason="Exact only",
            config=c.config,
        )
    assert c.item == original
    assert Path(c.path).read_bytes() == disk


@pytest.mark.parametrize(
    "step",
    [
        "delivery_identity",
        "worker_handoff",
        "review_ledger_generation",
        "review_obligations",
        "review_subject_digest",
        "phase",
        "decision_required",
        "status",
    ],
)
@pytest.mark.parametrize("accepted", [False, True])
def test_eight_step_unknown_restart_never_repeats_accepted_effect(
    captured, monkeypatch, step, accepted
):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
        consume_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest
    from omac.errors import PlatformError

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    metadata = c.engine.store.update_work_item_metadata
    status = c.engine.store.update_status
    lost = False
    effects = []

    def perform(name, operation):
        nonlocal lost
        if name == step and not lost:
            lost = True
            if accepted:
                operation()
                effects.append(name)
            raise PlatformError("offline Unknown write result")
        result = operation()
        effects.append(name)
        return result

    monkeypatch.setattr(
        c.engine.store,
        "update_work_item_metadata",
        lambda item_id, **kwargs: perform(
            next(iter(kwargs)), lambda: metadata(item_id, **kwargs)
        ),
    )
    monkeypatch.setattr(
        c.engine.store,
        "update_status",
        lambda item_id, value: perform("status", lambda: status(item_id, value)),
    )
    with pytest.raises(PlatformError, match="Unknown"):
        consume_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            c.key,
            c.config,
        )
    assert lost
    resumed = load_manifest(c.path)
    assert consume_evidence_handoff(
        c.engine.store, c.engine.runtime, resumed, c.path, c.key, c.config
    )
    assert max(effects.count(name) for name in effects) == 1
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2
    assert c.item.phase == TaskPhase.REVIEW
    assert sum(n.status == "done" for n in resumed.nodes.values()) == 79
    assert not consume_evidence_handoff(
        c.engine.store, c.engine.runtime, resumed, c.path, c.key, c.config
    )


def test_unconfirmed_journal_has_zero_control_effects(captured, monkeypatch):
    from omac.pipeline import evidence_review
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
        consume_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    original = deepcopy(c.item)
    monkeypatch.setattr(evidence_review, "save_manifest", lambda *args: None)
    with pytest.raises(ValidationError, match="unconfirmed"):
        consume_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            c.key,
            c.config,
        )
    assert c.item == original


@pytest.mark.parametrize("stage", ["assign", "wake"])
@pytest.mark.parametrize("accepted", [False, True])
def test_unknown_reviewer_effect_is_observed_or_stops_without_repeat(
    captured, monkeypatch, stage, accepted
):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest
    from omac.pipeline.loop import collect_results
    from omac.errors import PlatformError, NeedsDecision

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    assigns = c.engine.store.assign_work_item
    wake = c.engine.runtime.wake
    calls = []

    def assign(item_id, agent, role, **kwargs):
        calls.append("assign")
        if stage == "assign":
            if accepted:
                assigns(item_id, agent, role, **kwargs)
            raise PlatformError("Unknown assignment")
        return assigns(item_id, agent, role, **kwargs)

    def wake_once(item_id, agent, role):
        calls.append("wake")
        if stage == "wake":
            if accepted:
                wake(item_id, agent, role)
            raise PlatformError("Unknown wake")
        return wake(item_id, agent, role)

    monkeypatch.setattr(c.engine.store, "assign_work_item", assign)
    monkeypatch.setattr(c.engine.runtime, "wake", wake_once)
    with pytest.raises(PlatformError, match="Unknown"):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert c.item.status == WorkItemStatus.IN_REVIEW
    assert c.item.decision_required in (None, {})
    before = list(calls)
    fresh = [
        r
        for r in c.engine.runtime.list_runs(c.item.id)
        if r.id not in {r.id for r in c.runs}
    ]
    if stage == "wake" and accepted:
        assert len(fresh) == 1
        assert (
            collect_results(
                c.engine.store,
                c.engine.runtime,
                load_manifest(c.path),
                c.path,
                retry_limits=c.config["retry"],
                config=c.config,
            )
            == {}
        )
        assert c.item.reviewer_run_baseline.target_run_id == fresh[0].id
    else:
        with pytest.raises(NeedsDecision):
            collect_results(
                c.engine.store,
                c.engine.runtime,
                load_manifest(c.path),
                c.path,
                retry_limits=c.config["retry"],
                config=c.config,
            )
    assert calls == before
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2


@pytest.mark.parametrize(
    "invalid", ["empty-native", "missing-record", "foreign-task", "dummy-draft"]
)
def test_complete_original_failed_history_cannot_be_omitted(captured, invalid):
    from omac.pipeline.evidence_handoff import prepare_evidence_handoff

    c = captured
    native = json.loads(Path(c.history[0]).read_bytes())
    draft = json.loads(Path(c.history[1]).read_bytes())
    if invalid == "empty-native":
        native = []
    elif invalid == "missing-record":
        native.pop(0)
    elif invalid == "foreign-task":
        native[0]["task_id"] = "unknown"
    else:
        draft = {"review_protocol": "omac.review/v2"}
    paths = [
        str(Path(c.path).parent / "native.json"),
        str(Path(c.path).parent / "draft.json"),
    ]
    Path(paths[0]).write_text(json.dumps(native))
    Path(paths[1]).write_text(json.dumps(draft))
    original = deepcopy(c.item)
    with pytest.raises(ValidationError):
        prepare_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            c.manifest,
            c.path,
            c.key,
            rejected_source=c.source,
            history_files=paths,
            config=c.config,
        )
    assert c.item == original


def test_coherent_failed_report_corruption_is_not_reject_authority(captured):
    from dataclasses import replace
    import yaml
    from omac.core.review_convergence import _review_report_digest
    from omac.pipeline.evidence_handoff import prepare_evidence_handoff

    c = captured
    feedback = c.item.worker_handoff.source_review_feedback
    report = yaml.safe_load((FIXTURES / "report.yaml").read_bytes())
    ledger = deepcopy(c.item.review_ledger)
    report["full_review_completed"] = False
    ledger["cycles"][-1]["report_digest"] = _review_report_digest(report)
    for field, body in [("report_ref", report), ("ledger_ref", ledger)]:
        ref = feedback[field]
        raw = yaml.safe_dump(body, sort_keys=False).encode()
        ref.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        c.observations[ref["attachment_id"]] = replace(
            c.observations[ref["attachment_id"]], content=raw, sha256=ref["sha256"]
        )
    c.item.review_ledger = ledger
    c.item.review_ledger_ref = deepcopy(feedback["ledger_ref"])
    original = deepcopy(c.item)
    with pytest.raises(ValidationError, match="malformed"):
        prepare_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            c.manifest,
            c.path,
            c.key,
            rejected_source=c.source,
            history_files=c.history,
            config=c.config,
        )
    assert c.item == original


def test_attachment_novelty_without_changed_publication_cannot_continue(captured):
    from dataclasses import replace
    import yaml
    from omac.pipeline.evidence_handoff import prepare_evidence_handoff

    c = captured
    raw = (FIXTURES / "old-verification.yaml").read_bytes()
    c.item.verification = yaml.safe_load(raw)
    ref = c.item.verification_ref
    ref.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    c.observations[ref["attachment_id"]] = replace(
        c.observations[ref["attachment_id"]], content=raw, sha256=ref["sha256"]
    )
    with pytest.raises(ValidationError, match="genuinely changed"):
        prepare_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            c.manifest,
            c.path,
            c.key,
            rejected_source=c.source,
            history_files=c.history,
            config=c.config,
        )


@pytest.mark.parametrize("bad", ["unapproved", "forged-approval", "wrong-request"])
def test_unapproved_or_forged_request_has_zero_control_effects(captured, bad):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
        consume_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    original = deepcopy(c.item)
    if bad == "unapproved":
        assert not consume_evidence_handoff(
            c.engine.store, c.engine.runtime, c.manifest, c.path, c.key, c.config
        )
    elif bad == "wrong-request":
        with pytest.raises(ValidationError):
            resolve_evidence_handoff(
                c.engine.store,
                c.engine.runtime,
                c.path,
                c.key,
                request,
                request_sha256="0" * 64,
                authority="offline coordinator",
                reason="Exact only",
                config=c.config,
            )
    else:
        resolve_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            c.path,
            c.key,
            request,
            request_sha256=_digest(request),
            authority="offline coordinator",
            reason="Exact only",
            config=c.config,
        )
        manifest = load_manifest(c.path)
        manifest.meta["rejected_evidence_resolutions"][_digest(request)]["approval"][
            "authority"
        ] = "forged"
        save_manifest(manifest, c.path)
        with pytest.raises(ValidationError):
            consume_evidence_handoff(
                c.engine.store, c.engine.runtime, manifest, c.path, c.key, c.config
            )
    assert c.item == original


@pytest.mark.parametrize("stage", ["reviewer_run_baseline", "description"])
def test_preparation_full_control_readback_stops_before_assignment(
    captured, monkeypatch, stage
):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest
    from omac.pipeline.loop import collect_results
    from omac.errors import PlatformError

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    metadata, status = (
        c.engine.store.update_work_item_metadata,
        c.engine.store.update_status,
    )
    changed = []

    def update(item_id, **kwargs):
        result = metadata(item_id, **kwargs)
        if stage in kwargs:
            changed.append(stage)
            c.item.title += " concurrent drift"
        return result

    def set_status(item_id, value):
        result = status(item_id, value)
        if stage == "status" and c.item.reviewer_run_baseline:
            changed.append(stage)
            c.item.title += " concurrent drift"
        return result

    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", update)
    monkeypatch.setattr(c.engine.store, "update_status", set_status)

    def forbid_dispatch(*args, **kwargs):
        pytest.fail("Actor assignment/wake must not occur after full control drift")

    monkeypatch.setattr(c.engine.store, "assign_reviewer", forbid_dispatch)
    monkeypatch.setattr(c.engine.runtime, "wake_reviewer", forbid_dispatch)
    with pytest.raises(PlatformError, match="not observable"):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert changed == [stage]
    assert c.item.reviewer in (None, "") and c.item.platform_assignee_id is None
    assert len(c.engine.runtime.list_runs(c.item.id)) == 17
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2


def test_final_seal_effect_full_readback_cannot_ignore_control_drift(
    captured, monkeypatch
):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
        consume_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    status = c.engine.store.update_status

    def drift(item_id, value):
        result = status(item_id, value)
        c.item.title += " concurrent drift"
        return result

    monkeypatch.setattr(c.engine.store, "update_status", drift)
    with pytest.raises(ValidationError, match="Full owned control readback"):
        consume_evidence_handoff(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            c.key,
            c.config,
        )
    record = next(
        iter(load_manifest(c.path).meta["rejected_evidence_handoffs"].values())
    )
    assert record["state"] == "pending"
    assert len(c.engine.runtime.list_runs(c.item.id)) == 17
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", "forged-review-generation"),
        ("attempt", 2),
        ("cutoff_created_at", "2020-01-01T00:00:00Z"),
    ],
)
def test_exact_baseline_drift_after_assignment_never_wakes(
    captured, monkeypatch, field, value
):
    from dataclasses import replace
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest
    from omac.pipeline.loop import collect_results

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    assignment = c.engine.store.assign_work_item

    def drift(item_id, agent, role, **kwargs):
        result = assignment(item_id, agent, role, **kwargs)
        c.item.reviewer_run_baseline = replace(
            c.item.reviewer_run_baseline, **{field: value}
        )
        return result

    monkeypatch.setattr(c.engine.store, "assign_work_item", drift)
    with pytest.raises(ValidationError, match="Exact pinned Reviewer baseline"):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert len(c.engine.runtime.list_runs(c.item.id)) == 17
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2


@pytest.mark.parametrize("prior_active_observation", [False, True])
def test_complete_public_flow_accepts_fresh_native_review_without_prior_poll(
    captured, monkeypatch, capsys, prior_active_observation
):
    import yaml
    from omac.cli.main import main
    import omac.cli.commands.node as node_cli
    import omac.cli.commands.work as work_cli
    from omac.pipeline.loop import collect_results

    c = captured
    monkeypatch.setattr(node_cli, "load_config", lambda: c.config)
    monkeypatch.setattr(node_cli, "_build_engine", lambda _: c.engine)
    monkeypatch.setattr(work_cli, "_resolve_store", lambda: c.engine.store)
    args = ["node", "continue-evidence", c.path, c.key]
    assert (
        main(
            args
            + [
                "--rejected-source-file",
                c.source,
                "--history-file",
                c.history[0],
                "--history-file",
                c.history[1],
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    request_path = str(Path(c.path).parent / "prepared.json")
    Path(request_path).write_text(json.dumps(prepared))
    assert (
        main(
            args
            + [
                "--resolve-request",
                request_path,
                "--request-sha256",
                prepared["request_sha256"],
                "--authority",
                "offline coordinator",
                "--reason",
                "Exact only",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
        == {}
    )
    if prior_active_observation:
        assert (
            collect_results(
                c.engine.store,
                c.engine.runtime,
                load_manifest(c.path),
                c.path,
                retry_limits=c.config["retry"],
                config=c.config,
            )
            == {}
        )
    assert main(["work", "show", c.item.id]) == 0
    shown = json.loads(capsys.readouterr().out)
    obligations = shown["context"]["review_obligations"]
    histories = [o for o in obligations if o.get("kind") == "history-assessment"]
    assert len(histories) == 2
    for o in histories:
        assert (
            hashlib.sha256(o["full_source"].encode()).hexdigest() == o["source_sha256"]
        )
        assert len(o["full_source"].encode()) == o["source_bytes"]
        assert o["accepted_verdict"] is False
    assert sorted(o["source_bytes"] for o in histories) == [54070, 219947]
    # This is explicitly a scripted OFFLINE transport report, not an OAC verdict.
    report = c.engine.store._mock_review_report(c.item.id, "pass-with-nits")
    # The captured contract has trace refs rather than evidence_targets claims.
    # Keep a complete explicit mapping in this synthetic transport report;
    # this does not replace any retained original failing report bytes.
    report["acceptance_mapping"] = [
        {
            "acceptance": row["acceptance"],
            "status": "pass",
            "evidence": "Scripted OFFLINE transport only; no OAC product verdict",
        }
        for row in yaml.safe_load((FIXTURES / "report.yaml").read_bytes())[
            "acceptance_mapping"
        ]
    ]
    report["pr_url"] = c.item.artifacts["pr_url"]
    report["source_commit"] = c.item.artifacts["head_sha"]
    pub = prepared["request"]["tuple"]["current_publication"]
    report["evidence_publication"] = {
        "index_commit": pub["url"].split("/")[6],
        "index_sha256": pub["sha256"],
        "payload_commits": sorted(
            {row["publication"]["commit"] for row in pub["artifacts"]}
        ),
    }
    report["history_assessment"] = [
        {
            "obligation_id": o["obligation_id"],
            "source_sha256": o["source_sha256"],
            "source_bytes": o["source_bytes"],
            "accepted_verdict": False,
            "disposition": "Scripted OFFLINE assessment; retain all failed bytes, no accepted historical verdict",
        }
        for o in c.item.review_obligations
        if o.get("kind") == "history-assessment"
    ]
    report_path = str(Path(c.path).parent / "offline-review.yaml")
    original = deepcopy(c.item)
    for bad in (
        "missing-history",
        "wrong-history-sha",
        "authoritative-history",
        "missing-publication",
        "wrong-index-sha",
        "omitted-commit",
    ):
        candidate = deepcopy(report)
        if bad == "missing-history":
            candidate.pop("history_assessment")
        elif bad == "wrong-history-sha":
            candidate["history_assessment"][0]["source_sha256"] = "0" * 64
        elif bad == "authoritative-history":
            candidate["history_assessment"][0]["accepted_verdict"] = True
        elif bad == "missing-publication":
            candidate.pop("evidence_publication")
        elif bad == "wrong-index-sha":
            candidate["evidence_publication"]["index_sha256"] = "0" * 64
        else:
            candidate["evidence_publication"]["payload_commits"].pop()
        Path(report_path).write_text(yaml.safe_dump(candidate, sort_keys=False))
        assert (
            main(
                [
                    "work",
                    "submit",
                    c.item.id,
                    "--verdict",
                    "pass-with-nits",
                    "--report-file",
                    report_path,
                ]
            )
            == 5
        )
        capsys.readouterr()
        assert c.item == original
        assert c.engine.runtime.list_runs(c.item.id)[-1].active
    Path(report_path).write_text(yaml.safe_dump(report, sort_keys=False))
    assert (
        main(
            [
                "work",
                "submit",
                c.item.id,
                "--verdict",
                "pass-with-nits",
                "--report-file",
                report_path,
            ]
        )
        == 0
    )
    capsys.readouterr()
    manifest = load_manifest(c.path)
    before = deepcopy(c.item)
    c.config["retry"]["review"] += 1
    with pytest.raises(ValidationError):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert c.item == before
    c.config["retry"]["review"] -= 1
    from dataclasses import replace

    attachment = c.item.review_report_ref["attachment_id"]
    native = c.observations[attachment]
    c.observations[attachment] = replace(native, uploader_id="foreign")
    with pytest.raises(ValidationError):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert c.item == before
    c.observations[attachment] = native
    failures = collect_results(
        c.engine.store,
        c.engine.runtime,
        manifest,
        c.path,
        retry_limits=c.config["retry"],
        config=c.config,
    )
    assert set(failures) == {
        c.key
    }  # unchanged normal nits decision, no merge or Worker retry
    assert manifest.nodes[c.key].status == "blocked"
    assert c.item.decision_required["reason_code"] == "review-nits-acceptance-required"
    before = deepcopy(c.item)
    assert (
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
        == {}
    )
    assert c.item == before
    record = next(
        iter(load_manifest(c.path).meta["rejected_evidence_handoffs"].values())
    )
    assert record["state"] == "consumed"
    assert record["review_dispatch"]["state"] == "normal-review-handed-off"
    from omac.pipeline.evidence_handoff import _retired_review
    from omac.pipeline.evidence_review import _digest
    from omac.core.review_convergence import advance_review_ledger

    disk = load_manifest(c.path)
    token = next(iter(disk.meta["rejected_evidence_handoffs"]))
    for bad in ("request", "history", "publication", "feedback", "verdict"):
        changed = deepcopy(disk)
        archived = changed.meta["rejected_evidence_handoffs"][token]
        if bad == "request":
            archived["request"]["tuple"]["source_file"]["content"] += "changed"
            changed.meta["rejected_evidence_resolutions"][token]["request"] = deepcopy(
                archived["request"]
            )
        elif bad in ("feedback", "verdict"):
            receipt = archived["review_dispatch"]["retirement"]
            if bad == "feedback":
                receipt["completed_control"]["machine_feedback"] = {"forged": True}
            else:
                receipt["completed_control"]["review_verdict"] = "pass"
            archived["review_dispatch"]["retirement_sha256"] = _digest(receipt)
        else:
            receipt = archived["review_dispatch"]["retirement"]
            new_report = deepcopy(receipt["completed_control"]["review_report"])
            if bad == "history":
                new_report["history_assessment"][0]["source_sha256"] = "0" * 64
            else:
                new_report["evidence_publication"]["index_sha256"] = "0" * 64
            new_ledger = advance_review_ledger(
                archived["request"]["tuple"]["full_control"]["review_ledger"],
                new_report,
                verdict=receipt["completed_control"]["review_verdict"],
                subject_digest=receipt["baseline"]["subject_digest"],
                round_index=3,
            )
            for kind, value in (("report", new_report), ("ledger", new_ledger)):
                raw = yaml.safe_dump(value, sort_keys=False)
                observation = receipt["completed_review"][kind]
                observation.update(
                    content=raw, sha256=hashlib.sha256(raw.encode()).hexdigest()
                )
                receipt["completed_control"]["review_" + kind] = value
                receipt["completed_control"]["review_" + kind + "_ref"].update(
                    sha256=observation["sha256"], bytes=len(raw.encode())
                )
            archived["review_dispatch"]["completed_review"] = deepcopy(
                receipt["completed_review"]
            )
            archived["review_dispatch"]["retirement_sha256"] = _digest(receipt)
        with pytest.raises(ValidationError):
            _retired_review(changed, token, archived)
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2
    assert len(c.engine.runtime.list_runs(c.item.id)) == 18
    assert len(c.item.review_ledger["cycles"]) == 3
    assert (
        c.item.review_ledger["cycles"][:2]
        == prepared["request"]["tuple"]["full_control"]["review_ledger"]["cycles"]
    )
    assert sum(n.status == "done" for n in manifest.nodes.values()) == 79

    # A retired receipt must leave later ordinary dispatch failure routing intact.
    import omac.pipeline.loop as loop
    from omac.errors import PlatformError

    for error_type, reason_code in (
        (loop._ReviewerDispatchUnresolved, "reviewer-run-dispatch-unresolved"),
        (PlatformError, "reviewer-run-baseline-unavailable"),
    ):
        later = deepcopy(disk)
        later.nodes[c.key].status = "in_progress"
        save_manifest(later, c.path)
        c.item.phase = TaskPhase.REVIEW
        c.item.status = WorkItemStatus.IN_REVIEW
        c.item.review_verdict = None
        c.item.decision_required = None

        def fail_dispatch(*args, **kwargs):
            raise error_type("offline later ordinary dispatch failure")

        monkeypatch.setattr(loop, "_dispatch_reviewer_for_current_subject", fail_dispatch)
        failures = collect_results(
            c.engine.store, c.engine.runtime, later, c.path,
            retry_limits=c.config["retry"], config=c.config,
        )
        assert set(failures) == {c.key}
        assert c.item.decision_required["reason_code"] == reason_code
        assert later.nodes[c.key].status == "blocked"
        assert later.meta["rejected_evidence_handoffs"][token] == record
        assert c.item.bounces.worker == 11 and c.item.bounces.review == 2
        assert len(c.engine.runtime.list_runs(c.item.id)) == 18


def test_late_actual_run_and_full_binding_readback_never_duplicate_dispatch(
    captured, monkeypatch
):
    from dataclasses import replace
    from omac.engines import mock
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest
    from omac.pipeline.loop import collect_results

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    assert (
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
        == {}
    )
    before = deepcopy(c.item)
    read = c.engine.store.read_immutable_artifact
    inserted = False

    def late_run(url):
        nonlocal inserted
        body = read(url)
        if not inserted:
            inserted = True
            mock._shared_runs[c.item.id].append(
                replace(mock._shared_runs[c.item.id][0], id="offline-late-foreign-run")
            )
        return body

    monkeypatch.setattr(c.engine.store, "read_immutable_artifact", late_run)
    with pytest.raises(ValidationError, match="Runs changed"):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert c.item == before
    # Remove only the injected offline negative fixture, never an actual Run.
    mock._shared_runs[c.item.id] = [
        r for r in mock._shared_runs[c.item.id] if r.id != "offline-late-foreign-run"
    ]
    monkeypatch.setattr(c.engine.store, "read_immutable_artifact", read)
    metadata = c.engine.store.update_work_item_metadata

    def changed_readback(item_id, **kwargs):
        result = metadata(item_id, **kwargs)
        if (
            kwargs.get("reviewer_run_baseline")
            and kwargs["reviewer_run_baseline"].target_run_id
        ):
            c.item.title += " concurrent binding drift"
        return result

    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", changed_readback)
    with pytest.raises(ValidationError, match="Full control changed"):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    assert len(c.engine.runtime.list_runs(c.item.id)) == 18
    assert (
        c.item.reviewer_run_baseline.target_run_id == mock._shared_runs[c.item.id][0].id
    )
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2


@pytest.mark.parametrize("accepted", [False, True])
def test_unknown_owned_body_is_observed_without_repeating_publication(
    captured, monkeypatch, accepted
):
    from omac.pipeline.evidence_handoff import (
        prepare_evidence_handoff,
        resolve_evidence_handoff,
    )
    from omac.pipeline.evidence_review import _digest
    from omac.pipeline.loop import collect_results
    from omac.errors import PlatformError, NeedsDecision

    c = captured
    request = prepare_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.manifest,
        c.path,
        c.key,
        rejected_source=c.source,
        history_files=c.history,
        config=c.config,
    )
    resolve_evidence_handoff(
        c.engine.store,
        c.engine.runtime,
        c.path,
        c.key,
        request,
        request_sha256=_digest(request),
        authority="offline coordinator",
        reason="Exact only",
        config=c.config,
    )
    metadata = c.engine.store.update_work_item_metadata
    count = 0
    lost = False

    def update(item_id, **kwargs):
        nonlocal count, lost
        if "description" in kwargs:
            if not lost:
                lost = True
                if accepted:
                    count += 1
                    metadata(item_id, **kwargs)
                raise PlatformError("Unknown owned body publication")
            count += 1
        return metadata(item_id, **kwargs)

    monkeypatch.setattr(c.engine.store, "update_work_item_metadata", update)
    with pytest.raises(PlatformError, match="Unknown"):
        collect_results(
            c.engine.store,
            c.engine.runtime,
            load_manifest(c.path),
            c.path,
            retry_limits=c.config["retry"],
            config=c.config,
        )
    if accepted:
        assert (
            collect_results(
                c.engine.store,
                c.engine.runtime,
                load_manifest(c.path),
                c.path,
                retry_limits=c.config["retry"],
                config=c.config,
            )
            == {}
        )
        assert count == 1
        assert len(c.engine.runtime.list_runs(c.item.id)) == 18
    else:
        with pytest.raises(NeedsDecision):
            collect_results(
                c.engine.store,
                c.engine.runtime,
                load_manifest(c.path),
                c.path,
                retry_limits=c.config["retry"],
                config=c.config,
            )
        assert count == 0
        assert len(c.engine.runtime.list_runs(c.item.id)) == 17
    assert c.item.bounces.worker == 11 and c.item.bounces.review == 2


def test_prepare_requires_independent_rejected_source_before_effects():
    from omac.pipeline.evidence_handoff import prepare_evidence_handoff

    with pytest.raises(ValidationError, match="rejected source"):
        prepare_evidence_handoff(
            None,
            None,
            None,
            "manifest.yaml",
            "node",
            rejected_source=None,
            publication_url=None,
            config={},
        )

"""SDK native publication uses current Store/Runtime facts; no session importer."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase, parse_worker_handoff
from omac.engines import create_engine
from omac.engines.models import (
    AgentRunObservation,
    EngineConfig,
    PullRequestReadiness,
    ReleaseAssetObservation,
    VerificationAttachmentObservation,
    WorkItem,
    WorkItemStatus,
)
from omac.errors import ValidationError, NeedsDecision, PlatformError
from omac.pipeline.evidence_review import _digest

KEY = "harness-sdk-production-build-repair"
INDEX = "https://github.com/xiaohei-info/open-agent-cluster/blob/8bbf89abc19d1e9d77aa6a4d97375a43ef9f1c96/artifacts/harness-sdk-build-repair-evidence/evidence-index.json"


pytestmark = pytest.mark.integration


@pytest.fixture
def sdk(tmp_path):
    folder = Path(__file__).parent / "fixtures/sdk_native_publication"
    data = json.loads((folder / "typed-item.json").read_text())
    data.update(
        status=WorkItemStatus(data["status"]),
        phase=TaskPhase(data["phase"]),
        kind=TaskKind(data["kind"]),
        bounces=Bounces(**data["bounces"]),
        worker_handoff=parse_worker_handoff(data["worker_handoff"]),
    )
    item = WorkItem(**data)
    engine = create_engine(
        "mock",
        EngineConfig("mock", item.workspace_id, extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    engine.store.get_work_item = lambda _: item
    engine.store.resolve_agent_id = lambda n: (
        "8dc91607-6825-41c6-b0de-45c001afc58d"
        if n == item.worker
        else "94f847b9-c10f-4d64-88b2-e90cb5c03ffc"
    )
    obs = json.loads((folder / "attachment-observations.json").read_text())
    blobs = {}
    refs = {}
    for key, meta in obs.items():
        blobs[meta["attachment_id"]] = VerificationAttachmentObservation(
            content=(folder / (key + ".yaml")).read_bytes(), **meta
        )
    for label in ("original", "execution"):
        ref = json.loads((folder / (label + "-ref.json")).read_text())
        refs[label] = ref
        meta = json.loads((folder / (label + "-observation.json")).read_text())
        blobs[meta["attachment_id"]] = VerificationAttachmentObservation(
            content=(folder / (label + "-verification.yaml")).read_bytes(), **meta
        )
    engine.store.observe_verification_attachment = lambda _, ref: blobs[
        ref["attachment_id"]
    ]

    def read_ref(iid, cid=None, *, attachment_id=None):
        found = [
            r
            for r in refs.values()
            if r["attachment_id"] == attachment_id or r["comment_id"] == cid
        ]
        if len(found) != 1:
            raise PlatformError("Native source reference is unavailable")
        return deepcopy(found[0])

    engine.store.read_verification_reference = read_ref
    engine.store.read_pull_request_readiness = lambda _: PullRequestReadiness(
        False, "OPEN", head_sha=item.artifacts["head_sha"]
    )
    runtime = Mock()
    runtime.list_runs.return_value = [
        AgentRunObservation(**r) for r in json.loads((folder / "runs.json").read_text())
    ]
    messages = {}
    for label, rid in [
        ("latest", item.worker_handoff.target_run_id),
        ("reviewer", obs["report"]["task_id"]),
        (
            "execution",
            json.loads((folder / "execution-observation.json").read_text())["task_id"],
        ),
    ]:
        messages[rid] = json.loads((folder / (label + "-messages.json")).read_text())
    runtime.read_run_messages.side_effect = lambda iid, rid: deepcopy(messages[rid])
    assets = {}
    for ref in json.loads((folder / "git-artifact-observations.json").read_text()):
        assets[ref["url"]] = ReleaseAssetObservation(
            ref, (folder / "assets" / Path(ref["path"]).name).read_bytes()
        )

    def observe(urls, *, download=True):
        return [
            ReleaseAssetObservation(
                deepcopy(assets[u].reference), assets[u].content if download else b""
            )
            for u in urls
        ]

    engine.store.observe_git_artifacts = observe
    manifest = load_manifest(str(folder / "manifest.yaml"))
    manifest.nodes[KEY].status = "blocked"
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    return SimpleNamespace(
        store=engine.store,
        runtime=runtime,
        item=item,
        manifest=manifest,
        path=path,
        blobs=blobs,
        refs=refs,
        assets=assets,
        messages=messages,
    )


def prepare(c):
    from omac.pipeline.sdk_publication_review import prepare_sdk_publication_review

    return prepare_sdk_publication_review(
        c.store,
        c.runtime,
        c.manifest,
        KEY,
        INDEX,
        "explicit SDK native-publication request",
    )


def apply(c, request):
    from omac.pipeline.sdk_publication_review import apply_sdk_publication_review

    return apply_sdk_publication_review(
        c.store,
        c.runtime,
        c.path,
        KEY,
        request,
        approved_request_sha256=_digest(request),
    )


def test_existing_preview_policy_refuses_actual_sdk_hold(sdk):
    from omac.pipeline.publication_review import prepare_publication_review
    from omac.pipeline.loop import _worker_handoff_has_new_delivery

    before = deepcopy(sdk.item)
    with pytest.raises(ValidationError):
        prepare_publication_review(
            sdk.store, sdk.runtime, sdk.manifest, KEY, INDEX, "SDK capability work"
        )
    assert not _worker_handoff_has_new_delivery(sdk.item, sdk.item.worker_handoff)
    assert sdk.item == before


def test_sdk_native_request_preserves_hold_and_routes_once(sdk):
    c = sdk
    before = deepcopy(c.item)
    done = {k for k, n in c.manifest.nodes.items() if n.status == "done"}
    request = prepare(c)
    assert c.item == before and len(done) == 63
    assert request["tuple"]["operator_hold"] == before.decision_required
    assert len(request["tuple"]["publication"]["assets"]) == 19
    assert (
        request["tuple"]["execution_provenance"]["run"]["id"]
        == "01a10062-994b-740f-9916-d7849dc301cc"
    )
    assert request["tuple"]["run"]["id"] == "01a100a6-13a4-7d17-97ef-cca4e913b8e0"
    assert (
        request["tuple"]["budget"]["absolute"]
        == request["tuple"]["budget"]["consumed"]
        == {"worker": 1, "review": 1, "merge": 0}
    )
    result = apply(c, request)
    assert (
        result["state"] == "ready-for-independent-review" and result["verdict"] is None
    )
    assert (
        c.item.phase == TaskPhase.REVIEW and c.item.status == WorkItemStatus.IN_REVIEW
    )
    assert c.item.delivery_identity.run_id == before.worker_handoff.target_run_id
    assert (
        c.item.review_ledger == before.review_ledger
        and c.item.review_ledger_ref == before.review_ledger_ref
    )
    assert (
        c.item.bounces == before.bounces
        and c.item.bounce_baseline == before.bounce_baseline
    )
    assert {
        k for k, n in load_manifest(c.path).nodes.items() if n.status == "done"
    } == done
    assert c.item.decision_required in (None, {})
    c.runtime.dispatch_reviewer.assert_not_called()
    c.runtime.wake.assert_not_called()
    with pytest.raises(NeedsDecision):
        apply(c, request)


@pytest.mark.parametrize("step", range(8))
def test_sdk_unknown_writes_resume_exact_prefix_without_dispatch(sdk, step):
    c = sdk
    request = prepare(c)
    original_meta, original_status = (
        c.store.update_work_item_metadata,
        c.store.update_status,
    )
    calls = 0

    def write(fn, *args, **kwargs):
        nonlocal calls
        result = fn(*args, **kwargs)
        index = calls
        calls += 1
        if index == step:
            raise PlatformError("unknown accepted write")
        return result

    c.store.update_work_item_metadata = lambda *a, **kw: write(original_meta, *a, **kw)
    c.store.update_status = lambda *a, **kw: write(original_status, *a, **kw)
    with pytest.raises(PlatformError):
        apply(c, request)
    c.store.update_work_item_metadata = original_meta
    c.store.update_status = original_status
    assert apply(c, request)["state"] == "ready-for-independent-review"
    assert c.item.bounces == Bounces(worker=1, review=1)
    assert c.item.bounce_baseline == {"worker": 0, "review": 0, "merge": 0}
    c.runtime.dispatch_reviewer.assert_not_called()


@pytest.mark.parametrize(
    "drift",
    [
        "hold",
        "head",
        "generation",
        "contract",
        "run",
        "uploader",
        "root",
        "metadata",
        "bytes",
    ],
)
def test_sdk_exact_source_drift_stops_before_write(sdk, drift):
    c = sdk
    request = prepare(c)
    if drift == "hold":
        c.item.decision_required["source_run_id"] = "foreign"
    elif drift == "head":
        c.item.artifacts["head_sha"] = "f" * 40
    elif drift == "generation":
        c.item.review_generation = "foreign"
    elif drift == "contract":
        c.item.contract["objective"] += " changed"
    elif drift == "run":
        c.runtime.list_runs.return_value[0] = __import__("dataclasses").replace(
            c.runtime.list_runs.return_value[0], status="running"
        )
    elif drift == "uploader":
        key = c.item.verification_ref["attachment_id"]
        c.blobs[key] = __import__("dataclasses").replace(
            c.blobs[key], task_id="foreign"
        )
    elif drift == "root":
        c.item.worker_handoff.source_review_feedback["blockers"][0][
            "root_cause_key"
        ] = "foreign"
    elif drift == "metadata":
        next(iter(c.assets.values())).reference["tree_sha"] = "f" * 40
    else:
        key = next(iter(c.assets))
        old = c.assets[key]
        c.assets[key] = ReleaseAssetObservation(old.reference, b"tampered")
    before = deepcopy(c.item)
    with pytest.raises((ValidationError, PlatformError)):
        apply(c, request)
    assert c.item == before
    c.runtime.dispatch_reviewer.assert_not_called()


@pytest.mark.parametrize(
    "fault", [None, "commit", "tree", "truncated", "mode", "blob", "bytes", "missing"]
)
def test_native_git_adapter_uses_only_get_and_checks_all_blob_bytes(monkeypatch, fault):
    import base64
    from omac.engines.multica import MulticaStore

    folder = Path(__file__).parent / "fixtures/sdk_native_publication"
    commit = json.loads((folder / "commit-native.json").read_text())
    tree = json.loads((folder / "tree-native.json").read_text())
    rows = json.loads((folder / "git-artifact-observations.json").read_text())
    assets = {
        r["blob_oid"]: (folder / "assets" / Path(r["path"]).name).read_bytes()
        for r in rows
    }
    if fault == "commit":
        commit["sha"] = "f" * 40
    elif fault == "tree":
        tree["sha"] = "f" * 40
    elif fault == "truncated":
        tree["truncated"] = True
    elif fault == "mode":
        next(r for r in tree["tree"] if r["path"] == rows[0]["path"])["mode"] = "120000"
    calls = []

    def api(argv, **kwargs):
        assert argv[:4] == ["gh", "api", "--method", "GET"]
        endpoint = argv[4]
        calls.append(endpoint)
        if "/commits/" in endpoint:
            payload = commit
        elif "/trees/" in endpoint:
            payload = tree
        else:
            oid = endpoint.rsplit("/", 1)[-1]
            body = assets[oid]
            if fault == "missing":
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"404")
            payload = {
                "sha": oid,
                "size": len(body),
                "encoding": "base64",
                "content": base64.b64encode(body).decode(),
            }
            if fault == "blob":
                payload["sha"] = "f" * 40
            if fault == "bytes":
                payload["content"] = base64.b64encode(b"changed").decode()
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(payload).encode(), stderr=b""
        )

    monkeypatch.setattr("omac.engines.multica.subprocess.run", api)
    store = MulticaStore(EngineConfig("multica", "ws"))
    urls = [r["url"] for r in rows]
    if fault is not None:
        with pytest.raises(PlatformError):
            store.observe_git_artifacts(urls)
    else:
        observed = store.observe_git_artifacts(urls)
        assert [o.reference for o in observed] == rows
        assert [o.content for o in observed] == [assets[r["blob_oid"]] for r in rows]
        before = len(calls)
        assert all(
            not o.content for o in store.observe_git_artifacts(urls, download=False)
        )
        assert len(calls) - before == 2


def test_sdk_real_metadata_budget_keeps_full_published_review_and_history(
    sdk, monkeypatch
):
    import hashlib
    from omac.engines.multica import MulticaStore
    from omac.core.review_convergence import build_review_obligations
    from omac.pipeline.sdk_publication_review import _obligations

    c = sdk
    request = prepare(c)
    body = _obligations(build_review_obligations(c.item), request["tuple"])
    folder = Path(__file__).parent / "fixtures/sdk_native_publication"
    metadata = json.loads((folder / "held-metadata.json").read_text())
    for key, value in list(metadata.items()):
        if isinstance(value, str):
            try:
                metadata[key] = json.loads(value)
            except ValueError:
                pass
    before = deepcopy(metadata)
    store = MulticaStore(EngineConfig("multica", c.item.workspace_id))
    payloads = {}

    def publish(iid, key, content, suffix):
        assert iid == c.item.id
        payloads[key] = content
        return {
            "comment_id": "fixture-comment",
            "attachment_id": "fixture-attachment",
            "filename": "fixture" + suffix,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "bytes": len(content.encode()),
        }

    monkeypatch.setattr(
        store,
        "_read_issue_metadata",
        lambda _: ({"id": c.item.id, "metadata": metadata}, metadata),
    )
    monkeypatch.setattr(
        store, "_set_metadata", lambda iid, key, value: metadata.__setitem__(key, value)
    )
    monkeypatch.setattr(store, "_publish_payload_comment", publish)
    monkeypatch.setattr(store, "get_work_item", lambda _: c.item)
    store.update_work_item_metadata(c.item.id, review_obligations=body)
    assert store._metadata_object_bytes(metadata) <= 8192
    import yaml

    assert yaml.safe_load(payloads["review-obligations"]) == body
    for key in [
        "worker_handoff",
        "review_ledger_ref",
        "worker_bounce",
        "review_bounce",
        "merge_bounce",
        "bounce_baseline",
        "verification_ref",
        "contract_ref",
        "decision_required",
    ]:
        assert metadata.get(key) == before.get(key)


def test_sdk_cli_prepare_needs_new_digest_and_no_session_flags(
    sdk, monkeypatch, capsys, tmp_path
):
    from omac.cli import main as cli
    from omac.cli.commands import node as cmd

    c = sdk
    monkeypatch.setattr(cmd, "load_config", lambda: {"engine": {"type": "mock"}})
    monkeypatch.setattr(
        cmd,
        "_build_engine",
        lambda _: SimpleNamespace(store=c.store, runtime=c.runtime),
    )
    assert (
        cli.main(
            [
                "node",
                "review-sdk-publication",
                c.path,
                KEY,
                "--index-url",
                INDEX,
                "--reason",
                "SDK work",
                "--output",
                "json",
            ]
        )
        == 0
    )
    request = json.loads(capsys.readouterr().out)
    assert request["schema"] == "omac.sdk-native-publication-review/v1"
    file = tmp_path / "request.json"
    file.write_text(json.dumps(request))
    assert (
        cli.main(
            [
                "node",
                "review-sdk-publication",
                c.path,
                KEY,
                "--apply-request",
                str(file),
                "--output",
                "json",
            ]
        )
        == 5
    )
    assert c.item.status == WorkItemStatus.BLOCKED


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "duplicate",
        "source",
        "parent",
        "unknown-run",
        "foreign-schema",
        "bad-digest",
        "budget",
        "other-done",
    ],
)
def test_sdk_strict_index_scope_and_retained_authority(sdk, change):
    import hashlib

    c = sdk
    if change in {"missing", "extra", "duplicate", "source"}:
        old = c.assets[INDEX]
        data = json.loads(old.content)
        if change == "missing":
            data["files"].pop()
        elif change == "extra":
            data["unauthorized_scope"] = "other node"
        elif change == "duplicate":
            data["files"][1] = data["files"][0]
        else:
            data["source_commit"] = "f" * 40
        content = json.dumps(data).encode()
        ref = deepcopy(old.reference)
        ref["bytes"] = len(content)
        ref["blob_oid"] = hashlib.sha1(
            b"blob " + str(len(content)).encode() + b"\0" + content
        ).hexdigest()
        c.assets[INDEX] = ReleaseAssetObservation(ref, content)
        with pytest.raises(ValidationError):
            prepare(c)
    else:
        request = prepare(c)
        if change == "parent":
            next(iter(c.assets.values())).reference["parent_shas"] = ["f" * 40]
        elif change == "unknown-run":
            c.runtime.list_runs.return_value.append(
                AgentRunObservation(
                    id="unknown",
                    kind="direct",
                    status="completed",
                    agent_id="foreign",
                    created_at="2026-10-03T08:00:00Z",
                    updated_at="2026-10-03T08:01:00Z",
                    trigger_kind="comment",
                )
            )
        elif change == "foreign-schema":
            request["schema"] = "omac.preview-publication-review/v1"
        elif change == "budget":
            c.item.bounce_baseline["review"] = 1
        elif change == "other-done":
            next(
                n for n in c.manifest.nodes.values() if n.status == "done"
            ).status = "blocked"
            save_manifest(c.manifest, c.path)
        if change == "bad-digest":
            from omac.pipeline.sdk_publication_review import (
                apply_sdk_publication_review,
            )

            with pytest.raises(ValidationError):
                apply_sdk_publication_review(
                    c.store,
                    c.runtime,
                    c.path,
                    KEY,
                    request,
                    approved_request_sha256="0" * 64,
                )
        else:
            with pytest.raises((ValidationError, PlatformError)):
                apply(c, request)
    c.runtime.dispatch_reviewer.assert_not_called()


def test_sdk_two_consecutive_unknown_writes_persist_observed_prefix(sdk):
    from omac.pipeline.sdk_publication_review import JOURNAL

    c = sdk
    request = prepare(c)
    original = c.store.update_work_item_metadata
    count = 0

    def write(*a, **kw):
        nonlocal count
        result = original(*a, **kw)
        count += 1
        if count <= 2:
            raise PlatformError("lost accepted write response")
        return result

    c.store.update_work_item_metadata = write
    with pytest.raises(PlatformError):
        apply(c, request)
    with pytest.raises(PlatformError):
        apply(c, request)
    entry = load_manifest(c.path).meta[JOURNAL][_digest(request)]
    assert entry["step"] == 1
    assert apply(c, request)["state"] == "ready-for-independent-review"
    assert c.item.bounces == Bounces(worker=1, review=1)


def test_sdk_normal_collection_dispatches_one_real_reviewer_seam(sdk, tmp_path):
    from types import MethodType
    from omac.core.manifest import Manifest
    from omac.engines.runtime import AgentRuntime
    from omac.pipeline import loop

    c = sdk
    apply(c, prepare(c))
    full = load_manifest(c.path)
    done = {k for k, n in full.nodes.items() if n.status == "done"}
    # The collector's SDK slice uses the exact applied node/meta. The full
    # 178-node apply snapshot remains unchanged and is checked separately.
    view = Manifest(meta=deepcopy(full.meta), nodes={KEY: deepcopy(full.nodes[KEY])})
    path = str(tmp_path / "collector.yaml")
    save_manifest(view, path)
    before = deepcopy(c.item)
    c.runtime.capabilities.stable_direct_run_identity = True
    c.runtime.dispatch_reviewer = MethodType(AgentRuntime.dispatch_reviewer, c.runtime)
    c.runtime.wake_reviewer = MethodType(AgentRuntime.wake_reviewer, c.runtime)
    runs = c.runtime.list_runs.return_value

    def wake(iid, agent, role):
        assert (iid, agent, role) == (c.item.id, "hermes-reviewer", "reviewer")
        runs.append(
            AgentRunObservation(
                id="sdk-new-review",
                kind="direct",
                status="running",
                agent_id=c.store.resolve_agent_id(agent),
                created_at="2026-10-03T09:00:00Z",
                trigger_kind="rerun",
            )
        )

    c.runtime.wake = Mock(side_effect=wake)
    c.runtime.is_active.side_effect = lambda _: any(r.active for r in runs)
    c.store.assign_work_item = Mock(wraps=c.store.assign_work_item)
    for _ in range(3):
        observed = loop.reconcile_with_observations(c.store, view, path)
        assert (
            loop.collect_results(
                c.store, c.runtime, view, path, observations=observed.observations
            )
            == {}
        )
    c.store.assign_work_item.assert_called_once_with(
        c.item.id, "hermes-reviewer", "reviewer", start_run=False
    )
    c.runtime.wake.assert_called_once()
    assert c.item.reviewer_run_baseline.target_run_id == "sdk-new-review"
    assert c.item.review_subject_digest == before.review_subject_digest
    assert c.item.review_obligations == before.review_obligations
    assert (
        c.item.review_ledger == before.review_ledger
        and c.item.review_ledger_ref == before.review_ledger_ref
    )
    assert (
        c.item.bounces == before.bounces
        and c.item.bounce_baseline == before.bounce_baseline
    )
    assert {
        k for k, n in load_manifest(c.path).nodes.items() if n.status == "done"
    } == done


def test_sdk_pending_receipt_blocks_runner_before_any_io(sdk):
    from omac.pipeline import loop

    c = sdk
    request = prepare(c)
    original = c.store.update_work_item_metadata

    def lost(*a, **kw):
        original(*a, **kw)
        raise PlatformError("unknown write")

    c.store.update_work_item_metadata = lost
    with pytest.raises(PlatformError):
        apply(c, request)
    current = load_manifest(c.path)
    c.store.get_work_item = Mock(
        side_effect=AssertionError("No I/O before journal guard")
    )
    c.runtime.list_runs = Mock(side_effect=AssertionError("No Run read before guard"))
    with pytest.raises(NeedsDecision):
        loop.tick(c.store, c.runtime, current, c.path)


def test_sdk_native_comment_lookup_is_exact_and_ambiguous_refs_fail(monkeypatch, sdk):
    from omac.engines.multica import MulticaStore

    c = sdk
    ref = c.refs["execution"]
    store = MulticaStore(EngineConfig("multica", c.item.workspace_id))
    calls = []
    root = {"id": ref["comment_id"], "attachments": [{"id": ref["attachment_id"]}]}

    def run(args):
        calls.append(args)
        return [deepcopy(root)]

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(
        store, "_review_attachment_ref", lambda comment, attachment, kind: deepcopy(ref)
    )
    assert (
        store.read_verification_reference(c.item.id, attachment_id=ref["attachment_id"])
        == ref
    )
    assert "--roots-only" in calls[0] and "--full" in calls[0]
    assert calls[1][calls[1].index("--thread") + 1] == ref["comment_id"]
    monkeypatch.setattr(store, "_run_multica", lambda _: [root, root])
    with pytest.raises(PlatformError):
        store.read_verification_reference(c.item.id, attachment_id=ref["attachment_id"])

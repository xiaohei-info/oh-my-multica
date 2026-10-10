"""Current native SDK successor, preserving legacy consumed authority."""

from copy import deepcopy
from dataclasses import replace
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
from omac.errors import ValidationError, PlatformError, NeedsDecision
from omac.pipeline.evidence_review import _digest
from omac.pipeline.sdk_publication_review import KEY

COMMIT = "6aebcc6a53992e7e9bc77b249596655fb7bb4b7a"
INDEX = (
    "https://github.com/xiaohei-info/open-agent-cluster/blob/"
    + COMMIT
    + "/artifacts/harness-sdk-build-repair-evidence/evidence-index.json"
)


pytestmark = pytest.mark.integration


@pytest.fixture
def successor(tmp_path):
    folder = Path(__file__).parent / "fixtures/sdk_command18_checkpoint"
    d = json.loads((folder / "typed-item.json").read_text())
    d.update(
        status=WorkItemStatus(d["status"]),
        phase=TaskPhase(d["phase"]),
        kind=TaskKind(d["kind"]),
        bounces=Bounces(**d["bounces"]),
        worker_handoff=parse_worker_handoff(d["worker_handoff"]),
    )
    item = WorkItem(**d)
    e = create_engine(
        "mock",
        EngineConfig("mock", item.workspace_id, extra={"MOCK_AUTO_COMPLETE": "false"}),
    )
    e.store.get_work_item = lambda _: item
    e.store.resolve_agent_id = lambda n: (
        "8dc91607-6825-41c6-b0de-45c001afc58d"
        if n == item.worker
        else "94f847b9-c10f-4d64-88b2-e90cb5c03ffc"
    )
    obs = json.loads((folder / "attachment-observations.json").read_text())
    blobs = {
        v["attachment_id"]: VerificationAttachmentObservation(
            content=(folder / (k + ".yaml")).read_bytes(), **v
        )
        for k, v in obs.items()
    }
    ref = json.loads((folder / "original-ref.json").read_text())
    original = json.loads((folder / "original-observation.json").read_text())
    blobs[ref["attachment_id"]] = VerificationAttachmentObservation(
        content=(folder / "original-verification.yaml").read_bytes(), **original
    )
    e.store.observe_verification_attachment = lambda _, r: blobs[r["attachment_id"]]

    def read(iid, cid=None, *, attachment_id=None):
        if ref["comment_id"] == cid or ref["attachment_id"] == attachment_id:
            return deepcopy(ref)
        raise PlatformError("No native reference")

    e.store.read_verification_reference = read
    head = item.artifacts["head_sha"]
    e.store.read_pull_request_readiness = lambda _: PullRequestReadiness(
        False, "OPEN", head_sha=head
    )
    runtime = Mock()
    runtime.list_runs.return_value = [
        AgentRunObservation(**r) for r in json.loads((folder / "runs.json").read_text())
    ]
    msgs = {
        item.worker_handoff.target_run_id: json.loads(
            (folder / "latest-messages.json").read_text()
        ),
        obs["report"]["task_id"]: json.loads(
            (folder / "reviewer-messages.json").read_text()
        ),
    }
    runtime.read_run_messages.side_effect = lambda _, rid: deepcopy(msgs[rid])
    assets = {
        r["url"]: ReleaseAssetObservation(
            r, (folder / "assets" / Path(r["path"]).name).read_bytes()
        )
        for r in json.loads((folder / "git-artifact-observations.json").read_text())
    }
    e.store.observe_git_artifacts = lambda urls, download=True: [
        ReleaseAssetObservation(
            deepcopy(assets[u].reference), assets[u].content if download else b""
        )
        for u in urls
    ]
    m = load_manifest(str(folder / "manifest.yaml"))
    path = str(tmp_path / "m.yaml")
    save_manifest(m, path)
    return SimpleNamespace(
        store=e.store,
        runtime=runtime,
        item=item,
        manifest=m,
        path=path,
        assets=assets,
        blobs=blobs,
        messages=msgs,
        original_ref=ref,
    )


def prepare(c):
    from omac.pipeline.sdk_checkpoint_review import prepare_sdk_checkpoint_review

    return prepare_sdk_checkpoint_review(
        c.store,
        c.runtime,
        c.manifest,
        KEY,
        INDEX,
        "delegated SDK command18 checkpoint recovery",
    )


def apply(c, r):
    from omac.pipeline.sdk_checkpoint_review import apply_sdk_checkpoint_review

    return apply_sdk_checkpoint_review(
        c.store, c.runtime, c.path, KEY, r, approved_request_sha256=_digest(r)
    )


def test_legacy_profile_and_global_delivery_guard_refuse_current_successor(successor):
    from omac.pipeline.sdk_publication_review import prepare_sdk_publication_review
    from omac.pipeline.loop import _worker_handoff_has_new_delivery

    c = successor
    before = deepcopy(c.item)
    with pytest.raises(ValidationError):
        prepare_sdk_publication_review(
            c.store, c.runtime, c.manifest, KEY, INDEX, "unsupported successor"
        )
    assert not _worker_handoff_has_new_delivery(c.item, c.item.worker_handoff)
    assert c.item == before


def test_current_checkpoint_request_seals_only_and_preserves_all_authority(successor):
    c = successor
    before = deepcopy(c.item)
    meta = deepcopy(c.manifest.meta)
    done = {k for k, n in c.manifest.nodes.items() if n.status == "done"}
    r = prepare(c)
    assert c.item == before
    assert len(done) == 64
    assert (
        r["tuple"]["checkpoint_provenance"]["sha256"]
        == "5e709ad69d68c6971a655a70f75a3cb754930b9f0f7c864a6f8d29304ee27670"
    )
    assert (
        r["tuple"]["budget"]["absolute"]
        == r["tuple"]["budget"]["consumed"]
        == {"worker": 1, "review": 2, "merge": 0}
    )
    assert r["tuple"]["budget"]["baseline"] is None
    assert apply(c, r)["verdict"] is None
    assert (
        c.item.status == WorkItemStatus.IN_REVIEW and c.item.phase == TaskPhase.REVIEW
    )
    assert c.item.delivery_identity.run_id == "01a101bf-006b-7412-af52-258d6979a7cd"
    assert (
        c.item.review_ledger == before.review_ledger
        and c.item.review_ledger_ref == before.review_ledger_ref
    )
    assert (
        c.item.bounces == before.bounces
        and c.item.bounce_baseline == before.bounce_baseline
    )
    after = load_manifest(c.path)
    assert {k for k, n in after.nodes.items() if n.status == "done"} == done
    assert (
        after.meta["sdk_native_publication_reviews"]
        == meta["sdk_native_publication_reviews"]
    )
    c.runtime.dispatch_reviewer.assert_not_called()
    with pytest.raises(NeedsDecision):
        apply(c, r)


@pytest.mark.parametrize("step", range(8))
def test_unknown_writes_resume_current_request(successor, step):
    c = successor
    r = prepare(c)
    metadata = c.store.update_work_item_metadata
    status = c.store.update_status
    count = 0

    def write(fn, *a, **kw):
        nonlocal count
        result = fn(*a, **kw)
        current = count
        count += 1
        if current == step:
            raise PlatformError("unknown accepted write")
        return result

    c.store.update_work_item_metadata = lambda *a, **kw: write(metadata, *a, **kw)
    c.store.update_status = lambda *a, **kw: write(status, *a, **kw)
    with pytest.raises(PlatformError):
        apply(c, r)
    c.store.update_work_item_metadata = metadata
    c.store.update_status = status
    assert apply(c, r)["state"] == "ready-for-independent-review"
    c.runtime.dispatch_reviewer.assert_not_called()


@pytest.mark.parametrize(
    "drift",
    [
        "head",
        "gen",
        "report",
        "run",
        "baseline",
        "script",
        "checkpoint",
        "archive",
        "index",
        "source-subject",
        "old-journal",
    ],
)
def test_current_tuple_drift_refuses_before_writes(successor, drift):
    c = successor
    r = prepare(c)
    if drift == "head":
        c.item.artifacts["head_sha"] = "f" * 40
    elif drift == "gen":
        c.item.review_generation = "wrong"
    elif drift == "report":
        c.item.worker_handoff.source_review_feedback["report_ref"]["sha256"] = "f" * 64
    elif drift == "run":
        c.runtime.list_runs.return_value[0] = replace(
            c.runtime.list_runs.return_value[0], status="running"
        )
    elif drift == "baseline":
        c.item.bounce_baseline = {"worker": 1, "review": 2, "merge": 0}
    elif drift == "source-subject":
        c.item.worker_handoff = replace(
            c.item.worker_handoff, source_review_subject_digest="f" * 64
        )
    elif drift == "old-journal":
        c.manifest.meta["sdk_native_publication_reviews"].clear()
        save_manifest(c.manifest, c.path)
    elif drift == "script":
        rows = c.messages[c.item.worker_handoff.target_run_id]
        next(x for x in rows if x.get("seq") == 43)["input"]["command"] = (
            "echo checkpoint"
        )
    else:
        name = {
            "checkpoint": "semantic-evidence.json",
            "archive": "harness-sdk-build-repair-evidence.tar.gz",
            "index": "evidence-index.json",
        }[drift]
        key = next(k for k in c.assets if k.endswith("/" + name))
        old = c.assets[key]
        c.assets[key] = ReleaseAssetObservation(old.reference, b"changed")
    before = deepcopy(c.item)
    with pytest.raises((ValidationError, PlatformError)):
        apply(c, r)
    assert c.item == before


def test_checkpoint_two_consecutive_unknown_writes(successor):
    from omac.pipeline.sdk_checkpoint_review import JOURNAL

    c = successor
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
    assert c.item.bounces == Bounces(worker=1, review=2)


def test_checkpoint_normal_collection_dispatches_once(successor, tmp_path):
    from types import MethodType
    from omac.core.manifest import Manifest
    from omac.engines.runtime import AgentRuntime
    from omac.pipeline import loop

    c = successor
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
                id="successor-new-review",
                kind="direct",
                status="running",
                agent_id=c.store.resolve_agent_id(agent),
                created_at="2026-10-03T14:00:00Z",
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
    assert c.item.reviewer_run_baseline.target_run_id == "successor-new-review"
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


def test_checkpoint_pending_receipt_blocks_runner_before_io(successor):
    from omac.pipeline import loop

    c = successor
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


def test_checkpoint_cli_requires_exact_digest(successor, monkeypatch, capsys, tmp_path):
    from omac.cli import main as cli
    from omac.cli.commands import node as cmd

    c = successor
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
                "review-sdk-checkpoint",
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
    assert request["schema"] == "omac.sdk-command18-checkpoint-review/v1"
    file = tmp_path / "request.json"
    file.write_text(json.dumps(request))
    assert (
        cli.main(
            [
                "node",
                "review-sdk-checkpoint",
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
    assert c.item.status == WorkItemStatus.DONE


@pytest.mark.parametrize(
    "change",
    [
        "truncated-result",
        "changed-result",
        "missing-source",
        "changed-source",
        "unknown-history",
        "wrong-original",
        "wrong-node",
        "wrong-index",
        "old-schema",
        "wrong-approval",
    ],
)
def test_checkpoint_closed_scope_refuses(change, successor):
    from omac.pipeline.sdk_checkpoint_review import (
        prepare_sdk_checkpoint_review,
        apply_sdk_checkpoint_review,
    )

    c = successor
    before = deepcopy(c.item)
    if change in {"old-schema", "wrong-approval"}:
        request = prepare(c)
        if change == "old-schema":
            request["schema"] = "omac.sdk-native-publication-review/v1"
        with pytest.raises(ValidationError):
            apply_sdk_checkpoint_review(
                c.store,
                c.runtime,
                c.path,
                KEY,
                request,
                approved_request_sha256="0" * 64
                if change == "wrong-approval"
                else _digest(request),
            )
    else:
        key, index = KEY, INDEX
        if change == "wrong-node":
            key = "preview-production-build-repair"
        elif change == "wrong-index":
            index = INDEX.replace(COMMIT, "f" * 40)
        elif change == "unknown-history":
            c.runtime.list_runs.return_value.append(
                replace(c.runtime.list_runs.return_value[0], id="unknown")
            )
        elif change == "wrong-original":
            c.original_ref["sha256"] = "f" * 64
        elif change in {"missing-source", "changed-source"}:
            rows = c.messages["01a1019d-907b-7c70-872a-26fd10521987"]
            if change == "missing-source":
                c.messages["01a1019d-907b-7c70-872a-26fd10521987"] = [
                    r for r in rows if r["seq"] != 47
                ]
            else:
                next(r for r in rows if r["seq"] == 3)["input"]["text"] = next(
                    r for r in rows if r["seq"] == 3
                )["input"]["text"].replace(
                    c.item.workspace_id, "00000000-0000-0000-0000-000000000000"
                )
        else:
            row = next(
                r
                for r in c.messages[c.item.worker_handoff.target_run_id]
                if r["seq"] == 48
            )
            if change == "truncated-result":
                row["output_truncated"] = True
            else:
                row["output"] = row["output"].replace("exit=0", "exit=1", 1)
        with pytest.raises(ValidationError):
            prepare_sdk_checkpoint_review(
                c.store, c.runtime, c.manifest, key, index, "scope boundary check"
            )
    assert c.item == before
    c.runtime.dispatch_reviewer.assert_not_called()


def test_checkpoint_actual_metadata_budget_preserves_reject_and_history(
    successor, monkeypatch
):
    import hashlib
    from omac.engines.multica import MulticaStore
    from omac.core.review_convergence import build_review_obligations
    from omac.pipeline.sdk_checkpoint_review import _obligations

    c = successor
    request = prepare(c)
    body = _obligations(build_review_obligations(c.item), request["tuple"])
    folder = Path(__file__).parent / "fixtures/sdk_command18_checkpoint"
    metadata = json.loads((folder / "metadata.json").read_text())
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


def test_checkpoint_rejects_consistently_changed_contract(successor):
    import hashlib
    import yaml
    from omac.pipeline.publication_review import _dump_contract

    c = successor
    c.manifest.nodes[KEY].contract.objective += "\nchanged authority"
    c.item.contract = _dump_contract(c.manifest.nodes[KEY].contract)
    binding = dict(c.item.worker_handoff.review_context_binding)
    binding["contract_sha256"] = _digest(c.item.contract)
    c.item.worker_handoff = replace(
        c.item.worker_handoff, review_context_binding=binding
    )
    body = yaml.safe_dump(c.item.contract, sort_keys=False).encode()
    ref = c.item.contract_ref
    ref["sha256"] = hashlib.sha256(body).hexdigest()
    ref["bytes"] = len(body)
    aid = ref["attachment_id"]
    c.blobs[aid] = replace(c.blobs[aid], content=body, sha256=ref["sha256"])
    before = deepcopy(c.item)
    with pytest.raises(ValidationError, match="Exact SDK checkpoint successor"):
        prepare(c)
    assert c.item == before

"""Narrow publication-only approval uses the intact native chain, never session files."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from omac.core.manifest import (
    Manifest,
    Node,
    _load_contract,
    save_manifest,
    load_manifest,
)
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase, parse_worker_handoff
from omac.engines import create_engine
from omac.engines.models import (
    AgentRunObservation,
    EngineConfig,
    WorkItem,
    WorkItemStatus,
    VerificationAttachmentObservation,
    ReleaseAssetObservation,
    PullRequestReadiness,
)
from omac.errors import ValidationError, NeedsDecision, PlatformError
from omac.pipeline.evidence_review import _digest
from omac.pipeline.publication_review import (
    prepare_publication_review,
    apply_publication_review,
    JOURNAL,
    KEY,
)
from omac.pipeline.loop import _worker_handoff_has_new_delivery


@pytest.fixture
def case(tmp_path):
    folder = Path(__file__).parent / "fixtures/preview_publication"
    raw = json.loads((folder / "issue.json").read_text())
    contract = yaml.safe_load((folder / "contract_ref.yaml").read_text())
    engine = create_engine(
        "mock",
        EngineConfig(
            "mock", raw["workspace_id"], extra={"MOCK_AUTO_COMPLETE": "false"}
        ),
    )
    item = WorkItem(
        id=raw["id"],
        workspace_id=raw["workspace_id"],
        title="preview",
        description="",
        status=WorkItemStatus.DONE,
        dag_key=KEY,
        worker="codex-macmini-newapi",
        contract=contract,
        artifacts=json.loads(raw["metadata"]["artifacts"]),
        verification=yaml.safe_load((folder / "verification_ref.yaml").read_text()),
        verification_ref=json.loads(raw["metadata"]["verification_ref"]),
        contract_ref=json.loads(raw["metadata"]["contract_ref"]),
        review_ledger=yaml.safe_load((folder / "review_ledger_ref.yaml").read_text()),
        review_ledger_ref=json.loads(raw["metadata"]["review_ledger_ref"]),
        review_obligations=yaml.safe_load(
            (folder / "review_obligations_ref.yaml").read_text()
        ),
        worker_handoff=parse_worker_handoff(
            json.loads(raw["metadata"]["worker_handoff"])
        ),
        bounces=Bounces(worker=4, review=1),
        phase=TaskPhase.AUTHORING,
        kind=TaskKind.DEVELOP,
    )
    engine.store.get_work_item = lambda _: item
    engine.store.resolve_agent_id = lambda n: (
        "037ad7f0-98f9-489f-bf43-0d1148233356"
        if n == item.worker
        else "94f847b9-c10f-4d64-88b2-e90cb5c03ffc"
    )
    observations = json.loads((folder / "attachment-observations.json").read_text())
    observations["original"] = json.loads(
        (folder / "original-observation.json").read_text()
    )
    blobs = {}
    for label, meta in observations.items():
        name = "original-verification.yaml" if label == "original" else label + ".yaml"
        blobs[meta["attachment_id"]] = VerificationAttachmentObservation(
            content=(folder / name).read_bytes(), **meta
        )
    engine.store.observe_verification_attachment = lambda _, ref: blobs[
        ref["attachment_id"]
    ]
    original_ref = json.loads((folder / "original-ref.json").read_text())
    engine.store.read_verification_reference = lambda iid, cid: (
        deepcopy(original_ref)
        if cid == original_ref["comment_id"]
        else (_ for _ in ()).throw(PlatformError("unknown reference"))
    )
    head = item.artifacts["head_sha"]
    engine.store.read_pull_request_readiness = lambda _: PullRequestReadiness(
        False, "OPEN", head_sha=head
    )
    run_data = json.loads((folder / "runs.json").read_text())
    runtime = Mock()
    runtime.list_runs.return_value = [AgentRunObservation(**r) for r in run_data]
    reviewer_messages = json.loads((folder / "reviewer-messages.json").read_text())
    worker_messages = json.loads((folder / "worker-messages.json").read_text())
    latest_run_id = item.worker_handoff.target_run_id
    runtime.read_run_messages.side_effect = lambda iid, rid: deepcopy(
        worker_messages if rid == latest_run_id else reviewer_messages
    )
    release = json.loads((folder / "release.json").read_text())
    index_url = next(
        a["browser_download_url"]
        for a in release["assets"]
        if a["name"] == "evidence-index.json"
    )
    assets = {}
    for a in release["assets"]:
        file = folder / "assets" / a["name"]
        if not file.exists():
            continue
        ref = {
            "url": a["browser_download_url"],
            "repository": "xiaohei-info/open-agent-cluster",
            "release_id": release["id"],
            "release_node_id": release["node_id"],
            "tag": release["tag_name"],
            "tag_commit_sha": head,
            "target_commitish": release["target_commitish"],
            "release_immutable": release["immutable"],
            "asset_id": a["id"],
            "asset_node_id": a["node_id"],
            "name": a["name"],
            "updated_at": a["updated_at"],
            "sha256": a["digest"].split(":")[1],
            "bytes": a["size"],
        }
        assets[ref["url"]] = ReleaseAssetObservation(ref, file.read_bytes())

    def observe(urls, *, download=True):
        return [
            ReleaseAssetObservation(
                deepcopy(assets[u].reference), assets[u].content if download else b""
            )
            for u in urls
        ]

    engine.store.observe_release_assets = observe
    node = Node(
        id=KEY,
        worker=item.worker,
        reviewer="hermes-reviewer",
        work_item_id=item.id,
        contract=_load_contract(contract),
        status="in_progress",
    )
    manifest = Manifest(meta={}, nodes={KEY: node})
    path = str(tmp_path / "m.yaml")
    save_manifest(manifest, path)
    return SimpleNamespace(
        store=engine.store,
        runtime=runtime,
        item=item,
        manifest=manifest,
        path=path,
        blobs=blobs,
        index_url=index_url,
        assets=assets,
        original_ref=original_ref,
        worker_messages=worker_messages,
        reviewer_messages=reviewer_messages,
    )


def prepare(c):
    return prepare_publication_review(
        c.store,
        c.runtime,
        c.manifest,
        KEY,
        c.index_url,
        "explicit publication-only review",
    )


def apply(c, request):
    return apply_publication_review(
        c.store,
        c.runtime,
        c.path,
        KEY,
        request,
        approved_request_sha256=_digest(request),
    )


def test_actual_fixture_preparation_and_budget_neutral_independent_review(case):
    c = case
    before = deepcopy(c.item)
    request = prepare(c)
    assert c.item == before
    assert request["tuple"]["run"]["id"] == "01a0fca3-1807-7814-96f0-683b2eff8773"
    assert request["tuple"]["original_verification_ref"] == c.original_ref
    assert request["tuple"]["verification_ref"]["sha256"] != c.original_ref["sha256"]
    assert len(request["tuple"]["publication"]["assets"]) == 9
    assert request["tuple"]["publication"]["assets"][0]["release_immutable"] is False
    assert request["tuple"]["budget"]["absolute"] == {
        "worker": 4,
        "review": 1,
        "merge": 0,
    }
    assert (
        request["tuple"]["budget"]["consumed"] == request["tuple"]["budget"]["absolute"]
    )
    assert _worker_handoff_has_new_delivery(c.item, c.item.worker_handoff) is False
    result = apply(c, request)
    assert (
        result["state"] == "ready-for-independent-review" and result["verdict"] is None
    )
    assert c.item.delivery_identity.run_id == "01a0fca3-1807-7814-96f0-683b2eff8773"
    assert (
        c.item.delivery_identity.verification_uploader_id
        == "037ad7f0-98f9-489f-bf43-0d1148233356"
    )
    assert (
        c.item.bounces == before.bounces
        and c.item.bounce_baseline == before.bounce_baseline
    )
    assert (
        c.item.review_ledger == before.review_ledger
        and c.item.review_ledger_ref == before.review_ledger_ref
    )
    evidence = next(
        o
        for o in c.item.review_obligations
        if o["obligation_id"] == "dimension:evidence"
    )
    assert (
        evidence["publication_recovery"]["original_report_ref"]
        == before.worker_handoff.source_review_feedback["report_ref"]
    )
    assert (
        evidence["publication_recovery"]["original_blockers"][0]["root_cause_key"]
        == "preview-repair-immutable-evidence-not-published"
    )
    assert c.item.status is WorkItemStatus.IN_REVIEW and c.item.review_verdict is None
    assert load_manifest(c.path).nodes[KEY].status == "in_review"
    with pytest.raises(NeedsDecision):
        apply(c, request)
    assert c.runtime.wake.call_count == 0


@pytest.mark.parametrize(
    "drift",
    [
        "head",
        "contract",
        "generation",
        "verification",
        "uploader",
        "run",
        "ledger-ref",
        "asset-id",
        "asset-bytes",
        "source-missing",
        "budget",
    ],
)
def test_source_or_cas_drift_refuses_before_store_writes(case, drift):
    c = case
    request = prepare(c)
    if drift == "head":
        c.store.read_pull_request_readiness = lambda _: PullRequestReadiness(
            False, "OPEN", head_sha="f" * 40
        )
    if drift == "contract":
        c.item.contract = {**c.item.contract, "objective": "changed"}
    if drift == "generation":
        c.item.review_generation = "foreign"
    if drift == "verification":
        c.item.verification_ref = {
            **c.item.verification_ref,
            "attachment_id": c.original_ref["attachment_id"],
        }
    if drift == "uploader":
        v = c.blobs[c.item.verification_ref["attachment_id"]]
        c.blobs[v.attachment_id] = replace(v, uploader_id="foreign")
    if drift == "run":
        c.runtime.list_runs.return_value = [
            replace(r, status="running")
            if r.id == c.item.worker_handoff.target_run_id
            else r
            for r in c.runtime.list_runs.return_value
        ]
    if drift == "ledger-ref":
        c.item.review_ledger_ref = {**c.item.review_ledger_ref, "sha256": "f" * 64}
    if drift == "asset-id":
        a = c.assets[c.index_url]
        c.assets[c.index_url] = ReleaseAssetObservation(
            {**a.reference, "asset_id": 999}, a.content
        )
    if drift == "asset-bytes":
        a = c.assets[c.index_url]
        c.assets[c.index_url] = ReleaseAssetObservation(a.reference, b"{}")
    if drift == "source-missing":
        c.runtime.read_run_messages.side_effect = lambda *a: []
    if drift == "budget":
        c.item.bounces.worker += 1
    c.store.update_work_item_metadata = Mock(side_effect=AssertionError("no writes"))
    c.store.update_status = Mock(side_effect=AssertionError("no writes"))
    with pytest.raises((ValidationError, PlatformError)):
        apply(c, request)
    assert c.store.update_work_item_metadata.call_count == 0


@pytest.mark.parametrize("step", range(8))
def test_unknown_write_response_resumes_only_exact_request(case, step):
    c = case
    request = prepare(c)
    original = c.store.update_work_item_metadata
    status = c.store.update_status
    counter = 0

    def write(fn, *a, **kw):
        nonlocal counter
        index = counter
        counter += 1
        result = fn(*a, **kw)
        if index == step:
            raise PlatformError("response unknown after committed write")
        return result

    c.store.update_work_item_metadata = lambda *a, **kw: write(original, *a, **kw)
    c.store.update_status = lambda *a, **kw: write(status, *a, **kw)
    with pytest.raises(PlatformError):
        apply(c, request)
    c.store.update_work_item_metadata = original
    c.store.update_status = status
    assert apply(c, request)["state"] == "ready-for-independent-review"
    assert c.item.bounces.worker == 4 and c.item.bounces.review == 1


def test_approval_digest_and_extra_identity_fields_are_rejected(case):
    c = case
    request = prepare(c)
    with pytest.raises(ValidationError):
        apply_publication_review(
            c.store, c.runtime, c.path, KEY, request, approved_request_sha256="0" * 64
        )
    tampered = {**request, "delivery_identity": {}}
    with pytest.raises(ValidationError):
        apply(c, tampered)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewed_pr", "https://github.com/xiaohei-info/open-agent-cluster/pull/999"),
        ("reviewed_head_sha", "f" * 40),
    ],
)
def test_contradictory_original_report_cannot_prepare(case, field, value):
    c = case
    feedback = deepcopy(c.item.worker_handoff.source_review_feedback)
    rr = feedback["report_ref"]
    lr = feedback["ledger_ref"]
    report = yaml.safe_load(c.blobs[rr["attachment_id"]].content)
    report[field] = value
    content = yaml.safe_dump(report, sort_keys=False).encode()
    import hashlib
    from omac.core.review_convergence import _review_report_digest

    feedback["report_ref"] = {
        **rr,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    c.blobs[rr["attachment_id"]] = replace(
        c.blobs[rr["attachment_id"]],
        content=content,
        sha256=feedback["report_ref"]["sha256"],
    )
    ledger = deepcopy(c.item.review_ledger)
    ledger["cycles"][-1]["report_digest"] = _review_report_digest(report)
    data = yaml.safe_dump(ledger, sort_keys=False).encode()
    feedback["ledger_ref"] = {
        **lr,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    c.blobs[lr["attachment_id"]] = replace(
        c.blobs[lr["attachment_id"]],
        content=data,
        sha256=feedback["ledger_ref"]["sha256"],
    )
    c.item.review_ledger = ledger
    c.item.review_ledger_ref = feedback["ledger_ref"]
    c.item.worker_handoff = replace(
        c.item.worker_handoff, source_review_feedback=feedback
    )
    with pytest.raises(ValidationError):
        prepare(c)


def test_forged_consumed_journal_cannot_start_runner(case):
    from omac.pipeline.publication_review import ensure_publication_review_complete

    case.manifest.meta[JOURNAL] = {"forged": {"state": "consumed", "step": 0}}
    with pytest.raises(ValidationError):
        ensure_publication_review_complete(case.manifest, case.path)


def test_pending_prefix_cannot_start_runner(case):
    from omac.pipeline.loop import tick

    c = case
    request = prepare(c)
    original = c.store.update_work_item_metadata

    def unknown(*a, **kw):
        original(*a, **kw)
        raise PlatformError("committed reply lost")

    c.store.update_work_item_metadata = unknown
    with pytest.raises(PlatformError):
        apply(c, request)
    pending = load_manifest(c.path)
    c.store.observe_work_item_control = Mock(
        side_effect=AssertionError("No Runner I/O before pending guard")
    )
    with pytest.raises(NeedsDecision):
        tick(c.store, c.runtime, pending, c.path)


@pytest.mark.parametrize("suffix", [" ; echo spoof", " && true", " | cat"])
def test_native_submit_shell_tail_is_not_a_receipt(case, suffix):
    c = case
    for row in c.worker_messages:
        if row.get("seq") == 119:
            row["input"]["command"] = "/bin/zsh -lc " + __import__("shlex").quote(
                "OMAC_ENGINE=multica omac work submit "
                + c.item.id
                + " --pr-url "
                + c.item.artifacts["pr_url"]
                + " --verification-file artifacts/verification.yaml"
                + suffix
            )
    with pytest.raises(ValidationError):
        prepare(c)


def test_apply_preserves_existing_budget_authority(case):
    c = case
    from omac.core.taskmeta import review_context_binding

    c.manifest.meta.update(
        last_amendment_id="existing",
        amendment_apply={
            "schema": "omac.amendment-apply/v1",
            "amendment_id": "existing",
            "nodes": {},
            "retained_bounce_baselines": {
                KEY: {
                    "amendment_id": "prior",
                    "work_item_id": c.item.id,
                    "contract_sha256": review_context_binding(c.item)[
                        "contract_sha256"
                    ],
                    "bounce_baseline": {"worker": 0, "review": 0, "merge": 0},
                }
            },
        },
    )
    save_manifest(c.manifest, c.path)
    before = deepcopy(c.manifest.meta)
    request = prepare(c)
    apply(c, request)
    meta = load_manifest(c.path).meta
    assert {k: v for k, v in meta.items() if k != JOURNAL} == before
    assert c.item.bounces.worker == 4 and c.item.bounces.review == 1


def test_asset_substitution_during_apply_stops_before_next_write(case):
    c = case
    request = prepare(c)
    original = c.store.update_work_item_metadata
    writes = []

    def first(*a, **kw):
        result = original(*a, **kw)
        writes.append(kw)
        asset = c.assets[c.index_url]
        c.assets[c.index_url] = ReleaseAssetObservation(
            {**asset.reference, "asset_id": asset.reference["asset_id"] + 1},
            asset.content,
        )
        return result

    c.store.update_work_item_metadata = first
    with pytest.raises(ValidationError):
        apply(c, request)
    assert len(writes) == 1 and c.item.status is WorkItemStatus.DONE
    assert load_manifest(c.path).meta[JOURNAL][_digest(request)]["state"] == "pending"


def test_independent_reviewer_receives_frozen_obligations(case, monkeypatch):
    from omac.pipeline import loop

    c = case
    request = prepare(c)
    apply(c, request)
    ready = load_manifest(c.path)
    obligations = deepcopy(c.item.review_obligations)
    c.store.update_status(c.item.id, WorkItemStatus.IN_REVIEW)
    c.store.read_pull_request_readiness = lambda _: PullRequestReadiness(
        False, "OPEN", head_sha=c.item.artifacts["head_sha"]
    )
    c.runtime.capabilities.stable_direct_run_identity = True
    from omac.core.review_convergence import review_subject_digest

    assert c.item.review_subject_digest == review_subject_digest(c.item, 2)
    source = c.item.delivery_identity
    assert source.run_id == request["tuple"]["run"]["id"]
    # Ensure normal dispatch's same-subject path cannot reset or overwrite the frozen obligation.
    c.store.prepare_review_cycle = Mock(
        side_effect=AssertionError("No reset for canonical prepared subject")
    )
    c.store.reset_review = Mock(side_effect=AssertionError("No clearing source"))
    c.store.update_work_item_metadata = Mock(wraps=c.store.update_work_item_metadata)
    monkeypatch.setattr(loop, "_refresh_develop_issue_body", lambda *a, **kw: None)

    def dispatch(store, item_id, reviewer):
        from dataclasses import replace

        c.item.reviewer = reviewer
        c.item.reviewer_run_baseline = replace(
            c.item.reviewer_run_baseline, target_run_id="new-independent-review"
        )
        return True

    c.runtime.dispatch_reviewer = Mock(side_effect=dispatch)
    c.runtime.is_active = lambda _: True
    assert loop._dispatch_reviewer_for_current_subject_locked(
        c.store, c.runtime, ready, "release-preview-audit-vocabulary-repair"
    )
    assert not loop._dispatch_reviewer_for_current_subject_locked(
        c.store, c.runtime, ready, "release-preview-audit-vocabulary-repair"
    )
    c.runtime.dispatch_reviewer.assert_called_once()
    assert c.item.review_obligations == obligations
    # The frozen obligation itself is observable through the existing Reviewer API.
    from omac.pipeline.dispatch import build_show_output

    shown = build_show_output(c.item, "reviewer:hermes-reviewer")
    assert shown["context"]["review_obligations"] == obligations
    assert (
        shown["context"]["prior_open_blockers"][0]["root_cause_key"]
        == "preview-repair-immutable-evidence-not-published"
    )


def test_cli_generates_request_and_requires_explicit_apply_digest(
    case, monkeypatch, capsys, tmp_path
):
    from omac.cli.main import main
    from omac.cli.commands import node as command

    c = case
    engine = SimpleNamespace(store=c.store, runtime=c.runtime)
    monkeypatch.setattr(command, "_build_engine", lambda _: engine)
    assert (
        main(
            [
                "node",
                "review-publication",
                c.path,
                KEY,
                "--index-url",
                c.index_url,
                "--reason",
                "approved scope",
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(prepared))
    assert (
        main(["node", "review-publication", c.path, KEY, "--apply-request", str(path)])
        == 5
    )
    capsys.readouterr()
    assert (
        main(
            [
                "node",
                "review-publication",
                c.path,
                KEY,
                "--apply-request",
                str(path),
                "--approve-request-sha256",
                _digest(prepared),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ready-for-independent-review"


@pytest.mark.parametrize(
    "error", ["digest", "id-replaced", "tag-changed", "foreign-url", "unavailable"]
)
def test_real_adapter_pins_assets_and_rejects_changed_native_facts(
    case, monkeypatch, error
):
    import subprocess
    from omac.engines.multica import MulticaStore

    c = case
    store = MulticaStore(EngineConfig("multica", c.item.workspace_id))
    data = json.loads(
        (
            Path(__file__).parent / "fixtures/preview_publication/release.json"
        ).read_text()
    )
    selected = next(
        a for a in data["assets"] if a["browser_download_url"] == c.index_url
    )
    body = c.assets[c.index_url].content
    downloads = 0

    def run(args, **kw):
        nonlocal downloads
        endpoint = args[4]
        binary = args[-1] == "Accept: application/octet-stream"
        assert args[:4] == ["gh", "api", "--method", "GET"]
        if error == "unavailable":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"404")
        if binary:
            downloads += 1
            if error == "id-replaced":
                selected["id"] += 1
            return SimpleNamespace(
                returncode=0, stdout=(b"{}" if error == "digest" else body), stderr=b""
            )
        if "/git/ref/" in endpoint:
            value = {
                "object": {
                    "type": "commit",
                    "sha": (
                        "f" * 40
                        if error == "tag-changed"
                        else c.item.artifacts["head_sha"]
                    ),
                }
            }
        else:
            value = deepcopy(data)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(value).encode(), stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", run)
    url = (
        c.index_url
        if error != "foreign-url"
        else c.index_url.replace("github.com", "evil.example")
    )
    if error == "tag-changed":
        observations = store.observe_release_assets([url])
        assert (
            observations[0].reference["tag_commit_sha"]
            != "1dd603b45fe0ef9ac4bbc2ac75b0d1e64f2ed277"
        )
        # Caller compares this exact native identity to the approved HEAD.
    else:
        with pytest.raises(PlatformError):
            store.observe_release_assets([url])


@pytest.fixture
def collected_review(case):
    from types import MethodType
    from omac.engines.runtime import AgentRuntime

    c = case
    request = prepare(c)
    apply(c, request)
    c.manifest = load_manifest(c.path)
    c.runtime.capabilities.stable_direct_run_identity = True
    c.runtime.dispatch_reviewer = MethodType(AgentRuntime.dispatch_reviewer, c.runtime)
    c.runtime.wake_reviewer = MethodType(AgentRuntime.wake_reviewer, c.runtime)
    runs = c.runtime.list_runs.return_value
    reviewer_id = c.store.resolve_agent_id("hermes-reviewer")

    def wake(iid, agent, role):
        assert (iid, agent, role) == (c.item.id, "hermes-reviewer", "reviewer")
        runs.append(
            AgentRunObservation(
                id="new-formal-reviewer",
                kind="direct",
                status="running",
                agent_id=reviewer_id,
                created_at="2026-10-03T02:29:00Z",
                trigger_kind="rerun",
            )
        )

    c.runtime.wake = Mock(side_effect=wake)
    c.runtime.is_active.side_effect = lambda _: any(r.active for r in runs)
    c.store.assign_work_item = Mock(wraps=c.store.assign_work_item)
    return c


def normal_review_collection(c):
    from omac.pipeline import loop

    observed = loop.reconcile_with_observations(c.store, c.manifest, c.path)
    return loop.collect_results(
        c.store, c.runtime, c.manifest, c.path, observations=observed.observations
    )


@pytest.mark.parametrize("persisted_baseline", [False, True])
def test_applied_publication_enters_normal_reviewer_dispatch(
    collected_review, persisted_baseline
):
    from omac.core.taskmeta import ReviewerRunBaseline

    c = collected_review
    if persisted_baseline:
        c.item.reviewer_run_baseline = ReviewerRunBaseline(
            schema="omac.reviewer-run-baseline/v1",
            subject_digest=c.item.review_subject_digest,
            target_reviewer="hermes-reviewer",
            target_agent_id=c.store.resolve_agent_id("hermes-reviewer"),
            cutoff_created_at=c.item.delivery_identity.verification_created_at,
            generation="review-5d159f823ee463da",
            baseline_direct_run_ids=("01a0fc04-b1eb-758c-b60e-4fe19e5d3dfb",),
        )
    before = deepcopy(c.item)
    journal = deepcopy(c.manifest.meta[JOURNAL])
    assert normal_review_collection(c) == {}
    c.store.assign_work_item.assert_called_once_with(
        c.item.id, "hermes-reviewer", "reviewer", start_run=False
    )
    c.runtime.wake.assert_called_once()
    assert normal_review_collection(c) == {}
    assert normal_review_collection(c) == {}
    assert c.runtime.wake.call_count == 1
    assert c.store.assign_work_item.call_count == 1
    assert c.item.reviewer_run_baseline.target_run_id == "new-formal-reviewer"
    if persisted_baseline:
        assert (
            c.item.reviewer_run_baseline.generation
            == before.reviewer_run_baseline.generation
        )
    assert c.item.review_subject_digest == before.review_subject_digest
    assert c.item.review_obligations == before.review_obligations
    assert c.item.review_ledger == before.review_ledger
    assert c.item.review_ledger_ref == before.review_ledger_ref
    assert c.item.delivery_identity == before.delivery_identity
    assert c.item.bounces == before.bounces
    assert c.item.bounce_baseline == before.bounce_baseline
    assert c.manifest.meta[JOURNAL] == journal
    assert c.item.review_verdict is None


@pytest.mark.parametrize(
    "status,bound",
    [
        ("running", False),
        ("running", True),
        ("completed", False),
        ("completed", True),
        ("missing", True),
    ],
)
def test_existing_reviewer_attempt_is_not_initial_dispatch(
    collected_review, monkeypatch, status, bound
):
    from omac.pipeline import loop

    c = collected_review
    reviewer_id = c.store.resolve_agent_id("hermes-reviewer")
    baseline, error = loop._reviewer_run_baseline_for_observation(
        c.store, c.runtime, c.manifest, KEY, c.item, "hermes-reviewer", reviewer_id
    )
    assert error is None
    if bound:
        c.item.reviewer_run_baseline = replace(
            baseline, target_run_id="existing-review"
        )
    if status != "missing":
        c.runtime.list_runs.return_value.append(
            AgentRunObservation(
                id="existing-review",
                kind="direct",
                status=status,
                agent_id=reviewer_id,
                created_at="2026-10-03T02:29:00Z",
                updated_at="2026-10-03T02:30:00Z" if status == "completed" else None,
                trigger_kind="rerun",
            )
        )
    monkeypatch.setattr(loop, "_reviewer_no_submit_grace_state", lambda *_: "waiting")
    normal_review_collection(c)
    c.store.assign_work_item.assert_not_called()
    c.runtime.wake.assert_not_called()
    assert c.item.reviewer_run_baseline.target_run_id == "existing-review"


@pytest.mark.parametrize(
    "boundary", ["assignment-before", "assignment-after", "wake-before", "wake-after"]
)
def test_prepared_dispatch_unknown_response_does_not_duplicate(
    collected_review, monkeypatch, boundary
):
    from omac.pipeline import loop

    c = collected_review
    monkeypatch.setattr(loop.time, "sleep", lambda *_: None)
    original_assign = c.store.assign_work_item
    original_wake = c.runtime.wake.side_effect

    def assign(*args, **kwargs):
        if boundary == "assignment-after":
            original_assign(*args, **kwargs)
        raise PlatformError("unknown assignment response")

    def wake(*args, **kwargs):
        if boundary == "wake-after":
            original_wake(*args, **kwargs)
        raise PlatformError("unknown wake response")

    if boundary.startswith("assignment"):
        c.store.assign_work_item = Mock(side_effect=assign)
    else:
        c.runtime.wake.side_effect = wake
    obligations = deepcopy(c.item.review_obligations)
    counters = deepcopy(c.item.bounces)
    failures = normal_review_collection(c)
    assert failures and c.item.decision_required
    first_assignment = c.store.assign_work_item.call_count
    first_wake = c.runtime.wake.call_count
    c.manifest = load_manifest(c.path)
    normal_review_collection(c)
    assert c.store.assign_work_item.call_count == first_assignment
    assert c.runtime.wake.call_count == first_wake
    assert c.item.review_obligations == obligations
    assert c.item.bounces == counters
    assert (
        c.manifest.meta[JOURNAL][next(iter(c.manifest.meta[JOURNAL]))]["state"]
        == "consumed"
    )


@pytest.mark.parametrize(
    "drift",
    [
        "decision",
        "head",
        "verification",
        "cutoff",
        "phase",
        "status",
        "assignee",
        "baseline",
    ],
)
def test_prepared_dispatch_rechecks_fresh_control_and_seal(collected_review, drift):
    c = collected_review
    observe = c.store.observe_work_item_control
    count = 0

    def changed(iid):
        nonlocal count
        result = observe(iid)
        count += 1
        # Initial reconcile reads the intact source; dispatch must see the newer fact.
        if count == 2:
            if drift == "decision":
                c.item.decision_required = {"reason_code": "external-stop"}
            elif drift == "head":
                c.item.artifacts["head_sha"] = "f" * 40
            elif drift == "verification":
                original = c.blobs[c.item.verification_ref["attachment_id"]]
                c.blobs["changed-reference"] = replace(
                    original, attachment_id="changed-reference"
                )
                c.item.verification_ref["attachment_id"] = "changed-reference"
            elif drift == "phase":
                c.item.phase = TaskPhase.CONFIRMATION
            elif drift == "status":
                c.item.status = WorkItemStatus.DONE
            elif drift == "assignee":
                c.item.platform_assignee_id = "foreign-agent"
            elif drift == "baseline":
                c.item.reviewer_run_baseline = replace(
                    c.item.reviewer_run_baseline, generation="foreign-generation"
                )
            else:
                c.item.delivery_identity = replace(
                    c.item.delivery_identity,
                    verification_created_at="2026-10-02T13:27:08Z",
                )
        return result

    c.store.observe_work_item_control = changed
    assert normal_review_collection(c)
    c.store.assign_work_item.assert_not_called()
    c.runtime.wake.assert_not_called()


def test_ambiguous_prepared_reviewer_assignment_is_observed_not_woken(
    collected_review, monkeypatch
):
    from omac.pipeline import loop

    c = collected_review
    c.item.reviewer = "hermes-reviewer"
    c.item.platform_assignee_id = c.store.resolve_agent_id("hermes-reviewer")
    monkeypatch.setattr(loop.time, "sleep", lambda *_: None)
    assert normal_review_collection(c) == {}
    assert c.item.decision_required in (None, {})
    c.store.assign_work_item.assert_not_called()
    c.runtime.wake.assert_not_called()


def test_prepared_confirmation_is_not_a_reviewer_dispatch(collected_review):
    c = collected_review
    c.item.phase = TaskPhase.CONFIRMATION
    normal_review_collection(c)
    c.store.assign_work_item.assert_not_called()
    c.runtime.wake.assert_not_called()
    assert c.item.phase == TaskPhase.CONFIRMATION

"""Captured source bytes through the public amendment pipeline, offline only.

Mock-native identities for the old Package amendment are transport fixtures;
they do not establish live eligibility or adjudicate any OAC owner claim.
"""

import copy
import hashlib
import json
import shutil
from dataclasses import asdict, fields
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from omac.core.manifest import load_manifest, save_manifest, _dump_contract
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
from omac.engines import mock
from omac.engines.models import (
    AgentRunObservation,
    EngineConfig,
    WorkItem,
    WorkItemStatus,
)
from omac.errors import ValidationError, NeedsDecision, PlatformError
from omac.pipeline.amendment import propose_amendment, accept_amendment

FIXTURES = Path(__file__).parent / "fixtures/owner_amendment_admission"
CASES = (
    "identity-local",
    "credential-store",
    "api-agent",
    "mcp-catalog-governed-hardening",
    "audit-ledger",
    "extension-package",
)


def api():
    from omac.pipeline.owner_amendment import (
        prepare_owner_amendment,
        resolve_owner_amendment,
    )

    return prepare_owner_amendment, resolve_owner_amendment


@pytest.fixture(params=CASES)
def case(request, tmp_path, monkeypatch):
    key = request.param
    shutil.copytree(FIXTURES / "required-contracts", tmp_path, dirs_exist_ok=True)
    monkeypatch.chdir(tmp_path)
    manifest = load_manifest(str(FIXTURES / "current77.yaml"))
    assert len(manifest.nodes) == 183
    assert sum(n.status == "done" for n in manifest.nodes.values()) == 77
    engine = create_engine(
        "mock",
        EngineConfig(
            engine_type="mock",
            workspace_id="410ade5e-8ae0-4402-b975-813dea2ff3e1",
            polling_interval=0,
            extra={"MOCK_AUTO_COMPLETE": "true"},
        ),
    )
    mock.MockStore.set_auto_complete(True, 0)
    # Full original acceptance bytes supplied by Root match the captured
    # manifest identity; retain the actual configured relative document path.
    (tmp_path / manifest.meta["acceptance_file"]).write_bytes(
        (FIXTURES / "open-agent-cluster.acceptance.yaml").read_bytes()
    )
    nodes = {}
    runs = {}
    bodies = {}
    for name in CASES:
        capture = json.loads(
            (FIXTURES / "current-six-full-controls" / (name + ".json")).read_text()
        )
        control = copy.deepcopy(capture["control"])
        values = {
            f.name: control[f.name] for f in fields(WorkItem) if f.name in control
        }
        values.update(
            status=WorkItemStatus(control["status"]),
            kind=TaskKind(control["kind"]),
            phase=TaskPhase(control["phase"]),
            bounces=Bounces(**control["bounces"]),
            worker_handoff=parse_worker_handoff(control.get("worker_handoff")),
            delivery_identity=parse_delivery_identity(control.get("delivery_identity")),
            reviewer_run_baseline=parse_reviewer_run_baseline(
                control.get("reviewer_run_baseline")
            ),
        )
        item = WorkItem(**values)
        nodes[name] = item
        mock._shared_work_items[item.id] = item
        runs[item.id] = [AgentRunObservation(**r) for r in capture["runs"]]
        if name != "extension-package":
            feedback = item.worker_handoff.source_review_feedback
            for kind, suffix in [
                ("report_ref", "retained_report_ref"),
                ("ledger_ref", "retained_ledger_ref"),
            ]:
                bodies[feedback[kind]["attachment_id"]] = (
                    FIXTURES
                    / "original-complete-failed-report-ledger-bytes"
                    / f"{name}-{suffix}.yaml"
                ).read_bytes()
    # The supplied Package evidence includes exact old reject/ledger bytes, but
    # no full old native WorkItem. Model that read using an explicitly offline
    # source item; never claim these synthetic platform IDs as captured facts.
    decision = nodes["extension-package"].decision_required
    source = WorkItem(
        id=decision["source_amendment_issue"],
        workspace_id="410ade5e-8ae0-4402-b975-813dea2ff3e1",
        title="offline old Package amendment",
        description="offline transport",
        status=WorkItemStatus.BLOCKED,
        dag_key="old-package-amendment",
        kind=TaskKind.AMENDMENT,
        phase=TaskPhase.REVIEW,
        worker="alice",
        reviewer="bob",
    )
    feedback = {
        "verdict": "reject",
        "subject_digest": "e4bb11308e55fd073417e779c3d36104e054f7f7b0f48072cfe45e34c8eb6dc7",
    }
    for kind, suffix in [
        ("report_ref", "original_package_amend_reject"),
        ("ledger_ref", "original_package_amend_ledger"),
    ]:
        body = (
            FIXTURES
            / "original-complete-failed-report-ledger-bytes"
            / f"extension-package-{suffix}.yaml"
        ).read_bytes()
        ref = {
            "attachment_id": "offline-package-" + kind,
            "comment_id": "offline-comment-" + kind,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }
        feedback[kind] = ref
        bodies[ref["attachment_id"]] = body
    mock._shared_work_items[source.id] = source
    runs[source.id] = []
    monkeypatch.setattr(
        engine.store,
        "recover_review_rework_context",
        lambda item_id: copy.deepcopy(feedback) if item_id == source.id else {},
    )
    from omac.engines.models import VerificationAttachmentObservation

    original_attachment = engine.store.observe_verification_attachment

    def attachment(item_id, ref):
        if ref["attachment_id"] not in bodies:
            return original_attachment(item_id, ref)
        body = bodies[ref["attachment_id"]]
        return VerificationAttachmentObservation(
            ref["attachment_id"],
            ref["comment_id"],
            hashlib.sha256(body).hexdigest(),
            body,
        )

    monkeypatch.setattr(engine.store, "observe_verification_attachment", attachment)
    # Model real native publication at the offline transport seam. Mock auto
    # Review ordinarily omits PASS attachment refs; production reports have
    # full immutable native report/ledger bytes and uploader/Run identities.
    original_clear = engine.store.clear_assignment

    def publish_then_clear(item_id):
        item = engine.store.get_work_item(item_id)
        if (
            item.kind == TaskKind.AMENDMENT
            and item.review_report
            and item.review_ledger
        ):
            reviewer = item.reviewer
            native_runs = engine.runtime.list_runs(item_id)
            run = next(
                r
                for r in native_runs
                if r.agent_id == engine.store.resolve_agent_id(reviewer)
            )
            for field in ("review_report", "review_ledger"):
                body = yaml.safe_dump(
                    getattr(item, field), allow_unicode=True, sort_keys=False
                ).encode()
                ref = {
                    "attachment_id": "offline-fresh-" + item_id + "-" + field,
                    "comment_id": "offline-fresh-comment-" + item_id + "-" + field,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                    "uploader_type": "agent",
                    "uploader_id": engine.store.resolve_agent_id(reviewer),
                    "task_id": run.id,
                    "created_at": "2026-01-01T00:00:01Z",
                }
                mock._shared_attachment_bodies[ref["attachment_id"]] = body
                setattr(item, field + "_ref", ref)
        original_clear(item_id)

    monkeypatch.setattr(engine.store, "clear_assignment", publish_then_clear)
    original_get = engine.store.get_work_item
    publishing_planner = set()

    def publish_planner_delivery(item_id):
        item = original_get(item_id)
        if (
            item.kind == TaskKind.AMENDMENT
            and item.deliverable
            and not item.deliverable_ref
            and item_id not in publishing_planner
        ):
            planner_id = engine.store.resolve_agent_id("alice")
            publishing_planner.add(item_id)
            try:
                matches = [
                    r
                    for r in engine.runtime.list_runs(item_id)
                    if r.agent_id == planner_id and r.status == "completed"
                ]
            finally:
                publishing_planner.remove(item_id)
            if matches:
                raw = item.deliverable.encode()
                ref = {
                    "attachment_id": "offline-planner-" + item.id,
                    "comment_id": "offline-planner-comment-" + item.id,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "uploader_type": "agent",
                    "uploader_id": planner_id,
                    "task_id": matches[-1].id,
                    "created_at": "2026-01-01T00:00:01Z",
                }
                mock._shared_attachment_bodies[ref["attachment_id"]] = raw
                item.deliverable_ref = ref
        return item

    monkeypatch.setattr(engine.store, "get_work_item", publish_planner_delivery)
    original_runs = engine.runtime.list_runs
    monkeypatch.setattr(
        engine.runtime,
        "list_runs",
        lambda item_id: (
            copy.deepcopy(runs[item_id]) if item_id in runs else original_runs(item_id)
        ),
    )
    pool = (
        {"alice", "bob"}
        | {n.worker for n in manifest.nodes.values()}
        | {n.reviewer for n in manifest.nodes.values() if n.reviewer}
    )
    monkeypatch.setattr(engine.store, "list_members", lambda _: sorted(pool))
    path = tmp_path / "dag.yaml"
    seed_manifest(FIXTURES / "current77.yaml", path)
    docs = tmp_path / "docs.md"
    docs.write_text("Offline assessment input; no product claims are adjudicated.")
    report = tmp_path / "request.md"
    report.write_text(
        "Assess a source-bound minimal coherent amendment. Do not approve owner claims."
    )
    return SimpleNamespace(
        key=key,
        engine=engine,
        manifest=manifest,
        path=str(path),
        item=nodes[key],
        nodes=nodes,
        runs=runs,
        bodies=bodies,
        pool=pool,
        report=str(report),
        docs=[str(docs)],
        output=str(tmp_path / "reviewed.yaml"),
        request=str(tmp_path / "owner-request.json"),
    )


def prepare(c, allowed=None):
    prepare_fn, _ = api()
    return prepare_fn(
        c.engine,
        c.path,
        blocked_nodes=[c.key],
        allowed_nodes=allowed or [c.key],
        report_file=c.report,
        docs=c.docs,
        output_file=c.request,
    )


def resolve(c, prepared):
    result = api()[1](
        c.engine,
        c.path,
        c.request,
        request_sha256=prepared["request_sha256"],
        authority="offline-coordinator",
        reason="Assess source-bound minimal coherent amendment; no product PASS.",
    )
    c.approval_sha256 = result["approval_sha256"]
    return result


def proposal(c, digest, other=None):
    # Scripted offline Reviewer evaluates a real typed responsibility change:
    # preserve every original claim/contribution/ref, add a trace-only canonical
    # governance reference and explicitly resume AUTHORING. Captured owner-held
    # generations have no current sealed delivery, so REVIEW recovery is invalid.
    # This tests protocol preservation, not Product PASS or a live owner remedy.
    contract = c.manifest.nodes[c.key].contract
    operations = [
        {
            "op": "update-responsibility",
            "node": c.key,
            "acceptance_claims": copy.deepcopy(contract.acceptance_claims),
            "acceptance_contributions": copy.deepcopy(
                contract.acceptance_contributions
            ),
            "acceptance_refs": list(contract.acceptance_refs)
            + ["UJ-AUTHORIZATION-POLICY-001"],
            "clear_legacy_acceptance": True,
            "resume_stage": "authoring",
            "reason": "Offline changed canonical governance trace responsibility assessment",
        }
    ]
    if other:
        operations.append({"op": "resume", "node": other, "stage": "authoring"})
    return {
        "schema": "omac.dag-amendment/v1",
        "reason": "Offline source-preserving assessment",
        "budget_policy": "preserve",
        "owner_resolution": digest,
        "owner_resolution_approval": getattr(c, "approval_sha256", "missing-approval"),
        "operations": operations,
    }


def propose(c, digest, blocked=None, delivery=None):
    mock.MockStore.set_kind_delivery(
        "amendment",
        {"amendment": yaml.safe_dump(delivery or proposal(c, digest), sort_keys=False)},
    )
    mock.MockStore.set_review_verdict("pass")
    return propose_amendment(
        c.engine,
        c.path,
        report_file=c.report,
        docs=c.docs,
        blocked_nodes=[c.key] if blocked is None else blocked,
        orchestrator="alice",
        reviewers=["bob"],
        max_revisions=1,
        owner_request_file=c.request,
        output_file=c.output,
        poll=lambda: None,
    )


def test_captured_prepare_resolve_public_propose_review_accept(case):
    c = case
    original = copy.deepcopy(asdict(c.item))
    original_meta = copy.deepcopy(c.manifest.meta)
    done = {k: asdict(n) for k, n in c.manifest.nodes.items() if n.status == "done"}
    p = prepare(c)
    assert asdict(c.item) == original
    assert load_manifest(c.path).meta == original_meta
    resolve(c, p)
    result = propose(c, p["request_sha256"])
    reviewed = yaml.safe_load(Path(c.output).read_text())
    assert reviewed["budget_policy"] == "preserve"
    assert reviewed["owner_resolution"] == p["request_sha256"]
    assert c.engine.store.get_work_item(result["issue_id"]).review_verdict == "pass"
    assert (
        asdict(c.item) == original
    )  # No original Worker retry/clear/generation change.
    binding = reviewed["base"]["budget_bindings"]["nodes"][c.key]
    accept_amendment(
        c.engine,
        c.path,
        c.output,
        reason="Offline accepted actual fresh assessment",
        agent_pool=c.pool,
    )
    updated = load_manifest(c.path)
    assert {k: asdict(n) for k, n in updated.nodes.items() if k in done} == done
    journal = updated.meta["owner_amendment_resolutions"][p["request_sha256"]]
    from omac.core.owner_amendment import plain

    assert journal["request"]["sources"][c.key]["item"] == plain(original)
    assert (
        journal["request"]["manifest"]["meta"]["amendment_apply"]
        == original_meta["amendment_apply"]
    )
    assert (
        journal["request"]["manifest"]["meta"]["operator_review_recovery"]
        == original_meta["operator_review_recovery"]
    )
    assert updated.nodes[c.key].status == "todo"
    from omac.core.retry_budget import preserved_amendment_budget

    current = preserved_amendment_budget(updated, c.key, c.item)
    assert current["consumed"] == binding["consumed"]
    # Reaccept is restart-safe and never dispatches either old Worker or Planner.
    assignments = list(c.engine.store.assign_log)
    accept_amendment(
        c.engine, c.path, c.output, reason="Offline repeat", agent_pool=c.pool
    )
    assert c.engine.store.assign_log == assignments


@pytest.mark.parametrize("status", ["running", "queued", "future-unknown"])
def test_nonterminal_original_run_has_no_prepare_or_effect(case, status):
    c = case
    run = c.runs[c.item.id][0]
    c.runs[c.item.id][0] = AgentRunObservation(**{**asdict(run), "status": status})
    before = Path(c.path).read_bytes()
    with pytest.raises(ValidationError):
        prepare(c)
    assert Path(c.path).read_bytes() == before
    assert not Path(c.request).exists()
    assert not c.engine.store.assign_log


@pytest.mark.parametrize(
    "drift",
    [
        "decision",
        "handoff",
        "budget",
        "failed-bytes",
        "done",
        "whole-authority",
        "report",
        "docs",
    ],
)
def test_complete_source_drift_fails_before_planner(case, drift):
    c = case
    p = prepare(c)
    resolve(c, p)
    if drift == "decision":
        c.item.decision_required["next_action"] += " drift"
    elif drift == "handoff":
        c.item.worker_handoff = (
            None
            if c.item.worker_handoff
            else parse_worker_handoff({"schema": "invalid"})
        )
    elif drift == "budget":
        c.item.bounces.review += 1
    elif drift == "failed-bytes":
        ref = (
            c.item.worker_handoff.source_review_feedback["report_ref"]["attachment_id"]
            if c.key != "extension-package"
            else "offline-package-report_ref"
        )
        c.bodies[ref] += b"\nchanged"
    elif drift in ("done", "whole-authority"):
        m = load_manifest(c.path)
        if drift == "done":
            next(n for n in m.nodes.values() if n.status == "done").title += " drift"
        else:
            m.meta["amendment_apply"]["tampered"] = True
        save_manifest(m, c.path)
    else:
        Path(c.report if drift == "report" else c.docs[0]).write_text("changed input")
    with pytest.raises(ValidationError):
        propose(c, p["request_sha256"])
    assert not c.engine.store.assign_log


def test_missing_approval_and_empty_selector_cannot_admit(case):
    c = case
    p = prepare(c)
    with pytest.raises(ValidationError):
        propose(c, p["request_sha256"])
    resolve(c, p)
    with pytest.raises(ValidationError):
        propose(c, p["request_sha256"], blocked=[])
    assert not c.engine.store.assign_log


def test_ambiguous_assessment_is_observed_without_duplicate_create_or_wake(
    case, monkeypatch
):
    c = case
    p = prepare(c)
    resolve(c, p)
    original = c.engine.runtime.wake
    effects = []

    def lost_reply(*args, **kwargs):
        if args[2] != "worker":
            return original(*args, **kwargs)
        effects.append(args)
        original(*args, **kwargs)
        raise PlatformError("offline wake reply lost after effect")

    monkeypatch.setattr(c.engine.runtime, "wake", lost_reply)
    with pytest.raises(PlatformError):
        propose(c, p["request_sha256"])
    count = len(mock._shared_work_items)
    # Native completed production is observable. Continue only into the first
    # independent Review; the same Planner create/assignment/wake is not replayed.
    result = propose(c, p["request_sha256"])
    assert result["reviewer_verdict"] == "pass"
    assert len(effects) == 1
    assert len(mock._shared_work_items) == count


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
def test_actual_none_generation_authoring_changed_contract_preserves_consumed_budget(
    case,
):
    c = case
    p = prepare(c)
    resolve(c, p)
    original = copy.deepcopy(asdict(c.item))
    delivery = proposal(c, p["request_sha256"])
    contract = _dump_contract(c.manifest.nodes[c.key].contract)
    contract["objective"] += " [offline changed owner requirement]"
    delivery["operations"] = [
        {"op": "update", "node": c.key, "set": {"contract": contract}}
    ]
    propose(c, p["request_sha256"], delivery=delivery)
    reviewed = yaml.safe_load(Path(c.output).read_text())
    assert reviewed["analysis"]["minimal_rerun"]["authoring"] == [c.key]
    assert original["review_generation"] is None
    assert asdict(c.item) == original
    accept_amendment(
        c.engine,
        c.path,
        c.output,
        reason="Offline new reviewed contract",
        agent_pool=c.pool,
    )
    m = load_manifest(c.path)
    assert m.nodes[c.key].status == "todo"
    assert (
        m.meta["owner_amendment_resolutions"][p["request_sha256"]]["request"][
            "sources"
        ][c.key]["item"]["review_generation"]
        is None
    )
    from omac.core.retry_budget import preserved_amendment_budget

    current = preserved_amendment_budget(m, c.key, c.item)
    assert (
        current["consumed"]
        == reviewed["base"]["budget_bindings"]["nodes"][c.key]["consumed"]
    )


@pytest.mark.parametrize(
    "case",
    [
        "identity-local",
        "credential-store",
        "mcp-catalog-governed-hardening",
        "audit-ledger",
        "extension-package",
    ],
    indirect=True,
)
def test_actual_derived_started_targets_cannot_escape_selector(case):
    c = case
    p = prepare(c)
    resolve(c, p)
    delivery = proposal(c, p["request_sha256"])
    contract = _dump_contract(c.manifest.nodes[c.key].contract)
    contract["objective"] += " [offline structural change]"
    delivery["operations"] = [
        {"op": "update", "node": c.key, "set": {"contract": contract}}
    ]
    from omac.core.amendment import _minimal_rerun

    minimal, derived, _ = _minimal_rerun(c.manifest, delivery)
    assert derived and set(minimal["authoring"]) - {c.key}
    original = copy.deepcopy(asdict(c.item))
    with pytest.raises(ValidationError, match="Actual affected/derived"):
        propose(c, p["request_sha256"], delivery=delivery)
    assert asdict(c.item) == original
    assert not any(entry[2] == "reviewer" for entry in c.engine.store.assign_log)


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize("kind", ["create", "review"])
def test_pending_unknown_checkpoint_cannot_repeat_external_effect(
    case, monkeypatch, kind
):
    c = case
    p = prepare(c)
    resolve(c, p)
    effects = []
    if kind == "create":

        def unknown(*args, **kwargs):
            effects.append(kind)
            raise PlatformError("offline create outcome unknown; no absence inference")

        monkeypatch.setattr(c.engine.store, "create_work_item", unknown)
    else:

        def unknown(*args, **kwargs):
            effects.append(kind)
            raise PlatformError("offline Review dispatch outcome unknown")

        monkeypatch.setattr(c.engine.runtime, "dispatch_reviewer", unknown)
    with pytest.raises(PlatformError):
        propose(c, p["request_sha256"])
    with pytest.raises(NeedsDecision):
        propose(c, p["request_sha256"])
    assert effects == [kind]


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize(
    "drift", ["decision", "failed-bytes", "approval", "done-marker", "review-delivery"]
)
def test_revalidated_full_source_and_approval_before_accept(case, drift):
    c = case
    p = prepare(c)
    resolve(c, p)
    result = propose(c, p["request_sha256"])
    if drift == "decision":
        c.item.decision_required["blocker"]["summary"] += " changed"
    elif drift == "failed-bytes":
        ref = c.item.worker_handoff.source_review_feedback["ledger_ref"][
            "attachment_id"
        ]
        c.bodies[ref] += b"\nchanged"
    elif drift == "review-delivery":
        c.engine.store.get_work_item(
            result["issue_id"]
        ).deliverable += "\nchanged: true"
    else:
        m = load_manifest(c.path)
        if drift == "approval":
            m.meta["owner_amendment_resolutions"][p["request_sha256"]]["approval"] = {}
        else:
            next(
                n for n in m.nodes.values() if n.status == "done"
            ).recovery_marker = True
        save_manifest(m, c.path)
    before = Path(c.path).read_bytes()
    with pytest.raises(ValidationError):
        accept_amendment(
            c.engine, c.path, c.output, reason="Offline accept", agent_pool=c.pool
        )
    assert Path(c.path).read_bytes() == before


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
def test_public_cli_prepare_resolve_propose_accept_uses_exact_authority(
    case, monkeypatch, capsys
):
    from omac.cli.main import main
    import omac.cli.commands.dag as dag

    c = case
    monkeypatch.setattr(dag, "_assemble_engine", lambda args: (c.engine, None))
    monkeypatch.setattr(dag, "commit_manifest", lambda *args, **kwargs: None)
    assert (
        main(
            [
                "dag",
                "amend",
                "prepare-owner",
                c.path,
                "--blocked-node",
                c.key,
                "--allowed-node",
                c.key,
                "--report-file",
                c.report,
                "--docs",
                c.docs[0],
                "--output-file",
                c.request,
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "dag",
                "amend",
                "resolve-owner",
                c.path,
                c.request,
                "--request-sha256",
                prepared["request_sha256"],
                "--authority",
                "offline-coordinator",
                "--reason",
                "Exact assessment request only",
            ]
        )
        == 0
    )
    c.approval_sha256 = json.loads(capsys.readouterr().out)["approval_sha256"]
    mock.MockStore.set_kind_delivery(
        "amendment",
        {"amendment": yaml.safe_dump(proposal(c, prepared["request_sha256"]))},
    )
    mock.MockStore.set_review_verdict("pass")
    assert (
        main(
            [
                "dag",
                "amend",
                "propose",
                c.path,
                "--report-file",
                c.report,
                "--docs",
                c.docs[0],
                "--blocked-node",
                c.key,
                "--owner-request-file",
                c.request,
                "--orchestrator",
                "alice",
                "--reviewer",
                "bob",
                "--max-revisions",
                "1",
                "--output-file",
                c.output,
            ]
        )
        == 20
    )
    capsys.readouterr()
    assert (
        main(
            [
                "dag",
                "amend",
                "accept",
                c.path,
                c.output,
                "--reason",
                "Offline fresh reviewed assessment",
            ]
        )
        == 0
    )


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
def test_source_drift_during_create_never_wakes_planner(case, monkeypatch):
    c = case
    p = prepare(c)
    resolve(c, p)
    original = c.engine.store.create_work_item
    wakes = []

    def drift(*args, **kwargs):
        item = original(*args, **kwargs)
        c.item.decision_required["blocker"]["summary"] += " changed during create"
        return item

    monkeypatch.setattr(c.engine.store, "create_work_item", drift)
    monkeypatch.setattr(
        c.engine.runtime, "wake", lambda *args, **kwargs: wakes.append(args)
    )
    with pytest.raises(ValidationError):
        propose(c, p["request_sha256"])
    assert not wakes
    assert not c.engine.store.assign_log


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize("kind", ["report-mismatch", "ledger-schema"])
def test_full_failed_semantic_corruption_with_coherent_native_hash_is_rejected(
    case, kind
):
    c = case
    feedback = c.item.worker_handoff.source_review_feedback
    key = "report_ref" if kind == "report-mismatch" else "ledger_ref"
    ref = feedback[key]
    body = yaml.safe_load(c.bodies[ref["attachment_id"]])
    if kind == "report-mismatch":
        body["summary"] = "unrelated but still a fully parseable published report"
    else:
        body["schema"] = "not-a-review-ledger"
    content = yaml.safe_dump(body).encode()
    c.bodies[ref["attachment_id"]] = content
    ref["sha256"] = hashlib.sha256(content).hexdigest()
    ref["bytes"] = len(content)
    with pytest.raises(ValidationError, match="ledger|report"):
        prepare(c)
    assert not c.engine.store.assign_log


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
def test_unapproved_new_node_identity_is_not_an_implicit_grant(case):
    c = case
    with pytest.raises(ValidationError, match="existing"):
        prepare(c, allowed=[c.key, "not-yet-approved-new-node"])
    assert not Path(c.request).exists()


@pytest.mark.parametrize("case", ["extension-package"], indirect=True)
@pytest.mark.parametrize(
    "field",
    [
        "source_amendment_issue",
        "source_blocker",
        "source_report_sha256",
        "source_ledger_sha256",
    ],
)
def test_distinct_package_current_original_source_cannot_be_replaced(case, field):
    c = case
    c.item.decision_required[field] = "wrong-source"
    from omac.errors import WorkItemNotFoundError

    with pytest.raises((ValidationError, WorkItemNotFoundError)):
        prepare(c)
    assert not Path(c.request).exists()


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize(
    "reason", ["quality-gate-failed", "completed-without-submit", "budget-exhausted"]
)
def test_generic_decisions_are_not_owner_authorizations(case, reason):
    c = case
    if reason == "quality-gate-failed":
        c.item.decision_required["blocker"]["reason_code"] = reason
    else:
        c.item.decision_required["reason_code"] = reason
    with pytest.raises(ValidationError):
        prepare(c)
    assert not c.engine.store.assign_log


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize("accepted", [False, True])
def test_unknown_accept_restore_is_observed_and_never_reapplied(
    case, monkeypatch, accepted
):
    c = case
    p = prepare(c)
    resolve(c, p)
    propose(c, p["request_sha256"])
    original = c.engine.store.restore_authoring_generation
    calls = []

    def unknown(*args, **kwargs):
        calls.append(args)
        if accepted:
            original(*args, **kwargs)
        raise PlatformError("offline restore reply lost; outcome unknown")

    monkeypatch.setattr(c.engine.store, "restore_authoring_generation", unknown)
    with pytest.raises(PlatformError):
        accept_amendment(
            c.engine, c.path, c.output, reason="Offline accepted", agent_pool=c.pool
        )
    if accepted:
        result = accept_amendment(
            c.engine,
            c.path,
            c.output,
            reason="Offline observe accepted target",
            agent_pool=c.pool,
        )
        assert result["state"] == "applied"
    else:
        with pytest.raises(NeedsDecision, match="unknown"):
            accept_amendment(
                c.engine,
                c.path,
                c.output,
                reason="Offline unknown remains",
                agent_pool=c.pool,
            )
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize("drift", ["required-file", "acceptance-file", "retry-limit"])
def test_full_required_inputs_acceptance_and_limits_drift_are_source_cas(case, drift):
    c = case
    p = prepare(c)
    resolve(c, p)
    if drift == "required-file":
        target = Path("contracts/governance/authz/contract.go")
        target.write_bytes(target.read_bytes() + b"\nchanged")
    elif drift == "acceptance-file":
        target = Path(c.path).parent / c.manifest.meta["acceptance_file"]
        target.write_bytes(target.read_bytes() + b"\nchanged")
    else:
        Path(".omac").mkdir(exist_ok=True)
        Path(".omac/config.yaml").write_text("retry:\n  review: 99\n")
    with pytest.raises(ValidationError):
        propose(c, p["request_sha256"])
    assert not c.engine.store.assign_log


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
def test_unconfirmed_recovery_intent_has_zero_restore(case, monkeypatch):
    import omac.core.amendment as core

    c = case
    p = prepare(c)
    resolve(c, p)
    propose(c, p["request_sha256"])
    original = core._save_ledger

    def silent_write(manifest, path, ledger):
        if any(e.get("state") == "syncing" for e in ledger["nodes"].values()):
            return
        original(manifest, path, ledger)

    calls = []
    monkeypatch.setattr(core, "_save_ledger", silent_write)
    monkeypatch.setattr(
        c.engine.store, "restore_authoring_generation", lambda *a, **k: calls.append(a)
    )
    with pytest.raises(ValidationError, match="not confirmed"):
        accept_amendment(
            c.engine, c.path, c.output, reason="Offline accept", agent_pool=c.pool
        )
    assert calls == []


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
def test_recovery_readback_rejects_done_marker_drift_before_restore(case, monkeypatch):
    import omac.core.amendment as core

    c = case
    p = prepare(c)
    resolve(c, p)
    propose(c, p["request_sha256"])
    original = core._save_ledger

    def drift_after_save(manifest, path, ledger):
        original(manifest, path, ledger)
        if any(e.get("state") == "syncing" for e in ledger["nodes"].values()):
            disk = load_manifest(path)
            done = next(n for n in disk.nodes.values() if n.status == "done")
            done.recovery_marker = {"foreign": "changed during intention write"}
            save_manifest(disk, path)

    calls = []
    monkeypatch.setattr(core, "_save_ledger", drift_after_save)
    monkeypatch.setattr(
        c.engine.store, "restore_authoring_generation", lambda *a, **k: calls.append(a)
    )
    with pytest.raises(ValidationError, match="not confirmed"):
        accept_amendment(
            c.engine, c.path, c.output, reason="Offline accept", agent_pool=c.pool
        )
    assert calls == []


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
def test_pending_recovery_revalidates_full_review_and_related_runs(case, monkeypatch):
    c = case
    p = prepare(c)
    resolve(c, p)
    result = propose(c, p["request_sha256"])

    def unknown(*a, **k):
        raise PlatformError("offline restore unknown")

    monkeypatch.setattr(c.engine.store, "restore_authoring_generation", unknown)
    with pytest.raises(PlatformError):
        accept_amendment(
            c.engine, c.path, c.output, reason="Offline accept", agent_pool=c.pool
        )
    reviewer_item = c.engine.store.get_work_item(result["issue_id"])
    reviewer_item.review_report["summary"] = (
        str(reviewer_item.review_report.get("summary", ""))
        + " changed after definition acceptance"
    )
    with pytest.raises(ValidationError, match="Review|review|report"):
        accept_amendment(
            c.engine,
            c.path,
            c.output,
            reason="Offline retry cannot consume drift",
            agent_pool=c.pool,
        )


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize("status", ["running", "queued", "unknown-native"])
def test_late_run_on_synced_target_blocks_second_pending_target(
    case, monkeypatch, status
):
    c = case
    other = "credential-store"
    p = api()[0](
        c.engine,
        c.path,
        blocked_nodes=[c.key, other],
        allowed_nodes=[c.key, other],
        report_file=c.report,
        docs=c.docs,
        output_file=c.request,
    )
    resolve(c, p)
    delivery = proposal(c, p["request_sha256"])
    secondary = copy.copy(c)
    secondary.key = other
    delivery["operations"].extend(
        proposal(secondary, p["request_sha256"])["operations"]
    )
    propose(c, p["request_sha256"], blocked=[c.key, other], delivery=delivery)
    original = c.engine.store.restore_authoring_generation
    calls = []

    def first_then_late(*args, **kwargs):
        calls.append(args[0])
        result = original(*args, **kwargs)
        if args[0] == c.item.id:
            c.runs[c.item.id].append(
                AgentRunObservation(
                    "late-nonformal", "comment", status, trigger_kind="issue_comment"
                )
            )
        return result

    monkeypatch.setattr(c.engine.store, "restore_authoring_generation", first_then_late)
    with pytest.raises(ValidationError, match="Run"):
        accept_amendment(
            c.engine, c.path, c.output, reason="Offline accepted", agent_pool=c.pool
        )
    assert calls == [c.item.id]


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize(
    "field", ["full_review_completed", "review_goals", "tests_rerun"]
)
def test_coherent_failed_report_semantic_corruption_cannot_prepare(case, field):
    c = case
    feedback = c.item.worker_handoff.source_review_feedback
    report_ref = feedback["report_ref"]
    ledger_ref = feedback["ledger_ref"]
    report = yaml.safe_load(c.bodies[report_ref["attachment_id"]])
    report[field] = [] if field == "review_goals" else False
    from omac.core.review_convergence import _review_report_digest

    ledger = yaml.safe_load(c.bodies[ledger_ref["attachment_id"]])
    ledger["cycles"][-1]["report_digest"] = _review_report_digest(report)
    for ref, body in [(report_ref, report), (ledger_ref, ledger)]:
        raw = yaml.safe_dump(body, allow_unicode=True).encode()
        c.bodies[ref["attachment_id"]] = raw
        ref["sha256"] = hashlib.sha256(raw).hexdigest()
        ref["bytes"] = len(raw)
    with pytest.raises(ValidationError, match="malformed"):
        prepare(c)
    assert not Path(c.request).exists()
    assert not c.engine.store.assign_log


@pytest.mark.parametrize("case", ["api-agent"], indirect=True)
@pytest.mark.parametrize(
    "drift",
    ["worker", "dag-key", "workspace", "uploader-run", "uploader", "foreign-body"],
)
def test_unknown_continuation_requires_exact_same_planner_source(
    case, monkeypatch, drift
):
    c = case
    p = prepare(c)
    resolve(c, p)
    original = c.engine.runtime.wake

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise PlatformError("offline lost Planner wake reply")

    monkeypatch.setattr(c.engine.runtime, "wake", lost)
    with pytest.raises(PlatformError):
        propose(c, p["request_sha256"])
    entry = load_manifest(c.path).meta["owner_amendment_resolutions"][
        p["request_sha256"]
    ]
    item = c.engine.store.find_work_item_by_dag_key(
        c.engine.store.config.workspace_id, entry["dag_key"]
    )
    item = c.engine.store.get_work_item(item.id)
    if drift == "worker":
        item.worker = "bob"
    elif drift == "dag-key":
        item.dag_key = "foreign-key"
        monkeypatch.setattr(
            c.engine.store, "find_work_item_by_dag_key", lambda *a: item
        )
    elif drift == "workspace":
        item.workspace_id = "foreign-workspace"
    elif drift == "uploader-run":
        item.deliverable_ref["task_id"] = "foreign-planner-run"
    elif drift == "uploader":
        item.deliverable_ref["uploader_id"] = c.engine.store.resolve_agent_id("bob")
    else:
        from omac.engines import mock

        raw = (item.deliverable + "\n# foreign native content\n").encode()
        mock._shared_attachment_bodies[item.deliverable_ref["attachment_id"]] = raw
        item.deliverable_ref["sha256"] = hashlib.sha256(raw).hexdigest()
        item.deliverable_ref["bytes"] = len(raw)
    before = list(c.engine.store.assign_log)
    with pytest.raises((ValidationError, NeedsDecision)):
        propose(c, p["request_sha256"])
    assert c.engine.store.assign_log == before

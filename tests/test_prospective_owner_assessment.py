"""Authentic frozen sources at offline Store/Runtime seams; no live effects."""
import copy
import json
import shutil
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from omac.core.manifest import load_manifest
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase, parse_worker_handoff
from omac.engines.models import AgentRunObservation, WorkItem, WorkItemStatus, VerificationAttachmentObservation
from omac.pipeline.owner_amendment import prepare_owner_amendment
from omac.errors import ValidationError

FIXTURES = Path(__file__).parent / "fixtures/prospective_owner_assessment"
OLD = Path(__file__).parent / "fixtures/owner_amendment_admission"


@pytest.fixture
def captured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(OLD / "required-contracts", tmp_path, dirs_exist_ok=True)
    control = tmp_path / ".omac"
    control.mkdir()
    shutil.copyfile(OLD / "open-agent-cluster.acceptance.yaml", control / "open-agent-cluster.acceptance.yaml")
    path = control / "dag.yaml"
    shutil.copyfile(FIXTURES / "manifest.yaml", path)
    manifest = load_manifest(str(path))
    original = json.loads((FIXTURES / "original-request.json").read_text())
    derived = json.loads((FIXTURES / "api-mcp.json").read_text())
    snapshots = {k: {"item": s["item"], "runs": s["runs"]} for k, s in original["sources"].items()}
    snapshots["api-mcp"] = {"item": derived["control"], "runs": derived["runs"]}
    items, runs = {}, {}
    for s in snapshots.values():
        d = s["item"]
        values = {f.name: copy.deepcopy(d[f.name]) for f in fields(WorkItem) if f.name in d}
        values.update(status=WorkItemStatus(d["status"]), kind=TaskKind(d["kind"]), phase=TaskPhase(d["phase"]), bounces=Bounces(**d["bounces"]), worker_handoff=parse_worker_handoff(d.get("worker_handoff")))
        item = WorkItem(**values)
        items[item.id] = item
        runs[item.id] = [AgentRunObservation(**r) for r in s["runs"]]
    native = {v["ref"]["attachment_id"]: v["native"] for s in original["sources"].values() for v in s["failed"].values()}

    def attachment(item_id, ref):
        d = native[ref["attachment_id"]]
        return VerificationAttachmentObservation(**{**d, "content": d["content"].encode()})

    engine = SimpleNamespace(store=SimpleNamespace(config=SimpleNamespace(workspace_id="410ade5e-8ae0-4402-b975-813dea2ff3e1"), get_work_item=lambda key: items[key], find_work_item_by_dag_key=lambda workspace, key: None, observe_verification_attachment=attachment), runtime=SimpleNamespace(list_runs=lambda key: copy.deepcopy(runs[key])))
    report = tmp_path / "report.md"
    report.write_text("Retain all original rejected evidence and unresolved owner/publication obligations.")
    docs = tmp_path / "docs.md"
    docs.write_text("Assess Root's exact prospective declarations; no allocation or product verdict.")
    return SimpleNamespace(engine=engine, manifest=manifest, path=str(path), items=items, runs=runs, report=str(report), docs=[str(docs)], output=str(tmp_path / "request.json"))


def test_public_prepare_separates_unborn_declarations_from_native_sources(captured):
    c = captured
    result = prepare_owner_amendment(c.engine, c.path, blocked_nodes=["api-agent"], allowed_nodes=["api-agent"], report_file=c.report, docs=c.docs, output_file=c.output, prospective_source_file=str(FIXTURES / "witness.json"))
    request = json.loads(Path(c.output).read_text())
    assert result["state"] == "pending_operator_resolution"
    assert set(request["sources"]) == {"api-agent"}
    p = request["prospective_assessment"]
    assert set(p["global_held_sources"]) == {"api-agent", "mcp-catalog-governed-hardening"}
    assert p["declarations"] == {"agent-catalog-production-commands": {"state": "assessment-only", "native_workitem": "not-created", "allocation": "not-authorized", "history": "not-observed", "publication_scope_sha256": p["publication_scope_sha256"]}}
    assert load_manifest(c.path) == c.manifest


@pytest.mark.parametrize("candidate", ["agent-catalog-production-commands", "mcp-platform-transaction-composition", "agentrun-mcp-consumption-evidence"])
def test_application_cannot_turn_assessment_into_allocation(candidate):
    from omac.core.amendment import apply_amendment

    with pytest.raises(ValidationError, match="separate typed allocation/application authority"):
        apply_amendment("not-read.yaml", {"operations": [{"op": "add", "value": {"id": candidate}}]}, object(), set())


@pytest.mark.parametrize("operations", [None, "invalid", {}])
def test_malformed_operations_keep_existing_application_validation(operations):
    from omac.core.amendment import apply_amendment

    with pytest.raises(ValidationError, match="has not passed Reviewer review"):
        apply_amendment("not-read.yaml", {"operations": operations}, object(), set())


def test_direct_handoff_rejects_unallocated_candidate_before_lock_or_store_access():
    from omac.pipeline.loop import _dispatch_worker_handoff

    with pytest.raises(ValidationError, match="not allocated"):
        _dispatch_worker_handoff(object(), object(), object(), "agent-catalog-production-commands")


@pytest.mark.parametrize("candidate", ["agent-catalog-production-commands", "mcp-platform-transaction-composition", "agentrun-mcp-consumption-evidence"])
def test_dispatch_cannot_create_or_assign_an_unallocated_candidate(candidate):
    from omac.pipeline.loop import _dispatch
    from omac.core.manifest import Manifest, Node

    manifest = Manifest(meta={}, nodes={candidate: Node(id=candidate, worker="offline-worker")})
    before = copy.deepcopy(manifest)
    store = SimpleNamespace(config=SimpleNamespace(workspace_id="offline"))
    with pytest.raises(ValidationError, match="not allocated"):
        _dispatch(store, object(), manifest, "not-written.yaml", [candidate], 1)
    assert manifest == before


def witness(tmp_path, group="agent-api"):
    data = json.loads((FIXTURES / "witness.json").read_text())
    data["group"] = group
    for ref in data["references"].values():
        ref["file"] = str(FIXTURES / ref["file"])
    path = tmp_path / "prospective.json"
    path.write_text(json.dumps(data))
    return path, data


def prepare(c, source):
    group = json.loads(Path(source).read_text())["group"]
    blocked = "api-agent" if group == "agent-api" else "mcp-catalog-governed-hardening"
    allowed = [blocked] if group == "agent-api" else ["api-mcp", blocked]
    return prepare_owner_amendment(c.engine, c.path, blocked_nodes=[blocked], allowed_nodes=allowed, report_file=c.report, docs=c.docs, output_file=c.output, prospective_source_file=str(source))


@pytest.mark.parametrize("group", ["agent-api", "mcp"])
def test_real_resolution_and_authorization_bind_all_original_sources(captured, tmp_path, group):
    from omac.pipeline.owner_amendment import resolve_owner_amendment
    from omac.core.owner_amendment import authorized_request

    c = captured
    source, _ = witness(tmp_path, group)
    result = prepare(c, source)
    resolved = resolve_owner_amendment(c.engine, c.path, c.output, request_sha256=result["request_sha256"], authority="offline Root exact group", reason="Assessment only; no allocation authority")
    manifest = load_manifest(c.path)
    entry = authorized_request(manifest, result["request_sha256"], c.engine.store, c.engine.runtime)
    assert resolved["state"] == entry["state"] == "approved"
    assert len(manifest.nodes) == 183
    assert sum(n.status == "done" for n in manifest.nodes.values()) == 80
    assert len(manifest.meta) == 20
    assert all(manifest.meta["owner_amendment_resolutions"][k] == v for k, v in c.manifest.meta["owner_amendment_resolutions"].items())
    assert set(entry["request"]["prospective_assessment"]["global_held_sources"]) == {"api-agent", "mcp-catalog-governed-hardening"}


@pytest.mark.parametrize("document", ["authority.json", "scope.yaml", "scope-native.json", "scope-issue.json", "scope-runs.json", "manifest.yaml", "original-request.json", "api-mcp.json", "api-mcp-contract.yaml", "api-mcp-ledger.yaml", "api-mcp-verification.yaml", "recovery.yaml", "history.json"])
def test_full_qualified_documents_cannot_be_replaced(tmp_path, document):
    from omac.core.prospective_owner import prospective_input

    path, data = witness(tmp_path)
    altered = tmp_path / document
    raw = (FIXTURES / document).read_bytes()
    altered.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    data["references"][document]["file"] = str(altered)
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError, match="source bytes changed"):
        prospective_input(str(path))


@pytest.mark.parametrize("change", ["extra", "group", "schema", "missing", "float_size", "hash", "renamed", "grant"])
def test_declaration_input_cannot_expand_authority(tmp_path, change):
    from omac.core.prospective_owner import prospective_input

    path, data = witness(tmp_path)
    if change == "extra":
        data["other_candidate"] = "extra"
    elif change == "group":
        data["group"] = "combined"
    elif change == "schema":
        data["schema"] += "/unknown"
    elif change == "missing":
        del data["references"]["history.json"]
    elif change == "float_size":
        data["references"]["scope.yaml"]["bytes"] = 11969.0
    elif change == "hash":
        data["references"]["scope.yaml"]["sha256"] = "0" * 64
    elif change == "renamed":
        data["references"]["other_scope"] = data["references"].pop("scope.yaml")
    else:
        data["budget"] = {"worker": 20, "consumed": 0}
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        prospective_input(str(path))


@pytest.mark.parametrize("change", ["done", "history", "receipt", "old_baseline", "held_generation", "held_budget", "held_unknown", "active_run", "native_collision", "node_collision", "derived_budget", "derived_baseline", "derived_unknown"])
def test_actual_source_history_run_and_budget_drift_fails_closed(captured, tmp_path, change):
    from omac.core.manifest import save_manifest
    from omac.core.prospective_owner import CANDIDATES

    c = captured
    source, _ = witness(tmp_path, "mcp")
    manifest = load_manifest(c.path)
    held = c.items[manifest.nodes["api-agent"].work_item_id]
    derived = c.items[manifest.nodes["api-mcp"].work_item_id]
    if change == "done":
        next(n for n in manifest.nodes.values() if n.status == "done").description = "changed"
    elif change == "history":
        next(iter(manifest.meta["owner_amendment_resolutions"].values()))["approval"]["reason"] = "changed"
    elif change == "receipt":
        manifest.meta["operator_review_recovery"] = {}
    elif change == "old_baseline":
        manifest.meta["amendment_apply"]["retained_bounce_baselines"]["api-mcp"]["bounce_baseline"]["review"] = 0
    elif change == "held_generation":
        held.review_generation = "changed"
    elif change == "held_budget":
        held.bounces.review = 0
    elif change == "held_unknown":
        held.unknown_persisted_fields["metadata.foreign_hold"] = "unknown"
    elif change == "active_run":
        c.runs[held.id][0] = replace(c.runs[held.id][0], status="running", trigger_kind="issue_assignment")
    elif change == "native_collision":
        c.engine.store.find_work_item_by_dag_key = lambda workspace, key: held if key in CANDIDATES else None
    elif change == "node_collision":
        from omac.core.manifest import Node
        manifest.nodes[next(iter(CANDIDATES))] = Node(id="collision", worker="offline")
    elif change == "derived_budget":
        derived.bounces.review = 1
    elif change == "derived_baseline":
        derived.bounce_baseline["review"] = 0
    else:
        derived.unknown_persisted_fields["metadata.foreign_hold"] = "unknown"
    save_manifest(manifest, c.path)
    with pytest.raises(ValidationError):
        prepare(c, source)
    assert not Path(c.output).exists()


@pytest.mark.parametrize("allowed", [["mcp-catalog-governed-hardening"], ["mcp-catalog-governed-hardening", "api-mcp", "workspace-sdk"], ["mcp-platform-transaction-composition"]])
def test_public_selectors_cannot_omit_derived_or_query_unsupported_sources(captured, tmp_path, allowed):
    c = captured
    source, _ = witness(tmp_path, "mcp")
    c.engine.store.get_work_item = lambda key: pytest.fail("Invalid selectors must reject before native reads")
    with pytest.raises(ValidationError, match="targets|selectors"):
        prepare_owner_amendment(c.engine, c.path, blocked_nodes=["mcp-catalog-governed-hardening"], allowed_nodes=allowed, report_file=c.report, docs=c.docs, output_file=c.output, prospective_source_file=str(source))
    assert not Path(c.output).exists()


def test_public_cli_uses_explicit_prospective_witness(captured, tmp_path, monkeypatch, capsys):
    from omac.cli.main import main
    import omac.cli.commands.dag as dag

    c = captured
    source, _ = witness(tmp_path)
    monkeypatch.setattr(dag, "_assemble_engine", lambda args: (c.engine, None))
    monkeypatch.setattr(dag, "commit_manifest", lambda *a, **kw: None)
    assert main(["dag", "amend", "prepare-owner", c.path, "--blocked-node", "api-agent", "--allowed-node", "api-agent", "--report-file", c.report, "--docs", c.docs[0], "--output-file", c.output, "--prospective-source-file", str(source)]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert main(["dag", "amend", "resolve-owner", c.path, c.output, "--request-sha256", prepared["request_sha256"], "--authority", "offline Root", "--reason", "assessment-only declarations"]) == 0
    capsys.readouterr()
    assert len(load_manifest(c.path).nodes) == 183


@pytest.mark.parametrize("group", ["agent-api", "mcp"])
def test_real_public_assessment_context_preserves_full_source_before_actor_effect(captured, tmp_path, monkeypatch, group):
    from omac.pipeline.owner_amendment import resolve_owner_amendment
    import omac.pipeline.amendment as pipeline

    c = captured
    source, _ = witness(tmp_path, group)
    prepared = prepare(c, source)
    resolve_owner_amendment(c.engine, c.path, c.output, request_sha256=prepared["request_sha256"], authority="offline Root", reason="assessment only")
    c.engine.store.list_members = lambda workspace: ["offline-planner", "offline-reviewer"]
    observed = {}

    class NoActorEffect(Exception):
        pass

    def stop_before_task(engine, kind, payload, *args, **kwargs):
        observed.update(payload)
        raise NoActorEffect

    monkeypatch.setattr(pipeline, "run_task", stop_before_task)
    blocked = "api-agent" if group == "agent-api" else "mcp-catalog-governed-hardening"
    with pytest.raises(NoActorEffect):
        pipeline.propose_amendment(c.engine, c.path, report_file=c.report, docs=c.docs, blocked_nodes=[blocked], owner_request_file=c.output, orchestrator="offline-planner", reviewers=["offline-reviewer"], max_revisions=1, output_file=str(tmp_path / "reviewed.yaml"))
    q = json.loads(Path(c.output).read_text())["prospective_assessment"]
    assert {ref["file"] for ref in q["references"].values()} <= set(observed["contract"].source_of_truth)
    assert q["input"]["file"] in observed["contract"].source_of_truth
    assert "not-created and not-allocated" in observed["description"]
    assert "not a submitted proposal or Reviewer PASS" in observed["description"]
    assert any("not-created/not-allocated" in text for text in observed["contract"].acceptance)
    assert not Path(tmp_path / "reviewed.yaml").exists()


@pytest.mark.parametrize("group", ["agent-api", "mcp"])
def test_affected_set_uses_actual_derivation_and_exact_declarations(captured, tmp_path, group):
    from omac.pipeline.owner_amendment import resolve_owner_amendment
    from omac.core.owner_amendment import validate_affected
    from omac.core.manifest import _dump_contract

    c = captured
    source, _ = witness(tmp_path, group)
    prepared = prepare(c, source)
    resolved = resolve_owner_amendment(c.engine, c.path, c.output, request_sha256=prepared["request_sha256"], authority="offline Root", reason="assessment only")
    manifest = load_manifest(c.path)
    root = "api-agent" if group == "agent-api" else "mcp-catalog-governed-hardening"
    candidate = "agent-catalog-production-commands" if group == "agent-api" else "mcp-platform-transaction-composition"
    # Offline structural shape only, not a proposed OAC definition or verdict.
    proposal = {"budget_policy": "preserve", "owner_resolution": prepared["request_sha256"], "owner_resolution_approval": resolved["approval_sha256"], "operations": [{"op": "update", "node": root, "set": {"blocked_by": manifest.nodes[root].blocked_by + [candidate]}}, {"op": "add", "value": {"id": candidate, "worker": manifest.nodes[root].worker, "reviewer": manifest.nodes[root].reviewer, "contract": _dump_contract(manifest.nodes[root].contract)}}]}
    entry = validate_affected(manifest, proposal, c.engine.store, c.engine.runtime)
    assert set(entry["request"]["sources"]) == ({"api-agent"} if group == "agent-api" else {"mcp-catalog-governed-hardening", "api-mcp"})
    proposal["operations"][1]["value"]["id"] = "extra-unapproved-candidate"
    with pytest.raises(ValidationError):
        validate_affected(manifest, proposal, c.engine.store, c.engine.runtime)

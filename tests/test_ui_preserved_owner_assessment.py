"""Real frozen UI/Identity/AuthMethod sources at offline Store seams."""
import copy
import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from omac.core.manifest import load_manifest
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase, parse_worker_handoff
from omac.engines.models import (
    AgentRunObservation, WorkItem, WorkItemStatus, VerificationAttachmentObservation,
)
from omac.errors import ValidationError
from omac.pipeline.owner_amendment import prepare_owner_amendment

FIXTURES = Path(__file__).parent / "fixtures/ui_preserved_owner_assessment"
OLD = Path(__file__).parent / "fixtures/owner_amendment_admission"
ALLOWED = ["authentication-methods", "identity-local", "ui-foundation"]


@pytest.fixture
def captured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(OLD / "required-contracts", tmp_path, dirs_exist_ok=True)
    control_dir = tmp_path / ".omac"
    control_dir.mkdir()
    shutil.copyfile(OLD / "open-agent-cluster.acceptance.yaml",
                    control_dir / "open-agent-cluster.acceptance.yaml")
    path = control_dir / "dag.yaml"
    shutil.copyfile(FIXTURES / "manifest.yaml", path)
    manifest = load_manifest(str(path))
    assert len(manifest.nodes) == 183
    assert sum(n.status == "done" for n in manifest.nodes.values()) == 80
    items, runs = {}, {}
    for name in ALLOWED:
        d = json.loads((FIXTURES / (name + ".json")).read_text())
        a = d["item"]
        item = WorkItem(**{
            **a, "status": WorkItemStatus(a["status"]), "kind": TaskKind(a["kind"]),
            "phase": TaskPhase(a["phase"]), "bounces": Bounces(**a["bounces"]),
            "worker_handoff": parse_worker_handoff(a["worker_handoff"]),
        })
        items[item.id] = item
        runs[item.id] = [AgentRunObservation(**r) for r in d["runs"]]
    old = manifest.meta["owner_amendment_resolutions"][
        "d43b1570760168e5769890f5ac57821ebb220801a44714c3706bae333cfe94d1"
    ]["request"]["sources"]["identity-local"]["failed"]
    bodies = {v["ref"]["attachment_id"]: v["native"] for v in old.values()}

    def attachment(item_id, ref):
        assert item_id == items[manifest.nodes["identity-local"].work_item_id].id
        native = bodies[ref["attachment_id"]]
        return VerificationAttachmentObservation(**{
            **native, "content": native["content"].encode(),
        })

    store = SimpleNamespace(
        config=SimpleNamespace(workspace_id="410ade5e-8ae0-4402-b975-813dea2ff3e1"),
        get_work_item=lambda key: items[key],
        observe_verification_attachment=attachment,
    )
    runtime = SimpleNamespace(list_runs=lambda key: copy.deepcopy(runs[key]))
    report = tmp_path / "report.md"
    report.write_text("Preserve all original unresolved failures; assess all actual derived nodes.")
    docs = tmp_path / "assessment-docs.md"
    docs.write_text("Offline assessment source; no technical product verdict.")
    return SimpleNamespace(
        manifest=manifest, path=str(path), engine=SimpleNamespace(store=store, runtime=runtime),
        items=items, runs=runs, report=str(report), docs=[str(docs)],
        output=str(tmp_path / "request.json"), witness=str(FIXTURES / "witness.json"),
    )


def prepare(c, **kwargs):
    return prepare_owner_amendment(
        c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=ALLOWED,
        report_file=c.report, docs=c.docs, output_file=c.output, **kwargs,
    )


def test_real_source_without_witness_remains_closed(captured):
    c = captured
    before = {k: copy.deepcopy(asdict(v)) for k, v in c.items.items()}
    with pytest.raises(ValidationError, match="unknown persisted control fields"):
        prepare(c)
    assert not Path(c.output).exists()
    assert {k: asdict(v) for k, v in c.items.items()} == before


def test_real_public_prepare_preserves_unknown_source_and_history(captured):
    c = captured
    before = {k: copy.deepcopy(asdict(v)) for k, v in c.items.items()}
    result = prepare(c, source_witness_file=c.witness)
    request = json.loads(Path(c.output).read_text())
    assert result["state"] == "pending_operator_resolution"
    assert request["allowed_nodes"] == ALLOWED
    assert request["sources"]["ui-foundation"]["item"]["unknown_persisted_fields"] == (
        before[c.manifest.nodes["ui-foundation"].work_item_id]["unknown_persisted_fields"]
    )
    assert request["sources"]["identity-local"]["failed"]
    assert request["source_qualification"]["disposition"] == "unresolved-product-obligation"
    assert {k: asdict(v) for k, v in c.items.items()} == before
    assert load_manifest(c.path) == c.manifest


def qualification(c):
    from omac.core.owner_amendment import preserved_source_input
    return preserved_source_input(c.witness)[0]


def ui(c):
    return c.items[c.manifest.nodes["ui-foundation"].work_item_id]


@pytest.mark.parametrize("change", [
    "fourth", "number_type", "pr", "reason", "generation", "native_baseline",
    "counter", "workspace", "id", "dag", "assignee", "artifacts", "contract",
    "decision", "status", "phase", "inline_report",
])
def test_qualified_source_drift_is_not_an_allowlist(captured, change):
    from omac.core.owner_amendment import verify_preserved_source
    c = captured
    item = copy.deepcopy(ui(c))
    if change == "fourth":
        item.unknown_persisted_fields["metadata.foreign_hold"] = "active"
    elif change == "number_type":
        item.unknown_persisted_fields["metadata.pr_number"] = 21.0
    elif change == "pr":
        item.unknown_persisted_fields["metadata.pr_url"] += "/changed"
    elif change == "reason":
        item.unknown_persisted_fields["metadata.blocked_reason"] = "fixed"
    elif change == "generation":
        item.review_generation = "changed"
    elif change == "native_baseline":
        item.bounce_baseline["worker"] = 0
    elif change == "counter":
        item.bounces.worker = 0
    elif change == "workspace":
        item.workspace_id = "foreign"
    elif change == "id":
        item.id = "foreign"
    elif change == "dag":
        item.dag_key = "foreign"
    elif change == "assignee":
        item.platform_assignee_id = "foreign"
    elif change == "artifacts":
        item.artifacts["head_sha"] = "0" * 40
    elif change == "contract":
        item.contract["objective"] = "changed"
    elif change == "decision":
        item.decision_required = {"reason_code": "active-hold"}
    elif change == "status":
        item.status = WorkItemStatus.BLOCKED
    elif change == "phase":
        item.phase = TaskPhase.REVIEW
    elif change == "inline_report":
        item.review_report = {"verdict": "pass"}
    with pytest.raises(ValidationError):
        verify_preserved_source(c.manifest, "ui-foundation", item, qualification(c))
    assert not Path(c.output).exists()


@pytest.mark.parametrize("change", ["done", "history", "receipt", "baseline"])
def test_whole_manifest_and_old_authority_are_preserved(captured, change):
    from omac.core.owner_amendment import verify_preserved_source
    c = captured
    changed = copy.deepcopy(c.manifest)
    if change == "done":
        next(n for n in changed.nodes.values() if n.status == "done").title += " changed"
    elif change == "history":
        entry = next(iter(changed.meta["owner_amendment_resolutions"].values()))
        entry["approval"]["reason"] = "rewritten"
    elif change == "receipt":
        changed.meta["operator_review_recovery"] = {}
    else:
        changed.meta["amendment_apply"]["retained_bounce_baselines"]["ui-foundation"]["bounce_baseline"]["worker"] = 0
    with pytest.raises(ValidationError):
        verify_preserved_source(changed, "ui-foundation", ui(c), qualification(c))


@pytest.mark.parametrize("field", [
    "authority.json", "history.json", "pr-run.json", "blocked-run.json",
    "recovery.yaml", "comments.json", "failed-call-correlation.json",
])
def test_full_pinned_provenance_cannot_be_replaced(captured, tmp_path, field):
    from omac.core.owner_amendment import preserved_source_input
    c = captured
    witness = json.loads(Path(c.witness).read_text())
    for ref in witness["references"].values():
        ref["file"] = str(FIXTURES / ref["file"])
    data = bytearray(Path(witness["references"][field]["file"]).read_bytes())
    data[-1] ^= 1
    changed = tmp_path / "changed-source"
    changed.write_bytes(data)
    witness["references"][field]["file"] = str(changed)
    file = tmp_path / "witness.json"
    file.write_text(json.dumps(witness))
    with pytest.raises(ValidationError, match="bytes changed"):
        preserved_source_input(str(file))


def test_captured_public_request_verification_authorization_and_derived_scope(captured):
    from omac.core.owner_amendment import authorized_request, validate_affected, verify_request
    from omac.pipeline.owner_amendment import resolve_owner_amendment
    c = captured
    result = prepare(c, source_witness_file=c.witness)
    request = json.loads(Path(c.output).read_text())
    resolve_owner_amendment(
        c.engine, c.path, c.output, request_sha256=result["request_sha256"],
        authority="offline Root fixture", reason="preservation-only assessment fixture",
    )
    manifest = load_manifest(c.path)
    entry = authorized_request(manifest, result["request_sha256"], c.engine.store, c.engine.runtime)
    proposal = yaml.safe_load((FIXTURES / "proposal.yaml").read_text())
    proposal.update(owner_resolution=result["request_sha256"],
                    owner_resolution_approval=entry["approval_sha256"])
    assert validate_affected(manifest, proposal, c.engine.store, c.engine.runtime) == entry
    wrong = copy.deepcopy(request)
    wrong["allowed_nodes"].remove("ui-foundation")
    del wrong["sources"]["ui-foundation"]
    with pytest.raises(ValidationError, match="complete actual"):
        verify_request(manifest, wrong, c.engine.store, c.engine.runtime)
    proposal["owner_resolution_approval"] = "0" * 64
    with pytest.raises(ValidationError, match="fresh coordinator"):
        validate_affected(manifest, proposal, c.engine.store, c.engine.runtime)
    ui(c).unknown_persisted_fields["metadata.foreign_hold"] = "new"
    with pytest.raises(ValidationError):
        authorized_request(manifest, result["request_sha256"], c.engine.store, c.engine.runtime)


def test_qualified_source_rejects_late_active_run(captured):
    c = captured
    c.runs[ui(c).id].append(AgentRunObservation(
        id="offline-late-run", kind="direct", status="running", agent_id="offline-agent",
        trigger_kind="issue_assignment",
    ))
    with pytest.raises(ValidationError, match="active, queued"):
        prepare(c, source_witness_file=c.witness)
    assert not Path(c.output).exists()


def test_pending_guard_rechecks_qualified_source_without_replay(captured, monkeypatch):
    from omac.core import owner_amendment as owner
    from omac.core.stage_recovery import recovery_control_snapshot
    from omac.pipeline.owner_amendment import resolve_owner_amendment
    c = captured
    result = prepare(c, source_witness_file=c.witness)
    resolve_owner_amendment(c.engine, c.path, c.output,
        request_sha256=result["request_sha256"], authority="offline Root fixture",
        reason="offline preservation-only pending recovery fixture")
    manifest = load_manifest(c.path)
    entry = manifest.meta["owner_amendment_resolutions"][result["request_sha256"]]
    # Model an already independently reviewed checkpoint at the transport seam.
    # No real Planner/Reviewer is created, queried, assigned or given a verdict.
    entry.update(state="reviewed", issue_id="offline-fresh-review",
                 planner_source={"offline": "planner"}, review_source={"offline": "review"},
                 review_dispatches={"offline-subject": {"item_id": "offline-fresh-review", "reviewer": "offline-reviewer"}})
    real_get = c.engine.store.get_work_item
    c.engine.store.get_work_item = lambda key: (
        SimpleNamespace(id=key, review_subject_digest="offline-subject", review_verdict="pass")
        if key == "offline-fresh-review" else real_get(key))
    monkeypatch.setattr(owner, "planner_source", lambda *a: {"offline": "planner"})
    monkeypatch.setattr(owner, "fresh_review_source", lambda *a: {"offline": "review"})
    baseline = recovery_control_snapshot(ui(c))
    manifest.meta["amendment_apply"] = {
        "schema": "omac.amendment-apply/v1", "nodes": {"ui-foundation": {
            "stage": "authoring", "state": "pending", "work_item_id": ui(c).id,
            "baseline": baseline, "expected_contract_sha256": owner.digest(ui(c).contract),
            "expected_review_generation": ui(c).review_generation,
            "bounce_baseline": copy.deepcopy(ui(c).bounce_baseline),
        }},
    }
    amendment = {"owner_resolution": result["request_sha256"],
                 "owner_resolution_approval": entry["approval_sha256"]}
    owner.guard_apply_resume(manifest, amendment, c.engine.store, c.engine.runtime)
    ui(c).unknown_persisted_fields["metadata.pr_number"] = 21.0
    with pytest.raises(ValidationError, match="opaque source"):
        owner.guard_apply_resume(manifest, amendment, c.engine.store, c.engine.runtime)
    ui(c).unknown_persisted_fields["metadata.pr_number"] = 21
    manifest.meta["amendment_apply"]["nodes"]["ui-foundation"]["state"] = "synced"
    ui(c).review_generation = "unowned-generation"
    with pytest.raises(ValidationError, match="target drifted"):
        owner.guard_apply_resume(manifest, amendment, c.engine.store, c.engine.runtime)


@pytest.mark.parametrize("change", [
    "disposition", "missing", "extra", "wrong_hash", "bool_size", "malformed", "nonfile",
])
def test_witness_shape_and_authority_fail_closed(tmp_path, change):
    from omac.core.owner_amendment import preserved_source_input
    witness = json.loads((FIXTURES / "witness.json").read_text())
    for ref in witness["references"].values():
        ref["file"] = str(FIXTURES / ref["file"])
    if change == "disposition":
        witness["disposition"] = "product-pass"
    elif change == "missing":
        del witness["references"]["authority.json"]
    elif change == "extra":
        witness["references"]["generic-hold"] = {}
    elif change == "wrong_hash":
        witness["references"]["history.json"]["sha256"] = "0" * 64
    elif change == "bool_size":
        witness["references"]["authority.json"]["bytes"] = True
    elif change == "nonfile":
        witness["references"]["authority.json"]["file"] = str(tmp_path)
    file = tmp_path / "witness.json"
    file.write_text("not-json" if change == "malformed" else json.dumps(witness))
    with pytest.raises(ValidationError):
        preserved_source_input(str(file))


def test_public_cli_witness_is_explicit_and_resolution_preserves_old_history(captured, monkeypatch, capsys):
    from omac.cli.main import main
    import omac.cli.commands.dag as dag
    c = captured
    monkeypatch.setattr(dag, "_assemble_engine", lambda args: (c.engine, None))
    monkeypatch.setattr(dag, "commit_manifest", lambda *a, **kw: None)
    args = ["dag", "amend", "prepare-owner", c.path, "--blocked-node", "identity-local",
            "--report-file", c.report, "--docs", c.docs[0], "--output-file", c.output,
            "--source-witness-file", c.witness]
    for name in ALLOWED:
        args += ["--allowed-node", name]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    history = copy.deepcopy(c.manifest.meta["owner_amendment_resolutions"])
    assert main(["dag", "amend", "resolve-owner", c.path, c.output,
                 "--request-sha256", result["request_sha256"],
                 "--authority", "offline Root fixture", "--reason", "preservation-only fixture"]) == 0
    capsys.readouterr()
    current = load_manifest(c.path)
    assert {k: current.meta["owner_amendment_resolutions"][k] for k in history} == history
    assert ui(c).unknown_persisted_fields
    assert ui(c).bounce_baseline == {"worker": 8, "review": 7, "merge": 0}


def test_public_assessment_context_contains_full_unresolved_history_without_actor_effect(captured, monkeypatch):
    import omac.pipeline.amendment as pipeline
    from omac.pipeline.owner_amendment import resolve_owner_amendment
    c = captured
    result = prepare(c, source_witness_file=c.witness)
    resolve_owner_amendment(c.engine, c.path, c.output,
        request_sha256=result["request_sha256"], authority="offline Root fixture",
        reason="preservation-only fixture")
    c.engine.store.list_members = lambda workspace: ["offline-planner", "offline-reviewer"]
    c.engine.store.find_work_item_by_dag_key = lambda *args: None
    observed = {}

    class NoActorEffect(Exception):
        pass

    def stop_before_task(engine, kind, payload, *args, **kwargs):
        observed.update(payload)
        raise NoActorEffect

    monkeypatch.setattr(pipeline, "run_task", stop_before_task)
    with pytest.raises(NoActorEffect):
        pipeline.propose_amendment(c.engine, c.path, report_file=c.report, docs=c.docs,
            blocked_nodes=["identity-local"], owner_request_file=c.output,
            orchestrator="offline-planner", reviewers=["offline-reviewer"], max_revisions=1,
            output_file=str(Path(c.output).with_name("reviewed.yaml")))
    q = json.loads(Path(c.output).read_text())["source_qualification"]
    from omac.pipeline.portable_owner import assessment_files
    request = json.loads(Path(c.output).read_bytes())
    files = assessment_files(c.path, c.output, request, c.report, c.docs)
    assert {v["file"] for v in q["references"].values()} <= {str(p) for p in files.values()}
    assert q["input"]["file"] in {str(p) for p in files.values()}
    assert all("omac work read" in source for source in observed["contract"].source_of_truth)
    assert "unresolved product obligations" in observed["description"]
    assert "never authority" in observed["description"]
    assert any("opaque fields" in text for text in observed["contract"].acceptance)


def test_integrated_public_help_exposes_both_exact_assessment_witnesses(capsys):
    from omac.cli.main import main
    with pytest.raises(SystemExit) as stopped:
        main(['dag', 'amend', 'prepare-owner', '--help'])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert '--source-witness-file' in help_text
    assert '--prospective-source-file' in help_text


def test_integrated_mixed_witness_request_stops_before_reads_or_effects(tmp_path, monkeypatch):
    import omac.pipeline.owner_amendment as pipeline
    monkeypatch.setattr(pipeline, 'load_manifest', lambda *args: pytest.fail('Mixed authority reached manifest reads'))
    output = tmp_path / 'must-not-exist.json'
    with pytest.raises(ValidationError, match='separate|cannot.*combine|one.*witness'):
        pipeline.prepare_owner_amendment(
            SimpleNamespace(store=None, runtime=None), str(tmp_path / 'unread-manifest'),
            blocked_nodes=['identity-local'], allowed_nodes=ALLOWED,
            report_file='unread-report', docs=['unread-doc'], output_file=str(output),
            source_witness_file='unread-preserved-witness', prospective_source_file='unread-prospective-witness',
        )
    assert not output.exists()

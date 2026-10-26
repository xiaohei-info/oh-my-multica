import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from _support.amendkit import add_transitional_boundary as _add_transitional_boundary, add_typed_boundary as _add_typed_boundary, contract_update as _contract_update, dag_manifest as _manifest, proposal as _proposal, responsibility_acceptance_doc as _responsibility_acceptance_doc, responsibility_update as _responsibility_update, topology_manifest as _topology_manifest, transitional_boundary_contract_update as _transitional_boundary_contract_update, typed_boundary_contract_update as _typed_boundary_contract_update

import omac.core.amendment as amendment_mod
import omac.pipeline.amendment as amendment_pipeline
from omac.core.amendment import validate_proposal
from omac.core.manifest import load_manifest
from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines.models import EngineConfig
from omac.errors import NeedsDecision, ValidationError


def test_legacy_acceptance_change_with_authoritative_doc_requires_responsibility_operation(
        tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))

    errors = validate_proposal(
        manifest,
        _proposal(_contract_update()),
        {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    assert any("responsibility migration must use update-responsibility" in error
               for error in errors)


def test_definition_update_rejects_noop_contract_replacement(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    current = amendment_mod._dump_contract(manifest.nodes["bootstrap"].contract)
    proposal = _proposal({
        "op": "update", "node": "bootstrap", "set": {"contract": current},
    })

    errors = validate_proposal(
        manifest, proposal, {"alice", "bob", "charlie"})

    assert any("update must change at least one node definition field" in error
               for error in errors)


def test_complete_contract_replacement_requires_boundary_preservation_or_clear(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    manifest = load_manifest(str(path))

    missing = validate_proposal(
        manifest, _proposal(_contract_update()), {"alice", "bob", "charlie"})
    preserved = validate_proposal(
        manifest, _proposal(_typed_boundary_contract_update()),
        {"alice", "bob", "charlie"})

    assert any(
        "must explicitly preserve evidence_mode, produces, and consumes" in error
        for error in missing
    )
    assert preserved == []


def test_complete_replacement_preserves_actual_transitional_boundary_fields(tmp_path):
    path = _manifest(tmp_path)
    _add_transitional_boundary(path)
    manifest = load_manifest(str(path))
    preserved = validate_proposal(
        manifest, _proposal(_transitional_boundary_contract_update()),
        {"alice", "bob", "charlie"})
    missing_produces = _transitional_boundary_contract_update()
    missing_produces["set"]["contract"].pop("produces")
    errors = validate_proposal(
        manifest, _proposal(missing_produces), {"alice", "bob", "charlie"})

    assert preserved == []
    assert any(
        "preserve evidence_mode, and produces" in error
        and "consumes" not in error
        for error in errors
    )


def test_complete_replacement_rejects_explicit_null_consumes(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    operation = _typed_boundary_contract_update()
    operation["set"]["contract"]["consumes"] = None

    errors = validate_proposal(
        load_manifest(str(path)), _proposal(operation),
        {"alice", "bob", "charlie"})

    assert any("contract.consumes must be a list" in error for error in errors)


def test_contract_boundary_clear_expression_is_explicit_and_unambiguous(tmp_path):
    path = _manifest(tmp_path)
    _add_typed_boundary(path)
    manifest = load_manifest(str(path))
    false_clear = _contract_update()
    false_clear["clear_contract_boundary"] = False
    mixed_clear = _typed_boundary_contract_update()
    mixed_clear["clear_contract_boundary"] = True
    resume_clear = {
        "op": "resume",
        "node": "bootstrap",
        "stage": "authoring",
        "clear_contract_boundary": True,
    }

    false_errors = validate_proposal(
        manifest, _proposal(false_clear), {"alice", "bob", "charlie"})
    mixed_errors = validate_proposal(
        manifest, _proposal(mixed_clear), {"alice", "bob", "charlie"})
    resume_errors = validate_proposal(
        manifest, _proposal(resume_clear), {"alice", "bob", "charlie"})

    assert any("must be true when present" in error for error in false_errors)
    assert any("cannot be combined" in error for error in mixed_errors)
    assert any("valid only for update operations" in error for error in resume_errors)


@pytest.mark.parametrize("field, value", [
    ("objective", "smuggled objective"),
    ("scope_paths", ["src/**"]),
    ("commands", ["rm -rf /"]),
    ("worker", "charlie"),
    ("blocked_by", ["closeout"]),
    ("status", "todo"),
])
def test_historical_responsibility_correction_rejects_every_non_whitelisted_field(
    tmp_path, field, value,
):
    manifest = load_manifest(str(_manifest(tmp_path)))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    operation = _responsibility_update(historical=True)
    operation[field] = value

    errors = validate_proposal(
        manifest, _proposal(operation), {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc())

    assert any("unsupported fields" in error for error in errors)


@pytest.mark.parametrize(("refs", "messages"), [
    ([{"bad": "value"}], ["entries must be non-empty strings"]),
    ([["nested"]], ["entries must be non-empty strings"]),
    ([42], ["entries must be non-empty strings"]),
    (["UJ-BOOTSTRAP", {"bad": "value"}, ["nested"]], [
        "entries must be non-empty strings",
    ]),
    (["  "], ["entries must be non-empty strings"]),
    (["UJ-BOOTSTRAP", "UJ-BOOTSTRAP"], ["must not contain duplicates"]),
    (["UJ-BOOTSTRAP", {"bad": "value"}, "UJ-BOOTSTRAP"], [
        "entries must be non-empty strings", "must not contain duplicates",
    ]),
])
def test_historical_correction_rejects_invalid_gate_refs(
    tmp_path, refs, messages,
):
    manifest = load_manifest(str(_manifest(tmp_path)))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    operation = _responsibility_update(historical=True)
    operation["integration_gate_responsibility_patches"][0]["acceptance_refs"] = refs

    errors = validate_proposal(
        manifest, _proposal(operation), {"alice", "bob", "charlie"})

    for message in messages:
        assert any(message in error for error in errors)


def test_historical_responsibility_correction_rejects_noop(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    node = manifest.nodes["bootstrap"]
    node.status = "done"
    node.merged = True
    node.contract.acceptance = []
    node.contract.acceptance_claims = ["UJ-BOOTSTRAP"]
    node.contract.acceptance_contributions = [{
        "flow_id": "UJ-BOOTSTRAP", "action_ids": ["ACT-BOOT-01"],
    }]
    node.contract.acceptance_refs = ["UJ-BOOTSTRAP"]
    node.contract.integration_gates[0]["acceptance_refs"] = ["UJ-BOOTSTRAP"]

    errors = validate_proposal(
        manifest, _proposal(_responsibility_update(historical=True)),
        {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc())

    assert any("must change at least one acceptance responsibility field" in error
               for error in errors)


def test_responsibility_resume_stage_validation_preserves_single_operation_invariant(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    acceptance = _responsibility_acceptance_doc()

    invalid = validate_proposal(
        manifest, _proposal(_responsibility_update(resume_stage="dispatch")),
        {"alice", "bob", "charlie"}, acceptance=acceptance,
    )
    assert any("resume_stage must be review, authoring, or merging" in error
               for error in invalid)

    manifest.nodes["bootstrap"].status = "done"
    manifest.nodes["bootstrap"].merged = True
    historical = validate_proposal(
        manifest, _proposal(_responsibility_update(
            historical=True, resume_stage="merging")),
        {"alice", "bob", "charlie"}, acceptance=acceptance,
    )
    assert any("historical contract correction cannot set resume_stage" in error
               for error in historical)

    duplicate = validate_proposal(
        load_manifest(str(_manifest(tmp_path))),
        _proposal(
            _responsibility_update(resume_stage="merging"),
            {"op": "resume", "node": "bootstrap", "stage": "merging"},
        ),
        {"alice", "bob", "charlie"}, acceptance=acceptance,
    )
    assert any("has multiple operations" in error for error in duplicate)


def test_restart_authoring_rejects_before_any_multica_access(tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("replace the reviewed amendment")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    from omac.engines.multica import MulticaRuntime, MulticaStore
    store = MulticaStore(EngineConfig(
        engine_type="multica", workspace_id="ws", project_id="project-1"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(
        store, "_run_multica",
        lambda *_args, **_kwargs: pytest.fail("unsupported restart must not read/write Multica"),
    )
    monkeypatch.setattr(
        runtime, "wake",
        lambda *_args, **_kwargs: pytest.fail("unsupported restart must not create a Run"),
    )
    engine = SimpleNamespace(store=store, runtime=runtime)

    with pytest.raises(NeedsDecision) as exc:
        amendment_pipeline.propose_amendment(
            engine,
            str(path),
            report_file=str(report),
            docs=[str(docs)],
            blocked_nodes=["bootstrap"],
            orchestrator="alice",
            reviewers=["bob"],
            max_revisions=1,
            resume_issue_id="old-issue-id",
            restart_authoring=True,
        )

    report_payload = exc.value.report
    assert report_payload["reason_code"] == "atomic-restart-unsupported"
    assert "--new-attempt" in report_payload["next_action"]
    assert "--supersedes-issue-id old-issue-id" in report_payload["next_action"]
    assert "--resume-issue-id" not in report_payload["next_action"]


def test_docs_digest_is_recursive_order_independent_and_content_bound(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    docs_a = project_root / "docs-a"
    nested = docs_a / "nested"
    nested.mkdir(parents=True)
    (docs_a / "overview.md").write_text("overview")
    detail = nested / "detail.md"
    detail.write_text("detail v1")
    docs_b = project_root / "single.md"
    docs_b.write_text("single")

    first = amendment_pipeline._docs_snapshot(
        [str(docs_a), str(docs_b)], project_root=project_root)
    reordered = amendment_pipeline._docs_snapshot(
        [str(docs_b), str(docs_a)], project_root=project_root)

    assert reordered == first
    assert any(path.endswith("nested/detail.md") for path in first["docs_files"])

    issue = SimpleNamespace(id="old", identifier="PROJ-811")
    manifest = _manifest(project_root)
    first_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=first,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    reordered_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=reordered,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    assert reordered_attempt["attempt_id"] == first_attempt["attempt_id"]

    detail.write_text("detail v2")
    changed = amendment_pipeline._docs_snapshot(
        [str(docs_a), str(docs_b)], project_root=project_root)
    changed_attempt = amendment_pipeline._attempt_context(
        str(manifest), report="report", docs_snapshot=changed,
        blocked_nodes=["bootstrap"], superseded_issue=issue)
    assert changed["docs_sha256"] != first["docs_sha256"]
    assert changed_attempt["attempt_id"] != first_attempt["attempt_id"]


@pytest.mark.integration  # runs real git
def test_docs_digest_uses_only_git_tracked_files_for_revision_snapshot(tmp_path):
    project_root = tmp_path / "project"
    docs = project_root / "docs"
    docs.mkdir(parents=True)
    (docs / "design.md").write_text("authoritative design")
    (docs / ".DS_Store").write_bytes(b"local desktop metadata")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project_root, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project_root, check=True)
    subprocess.run(
        ["git", "add", "docs/design.md"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "authoritative docs"],
        cwd=project_root, check=True)

    snapshot = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=project_root)

    assert snapshot["docs_files"] == ["docs/design.md"]


@pytest.mark.integration  # runs real git
def test_docs_digest_treats_git_directory_path_as_literal(tmp_path):
    project_root = tmp_path / "project"
    selected = project_root / "docs*"
    leaked = project_root / "docs-other"
    selected.mkdir(parents=True)
    leaked.mkdir()
    (selected / "design.md").write_text("selected")
    (leaked / "leak.md").write_text("must not leak")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "."], cwd=project_root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "commit", "-qm", "authoritative docs"],
        cwd=project_root, check=True)

    snapshot = amendment_pipeline._docs_snapshot(
        [str(selected)], project_root=project_root)

    assert snapshot["docs_files"] == ["docs*/design.md"]


@pytest.mark.integration  # runs real git
def test_docs_digest_reads_revision_blob_instead_of_dirty_worktree(tmp_path):
    project_root = tmp_path / "project"
    docs = project_root / "docs"
    docs.mkdir(parents=True)
    design = docs / "design.md"
    design.write_text("revision v1")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "docs/design.md"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "commit", "-qm", "authoritative docs"],
        cwd=project_root, check=True)
    expected = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=project_root)

    design.write_text("dirty worktree v2")
    actual = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=project_root)

    assert actual == expected


@pytest.mark.parametrize("link_at_root", [False, True])
def test_docs_digest_rejects_symlinks_and_path_escape(tmp_path, link_at_root):
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    docs = tmp_path / "docs"
    docs.mkdir()
    if link_at_root:
        root = tmp_path / "docs-link"
        root.symlink_to(docs, target_is_directory=True)
    else:
        (docs / "escape.md").symlink_to(outside)
        root = docs

    with pytest.raises(ValidationError, match="symlink"):
        amendment_pipeline._docs_snapshot([str(root)], project_root=tmp_path)


def test_docs_digest_rejects_symlinked_parent_component(tmp_path):
    real = tmp_path / "real"
    nested = real / "nested"
    nested.mkdir(parents=True)
    (nested / "design.md").write_text("design")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValidationError, match="symlink"):
        amendment_pipeline._docs_snapshot(
            [str(alias / "nested")], project_root=tmp_path)


def test_docs_digest_rejects_unreadable_file(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    blocked = docs / "blocked.md"
    blocked.write_text("secret")
    original = Path.read_bytes

    def fail_blocked(path):
        if path == blocked:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_blocked)

    with pytest.raises(ValidationError, match="Could not read"):
        amendment_pipeline._docs_snapshot([str(docs)], project_root=tmp_path)


def test_docs_digest_is_stable_across_cwd_and_relative_absolute_inputs(
    tmp_path, monkeypatch,
):
    project_root = tmp_path / "project"
    manifest_dir = project_root / ".omac"
    manifest_dir.mkdir(parents=True)
    manifest = _manifest(manifest_dir)
    docs = project_root / "docs"
    nested = docs / "nested"
    nested.mkdir(parents=True)
    (docs / "overview.md").write_text("overview")
    (nested / "detail.md").write_text("detail")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    expected_root = amendment_pipeline._manifest_project_root(str(manifest))
    assert expected_root == project_root.resolve()

    monkeypatch.chdir(project_root)
    from_root = amendment_pipeline._docs_snapshot(
        ["docs"], project_root=expected_root)
    monkeypatch.chdir(nested)
    from_subdir = amendment_pipeline._docs_snapshot(
        ["docs"], project_root=expected_root)
    monkeypatch.chdir(elsewhere)
    from_elsewhere = amendment_pipeline._docs_snapshot(
        [str(docs)], project_root=expected_root)
    absolute_file = amendment_pipeline._docs_snapshot(
        [str(docs / "overview.md")], project_root=expected_root)
    relative_file = amendment_pipeline._docs_snapshot(
        ["docs/overview.md"], project_root=expected_root)

    assert from_root == from_subdir == from_elsewhere
    assert relative_file == absolute_file
    assert from_root["docs_files"] == [
        "docs/nested/detail.md", "docs/overview.md"]

    superseded = SimpleNamespace(id="old", identifier="PROJ-811")
    attempts = [
        amendment_pipeline._attempt_context(
            str(manifest), report="report", docs_snapshot=snapshot,
            blocked_nodes=["bootstrap"], superseded_issue=superseded)
        for snapshot in (from_root, from_subdir, from_elsewhere)
    ]
    assert {attempt["docs_sha256"] for attempt in attempts} == {
        from_root["docs_sha256"]}
    assert len({attempt["request_digest"] for attempt in attempts}) == 1
    assert len({attempt["attempt_id"] for attempt in attempts}) == 1


def test_docs_digest_rejects_inputs_outside_manifest_project(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside")

    with pytest.raises(ValidationError, match="outside the manifest project"):
        amendment_pipeline._docs_snapshot(
            [str(outside)], project_root=project_root)


@pytest.mark.parametrize(("entry", "message"), [
    ([], "entry must be an object"),
    ({"stage": "authoring", "state": "unknown"}, "unknown apply state"),
])
def test_authoring_preflight_rejects_invalid_ledger_entry(
    entry, message,
):
    manifest = SimpleNamespace(meta={
        "amendment_apply": {
            "schema": amendment_mod.APPLY_LEDGER_SCHEMA,
            "nodes": {"bootstrap": entry},
        },
    })

    with pytest.raises(ValidationError, match=message):
        amendment_mod._preflight_authoring_repair_ledger(manifest)


@pytest.mark.parametrize("attempt_baseline", [
    [],
    {"status": "todo"},
])
def test_authoring_preflight_rejects_unstructured_attempt_baseline(
    attempt_baseline,
):
    manifest = SimpleNamespace(meta={
        "amendment_apply": {
            "schema": amendment_mod.APPLY_LEDGER_SCHEMA,
            "nodes": {
                "bootstrap": {
                    "stage": "authoring",
                    "state": "repairing",
                    "attempt_baseline": attempt_baseline,
                },
            },
        },
    })

    with pytest.raises(ValidationError, match="attempt_baseline"):
        amendment_mod._preflight_authoring_repair_ledger(manifest)


@pytest.mark.parametrize("ledger", [
    [],
    {"schema": amendment_mod.APPLY_LEDGER_SCHEMA, "nodes": []},
    {"schema": "unknown", "nodes": {}},
])
def test_authoring_preflight_rejects_invalid_apply_ledger_shape(ledger):
    manifest = SimpleNamespace(meta={"amendment_apply": ledger})

    with pytest.raises(ValidationError):
        amendment_mod._preflight_authoring_repair_ledger(manifest)


def test_authoring_recovery_includes_synced_entry_with_review_generation():
    manifest = SimpleNamespace(meta={
        "last_amendment_id": "amendment-1",
        "amendment_apply": {
            "nodes": {
                "authoring": {
                    "stage": "authoring",
                    "state": "synced",
                    "expected_review_generation": "generation-1",
                },
            },
        },
    })

    assert amendment_mod.authoring_recovery_node_ids(
        manifest, {"amendment_id": "amendment-1"}) == ["authoring"]


def test_done_or_merged_node_cannot_be_rewritten_or_removed(tmp_path):
    path = _manifest(tmp_path)
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "done"
    manifest.nodes["bootstrap"].merged = True
    manifest.nodes["bootstrap"].merged_at = "2026-07-27T00:00:00Z"

    update_errors = validate_proposal(
        manifest, _proposal(_contract_update()), {"alice", "bob", "charlie"})
    remove_errors = validate_proposal(
        manifest, _proposal({"op": "remove", "node": "bootstrap"}),
        {"alice", "bob", "charlie"})

    assert any("done/merged" in error for error in update_errors)
    assert any("done/merged" in error for error in remove_errors)


def test_amendment_rejects_cycles_and_reports_changed_nodes(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["closeout"]},
    })

    errors = validate_proposal(manifest, proposal, {"alice", "bob", "charlie"})

    assert "manifest DAG has a cycle" in errors


def test_topology_change_fails_closed_for_completed_downstream(tmp_path):
    path = _topology_manifest(tmp_path)
    manifest = load_manifest(str(path))
    manifest.nodes["started-dependent"].status = "done"
    manifest.nodes["started-dependent"].merged = True
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["foundation"]},
    })

    errors = validate_proposal(
        manifest, proposal, {"alice", "bob", "charlie"})

    assert any("done/merged downstream" in error for error in errors)


def test_added_node_malformed_contract_is_validation_error_not_raw_exception(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    proposal = _proposal({
        "op": "add",
        "value": {
            "id": "new-node",
            "worker": "alice",
            "blocked_by": [],
            "contract": {"source_of_truth": None},
        },
    })

    errors = validate_proposal(manifest, proposal, {"alice", "bob", "charlie"})

    assert errors
    assert errors[0].startswith("operations[0]")


def test_ownership_migration_requires_structured_string_reason(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"worker": "charlie"},
        "migration": "ownership transferred",
    })

    errors = validate_proposal(manifest, proposal, {"alice", "bob", "charlie"})

    assert "operations[0].migration must be an object" in errors


def test_added_node_cannot_smuggle_runtime_facts(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    proposal = _proposal({
        "op": "add",
        "value": {
            "id": "new-node",
            "worker": "alice",
            "blocked_by": [],
            "status": "done",
            "work_item_id": "forged",
        },
    })

    errors = validate_proposal(manifest, proposal, {"alice", "bob", "charlie"})

    assert any("runtime fields" in error for error in errors)


def test_cli_restart_authoring_fails_closed_without_multica_access(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "multica",
        "workspace": "ws",
        "project": "project-1",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("replace confirmation")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    from omac.engines.multica import MulticaStore
    monkeypatch.setattr(
        MulticaStore, "_run_multica",
        lambda *_args, **_kwargs: pytest.fail("restart must not access Multica"))

    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--resume-issue-id", "old-issue-id",
        "--restart-authoring",
        "--output", "json",
    ])

    output = capsys.readouterr()
    assert code == exit_codes.NEEDS_DECISION
    structured = json.loads(output.out)
    assert "--new-attempt" in structured["next_action"]
    assert "--supersedes-issue-id old-issue-id" in structured["next_action"]


# ---- validate_affected: the derived affected set must be safe to touch ----


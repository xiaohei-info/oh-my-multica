import copy
from _support.fake_platform import MockCodeHost
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from _support.state import save_literal
from _support.amendkit import add_transitional_boundary as _add_transitional_boundary, add_typed_boundary as _add_typed_boundary, contract_update as _contract_update, dag_manifest as _manifest, old_worker_handoff as _old_worker_handoff, proposal as _proposal, responsibility_acceptance_doc as _responsibility_acceptance_doc, responsibility_update as _responsibility_update, topology_manifest as _topology_manifest, transitional_boundary_contract_update as _transitional_boundary_contract_update, typed_boundary_contract_update as _typed_boundary_contract_update

import omac.cli.commands.dag as dag_cmd
import omac.core.amendment as amendment_mod
import omac.pipeline.amendment as amendment_pipeline
from omac.core.amendment import (
    apply_amendment,
    build_reviewed_amendment,
    manifest_definition_digest,
    manifest_digest,
    validate_proposal,
)
from omac.core.manifest import load_manifest, save_manifest
from omac.core.review_convergence import review_subject_digest
from omac.core.retry_budget import bounce_log_fields, consumed_bounces
from omac.core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity,
    TaskKind, TaskPhase,
)
from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from _support.fake_platform import MockStore
from omac.engines.models import EngineConfig
from omac.core.workitem import AgentRunObservation, WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import loop
from omac.pipeline.delivery import run_merge_delivery
from omac.pipeline.dispatch import build_show_output, submit


def _seal_review_fixture(engine, item_id="1"):
    """Review recovery fixtures model a real prior controller-sealed submission."""
    from _support.sealing import seal_delivery
    from dataclasses import replace
    current = engine.store.get_work_item(item_id)
    old_head = (current.artifacts or {}).get("head_sha")
    sealed = seal_delivery(
        engine.store, item_id,
        (current.artifacts or {}).get("pr_url", "https://example.test/pr/1"),
        current.verification or {"commands": []},
    )
    if old_head:
        engine.store.update_work_item_metadata(
            item_id, artifacts={**sealed.artifacts, "head_sha": old_head},
            delivery_identity=replace(sealed.delivery_identity, pr_head_sha=old_head))


def _engine():
    return create_engine("mock", EngineConfig(
        engine_type="mock",
        workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false"},
    ))


def _pass_with_nits_amendment_fixture(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-pass-with-nits", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    proposal = _proposal(_contract_update())
    deliverable = yaml.safe_dump(proposal, sort_keys=False)
    report = {
        "full_review_completed": True,
        "blockers": [],
        "nits": ["tighten the operator guide", "add one regression example"],
    }
    ledger = {"schema": "omac.review-ledger/v1", "rounds": [], "blockers": []}
    engine.store.update_work_item_metadata(
        issue.id,
        deliverable=deliverable,
        review_report=report,
        review_report_source=yaml.safe_dump(report, sort_keys=False),
        review_ledger=ledger,
        review_ledger_source=yaml.safe_dump(ledger, sort_keys=False),
        review_verdict="pass-with-nits",
        phase=TaskPhase.CONFIRMATION,
    )
    issue = engine.store.get_work_item(issue.id)
    engine.store.update_work_item_metadata(
        issue.id,
        review_subject_digest=review_subject_digest(issue, 1),
    )
    engine.store.update_status(issue.id, WorkItemStatus.IN_REVIEW)
    _seal_review_fixture(engine)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id=issue.id, reviewer_verdict="pass-with-nits",
    )
    amendment_file = tmp_path / "pass-with-nits.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    return path, engine, issue, amendment_file, reviewed


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


def _merge_ready_responsibility_amendment(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": ["pytest -q"]},
        review_verdict="pass",
        review_report={"blockers": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    old_contract = load_manifest(str(path)).nodes["bootstrap"].contract
    engine.store.set_node_contract(item.id, old_contract)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage="merging")),
        engine.store, issue_id="amendment-issue", reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )
    return path, engine, item, reviewed


def _presynced_merging_amendment_with_handoff(tmp_path):
    """Store contract 已提前同步，但仍残留真实旧 handoff 的 pure merging。"""
    path, engine, item, reviewed = _merge_ready_responsibility_amendment(
        tmp_path)
    amended = amendment_mod._apply_definition(
        load_manifest(str(path)), reviewed)
    engine.store.set_node_contract(
        item.id, amended.nodes["bootstrap"].contract)
    current = engine.store.get_work_item(item.id)
    engine.store.prepare_review_cycle(
        item.id,
        review_subject_digest(
            current, max(1, current.bounces.review + 1)),
    )
    engine.store.update_work_item_metadata(
        item.id,
        review_verdict="pass",
        review_report={"blockers": []},
        worker_handoff=_old_worker_handoff(),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    return path, engine, item, reviewed


def _done_node_manifest(tmp_path):
    path = tmp_path / "done.yaml"
    save_literal(path, {
        "meta": {"name": "done-demo"},
        "nodes": [{
            "id": "baseline", "worker": "alice", "reviewer": "bob", "blocked_by": [],
            "work_item_id": "7", "status": "done", "merged": True,
            "merged_at": "2026-01-01T00:00:00Z", "merge_request_state": "merged",
            "contract": {
                "objective": "baseline", "source_of_truth": ["docs/design.md"],
                "acceptance": ["UJ-LEGACY-1"], "non_goals": ["feature work"],
                "verification_commands": ["pytest -q"],
                "integration_gates": [{
                    "name": "baseline-gate", "layer": "L1", "delivery_goal": "baseline holds",
                    "source_of_truth": ["docs/design.md"], "covers": ["baseline"],
                    "acceptance_refs": ["UJ-LEGACY-1"], "commands": ["pytest -q"],
                }],
                "pr_base": "main", "scope_paths": ["src"],
            },
        }],
    })
    return path


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


def test_contract_only_amendment_preserves_runtime_facts_and_resumes_review(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
        review_bounce=4,
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)

    base = load_manifest(str(path))
    _seal_review_fixture(engine)
    reviewed = build_reviewed_amendment(
        base, _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    result = apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    updated = load_manifest(str(path))
    node = updated.nodes["bootstrap"]
    got = engine.store.get_work_item("1")
    assert result["minimal_rerun"] == {"review": ["bootstrap"], "authoring": [], "merging": []}
    assert node.work_item_id == "1"
    assert node.status == "in_review"
    assert node.contract.acceptance == ["bootstrap workspace is valid"]
    assert got.artifacts == {"pr_url": "https://example.test/pr/1", "head_sha": "abc"}
    assert got.verification == {"subject_digest": "verify-1", "commands": []}
    assert got.contract.acceptance == ["bootstrap workspace is valid"]
    assert got.bounces.review == 4
    assert got.status == WorkItemStatus.IN_REVIEW


def _stage_amendment_with_handoff(tmp_path, stage):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="pass",
        worker_bounce=3,
        review_bounce=4,
        merge_bounce=5,
        worker_handoff=_old_worker_handoff(),
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    _seal_review_fixture(engine)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage=stage)),
        engine.store,
        issue_id=f"amendment-{stage}",
        reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )
    return path, engine, item, reviewed


def _apply_exhausted_stage_amendment(tmp_path, stage):
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, stage)
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    return path, engine, item


def _seed_legacy_convergence_review_control(engine, item, snapshot):
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": "review-convergence-ledger-unverifiable",
        "kind": "develop",
        "phase": "review",
        "gate": "review-convergence",
        "rounds": 3,
        "resume_issue_id": item.id,
        "node_id": snapshot["dag_key"],
        "verdict": "reject",
        "recommended_action": "dag-amendment",
        "convergence": {
            "schema": "omac.review-convergence-decision/v1",
            "mode": "unverifiable-legacy-ledger",
            "reason_code": "review-convergence-ledger-unverifiable",
            "cycle_count": 3,
        },
    }
    engine.store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        worker_bounce=15,
        review_bounce=3,
        review_verdict="reject",
        review_report={"blockers": ["legacy convergence audit"]},
        review_report_source="blockers:\n  - legacy convergence audit\n",
        review_subject_digest="subject-round-3",
        review_ledger=snapshot["review_ledger"],
        review_ledger_source=yaml.safe_dump(
            snapshot["review_ledger"], sort_keys=False),
        review_continuation={
            "schema": "omac.review-continuation/v1",
            "authorized_through_round": 6,
        },
        decision_required=decision,
        worker_handoff=snapshot["worker_handoff"],
    )


@pytest.mark.parametrize("checkpoint", ("before_switch", "after_switch"))
def test_legacy_convergence_authoring_generation_apply_is_restart_safe_and_idempotent(
    tmp_path, monkeypatch, legacy_convergence_snapshot, checkpoint,
):
    snapshot = legacy_convergence_snapshot["work_item"]
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, "authoring")
    _seed_legacy_convergence_review_control(engine, item, snapshot)
    original_restore = engine.store.restore_authoring_generation
    crashed = False

    def crash_at_switch(
        item_id, contract, generation, bounce_baseline=None,
    ):
        nonlocal crashed
        if not crashed:
            crashed = True
            if checkpoint == "before_switch":
                raise RuntimeError("crash before authoring generation switch")
            result = original_restore(
                item_id, contract, generation, bounce_baseline)
            raise RuntimeError("crash after authoring generation switch")
        return original_restore(item_id, contract, generation, bounce_baseline)

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", crash_at_switch)
    with pytest.raises(RuntimeError, match="authoring generation switch"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    partial = load_manifest(str(path))
    entry = partial.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing"
    assert entry["expected_review_generation"].startswith("amendment-")

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", original_restore)
    recovered = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    repeated = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    current = engine.store.get_work_item(item.id)
    applied = load_manifest(str(path))
    entry = applied.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert recovered["sync"]["synced"] == ["bootstrap"]
    assert repeated["sync"]["already_complete"] == ["bootstrap"]
    assert current.review_generation == entry["expected_review_generation"]
    assert current.review_ledger == snapshot["review_ledger"]
    assert current.review_ledger_ref is not None
    assert current.current_review_ledger is None
    assert current.decision_required is None
    assert current.review_continuation is None
    assert current.worker_handoff is None
    assert current.bounces.worker == 15
    assert current.bounces.review == 3
    assert entry["observed"]["review_generation"] == (
        entry["expected_review_generation"])
    assert entry["observed"]["review_ledger_current"] is False
    assert entry["observed"]["decision_required_pending"] is False
    assert entry["observed"]["review_report_pending"] is False
    assert entry["observed"]["review_continuation_pending"] is False


def test_runner_restart_after_legacy_convergence_amendment_uses_fresh_worker_budget(
    tmp_path, legacy_convergence_snapshot,
):
    snapshot = legacy_convergence_snapshot["work_item"]
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, "authoring")
    _seed_legacy_convergence_review_control(engine, item, snapshot)
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))

    first = loop.tick(
        engine.store, engine.runtime, engine.code_host, manifest, str(path), max_parallel=1)
    second = loop.tick(
        engine.store, engine.runtime, engine.code_host, manifest, str(path), max_parallel=1)

    current = engine.store.get_work_item(item.id)
    assert first.state == "running"
    assert second.state == "running"
    assert current.bounces.worker == 15
    assert current.decision_required is None
    assert current.current_review_ledger is None
    assert current.worker_handoff is not None


def test_accept_amendment_rejects_review_issue_of_wrong_kind(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    issue = engine.store.create_work_item(
        "ws", "develop", "desc", "develop", "alice", reviewer="bob",
        kind=TaskKind.DEVELOP)
    engine.store.update_work_item_metadata(
        issue.id, phase=TaskPhase.CONFIRMATION, review_verdict="pass")
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id=issue.id, reviewer_verdict="pass")
    amendment_file = tmp_path / "wrong-kind.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))

    with pytest.raises(ValidationError, match="must reference an amendment"):
        amendment_pipeline.accept_amendment(
            engine, str(path), str(amendment_file), reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"})


def test_accept_amendment_pass_with_nits_records_marker_without_apply_and_is_idempotent(
    tmp_path,
):
    path, engine, issue, amendment_file, reviewed = (
        _pass_with_nits_amendment_fixture(tmp_path))
    before_manifest = path.read_bytes()

    result = amendment_pipeline.accept_amendment_nits(
        engine, str(path), str(amendment_file), reason="operator accepted nits")

    assert result["state"] == "nits_accepted"
    assert result["next_action"].startswith("omac dag amend accept ")
    assert path.read_bytes() == before_manifest
    current = engine.store.get_work_item(issue.id)
    assert current.review_verdict == "pass-with-nits"
    assert current.phase == TaskPhase.CONFIRMATION
    assert current.status == WorkItemStatus.IN_REVIEW
    marker = current.review_nits_acceptance
    assert marker["schema"] == "omac.amendment-review-nits-acceptance/v1"
    assert marker["issue_id"] == issue.id
    assert marker["amendment_id"] == reviewed["amendment_id"]

    repeated = amendment_pipeline.accept_amendment_nits(
        engine, str(path), str(amendment_file), reason="same operator decision")
    assert repeated["state"] == "nits_accepted"
    assert engine.store.get_work_item(issue.id).review_nits_acceptance == marker


def test_accept_amendment_pass_with_nits_official_accept_applies_after_marker(
    tmp_path,
):
    path, engine, issue, amendment_file, reviewed = (
        _pass_with_nits_amendment_fixture(tmp_path))
    amendment_pipeline.accept_amendment_nits(
        engine, str(path), str(amendment_file), reason="operator accepted nits")

    result = amendment_pipeline.accept_amendment(
        engine, str(path), str(amendment_file), reason="operator applies amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    assert result["state"] == "applied"
    assert load_manifest(str(path)).meta["last_amendment_id"] == reviewed["amendment_id"]
    assert engine.store.get_work_item(issue.id).status == WorkItemStatus.DONE


def test_accept_amendment_pass_with_nits_rejects_subject_or_report_drift(
    tmp_path,
):
    path, engine, issue, amendment_file, _reviewed = (
        _pass_with_nits_amendment_fixture(tmp_path))
    amendment = yaml.safe_load(amendment_file.read_text())
    amendment["review"]["review_subject_digest"] = "stale-subject"
    amendment_file.write_text(yaml.safe_dump(amendment, sort_keys=False))
    before_manifest = path.read_bytes()

    with pytest.raises(ValidationError, match="subject/report/ledger/deliverable"):
        amendment_pipeline.accept_amendment_nits(
            engine, str(path), str(amendment_file), reason="operator accepted nits")

    assert path.read_bytes() == before_manifest
    assert engine.store.get_work_item(issue.id).review_nits_acceptance in (None, {})


def test_accept_amendment_pass_with_nits_rejects_current_report_ref_drift(
    tmp_path,
):
    path, engine, issue, amendment_file, _reviewed = (
        _pass_with_nits_amendment_fixture(tmp_path))
    engine.store.get_work_item(issue.id).review_report_ref = {
        "attachment_id": "replacement-report",
        "sha256": "b" * 64,
    }
    before_manifest = path.read_bytes()

    with pytest.raises(ValidationError, match="subject/report/ledger/deliverable"):
        amendment_pipeline.accept_amendment_nits(
            engine, str(path), str(amendment_file), reason="operator accepted nits")

    assert path.read_bytes() == before_manifest
    assert engine.store.get_work_item(issue.id).review_nits_acceptance in (None, {})


def test_accept_amendment_pass_with_nits_rejects_active_direct_run_without_write(
    tmp_path, monkeypatch,
):
    path, engine, issue, amendment_file, _reviewed = (
        _pass_with_nits_amendment_fixture(tmp_path))
    active = AgentRunObservation(
        id="amendment-review-run", kind="direct", status="running",
        agent_id="agent-bob", trigger_kind="issue_assignment",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [active] if item_id == issue.id else [],
    )
    before_manifest = path.read_bytes()
    before_amendment = amendment_file.read_bytes()

    with pytest.raises(ValidationError, match="active.*direct Run"):
        amendment_pipeline.accept_amendment_nits(
            engine, str(path), str(amendment_file), reason="operator accepted nits")

    assert path.read_bytes() == before_manifest
    assert amendment_file.read_bytes() == before_amendment
    assert engine.store.get_work_item(issue.id).review_nits_acceptance in (None, {})


def test_accept_authoring_amendment_fails_before_manifest_write_for_active_formal_run(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-active-run", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"}),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
    )
    amendment_file = tmp_path / "active-run.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    before = path.read_bytes()
    active = AgentRunObservation(
        id="formal-run-1",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="issue_assignment",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert path.read_bytes() == before
    assert engine.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


def test_already_applied_authoring_crash_resume_blocks_active_formal_run(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-crash-resume", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"}),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
    )
    amendment_file = tmp_path / "crash-resume.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    original_prepare = amendment_mod.prepare_stage_recovery
    monkeypatch.setattr(
        amendment_mod,
        "prepare_stage_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after manifest write")),
    )

    with pytest.raises(RuntimeError, match="after manifest write"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    interrupted = load_manifest(str(path))
    entry = interrupted.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing"
    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery", original_prepare)
    active = AgentRunObservation(
        id="formal-crash-resume-run",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="issue_assignment",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )
    before = path.read_bytes()

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert path.read_bytes() == before
    assert engine.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


def _legacy_synced_authoring_accept_fixture(
    tmp_path, legacy_convergence_snapshot,
):
    snapshot = legacy_convergence_snapshot["work_item"]
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.set_node_contract(
        target.id, load_manifest(str(path)).nodes["bootstrap"].contract)
    _seed_legacy_convergence_review_control(engine, target, snapshot)
    engine.store.update_work_item_metadata(target.id, worker_bounce=14)
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-proj-850", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal(_responsibility_update(resume_stage="authoring")),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
        acceptance=_responsibility_acceptance_doc(),
    )
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    legacy = load_manifest(str(path))
    entry = legacy.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry.pop("expected_review_generation", None)
    entry.pop("observed", None)
    entry["state"] = "synced"
    save_manifest(legacy, str(path))

    _seed_legacy_convergence_review_control(engine, target, snapshot)
    current = engine.store.get_work_item(target.id)
    current.review_generation = None
    current.review_ledger_generation = None
    current.bounce_baseline = None
    current.phase = TaskPhase.AUTHORING
    current.status = WorkItemStatus.IN_PROGRESS
    current.bounces.worker = 17

    reviewed["human_confirmation"] = "applied"
    reviewed["apply_result"] = {"legacy": True}
    amendment_file = tmp_path / "proj-850.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    engine.store.update_status(amendment_issue.id, WorkItemStatus.DONE)
    return path, amendment_file, engine, target, reviewed


def test_same_legacy_synced_accept_repairs_missing_authoring_projection(
    tmp_path, legacy_convergence_snapshot,
):
    path, amendment_file, engine, target, reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))

    result = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    current = engine.store.get_work_item(target.id)
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert result["sync"]["synced"] == ["bootstrap"]
    assert current.status is WorkItemStatus.TODO
    assert current.phase is TaskPhase.AUTHORING
    assert current.bounces.worker == 17
    assert current.bounce_baseline == {
        "worker": 14, "review": 3, "merge": 0}
    assert consumed_bounces(manifest, "bootstrap", current, "worker") == 3
    assert current.review_generation == entry["expected_review_generation"]
    assert current.current_review_ledger is None
    assert current.decision_required is None
    assert current.review_subject_digest is None
    assert current.review_verdict is None
    assert current.review_report is None
    assert current.worker_handoff is None
    assert build_show_output(current, "worker:alice").get("ok", True) is True
    assert entry["observed"]["contract_sha256"] == (
        entry["expected_contract_sha256"])
    assert entry["observed"]["review_generation"] == (
        entry["expected_review_generation"])
    assert entry["observed"]["review_ledger_current"] is False
    assert yaml.safe_load(amendment_file.read_text())["human_confirmation"] == "applied"
    repeated = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    assert repeated["sync"]["already_complete"] == ["bootstrap"]


def test_synced_authoring_reobserves_stale_projection_with_expected_generation(
    tmp_path, monkeypatch, legacy_convergence_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["expected_review_generation"] = amendment_mod._authoring_review_generation(
        manifest.meta["amendment_apply"]["amendment_id"], "bootstrap")
    save_manifest(manifest, str(path))

    stale = engine.store.get_work_item(target.id)
    assert stale.review_generation is None
    assert stale.current_review_ledger is not None
    assert stale.worker_handoff is not None
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])

    result = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    assert result["sync"]["synced"] == ["bootstrap"]
    assert result["sync"]["already_complete"] == []
    current = engine.store.get_work_item(target.id)
    applied = load_manifest(str(path))
    entry = applied.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert current.review_generation == entry["expected_review_generation"]
    assert current.current_review_ledger is None
    assert current.worker_handoff is None
    assert entry["observed"]["review_generation"] == (
        entry["expected_review_generation"])


def test_authoring_repair_noop_restore_does_not_mark_synced(
    tmp_path, monkeypatch, legacy_convergence_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    before = copy.deepcopy(engine.store.get_work_item(target.id))
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *_args, **_kwargs: before,
    )

    with pytest.raises(
        ValidationError, match="authoring recovery did not reach its target",
    ):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    entry = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "repairing"
    assert entry["observed"]["review_generation"] is None


def test_legacy_authoring_repair_baseline_read_failure_has_no_side_effect(
    tmp_path, monkeypatch, legacy_convergence_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    legacy = load_manifest(str(path))
    legacy.meta["amendment_apply"]["nodes"]["bootstrap"].pop(
        "attempt_baseline", None)
    save_manifest(legacy, str(path))
    legacy_entry = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert legacy_entry["state"] == "synced"
    assert "expected_review_generation" not in legacy_entry
    assert "attempt_baseline" not in legacy_entry
    before_manifest = path.read_bytes()
    before_amendment = amendment_file.read_bytes()
    before_item = copy.deepcopy(engine.store.get_work_item(target.id))
    original_get_work_item = engine.store.get_work_item
    target_reads = 0
    failed = False

    def fail_once_for_target(item_id):
        nonlocal failed, target_reads
        if item_id == target.id:
            target_reads += 1
        if item_id == target.id and target_reads == 1:
            failed = True
            raise RuntimeError("legacy repair baseline read failed")
        return original_get_work_item(item_id)

    monkeypatch.setattr(engine.store, "get_work_item", fail_once_for_target)

    with pytest.raises(RuntimeError, match="legacy repair baseline read failed"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert failed
    assert target_reads == 1
    entry = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert path.read_bytes() == before_manifest
    assert amendment_file.read_bytes() == before_amendment
    assert entry["state"] == "synced"
    assert "attempt_baseline" not in entry
    assert "expected_review_generation" not in entry
    assert engine.store.get_work_item(target.id) == before_item


def test_authoring_preflight_rejects_later_malformed_entry_before_any_write(
    tmp_path, monkeypatch,
):
    path = _topology_manifest(tmp_path)
    engine = _engine()
    first = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(first.id, WorkItemStatus.BLOCKED)
    second = engine.store.create_work_item(
        "ws", "dependent", "desc", "started-dependent", "charlie",
        reviewer="bob")
    engine.store.update_status(second.id, WorkItemStatus.IN_REVIEW)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({
            "op": "update",
            "node": "bootstrap",
            "set": {"blocked_by": ["foundation"]},
        }),
        engine.store,
        issue_id="amendment-preflight",
        reviewer_verdict="pass",
    )
    original_resume = amendment_mod._resume_apply_ledger
    monkeypatch.setattr(
        amendment_mod,
        "_resume_apply_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("stop after ledger creation")),
    )
    with pytest.raises(RuntimeError, match="after ledger creation"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    manifest = load_manifest(str(path))
    entries = manifest.meta["amendment_apply"]["nodes"]
    entries["started-dependent"]["state"] = "repairing"
    entries["started-dependent"].pop("attempt_baseline", None)
    save_manifest(manifest, str(path))
    before_manifest = path.read_bytes()
    before_first = copy.deepcopy(engine.store.get_work_item(first.id))
    before_second = copy.deepcopy(engine.store.get_work_item(second.id))
    monkeypatch.setattr(amendment_mod, "_resume_apply_ledger", original_resume)

    with pytest.raises(
        ValidationError, match="repairing entry is missing attempt_baseline",
    ):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert path.read_bytes() == before_manifest
    assert engine.store.get_work_item(first.id) == before_first
    assert engine.store.get_work_item(second.id) == before_second


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


def test_authoring_unknown_formal_run_blocks_first_accept(
    tmp_path, monkeypatch,
):
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-unknown-run", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"}),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
    )
    amendment_file = tmp_path / "unknown-run.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    unknown = AgentRunObservation(
        id="formal-unknown-run",
        kind="direct",
        status="unknown",
        agent_id="agent-alice",
        trigger_kind="issue_assignment",
    )
    assert unknown.formal and not unknown.active and not unknown.terminal
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [unknown] if item_id == target.id else [],
    )
    before_manifest = path.read_bytes()
    before_amendment = amendment_file.read_bytes()
    before_item = copy.deepcopy(engine.store.get_work_item(target.id))

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert path.read_bytes() == before_manifest
    assert amendment_file.read_bytes() == before_amendment
    assert engine.store.get_work_item(target.id) == before_item


@pytest.mark.parametrize("checkpoint", ("before_switch", "after_switch"))
def test_legacy_synced_authoring_repair_is_restart_safe(
    tmp_path, monkeypatch, legacy_convergence_snapshot, checkpoint,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    original_restore = engine.store.restore_authoring_generation
    crashed = False

    def crash_at_switch(
        item_id, contract, generation, bounce_baseline=None,
    ):
        nonlocal crashed
        if not crashed:
            crashed = True
            if checkpoint == "before_switch":
                raise RuntimeError("crash before legacy repair switch")
            result = original_restore(
                item_id, contract, generation, bounce_baseline)
            raise RuntimeError("crash after legacy repair switch")
        return original_restore(item_id, contract, generation, bounce_baseline)

    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", crash_at_switch)
    with pytest.raises(RuntimeError, match="legacy repair switch"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    interrupted = load_manifest(str(path))
    assert interrupted.meta[
        "amendment_apply"]["nodes"]["bootstrap"]["state"] == "repairing"
    monkeypatch.setattr(
        engine.store, "restore_authoring_generation", original_restore)
    recovered = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    repeated = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    assert recovered["sync"]["synced"] == ["bootstrap"]
    assert repeated["sync"]["already_complete"] == ["bootstrap"]
    current = engine.store.get_work_item(target.id)
    assert current.review_generation is not None
    assert current.current_review_ledger is None


def test_legacy_synced_repair_fails_closed_for_active_formal_run(
    tmp_path, monkeypatch, legacy_convergence_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    before_manifest = path.read_bytes()
    before_item = copy.deepcopy(engine.store.get_work_item(target.id))
    active = AgentRunObservation(
        id="formal-repair-run",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="rerun",
    )
    monkeypatch.setattr(
        engine.runtime, "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    assert path.read_bytes() == before_manifest
    current = engine.store.get_work_item(target.id)
    assert current.review_generation == before_item.review_generation
    assert current.decision_required == before_item.decision_required
    assert current.worker_handoff == before_item.worker_handoff


@pytest.mark.parametrize("progress", ("delivery", "review"))
@pytest.mark.parametrize("repair_state", ("synced", "repairing"))
def test_legacy_synced_repair_does_not_rollback_progressed_work_item(
    tmp_path, monkeypatch, legacy_convergence_snapshot, progress, repair_state,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    if repair_state == "repairing":
        entry["state"] = "repairing"
        entry["expected_review_generation"] = "legacy-repair-generation"
        save_manifest(manifest, str(path))

    current = engine.store.get_work_item(target.id)
    if progress == "delivery":
        current.delivery_identity = DeliveryIdentity(
            schema=DELIVERY_IDENTITY_SCHEMA,
            handoff_generation="progressed-handoff",
            worker="alice",
            agent_id="agent-alice",
            run_id="completed-worker-run",
            pr_url="https://github.com/acme/repo/pull/146",
            pr_head_sha="progressed-head",
            verification_sha256="progressed-verification",
            verification_attachment_id="progressed-attachment",
            verification_comment_id="progressed-comment",
            verification_uploader_id="agent-alice",
            verification_uploader_type="agent",
        )
    else:
        current.phase = TaskPhase.REVIEW
        current.status = WorkItemStatus.IN_REVIEW
        current.review_generation = "progressed-review-generation"
        current.review_ledger_generation = "progressed-review-generation"

    before_item = copy.deepcopy(current)
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("progressed work item must not be restored")),
    )

    result = amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    assert result["sync"]["observed_progress"] == ["bootstrap"]
    assert engine.store.get_work_item(target.id) == before_item
    repaired = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert repaired["state"] == "observed_progress"
    assert repaired["observed"]["delivery_identity_pending"] is (
        progress == "delivery")
    assert repaired["observed"]["phase"] == before_item.phase.value


def test_legacy_synced_repair_first_reconcile_dispatches_one_worker(
    tmp_path, legacy_convergence_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    before = list(engine.runtime.list_runs(target.id))

    loop.tick(engine.store, engine.runtime, engine.code_host, manifest, str(path), max_parallel=1)
    loop.tick(engine.store, engine.runtime, engine.code_host, manifest, str(path), max_parallel=1)

    after = list(engine.runtime.list_runs(target.id))
    new_formal = [run for run in after if run not in before and run.formal]
    assert len(new_formal) == 1
    assert new_formal[0].agent_id == engine.store.resolve_agent_id("alice")


def test_recovery_normalizes_stale_in_progress_authoring_node_to_dispatchable(
    tmp_path, legacy_convergence_snapshot,
):
    """历史 dispatch 残留的 in_progress 节点恢复收敛后必须回到 todo。

    生产现场:节点在 amendment 前已被派发为 in_progress,WorkItem 被恢复
    到 authoring 起点后,若 manifest 节点仍是 in_progress,ready 判定只认
    todo,reconcile 也没有 TODO 回收分支,节点永远无法再次派发。
    """
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "in_progress"
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))

    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    manifest = load_manifest(str(path))
    assert manifest.nodes["bootstrap"].status == "todo"
    item = engine.store.get_work_item(target.id)
    assert item.status == WorkItemStatus.TODO
    before = list(engine.runtime.list_runs(target.id))

    loop.tick(engine.store, engine.runtime, engine.code_host, manifest, str(path), max_parallel=1)

    after = list(engine.runtime.list_runs(target.id))
    new_formal = [run for run in after if run not in before and run.formal]
    assert len(new_formal) == 1


def test_recovery_reaccept_normalizes_stale_node_when_entry_already_synced(
    tmp_path, legacy_convergence_snapshot,
):
    """已 synced 的 authoring entry 在重入 accept 时仍要归一节点状态。"""
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    # 模拟恢复收敛后、再次派发前的崩溃残留:节点回到 in_progress,
    # WorkItem 仍停在 authoring 起点。
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "in_progress"
    save_manifest(manifest, str(path))

    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    assert load_manifest(str(path)).nodes["bootstrap"].status == "todo"


def test_reaccept_upgrades_legacy_baseline_when_work_item_at_recovery_target(
    tmp_path, legacy_convergence_snapshot,
):
    """synced entry 带着 legacy 残缺 baseline 重入时必须升级而非卡死。

    生产现场:修复前的 repair 路径把 entry 收敛为 synced 时保留了 6 字段
    legacy baseline;重入 accept 走 reobserve 分支被严格校验卡死,恢复无法
    重启。修复后:当前观测就是恢复目标时升级 baseline 为结构化快照并继续
    收敛,且不触发冗余 restore。
    """
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    # 模拟修复前遗留:把 baseline 降级为 legacy 6 字段,节点残留 in_progress。
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["baseline"] = {
        "status": "todo",
        "phase": "authoring",
        "review_verdict": None,
        "review_subject_digest": None,
        "contract_sha256": entry["baseline"]["contract_sha256"],
        "worker_handoff_pending": False,
    }
    manifest.nodes["bootstrap"].status = "in_progress"
    save_manifest(manifest, str(path))
    before_item = engine.store.get_work_item(target.id)

    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    repaired = load_manifest(str(path))
    new_entry = repaired.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert new_entry["state"] == "synced"
    assert amendment_mod._RECOVERY_SNAPSHOT_FIELDS.issubset(new_entry["baseline"])
    assert repaired.nodes["bootstrap"].status == "todo"
    assert engine.store.get_work_item(target.id) == before_item


def test_legacy_repair_upgrades_incomplete_baseline_when_syncing(
    tmp_path, legacy_convergence_snapshot,
):
    """repair 路径收敛 synced 时必须把残缺 baseline 升级为结构化快照。"""
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["baseline"] = {
        "status": "blocked",
        "phase": "review",
        "review_verdict": None,
        "review_subject_digest": None,
        "contract_sha256": entry["baseline"]["contract_sha256"],
        "worker_handoff_pending": True,
    }
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))

    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )

    repaired = load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]
    assert repaired["state"] == "synced"
    assert amendment_mod._RECOVERY_SNAPSHOT_FIELDS.issubset(repaired["baseline"])


def test_reaccept_legacy_baseline_fails_closed_when_work_item_drifted(
    tmp_path, legacy_convergence_snapshot,
):
    """legacy baseline + WorkItem 已漂移时必须保持 fail-closed。"""
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    manifest = load_manifest(str(path))
    entry = manifest.meta["amendment_apply"]["nodes"]["bootstrap"]
    entry["baseline"] = {
        "status": "todo",
        "phase": "authoring",
        "review_verdict": None,
        "review_subject_digest": None,
        "contract_sha256": entry["baseline"]["contract_sha256"],
        "worker_handoff_pending": False,
    }
    save_manifest(manifest, str(path))
    engine.store.update_status(target.id, WorkItemStatus.IN_PROGRESS)

    with pytest.raises(amendment_mod.ValidationError):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="repeat official accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )


def test_worker_retry_log_distinguishes_absolute_and_generation_consumption(
    tmp_path, legacy_convergence_snapshot,
):
    path, amendment_file, engine, target, _reviewed = (
        _legacy_synced_authoring_accept_fixture(
            tmp_path, legacy_convergence_snapshot))
    amendment_pipeline.accept_amendment(
        engine,
        str(path),
        str(amendment_file),
        reason="repeat official accepted amendment",
        agent_pool={"alice", "bob", "charlie"},
    )
    manifest = load_manifest(str(path))
    manifest.nodes["closeout"].status = "abandoned"
    save_manifest(manifest, str(path))
    current = engine.store.get_work_item(target.id)
    worker_retry = bounce_log_fields(
        current, "worker", absolute_count=18, limit=5)
    assert worker_retry["absolute_audit_round"] == 18
    assert worker_retry["current_generation_consumed"] == 4
    assert worker_retry["current_generation_limit"] == 5


def test_accept_authoring_amendment_resume_rechecks_active_formal_run(
    tmp_path, monkeypatch,
):
    """Crash resume must not bypass the formal Run boundary after manifest apply."""
    path = _manifest(tmp_path)
    engine = _engine()
    target = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(target.id, WorkItemStatus.BLOCKED)
    amendment_issue = engine.store.create_work_item(
        "ws", "amendment", "desc", "amend-active-resume", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    engine.store.update_work_item_metadata(
        amendment_issue.id,
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
    )
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)),
        _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"}),
        engine.store,
        issue_id=amendment_issue.id,
        reviewer_verdict="pass",
    )
    amendment_file = tmp_path / "active-resume.amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    monkeypatch.setattr(engine.runtime, "list_runs", lambda _item_id: [])
    original_resume = amendment_mod._resume_apply_ledger
    monkeypatch.setattr(
        amendment_mod,
        "_resume_apply_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after manifest apply")),
    )

    with pytest.raises(RuntimeError, match="crash after manifest apply"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="operator accepted",
            agent_pool={"alice", "bob", "charlie"},
        )

    monkeypatch.setattr(amendment_mod, "_resume_apply_ledger", original_resume)
    active = AgentRunObservation(
        id="formal-run-resume",
        kind="direct",
        status="running",
        agent_id="agent-alice",
        trigger_kind="issue_assignment",
    )
    monkeypatch.setattr(
        engine.runtime,
        "list_runs",
        lambda item_id: [active] if item_id == target.id else [],
    )

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        amendment_pipeline.accept_amendment(
            engine,
            str(path),
            str(amendment_file),
            reason="resume accepted amendment",
            agent_pool={"alice", "bob", "charlie"},
        )

    interrupted = load_manifest(str(path))
    assert interrupted.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == (
        "pending"
    )
    assert engine.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


@pytest.mark.parametrize("stage", ("authoring", "review", "merging"))
def test_amendment_recovery_preserves_absolute_bounce_audit_and_records_fresh_budget(
    tmp_path, stage,
):
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, stage)

    got = engine.store.get_work_item(item.id)
    ledger = load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]

    assert got.bounces.worker == 3
    assert got.bounces.review == 4
    assert got.bounces.merge == 5
    assert got.worker_handoff is None
    assert ledger["bounce_baseline"] == {
        "worker": 3,
        "review": 4,
        "merge": 5,
    }


@pytest.mark.parametrize("stage", ("review", "merging"))
@pytest.mark.parametrize("checkpoint", ("before_clear", "after_clear"))
def test_amendment_stage_recovery_retires_handoff_restart_safely(
    tmp_path, monkeypatch, stage, checkpoint,
):
    """apply ledger 在 intent clear 写入前后崩溃都可重入并幂等收口。"""
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, stage)
    original_update = engine.store.update_work_item_metadata
    crashed = False

    def crash_at_clear(item_id, **metadata):
        nonlocal crashed
        if metadata.get("worker_handoff") == {} and not crashed:
            crashed = True
            if checkpoint == "before_clear":
                raise RuntimeError("crash before stage handoff retirement")
            result = original_update(item_id, **metadata)
            raise RuntimeError("crash after stage handoff retirement")
        return original_update(item_id, **metadata)

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", crash_at_clear)
    with pytest.raises(RuntimeError, match="stage handoff retirement"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            acceptance=_responsibility_acceptance_doc(),
        )

    interrupted = load_manifest(str(path))
    entry = interrupted.meta["amendment_apply"]["nodes"]["bootstrap"]
    assert entry["state"] == "syncing"
    assert (engine.store.get_work_item(item.id).worker_handoff is None) is (
        checkpoint == "after_clear")

    monkeypatch.setattr(
        engine.store, "update_work_item_metadata", original_update)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    assert result["sync"]["synced"] == ["bootstrap"]
    recovered = engine.store.get_work_item(item.id)
    assert recovered.worker_handoff is None
    assert load_manifest(str(path)).meta[
        "amendment_apply"]["nodes"]["bootstrap"]["state"] == "synced"

    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )
    assert engine.store.get_work_item(item.id).worker_handoff is None


def test_merging_stage_retires_handoff_before_contract_sync(
    tmp_path, monkeypatch,
):
    """merging 不能先同步 contract 再遗留旧 handoff。"""
    path, engine, item, reviewed = _stage_amendment_with_handoff(
        tmp_path, "merging")
    original_set_contract = engine.store.set_node_contract
    observed = []

    def assert_retired_before_contract(item_id, contract):
        observed.append(engine.store.get_work_item(item_id).worker_handoff)
        return original_set_contract(item_id, contract)

    monkeypatch.setattr(
        engine.store, "set_node_contract", assert_retired_before_contract)
    apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        acceptance=_responsibility_acceptance_doc(),
    )

    assert observed == [None]
    assert engine.store.get_work_item(item.id).worker_handoff is None


def test_review_recovery_uses_fresh_budget_without_erasing_absolute_history(
    tmp_path, monkeypatch,
):
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, "review")
    manifest = load_manifest(str(path))
    engine.store.update_work_item_metadata(item.id, review_verdict="reject")
    monkeypatch.setattr(loop, "validate_review_evidence", lambda *_args: [])

    failures = loop.collect_results(
        engine.store, engine.runtime, engine.code_host, manifest, str(path),
        retry_limits={"worker": 3, "ci": 3, "review": 3, "merge": 3},
    )

    got = engine.store.get_work_item(item.id)
    assert failures == {}
    assert manifest.nodes["bootstrap"].status == "in_progress"
    assert got.bounces.review == 5


def test_authoring_recovery_uses_fresh_worker_budget_without_erasing_history(tmp_path):
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, "authoring")
    manifest = load_manifest(str(path))
    manifest.nodes["bootstrap"].status = "in_progress"
    engine.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    engine.store.get_work_item(item.id).agent_run_finished_without_submit = True

    failures = loop.collect_results(
        engine.store, engine.runtime, engine.code_host, manifest, str(path),
        retry_limits={"worker": 3, "ci": 3, "review": 3, "merge": 3},
    )

    got = engine.store.get_work_item(item.id)
    assert "bootstrap" in failures
    assert manifest.nodes["bootstrap"].status == "blocked"
    assert got.bounces.worker == 3
    assert got.decision_required["reason_code"] == "worker-retry-intent-required"


def test_merging_recovery_uses_fresh_merge_budget_without_erasing_history(tmp_path):
    code_host = MockCodeHost()
    path, engine, item = _apply_exhausted_stage_amendment(tmp_path, "merging")
    manifest = load_manifest(str(path))
    code_host.observe_pull_request = lambda _url: type("Observation", (), {
        "state": "closed_unmerged",
        "merged_at": None,
        "detail": "closed without merge",
    })()

    result = run_merge_delivery(
        {}, manifest, "bootstrap", engine.store, engine.runtime,
        code_host, {"worker": 3, "ci": 3, "review": 3, "merge": 3}, str(path),
    )

    got = engine.store.get_work_item(item.id)
    assert result == "bounce"
    assert manifest.nodes["bootstrap"].status == "in_progress"
    assert got.bounces.merge == 6


def test_repeated_accept_after_node_progress_never_rolls_it_back(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    _seal_review_fixture(engine)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    first = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    assert first["sync"]["synced"] == ["bootstrap"]

    engine.store.update_work_item_metadata(
        item.id,
        review_verdict="pass",
        review_report={"blockers": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.DONE)
    progressed = load_manifest(str(path))
    progressed.nodes["bootstrap"].status = "done"
    progressed.nodes["bootstrap"].merged = True
    progressed.nodes["bootstrap"].merged_at = "2026-07-27T01:00:00Z"
    save_manifest(progressed, str(path))

    second = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    got = engine.store.get_work_item(item.id)
    reloaded = load_manifest(str(path))
    assert second["sync"]["already_complete"] == ["bootstrap"]
    assert got.status == WorkItemStatus.DONE
    assert got.review_verdict == "pass"
    assert reloaded.nodes["bootstrap"].status == "done"
    assert reloaded.nodes["bootstrap"].merged is True
    assert reloaded.meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "synced"


def test_apply_ledger_completes_after_contract_write_but_before_review_reset(tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        verification={"subject_digest": "verify-1"},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    _seal_review_fixture(engine)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    original_reset = engine.store.reset_review
    failed = False

    def fail_reset(item_id):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("simulated reset interruption")
        return original_reset(item_id)

    monkeypatch.setattr(engine.store, "reset_review", fail_reset)
    with pytest.raises(RuntimeError, match="reset interruption"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    partial = engine.store.get_work_item(item.id)
    assert partial.contract.acceptance == ["bootstrap workspace is valid"]
    assert partial.review_verdict == "reject"

    monkeypatch.setattr(engine.store, "reset_review", original_reset)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    completed = engine.store.get_work_item(item.id)
    assert result["sync"]["synced"] == ["bootstrap"]
    assert completed.status == WorkItemStatus.IN_REVIEW
    assert completed.review_verdict is None


def test_merge_only_recovery_keeps_pass_verdict_and_enters_merging(tmp_path):
    code_host = MockCodeHost()
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        review_verdict="pass",
        review_report={"blockers": []},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    proposal = _proposal({"op": "resume", "node": "bootstrap", "stage": "merging"})
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    code_host.observe_pull_request = lambda *_args, **_kwargs: pytest.fail(
        "amendment accept must delegate PR observation to dag run")
    code_host.request_pull_request_merge = lambda *_args, **_kwargs: pytest.fail(
        "amendment accept must delegate merge requests to dag run")
    engine.store.set_node_contract = lambda *_args, **_kwargs: pytest.fail(
        "pure merge resume must not republish the unchanged contract")

    result = apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert result["minimal_rerun"]["merging"] == ["bootstrap"]
    assert load_manifest(str(path)).nodes["bootstrap"].status == "merging"
    assert engine.store.get_work_item("1").review_verdict == "pass"


def test_merge_only_precondition_failure_does_not_modify_manifest(tmp_path):
    path = _manifest(tmp_path)
    original = path.read_bytes()
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        review_verdict="reject",
        review_report={"blockers": ["not ready"]},
        worker_handoff=_old_worker_handoff(),
    )
    proposal = _proposal({"op": "resume", "node": "bootstrap", "stage": "merging"})
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    with pytest.raises(ValidationError, match="passed review and PR"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert path.read_bytes() == original
    assert engine.store.get_work_item(item.id).worker_handoff is not None


def test_implementation_scope_change_requires_authoring_and_explicit_migration(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item("ws", "bootstrap", "desc", "bootstrap", "alice")
    operation = _contract_update()
    operation["set"]["contract"]["scope_paths"] = ["src/**"]

    errors = validate_proposal(
        load_manifest(str(path)), _proposal(operation), {"alice", "bob", "charlie"})
    assert any("ownership migration" in error for error in errors)

    operation["migration"] = {
        "ownership_transfer": True,
        "reason": "bootstrap now owns the source skeleton",
    }
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(operation), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    result = apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert result["minimal_rerun"]["authoring"] == ["bootstrap"]
    manifest = load_manifest(str(path))
    assert manifest.nodes["bootstrap"].status == "todo"
    assert engine.store.get_work_item("1").worker_handoff is None

    dispatched = loop.tick(
        engine.store, engine.runtime, engine.code_host, manifest, str(path), max_parallel=1)

    handoff = engine.store.get_work_item("1").worker_handoff
    assert dispatched.dispatched == ["bootstrap"]
    assert handoff is not None
    assert handoff.gate == "explicit-dispatch"
    assert handoff.target_worker_bounce == 0


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


def test_runtime_drift_is_rebased_but_definition_drift_fails_cas(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item("ws", "bootstrap", "desc", "bootstrap", "alice")
    base = load_manifest(str(path))
    _seal_review_fixture(engine)
    reviewed = build_reviewed_amendment(
        base, _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    assert reviewed["base"]["manifest_sha256"] == manifest_digest(base)
    assert reviewed["base"]["definition_sha256"] == manifest_definition_digest(base)

    drifted = load_manifest(str(path))
    list(drifted.nodes.values())[1].status = "blocked"
    save_manifest(drifted, str(path))
    apply_amendment(str(path), copy.deepcopy(reviewed), engine.store, {"alice", "bob", "charlie"})

    path = _manifest(tmp_path)
    drifted = load_manifest(str(path))
    list(drifted.nodes.values())[1].blocked_by = []
    save_manifest(drifted, str(path))
    with pytest.raises(ValidationError, match="definition changed"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})


def test_amendment_rejects_cycles_and_reports_changed_nodes(tmp_path):
    manifest = load_manifest(str(_manifest(tmp_path)))
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["closeout"]},
    })

    errors = validate_proposal(manifest, proposal, {"alice", "bob", "charlie"})

    assert "manifest DAG has a cycle" in errors


def test_topology_change_recovers_started_downstream_but_not_unstarted_closure(tmp_path):
    path = _topology_manifest(tmp_path)
    engine = _engine()
    first = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(first.id, WorkItemStatus.BLOCKED)
    second = engine.store.create_work_item(
        "ws", "dependent", "desc", "started-dependent", "charlie", reviewer="bob")
    engine.store.update_status(second.id, WorkItemStatus.IN_REVIEW)
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["foundation"]},
    })

    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    assert reviewed["analysis"]["derived_started_downstream"] == ["started-dependent"]
    assert reviewed["analysis"]["minimal_rerun"] == {
        "review": [],
        "authoring": ["bootstrap", "started-dependent"],
        "merging": [],
    }
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})
    updated = load_manifest(str(path))
    assert result["minimal_rerun"]["authoring"] == [
        "bootstrap", "started-dependent"]
    assert updated.nodes["bootstrap"].status == "todo"
    assert updated.nodes["started-dependent"].status == "todo"
    assert updated.nodes["future-dependent"].status == "todo"
    assert "future-dependent" not in result["minimal_rerun"]["authoring"]


def test_apply_ledger_retries_only_unfinished_node_side_effects(tmp_path, monkeypatch):
    path = _topology_manifest(tmp_path)
    engine = _engine()
    first = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_status(first.id, WorkItemStatus.BLOCKED)
    second = engine.store.create_work_item(
        "ws", "dependent", "desc", "started-dependent", "charlie", reviewer="bob")
    engine.store.update_status(second.id, WorkItemStatus.IN_REVIEW)
    proposal = _proposal({
        "op": "update",
        "node": "bootstrap",
        "set": {"blocked_by": ["foundation"]},
    })
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    original_sync = amendment_mod.prepare_stage_recovery
    failed = False

    def fail_second(
        node, store, stage, *, expected_review_subject=None,
        expected_review_generation=None, expected_bounce_baseline=None,
        sync_contract=False,
    ):
        nonlocal failed
        if node.id == "started-dependent" and not failed:
            failed = True
            raise RuntimeError("simulated Store interruption")
        return original_sync(
            node, store, stage,
            expected_review_subject=expected_review_subject,
            expected_review_generation=expected_review_generation,
            expected_bounce_baseline=expected_bounce_baseline,
            sync_contract=sync_contract)

    monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", fail_second)
    with pytest.raises(RuntimeError, match="Store interruption"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    interrupted = load_manifest(str(path))
    ledger = interrupted.meta["amendment_apply"]["nodes"]
    assert ledger["bootstrap"]["state"] == "synced"
    assert ledger["started-dependent"]["state"] == "syncing"

    engine.store.update_status(first.id, WorkItemStatus.DONE)
    interrupted.nodes["bootstrap"].status = "done"
    save_manifest(interrupted, str(path))
    monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", original_sync)

    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert engine.store.get_work_item(first.id).status == WorkItemStatus.DONE
    assert engine.store.get_work_item(second.id).status == WorkItemStatus.TODO
    assert result["sync"]["observed_progress"] == ["bootstrap"]
    assert result["sync"]["synced"] == ["started-dependent"]
    completed = load_manifest(str(path)).meta["amendment_apply"]["nodes"]
    assert completed["bootstrap"]["state"] == "observed_progress"
    assert completed["started-dependent"]["state"] == "synced"


def test_pending_apply_blocks_all_dag_progress_until_same_accept_resumes(
    tmp_path, monkeypatch, capsys,
):
    path = _manifest(tmp_path)
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(item.id, WorkItemStatus.BLOCKED)
    _seal_review_fixture(engine)
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    amendment_file = tmp_path / "reviewed-amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))
    original_prepare = amendment_mod.prepare_stage_recovery

    def crash_before_store(*_args, **_kwargs):
        raise RuntimeError("crash after manifest before Store compensation")

    monkeypatch.setattr(
        amendment_mod, "prepare_stage_recovery", crash_before_store)
    with pytest.raises(RuntimeError, match="before Store compensation"):
        apply_amendment(
            str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
            amendment_file=str(amendment_file),
        )

    partial = load_manifest(str(path))
    ledger = partial.meta["amendment_apply"]
    assert ledger["amendment_id"] == reviewed["amendment_id"]
    assert ledger["amendment_file"] == str(amendment_file)
    assert ledger["nodes"]["bootstrap"]["state"] == "syncing"

    original_get = engine.store.get_work_item
    monkeypatch.setattr(
        engine.store, "get_work_item",
        lambda *_args, **_kwargs: pytest.fail(
            "pending amendment gate must run before reading Store"),
    )
    for advance in (
        lambda: loop.reconcile(engine.store, engine.code_host, partial, str(path)),
        lambda: loop.tick(engine.store, engine.runtime, engine.code_host, partial, str(path)),
    ):
        with pytest.raises(NeedsDecision) as exc_info:
            advance()
        report = exc_info.value.report
        assert report["reason"] == "amendment_apply_incomplete"
        assert report["amendment_id"] == reviewed["amendment_id"]
        assert report["incomplete_nodes"] == [{
            "node_id": "bootstrap", "stage": "review", "state": "syncing",
        }]
        assert report["resume_command"] == (
            f"omac dag amend accept {path} {amendment_file}")

    monkeypatch.setattr(
        dag_cmd, "_assemble_engine",
        lambda _args: pytest.fail(
            "CLI run must block before engine assembly or dispatch"),
    )
    assert main(["dag", "run", str(path), "--output", "json"]) == exit_codes.NEEDS_DECISION
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["amendment_id"] == reviewed["amendment_id"]
    assert cli_report["resume_command"].endswith(str(amendment_file))

    monkeypatch.setattr(engine.store, "get_work_item", original_get)
    monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", original_prepare)
    result = apply_amendment(
        str(path), reviewed, engine.store, {"alice", "bob", "charlie"},
        amendment_file=str(amendment_file),
    )

    assert result["sync"]["synced"] == ["bootstrap"]
    completed = load_manifest(str(path))
    assert all(
        entry["state"] in {"synced", "observed_progress"}
        for entry in completed.meta["amendment_apply"]["nodes"].values()
    )
    loop.reconcile(engine.store, engine.code_host, completed, str(path))


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


def test_work_submit_accepts_structured_amendment_delivery(tmp_path):
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "amend", "desc", "amend-demo", "alice",
        reviewer="bob", kind=TaskKind.AMENDMENT)
    amendment_file = tmp_path / "amendment.yaml"
    amendment_file.write_text(yaml.safe_dump(_proposal(_contract_update())))

    result = submit(
        engine.store, item.id, code_host=engine.code_host, amendment_file=str(amendment_file),
        agent_pool={"alice", "bob", "charlie"})

    got = engine.store.get_work_item(item.id)
    assert result.deliverable_key == "amendment"
    assert got.deliverable
    assert got.phase.value == "review"
    assert got.status == WorkItemStatus.IN_REVIEW


def test_tampered_minimal_rerun_is_rejected(tmp_path):
    path = _manifest(tmp_path)
    engine = _engine()
    engine.store.create_work_item("ws", "bootstrap", "desc", "bootstrap", "alice")
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    reviewed["analysis"]["minimal_rerun"] = {
        "review": [], "authoring": [], "merging": ["bootstrap"],
    }

    with pytest.raises(ValidationError, match="identity does not match"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})


def test_cli_amendment_stops_after_reviewer_then_human_accepts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {
            "workers": ["alice", "charlie"],
            "orchestrator": "alice",
            "reviewers": ["bob"],
        },
        "retry": {"review": 2, "ci": 2, "merge": 2, "worker": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("bootstrap wrongly claims the whole user journey")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("bootstrap contributes only a local gate")

    engine = _engine()
    bootstrap = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    engine.store.update_work_item_metadata(
        bootstrap.id,
        artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
        verification={"subject_digest": "verify-1", "commands": []},
        review_verdict="reject",
        review_report={"blockers": ["bad mapping"]},
    )
    engine.store.update_status(bootstrap.id, WorkItemStatus.BLOCKED)
    _seal_review_fixture(engine, bootstrap.id)
    MockStore.set_kind_delivery("amendment", {
        "amendment": yaml.safe_dump(_proposal(_contract_update()), sort_keys=False),
    })
    MockStore.set_review_verdict("pass")

    amendment_path = omac_dir / "dag.amendment.yaml"
    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--blocked-node", "bootstrap",
        "--output-file", str(amendment_path),
    ])

    assert code == exit_codes.NEEDS_DECISION
    reviewed = yaml.safe_load(amendment_path.read_text())
    amendment_issue = engine.store.get_work_item(reviewed["review"]["issue_id"])
    assert amendment_issue.phase.value == "confirmation"
    assert amendment_issue.review_verdict == "pass"
    assert load_manifest(str(manifest_path)).nodes["bootstrap"].status == "blocked"

    code = main([
        "dag", "amend", "accept", str(manifest_path), str(amendment_path),
        "--reason", "reviewed by operator",
    ])

    assert code == exit_codes.OK
    updated = load_manifest(str(manifest_path))
    assert updated.nodes["bootstrap"].status == "in_review"
    assert updated.nodes["bootstrap"].work_item_id == "1"
    assert engine.store.get_work_item(reviewed["review"]["issue_id"]).status == WorkItemStatus.DONE
    assert yaml.safe_load(amendment_path.read_text())["human_confirmation"] == "applied"


def test_cli_amendment_resume_failed_authoring_reuses_issue(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {
            "workers": ["alice", "charlie"],
            "orchestrator": "alice",
            "reviewers": ["bob"],
        },
        "retry": {"review": 2, "ci": 2, "merge": 2, "worker": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("resume failed amendment authoring")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")

    resume_issue_id = "e66f44e4-bd0f-4a9c-8bfa-2f60a7641d84"
    observed = {}

    def fake_propose(*_args, **kwargs):
        observed["resume_issue_id"] = kwargs["resume_issue_id"]
        return {
            "state": "pending_human_confirmation",
            "manifest": str(manifest_path),
            "amendment_file": str(omac_dir / "dag.amendment.yaml"),
            "amendment_id": "amendment-1",
            "issue_id": resume_issue_id,
            "reviewer_verdict": "pass",
        }

    monkeypatch.setattr(amendment_pipeline, "propose_amendment", fake_propose)

    amendment_path = omac_dir / "dag.amendment.yaml"
    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--blocked-node", "bootstrap",
        "--resume-issue-id", resume_issue_id,
        "--output-file", str(amendment_path),
    ])

    assert code == exit_codes.NEEDS_DECISION
    assert observed["resume_issue_id"] == resume_issue_id


def test_cli_amendment_forwards_restart_authoring(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("replace confirmation")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    observed = {}

    def fake_propose(*_args, **kwargs):
        observed.update(kwargs)
        return {
            "state": "pending_human_confirmation",
            "manifest": str(manifest_path),
            "amendment_file": str(omac_dir / "dag.amendment.yaml"),
            "amendment_id": "amendment-2",
            "issue_id": "issue-1",
            "reviewer_verdict": "pass",
        }

    monkeypatch.setattr(amendment_pipeline, "propose_amendment", fake_propose)

    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--resume-issue-id", "issue-1",
        "--restart-authoring",
    ])

    assert code == exit_codes.NEEDS_DECISION
    assert observed["resume_issue_id"] == "issue-1"
    assert observed["restart_authoring"] is True


@pytest.mark.integration  # starts a Python subprocess / runs real git
def test_cli_concurrent_amend_propose_fails_before_engine_or_pipeline_side_effects(
    tmp_path, monkeypatch, capsys,
):
    project = tmp_path / "project"
    project.mkdir()
    omac_dir = project / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(project)
    report = project / "review.md"
    report.write_text("concurrent amendment")
    docs = project / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    output_file = omac_dir / "dag.concurrent.amendment.yaml"
    ready = tmp_path / "first-propose-ready"
    release = tmp_path / "release-first-propose"
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(repo_root / "src"), env.get("PYTHONPATH", ""),
    ]))
    script = """
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[7])
from _support import fake_platform  # noqa: E402

fake_platform.install()

import omac.pipeline.amendment as amendment_pipeline
from omac.cli.main import main

ready = Path(sys.argv[1])
release = Path(sys.argv[2])
manifest, report, docs, output_file = sys.argv[3:7]

def hold_propose(*_args, **_kwargs):
    ready.write_text("locked")
    while not release.exists():
        time.sleep(0.01)
    return {
        "state": "pending_human_confirmation",
        "manifest": manifest,
        "amendment_file": output_file,
        "amendment_id": "held-amendment",
        "issue_id": "held-issue",
        "reviewer_verdict": "pass",
    }

amendment_pipeline.propose_amendment = hold_propose
raise SystemExit(main([
    "dag", "amend", "propose", manifest,
    "--report-file", report,
    "--docs", docs,
    "--output-file", output_file,
    "--output", "json",
]))
"""
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(ready), str(release),
         str(manifest_path), str(report), str(docs), str(output_file), str(repo_root / "tests")],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if first.poll() is not None:
                stdout, stderr = first.communicate()
                pytest.fail(
                    f"first propose exited before holding lock: {stdout}\n{stderr}")
            time.sleep(0.01)
        assert ready.exists(), "first propose did not reach the locked pipeline"

        monkeypatch.setattr(
            dag_cmd, "_assemble_engine",
            lambda _args: pytest.fail(
                "second propose must fail before engine assembly"))
        monkeypatch.setattr(
            amendment_pipeline, "propose_amendment",
            lambda *_args, **_kwargs: pytest.fail(
                "second propose must not enter the pipeline"))

        code = main([
            "dag", "amend", "propose", str(manifest_path),
            "--report-file", str(report),
            "--docs", str(docs),
            "--output-file", str(output_file),
            "--output", "json",
        ])

        assert code == exit_codes.VALIDATION
        assert "dag amend propose" in capsys.readouterr().err
    finally:
        release.write_text("release")
        stdout, stderr = first.communicate(timeout=10)
        assert first.returncode == exit_codes.NEEDS_DECISION, (stdout, stderr)


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


def test_cli_amendment_forwards_new_attempt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    omac_dir = tmp_path / ".omac"
    omac_dir.mkdir()
    (omac_dir / "config.yaml").write_text(yaml.safe_dump({
        "engine": "mock",
        "workspace": "ws",
        "roles": {"orchestrator": "alice", "reviewers": ["bob"]},
        "retry": {"review": 2},
        "defaults": {"poll_interval": 0},
    }))
    manifest_path = _manifest(tmp_path)
    report = tmp_path / "review.md"
    report.write_text("new amendment attempt")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design")
    observed = {}

    def fake_propose(*_args, **kwargs):
        observed.update(kwargs)
        return {
            "state": "pending_human_confirmation",
            "manifest": str(manifest_path),
            "amendment_file": str(omac_dir / "dag.amendment.yaml"),
            "amendment_id": "amendment-attempt",
            "issue_id": "issue-new",
            "reviewer_verdict": "pass",
        }

    monkeypatch.setattr(amendment_pipeline, "propose_amendment", fake_propose)

    code = main([
        "dag", "amend", "propose", str(manifest_path),
        "--report-file", str(report),
        "--docs", str(docs),
        "--new-attempt",
        "--supersedes-issue-id", "issue-old",
    ])

    assert code == exit_codes.NEEDS_DECISION
    assert observed["new_attempt"] is True
    assert observed["supersedes_issue_id"] == "issue-old"


# ---- validate_affected: the derived affected set must be safe to touch ----

def _affected_case(tmp_path, decision=None):
    path = _manifest(tmp_path)
    manifest = load_manifest(str(path))
    engine = _engine()
    item = engine.store.create_work_item(
        "ws", "bootstrap", "desc", "bootstrap", "alice", reviewer="bob")
    assert item.id == manifest.nodes["bootstrap"].work_item_id
    item.decision_required = decision
    return manifest, engine.store, _proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"})


def test_validate_affected_accepts_a_node_without_held_decision(tmp_path):
    manifest, store, proposal = _affected_case(tmp_path)
    amendment_mod.validate_affected(manifest, proposal, store)


def test_validate_affected_rejects_a_node_holding_a_worker_decision(tmp_path):
    manifest, store, proposal = _affected_case(
        tmp_path, decision={"reason_code": "worker-decision-required"})
    with pytest.raises(ValidationError, match="bootstrap.*unresolved worker decision"):
        amendment_mod.validate_affected(manifest, proposal, store)


def test_validate_affected_rejects_a_vanished_work_item(tmp_path, monkeypatch):
    from omac.errors import WorkItemNotFoundError

    manifest, store, proposal = _affected_case(tmp_path)

    def gone(_item_id):
        raise WorkItemNotFoundError("gone")

    monkeypatch.setattr(store, "get_work_item", gone)
    with pytest.raises(ValidationError, match="lost its work item"):
        amendment_mod.validate_affected(manifest, proposal, store)


def test_validate_affected_ignores_nodes_outside_the_derived_set(tmp_path):
    manifest, store, proposal = _affected_case(
        tmp_path, decision={"reason_code": "worker-decision-required"})
    manifest.nodes["bootstrap"].work_item_id = "unrelated"
    other = _proposal({"op": "resume", "node": "closeout", "stage": "authoring"})
    amendment_mod.validate_affected(manifest, other, store)

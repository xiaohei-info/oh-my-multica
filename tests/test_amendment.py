import copy

import pytest
import yaml

import omac.core.amendment as amendment_mod
from omac.core.amendment import (
    apply_amendment,
    build_reviewed_amendment,
    manifest_definition_digest,
    manifest_digest,
    validate_proposal,
)
from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import TaskKind
from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline.dispatch import submit


def _engine():
    return create_engine("mock", EngineConfig(
        engine_type="mock",
        workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false"},
    ))


def _manifest(tmp_path):
    path = tmp_path / "dag.yaml"
    path.write_text(yaml.safe_dump({
        "meta": {"name": "demo", "closeout_node": "closeout"},
        "nodes": [
            {
                "id": "bootstrap",
                "worker": "alice",
                "reviewer": "bob",
                "blocked_by": [],
                "work_item_id": "1",
                "status": "blocked",
                "contract": {
                    "objective": "bootstrap",
                    "source_of_truth": ["docs/design.md"],
                    "acceptance": ["UJ-WHOLE-FLOW"],
                    "non_goals": ["feature work"],
                    "verification_commands": ["pytest -q"],
                    "integration_gates": [{
                        "name": "bootstrap",
                        "layer": "L1",
                        "delivery_goal": "workspace boots",
                        "source_of_truth": ["docs/design.md"],
                        "covers": ["bootstrap"],
                        "acceptance_refs": ["UJ-WHOLE-FLOW"],
                        "commands": ["pytest -q"],
                    }],
                    "pr_base": "main",
                    "scope_paths": ["pyproject.toml"],
                },
            },
            {
                "id": "closeout",
                "worker": "charlie",
                "reviewer": "bob",
                "blocked_by": ["bootstrap"],
                "status": "todo",
            },
        ],
    }, allow_unicode=True, sort_keys=False))
    return path


def _proposal(*operations):
    return {
        "schema": "omac.dag-amendment/v1",
        "reason": "Reviewer found an invalid acceptance mapping",
        "operations": list(operations),
    }


def _topology_manifest(tmp_path):
    path = tmp_path / "topology.yaml"
    path.write_text(yaml.safe_dump({
        "meta": {"name": "topology"},
        "nodes": [
            {
                "id": "foundation",
                "worker": "charlie",
                "blocked_by": [],
                "status": "done",
            },
            {
                "id": "bootstrap",
                "worker": "alice",
                "reviewer": "bob",
                "blocked_by": [],
                "work_item_id": "1",
                "status": "blocked",
                "contract": _contract_update()["set"]["contract"],
            },
            {
                "id": "started-dependent",
                "worker": "charlie",
                "reviewer": "bob",
                "blocked_by": ["bootstrap"],
                "work_item_id": "2",
                "status": "in_review",
            },
            {
                "id": "future-dependent",
                "worker": "alice",
                "reviewer": "bob",
                "blocked_by": ["started-dependent"],
                "status": "todo",
            },
        ],
    }, allow_unicode=True, sort_keys=False))
    return path


def _contract_update():
    return {
        "op": "update",
        "node": "bootstrap",
        "set": {
            "contract": {
                "objective": "bootstrap",
                "source_of_truth": ["docs/design.md"],
                "acceptance": ["bootstrap workspace is valid"],
                "non_goals": ["feature work"],
                "verification_commands": ["pytest -q"],
                "integration_gates": [{
                    "name": "bootstrap",
                    "layer": "L1",
                    "delivery_goal": "workspace boots",
                    "source_of_truth": ["docs/design.md"],
                    "covers": ["bootstrap"],
                    "acceptance_refs": ["bootstrap workspace is valid"],
                    "commands": ["pytest -q"],
                }],
                "pr_base": "main",
                "scope_paths": ["pyproject.toml"],
            },
        },
    }


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
    engine.store.observe_pull_request = lambda *_args, **_kwargs: pytest.fail(
        "amendment accept must delegate PR observation to dag run")
    engine.store.request_pull_request_merge = lambda *_args, **_kwargs: pytest.fail(
        "amendment accept must delegate merge requests to dag run")

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
    )
    proposal = _proposal({"op": "resume", "node": "bootstrap", "stage": "merging"})
    reviewed = build_reviewed_amendment(
        load_manifest(str(path)), proposal, engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")

    with pytest.raises(ValidationError, match="passed review and PR"):
        apply_amendment(str(path), reviewed, engine.store, {"alice", "bob", "charlie"})

    assert path.read_bytes() == original


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
    assert load_manifest(str(path)).nodes["bootstrap"].status == "todo"


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
    reviewed = build_reviewed_amendment(
        base, _proposal(_contract_update()), engine.store,
        issue_id="amendment-issue", reviewer_verdict="pass")
    assert reviewed["base"]["manifest_sha256"] == manifest_digest(base)
    assert reviewed["base"]["definition_sha256"] == manifest_definition_digest(base)

    raw = yaml.safe_load(path.read_text())
    raw["nodes"][1]["status"] = "blocked"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    apply_amendment(str(path), copy.deepcopy(reviewed), engine.store, {"alice", "bob", "charlie"})

    path = _manifest(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["nodes"][1]["blocked_by"] = []
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
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
        node, store, stage, *, expected_review_subject=None, sync_contract=False,
    ):
        nonlocal failed
        if node.id == "started-dependent" and not failed:
            failed = True
            raise RuntimeError("simulated Store interruption")
        return original_sync(
            node, store, stage,
            expected_review_subject=expected_review_subject,
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
    assert result["sync"]["already_complete"] == ["bootstrap"]
    assert result["sync"]["synced"] == ["started-dependent"]
    completed = load_manifest(str(path)).meta["amendment_apply"]["nodes"]
    assert completed["bootstrap"]["state"] == "synced"
    assert completed["started-dependent"]["state"] == "synced"


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
        engine.store, item.id, amendment_file=str(amendment_file),
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

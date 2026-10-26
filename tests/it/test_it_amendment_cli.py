"""``omac dag amend`` on a real Multica: propose with real agents, accept, the options forwarded to the pipeline, and the propose lock."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

import omac.core.amendment as amendment_mod
import omac.pipeline.amendment as amendment_pipeline
from _support.amendkit import bootstrap, contract_update, proposal, seal_review_fixture
from _support.taskkit import submit_step
from omac.cli import exit_codes
from omac.cli.commands import dag as dag_cmd
from omac.cli.main import main
from omac.core.manifest import load_manifest
from omac.core.workitem import WorkItemStatus
from omac.errors import ValidationError

ROLES = {"workers": ["alice", "charlie"], "orchestrator": "alice", "reviewers": ["bob"]}


@pytest.fixture()
def cli(kit, tmp_path):
    """The project's `.omac` directory, configured with the roles an amendment run uses, and a report plus docs for it."""
    kit.project.configure(roles=ROLES, retry={"review": 2, "ci": 2, "merge": 2, "worker": 2})
    omac_dir = kit.project.work / ".omac"
    report = kit.project.work / "review.md"
    report.write_text("bootstrap wrongly claims the whole user journey")
    docs = kit.project.work / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("bootstrap contributes only a local gate")
    subprocess.run("git add -A && git commit -qm docs", shell=True, cwd=kit.project.work, check=True, capture_output=True)   # docs are read at a git revision
    return omac_dir, report, docs


def blocked_bootstrap(kit, omac_dir):
    path, item = bootstrap(kit, omac_dir)
    return path, item


def propose_args(path, report, docs, *extra):
    return ["dag", "amend", "propose", str(path), "--report-file", str(report), "--docs", str(docs), *extra]


def test_the_command_line_stops_after_the_reviewer_and_a_human_accept_applies_the_amendment(kit, cli):
    omac_dir, report, docs = cli
    path, item = blocked_bootstrap(kit, omac_dir)
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/1", "head_sha": "abc"},
                                        verification={"subject_digest": "verify-1", "commands": []}, review_verdict="reject",
                                        review_report={"blockers": ["bad mapping"]})
    seal_review_fixture(kit.store, item.id)
    amendment = omac_dir / "amendment-proposal.yaml"
    amendment.write_text(yaml.safe_dump(proposal(contract_update()), sort_keys=False))
    kit.stack.set_scenario("alice", [[submit_step(kit.stack, "--amendment-file", str(amendment))]])
    kit.stack.set_scenario("bob", [[kit.stack.review("pass")]])
    amendment_path = omac_dir / "dag.amendment.yaml"

    code = main(propose_args(path, report, docs, "--blocked-node", "bootstrap", "--output-file", str(amendment_path)))

    assert code == exit_codes.NEEDS_DECISION
    reviewed = yaml.safe_load(amendment_path.read_text())
    issue = kit.store.get_work_item(reviewed["review"]["issue_id"])
    assert (issue.phase.value, issue.review_verdict) == ("confirmation", "pass")
    assert load_manifest(str(path)).nodes["bootstrap"].status == "blocked"

    code = main(["dag", "amend", "accept", str(path), str(amendment_path), "--reason", "reviewed by operator"])

    assert code == exit_codes.OK
    updated = load_manifest(str(path))
    assert (updated.nodes["bootstrap"].status, updated.nodes["bootstrap"].work_item_id) == ("in_review", item.id)
    assert kit.store.get_work_item(reviewed["review"]["issue_id"]).status == WorkItemStatus.DONE
    assert yaml.safe_load(amendment_path.read_text())["human_confirmation"] == "applied"


def forwarded(kit, monkeypatch, cli, *extra):
    """Run `omac dag amend propose` with the pipeline replaced by a recorder; returns (exit code, keyword arguments received)."""
    omac_dir, report, docs = cli
    path, _ = blocked_bootstrap(kit, omac_dir)
    observed = {}

    def fake_propose(*_args, **kwargs):
        observed.update(kwargs)
        return {"state": "pending_human_confirmation", "manifest": str(path), "amendment_file": str(omac_dir / "dag.amendment.yaml"),
                "amendment_id": "amendment-1", "issue_id": "issue-1", "reviewer_verdict": "pass"}

    monkeypatch.setattr(amendment_pipeline, "propose_amendment", fake_propose)
    return main(propose_args(path, report, docs, *extra)), observed


def test_resuming_a_failed_authoring_issue_is_forwarded_to_the_pipeline(kit, cli):
    code, observed = forwarded(kit, kit.monkeypatch, cli, "--blocked-node", "bootstrap", "--resume-issue-id", "e66f44e4-bd0f-4a9c-8bfa-2f60a7641d84")

    assert code == exit_codes.NEEDS_DECISION and observed["resume_issue_id"] == "e66f44e4-bd0f-4a9c-8bfa-2f60a7641d84"


def test_restarting_authoring_is_forwarded_to_the_pipeline(kit, cli):
    code, observed = forwarded(kit, kit.monkeypatch, cli, "--resume-issue-id", "issue-1", "--restart-authoring")

    assert code == exit_codes.NEEDS_DECISION and observed["resume_issue_id"] == "issue-1" and observed["restart_authoring"] is True


def test_a_new_attempt_and_the_issue_it_supersedes_are_forwarded_to_the_pipeline(kit, cli):
    code, observed = forwarded(kit, kit.monkeypatch, cli, "--new-attempt", "--supersedes-issue-id", "issue-old")

    assert code == exit_codes.NEEDS_DECISION and observed["new_attempt"] is True and observed["supersedes_issue_id"] == "issue-old"


def test_a_concurrent_propose_fails_before_it_assembles_an_engine_or_enters_the_pipeline(kit, cli, tmp_path, capsys):
    omac_dir, report, docs = cli
    path, _ = blocked_bootstrap(kit, omac_dir)
    output_file = omac_dir / "dag.concurrent.amendment.yaml"
    ready, release = tmp_path / "first-propose-ready", tmp_path / "release-first-propose"
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, **kit.stack.cli_env(), **kit.stack.omac_env()}
    script = """
import sys, time
from pathlib import Path
import omac.pipeline.amendment as amendment_pipeline
from omac.cli.main import main

ready, release = Path(sys.argv[1]), Path(sys.argv[2])
manifest, report, docs, output_file = sys.argv[3:7]

def hold_propose(*_args, **_kwargs):
    ready.write_text("locked")
    while not release.exists():
        time.sleep(0.01)
    return {"state": "pending_human_confirmation", "manifest": manifest, "amendment_file": output_file,
            "amendment_id": "held-amendment", "issue_id": "held-issue", "reviewer_verdict": "pass"}

amendment_pipeline.propose_amendment = hold_propose
raise SystemExit(main(["dag", "amend", "propose", manifest, "--report-file", report, "--docs", docs,
                       "--output-file", output_file, "--output", "json"]))
"""
    first = subprocess.Popen([sys.executable, "-c", script, str(ready), str(release), str(path), str(report), str(docs), str(output_file)],
                             cwd=kit.project.work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            if first.poll() is not None:
                stdout, stderr = first.communicate()
                pytest.fail(f"first propose exited before holding the lock: {stdout}\n{stderr}")
            time.sleep(0.01)
        assert ready.exists(), "first propose did not reach the locked pipeline"
        kit.monkeypatch.setattr(dag_cmd, "_assemble_engine", lambda _args: pytest.fail("the second propose must fail before assembling an engine"))
        kit.monkeypatch.setattr(amendment_pipeline, "propose_amendment", lambda *_a, **_k: pytest.fail("the second propose must not enter the pipeline"))

        code = main(propose_args(path, report, docs, "--output-file", str(output_file), "--output", "json"))

        assert code == exit_codes.VALIDATION and "dag amend propose" in capsys.readouterr().err
    finally:
        release.write_text("release")
        stdout, stderr = first.communicate(timeout=30)
        assert first.returncode == exit_codes.NEEDS_DECISION, (stdout, stderr)


# ---- which nodes an amendment may touch ---------------------------------------------------------------------------------------

def affected_case(kit, tmp_path, decision=None):
    path, item = bootstrap(kit, tmp_path)
    if decision is not None:
        kit.store.update_work_item_metadata(item.id, decision_required=decision)
    return load_manifest(str(path)), proposal({"op": "resume", "node": "bootstrap", "stage": "authoring"})


def test_a_node_without_a_held_decision_may_be_amended(kit, tmp_path):
    manifest, resume = affected_case(kit, tmp_path)

    amendment_mod.validate_affected(manifest, resume, kit.store)


def test_a_node_holding_a_worker_decision_may_not_be_amended(kit, tmp_path):
    manifest, resume = affected_case(kit, tmp_path, decision={"schema": "omac.decision-required/v1", "reason_code": "worker-decision-required"})

    with pytest.raises(ValidationError, match="bootstrap.*unresolved worker decision"):
        amendment_mod.validate_affected(manifest, resume, kit.store)


def test_a_node_whose_work_item_vanished_may_not_be_amended(kit, tmp_path):
    manifest, resume = affected_case(kit, tmp_path)
    manifest.nodes["bootstrap"].work_item_id = "01a00000-0000-7000-8000-000000000000"   # no such issue on the platform

    with pytest.raises(ValidationError, match="lost its work item"):
        amendment_mod.validate_affected(manifest, resume, kit.store)


def test_nodes_outside_the_derived_set_are_not_checked(kit, tmp_path):
    manifest, _ = affected_case(kit, tmp_path, decision={"schema": "omac.decision-required/v1", "reason_code": "worker-decision-required"})
    manifest.nodes["bootstrap"].work_item_id = "unrelated"

    amendment_mod.validate_affected(manifest, proposal({"op": "resume", "node": "closeout", "stage": "authoring"}), kit.store)

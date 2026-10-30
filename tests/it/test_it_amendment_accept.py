"""Accepting a reviewed amendment on a real Multica: the review issue, nits acceptance, and what an active Run blocks."""
import pytest
import yaml

import omac.pipeline.amendment as amendment_mod
from _support.amendkit import bootstrap, contract_update, pass_with_nits_amendment_fixture, proposal
from omac.core.amendment import build_reviewed_amendment
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import ValidationError
from _support.amendkit import accept, accept_nits, reviewed_resume









def test_an_amendment_must_reference_an_amendment_issue(kit, tmp_path):
    path, _ = bootstrap(kit, tmp_path)
    issue = kit.store.create_work_item(kit.workspace, "develop", "desc", "develop", "alice", reviewer="bob", kind=TaskKind.DEVELOP)
    kit.store.update_work_item_metadata(issue.id, phase=TaskPhase.CONFIRMATION, review_verdict="pass")
    reviewed = build_reviewed_amendment(load_manifest(str(path)), proposal(contract_update()), kit.store, issue_id=issue.id,
                                        reviewer_verdict="pass")
    amendment_file = tmp_path / "wrong-kind.yaml"
    amendment_file.write_text(yaml.safe_dump(reviewed, sort_keys=False))

    with pytest.raises(ValidationError, match="must reference an amendment"):
        accept(kit, path, amendment_file)


def test_accepting_nits_records_a_marker_without_applying_and_is_idempotent(kit, tmp_path):
    path, issue, amendment_file, reviewed = pass_with_nits_amendment_fixture(kit, tmp_path)
    before_manifest = path.read_bytes()

    result = accept_nits(kit, path, amendment_file)

    assert result["state"] == "nits_accepted" and result["next_action"].startswith("omac dag amend accept ")
    assert path.read_bytes() == before_manifest
    current = kit.store.get_work_item(issue.id)
    assert (current.review_verdict, current.phase, current.status) == ("pass-with-nits", TaskPhase.CONFIRMATION, WorkItemStatus.IN_REVIEW)
    marker = current.review_nits_acceptance
    assert marker["schema"] == "omac.amendment-review-nits-acceptance/v1"
    assert (marker["issue_id"], marker["amendment_id"]) == (issue.id, reviewed["amendment_id"])

    assert accept_nits(kit, path, amendment_file, "same operator decision")["state"] == "nits_accepted"
    assert kit.store.get_work_item(issue.id).review_nits_acceptance == marker


def test_the_official_accept_applies_the_amendment_once_the_nits_marker_exists(kit, tmp_path):
    path, issue, amendment_file, reviewed = pass_with_nits_amendment_fixture(kit, tmp_path)
    accept_nits(kit, path, amendment_file)

    result = accept(kit, path, amendment_file)

    assert result["state"] == "applied"
    assert load_manifest(str(path)).meta["last_amendment_id"] == reviewed["amendment_id"]
    assert kit.store.get_work_item(issue.id).status == WorkItemStatus.DONE


def replace_report(kit, issue):   # the platform now holds a different report attachment than the one that was reviewed
    report = {"full_review_completed": True, "blockers": [], "nits": ["a replacement nit"]}
    kit.store.update_work_item_metadata(issue.id, review_report=report, review_report_source=yaml.safe_dump(report, sort_keys=False))


def stale_subject(kit, issue, amendment_file):
    amendment = yaml.safe_load(amendment_file.read_text())
    amendment["review"]["review_subject_digest"] = "stale-subject"
    amendment_file.write_text(yaml.safe_dump(amendment, sort_keys=False))


@pytest.mark.parametrize("drift", [lambda kit, issue, file: stale_subject(kit, issue, file),
                                   lambda kit, issue, file: replace_report(kit, issue)], ids=["subject", "current-report-ref"])
def test_nits_are_not_accepted_when_the_subject_or_the_report_drifted(kit, tmp_path, drift):
    path, issue, amendment_file, _ = pass_with_nits_amendment_fixture(kit, tmp_path)
    drift(kit, issue, amendment_file)
    before_manifest = path.read_bytes()

    with pytest.raises(ValidationError, match="subject/report/ledger/deliverable"):
        accept_nits(kit, path, amendment_file)

    assert path.read_bytes() == before_manifest
    assert kit.store.get_work_item(issue.id).review_nits_acceptance in (None, {})


def test_nits_are_not_accepted_while_a_direct_run_is_active_on_the_review_issue(kit, tmp_path):
    path, issue, amendment_file, _ = pass_with_nits_amendment_fixture(kit, tmp_path)
    kit.assign(issue.id, "bob")   # the Reviewer's Run is still going
    before_manifest, before_amendment = path.read_bytes(), amendment_file.read_bytes()

    with pytest.raises(ValidationError, match="active.*direct Run"):
        accept_nits(kit, path, amendment_file)

    assert path.read_bytes() == before_manifest and amendment_file.read_bytes() == before_amendment
    assert kit.store.get_work_item(issue.id).review_nits_acceptance in (None, {})


def test_accepting_an_authoring_resume_fails_before_the_manifest_is_written_while_a_formal_run_is_active(kit, tmp_path):
    path, target, amendment_file = reviewed_resume(kit, tmp_path, "amend-active-run")
    kit.assign(target.id, "alice")   # the node's Worker still has a formal Run
    before = path.read_bytes()

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        accept(kit, path, amendment_file)

    assert path.read_bytes() == before and kit.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


def test_resuming_an_applied_authoring_amendment_after_a_crash_still_refuses_while_a_formal_run_is_active(kit, tmp_path):
    path, target, amendment_file = reviewed_resume(kit, tmp_path, "amend-crash-resume")
    original = amendment_mod.prepare_stage_recovery
    kit.monkeypatch.setattr(amendment_mod, "prepare_stage_recovery",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("crash after manifest write")))
    with pytest.raises(RuntimeError, match="after manifest write"):
        accept(kit, path, amendment_file)
    assert load_manifest(str(path)).meta["amendment_apply"]["nodes"]["bootstrap"]["state"] == "syncing"
    kit.monkeypatch.setattr(amendment_mod, "prepare_stage_recovery", original)
    kit.assign(target.id, "alice")
    before = path.read_bytes()

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        accept(kit, path, amendment_file)

    assert path.read_bytes() == before and kit.store.get_work_item(target.id).status is WorkItemStatus.BLOCKED


def test_a_formal_run_without_an_explicit_terminal_state_blocks_the_first_accept(kit, tmp_path):
    from omac.core.workitem import AgentRunObservation

    path, target, amendment_file = reviewed_resume(kit, tmp_path, "amend-unknown-run")
    unknown = AgentRunObservation(id="formal-unknown-run", kind="direct", status="unknown", agent_id="agent-alice",
                                  trigger_kind="issue_assignment")
    assert unknown.formal and not unknown.active and not unknown.terminal
    # the platform never reports a status it does not know: the adapter's answer is replaced for this one method
    kit.monkeypatch.setattr(kit.runtime, "list_runs", lambda item_id: [unknown] if item_id == target.id else [])
    before_manifest, before_amendment = path.read_bytes(), amendment_file.read_bytes()
    before_item = kit.store.get_work_item(target.id)

    with pytest.raises(ValidationError, match="active formal Agent Runs"):
        accept(kit, path, amendment_file)

    assert path.read_bytes() == before_manifest and amendment_file.read_bytes() == before_amendment
    assert kit.store.get_work_item(target.id) == before_item

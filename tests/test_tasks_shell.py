"""Which persisted facts make a deterministic amendment attempt "started" (pure: facts in, verdict out, no platform).

``run_task`` and ``finalize_authoring_shell`` refuse to dispatch a shell for which these checks report any activity
or identity mismatch; the refusals themselves (nothing written, reason codes) run against Docker Multica in
tests/it/test_it_tasks_attempts.py.
"""
from __future__ import annotations

import copy
from dataclasses import fields

import pytest

import omac.pipeline.tasks as tasks_module
from _support.samples import work_item
from omac.core.manifest import Contract
from omac.core.taskmeta import (
    DELIVERY_IDENTITY_SCHEMA, REVIEWER_RUN_BASELINE_SCHEMA, Bounces, DeliveryIdentity, ReviewerRunBaseline, TaskKind,
    TaskPhase, WorkerHandoffIntent,
)
from omac.core.workitem import WorkItem, WorkItemStatus
from omac.engines import create_engine
from omac.engines.models import EngineConfig
from omac.engines.multica import MulticaStore
from omac.pipeline.tasks import AuthoringTaskSpec

DAG_KEY = "amend-project-attempt-pristine"
ATTEMPT = {"request_digest": "pristine"}
REFS = [{"issue_id": "old", "relation": "supersedes"}]
BODY = "rendered amendment body"
CONTRACT = Contract(objective="实现 feature-x", acceptance=["端到端可走通"], non_goals=["不碰其他模块"])
SPEC = AuthoringTaskSpec(kind=TaskKind.AMENDMENT, title="new amendment", dag_key=DAG_KEY, assignee="alice",
                         contract=CONTRACT, source_refs=REFS, amendment_attempt=ATTEMPT)

_MULTICA_PRISTINE_ISSUE = {
    "id": "issue-pristine-1", "identifier": "PROJ-812",
    "title": f"[DAG:{DAG_KEY}] new amendment",
    "description": BODY, "status": "todo", "metadata": {}, "assignee_type": None,
    "creator_type": "member", "due_date": None, "labels": [], "number": 812,
    "parent_issue_id": None, "position": -1, "priority": "none", "properties": {},
    "stage": None, "start_date": None,
}

_PRISTINE_AMENDMENT_INITIALIZATION_FIELDS = {
    "id", "workspace_id", "identifier", "title", "description", "status",
    "dag_key", "worker", "contract", "contract_ref", "source_refs", "kind",
    "phase", "amendment_attempt", "created_at", "updated_at",
}

_AMENDMENT_ACTIVITY_FIELD_CASES = [
    ("reviewer", "bob", "reviewer"),
    ("blocked_by", ["upstream"], "blocked_by"),
    ("wave", 1, "wave"),
    ("artifacts", {"pr_url": "https://example.test/pr/1"}, "artifacts"),
    ("verification", {"commands": []}, "verification"),
    ("verification_ref", {"attachment_id": "verification"}, "verification_ref"),
    ("review_verdict", "pass", "review_verdict"),
    ("review_comment", "reviewed", "review_comment"),
    ("machine_feedback", {"schema": "omac.machine-feedback/v1"}, "machine_feedback"),
    ("machine_feedback_ref", {"attachment_id": "feedback"}, "machine_feedback_ref"),
    ("review_report", {"blockers": []}, "review_report"),
    ("review_report_ref", {"attachment_id": "review"}, "review_report_ref"),
    ("review_subject_digest", "subject", "review_subject_digest"),
    ("review_obligations", [{"obligation_id": "one"}], "review_obligations"),
    ("review_obligations_ref", {"attachment_id": "obligations"}, "review_obligations_ref"),
    ("review_ledger", {"schema": "omac.review-ledger/v1"}, "review_ledger"),
    ("review_ledger_ref", {"attachment_id": "ledger"}, "review_ledger_ref"),
    ("review_generation", "review-generation-1", "review_generation"),
    (
        "review_ledger_generation",
        "review-generation-1",
        "review_ledger_generation",
    ),
    (
        "bounce_baseline",
        {"worker": 14, "review": 3, "merge": 0},
        "bounce_baseline",
    ),
    ("review_continuation", {"authorized_rounds": 1}, "review_continuation"),
    (
        "review_nits_acceptance",
        {
            "schema": "omac.review-nits-acceptance/v1",
            "review_subject_digest": "subject",
            "review_report_ref": {"attachment_id": "report", "sha256": "a" * 64},
            "verdict": "pass-with-nits",
        },
        "review_nits_acceptance",
    ),
    (
        "reviewer_run_baseline",
        ReviewerRunBaseline(
            schema=REVIEWER_RUN_BASELINE_SCHEMA,
            subject_digest="subject",
            target_reviewer="bob",
            target_agent_id="agent-bob",
            baseline_direct_run_ids=("run-old",),
        ),
        "reviewer_run_baseline",
    ),
    (
        "worker_handoff",
        WorkerHandoffIntent(
            schema="omac.worker-handoff/v1",
            state="pending",
            target_worker="alice",
            gate="review",
            source_review_subject_digest="subject",
            source_review_round=1,
            target_review_bounce=1,
        ),
        "worker_handoff",
    ),
    (
        "delivery_identity",
        DeliveryIdentity(
            schema=DELIVERY_IDENTITY_SCHEMA,
            handoff_generation="generation-1",
            worker="alice",
            agent_id="agent-alice",
            run_id="run-1",
            pr_url="https://github.com/acme/repo/pull/1",
            pr_head_sha="head-1",
            verification_sha256="verification-1",
        ),
        "delivery_identity",
    ),
    ("decision_required", {"reason": "human"}, "decision_required"),
    ("bounces", Bounces(worker=1), "bounces.worker"),
    ("bounces", Bounces(ci=1), "bounces.ci"),
    ("bounces", Bounces(review=1), "bounces.review"),
    ("bounces", Bounces(merge=1), "bounces.merge"),
    ("deliverable", "proposal", "deliverable"),
    ("deliverable_ref", {"attachment_id": "delivery"}, "deliverable_ref"),
    ("project_rules", "rules", "project_rules"),
    ("project_rules_ref", {"attachment_id": "rules"}, "project_rules_ref"),
    ("agent_run_finished_without_submit", True, "agent_run_finished_without_submit"),
    ("agent_run_failed", True, "agent_run_failed"),
    ("platform_assignee_id", "agent-1", "platform_assignee_id"),
    ("unknown_persisted_fields", {"future_execution_fact": False},
     "unknown_persisted_fields"),
]




def shell(**over):
    """A shell as the platform returns it right after the creation steps of an amendment attempt."""
    base = dict(
        title=f"[DAG:{DAG_KEY}] new amendment", description=BODY, dag_key=DAG_KEY, worker="alice", kind=TaskKind.AMENDMENT,
        contract=CONTRACT, source_refs=REFS, amendment_attempt=ATTEMPT, phase=TaskPhase.AUTHORING)
    return work_item(**{**base, **over})


def verdict(item, *, finalized=True):
    return tasks_module._pristine_amendment_shell_errors(item, SPEC, body=BODY, refs=REFS, workspace_id="ws", finalized=finalized)


def multica_shell(issue_overrides=None, metadata_overrides=None):
    """The shell as the Multica adapter projects an issue payload (a synthetic payload, nothing is contacted)."""
    issue = copy.deepcopy(_MULTICA_PRISTINE_ISSUE)
    issue.update(issue_overrides or {})
    issue["metadata"] = {**issue["metadata"], **(metadata_overrides or {})}
    return MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))._issue_to_work_item(issue, "ws")



def test_an_emptied_reviewer_is_the_initial_state_not_activity():
    # ClearAssignment writes "" for "no reviewer"; the shell must not be refused as started because of its own cleanup
    assert verdict(shell(reviewer=""))[1] == [] and verdict(shell(reviewer="bob"))[1] == ["reviewer"]


def test_the_projection_covers_every_work_item_field():
    activity_fields = {case[0] for case in _AMENDMENT_ACTIVITY_FIELD_CASES}
    assert {definition.name for definition in fields(WorkItem)} == _PRISTINE_AMENDMENT_INITIALIZATION_FIELDS | activity_fields


@pytest.mark.parametrize("field_name", sorted(_PRISTINE_AMENDMENT_INITIALIZATION_FIELDS))
def test_a_shell_with_only_initialization_facts_is_pristine(field_name):
    overrides = {
        "identifier": {"identifier": "PROJ-ATTEMPT"}, "contract_ref": {"contract_ref": {"attachment_id": "contract"}},
        "created_at": {"created_at": "2026-07-28T00:00:00Z"}, "updated_at": {"updated_at": "2026-07-28T00:01:00Z"},
    }.get(field_name, {})

    assert verdict(shell(**overrides)) == ([], [])


@pytest.mark.parametrize(("field_name", "value", "case_id"), _AMENDMENT_ACTIVITY_FIELD_CASES,
                         ids=[case[2] for case in _AMENDMENT_ACTIVITY_FIELD_CASES])
def test_every_non_default_activity_fact_means_the_attempt_started(field_name, value, case_id):
    item = shell()
    setattr(item, field_name, value)

    identity_errors, activity = verdict(item)

    assert identity_errors == [] and field_name in activity


@pytest.mark.parametrize(("status", "phase"), [
    (WorkItemStatus.IN_PROGRESS, TaskPhase.AUTHORING), (WorkItemStatus.IN_REVIEW, TaskPhase.AUTHORING),
    (WorkItemStatus.BLOCKED, TaskPhase.AUTHORING), (WorkItemStatus.FAILED, TaskPhase.AUTHORING),
    (WorkItemStatus.DONE, TaskPhase.AUTHORING), (WorkItemStatus.TODO, TaskPhase.REVIEW), (WorkItemStatus.TODO, TaskPhase.CONFIRMATION),
])
def test_a_status_or_phase_past_the_shell_means_the_attempt_started(status, phase):
    _, activity = verdict(shell(status=status, phase=phase))

    assert set(activity) == ({"status"} if status is not WorkItemStatus.TODO else set()) | ({"phase"} if phase is not TaskPhase.AUTHORING else set())


@pytest.mark.parametrize(("field", "value"), [
    ("deliverable", "existing delivery"), ("deliverable_ref", {"attachment_id": "delivery"}), ("review_verdict", "pass"),
    ("review_report", {"blockers": []}), ("review_report_ref", {"attachment_id": "review"}), ("review_subject_digest", "subject"),
    ("review_ledger", {"schema": "omac.review-ledger/v1"}), ("review_ledger_ref", {"attachment_id": "ledger"}),
    ("review_continuation", {"authorized_rounds": 1}), ("decision_required", {"reason": "human confirmation"}),
])
def test_a_shell_with_delivery_or_review_history_is_refused(field, value):
    assert verdict(shell(**{field: value}))[1] == [field]


@pytest.mark.parametrize(("conflict", "identity", "activity"), [
    ("attempt", ["amendment_attempt"], []), ("kind", ["kind"], []), ("dag_key", ["dag_key"], []),
    ("status", [], ["status"]), ("phase", [], ["phase"]), ("deliverable", [], ["deliverable"]),
])
def test_a_shell_that_does_not_match_the_attempt_is_a_conflict_or_started(conflict, identity, activity):
    over = {"attempt": {"amendment_attempt": {"request_digest": "other"}}, "kind": {"kind": TaskKind.PLAN},
            "dag_key": {"dag_key": "other-attempt"}, "status": {"status": WorkItemStatus.IN_REVIEW},
            "phase": {"phase": TaskPhase.REVIEW}, "deliverable": {"deliverable": "unexpected"}}[conflict]

    assert verdict(shell(**over), finalized=False) == (identity, activity)


def test_a_finalized_check_demands_the_whole_identity_while_the_partial_one_tolerates_missing_steps():
    partial = shell(dag_key="", kind=TaskKind.DEVELOP, contract=None, amendment_attempt=None, source_refs=[], description="new amendment")

    assert verdict(partial, finalized=False) == ([], [])
    assert verdict(partial, finalized=True)[0] == ["description", "dag_key", "kind", "contract", "source_refs", "amendment_attempt"]


def test_an_orphan_shell_with_only_a_platform_assignee_is_recognised_as_created_by_us():
    engine = create_engine("multica", EngineConfig(engine_type="multica", workspace_id="ws"))
    orphan = shell(amendment_attempt=None, description="new amendment", platform_assignee_id="platform-auto-assignee")
    orphan.id = "orphan-1"
    body, _refs = tasks_module._authoring_materialization(engine, orphan.id, SPEC, orphan)
    orphan.description = body

    assert tasks_module._is_pristine_created_amendment_shell(engine, orphan, SPEC) is True
    assert tasks_module._is_pristine_created_amendment_shell(engine, shell(amendment_attempt=ATTEMPT), SPEC) is False
    started = shell(amendment_attempt=None, description=body, platform_assignee_id="auto", deliverable="x")
    assert tasks_module._is_pristine_created_amendment_shell(engine, started, SPEC) is False


# ---- the Multica issue payload -> shell facts -------------------------------------------------------------------------

def test_the_stable_multica_issue_envelope_fields_are_not_unknown_activity():
    item = multica_shell({"last_activity_at": "2026-09-04T01:00:00Z", "revision": 4, "status_category": "backlog", "status_name": "todo"})

    assert item.unknown_persisted_fields == {}


@pytest.mark.parametrize("issue_overrides", [{}, {"duplicate_of": None}])
def test_a_standard_multica_envelope_is_a_pristine_shell(issue_overrides):
    item = multica_shell(issue_overrides)

    assert item.identifier == "PROJ-812" and item.unknown_persisted_fields == {}
    assert verdict(item, finalized=False) == ([], [])


@pytest.mark.parametrize(("issue_overrides", "metadata_overrides", "expected_unknown"), [
    ({"future_run_fact": False}, {}, "issue.future_run_fact"),
    ({}, {"future_execution_fact": False}, "metadata.future_execution_fact"),
    ({"properties": {"future_run_fact": False}}, {}, "issue.properties"),
    ({"duplicate_of": "another-issue"}, {}, "issue.duplicate_of"),
    ({"duplicate_of": ""}, {}, "issue.duplicate_of"),
    ({"duplicate_of": False}, {}, "issue.duplicate_of"),
    ({"duplicate_of": 0}, {}, "issue.duplicate_of"),
    ({"duplicate_of": {}}, {}, "issue.duplicate_of"),
    ({"duplicate_of": []}, {}, "issue.duplicate_of"),
    ({"future_nullable_fact": None}, {}, "issue.future_nullable_fact"),
])
def test_a_multica_payload_with_a_fact_we_do_not_know_keeps_the_shell_fail_closed(issue_overrides, metadata_overrides, expected_unknown):
    item = multica_shell(issue_overrides, metadata_overrides)
    scope, _, name = expected_unknown.partition(".")
    raw = issue_overrides[name] if scope == "issue" else metadata_overrides[name]

    assert item.unknown_persisted_fields == {expected_unknown: raw}
    assert verdict(item, finalized=False)[1] == ["unknown_persisted_fields"]

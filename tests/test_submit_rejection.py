"""A failed `omac work submit` leaves a comment the Loop can read; and what the Loop then tells the operator to run."""
import shlex
from dataclasses import replace
from types import SimpleNamespace

import pytest

from _support.state import save_literal
from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import load_manifest
from omac.core.submit_rejection import (
    FAILURE_CLASS_METADATA_BUDGET, REASON_REVIEWER_SUBMIT_REJECTED, SubmitRejection, format_submit_rejection,
    parse_submit_rejection,
)
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.errors import MetadataBudgetError
from omac.pipeline import dispatch
from omac.pipeline.decide_recovery import classify_reviewer_recovery_decision, reviewer_recovery_decision
from _support.recoverykit import KEY, delivered_item


def test_the_comment_round_trips_and_ignores_unrelated_text():
    sent = SubmitRejection(FAILURE_CLASS_METADATA_BUDGET, "review", "subj", "pass", "x" * 900)
    got = parse_submit_rejection("hello\n" + format_submit_rejection(sent))
    assert (got.failure_class, got.phase, got.subject_digest, got.verdict) == (
        FAILURE_CLASS_METADATA_BUDGET, "review", "subj", "pass")
    assert len(got.detail) == 500
    assert parse_submit_rejection("plain comment") is None


@pytest.mark.parametrize("text", [
    "OMAC-SUBMIT-REJECTED not-json",
    'OMAC-SUBMIT-REJECTED {"schema": "other/v1", "failure_class": "x"}',
    'OMAC-SUBMIT-REJECTED {"schema": "omac.submit-rejected/v1", "failure_class": ""}',
    None,
])
def test_a_malformed_marker_is_not_a_rejection(text):
    assert parse_submit_rejection(text) is None


class FakeStore:
    def __init__(self, fail=True):
        self.fail, self.comments = fail, []

    def update_work_item_metadata(self, *_args, **_kwargs):
        raise MetadataBudgetError("platform-metadata-budget: full")

    def add_comment(self, item_id, text):
        self.comments.append((item_id, text))


def _submit_review(store, monkeypatch):
    item = SimpleNamespace(review_subject_digest="subj")
    monkeypatch.setattr(dispatch, "_validate_review", lambda *_: {"ok": True})
    monkeypatch.setattr(dispatch, "_review_ledger_metadata", lambda *_: {})
    monkeypatch.setattr(dispatch, "_read_text", lambda _path: "report")
    args = SimpleNamespace(verdict="pass", report_file="r.yaml")
    return dispatch._submit_review(store, "issue-1", item, TaskKind.DEVELOP, TaskPhase.REVIEW, args)


def test_a_review_submit_the_platform_rejects_is_recorded_for_the_loop(monkeypatch):
    store = FakeStore()
    with pytest.raises(MetadataBudgetError, match="NOT stored"):
        _submit_review(store, monkeypatch)
    (item_id, text), = store.comments
    recorded = parse_submit_rejection(text)
    assert item_id == "issue-1" and recorded.subject_digest == "subj" and recorded.verdict == "pass"
    assert recorded.failure_class == FAILURE_CLASS_METADATA_BUDGET


def test_when_the_rejection_cannot_be_recorded_the_agent_still_gets_the_budget_error(monkeypatch):
    store = FakeStore()
    store.add_comment = lambda *_: (_ for _ in ()).throw(MetadataBudgetError("also full"))
    with pytest.raises(MetadataBudgetError, match="Recording the rejection also failed"):
        _submit_review(store, monkeypatch)


def test_the_advertised_recovery_command_runs_through_main_and_clears_the_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("OMAC_ENGINE", "OMAC_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)
    nodes = [{"id": KEY, "worker": "alice", "reviewer": "bob", "status": "blocked", "work_item_id": "item-1"}]
    path = save_literal(tmp_path / "dag.yaml", {"meta": {}, "nodes": nodes})

    item = delivered_item()
    decision = reviewer_recovery_decision(
        path, KEY, item, REASON_REVIEWER_SUBMIT_REJECTED, run_id="run-1", failure_class=FAILURE_CLASS_METADATA_BUDGET)
    item = replace(item, decision_required=decision)

    assert decision["failure_class"] == FAILURE_CLASS_METADATA_BUDGET
    assert classify_reviewer_recovery_decision(path, KEY, item) == "explicit-review-retry"
    argv = shlex.split(decision["next_action"])
    assert argv[:3] == ["omac", "node", "retry"]
    assert main(argv[1:]) == exit_codes.OK
    assert load_manifest(path).nodes[KEY].status == "in_review"



def test_the_adapter_returns_the_latest_rejection_with_its_platform_time():
    from omac.engines.models import EngineConfig
    from omac.engines.multica import MulticaStore

    def body(subject):
        return format_submit_rejection(SubmitRejection(FAILURE_CLASS_METADATA_BUDGET, "review", subject))

    comments = [
        {"id": "c1", "content": body("old"), "created_at": "2026-08-01T01:00:00Z"},
        {"id": "c2", "content": "unrelated"},
        {"id": "c3", "content": body("new"), "created_at": "2026-08-01T02:00:00Z"},
    ]
    store = MulticaStore(EngineConfig("multica", "ws"))
    store._run_multica = lambda args, **_: comments if args[:3] == ["issue", "comment", "list"] else None

    found = store.observe_submit_rejection("issue-1")

    assert (found.subject_digest, found.comment_id, found.created_at) == ("new", "c3", "2026-08-01T02:00:00Z")
    comments[:] = [{"id": "c2", "content": "unrelated"}]
    assert store.observe_submit_rejection("issue-1") is None

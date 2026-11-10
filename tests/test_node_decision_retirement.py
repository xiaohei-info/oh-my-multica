"""An operator decision (node accept / abandon) retires the decision and handoff it answers, and the node's recovery marker.

Reproduces: reviewer rejects, worker blocks with a decision, the operator merges the PR by hand and accepts. The node is
done + merged, so a leftover decision / handoff on the issue used to make the next reconcile raise and stop the whole run.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from _support.state import save_literal
from _support.workitems import make_item

import omac.cli.commands.node as node_cmd
from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskKind, TaskPhase, WorkerHandoffIntent
from omac.core.workitem import PullRequestObservation, PullRequestState, WorkItemStatus
from omac.pipeline import loop

PR = "https://example.test/pr/1"
HANDOFF = WorkerHandoffIntent(
    schema="omac.worker-handoff/v1", state="pending", target_worker="worker", gate="review",
    source_review_subject_digest="subject", source_review_round=1, target_review_bounce=1, generation="handoff-1",
    target_agent_id="agent-worker", baseline_direct_run_ids=(), baseline_verification_attachment_id="att-1",
    target_run_id="run-worker", target_worker_bounce=0)


class _Store:
    """Holds one WorkItem and applies the metadata writes the way the platform does (an empty value clears)."""

    def __init__(self, item):
        self.item = item
        self.writes = []

    def get_work_item(self, item_id):
        return self.item

    def update_work_item_metadata(self, item_id, **fields):
        self.writes.append(fields)
        self.item = replace(self.item, **{k: (None if k == "worker_handoff" and not v else v) for k, v in fields.items()})
        return self.item

    def update_status(self, item_id, status):
        self.item = replace(self.item, status=status)

    def normalize_confirmed_merge(self, item_id):
        self.item = replace(self.item, status=WorkItemStatus.DONE, platform_assignee_id=None)


class _Merged:
    def __init__(self, state):
        self.state = state

    def observe_pull_request(self, url):
        return PullRequestObservation(self.state, "2026-11-06T00:00:00Z" if self.state is PullRequestState.MERGED else None)


def _setup(tmp_path, monkeypatch, pr_state=PullRequestState.MERGED, with_engine=True):
    monkeypatch.chdir(tmp_path)
    item = make_item(
        TaskKind.DEVELOP, TaskPhase.AUTHORING, status=WorkItemStatus.BLOCKED, artifacts={"pr_url": PR},
        decision_required={"schema": "omac.decision-required/v1", "reason_code": "owner-decision-required"},
        worker_handoff=HANDOFF)
    store = _Store(item)
    monkeypatch.setattr(
        node_cmd, "_build_engine",
        lambda _config: SimpleNamespace(store=store, code_host=_Merged(pr_state)) if with_engine else None)
    path = save_literal(tmp_path / "m.yaml", {"meta": {}, "nodes": [
        {"id": "a", "worker": "alice", "status": "blocked", "work_item_id": item.id, "recovery_marker": True}]})
    return store, path


def test_accept_after_a_manual_merge_leaves_nothing_that_stops_the_next_reconcile(tmp_path, monkeypatch):
    store, path = _setup(tmp_path, monkeypatch, pr_state=PullRequestState.OPEN)
    assert main(["node", "accept", path, "a"]) == exit_codes.VALIDATION      # PR not merged yet: nothing changes
    assert store.writes == [] and load_manifest(path).nodes["a"].recovery_marker is True
    node_cmd._build_engine = lambda _c: SimpleNamespace(store=store, code_host=_Merged(PullRequestState.MERGED))

    assert main(["node", "accept", path, "a"]) == exit_codes.OK

    node = load_manifest(path).nodes["a"]
    assert (node.status, node.merged, node.recovery_marker) == ("done", True, False)
    assert (store.item.decision_required, store.item.worker_handoff) == ({}, None)
    manifest = load_manifest(path)
    # the next dag run's reconcile of this confirmed-merge node must not raise
    loop._reconcile_confirmed_merge(store, manifest, "a", manifest.nodes["a"], store.item)


def test_abandon_retires_the_decision_and_handoff_and_the_recovery_marker(tmp_path, monkeypatch):
    store, path = _setup(tmp_path, monkeypatch)

    assert main(["node", "abandon", path, "a"]) == exit_codes.OK

    node = load_manifest(path).nodes["a"]
    assert (node.status, node.recovery_marker) == ("abandoned", False)
    assert (store.item.decision_required, store.item.worker_handoff) == ({}, None)
    assert store.item.status is WorkItemStatus.BLOCKED                          # the platform status is not the operator's decision here


def test_abandon_writes_nothing_to_an_issue_that_carries_neither_fact(tmp_path, monkeypatch):
    store, path = _setup(tmp_path, monkeypatch)
    store.item = replace(store.item, decision_required=None, worker_handoff=None)

    assert main(["node", "abandon", path, "a"]) == exit_codes.OK

    assert store.writes == []


# ---------------- owner answer to a Worker blocker ----------------

BLOCKER = {"schema": "omac.decision-required/v1", "reason_code": "worker-decision-required",
           "blocker": {"handoff_generation": "handoff-1"}}


def test_retry_answer_is_bound_to_the_blockers_generation_and_reaches_the_worker(tmp_path, monkeypatch):
    store, path = _setup(tmp_path, monkeypatch)
    store.item = replace(store.item, decision_required=BLOCKER)
    store.comments = []
    store.add_comment = lambda item_id, text: store.comments.append(text)
    monkeypatch.setattr(node_cmd, "_retry_on_platform", lambda *a, **k: None)

    assert main(["node", "retry", path, "a", "--answer", "take option 2"]) == exit_codes.OK

    assert store.item.owner_answer == {"handoff_generation": "handoff-1", "text": "take option 2"}
    assert store.comments and "OWNER ANSWER to blocker handoff-1: take option 2" in store.comments[0]
    assert load_manifest(path).nodes["a"].retry_note == "OWNER ANSWER to blocker handoff-1: take option 2"


def test_retry_answer_needs_an_open_blocker_and_excludes_note(tmp_path, monkeypatch):
    store, path = _setup(tmp_path, monkeypatch)            # decision without a Worker blocker
    assert main(["node", "retry", path, "a", "--answer", "x"]) == exit_codes.VALIDATION
    store.item = replace(store.item, decision_required=BLOCKER)
    assert main(["node", "retry", path, "a", "--answer", "x", "--note", "y"]) == exit_codes.VALIDATION
    assert store.writes == []

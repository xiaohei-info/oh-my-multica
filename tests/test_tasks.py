"""Pure decisions of the authoring task runner (no platform): what counts as produced, who reviews, when a resumed issue is refreshed.

The task runner itself (create, dispatch, review rounds, resume, attempts) runs on Docker Multica: tests/it/test_it_tasks_*.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import omac.pipeline.tasks as tasks_module
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus


def test_produced_requires_review_phase_and_review_status_to_agree():
    """远端 phase 写后读延迟时，旧 review phase 不能跳过 producer 返工。"""
    stale = SimpleNamespace(
        phase=TaskPhase.REVIEW,
        status=WorkItemStatus.IN_PROGRESS,
    )
    submitted = SimpleNamespace(
        phase=TaskPhase.REVIEW,
        status=WorkItemStatus.IN_REVIEW,
    )

    assert tasks_module._produced(stale) is False
    assert tasks_module._produced(submitted) is True


def test_pick_reviewer_falls_back_to_self_when_only_producer():
    """池里仅产出者时回退自审(角色可自由指定),不再报错。"""
    from omac.pipeline.tasks import _pick_reviewer
    assert _pick_reviewer(["alice"], "alice", 0) == "alice"


def test_pick_reviewer_prefers_non_producer_when_available():
    """有非产出者时仍优先选非产出者(保留独立性)。"""
    from omac.pipeline.tasks import _pick_reviewer
    assert _pick_reviewer(["alice", "bob"], "alice", 0) == "bob"
    assert _pick_reviewer(["alice", "bob"], "alice", 1) == "bob"


@pytest.mark.parametrize("facts,unstarted,stopped_with_delivery", [
    (dict(), True, False),
    (dict(deliverable_ref={"attachment_id": "existing-delivery"}), False, False),
    (dict(agent_run_failed=True), False, False),
    (dict(agent_run_finished_without_submit=True), False, False),
    (dict(status=WorkItemStatus.IN_PROGRESS), False, False),
    (dict(phase=TaskPhase.REVIEW), False, False),
    (dict(deliverable="existing proposal"), False, False),
    (dict(deliverable="existing proposal", status=WorkItemStatus.BLOCKED), False, True),
    (dict(deliverable="existing proposal", status=WorkItemStatus.FAILED), False, True),
    (dict(deliverable="existing proposal", status=WorkItemStatus.IN_PROGRESS, agent_run_failed=True), False, True),
    (dict(deliverable="existing proposal", status=WorkItemStatus.IN_PROGRESS, agent_run_finished_without_submit=True), False, True),
    (dict(deliverable="existing proposal", phase=TaskPhase.CONFIRMATION, status=WorkItemStatus.BLOCKED), False, False),
])
def test_a_resumed_authoring_issue_is_refreshed_in_place_only_when_nothing_was_delivered_or_stopped(facts, unstarted, stopped_with_delivery):
    from _support.samples import work_item

    item = work_item(kind=TaskKind.PLAN, **{"status": WorkItemStatus.TODO, "phase": TaskPhase.AUTHORING, **facts})

    assert tasks_module._is_unstarted_authoring(item) is unstarted
    assert tasks_module._is_stopped_authoring_with_delivery(item) is stopped_with_delivery

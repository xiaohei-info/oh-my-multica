"""A later public authoring retry retains the original rejected source pair."""
from copy import deepcopy

from omac.cli.main import main
from omac.core.taskmeta import TaskPhase
from omac.engines.models import WorkItemStatus
from test_cli_node import _pass_with_nits_fixture


def test_blocked_authoring_retry_retains_rejected_subject(tmp_path, monkeypatch):
    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    item = engine.store.get_work_item(item_id)
    item.review_verdict = "reject"
    item.bounces.review = 2
    subject = item.review_subject_digest
    assert main(["node", "retry", path, "b", "--stage", "authoring"]) == 0
    prior = deepcopy(item.worker_handoff)
    assert prior.source_review_subject_digest == subject
    assert item.review_subject_digest is None
    item.status = WorkItemStatus.BLOCKED
    counters = deepcopy(item.bounces)
    monkeypatch.setattr(engine.runtime, "wake", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("retry cannot dispatch")))
    assert main(["node", "retry", path, "b", "--stage", "authoring"]) == 0
    assert item.worker_handoff.source_review_subject_digest == subject
    assert item.worker_handoff.source_review_feedback == prior.source_review_feedback
    assert item.worker_handoff.source_review_round == prior.source_review_round
    assert item.bounces == counters
    assert item.phase == TaskPhase.AUTHORING

"""The Multica metadata adapter keeps the full durable retry intent when the reset of an authoring generation fails half way.

The stand-in is the platform CLI boundary (tests/test_handoff_control_payload.py); the order of the Loop's own writes around a
retry is exercised on a real Multica: tests/it/test_it_node_retry_authoring.py.
"""
import pytest

from omac.core.taskmeta import parse_worker_handoff
from omac.errors import PlatformError
from _support.handoffcase import case  # noqa: F401  (the fixture)


@pytest.mark.parametrize(
    "failure", ["clear-rejected", "clear-reply-lost", "issue-put-reply-lost"]
)
def test_multica_partial_reset_preserves_full_durable_intent(
    case,
    monkeypatch,
    failure,
):
    store, metadata, raw_intent, bodies, writes, publications, issue = case
    raw_intent = {
        **raw_intent,
        "state": "recovering",
        "authoring_recovery": {
            "generation": "new-generation",
            "source": {"binding": {"generation": "old"}},
        },
    }
    intent = parse_worker_handoff(raw_intent)
    store._set_metadata("issue-1", "worker_handoff", raw_intent)
    assert len(publications) == 1  # aggregate overflow uses the immutable payload
    assert not intent.is_causally_bound()
    before_counters = {k: metadata[k] for k in ("worker_bounce", "review_bounce")}
    monkeypatch.setattr(
        store,
        "get_work_item",
        lambda _id: store._issue_to_control_projection(issue(), "ws").work_item,
    )
    original = store._run_multica
    injected = False

    def run(args, **kw):
        nonlocal injected
        is_clear = (
            args[:3] == ["issue", "metadata", "set"]
            and args[args.index("--key") + 1] != "worker_handoff"
        )
        if is_clear and not injected and failure == "clear-rejected":
            injected = True
            raise PlatformError("offline clear rejected")
        result = original(args, **kw)
        if is_clear and not injected and failure == "clear-reply-lost":
            injected = True
            raise PlatformError("offline clear response lost")
        return result

    def put(_id, fields, **kw):
        nonlocal injected
        assert fields["suppress_run"] and fields["assignee_id"] is None
        if not injected and failure == "issue-put-reply-lost":
            injected = True
            raise PlatformError("offline PUT response lost")

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(store, "_put_issue_fields_direct", put)
    with pytest.raises(PlatformError):
        store.restore_authoring_generation(
            "issue-1", {}, "new-generation", worker_handoff=intent
        )
    assert injected
    assert store.get_work_item("issue-1").worker_handoff == intent
    assert {k: metadata[k] for k in before_counters} == before_counters
    store.restore_authoring_generation(
        "issue-1", {}, "new-generation", worker_handoff=intent
    )
    after = store.get_work_item("issue-1")
    assert after.worker_handoff == intent
    assert (
        after.worker_handoff.source_review_feedback
        == raw_intent["source_review_feedback"]
    )
    assert after.review_generation == "new-generation"
    assert not after.review_report_ref and not after.delivery_identity
    assert {k: metadata[k] for k in before_counters} == before_counters
    assert store._control_metadata_bytes(metadata) <= 8192

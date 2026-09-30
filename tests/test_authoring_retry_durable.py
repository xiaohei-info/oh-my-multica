"""Authoring retry never retires rejected feedback before a durable successor."""

from copy import deepcopy

import pytest

from omac.cli.main import main
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskPhase, review_context_binding
from omac.engines.models import WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline.dispatch import _previous_review_context
from test_cli_node import _pass_with_nits_fixture


@pytest.mark.parametrize(
    "checkpoint",
    [
        "intent-rejected",
        "intent-reply-lost",
        "restore-reply-lost",
        "crash-after-intent",
        "crash-after-restore",
        "finish-reply-lost",
        "finish-rejected",
        "partial-restore",
    ],
)
def test_retry_durable_source_and_restart(tmp_path, monkeypatch, capsys, checkpoint):
    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    item = engine.store.get_work_item(item_id)
    item.review_verdict = "reject"
    item.bounces.worker = 20
    item.bounces.review = 1
    item.delivery_identity = None
    item.review_generation = item.review_ledger_generation = "old-generation"
    old_ref = deepcopy(item.review_report_ref)
    old_subject = item.review_subject_digest
    old_metadata = deepcopy(load_manifest(path).meta)
    update = engine.store.update_work_item_metadata
    restore = engine.store.restore_authoring_generation
    writes = []
    injected = False

    def persist(*a, **kw):
        nonlocal injected
        intent = kw.get("worker_handoff")
        if intent:
            writes.append(intent)
            recovering = intent.state == "recovering"
            if not injected and checkpoint == "intent-rejected" and recovering:
                injected = True
                raise PlatformError("offline definitive rejection")
            if not injected and not recovering and checkpoint == "finish-rejected":
                injected = True
                raise PlatformError("offline finish rejected")
            result = update(*a, **kw)
            if (
                not injected
                and recovering
                and checkpoint in {"intent-reply-lost", "crash-after-intent"}
            ):
                injected = True
                if checkpoint.startswith("crash"):
                    raise RuntimeError("offline crash")
                raise PlatformError("offline response lost")
            if not injected and not recovering and checkpoint == "finish-reply-lost":
                injected = True
                raise PlatformError("offline finish response lost")
            return result
        return update(*a, **kw)

    def recover(*a, **kw):
        nonlocal injected
        assert item.worker_handoff and item.worker_handoff.state == "recovering"
        assert item.worker_handoff.source_review_feedback["report_ref"] == old_ref
        if not injected and checkpoint == "partial-restore":
            injected = True
            item.status = WorkItemStatus.TODO
            item.phase = TaskPhase.AUTHORING
            item.review_report = item.review_report_ref = None
            item.review_subject_digest = item.review_verdict = None
            item.delivery_identity = None
            raise PlatformError("offline partial restore")
        result = restore(*a, **kw)
        if not injected and checkpoint in {"restore-reply-lost", "crash-after-restore"}:
            injected = True
            if checkpoint.startswith("crash"):
                raise RuntimeError("offline crash")
            raise PlatformError("offline restore response lost")
        return result

    monkeypatch.setattr(engine.store, "update_work_item_metadata", persist)
    monkeypatch.setattr(engine.store, "restore_authoring_generation", recover)
    monkeypatch.setattr(
        engine.runtime, "wake", lambda *a, **kw: pytest.fail("retry cannot dispatch")
    )
    if checkpoint.startswith("crash"):
        with pytest.raises(RuntimeError, match="offline crash"):
            main(["node", "retry", path, "b"])
    else:
        code = main(["node", "retry", path, "b"])
        assert code == (
            2
            if checkpoint in {"intent-rejected", "finish-rejected", "partial-restore"}
            else 0
        )
    assert injected
    if checkpoint == "intent-rejected":
        assert item.review_report_ref == old_ref
        assert item.review_subject_digest == old_subject
        assert item.review_generation == "old-generation"
    else:
        assert item.worker_handoff.source_review_feedback["report_ref"] == old_ref
    monkeypatch.setattr(engine.store, "update_work_item_metadata", update)
    monkeypatch.setattr(engine.store, "restore_authoring_generation", restore)
    assert main(["node", "retry", path, "b"]) == 0
    completed = deepcopy(item.worker_handoff)
    assert completed.state == "pending"
    assert completed.authoring_recovery is None
    assert completed.review_context_binding == review_context_binding(item)
    assert completed.source_review_subject_digest == old_subject
    assert _previous_review_context(item)["report_ref"] == old_ref
    assert item.bounces.worker == 20 and item.bounces.review == 1
    assert item.phase is TaskPhase.AUTHORING and item.status is WorkItemStatus.TODO
    assert main(["node", "retry", path, "b"]) == 0
    assert item.worker_handoff == completed
    assert load_manifest(path).meta == old_metadata
    capsys.readouterr()


@pytest.mark.parametrize(
    "drift",
    [
        "contract",
        "generation",
        "head",
        "verification",
        "counter",
        "subject",
        "status",
        "assignee",
    ],
)
def test_pending_authoring_retry_fails_closed_on_drift(
    tmp_path, monkeypatch, capsys, drift
):
    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    engine.store.get_work_item(item_id).bounces.review = 1
    original = engine.store.restore_authoring_generation
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("offline crash")),
    )
    with pytest.raises(RuntimeError):
        main(["node", "retry", path, "b"])
    item = engine.store.get_work_item(item_id)
    assert item.worker_handoff.state == "recovering"
    if drift == "contract":
        item.contract = {"changed": True}
    if drift == "generation":
        item.review_generation = "foreign"
    if drift == "head":
        item.artifacts = {**item.artifacts, "head_sha": "foreign"}
    if drift == "verification":
        item.verification_ref = {**item.verification_ref, "attachment_id": "foreign"}
    if drift == "counter":
        item.bounces.worker += 1
    if drift == "subject":
        item.review_subject_digest = "foreign"
    if drift == "status":
        item.status = WorkItemStatus.IN_PROGRESS
    if drift == "assignee":
        item.platform_assignee_id = "foreign"
    before = deepcopy(item)
    monkeypatch.setattr(engine.store, "restore_authoring_generation", original)
    assert main(["node", "retry", path, "b"]) == 2
    assert item == before
    capsys.readouterr()


# Exercise the actual Multica KV adapter on the existing real 8KB failure fixture.
from test_handoff_control_payload import case  # noqa: E402,F401
from omac.core.taskmeta import parse_worker_handoff  # noqa: E402


@pytest.mark.parametrize(
    "failure", ["clear-rejected", "clear-reply-lost", "issue-put-reply-lost"]
)
def test_multica_partial_reset_preserves_full_durable_intent(
    case,  # noqa: F811
    monkeypatch,
    failure,  # noqa: F811
):  # noqa: F811
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


def test_recovery_intent_blocks_dispatch(tmp_path, monkeypatch):
    from omac.pipeline.loop import _dispatch_worker_handoff_locked
    from omac.engines.models import WorkItemControlProjection

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    engine.store.get_work_item(item_id).bounces.review = 1
    monkeypatch.setattr(
        engine.store,
        "restore_authoring_generation",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("offline crash")),
    )
    with pytest.raises(RuntimeError):
        main(["node", "retry", path, "b"])
    item = engine.store.get_work_item(item_id)
    assert not item.worker_handoff.is_causally_bound()
    monkeypatch.setattr(
        engine.runtime,
        "wake",
        lambda *a, **kw: pytest.fail("incomplete recovery cannot dispatch"),
    )
    with pytest.raises(PlatformError, match="Authoring recovery is incomplete"):
        _dispatch_worker_handoff_locked(
            engine.store,
            engine.runtime,
            load_manifest(path),
            "b",
            projection=WorkItemControlProjection(item),
        )


def test_repeat_retry_does_not_accept_malformed_pending_intent(
    tmp_path, monkeypatch, capsys
):
    from dataclasses import replace

    engine, path, item_id = _pass_with_nits_fixture(tmp_path, monkeypatch)
    engine.store.get_work_item(item_id).bounces.review = 1
    assert main(["node", "retry", path, "b"]) == 0
    item = engine.store.get_work_item(item_id)
    item.worker_handoff = replace(item.worker_handoff, target_agent_id=None)
    before = deepcopy(item)
    assert main(["node", "retry", path, "b"]) == 2
    assert item == before
    capsys.readouterr()

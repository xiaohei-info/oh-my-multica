"""A full control intent must fit the aggregate metadata budget without loss."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from omac.engines.multica import MulticaStore
from omac.engines.models import EngineConfig
from omac.errors import PlatformError


@pytest.fixture
def case(monkeypatch):
    folder = Path(__file__).parent / "fixtures"
    metadata = json.loads(
        (folder / "convergence_metadata_before_handoff.json").read_text()
    )
    intent = json.loads((folder / "convergence_failed_handoff.json").read_text())
    store = MulticaStore(EngineConfig("multica", "ws"))
    bodies = {}
    writes = []
    publications = []

    def issue():
        return {
            "id": "issue-1",
            "title": "node",
            "status": "in_review",
            "metadata": deepcopy(metadata),
        }

    def run(args, **kwargs):
        if args[:2] == ["issue", "get"]:
            return issue()
        if args[:3] == ["issue", "comment", "list"]:
            return [
                {
                    "id": ref["comment_id"],
                    "content": "- sha256: "
                    + ref["sha256"]
                    + "\n- bytes: "
                    + str(ref["bytes"]),
                    "attachments": [
                        {"id": ref["attachment_id"], "filename": ref["filename"]}
                    ],
                }
                for ref in publications
            ]
        if args[:3] == ["issue", "metadata", "set"]:
            key = args[args.index("--key") + 1]
            value = args[args.index("--value") + 1]
            candidate = {**metadata, key: value}
            if len(json.dumps(candidate, ensure_ascii=False).encode()) > 8192:
                raise PlatformError("metadata exceeds the 8KB size limit")
            writes.append((key, value))
            metadata[key] = value
            return {}
        pytest.fail(str(args))

    def publish(item_id, key, content, suffix):
        digest = hashlib.sha256(content.encode()).hexdigest()
        ref = {
            "comment_id": "comment-" + digest[:8],
            "attachment_id": "attachment-" + digest[:8],
            "sha256": digest,
            "bytes": len(content.encode()),
            "filename": "omac-" + key + "-" + digest[:12] + suffix,
        }
        bodies[ref["attachment_id"]] = content
        publications.append(ref)
        return ref

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(store, "_publish_payload_comment", publish)
    monkeypatch.setattr(
        store,
        "_load_payload_comment",
        lambda _id, _key, ref: bodies[ref["attachment_id"]],
    )
    return store, metadata, intent, bodies, writes, publications, issue


def test_real_total_limit_fixture_roundtrips_complete_intent_without_other_changes(
    case,
):
    store, metadata, intent, bodies, writes, publications, issue = case
    before = deepcopy(metadata)
    assert len(json.dumps(intent, ensure_ascii=False).encode()) < 8192
    assert (
        len(
            json.dumps(
                {**metadata, "worker_handoff": json.dumps(intent, ensure_ascii=False)},
                ensure_ascii=False,
            ).encode()
        )
        > 8192
    )
    store._set_metadata("issue-1", "worker_handoff", intent)
    assert len(publications) == 1
    assert {k: v for k, v in metadata.items() if k != "worker_handoff"} == {
        k: v for k, v in before.items() if k != "worker_handoff"
    }
    result = store._issue_to_control_projection(issue(), "ws").work_item.worker_handoff
    assert result.as_dict() == intent
    assert (
        len(result.baseline_direct_run_ids) == 20
        and len(result.source_review_feedback["blockers"]) == 3
    )
    assert result.target_worker_bounce == 20 and result.target_review_bounce == 2
    assert len(json.dumps(metadata, ensure_ascii=False).encode()) <= 8192


def test_small_control_intent_stays_inline(case):
    store, metadata, intent, _, writes, publications, _ = case
    metadata.clear()
    store._set_metadata("issue-1", "worker_handoff", intent)
    assert not publications and json.loads(writes[0][1]) == intent


def test_same_large_intent_retry_reuses_published_reference(case):
    store, metadata, intent, _, writes, publications, _ = case
    store._set_metadata("issue-1", "worker_handoff", intent)
    store._set_metadata("issue-1", "worker_handoff", intent)
    assert len(publications) == 1 and len(writes) == 1


@pytest.mark.parametrize("change", ["missing", "tampered", "wrong-key", "invalid-body"])
def test_reference_failure_never_looks_like_no_intent(case, change):
    store, metadata, intent, bodies, _, _, issue = case
    store._set_metadata("issue-1", "worker_handoff", intent)
    wrapper = json.loads(metadata["worker_handoff"])
    ref = wrapper["ref"]
    if change == "missing":
        bodies[ref["attachment_id"]] = None
    if change == "tampered":
        bodies[ref["attachment_id"]] = "{}"
    if change == "wrong-key":
        wrapper["key"] = "delivery_identity"
        metadata["worker_handoff"] = json.dumps(wrapper)
    if change == "invalid-body":
        bodies[ref["attachment_id"]] = "[]"
        ref["sha256"] = hashlib.sha256(b"[]").hexdigest()
        ref["bytes"] = 2
        metadata["worker_handoff"] = json.dumps(wrapper)
    with pytest.raises(PlatformError):
        store._issue_to_control_projection(issue(), "ws")


def test_unknown_set_reply_recovers_exact_committed_intent(case, monkeypatch):
    store, metadata, intent, _, _, _, issue = case
    original = store._run_multica

    def lost_reply(args, **kwargs):
        result = original(args, **kwargs)
        if args[:3] == ["issue", "metadata", "set"]:
            raise PlatformError("connection reset by peer")
        return result

    monkeypatch.setattr(store, "_run_multica", lost_reply)
    with pytest.raises(PlatformError):
        store._set_metadata("issue-1", "worker_handoff", intent)
    assert (
        store._issue_to_control_projection(
            issue(), "ws"
        ).work_item.worker_handoff.as_dict()
        == intent
    )


def test_irreducible_total_budget_fails_without_pruning_or_control_write(case):
    store, metadata, intent, _, writes, _, _ = case
    metadata["foreign_data"] = "x" * 8100
    before = deepcopy(metadata)
    with pytest.raises((ValueError, PlatformError)):
        store._set_metadata("issue-1", "worker_handoff", intent)
    assert metadata == before and not writes


def test_utf8_value_larger_than_single_limit_is_losslessly_externalized(case):
    store, metadata, intent, _, _, _, issue = case
    metadata.clear()
    intent["source_review_feedback"]["complete_non_ascii_note"] = "证据" * 5000
    store._set_metadata("issue-1", "worker_handoff", intent)
    assert (
        store._issue_to_control_projection(
            issue(), "ws"
        ).work_item.worker_handoff.as_dict()
        == intent
    )
    assert len(json.dumps(metadata, ensure_ascii=False).encode()) <= 8192


def test_publication_before_reference_crash_is_reused_on_restart(case, monkeypatch):
    store, metadata, intent, _, writes, publications, issue = case
    before = deepcopy(metadata)
    original = store._run_multica

    def before_commit(args, **kwargs):
        if args[:3] == ["issue", "metadata", "set"]:
            raise PlatformError("connection reset by peer")
        return original(args, **kwargs)

    monkeypatch.setattr(store, "_run_multica", before_commit)
    with pytest.raises(PlatformError):
        store._set_metadata("issue-1", "worker_handoff", intent)
    assert metadata == before and len(publications) == 1 and not writes
    restarted = MulticaStore(EngineConfig("multica", "ws"))
    monkeypatch.setattr(restarted, "_run_multica", original)
    monkeypatch.setattr(restarted, "_load_payload_comment", store._load_payload_comment)
    monkeypatch.setattr(
        restarted,
        "_publish_payload_comment",
        lambda *_args: pytest.fail("must reuse original immutable publication"),
    )
    restarted._set_metadata("issue-1", "worker_handoff", intent)
    assert (
        restarted._issue_to_control_projection(
            issue(), "ws"
        ).work_item.worker_handoff.as_dict()
        == intent
    )
    assert len(publications) == 1


def test_control_reference_cannot_be_replayed_on_a_different_issue(case):
    store, _, intent, _, _, _, issue = case
    store._set_metadata("issue-1", "worker_handoff", intent)
    foreign = issue()
    foreign["id"] = "different-issue"
    with pytest.raises(PlatformError):
        store._issue_to_control_projection(foreign, "ws")


def test_mutable_handoff_checkpoint_reuses_full_payload_without_system_comment(case):
    store, metadata, intent, _, _, publications, issue = case
    store._set_metadata("issue-1", "worker_handoff", intent)
    first = deepcopy(json.loads(metadata["worker_handoff"]))
    updated = deepcopy(intent)
    updated["target_run_id"] = "new-worker-run"
    updated["terminal_observed_at"] = "2026-09-30T07:01:00Z"
    store._set_metadata("issue-1", "worker_handoff", updated)
    assert len(publications) == 1
    assert json.loads(metadata["worker_handoff"])["ref"] == first["ref"]
    assert (
        store._issue_to_control_projection(
            issue(), "ws"
        ).work_item.worker_handoff.as_dict()
        == updated
    )


@pytest.mark.parametrize(
    "overlay",
    [
        {"source_review_subject_digest": "foreign"},
        {"baseline_direct_run_ids": []},
        {"target_run_id": 42},
    ],
)
def test_overlay_cannot_change_immutable_binding_or_hide_malformed_progress(
    case, overlay
):
    store, metadata, intent, _, _, _, issue = case
    store._set_metadata("issue-1", "worker_handoff", intent)
    wrapper = json.loads(metadata["worker_handoff"])
    wrapper["overlay"] = overlay
    metadata["worker_handoff"] = json.dumps(wrapper)
    with pytest.raises(PlatformError):
        store._issue_to_control_projection(issue(), "ws")


def test_small_control_write_rechecks_concurrent_intent_without_overwriting(
    case, monkeypatch
):
    store, metadata, intent, _, writes, publications, _ = case
    metadata.clear()
    reads = 0
    original = store._read_issue_metadata
    foreign = deepcopy(intent)
    foreign["generation"] = "foreign-handoff"

    def concurrent(item_id):
        nonlocal reads
        reads += 1
        if reads == 2:
            metadata["worker_handoff"] = json.dumps(foreign)
        return original(item_id)

    monkeypatch.setattr(store, "_read_issue_metadata", concurrent)
    with pytest.raises(PlatformError, match="changed"):
        store._set_metadata("issue-1", "worker_handoff", intent)
    assert (
        not writes
        and not publications
        and json.loads(metadata["worker_handoff"]) == foreign
    )

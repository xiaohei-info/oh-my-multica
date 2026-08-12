import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from omac.core.contract_boundaries import responsibility_summary
from omac.core.manifest import _load_contract
from omac.core.manifest import Contract, EvidenceMode, ProducedArtifact
from omac.core.taskmeta import (
    DECISION_REQUIRED_KEY, MACHINE_FEEDBACK_REF_KEY, PHASE_KEY,
    REVIEWER_RUN_BASELINE_KEY, REVIEW_LEDGER_REF_KEY, REVIEW_REPORT_REF_KEY,
    REVIEW_SUBJECT_DIGEST_KEY, TaskKind, TaskPhase,
)
from omac.engines.models import (
    AgentRunObservation, EngineConfig, PullRequestReadinessFailure, PullRequestState,
)
from omac.engines.models import WorkItemStatus
from omac.engines.metadata_policy import encode_metadata_value
from omac.engines.multica import MulticaRuntime, MulticaStore
from omac.errors import PlatformError
from omac.pipeline import dispatch as dispatch_mod


@pytest.mark.parametrize("message", [
    "Request timed out: the server did not respond in time.",
    "context deadline exceeded",
    "HTTP 503 Service Unavailable",
    "connection reset by peer",
])
def test_multica_classifies_known_transient_transport_errors(message):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    assert store.is_transient_transport_error(PlatformError(message))


def test_multica_list_runs_preserves_platform_retry_parent(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(runtime, "_issue_runs", lambda _item_id: [{
        "id": "run-2",
        "kind": "direct",
        "status": "running",
        "agent_id": "agent-1",
        "retry_of_task_id": "run-1",
    }])

    runs = runtime.list_runs("issue-1")

    assert len(runs) == 1
    assert runs[0].retry_of_run_id == "run-1"


@pytest.mark.parametrize("message", [
    "HTTP 401 unauthorized",
    "HTTP 403 forbidden",
    "validation rejected: invalid metadata",
    "issue not found",
])
def test_multica_rejects_hard_errors_as_transient_transport(message):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    assert not store.is_transient_transport_error(PlatformError(message))


def test_multica_finalizes_authoring_identity_with_existing_store_writes(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    writes = []
    expected = SimpleNamespace(id="issue-1")
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda item_id, key, value: writes.append((item_id, key, value)))
    monkeypatch.setattr(store, "get_work_item", lambda _item_id: expected)

    observed = store.set_authoring_identity(
        "issue-1", dag_key="amend-project-attempt-abc", kind=TaskKind.AMENDMENT)

    assert observed is expected
    assert writes == [
        ("issue-1", "dag_key", "amend-project-attempt-abc"),
        ("issue-1", "kind", "amendment"),
    ]


def test_multica_empty_ref_tombstones_suppress_legacy_payloads(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    item = store._issue_to_work_item({
        "id": "issue-1",
        "title": "amendment",
        "description": "history remains in comments",
        "status": "todo",
        "metadata": {
            "dag_key": "amend-restart",
            "kind": "amendment",
            "phase": "authoring",
            "deliverable": "legacy old amendment",
            "deliverable_ref": {},
            "verification": {"old": True},
            "verification_ref": {},
            "review_report": {"old": True},
            "review_report_ref": {},
        },
    }, "ws")

    assert item.deliverable is None
    assert item.deliverable_ref is None
    assert item.verification is None
    assert item.verification_ref is None
    assert item.review_report is None
    assert item.review_report_ref is None


def test_multica_empty_review_control_tombstones_are_canonical_absence():
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_control_projection({
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_progress",
        "metadata": {
            "dag_key": "develop-a",
            "kind": "develop",
            DECISION_REQUIRED_KEY: "{}",
            MACHINE_FEEDBACK_REF_KEY: "{}",
            REVIEW_REPORT_REF_KEY: "{}",
            REVIEWER_RUN_BASELINE_KEY: "{}",
            "worker_handoff": "{}",
        },
    }, "ws").work_item

    assert item.decision_required is None
    assert item.machine_feedback_ref is None
    assert item.review_report_ref is None
    assert item.reviewer_run_baseline is None
    assert item.worker_handoff is None


def test_multica_maps_current_and_ledger_review_generations():
    """Store 映射必须保留最小持久边界，不能让展示层猜测 phase。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_control_projection({
        "id": "issue-1",
        "title": "review generation",
        "description": "review generation",
        "status": "todo",
        "metadata": {
            "dag_key": "contracts-platform-resource-schema",
            "kind": "develop",
            "phase": "authoring",
            "review_generation": "amendment-aiteam-850",
            "review_ledger_generation": "review-aiteam-849",
            "bounce_baseline": '{"worker":14,"review":3,"merge":0}',
        },
    }, "ws").work_item

    assert item.review_generation == "amendment-aiteam-850"
    assert item.review_ledger_generation == "review-aiteam-849"
    assert item.bounce_baseline == {"worker": 14, "review": 3, "merge": 0}


def test_authoring_generation_tombstone_hides_legacy_inline_review_report(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    item_id = "11111111-1111-4111-8111-111111111111"
    old_metadata = {
        "dag_key": "contracts-platform-resource-schema",
        "kind": "develop",
        "phase": "review",
        "review_verdict": "reject",
        "review_report": {"blockers": ["legacy inline report"]},
    }
    metadata_sets = []
    monkeypatch.setattr(
        store, "_publish_payload_comment",
        lambda *_args, **_kwargs: {
            "attachment_id": "contract-850", "sha256": "b" * 64})
    monkeypatch.setattr(
        store, "_read_issue_metadata",
        lambda _item_id: ({"id": item_id}, dict(old_metadata)))
    monkeypatch.setattr(
        store, "_put_issue_fields_direct",
        lambda _item_id, fields, *, operation: None)
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda target_id, key, value: metadata_sets.append((key, value)))
    monkeypatch.setattr(store, "get_work_item", lambda _item_id: SimpleNamespace())

    store.restore_authoring_generation(
        item_id, {"objective": "amended"}, "amendment-aiteam-850")

    # restore 现在逐键经 `issue metadata set` 写入;在服务端侧合并被写入的键。
    projected = dict(old_metadata)
    for key, value in metadata_sets:
        projected[key] = encode_metadata_value(value)
    mapped = store._issue_to_control_projection({
        "id": item_id,
        "title": "legacy inline report",
        "description": "legacy inline report",
        "status": "todo",
        "metadata": projected,
    }, "ws").work_item
    assert mapped.review_report is None
    assert mapped.review_report_ref is None


def test_multica_restore_writes_metadata_via_metadata_set_not_issue_put(
    monkeypatch,
):
    """恢复 metadata 必须走 `issue metadata set`，issue PUT body 不得携带 metadata。

    真实 multica 服务端的 PUT /api/issues/{id} 会静默忽略 body 里的 metadata
    字段(metadata 是独立 KV 子资源);把恢复 metadata 塞进 PUT body 会导致
    amendment accept 的 authoring recovery read-after-write 永不收敛。
    """
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    item_id = "11111111-1111-4111-8111-111111111111"
    old_metadata = {
        "dag_key": "contracts-platform-resource-schema",
        "kind": "develop",
        "phase": "review",
        "worker_bounce": "15",
        "review_bounce": "3",
        "decision_required": '{"reason_code":"old"}',
        "review_verdict": "reject",
        "review_report_ref": '{"attachment_id":"report-849"}',
        "review_ledger_ref": json.dumps(
            {"attachment_id": "ledger-849", "sha256": "a" * 64}),
        "review_ledger_generation": "review-aiteam-849",
        "review_continuation": '{"authorized_through_round":6}',
        "worker_handoff": '{"schema":"omac.worker-handoff/v1"}',
    }
    contract_ref = {"attachment_id": "contract-850", "sha256": "b" * 64}
    put_bodies = []
    metadata_sets = []
    expected = SimpleNamespace(
        id=item_id, review_generation="amendment-aiteam-850")
    monkeypatch.setattr(
        store, "_publish_payload_comment",
        lambda *_args, **_kwargs: dict(contract_ref))
    monkeypatch.setattr(
        store, "_read_issue_metadata",
        lambda _item_id: ({"id": item_id}, dict(old_metadata)))
    monkeypatch.setattr(
        store, "_put_issue_fields_direct",
        lambda target_id, fields, *, operation: put_bodies.append(
            (target_id, fields, operation)))
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda target_id, key, value: metadata_sets.append(
            (target_id, key, value)))
    monkeypatch.setattr(store, "get_work_item", lambda _item_id: expected)

    result = store.restore_authoring_generation(
        item_id,
        {"objective": "amended contract"},
        "amendment-aiteam-850",
        {"worker": 14, "review": 3, "merge": 0},
    )

    # (c) restore 必须读回并返回最新 WorkItem。
    assert result is expected
    # (b) issue PUT 只负责 issue 字段,body 中不得出现 metadata。
    assert len(put_bodies) == 1
    target_id, fields, operation = put_bodies[0]
    assert target_id == item_id
    assert operation == "authoring-generation recovery"
    assert "metadata" not in fields
    assert fields["status"] == "todo"
    assert fields["assignee_type"] is None
    assert fields["assignee_id"] is None
    assert fields["suppress_run"] is True
    # (a) 恢复 metadata 逐键经 `issue metadata set` 持久化。
    written = {
        key: value for set_id, key, value in metadata_sets
        if set_id == item_id
    }
    assert written["review_generation"] == "amendment-aiteam-850"
    assert written["bounce_baseline"] == {
        "worker": 14, "review": 3, "merge": 0}
    assert written["contract_ref"] == contract_ref
    assert written["phase"] == "authoring"
    assert written["worker_handoff"] == "{}"
    assert written["delivery_identity"] == "{}"
    assert written["review_obligations"] == []
    assert written["review_obligations_ref"] == "{}"
    assert written["review_continuation"] == "{}"
    assert written["review_report_ref"] == "{}"
    assert written["decision_required"] == "{}"
    assert written["review_verdict"] == ""
    assert written["reviewer"] == ""
    # 历史 ledger / 绝对 bounce 计数不允许被恢复重写。
    assert "review_ledger_ref" not in written
    assert "review_ledger_generation" not in written
    assert "worker_bounce" not in written
    assert "review_bounce" not in written


def test_multica_restore_rerun_skips_already_matching_metadata(monkeypatch):
    """restart-safe:metadata 已达目标态时重跑只补齐 issue 字段,不重写 metadata。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    item_id = "11111111-1111-4111-8111-111111111111"
    contract_ref = {"attachment_id": "contract-850", "sha256": "b" * 64}
    restored_metadata = {
        "dag_key": "contracts-platform-resource-schema",
        "kind": "develop",
        "phase": "authoring",
        "review_comment": "",
        "machine_feedback_ref": "{}",
        "review_report_ref": "{}",
        "decision_required": "{}",
        "reviewer_run_baseline": "{}",
        "review_verdict": "",
        "review_subject_digest": "",
        "review_obligations": "[]",
        "review_obligations_ref": "{}",
        "review_continuation": "{}",
        "worker_handoff": "{}",
        "delivery_identity": "{}",
        "review_generation": "amendment-aiteam-850",
        "bounce_baseline": json.dumps(
            {"worker": 14, "review": 3, "merge": 0}),
        "contract_ref": json.dumps(contract_ref),
        "reviewer": "",
        "review_ledger_ref": json.dumps(
            {"attachment_id": "ledger-849", "sha256": "a" * 64}),
        "review_ledger_generation": "review-aiteam-849",
    }
    put_bodies = []
    metadata_sets = []
    monkeypatch.setattr(
        store, "_publish_payload_comment",
        lambda *_args, **_kwargs: dict(contract_ref))
    monkeypatch.setattr(
        store, "_read_issue_metadata",
        lambda _item_id: ({"id": item_id}, dict(restored_metadata)))
    monkeypatch.setattr(
        store, "_put_issue_fields_direct",
        lambda target_id, fields, *, operation: put_bodies.append(fields))
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda target_id, key, value: metadata_sets.append(
            (target_id, key, value)))
    monkeypatch.setattr(
        store, "get_work_item", lambda _item_id: SimpleNamespace(id=item_id))

    store.restore_authoring_generation(
        item_id,
        {"objective": "amended contract"},
        "amendment-aiteam-850",
        {"worker": 14, "review": 3, "merge": 0},
    )

    assert metadata_sets == []
    assert len(put_bodies) == 1
    assert "metadata" not in put_bodies[0]
    assert put_bodies[0]["status"] == "todo"


def test_multica_malformed_empty_decision_value_remains_fail_closed():
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_control_projection({
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_progress",
        "metadata": {
            "dag_key": "develop-a",
            "kind": "develop",
            DECISION_REQUIRED_KEY: "[]",
        },
    }, "ws").work_item

    assert item.decision_required == {
        "schema": "omac.invalid-object-metadata/v1",
        "decoded_type": "list",
        "value": [],
    }
    assert item.requires_decision


def test_multica_preserves_unknown_persisted_issue_facts_for_fail_closed_checks():
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_work_item({
        "id": "issue-1",
        "title": "amendment",
        "description": "shell",
        "status": "todo",
        "created_at": "2026-07-28T00:00:00Z",
        "updated_at": "2026-07-28T00:01:00Z",
        "assignee_id": "agent-1",
        "future_run_fact": {"run_id": "run-1"},
        "metadata": {
            "dag_key": "amend-project-attempt-pristine",
            "kind": "amendment",
            "phase": "authoring",
            "future_execution_fact": False,
        },
    }, "ws")

    assert item.created_at == "2026-07-28T00:00:00Z"
    assert item.updated_at == "2026-07-28T00:01:00Z"
    assert item.platform_assignee_id == "agent-1"
    assert item.unknown_persisted_fields == {
        "issue.future_run_fact": {"run_id": "run-1"},
        "metadata.future_execution_fact": False,
    }


def test_multica_text_file_commands_allow_process_owned_external_file(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def run(args, capture=True):
        calls.append(args)
        path = Path(args[args.index("--description-file") + 1])
        assert path.read_text() == "request body"
        return {"id": "issue-1"}

    monkeypatch.setattr(store, "_run_multica", run)

    store._run_multica_with_text_file(
        ["issue", "create", "--title", "demo"],
        "--description-file",
        "request body",
    )

    assert "--allow-external-file" in calls[0]


def test_multica_description_repair_runs_before_metadata_updates(monkeypatch):
    """紧凑正文必须先落盘，避免旧巨型 description 拖死 metadata API。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    events = []

    monkeypatch.setattr(
        store,
        "_run_multica_with_text_file",
        lambda args, flag, text: events.append(("description", text)),
    )
    monkeypatch.setattr(
        store,
        "_set_metadata",
        lambda item_id, key, value: events.append(("metadata", key)),
    )
    monkeypatch.setattr(
        store,
        "get_work_item",
        lambda item_id: store._issue_to_work_item(
            {
                "id": item_id,
                "title": "t",
                "description": "compact",
                "status": "todo",
                "metadata": {"dag_key": "decompose-p1", "kind": "decompose"},
            },
            "ws",
        ),
    )

    store.update_work_item_metadata(
        "issue-1",
        worker="bob",
        source_refs=[{"label": "acceptance", "issue_id": "issue-a"}],
        description="compact body",
    )

    assert events[0] == ("description", "compact body")
    assert events[1:] == [
        ("metadata", "worker"),
        ("metadata", "source_refs"),
    ]


def test_multica_reset_review_clears_report_ref_without_touching_ledger_or_history(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    report_ref = {"attachment_id": "report-attachment"}
    ledger_ref = {"attachment_id": "ledger-attachment"}
    metadata = {
        REVIEW_REPORT_REF_KEY: report_ref,
        REVIEW_LEDGER_REF_KEY: ledger_ref,
    }
    writes = []

    def set_metadata(item_id, key, value):
        writes.append((item_id, key, value))
        metadata[key] = value

    monkeypatch.setattr(store, "_set_metadata", set_metadata)
    monkeypatch.setattr(
        store, "_publish_payload_comment",
        lambda *_args: pytest.fail("reset_review must not publish attachments"),
    )
    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_review",
        "metadata": metadata,
    })

    store.reset_review("issue-1")

    assert metadata[REVIEW_REPORT_REF_KEY] == "{}"
    assert metadata[REVIEW_LEDGER_REF_KEY] is ledger_ref
    assert ("issue-1", REVIEW_REPORT_REF_KEY, "{}") in writes
    assert not any(key == REVIEW_LEDGER_REF_KEY for _item_id, key, _value in writes)


def test_multica_reset_review_restart_writes_only_remaining_projection(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {
        "review_verdict": "reject",
        "review_comment": "fix it",
        MACHINE_FEEDBACK_REF_KEY: {"attachment_id": "feedback-1"},
        REVIEW_REPORT_REF_KEY: {"attachment_id": "report-1"},
        DECISION_REQUIRED_KEY: {"reason": "review"},
        REVIEWER_RUN_BASELINE_KEY: {"schema": "old"},
        REVIEW_SUBJECT_DIGEST_KEY: "subject-v1",
        PHASE_KEY: "review",
    }
    writes = []
    fail_once = {REVIEW_REPORT_REF_KEY}

    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_review",
        "metadata": metadata,
    })

    def set_metadata(item_id, key, value):
        writes.append((key, value))
        if key in fail_once:
            fail_once.remove(key)
            raise PlatformError("HTTP 503 Service Unavailable")
        metadata[key] = value

    monkeypatch.setattr(store, "_set_metadata", set_metadata)

    with pytest.raises(PlatformError, match="503"):
        store.reset_review("issue-1")
    split = len(writes)

    store.reset_review("issue-1")

    assert writes[:split] == [
        ("review_comment", ""),
        (MACHINE_FEEDBACK_REF_KEY, "{}"),
        (REVIEW_REPORT_REF_KEY, "{}"),
    ]
    assert writes[split:] == [
        (REVIEW_REPORT_REF_KEY, "{}"),
        (DECISION_REQUIRED_KEY, "{}"),
        (REVIEWER_RUN_BASELINE_KEY, "{}"),
        ("review_verdict", ""),
        (REVIEW_SUBJECT_DIGEST_KEY, ""),
        (PHASE_KEY, "authoring"),
    ]


def test_multica_reset_review_missing_clear_keys_writes_nothing(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {}
    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "todo",
        "metadata": metadata,
    })
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda *_args, **_kwargs: pytest.fail("satisfied projection must not write"),
    )

    store.reset_review("issue-1")


_MISSING_METADATA = object()


@pytest.mark.parametrize("key", [
    MACHINE_FEEDBACK_REF_KEY,
    REVIEW_REPORT_REF_KEY,
    DECISION_REQUIRED_KEY,
    REVIEWER_RUN_BASELINE_KEY,
])
@pytest.mark.parametrize(
    "raw",
    [_MISSING_METADATA, None, {}, "{}", "", "null"],
    ids=["missing", "python-null", "dict", "json-dict", "empty-text", "json-null"],
)
def test_multica_reset_review_accepts_all_canonical_object_clear_values(
    monkeypatch, key, raw,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {} if raw is _MISSING_METADATA else {key: raw}
    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "todo",
        "metadata": metadata,
    })
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda *_args, **_kwargs: pytest.fail(
            f"canonical clear value for {key} must not write"),
    )

    store.reset_review("issue-1")


def test_multica_missing_report_ref_with_legacy_report_writes_shadow_tombstone(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {"review_report": {"verdict": "reject"}}
    writes = []
    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "todo",
        "metadata": metadata,
    })

    def set_metadata(item_id, key, value):
        writes.append((key, value))
        metadata[key] = value

    monkeypatch.setattr(store, "_set_metadata", set_metadata)

    store.reset_review("issue-1")

    assert writes == [(REVIEW_REPORT_REF_KEY, "{}")]


@pytest.mark.parametrize("raw", [
    "not-json",
    '{"reason":"manual"}',
    [],
    ["unknown"],
])
def test_multica_non_clear_object_metadata_still_requires_reset(monkeypatch, raw):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {DECISION_REQUIRED_KEY: raw}
    writes = []
    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "todo",
        "metadata": metadata,
    })

    def set_metadata(item_id, key, value):
        writes.append((key, value))
        metadata[key] = value

    monkeypatch.setattr(store, "_set_metadata", set_metadata)

    store.reset_review("issue-1")

    assert writes == [(DECISION_REQUIRED_KEY, "{}")]


@pytest.mark.parametrize(
    ("key", "field"),
    [
        (MACHINE_FEEDBACK_REF_KEY, "machine_feedback_ref"),
        (REVIEW_REPORT_REF_KEY, "review_report_ref"),
        (DECISION_REQUIRED_KEY, "decision_required"),
        (REVIEWER_RUN_BASELINE_KEY, "reviewer_run_baseline"),
    ],
)
@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        {"attachment_id": "fact-1"},
        [],
        ["fact"],
        '"fact"',
        "7",
        "true",
    ],
    ids=[
        "malformed", "object", "empty-list", "list",
        "string-scalar", "number-scalar", "bool-scalar",
    ],
)
def test_multica_non_clear_object_metadata_projects_explicit_fact(key, field, raw):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {key: raw}

    canonical = store._json_metadata(metadata, key)
    item = store._issue_to_control_projection({
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_progress",
        "metadata": {
            "dag_key": "develop-a",
            "kind": "develop",
            **metadata,
        },
    }, "ws").work_item

    assert canonical is not None
    assert getattr(item, field) is not None
    if isinstance(raw, dict):
        assert canonical == raw
    elif raw == "not-json":
        assert canonical == {"raw": raw}
    else:
        assert canonical["schema"] == "omac.invalid-object-metadata/v1"
        assert canonical["decoded_type"]
        assert "value" in canonical


@pytest.mark.parametrize("verdict", ["pass", "reject"])
def test_multica_prepare_same_completed_subject_preserves_review_evidence(
    monkeypatch, verdict,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    baseline = {
        "schema": "omac.reviewer-run-baseline/v1",
        "subject_digest": "subject-v1",
        "target_reviewer": "reviewer",
        "target_agent_id": "agent-1",
        "cutoff_created_at": "2026-08-01T00:00:00Z",
        "generation": "review-1",
        "attempt": 1,
        "baseline_direct_run_ids": [],
    }
    report = {"verdict": verdict, "full_review_completed": True}
    metadata = {
        "dag_key": "develop-a",
        "kind": "develop",
        "review_verdict": verdict,
        "review_report": report,
        REVIEWER_RUN_BASELINE_KEY: baseline,
        REVIEW_SUBJECT_DIGEST_KEY: "subject-v1",
        PHASE_KEY: "review",
    }
    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_review",
        "metadata": metadata,
    })
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda *_args, **_kwargs: pytest.fail(
            "completed same-subject review must not write metadata"),
    )

    prepared = store.prepare_review_cycle("issue-1", "subject-v1")

    assert prepared.review_verdict == verdict
    assert prepared.review_report == report
    assert prepared.reviewer_run_baseline is not None
    assert prepared.review_subject_digest == "subject-v1"
    assert prepared.phase.value == "review"


def test_multica_prepare_same_subject_partial_projection_writes_only_missing(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {
        "dag_key": "develop-a",
        "kind": "develop",
        DECISION_REQUIRED_KEY: {"reason": "stale"},
        REVIEW_SUBJECT_DIGEST_KEY: "subject-v1",
        PHASE_KEY: "authoring",
    }
    writes = []

    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_review",
        "metadata": metadata,
    })

    def set_metadata(item_id, key, value):
        writes.append((key, value))
        metadata[key] = value

    monkeypatch.setattr(store, "_set_metadata", set_metadata)

    prepared = store.prepare_review_cycle("issue-1", "subject-v1")

    assert writes == [
        (DECISION_REQUIRED_KEY, "{}"),
        (PHASE_KEY, "review"),
    ]
    assert prepared.decision_required is None
    assert prepared.review_subject_digest == "subject-v1"
    assert prepared.phase.value == "review"


def test_multica_prepare_review_cycle_writes_cleanup_then_phase_and_subject(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {
        "dag_key": "develop-a",
        "kind": "develop",
        "review_verdict": "pass",
        "review_comment": "old",
        MACHINE_FEEDBACK_REF_KEY: {"attachment_id": "feedback-1"},
        REVIEW_REPORT_REF_KEY: {"attachment_id": "report-1"},
        DECISION_REQUIRED_KEY: {"reason": "old"},
        REVIEWER_RUN_BASELINE_KEY: {"schema": "old"},
        REVIEW_SUBJECT_DIGEST_KEY: "subject-v1",
        PHASE_KEY: "authoring",
    }
    writes = []

    def run(args, capture=True):
        assert args[:2] == ["issue", "get"]
        return {
            "id": "issue-1",
            "title": "review",
            "description": "review",
            "status": "in_review",
            "metadata": metadata,
        }

    def set_metadata(item_id, key, value):
        writes.append((key, value))
        metadata[key] = value

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(store, "_set_metadata", set_metadata)

    prepared = store.prepare_review_cycle("issue-1", "subject-v2")

    assert writes == [
        ("review_comment", ""),
        (MACHINE_FEEDBACK_REF_KEY, "{}"),
        (REVIEW_REPORT_REF_KEY, "{}"),
        (DECISION_REQUIRED_KEY, "{}"),
        (REVIEWER_RUN_BASELINE_KEY, "{}"),
        ("review_verdict", ""),
        (REVIEW_SUBJECT_DIGEST_KEY, "subject-v2"),
        (PHASE_KEY, "review"),
    ]
    assert prepared.phase.value == "review"
    assert prepared.review_subject_digest == "subject-v2"


def test_multica_review_projection_propagates_hard_metadata_error(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    metadata = {
        "review_verdict": "reject",
        "review_comment": "old",
        PHASE_KEY: "review",
    }
    monkeypatch.setattr(store, "_run_multica", lambda *_args, **_kwargs: {
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_review",
        "metadata": metadata,
    })
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PlatformError("HTTP 403 forbidden")),
    )

    with pytest.raises(PlatformError, match="403"):
        store.reset_review("issue-1")


def test_multica_externalizes_machine_feedback_before_bounded_summary(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    errors = [f"error-{index}: {'x' * 80}" for index in range(160)]
    feedback = {
        "schema": "omac.machine-feedback/v1",
        "gate": "machine-gate",
        "error_count": len(errors),
        "errors": errors,
    }
    summary = (
        "Machine gate found 160 errors. Read the complete structured feedback with "
        "`omac work show issue-1 --output json` at `context.machine_feedback`."
    )
    events = []

    def publish(item_id, key, source, suffix):
        events.append(("payload", key, source, suffix))
        return {
            "attachment_id": "attachment-1",
            "sha256": "a" * 64,
            "bytes": len(source.encode("utf-8")),
            "filename": "omac-machine-feedback.json",
        }

    monkeypatch.setattr(store, "_publish_payload_comment", publish)
    monkeypatch.setattr(
        store,
        "_set_metadata",
        lambda item_id, key, value: events.append(("metadata", key, value)),
    )
    monkeypatch.setattr(store, "get_work_item", lambda item_id: SimpleNamespace(id=item_id))

    store.update_work_item_metadata(
        "issue-1",
        machine_feedback=feedback,
        review_comment=summary,
    )

    payload_event = events[0]
    assert payload_event[0:2] == ("payload", "machine-feedback")
    assert len(payload_event[2].encode("utf-8")) > 8192
    metadata_events = [event for event in events if event[0] == "metadata"]
    assert [event[1] for event in metadata_events] == [
        "machine_feedback_ref",
        "review_comment",
    ]
    assert all(
        len(json.dumps(event[2], ensure_ascii=False).encode("utf-8")) <= 8192
        for event in metadata_events
    )


def test_multica_machine_feedback_ref_fails_closed_when_payload_is_missing(
        monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_load_payload_comment", lambda *_args: None)

    with pytest.raises(PlatformError, match="machine feedback attachment"):
        store._issue_to_work_item(
            {
                "id": "issue-1",
                "title": "t",
                "description": "d",
                "status": "todo",
                "metadata": {
                    "dag_key": "decompose-p1",
                    "kind": "decompose",
                    "machine_feedback_ref": json.dumps({
                        "attachment_id": "attachment-1",
                        "sha256": "a" * 64,
                        "bytes": 9000,
                        "filename": "omac-machine-feedback.json",
                    }),
                },
            },
            "ws",
        )


def test_multica_machine_feedback_ref_loads_complete_structured_payload(
        monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    feedback = {
        "schema": "omac.machine-feedback/v1",
        "gate": "machine-gate",
        "error_count": 2,
        "errors": ["first", "second"],
    }
    monkeypatch.setattr(
        store,
        "_load_payload_comment",
        lambda *_args: json.dumps(feedback),
    )

    item = store._issue_to_work_item(
        {
            "id": "issue-1",
            "title": "t",
            "description": "d",
            "status": "todo",
            "metadata": {
                "dag_key": "decompose-p1",
                "kind": "decompose",
                "review_comment": "bounded summary",
                "machine_feedback_ref": json.dumps({
                    "attachment_id": "attachment-1",
                    "sha256": hashlib.sha256(
                        json.dumps(feedback).encode("utf-8")).hexdigest(),
                    "bytes": 9000,
                    "filename": "omac-machine-feedback.json",
                }),
            },
        },
        "ws",
    )

    assert item.review_comment == "bounded summary"
    assert item.machine_feedback == feedback
    assert item.machine_feedback_ref["attachment_id"] == "attachment-1"


def test_multica_description_resolve_timeout_uses_direct_exact_update(monkeypatch):
    """CLI resolver 被巨型正文拖死时，精确 UUID 走同一 API 的幂等 PUT。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    events = []

    def fail_cli(*_args, **_kwargs):
        raise PlatformError("resolve issue: context deadline exceeded")

    monkeypatch.setattr(store, "_run_multica_with_text_file", fail_cli)
    monkeypatch.setattr(
        store,
        "_put_issue_description_direct",
        lambda item_id, description: events.append(
            ("direct-description", item_id, description)),
        raising=False,
    )
    monkeypatch.setattr(
        store,
        "_set_metadata",
        lambda item_id, key, value: events.append(("metadata", key)),
    )
    monkeypatch.setattr(
        store,
        "get_work_item",
        lambda item_id: store._issue_to_work_item(
            {
                "id": item_id,
                "title": "t",
                "description": "compact",
                "status": "todo",
                "metadata": {"dag_key": "decompose-p1", "kind": "decompose"},
            },
            "ws",
        ),
    )

    store.update_work_item_metadata(
        "8e6bd282-6039-41d2-aa00-969a0bf1554a",
        worker="bob",
        description="compact body",
    )

    assert events == [
        (
            "direct-description",
            "8e6bd282-6039-41d2-aa00-969a0bf1554a",
            "compact body",
        ),
        ("metadata", "worker"),
    ]


def test_direct_description_update_keeps_token_out_of_process_args(
        tmp_path, monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"server_url":"https://api.example.test","token":"secret-token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MULTICA_CONFIG_PATH", str(config_path))
    observed = {}

    def run(args, **kwargs):
        observed["args"] = args
        header_path = args[args.index("--header") + 1].removeprefix("@")
        body_path = args[args.index("--data-binary") + 1].removeprefix("@")
        observed["headers"] = Path(header_path).read_text(encoding="utf-8")
        observed["body"] = Path(body_path).read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("omac.engines.multica.subprocess.run", run)

    store._put_issue_description_direct(
        "8e6bd282-6039-41d2-aa00-969a0bf1554a",
        "compact body",
    )

    assert "secret-token" not in " ".join(observed["args"])
    assert "Authorization: Bearer secret-token" in observed["headers"]
    assert '"description": "compact body"' in observed["body"]


def test_confirmed_merge_normalization_is_one_atomic_suppressed_update(
    tmp_path, monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"server_url":"https://api.example.test","token":"secret-token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MULTICA_CONFIG_PATH", str(config_path))
    observed = []

    def run(args, **kwargs):
        body_path = args[args.index("--data-binary") + 1].removeprefix("@")
        observed.append(json.loads(Path(body_path).read_text(encoding="utf-8")))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("omac.engines.multica.subprocess.run", run)

    store.normalize_confirmed_merge(
        "8e6bd282-6039-41d2-aa00-969a0bf1554a")

    assert observed == [{
        "status": "done",
        "assignee_type": None,
        "assignee_id": None,
        "suppress_run": True,
    }]


def test_multica_payload_upload_allows_process_owned_external_files(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return {"id": "issue-1", "assignee_id": "agent-1"}
        if args[:2] == ["issue", "assign"]:
            return {"id": "issue-1", "assignee_id": None}
        return {
            "id": "comment-1",
            "attachments": [{"id": "attachment-1", "filename": "payload.md"}],
        }

    monkeypatch.setattr(store, "_run_multica", run)

    store._publish_payload_comment("issue-1", "deliverable", "payload", ".md")

    assert calls[0] == ["issue", "get", "issue-1", "--output", "json"]
    assert calls[1] == ["issue", "assign", "issue-1", "--unassign"]
    assert "--allow-external-file" in calls[2]


def test_multica_system_comment_unassigns_agent_before_posting(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return {"id": "issue-1", "assignee_id": "agent-1"}
        if args[:2] == ["issue", "assign"]:
            return {"id": "issue-1", "assignee_id": None}
        if args[:3] == ["issue", "comment", "add"]:
            return {"id": "comment-1"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", run)

    store.add_comment("issue-1", "failure details")

    assert calls[0] == ["issue", "get", "issue-1", "--output", "json"]
    assert calls[1] == ["issue", "assign", "issue-1", "--unassign"]
    assert calls[2][:3] == ["issue", "comment", "add"]


def test_multica_system_comment_skips_unassign_when_issue_has_no_assignee(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return {"id": "issue-1", "assignee_id": None}
        if args[:3] == ["issue", "comment", "add"]:
            return {"id": "comment-1"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", run)

    store.add_comment("issue-1", "failure details")

    assert calls[0] == ["issue", "get", "issue-1", "--output", "json"]
    assert len(calls) == 2
    assert calls[1][:3] == ["issue", "comment", "add"]


def test_multica_list_work_items_is_scoped_to_configured_project(monkeypatch):
    store = MulticaStore(EngineConfig(
        engine_type="multica",
        workspace_id="ws",
        project_id="project-1",
    ))
    calls = []

    def run(args, capture=True):
        calls.append(args)
        return []

    monkeypatch.setattr(store, "_run_multica", run)

    assert store.list_work_items("ws") == []
    assert "--project" in calls[0]
    assert calls[0][calls[0].index("--project") + 1] == "project-1"


def test_multica_batch_control_observation_projects_pages_without_hydration(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(
        engine_type="multica", workspace_id="ws", project_id="project-1"))
    calls = []
    issues = [
        {
            "id": f"issue-{index}", "title": "t", "description": "d",
            "status": "todo", "metadata": {"dag_key": f"node-{index}"},
        }
        for index in range(101)
    ]

    def run(args, capture=True):
        calls.append(args)
        assert args[:2] == ["issue", "list"]
        assert args[args.index("--project") + 1] == "project-1"
        assert args[args.index("--limit") + 1] == "10"
        offset = int(args[args.index("--offset") + 1])
        return issues[offset:offset + 10]

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(
        store,
        "hydrate_work_item_evidence",
        lambda *_args: pytest.fail("batch control observation must not hydrate"),
    )

    observed = store.observe_work_item_controls(["issue-100", "issue-0"])

    assert list(observed) == ["issue-100", "issue-0"]
    assert observed["issue-100"].work_item.dag_key == "node-100"
    assert len(calls) == 11


def test_multica_batch_control_observation_falls_back_for_missing_list_id(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(
        engine_type="multica", workspace_id="ws", project_id="project-1"))
    calls = []

    def run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            return []
        if args[:2] == ["issue", "get"]:
            return {
                "id": "issue-1", "title": "t", "description": "d",
                "status": "todo", "metadata": {"dag_key": "node-1"},
            }
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", run)

    observed = store.observe_work_item_controls(["issue-1"])

    assert list(observed) == ["issue-1"]
    assert [call[:2] for call in calls] == [["issue", "list"], ["issue", "get"]]


def test_multica_empty_review_verdict_is_read_as_missing():
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_work_item(
        {
            "id": "issue-1",
            "title": "t",
            "description": "d",
            "status": "in_review",
            "metadata": {
                "dag_key": "plan-p1",
                "kind": "plan",
                "phase": "authoring",
                "review_verdict": "",
                "review_comment": "",
                "review_report": "{}",
            },
        },
        "ws",
    )

    assert item.review_verdict is None
    assert item.review_comment is None


def test_multica_issue_identifier_is_exposed_on_work_item():
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_work_item(
        {
            "id": "issue-1",
            "identifier": "AITEAM-762",
            "title": "t",
            "description": "d",
            "status": "todo",
            "metadata": {"dag_key": "node-a", "kind": "develop"},
        },
        "ws",
    )

    assert item.identifier == "AITEAM-762"


def test_multica_review_report_source_writes_ref_without_full_report_metadata(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    writes = []

    monkeypatch.setattr(store, "_set_metadata", lambda item_id, key, value: writes.append((key, value)))
    monkeypatch.setattr(
        store,
        "_publish_payload_comment",
        lambda item_id, label, source, suffix: {
            "comment_id": "c1",
            "attachment_id": "a1",
            "sha256": "s1",
            "bytes": len(source.encode("utf-8")),
            "filename": f"omac-{label}{suffix}",
        },
    )
    monkeypatch.setattr(
        store,
        "get_work_item",
        lambda item_id: store._issue_to_work_item(
            {
                "id": item_id,
                "title": "t",
                "description": "d",
                "status": "in_review",
                "metadata": {"dag_key": "plan-p1", "kind": "plan", "phase": "review"},
            },
            "ws",
        ),
    )

    store.update_work_item_metadata(
        "issue-1",
        review_report={"summary": "large reviewer report"},
        review_report_source="summary: large reviewer report\n",
    )

    assert "review_report" not in [key for key, _ in writes]
    assert ("review_report_ref", {
        "comment_id": "c1",
        "attachment_id": "a1",
        "sha256": "s1",
        "bytes": len("summary: large reviewer report\n".encode("utf-8")),
        "filename": "omac-review-report.yaml",
    }) in writes


def test_multica_review_ledger_and_obligations_roundtrip_from_metadata(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(
        store,
        "_load_payload_comment",
        lambda item_id, label, ref: (
            "schema: omac.review-ledger/v1\ncycles: []\nblockers: []\n"
            if label == "review-ledger" else None),
    )

    item = store._issue_to_work_item({
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_review",
        "metadata": {
            "dag_key": "review-1",
            "kind": "decompose",
            "phase": "review",
            "review_obligations": '[{"obligation_id":"dimension:authority"}]',
            "review_ledger_ref": '{"attachment_id":"ledger-1"}',
        },
    }, "ws")

    assert item.review_obligations == [
        {"obligation_id": "dimension:authority"}]
    assert item.review_ledger == {
        "schema": "omac.review-ledger/v1", "cycles": [], "blockers": []}
    assert item.review_ledger_ref == {"attachment_id": "ledger-1"}


def test_multica_review_obligations_use_attachment_ref_and_roundtrip_above_metadata_limit(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    writes = []
    obligations = [{
        "obligation_id": f"acceptance-responsibility:{index}",
        "requirement": "review compact matrix",
        "before": [{"flow_id": f"UJ-{index}", "missing_business_action_ids": []}],
        "after": [{"flow_id": f"UJ-{index}", "missing_business_action_ids": []}],
    } for index in range(145)]
    source = yaml.safe_dump(obligations, allow_unicode=True, sort_keys=False)
    assert len(json.dumps(obligations).encode("utf-8")) > 8 * 1024

    monkeypatch.setattr(
        store, "_set_metadata", lambda item_id, key, value: writes.append((key, value)))
    monkeypatch.setattr(
        store, "_publish_payload_comment",
        lambda item_id, label, content, suffix: {
            "comment_id": "obligation-comment",
            "attachment_id": "obligation-attachment",
            "sha256": "obligation-sha",
            "bytes": len(content.encode("utf-8")),
            "filename": f"omac-{label}{suffix}",
        },
    )
    monkeypatch.setattr(
        store, "get_work_item",
        lambda item_id: store._issue_to_work_item({
            "id": item_id,
            "title": "review",
            "description": "review",
            "status": "in_review",
            "metadata": {
                "dag_key": "amend-1", "kind": "amendment", "phase": "review",
                "review_obligations_ref": {"attachment_id": "obligation-attachment"},
            },
        }, "ws"),
    )
    monkeypatch.setattr(
        store, "_load_payload_comment",
        lambda item_id, label, ref: source if label == "review-obligations" else None,
    )

    item = store.update_work_item_metadata("issue-1", review_obligations=obligations)

    assert [key for key, _ in writes] == ["review_obligations_ref"]
    assert writes[0][1]["attachment_id"] == "obligation-attachment"
    assert writes[0][1]["bytes"] == len(source.encode("utf-8"))
    assert item.review_obligations == obligations
    comment = store._payload_comment(
        "review-obligations", "obligation-sha", len(source.encode("utf-8")),
        "omac-review-obligations.yaml")
    assert "`review_obligations_ref`" in comment


def test_multica_reads_legacy_inline_review_obligations_when_no_ref_exists():
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_work_item({
        "id": "issue-1",
        "title": "review",
        "description": "review",
        "status": "in_review",
        "metadata": {
            "dag_key": "review-1",
            "kind": "decompose",
            "phase": "review",
            "review_obligations": '[{"obligation_id":"dimension:authority"}]',
        },
    }, "ws")

    assert item.review_obligations == [{"obligation_id": "dimension:authority"}]
    assert item.review_obligations_ref is None


@pytest.mark.parametrize("ledger_text", [None, "not: [valid", "- blocker-a\n"])
def test_multica_review_ledger_ref_fails_closed_when_payload_is_invalid(
    monkeypatch, ledger_text
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(
        store,
        "_load_payload_comment",
        lambda item_id, label, ref: ledger_text,
    )

    with pytest.raises(PlatformError, match="review ledger"):
        store._issue_to_work_item({
            "id": "issue-1",
            "title": "review",
            "description": "review",
            "status": "in_review",
            "metadata": {
                "dag_key": "review-1",
                "kind": "decompose",
                "phase": "review",
                "review_ledger_ref": '{"attachment_id":"ledger-1"}',
            },
        }, "ws")


def test_multica_writes_review_evidence_before_terminal_verdict(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    events = []

    monkeypatch.setattr(
        store,
        "_publish_payload_comment",
        lambda item_id, label, source, suffix: (
            events.append(("publish", label))
            or {"attachment_id": f"{label}-attachment"}),
    )
    monkeypatch.setattr(
        store,
        "_set_metadata",
        lambda item_id, key, value: events.append(("metadata", key)),
    )
    monkeypatch.setattr(
        store,
        "get_work_item",
        lambda item_id: store._issue_to_work_item({
            "id": item_id,
            "title": "review",
            "description": "review",
            "status": "in_review",
            "metadata": {"dag_key": "review-1", "kind": "decompose"},
        }, "ws"),
    )

    store.update_work_item_metadata(
        "issue-1",
        review_report_source="full_review_completed: true\n",
        review_ledger_source=(
            "schema: omac.review-ledger/v1\ncycles: []\nblockers: []\n"),
        review_verdict="reject",
    )

    assert events.index(("metadata", "review_report_ref")) < events.index(
        ("metadata", "review_verdict"))
    assert events.index(("metadata", "review_ledger_ref")) < events.index(
        ("metadata", "review_verdict"))


def test_multica_payload_ref_downloads_known_attachment_without_comment_thread(monkeypatch):
    """ref 已含 attachment_id 时直接取附件，评论线程超时不应拖死轮询。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args, capture=True):
        calls.append(args)
        if args[:2] == ["attachment", "download"]:
            output_dir = Path(args[args.index("--output-dir") + 1])
            (output_dir / "review.yaml").write_text("verdict: reject\n")
            return None
        if args[:3] == ["issue", "comment", "list"]:
            raise PlatformError("Request timed out: server did not respond")
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    content = store._load_payload_comment("issue-1", "review-report", {
        "comment_id": "comment-1",
        "attachment_id": "attachment-1",
        "filename": "review.yaml",
    })

    assert content == "verdict: reject\n"
    assert not any(args[:3] == ["issue", "comment", "list"] for args in calls)


def test_multica_verification_download_rejects_declared_sha_mismatch(monkeypatch):
    """verification ref 的声明摘要不能替代实际下载字节摘要。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    def fake_run(args, capture=True):
        if args[:2] == ["attachment", "download"]:
            output_dir = Path(args[args.index("--output-dir") + 1])
            (output_dir / "verification.yaml").write_text("commands: []\n")
            return None
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    with pytest.raises(PlatformError, match="sha|SHA|digest"):
        store._load_payload_comment("issue-1", "verification", {
            "attachment_id": "attachment-1",
            "filename": "verification.yaml",
            "sha256": "0" * 64,
        })


def test_multica_observes_verification_platform_identity_and_actual_bytes(
    monkeypatch,
):
    """Controller seal 使用 comment/attachment 平台事实，不读取 Agent env。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    body = b"commands:\n  - command: pytest\n"
    sha = __import__("hashlib").sha256(body).hexdigest()

    def fake_run(args, capture=True):
        if args[:3] == ["issue", "comment", "list"]:
            return [{
                "id": "comment-1",
                "attachments": [{
                    "id": "attachment-1",
                    "filename": "verification.yaml",
                    "uploader_type": "agent",
                    "uploader_id": "agent-1",
                    "task_id": "run-1",
                    "created_at": "2026-07-30T01:00:00Z",
                }],
            }]
        if args[:2] == ["attachment", "download"]:
            output_dir = Path(args[args.index("--output-dir") + 1])
            (output_dir / "verification.yaml").write_bytes(body)
            return None
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    observed = store.observe_verification_attachment("issue-1", {
        "comment_id": "comment-1",
        "attachment_id": "attachment-1",
        "filename": "verification.yaml",
        "sha256": sha,
    })

    assert observed.content == body
    assert observed.sha256 == sha
    assert observed.uploader_id == "agent-1"
    assert observed.task_id == "run-1"


def test_multica_environment_run_ids_are_not_authenticated_submit_identity(
    monkeypatch,
):
    """Agent 可覆盖的环境变量不能暴露为 Store 的认证身份 API。"""
    monkeypatch.setenv("MULTICA_AGENT_ID", "agent-1")
    monkeypatch.setenv("MULTICA_AGENT_NAME", "alice")
    monkeypatch.setenv("MULTICA_TASK_ID", "run-old")
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    assert not hasattr(store, "current_submission_identity")


def test_multica_project_rules_are_uploaded_and_read_through_ref(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    writes = []
    rules = "## Project rules\n\n- Preserve compatibility.\n"

    monkeypatch.setattr(
        store, "_set_metadata",
        lambda item_id, key, value: writes.append((key, value)),
    )
    monkeypatch.setattr(
        store,
        "_publish_payload_comment",
        lambda item_id, label, source, suffix: {
            "comment_id": "c-rules",
            "attachment_id": "a-rules",
            "sha256": "rules-sha",
            "bytes": len(source.encode("utf-8")),
            "filename": f"omac-{label}{suffix}",
        },
    )
    monkeypatch.setattr(
        store,
        "get_work_item",
        lambda item_id: store._issue_to_work_item(
            {
                "id": item_id,
                "title": "t",
                "description": "d",
                "status": "in_review",
                "metadata": {
                    "dag_key": "plan-p1",
                    "kind": "plan",
                    "phase": "review",
                    "project_rules_ref": {
                        "comment_id": "c-rules",
                        "attachment_id": "a-rules",
                    },
                },
            },
            "ws",
        ),
    )
    monkeypatch.setattr(
        store,
        "_load_payload_comment",
        lambda item_id, key, ref: rules if key == "project-rules" else None,
    )

    item = store.update_work_item_metadata("issue-1", project_rules=rules)

    assert writes == [("project_rules_ref", {
        "comment_id": "c-rules",
        "attachment_id": "a-rules",
        "sha256": "rules-sha",
        "bytes": len(rules.encode("utf-8")),
        "filename": "omac-project-rules.md",
    })]
    assert item.project_rules == rules
    assert item.project_rules_ref["attachment_id"] == "a-rules"


def test_multica_plan_delivery_does_not_write_refs_when_second_upload_fails(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    writes = []
    uploads = []

    monkeypatch.setattr(
        store, "_set_metadata",
        lambda item_id, key, value: writes.append((key, value)),
    )

    def publish(item_id, label, source, suffix):
        uploads.append(label)
        if label == "project-rules":
            raise RuntimeError("upload failed")
        return {"comment_id": "c1", "attachment_id": "a1"}

    monkeypatch.setattr(store, "_publish_payload_comment", publish)

    with pytest.raises(RuntimeError, match="upload failed"):
        store.update_work_item_metadata(
            "issue-1",
            deliverable="# Design\n",
            project_rules="## Project rules\n",
        )

    assert uploads == ["deliverable", "project-rules"]
    assert writes == []


def test_multica_set_node_contract_writes_ref_without_full_contract_metadata(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    writes = []
    published = []

    monkeypatch.setattr(store, "_set_metadata", lambda item_id, key, value: writes.append((key, value)))
    monkeypatch.setattr(
        store,
        "_publish_payload_comment",
        lambda item_id, label, source, suffix: (
            published.append((label, source, suffix)) or {
                "comment_id": "c1",
                "attachment_id": "a1",
                "sha256": "s1",
                "bytes": len(source.encode("utf-8")),
                "filename": f"omac-{label}{suffix}",
            }
        ),
    )

    store.set_node_contract("issue-1", {
        "objective": "实现很长的自然语言目标",
        "verification_commands": ["pytest -q"],
    })

    assert "contract" not in [key for key, _ in writes]
    assert writes == [("contract_ref", {
        "comment_id": "c1",
        "attachment_id": "a1",
        "sha256": "s1",
        "bytes": published[0][1].encode("utf-8").__len__(),
        "filename": "omac-contract.yaml",
    })]
    assert published[0][0] == "contract"
    assert published[0][2] == ".yaml"
    assert "实现很长的自然语言目标" in published[0][1]


def test_multica_contract_attachment_preserves_consumes_tristate(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    payloads = []
    monkeypatch.setattr(store, "_set_metadata", lambda *_args: None)
    monkeypatch.setattr(
        store, "_publish_payload_comment",
        lambda _item, _label, source, _suffix: (
            payloads.append(yaml.safe_load(source)) or {"sha256": "s"}),
    )
    base = dict(
        evidence_mode=EvidenceMode.FIXTURE,
        produces=[ProducedArtifact("tooling-package")],
    )

    store.set_node_contract("legacy", Contract(**base))
    store.set_node_contract("none", Contract(**base, consumes=[]))

    assert "consumes" not in payloads[0]
    assert payloads[1]["consumes"] == []


def test_multica_done_contract_publish_keeps_issue_unassigned_and_does_not_start_run(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    writes = []
    issue = {
        "id": "issue-1", "status": "done", "assignee_id": None,
        "metadata": {},
    }

    def run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return dict(issue)
        if args[:3] == ["issue", "comment", "add"]:
            return {
                "id": "comment-1",
                "attachments": [{"id": "attachment-1", "filename": "contract.yaml"}],
            }
        raise AssertionError(f"unexpected multica command: {args}")

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(
        store, "_set_metadata",
        lambda item_id, key, value: writes.append((key, value)),
    )

    store.set_node_contract("issue-1", {"acceptance_claims": ["UJ-BOOTSTRAP"]})

    assert issue["status"] == "done"
    assert issue["assignee_id"] is None
    assert not any(command[:3] == ["issue", "assign", "issue-1"] for command in calls)
    assert not any("run" in command or "rerun" in command for command in calls)
    assert writes[0][0] == "contract_ref"
    assert writes[0][1]["sha256"]


def test_multica_source_refs_are_small_structured_metadata(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    writes = []
    published = []

    monkeypatch.setattr(store, "_set_metadata", lambda item_id, key, value: writes.append((key, value)))
    monkeypatch.setattr(
        store,
        "_publish_payload_comment",
        lambda item_id, label, source, suffix: published.append((label, source, suffix)),
    )
    monkeypatch.setattr(
        store,
        "get_work_item",
        lambda item_id: store._issue_to_work_item(
            {
                "id": item_id,
                "title": "t",
                "description": "d",
                "status": "in_progress",
                "metadata": {
                    "dag_key": "a",
                    "kind": "develop",
                    "source_refs": (
                        '[{"label":"设计方案","issue_id":"plan-1",'
                        '"url":"https://multica.ai/i/plan-1"}]'
                    ),
                },
            },
            "ws",
        ),
    )

    item = store.update_work_item_metadata(
        "issue-1",
        source_refs=[{"label": "设计方案", "issue_id": "plan-1",
                      "url": "https://multica.ai/i/plan-1"}],
    )

    assert writes == [("source_refs", [{"label": "设计方案", "issue_id": "plan-1",
                                        "url": "https://multica.ai/i/plan-1"}])]
    assert published == []
    assert item.source_refs == [{"label": "设计方案", "issue_id": "plan-1",
                                 "url": "https://multica.ai/i/plan-1"}]


def test_multica_reads_contract_from_ref_before_legacy_inline(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(
        store,
        "_load_payload_comment",
        lambda item_id, key, ref: "objective: 来自 ref\nverification_commands:\n  - pytest -q\n",
    )

    item = store._issue_to_work_item(
        {
            "id": "issue-1",
            "title": "t",
            "description": "d",
            "status": "todo",
            "metadata": {
                "dag_key": "node-a",
                "kind": "develop",
                "contract_ref": {"comment_id": "c1"},
                "contract": '{"objective":"旧 inline"}',
            },
        },
        "ws",
    )

    assert item.contract["objective"] == "来自 ref"
    assert item.contract["verification_commands"] == ["pytest -q"]


@pytest.mark.parametrize(("phase", "status"), [
    (TaskPhase.AUTHORING, "todo"),
    (TaskPhase.REVIEW, "in_review"),
])
def test_submit_fails_closed_when_deferred_multica_contract_body_is_missing(
    tmp_path, monkeypatch, phase, status,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    projection = store._issue_to_control_projection({
        "id": "issue-1",
        "title": "develop",
        "description": "develop",
        "status": status,
        "metadata": {
            "dag_key": "develop-a",
            "kind": "develop",
            "phase": phase.value,
            "contract_ref": {
                "comment_id": "contract-comment",
                "attachment_id": "contract-attachment",
            },
        },
    }, "ws")
    monkeypatch.setattr(
        store, "observe_work_item_control", lambda _item_id: projection)
    monkeypatch.setattr(store, "_load_payload_comment", lambda *_args: None)
    monkeypatch.setattr(store, "update_work_item_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "update_status", lambda *_args, **_kwargs: None)

    if phase is TaskPhase.AUTHORING:
        verification = tmp_path / "verification.yaml"
        verification.write_text("{}")
        call = lambda: dispatch_mod.submit(
            store,
            "issue-1",
            pr_url="https://example.test/pr/42",
            verification_file=str(verification),
        )
    else:
        report = tmp_path / "report.yaml"
        report.write_text("full_review_completed: true\n")
        call = lambda: dispatch_mod.submit(
            store,
            "issue-1",
            verdict="pass",
            report_file=str(report),
        )

    with pytest.raises(PlatformError, match="contract"):
        call()


def test_multica_readback_keeps_explicit_null_consumes_invalid(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(
        store, "_load_payload_comment",
        lambda *_args: "evidence_mode: fixture\nconsumes: null\n",
    )

    item = store._issue_to_work_item({
        "id": "issue-1", "title": "t", "description": "d", "status": "todo",
        "metadata": {
            "dag_key": "node-a", "kind": "develop",
            "contract_ref": {"comment_id": "c1"},
        },
    }, "ws")

    assert item.contract["consumes"] is None
    assert responsibility_summary(item.contract)["input_policy"] == "invalid"
    assert responsibility_summary(
        _load_contract(item.contract))["input_policy"] == "invalid"


@pytest.mark.parametrize("status", ["in_progress", "in_review"])
def test_multica_get_work_item_does_not_infer_issue_state_from_unbound_runs(
    monkeypatch, status,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return {
                "id": "issue-1",
                "title": "t",
                "description": "d",
                "status": status,
                "metadata": {
                    "dag_key": "node-a", "kind": "develop",
                    "phase": "review" if status == "in_review" else "authoring",
                },
            }
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    item = store.get_work_item("issue-1")

    assert item.status == (
        WorkItemStatus.IN_REVIEW
        if status == "in_review" else WorkItemStatus.IN_PROGRESS)
    assert item.agent_run_failed is False
    assert item.agent_run_finished_without_submit is False
    assert calls == [["issue", "get", "issue-1", "--output", "json"]]


def test_multica_runtime_wake_does_not_rerun_active_direct_run(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{
                "id": "run-active", "status": "running", "kind": "direct",
                "created_at": "2026-07-27T01:00:00Z",
            }]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert calls == [["issue", "runs", "issue-1", "--output", "json"]]


def test_multica_get_work_item_does_not_treat_cancelled_run_as_worker_failure(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    def fake_run(args):
        if args[:2] == ["issue", "get"]:
            return {
                "id": "issue-1",
                "title": "t",
                "description": "d",
                "status": "in_progress",
                "metadata": {"dag_key": "node-a", "kind": "develop"},
            }
        if args[:2] == ["issue", "runs"]:
            return [
                {"id": "run-1", "status": "cancelled", "created_at": "2026-07-09T08:35:23Z"},
            ]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    item = store.get_work_item("issue-1")

    assert item.status == WorkItemStatus.IN_PROGRESS


def test_multica_runtime_reruns_existing_cancelled_assignment(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [
                {"id": "run-1", "status": "cancelled", "kind": "direct",
                 "created_at": "2026-07-09T08:35:23Z"},
            ]
        if args[:2] == ["issue", "rerun"]:
            return {"id": "run-2", "status": "queued"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    from omac.engines.multica import MulticaRuntime
    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert ["issue", "rerun", "issue-1", "--output", "json"] in calls


def test_multica_runtime_reruns_failed_direct_run_once(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{
                "id": "run-failed", "status": "failed", "kind": "direct",
                "created_at": "2026-07-27T01:00:00Z",
            }]
        if args[:2] == ["issue", "rerun"]:
            return {"id": "run-retry", "status": "queued"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert calls.count([
        "issue", "rerun", "issue-1", "--output", "json",
    ]) == 1


def test_multica_runtime_accepts_rerun_created_before_response_failure(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-expected")
    observations = iter([
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [
            {
                "id": "run-failed", "status": "failed", "kind": "direct",
                "agent_id": "agent-old",
            },
            {
                "id": "run-retry", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
                "retry_of_task_id": "run-failed",
                "attribution": {"evidence": {"kind": "rerun"}},
            },
        ],
    ])

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            raise PlatformError("rerun response unavailable")
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    runtime.wake("issue-1", "alice", "worker")

    assert calls.count([
        "issue", "rerun", "issue-1", "--output", "json",
    ]) == 1


def test_multica_runtime_accepts_single_fresh_run_before_response_failure(
    monkeypatch,
):
    """空 body rerun 创建的唯一新 direct Run 无 lineage 也可证明已生效。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    rerun_error = PlatformError("rerun response unavailable")
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-expected")
    observations = iter([
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [
            {
                "id": "run-failed", "status": "failed", "kind": "direct",
                "agent_id": "agent-old",
            },
            {
                "id": "run-fresh", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
                "attribution": {"evidence": {"kind": "rerun"}},
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            raise rerun_error
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    runtime.wake("issue-1", "alice", "worker")


def test_multica_runtime_rejects_non_rerun_run_after_response_failure(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    rerun_error = PlatformError("rerun response unavailable")
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-expected")
    observations = iter([
        [{"id": "run-failed", "status": "failed", "kind": "direct"}],
        [{"id": "run-failed", "status": "failed", "kind": "direct"}],
        [
            {"id": "run-failed", "status": "failed", "kind": "direct"},
            {
                "id": "run-assignment", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
                "attribution": {"evidence": {"kind": "issue_assignment"}},
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            raise rerun_error
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    with pytest.raises(PlatformError) as exc_info:
        runtime.wake("issue-1", "alice", "worker")

    assert exc_info.value is rerun_error


def test_multica_runtime_accepts_expected_run_after_not_assigned_rerun(
    monkeypatch,
):
    """not-assigned 仅在观察到目标 Agent 的关联新 Run 后才可收敛。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-expected")
    observations = iter([
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [
            {
                "id": "run-failed", "status": "failed", "kind": "direct",
                "agent_id": "agent-old",
            },
            {
                "id": "run-retry", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
                "retry_of_task_id": "run-failed",
                "attribution": {"evidence": {"kind": "rerun"}},
            },
        ],
    ])

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            raise PlatformError(
                "Invalid request: issue is not assigned to an agent or squad")
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    runtime.wake("issue-1", "alice", "worker")

    assert calls.count([
        "issue", "rerun", "issue-1", "--output", "json",
    ]) == 1


def test_multica_runtime_accepts_real_server_rerun_attribution(monkeypatch):
    """真实 multica 服务端把 rerun 派发标记为 issue_assignment。

    reviewer 续跑的严格 read-back 若只接受 trigger kind "rerun"，在真实
    服务端上永远失败（生产现场：hermes-architect 续跑被拦，reason=
    "rerun is not the expected fresh direct Run"）。Run 身份已由 rerun
    响应返回的精确 id + kind=direct + agent 匹配证明，触发标记应同时
    接受服务端实际产出的 issue_assignment。
    """
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-expected")
    observations = iter([
        [{
            "id": "run-done", "status": "completed", "kind": "direct",
            "agent_id": "agent-expected",
        }],
        [
            {
                "id": "run-done", "status": "completed", "kind": "direct",
                "agent_id": "agent-expected",
            },
            {
                "id": "run-new", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
                "attribution": {"evidence": {
                    "kind": "issue_assignment", "ref_id": "issue-1"}},
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            return {"id": "run-new", "agent_id": "agent-expected"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    runtime.wake("issue-1", "alice", "reviewer")


def test_multica_runtime_rejects_unattributed_rerun_run(monkeypatch):
    """没有触发标记的 Run 仍须 fail-closed，不接受无归因派发。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-expected")
    observations = iter([
        [{
            "id": "run-done", "status": "completed", "kind": "direct",
            "agent_id": "agent-expected",
        }],
        [
            {
                "id": "run-done", "status": "completed", "kind": "direct",
                "agent_id": "agent-expected",
            },
            {
                "id": "run-new", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            return {"id": "run-new", "agent_id": "agent-expected"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    with pytest.raises(PlatformError):
        runtime.wake("issue-1", "alice", "reviewer")


def test_multica_runtime_accepts_parented_rerun_before_response_failure(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-expected")
    observations = iter([
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [{
            "id": "run-failed", "status": "failed", "kind": "direct",
            "agent_id": "agent-old",
        }],
        [
            {
                "id": "run-failed", "status": "failed", "kind": "direct",
                "agent_id": "agent-old",
            },
            {
                "id": "run-retry", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
                "parent_task_id": "run-failed",
                "attribution": {"evidence": {"kind": "rerun"}},
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            raise PlatformError("rerun response unavailable")
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    runtime.wake("issue-1", "alice", "worker")


def test_multica_runtime_preserves_rerun_error_for_wrong_agent_run(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    rerun_error = PlatformError("rerun response unavailable")
    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-expected")
    observations = iter([
        [{"id": "run-failed", "status": "failed", "kind": "direct"}],
        [{"id": "run-failed", "status": "failed", "kind": "direct"}],
        [
            {"id": "run-failed", "status": "failed", "kind": "direct"},
            {
                "id": "run-other", "status": "queued", "kind": "direct",
                "agent_id": "agent-other",
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            raise rerun_error
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    with pytest.raises(PlatformError) as exc_info:
        runtime.wake("issue-1", "alice", "worker")

    assert exc_info.value is rerun_error


def test_multica_runtime_preserves_rerun_error_for_unparented_concurrent_runs(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    rerun_error = PlatformError("rerun response unavailable")
    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-expected")
    observations = iter([
        [{"id": "run-failed", "status": "failed", "kind": "direct"}],
        [{"id": "run-failed", "status": "failed", "kind": "direct"}],
        [
            {"id": "run-failed", "status": "failed", "kind": "direct"},
            {
                "id": "run-expected", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
            },
            {
                "id": "run-other", "status": "queued", "kind": "direct",
                "agent_id": "agent-expected",
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            raise rerun_error
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    with pytest.raises(PlatformError) as exc_info:
        runtime.wake("issue-1", "alice", "worker")

    assert exc_info.value is rerun_error


def test_multica_runtime_preserves_rerun_error_when_no_run_was_created(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    rerun_error = PlatformError("Invalid request: issue has no assignee")

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{"id": "run-failed", "status": "failed", "kind": "direct"}]
        if args[:2] == ["issue", "rerun"]:
            raise rerun_error
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    with pytest.raises(PlatformError, match="issue has no assignee") as exc_info:
        runtime.wake("issue-1", "alice", "worker")

    assert exc_info.value is rerun_error
    assert calls.count([
        "issue", "runs", "issue-1", "--output", "json",
    ]) == 3
    assert calls.count([
        "issue", "rerun", "issue-1", "--output", "json",
    ]) == 1
    assert not any(args[:2] == ["issue", "assign"] for args in calls)


def test_multica_runtime_preserves_rerun_error_when_observation_fails(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    rerun_error = PlatformError("rerun response unavailable")
    observations = 0

    def fake_run(args):
        nonlocal observations
        if args[:2] == ["issue", "runs"]:
            observations += 1
            if observations < 3:
                return [{
                    "id": "run-failed", "status": "failed", "kind": "direct",
                }]
            raise PlatformError("run observation unavailable")
        if args[:2] == ["issue", "rerun"]:
            raise rerun_error
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    with pytest.raises(PlatformError) as exc_info:
        runtime.wake("issue-1", "alice", "worker")

    assert exc_info.value is rerun_error


def test_multica_runtime_cancel_interrupts_active_direct_run(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{"id": "task-active", "kind": "direct", "status": "running"}]
        if args[:2] == ["issue", "cancel-task"]:
            return {"id": "task-active", "status": "cancelled"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    assert runtime.cancel("issue-1") is True
    assert [
        "issue", "cancel-task", "task-active",
        "--issue", "issue-1", "--output", "json",
    ] in calls
    assert not any(args[:2] == ["issue", "assign"] for args in calls)


def test_multica_runtime_reports_active_direct_run_without_cancelling(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{"id": "task-active", "kind": "direct", "status": "running"}]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    assert runtime.is_active("issue-1") is True
    assert calls == [["issue", "runs", "issue-1", "--output", "json"]]


def test_multica_runtime_active_checks_every_run_not_latest_direct(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(store, "_run_multica", lambda _args: [
        {
            "id": "old-active-direct", "kind": "direct", "status": "running",
            "created_at": "2026-07-28T01:00:00Z",
        },
        {
            "id": "new-completed-direct", "kind": "direct", "status": "completed",
            "created_at": "2026-07-28T02:00:00Z",
        },
    ])

    assert runtime.is_active("issue-1") is True


def test_multica_runtime_active_includes_comment_and_indirect_runs(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(store, "_run_multica", lambda _args: [
        {"id": "comment-active", "kind": "comment", "status": "pending"},
        {"id": "indirect-active", "kind": "indirect", "status": "dispatching"},
    ])

    assert runtime.is_active("issue-1") is True


def test_multica_runtime_wake_does_not_rerun_active_comment_run(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [
                {"id": "comment-active", "status": "running", "kind": "comment"},
                {"id": "direct-old", "status": "completed", "kind": "direct"},
            ]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert not any(args[:2] == ["issue", "rerun"] for args in calls)


def test_multica_runtime_does_not_rerun_completed_comment(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    monkeypatch.setattr(store, "_run_multica", lambda args: (
        calls.append(args) or [{
            "id": "comment-completed", "status": "completed", "kind": "comment",
        }]
    ))

    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert calls == [["issue", "runs", "issue-1", "--output", "json"]]


def test_multica_runtime_observes_eventually_visible_active_run(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    sleeps = []
    observations = iter([
        [{"id": "direct-old", "status": "completed", "kind": "direct"}],
        [{"id": "direct-old", "status": "completed", "kind": "direct"}],
        [
            {"id": "comment-active", "status": "running", "kind": "comment"},
            {"id": "direct-old", "status": "completed", "kind": "direct"},
        ],
    ])

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=3,
        active_observation_interval=0.25, sleeper=sleeps.append)

    runtime.wake("issue-1", "alice", "worker")

    assert sleeps == [0.25, 0.25]
    assert not any(args[:2] == ["issue", "rerun"] for args in calls)


def test_multica_runtime_reruns_once_after_bounded_observation(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    sleeps = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{"id": "direct-failed", "status": "failed", "kind": "direct"}]
        if args[:2] == ["issue", "rerun"]:
            return {"id": "direct-retry", "status": "queued"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=3,
        active_observation_interval=0.25, sleeper=sleeps.append)

    runtime.wake("issue-1", "alice", "worker")

    assert sleeps == [0.25, 0.25]
    assert calls.count([
        "issue", "rerun", "issue-1", "--output", "json",
    ]) == 1


def test_multica_runtime_rejects_success_without_rerun_task_identity(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-reviewer")

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return [{
                "id": "worker-run", "status": "completed", "kind": "direct",
                "agent_id": "agent-worker",
            }]
        if args[:2] == ["issue", "rerun"]:
            return None
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    with pytest.raises(PlatformError, match="did not return a task identity"):
        MulticaRuntime(
            store, active_observation_attempts=1,
            active_observation_interval=0, sleeper=lambda _seconds: None,
        ).wake("issue-1", "hermes-reviewer", "reviewer")


def test_multica_runtime_confirms_successful_rerun_is_observable(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-reviewer")
    observations = iter([
        [{
            "id": "worker-run", "status": "completed", "kind": "direct",
            "agent_id": "agent-worker",
        }],
        [{
            "id": "worker-run", "status": "completed", "kind": "direct",
            "agent_id": "agent-worker",
        }],
        [
            {
                "id": "worker-run", "status": "completed", "kind": "direct",
                "agent_id": "agent-worker",
            },
            {
                "id": "review-run", "status": "queued", "kind": "direct",
                "agent_id": "agent-reviewer",
                "attribution": {"evidence": {"kind": "rerun"}},
            },
        ],
    ])

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            return {
                "id": "review-run", "status": "queued",
                "agent_id": "agent-reviewer",
            }
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None,
    ).wake("issue-1", "hermes-reviewer", "reviewer")


def test_multica_runtime_waits_for_foreign_active_run_before_reviewer_rerun(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-reviewer")
    calls = []
    observations = iter([
        [{
            "id": "worker-run", "status": "running", "kind": "direct",
            "agent_id": "agent-worker",
        }],
        [{
            "id": "worker-run", "status": "completed", "kind": "direct",
            "agent_id": "agent-worker",
        }],
        [
            {
                "id": "worker-run", "status": "completed", "kind": "direct",
                "agent_id": "agent-worker",
            },
            {
                "id": "review-run", "status": "queued", "kind": "direct",
                "agent_id": "agent-reviewer",
                "attribution": {"evidence": {"kind": "rerun"}},
            },
        ],
    ])

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return next(observations)
        if args[:2] == ["issue", "rerun"]:
            return {
                "id": "review-run", "status": "queued",
                "agent_id": "agent-reviewer",
            }
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    MulticaRuntime(
        store, active_observation_attempts=2,
        active_observation_interval=0, sleeper=lambda _seconds: None,
    ).wake("issue-1", "hermes-reviewer", "reviewer")

    assert calls.count([
        "issue", "rerun", "issue-1", "--output", "json",
    ]) == 1


def test_multica_runtime_rejects_rerun_task_that_never_becomes_observable(
    monkeypatch,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-reviewer")

    def fake_run(args):
        if args[:2] == ["issue", "runs"]:
            return [{
                "id": "worker-run", "status": "completed", "kind": "direct",
                "agent_id": "agent-worker",
            }]
        if args[:2] == ["issue", "rerun"]:
            return {
                "id": "review-run", "status": "queued",
                "agent_id": "agent-reviewer",
            }
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    with pytest.raises(PlatformError, match="is not observable"):
        MulticaRuntime(
            store, active_observation_attempts=2,
            active_observation_interval=0, sleeper=lambda _seconds: None,
        ).wake("issue-1", "hermes-reviewer", "reviewer")


def test_multica_runtime_observation_error_fails_closed(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    observations = 0

    def fake_run(args):
        nonlocal observations
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            observations += 1
            if observations == 1:
                return [{
                    "id": "direct-failed", "status": "failed", "kind": "direct",
                }]
            raise PlatformError("active run observation unavailable")
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)
    runtime = MulticaRuntime(
        store, active_observation_attempts=3,
        active_observation_interval=0, sleeper=lambda _seconds: None)

    with pytest.raises(PlatformError, match="observation unavailable"):
        runtime.wake("issue-1", "alice", "worker")

    assert not any(args[:2] == ["issue", "rerun"] for args in calls)


def test_multica_runtime_assignment_fast_path_does_not_observe_runs(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    store._mark_assignment_wake_pending("issue-1")
    monkeypatch.setattr(
        store, "_run_multica",
        lambda *_args: pytest.fail("assignment-triggered wake must stay a fast path"),
    )

    MulticaRuntime(store).wake("issue-1", "alice", "worker")


def test_multica_runtime_lists_typed_run_identity(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(store, "_run_multica", lambda _args: [
        {"id": "run-1", "kind": "direct", "status": "failed",
         "agent_id": "agent-1", "attempt": 1, "max_attempts": 2,
         "error": "Selected model is at capacity. Please try a different model.",
         "failure_reason": "agent_error.model_not_found_or_unavailable",
         "retry_of_task_id": "run-0",
         "attribution": {"evidence": {"kind": "rerun"}}},
        {"id": "run-2", "kind": "comment", "status": "running",
         "agent_id": "agent-2"},
        {"id": "run-3", "kind": "direct", "status": "running",
         "agent_id": "agent-3",
         "attribution": {"evidence": {"kind": "issue_assignment"}}},
    ])

    assert runtime.list_runs("issue-1") == [
        AgentRunObservation(
            id="run-1", kind="direct", status="failed", agent_id="agent-1",
            error="Selected model is at capacity. Please try a different model.",
            retry_of_run_id="run-0", trigger_kind="rerun"),
        AgentRunObservation(
            id="run-2", kind="comment", status="running", agent_id="agent-2"),
        AgentRunObservation(
            id="run-3", kind="direct", status="running", agent_id="agent-3",
            trigger_kind="issue_assignment"),
    ]


@pytest.mark.parametrize(
    "status",
    ["queued", "dispatched", "running", "waiting_local_directory", "deferred"],
)
def test_multica_runtime_treats_current_nonterminal_statuses_as_active(
    monkeypatch, status,
):
    """Multica v0.4.16 task-runs 非终态必须保持 active。"""
    payload = [{
        "id": "b8e84b2e-ab15-40e2-a67a-7245cac713c0",
        "kind": "direct",
        "status": status,
        "agent_id": "0deb1d04-7ed4-4c63-b808-ae7f2b3e7276",
        "created_at": "2026-08-02T23:40:05Z",
        "dispatched_at": "2026-08-02T23:40:05.348Z",
        "started_at": None,
        "completed_at": None,
    }]
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(store, "_run_multica", lambda _args: payload)

    assert runtime.is_active("issue-1") is True
    observed = runtime.list_runs("issue-1")
    assert len(observed) == 1
    assert observed[0].status == status
    assert observed[0].active is True
    assert observed[0].terminal is False


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_multica_runtime_preserves_terminal_status_semantics(monkeypatch, status):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(store, "_run_multica", lambda _args: [{
        "id": f"run-{status}", "kind": "direct", "status": status,
    }])

    assert runtime.is_active("issue-1") is False
    observed = runtime.list_runs("issue-1")
    assert observed[0].active is False
    assert observed[0].terminal is True


def test_multica_runtime_unknown_status_reports_raw_value(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(store, "_run_multica", lambda _args: [{
        "id": "run-future", "kind": "direct", "status": "future_transition",
    }])

    with pytest.raises(
        PlatformError,
        match="unknown status 'future_transition' for run-future",
    ):
        runtime.list_runs("issue-1")


@pytest.mark.parametrize("payload", [
    None,
    {"data": []},
    ["not-a-run"],
    [{"status": "running"}],
    [{"id": "run-1"}],
])
def test_multica_runtime_rejects_malformed_run_payload(monkeypatch, payload):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    monkeypatch.setattr(store, "_run_multica", lambda _args: payload)

    with pytest.raises(PlatformError, match="run payload"):
        runtime.list_runs("issue-1")


def test_multica_runtime_cancel_clears_stale_assignment_without_active_run(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{"id": "task-old", "kind": "direct", "status": "cancelled"}]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    assert runtime.cancel("issue-1") is False
    assert not any(args[:2] == ["issue", "assign"] for args in calls)


def test_multica_clear_assignment_preserves_review_evidence(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "assign"]:
            return {"id": "issue-1", "assignee_id": None}
        if args[:3] == ["issue", "metadata", "set"]:
            return None
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    store.clear_assignment("issue-1")

    assert ["issue", "assign", "issue-1", "--unassign"] in calls
    assert [
        "issue", "metadata", "set", "issue-1",
        "--key", "reviewer", "--value", "",
    ] in calls


def test_multica_runtime_reruns_cancelled_direct_even_when_comment_is_newer(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [
                {"id": "comment-1", "status": "cancelled", "kind": "comment",
                 "created_at": "2026-07-09T09:00:00Z"},
                {"id": "direct-1", "status": "cancelled", "kind": "direct",
                 "created_at": "2026-07-09T08:35:23Z"},
            ]
        if args[:2] == ["issue", "rerun"]:
            return {"id": "direct-2", "status": "queued"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    from omac.engines.multica import MulticaRuntime
    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert ["issue", "rerun", "issue-1", "--output", "json"] in calls


def test_multica_runtime_reruns_completed_direct_without_submit(monkeypatch):
    """direct run completed 但 issue 仍 in_progress 时,wake 应 rerun 原 issue。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [
                {"id": "direct-1", "status": "completed", "kind": "direct",
                 "created_at": "2026-07-10T01:00:00Z"},
            ]
        if args[:2] == ["issue", "rerun"]:
            return {"id": "direct-2", "status": "queued"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    from omac.engines.multica import MulticaRuntime
    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert ["issue", "rerun", "issue-1", "--output", "json"] in calls


def test_multica_runtime_does_not_rerun_fresh_failed_assignment(monkeypatch):
    """assign 已触发的新 run 即使很快失败，紧随其后的 wake 也不能重复 rerun。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    rerun_created = False

    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-1")

    def fake_run(args, capture=True):
        nonlocal rerun_created
        calls.append(args)
        if args[:2] == ["issue", "assign"]:
            return {"id": "issue-1", "assignee_id": "agent-1"}
        if args[:3] == ["issue", "metadata", "set"]:
            return None
        if args[:2] == ["issue", "get"]:
            return {
                "id": "issue-1",
                "assignee_id": "agent-old",
                "title": "t",
                "description": "d",
                "status": "in_review",
                "metadata": {"dag_key": "node-a", "kind": "develop"},
            }
        if args[:2] == ["issue", "runs"]:
            runs = [
                {"id": "direct-2", "status": "failed", "kind": "direct",
                 "created_at": "2026-07-16T16:20:58Z"},
            ]
            if rerun_created:
                runs.append({
                    "id": "direct-3", "status": "queued", "kind": "direct",
                    "agent_id": "agent-1",
                    "attribution": {"evidence": {"kind": "rerun"}},
                })
            return runs
        if args[:2] == ["issue", "rerun"]:
            rerun_created = True
            return {
                "id": "direct-3", "status": "queued", "agent_id": "agent-1",
            }
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    from omac.engines.multica import MulticaRuntime
    store.assign_work_item("issue-1", "alice", "reviewer")
    runtime = MulticaRuntime(store)
    runtime.wake("issue-1", "alice", "reviewer")

    assert ["issue", "rerun", "issue-1", "--output", "json"] not in calls

    runtime.wake("issue-1", "alice", "reviewer")

    assert calls.count(["issue", "rerun", "issue-1", "--output", "json"]) == 1


def test_multica_silent_reviewer_assignment_uses_suppressed_update(
    tmp_path, monkeypatch,
):
    """Reviewer identity is assigned without letting UpdateIssue resume a session."""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    item_id = "8e6bd282-6039-41d2-aa00-969a0bf1554a"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"server_url":"https://api.example.test","token":"secret-token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MULTICA_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-reviewer")
    metadata = []
    monkeypatch.setattr(
        store,
        "update_work_item_metadata",
        lambda observed_id, **values: metadata.append((observed_id, values)),
    )
    payloads = []

    def run(args, **_kwargs):
        body_path = args[args.index("--data-binary") + 1].removeprefix("@")
        payloads.append(json.loads(Path(body_path).read_text(encoding="utf-8")))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("omac.engines.multica.subprocess.run", run)

    # A worker assignment can leave this process-local fast-path marker behind
    # when the controller observes the assignment-created Run before calling
    # runtime.wake. The next silent reviewer assignment must retire it.
    store._mark_assignment_wake_pending(item_id)
    store.assign_work_item(
        item_id, "hermes-reviewer", "reviewer", start_run=False)

    assert metadata == [(item_id, {"reviewer": "hermes-reviewer"})]
    assert payloads == [{
        "assignee_type": "agent",
        "assignee_id": "agent-reviewer",
        "suppress_run": True,
    }]
    assert store._consume_assignment_wake_pending(item_id) is False


def test_multica_silent_reviewer_assignment_then_wake_avoids_active_duplicate(
    tmp_path, monkeypatch,
):
    """A silent handoff still lets runtime.wake preserve active-Run idempotency."""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    item_id = "8e6bd282-6039-41d2-aa00-969a0bf1554a"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"server_url":"https://api.example.test","token":"secret-token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MULTICA_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-reviewer")
    monkeypatch.setattr(store, "update_work_item_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "omac.engines.multica.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""),
    )
    calls = []

    def run_multica(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            return [{
                "id": "review-run-1",
                "status": "running",
                "kind": "direct",
                "agent_id": "agent-reviewer",
                "created_at": "2026-08-01T01:00:00Z",
            }]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", run_multica)

    store.assign_work_item(
        item_id, "hermes-reviewer", "reviewer", start_run=False)
    MulticaRuntime(store).wake(item_id, "hermes-reviewer", "reviewer")

    assert not any(args[:2] == ["issue", "rerun"] for args in calls)


def test_multica_silent_initial_reviewer_handoff_wakes_with_fresh_rerun(
    tmp_path, monkeypatch,
):
    """The first reviewer Run is created by issue rerun without a task id."""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    item_id = "8e6bd282-6039-41d2-aa00-969a0bf1554a"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"server_url":"https://api.example.test","token":"secret-token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MULTICA_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-reviewer")
    monkeypatch.setattr(store, "update_work_item_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "omac.engines.multica.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""),
    )
    calls = []
    rerun_created = False

    def run_multica(args, capture=True):
        nonlocal rerun_created
        calls.append(args)
        if args[:2] == ["issue", "runs"]:
            runs = [{
                "id": "worker-run-1",
                "status": "completed",
                "kind": "direct",
                "agent_id": "agent-worker",
                "created_at": "2026-08-01T01:00:00Z",
            }]
            if rerun_created:
                runs.append({
                    "id": "review-run-1",
                    "status": "queued",
                    "kind": "direct",
                    "agent_id": "agent-reviewer",
                    "attribution": {"evidence": {"kind": "rerun"}},
                })
            return runs
        if args[:2] == ["issue", "rerun"]:
            rerun_created = True
            return {
                "id": "review-run-1", "status": "queued",
                "agent_id": "agent-reviewer",
            }
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", run_multica)

    store.assign_work_item(
        item_id, "hermes-reviewer", "reviewer", start_run=False)
    MulticaRuntime(
        store, sleeper=lambda _seconds: None,
        active_observation_attempts=1,
    ).wake(item_id, "hermes-reviewer", "reviewer")

    assert ["issue", "rerun", item_id, "--output", "json"] in calls
    assert not any("--task-id" in args for args in calls)


def test_multica_worker_assignment_keeps_existing_start_run_path(monkeypatch):
    """The backward-compatible default still delegates run startup to assign."""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    monkeypatch.setattr(store, "_resolve_agent_id", lambda _name: "agent-worker")

    def run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return {
                "id": "issue-1",
                "assignee_id": "old-agent",
                "metadata": {"kind": "develop"},
            }
        if args[:3] == ["issue", "metadata", "set"]:
            return None
        if args[:2] == ["issue", "assign"]:
            return {"id": "issue-1", "assignee_id": "agent-worker"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", run)

    store.assign_work_item("issue-1", "alice", "worker")

    assert ["issue", "assign", "issue-1", "--to", "agent-worker"] in calls
    assert store._consume_assignment_wake_pending("issue-1") is True


def test_multica_reviewer_metadata_failure_does_not_start_assignment(monkeypatch):
    """评审身份必须先持久化；metadata 失败时不能先启动错误身份的 run。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-reviewer")

    def fake_run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return {
                "id": "issue-1",
                "assignee_id": "agent-old",
                "title": "t",
                "description": "d",
                "status": "in_review",
                "metadata": {
                    "dag_key": "node-a",
                    "kind": "decompose",
                    "reviewer": "old-reviewer",
                },
            }
        if args[:3] == ["issue", "metadata", "set"]:
            raise PlatformError("Request timed out: server did not respond")
        if args[:2] == ["issue", "assign"]:
            return {"id": "issue-1", "assignee_id": "agent-reviewer"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    with pytest.raises(PlatformError, match="timed out"):
        store.assign_work_item("issue-1", "hermes-reviewer", "reviewer")

    assert not any(args[:2] == ["issue", "assign"] for args in calls)


def test_multica_runtime_reruns_completed_same_assignee_assignment(monkeypatch):
    """同一 assignee 的 assign 不会启动新 run，wake 必须 rerun 已结束任务。"""
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-1")

    def fake_run(args, capture=True):
        calls.append(args)
        if args[:2] == ["issue", "get"]:
            return {
                "id": "issue-1",
                "assignee_id": "agent-1",
                "title": "t",
                "description": "d",
                "status": "in_progress",
                "metadata": {"dag_key": "node-a", "kind": "develop"},
            }
        if args[:2] == ["issue", "assign"]:
            return {"id": "issue-1", "assignee_id": "agent-1"}
        if args[:3] == ["issue", "metadata", "set"]:
            return None
        if args[:2] == ["issue", "runs"]:
            return [
                {
                    "id": "direct-1",
                    "status": "completed",
                    "kind": "direct",
                    "created_at": "2026-07-16T18:50:47Z",
                },
            ]
        if args[:2] == ["issue", "rerun"]:
            return {"id": "direct-2", "status": "queued"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    from omac.engines.multica import MulticaRuntime

    store.assign_work_item("issue-1", "alice", "worker")
    MulticaRuntime(store).wake("issue-1", "alice", "worker")

    assert calls.count(["issue", "rerun", "issue-1", "--output", "json"]) == 1


def test_multica_runtime_provisions_missing_skill_then_creates_agent(tmp_path, monkeypatch):
    from omac.agent_templates import SkillTemplate
    from omac.engines.models import AgentProvisionSpec
    from omac.engines.multica import MulticaRuntime

    skill_root = tmp_path / "quality"
    (skill_root / "references").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: quality\ndescription: quality rules\n---\n\n# Quality\n",
        encoding="utf-8",
    )
    (skill_root / "references" / "guide.md").write_text("guide", encoding="utf-8")
    skill = SkillTemplate(
        name="quality",
        description="quality rules",
        path=skill_root,
        files=tuple(sorted(p for p in skill_root.rglob("*") if p.is_file())),
    )
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    runtime = MulticaRuntime(store)
    calls = []
    imported = False

    def fake_run(args, capture=True):
        nonlocal imported
        calls.append(args)
        if args[:2] == ["agent", "list"]:
            return []
        if args[:2] == ["skill", "list"]:
            return ([{"id": "skill-1", "name": "quality"}] if imported else [])
        if args[:2] == ["skill", "import"]:
            archive = Path(args[args.index("--file") + 1])
            assert archive.exists()
            import zipfile
            with zipfile.ZipFile(archive) as zf:
                assert sorted(zf.namelist()) == ["SKILL.md", "references/guide.md"]
            imported = True
            return {"id": "skill-1", "name": "quality"}
        if args[:2] == ["agent", "create"]:
            assert args[args.index("--runtime-id") + 1] == "runtime-1"
            assert args[args.index("--instructions") + 1] == "rules"
            return {"id": "agent-1", "name": "template-worker"}
        if args[:3] == ["agent", "skills", "set"]:
            assert args[3] == "agent-1"
            assert args[args.index("--skill-ids") + 1] == "skill-1"
            return {"ok": True}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    created = runtime.provision_agent(AgentProvisionSpec(
        name="template-worker",
        description="worker template",
        instructions="rules",
        runtime_id="runtime-1",
        skills=[skill],
    ))

    assert created.id == "agent-1"
    assert any(call[:2] == ["skill", "import"] for call in calls)
    assert any(call[:3] == ["agent", "skills", "set"] for call in calls)


def test_multica_runtime_lists_actual_runtime_shape(monkeypatch):
    from omac.engines.multica import MulticaRuntime

    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(store, "_run_multica", lambda args: [{
        "id": "runtime-1",
        "name": "Codex Runtime",
        "provider": "codex",
        "runtime_mode": "app-server",
        "status": "online",
    }])

    targets = MulticaRuntime(store).list_targets()

    assert len(targets) == 1
    assert targets[0].type == "codex"


@pytest.mark.parametrize(
    ("payload", "expected_state", "expected_merged_at"),
    [
        ({"state": "MERGED", "mergedAt": "2026-07-26T08:45:00Z"},
         PullRequestState.MERGED, "2026-07-26T08:45:00Z"),
        ({"state": "OPEN", "mergedAt": None, "mergeStateStatus": "CLEAN"},
         PullRequestState.OPEN, None),
        ({"state": "OPEN", "mergedAt": None,
          "autoMergeRequest": {"enabledAt": "2026-07-26T08:40:00Z"},
          "mergeStateStatus": "CLEAN"},
         getattr(PullRequestState, "PENDING", "pending"), None),
        ({"state": "OPEN", "mergedAt": None,
          "autoMergeRequest": None, "mergeStateStatus": "QUEUED"},
         getattr(PullRequestState, "PENDING", "pending"), None),
        ({"state": "CLOSED", "mergedAt": None},
         PullRequestState.CLOSED_UNMERGED, None),
    ],
)
def test_multica_observe_pull_request_classifies_remote_states(
    monkeypatch, payload, expected_state, expected_merged_at,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("omac.engines.multica.subprocess.run", run)

    observation = store.observe_pull_request("https://example.com/pr/1")

    assert observation.state is expected_state
    assert observation.merged_at == expected_merged_at
    assert "autoMergeRequest" in calls[0][0][-1]
    assert "mergeStateStatus" in calls[0][0][-1]
    assert "isInMergeQueue" not in calls[0][0][-1]


def test_multica_pr_view_fields_are_supported_by_local_gh():
    if shutil.which("gh") is None:
        pytest.skip("gh is not installed")

    result = subprocess.run(
        ["gh", "pr", "view", "--help"], capture_output=True, text=True,
        check=False,
    )
    assert result.returncode == 0
    module = __import__("omac.engines.multica", fromlist=["x"])
    fields = module.MULTICA_PR_VIEW_FIELDS
    for field in fields.split(","):
        assert field in result.stdout


@pytest.mark.parametrize(
    "run_result",
    [
        SimpleNamespace(returncode=1, stdout="", stderr="authentication failed"),
        subprocess.TimeoutExpired(cmd="gh", timeout=30),
        SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    ],
)
def test_multica_observe_pull_request_fails_closed_for_unreadable_remote(
    monkeypatch, run_result,
):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    def run(*args, **kwargs):
        if isinstance(run_result, BaseException):
            raise run_result
        return run_result

    monkeypatch.setattr("omac.engines.multica.subprocess.run", run)

    observation = store.observe_pull_request("https://example.com/pr/1")

    assert observation.state is PullRequestState.UNKNOWN


def test_multica_pr_check_and_readiness_stay_in_adapter(monkeypatch):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    calls = []
    responses = iter([
        SimpleNamespace(returncode=0, stdout="checks ok", stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({
            "isDraft": False, "state": "OPEN", "headRefOid": "head-1"}),
            stderr=""),
    ])

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr("omac.engines.multica.subprocess.run", run)

    check = store.check_pull_request(
        "https://github.com/acme/repo/pull/1", "gh pr checks {pr_url}", 30)
    readiness = store.read_pull_request_readiness(
        "https://github.com/acme/repo/pull/1")

    assert check.succeeded is True
    assert readiness.is_draft is False
    assert readiness.state == "OPEN"
    assert readiness.head_sha == "head-1"
    assert calls[0][0] == "gh pr checks https://github.com/acme/repo/pull/1"
    assert calls[1][0][-1] == "isDraft,state,headRefOid"


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"isDraft": None, "state": "OPEN", "headRefOid": "head"},
     {"isDraft": False},
     {"isDraft": "false", "state": "OPEN", "headRefOid": "head"},
     {"isDraft": False, "state": "OPEN"}],
)
def test_multica_readiness_malformed_payload_fails_closed(monkeypatch, payload):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    monkeypatch.setattr(
        "omac.engines.multica.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""),
    )

    result = store.read_pull_request_readiness("https://github.com/acme/repo/pull/1")

    assert isinstance(result, PullRequestReadinessFailure)

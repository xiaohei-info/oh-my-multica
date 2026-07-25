from pathlib import Path
from types import SimpleNamespace

import pytest

from omac.engines.models import EngineConfig
from omac.engines.models import WorkItemStatus
from omac.engines.multica import MulticaRuntime, MulticaStore
from omac.errors import PlatformError


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


def test_multica_get_work_item_maps_exhausted_failed_runs_to_failed(monkeypatch):
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
                {"id": "run-2", "status": "failed", "created_at": "2026-07-09T08:35:58Z"},
                {"id": "run-1", "status": "failed", "created_at": "2026-07-09T08:35:23Z"},
            ]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    item = store.get_work_item("issue-1")

    assert item.status == WorkItemStatus.FAILED


def test_multica_get_work_item_marks_completed_without_submit_for_worker_followup(monkeypatch):
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
                {
                    "id": "run-2",
                    "status": "completed",
                    "result": {"pr_url": ""},
                    "created_at": "2026-07-09T08:35:58Z",
                },
            ]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    item = store.get_work_item("issue-1")

    assert item.status == WorkItemStatus.IN_PROGRESS
    assert item.agent_run_finished_without_submit is True


def test_multica_get_work_item_keeps_in_progress_when_any_run_active(monkeypatch):
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
                {"id": "run-2", "status": "running", "created_at": "2026-07-09T08:35:58Z"},
                {"id": "run-1", "status": "failed", "created_at": "2026-07-09T08:35:23Z"},
            ]
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    item = store.get_work_item("issue-1")

    assert item.status == WorkItemStatus.IN_PROGRESS


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

    monkeypatch.setattr(store, "_resolve_agent_id", lambda name: "agent-1")

    def fake_run(args, capture=True):
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
            return [
                {"id": "direct-2", "status": "failed", "kind": "direct",
                 "created_at": "2026-07-16T16:20:58Z"},
            ]
        if args[:2] == ["issue", "rerun"]:
            return {"id": "direct-3", "status": "queued"}
        raise AssertionError(args)

    monkeypatch.setattr(store, "_run_multica", fake_run)

    from omac.engines.multica import MulticaRuntime
    store.assign_work_item("issue-1", "alice", "reviewer")
    runtime = MulticaRuntime(store)
    runtime.wake("issue-1", "alice", "reviewer")

    assert ["issue", "rerun", "issue-1", "--output", "json"] not in calls

    runtime.wake("issue-1", "alice", "reviewer")

    assert calls.count(["issue", "rerun", "issue-1", "--output", "json"]) == 1


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

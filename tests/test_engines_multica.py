from omac.engines.models import EngineConfig
from omac.engines.models import WorkItemStatus
from omac.engines.multica import MulticaStore


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

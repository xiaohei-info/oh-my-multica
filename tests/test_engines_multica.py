from omac.engines.models import EngineConfig
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

"""Every control metadata write shares one 8KB budget; room comes from reversible attachment references."""

import json

import pytest

from _support.handoffcase import case  # noqa: F401
from omac.core.taskmeta import REVIEW_REPORT_REF_KEY, parse_delivery_identity
from omac.errors import MetadataBudgetError


def _near_full_identity(metadata, spare: int) -> dict:
    """A delivery identity sized so only `spare` bytes of the 8KB budget remain."""
    identity = {
        "schema": "omac.delivery-identity/v1", "worker": "worker-a", "run_id": "run-1",
        "pr_url": "https://example.test/acme/demo/pull/85?",
    }
    metadata["delivery_identity"] = json.dumps(identity)
    fill = 8192 - len(json.dumps(metadata, ensure_ascii=False).encode()) - spare
    identity["pr_url"] += "x" * fill
    metadata["delivery_identity"] = json.dumps(identity)
    return identity


def test_publish_ref_over_budget_moves_delivery_identity_to_an_attachment(case):
    store, metadata, _, _, _, publications, issue = case
    del metadata[REVIEW_REPORT_REF_KEY]
    # Nothing cheaper to compact first: the identity itself must move.
    metadata.update(reviewer_run_baseline="{}", source_refs="[]", review_obligations="[]")
    identity = _near_full_identity(metadata, spare=20)
    ref_value = json.dumps({"sha256": "a" * 64, "filename": "omac-review-report-x.yaml", "bytes": 5000})
    assert len(json.dumps({**metadata, REVIEW_REPORT_REF_KEY: ref_value}).encode()) > 8192

    store._publish_ref("issue-1", "review-report", "report: ok\n", ".yaml", REVIEW_REPORT_REF_KEY)

    assert len(json.dumps(metadata, ensure_ascii=False).encode()) <= 8192
    assert json.loads(metadata["delivery_identity"])["schema"] == "omac.control-payload/v1"
    assert metadata[REVIEW_REPORT_REF_KEY]
    restored = store._issue_to_control_projection(issue(), "ws").work_item.delivery_identity
    assert restored == parse_delivery_identity(identity)
    assert len(publications) == 2  # the report itself and the moved identity


def test_publish_ref_without_recoverable_room_fails_closed_with_a_named_class(case):
    store, metadata, _, _, writes, _, _ = case
    metadata["foreign_data"] = "x" * 8100
    before = dict(metadata)

    with pytest.raises(MetadataBudgetError, match="platform-metadata-budget"):
        store._publish_ref("issue-1", "review-report", "report: ok\n", ".yaml", REVIEW_REPORT_REF_KEY)

    assert metadata == before and not writes

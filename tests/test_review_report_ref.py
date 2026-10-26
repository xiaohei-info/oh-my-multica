"""A review report reference is trusted only when it names a downloadable attachment and binds its digest."""
import pytest

from omac.core.taskmeta import exact_review_report_ref

DIGEST = "a" * 64


@pytest.mark.parametrize(("value", "exact"), [
    pytest.param({"attachment_id": "att-1", "sha256": DIGEST}, True, id="minimal"),
    pytest.param({"attachment_id": "att-1", "sha256": DIGEST.upper(), "comment_id": "c-1", "filename": "report.yaml", "bytes": 12}, True,
                 id="all-optional-fields"),
    pytest.param(None, False, id="none"),
    pytest.param({}, False, id="empty"),
    pytest.param(["att-1"], False, id="not-a-mapping"),
    pytest.param({"attachment_id": "", "sha256": DIGEST}, False, id="blank-attachment"),
    pytest.param({"attachment_id": "  ", "sha256": DIGEST}, False, id="whitespace-attachment"),
    pytest.param({"attachment_id": 7, "sha256": DIGEST}, False, id="numeric-attachment"),
    pytest.param({"attachment_id": "att-1"}, False, id="no-digest"),
    pytest.param({"attachment_id": "att-1", "sha256": "not-a-sha"}, False, id="malformed-digest"),
    pytest.param({"attachment_id": "att-1", "sha256": "a" * 63}, False, id="short-digest"),
    pytest.param({"attachment_id": "att-1", "sha256": DIGEST, "extra": 1}, False, id="unknown-field"),
    pytest.param({"attachment_id": "att-1", "sha256": DIGEST, "comment_id": " "}, False, id="blank-comment"),
    pytest.param({"attachment_id": "att-1", "sha256": DIGEST, "bytes": -1}, False, id="negative-size"),
    pytest.param({"attachment_id": "att-1", "sha256": DIGEST, "bytes": True}, False, id="boolean-size"),
])
def test_a_review_report_ref_must_be_exact(value, exact):
    assert exact_review_report_ref(value) is exact

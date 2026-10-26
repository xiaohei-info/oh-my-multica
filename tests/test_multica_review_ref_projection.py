"""How the Multica adapter reads a review reference that is not a mapping, and what the Loop makes of it. No network."""
import pytest

from omac.core.taskmeta import MACHINE_FEEDBACK_REF_KEY, REVIEW_REPORT_REF_KEY
from omac.engines.models import EngineConfig
from omac.engines.multica import MulticaStore
from omac.pipeline import loop


@pytest.mark.parametrize(("key", "field"), [(MACHINE_FEEDBACK_REF_KEY, "machine_feedback_ref"), (REVIEW_REPORT_REF_KEY, "review_report_ref")])
@pytest.mark.parametrize("raw", [[], ["fact"]], ids=["empty-list", "list"])
def test_a_review_reference_that_is_not_a_mapping_is_kept_and_never_counts_as_a_cleared_review(key, field, raw):
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_control_projection({
        "id": "issue-1", "title": "review", "description": "review", "status": "in_progress",
        "metadata": {"dag_key": "develop-a", "kind": "develop", "phase": "authoring", key: raw}}, "ws").work_item

    assert getattr(item, field) is not None
    assert not loop._review_projection_is_clear(item)


def test_an_issue_without_review_facts_reads_as_a_cleared_review():
    store = MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))

    item = store._issue_to_control_projection({
        "id": "issue-1", "title": "review", "description": "review", "status": "in_progress",
        "metadata": {"dag_key": "develop-a", "kind": "develop", "phase": "authoring", "decision_required": "{}"}}, "ws").work_item

    assert loop._review_projection_is_clear(item) and item.decision_required is None

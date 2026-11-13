"""decide_legacy_delivery: a legacy Worker delivery is adopted only when every proof item holds; else each missing item is named."""
from dataclasses import replace

import pytest

from omac.pipeline import decide_legacy as d
from omac.pipeline.decide_runs import BASELINE_MAX_DIRECT_RUN_IDS

PROVEN = d.LegacyDeliveryFacts(
    stable_run_identity=True, foreign_run=False, prior_review_facts=False, operator_note=False, direct_run_count=2,
    delivery_problem=None, causal_completed_run_ids=("run-1",))


@pytest.mark.parametrize("change, run_id, missing", [
    ({}, "run-1", ()),
    ({"direct_run_count": BASELINE_MAX_DIRECT_RUN_IDS}, "run-1", ()),
    ({"stable_run_identity": False}, None, (d.NO_STABLE_RUN_IDENTITY,)),
    ({"foreign_run": True}, None, (d.FOREIGN_RUN,)),
    ({"prior_review_facts": True}, None, (d.PRIOR_REVIEW_FACTS,)),
    ({"operator_note": True}, None, (d.OPERATOR_NOTE,)),
    ({"direct_run_count": BASELINE_MAX_DIRECT_RUN_IDS + 1}, None, (d.RUN_HISTORY_TOO_LONG,)),
    ({"delivery_problem": d.PR_HEAD_MOVED}, None, (d.PR_HEAD_MOVED,)),
    ({"delivery_problem": d.NO_DELIVERY}, None, (d.NO_DELIVERY,)),
    ({"causal_completed_run_ids": ()}, None, (d.NO_CAUSAL_RUN,)),
    ({"causal_completed_run_ids": ("run-1", "run-2")}, None, (d.AMBIGUOUS_CAUSAL_RUN,)),
    ({"foreign_run": True, "delivery_problem": d.PR_HEAD_MOVED}, None, (d.FOREIGN_RUN, d.PR_HEAD_MOVED)),
])
def test_a_legacy_delivery_is_adopted_only_with_every_proof_item(change, run_id, missing):
    result = d.decide_legacy_delivery(replace(PROVEN, **change))

    assert (result.run_id, result.missing, result.adopt) == (run_id, missing, run_id is not None)

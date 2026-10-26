"""May a rejection go back to the Worker once more? Counters, the configured limit and an operator's continuation in; yes or no out."""
from types import SimpleNamespace

import pytest

from omac.core.manifest import Manifest, Node
from omac.core.retry_budget import review_rework_budget


def item(review_bounces, continuation=None):
    return SimpleNamespace(
        id="item-1", bounces=SimpleNamespace(review=review_bounces, worker=0, ci=0, merge=0), bounce_baseline=None,
        review_continuation=continuation)


def manifest():
    return Manifest(meta={}, nodes={"a": Node(id="a", worker="alice", work_item_id="item-1")})


@pytest.mark.parametrize(("bounces", "limit", "allowed"), [
    pytest.param(0, 0, False, id="no-rework-allowed-at-all"),
    pytest.param(0, 1, True, id="first-rework-of-one"),
    pytest.param(1, 1, False, id="second-rejection-exhausts-a-limit-of-one"),
    pytest.param(0, 3, True, id="default-limit-first"),
    pytest.param(2, 3, True, id="default-limit-third"),
    pytest.param(3, 3, False, id="default-limit-fourth"),
])
def test_a_rejection_goes_back_to_the_worker_only_within_the_review_limit(bounces, limit, allowed):
    budget = review_rework_budget(manifest(), "a", item(bounces), limit)

    assert budget.allows_rework is allowed and budget.next_round == bounces + 1


def test_an_operator_continuation_extends_the_limit_to_an_absolute_round():
    continuation = {"schema": "omac.review-continuation/v1", "stage": "develop", "mode": "producer-rework",
                    "authorized_through_round": 10, "decision_count": 1, "reason": "operator approved one additional round"}

    assert not review_rework_budget(manifest(), "a", item(9), 9).allows_rework
    assert review_rework_budget(manifest(), "a", item(9, continuation), 9).allows_rework
    assert not review_rework_budget(manifest(), "a", item(10, continuation), 9).allows_rework

"""Node-level failures stop or rework only their own node while the Loop keeps advancing the others.

The two real cases are a PR HEAD that moves after the Worker's submit: once on a first delivery, once on a delivery in rework
after a CI bounce. Node `a` (alice, reviewed by bob) is the node under test; node `d` (carol) is independent work that must
reach done in the same run. (Every other node-level failure class is covered where its scenario lives:
tests/it/test_it_rework_delivery.py, test_it_rework_verdicts.py, test_it_rework_ledger.py.)
"""
import json
from dataclasses import replace

import pytest

from _support.loopkit import HANG, contract
from omac.errors import NodeFailure, PlatformError

STALE_NOTE = "PR HEAD changed after your submit"
GREEN_CI = {"ci": {"check_command": "true {pr_url}", "timeout_minutes": 5}}


def dag(kit, *alice_runs):
    kit.stack.set_scenario("alice", list(alice_runs))
    kit.agents(HANG, names=("bob",))
    kit.stack.set_scenario("carol", [[kit.stack.tool("worker-submit")]])
    manifest = kit.manifest([kit.node("a", reviewer="bob", contract=contract()), kit.node("d", worker="carol", contract=contract())])
    return manifest, kit.save(manifest)


def submit(kit):
    return kit.stack.tool("worker-submit")


def push(kit):
    return kit.stack.tool("push")


def item_a(kit, manifest):
    return kit.store.get_work_item(manifest.nodes["a"].work_item_id)


def remote_head(kit, url):
    return kit.code_host.read_pull_request_readiness(url).head_sha


def pushes_of(kit, url):
    return json.loads((kit.stack.gh_state / "prs.json").read_text()).get(url, {}).get("pushes", 0)


def fail_first_ci_of_a(kit, manifest):
    """The first CI check of node a's pull request fails; every other check passes."""
    kit.distort(kit.code_host, "check_pull_request",
                lambda real: replace(real, succeeded=False, exit_code=1, output="build fail"),
                only=lambda pr_url, *_a, **_k: pr_url == (item_a(kit, manifest).artifacts or {}).get("pr_url"))


def until(kit, manifest, path, predicate, what, **kwargs):
    """Tick until ``predicate``: any tick that raises fails the test, which is the run exiting."""
    kwargs.setdefault("retry_limits", {"worker": 3, "ci": 3})
    kit.tick_until(manifest, path, predicate, timeout=150, what=what, **kwargs)


def test_a_push_after_the_submit_returns_the_delivery_to_its_worker_while_the_dag_goes_on(kit):
    # first real case: submitted at one head, then pushed a merge of main before the Loop sealed the delivery
    manifest, path = dag(kit, [submit(kit), push(kit)], [submit(kit)])

    until(kit, manifest, path, lambda: manifest.nodes["a"].status == "in_review" and manifest.nodes["d"].status == "done",
          "the redelivery to be reviewed and the independent node to be done")

    item = item_a(kit, manifest)
    url = item.artifacts["pr_url"]
    assert item.bounces.worker == 1 and any(STALE_NOTE in c for c in kit.comments(item.id))
    # sealed at the head of the next submit (the third push), not at the void one
    assert pushes_of(kit, url) == 3 and item.delivery_identity.pr_head_sha == remote_head(kit, url)


def test_a_push_in_rework_without_a_new_submit_is_never_compared_with_the_old_submit(kit):
    # second real case, the rework half: after a CI bounce the Worker merges main and pushes; until it submits again,
    # the old submitted head is not a delivery anymore and nothing compares the PR against it
    manifest, path = dag(kit, [submit(kit)], [push(kit), {"hang": 10}, submit(kit)])
    fail_first_ci_of_a(kit, manifest)
    until(kit, manifest, path, lambda: item_a(kit, manifest).bounces.ci == 1, "the CI bounce", config=GREEN_CI)
    url = item_a(kit, manifest).artifacts["pr_url"]
    kit.wait_for(lambda: pushes_of(kit, url) == 2, what="the rework push")
    compared = kit.record(kit.code_host, "read_pull_request_readiness")

    for _ in range(2):
        result = kit.tick(manifest, path, config=GREEN_CI)
        assert result.state == "running" and manifest.nodes["a"].status == "in_progress"
    assert not compared                                             # the old submit is never held against the moved head

    until(kit, manifest, path, lambda: manifest.nodes["a"].status == "in_review", "the rework to be reviewed", config=GREEN_CI)
    item = item_a(kit, manifest)
    assert item.bounces.worker == 0 and not any(STALE_NOTE in c for c in kit.comments(item.id))
    assert pushes_of(kit, url) == 3 and item.delivery_identity.pr_head_sha == remote_head(kit, url)


def test_a_rework_submit_followed_by_a_push_returns_to_the_worker_while_the_dag_goes_on(kit):
    # second real case as the platform recorded it: the rework run submitted at the old head, then its merge of main landed
    manifest, path = dag(kit, [submit(kit)], [submit(kit), push(kit)], [submit(kit)])
    fail_first_ci_of_a(kit, manifest)

    until(kit, manifest, path, lambda: manifest.nodes["a"].status == "in_review" and manifest.nodes["d"].status == "done",
          "the redelivery to be reviewed and the independent node to be done", config=GREEN_CI)

    item = item_a(kit, manifest)
    assert item.bounces.ci == 1 and item.bounces.worker == 1 and any(STALE_NOTE in c for c in kit.comments(item.id))
    url = item.artifacts["pr_url"]
    assert pushes_of(kit, url) == 4 and item.delivery_identity.pr_head_sha == remote_head(kit, url)


def test_a_void_delivery_without_retry_budget_stops_only_its_node(kit):
    manifest, path = dag(kit, [submit(kit), push(kit)])

    until(kit, manifest, path, lambda: manifest.nodes["a"].status == "blocked" and manifest.nodes["d"].status == "done",
          "the void delivery to stop its node and the independent node to be done", retry_limits={"worker": 0})

    decision = item_a(kit, manifest).decision_required
    assert decision["reason_code"] == "delivery-head-moved" and decision["next_action"] == f"omac node retry {path} a"
    result = kit.tick(manifest, path, retry_limits={"worker": 0})
    assert result.state == "needs_decision" and result.failed == ["a"] and result.done == ["d"]


def test_a_code_host_that_cannot_report_the_pr_head_still_stops_the_run(kit):
    kit.stack.set_scenario("alice", [[submit(kit)]])
    manifest = kit.manifest([kit.node("a", reviewer="bob", contract=contract())])
    path = kit.save(manifest)
    kit.tick(manifest, path)
    kit.wait_runs_ended(manifest.nodes["a"].work_item_id)
    kit.wait_for(lambda: item_a(kit, manifest).artifacts, what="the submit")
    kit.stack.gh_behavior(view="fail")                              # a platform failure, not a fact about this node

    with pytest.raises(PlatformError) as stopped:
        kit.tick(manifest, path)

    assert not isinstance(stopped.value, NodeFailure)
    assert manifest.nodes["a"].status == "in_progress" and not item_a(kit, manifest).decision_required

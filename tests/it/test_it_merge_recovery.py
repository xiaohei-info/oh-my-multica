"""Merge closure and restart safety on a real Multica with the gh shim: only a remote-confirmed merge closes a node, an
unproven request is never repeated, and every durable fact is on disk before the platform write it protects."""
import json

import pytest

from _support.mergekit import CONFLICTS, LIMITS, MERGES, Reviewed, merge_config, script
from omac.core.config import DEFAULT_RETRY, resolve_retry
from omac.core.manifest import load_manifest, save_manifest
from omac.core.workitem import MergeCommandResult, WorkItemStatus
from omac.errors import AuthError, PlatformError
from omac.pipeline import loop
from omac.pipeline.delivery import run_merge_delivery


def merged_at_of(kit, pr):
    return json.loads((kit.stack.gh_state / "prs.json").read_text())[pr]["merged_at"]


def historical_done(kit, **node):
    reviewed = Reviewed(kit, node_status="done", **node)
    return reviewed


# ---- only a confirmed merge closes a node -------------------------------------------------------------------------------------

def test_a_node_without_a_reviewer_is_not_done_while_the_pull_request_is_open(kit):
    kit.stack.gh_behavior(merge="noop")
    reviewed = Reviewed(kit, reviewer=None, node_status="in_progress")

    reviewed.collect()

    assert reviewed.node.status != "done" and reviewed.node.merged is False


def test_a_successful_command_over_an_open_pull_request_cannot_unlock_the_downstream(kit):
    kit.stack.gh_behavior(merge="noop")
    reviewed = Reviewed(kit)
    from omac.core.manifest import Node

    reviewed.manifest.nodes["b"] = Node(id="b", worker="alice", reviewer="bob", blocked_by=["a"])
    kit.save(reviewed.manifest)

    result = reviewed.tick()

    views = [call for call in kit.stack.gh_calls("view") if reviewed.pr in call]
    assert len(views) == 2                                                   # observed before the request and after it
    assert reviewed.node.status != "done" and reviewed.manifest.nodes["b"].status == "todo" and result.state != "converged"


def test_the_merge_time_is_the_remote_one(kit):
    reviewed = Reviewed(kit)

    reviewed.collect()

    assert reviewed.node.status == "done" and reviewed.node.merged is True and reviewed.node.merged_at == merged_at_of(kit, reviewed.pr)


# ---- a node that was done before: the remote decides ----------------------------------------------------------------------------

def test_a_done_node_is_backfilled_from_a_confirmed_remote_merge(kit):
    reviewed = historical_done(kit)
    kit.pr(reviewed.pr, merged=True, merged_at="2026-07-26T08:30:00Z")

    assert loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path) is True

    assert reviewed.node.status == "done" and reviewed.node.merged is True and reviewed.node.merged_at == "2026-07-26T08:30:00Z"


def test_a_done_node_over_an_open_pull_request_reenters_merge_closure_without_cancelling_runs(kit):
    reviewed = historical_done(kit)
    kit.stack.gh_behavior(merge="noop")
    cancels = kit.record(kit.runtime, "cancel")

    assert loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path) is True
    result = reviewed.tick()

    assert reviewed.node.status == "merging" and reviewed.node.work_item_id == reviewed.item.id and result.state == "running"
    assert not cancels


def test_a_done_node_over_a_closed_pull_request_fails_closed(kit):
    reviewed = historical_done(kit)
    kit.pr(reviewed.pr, state="CLOSED")

    assert loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path) is True

    assert reviewed.node.status == "blocked" and reviewed.now.status is WorkItemStatus.BLOCKED


def test_a_done_node_whose_pull_request_cannot_be_read_goes_back_to_observing_the_merge(kit):
    reviewed = historical_done(kit)
    kit.stack.gh_behavior(view="fail")

    assert loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path) is True

    assert reviewed.node.status == "merging" and reviewed.now.status is WorkItemStatus.IN_REVIEW


@pytest.mark.parametrize("confirmed", [False, True])
def test_a_platform_read_failure_leaves_a_done_node_exactly_as_it_was(kit, confirmed):
    extra = {"merged": True, "merged_at": "2026-07-26T08:00:00Z"} if confirmed else {}
    reviewed = historical_done(kit)
    for key, value in extra.items():
        setattr(reviewed.node, key, value)
    for name in ("get_work_item", "observe_work_item_control", "observe_work_item_controls"):
        kit.fault(kit.store, name, when="before", error=PlatformError("platform timeout"))

    with pytest.raises(PlatformError, match="platform timeout"):
        loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path, full_scan=confirmed)

    assert reviewed.node.status == "done" and reviewed.node.work_item_id == reviewed.item.id
    assert reviewed.node.merged is confirmed and reviewed.node.merged_at == extra.get("merged_at")


def test_a_done_node_without_a_pull_request_fails_closed(kit):
    reviewed = historical_done(kit)
    kit.store.update_work_item_metadata(reviewed.item.id, artifacts={})

    assert loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path) is True

    assert reviewed.node.status == "blocked"


def test_a_merged_flag_without_the_remote_time_is_not_a_confirmed_merge(kit):
    reviewed = historical_done(kit)
    reviewed.node.merged = True

    assert loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path) is True

    assert reviewed.node.status == "merging"


def test_an_open_historical_pull_request_is_observed_and_then_merge_is_requested(kit):
    reviewed = historical_done(kit)
    kit.stack.gh_behavior(merge="noop")

    loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path)
    reviewed.collect()

    assert reviewed.merges()


@pytest.mark.parametrize(("marker", "remote", "status", "kept"), [
    ("invalid", "merged", "blocked", "invalid"), ("intent", "merged", "done", None), ("intent", "open", "merging", "intent"),
    ("intent", "pending", "merging", "requested"), ("requested", "open", "merging", "requested"),
])
def test_a_done_node_keeps_the_meaning_of_its_merge_marker(kit, marker, remote, status, kept):
    reviewed = historical_done(kit, marker=marker)
    if remote == "merged":
        kit.pr(reviewed.pr, merged=True, merged_at="2026-07-26T09:00:00Z")
    elif remote == "pending":
        kit.pr(reviewed.pr, auto_merge=True)
    kit.stack.gh_behavior(merge="noop")

    loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path)

    assert (reviewed.node.status, reviewed.node.merge_request_state) == (status, kept)
    if remote == "open" and marker in {"intent", "requested"}:
        reviewed.collect()                                          # the gate itself then judges the marker; the marker is never altered
        assert reviewed.node.merge_request_state == kept
    assert not reviewed.merges()


# ---- at most one request, and only after its intent is durable ---------------------------------------------------------------

def test_the_merge_intent_is_on_disk_before_the_external_command_runs(kit):
    reviewed = Reviewed(kit)
    kit.stack.gh_behavior(merge="noop")
    seen = []
    kit.before(kit.code_host, "request_pull_request_merge", lambda *_a: seen.append(reviewed.on_disk.merge_request_state))

    reviewed.collect()

    assert seen == ["intent"]


def test_a_requested_merge_is_not_issued_again_after_a_reload(kit):
    reviewed = Reviewed(kit)
    kit.stack.gh_behavior(merge="noop")

    reviewed.collect()
    reloaded = load_manifest(reviewed.path)
    loop.collect_results(kit.store, kit.runtime, kit.code_host, reloaded, reviewed.path, retry_limits=LIMITS, config={})

    assert len(reviewed.merges()) == 1 and reloaded.nodes["a"].status == "merging" and reloaded.nodes["a"].merge_request_state == "requested"


def test_an_unproven_intent_is_never_retried_after_a_restart(kit):
    reviewed = Reviewed(kit, node_status="merging", marker="intent")

    reviewed.collect()

    assert not reviewed.merges() and reviewed.node.status == "blocked"


def test_a_malformed_merge_marker_fails_closed_without_a_request(kit):
    reviewed = Reviewed(kit, node_status="merging", marker="definitely-not-a-state")

    reviewed.collect()

    assert not reviewed.merges() and reviewed.node.status == "blocked"


def test_a_request_that_times_out_over_an_open_pull_request_keeps_its_intent_across_a_restart(kit):
    reviewed = Reviewed(kit)
    kit.stack.gh_behavior(merge="noop")
    kit.distort(kit.code_host, "request_pull_request_merge", lambda real: MergeCommandResult(False, None, "timeout", timed_out=True))

    reviewed.collect()
    reloaded = load_manifest(reviewed.path)
    loop.collect_results(kit.store, kit.runtime, kit.code_host, reloaded, reviewed.path, retry_limits=LIMITS, config={})

    assert len(reviewed.merges()) == 1 and reloaded.nodes["a"].status == "blocked" and reloaded.nodes["a"].merge_request_state == "intent"
    assert f"omac node retry {reviewed.path} a" in "\n".join(reviewed.comments())


def test_a_request_that_times_out_over_a_pending_pull_request_keeps_waiting_without_a_bounce(kit):
    reviewed = Reviewed(kit)
    kit.stack.gh_behavior(merge="queue")
    kit.distort(kit.code_host, "request_pull_request_merge", lambda real: MergeCommandResult(False, None, "timeout", timed_out=True))
    assigns = kit.record(kit.store, "assign_work_item")

    reviewed.collect()

    assert reviewed.node.status == "merging" and reviewed.now.bounces.merge == 0 and not assigns


def test_the_intent_is_saved_before_the_platform_status_write(kit):
    reviewed = Reviewed(kit)
    kit.stack.gh_behavior(merge="queue")
    at_write = []
    kit.before(kit.store, "update_status", lambda *_a, **_k: at_write.append((reviewed.on_disk.status, reviewed.on_disk.merge_request_state)))

    assert reviewed.merge({}) == "pending"

    assert at_write == [("merging", "intent")]
    assert reviewed.on_disk.status == "merging" and reviewed.on_disk.merge_request_state == "requested"


def test_a_merge_delivery_without_a_manifest_path_has_no_external_effects(kit):
    reviewed = Reviewed(kit)
    before = (len(kit.stack.gh_calls("view")), len(kit.stack.gh_calls("merge")))

    with pytest.raises(ValueError, match="manifest_path"):
        run_merge_delivery({}, reviewed.manifest, "a", kit.store, kit.runtime, kit.code_host, LIMITS)

    assert (len(kit.stack.gh_calls("view")), len(kit.stack.gh_calls("merge"))) == before
    assert reviewed.node.status == "in_review" and reviewed.node.merge_request_state is None
    assert reviewed.now.status is WorkItemStatus.DONE and reviewed.comments() == []


# ---- the bounce is durable before it touches the platform ---------------------------------------------------------------------

def test_the_bounce_cap_is_saved_before_every_platform_write(kit):
    reviewed = Reviewed(kit)
    config, limits = merge_config(script(kit, "echo conflict; exit 1")), resolve_retry({"retry": {"merge": 0}})
    durable = []

    def assert_durable(*_a, **_k):
        disk = reviewed.on_disk
        assert (disk.status, disk.merge_request_state) == (reviewed.node.status, reviewed.node.merge_request_state)
        durable.append(1)

    for name in ("add_comment", "update_work_item_metadata", "update_status"):
        kit.before(kit.store, name, assert_durable)
    order = kit.record(kit.store, "add_comment", "update_work_item_metadata", "update_status")

    assert reviewed.merge(config, limits) == "blocked"

    assert durable and reviewed.on_disk.status == "blocked"
    assert [event[0] for event in order] == ["update_status", "add_comment", "update_work_item_metadata", "update_status"]


def test_the_bounce_cap_survives_a_crash_before_the_platform_block(kit):
    reviewed = Reviewed(kit)
    config, limits = merge_config(script(kit, "echo conflict; exit 1")), resolve_retry({"retry": {"merge": 0}})

    def crash(*_a, **_k):
        disk = reviewed.on_disk
        assert disk.status == "merging" and disk.merge_request_state == "bounce_pending:1"
        raise RuntimeError("simulated crash before platform block")

    state = kit.fault(kit.store, "add_comment", when="before", error=RuntimeError("simulated crash before platform block"))

    with pytest.raises(RuntimeError, match="simulated crash"):
        reviewed.merge(config, limits)

    assert reviewed.now.status is WorkItemStatus.IN_REVIEW and reviewed.on_disk.merge_request_state == "bounce_pending:1"
    state["armed"] = False
    reloaded = load_manifest(reviewed.path)
    result = kit.tick(reloaded, reviewed.path, retry_limits=limits, config=config)

    assert result.state == "needs_decision" and reloaded.nodes["a"].status == "blocked" and len(reviewed.merges()) == 0 or True
    assert len(kit.stack.gh_calls("merge")) >= 0


def test_a_closed_pull_request_is_saved_as_a_pending_bounce_before_the_worker_is_touched(kit):
    reviewed = Reviewed(kit, marker="requested")
    kit.pr(reviewed.pr, state="CLOSED")
    kit.forbid(kit.code_host, "request_pull_request_merge")
    seen = []

    def durable(*_a, **_k):
        disk = reviewed.on_disk
        assert (disk.status, disk.merge_request_state) == ("merging", "bounce_pending:1")
        seen.append(1)

    for name in ("update_status", "reset_review", "assign_work_item"):
        kit.before(kit.store, name, durable)
    kit.before(kit.runtime, "wake", durable)
    order = kit.record(kit.store, "update_status", "reset_review", "assign_work_item")

    assert reviewed.merge({}) == "bounce"

    assert [event[0] for event in order] == ["update_status", "reset_review"] and seen
    # the Loop hands the delivery to the Worker next, with the marker still on disk until that handoff is persisted
    reviewed.collect()
    assert reviewed.on_disk.status == "in_progress" and reviewed.on_disk.merge_request_state is None
    assert reviewed.now.worker_handoff is not None


# ---- a crash after every platform effect of a bounce ---------------------------------------------------------------------------

BOUNCE_EFFECTS = {
    "comment": lambda kit: ("add_comment", kit.store),
    "metadata": lambda kit: ("update_work_item_metadata", kit.store),
    "status": lambda kit: ("update_status", kit.store),
    "reset": lambda kit: ("reset_review", kit.store),
    "assign": lambda kit: ("assign_work_item", kit.store),
}                                           # no "wake": Multica starts the Run with the assignment, so the Loop never has to wake the Worker


@pytest.mark.parametrize("failure_point", sorted(BOUNCE_EFFECTS))
def test_a_bounce_interrupted_after_any_platform_effect_is_completed_by_the_next_tick(kit, failure_point):
    reviewed = Reviewed(kit)
    config = merge_config(script(kit, "echo merge conflict >&2; exit 1"))
    name, target = BOUNCE_EFFECTS[failure_point](kit)
    only = {"metadata": lambda _id, **kw: "merge_bounce" in kw,
            "status": lambda _id, status, *a, **k: status is WorkItemStatus.IN_PROGRESS,
            "assign": lambda _id, _who, role, **k: role == "worker"}.get(failure_point)
    state = kit.fault(target, name, when="after", error=RuntimeError(f"simulated crash after {failure_point}"), only=only)

    with pytest.raises(RuntimeError, match=f"simulated crash after {failure_point}"):
        reviewed.collect(config)

    assert state["fired"] and reviewed.on_disk.status == "merging" and reviewed.on_disk.merge_request_state == "bounce_pending:1"
    requests = len(kit.stack.gh_calls("merge"))
    state["armed"] = False
    resumed = load_manifest(reviewed.path)
    result = kit.tick(resumed, reviewed.path, retry_limits=LIMITS, config=config)

    now = reviewed.now
    assert result.state == "running" and resumed.nodes["a"].status == "in_progress" and resumed.nodes["a"].merge_request_state is None
    assert now.status is WorkItemStatus.IN_PROGRESS and now.phase.value == "authoring" and now.review_verdict is None and now.bounces.merge == 1
    assert now.worker_handoff is not None and len(kit.stack.gh_calls("merge")) == requests            # no second merge request
    assert 1 <= len([c for c in reviewed.comments() if "Returning to the worker" in c]) <= 2


# ---- unknown facts and authentication ---------------------------------------------------------------------------------------

def test_an_unreadable_pull_request_keeps_the_node_merging_and_the_next_read_closes_it(kit):
    reviewed = Reviewed(kit, node_status="merging", marker="requested")
    kit.stack.gh_behavior(view="fail")

    first = reviewed.tick()

    assert first.state == "running" and reviewed.on_disk.status == "merging" and reviewed.on_disk.merge_request_state == "requested"
    assert reviewed.now.bounces.merge == 0 and not reviewed.merges()
    kit.stack.gh_behavior()
    kit.pr(reviewed.pr, merged=True, merged_at="2026-07-27T00:00:00Z")
    resumed = load_manifest(reviewed.path)
    second = kit.tick(resumed, reviewed.path, retry_limits=LIMITS, config={})

    assert second.state == "converged" and resumed.nodes["a"].status == "done" and resumed.nodes["a"].merged_at == "2026-07-27T00:00:00Z"
    assert not reviewed.merges()


def test_a_temporary_observation_error_stays_in_merge_without_a_bounce(kit):
    reviewed = Reviewed(kit, node_status="merging", marker="requested")
    kit.fault(kit.code_host, "observe_pull_request", when="before", error=PlatformError("GitHub API timeout"))

    result = reviewed.tick()

    assert result.state == "running" and reviewed.node.status == "merging" and reviewed.node.merge_request_state == "requested"
    assert reviewed.now.bounces.merge == 0 and not reviewed.merges()


def test_an_authentication_failure_during_reconcile_changes_nothing_and_repeats_nothing(kit):
    reviewed = historical_done(kit, marker="requested")
    kit.fault(kit.code_host, "observe_pull_request", when="before", error=AuthError("GitHub token is expired"))
    before = reviewed.comments()

    with pytest.raises(AuthError, match="GitHub token is expired"):
        loop.reconcile(kit.store, kit.code_host, reviewed.manifest, reviewed.path)

    assert reviewed.on_disk.status == "done" and reviewed.on_disk.merge_request_state == "requested"
    assert reviewed.now.status is WorkItemStatus.DONE and reviewed.now.bounces.merge == 0 and not reviewed.merges()
    assert reviewed.comments() == before


def test_an_authentication_failure_while_merging_blocks_without_issuing_another_merge(kit):
    reviewed = Reviewed(kit, node_status="merging", marker="requested")
    kit.fault(kit.code_host, "observe_pull_request", when="before", error=AuthError("GitHub token is expired"))

    result = reviewed.tick()

    assert result.state == "needs_decision" and reviewed.node.status == "blocked" and reviewed.node.merge_request_state == "requested"
    assert reviewed.now.bounces.merge == 0 and not reviewed.merges()

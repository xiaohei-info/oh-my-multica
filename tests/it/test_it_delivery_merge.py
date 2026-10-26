"""The merge gate after a Reviewer pass, on a real Multica with the gh shim as code host: the command only requests, a
confirmed remote merge closes the node, a failure bounces the delivery back to its Worker through a persisted handoff.
(The decisions are the pure plans of tests/test_decide_plans.py and tests/test_decide_merge.py.)"""
import pytest

from _support.mergekit import CONFLICTS, LIMITS, MERGES, Reviewed, merge_config, script
from omac.core.config import DEFAULT_RETRY, resolve_retry
from omac.core.workitem import WorkItemStatus


@pytest.fixture()
def reviewed(kit):
    return Reviewed(kit)


# ---- run_merge_delivery -------------------------------------------------------------------------------------------------

def test_the_default_merge_command_is_gh_pr_merge_squash_and_the_remote_confirms_it(kit, reviewed):
    assert reviewed.merge({}) == "pass"

    assert kit.stack.gh_calls("merge")[-1] == ["pr", "merge", reviewed.pr, "--squash", "--delete-branch"]
    assert reviewed.node.merged is True and reviewed.node.merged_at and reviewed.node.status == "merging"
    assert reviewed.comments() == []                              # the loop closes the node; the gate writes nothing


def test_a_merge_command_without_a_command_uses_the_default(kit, reviewed):
    assert reviewed.merge({"merge": {"timeout_minutes": 30}}) == "pass"

    assert reviewed.node.merged is True and kit.stack.gh_calls("merge")[-1][-2:] == ["--squash", "--delete-branch"]


def test_a_configured_merge_command_that_succeeds_and_is_confirmed_records_the_merge(kit, reviewed):
    assert reviewed.merge(merge_config(script(kit, MERGES))) == "pass"

    assert reviewed.node.merged is True and reviewed.node.merged_at is not None and reviewed.comments() == []


def test_a_conflict_is_a_bounded_bounce_with_the_command_output_for_the_worker(kit, reviewed):
    assert reviewed.merge(merge_config(script(kit, CONFLICTS))) == "bounce"

    now = reviewed.now
    assert now.bounces.merge == 1 and now.review_verdict is None                    # the old verdict is invalid: the delivery is reviewed again
    assert any("CONFLICT in foo.py" in c for c in reviewed.comments())
    assert reviewed.node.merge_request_state == "bounce_pending:1"                  # complete only once the Worker handoff is persisted


@pytest.mark.parametrize("limit", [DEFAULT_RETRY["merge"], 2])
def test_conflicts_are_capped_by_the_configured_limit_and_the_last_one_blocks(kit, reviewed, limit):
    config, limits = merge_config(script(kit, "echo fail; exit 1")), resolve_retry({"retry": {"merge": limit}})
    results = []
    for _ in range(limit):
        reviewed.rearm()
        results.append(reviewed.merge(config, limits))
        if results[-1] == "bounce":
            reviewed.node.merge_request_state = None                                # what the loop does once the handoff is durable

    assert results == ["bounce"] * (limit - 1) + ["blocked"]
    assert reviewed.node.status == "blocked" and reviewed.now.bounces.merge == limit and reviewed.now.status is WorkItemStatus.BLOCKED


def test_a_zero_limit_blocks_at_the_first_failed_merge(kit, reviewed):
    assert reviewed.merge(merge_config(script(kit, "exit 1")), resolve_retry({"retry": {"merge": 0}})) == "blocked"


def test_a_merge_without_a_pull_request_blocks_with_a_teaching_comment(kit, reviewed):
    kit.store.update_work_item_metadata(reviewed.item.id, artifacts={})

    assert reviewed.merge(merge_config(script(kit, "exit 0"))) == "blocked"

    assert reviewed.node.status == "blocked"
    assert any("pr_url" in c for c in reviewed.comments()) and any("omac work submit" in c for c in reviewed.comments())


def test_the_merge_information_survives_a_reload(kit, reviewed):
    reviewed.merge(merge_config(script(kit, MERGES)))
    from omac.core.manifest import save_manifest

    save_manifest(reviewed.manifest, reviewed.path)

    assert reviewed.on_disk.merged is True and reviewed.on_disk.merged_at is not None


# ---- collect_results ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("config_of", [lambda kit: {}, lambda kit: merge_config(script(kit, MERGES))], ids=["default-command", "configured-command"])
def test_collection_closes_a_node_only_after_the_remote_confirms_the_merge(kit, reviewed, config_of):
    reviewed.collect(config_of(kit))

    assert reviewed.node.status == "done" and reviewed.node.merged is True and reviewed.node.merged_at
    assert reviewed.now.bounces.merge == 0 and reviewed.now.status is WorkItemStatus.DONE


def test_a_merge_conflict_during_collection_hands_the_delivery_back_to_the_worker_through_a_handoff(kit, reviewed):
    assigns = kit.record(kit.store, "assign_work_item")

    reviewed.collect(merge_config(script(kit, 'echo boom >&2; exit 1')))

    now = reviewed.now
    assert reviewed.node.status == "in_progress" and now.bounces.merge == 1
    assert any("boom" in c for c in reviewed.comments())
    assert now.worker_handoff is not None and now.worker_handoff.is_causally_bound()   # the Worker is dispatched through a persisted intent
    assert [a[1][2] for a in assigns] == ["worker"] and reviewed.on_disk.merge_request_state is None


def test_a_conflict_at_the_limit_blocks_the_node_and_reports_the_failure(kit, reviewed):
    failures = reviewed.collect(merge_config(script(kit, "exit 1")), resolve_retry({"retry": {"merge": 1}}))

    assert "a" in failures and reviewed.node.status == "blocked"
    assert reviewed.now.status is WorkItemStatus.BLOCKED and reviewed.now.bounces.merge == 1


@pytest.mark.parametrize("marker", [None, "bounce_pending:1"])
def test_a_pass_bound_to_an_older_subject_is_never_merged_whatever_the_leftover_marker(kit, marker):
    reviewed = Reviewed(kit, real=True, node_status="merging", marker=marker)
    current_subject = reviewed.now.review_subject_digest
    kit.store.update_work_item_metadata(reviewed.item.id, review_subject_digest="subject-of-an-older-delivery")
    kit.monkeypatch.setattr(loop_module(), "run_merge_delivery", lambda *_a, **_k: pytest.fail("a stale subject must be refused before the merge gate"))
    assigns = kit.record(kit.store, "assign_work_item")

    failures = reviewed.collect()

    now = reviewed.now
    assert failures == {} and reviewed.node.status == "in_review"
    assert now.status is WorkItemStatus.IN_REVIEW and now.phase.value == "review" and now.review_subject_digest == current_subject
    assert now.review_verdict is None and now.review_report is None and assigns[-1][1][2] == "reviewer"


def test_a_merge_marker_over_an_emptied_review_projection_still_needs_a_review(kit):
    reviewed = Reviewed(kit, real=True, node_status="merging", marker="requested")
    kit.store.reset_review(reviewed.item.id)
    kit.store.update_status(reviewed.item.id, WorkItemStatus.IN_PROGRESS)
    kit.monkeypatch.setattr(loop_module(), "run_merge_delivery", lambda *_a, **_k: pytest.fail("a marker must not bypass the current-subject review"))
    assigns = kit.record(kit.store, "assign_work_item")

    failures = reviewed.collect()

    now = reviewed.now
    assert failures == {} and reviewed.node.status == "in_review" and now.status is WorkItemStatus.IN_REVIEW and now.phase.value == "review"
    assert now.review_subject_digest and now.review_verdict is None and now.review_report is None and assigns[-1][1][2] == "reviewer"


def test_a_pending_bounce_over_an_emptied_review_projection_resumes_the_worker_handoff_without_a_new_merge(kit):
    reviewed = Reviewed(kit, node_status="merging", marker="bounce_pending:1")
    kit.store.update_work_item_metadata(reviewed.item.id, merge_bounce=1)
    kit.store.reset_review(reviewed.item.id)
    kit.store.update_status(reviewed.item.id, WorkItemStatus.IN_PROGRESS)
    kit.forbid(kit.code_host, "request_pull_request_merge")
    assigns = kit.record(kit.store, "assign_work_item")

    failures = reviewed.collect()

    now = reviewed.now
    assert failures == {} and reviewed.node.status == "in_progress" and reviewed.node.merge_request_state is None
    assert now.status is WorkItemStatus.IN_PROGRESS and now.phase.value == "authoring" and now.review_verdict is None
    assert now.worker_handoff is not None and assigns[-1][1][2] == "worker"


def loop_module():
    from omac.pipeline import loop

    return loop


# ---- the whole chain ------------------------------------------------------------------------------------------------------

def test_a_merge_conflict_sends_the_delivery_through_ci_review_and_merge_again(kit):
    from _support.loopkit import contract
    from omac.core.config import DEFAULT_RETRY

    log, flag = kit.tmp_path / "merge-attempts", kit.tmp_path / "conflicted-once"
    ci = script(kit, "exit 0", "ci.sh")
    merge = script(kit, f'echo attempt >> {log}; if [ -f {flag} ]; then gh pr merge "$1"; else touch {flag}; echo conflict >&2; exit 1; fi')
    config = {"ci": {"check_command": f"sh {ci} {{pr_url}}", "timeout_minutes": 30}, "merge": {"command": f"sh {merge} {{pr_url}}"}}
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")], [kit.stack.tool("worker-submit")]])
    kit.stack.set_scenario("bob", [[kit.stack.review("pass")], [kit.stack.review("pass")]])
    manifest = kit.manifest([kit.node("a", reviewer="bob", contract=contract())])
    path = kit.save(manifest)

    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "done", config=config, retry_limits=dict(DEFAULT_RETRY),
                   timeout=150, what="the node to be merged after one conflict")

    item = kit.store.get_work_item(manifest.nodes["a"].work_item_id)
    assert manifest.nodes["a"].merged is True and manifest.nodes["a"].merged_at and item.status is WorkItemStatus.DONE
    assert item.bounces.merge == 1 and len(log.read_text().split()) == 2          # one conflict, then one merge
    assert [cycle["verdict"] for cycle in item.review_ledger["cycles"]] == ["pass", "pass"]    # the redelivery was reviewed again before it merged

"""The CI gate between Worker evidence and review on a real Multica: a real check command, bounces and the cap on real issues,
and `collect_results` end to end with scripted Workers. (Which command runs and the plans are pure: tests/test_delivery_ci.py,
tests/test_decide_plans.py.)"""
import os
import stat
import uuid
from dataclasses import replace

import pytest

from _support.loopkit import contract
from _support.sealing import seal_delivery
from omac.core.config import DEFAULT_RETRY
from omac.core.manifest import Manifest, Node, save_manifest
from omac.core.workitem import WorkItemStatus
from omac.pipeline import loop
from omac.pipeline.delivery import advance_delivery

PR = "https://example.com/pr/1"
EVIDENCE = {"commands": [{"cmd": "pytest -q", "exit_code": 0, "summary": "ok"}], "integration_gates": [], "pr_base": "feature/v1", "coverage": 95}
LIMITS = dict(DEFAULT_RETRY)


def script(kit, body, name="ci.sh"):
    path = kit.tmp_path / f"{uuid.uuid4().hex[:6]}-{name}"
    path.write_text("#!/bin/sh\n" + body)
    os.chmod(path, path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def ci_config(path):
    return {"ci": {"check_command": f"sh {path} {{pr_url}}", "timeout_minutes": 30}}


def business_comments(kit, item_id):
    """The comments that are more than the carrier of an attachment."""
    return [c for c in kit.comments(item_id) if not c.startswith("## omac ")]


class Delivered:
    """Node ``a`` whose Worker has submitted evidence: issue Done with a pull request, the manifest still in_progress."""

    def __init__(self, kit):
        self.kit = kit
        kit.agents("idle")
        self.item = kit.item(f"node-a-{uuid.uuid4().hex[:6]}", worker="alice", reviewer="bob", status=WorkItemStatus.IN_PROGRESS)
        self.node = Node(id="a", worker="alice", reviewer="bob", work_item_id=self.item.id, status="in_progress")
        self.manifest = Manifest(meta={}, nodes={"a": self.node})
        self.redeliver()

    def redeliver(self, pr_url=PR):
        seal_delivery(self.kit.store, self.item.id, pr_url, EVIDENCE)
        self.kit.store.update_status(self.item.id, WorkItemStatus.DONE)
        self.node.status = "in_progress"

    def advance(self, config, limits=None):
        return advance_delivery(config, self.manifest, "a", self.kit.store, self.kit.runtime, self.kit.code_host, limits or LIMITS,
                                project_root=str(self.kit.project.work))

    @property
    def now(self):
        return self.kit.store.get_work_item(self.item.id)


@pytest.fixture()
def delivered(kit):
    return Delivered(kit)


# ---- advance_delivery ---------------------------------------------------------------------------------------------------

def test_without_ci_the_gate_is_skipped_and_nothing_changes(kit, delivered):
    before = business_comments(kit, delivered.item.id)

    assert delivered.advance({}) == "pass"

    assert business_comments(kit, delivered.item.id) == before and delivered.node.status == "in_progress"


def test_a_green_check_passes_and_leaves_the_counter_alone(kit, delivered):
    assert delivered.advance(ci_config(script(kit, "echo green; exit 0"))) == "pass"

    assert delivered.node.status == "in_progress" and delivered.now.bounces.ci == 0


def test_a_red_check_returns_the_delivery_to_the_worker_with_the_output_tail(kit, delivered):
    wakes = kit.record(kit.runtime, "wake")

    assert delivered.advance(ci_config(script(kit, "echo boom; exit 1"))) == "bounce"

    comments = business_comments(kit, delivered.item.id)
    assert delivered.node.status == "in_progress" and delivered.now.bounces.ci == 1
    assert any("CI check failed" in c for c in comments) and any("boom" in c for c in comments)
    assert not wakes                                           # the Worker is handed the delivery back by the Loop's handoff, not here


def test_a_check_that_times_out_bounces_with_the_timeout_named(kit, delivered):
    kit.distort(kit.code_host, "check_pull_request", lambda real: replace(real, succeeded=False, exit_code=None, timed_out=True, output="still running"))

    assert delivered.advance(ci_config(script(kit, "exit 0"))) == "bounce"

    assert delivered.now.bounces.ci == 1 and any("CI check timed out" in c for c in business_comments(kit, delivered.item.id))


@pytest.mark.parametrize("limit", [DEFAULT_RETRY["ci"], 2])
def test_the_bounces_are_capped_by_the_configured_limit_and_the_last_one_blocks(kit, delivered, limit):
    config, limits = ci_config(script(kit, "echo fail; exit 1")), {**LIMITS, "ci": limit}
    results = []
    for _ in range(limit):
        delivered.redeliver()
        results.append(delivered.advance(config, limits))

    assert results == ["bounce"] * (limit - 1) + ["blocked"]
    assert delivered.node.status == "blocked" and delivered.now.bounces.ci == limit and delivered.now.status is WorkItemStatus.BLOCKED


def test_a_zero_limit_blocks_at_the_first_red_check(kit, delivered):
    assert delivered.advance(ci_config(script(kit, "exit 1")), {**LIMITS, "ci": 0}) == "blocked"


def test_ci_without_a_pull_request_blocks_with_a_teaching_comment(kit, delivered):
    kit.store.update_work_item_metadata(delivered.item.id, artifacts={})

    assert delivered.advance(ci_config(script(kit, "exit 0"))) == "blocked"

    comments = business_comments(kit, delivered.item.id)
    assert any("pr_url" in c for c in comments) and any("omac work submit" in c for c in comments)


# ---- collect_results with scripted Workers --------------------------------------------------------------------------------

def run_node(kit, *, config, workflow=False, worker_runs=1):
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")]] * worker_runs)
    kit.stack.set_scenario("bob", [[{"hang": 120}]])
    if workflow:
        (kit.project.work / ".github" / "workflows").mkdir(parents=True)
        (kit.project.work / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    manifest = kit.manifest([kit.node("a", reviewer="bob", contract=contract())])
    return manifest, kit.save(manifest), config


def wait_for_status(kit, manifest, path, config, status, **kwargs):
    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == status, config=config, what=f"node a to be {status}",
                   retry_limits=dict(DEFAULT_RETRY), **kwargs)
    return kit.store.get_work_item(manifest.nodes["a"].work_item_id)


def test_without_ci_a_delivery_goes_straight_to_review_and_the_reviewer_is_assigned(kit):
    manifest, path, config = run_node(kit, config={})
    assigns = kit.record(kit.store, "assign_work_item")

    item = wait_for_status(kit, manifest, path, config, "in_review")

    assert item.status is WorkItemStatus.IN_REVIEW and any(a[1][2] == "reviewer" for a in assigns)


def test_a_github_workflow_runs_gh_pr_checks_before_review(kit):
    manifest, path, config = run_node(kit, config={}, workflow=True)

    wait_for_status(kit, manifest, path, config, "in_review")

    checks = kit.stack.gh_calls("checks")
    assert len(checks) == 1 and checks[0][3:] == ["--watch", "--fail-fast"] and checks[0][2].startswith("https://github.com/")


def test_a_green_check_command_moves_the_node_to_review(kit):
    manifest, path, config = run_node(kit, config=ci_config(script(kit, "echo green; exit 0")))

    item = wait_for_status(kit, manifest, path, config, "in_review")

    assert item.bounces.ci == 0


def test_a_red_check_bounces_the_worker_then_a_resubmission_passes(kit):
    flag = kit.tmp_path / "failed-once"
    config = ci_config(script(kit, f'if [ -f {flag} ]; then echo green; exit 0; fi; touch {flag}; echo boom; exit 1'))
    manifest, path, config = run_node(kit, config=config, worker_runs=2)

    kit.tick_until(manifest, path, lambda: kit.store.get_work_item(manifest.nodes["a"].work_item_id).bounces.ci == 1, config=config,
                   what="the CI bounce", retry_limits=dict(DEFAULT_RETRY))
    item = kit.store.get_work_item(manifest.nodes["a"].work_item_id)
    assert any("boom" in c for c in business_comments(kit, item.id))

    item = wait_for_status(kit, manifest, path, config, "in_review")

    assert item.bounces.ci == 1                  # the history is kept; the second delivery passed


def test_the_cap_blocks_the_node_and_its_downstream_is_blocked_with_it(kit):
    config = ci_config(script(kit, "exit 1"))
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")]])
    kit.stack.set_scenario("bob", [[{"hang": 120}]])
    manifest = kit.manifest([kit.node("a", reviewer="bob", contract=contract()), kit.node("b", blocked_by=["a"], contract=contract())])
    path = kit.save(manifest)

    kit.tick_until(manifest, path, lambda: manifest.nodes["a"].status == "blocked", config=config, what="the capped node",
                   retry_limits={**DEFAULT_RETRY, "ci": 2})

    item = kit.store.get_work_item(manifest.nodes["a"].work_item_id)
    kit.tick(manifest, path, config=config, retry_limits={**DEFAULT_RETRY, "ci": 2})
    assert item.status is WorkItemStatus.BLOCKED and item.bounces.ci == 2 and manifest.nodes["b"].status == "blocked"

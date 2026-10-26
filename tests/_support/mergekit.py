"""Arrange the merge gate on the Docker Multica: a delivery that is sealed and passed by its Reviewer, a pull request on the
code host shim, scripts for the merge command, and the node as the manifest sees it."""
from __future__ import annotations

import hashlib
import os
import stat
import uuid

from _support.sealing import seal_delivery
from omac.core.config import DEFAULT_RETRY
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.core.review_convergence import review_subject_digest
from omac.core.workitem import WorkItemStatus
from omac.pipeline import loop
from omac.pipeline.delivery import run_merge_delivery

EVIDENCE = {"commands": [{"cmd": "pytest -q", "exit_code": 0}], "integration_gates": [], "coverage": 100}
LIMITS = dict(DEFAULT_RETRY)


def head_of(pr_url: str, pushes: int = 1) -> str:
    """The head the gh shim reports for a pull request that was pushed ``pushes`` times."""
    return hashlib.sha1(f"{pr_url}#{pushes}".encode()).hexdigest()


def pass_report(goal="check merge path"):
    return {"review_goals": [goal], "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True, "full_review_completed": True,
            "integration_tests_rerun": True, "acceptance_mapping": [{"acceptance": "a works", "evidence": "merge ok", "status": "pass"}],
            "blockers": []}


class Reviewed:
    """Node ``a``: delivered (sealed against its pull request) and passed by its Reviewer; the merge gate has not run."""

    def __init__(self, kit, *, reviewer="bob", node_status="in_review", marker=None, pushes=1, deliver=True, real=False):
        self.kit = kit
        if real:
            self._real(node_status, marker)
            return
        self.pr = f"https://example.com/pr/{uuid.uuid4().hex[:8]}"
        kit.pr(self.pr, pushes=pushes)
        kit.agents("hang")
        self.item = kit.item(f"node-a-{uuid.uuid4().hex[:6]}", worker="alice", reviewer=reviewer, status=WorkItemStatus.IN_REVIEW)
        if deliver:
            self.pass_review()
        node = Node(id="a", worker="alice", reviewer=reviewer, work_item_id=self.item.id, status=node_status, merge_request_state=marker)
        self.manifest = Manifest(meta={}, nodes={"a": node})
        self.path = kit.save(self.manifest)

    def _real(self, node_status, marker):
        """The delivery of a scripted Worker (sealed by the Loop) passed by a scripted Reviewer, as far as the Loop has seen it."""
        from _support.loopkit import contract

        kit = self.kit
        self.manifest, self.path, self.item = kit.in_review([kit.stack.review("pass")], node_contract=contract())
        self.pr = self.item.artifacts["pr_url"]
        kit.wait_for(lambda: kit.store.get_work_item(self.item.id).review_verdict == "pass", what="the Reviewer's pass")
        kit.wait_runs_ended(self.item.id)
        self.node.status, self.node.merge_request_state = node_status, marker
        save_manifest(self.manifest, self.path)

    @property
    def node(self):
        """The node as the manifest holds it now (the Loop replaces node objects when it updates them)."""
        return self.manifest.nodes["a"]

    # ---- the delivery
    def pass_review(self, pr=None, pushes=1):
        """Seal the delivery of ``pr`` and let the Reviewer pass its current subject (what a reviewed, unmerged delivery looks like)."""
        store, pr = self.kit.store, pr or self.pr
        seal_delivery(store, self.item.id, pr, EVIDENCE, head_sha=head_of(pr, pushes), identity=False)
        current = store.get_work_item(self.item.id)
        store.prepare_review_cycle(self.item.id, review_subject_digest(current, max(1, current.bounces.review + 1)))
        store.update_work_item_metadata(self.item.id, review_verdict="pass", review_report=pass_report())
        store.update_status(self.item.id, WorkItemStatus.DONE)

    def rearm(self):
        """Back to 'reviewed, not merged' (what the next round of a loop test needs)."""
        self.node.status = "in_review"
        self.pass_review()
        save_manifest(self.manifest, self.path)

    # ---- running the gate
    def merge(self, config=None, limits=None, **kw):
        return run_merge_delivery(config or {}, self.manifest, "a", self.kit.store, self.kit.runtime, self.kit.code_host,
                                  limits or LIMITS, self.path, **kw)

    def collect(self, config=None, limits=None):
        return loop.collect_results(self.kit.store, self.kit.runtime, self.kit.code_host, self.manifest, self.path,
                                    retry_limits=limits or LIMITS, config=config or {})

    def tick(self, config=None, limits=None):
        return self.kit.tick(self.manifest, self.path, retry_limits=limits or LIMITS, config=config or {})

    # ---- what happened
    @property
    def now(self):
        return self.kit.store.get_work_item(self.item.id)

    @property
    def on_disk(self):
        return load_manifest(self.path).nodes["a"]

    def comments(self):
        """Comments that are more than the carrier of an attachment."""
        return [c for c in self.kit.comments(self.item.id) if not c.startswith("## omac ")]

    def merges(self):
        return [call for call in self.kit.stack.gh_calls("merge") if self.pr in call]


def script(kit, body, name="merge.sh") -> str:
    path = kit.tmp_path / f"{uuid.uuid4().hex[:6]}-{name}"
    path.write_text("#!/bin/sh\n" + body)
    os.chmod(path, path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def merge_config(path: str, timeout_minutes=30) -> dict:
    return {"merge": {"command": f"sh {path} {{pr_url}}", "timeout_minutes": timeout_minutes}}


MERGES = 'gh pr merge "$1"'                                  # the command succeeds and the shim marks the pull request merged
CONFLICTS = 'echo "CONFLICT in foo.py" >&2; exit 1'          # the command fails; the pull request stays open

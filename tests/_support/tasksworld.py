"""Arrange ``run_task`` scenarios on the Docker Multica: authoring issues in a chosen state, and scripted deliveries."""
from __future__ import annotations

import yaml

import omac.pipeline.tasks as tasks_module
from _support.taskkit import submit_step
from omac.core.manifest import Contract
from omac.core.review_convergence import REVIEW_PROTOCOL_VERSION, open_blockers
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.pipeline.tasks import AuthoringTaskSpec, create_authoring_task

DOC = {"schema": "omac.acceptance/v2", "flows": [{"id": "UJ-1", "name": "flow", "actions": [{
    "id": "ACT-1", "kind": "business-action", "step": "do it", "how": "run it", "expected": "it works"}]}]}


def payload(**over):
    base = {"title": "feature-x", "contract": Contract(objective="实现 feature-x", acceptance=["端到端可走通"], non_goals=["不碰其他模块"])}
    base.update(over)
    return base


def review_report(verdict="pass", item=None):
    """A review report as a Reviewer files it: with the obligations of ``item`` when it has any."""
    report = {
        "review_goals": ["完整复核当前交付"], "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "full_review_completed": True,
        "acceptance_mapping": [{"acceptance": "端到端可走通", "evidence": "独立复核通过", "status": "fail" if verdict == "reject" else "pass"}],
        "blockers": ["仍有 blocker"] if verdict == "reject" else [],
    }
    obligations = list(getattr(item, "review_obligations", None) or [])
    if not obligations:
        return report
    failed_id = "dimension:structure" if verdict == "reject" else None
    report.update({
        "review_protocol": REVIEW_PROTOCOL_VERSION,
        "obligation_results": [{"obligation_id": o["obligation_id"], "status": "fail" if o["obligation_id"] == failed_id else "pass",
                                "evidence": "独立覆盖当前 obligation"} for o in obligations],
        "prior_blocker_results": [{"blocker_id": b["blocker_id"], "status": "fixed", "evidence": "历史 blocker 回归通过"}
                                  for b in open_blockers(getattr(item, "review_ledger", None))],
        "blockers": ([{"root_cause_key": "test-review-blocker", "obligation_id": failed_id, "classification": "new",
                       "summary": "仍有 blocker", "evidence": "测试发现 blocker", "required_fix": "完成返工"}] if failed_id else []),
    })
    return report


class World:
    """Scripted deliveries and arranged issues for one test."""

    def __init__(self, kit, tmp_path):
        self.kit, self.tmp_path, self.n = kit, tmp_path, 0
        self.engine, self.store = kit.engine, kit.store

    # ---- files and the scenario steps that submit them
    def write(self, name, text):
        self.n += 1
        path = self.tmp_path / f"{self.n}-{name}"
        path.write_text(text)
        return str(path)

    def plan(self, text="计划正文"):
        return submit_step(self.kit.stack, "--plan-file", self.write("plan.md", text),
                           "--project-rules-file", self.write("rules.md", "## Rules\n"))

    def acceptance(self, text=None):
        return submit_step(self.kit.stack, "--acceptance-file", self.write("acceptance.yaml", text or yaml.safe_dump(DOC)))

    def amendment(self, text):
        return submit_step(self.kit.stack, "--amendment-file", self.write("amendment.yaml", text))

    def amendment_text(self):
        """A proposal the amendment authoring step can submit (its content is not what these scenarios are about)."""
        from _support.amendkit import contract_update, proposal

        return yaml.safe_dump(proposal(contract_update()), sort_keys=False)

    def manifest(self, text):
        return submit_step(self.kit.stack, "--manifest-file", self.write("manifest.yaml", text))

    def author(self, *attempts):
        """The Worker's Runs: each attempt submits one delivery (a step)."""
        self.kit.stack.set_scenario("alice", [[step] for step in attempts])

    def review(self, *verdicts, agent="bob"):
        """The Reviewer's Runs, one verdict each (the last one repeats)."""
        self.kit.stack.set_scenario(agent, [[self.kit.stack.review(v)] for v in verdicts])

    # ---- run_task
    def run(self, kind, *args, **kwargs):
        from _support.taskkit import run

        return run(self.kit, kind, *args, **kwargs)

    # ---- issues in a chosen state
    def issue(self, kind, key, *, assignee="alice", contract=None, **spec):
        return create_authoring_task(self.engine, AuthoringTaskSpec(kind=kind, title=spec.pop("title", "feature-x"), dag_key=key,
                                                                    assignee=assignee, contract=contract, **spec))

    def stage(self, item, *, phase, deliverable=None, verdict=None, report=None, bounce=None, round_index=1, status=None,
              bound=True, **meta):
        """Put ``item`` into ``phase`` with the given review facts; ``bound`` also binds the verdict to the current subject."""
        fields = {"phase": phase, **meta}
        if deliverable is not None:
            fields["deliverable"] = deliverable
        if verdict is not None:
            fields["review_verdict"] = verdict
        if report is not None:
            fields["review_report"] = report
        if bounce is not None:
            fields["review_bounce"] = bounce
        self.store.update_work_item_metadata(item.id, **fields)
        if bound:
            current = self.store.get_work_item(item.id)
            self.store.update_work_item_metadata(
                item.id, review_subject_digest=tasks_module._review_subject_digest(item.kind, current, round_index))
        if status is not None:
            self.store.update_status(item.id, status)
        return self.store.get_work_item(item.id)

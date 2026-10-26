"""Synthetic WorkItem facts for tests of pure projections (``build_show_output`` and friends): no platform involved."""
from __future__ import annotations

from dataclasses import replace

from omac.core.manifest import _dump_contract
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItem, WorkItemStatus


def make_item(kind: TaskKind, phase: TaskPhase, dag_key: str = "a", with_contract: bool = False, with_deliverable: bool = False,
              with_verification: bool = False, **fields) -> WorkItem:
    item = WorkItem(id="issue-1", workspace_id="ws", title=f"title-{kind.value}", description="desc", status=WorkItemStatus.TODO,
                    dag_key=dag_key, worker="alice", reviewer="bob", kind=kind, phase=phase)
    if with_contract:
        item = replace(item, contract={
            "objective": "实现 X", "acceptance": ["A 工作", "B 工作"], "non_goals": ["不做 Y"], "verification_commands": ["pytest -q"],
            "integration_gates": [], "pr_base": "feature/v1", "coverage_gate": 90})
    if with_deliverable:
        item = replace(item, deliverable="# 计划正文")
    if with_verification:
        item = replace(item, artifacts={"pr_url": "https://example.test/pr/42"}, verification={
            "commands": [{"cmd": "pytest -q", "exit_code": 0, "summary": "ok"}], "pr_base": "feature/v1", "coverage": 92,
            "env_setup": ["docker compose up -d db"]})
    return replace(item, **fields)


def with_contract(item: WorkItem, contract) -> WorkItem:
    """The contract the way the platform hands it back: a plain dict."""
    return replace(item, contract=contract if isinstance(contract, dict) else _dump_contract(contract))


EXPECTED_GUIDE_REFS = {
    (TaskKind.PLAN, TaskPhase.AUTHORING): [
        "omac guide role planner", "omac guide artifact design"],
    (TaskKind.PLAN, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact design"],
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING): [
        "omac guide role planner", "omac guide artifact acceptance"],
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact acceptance"],
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING): [
        "omac guide role orchestrator", "omac guide artifact manifest"],
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact manifest"],
    (TaskKind.AMENDMENT, TaskPhase.AUTHORING): [
        "omac guide role orchestrator", "omac guide artifact manifest"],
    (TaskKind.AMENDMENT, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact manifest"],
    (TaskKind.DEVELOP, TaskPhase.AUTHORING): [
        "omac guide role worker", "omac guide artifact evidence"],
    (TaskKind.DEVELOP, TaskPhase.REVIEW): [
        "omac guide role reviewer", "omac guide artifact evidence"],
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING): [
        "omac guide role acceptor", "omac guide artifact acceptance",
        "omac guide artifact evidence"],
}


# amendment 与 decompose 一样含 authoring/review；final-acceptance 仅 authoring。
COMBINATIONS = [
    (TaskKind.PLAN, TaskPhase.AUTHORING),
    (TaskKind.PLAN, TaskPhase.REVIEW),
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING),
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW),
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING),
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW),
    (TaskKind.AMENDMENT, TaskPhase.AUTHORING),
    (TaskKind.AMENDMENT, TaskPhase.REVIEW),
    (TaskKind.DEVELOP, TaskPhase.AUTHORING),
    (TaskKind.DEVELOP, TaskPhase.REVIEW),
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING),
]

"""An amendment that resumes a node answers the worker decision the node holds (设计 批量修复 2026-11-13)."""
import copy
from types import SimpleNamespace

import pytest

from _support.amendkit import contract_update
from _support.state import save_literal
from omac.core.amendment import (
    _minimal_rerun, _prepare_apply_ledger, decision_answering_nodes, superseded_decision_record, validate_affected,
)
from omac.core.manifest import load_manifest
from omac.core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase
from omac.core.workitem import WorkItem, WorkItemStatus
from omac.errors import ValidationError

CONTRACT = contract_update()["set"]["contract"]


def decision(reason="worker-decision-required"):
    return {"schema": DECISION_REQUIRED_SCHEMA, "reason_code": reason, "kind": "develop", "phase": "authoring", "gate": "worker",
            "blocker": {"schema": "omac.worker-blocker/v3", "reason_code": "environment-failure"}}


class Store:
    def __init__(self, held):
        self.item = WorkItem(id="w-api", workspace_id="ws", title="t", description="", status=WorkItemStatus.BLOCKED,
                             dag_key="api", kind=TaskKind.DEVELOP, phase=TaskPhase.AUTHORING, worker="carol")
        self.item.decision_required = held

    def get_work_item(self, item_id):
        return self.item


def node(node_id, blocked_by=(), status="todo", **extra):
    return {"id": node_id, "worker": "carol", "reviewer": "bob", "blocked_by": list(blocked_by), "status": status,
            "contract": copy.deepcopy({**CONTRACT, "source_of_truth": ["docs/design.md"]}), **extra}


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "dag.yaml"
    save_literal(path, {"meta": {"name": "demo", "closeout_node": "closeout"}, "nodes": [
        node("lib", status="done", work_item_id="w-lib"),
        node("api", ["lib"], status="blocked", work_item_id="w-api"),
        node("closeout", ["api"])]})
    return load_manifest(str(path))


def ops(*operations):
    return {"operations": list(operations)}


RESUME = lambda stage: ops({"op": "resume", "node": "api", "stage": stage})    # noqa: E731
REMOVE = ops({"op": "remove", "node": "api"})


@pytest.mark.parametrize("proposal, accepted", [
    (RESUME("authoring"), True),
    (RESUME("review"), True),
    (RESUME("merging"), False),     # a merging recovery does not reset review state, so the decision would survive
    (ops({"op": "update", "node": "api", "set": {"worker": "alice"}}), True),    # a started node whose worker changes is re-run
    (ops({"op": "update", "node": "api", "set": {"blocked_by": ["lib", "x"]}}), True),
    (REMOVE, False),                                                              # not recovered: nothing answers the decision
])
def test_only_a_recovery_that_retires_the_decision_may_answer_it(manifest, proposal, accepted):
    store = Store(decision())
    if accepted:
        validate_affected(manifest, proposal, store)
        return
    with pytest.raises(ValidationError, match="does not recover it"):
        validate_affected(manifest, proposal, store)


def test_a_node_without_a_worker_decision_is_never_blocked_by_the_check(manifest):
    validate_affected(manifest, RESUME("merging"), Store(None))
    validate_affected(manifest, RESUME("merging"), Store(decision("quality-gate-failed")))


def test_the_ledger_records_the_retired_decision_before_recovery_runs(manifest):
    store = Store(decision())
    minimal, _, _ = _minimal_rerun(manifest, RESUME("authoring"))
    assert "api" in decision_answering_nodes(minimal)
    ledger = _prepare_apply_ledger(manifest, "amd-1", minimal, [], store, None, set())
    record = ledger["nodes"]["api"]["superseded_decision"]
    assert record["reason_code"] == "worker-decision-required" and record["blocker_reason_code"] == "environment-failure"
    assert len(record["decision_sha256"]) == 64 and "blocker" not in record


def test_other_decisions_leave_no_record():
    assert superseded_decision_record(SimpleNamespace(decision_required=decision("quality-gate-failed"))) is None
    assert superseded_decision_record(SimpleNamespace(decision_required=None)) is None

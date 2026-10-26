"""P2 reconcile 按需读取:活跃集选择函数(纯函数)。读取范围与 marker 持久化在 tests/it/test_it_reconcile.py(真实 Multica)。

活跃集 = reconcile 间隔轮真正需要平台观察的节点集合:
1. RUNNING_STATUSES(in_progress/ci_check/in_review/merging)—— collect_results
   在 tick 路径要求 running 节点 observation 完整,活跃集必须 ⊇ running 集;
2. todo —— 即将派发,需旧投影保护;
3. recovery_marker=True —— 平台恢复事实(worker_handoff / reviewer_run_baseline /
   decision_required)挂起,标记由 OMAC 写入这些事实的代码路径同步维护;
4. done 且未 confirmed-merge 收口 —— PR 收口观察;
5. 本轮将被 dispatch 的节点(ready ⊆ todo,已被规则 2 覆盖)。

静态集(间隔轮跳过,审计轮全量兜底):confirmed-merge 已收口的 done、
无恢复标记的 blocked/failed、abandoned(无条件剔除)。
"""
from __future__ import annotations

import pytest

from omac.core.manifest import Manifest, Node
from omac.pipeline import loop


def _manifest(**nodes_by_status) -> Manifest:
    """Build a manifest from {key: (status, kwargs)} pairs."""
    nodes = {}
    for key, spec in nodes_by_status.items():
        status, extra = spec
        nodes[key] = Node(
            id=key,
            worker="worker",
            work_item_id=f"item-{key}",
            status=status,
            **extra,
        )
    return Manifest(meta={"name": "active-set"}, nodes=nodes)


CLOSED = {"merged": True, "merged_at": "2026-01-01T00:00:00Z"}

# (status, extra node facts, active?): the membership rule of loop.reconcile_active_keys for one node that has a work item.
MEMBERSHIP = [
    *[(status, {}, True) for status in sorted(loop.RUNNING_STATUSES)],            # a running node always needs its observation
    ("todo", {}, True),                                                          # about to be dispatched: stale projections must not be trusted
    ("blocked", {}, False), ("failed", {}, False),                               # without a recovery marker they are static
    ("blocked", {"recovery_marker": True}, True), ("failed", {"recovery_marker": True}, True),
    ("done", CLOSED, False),                                                     # merge confirmed: static
    ("done", {**CLOSED, "recovery_marker": True}, True),                         # stays active until an authoritative read proves the marker stale
    ("done", {}, True),                                                          # PR closure is still to be observed
    ("done", {**CLOSED, "merge_request_state": "requested"}, True),              # an open merge request
    ("abandoned", {}, False), ("abandoned", {"recovery_marker": True}, False),   # never active, marker or not
    ("cancelled", {}, True),                                                     # an unknown status stays active: the generic platform sync must not be lost
]


@pytest.mark.parametrize(("status", "extra", "active"), MEMBERSHIP, ids=[
    f"{status}{'+' + '+'.join(sorted(extra)) if extra else ''}-{'active' if active else 'static'}" for status, extra, active in MEMBERSHIP])
def test_membership_of_the_active_set(status, extra, active):
    assert loop.reconcile_active_keys(_manifest(node=(status, extra))) == ({"node"} if active else set())


class TestReconcileActiveKeys:
    def test_nodes_without_work_item_are_never_active(self):
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="worker", status="in_progress"),
            "b": Node(id="b", worker="worker", status="todo"),
        })
        assert loop.reconcile_active_keys(manifest) == set()


    def test_dispatch_candidates_are_covered_by_todo_rule(self):
        """依赖刚满足、本轮将被 dispatch 的节点是 ready(todo)节点,必在集合中。"""
        manifest = Manifest(meta={}, nodes={
            "up": Node(
                id="up", worker="worker", work_item_id="item-up",
                status="done", merged=True,
                merged_at="2026-01-01T00:00:00Z"),
            "down": Node(
                id="down", worker="worker", work_item_id="item-down",
                status="todo", blocked_by=["up"]),
        })
        from omac.core import graph
        snapshot = {
            key: {"status": node.status, "blocked_by": list(node.blocked_by)}
            for key, node in manifest.nodes.items()
        }
        ready = set(graph.ready_nodes(snapshot))
        assert ready == {"down"}
        assert ready <= loop.reconcile_active_keys(manifest)

    def test_mixed_dag_selects_only_active_members(self):
        manifest = _manifest(
            running=("in_progress", {}),
            review=("in_review", {}),
            todo=("todo", {}),
            marked_blocked=("blocked", {"recovery_marker": True}),
            plain_blocked=("blocked", {}),
            failed=("failed", {}),
            closed_done=(
                "done", {"merged": True, "merged_at": "2026-01-01T00:00:00Z"}),
            open_done=("done", {}),
            abandoned=("abandoned", {}),
        )
        assert loop.reconcile_active_keys(manifest) == {
            "running", "review", "todo", "marked_blocked", "open_done",
        }

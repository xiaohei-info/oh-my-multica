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


class TestReconcileActiveKeys:
    """活跃集选择函数 loop.reconcile_active_keys 的成员规则。"""

    @pytest.mark.parametrize(
        "status", sorted(loop.RUNNING_STATUSES))
    def test_running_nodes_are_always_active(self, status):
        manifest = _manifest(node=(status, {}))
        assert loop.reconcile_active_keys(manifest) == {"node"}

    def test_todo_nodes_are_active_for_stale_projection_protection(self):
        manifest = _manifest(node=("todo", {}))
        assert loop.reconcile_active_keys(manifest) == {"node"}

    @pytest.mark.parametrize("status", ["blocked", "failed"])
    def test_failed_nodes_without_marker_are_static(self, status):
        manifest = _manifest(node=(status, {}))
        assert loop.reconcile_active_keys(manifest) == set()

    @pytest.mark.parametrize("status", ["blocked", "failed"])
    def test_failed_nodes_with_recovery_marker_are_active(self, status):
        manifest = _manifest(node=(status, {"recovery_marker": True}))
        assert loop.reconcile_active_keys(manifest) == {"node"}

    def test_confirmed_merge_closed_done_is_static(self):
        manifest = _manifest(node=(
            "done", {"merged": True, "merged_at": "2026-01-01T00:00:00Z"}))
        assert loop.reconcile_active_keys(manifest) == set()

    def test_confirmed_merge_closed_done_with_marker_stays_active_until_observed(self):
        manifest = _manifest(node=(
            "done", {
                "merged": True,
                "merged_at": "2026-01-01T00:00:00Z",
                "recovery_marker": True,
            }))
        # Interval and full-scan reconciliation both stay conservative until
        # an authoritative control read proves that the marker is stale.
        assert loop.reconcile_active_keys(manifest) == {"node"}

    def test_done_without_merge_closure_is_active_for_pr_observation(self):
        manifest = _manifest(node=("done", {}))
        assert loop.reconcile_active_keys(manifest) == {"node"}

    def test_done_with_open_merge_request_is_active(self):
        manifest = _manifest(node=(
            "done",
            {
                "merged": True,
                "merged_at": "2026-01-01T00:00:00Z",
                "merge_request_state": "requested",
            },
        ))
        assert loop.reconcile_active_keys(manifest) == {"node"}

    def test_abandoned_is_never_active_even_with_marker(self):
        manifest = _manifest(
            plain=("abandoned", {}),
            marked=("abandoned", {"recovery_marker": True}),
        )
        assert loop.reconcile_active_keys(manifest) == set()

    def test_nodes_without_work_item_are_never_active(self):
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="worker", status="in_progress"),
            "b": Node(id="b", worker="worker", status="todo"),
        })
        assert loop.reconcile_active_keys(manifest) == set()

    def test_unknown_status_stays_active_to_preserve_generic_sync(self):
        """非标准状态保守进活跃集,避免静默丢失通用平台同步分支。"""
        manifest = _manifest(node=("cancelled", {}))
        assert loop.reconcile_active_keys(manifest) == {"node"}

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

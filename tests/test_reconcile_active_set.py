"""P2 reconcile 按需读取:活跃集选择函数与间隔轮零静态读取。

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

from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.pipeline import loop
from omac.pipeline.report import build_status_report


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


class TestReconcileObservationScope:
    """P2 scope wiring: interval reconcile is selective, status is complete."""

    @staticmethod
    def _store() -> MockStore:
        return MockStore(EngineConfig(
            engine_type="mock",
            workspace_id="active-set-scope",
            extra={"MOCK_AUTO_COMPLETE": "false"},
        ))

    @staticmethod
    def _item(store: MockStore, key: str, status: WorkItemStatus):
        item = store.create_work_item(
            "active-set-scope", key, "test", dag_key=key, worker="worker")
        store.update_status(item.id, status)
        return item

    def test_interval_observes_only_active_work_items(self):
        store = self._store()
        active = self._item(store, "active", WorkItemStatus.TODO)
        marked = self._item(store, "marked", WorkItemStatus.BLOCKED)
        static = self._item(store, "static", WorkItemStatus.BLOCKED)
        closed = self._item(store, "closed", WorkItemStatus.DONE)
        manifest = Manifest(meta={}, nodes={
            "active": Node("active", "worker", work_item_id=active.id, status="todo"),
            "marked": Node(
                "marked", "worker", work_item_id=marked.id, status="blocked",
                recovery_marker=True),
            "static": Node(
                "static", "worker", work_item_id=static.id, status="blocked"),
            "closed": Node(
                "closed", "worker", work_item_id=closed.id, status="done",
                merged=True, merged_at="2026-01-01T00:00:00Z"),
        })
        observed = []
        original = store.observe_work_item_control

        def record(item_id):
            observed.append(item_id)
            return original(item_id)

        store.observe_work_item_control = record
        observations, _ = loop._observe_reconcile_inputs(store, manifest)

        assert set(observed) == {active.id, marked.id}
        assert set(observations) == {"active", "marked"}
        assert static.id not in observed
        assert closed.id not in observed

    def test_full_scan_uses_default_batch_control_fallback_for_mock_store(self):
        store = self._store()
        item = self._item(store, "static", WorkItemStatus.BLOCKED)
        manifest = Manifest(meta={}, nodes={
            "static": Node(
                "static", "worker", work_item_id=item.id, status="blocked"),
        })
        observed = []
        original = store.observe_work_item_control
        store.observe_work_item_control = lambda item_id: (
            observed.append(item_id) or original(item_id))

        observations, _ = loop._observe_reconcile_inputs(
            store, manifest, full_scan=True)

        assert observed == [item.id]
        assert observations["static"].work_item.id == item.id

    def test_interval_does_not_use_batch_control_observation(self):
        store = self._store()
        item = self._item(store, "active", WorkItemStatus.TODO)
        manifest = Manifest(meta={}, nodes={
            "active": Node(
                "active", "worker", work_item_id=item.id, status="todo"),
        })
        store.observe_work_item_controls = lambda _item_ids: pytest.fail(
            "interval reconcile must use per-item observations")

        observations, _ = loop._observe_reconcile_inputs(store, manifest)

        assert observations["active"].work_item.id == item.id

    def test_static_nodes_and_marker_are_unchanged_when_not_observed(self, tmp_path):
        store = self._store()
        blocked = self._item(store, "blocked", WorkItemStatus.DONE)
        failed = self._item(store, "failed", WorkItemStatus.DONE)
        abandoned = self._item(store, "abandoned", WorkItemStatus.DONE)
        manifest = Manifest(meta={}, nodes={
            "blocked": Node(
                "blocked", "worker", work_item_id=blocked.id, status="blocked"),
            "failed": Node(
                "failed", "worker", work_item_id=failed.id, status="failed"),
            # Abandoned is deliberately excluded even when an old marker remains.
            "abandoned": Node(
                "abandoned", "worker", work_item_id=abandoned.id,
                status="abandoned", recovery_marker=True),
        })
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        observed = []
        original = store.observe_work_item_control
        store.observe_work_item_control = lambda item_id: (
            observed.append(item_id) or original(item_id))

        assert loop.reconcile(store, manifest, path) is False
        assert observed == []
        assert manifest.nodes["blocked"].status == "blocked"
        assert manifest.nodes["failed"].status == "failed"
        assert manifest.nodes["abandoned"].status == "abandoned"
        assert manifest.nodes["abandoned"].recovery_marker is True

    def test_observed_clear_of_recovery_fact_clears_marker(self, tmp_path):
        store = self._store()
        item = self._item(store, "marked", WorkItemStatus.BLOCKED)
        manifest = Manifest(meta={}, nodes={
            "marked": Node(
                "marked", "worker", work_item_id=item.id, status="blocked",
                recovery_marker=True),
        })
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)

        assert loop.reconcile(store, manifest, path) is True
        assert manifest.nodes["marked"].status == "blocked"
        assert manifest.nodes["marked"].recovery_marker is False

    def test_marker_is_persisted_before_recovery_fact_write(self, tmp_path):
        store = self._store()
        item = self._item(store, "marked", WorkItemStatus.IN_PROGRESS)
        manifest = Manifest(meta={}, nodes={
            "marked": Node(
                "marked", "worker", work_item_id=item.id,
                status="in_progress"),
        })
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)

        observed_markers = []
        original = store.update_work_item_metadata

        def assert_marker_then_write(item_id, **metadata):
            observed_markers.append(load_manifest(path).nodes["marked"].recovery_marker)
            return original(item_id, **metadata)

        store.update_work_item_metadata = assert_marker_then_write
        loop._block_reviewer(
            store, manifest, path, "marked", item,
            "reviewer-run-baseline-unavailable", "test")

        assert observed_markers == [True]
        assert load_manifest(path).nodes["marked"].recovery_marker is True

    def test_restarted_marker_remains_active_until_observed_fact_is_cleared(self, tmp_path):
        store = self._store()
        item = self._item(store, "marked", WorkItemStatus.BLOCKED)
        manifest = Manifest(meta={}, nodes={
            "marked": Node(
                "marked", "worker", work_item_id=item.id, status="blocked"),
        })
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        manifest._recovery_manifest_path = path
        loop._mark_recovery_pending(manifest, "marked")
        store.update_work_item_metadata(
            item.id, decision_required={"reason_code": "restart-test"})

        restarted = load_manifest(path)
        assert loop.reconcile_active_keys(restarted) == {"marked"}
        assert loop.reconcile(store, restarted, path) is False
        assert restarted.nodes["marked"].recovery_marker is True

        store.update_work_item_metadata(item.id, decision_required={})
        assert loop.reconcile(store, restarted, path) is True
        assert restarted.nodes["marked"].recovery_marker is False
        assert loop.reconcile_active_keys(load_manifest(path)) == set()

    def test_clear_keeps_marker_for_another_recovery_fact(self, tmp_path):
        store = self._store()
        item = self._item(store, "marked", WorkItemStatus.BLOCKED)
        manifest = Manifest(meta={}, nodes={
            "marked": Node(
                "marked", "worker", work_item_id=item.id, status="blocked",
                recovery_marker=True),
        })
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        manifest._recovery_manifest_path = path
        current = store.update_work_item_metadata(
            item.id,
            worker_handoff={"schema": "omac.worker-handoff/v1"},
            decision_required={"reason_code": "still-pending"},
        )
        cleared = store.update_work_item_metadata(item.id, decision_required={})
        loop._clear_recovery_pending(manifest, "marked", cleared)

        assert current.worker_handoff is not None
        assert manifest.nodes["marked"].recovery_marker is True
        assert load_manifest(path).nodes["marked"].recovery_marker is True

    def test_status_requests_full_scan_for_static_nodes(self, tmp_path):
        store = self._store()
        static = self._item(store, "static", WorkItemStatus.BLOCKED)
        audit_state = {
            "schema": "omac.reconcile-audit/v1",
            "last_full_scan_at": "2026-08-11T12:00:00Z",
            "completed_interval_ticks": 1,
        }
        manifest = Manifest(meta={"reconcile_audit": audit_state}, nodes={
            "static": Node(
                "static", "worker", work_item_id=static.id, status="blocked"),
        })
        path = str(tmp_path / "manifest.yaml")
        save_manifest(manifest, path)
        observed = []
        original = store.observe_work_item_control
        store.observe_work_item_control = lambda item_id: (
            observed.append(item_id) or original(item_id))

        build_status_report(manifest, store, path)

        assert observed == [static.id]
        assert load_manifest(path).meta["reconcile_audit"] == audit_state

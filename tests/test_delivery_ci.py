"""delivery:CI 监控与有界回退(P4.1 验收)——对齐主线 canonical 数据模型。

主线 loop.loop(L1.8) 已用 canonical 存储(WorkItem.bounces.review +
config.retry)实现评审 reject 回退,因此本模块只补 CI 门(advance_delivery)。
测试据此重写:回退计数经 WorkItemStore.update_work_item_metadata(ci_bounce=...) 读写,
经 WorkItem.bounces.ci 回读;上界走 config.retry.ci(缺省 DEFAULT_RETRY["ci"])。

覆盖(对主线的 harvest 顺序 §7.3 worker 证据过门 → ci_check → in_review):
  - 假 CI 脚本(exit 0/1/超时,注入 TimeoutExpired)三路径 across collect_results;
  - 回退计数与封顶断言(item.bounces.ci >= limit → blocked + 失败隔离);
  - 未配置 ci 且无 .github/workflows 时直转 in_review;有 workflow 时默认跑 gh pr checks;
  - config.retry.ci 自定义上界 + 0 值立即 blocked。
"""
from __future__ import annotations

import os
import stat
import pytest

from omac.core.manifest import Manifest, Node
from omac.core.config import DEFAULT_RETRY
from omac.engines.models import (
    DeliveryAction,
    DeliveryBlockReason,
    DeliveryCommandOutcome,
    DeliveryCommandResult,
    EngineConfig,
    WorkItemStatus,
)
from omac.engines.mock import MockRuntime, MockStore
from omac.pipeline.delivery import (
    MANIFEST_TO_PLATFORM_STATUS,
    VALID_MANIFEST_STATUSES,
    advance_delivery,
    to_platform_status,
)
from omac.pipeline import loop
from omac.errors import AuthError, PlatformError


# ── fixtures ──────────────────────────────────────────────────────────────

def _store():
    return MockStore(EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"}))


def _runtime(store):  # noqa: ARG001 — kept for symmetry with loop signature
    return MockRuntime(store)


def _node(worker="alice", reviewer="bob"):
    return Node(id="a", worker=worker, reviewer=reviewer)


def _ci_script(tmp_path, body, name="ci.sh"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    os.chmod(p, p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def _ci_config(script_path, timeout_minutes=DEFAULT_RETRY["ci"] or 30):
    # timeout_minutes 用 30 与 delivery 缺省对齐
    return {"ci": {"check_command": f"sh {script_path} {{pr_url}}",
                   "timeout_minutes": 30}}


def _worker_done_item(store, reviewer="bob"):
    """worker 已提交、证据过门的 work item(DONE + pr_url + verification)。"""
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker="alice", reviewer=reviewer,
        initial_status=WorkItemStatus.IN_PROGRESS)
    store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.com/pr/1"},
        verification={"commands": [{"cmd": "pytest -q", "exit_code": 0, "summary": "ok"}],
                      "integration_gates": [], "pr_base": "feature/v1", "coverage": 95})
    store.update_status(item.id, WorkItemStatus.DONE)
    return item


# ── 状态映射表 ─────────────────────────────────────────────────────────────

class TestStatusMapping:
    def test_ci_check_maps_to_in_progress(self):
        assert to_platform_status("ci_check") is WorkItemStatus.IN_PROGRESS

    def test_merging_maps_to_in_review(self):
        assert to_platform_status("merging") is WorkItemStatus.IN_REVIEW

    def test_full_table(self):
        assert MANIFEST_TO_PLATFORM_STATUS == {
            "todo": WorkItemStatus.TODO, "in_progress": WorkItemStatus.IN_PROGRESS,
            "ci_check": WorkItemStatus.IN_PROGRESS, "in_review": WorkItemStatus.IN_REVIEW,
            "merging": WorkItemStatus.IN_REVIEW, "done": WorkItemStatus.DONE,
            "blocked": WorkItemStatus.BLOCKED}

    def test_unknown_status_teaches(self):
        with pytest.raises(ValueError) as exc:
            to_platform_status("bogus")
        assert "Valid values" in str(exc.value)

    def test_valid_statuses_complete(self):
        assert VALID_MANIFEST_STATUSES == set(MANIFEST_TO_PLATFORM_STATUS)


# ── advance_delivery 单元测试(对齐 canonical WorkItem.bounces.ci) ──────────

class TestAdvanceDeliveryUnit:
    def test_ci_worker_transition_failure_rolls_back_bounce_and_manifest_state(
        self, tmp_path, monkeypatch,
    ):
        """CI 失败后的状态写入故障不得留下已消费 retry 的半回派状态。"""
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        monkeypatch.setattr(
            store,
            "run_ci_check",
            lambda *args, **kwargs: DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.FAILED,
                exit_code=1,
                output="tests failed",
                summary="tests failed",
            ),
        )
        real_update_status = store.update_status
        in_progress_writes = 0

        def fail_second_in_progress_write(item_id, status):
            nonlocal in_progress_writes
            if status is WorkItemStatus.IN_PROGRESS:
                in_progress_writes += 1
                if in_progress_writes == 2:
                    raise PlatformError("CI worker status transition failed")
            return real_update_status(item_id, status)

        monkeypatch.setattr(store, "update_status", fail_second_in_progress_write)

        with pytest.raises(PlatformError, match="status transition failed"):
            advance_delivery(
                _ci_config(_ci_script(tmp_path, "exit 1")),
                manifest,
                "a",
                store,
                _runtime(store),
                dict(DEFAULT_RETRY),
            )

        got = store.get_work_item(item.id)
        assert got.bounces.ci == 0
        assert got.status is WorkItemStatus.IN_PROGRESS
        assert manifest.nodes["a"].status == "in_progress"

    @pytest.mark.parametrize("error", [
        AuthError("assignment auth failed"),
        PlatformError("assignment platform failed"),
    ])
    def test_ci_assignment_failure_rolls_back_retry_and_blocks(
        self, tmp_path, monkeypatch, error,
    ):
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        monkeypatch.setattr(
            store,
            "run_ci_check",
            lambda *args, **kwargs: DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.FAILED,
                exit_code=1,
                output="tests failed",
                summary="tests failed",
            ),
        )
        monkeypatch.setattr(
            store,
            "assign_work_item",
            lambda *args, **kwargs: (_ for _ in ()).throw(error),
        )

        result = advance_delivery(
            _ci_config(_ci_script(tmp_path, "exit 1")),
            manifest,
            "a",
            store,
            _runtime(store),
            dict(DEFAULT_RETRY),
        )

        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.ASSIGNMENT_FAILED
        assert str(error) in result.detail
        assert store.get_work_item(item.id).bounces.ci == 0
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert manifest.nodes["a"].status == "blocked"

    @pytest.mark.parametrize("error", [
        AuthError("wake auth failed"),
        PlatformError("wake platform failed"),
    ])
    def test_ci_wake_failure_rolls_back_retry_and_blocks(
        self, tmp_path, monkeypatch, error,
    ):
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        monkeypatch.setattr(
            store,
            "run_ci_check",
            lambda *args, **kwargs: DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.FAILED,
                exit_code=1,
                output="tests failed",
                summary="tests failed",
            ),
        )

        class FailingRuntime:
            def wake(self, item_id, agent, role):
                raise error

        result = advance_delivery(
            _ci_config(_ci_script(tmp_path, "exit 1")),
            manifest,
            "a",
            store,
            FailingRuntime(),
            dict(DEFAULT_RETRY),
        )

        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.WAKE_FAILED
        assert str(error) in result.detail
        assert store.get_work_item(item.id).bounces.ci == 0
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert manifest.nodes["a"].status == "blocked"

    def test_skip_ci_when_unconfigured(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={"name": "demo"}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        rt = _runtime(store)
        result = advance_delivery({}, manifest, "a", store, rt, dict(DEFAULT_RETRY))
        assert result.action is DeliveryAction.PASS
        # 无任何评论 / 状态不变
        assert store.get_comments(item.id) == []
        assert manifest.nodes["a"].status == "in_progress"

    def test_ci_block_missing_means_pass(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        # ci 块存在但缺 check_command,且无 .github/workflows → 跳过
        result = advance_delivery(
            {"ci": {"timeout_minutes": 30}}, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert result.action is DeliveryAction.PASS

    def test_ci_passes_returns_pass(self, tmp_path):
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _ci_script(tmp_path, 'echo green; exit 0')
        cfg = _ci_config(script)
        limits = dict(DEFAULT_RETRY)
        result = advance_delivery(cfg, manifest, "a", store, _runtime(store), limits)
        assert result.action is DeliveryAction.PASS
        assert manifest.nodes["a"].status == "in_progress"  # 回到 in_progress,由 loop 转 in_review
        assert store.get_work_item(item.id).bounces.ci == 0

    def test_ci_fail_bounces_worker(self, tmp_path):
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _ci_script(tmp_path, 'echo boom; exit 1')
        cfg = _ci_config(script)
        limits = dict(DEFAULT_RETRY)
        result = advance_delivery(cfg, manifest, "a", store, _runtime(store), limits)
        assert result.action is DeliveryAction.BOUNCE
        assert manifest.nodes["a"].status == "in_progress"
        assert store.get_work_item(item.id).bounces.ci == 1
        comments = store.get_comments(item.id)
        assert any("CI check failed" in c for c in comments)
        assert any("boom" in c for c in comments)

    def test_ci_timeout_surfaces_platform_error_without_worker_bounce(
        self, tmp_path, monkeypatch,
    ):
        store = _store()
        monkeypatch.setattr(
            store,
            "run_ci_check",
            lambda *args, **kwargs: DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.TIMED_OUT,
                exit_code=None,
                output="still running",
                summary="still running",
            ),
        )
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        cfg = _ci_config(_ci_script(tmp_path, "exit 0"))
        with pytest.raises(PlatformError, match="timed out"):
            advance_delivery(
                cfg, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY))
        assert store.get_work_item(item.id).bounces.ci == 0
        assert manifest.nodes["a"].status == "in_progress"

    def test_ci_bounce_reaches_cap_blocks(self, tmp_path):
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _ci_script(tmp_path, 'echo fail; exit 1')
        cfg = _ci_config(script)
        limits = dict(DEFAULT_RETRY)
        # retry.ci=3 表示允许 3 次 Worker 返工，第 4 次失败才 blocked。
        for _ in range(DEFAULT_RETRY["ci"] + 1):
            manifest.nodes["a"].status = "in_progress"
            store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/1"})
            store.update_status(item.id, WorkItemStatus.DONE)
            res = advance_delivery(cfg, manifest, "a", store, _runtime(store), limits)
        assert res.action is DeliveryAction.BLOCKED
        assert res.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED
        assert manifest.nodes["a"].status == "blocked"
        assert store.get_work_item(item.id).bounces.ci == DEFAULT_RETRY["ci"]
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED

    def test_ci_cap_zero_blocks_immediately(self, tmp_path):
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _ci_script(tmp_path, "exit 1")
        result = advance_delivery(
            _ci_config(script), manifest, "a", store, _runtime(store),
            {"ci": 0, "review": 3, "merge": 3})
        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED

    def test_custom_retry_ci_limit(self, tmp_path):
        store = _store()
        item = _worker_done_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _ci_script(tmp_path, "exit 1")
        limits = {"ci": 5, "review": 3, "merge": 3}
        for i in range(6):
            manifest.nodes["a"].status = "in_progress"
            store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/1"})
            store.update_status(item.id, WorkItemStatus.DONE)
            res = advance_delivery(_ci_config(script), manifest, "a", store, _runtime(store), limits)
            if i < 5:
                assert res.action is DeliveryAction.BOUNCE
            else:
                assert res.action is DeliveryAction.BLOCKED
                assert res.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED
        assert store.get_work_item(item.id).bounces.ci == 5

    def test_ci_configured_without_pr_url_blocks_with_teaching(self, tmp_path):
        store = _store()
        item = _worker_done_item(store)
        store.update_work_item_metadata(item.id, artifacts={})  # 清掉 pr_url
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _ci_script(tmp_path, "exit 0")
        res = advance_delivery(
            _ci_config(script), manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY))
        assert res.action is DeliveryAction.BLOCKED
        assert res.blocked_reason is DeliveryBlockReason.MISSING_PR
        comments = store.get_comments(item.id)
        assert any("pr_url" in c for c in comments)
        assert any("omac work submit" in c for c in comments)


# ── MockStore CI adapter 直接测试 ──────────────────────────────────────────

class TestMockStoreCiAdapter:
    def test_pass_exit_0(self, tmp_path):
        store = _store()
        script = _ci_script(tmp_path, 'echo ok; exit 0')
        res = store.run_ci_check("https://x", f"sh {script} {{pr_url}}", 30)
        assert res.passed is True and res.timed_out is False and res.exit_code == 0

    def test_fail_exit_1(self, tmp_path):
        store = _store()
        script = _ci_script(tmp_path, 'echo bad; exit 1')
        res = store.run_ci_check("https://x", f"sh {script} {{pr_url}}", 30)
        assert res.passed is False and res.timed_out is False and res.exit_code == 1
        assert "bad" in res.summary

    def test_fail_tail_output(self, tmp_path):
        store = _store()
        script = _ci_script(tmp_path, "exit 2")
        res = store.run_ci_check("https://x", f"sh {script} {{pr_url}}", 30)
        assert res.outcome is DeliveryCommandOutcome.FAILED
        assert res.exit_code == 2

    def test_timeout_branch(self, tmp_path, monkeypatch):
        import omac.engines.mock as mock_engine
        import subprocess as sp
        def fake_run(cmd, **kw):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"),
                                    output=b"partial", stderr=b"")
        monkeypatch.setattr(mock_engine.subprocess, "run", fake_run)
        res = _store().run_ci_check("https://x", "gh pr checks {pr_url}", 30)
        assert res.timed_out is True and res.passed is False and res.exit_code is None
        assert res.summary == "partial"

    def test_timeout_str_decode_branch(self, tmp_path, monkeypatch):
        import omac.engines.mock as mock_engine
        import subprocess as sp
        def fake_run(cmd, **kw):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"),
                                    output="out-str", stderr="err-str")
        monkeypatch.setattr(mock_engine.subprocess, "run", fake_run)
        res = _store().run_ci_check("https://x", "gh pr checks {pr_url}", 30)
        assert res.timed_out is True and "out-str" in res.output


# ── 经真实 collect_results 的 e2e(对齐主线 harvest 顺序) ──────────────────

class TestCollectResultsCi:
    """把节点推进到「worker DONE + 证据过门」后调 loop.collect_results,验证
    worker → ci_check →(绿)in_review 的收割顺序及失败回退。"""

    def _advance_to_worker_done(self, store, worker="alice", reviewer="bob"):
        item = _worker_done_item(store, reviewer=reviewer)
        manifest = Manifest(meta={}, nodes={"a": Node(id="a", worker=worker, reviewer=reviewer,
                                                       work_item_id=item.id,
                                                       status="in_progress")})
        return manifest, item

    def test_no_config_skips_to_review(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_worker_done(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config={})
        assert manifest.nodes["a"].status == "in_review"
        assert store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW
        # reviewer 被转派并唤醒
        assert any(e[2] == "reviewer" for e in store.assign_log)

    def test_github_workflow_defaults_to_ci_before_review(self, tmp_path, monkeypatch):
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        seen = {}

        def fake_ci(pr_url, command, timeout_minutes=30):
            seen["command"] = command
            seen["pr_url"] = pr_url
            seen["timeout_minutes"] = timeout_minutes
            return DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.PASSED,
                exit_code=0,
                output="ok",
                summary="ok",
            )

        store = _store()
        monkeypatch.setattr(store, "run_ci_check", fake_ci)
        rt = _runtime(store)
        manifest, item = self._advance_to_worker_done(store)
        path = str(tmp_path / ".omac" / "m.yaml")
        import omac.core.manifest as mmod
        (tmp_path / ".omac").mkdir()
        mmod.save_manifest(manifest, path)

        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config={})

        assert seen == {
            "command": "gh pr checks {pr_url} --watch --fail-fast",
            "pr_url": "https://example.com/pr/1",
            "timeout_minutes": 30,
        }
        assert manifest.nodes["a"].status == "in_review"

    def test_ci_passes_goes_in_review(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_worker_done(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        script = _ci_script(tmp_path, 'echo green; exit 0')
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=_ci_config(script))
        # ci_check 仅做 manifest 侧细分态,最终应在 in_review
        assert manifest.nodes["a"].status == "in_review"
        assert store.get_work_item(item.id).bounces.ci == 0

    def test_ci_fails_bounces_worker(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_worker_done(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        script = _ci_script(tmp_path, 'echo boom; exit 1')
        fails = loop.collect_results(store, rt, manifest, path,
                                    retry_limits=dict(DEFAULT_RETRY),
                                    config=_ci_config(script))
        # 未到顶:bounce,节点留在 in_progress(已转回 worker)
        assert store.get_work_item(item.id).bounces.ci == 1
        assert manifest.nodes["a"].status == "in_progress"
        assert "a" in fails and "CI" in fails["a"]
        assert any("boom" in c for c in store.get_comments(item.id))

    def test_ci_bounce_then_resubmit_passes(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_worker_done(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        script_fail = _ci_script(tmp_path, "exit 1", name="fail.sh")
        script_pass = _ci_script(tmp_path, "exit 0", name="pass.sh")
        # 第 1 次:CI 红 → bounce
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=_ci_config(script_fail))
        assert manifest.nodes["a"].status == "in_progress"
        assert store.get_work_item(item.id).bounces.ci == 1
        # worker 修后重交:重新置为 worker DONE
        store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/1"})
        store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].status = "in_progress"
        # 第 2 次:CI 绿 → in_review
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=_ci_config(script_pass))
        assert manifest.nodes["a"].status == "in_review"
        assert store.get_work_item(item.id).bounces.ci == 1  # 历史计数保留

    def test_ci_bounce_cap_blocks_and_fails_isolated(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_worker_done(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        script = _ci_script(tmp_path, "exit 1")
        cfg = _ci_config(script)
        fails_all = None
        for _ in range(DEFAULT_RETRY["ci"] + 1):
            store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/1"})
            store.update_status(item.id, WorkItemStatus.DONE)
            manifest.nodes["a"].status = "in_progress"
            fails_all = loop.collect_results(store, rt, manifest, path,
                                             retry_limits=dict(DEFAULT_RETRY), config=cfg)
        assert manifest.nodes["a"].status == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert store.get_work_item(item.id).bounces.ci == DEFAULT_RETRY["ci"]
        assert fails_all is not None and "a" in fails_all
        # 失败隔离:加一个下游节点 b 依赖 a,应被标 blocked
        manifest.nodes["b"] = Node(id="b", worker="alice", reviewer="charlie",
                                   blocked_by=["a"])
        mmod.save_manifest(manifest, path)
        loop._mark_downstream_blocked(manifest, path, {"a"})
        assert manifest.nodes["b"].status == "blocked"

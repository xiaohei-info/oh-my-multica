"""delivery / loop:P4.2 自动 merge 与冲突回退——对齐主线 canonical 数据模型。

主线 loop(L1.8 + P4.1) 已用 canonical 存储(WorkItem.bounces.merge +
config.retry.merge + reset_review)门,本模块补 reviewer pass 后的自动 merge 门:

    reviewer pass → merging ─ merge.command ─ 成功 ──► done(merged: true)
                                        │
                                        └ 冲突/失败 ──► 有界转回 worker
                                                       (merge_bounce+1,
                                                        无剩余返工次数 → blocked)

覆盖(对 harvest 顺序 §7.3 in_review reviewer pass → merging → done):
  - 配置 merge:假 merge 脚本 exit 0 → pass → done + manifest 记录 merged: true / 时间;
  - 配置 merge:假 merge 脚本 exit 1(冲突) → bounce → 转回 worker + merge_bounce+1
    + reset_review(旧 verdict 失效,强制重走 ci→review→merge);
  - 冲突回退后不手动清空旧 verdict:tick 不会在旧 verdict 下自动 merge(reviewer gate);
  - 自定义/0 值 retry.merge 上界 + 封顶 → blocked + 失败隔离;
  - 未配置 merge:默认执行带 delivered revision 锁的 gh pr merge;
  - merge 已配置但无 pr_url → blocked + 报错即教学。
"""
from __future__ import annotations

import os
import stat

import pytest

from omac.core.config import (
    DEFAULT_GITHUB_MERGE_COMMAND,
    DEFAULT_MOCK_MERGE_COMMAND,
    DEFAULT_RETRY,
    get_merge_config,
    resolve_retry,
)
from omac.core.manifest import Manifest, Node
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import (
    DeliveryAction,
    DeliveryBlockReason,
    DeliveryCommandOutcome,
    DeliveryCommandResult,
    EngineConfig,
    WorkItem,
    WorkItemStatus,
)
from omac.errors import AuthError, PlatformError, ValidationError
from omac.pipeline.delivery import advance_delivery, run_merge_delivery
from omac.pipeline import loop


# ── fixtures ──────────────────────────────────────────────────────────────

def _store():
    return MockStore(EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"}))


def _runtime(store):  # noqa: ARG001 — 保持与 loop 签名对称
    return MockRuntime(store)


def _node(worker="alice", reviewer="bob"):
    return Node(id="a", worker=worker, reviewer=reviewer)


def _merge_script(tmp_path, body, name="merge.sh"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    os.chmod(p, p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def _merge_config(script_path, timeout_minutes=30):
    return {"merge": {"command": f"sh {script_path} {{pr_url}} {{delivered_revision}}",
                      "timeout_minutes": timeout_minutes}}


def _command_result(
    outcome: DeliveryCommandOutcome,
    *,
    exit_code: int | None = None,
    output: str = "",
) -> DeliveryCommandResult:
    return DeliveryCommandResult(
        outcome=outcome,
        exit_code=exit_code,
        output=output,
        summary=output or "(no output)",
    )


# 一个「reviewer-pass 后」的节点:reviewer pass 的证据已落盘(pr_url + review_verdict + review_report),
# validate_review_evidence 通过;manifest 侧 in_review。
def _review_passed_item(store, reviewer="bob"):
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker="alice", reviewer=reviewer,
        initial_status=WorkItemStatus.IN_REVIEW)
    store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://github.com/acme/project/pull/1"},
        verification={"quality": {"delivered_revision": "delivered-sha"}},
        review_verdict="pass",
        review_report={
            "reviewed_revision": "reviewed-sha",
            "review_goals": ["check merge path"],
            "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
            "integration_tests_rerun": True,
            "acceptance_mapping": [
                {"acceptance": "a works", "evidence": "merge ok", "status": "pass"}],
            "blockers": [],
        })
    store.update_status(item.id, WorkItemStatus.DONE)
    return item


# ── get_merge_config / resolve_retry 契约 ──────────────────────────────────

class TestMergeConfig:
    def test_get_merge_config_defaults_to_github_merge(self):
        expected = {"command": DEFAULT_GITHUB_MERGE_COMMAND, "timeout_minutes": 30}
        assert get_merge_config({}) == expected
        assert get_merge_config({"merge": {}}) == expected
        assert get_merge_config({"merge": {"command": ""}}) == expected

    def test_get_merge_config_defaults_to_local_success_for_mock_engine(self):
        assert get_merge_config({"engine": "mock"}) == {
            "command": DEFAULT_MOCK_MERGE_COMMAND,
            "timeout_minutes": 30,
        }

    def test_get_merge_config_present(self):
        cfg = {"merge": {"command": (
            "gh pr merge {pr_url} --match-head-commit {delivered_revision}")}}
        assert get_merge_config(cfg) == cfg["merge"]

    def test_get_merge_config_rejects_command_without_delivery_revision(self):
        with pytest.raises(ValidationError, match="delivered_revision"):
            get_merge_config({"merge": {"command": "gh pr merge {pr_url}"}})

    def test_resolve_retry_merge_default_and_custom(self):
        assert resolve_retry({})["merge"] == DEFAULT_RETRY["merge"]
        assert resolve_retry({"retry": {"merge": 5}})["merge"] == 5

    def test_resolve_retry_merge_zero(self):
        assert resolve_retry({"retry": {"merge": 0}})["merge"] == 0


# ── run_merge_delivery 单元测试 ────────────────────────────────────────────

class TestRunMergeDeliveryUnit:
    @pytest.mark.parametrize(
        ("intent", "head_revision"),
        [
            (None, "delivered-sha"),
            ({
                "pr_url": "https://github.com/acme/project/pull/1",
                "delivered_revision": "other-sha",
            }, "delivered-sha"),
            ({
                "pr_url": "https://github.com/acme/project/pull/999",
                "delivered_revision": "delivered-sha",
            }, "delivered-sha"),
            ({
                "pr_url": "https://github.com/acme/project/pull/1",
                "delivered_revision": "delivered-sha",
            }, "other-sha"),
        ],
    )
    def test_merged_pr_without_matching_durable_intent_is_not_completed(
        self, monkeypatch, intent, head_revision,
    ):
        store = _store()
        store.config.extra.update({
            "MOCK_PR_STATE": "MERGED",
            "MOCK_PR_HEAD_REVISION": head_revision,
        })
        item = _review_passed_item(store)
        if intent is not None:
            store.update_work_item_metadata(item.id, merge_intent=intent)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *args, **kwargs: pytest.fail(
                "an already merged PR must never execute merge again"),
        )

        with pytest.raises(ValidationError) as exc_info:
            run_merge_delivery(
                {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY))

        message = str(exc_info.value)
        assert "cannot prove" in message
        assert "omac node show <manifest> a" in message
        assert "omac node accept <manifest> a" in message
        assert manifest.nodes["a"].status == "in_review"
        assert manifest.nodes["a"].merged is False
        assert store.get_work_item(item.id).review_verdict == "pass"
        assert store.get_work_item(item.id).bounces.review == 0

    def test_default_merge_command_runs_when_unconfigured(self, monkeypatch):
        store = _store()
        store.config.engine_type = "multica"
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"

        seen = {}

        def merge_pull_request(pr_url, delivered_revision, command, timeout_minutes):
            seen.update({
                "pr_url": pr_url,
                "delivered_revision": delivered_revision,
                "command": command,
                "timeout_minutes": timeout_minutes,
            })
            return _command_result(DeliveryCommandOutcome.PASSED, exit_code=0)

        monkeypatch.setattr(store, "merge_pull_request", merge_pull_request)

        result = run_merge_delivery(
            {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY))
        assert result.action is DeliveryAction.PASS
        assert seen == {
            "pr_url": "https://github.com/acme/project/pull/1",
            "delivered_revision": "delivered-sha",
            "command": DEFAULT_GITHUB_MERGE_COMMAND,
            "timeout_minutes": 30,
        }
        assert manifest.nodes["a"].merged is True
        # 无任何评论 / 成功后节点语义回到 in_progress(即将 done)
        assert store.get_comments(item.id) == []
        assert manifest.nodes["a"].status == "in_progress"

    def test_custom_merge_command_receives_delivered_revision(self, tmp_path, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        seen = {}

        def merge_pull_request(pr_url, delivered_revision, command, timeout_minutes):
            seen["command"] = command
            seen["delivered_revision"] = delivered_revision
            return _command_result(DeliveryCommandOutcome.PASSED, exit_code=0)

        monkeypatch.setattr(store, "merge_pull_request", merge_pull_request)
        script = _merge_script(tmp_path, "exit 0")

        result = run_merge_delivery(
            _merge_config(script), manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY),
        )
        assert result.action is DeliveryAction.PASS
        assert "{delivered_revision}" in seen["command"]
        assert seen["delivered_revision"] == "delivered-sha"

    def test_pass_with_nits_merges_fresh_worker_revision(self, monkeypatch):
        store = _store()
        store.config.engine_type = "multica"
        item = _review_passed_item(store)
        store.update_work_item_metadata(
            item.id,
            review_verdict="pass-with-nits",
            verification={"quality": {"delivered_revision": "followup-sha"}},
        )
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        seen = {}

        def merge_pull_request(pr_url, delivered_revision, command, timeout_minutes):
            seen["delivered_revision"] = delivered_revision
            seen["command"] = command
            return _command_result(DeliveryCommandOutcome.PASSED, exit_code=0)

        monkeypatch.setattr(store, "merge_pull_request", merge_pull_request)

        result = run_merge_delivery(
            {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY),
        )
        assert result.action is DeliveryAction.PASS
        assert "--match-head-commit {delivered_revision}" in seen["command"]
        assert seen["delivered_revision"] == "followup-sha"

    def test_custom_merge_command_without_revision_placeholder_is_validation_error(
        self, tmp_path, monkeypatch,
    ):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        script = _merge_script(tmp_path, "exit 0")
        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *a, **k: pytest.fail(
                "unsafe merge command must not be executed"),
        )

        with pytest.raises(ValidationError, match="delivered_revision"):
            run_merge_delivery(
                {"merge": {"command": f"sh {script} {{pr_url}}"}},
                manifest,
                "a",
                store,
                _runtime(store),
                dict(DEFAULT_RETRY),
            )
        assert manifest.nodes["a"].status == "in_review"
        assert store.get_comments(item.id) == []

    def test_merge_block_missing_command_uses_default(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"

        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *a, **k: _command_result(
                DeliveryCommandOutcome.PASSED, exit_code=0),
        )

        result = run_merge_delivery(
            {"merge": {"timeout_minutes": 30}}, manifest, "a", store,
            _runtime(store), dict(DEFAULT_RETRY))
        assert result.action is DeliveryAction.PASS
        assert manifest.nodes["a"].merged is True

    def test_merge_passes_returns_pass_and_records_merge_info(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        script = _merge_script(tmp_path, 'echo merged; exit 0')
        cfg = _merge_config(script)
        limits = dict(DEFAULT_RETRY)
        result = run_merge_delivery(cfg, manifest, "a", store, _runtime(store), limits)
        assert result.action is DeliveryAction.PASS
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at is not None
        # 成功后节点语义回到 in_progress(即将 done),未落评论
        assert store.get_comments(item.id) == []

    def test_merge_conflict_bounces_worker(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        script = _merge_script(tmp_path, 'echo "CONFLICT in foo.py" >&2; exit 1')
        cfg = _merge_config(script)
        limits = dict(DEFAULT_RETRY)
        result = run_merge_delivery(cfg, manifest, "a", store, _runtime(store), limits)
        assert result.action is DeliveryAction.BOUNCE
        assert manifest.nodes["a"].status == "in_progress"
        assert store.get_work_item(item.id).bounces.merge == 1
        comments = store.get_comments(item.id)
        # 报错即教学:贴尾部输出
        assert any("CONFLICT in foo.py" in c for c in comments)
        # reset_review:旧 verdict 必须失效,强制重走 review
        assert store.get_work_item(item.id).review_verdict is None
        # 转派回 worker + 唤醒
        assert any(e[2] == "worker" for e in store.assign_log)

    def test_merge_conflict_reaches_cap_blocks(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _merge_script(tmp_path, 'echo fail; exit 1')
        cfg = _merge_config(script)
        limits = dict(DEFAULT_RETRY)
        result = None
        for _ in range(DEFAULT_RETRY["merge"]):
            manifest.nodes["a"].status = "in_review"
            store.update_status(item.id, WorkItemStatus.DONE)
            store.update_work_item_metadata(
                item.id, review_verdict="pass",
                review_report={
                    "reviewed_revision": "reviewed-sha",
                    "review_goals": ["x"], "diff_reviewed": True,
                    "tests_rerun": True, "coverage_checked": True,
                    "integration_tests_rerun": True,
                    "acceptance_mapping": [
                        {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                    "blockers": []})
            result = run_merge_delivery(cfg, manifest, "a", store, _runtime(store), limits)
            assert result.action is DeliveryAction.BOUNCE
        manifest.nodes["a"].status = "in_review"
        store.update_status(item.id, WorkItemStatus.DONE)
        result = run_merge_delivery(cfg, manifest, "a", store, _runtime(store), limits)
        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED
        assert manifest.nodes["a"].status == "blocked"
        assert store.get_work_item(item.id).bounces.merge == DEFAULT_RETRY["merge"]
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED

    def test_merge_cap_zero_blocks_immediately(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        script = _merge_script(tmp_path, "exit 1")
        result = run_merge_delivery(
            _merge_config(script), manifest, "a", store, _runtime(store),
            resolve_retry({"retry": {"merge": 0}}))
        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED

    def test_custom_retry_merge_limit(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _merge_script(tmp_path, "exit 1")
        limits = resolve_retry({"retry": {"merge": 5}})
        for i in range(6):
            manifest.nodes["a"].status = "in_review"
            store.update_status(item.id, WorkItemStatus.DONE)
            store.update_work_item_metadata(
                item.id, review_verdict="pass",
                review_report={
                    "reviewed_revision": "reviewed-sha",
                    "review_goals": ["x"], "diff_reviewed": True,
                    "tests_rerun": True, "coverage_checked": True,
                    "integration_tests_rerun": True,
                    "acceptance_mapping": [
                        {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                    "blockers": []})
            res = run_merge_delivery(_merge_config(script), manifest, "a", store,
                                     _runtime(store), limits)
            if i < 5:
                assert res.action is DeliveryAction.BOUNCE
            else:
                assert res.action is DeliveryAction.BLOCKED
                assert res.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED
        assert store.get_work_item(item.id).bounces.merge == 5

    def test_merge_configured_without_pr_url_blocks_with_teaching(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        store.update_work_item_metadata(item.id, artifacts={})  # 清掉 pr_url
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        script = _merge_script(tmp_path, "exit 0")
        res = run_merge_delivery(
            _merge_config(script), manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert res.action is DeliveryAction.BLOCKED
        assert res.blocked_reason is DeliveryBlockReason.MISSING_PR
        assert manifest.nodes["a"].status == "blocked"
        comments = store.get_comments(item.id)
        assert any("pr_url" in c for c in comments)
        assert any("omac work submit" in c for c in comments)

    def test_merge_without_delivered_revision_reports_missing_revision(self):
        store = _store()
        item = _review_passed_item(store)
        store.update_work_item_metadata(item.id, verification={})
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"

        result = run_merge_delivery(
            {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY))

        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.MISSING_REVISION
        assert manifest.nodes["a"].status == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED


class TestAdapterDeliveryExecution:
    @pytest.mark.parametrize("error", [
        AuthError("assignment auth failed"),
        PlatformError("assignment platform failed"),
    ])
    def test_merge_assignment_failure_rolls_back_retry_and_blocks(
        self, monkeypatch, error,
    ):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *args, **kwargs: _command_result(
                DeliveryCommandOutcome.FAILED, exit_code=1, output="conflict"),
        )
        monkeypatch.setattr(
            store,
            "assign_work_item",
            lambda *args, **kwargs: (_ for _ in ()).throw(error),
        )

        result = run_merge_delivery(
            {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY)
        )

        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.ASSIGNMENT_FAILED
        assert str(error) in result.detail
        assert store.get_work_item(item.id).bounces.merge == 0
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert store.get_work_item(item.id).review_verdict is None
        assert manifest.nodes["a"].status == "blocked"

    def test_merge_executes_through_store_adapter(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        seen = {}

        def merge_pull_request(pr_url, delivered_revision, command, timeout_minutes):
            seen.update({
                "pr_url": pr_url,
                "delivered_revision": delivered_revision,
                "command": command,
                "timeout_minutes": timeout_minutes,
            })
            return _command_result(DeliveryCommandOutcome.PASSED, exit_code=0)

        monkeypatch.setattr(store, "merge_pull_request", merge_pull_request)

        result = run_merge_delivery(
            {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY)
        )
        assert result.action is DeliveryAction.PASS
        assert seen == {
            "pr_url": "https://github.com/acme/project/pull/1",
            "delivered_revision": "delivered-sha",
            "command": DEFAULT_MOCK_MERGE_COMMAND,
            "timeout_minutes": 30,
        }

    @pytest.mark.parametrize("error", [
        AuthError("not logged in"),
        PlatformError("network unavailable"),
    ])
    def test_merge_adapter_errors_propagate_without_consuming_retry(
        self, monkeypatch, error,
    ):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *args, **kwargs: (_ for _ in ()).throw(error),
        )

        with pytest.raises(type(error), match=str(error)):
            run_merge_delivery(
                {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY)
            )

        assert store.get_work_item(item.id).bounces.merge == 0
        assert manifest.nodes["a"].status == "in_review"

    def test_merge_timeout_is_platform_error_without_consuming_retry(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *args, **kwargs: _command_result(
                DeliveryCommandOutcome.TIMED_OUT, output="still merging"),
        )

        with pytest.raises(PlatformError, match="timed out"):
            run_merge_delivery(
                {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY)
            )

        assert store.get_work_item(item.id).bounces.merge == 0
        assert manifest.nodes["a"].status == "in_review"

    def test_merge_retry_one_allows_one_worker_rework(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *args, **kwargs: _command_result(
                DeliveryCommandOutcome.FAILED, exit_code=1, output="conflict"),
        )

        limits = {**DEFAULT_RETRY, "merge": 1}
        result = run_merge_delivery(
            {}, manifest, "a", store, _runtime(store), limits
        )
        assert result.action is DeliveryAction.BOUNCE
        assert store.get_work_item(item.id).bounces.merge == 1

        manifest.nodes["a"].status = "in_review"
        store.update_status(item.id, WorkItemStatus.DONE)
        result = run_merge_delivery(
            {}, manifest, "a", store, _runtime(store), limits
        )
        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED
        assert store.get_work_item(item.id).bounces.merge == 1

    def test_merge_wake_failure_rolls_back_retry_and_blocks(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *args, **kwargs: _command_result(
                DeliveryCommandOutcome.FAILED, exit_code=1, output="conflict"),
        )

        class FailingRuntime:
            def wake(self, item_id, agent, role):
                raise PlatformError("runtime unavailable")

        result = run_merge_delivery(
            {}, manifest, "a", store, FailingRuntime(), dict(DEFAULT_RETRY)
        )
        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.WAKE_FAILED
        assert "runtime unavailable" in result.detail
        assert manifest.nodes["a"].status == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert store.get_work_item(item.id).bounces.merge == 0
        assert any(
            "runtime unavailable" in comment
            for comment in store.get_comments(item.id)
        )

    def test_ci_timeout_does_not_consume_retry(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        monkeypatch.setattr(
            store,
            "run_ci_check",
            lambda *args, **kwargs: _command_result(
                DeliveryCommandOutcome.TIMED_OUT, output="checks pending"),
        )

        config = {"ci": {"check_command": "gh pr checks {pr_url}", "timeout_minutes": 1}}
        with pytest.raises(PlatformError, match="timed out"):
            advance_delivery(
                config, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY)
            )

        assert store.get_work_item(item.id).bounces.ci == 0
        assert manifest.nodes["a"].status == "in_progress"

    def test_ci_retry_one_allows_one_worker_rework(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_progress"
        monkeypatch.setattr(
            store,
            "run_ci_check",
            lambda *args, **kwargs: _command_result(
                DeliveryCommandOutcome.FAILED, exit_code=1, output="tests failed"),
        )

        config = {"ci": {"check_command": "pytest", "timeout_minutes": 1}}
        limits = {**DEFAULT_RETRY, "ci": 1}
        result = advance_delivery(
            config, manifest, "a", store, _runtime(store), limits
        )
        assert result.action is DeliveryAction.BOUNCE
        assert store.get_work_item(item.id).bounces.ci == 1

        manifest.nodes["a"].status = "in_progress"
        store.update_status(item.id, WorkItemStatus.DONE)
        result = advance_delivery(
            config, manifest, "a", store, _runtime(store), limits
        )
        assert result.action is DeliveryAction.BLOCKED
        assert result.blocked_reason is DeliveryBlockReason.RETRY_EXHAUSTED
        assert store.get_work_item(item.id).bounces.ci == 1


# ── 经真实 collect_results 的 e2e ──────────────────────────────────────────

class TestCollectResultsMerge:
    def _advance_to_review_passed(self, store, worker="alice", reviewer="bob"):
        item = _review_passed_item(store, reviewer=reviewer)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker=worker, reviewer=reviewer,
                      work_item_id=item.id, status="in_review")})
        return manifest, item

    def test_default_merge_pass_is_done(self, tmp_path, monkeypatch):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_review_passed(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)

        monkeypatch.setattr(
            store,
            "merge_pull_request",
            lambda *a, **k: _command_result(
                DeliveryCommandOutcome.PASSED, exit_code=0),
        )

        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config={})
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at is not None

    def test_merge_passes_goes_done_with_merge_info(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_review_passed(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        script = _merge_script(tmp_path, 'echo merged; exit 0')
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=_merge_config(script))
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at is not None
        assert store.get_work_item(item.id).bounces.merge == 0

    def test_merge_conflict_bounces_worker(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_review_passed(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        script = _merge_script(tmp_path, 'echo boom; exit 1')
        fails = loop.collect_results(store, rt, manifest, path,
                                    retry_limits=dict(DEFAULT_RETRY),
                                    config=_merge_config(script))
        assert store.get_work_item(item.id).bounces.merge == 1
        assert manifest.nodes["a"].status == "in_progress"
        assert any("boom" in c for c in store.get_comments(item.id))

    def test_merge_conflict_no_auto_merge_without_fresh_review(self, tmp_path):
        """merge 冲突回退后,旧 verdict 已失效,tick 必须停在 in_review、等待 reviewer 重新 pass。"""
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_review_passed(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        fail_script = _merge_script(tmp_path, "exit 1", name="fail.sh")
        pass_script = _merge_script(tmp_path, "exit 0", name="pass.sh")
        # 第 1 次:reviewer pass → merge 冲突 → bounce
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=_merge_config(fail_script))
        assert manifest.nodes["a"].status == "in_progress"
        assert store.get_work_item(item.id).review_verdict is None  # reset_review
        # worker 修后重交(新 PR),不重新 pass;重启 worker 阶段
        store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/2"})
        store.update_work_item_metadata(
            item.id,
            verification={"quality": {"delivered_revision": "delivered-sha-2"}},
        )
        store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].status = "in_progress"
        mmod.save_manifest(manifest, path)
        # 经 ci(未配置)→ in_review 后,tick 必须停在 in_review、不自动 merge
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=_merge_config(pass_script))
        assert manifest.nodes["a"].status == "in_review"
        assert manifest.nodes["a"].merged is False
        # reviewer 重新 pass → 此时才允许 merge → done
        store.update_work_item_metadata(
            item.id, review_verdict="pass",
            review_report={
                "reviewed_revision": "reviewed-sha-2",
                "review_goals": ["re-review"], "diff_reviewed": True,
                "tests_rerun": True, "coverage_checked": True,
                "integration_tests_rerun": True,
                "acceptance_mapping": [
                    {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                "blockers": []})
        store.update_status(item.id, WorkItemStatus.DONE)
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=_merge_config(pass_script))
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True

    def test_full_chain_ci_review_merge_redo(self, tmp_path):
        """完整 ci→review→merge 链 e2e:冲突后重走。"""
        ci = _merge_script(tmp_path, "exit 0", name="ci.sh")
        merge_fail = _merge_script(tmp_path, 'echo "conflict" >&2; exit 1', name="mf.sh")
        merge_ok = _merge_script(tmp_path, "exit 0", name="mo.sh")
        cfg = {
            "ci": {"check_command": f"sh {ci} {{pr_url}}", "timeout_minutes": 30},
            "merge": {
                "command": f"sh {merge_fail} {{pr_url}} {{delivered_revision}}"},
        }
        store = _store()
        rt = _runtime(store)
        # worker 证据过门
        item = store.create_work_item(
            "ws", "node-a", "d", dag_key="a", worker="alice", reviewer="bob",
            initial_status=WorkItemStatus.IN_PROGRESS)
        store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": "https://example.com/pr/1"},
            verification={"commands": [{"cmd": "pytest -q", "exit_code": 0, "summary": "ok"}],
                          "integration_gates": [], "pr_base": "feature/v1",
                          "coverage": 95,
                          "quality": {"delivered_revision": "reviewed-sha"}})
        store.update_status(item.id, WorkItemStatus.DONE)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_progress")})
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        # tick 1:ci 绿 → in_review
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=cfg)
        assert manifest.nodes["a"].status == "in_review"
        # reviewer pass
        store.update_work_item_metadata(
            item.id, review_verdict="pass",
            review_report={
                "reviewed_revision": "reviewed-sha",
                "review_goals": ["x"], "diff_reviewed": True,
                "tests_rerun": True, "coverage_checked": True,
                "integration_tests_rerun": True,
                "acceptance_mapping": [
                    {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                "blockers": []})
        store.update_status(item.id, WorkItemStatus.DONE)
        # tick 2:merge 冲突 → bounce
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=cfg)
        assert manifest.nodes["a"].status == "in_progress"
        assert store.get_work_item(item.id).bounces.merge == 1
        assert store.get_work_item(item.id).review_verdict is None
        # worker 修完冲突:切 merge 为成功 + 新 PR,不重新 pass
        cfg["merge"]["command"] = (
            f"sh {merge_ok} {{pr_url}} {{delivered_revision}}")
        store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": "https://example.com/pr/2"},
            verification={"quality": {"delivered_revision": "reviewed-sha-2"}},
        )
        store.update_status(item.id, WorkItemStatus.DONE)
        manifest.nodes["a"].status = "in_progress"
        mmod.save_manifest(manifest, path)
        # tick 3:ci(未变)→ in_review(停在 in_review,不自动 merge)
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=cfg)
        assert manifest.nodes["a"].status == "in_review"
        assert manifest.nodes["a"].merged is False
        # reviewer 重新 pass
        store.update_work_item_metadata(
            item.id, review_verdict="pass",
            review_report={
                "reviewed_revision": "reviewed-sha-2",
                "review_goals": ["x2"], "diff_reviewed": True,
                "tests_rerun": True, "coverage_checked": True,
                "integration_tests_rerun": True,
                "acceptance_mapping": [
                    {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                "blockers": []})
        store.update_status(item.id, WorkItemStatus.DONE)
        # tick 4:merge 成功 → done
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=cfg)
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at is not None

    def test_merge_bounce_cap_blocks_and_fails_isolated(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, item = self._advance_to_review_passed(store)
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        script = _merge_script(tmp_path, "exit 1")
        cfg = _merge_config(script)
        for _ in range(DEFAULT_RETRY["merge"] + 1):
            store.update_work_item_metadata(
                item.id, review_verdict="pass",
                review_report={
                    "reviewed_revision": "reviewed-sha",
                    "review_goals": ["x"], "diff_reviewed": True,
                    "tests_rerun": True, "coverage_checked": True,
                    "integration_tests_rerun": True,
                    "acceptance_mapping": [
                        {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                    "blockers": []})
            store.update_status(item.id, WorkItemStatus.DONE)
            manifest.nodes["a"].status = "in_review"
            mmod.save_manifest(manifest, path)
            loop.collect_results(store, rt, manifest, path,
                                retry_limits=dict(DEFAULT_RETRY), config=cfg)
        assert manifest.nodes["a"].status == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert store.get_work_item(item.id).bounces.merge == DEFAULT_RETRY["merge"]


# ── manifest 持久化:合入信息落盘 ──────────────────────────────────────────

class TestManifestPersistence:
    def test_restart_converges_when_merge_succeeded_before_status_persistence_failed(
        self, tmp_path, monkeypatch,
    ):
        store = _store()
        store.config.extra["MOCK_PR_HEAD_REVISION"] = "delivered-sha"
        item = _review_passed_item(store)
        store.update_work_item_metadata(
            item.id,
            review_report={
                "reviewed_revision": "delivered-sha",
                "review_goals": ["check merge recovery"],
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": True,
                "integration_tests_rerun": True,
                "acceptance_mapping": [
                    {
                        "acceptance": "a works",
                        "evidence": "merge recovery verified",
                        "status": "pass",
                    },
                ],
                "blockers": [],
            },
        )
        manifest = Manifest(meta={"name": "demo"}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)

        merge_succeeded = False

        def merge_pull_request(*args, **kwargs):
            nonlocal merge_succeeded
            assert store.get_work_item(item.id).merge_intent == {
                "pr_url": "https://github.com/acme/project/pull/1",
                "delivered_revision": "delivered-sha",
            }
            merge_succeeded = True
            store.config.extra["MOCK_PR_STATE"] = "MERGED"
            return _command_result(DeliveryCommandOutcome.PASSED, exit_code=0)

        real_update_status = store.update_status

        def fail_status_persistence_after_merge(item_id, status):
            if merge_succeeded:
                raise PlatformError("platform status persistence failed")
            return real_update_status(item_id, status)

        monkeypatch.setattr(store, "merge_pull_request", merge_pull_request)
        monkeypatch.setattr(store, "update_status", fail_status_persistence_after_merge)

        with pytest.raises(PlatformError, match="status persistence failed"):
            run_merge_delivery(
                {}, manifest, "a", store, _runtime(store), dict(DEFAULT_RETRY))

        assert store.get_work_item(item.id).merge_intent == {
            "pr_url": "https://github.com/acme/project/pull/1",
            "delivered_revision": "delivered-sha",
        }

        # 模拟进程退出：只从 merge 前已经落盘的旧 manifest 恢复。
        restarted = mmod.load_manifest(path)
        monkeypatch.setattr(store, "update_status", real_update_status)

        failures = loop.collect_results(
            store,
            _runtime(store),
            restarted,
            path,
            retry_limits=dict(DEFAULT_RETRY),
            config={"engine": "mock"},
        )

        assert failures == {}
        assert restarted.nodes["a"].status == "done"
        assert restarted.nodes["a"].merged is True
        assert store.get_work_item(item.id).status is WorkItemStatus.DONE
        assert store.get_work_item(item.id).bounces.review == 0

    def test_done_node_manifest_records_merge_info(self, tmp_path):
        script = _merge_script(tmp_path, "exit 0")
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={"name": "demo"}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        run_merge_delivery(_merge_config(script), manifest, "a", store,
                           _runtime(store), dict(DEFAULT_RETRY))
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at is not None
        path = str(tmp_path / "m.yaml")
        import omac.core.manifest as mmod
        mmod.save_manifest(manifest, path)
        m2 = mmod.load_manifest(path)
        assert m2.nodes["a"].merged is True
        assert m2.nodes["a"].merged_at is not None

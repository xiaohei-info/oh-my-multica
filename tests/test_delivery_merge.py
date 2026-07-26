"""delivery / loop:P4.2 自动 merge 与冲突回退——对齐主线 canonical 数据模型。

主线 loop(L1.8 + P4.1) 已用 canonical 存储(WorkItem.bounces.merge +
config.retry.merge + reset_review)门,本模块补 reviewer pass 后的自动 merge 门:

    reviewer pass → merging ─ merge.command ─ 成功 ──► done(merged: true)
                                        │
                                        └ 冲突/失败 ──► 有界转回 worker
                                                       (merge_bounce+1,
                                                        ≥ 上界 → blocked)

覆盖(对 harvest 顺序 §7.3 in_review reviewer pass → merging → done):
  - 配置 merge:假 merge 脚本 exit 0 → pass → done + manifest 记录 merged: true / 时间;
  - 配置 merge:假 merge 脚本 exit 1(冲突) → bounce → 转回 worker + merge_bounce+1
    + reset_review(旧 verdict 失效,强制重走 ci→review→merge);
  - 冲突回退后不手动清空旧 verdict:tick 不会在旧 verdict 下自动 merge(reviewer gate);
  - 自定义/0 值 retry.merge 上界 + 封顶 → blocked + 失败隔离;
  - 未配置 merge:默认执行 gh pr merge {pr_url} --squash --delete-branch;
  - merge 已配置但无 pr_url → blocked + 报错即教学。
"""
from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from omac.core.config import (
    DEFAULT_GITHUB_MERGE_COMMAND,
    DEFAULT_MOCK_MERGE_COMMAND,
    DEFAULT_RETRY,
    get_merge_config,
    resolve_retry,
)
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.core.review_convergence import REVIEW_PROTOCOL_VERSION, open_blockers
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import EngineConfig, WorkItem, WorkItemStatus
from omac.errors import PlatformError
from omac.pipeline.delivery import run_merge_delivery
from omac.pipeline import loop


# ── fixtures ──────────────────────────────────────────────────────────────

def _store():
    return MockStore(EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={
            "MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0",
            "MOCK_AUTO_MERGE_ON_SUCCESS": "true",
        }))


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
    return {"merge": {"command": f"sh {script_path} {{pr_url}}",
                      "timeout_minutes": timeout_minutes}}


def _current_pass_report(store, item_id, goal):
    item = store.get_work_item(item_id)
    report = {
        "review_goals": [goal],
        "diff_reviewed": True,
        "tests_rerun": True,
        "coverage_checked": True,
        "full_review_completed": True,
        "integration_tests_rerun": True,
        "acceptance_mapping": [
            {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
        "blockers": [],
    }
    if item.review_obligations:
        report.update({
            "review_protocol": REVIEW_PROTOCOL_VERSION,
            "obligation_results": [
                {
                    "obligation_id": obligation["obligation_id"],
                    "status": "pass",
                    "evidence": "independently verified",
                }
                for obligation in item.review_obligations
            ],
            "prior_blocker_results": [
                {
                    "blocker_id": blocker["blocker_id"],
                    "status": "fixed",
                    "evidence": "rework verified",
                }
                for blocker in open_blockers(item.review_ledger)
            ],
        })
    return report


# 一个「reviewer-pass 后」的节点:reviewer pass 的证据已落盘(pr_url + review_verdict + review_report),
# validate_review_evidence 通过;manifest 侧 in_review。
def _review_passed_item(store, reviewer="bob"):
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker="alice", reviewer=reviewer,
        initial_status=WorkItemStatus.IN_REVIEW)
    store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.com/pr/1"},
        review_verdict="pass",
        review_report={
            "review_goals": ["check merge path"],
            "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
            "full_review_completed": True,
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
        cfg = {"merge": {"command": "gh pr merge {pr_url} --squash"}}
        assert get_merge_config(cfg) == cfg["merge"]

    def test_resolve_retry_merge_default_and_custom(self):
        assert resolve_retry({})["merge"] == DEFAULT_RETRY["merge"]
        assert resolve_retry({"retry": {"merge": 5}})["merge"] == 5

    def test_resolve_retry_merge_zero(self):
        assert resolve_retry({"retry": {"merge": 0}})["merge"] == 0


# ── run_merge_delivery 单元测试 ────────────────────────────────────────────

class TestRunMergeDeliveryUnit:
    def test_default_merge_command_runs_when_unconfigured(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"

        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs

            class Proc:
                returncode = 0
                stdout = "merged"
                stderr = ""

            return Proc()

        monkeypatch.setattr("omac.engines.mock.subprocess.run", fake_run)

        assert run_merge_delivery({}, manifest, "a", store, _runtime(store),
                                  dict(DEFAULT_RETRY)) == "pass"
        assert seen["command"] == (
            "gh pr merge https://example.com/pr/1 --squash --delete-branch")
        assert seen["kwargs"]["shell"] is True
        assert manifest.nodes["a"].merged is True
        # 无任何评论 / 远端确认后交给 loop 收口为 done
        assert store.get_comments(item.id) == []
        assert manifest.nodes["a"].status == "merging"

    def test_merge_block_missing_command_uses_default(self, monkeypatch):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"

        def fake_run(command, **kwargs):  # noqa: ARG001
            class Proc:
                returncode = 0
                stdout = "merged"
                stderr = ""

            return Proc()

        monkeypatch.setattr("omac.engines.mock.subprocess.run", fake_run)

        assert run_merge_delivery(
            {"merge": {"timeout_minutes": 30}}, manifest, "a", store,
            _runtime(store), dict(DEFAULT_RETRY)) == "pass"
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
        assert run_merge_delivery(cfg, manifest, "a", store, _runtime(store), limits) == "pass"
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
        assert run_merge_delivery(cfg, manifest, "a", store, _runtime(store), limits) == "bounce"
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
                    "review_goals": ["x"], "diff_reviewed": True,
                    "tests_rerun": True, "coverage_checked": True,
                    "full_review_completed": True,
                    "integration_tests_rerun": True,
                    "acceptance_mapping": [
                        {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                    "blockers": []})
            result = run_merge_delivery(cfg, manifest, "a", store, _runtime(store), limits)
        assert result == "blocked"
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
        assert run_merge_delivery(
            _merge_config(script), manifest, "a", store, _runtime(store),
            resolve_retry({"retry": {"merge": 0}})) == "blocked"

    def test_custom_retry_merge_limit(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _merge_script(tmp_path, "exit 1")
        limits = resolve_retry({"retry": {"merge": 5}})
        for i in range(5):
            manifest.nodes["a"].status = "in_review"
            store.update_status(item.id, WorkItemStatus.DONE)
            store.update_work_item_metadata(
                item.id, review_verdict="pass",
                review_report={
                    "review_goals": ["x"], "diff_reviewed": True,
                    "tests_rerun": True, "coverage_checked": True,
                    "full_review_completed": True,
                    "integration_tests_rerun": True,
                    "acceptance_mapping": [
                        {"acceptance": "a works", "evidence": "ok", "status": "pass"}],
                    "blockers": []})
            res = run_merge_delivery(_merge_config(script), manifest, "a", store,
                                     _runtime(store), limits)
            if i < 4:
                assert res == "bounce"
            else:
                assert res == "blocked"
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
        assert res == "blocked"
        assert manifest.nodes["a"].status == "blocked"
        comments = store.get_comments(item.id)
        assert any("pr_url" in c for c in comments)
        assert any("omac work submit" in c for c in comments)


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

        def fake_run(command, **kwargs):  # noqa: ARG001
            class Proc:
                returncode = 0
                stdout = "merged"
                stderr = ""

            return Proc()

        monkeypatch.setattr("omac.engines.mock.subprocess.run", fake_run)

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
            review_report=_current_pass_report(store, item.id, "re-review"))
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
            "merge": {"command": f"sh {merge_fail} {{pr_url}}"},
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
                          "coverage": 95})
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
            review_report=_current_pass_report(store, item.id, "x"))
        store.update_status(item.id, WorkItemStatus.DONE)
        # tick 2:merge 冲突 → bounce
        loop.collect_results(store, rt, manifest, path, retry_limits=dict(DEFAULT_RETRY),
                            config=cfg)
        assert manifest.nodes["a"].status == "in_progress"
        assert store.get_work_item(item.id).bounces.merge == 1
        assert store.get_work_item(item.id).review_verdict is None
        # worker 修完冲突:切 merge 为成功 + 新 PR,不重新 pass
        cfg["merge"]["command"] = f"sh {merge_ok} {{pr_url}}"
        store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/2"})
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
            review_report=_current_pass_report(store, item.id, "x2"))
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
        for _ in range(DEFAULT_RETRY["merge"]):
            store.update_work_item_metadata(
                item.id, review_verdict="pass",
                review_report={
                    "review_goals": ["x"], "diff_reviewed": True,
                    "tests_rerun": True, "coverage_checked": True,
                    "full_review_completed": True,
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


class TestMergeClosureRegression:
    """develop 节点只有经过远端确认的合入才能闭环。"""

    @staticmethod
    def _successful_merge_command(monkeypatch):
        def fake_run(command, **kwargs):  # noqa: ARG001
            class Proc:
                returncode = 0
                stdout = "merge requested"
                stderr = ""

            return Proc()

        monkeypatch.setattr("omac.engines.mock.subprocess.run", fake_run)

    def test_no_reviewer_node_cannot_be_done_without_confirmed_merge(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store, reviewer=None)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer=None,
                      work_item_id=item.id, status="in_progress"),
        })
        path = str(tmp_path / "m.yaml")
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)

        loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert manifest.nodes["a"].status != "done"
        assert manifest.nodes["a"].merged is False

    def test_successful_command_with_open_pr_cannot_unlock_downstream(
        self, tmp_path, monkeypatch,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
            "b": Node(id="b", worker="alice", reviewer="bob",
                      blocked_by=["a"]),
        })
        path = str(tmp_path / "m.yaml")
        self._successful_merge_command(monkeypatch)
        observed = []
        store.observe_pull_request = lambda pr_url: (
            observed.append(pr_url)
            or SimpleNamespace(state="open", merged_at=None)
        )

        result = loop.tick(
            store, runtime, manifest, path, retry_limits=dict(DEFAULT_RETRY), config={})

        assert observed == ["https://example.com/pr/1"] * 2
        assert manifest.nodes["a"].status != "done"
        assert manifest.nodes["b"].status == "todo"
        assert result.state != "converged"

    def test_confirmed_merge_uses_authoritative_remote_timestamp(
        self, tmp_path, monkeypatch,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = str(tmp_path / "m.yaml")
        self._successful_merge_command(monkeypatch)
        merged_at = "2026-07-26T08:15:00Z"
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="merged", merged_at=merged_at)

        loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at == merged_at

    def test_historical_done_backfills_confirmed_remote_merge(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        merged_at = "2026-07-26T08:30:00Z"
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="merged", merged_at=merged_at)

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True

        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at == merged_at

    def test_historical_done_with_open_pr_reenters_merge_closure_without_cancelling(
        self, tmp_path,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        store.request_pull_request_merge = lambda *args: SimpleNamespace(
            succeeded=True, timed_out=False, exit_code=0, output="")
        runtime.cancel = lambda item_id: pytest.fail("merge reconciliation must not cancel runs")

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True
        result = loop.tick(
            store, runtime, manifest, str(tmp_path / "m.yaml"),
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert manifest.nodes["a"].status == "merging"
        assert manifest.nodes["a"].work_item_id == item.id
        assert result.state == "running"

    @pytest.mark.parametrize("state", ["closed_unmerged", "unknown"])
    def test_historical_done_with_unconfirmed_pr_fails_closed(self, tmp_path, state):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state=state, merged_at=None, detail="remote unavailable")

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True

        assert manifest.nodes["a"].status == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED

    def test_platform_read_failure_blocks_unconfirmed_historical_done(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })
        store.get_work_item = lambda item_id: (_ for _ in ()).throw(
            PlatformError("platform timeout"))

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True

        assert manifest.nodes["a"].status == "blocked"
        assert manifest.nodes["a"].work_item_id == item.id

    def test_platform_read_failure_blocks_historical_done_with_cached_merge_marker(
        self, tmp_path,
    ):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done", merged=True,
                      merged_at="2026-07-26T08:00:00Z"),
        })
        store.get_work_item = lambda item_id: (_ for _ in ()).throw(
            PlatformError("platform timeout"))

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True

        assert manifest.nodes["a"].status == "blocked"

    def test_historical_done_without_pr_fails_closed(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        store.update_work_item_metadata(item.id, artifacts={})
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True

        assert manifest.nodes["a"].status == "blocked"

    def test_historical_done_without_authoritative_merged_at_fails_closed(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done", merged=True),
        })

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True

        assert manifest.nodes["a"].status == "merging"

    def test_historical_open_pr_observes_then_requests_merge(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })
        path = str(tmp_path / "m.yaml")
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        requested = []
        store.request_pull_request_merge = lambda *args: requested.append(args) or SimpleNamespace(
            succeeded=True, exit_code=0, output="")

        loop.reconcile(store, manifest, path)
        loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert requested

    def test_merge_request_intent_is_persisted_before_external_command(
        self, tmp_path,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        persisted_states = []

        def request(*args):
            persisted_states.append(getattr(
                load_manifest(path).nodes["a"], "merge_request_state", None))
            return SimpleNamespace(succeeded=True, exit_code=0, output="")

        store.request_pull_request_merge = request

        loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert persisted_states == ["intent"]

    def test_requested_merge_is_not_reissued_after_reload(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args) or SimpleNamespace(
            succeeded=True, exit_code=0, output="")

        loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})
        reloaded = load_manifest(path)
        loop.collect_results(
            store, runtime, reloaded, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert len(requests) == 1
        assert reloaded.nodes["a"].status == "merging"
        assert reloaded.nodes["a"].merge_request_state == "requested"

    def test_unproven_merge_intent_does_not_retry_after_restart(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="merging",
                      merge_request_state="intent"),
        })
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)

        loop.collect_results(
            store, runtime, manifest, str(tmp_path / "m.yaml"),
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert requests == []
        assert manifest.nodes["a"].status == "blocked"

    def test_malformed_merge_request_state_fails_closed_without_request(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="merging",
                      merge_request_state="definitely-not-a-state"),
        })
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)

        loop.collect_results(
            store, runtime, manifest, str(tmp_path / "m.yaml"),
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert requests == []
        assert manifest.nodes["a"].status == "blocked"
        assert manifest.nodes["a"].status != "done"

    @pytest.mark.parametrize(
        ("marker", "remote_state", "expected_status", "expected_marker"),
        [
            ("invalid", "merged", "blocked", "invalid"),
            ("invalid", "open", "blocked", "invalid"),
            ("invalid", "pending", "blocked", "invalid"),
            ("intent", "merged", "done", None),
            ("intent", "open", "merging", "intent"),
            ("intent", "pending", "merging", "requested"),
            ("requested", "merged", "done", None),
            ("requested", "open", "merging", "requested"),
            ("requested", "pending", "merging", "requested"),
        ],
    )
    def test_historical_done_preserves_merge_marker_closure(
        self, tmp_path, marker, remote_state, expected_status, expected_marker,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done",
                      merge_request_state=marker),
        })
        merged_at = "2026-07-26T09:00:00Z" if remote_state == "merged" else None
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state=remote_state, merged_at=merged_at)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)
        path = str(tmp_path / "m.yaml")

        loop.reconcile(store, manifest, path)

        assert manifest.nodes["a"].status == expected_status
        assert manifest.nodes["a"].merge_request_state == expected_marker
        if remote_state == "open" and marker in {"intent", "requested"}:
            loop.collect_results(
                store, runtime, manifest, path,
                retry_limits=dict(DEFAULT_RETRY), config={})
            assert manifest.nodes["a"].merge_request_state == expected_marker
        assert requests == []

    def test_timeout_with_open_pr_preserves_intent_across_restart(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args) or SimpleNamespace(
            succeeded=False, timed_out=True, exit_code=None, output="timeout")

        loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})
        reloaded = load_manifest(path)
        loop.collect_results(
            store, runtime, reloaded, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert len(requests) == 1
        assert reloaded.nodes["a"].status == "blocked"
        assert reloaded.nodes["a"].merge_request_state == "intent"
        assert f"omac node retry {path} a" in "\n".join(store.get_comments(item.id))

    def test_timeout_with_pending_pr_stays_merging_without_worker_bounce(
        self, tmp_path,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        store.request_pull_request_merge = lambda *args: SimpleNamespace(
            succeeded=False, timed_out=True, exit_code=None, output="timeout")
        observations = iter(("open", "pending"))
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state=next(observations), merged_at=None)

        loop.collect_results(
            store, runtime, manifest, str(tmp_path / "m.yaml"),
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert manifest.nodes["a"].status == "merging"
        assert store.get_work_item(item.id).bounces.merge == 0
        assert not store.assign_log

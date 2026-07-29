"""delivery / loop:P4.2 自动 merge 与冲突回退——对齐主线 canonical 数据模型。

主线 loop(L1.8 + P4.1) 已用 canonical 存储(WorkItem.bounces.merge +
config.retry.merge + reset_review)门,本模块补 reviewer pass 后的自动 merge 门:

    reviewer pass → merging ─ merge.command ─► observe remote PR
                                                   │
                          MERGED + mergedAt ───────└──► done
                          OPEN / pending ─────────────► merging
                          明确失败 + OPEN ──────────► 有界转回 worker

覆盖(对 result collection 顺序 §7.3 in_review reviewer pass → merging → done):
  - 配置 merge:假 merge 脚本 exit 0 后仍需远端 MERGED + mergedAt,才记录 done;
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
from omac.core.review_convergence import (
    REVIEW_PROTOCOL_VERSION, open_blockers, review_subject_digest,
)
from omac.core.taskmeta import TaskPhase
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import EngineConfig, WorkItem, WorkItemStatus
from omac.errors import AuthError, PlatformError
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


def _saved_manifest(tmp_path, manifest, name="m.yaml"):
    path = str(tmp_path / name)
    save_manifest(manifest, path)
    return path


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
        item.id, artifacts={"pr_url": "https://example.com/pr/1"})
    current = store.get_work_item(item.id)
    store.prepare_review_cycle(item.id, review_subject_digest(current, 1))
    store.update_work_item_metadata(
        item.id,
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
    def test_default_merge_command_runs_when_unconfigured(self, monkeypatch, tmp_path):
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

        path = _saved_manifest(tmp_path, manifest)
        assert run_merge_delivery({}, manifest, "a", store, _runtime(store),
                                  dict(DEFAULT_RETRY), path) == "pass"
        assert seen["command"] == (
            "gh pr merge https://example.com/pr/1 --squash --delete-branch")
        assert seen["kwargs"]["shell"] is True
        assert manifest.nodes["a"].merged is True
        # 无任何评论 / 远端确认后交给 loop 收口为 done
        assert store.get_comments(item.id) == []
        assert manifest.nodes["a"].status == "merging"

    def test_merge_block_missing_command_uses_default(self, monkeypatch, tmp_path):
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

        path = _saved_manifest(tmp_path, manifest)
        assert run_merge_delivery(
            {"merge": {"timeout_minutes": 30}}, manifest, "a", store,
            _runtime(store), dict(DEFAULT_RETRY), path) == "pass"
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
        path = _saved_manifest(tmp_path, manifest)
        assert run_merge_delivery(
            cfg, manifest, "a", store, _runtime(store), limits, path) == "pass"
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
        path = _saved_manifest(tmp_path, manifest)
        assert run_merge_delivery(
            cfg, manifest, "a", store, _runtime(store), limits, path) == "bounce"
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
        path = _saved_manifest(tmp_path, manifest)
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
            result = run_merge_delivery(
                cfg, manifest, "a", store, _runtime(store), limits, path)
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
        path = _saved_manifest(tmp_path, manifest)
        assert run_merge_delivery(
            _merge_config(script), manifest, "a", store, _runtime(store),
            resolve_retry({"retry": {"merge": 0}}), path) == "blocked"

    def test_custom_retry_merge_limit(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        script = _merge_script(tmp_path, "exit 1")
        limits = resolve_retry({"retry": {"merge": 5}})
        path = _saved_manifest(tmp_path, manifest)
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
                                     _runtime(store), limits, path)
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
        path = _saved_manifest(tmp_path, manifest)
        res = run_merge_delivery(
            _merge_config(script), manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY), path)
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
            current = store.get_work_item(item.id)
            store.prepare_review_cycle(
                item.id,
                review_subject_digest(
                    current, max(1, current.bounces.review + 1)),
            )
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

    @pytest.mark.parametrize(
        "merge_request_state",
        [None, "requested", "intent", "bounce_pending:1"],
    )
    def test_merging_rechecks_current_review_subject_before_any_merge_effect(
        self, tmp_path, monkeypatch, merge_request_state,
    ):
        """其他路径遗留 merging 时，最后安全门仍拒绝旧 subject 的 pass。"""
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        old_subject = store.get_work_item(item.id).review_subject_digest
        store.update_work_item_metadata(
            item.id,
            artifacts={"pr_url": "https://example.com/pr/2"},
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0}],
                "revision": 2,
            },
        )
        store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        manifest = Manifest(meta={}, nodes={
            "a": Node(
                id="a", worker="alice", reviewer="bob",
                work_item_id=item.id, status="merging",
                merge_request_state=merge_request_state,
            ),
        })
        path = _saved_manifest(tmp_path, manifest)
        monkeypatch.setattr(
            loop,
            "run_merge_delivery",
            lambda *_args, **_kwargs: pytest.fail(
                "stale review subject must be rejected before merge delivery"),
        )

        failures = loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={},
        )

        recovered = store.get_work_item(item.id)
        assert failures == {}
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.status is WorkItemStatus.IN_REVIEW
        assert recovered.phase is TaskPhase.REVIEW
        assert recovered.review_subject_digest != old_subject
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert store.assign_log[-1][2] == "reviewer"

    @pytest.mark.parametrize("merge_request_state", ["requested", "intent"])
    def test_non_bounce_merge_marker_with_empty_projection_still_requires_review(
        self, tmp_path, monkeypatch, merge_request_state,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        store.reset_review(item.id)
        store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
        manifest = Manifest(meta={}, nodes={
            "a": Node(
                id="a", worker="alice", reviewer="bob",
                work_item_id=item.id, status="merging",
                merge_request_state=merge_request_state,
            ),
        })
        path = _saved_manifest(tmp_path, manifest)
        monkeypatch.setattr(
            loop,
            "run_merge_delivery",
            lambda *_args, **_kwargs: pytest.fail(
                "non-bounce merge marker must not bypass current-subject review"),
        )

        failures = loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={},
        )

        recovered = store.get_work_item(item.id)
        assert failures == {}
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.status is WorkItemStatus.IN_REVIEW
        assert recovered.phase is TaskPhase.REVIEW
        assert recovered.review_subject_digest
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert store.assign_log[-1][2] == "reviewer"

    def test_bounce_pending_with_empty_projection_resumes_worker_handoff(
        self, tmp_path,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        store.update_work_item_metadata(item.id, merge_bounce=1)
        store.reset_review(item.id)
        store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
        manifest = Manifest(meta={}, nodes={
            "a": Node(
                id="a", worker="alice", reviewer="bob",
                work_item_id=item.id, status="merging",
                merge_request_state="bounce_pending:1",
            ),
        })
        path = _saved_manifest(tmp_path, manifest)
        store.request_pull_request_merge = lambda *_args: pytest.fail(
            "bounce recovery must not issue another merge request")

        failures = loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={},
        )

        recovered = store.get_work_item(item.id)
        assert failures == {}
        assert manifest.nodes["a"].status == "in_progress"
        assert manifest.nodes["a"].merge_request_state is None
        assert recovered.status is WorkItemStatus.IN_PROGRESS
        assert recovered.phase is TaskPhase.AUTHORING
        assert recovered.review_subject_digest is None
        assert recovered.review_verdict is None
        assert recovered.review_report is None
        assert store.assign_log[-1][2] == "worker"


# ── manifest 持久化:合入信息落盘 ──────────────────────────────────────────

class TestManifestPersistence:
    def test_done_node_manifest_records_merge_info(self, tmp_path):
        script = _merge_script(tmp_path, "exit 0")
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={"name": "demo"}, nodes={"a": _node()})
        manifest.nodes["a"].work_item_id = item.id
        manifest.nodes["a"].status = "in_review"
        path = _saved_manifest(tmp_path, manifest)
        run_merge_delivery(_merge_config(script), manifest, "a", store,
                           _runtime(store), dict(DEFAULT_RETRY), path)
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at is not None
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

    @pytest.mark.parametrize("state", ["closed_unmerged"])
    def test_historical_done_with_confirmed_unmerged_pr_fails_closed(self, tmp_path, state):
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

    def test_historical_done_with_unknown_pr_reopens_merge_observation(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="unknown", merged_at=None, detail="remote unavailable")

        assert loop.reconcile(store, manifest, str(tmp_path / "m.yaml")) is True

        assert manifest.nodes["a"].status == "merging"
        assert store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW

    def test_platform_read_failure_preserves_unconfirmed_historical_done(self, tmp_path):
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done"),
        })
        store.get_work_item = lambda item_id: (_ for _ in ()).throw(
            PlatformError("platform timeout"))

        with pytest.raises(PlatformError, match="platform timeout"):
            loop.reconcile(store, manifest, str(tmp_path / "m.yaml"))

        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].work_item_id == item.id

    def test_platform_read_failure_preserves_confirmed_historical_done(
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

        with pytest.raises(PlatformError, match="platform timeout"):
            loop.reconcile(store, manifest, str(tmp_path / "m.yaml"))

        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at == "2026-07-26T08:00:00Z"

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

    @pytest.mark.parametrize(
        "failure_point", ["comment", "metadata", "status", "reset", "assign", "wake"])
    def test_merge_bounce_pending_recovers_after_each_platform_effect(
        self, tmp_path, failure_point,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW)
        store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        old_pr = "https://example.com/pr/1"
        new_pr = "https://example.com/pr/2"
        merged_at = "2026-07-26T10:00:00Z"
        requests = []
        merged_prs = set()

        def request(pr_url, *args):
            requests.append(pr_url)
            if pr_url == old_pr:
                return SimpleNamespace(
                    succeeded=False, timed_out=False, exit_code=1,
                    output="merge conflict")
            merged_prs.add(pr_url)
            return SimpleNamespace(
                succeeded=True, timed_out=False, exit_code=0,
                output="merge requested")

        def observe(pr_url):
            if pr_url in merged_prs:
                return SimpleNamespace(state="merged", merged_at=merged_at)
            return SimpleNamespace(state="open", merged_at=None)

        store.request_pull_request_merge = request
        store.observe_pull_request = observe
        original_add_comment = store.add_comment
        original_update_metadata = store.update_work_item_metadata
        completed_update_status = store.update_status
        completed_reset_review = store.reset_review
        completed_assign = store.assign_work_item
        completed_wake = runtime.wake
        crashed = False

        def after_effect(effect):
            nonlocal crashed
            persisted = load_manifest(path).nodes["a"]
            assert persisted.status == "merging"
            assert persisted.merge_request_state.startswith("bounce_pending:")
            if effect == failure_point and not crashed:
                crashed = True
                raise RuntimeError(f"simulated crash after {effect}")

        def add_comment_then_maybe_crash(item_id, comment):
            original_add_comment(item_id, comment)
            after_effect("comment")

        def update_metadata_then_maybe_crash(item_id, **kwargs):
            result = original_update_metadata(item_id, **kwargs)
            if "merge_bounce" in kwargs:
                after_effect("metadata")
            return result

        def update_status_after_persist(item_id, status):
            completed_update_status(item_id, status)
            if requests and status is WorkItemStatus.IN_PROGRESS:
                after_effect("status")

        def reset_review_after_persist(item_id):
            completed_reset_review(item_id)
            after_effect("reset")

        def assign_after_persist(item_id, agent, role):
            completed_assign(item_id, agent, role)
            after_effect("assign")

        def wake_after_persist(item_id, agent, role):
            completed_wake(item_id, agent, role)
            after_effect("wake")

        store.add_comment = add_comment_then_maybe_crash
        store.update_work_item_metadata = update_metadata_then_maybe_crash
        store.update_status = update_status_after_persist
        store.reset_review = reset_review_after_persist
        store.assign_work_item = assign_after_persist
        runtime.wake = wake_after_persist

        with pytest.raises(RuntimeError, match=f"simulated crash after {failure_point}"):
            loop.collect_results(
                store, runtime, manifest, path,
                retry_limits=dict(DEFAULT_RETRY), config={})

        persisted = load_manifest(path)
        assert persisted.nodes["a"].status == "merging"
        assert persisted.nodes["a"].merge_request_state == "bounce_pending:1"
        assert requests == [old_pr]

        store.add_comment = original_add_comment
        store.update_work_item_metadata = original_update_metadata
        store.update_status = completed_update_status
        store.reset_review = completed_reset_review
        store.assign_work_item = completed_assign
        runtime.wake = completed_wake

        resumed_runtime = _runtime(store)
        running = loop.tick(
            store, resumed_runtime, persisted, path,
            retry_limits=dict(DEFAULT_RETRY), config={})
        assert running.state == "running"
        assert persisted.nodes["a"].status == "in_progress"
        assert persisted.nodes["a"].merge_request_state is None
        platform_item = store.get_work_item(item.id)
        assert platform_item.status is WorkItemStatus.IN_PROGRESS
        assert platform_item.phase is TaskPhase.AUTHORING
        assert platform_item.review_verdict is None
        assert platform_item.bounces.merge == 1
        assert requests == [old_pr]
        worker_assignments = len([entry for entry in store.assign_log if entry[2] == "worker"])
        assert worker_assignments in {1, 2}

        loop.tick(
            store, resumed_runtime, persisted, path,
            retry_limits=dict(DEFAULT_RETRY), config={})
        assert len([entry for entry in store.assign_log if entry[2] == "worker"]) == worker_assignments
        assert requests == [old_pr]

        store.update_work_item_metadata(
            item.id, artifacts={"pr_url": new_pr})
        store.update_status(item.id, WorkItemStatus.DONE)
        reviewing = loop.tick(
            store, resumed_runtime, persisted, path,
            retry_limits=dict(DEFAULT_RETRY), config={})
        assert reviewing.state == "running"
        assert persisted.nodes["a"].status == "in_review"

        store.update_work_item_metadata(
            item.id,
            review_verdict="pass",
            review_report=_current_pass_report(
                store, item.id, "review restarted delivery"),
        )
        store.update_status(item.id, WorkItemStatus.DONE)
        converged = loop.tick(
            store, resumed_runtime, persisted, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        final_disk = load_manifest(path)
        assert converged.state == "converged"
        assert persisted.nodes["a"].status == "done"
        assert final_disk.nodes["a"].status == "done"
        assert final_disk.nodes["a"].merged is True
        assert final_disk.nodes["a"].merged_at == merged_at
        assert store.get_work_item(item.id).status is WorkItemStatus.DONE
        assert requests == [old_pr, new_pr]
        bounce_comments = [
            comment for comment in store.get_comments(item.id)
            if "Returning to the worker" in comment]
        assert 1 <= len(bounce_comments) <= 2

    def test_merge_intent_is_saved_before_platform_status_write(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = _saved_manifest(tmp_path, manifest)
        observations = iter(("open", "pending"))
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state=next(observations), merged_at=None)
        store.request_pull_request_merge = lambda *args: SimpleNamespace(
            succeeded=True, timed_out=False, exit_code=0, output="requested")
        original_update_status = store.update_status
        persisted_at_write = []

        def update_status(item_id, status):
            persisted = load_manifest(path).nodes["a"]
            persisted_at_write.append(
                (persisted.status, persisted.merge_request_state))
            original_update_status(item_id, status)

        store.update_status = update_status

        result = run_merge_delivery(
            {}, manifest, "a", store, runtime, dict(DEFAULT_RETRY), path)

        assert result == "pending"
        assert persisted_at_write == [("merging", "intent")]
        reloaded = load_manifest(path).nodes["a"]
        assert reloaded.status == "merging"
        assert reloaded.merge_request_state == "requested"

    def test_merge_delivery_without_manifest_path_has_no_external_effects(self):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        effects = []
        store.observe_pull_request = lambda *args: effects.append("observe")
        store.request_pull_request_merge = lambda *args: effects.append("merge")

        with pytest.raises(ValueError, match="manifest_path"):
            run_merge_delivery(
                {}, manifest, "a", store, runtime, dict(DEFAULT_RETRY))

        assert effects == []
        assert manifest.nodes["a"].status == "in_review"
        assert manifest.nodes["a"].merge_request_state is None
        assert store.get_work_item(item.id).status is WorkItemStatus.DONE
        assert store.get_comments(item.id) == []
        assert store.assign_log == []

    def test_merge_bounce_cap_is_saved_before_platform_writes(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = _saved_manifest(tmp_path, manifest)
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        store.request_pull_request_merge = lambda *args: SimpleNamespace(
            succeeded=False, timed_out=False, exit_code=1, output="conflict")
        effects = []
        original_add_comment = store.add_comment
        original_update_metadata = store.update_work_item_metadata
        original_update_status = store.update_status

        def assert_durable(effect):
            persisted = load_manifest(path).nodes["a"]
            assert (persisted.status, persisted.merge_request_state) == (
                manifest.nodes["a"].status,
                manifest.nodes["a"].merge_request_state,
            )
            effects.append(effect)

        def add_comment(item_id, comment):
            assert_durable("comment")
            original_add_comment(item_id, comment)

        def update_metadata(item_id, **kwargs):
            assert_durable("metadata")
            return original_update_metadata(item_id, **kwargs)

        def update_status(item_id, status):
            assert_durable("status")
            original_update_status(item_id, status)

        store.add_comment = add_comment
        store.update_work_item_metadata = update_metadata
        store.update_status = update_status

        result = run_merge_delivery(
            {}, manifest, "a", store, runtime,
            resolve_retry({"retry": {"merge": 0}}), path)

        assert result == "blocked"
        assert load_manifest(path).nodes["a"].status == "blocked"
        assert effects == ["status", "comment", "metadata", "status"]
        assert store.assign_log == []

    def test_merge_bounce_cap_survives_crash_before_platform_block(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = _saved_manifest(tmp_path, manifest)
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="open", merged_at=None)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args) or SimpleNamespace(
            succeeded=False, timed_out=False, exit_code=1, output="conflict")
        original_add_comment = store.add_comment

        def crash_after_durable_block(item_id, comment):
            persisted = load_manifest(path).nodes["a"]
            assert persisted.status == "merging"
            assert persisted.merge_request_state == "bounce_pending:1"
            raise RuntimeError("simulated crash before platform block")

        store.add_comment = crash_after_durable_block

        with pytest.raises(RuntimeError, match="simulated crash"):
            run_merge_delivery(
                {}, manifest, "a", store, runtime,
                resolve_retry({"retry": {"merge": 0}}), path)

        assert store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW
        reloaded = load_manifest(path)
        store.add_comment = original_add_comment
        result = loop.tick(
            store, _runtime(store), reloaded, path,
            retry_limits=resolve_retry({"retry": {"merge": 0}}), config={})

        assert result.state == "needs_decision"
        assert reloaded.nodes["a"].status == "blocked"
        assert len(requests) == 1
        assert requests[0][0] == "https://example.com/pr/1"

    def test_closed_unmerged_is_saved_before_worker_side_effects(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review",
                      merge_request_state="requested"),
        })
        path = _saved_manifest(tmp_path, manifest)
        store.observe_pull_request = lambda pr_url: SimpleNamespace(
            state="closed_unmerged", merged_at=None)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)
        effects = []
        original_update_status = store.update_status
        original_reset_review = store.reset_review
        original_assign = store.assign_work_item
        original_wake = runtime.wake

        def assert_durable(effect):
            persisted = load_manifest(path).nodes["a"]
            assert persisted.status == "merging"
            assert persisted.merge_request_state == "bounce_pending:1"
            effects.append(effect)

        def update_status(item_id, status):
            assert_durable("status")
            original_update_status(item_id, status)

        def reset_review(item_id):
            assert_durable("reset")
            original_reset_review(item_id)

        def assign(item_id, agent, role):
            assert_durable("assign")
            original_assign(item_id, agent, role)

        def wake(item_id, agent, role):
            assert_durable("wake")
            original_wake(item_id, agent, role)

        store.update_status = update_status
        store.reset_review = reset_review
        store.assign_work_item = assign
        runtime.wake = wake

        result = run_merge_delivery(
            {}, manifest, "a", store, runtime, dict(DEFAULT_RETRY), path)

        assert result == "bounce"
        assert requests == []
        assert effects == ["status", "reset", "assign", "wake"]
        persisted = load_manifest(path).nodes["a"]
        assert persisted.status == "in_progress"
        assert persisted.merge_request_state is None

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

    def test_unknown_merge_observation_recovers_from_merge_without_bounce_or_reissue(
        self, tmp_path,
    ):
        """bootstrap-e2e 型暂态读失败只留在 merge，重启后继续观察。"""
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="merging",
                      merge_request_state="requested"),
        })
        path = _saved_manifest(tmp_path, manifest)
        observations = iter((
            SimpleNamespace(state="unknown", merged_at=None,
                            detail="temporary GitHub read failure"),
            SimpleNamespace(state="merged", merged_at="2026-07-27T00:00:00Z"),
        ))
        store.observe_pull_request = lambda pr_url: next(observations)
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)

        first = loop.tick(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        persisted = load_manifest(path).nodes["a"]
        assert first.state == "running"
        assert persisted.status == "merging"
        assert persisted.merge_request_state == "requested"
        assert store.get_work_item(item.id).bounces.merge == 0
        assert requests == []
        assert not store.assign_log

        resumed = load_manifest(path)
        second = loop.tick(
            store, runtime, resumed, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert second.state == "converged"
        assert resumed.nodes["a"].status == "done"
        assert resumed.nodes["a"].merged_at == "2026-07-27T00:00:00Z"
        assert requests == []

    def test_temporary_merge_observation_error_stays_in_merge_without_bounce(
        self, tmp_path,
    ):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="merging",
                      merge_request_state="requested"),
        })
        path = _saved_manifest(tmp_path, manifest)
        store.observe_pull_request = lambda pr_url: (_ for _ in ()).throw(
            PlatformError("GitHub API timeout"))
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)

        result = loop.tick(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert result.state == "running"
        assert manifest.nodes["a"].status == "merging"
        assert manifest.nodes["a"].merge_request_state == "requested"
        assert store.get_work_item(item.id).bounces.merge == 0
        assert requests == []

    def test_auth_error_during_reconcile_preserves_merge_recovery_facts(
        self, tmp_path,
    ):
        """认证结果未知时向上传播，保持业务事实且不得重放 merge。"""
        store = _store()
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="done",
                      merge_request_state="requested"),
        })
        path = _saved_manifest(tmp_path, manifest)
        store.observe_pull_request = lambda pr_url: (_ for _ in ()).throw(
            AuthError("GitHub token is expired"))
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)

        with pytest.raises(AuthError, match="GitHub token is expired"):
            loop.reconcile(store, manifest, path)

        persisted = load_manifest(path).nodes["a"]
        comments = "\n".join(store.get_comments(item.id))
        assert persisted.status == "done"
        assert persisted.merge_request_state == "requested"
        assert store.get_work_item(item.id).status is WorkItemStatus.DONE
        assert store.get_work_item(item.id).bounces.merge == 0
        assert requests == []
        assert comments == ""

    def test_auth_error_while_merging_blocks_without_reissuing_merge(self, tmp_path):
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="merging",
                      merge_request_state="requested"),
        })
        path = _saved_manifest(tmp_path, manifest)
        store.observe_pull_request = lambda pr_url: (_ for _ in ()).throw(
            AuthError("GitHub token is expired"))
        requests = []
        store.request_pull_request_merge = lambda *args: requests.append(args)

        result = loop.tick(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        assert result.state == "needs_decision"
        assert manifest.nodes["a"].status == "blocked"
        assert manifest.nodes["a"].merge_request_state == "requested"
        assert store.get_work_item(item.id).bounces.merge == 0
        assert requests == []

    def test_failed_reviewer_run_recovers_in_review_without_restarting_worker(
        self, tmp_path,
    ):
        """reviewer run 失败只恢复 review 阶段，保留已完成 worker 交付。"""
        store = _store()
        runtime = _runtime(store)
        item = _review_passed_item(store)
        store.reset_review(item.id)
        store.update_work_item_metadata(
            item.id,
            phase=TaskPhase.REVIEW,
            verification={"commands": [{"command": "pytest", "exit_code": 0}]},
        )
        store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        failed_review = store.get_work_item(item.id)
        failed_review.agent_run_failed = True
        manifest = Manifest(meta={}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                      work_item_id=item.id, status="in_review"),
        })
        path = _saved_manifest(tmp_path, manifest)
        artifacts_before = dict(failed_review.artifacts)
        verification_before = dict(failed_review.verification)

        result = loop.collect_results(
            store, runtime, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})

        recovered = store.get_work_item(item.id)
        assert result == {}
        assert manifest.nodes["a"].status == "in_review"
        assert recovered.status is WorkItemStatus.IN_REVIEW
        assert recovered.phase is TaskPhase.REVIEW
        assert recovered.reviewer == "bob"
        assert recovered.artifacts == artifacts_before
        assert recovered.verification == verification_before
        assert store.assign_log[-1][0] == item.id
        assert store.assign_log[-1][2] == "reviewer"
        assert not [entry for entry in store.assign_log if entry[2] == "worker"]

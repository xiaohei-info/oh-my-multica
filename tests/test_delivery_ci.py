"""delivery:CI 监控与有界回退(P4.1 验收)。

覆盖设计文档 §7.3 的三类路径与回退封顶:
  - 假 CI 脚本(exit 0/1/超时)三路径 e2e;回退计数与封顶断言
  - 评审 reject 回退:mock 注入 reject 1 次 → worker 重交 → 过
  - 未配置 ci 的全量回归不变(直接 in_review)
"""
from __future__ import annotations

import os
import stat

import pytest

from omac.core.manifest import Manifest, Node, save_manifest
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.engines.mock import MockRuntime, MockStore
from omac.pipeline.delivery import (
    DEFAULT_MAX_BOUNCES,
    DEFAULT_TIMEOUT_MINUTES,
    MANIFEST_TO_PLATFORM_STATUS,
    VALID_MANIFEST_STATUSES,
    handle_review_result,
    advance_delivery,
    run_ci_check,
    to_platform_status,
)


# ── fixtures ──────────────────────────────────────────────────────────────

def _store():
    cfg = EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"},
    )
    return MockStore(cfg)


def _runtime(store):
    return MockRuntime(store)


def _make_node(store, *, reviewer="bob", worker="alice"):
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker=worker, reviewer=reviewer,
        initial_status=WorkItemStatus.IN_PROGRESS,
    )
    store.update_work_item_metadata(
        item.id,
        artifacts={"pr_url": "https://example.com/pr/1"},
        verification={
            "commands": [{"cmd": "pytest -q", "exit_code": 0, "summary": "ok"}],
            "integration_gates": [],
            "pr_base": "feature/v1",
            "coverage": 95,
        },
    )
    store.update_status(item.id, WorkItemStatus.DONE)
    node = Node(id="a", worker=worker, reviewer=reviewer, work_item_id=item.id,
                status="in_progress")
    manifest = Manifest(meta={"name": "demo"}, nodes={"a": node})
    return manifest, node, item


def _ci_script(tmp_path, body, name="ci.sh"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    os.chmod(p, p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def _ci_config(script_path, timeout_minutes=DEFAULT_TIMEOUT_MINUTES):
    return {"ci": {"check_command": f"sh {script_path} {{pr_url}}",
                   "timeout_minutes": timeout_minutes}}


# ── 状态映射表 ─────────────────────────────────────────────────────────────

class TestStatusMapping:
    def test_ci_check_maps_to_in_progress(self):
        assert to_platform_status("ci_check") is WorkItemStatus.IN_PROGRESS

    def test_merging_maps_to_in_review(self):
        assert to_platform_status("merging") is WorkItemStatus.IN_REVIEW

    def test_full_mapping_table(self):
        assert MANIFEST_TO_PLATFORM_STATUS == {
            "todo": WorkItemStatus.TODO,
            "in_progress": WorkItemStatus.IN_PROGRESS,
            "ci_check": WorkItemStatus.IN_PROGRESS,
            "in_review": WorkItemStatus.IN_REVIEW,
            "merging": WorkItemStatus.IN_REVIEW,
            "done": WorkItemStatus.DONE,
            "blocked": WorkItemStatus.BLOCKED,
        }

    def test_unknown_status_teaches(self):
        with pytest.raises(ValueError) as exc:
            to_platform_status("bogus")
        assert "合法值" in str(exc.value)

    def test_valid_statuses_cover_lifecycle(self):
        assert VALID_MANIFEST_STATUSES == set(MANIFEST_TO_PLATFORM_STATUS)


# ── 未配置 CI:回归保证 ──────────────────────────────────────────────────

class TestCiNotConfigured:
    def test_skip_ci_direct_to_review(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        new = advance_delivery({}, manifest, "a", store, rt)
        assert new == "in_review"
        assert node.status == "in_review"
        got = store.get_work_item(item.id)
        assert got.status is WorkItemStatus.IN_REVIEW
        assert any(e[2] == "reviewer" for e in store.assign_log)
        assert store.get_comments(item.id) == []

    def test_ci_block_missing_means_skip(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        new = advance_delivery({"ci": {"timeout_minutes": 30}}, manifest, "a", store, rt)
        assert new == "in_review"
        assert node.status == "in_review"


# ── CI 三路径(直跑与失败) ────────────────────────────────────────────────

class TestCiCheckPaths:
    def test_ci_pass_transitions_to_review(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        script = _ci_script(tmp_path, 'echo "all green for $1"; exit 0')
        cfg = _ci_config(script)
        new = advance_delivery(cfg, manifest, "a", store, rt)
        assert new == "in_review"
        assert node.status == "in_review"
        assert store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW
        assert node.ci_bounce == 0

    def test_ci_fail_bounces_to_worker(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        script = _ci_script(tmp_path, 'echo "boom fail"; exit 1')
        cfg = _ci_config(script)
        new = advance_delivery(cfg, manifest, "a", store, rt)
        assert new == "in_progress"
        assert node.status == "in_progress"
        assert node.ci_bounce == 1
        got = store.get_work_item(item.id)
        assert got.status is WorkItemStatus.IN_PROGRESS
        comments = store.get_comments(item.id)
        assert len(comments) == 1
        assert "CI 检查失败" in comments[0]
        assert "boom fail" in comments[0]
        assert any(e[2] == "worker" for e in store.assign_log)

    def test_ci_fail_exit_code_in_comment(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        script = _ci_script(tmp_path, 'echo "segfault-ish"; exit 2')
        cfg = _ci_config(script)
        new = advance_delivery(cfg, manifest, "a", store, rt)
        assert new == "in_progress"
        assert node.ci_bounce == 1
        assert "退出码 2" in store.get_comments(item.id)[0]


# ── 超时路径(注入 TimeoutExpired) ─────────────────────────────────────────

class TestCiTimeoutBranch:
    def test_timeout_branch_returns_timeout_result(self, tmp_path, monkeypatch):
        import omac.pipeline.delivery as delivery
        import subprocess as sp

        def fake_run(cmd, **kw):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"),
                                    output=b"partial stdout", stderr=b"")

        monkeypatch.setattr(delivery.subprocess, "run", fake_run)
        result = run_ci_check("gh pr checks {pr_url}", "https://x", timeout_minutes=1)
        assert result.passed is False
        assert result.timed_out is True
        assert result.exit_code is None
        assert "partial stdout" in result.output
        assert "CI 检查超时" in result.label

    def test_timeout_then_bounce_to_worker(self, tmp_path, monkeypatch):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        import omac.pipeline.delivery as delivery
        import subprocess as sp

        def fake_run(cmd, **kw):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"),
                                    output=b"ci still running", stderr=b"")

        monkeypatch.setattr(delivery.subprocess, "run", fake_run)
        script = _ci_script(tmp_path, "exit 0")
        cfg = _ci_config(script)
        new = advance_delivery(cfg, manifest, "a", store, rt)
        assert new == "in_progress"
        assert node.ci_bounce == 1
        comments = store.get_comments(item.id)
        assert "CI 检查超时" in comments[0]
        assert "ci still running" in comments[0]


# ── 回退计数与封顶 ─────────────────────────────────────────────────────────

class TestBounceCap:
    def test_ci_bounce_cap_blocks(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        script = _ci_script(tmp_path, 'echo "fail"; exit 1')
        cfg = _ci_config(script)
        results = []
        for _ in range(DEFAULT_MAX_BOUNCES):
            store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/1"})
            store.update_status(item.id, WorkItemStatus.DONE)
            node.status = "in_progress"
            results.append(advance_delivery(cfg, manifest, "a", store, rt))
        assert results == ["in_progress", "in_progress", "blocked"]
        assert node.ci_bounce == DEFAULT_MAX_BOUNCES
        assert node.status == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert len(store.get_comments(item.id)) == DEFAULT_MAX_BOUNCES

    def test_ci_bounce_then_resubmit_passes(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        fail_script = _ci_script(tmp_path, 'echo "fail"; exit 1', name="fail.sh")
        pass_script = _ci_script(tmp_path, 'echo "green"; exit 0', name="pass.sh")
        cfg = _ci_config(fail_script)
        assert advance_delivery(cfg, manifest, "a", store, rt) == "in_progress"
        assert node.ci_bounce == 1
        store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.com/pr/1"})
        store.update_status(item.id, WorkItemStatus.DONE)
        node.status = "in_progress"
        cfg2 = _ci_config(pass_script)
        assert advance_delivery(cfg2, manifest, "a", store, rt) == "in_review"
        assert node.status == "in_review"
        assert node.ci_bounce == 1
        assert store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW


# ── 评审 reject 回退 ──────────────────────────────────────────────────────

class TestReviewReject:
    def _reject_item(self, store, item):
        store.update_work_item_metadata(
            item.id,
            review_verdict="reject",
            review_comment="覆盖率不达标,补测试",
            review_report={
                "diff_reviewed": True,
                "tests_rerun": True,
                "coverage_checked": False,
                "review_goals": "覆盖率 >= 90,补全 edge case",
            },
        )

    def _pass_item(self, store, item):
        store.update_work_item_metadata(
            item.id,
            review_verdict="pass",
            review_comment="LGTM",
            review_report={"diff_reviewed": True, "tests_rerun": True,
                           "coverage_checked": True, "review_goals": "ok"},
        )

    def test_reject_bounces_to_worker(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        node.status = "in_review"
        store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        self._reject_item(store, item)
        new = handle_review_result({}, manifest, "a", store, rt)
        assert new == "in_progress"
        assert node.review_bounce == 1
        assert node.status == "in_progress"
        comments = store.get_comments(item.id)
        assert "评审 reject" in comments[0]
        assert "覆盖率不达标" in comments[0]
        assert "覆盖率 >= 90" in comments[0]
        assert any(e[2] == "worker" for e in store.assign_log)

    def test_reject_cap_blocks(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        results = []
        for _ in range(DEFAULT_MAX_BOUNCES):
            store.update_status(item.id, WorkItemStatus.IN_REVIEW)
            node.status = "in_review"
            self._reject_item(store, item)
            results.append(handle_review_result({}, manifest, "a", store, rt))
        assert results == ["in_progress", "in_progress", "blocked"]
        assert node.review_bounce == DEFAULT_MAX_BOUNCES
        assert node.status == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED

    def test_reject_then_resubmit_passes(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        node.status = "in_review"
        self._reject_item(store, item)
        assert handle_review_result({}, manifest, "a", store, rt) == "in_progress"
        assert node.review_bounce == 1
        store.update_status(item.id, WorkItemStatus.IN_REVIEW)
        node.status = "in_review"
        self._pass_item(store, item)
        assert handle_review_result({}, manifest, "a", store, rt) == "in_review"
        assert node.review_bounce == 1
        assert node.status == "in_review"


# ── manifest 持久化与防御分支 ─────────────────────────────────────────────

class TestBouncePersist:
    def test_ci_bounce_roundtrip(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        script = _ci_script(tmp_path, "exit 1")
        cfg = _ci_config(script)
        advance_delivery(cfg, manifest, "a", store, rt)
        assert node.ci_bounce == 1
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        from omac.core.manifest import load_manifest
        m2 = load_manifest(path)
        assert m2.nodes["a"].ci_bounce == 1
        assert m2.nodes["a"].status == "in_progress"

    def test_state_mapping_survives_save(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        script = _ci_script(tmp_path, "exit 1")
        cfg = _ci_config(script)
        advance_delivery(cfg, manifest, "a", store, rt)
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        text = open(path).read()
        assert "status: in_progress" in text


class TestDeliveryDefensive:
    def test_ci_configured_but_no_pr_url_blocks_with_teaching(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        store.update_work_item_metadata(item.id, artifacts={})
        script = _ci_script(tmp_path, "exit 0")
        cfg = _ci_config(script)
        new = advance_delivery(cfg, manifest, "a", store, rt)
        assert new == "blocked"
        assert node.status == "blocked"
        comments = store.get_comments(item.id)
        assert "pr_url" in comments[0]
        assert "omac work submit" in comments[0]

    def test_ci_short_output_tail(self, tmp_path):
        store = _store()
        rt = _runtime(store)
        manifest, node, item = _make_node(store)
        script = _ci_script(tmp_path, 'echo "tiny"; exit 1')
        cfg = _ci_config(script)
        advance_delivery(cfg, manifest, "a", store, rt)
        comm = store.get_comments(item.id)[0]
        assert "tiny" in comm
        assert "..." not in comm  # 短输出不应出现省略号

    def test_timeout_str_decode_branch(self, tmp_path, monkeypatch):
        import omac.pipeline.delivery as delivery
        import subprocess as sp

        def fake_run(cmd, **kw):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"),
                                    output="out-str", stderr="err-str")

        monkeypatch.setattr(delivery.subprocess, "run", fake_run)
        result = run_ci_check("gh pr checks {pr_url}", "https://x", timeout_minutes=1)
        assert result.timed_out is True
        assert "out-str" in result.output

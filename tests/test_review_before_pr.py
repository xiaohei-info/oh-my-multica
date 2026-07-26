"""review-before-PR 适配(切片 1)的 RED 测试:分支+精确 tip 证据、评审后确定性 draft PR 发布。

Feature gate:``config.delivery.review_before_pr``(缺省 false —— 上游默认行为不变)。

适配模式交付顺序(仅 gate 开启时生效;上游默认顺序完全不变):

    worker 交付(branch + tip_sha,无 pr_url)
      → 证据门(validate_worker_evidence(require_pr_url=False))
      → 独立 reviewer 对精确 tip 评审
      → run_pr_publish:评审绑定的 tip 与当前交付 tip 一致才发布 draft PR
      → CI(若配置,对刚发布的 PR)→ merge 门

安全不变量(本文件锁定):
  - 评审 pass 之前绝不调用 publish_draft_pr;
  - review_report.tip_sha 必须等于 artifacts.tip_sha(stale-tip 拒绝);
  - 默认模式仍强制 pr_url,不要求 branch/tip_sha;
  - 平台调用只经 WorkItemStore 新数据面方法 publish_draft_pr(§12.4 红线:
    pipeline 不直接 shell 平台 CLI;不支持该能力的引擎由基类抛 PlatformError)。
"""
from __future__ import annotations

import os
import stat

import pytest

from omac.core.config import DEFAULT_RETRY, resolve_delivery
from omac.core.evidence import validate_worker_evidence
from omac.core.manifest import Manifest, Node, save_manifest
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.engines.store import WorkItemStore
from omac.errors import PlatformError, ValidationError
from omac.pipeline import loop
from omac.pipeline.delivery import run_pr_publish

TIP_A = "a" * 40
TIP_B = "b" * 40

ADAPTED_CONFIG = {"engine": "mock", "delivery": {"review_before_pr": True}}


# ── fixtures ──────────────────────────────────────────────────────────────

def _store():
    return MockStore(EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"}))


def _runtime(store):  # noqa: ARG001 — 保持与 loop 签名对称
    return MockRuntime(store)


def _node(worker="alice", reviewer="bob"):
    return Node(id="a", worker=worker, reviewer=reviewer)


def _script(tmp_path, body, name="script.sh"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    os.chmod(p, p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def _review_report(tip_sha):
    return {"full_review_completed": True, "tip_sha": tip_sha}


def _item_with_artifacts(store, artifacts):
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker="alice")
    store.update_work_item_metadata(item.id, artifacts=artifacts)
    return item


def _review_passed_branch_item(store, *, tip_sha=TIP_A, review_tip=TIP_A):
    """适配模式下 reviewer 已 pass 的节点:branch+tip 交付 + 绑定 tip 的评审证据。"""
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker="alice", reviewer="bob",
        initial_status=WorkItemStatus.IN_REVIEW)
    store.update_work_item_metadata(
        item.id,
        artifacts={"branch": "feat/x", "tip_sha": tip_sha},
        review_verdict="pass",
        review_report=_review_report(review_tip))
    store.update_status(item.id, WorkItemStatus.DONE)
    return item


def _manifest_for(item, status):
    manifest = Manifest(meta={}, nodes={"a": _node()})
    manifest.nodes["a"].work_item_id = item.id
    manifest.nodes["a"].status = status
    return manifest


# ── resolve_delivery:feature gate 解析与校验 ───────────────────────────────

class TestResolveDelivery:
    def test_defaults_off_when_unconfigured(self):
        assert resolve_delivery({}) == {
            "review_before_pr": False, "external_merge": False}
        assert resolve_delivery({"delivery": {}}) == {
            "review_before_pr": False, "external_merge": False}

    def test_enabled(self):
        resolved = resolve_delivery({"delivery": {"review_before_pr": True}})
        assert resolved["review_before_pr"] is True

    def test_explicit_false(self):
        resolved = resolve_delivery({"delivery": {"review_before_pr": False}})
        assert resolved["review_before_pr"] is False

    def test_rejects_non_mapping(self):
        with pytest.raises(ValidationError):
            resolve_delivery({"delivery": "yes"})

    def test_rejects_non_bool(self):
        with pytest.raises(ValidationError):
            resolve_delivery({"delivery": {"review_before_pr": "yes"}})


# ── validate_worker_evidence:branch + 精确 tip 证据 ────────────────────────

class TestWorkerEvidenceBranchTip:
    def test_default_mode_still_requires_pr_url(self):
        store = _store()
        item = _item_with_artifacts(store, {"branch": "feat/x", "tip_sha": TIP_A})
        errors = validate_worker_evidence(_node(), item)
        assert any("pr_url" in e for e in errors)

    def test_adapted_mode_accepts_branch_and_tip(self):
        store = _store()
        item = _item_with_artifacts(store, {"branch": "feat/x", "tip_sha": TIP_A})
        errors = validate_worker_evidence(_node(), item, require_pr_url=False)
        assert errors == []

    def test_adapted_mode_requires_branch(self):
        store = _store()
        item = _item_with_artifacts(store, {"tip_sha": TIP_A})
        errors = validate_worker_evidence(_node(), item, require_pr_url=False)
        assert any("branch" in e for e in errors)

    def test_adapted_mode_requires_tip_sha(self):
        store = _store()
        item = _item_with_artifacts(store, {"branch": "feat/x"})
        errors = validate_worker_evidence(_node(), item, require_pr_url=False)
        assert any("tip_sha" in e for e in errors)

    @pytest.mark.parametrize("bad_tip", ["abc123", "A" * 40, "g" * 40, "a" * 39, "a" * 41])
    def test_adapted_mode_rejects_malformed_tip(self, bad_tip):
        store = _store()
        item = _item_with_artifacts(store, {"branch": "feat/x", "tip_sha": bad_tip})
        errors = validate_worker_evidence(_node(), item, require_pr_url=False)
        assert any("tip_sha" in e for e in errors)


# ── WorkItemStore.publish_draft_pr:引擎数据面契约 ──────────────────────────

class TestPublishDraftPrStoreContract:
    def test_base_store_raises_platform_error(self):
        store = _store()
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
        with pytest.raises(PlatformError):
            WorkItemStore.publish_draft_pr(
                store, item.id, branch="feat/x", tip_sha=TIP_A)

    def test_mock_store_records_publish_and_returns_url(self):
        store = _store()
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
        url = store.publish_draft_pr(item.id, branch="feat/x", tip_sha=TIP_A)
        assert url
        assert store.pr_publish_log == [(item.id, "feat/x", TIP_A)]


# ── run_pr_publish:评审后确定性 draft PR 发布 ──────────────────────────────

class TestRunPrPublish:
    def test_publishes_draft_pr_and_records_url(self):
        store = _store()
        item = _review_passed_branch_item(store)
        manifest = _manifest_for(item, "in_review")
        assert run_pr_publish(manifest, "a", store) == "pass"
        assert store.pr_publish_log == [(item.id, "feat/x", TIP_A)]
        artifacts = store.get_work_item(item.id).artifacts
        assert artifacts["pr_url"]
        assert artifacts["pr_tip_sha"] == TIP_A

    def test_rejects_stale_tip_approval(self):
        store = _store()
        item = _review_passed_branch_item(store, tip_sha=TIP_A, review_tip=TIP_B)
        manifest = _manifest_for(item, "in_review")
        assert run_pr_publish(manifest, "a", store) == "blocked"
        assert store.pr_publish_log == []
        assert not store.get_work_item(item.id).artifacts.get("pr_url")
        assert any("tip" in c for c in store.get_comments(item.id))

    def test_rejects_review_without_tip_binding(self):
        store = _store()
        item = _review_passed_branch_item(store)
        store.update_work_item_metadata(
            item.id, review_report={"full_review_completed": True})
        manifest = _manifest_for(item, "in_review")
        assert run_pr_publish(manifest, "a", store) == "blocked"
        assert store.pr_publish_log == []

    def test_missing_branch_or_tip_blocks_with_teaching(self):
        store = _store()
        item = _review_passed_branch_item(store)
        store.update_work_item_metadata(item.id, artifacts={})
        manifest = _manifest_for(item, "in_review")
        assert run_pr_publish(manifest, "a", store) == "blocked"
        comments = store.get_comments(item.id)
        assert any("branch" in c for c in comments)
        assert any("omac work submit" in c for c in comments)

    def test_platform_error_blocks(self, monkeypatch):
        store = _store()
        item = _review_passed_branch_item(store)
        manifest = _manifest_for(item, "in_review")

        def boom(item_id, *, branch, tip_sha):  # noqa: ARG001
            raise PlatformError("engine does not support draft PR publication")

        monkeypatch.setattr(store, "publish_draft_pr", boom)
        assert run_pr_publish(manifest, "a", store) == "blocked"
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED

    def test_idempotent_when_pr_already_published_for_tip(self):
        store = _store()
        item = _review_passed_branch_item(store)
        store.update_work_item_metadata(item.id, artifacts={
            "branch": "feat/x", "tip_sha": TIP_A,
            "pr_url": "https://example.com/pr/9", "pr_tip_sha": TIP_A})
        manifest = _manifest_for(item, "in_review")
        assert run_pr_publish(manifest, "a", store) == "pass"
        assert store.pr_publish_log == []


# ── collect_results e2e:适配顺序与默认回归 ─────────────────────────────────

class TestCollectResultsReviewBeforePr:
    def _worker_done_branch_item(self, store):
        """适配模式下 worker 已交付(branch+tip,无 pr_url)、平台 DONE 的节点。"""
        item = store.create_work_item(
            "ws", "node-a", "d", dag_key="a", worker="alice", reviewer="bob",
            initial_status=WorkItemStatus.IN_PROGRESS)
        store.update_work_item_metadata(
            item.id, artifacts={"branch": "feat/x", "tip_sha": TIP_A})
        store.update_status(item.id, WorkItemStatus.DONE)
        return item

    def test_worker_delivery_without_pr_advances_to_review(self, tmp_path):
        store = _store()
        item = self._worker_done_branch_item(store)
        manifest = _manifest_for(item, "in_progress")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        failures = loop.collect_results(
            store, _runtime(store), manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=ADAPTED_CONFIG)
        assert not failures
        assert manifest.nodes["a"].status == "in_review"
        # 评审 pass 之前绝不发布 PR
        assert store.pr_publish_log == []

    def test_publish_after_green_review_then_merge_done(self, tmp_path):
        store = _store()
        item = _review_passed_branch_item(store)
        manifest = _manifest_for(item, "in_review")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        failures = loop.collect_results(
            store, _runtime(store), manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=ADAPTED_CONFIG)
        assert not failures
        assert store.pr_publish_log == [(item.id, "feat/x", TIP_A)]
        assert store.get_work_item(item.id).artifacts["pr_url"]
        # 发布后照常走 merge 门(mock 引擎默认 merge 命令为 true)
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True

    def test_stale_tip_approval_is_rejected(self, tmp_path):
        store = _store()
        item = _review_passed_branch_item(store, tip_sha=TIP_A, review_tip=TIP_B)
        manifest = _manifest_for(item, "in_review")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        failures = loop.collect_results(
            store, _runtime(store), manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=ADAPTED_CONFIG)
        assert failures
        assert manifest.nodes["a"].status == "blocked"
        assert store.pr_publish_log == []
        assert manifest.nodes["a"].merged is False

    def test_default_mode_unchanged_without_pr_url(self, tmp_path):
        store = _store()
        item = self._worker_done_branch_item(store)
        manifest = _manifest_for(item, "in_progress")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)
        failures = loop.collect_results(
            store, _runtime(store), manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})
        assert failures
        assert manifest.nodes["a"].status == "blocked"
        assert any("pr_url" in reason for reason in failures.values())
        assert store.pr_publish_log == []

    def test_ci_runs_against_published_pr_not_before(self, tmp_path):
        """适配顺序:评审前无 PR 可检,CI 只能在 draft PR 发布后对 pr_url 运行。"""
        store = _store()
        rt = _runtime(store)
        ci_log = tmp_path / "ci.log"
        ci_script = _script(tmp_path, f'echo "$1" >> {ci_log}; exit 0', name="ci.sh")
        config = dict(ADAPTED_CONFIG)
        config["ci"] = {"check_command": f"sh {ci_script} {{pr_url}}",
                        "timeout_minutes": 30}

        item = self._worker_done_branch_item(store)
        manifest = _manifest_for(item, "in_progress")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)

        # tick 1:worker 交付 → in_review;此刻无 PR,CI 不得运行
        loop.collect_results(store, rt, manifest, path,
                             retry_limits=dict(DEFAULT_RETRY), config=config)
        assert manifest.nodes["a"].status == "in_review"
        assert not ci_log.exists()

        # reviewer 对精确 tip pass
        store.update_work_item_metadata(
            item.id, review_verdict="pass", review_report=_review_report(TIP_A))
        store.update_status(item.id, WorkItemStatus.DONE)

        # tick 2:发布 draft PR → CI 对发布的 pr_url 运行 → merge → done
        loop.collect_results(store, rt, manifest, path,
                             retry_limits=dict(DEFAULT_RETRY), config=config)
        assert store.pr_publish_log == [(item.id, "feat/x", TIP_A)]
        pr_url = store.get_work_item(item.id).artifacts["pr_url"]
        assert ci_log.read_text().splitlines() == [pr_url]
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True

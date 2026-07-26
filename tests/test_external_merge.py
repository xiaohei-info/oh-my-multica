"""external merge 适配(切片 1b / Stage 2)的 RED 测试:OMAC 在 external 模式绝不自行合并。

Feature gate:``config.delivery.external_merge``(缺省 false —— 上游默认行为不变)。

external 模式语义(仅 gate 开启时生效):
  - run_merge_delivery 绝不执行 merge 命令(不 spawn subprocess);
  - 无外部证据 → 结构化等待态(node「merging」,平台 in_review,幂等评论,
    artifacts.external_merge_wait 记录绑定的 pr_url + tip),返回 'waiting';
  - 只接受绑定到已批准 pr_url + tip 的外部 merge 证据
    (artifacts.external_merge = {merged: true, pr_url, tip_sha, merged_at?});
  - stale/wrong/畸形证据 → blocked + 报错即教学,绝不放行;
  - 默认模式(external_merge 缺省/false)行为逐字节不变,仍执行 merge 命令。
"""
from __future__ import annotations

import pytest

from omac.core.config import DEFAULT_RETRY, resolve_delivery
from omac.core.evidence import validate_external_merge_evidence
from omac.core.manifest import Manifest, Node, save_manifest
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline import delivery, loop
from omac.pipeline.delivery import run_merge_delivery

TIP_A = "a" * 40
TIP_B = "b" * 40
PR_URL = "https://example.com/pr/9"

EXTERNAL_CONFIG = {"engine": "mock", "delivery": {"external_merge": True}}
ADAPTED_EXTERNAL_CONFIG = {
    "engine": "mock",
    "delivery": {"review_before_pr": True, "external_merge": True},
}


# ── fixtures ──────────────────────────────────────────────────────────────

def _store():
    return MockStore(EngineConfig(
        engine_type="mock", workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"}))


def _runtime(store):  # noqa: ARG001 — 保持与 loop 签名对称
    return MockRuntime(store)


def _node(worker="alice", reviewer="bob"):
    return Node(id="a", worker=worker, reviewer=reviewer)


def _review_report(tip_sha):
    return {"full_review_completed": True, "tip_sha": tip_sha}


def _review_passed_pr_item(store, *, pr_url=PR_URL, tip_sha=TIP_A,
                           evidence=None):
    """reviewer 已 pass、PR 已存在(默认排序)的节点。"""
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker="alice", reviewer="bob",
        initial_status=WorkItemStatus.IN_REVIEW)
    artifacts = {"pr_url": pr_url, "tip_sha": tip_sha}
    if evidence is not None:
        artifacts["external_merge"] = evidence
    store.update_work_item_metadata(
        item.id, artifacts=artifacts,
        review_verdict="pass", review_report=_review_report(tip_sha))
    store.update_status(item.id, WorkItemStatus.DONE)
    return item


def _review_passed_published_item(store, *, tip_sha=TIP_A, evidence=None):
    """review-before-PR 适配:评审 pass 且 draft PR 已发布(pr_tip_sha 已记录)。"""
    item = store.create_work_item(
        "ws", "node-a", "d", dag_key="a", worker="alice", reviewer="bob",
        initial_status=WorkItemStatus.IN_REVIEW)
    artifacts = {
        "branch": "feat/x", "tip_sha": tip_sha,
        "pr_url": PR_URL, "pr_tip_sha": tip_sha,
    }
    if evidence is not None:
        artifacts["external_merge"] = evidence
    store.update_work_item_metadata(
        item.id, artifacts=artifacts,
        review_verdict="pass", review_report=_review_report(tip_sha))
    store.update_status(item.id, WorkItemStatus.DONE)
    return item


def _manifest_for(item, status):
    manifest = Manifest(meta={}, nodes={"a": _node()})
    manifest.nodes["a"].work_item_id = item.id
    manifest.nodes["a"].status = status
    return manifest


def _valid_evidence(*, pr_url=PR_URL, tip_sha=TIP_A):
    return {
        "merged": True,
        "pr_url": pr_url,
        "tip_sha": tip_sha,
        "merged_at": "2026-07-26T12:00:00Z",
        "source": "multica-auto-merge",
    }


@pytest.fixture
def no_subprocess(monkeypatch):
    """record subprocess.run calls inside pipeline.delivery;外部模式下必须为零。"""
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("external merge mode must not spawn subprocess")

    monkeypatch.setattr(delivery.subprocess, "run", spy)
    return calls


# ── resolve_delivery:external_merge gate 解析与校验 ────────────────────────

class TestResolveDeliveryExternalMerge:
    def test_defaults_off_when_unconfigured(self):
        assert resolve_delivery({})["external_merge"] is False
        assert resolve_delivery({"delivery": {}})["external_merge"] is False

    def test_enabled(self):
        resolved = resolve_delivery({"delivery": {"external_merge": True}})
        assert resolved["external_merge"] is True

    def test_rejects_non_bool(self):
        with pytest.raises(ValidationError):
            resolve_delivery({"delivery": {"external_merge": "yes"}})


# ── validate_external_merge_evidence:证据绑定校验 ──────────────────────────

class TestValidateExternalMergeEvidence:
    def test_valid_evidence_passes(self):
        errors = validate_external_merge_evidence(
            _valid_evidence(), pr_url=PR_URL, tip_sha=TIP_A)
        assert errors == []

    def test_rejects_non_dict(self):
        errors = validate_external_merge_evidence(
            "merged", pr_url=PR_URL, tip_sha=TIP_A)
        assert errors

    def test_rejects_merged_not_true(self):
        bad = _valid_evidence()
        bad["merged"] = "yes"
        errors = validate_external_merge_evidence(bad, pr_url=PR_URL, tip_sha=TIP_A)
        assert any("merged" in e for e in errors)

    def test_rejects_missing_pr_url(self):
        bad = _valid_evidence()
        del bad["pr_url"]
        errors = validate_external_merge_evidence(bad, pr_url=PR_URL, tip_sha=TIP_A)
        assert any("pr_url" in e for e in errors)

    def test_rejects_wrong_pr_url(self):
        errors = validate_external_merge_evidence(
            _valid_evidence(pr_url="https://example.com/pr/10"),
            pr_url=PR_URL, tip_sha=TIP_A)
        assert any("pr_url" in e for e in errors)

    def test_rejects_stale_tip(self):
        errors = validate_external_merge_evidence(
            _valid_evidence(tip_sha=TIP_B), pr_url=PR_URL, tip_sha=TIP_A)
        assert any("tip_sha" in e for e in errors)

    def test_rejects_missing_tip_when_expected(self):
        bad = _valid_evidence()
        del bad["tip_sha"]
        errors = validate_external_merge_evidence(bad, pr_url=PR_URL, tip_sha=TIP_A)
        assert any("tip_sha" in e for e in errors)

    @pytest.mark.parametrize("bad_tip", ["abc123", "A" * 40, "g" * 40])
    def test_rejects_malformed_tip(self, bad_tip):
        errors = validate_external_merge_evidence(
            _valid_evidence(tip_sha=bad_tip), pr_url=PR_URL, tip_sha=bad_tip)
        assert any("tip_sha" in e for e in errors)

    def test_tip_optional_when_no_approved_tip(self):
        """默认排序下 worker 未交 tip 时,证据只绑定 pr_url。"""
        ev = _valid_evidence()
        del ev["tip_sha"]
        errors = validate_external_merge_evidence(ev, pr_url=PR_URL, tip_sha="")
        assert errors == []


# ── run_merge_delivery:external 模式绝不合并 ───────────────────────────────

class TestRunMergeDeliveryExternal:
    def test_waits_without_spawning_merge(self, no_subprocess):
        store = _store()
        item = _review_passed_pr_item(store)
        manifest = _manifest_for(item, "in_review")
        action = run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert action == "waiting"
        assert no_subprocess == []
        assert manifest.nodes["a"].merged is False
        assert manifest.nodes["a"].status == "merging"
        # 结构化等待态:平台 in_review + 绑定 pr_url/tip 的 wait 记录 + 教学评论
        assert store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW
        wait = store.get_work_item(item.id).artifacts["external_merge_wait"]
        assert wait["pr_url"] == PR_URL
        assert wait["tip_sha"] == TIP_A
        comments = store.get_comments(item.id)
        assert any("external" in c.lower() or "外部" in c for c in comments)

    def test_waiting_is_idempotent(self, no_subprocess):
        store = _store()
        item = _review_passed_pr_item(store)
        manifest = _manifest_for(item, "in_review")
        rt = _runtime(store)
        assert run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, rt, dict(DEFAULT_RETRY)) == "waiting"
        assert run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, rt, dict(DEFAULT_RETRY)) == "waiting"
        comments = store.get_comments(item.id)
        waiting_comments = [
            c for c in comments if "external" in c.lower() or "外部" in c]
        assert len(waiting_comments) == 1

    def test_valid_evidence_advances_without_merge_command(self, no_subprocess):
        store = _store()
        item = _review_passed_pr_item(store, evidence=_valid_evidence())
        manifest = _manifest_for(item, "merging")
        action = run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert action == "pass"
        assert no_subprocess == []
        assert manifest.nodes["a"].merged is True
        assert manifest.nodes["a"].merged_at == "2026-07-26T12:00:00Z"

    def test_stale_tip_evidence_rejected(self, no_subprocess):
        store = _store()
        item = _review_passed_pr_item(
            store, evidence=_valid_evidence(tip_sha=TIP_B))
        manifest = _manifest_for(item, "merging")
        action = run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert action == "blocked"
        assert no_subprocess == []
        assert manifest.nodes["a"].merged is False
        assert store.get_work_item(item.id).status is WorkItemStatus.BLOCKED
        assert any("tip" in c for c in store.get_comments(item.id))

    def test_wrong_pr_url_evidence_rejected(self, no_subprocess):
        store = _store()
        item = _review_passed_pr_item(
            store, evidence=_valid_evidence(pr_url="https://example.com/pr/10"))
        manifest = _manifest_for(item, "merging")
        action = run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert action == "blocked"
        assert manifest.nodes["a"].merged is False
        assert any("pr_url" in c for c in store.get_comments(item.id))

    def test_malformed_evidence_rejected(self, no_subprocess):
        store = _store()
        item = _review_passed_pr_item(store, evidence={"merged": "yes"})
        manifest = _manifest_for(item, "merging")
        action = run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert action == "blocked"
        assert manifest.nodes["a"].merged is False

    def test_missing_pr_url_blocks_with_teaching(self, no_subprocess):
        store = _store()
        item = _review_passed_pr_item(store)
        store.update_work_item_metadata(item.id, artifacts={})
        manifest = _manifest_for(item, "in_review")
        action = run_merge_delivery(
            EXTERNAL_CONFIG, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert action == "blocked"
        assert no_subprocess == []
        assert any("pr_url" in c for c in store.get_comments(item.id))

    def test_default_mode_still_runs_merge_command(self, monkeypatch):
        """external_merge 缺省关闭:即使存在外部证据键,也照常执行 merge 命令。"""
        calls = []

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return _Proc()

        monkeypatch.setattr(delivery.subprocess, "run", spy)
        store = _store()
        item = _review_passed_pr_item(store, evidence=_valid_evidence())
        manifest = _manifest_for(item, "in_review")
        action = run_merge_delivery(
            {"engine": "mock"}, manifest, "a", store, _runtime(store),
            dict(DEFAULT_RETRY))
        assert action == "pass"
        assert calls  # 默认模式 merge 命令照常执行
        assert manifest.nodes["a"].merged is True


# ── collect_results e2e:external 等待 → 证据推进;默认回归 ──────────────────

class TestCollectResultsExternalMerge:
    def test_adapted_waits_for_external_merge_then_done(self, tmp_path, no_subprocess):
        """review-before-PR + external:评审 pass → 发布 draft PR → 等待外部 merge;
        证据到达后下一 tick 推进 done。全程零 subprocess。"""
        store = _store()
        rt = _runtime(store)
        item = _review_passed_published_item(store)
        # 清掉 pr_url/pr_tip_sha,让 collect_results 走完整 publish 路径
        store.update_work_item_metadata(item.id, artifacts={
            "branch": "feat/x", "tip_sha": TIP_A})
        manifest = _manifest_for(item, "in_review")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)

        # tick 1:发布 draft PR → 无外部证据 → waiting(不 done、不合并)
        failures = loop.collect_results(
            store, rt, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=ADAPTED_EXTERNAL_CONFIG)
        assert not failures
        assert no_subprocess == []
        assert store.pr_publish_log == [(item.id, "feat/x", TIP_A)]
        assert manifest.nodes["a"].status == "merging"
        assert manifest.nodes["a"].merged is False
        wait = store.get_work_item(item.id).artifacts["external_merge_wait"]
        assert wait["pr_url"] == store.get_work_item(item.id).artifacts["pr_url"]
        assert wait["tip_sha"] == TIP_A

        # 外部 merge 权威投递绑定证据(经 store 元数据;bridge/CLI 在切片 5)
        artifacts = dict(store.get_work_item(item.id).artifacts)
        artifacts["external_merge"] = _valid_evidence(
            pr_url=artifacts["pr_url"], tip_sha=TIP_A)
        store.update_work_item_metadata(item.id, artifacts=artifacts)

        # tick 2:证据校验通过 → done,全程仍零 subprocess
        failures = loop.collect_results(
            store, rt, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=ADAPTED_EXTERNAL_CONFIG)
        assert not failures
        assert no_subprocess == []
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True
        assert store.get_work_item(item.id).status is WorkItemStatus.DONE

    def test_stale_evidence_blocks_on_next_tick(self, tmp_path, no_subprocess):
        store = _store()
        rt = _runtime(store)
        item = _review_passed_published_item(store)
        store.update_work_item_metadata(item.id, artifacts={
            "branch": "feat/x", "tip_sha": TIP_A})
        manifest = _manifest_for(item, "in_review")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)

        loop.collect_results(
            store, rt, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=ADAPTED_EXTERNAL_CONFIG)
        assert manifest.nodes["a"].status == "merging"

        # stale 证据:merge 的 tip 不是评审批准的 tip → 拒绝
        artifacts = dict(store.get_work_item(item.id).artifacts)
        artifacts["external_merge"] = _valid_evidence(
            pr_url=artifacts["pr_url"], tip_sha=TIP_B)
        store.update_work_item_metadata(item.id, artifacts=artifacts)

        failures = loop.collect_results(
            store, rt, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=ADAPTED_EXTERNAL_CONFIG)
        assert failures
        assert no_subprocess == []
        assert manifest.nodes["a"].status == "blocked"
        assert manifest.nodes["a"].merged is False

    def test_standalone_external_mode_without_review_before_pr(self, tmp_path, no_subprocess):
        """external_merge 独立可用(默认排序 + 外部 merge 权威)。"""
        store = _store()
        rt = _runtime(store)
        item = _review_passed_pr_item(store)
        manifest = _manifest_for(item, "in_review")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)

        failures = loop.collect_results(
            store, rt, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=EXTERNAL_CONFIG)
        assert not failures
        assert no_subprocess == []
        assert manifest.nodes["a"].status == "merging"
        assert store.pr_publish_log == []

        artifacts = dict(store.get_work_item(item.id).artifacts)
        artifacts["external_merge"] = _valid_evidence()
        store.update_work_item_metadata(item.id, artifacts=artifacts)

        failures = loop.collect_results(
            store, rt, manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config=EXTERNAL_CONFIG)
        assert not failures
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True

    def test_default_mode_ignores_external_evidence(self, tmp_path, monkeypatch):
        """gate 关闭:外部证据键不影响默认自动 merge 路径。"""
        calls = []

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return _Proc()

        monkeypatch.setattr(delivery.subprocess, "run", spy)
        store = _store()
        item = _review_passed_pr_item(store, evidence=_valid_evidence())
        manifest = _manifest_for(item, "in_review")
        path = str(tmp_path / "m.yaml")
        save_manifest(manifest, path)

        failures = loop.collect_results(
            store, _runtime(store), manifest, path,
            retry_limits=dict(DEFAULT_RETRY), config={})
        assert not failures
        assert calls
        assert manifest.nodes["a"].status == "done"
        assert manifest.nodes["a"].merged is True

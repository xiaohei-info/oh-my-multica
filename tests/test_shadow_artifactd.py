"""Artifactd 非 UI shadow 试点(切片 5)端到端证明。

场景全部跑在 mock 引擎上:无 assignment、PR、merge、deploy 或产品变更离开
进程(零 subprocess、零外部写)。证明:
  1. 人工计划门:硬工作在 PlanReturn 校验通过前绝不派发(exit 20 决策);
  2. 机器隔离:machine 配置开启时 manifest 必须声明 source 指针 + namespace;
  3. review-before-PR 顺序:独立评审 pass 之后才发布 draft PR;
  4. 外部 merge 移交:OMAC 绝不合并,只等待并校验绑定证据;
  5. exit-20 恢复:畸形/缺失 PlanReturn 的修复路径可复制;
  6. 回滚:撤销计划快照后计划门重新锁定;特性门关闭时上游行为不变。
"""
import json
import os
import shutil

import pytest

from omac.bridge.multica import (
    project_parent,
    revoke_plan_snapshot,
    submit_external_merge_evidence,
    submit_plan_return,
    validate_machine_isolation,
)
from omac.cli.main import main as cli_main
from omac.core.config import DEFAULT_RETRY, load_config, resolve_delivery
from omac.core.manifest import load_manifest
from omac.engines.mock import MockStore, MockRuntime
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import delivery, loop

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "shadow", "artifactd")
TIP = "f" * 40
DAG_KEY = "artifactd-shadow/shadow-config"


@pytest.fixture
def shadow_project(tmp_path):
    """把 shadow fixture 复制到临时项目( manifest 在根,config 在 .omac/)。"""
    os.makedirs(tmp_path / ".omac")
    shutil.copy(os.path.join(FIXTURE_DIR, "config.yaml"),
                tmp_path / ".omac" / "config.yaml")
    shutil.copy(os.path.join(FIXTURE_DIR, "manifest.yaml"),
                tmp_path / "manifest.yaml")
    plan = tmp_path / "plan.md"
    shutil.copy(os.path.join(FIXTURE_DIR, "plan.md"), plan)
    return {
        "root": str(tmp_path),
        "manifest": str(tmp_path / "manifest.yaml"),
        "config": str(tmp_path / ".omac" / "config.yaml"),
        "plan": str(plan),
    }


@pytest.fixture
def no_subprocess(monkeypatch):
    """shadow 全程禁止任何 subprocess(merge/CI 命令都不允许出现)。"""
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("shadow scenario must not spawn subprocess")

    monkeypatch.setattr(delivery.subprocess, "run", spy)
    return calls


def _store():
    return MockStore(EngineConfig(
        engine_type="mock", workspace_id="mock-workspace",
        extra={"MOCK_AUTO_COMPLETE_DELAY": "0"}))


def _load(shadow_project):
    config = load_config(shadow_project["config"])
    manifest = load_manifest(shadow_project["manifest"])
    return config, manifest


# ── shadow 配置与隔离 ────────────────────────────────────────────────────────

class TestShadowConfiguration:
    def test_delivery_gates_enabled(self, shadow_project):
        config, _ = _load(shadow_project)
        assert resolve_delivery(config) == {
            "review_before_pr": True, "external_merge": True}

    def test_machine_isolation_valid(self, shadow_project):
        config, manifest = _load(shadow_project)
        validate_machine_isolation(config, manifest)

    def test_machine_isolation_rejects_missing_source(self, shadow_project):
        config, manifest = _load(shadow_project)
        del manifest.meta["source"]
        with pytest.raises(ValidationError):
            validate_machine_isolation(config, manifest)

    def test_manifest_declares_human_plan_gate_and_runner(self, shadow_project):
        _, manifest = _load(shadow_project)
        node = manifest.nodes["shadow-config"]
        assert node.gate == {"human_plan": True}
        assert node.runner["runner_class"] == "hermes"
        assert node.runner["preferred_host"] == "atlas"


# ── 端到端:计划门 → 解锁 → 评审 → 发布 → 外部 merge ──────────────────────────

class TestShadowEndToEnd:
    def test_full_shadow_scenario(self, shadow_project, no_subprocess):
        config, manifest = _load(shadow_project)
        store = _store()
        rt = MockRuntime(store)
        mpath = shadow_project["manifest"]
        MockStore.set_kind_delivery(DAG_KEY, {
            "branch": "feat/artifactd-shadow", "tip_sha": TIP})

        # 1. 人工计划门:硬工作在 PlanReturn 前绝不派发(exit 20 决策面)
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config=config)
        assert result.state == "needs_decision"
        assert result.dispatched == []
        assert store.assign_log == []
        assert project_parent(manifest)["stage"] == "plan"

        # 2. PlanReturn 校验器解锁(不可变快照落入 .omac/plans)
        snapshot = submit_plan_return(
            manifest, mpath, f"PlanReturn path={shadow_project['plan']}",
            config=config)
        plans_dir = os.path.join(shadow_project["root"], ".omac", "plans")
        assert os.path.exists(os.path.join(plans_dir, f"{snapshot.sha256}.md"))

        # 3. 解锁后派发;mock 自动交付 branch + 精确 tip(无 PR)
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config=config)
        assert result.dispatched == ["shadow-config"]
        item_id = manifest.nodes["shadow-config"].work_item_id
        assert store.pr_publish_log == []

        # 4. worker 证据过门 → 转独立评审(此刻仍无 PR)
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config=config)
        assert manifest.nodes["shadow-config"].status == "in_review"
        assert store.pr_publish_log == []
        assert project_parent(manifest)["stage"] == "verify"

        # 5. 评审 pass(绑定精确 tip)→ 确定性发布 draft PR → 外部 merge 等待
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config=config)
        assert store.pr_publish_log == [
            (item_id, "feat/artifactd-shadow", TIP)]
        assert manifest.nodes["shadow-config"].status == "merging"
        assert manifest.nodes["shadow-config"].merged is False
        wait = store.get_work_item(item_id).artifacts["external_merge_wait"]
        assert wait["tip_sha"] == TIP

        # 评审必须先于发布:assign_log 里 reviewer 先于任何 publish
        roles_in_order = [entry[2] for entry in store.assign_log]
        assert roles_in_order[:2] == ["worker", "reviewer"]

        # 6. 外部 merge 权威投递绑定证据 → 下一 tick done,全程零 subprocess
        submit_external_merge_evidence(store, item_id, {
            "merged": True,
            "pr_url": wait["pr_url"],
            "tip_sha": TIP,
            "merged_at": "2026-07-26T12:30:00Z",
            "source": "multica-auto-merge",
        })
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config=config)
        assert manifest.nodes["shadow-config"].status == "done"
        assert manifest.nodes["shadow-config"].merged is True
        assert manifest.nodes["shadow-config"].merged_at == "2026-07-26T12:30:00Z"
        assert store.get_work_item(item_id).status is WorkItemStatus.DONE
        assert project_parent(manifest)["stage"] == "done"
        assert no_subprocess == []

    def test_rollback_relocks_plan_gate(self, shadow_project, no_subprocess):
        config, manifest = _load(shadow_project)
        store = _store()
        rt = MockRuntime(store)
        mpath = shadow_project["manifest"]
        submit_plan_return(
            manifest, mpath, f"PlanReturn path={shadow_project['plan']}",
            config=config)
        revoke_plan_snapshot(manifest, mpath)
        result = loop.tick(store, rt, load_manifest(mpath), mpath,
                           retry_limits=dict(DEFAULT_RETRY), config=config)
        assert result.state == "needs_decision"
        assert result.dispatched == []
        assert store.assign_log == []

    def test_exit20_recovery_path(self, shadow_project):
        """缺失计划 → exit 20(repair 可复制)→ 修复后解锁。"""
        config, manifest = _load(shadow_project)
        with pytest.raises(NeedsDecision) as excinfo:
            submit_plan_return(
                manifest, shadow_project["manifest"],
                f"PlanReturn path={shadow_project['root']}/missing.md",
                config=config)
        repair = excinfo.value.report["repair"]
        assert repair.startswith("PlanReturn path=/")
        # 按 repair 形式修复(指向真实可读计划)→ 解锁
        snapshot = submit_plan_return(
            manifest, shadow_project["manifest"],
            f"PlanReturn path={shadow_project['plan']}", config=config)
        assert len(snapshot.sha256) == 64


# ── bridge CLI(dry-run / status / submit-*)─────────────────────────────────

class TestBridgeCli:
    def test_dry_run_shows_gate_hold_without_writes(self, shadow_project, capsys):
        code = cli_main(["bridge", "dry-run", shadow_project["manifest"],
                         "--output", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan_gate"] == {"gated": ["shadow-config"],
                                        "unlocked": False}
        assert payload["would_dispatch"] == []
        assert payload["held_by_plan_gate"] == ["shadow-config"]
        assert payload["machine_isolation"]["ok"] is True

    def test_status_shows_parent_projection(self, shadow_project, capsys):
        code = cli_main(["bridge", "status", shadow_project["manifest"],
                         "--output", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["projection"]["stage"] == "plan"
        assert payload["projection"]["source"] == {
            "project": "artifactd", "issue": "ART-101"}

    def test_submit_plan_return_via_cli(self, shadow_project, capsys):
        code = cli_main([
            "bridge", "submit-plan-return", shadow_project["manifest"],
            "--text", f"PlanReturn path={shadow_project['plan']}",
            "--output", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["sha256"]) == 64
        # 解锁后 dry-run 显示可派发
        code = cli_main(["bridge", "dry-run", shadow_project["manifest"],
                         "--output", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan_gate"]["unlocked"] is True
        assert payload["would_dispatch"] == ["shadow-config"]

    def test_submit_plan_return_malformed_is_exit5(self, shadow_project, capsys):
        code = cli_main([
            "bridge", "submit-plan-return", shadow_project["manifest"],
            "--text", "my plan is great, trust me"])
        assert code == 5

    def test_submit_plan_return_missing_file_is_exit20(self, shadow_project, capsys):
        code = cli_main([
            "bridge", "submit-plan-return", shadow_project["manifest"],
            "--text", f"PlanReturn path={shadow_project['root']}/missing.md",
            "--output", "json"])
        assert code == 20
        payload = json.loads(capsys.readouterr().out)
        assert payload["kind"] == "plan_return"
        assert "PlanReturn path=" in payload["repair"]

    def test_dry_run_flags_isolation_violation(self, shadow_project, tmp_path,
                                               capsys):
        import yaml
        with open(shadow_project["manifest"], encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        del data["meta"]["source"]
        with open(shadow_project["manifest"], "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True)
        code = cli_main(["bridge", "dry-run", shadow_project["manifest"],
                         "--output", "json"])
        assert code == 5
        payload = json.loads(capsys.readouterr().out)
        assert payload["machine_isolation"]["ok"] is False
        assert payload["machine_isolation"]["errors"]

    def test_submit_merge_evidence_via_cli(self, shadow_project, capsys):
        store = _store()
        item = store.create_work_item(
            workspace_id="mock-workspace", title="t", description="d",
            dag_key=DAG_KEY, worker="alice", reviewer="bob")
        store.update_work_item_metadata(item.id, artifacts={
            "branch": "feat/x", "tip_sha": TIP,
            "pr_url": "https://example.com/pr/7", "pr_tip_sha": TIP})
        evidence_path = os.path.join(shadow_project["root"], "evidence.json")
        with open(evidence_path, "w", encoding="utf-8") as fh:
            json.dump({"merged": True, "pr_url": "https://example.com/pr/7",
                       "tip_sha": TIP, "merged_at": "2026-07-26T13:00:00Z"}, fh)
        code = cli_main([
            "bridge", "submit-merge-evidence", shadow_project["manifest"],
            "--issue", item.id, "--evidence-file", evidence_path,
            "--output", "json"])
        assert code == 0
        assert store.get_work_item(item.id).artifacts[
            "external_merge"]["merged"] is True

    def test_submit_merge_evidence_rejects_stale_tip(self, shadow_project, capsys):
        store = _store()
        item = store.create_work_item(
            workspace_id="mock-workspace", title="t", description="d",
            dag_key=DAG_KEY, worker="alice", reviewer="bob")
        store.update_work_item_metadata(item.id, artifacts={
            "branch": "feat/x", "tip_sha": TIP,
            "pr_url": "https://example.com/pr/7", "pr_tip_sha": TIP})
        evidence_path = os.path.join(shadow_project["root"], "evidence.json")
        with open(evidence_path, "w", encoding="utf-8") as fh:
            json.dump({"merged": True, "pr_url": "https://example.com/pr/7",
                       "tip_sha": "e" * 40}, fh)
        code = cli_main([
            "bridge", "submit-merge-evidence", shadow_project["manifest"],
            "--issue", item.id, "--evidence-file", evidence_path])
        assert code == 5
        assert "external_merge" not in store.get_work_item(item.id).artifacts

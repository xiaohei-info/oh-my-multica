"""Multica 桥接层(切片 5)测试。

覆盖:
  - 人工计划门:node.gate.human_plan 标记、ready 分区、exit-20 报告;
  - PlanReturn 桥接摄入:严格解析 + 不可变快照落入 manifest meta(仅校验器
    可解锁),畸形输入 exit 5 / 不可安全解析 exit 20;
  - 外部 merge 证据摄入:只接受绑定已批准 pr_url + tip 的证据;
  - 五阶段父工单投影(intake/plan/build/verify/done,blocked 是异常态);
  - 机器隔离校验:machine 配置开启时 manifest 必须声明 source 指针与 namespace;
  - loop tick 集成:人工计划门节点在解锁前绝不派发。
"""
import hashlib

import pytest

from omac.bridge.multica import (
    build_plan_gate_report,
    is_human_plan_node,
    partition_ready_by_plan_gate,
    plan_snapshot_of,
    project_parent,
    revoke_plan_snapshot,
    submit_external_merge_evidence,
    submit_plan_return,
    validate_machine_isolation,
)
from omac.core.config import DEFAULT_RETRY, resolve_delivery
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.engines.mock import MockStore, MockRuntime
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline import loop
from omac.pipeline.report import NEEDS_DECISION_KEYS

TIP_A = "a" * 40
TIP_B = "b" * 40
PR_URL = "https://example.com/pr/1"


def _store():
    return MockStore(EngineConfig(engine_type="mock", workspace_id="mock-workspace"))


def _node(node_id="a", **kwargs):
    base = dict(worker="alice", reviewer="bob")
    base.update(kwargs)
    return Node(id=node_id, **base)


def _manifest(nodes, meta=None, tmp_path=None, name="m.yaml"):
    manifest = Manifest(meta=meta or {}, nodes={n.id: n for n in nodes})
    if tmp_path is not None:
        path = str(tmp_path / name)
        save_manifest(manifest, path)
        return manifest, path
    return manifest


def _write_plan(tmp_path, body="# Plan\n\nDo the thing.\n"):
    plan = tmp_path / "plan.md"
    plan.write_text(body, encoding="utf-8")
    return str(plan)


# ── 人工计划门标记与 ready 分区 ─────────────────────────────────────────────

class TestHumanPlanGateMarker:
    def test_node_without_gate_is_not_gated(self):
        assert is_human_plan_node(_node()) is False

    def test_gate_without_human_plan_key_is_not_gated(self):
        assert is_human_plan_node(_node(gate={"other": True})) is False

    def test_human_plan_true_is_gated(self):
        assert is_human_plan_node(_node(gate={"human_plan": True})) is True

    def test_human_plan_false_is_not_gated(self):
        assert is_human_plan_node(_node(gate={"human_plan": False})) is False


class TestPartitionReadyByPlanGate:
    def test_no_gated_nodes_passes_ready_through(self):
        manifest = _manifest([_node("a"), _node("b")])
        ready, gated = partition_ready_by_plan_gate(manifest, ["a", "b"])
        assert ready == ["a", "b"]
        assert gated == []

    def test_gated_node_held_until_snapshot_recorded(self):
        manifest = _manifest([_node("a", gate={"human_plan": True}), _node("b")])
        ready, gated = partition_ready_by_plan_gate(manifest, ["a", "b"])
        assert ready == ["b"]
        assert gated == ["a"]

    def test_gated_node_dispatches_once_snapshot_recorded(self):
        manifest = _manifest(
            [_node("a", gate={"human_plan": True})],
            meta={"plan_snapshot": {"sha256": "c" * 64}})
        ready, gated = partition_ready_by_plan_gate(manifest, ["a"])
        assert ready == ["a"]
        assert gated == []

    def test_snapshot_without_sha256_does_not_unlock(self):
        manifest = _manifest(
            [_node("a", gate={"human_plan": True})],
            meta={"plan_snapshot": {"snapshot_path": "/tmp/x.md"}})
        ready, gated = partition_ready_by_plan_gate(manifest, ["a"])
        assert ready == []
        assert gated == ["a"]


class TestBuildPlanGateReport:
    def test_report_shape_matches_needs_decision_schema(self):
        manifest = _manifest([_node("a", gate={"human_plan": True})])
        report = build_plan_gate_report(manifest, "m.yaml", ["a"])
        assert set(report.keys()) == set(NEEDS_DECISION_KEYS)
        assert [n["key"] for n in report["failed_nodes"]] == ["a"]
        reason = report["failed_nodes"][0]["reason"]
        assert "PlanReturn" in reason

    def test_report_next_actions_are_copyable_bridge_commands(self):
        manifest = _manifest([_node("a", gate={"human_plan": True})])
        report = build_plan_gate_report(manifest, "m.yaml", ["a"])
        actions = report["next_actions"]
        assert any("submit-plan-return" in a and "m.yaml" in a for a in actions)
        assert any("PlanReturn path=" in a for a in actions)

    def test_report_lists_blocked_downstream(self):
        manifest = _manifest([
            _node("a", gate={"human_plan": True}),
            _node("b", blocked_by=["a"]),
        ])
        report = build_plan_gate_report(manifest, "m.yaml", ["a"])
        assert report["blocked_downstream"] == ["b"]


# ── PlanReturn 桥接摄入(仅校验器可解锁) ────────────────────────────────────

class TestSubmitPlanReturn:
    def test_path_form_records_immutable_snapshot(self, tmp_path):
        plan_path = _write_plan(tmp_path)
        manifest, mpath = _manifest(
            [_node("a", gate={"human_plan": True})], tmp_path=tmp_path)
        snapshot = submit_plan_return(manifest, mpath,
                                      f"PlanReturn path={plan_path}", config={})
        digest = hashlib.sha256(open(plan_path, "rb").read()).hexdigest()
        assert snapshot.sha256 == digest
        assert snapshot.snapshot_path.endswith(f"{digest}.md")
        # manifest meta 记录不可变快照(重新读盘确认已持久化)
        reloaded = load_manifest(mpath)
        recorded = plan_snapshot_of(reloaded)
        assert recorded["sha256"] == digest
        assert recorded["source"] == {"kind": "path", "path": plan_path}

    def test_unlocks_partition(self, tmp_path):
        plan_path = _write_plan(tmp_path)
        manifest, mpath = _manifest(
            [_node("a", gate={"human_plan": True})], tmp_path=tmp_path)
        submit_plan_return(manifest, mpath, f"PlanReturn path={plan_path}",
                           config={})
        ready, gated = partition_ready_by_plan_gate(manifest, ["a"])
        assert ready == ["a"]
        assert gated == []

    def test_malformed_text_is_exit5(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        with pytest.raises(ValidationError):
            submit_plan_return(manifest, mpath, "here is my plan, enjoy", config={})

    def test_relative_path_is_exit5(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        with pytest.raises(ValidationError):
            submit_plan_return(manifest, mpath, "PlanReturn path=plans/x.md",
                               config={})

    def test_missing_file_is_exit20_with_repair(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        with pytest.raises(NeedsDecision) as excinfo:
            submit_plan_return(
                manifest, mpath,
                f"PlanReturn path={tmp_path}/nope.md", config={})
        assert excinfo.value.exit_code == 20
        assert excinfo.value.report["kind"] == "plan_return"
        assert "PlanReturn path=" in excinfo.value.report["repair"]

    def test_artifact_form_without_fetch_is_exit20(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        with pytest.raises(NeedsDecision):
            submit_plan_return(
                manifest, mpath,
                "PlanReturn artifact=https://artifactd.example/plans/1", config={})

    def test_artifact_form_with_injected_fetch(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        body = b"# Plan\n"
        seen = []

        def fetch(source):
            seen.append(source)
            return body

        snapshot = submit_plan_return(
            manifest, mpath,
            "PlanReturn artifact=https://artifactd.example/plans/1",
            config={}, fetch=fetch)
        assert seen == [{"kind": "artifact",
                         "url": "https://artifactd.example/plans/1"}]
        assert snapshot.sha256 == hashlib.sha256(body).hexdigest()

    def test_host_form_requires_allowlist(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        with pytest.raises(NeedsDecision):
            submit_plan_return(
                manifest, mpath,
                f"PlanReturn host=artemis path=/plans/p.md sha256={'c' * 64}",
                config={}, fetch=lambda source: b"x")

    def test_host_form_with_allowlist_and_fetch(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        body = b"# Plan from artemis\n"
        digest = hashlib.sha256(body).hexdigest()
        config = {"plan_gate": {"allowed_hosts": ["artemis"]}}
        snapshot = submit_plan_return(
            manifest, mpath,
            f"PlanReturn host=artemis path=/plans/p.md sha256={digest}",
            config=config, fetch=lambda source: body)
        assert snapshot.sha256 == digest

    def test_host_form_hash_mismatch_is_exit20(self, tmp_path):
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        config = {"plan_gate": {"allowed_hosts": ["artemis"]}}
        with pytest.raises(NeedsDecision):
            submit_plan_return(
                manifest, mpath,
                f"PlanReturn host=artemis path=/plans/p.md sha256={'d' * 64}",
                config=config, fetch=lambda source: b"tampered")

    def test_revoke_relocks_gate(self, tmp_path):
        plan_path = _write_plan(tmp_path)
        manifest, mpath = _manifest(
            [_node("a", gate={"human_plan": True})], tmp_path=tmp_path)
        submit_plan_return(manifest, mpath, f"PlanReturn path={plan_path}",
                           config={})
        revoke_plan_snapshot(manifest, mpath)
        assert plan_snapshot_of(load_manifest(mpath)) is None
        ready, gated = partition_ready_by_plan_gate(manifest, ["a"])
        assert ready == []
        assert gated == ["a"]


# ── 外部 merge 证据摄入 ─────────────────────────────────────────────────────

class TestSubmitExternalMergeEvidence:
    def _item_with_approved_pr(self, store, *, tip_sha=TIP_A):
        item = store.create_work_item(
            workspace_id="mock-workspace", title="t", description="d",
            dag_key="k", worker="alice", reviewer="bob")
        store.update_work_item_metadata(item.id, artifacts={
            "branch": "feat/x", "tip_sha": tip_sha,
            "pr_url": PR_URL, "pr_tip_sha": tip_sha})
        return item

    def test_valid_evidence_is_recorded(self):
        store = _store()
        item = self._item_with_approved_pr(store)
        submit_external_merge_evidence(store, item.id, {
            "merged": True, "pr_url": PR_URL, "tip_sha": TIP_A,
            "merged_at": "2026-07-26T12:00:00Z"})
        recorded = store.get_work_item(item.id).artifacts["external_merge"]
        assert recorded["merged"] is True
        assert recorded["tip_sha"] == TIP_A

    def test_missing_pr_url_is_exit5_teaching(self):
        store = _store()
        item = store.create_work_item(
            workspace_id="mock-workspace", title="t", description="d",
            dag_key="k", worker="alice")
        with pytest.raises(ValidationError) as excinfo:
            submit_external_merge_evidence(store, item.id, {
                "merged": True, "pr_url": PR_URL, "tip_sha": TIP_A})
        assert "pr_url" in str(excinfo.value)

    def test_stale_tip_rejected(self):
        store = _store()
        item = self._item_with_approved_pr(store)
        with pytest.raises(ValidationError):
            submit_external_merge_evidence(store, item.id, {
                "merged": True, "pr_url": PR_URL, "tip_sha": TIP_B})

    def test_wrong_pr_url_rejected(self):
        store = _store()
        item = self._item_with_approved_pr(store)
        with pytest.raises(ValidationError):
            submit_external_merge_evidence(store, item.id, {
                "merged": True, "pr_url": "https://example.com/pr/99",
                "tip_sha": TIP_A})

    def test_unmerged_evidence_rejected(self):
        store = _store()
        item = self._item_with_approved_pr(store)
        with pytest.raises(ValidationError):
            submit_external_merge_evidence(store, item.id, {
                "merged": False, "pr_url": PR_URL, "tip_sha": TIP_A})

    def test_rejected_evidence_leaves_artifacts_untouched(self):
        store = _store()
        item = self._item_with_approved_pr(store)
        with pytest.raises(ValidationError):
            submit_external_merge_evidence(store, item.id, {"merged": "yes"})
        assert "external_merge" not in store.get_work_item(item.id).artifacts


# ── 五阶段父工单投影 ─────────────────────────────────────────────────────────

class TestProjectParent:
    def test_all_todo_projects_plan(self):
        manifest = _manifest([_node("a"), _node("b")])
        projection = project_parent(manifest)
        assert projection["stage"] == "plan"

    def test_in_progress_projects_build(self):
        manifest = _manifest([_node("a", status="in_progress"), _node("b")])
        assert project_parent(manifest)["stage"] == "build"

    def test_ci_check_projects_build(self):
        manifest = _manifest([_node("a", status="ci_check")])
        assert project_parent(manifest)["stage"] == "build"

    def test_in_review_projects_verify(self):
        manifest = _manifest([
            _node("a", status="done"), _node("b", status="in_review")])
        assert project_parent(manifest)["stage"] == "verify"

    def test_merging_projects_verify(self):
        manifest = _manifest([_node("a", status="merging")])
        assert project_parent(manifest)["stage"] == "verify"

    def test_all_done_projects_done(self):
        manifest = _manifest([_node("a", status="done"), _node("b", status="done")])
        assert project_parent(manifest)["stage"] == "done"

    def test_abandoned_counts_as_done(self):
        manifest = _manifest([
            _node("a", status="done"), _node("b", status="abandoned")])
        assert project_parent(manifest)["stage"] == "done"

    def test_blocked_is_exception_not_stage(self):
        manifest = _manifest([
            _node("a", status="done"), _node("b", status="blocked")])
        projection = project_parent(manifest)
        assert projection["stage"] == "done"
        assert projection["blocked"] == ["b"]
        assert projection["node_stages"]["b"] is None

    def test_blocked_does_not_drag_stage_back(self):
        """blocked 节点不投影阶段;其余节点决定父阶段。"""
        manifest = _manifest([
            _node("a", status="in_review"), _node("b", status="failed")])
        projection = project_parent(manifest)
        assert projection["stage"] == "verify"
        assert projection["blocked"] == ["b"]

    def test_projection_exposes_plan_gate_state(self):
        manifest = _manifest([_node("a", gate={"human_plan": True})])
        projection = project_parent(manifest)
        assert projection["plan_gate"] == {"gated": ["a"], "unlocked": False}

    def test_projection_exposes_source_pointer(self):
        meta = {"source": {"project": "artifactd", "issue": "ART-1"}}
        manifest = _manifest([_node("a")], meta=meta)
        assert project_parent(manifest)["source"] == meta["source"]

    def test_unknown_status_is_exit5(self):
        manifest = _manifest([_node("a", status="weird")])
        with pytest.raises(ValidationError):
            project_parent(manifest)


# ── 机器隔离校验 ─────────────────────────────────────────────────────────────

class TestValidateMachineIsolation:
    def test_absent_machine_block_is_noop(self):
        manifest = _manifest([_node("a")])
        validate_machine_isolation({}, manifest)  # 不抛异常即通过

    def test_machine_on_requires_source_pointer(self):
        config = {"machine": {"project": "delivery-ops", "namespace": "omac"}}
        manifest = _manifest([_node("a")])
        with pytest.raises(ValidationError) as excinfo:
            validate_machine_isolation(config, manifest)
        assert "source" in str(excinfo.value)

    def test_machine_on_with_source_and_namespace_passes(self):
        config = {"machine": {"project": "delivery-ops", "namespace": "omac"}}
        manifest = _manifest([_node("a")], meta={
            "namespace": "omac",
            "source": {"project": "artifactd", "issue": "ART-1"},
        })
        validate_machine_isolation(config, manifest)

    def test_machine_on_rejects_namespace_mismatch(self):
        config = {"machine": {"project": "delivery-ops", "namespace": "omac"}}
        manifest = _manifest([_node("a")], meta={
            "namespace": "human-board",
            "source": {"project": "artifactd", "issue": "ART-1"},
        })
        with pytest.raises(ValidationError) as excinfo:
            validate_machine_isolation(config, manifest)
        assert "namespace" in str(excinfo.value)

    def test_machine_on_rejects_malformed_source(self):
        config = {"machine": {"project": "delivery-ops", "namespace": "omac"}}
        manifest = _manifest([_node("a")], meta={
            "namespace": "omac",
            "source": {"project": "artifactd"},
        })
        with pytest.raises(ValidationError):
            validate_machine_isolation(config, manifest)


# ── loop tick 集成:解锁前绝不派发 ───────────────────────────────────────────

class TestTickPlanGate:
    def test_gated_node_needs_decision_and_no_dispatch(self, tmp_path):
        store = _store()
        rt = MockRuntime(store)
        manifest, mpath = _manifest(
            [_node("a", gate={"human_plan": True})], tmp_path=tmp_path)
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config={})
        assert result.state == "needs_decision"
        assert result.dispatched == []
        assert manifest.nodes["a"].status == "todo"
        assert store.assign_log == []
        assert set(result.report.keys()) == set(NEEDS_DECISION_KEYS)
        assert result.report["failed_nodes"][0]["key"] == "a"
        assert "PlanReturn" in result.report["failed_nodes"][0]["reason"]

    def test_unlock_then_tick_dispatches(self, tmp_path):
        store = _store()
        rt = MockRuntime(store)
        plan_path = _write_plan(tmp_path)
        manifest, mpath = _manifest(
            [_node("a", gate={"human_plan": True})], tmp_path=tmp_path)
        submit_plan_return(manifest, mpath, f"PlanReturn path={plan_path}",
                           config={})
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config={})
        assert result.dispatched == ["a"]
        assert manifest.nodes["a"].status == "in_progress"
        assert store.assign_log != []

    def test_manifest_without_gate_unchanged(self, tmp_path):
        """无 gate 标记的 manifest:行为与上游一致(立即派发)。"""
        store = _store()
        rt = MockRuntime(store)
        manifest, mpath = _manifest([_node("a")], tmp_path=tmp_path)
        result = loop.tick(store, rt, manifest, mpath,
                           retry_limits=dict(DEFAULT_RETRY), config={})
        assert result.dispatched == ["a"]
        assert result.report == {}

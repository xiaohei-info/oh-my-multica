"""P2.6 live Multica 联调验收 -- env-gated,真实数据面。

双重门控(满足"MULTICA_WORKSPACE_ID 未设即 skip,不进普通 CI"):
  - 模块级 pytest.mark.live + pytest.mark.skipif(MULTICA_WORKSPACE_ID 未设 -> 整组 skip)
  - 普通 CI 跑 pytest -m "not live",本文件零触碰;本地未 export 时即使误带 -m live 也全 skip

活的 Multica 引擎(MulticaStore):
  - 真实成员池可读
  - 三段式 bootstrap body 模板(bootstrap/简报/硬约束)可生成 —— 零 skill 协议层 OK
  - 真实 issue 创建 -> 写后读一致性(metadata 全字段)
  - contract 下发后 work item 可回读
  - 状态推进 todo -> in_progress -> done
  - 评论追加
  - 幂等:同一 dag_key 重复创建不破坏状态

红线(§12.4):测试代码只调 engines 的 WorkItemStore 接口,绝不直接 shell out 平台 CLI。
只读/写 metadata,不 assign、不 wake agent(避免在联调期间惊扰线上 agent)。
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omac.core.manifest import Contract, Manifest, Node, load_manifest  # noqa: E402
from omac.core.taskmeta import TaskKind, TaskPhase  # noqa: E402
from omac.engines import create_engine  # noqa: E402
from omac.engines.models import EngineConfig, WorkItemStatus  # noqa: E402
from omac.pipeline.dispatch import render_issue_body  # noqa: E402


# ==================== env gate ====================

def _live_ready() -> bool:
    return bool(os.environ.get("MULTICA_WORKSPACE_ID"))


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _live_ready(),
        reason="MULTICA_WORKSPACE_ID 未设,live 测试 skip",
    ),
]


# ==================== engine / fixtures ====================


def _workspace_id() -> str:
    """惰性读取真实 workspace id;未设则 module-level skip,避免收集期 KeyError。"""
    ws = os.environ.get("MULTICA_WORKSPACE_ID")
    if not ws:
        pytest.skip("MULTICA_WORKSPACE_ID 未设, live 测试 skip")
    return ws


# 每次运行的唯一后缀:避免前次 live 运行遗留的 issue 被 Multica 按 title 去重拦截,
# 保证可重复执行(真实集成测试常见做法 —— 用随机标记区分运行,便于事后扫尾)。
_RUN = os.environ.get("OMAC_LIVE_RUN") or f"run-{random.randrange(10**9)}"


def _config(extra: dict | None = None) -> EngineConfig:
    return EngineConfig(engine_type="multica", workspace_id=_workspace_id(), extra=extra)


def _engine(extra: dict | None = None):
    return create_engine("multica", _config(extra))


@pytest.fixture()
def store():
    return _engine().store


@pytest.fixture()
def sample_contract() -> Contract:
    return Contract(
        objective="Exercise the real Multica data plane end-to-end.",
        acceptance=["real work item created", "status can progress to done"],
        non_goals=["do not assign to a real agent", "do not wake any runtime"],
        verification_commands=["pytest", "--version"],
        integration_gates=[{
            "name": "live-gate",
            "layer": "L1",
            "delivery_goal": "live data plane verified",
            "source_of_truth": ["docs"],
            "covers": ["data-plane"],
            "acceptance_refs": ["real work item created"],
            "commands": ["pytest", "--version"],
            "required_metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
        }],
        pr_base="feature/live-smoke",
        coverage_gate=90,
    )


# ==================== 1. 成员池可读 ====================

def test_member_pool_readable(store):
    members = store.list_members(_workspace_id())
    assert isinstance(members, list)
    assert len(members) > 0, "工作空间成员池为空 —— multica CLI 登录 / workspace 成员不足"


# ==================== 2. 三段式 bootstrap body 模板 ====================

class TestBootstrapBody:
    def test_three_paragraphs_present_and_copy_pasteable(self, sample_contract):
        node = Node(id="live-proto", worker="alice", reviewer="bob",
                    title="Proto node", contract=sample_contract)
        body = render_issue_body(node, sample_contract, TaskKind.DEVELOP,
                                 "ISSUE-REAL-1000")
        assert "omac work show ISSUE-REAL-1000" in body
        assert "omac work submit ISSUE-REAL-1000" in body
        assert "omac guide" in body
        assert "简报" in body
        assert "objective" in body
        assert "硬约束" in body
        assert "non_goals" in body

    def test_pr_base_and_reviewer_mentions(self, sample_contract):
        node = Node(id="live-proto-2", worker="alice", reviewer="carol",
                    title="Proto 2", contract=sample_contract)
        body = render_issue_body(node, sample_contract, TaskKind.DEVELOP, "ID")
        assert "feature/live-smoke" in body
        assert "carol" in body


# ==================== 3. 真实 issue 创建 -> 写后读一致性 ====================

class TestLiveWorkItem:
    def test_create_then_read_back_metadata_consistency(self, store):
        created = store.create_work_item(
            workspace_id=_workspace_id(),
            title=f"P2.6 live node {_RUN}",
            description="zero-skill protocol verification body",
            dag_key="live-consistency",
            worker="alice",
            reviewer="bob",
            wave=1,
            kind=TaskKind.DEVELOP,
        )
        assert created.id, "work item id 为空"
        got = store.get_work_item(created.id)
        assert got.id == created.id
        assert got.dag_key == "live-consistency"
        assert got.worker == "alice"
        assert got.reviewer == "bob"
        assert got.wave == 1
        assert got.status == WorkItemStatus.TODO
        assert got.kind == TaskKind.DEVELOP
        assert got.phase == TaskPhase.AUTHORING
        assert "[DAG:live-consistency]" in got.title

    def test_set_node_contract_then_read_back(self, store, sample_contract):
        created = store.create_work_item(
            workspace_id=_workspace_id(),
            title=f"P2.6 contract node {_RUN}",
            description="contract round-trip",
            dag_key="live-contract",
            worker="alice",
        )
        store.set_node_contract(created.id, sample_contract)
        got = store.get_work_item(created.id)
        assert got.contract is not None, "contract 未持久化"
        contract = got.contract
        if isinstance(contract, dict):
            assert contract["objective"] == sample_contract.objective
            assert contract["acceptance"] == sample_contract.acceptance
            assert contract["pr_base"] == "feature/live-smoke"
            assert contract["coverage_gate"] == 90
        else:
            assert contract.objective == sample_contract.objective
            assert contract.pr_base == "feature/live-smoke"

    def test_status_progression_todo_to_done(self, store):
        created = store.create_work_item(
            workspace_id=_workspace_id(),
            title=f"P2.6 status node {_RUN}",
            description="status progression",
            dag_key="live-status",
            worker="alice",
        )
        assert store.get_work_item(created.id).status == WorkItemStatus.TODO
        store.update_status(created.id, WorkItemStatus.IN_PROGRESS)
        assert store.get_work_item(created.id).status == WorkItemStatus.IN_PROGRESS
        store.update_status(created.id, WorkItemStatus.DONE)
        assert store.get_work_item(created.id).status == WorkItemStatus.DONE

    def test_comment_append(self, store):
        created = store.create_work_item(
            workspace_id=_workspace_id(),
            title=f"P2.6 comment node {_RUN}",
            description="comment append",
            dag_key="live-comment",
            worker="alice",
        )
        store.add_comment(created.id, "P2.6 live integration — zero skill body OK")
        got = store.get_work_item(created.id)
        assert got.id == created.id

    def test_same_dag_key_does_not_corrupt(self, store):
        """同 dag_key、不同 title(避平台去重)的多个 work item 互相独立可读。

        omac 幂等性由 manifest 层保证:dispatch 把 work_item_id 写回 manifest,
        续跑复用同 issue,不在平台侧按 title 去重。数据面只承诺:同 dag_key 的多条
        work item 彼此独立、可读、互不覆盖。
        """
        a = store.create_work_item(
            workspace_id=_workspace_id(), title=f"P2.6 dagkey A {_RUN}",
            description="first", dag_key="live-idempotent", worker="alice",
        )
        b = store.create_work_item(
            workspace_id=_workspace_id(), title=f"P2.6 dagkey B {_RUN}",
            description="second", dag_key="live-idempotent", worker="bob",
        )
        assert a.id and b.id
        assert a.id != b.id, "两条 work item 必须产生不同 id"
        assert store.get_work_item(a.id).worker == "alice"
        assert store.get_work_item(b.id).worker == "bob"
        assert store.get_work_item(a.id).dag_key == "live-idempotent"
        assert store.get_work_item(b.id).dag_key == "live-idempotent"

    def test_update_metadata_round_trip(self, store):
        created = store.create_work_item(
            workspace_id=_workspace_id(), title=f"P2.6 meta node {_RUN}",
            description="metadata round-trip", dag_key="live-meta",
            worker="alice", reviewer="bob",
        )
        artifacts = {"pr_url": "https://example.com/pr/1", "note": "live"}
        verification = {
            "commands": [{"cmd": "python3 -c 'print(\"ok\")'", "exit_code": 0,
                          "summary": "live pass"}],
            "integration_gates": [], "pr_base": "feature/live-smoke",
            "ci_status": "passed", "coverage": 95,
        }
        review_report = {
            "review_goals": ["live goal"], "diff_reviewed": True,
            "tests_rerun": True, "coverage_checked": True,
            "acceptance_mapping": [], "blockers": [], "nits": [],
        }
        updated = store.update_work_item_metadata(
            created.id,
            worker="carol",
            artifacts=artifacts,
            verification=verification,
            review_verdict="pass",
            review_comment="live LGTM",
            review_report=review_report,
            phase=TaskPhase.REVIEW,
            ci_bounce=1,
            deliverable="live deliverable body",
        )
        assert updated.worker == "carol"
        assert updated.artifacts == artifacts
        assert updated.phase == TaskPhase.REVIEW
        assert updated.bounces.ci == 1
        assert updated.deliverable == "live deliverable body"
        got = store.get_work_item(created.id)
        assert got.verification is not None
        if isinstance(got.verification, dict):
            assert got.verification.get("coverage") == 95
        assert got.review_verdict == "pass"
        assert got.review_report is not None


# ==================== 4. smoke_live_manifest 加载 ====================

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMOKE_LIVE = FIXTURES / "smoke_live_manifest.yaml"


def test_smoke_live_manifest_loads():
    if not SMOKE_LIVE.exists():
        pytest.skip(f"fixture 不存在: {SMOKE_LIVE}")
    manifest = load_manifest(str(SMOKE_LIVE))
    assert "live-smoke-A" in manifest.nodes
    node = manifest.nodes["live-smoke-A"]
    assert node.worker
    assert node.contract is not None
    assert node.contract.objective
    assert node.contract.verification_commands
    assert node.contract.pr_base == "feature/live-smoke"


# ==================== 5. pipeline 永不 shell out CLI(§12.4 红线) ====================

def test_pipeline_does_not_subprocess_multica():
    import inspect
    import omac.pipeline.loop as loop_mod
    import omac.pipeline.dispatch as dispatch_mod
    for mod in (loop_mod, dispatch_mod):
        src = inspect.getsource(mod)
        assert "shell out" not in src
        assert "subprocess" not in src, (
            f"{mod.__name__} 直接依赖 subprocess —— 违反 §12.4 红线"
        )

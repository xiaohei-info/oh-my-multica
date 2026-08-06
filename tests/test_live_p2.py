"""P2.6 live Multica 联调验收 -- env-gated,真实数据面。

双重门控(满足"MULTICA_WORKSPACE_ID 未设即 skip,不进普通 CI"):
  - 模块级 pytest.mark.live + pytest.mark.skipif(MULTICA_WORKSPACE_ID 未设 -> 整组 skip)
  - 普通 CI 跑 pytest -m "not live",本文件零触碰;本地未 export 时即使误带 -m live 也全 skip

活的 Multica 引擎(MulticaStore):
  - 真实成员池可读
  - Human-first issue body + 单一 Agent JSON 入口可生成
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


# 每次运行的唯一后缀:避免前次 live 运行(若扫尾失败)的 issue 被 Multica 按 title
# 去重拦截,保证可重复执行。注:store fixture 已在 teardown 自动 cancel 本次创建的
# issue(见上),此后缀只是二次保险,不再依赖手动扫尾。
_RUN = os.environ.get("OMAC_LIVE_RUN") or f"run-{random.randrange(10**9)}"


def _config(extra: dict | None = None) -> EngineConfig:
    return EngineConfig(engine_type="multica", workspace_id=_workspace_id(), extra=extra)


def _engine(extra: dict | None = None):
    return create_engine("multica", _config(extra))


@pytest.fixture()
def store():
    """真实 Multica store,自带扫尾:记录本用例创建的每个 work item,
    teardown 时全部 cancel,保证 live 套件跑完不留垃圾(幂等,§12.4 只调 Store 接口)。
    """
    real = _engine().store
    created: list[str] = []
    orig_create = real.create_work_item

    def _tracking_create(*args, **kwargs):
        item = orig_create(*args, **kwargs)
        item_id = getattr(item, "id", None)
        if item_id:
            created.append(item_id)
        return item

    real.create_work_item = _tracking_create
    try:
        yield real
    finally:
        for item_id in created:
            try:
                real.cancel_work_item(item_id)
            except Exception:
                pass  # 扫尾尽力而为,不因清理失败让用例红


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


# ==================== 2. Human-first issue body ====================

class TestBootstrapBody:
    def test_human_sections_and_single_agent_entry(self, sample_contract):
        node = Node(id="live-proto", worker="alice", reviewer="bob",
                    title="Proto node", contract=sample_contract)
        body = render_issue_body(node, sample_contract, TaskKind.DEVELOP,
                                 "ISSUE-REAL-1000")
        assert "omac work show ISSUE-REAL-1000 --output json" in body
        assert "omac work submit ISSUE-REAL-1000" not in body
        assert "omac guide" not in body
        assert "## 任务摘要" in body
        assert "## 完成标准" in body
        assert "## 非目标" in body
        assert "## 硬约束" not in body

    def test_pr_base_is_human_readable_without_reviewer_protocol(self, sample_contract):
        node = Node(id="live-proto-2", worker="alice", reviewer="carol",
                    title="Proto 2", contract=sample_contract)
        body = render_issue_body(node, sample_contract, TaskKind.DEVELOP, "ID")
        assert "PR 基线: `feature/live-smoke`" in body
        assert "carol" not in body


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
            # 真实回读是裸 dict，而 _dump_contract 对默认值 coverage_gate=90
            # 故意省略，读回侧也不走 _contract_from_raw 的默认还原，
            # 故裸 dict 里可能没有该键。这是设计行为，不是服务器丢字段。
            assert contract.get("coverage_gate", 90) == 90
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
            "full_review_completed": True,
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


# ==================== 3.5 真实服务器行为契约(恢复事故固化) ====================
#
# 以下两条是 OAC amendment 恢复期间踩出的真实服务器行为,固化成 live 契约
# 防止回归。全部满足 §12.4 红线:不 assign、不 wake agent。


class TestLiveRecoveryContracts:
    def test_restore_authoring_generation_converges_on_real_server(
        self, store, sample_contract,
    ):
        """恢复元数据写后读必须收敛(#1 回归契约)。

        真实 Multica 服务器会静默忽略 issue PUT body 里的 metadata 字段
        (metadata 是独立 KV 子资源);恢复元数据必须走 metadata CLI 逐键写,
        否则写后读永不收敛。本用例直接调生产恢复入口
        restore_authoring_generation,写后从服务器重新读回验证。
        """
        created = store.create_work_item(
            workspace_id=_workspace_id(),
            title=f"P2.6 recovery node {_RUN}",
            description="authoring generation recovery contract",
            dag_key="live-recovery",
            worker="alice", reviewer="bob",
        )
        generation = f"live-generation-{_RUN}"
        baseline = {"worker": 2, "review": 1, "ci": 0, "merge": 0}

        restored = store.restore_authoring_generation(
            created.id, sample_contract,
            review_generation=generation, bounce_baseline=baseline)

        # 写后读:不信内存返回值,从服务器重新读回
        got = store.get_work_item(created.id)
        assert got.review_generation == generation, (
            "review_generation 写后读不收敛 —— metadata 可能又被塞进 issue PUT body")
        assert got.bounce_baseline == baseline
        assert got.status == WorkItemStatus.TODO
        assert got.phase == TaskPhase.AUTHORING
        assert got.worker_handoff in (None, {}), "恢复后 worker_handoff 必须清空"
        assert restored.review_generation == generation

        # 幂等收敛:重跑跳过已匹配键,结果不变(恢复重入语义)
        again = store.restore_authoring_generation(
            created.id, sample_contract,
            review_generation=generation, bounce_baseline=baseline)
        assert again.review_generation == generation
        assert again.bounce_baseline == baseline


# ==================== 3.6 真实 Run 归因契约(#4 回归契约) ====================


def test_formal_run_attribution_kinds_on_real_server():
    """真实服务器的 direct Run 归因标签必须在 OMAC 接受的 formal 集合内。

    恢复事故发现:真实服务器从不给 rerun 打 'rerun' 标签,一律盖
    'issue_assignment';旧代码只认 'rerun',导致评审续跑永远 fail-closed。
    本契约只读观察已有 Run(不新建、不 wake),把真实服务器的归因行为钉死:
    未来服务器若改标签,这里先红,而不是在生产恢复里翻车。

    需设 MULTICA_LIVE_OBSERVE_ITEM_ID 指向一个有 direct Run 的 work item
    (如恢复金丝雀节点);未设则 skip(纯只读,无副作用)。
    """
    observe_id = os.environ.get("MULTICA_LIVE_OBSERVE_ITEM_ID")
    if not observe_id:
        pytest.skip("MULTICA_LIVE_OBSERVE_ITEM_ID 未设,归因契约观察 skip")

    runs = _engine().runtime.list_runs(observe_id)
    direct = [run for run in runs if run.kind == "direct"]
    assert direct, f"观察目标 {observe_id} 没有 direct Run,无法验证归因契约"

    unknown = [
        run.id for run in direct
        if run.trigger_kind not in {"issue_assignment", "rerun"}
    ]
    assert not unknown, (
        f"真实服务器出现了 OMAC formal 集合之外的归因标签: {unknown} —— "
        "评审续跑/交付身份判定会 fail-closed,需先更新 engines 层再恢复")
    # 记录真实观测到的标签分布,供后续契约演进参考
    observed = sorted({run.trigger_kind for run in direct})
    assert observed, "direct Run 全部无归因 —— 交付因果链不可证明"


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

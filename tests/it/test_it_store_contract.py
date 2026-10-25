"""Work-item store contract against a real Multica server (the private Docker instance).

The tests call only the WorkItemStore / AgentRuntime ports, never a platform CLI, and do not assign or wake
agents unless a test says so.
"""
from __future__ import annotations

import random
import time

import pytest

from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus

_RUN = f"run-{random.randrange(10**9)}"


@pytest.fixture(autouse=True)
def _bind(stack):
    _ws["id"] = stack.workspace_id


_ws: dict = {}


def _workspace_id() -> str:
    return _ws["id"]


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
# 以下两条是 amendment 恢复期间踩出的真实服务器行为,固化成 live 契约
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


def test_formal_run_attribution_kinds_on_real_server(engine, store, stack):
    """A Run started by assigning an issue is stamped with an attribution OMAC accepts as formal.

    The real server never labels a rerun 'rerun'; it stamps 'issue_assignment'. If a future server changes
    the label, this fails first instead of failing closed in review continuation on a production run.
    """
    stack.set_scenario("alice", [[]])
    item = store.create_work_item(
        workspace_id=_workspace_id(), title=f"attribution {_RUN}", description="run attribution",
        dag_key="it-attribution", worker="alice")
    store.assign_work_item(item.id, "alice", "worker")
    deadline = time.time() + 25
    while time.time() < deadline:
        direct = [r for r in engine.runtime.list_runs(item.id) if r.kind == "direct"]
        if direct and all(r.terminal for r in direct):
            break
        time.sleep(0.3)
    assert direct, "assigning the issue to a scripted agent produced no direct Run"
    assert {r.trigger_kind for r in direct} <= {"issue_assignment", "rerun"}
    assert all(r.formal for r in direct)

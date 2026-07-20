"""engines:双接口装配、MockStore 写后读一致性、自动完成模拟、证据生成。"""
import pytest

from omac.core.manifest import Contract
from omac.engines import Engine, create_engine
from omac.engines.models import AgentProvisionSpec, EngineConfig, WorkItemStatus
from omac.engines.mock import MockRuntime, MockStore
from omac.errors import ValidationError


def _config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="ws", extra=base)


def _engine(**extra) -> Engine:
    return create_engine("mock", _config(**extra))


def test_factory_assembles_store_and_runtime():
    eng = _engine()
    assert isinstance(eng.store, MockStore)
    assert isinstance(eng.runtime, MockRuntime)
    assert "assign" in eng.runtime.describe()


def test_factory_rejects_unknown_engine():
    with pytest.raises(ValidationError):
        create_engine("jira", _config())


def test_create_and_get_roundtrip():
    store = _engine().store
    item = store.create_work_item("ws", "t", "desc", dag_key="a", worker="alice")
    got = store.get_work_item(item.id)
    assert got.dag_key == "a"
    assert got.title.startswith("[DAG:a]")
    assert got.status == WorkItemStatus.TODO


def test_create_rejects_empty_description():
    """parity:真实 multica issue create 校验 --description-file 非空,mock 须对等。

    否则 run_task 的空壳建 issue(tasks.py 两段式)在 mock 上悄悄通过、真机才炸。
    """
    store = _engine().store
    with pytest.raises(ValidationError):
        store.create_work_item("ws", "t", "", dag_key="a", worker="alice")


def test_update_rejects_empty_description():
    """parity:issue update --description-file 同样校验非空(create/update 共用一条校验)。"""
    store = _engine().store
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    with pytest.raises(ValidationError):
        store.update_work_item_metadata(item.id, description="")


def test_cancel_work_item_removes_it():
    """清理原语:cancel 后 work item 不再存在(扫尾幂等的地基)。"""
    store = _engine(MOCK_AUTO_COMPLETE="false").store
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    assert store.get_work_item(item.id).id == item.id
    store.cancel_work_item(item.id)
    with pytest.raises(RuntimeError):
        store.get_work_item(item.id)
    # 幂等:重复 cancel 不报错
    store.cancel_work_item(item.id)


def test_create_and_list_projects():
    """project 发现/创建原语:create 落库,list 可见,repos 保留。"""
    MockStore.reset()
    store = _engine().store
    p = store.create_project("ws", "demo", ["https://github.com/x/y.git"])
    assert p.id and p.title == "demo"
    assert p.repos == ["https://github.com/x/y.git"]
    assert p.id in [x.id for x in store.list_projects("ws")]


def test_metadata_write_then_read():
    store = _engine(MOCK_AUTO_COMPLETE="false").store
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    store.update_work_item_metadata(item.id, artifacts={"pr_url": "u"}, review_verdict="pass")
    got = store.get_work_item(item.id)
    assert got.artifacts == {"pr_url": "u"}
    assert got.review_verdict == "pass"


def test_assign_and_auto_complete_with_contract_evidence():
    store = _engine().store
    contract = Contract(
        objective="o", acceptance=["works"], non_goals=["x"],
        verification_commands=["pytest -q"],
        integration_gates=[{"name": "g", "commands": ["c"], "required_metrics": {},
                            "artifacts": [], "covers": [], "source_of_truth": [],
                            "delivery_goal": "d"}],
        pr_base="feature/v1", coverage_gate=90)
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    store.set_node_contract(item.id, contract)
    store.assign_work_item(item.id, "alice", "worker")
    store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    store.assign_work_item(item.id, "alice", "worker")  # 重新计时,delay=0 立即完成

    got = store.get_work_item(item.id)
    assert got.status == WorkItemStatus.DONE
    assert got.artifacts["pr_url"]
    assert got.verification["pr_base"] == "feature/v1"
    assert got.verification["commands"][0]["cmd"] == "pytest -q"
    assert got.verification["commands"][0]["business_tests"] == [{
        "acceptance": "works",
        "test": "mock://a/acceptance/works",
    }]


def test_fail_injection():
    store = _engine().store
    store.set_fail_keys({"a"})
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    store.assign_work_item(item.id, "alice", "worker")
    assert store.get_work_item(item.id).status == WorkItemStatus.FAILED


def test_review_handoff_on_same_item():
    """阶段交接 = 同一 work item 转派 reviewer(设计文档 §7.4)。"""
    store = _engine().store
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    store.assign_work_item(item.id, "bob", "reviewer")
    got = store.get_work_item(item.id)
    assert got.reviewer == "bob"
    assert got.review_verdict == "pass"  # 自动评审模拟
    assert got.review_report["blockers"] == []
    assert got.review_report["full_review_completed"] is True


def test_runtime_wake_is_idempotent():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = eng.store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    eng.runtime.wake(item.id, "alice", "worker")
    eng.runtime.wake(item.id, "alice", "worker")  # 幂等,无副作用


def test_runtime_can_provision_agent_and_add_it_to_member_pool():
    eng = _engine()
    targets = eng.runtime.list_targets()
    assert targets and targets[0].status == "online"

    created = eng.runtime.provision_agent(AgentProvisionSpec(
        name="template-worker",
        description="worker template",
        instructions="rules",
        runtime_id=targets[0].id,
        skills=[],
    ))

    assert created.name == "template-worker"
    assert "template-worker" in eng.store.list_members("ws")


def test_list_members_and_comments():
    store = _engine().store
    assert store.list_members("ws") == ["alice", "bob", "charlie"]
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    store.add_comment(item.id, "hello")
    assert store.get_comments(item.id) == ["hello"]

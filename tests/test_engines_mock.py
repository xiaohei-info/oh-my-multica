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


def test_mock_pull_request_snapshot_uses_explicit_authoritative_scope_config():
    store = _engine(
        MOCK_PR_HEAD_REVISION="a" * 40,
        MOCK_PR_BASE_REVISION="b" * 40,
        MOCK_PR_AUTHOR="worker-login",
        MOCK_PR_COMMIT_AUTHORS='["worker-login", "pair-login"]',
        MOCK_PR_CHANGED_FILES='["src/feature.py", "tests/test_feature.py"]',
    ).store

    snapshot = store.inspect_pull_request(
        "https://github.com/acme/project/pull/7")

    assert snapshot.author_login == "worker-login"
    assert snapshot.commit_authors == ("worker-login", "pair-login")
    assert snapshot.base_revision == "b" * 40
    assert snapshot.changed_files == ("src/feature.py", "tests/test_feature.py")


def test_mock_pull_request_snapshot_does_not_fabricate_review_scope():
    snapshot = _engine().store.inspect_pull_request(
        "https://github.com/acme/project/pull/7")

    assert snapshot.author_login == ""
    assert snapshot.commit_authors == ()
    assert snapshot.base_revision == ""
    assert snapshot.changed_files == ()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MOCK_PR_DRAFT", "sometimes"),
        ("MOCK_PR_STATE", "open"),
        ("MOCK_PR_COMMIT_AUTHORS", "not-json"),
        ("MOCK_PR_CHANGED_FILES", '["", "src/a.py"]'),
    ],
)
def test_mock_pull_request_snapshot_rejects_malformed_authority_config(key, value):
    store = _engine(**{key: value}).store

    with pytest.raises(ValidationError):
        store.inspect_pull_request("https://github.com/acme/project/pull/7")


def test_mock_merge_rejects_command_injection_revision(tmp_path):
    store = _engine().store
    marker = tmp_path / "revision-injected"

    with pytest.raises(ValidationError, match="revision"):
        store.merge_pull_request(
            "https://github.com/acme/project/pull/7",
            f"abc123; touch {marker}",
            "sh -c 'exit 0' ignored {pr_url} {delivered_revision}",
            30,
        )
    assert not marker.exists()


def test_mock_delivery_rejects_unsafe_pr_url_before_execution(tmp_path):
    store = _engine().store
    marker = tmp_path / "pr-url-injected"

    with pytest.raises(ValidationError, match="PR URL"):
        store.run_ci_check(
            f"https://x; touch {marker}",
            "sh -c 'exit 0' ignored {pr_url}",
            30,
        )
    assert not marker.exists()


def test_mock_delivery_command_executes_as_argv_without_shell(monkeypatch):
    store = _engine().store
    seen = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr("omac.engines.mock.subprocess.run", run)

    result = store.merge_pull_request(
        "https://github.com/acme/project/pull/7",
        "a" * 40,
        "custom-merge --url {pr_url} --revision {delivered_revision}",
        30,
    )

    assert result.passed
    assert seen["argv"] == [
        "custom-merge", "--url", "https://github.com/acme/project/pull/7",
        "--revision", "a" * 40,
    ]
    assert seen["kwargs"].get("shell") is not True

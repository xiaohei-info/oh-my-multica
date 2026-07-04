"""work show:9 种(kind × phase)组合快照 + submit 模板防漂移。"""
from __future__ import annotations

import pytest

from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines import create_engine
from omac.engines.models import EngineConfig
from omac.pipeline.dispatch import (
    SUBMIT_PARAM_SPECS,
    SUBMIT_PARAMS_BY_KIND_PHASE,
    build_show_output,
    submit_template_for,
)
from omac.cli.main import main
from omac.cli import exit_codes


def _store(auto_complete: str = "false"):
    config = EngineConfig(
        engine_type="mock", workspace_id="mock-workspace",
        extra={"MOCK_AUTO_COMPLETE": auto_complete,
              "MOCK_AUTO_COMPLETE_DELAY": "0"})
    return create_engine("mock", config).store


def _make_item(store, kind: TaskKind, phase: TaskPhase, dag_key: str = "a",
               with_contract: bool = False, with_deliverable: bool = False,
               with_verification: bool = False):
    item = store.create_work_item(
        "mock-workspace", f"title-{kind.value}", "desc",
        dag_key=dag_key, worker="alice", reviewer="bob",
        kind=kind)
    store.update_work_item_metadata(item.id, phase=phase)
    if with_contract:
        # 走真实 dispatch 路径:set_node_contract 下发 contract(§7.4),
        # 验证 work show 能读回完整上下文(回归 set_node_contract → work show 链路)。
        store.set_node_contract(item.id, {
            "objective": "实现 X",
            "acceptance": ["A 工作", "B 工作"],
            "non_goals": ["不做 Y"],
            "verification_commands": ["pytest -q"],
            "integration_gates": [],
            "pr_base": "feature/v1",
            "coverage_gate": 90,
        })
    if with_deliverable:
        store.update_work_item_metadata(
            item.id, phase=phase, deliverable="# 计划正文")
    if with_verification:
        store.update_work_item_metadata(
            item.id, phase=phase,
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0,
                              "summary": "ok"}],
                "pr_base": "feature/v1",
                "coverage": 92,
                "env_setup": ["docker compose up -d db"],
            })
    return store.get_work_item(item.id)


# 9 种组合(final-acceptance 仅 authoring)
COMBINATIONS = [
    (TaskKind.PLAN, TaskPhase.AUTHORING),
    (TaskKind.PLAN, TaskPhase.REVIEW),
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING),
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW),
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING),
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW),
    (TaskKind.DEVELOP, TaskPhase.AUTHORING),
    (TaskKind.DEVELOP, TaskPhase.REVIEW),
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING),
]


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_show_output_structure(kind, phase):
    """每种组合输出都包含四段:task/context/protocol/submit。"""
    store = _store()
    with_contract = (phase == TaskPhase.AUTHORING)
    item = _make_item(store, kind, phase, with_contract=with_contract,
                      with_deliverable=(phase == TaskPhase.REVIEW),
                      with_verification=(kind == TaskKind.DEVELOP
                                         and phase == TaskPhase.REVIEW))
    identity = f"worker:{item.worker}" if phase == TaskPhase.AUTHORING \
        else f"reviewer:{item.reviewer}"
    out = build_show_output(item, identity)

    assert "task" in out
    assert "context" in out
    assert "protocol" in out
    assert "submit" in out

    # 任务标识
    assert out["task"]["kind"] == kind.value
    assert out["task"]["phase"] == phase.value
    assert out["task"]["dag_key"] == "a"
    assert out["task"]["identity"] == identity

    # 协议非空
    assert out["protocol"].strip() != ""

    # submit 模板以 omac work submit <id> 开头
    assert out["submit"].startswith(f"omac work submit {item.id}")

    # authoring 阶段 context 含 contract
    if phase == TaskPhase.AUTHORING:
        assert "contract" in out["context"]

    # develop×review 阶段 context 含 env_setup 复跑清单
    if kind == TaskKind.DEVELOP and phase == TaskPhase.REVIEW:
        assert "env_setup" in out["context"]
        assert out["context"]["env_setup"] == ["docker compose up -d db"]


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_submit_template_matches_registered_params(kind, phase):
    """submit 模板使用的参数名必须与 SUBMIT_PARAM_SPECS 注册的一致(防漂移)。"""
    template = submit_template_for(kind, phase, "42")
    expected_params = SUBMIT_PARAMS_BY_KIND_PHASE[(kind, phase)]
    # 模板中每个 --xxx 都出现在注册表中
    for param in expected_params:
        assert param in template, f"模板缺少参数 {param}: {template}"
    # 模板中不应出现未注册的 -- 参数
    import re
    used_flags = re.findall(r"--\w+(?:-\w+)*", template)
    for flag in used_flags:
        assert flag in SUBMIT_PARAM_SPECS, \
            f"模板使用了未注册参数 {flag}: {template}"


def test_all_kind_phase_pairs_covered():
    """9 种组合全部在 SUBMIT_PARAMS_BY_KIND_PHASE 中有定义。"""
    assert set(SUBMIT_PARAMS_BY_KIND_PHASE.keys()) == set(COMBINATIONS)


def test_show_cli_json_output(tmp_path, monkeypatch, capsys):
    """CLI 入口:work show --output json 输出合法 JSON,exit 0。"""
    monkeypatch.chdir(tmp_path)
    # 写配置指向 mock 引擎
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)

    assert main(["work", "show", item.id, "--output", "json"]) == exit_codes.OK
    import json
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["task"]["kind"] == "develop"
    assert data["task"]["phase"] == "authoring"
    assert data["context"]["contract"]["objective"] == "实现 X"
    assert data["submit"].startswith(f"omac work submit {item.id}")


def test_show_cli_table_output(tmp_path, monkeypatch, capsys):
    """CLI 入口:work show 默认 table 输出包含四段标题。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.REVIEW,
                      with_deliverable=True)

    assert main(["work", "show", item.id]) == exit_codes.OK
    out = capsys.readouterr().out
    for section in ("任务标识", "完整上下文", "执行协议", "submit 模板"):
        assert section in out
    assert "plan" in out
    assert "review" in out


def test_set_node_contract_visible_in_show():
    """回归:set_node_contract 下发的 contract 必须在 work show 中可见(真实 dispatch 路径)。

    这是被派发 agent 第一入口的关键链路:dispatch 侧调用 set_node_contract 下发契约,
    被派发 agent 调 work show 必须能读回完整 contract,否则拿到的是空上下文。
    """
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    # 从 store 重新读取(模拟 agent 侧 get_work_item),确认 contract 已持久化
    got = store.get_work_item(item.id)
    assert got.contract is not None, (
        "set_node_contract 后 WorkItem.contract 必须非空,否则 work show 拿不到上下文")
    assert got.contract["objective"] == "实现 X"
    # 走 build_show_output 验证上下文完整
    out = build_show_output(got, f"worker:{got.worker}")
    assert out["context"]["contract"] is not None
    assert out["context"]["contract"]["acceptance"] == ["A 工作", "B 工作"]
    assert out["context"]["contract"]["pr_base"] == "feature/v1"



def test_show_unknown_issue_id(tmp_path, monkeypatch, capsys):
    """issue_id 不存在时给出教学性报错,exit 5。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    assert main(["work", "show", "99999"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "99999" in err

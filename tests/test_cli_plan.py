"""cli.plan:check(lint 门 + review 阶段;exit 0/5/20) 与 show(摘要拓扑 + 契约覆盖)。

review 拒绝注入用 main 的 MockStore.set_review_rejects(n),配合
MOCK_AUTO_COMPLETE_DELAY=0 让评审在首轮 wake 即收敛,避免真实等待。
"""
from __future__ import annotations

import json
import os

import pytest

from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig


# ── fixtures ──────────────────────────────────────────────────────────────

CLEAN_MANIFEST = """\
meta:
  name: demo
nodes:
  - id: a
    worker: alice
    contract:
      objective: 实现 a
      acceptance: ["a 可运行"]
      source_of_truth: ["docs/a.md"]
      non_goals: ["不碰 b"]
      verification_commands: ["pytest tests/a"]
      integration_gates:
        - name: a-gate
          layer: L1
          delivery_goal: a 交付
          source_of_truth: ["docs/a.md"]
          covers: ["route-a"]
          acceptance_refs: ["a 可运行"]
          commands: ["pytest tests/int/a"]
      pr_base: feature/v1
  - id: b
    worker: bob
    reviewer: alice
    blocked_by: [a]
    contract:
      objective: 实现 b
      acceptance: ["b 可运行"]
      source_of_truth: ["docs/b.md"]
      non_goals: ["不碰 a"]
      verification_commands: ["pytest tests/b"]
      integration_gates:
        - name: b-gate
          layer: L1
          delivery_goal: b 交付
          source_of_truth: ["docs/b.md"]
          covers: ["route-b"]
          acceptance_refs: ["b 可运行"]
          commands: ["pytest tests/int/b"]
      pr_base: feature/v1
"""

BAD_MANIFEST = """\
meta:
  name: bad
nodes:
  - id: a
    worker: ghost
    blocked_by: [missing]
    contract:
      objective: ""
      acceptance: []
      non_goals: []
      verification_commands: []
      integration_gates: []
      pr_base: ""
"""


def _write(tmp_path, content, name="m.yaml"):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _configure_mock(tmp_path, monkeypatch, *, reviewers=("alice",)):
    """写入最小 mock 配置(引擎 + 角色),返回建好的 mock engine。"""
    monkeypatch.chdir(tmp_path)
    assert main(["config", "set", "engine", "mock"]) == exit_codes.OK
    assert main(["config", "set", "workspace", "mock-workspace"]) == exit_codes.OK
    assert main(["config", "set", "roles.workers", '["alice", "bob"]']) == exit_codes.OK
    reviewers_list = "[" + ", ".join(f'"{r}"' for r in reviewers) + "]"
    assert main(["config", "set", "roles.reviewers", reviewers_list]) == exit_codes.OK
    engine = create_engine(
        "mock",
        EngineConfig(
            engine_type="mock",
            workspace_id="mock-workspace",
            extra={"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"},
        ),
    )
    return engine


# ── help / command taxonomy ───────────────────────────────────────────────

def test_plan_help_focuses_on_design_solution(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["plan", "-h"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "design-to-manifest" in out
    assert "计划制定" not in out
    assert "check     lint" not in out
    assert "show      查看 manifest" not in out


def test_dag_help_owns_manifest_check_and_show(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["dag", "-h"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "check" in out and "manifest" in out
    assert "show" in out and "manifest" in out


# ── dag check ─────────────────────────────────────────────────────────────

def test_plan_rejects_manifest_subcommands(capsys):
    for action in ("check", "show"):
        with pytest.raises(SystemExit) as exc:
            main(["plan", action, "m.yaml"])
        assert exc.value.code == exit_codes.GENERIC
    err = capsys.readouterr().err
    assert "invalid choice" in err

def test_check_missing_file_is_validation_error(tmp_path):
    assert main(["dag", "check", str(tmp_path / "nope.yaml")]) == exit_codes.VALIDATION


def test_check_requires_engine_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["dag", "check", path]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "引擎" in err or "engine" in err.lower()


def test_check_clean_manifest_passes(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(0)
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["dag", "check", path]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "Lint passed" in out
    assert "Review passed" in out


def test_check_review_issue_is_human_first_with_agent_json_entry(
        tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(0)
    assert main(["config", "set", "project", "project-42"]) == exit_codes.OK
    path = _write(tmp_path, CLEAN_MANIFEST)

    assert main(["dag", "check", path]) == exit_codes.OK
    capsys.readouterr()
    items = engine.store.list_work_items("mock-workspace")
    review_item = items[-1]

    assert review_item.kind.value == "decompose"
    assert (
        "OMAC_ENGINE=mock OMAC_WORKSPACE_ID=mock-workspace "
        "OMAC_PROJECT_ID=project-42 "
        f"omac work show {review_item.id} --output json"
        in review_item.description
    )
    assert "## Task summary" in review_item.description
    assert "objective: 实现 a" not in review_item.description
    assert "objective: 实现 a" in review_item.deliverable
    assert "omac work submit" not in review_item.description

    assert main(["work", "show", review_item.id]) == exit_codes.OK
    agent_view = json.loads(capsys.readouterr().out)
    assert "objective: 实现 a" in agent_view["context"]["deliverable"]
    assert agent_view["guide_refs"] == [
        "omac guide role reviewer",
        "omac guide artifact manifest",
    ]
    assert "## 硬约束" not in review_item.description


def test_check_clean_manifest_json_output(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(0)
    capsys.readouterr()  # 清空 config set 输出
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["dag", "check", path, "--output", "json"]) == exit_codes.OK
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["lint_errors"] == 0


def test_check_bad_manifest_exit_5_with_full_error_list(tmp_path, monkeypatch, capsys):
    _configure_mock(tmp_path, monkeypatch)
    path = _write(tmp_path, BAD_MANIFEST)
    assert main(["dag", "check", path, "--no-review"]) == exit_codes.VALIDATION
    out = capsys.readouterr().out
    # 错误清单应覆盖:worker 不在池、未知依赖、contract 必填字段
    for needle in ("ghost", "missing", "objective", "acceptance", "pr_base"):
        assert needle in out, f"缺错误项:{needle}"


def test_check_bad_manifest_json_error_list(tmp_path, monkeypatch, capsys):
    _configure_mock(tmp_path, monkeypatch)
    capsys.readouterr()  # 清空 config set 输出
    path = _write(tmp_path, BAD_MANIFEST)
    assert main(["dag", "check", path, "--no-review", "--output", "json"]) == exit_codes.VALIDATION
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert len(data["errors"]) >= 1


def test_check_review_reject_exit_20(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(1)  # 首轮评审自动 reject
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["dag", "check", path]) == exit_codes.NEEDS_DECISION


def test_check_no_review_skips_review_stage(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(1)  # 即便注入 reject
    path = _write(tmp_path, CLEAN_MANIFEST)
    # --no-review 应跳过 review,exit 0
    assert main(["dag", "check", path, "--no-review"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "Lint passed" in out


# ── dag show ──────────────────────────────────────────────────────────────

def test_show_missing_file_is_validation_error(tmp_path):
    assert main(["dag", "show", str(tmp_path / "nope.yaml")]) == exit_codes.VALIDATION


def test_show_table_summary(tmp_path, capsys):
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["dag", "show", path]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "Nodes: 2" in out
    assert "Contract coverage: 2/2" in out
    assert "wave" in out
    assert "a" in out and "b" in out


def test_show_json_structure(tmp_path, capsys):
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["dag", "show", path, "--output", "json"]) == exit_codes.OK
    data = json.loads(capsys.readouterr().out)
    assert data["nodes"]["total"] == 2
    assert data["nodes"]["with_contract"] == 2
    assert data["nodes"]["contract_coverage"] == "2/2"
    waves = data["topology"]["waves"]
    assert "0" in waves and "a" in waves["0"]
    assert "1" in waves and "b" in waves["1"]
    assert ["a", "b"] in data["topology"]["edges"]


def test_show_partial_contract_coverage(tmp_path, capsys):
    partial = """\
meta:
  name: partial
nodes:
  - id: a
    worker: alice
    contract:
      objective: do a
      acceptance: ["a works"]
      non_goals: []
      verification_commands: ["pytest a"]
      integration_gates:
        - name: g
          layer: L1
          delivery_goal: d
          source_of_truth: ["docs"]
          covers: ["x"]
          acceptance_refs: ["a works"]
          commands: ["pytest int"]
      pr_base: feature/v1
  - id: b
    worker: bob
    blocked_by: [a]
"""
    path = _write(tmp_path, partial)
    assert main(["dag", "show", path, "--output", "json"]) == exit_codes.OK
    data = json.loads(capsys.readouterr().out)
    assert data["nodes"]["total"] == 2
    assert data["nodes"]["with_contract"] == 1
    assert data["nodes"]["contract_coverage"] == "1/2"


# ── plan create(P3.3) ──────────────────────────────────────────────────────

PLAN_TEXT = """\
# 演示设计方案

目标:实现 demo 特性,包含 login 与 dashboard 两个节点。
步骤:
1. 搭 login
2. 搭 dashboard
"""

ACCEPTANCE_YAML = """\
flows:
  - id: flow-login
    name: 登录流程
    actions:
      - step: 打开登录页
        how: GET /login
        expected: 返回 200 与登录表单
      - step: 提交合法凭证
        how: POST /login {user, pwd}
        expected: 跳转到首页
  - id: flow-dashboard
    name: 仪表盘流程
    actions:
      - step: 访问仪表盘
        how: GET /dash
        expected: 显示数据卡片
"""

GOOD_MANIFEST = """\
meta:
  name: demo-create
nodes:
  - id: login
    worker: alice
    contract:
      objective: 实现登录
      acceptance:
        - flow-login
      source_of_truth: ["docs/login.md"]
      non_goals: ["不改 dashboard"]
      verification_commands: ["pytest tests/login"]
      integration_gates:
        - name: login-gate
          layer: L1
          delivery_goal: 登录交付
          source_of_truth: ["docs/login.md"]
          covers: ["route-login"]
          acceptance_refs: ["flow-login"]
          commands: ["pytest tests/int/login"]
      pr_base: feature/demo
  - id: dashboard
    worker: bob
    blocked_by: [login]
    contract:
      objective: 实现仪表盘
      acceptance:
        - flow-dashboard
      source_of_truth: ["docs/dash.md"]
      non_goals: ["不改 login"]
      verification_commands: ["pytest tests/dash"]
      integration_gates:
        - name: dash-gate
          layer: L1
          delivery_goal: 仪表盘交付
          source_of_truth: ["docs/dash.md"]
          covers: ["route-dash"]
          acceptance_refs: ["flow-dashboard"]
          commands: ["pytest tests/int/dash"]
      pr_base: feature/demo
"""

BAD_MANIFEST_LINT = """\
meta:
  name: bad-create
nodes:
  - id: login
    worker: ghost
    blocked_by: [missing]
    contract:
      objective: ""
      acceptance: []
      non_goals: []
      verification_commands: []
      integration_gates: []
      pr_base: ""
"""


def _configure_create_mock(tmp_path, monkeypatch):
    """完整 mock 配置:planner/orchestrator/workers/reviewers 角色齐全。"""
    from omac.core.taskmeta import TaskKind
    monkeypatch.chdir(tmp_path)
    assert main(["config", "set", "engine", "mock"]) == exit_codes.OK
    assert main(["config", "set", "workspace", "mock-workspace"]) == exit_codes.OK
    assert main(["config", "set", "roles.planner", "alice"]) == exit_codes.OK
    assert main(["config", "set", "roles.orchestrator", "bob"]) == exit_codes.OK
    assert main(["config", "set", "roles.workers", '["alice", "bob"]']) == exit_codes.OK
    assert main(["config", "set", "roles.reviewers",
                 '["alice", "bob", "charlie"]']) == exit_codes.OK
    engine = create_engine(
        "mock",
        EngineConfig(
            engine_type="mock",
            workspace_id="mock-workspace",
            extra={"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"},
        ),
    )
    engine.store.set_review_rejects(0)
    # 人机门默认开启:模拟人工把设计/验收 issue 流转到 DONE 放行。
    MockStore.set_auto_confirm(True)
    MockStore.set_kind_delivery("plan", {"plan": PLAN_TEXT})
    MockStore.set_kind_delivery("acceptance", {"acceptance": ACCEPTANCE_YAML})
    MockStore.set_kind_delivery("decompose", {"manifest": GOOD_MANIFEST})
    return engine


def _first_item_of_kind(engine, kind):
    """取该 kind 的首个 work item(按创建顺序)。"""
    from omac.core.taskmeta import TaskKind
    items = [i for i in engine.store.list_work_items(engine.store.config.workspace_id)
             if i.kind == kind]
    return items[0] if items else None


def test_create_default_combination(tmp_path, monkeypatch, capsys):
    import yaml
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "demo-create"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "manifest: .omac/demo-create.yaml" in out
    assert "omac dag run .omac/demo-create.yaml" in out
    assert (tmp_path / ".omac" / "demo-create.yaml").exists()
    assert (tmp_path / ".omac" / "demo-create.acceptance.yaml").exists()
    data = yaml.safe_load((tmp_path / ".omac" / "demo-create.yaml").read_text())
    assert data["meta"]["acceptance_required"] is True
    assert data["meta"]["acceptance_file"] == "demo-create.acceptance.yaml"
    plan_id = data["meta"]["plan_id"]
    # 验证上游产物已流入 acceptance / decompose 的 issue body
    from omac.core.taskmeta import TaskKind
    plan_item = _first_item_of_kind(engine, TaskKind.PLAN)
    acc_item = _first_item_of_kind(engine, TaskKind.ACCEPTANCE)
    dec_item = _first_item_of_kind(engine, TaskKind.DECOMPOSE)
    assert plan_item is not None, "应创建 plan work item"
    assert acc_item is not None, "应创建 acceptance work item"
    assert dec_item is not None, "应创建 decompose work item"
    assert plan_item.dag_key == f"plan-{plan_id}"
    assert acc_item.dag_key == f"acceptance-{plan_id}"
    assert dec_item.dag_key == f"decompose-{plan_id}"
    assert "演示设计方案" in acc_item.description, "acceptance issue body 应含定稿设计方案"
    assert "演示设计方案" in dec_item.description, "decompose issue body 应含定稿设计方案"
    assert "登录流程" in dec_item.description, "decompose issue body 应含验收文档(flow)"


def test_create_chinese_name_uses_generated_plan_id_for_dag_keys(tmp_path, monkeypatch):
    import re
    import yaml
    from omac.core.taskmeta import TaskKind

    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "支付流程重构"]) == exit_codes.OK
    data = yaml.safe_load((tmp_path / ".omac" / "支付流程重构.yaml").read_text())
    plan_id = data["meta"].get("plan_id")
    assert re.fullmatch(r"p-[0-9a-f]{8}", plan_id)

    plan_item = _first_item_of_kind(engine, TaskKind.PLAN)
    acc_item = _first_item_of_kind(engine, TaskKind.ACCEPTANCE)
    dec_item = _first_item_of_kind(engine, TaskKind.DECOMPOSE)
    assert plan_item.dag_key == f"plan-{plan_id}"
    assert acc_item.dag_key == f"acceptance-{plan_id}"
    assert dec_item.dag_key == f"decompose-{plan_id}"
    assert all("-task" not in i.dag_key for i in (plan_item, acc_item, dec_item))


def test_create_with_goal_injects_requirement_into_planner(tmp_path, monkeypatch):
    """--goal 时:planner 的 PLAN issue body 应含需求(经 source_of_truth 通道)。"""
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "demo-goal",
                 "--goal", "实现函数 add(a,b) 返回两数之和"]) == exit_codes.OK
    plan_item = _first_item_of_kind(engine, TaskKind.PLAN)
    assert plan_item is not None, "无 --doc 时应创建 plan 阶段 work item"
    assert "实现函数 add" in plan_item.description, "planner issue body 应含需求"


def test_create_goal_required_rejects_empty_goal(tmp_path, monkeypatch, capsys):
    _configure_create_mock(tmp_path, monkeypatch)
    assert main(["config", "set", "workflow.goal_required", "true"]) == exit_codes.OK
    capsys.readouterr()

    code = main(["plan", "create", "--name", "demo-need-goal"])

    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "workflow.goal_required" in err
    assert "--goal" in err


def test_create_workflow_human_in_loop_false_skips_confirm_gate(tmp_path, monkeypatch):
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["config", "set", "workflow.human_in_loop", "false"]) == exit_codes.OK
    MockStore.set_auto_confirm(False)

    assert main(["plan", "create", "--name", "demo-agent-flow"]) == exit_codes.OK

    plan_item = _first_item_of_kind(engine, TaskKind.PLAN)
    assert plan_item is not None
    assert plan_item.status.value == "done"


def test_create_workflow_review_false_skips_review_stages(tmp_path, monkeypatch):
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["config", "set", "workflow.review", "false"]) == exit_codes.OK
    engine.store.set_review_verdict("reject")

    assert main(["plan", "create", "--name", "demo-workflow-noreview"]) == exit_codes.OK


def test_create_workflow_acceptance_doc_false_skips_acceptance_phase(tmp_path, monkeypatch):
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["config", "set", "workflow.acceptance_doc", "false"]) == exit_codes.OK

    assert main(["plan", "create", "--name", "demo-workflow-noacc"]) == exit_codes.OK

    assert (tmp_path / ".omac" / "demo-workflow-noacc.yaml").exists()
    assert not (tmp_path / ".omac" / "demo-workflow-noacc.acceptance.yaml").exists()
    acc_item = _first_item_of_kind(engine, TaskKind.ACCEPTANCE)
    assert acc_item is None, "workflow.acceptance_doc=false 时不应创建 acceptance work item"


def test_resolve_goal_precedence_and_exclusivity(tmp_path):
    """_resolve_goal:--goal 直给 / --goal-file 读文件 / 二者互斥报错 / 缺省 None。"""
    from types import SimpleNamespace
    from omac.cli.commands.plan import _resolve_goal
    from omac.errors import ValidationError

    assert _resolve_goal(SimpleNamespace(goal="G", goal_file=None)) == "G"
    f = tmp_path / "need.md"
    f.write_text("需求正文")
    assert _resolve_goal(SimpleNamespace(goal=None, goal_file=str(f))) == "需求正文"
    assert _resolve_goal(SimpleNamespace(goal=None, goal_file=None)) is None
    with pytest.raises(ValidationError):
        _resolve_goal(SimpleNamespace(goal="G", goal_file=str(f)))
    with pytest.raises(ValidationError):
        _resolve_goal(SimpleNamespace(goal=None, goal_file="/no/such/file"))


def test_create_with_doc_skips_plan(tmp_path, monkeypatch):
    """给了 --doc 时,不应创建 plan 阶段的 work item。"""
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    doc = tmp_path / "design.md"
    doc.write_text(PLAN_TEXT)
    assert main(["plan", "create", "--name", "demo-doc", "--doc", str(doc)]) == exit_codes.OK
    assert (tmp_path / ".omac" / "demo-doc.yaml").exists()
    plan_item = _first_item_of_kind(engine, TaskKind.PLAN)
    assert plan_item is None, "带 --doc 时不应创建 plan 阶段 work item"


def test_create_threads_source_refs_through_chain(tmp_path, monkeypatch):
    """provenance:验收 body 引用计划 issue;拆解 body 引用计划+验收 issue(防跑偏)。"""
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "demo-prov"]) == exit_codes.OK
    plan_item = _first_item_of_kind(engine, TaskKind.PLAN)
    acc_item = _first_item_of_kind(engine, TaskKind.ACCEPTANCE)
    dec_item = _first_item_of_kind(engine, TaskKind.DECOMPOSE)
    assert f"#{plan_item.id}" in acc_item.description
    assert f"#{plan_item.id}" in dec_item.description
    assert f"#{acc_item.id}" in dec_item.description


def test_create_records_source_issues_in_manifest_meta(tmp_path, monkeypatch):
    """provenance:manifest meta.source_issues 记录设计/验收/拆解源头 issue。"""
    import yaml
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "demo-prov2"]) == exit_codes.OK
    data = yaml.safe_load((tmp_path / ".omac" / "demo-prov2.yaml").read_text())
    src = [str(x) for x in (data["meta"].get("source_issues") or [])]
    dec_item = _first_item_of_kind(engine, TaskKind.DECOMPOSE)
    assert dec_item.id in src, "manifest meta.source_issues 应含拆解源头 issue"


def test_plan_confirm_marks_waiting_issue_done(tmp_path, monkeypatch):
    """omac plan confirm <name>:手动把停在人机门的设计/验收 issue 流转到 DONE。"""
    from omac.core.taskmeta import TaskKind, TaskPhase
    from omac.engines.models import WorkItemStatus
    engine = _configure_create_mock(tmp_path, monkeypatch)
    MockStore.set_auto_confirm(False)  # 关自动确认,验证手动放行
    item = engine.store.create_work_item(
        "mock-workspace", "demo-confirm 设计方案", "demo-confirm 设计方案", dag_key="plan",
        worker="alice", kind=TaskKind.PLAN)
    engine.store.update_work_item_metadata(
        item.id, deliverable="设计方案正文", phase=TaskPhase.REVIEW)
    engine.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    assert main(["plan", "confirm", "--name", "demo-confirm"]) == exit_codes.OK
    assert engine.store.get_work_item(item.id).status == WorkItemStatus.DONE


def test_plan_confirm_dag_key_exactly_selects_waiting_issue(tmp_path, monkeypatch):
    """name 会重复;人工门确认必须支持用 dag_key 精确定位同一条 plan 流水线。"""
    from omac.core.taskmeta import TaskKind, TaskPhase
    from omac.engines.models import WorkItemStatus

    engine = _configure_create_mock(tmp_path, monkeypatch)
    MockStore.set_auto_confirm(False)
    first = engine.store.create_work_item(
        "mock-workspace", "重复名 设计方案", "重复名 设计方案",
        dag_key="plan-p-first111", worker="alice", kind=TaskKind.PLAN)
    second = engine.store.create_work_item(
        "mock-workspace", "重复名 设计方案", "重复名 设计方案",
        dag_key="plan-p-second22", worker="alice", kind=TaskKind.PLAN)
    for item in (first, second):
        engine.store.update_work_item_metadata(
            item.id, deliverable="设计方案正文", phase=TaskPhase.REVIEW)
        engine.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    assert main(["plan", "confirm", "--dag-key", "plan-p-second22"]) == exit_codes.OK
    assert engine.store.get_work_item(first.id).status == WorkItemStatus.IN_REVIEW
    assert engine.store.get_work_item(second.id).status == WorkItemStatus.DONE


def test_plan_resume_reuses_existing_plan_issue_by_dag_key(tmp_path, monkeypatch, capsys):
    """中断后续跑以 dag_key 为锚点,不能按 name 新建第二个 plan issue。"""
    from omac.core.taskmeta import TaskKind, TaskPhase
    from omac.engines.models import WorkItemStatus

    engine = _configure_create_mock(tmp_path, monkeypatch)
    plan_item = engine.store.create_work_item(
        "mock-workspace", "[DAG:plan-p-resume01] 重复名 设计方案",
        "重复名 设计方案", dag_key="plan-p-resume01",
        worker="alice", kind=TaskKind.PLAN)
    engine.store.update_work_item_metadata(
        plan_item.id, deliverable=PLAN_TEXT, phase=TaskPhase.REVIEW)
    engine.store.update_status(plan_item.id, WorkItemStatus.DONE)

    assert main(["plan", "resume", "--dag-key", "plan-p-resume01"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "manifest: .omac/重复名.yaml" in out
    assert "omac dag run '.omac/重复名.yaml'" in out

    plan_items = [
        item for item in engine.store.list_work_items("mock-workspace")
        if item.kind == TaskKind.PLAN and item.dag_key == "plan-p-resume01"
    ]
    assert [item.id for item in plan_items] == [plan_item.id]
    assert (tmp_path / ".omac" / "重复名.yaml").exists()


def test_plan_confirm_no_waiting_issue_is_validation_error(tmp_path, monkeypatch):
    """没有待确认的 issue 时 confirm 报校验错(exit 5),提示无可放行对象。"""
    _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "confirm", "--name", "nonexistent"]) == exit_codes.VALIDATION


def test_create_no_review_skips_review_stages(tmp_path, monkeypatch):
    _configure_create_mock(tmp_path, monkeypatch)
    # 即便注入 reject,--no-review 应跳过 review 仍 exit 0
    assert main(["plan", "create", "--name", "demo-noreview",
                 "--no-review"]) == exit_codes.OK


def test_create_no_acceptance_skips_acceptance_phase(tmp_path, monkeypatch):
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "demo-noacc",
                 "--no-acceptance"]) == exit_codes.OK
    assert (tmp_path / ".omac" / "demo-noacc.yaml").exists()
    assert not (tmp_path / ".omac" / "demo-noacc.acceptance.yaml").exists()
    import yaml
    data = yaml.safe_load((tmp_path / ".omac" / "demo-noacc.yaml").read_text())
    assert data["meta"]["acceptance_required"] is False
    acc_item = _first_item_of_kind(engine, TaskKind.ACCEPTANCE)
    assert acc_item is None, "带 --no-acceptance 时不应创建 acceptance work item"


def test_create_syncs_manifest_and_acceptance_as_one_plan_output(
        tmp_path, monkeypatch):
    import omac.pipeline.plan as plan_mod

    _configure_create_mock(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        plan_mod,
        "commit_files",
        lambda paths, message, **kwargs: calls.append((list(paths), message)) or True,
    )

    assert main(["plan", "create", "--name", "demo-sync"]) == exit_codes.OK

    assert calls == [
        ([".omac/demo-sync.yaml", ".omac/demo-sync.acceptance.yaml"],
         "chore(omac): sync plan outputs"),
    ]


def test_create_lint_reject_revises_then_passes(tmp_path, monkeypatch):
    """注入一次坏 manifest → lint 机器门回贴修订 → 第二次好 manifest 通过。"""
    _configure_create_mock(tmp_path, monkeypatch)
    MockStore.set_kind_delivery_sequence(
        "decompose",
        [{"manifest": BAD_MANIFEST_LINT}, {"manifest": GOOD_MANIFEST}])
    assert main(["plan", "create", "--name", "demo-lint"]) == exit_codes.OK
    assert (tmp_path / ".omac" / "demo-lint.yaml").exists()

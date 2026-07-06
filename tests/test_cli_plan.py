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


# ── plan check ────────────────────────────────────────────────────────────

def test_check_missing_file_is_validation_error(tmp_path):
    assert main(["plan", "check", str(tmp_path / "nope.yaml")]) == exit_codes.VALIDATION


def test_check_requires_engine_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["plan", "check", path]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "引擎" in err or "engine" in err.lower()


def test_check_clean_manifest_passes(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(0)
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["plan", "check", path]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "lint 通过" in out
    assert "review 通过" in out


def test_check_clean_manifest_json_output(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(0)
    capsys.readouterr()  # 清空 config set 输出
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["plan", "check", path, "--output", "json"]) == exit_codes.OK
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["lint_errors"] == 0


def test_check_bad_manifest_exit_5_with_full_error_list(tmp_path, monkeypatch, capsys):
    _configure_mock(tmp_path, monkeypatch)
    path = _write(tmp_path, BAD_MANIFEST)
    assert main(["plan", "check", path, "--no-review"]) == exit_codes.VALIDATION
    out = capsys.readouterr().out
    # 错误清单应覆盖:worker 不在池、未知依赖、contract 必填字段
    for needle in ("ghost", "missing", "objective", "acceptance", "pr_base"):
        assert needle in out, f"缺错误项:{needle}"


def test_check_bad_manifest_json_error_list(tmp_path, monkeypatch, capsys):
    _configure_mock(tmp_path, monkeypatch)
    capsys.readouterr()  # 清空 config set 输出
    path = _write(tmp_path, BAD_MANIFEST)
    assert main(["plan", "check", path, "--no-review", "--output", "json"]) == exit_codes.VALIDATION
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert len(data["errors"]) >= 1


def test_check_review_reject_exit_20(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(1)  # 首轮评审自动 reject
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["plan", "check", path]) == exit_codes.NEEDS_DECISION


def test_check_no_review_skips_review_stage(tmp_path, monkeypatch, capsys):
    engine = _configure_mock(tmp_path, monkeypatch)
    engine.store.set_review_rejects(1)  # 即便注入 reject
    path = _write(tmp_path, CLEAN_MANIFEST)
    # --no-review 应跳过 review,exit 0
    assert main(["plan", "check", path, "--no-review"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "lint 通过" in out


# ── plan show ─────────────────────────────────────────────────────────────

def test_show_missing_file_is_validation_error(tmp_path):
    assert main(["plan", "show", str(tmp_path / "nope.yaml")]) == exit_codes.VALIDATION


def test_show_table_summary(tmp_path, capsys):
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["plan", "show", path]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "节点:2" in out
    assert "契约覆盖:2/2" in out
    assert "wave" in out
    assert "a" in out and "b" in out


def test_show_json_structure(tmp_path, capsys):
    path = _write(tmp_path, CLEAN_MANIFEST)
    assert main(["plan", "show", path, "--output", "json"]) == exit_codes.OK
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
    assert main(["plan", "show", path, "--output", "json"]) == exit_codes.OK
    data = json.loads(capsys.readouterr().out)
    assert data["nodes"]["total"] == 2
    assert data["nodes"]["with_contract"] == 1
    assert data["nodes"]["contract_coverage"] == "1/2"


# ── plan create(P3.3) ──────────────────────────────────────────────────────

PLAN_TEXT = """\
# 演示计划

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


def test_create_default_combination(tmp_path, monkeypatch):
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "demo-create"]) == exit_codes.OK
    assert (tmp_path / ".omac" / "demo-create.yaml").exists()
    assert (tmp_path / ".omac" / "demo-create.acceptance.yaml").exists()
    # 验证上游产物已流入 acceptance / decompose 的 issue body
    from omac.core.taskmeta import TaskKind
    acc_item = _first_item_of_kind(engine, TaskKind.ACCEPTANCE)
    assert acc_item is not None, "应创建 acceptance work item"
    assert "演示计划" in acc_item.description, "acceptance issue body 应含定稿计划"
    dec_item = _first_item_of_kind(engine, TaskKind.DECOMPOSE)
    assert dec_item is not None, "应创建 decompose work item"
    assert "演示计划" in dec_item.description, "decompose issue body 应含定稿计划"
    assert "登录流程" in dec_item.description, "decompose issue body 应含验收文档(flow)"


def test_create_with_goal_injects_requirement_into_planner(tmp_path, monkeypatch):
    """--goal 时:planner 的 PLAN issue body 应含需求(经 source_of_truth 通道)。"""
    from omac.core.taskmeta import TaskKind
    engine = _configure_create_mock(tmp_path, monkeypatch)
    assert main(["plan", "create", "--name", "demo-goal",
                 "--goal", "实现函数 add(a,b) 返回两数之和"]) == exit_codes.OK
    plan_item = _first_item_of_kind(engine, TaskKind.PLAN)
    assert plan_item is not None, "无 --doc 时应创建 plan 阶段 work item"
    assert "实现函数 add" in plan_item.description, "planner issue body 应含需求"


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
    acc_item = _first_item_of_kind(engine, TaskKind.ACCEPTANCE)
    assert acc_item is None, "带 --no-acceptance 时不应创建 acceptance work item"


def test_create_lint_reject_revises_then_passes(tmp_path, monkeypatch):
    """注入一次坏 manifest → lint 机器门回贴修订 → 第二次好 manifest 通过。"""
    _configure_create_mock(tmp_path, monkeypatch)
    MockStore.set_kind_delivery_sequence(
        "decompose",
        [{"manifest": BAD_MANIFEST_LINT}, {"manifest": GOOD_MANIFEST}])
    assert main(["plan", "create", "--name", "demo-lint"]) == exit_codes.OK
    assert (tmp_path / ".omac" / "demo-lint.yaml").exists()

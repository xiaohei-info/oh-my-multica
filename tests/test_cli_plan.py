"""cli.plan / cli.dag 中不依赖平台的命令行为(帮助、参数、dag show、goal/doc 解析);需要真实平台的 check/create/resume/confirm 见 tests/it/test_it_plan_cli.py。"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from omac.cli import exit_codes
from omac.cli.main import main


def test_acceptance_guard_rejects_generic_expected_template():
    from omac.pipeline.plan import _acceptance_guard

    content = yaml.safe_dump({
        "flows": [{
            "id": "flow-generic",
            "name": "通用模板",
            "actions": [{
                "step": f"执行不同业务动作 {i}",
                "how": f"在页面 {i} 执行动作",
                "expected": "证明本 Action step 中的每个子句成立，否则失败。",
            } for i in range(40)],
        }],
    }, allow_unicode=True)

    errors = _acceptance_guard(SimpleNamespace(deliverable=content))

    assert len(errors) == 1
    assert "expected is reused by 40/40 actions" in errors[0]


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

PROJECT_RULES_TEXT = """\
## OMAC 项目级开发规范

- 保持现有登录与权限错误语义向后兼容。
- 新行为必须先写测试，并运行完整测试集。
"""

ACCEPTANCE_YAML = """\
schema: omac.acceptance/v2
flows:
  - id: flow-login
    name: 登录流程
    actions:
      - id: ACT-LOGIN-01
        kind: business-action
        step: 打开登录页
        how: GET /login
        expected: 返回 200 与登录表单
      - id: ACT-LOGIN-02
        kind: business-action
        step: 提交合法凭证
        how: POST /login {user, pwd}
        expected: 跳转到首页
  - id: flow-dashboard
    name: 仪表盘流程
    actions:
      - id: ACT-DASHBOARD-01
        kind: business-action
        step: 访问仪表盘
        how: GET /dash
        expected: 显示数据卡片
"""

GOOD_MANIFEST = """\
meta:
  name: demo-create
nodes:
  - id: login
    worker: alice
    description: Implement the login flow and its observable session behavior.
    contract:
      objective: 实现登录
      acceptance_claims:
        - flow-login
      acceptance_contributions:
        - flow_id: flow-login
          action_ids: [ACT-LOGIN-01, ACT-LOGIN-02]
      acceptance_refs: [flow-login]
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
      scope_paths: [src/login/**]
      evidence_mode: fixture
      produces: [{artifact_id: login-component}]
      consumes: []
  - id: dashboard
    worker: bob
    description: Implement the dashboard flow and its observable data behavior.
    blocked_by: [login]
    contract:
      objective: 实现仪表盘
      acceptance_claims:
        - flow-dashboard
      acceptance_contributions:
        - flow_id: flow-dashboard
          action_ids: [ACT-DASHBOARD-01]
      acceptance_refs: [flow-dashboard]
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
      scope_paths: [src/dashboard/**]
      evidence_mode: fixture
      produces: [{artifact_id: dashboard-component}]
      consumes: []
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




def test_doc_directory_builds_generic_design_source_set(tmp_path, monkeypatch):
    """目录输入只提供完整文件清单和通用阅读要求，不内置项目目录约定。"""
    from omac.pipeline.plan import _read_file

    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    (docs / "archive").mkdir(parents=True)
    (docs / "README.md").write_text("repository-specific index", encoding="utf-8")
    (docs / "overview.md").write_text("CURRENT DESIGN BODY", encoding="utf-8")
    (docs / "archive" / "old.md").write_text("OLD DESIGN BODY", encoding="utf-8")

    source_set = _read_file("docs", language="cn")

    assert "docs/README.md" in source_set
    assert "docs/overview.md" in source_set
    assert "docs/archive/old.md" in source_set
    assert "CURRENT DESIGN BODY" not in source_set
    assert "OLD DESIGN BODY" not in source_set
    assert "递归检查该目录" in source_set
    assert "根据仓库治理规则和文档内容判断" in source_set
    assert "只完成 work show 当前阶段要求的交付" in source_set
    assert "生成验收计划" not in source_set
    assert "完整 DAG" not in source_set
    assert "排除 archive" not in source_set
    assert "README.md 是权威" not in source_set


@pytest.mark.integration  # starts a Python subprocess / runs real git
def test_doc_directory_uses_only_git_tracked_files_for_revision_source_set(
        tmp_path, monkeypatch):
    """Git revision 输入不得把本地未跟踪噪声纳入远端不可复现的文档集合。"""
    from omac.pipeline.plan import _read_file

    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design", encoding="utf-8")
    (docs / ".DS_Store").write_bytes(b"local desktop metadata")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "docs/design.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "authoritative docs"],
        cwd=tmp_path, check=True)

    source_set = _read_file("docs", language="cn")

    assert "docs/design.md" in source_set
    assert ".DS_Store" not in source_set


@pytest.mark.integration  # runs real git
def test_doc_directory_rejects_tracked_symlink(tmp_path, monkeypatch):
    from omac.errors import ValidationError
    from omac.pipeline.plan import _read_file

    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("authoritative design", encoding="utf-8")
    (docs / "alias.md").symlink_to("design.md")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "docs"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "commit", "-qm", "authoritative docs"],
        cwd=tmp_path, check=True)

    with pytest.raises(ValidationError, match="symlink"):
        _read_file("docs", language="cn")









































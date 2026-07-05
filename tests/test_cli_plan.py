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

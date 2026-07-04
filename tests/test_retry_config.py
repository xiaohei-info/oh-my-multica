"""config.retry.{ci|review|merge} 可配性 + 校验出口(AITEAM-354)。

验收:
- init 生成的 config.yaml 含 retry.{ci|review|merge},缺省 3
- omac config get/set retry.review 可读可写
- 负数被校验拒绝(exit 5)
- resolve_retry 合并缺省 + 校验非法值
- dag run loop 与 plan 流水线评审回退受 retry.review 控制(各一条回归)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from omac.cli import exit_codes
from omac.cli.commands import plan as plan_cmd
from omac.cli.main import main
from omac.core import config as config_mod
from omac.errors import ValidationError


# ==================== resolve_retry 合并 + 校验 ====================

def test_resolve_retry_defaults_when_missing():
    assert config_mod.resolve_retry({"engine": "mock"}) == {"ci": 3, "review": 3, "merge": 3}


def test_resolve_retry_merges_partial_override():
    cfg = {"retry": {"review": 5}}
    assert config_mod.resolve_retry(cfg) == {"ci": 3, "review": 5, "merge": 3}


def test_resolve_retry_zero_is_valid():
    cfg = {"retry": {"ci": 0, "review": 0, "merge": 0}}
    assert config_mod.resolve_retry(cfg) == {"ci": 0, "review": 0, "merge": 0}


def test_resolve_retry_rejects_negative():
    with pytest.raises(ValidationError):
        config_mod.resolve_retry({"retry": {"ci": -1}})


def test_resolve_retry_rejects_non_int():
    with pytest.raises(ValidationError):
        config_mod.resolve_retry({"retry": {"review": "abc"}})


def test_resolve_retry_ignores_unknown_subkey():
    cfg = {"retry": {"review": 2, "bogus": 9}}
    assert config_mod.resolve_retry(cfg) == {"ci": 3, "review": 2, "merge": 3}


# ==================== init 生成含 retry 块 ====================

def test_init_writes_retry_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main([
        "init", "--engine", "mock", "--workspace", "mock-workspace",
        "--planner", "alice", "--orchestrator", "bob",
        "--workers", "charlie", "--reviewers", "alice",
    ])
    assert code == exit_codes.OK
    cfg = config_mod.load_config()
    assert cfg["retry"] == {"ci": 3, "review": 3, "merge": 3}


# ==================== config get/set 可读写 retry ====================

def test_config_get_retry_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "retry.review", "5"])
    assert main(["config", "get", "retry.review"]) == exit_codes.OK


def test_config_set_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "retry.ci", "1"])
    capsys.readouterr()
    assert main(["config", "get", "retry.ci"]) == exit_codes.OK
    assert capsys.readouterr().out.strip() == "1"


def test_config_set_negative_retry_rejected(tmp_path, monkeypatch, capsys):
    """负数 retry 被校验期拒绝 → exit 5。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    code = main(["config", "set", "retry.merge", "-2"])
    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "不能为负数" in err or "负" in err


# ==================== plan 流水线共用 retry.review ====================

def test_plan_resolve_review_rounds_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    assert plan_cmd.resolve_review_rounds(config_mod.load_config()) == 3


def test_plan_resolve_review_rounds_configurable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "retry.review", "7"])
    cfg = config_mod.load_config()
    assert plan_cmd.resolve_review_rounds(cfg) == 7


def test_plan_resolve_review_rounds_rejects_negative(tmp_path, monkeypatch):
    """plan 读取的 config 不应含负数(validate 在更早阶段拦截,此处防御性校验)。"""
    with pytest.raises(ValidationError):
        plan_cmd.resolve_review_rounds({"retry": {"review": -1}})

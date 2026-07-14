"""config.retry.{worker|ci|review|merge} 可配性 + 校验出口(AITEAM-354)。

验收:
- init 生成的 config.yaml 含 retry.{worker|ci|review|merge},缺省 3
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
    assert config_mod.resolve_retry({"engine": "mock"}) == {
        "worker": 3, "ci": 3, "review": 3, "merge": 3}


def test_resolve_retry_merges_partial_override():
    cfg = {"retry": {"review": 5}}
    assert config_mod.resolve_retry(cfg) == {
        "worker": 3, "ci": 3, "review": 5, "merge": 3}


def test_resolve_retry_zero_is_valid():
    cfg = {"retry": {"worker": 0, "ci": 0, "review": 0, "merge": 0}}
    assert config_mod.resolve_retry(cfg) == {
        "worker": 0, "ci": 0, "review": 0, "merge": 0}


def test_resolve_retry_rejects_negative():
    with pytest.raises(ValidationError):
        config_mod.resolve_retry({"retry": {"ci": -1}})


def test_resolve_retry_rejects_non_int():
    with pytest.raises(ValidationError):
        config_mod.resolve_retry({"retry": {"review": "abc"}})


def test_resolve_retry_ignores_unknown_subkey():
    cfg = {"retry": {"review": 2, "bogus": 9}}
    assert config_mod.resolve_retry(cfg) == {
        "worker": 3, "ci": 3, "review": 2, "merge": 3}


# ==================== CI 默认检测 ====================

def test_get_ci_config_skips_when_no_explicit_command_and_no_github_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert config_mod.get_ci_config({}) is None
    assert config_mod.get_ci_config({"ci": {"timeout_minutes": 12}}) is None


def test_get_ci_config_defaults_to_gh_checks_when_github_workflow_exists(tmp_path, monkeypatch):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert config_mod.get_ci_config({}) == {
        "check_command": "gh pr checks {pr_url} --watch --fail-fast",
        "timeout_minutes": 30,
    }
    assert config_mod.get_ci_config({"ci": {"timeout_minutes": 9}}) == {
        "check_command": "gh pr checks {pr_url} --watch --fail-fast",
        "timeout_minutes": 9,
    }


def test_get_ci_config_explicit_command_wins_over_github_workflow(tmp_path, monkeypatch):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yaml").write_text("name: ci\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cfg = {"ci": {"check_command": "custom {pr_url}", "timeout_minutes": 5}}

    assert config_mod.get_ci_config(cfg) == cfg["ci"]


# ==================== workflow 默认策略 ====================

def test_resolve_workflow_defaults_when_missing():
    assert config_mod.resolve_workflow({}) == {
        "human_in_loop": True,
        "review": True,
        "acceptance_doc": True,
        "goal_required": False,
    }


def test_resolve_workflow_merges_partial_override():
    cfg = {"workflow": {"human_in_loop": False, "goal_required": True}}
    assert config_mod.resolve_workflow(cfg) == {
        "human_in_loop": False,
        "review": True,
        "acceptance_doc": True,
        "goal_required": True,
    }


def test_resolve_workflow_rejects_non_bool():
    with pytest.raises(ValidationError):
        config_mod.resolve_workflow({"workflow": {"acceptance_doc": "yes"}})


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
    assert cfg["retry"] == {"worker": 3, "ci": 3, "review": 3, "merge": 3}
    # DEFAULT_MAX_ROUNDS 与 acceptance.max_rounds 同源,无重复 authority(Nit 6)
    assert cfg["acceptance"] == {"max_rounds": config_mod.DEFAULT_MAX_ROUNDS}
    assert cfg["acceptance"]["max_rounds"] == 3
    assert cfg["workflow"] == config_mod.DEFAULT_WORKFLOW


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
    assert "cannot be negative" in err


def test_dag_tick_passes_configured_retry_limits(tmp_path, monkeypatch, capsys):
    """dag run/tick 必须把 config.retry 注入主 tick,否则配置写了不生效。"""
    import yaml
    from omac.cli.commands import dag as dag_cmd
    from omac.pipeline.loop import TickResult

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".omac").mkdir()
    with open(tmp_path / ".omac" / "config.yaml", "w") as f:
        yaml.dump({
            "engine": "mock",
            "workspace": "ws",
            "retry": {"worker": 3, "ci": 0, "review": 1, "merge": 2},
        }, f)
    manifest = tmp_path / ".omac" / "m.yaml"
    with open(manifest, "w") as f:
        yaml.dump({
            "meta": {"name": "m"},
            "nodes": [{"id": "a", "worker": "alice", "status": "done"}],
        }, f)

    seen = {}

    def fake_tick(store, runtime, manifest_obj, manifest_path, *,
                  max_parallel=4, retry_limits=None, config=None):
        seen["retry_limits"] = retry_limits
        return TickResult(state="converged", done=["a"])

    monkeypatch.setattr(dag_cmd, "tick", fake_tick)

    assert main(["dag", "tick", str(manifest)]) == exit_codes.OK
    assert seen["retry_limits"] == {"worker": 3, "ci": 0, "review": 1, "merge": 2}


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

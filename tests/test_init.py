"""omac init:非交互直出、交互主路径、--check 引擎校验、引擎发现接口。"""
import builtins
import json
import sys

import pytest

from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.models import EngineConfig, WorkspaceInfo
from omac.engines.codehost import CodeHost
from omac.engines.store import WorkItemStore


# ==================== 引擎发现接口 ====================

def test_list_workspaces_is_part_of_store_interface():
    assert hasattr(WorkItemStore, "list_workspaces")


def test_pr_operations_belong_to_the_code_host_port_not_the_store():
    assert hasattr(CodeHost, "check_pull_request")
    assert hasattr(CodeHost, "read_pull_request_readiness")
    assert not hasattr(WorkItemStore, "check_pull_request")




def test_multica_list_workspaces_parses_json(monkeypatch):
    """平台 CLI 的固定输出:multica workspace list --output json → WorkspaceInfo。"""
    from omac.engines import multica as m

    payload = json.dumps([
        {"id": "ws-a", "name": "Team A", "description": "主", "member_count": 5},
        {"id": "ws-b", "name": "Team B"},
    ])

    class _R:
        returncode = 0
        stdout = payload
        stderr = ""

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        assert cmd[0] == "multica"
        # discovery 期 workspace_id 为空,不应注入 --workspace-id
        assert "--workspace-id" not in cmd
        assert "workspace" in cmd and "list" in cmd
        return _R()

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    store = m.MulticaStore(EngineConfig(engine_type="multica", workspace_id=""))
    infos = store.list_workspaces()
    assert [i.id for i in infos] == ["ws-a", "ws-b"]
    assert infos[0].name == "Team A" and infos[0].member_count == 5


def test_multica_list_workspaces_surfaces_auth_error(monkeypatch):
    from omac.engines import multica as m
    from omac.errors import AuthError

    class _R:
        returncode = 3
        stdout = ""
        stderr = "not authenticated, run multica login"

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: _R())
    store = m.MulticaStore(EngineConfig(engine_type="multica", workspace_id=""))
    with pytest.raises(AuthError):
        store.list_workspaces()


# ==================== 非交互模式 ====================

def test_bare_init_rejects_non_tty_with_config_set_guidance(tmp_path, monkeypatch, capsys):
    class _NonTty:
        def isatty(self):
            return False

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", _NonTty())

    code = main(["init"])

    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "omac init is an interactive setup wizard" in err
    assert "omac config set engine" in err
    assert "omac init --check" in err








# ==================== --check 增强 ====================

def test_check_missing_config_guides_agent_ci_to_config_set(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["init", "--check"]) == exit_codes.VALIDATION

    err = capsys.readouterr().err
    assert "Configuration file not found" in err
    assert "omac config set engine" in err
    assert "omac init --check" in err






# ==================== project 必填(multica)====================

def test_resolve_engine_settings_multica_requires_project():
    """multica 缺 project → ValidationError(不 fallback)。"""
    from omac.core import config as cfg_mod
    from omac.errors import ValidationError

    with pytest.raises(ValidationError):
        cfg_mod.resolve_engine_settings({"engine": "multica", "workspace": "ws"})
    et, ws, pid = cfg_mod.resolve_engine_settings(
        {"engine": "multica", "workspace": "ws", "project": "p1"})
    assert (et, ws, pid) == ("multica", "ws", "p1")


def test_multica_create_work_item_passes_project(monkeypatch):
    """config.project_id 有值时,issue create 命令带 --project <id>。"""
    from omac.engines import multica as m

    seen = {}

    class _R:
        returncode = 0
        stderr = ""
        def __init__(self, out): self.stdout = out

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        if "create" in cmd and "issue" in cmd:
            seen["create_cmd"] = cmd
            return _R(json.dumps({"id": "issue-1"}))
        if "get" in cmd and "issue" in cmd:
            return _R(json.dumps({"id": "issue-1", "title": "[DAG:a] t",
                                  "status": "todo", "metadata": {"dag_key": "a"}}))
        return _R("")  # metadata set 等

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    store = m.MulticaStore(EngineConfig(
        engine_type="multica", workspace_id="ws", project_id="proj-42"))
    store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    assert "--project" in seen["create_cmd"]
    assert "proj-42" in seen["create_cmd"]


def test_multica_create_project_registers_project_and_workspace_repos(monkeypatch):
    """repo 同时挂 project resource 与 workspace registry;workspace 侧跳过已有 URL。"""
    from omac.engines import multica as m
    from omac.pipeline.dispatch import OMAC_PROJECT_DESCRIPTION

    calls = []

    class _R:
        returncode = 0
        stderr = ""
        def __init__(self, out): self.stdout = out

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        calls.append(cmd)
        if "project" in cmd and "create" in cmd:
            return _R(json.dumps({"id": "proj-9", "title": "demo"}))
        if "repo" in cmd and "list" in cmd:
            return _R(json.dumps([{"url": "https://github.com/x/existing.git"}]))
        if "repo" in cmd and "add" in cmd:
            return _R(json.dumps([{"url": "https://github.com/x/new.git"}]))
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    store = m.MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))
    info = store.create_project(
        "ws", "demo",
        ["https://github.com/x/existing.git", "https://github.com/x/new.git"],
        description=OMAC_PROJECT_DESCRIPTION,
    )

    create_cmd = calls[0]
    assert "project" in create_cmd and "create" in create_cmd
    assert "--description" in create_cmd
    assert create_cmd[create_cmd.index("--description") + 1] == OMAC_PROJECT_DESCRIPTION
    assert create_cmd.count("--repo") == 2
    assert "https://github.com/x/existing.git" in create_cmd
    assert "https://github.com/x/new.git" in create_cmd

    add_cmds = [c for c in calls if "repo" in c and "add" in c]
    assert len(add_cmds) == 1
    assert "https://github.com/x/new.git" in add_cmds[0]
    assert "https://github.com/x/existing.git" not in add_cmds[0]
    assert info.repos == ["https://github.com/x/existing.git", "https://github.com/x/new.git"]


def test_project_description_routes_agent_to_json_instance_facts():
    from omac.pipeline.dispatch import OMAC_PROJECT_DESCRIPTION

    assert "omac work show <该 issue id> --output json" in OMAC_PROJECT_DESCRIPTION
    assert "实例事实" in OMAC_PROJECT_DESCRIPTION
    assert "guide_refs" in OMAC_PROJECT_DESCRIPTION
    assert "静态 guide" in OMAC_PROJECT_DESCRIPTION



def test_check_flags_missing_project_for_multica(tmp_path, monkeypatch, capsys):
    """multica 配置缺 project → --check exit 5 并提示。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "multica"])
    main(["config", "set", "workspace", "ws"])
    main(["config", "set", "roles.workers", '["alice"]'])
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.VALIDATION
    assert "project" in capsys.readouterr().err


def test_check_degrades_when_multica_unreachable(tmp_path, monkeypatch, capsys):
    """multica 不在 PATH → 降级本地体检+警告,不崩。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda x: None)
    main(["config", "set", "engine", "multica"])
    main(["config", "set", "workspace", "ws"])
    main(["config", "set", "roles.workers", '["alice"]'])
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "multica CLI is not on PATH" in err


# ==================== 交互模式(monkeypatch stdin)====================

def _answers(seq):
    """把一串回答喂给 input(),逐条弹出。"""
    it = iter(seq)

    def _fake(prompt=""):
        try:
            return next(it)
        except StopIteration:
            return ""
    return _fake


class _Tty:
    def isatty(self):
        return True


def test_engine_prompt_defaults_to_multica(monkeypatch):
    from omac.cli.commands.init_cmd import _select_engine
    from types import SimpleNamespace

    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert _select_engine(SimpleNamespace(engine=None), language="en") == "multica"









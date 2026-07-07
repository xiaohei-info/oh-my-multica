"""omac init:非交互直出、交互主路径、--check 引擎校验、引擎发现接口。"""
import builtins
import json

import pytest

from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines import create_engine
from omac.engines.models import EngineConfig, WorkspaceInfo
from omac.engines.store import WorkItemStore


# ==================== 引擎发现接口 ====================

def test_list_workspaces_is_part_of_store_interface():
    assert hasattr(WorkItemStore, "list_workspaces")


def test_mock_list_workspaces_returns_workspaceinfo():
    store = create_engine("mock", EngineConfig(engine_type="mock", workspace_id="ws")).store
    infos = store.list_workspaces()
    assert infos and isinstance(infos[0], WorkspaceInfo)
    assert any(w.id == "mock-workspace" for w in infos)


def test_multica_list_workspaces_parses_json(monkeypatch):
    """subprocess mock:multica workspace list --output json → WorkspaceInfo。"""
    from omac.engines import multica as m

    payload = json.dumps([
        {"id": "ws-a", "name": "Team A", "description": "主", "member_count": 5},
        {"id": "ws-b", "name": "Team B"},
    ])

    class _R:
        returncode = 0
        stdout = payload
        stderr = ""

    def fake_run(cmd, capture_output=False, text=False):
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

def test_non_interactive_writes_valid_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main([
        "init",
        "--engine", "mock",
        "--workspace", "mock-workspace",
        "--planner", "alice",
        "--orchestrator", "bob",
        "--workers", "charlie",
        "--reviewers", "alice,bob",
    ])
    assert code == exit_codes.OK
    assert (tmp_path / ".omac" / "config.yaml").exists()

    import yaml
    cfg = yaml.safe_load((tmp_path / ".omac" / "config.yaml").read_text())
    assert cfg["engine"] == "mock"
    assert cfg["workspace"] == "mock-workspace"
    assert cfg["roles"]["planner"] == "alice"
    assert cfg["roles"]["orchestrator"] == "bob"
    assert cfg["roles"]["workers"] == ["charlie"]
    assert cfg["roles"]["reviewers"] == ["alice", "bob"]
    assert "acceptor" not in cfg["roles"]  # 可选缺省

    # 紧接着 --check 通过
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.OK


def test_non_interactive_acceptor_optional(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main([
        "init", "--engine", "mock", "--workspace", "mock-workspace",
        "--planner", "alice", "--orchestrator", "bob",
        "--workers", "charlie", "--reviewers", "alice",
        "--acceptor", "bob",
    ])
    assert code == exit_codes.OK
    import yaml
    cfg = yaml.safe_load((tmp_path / ".omac" / "config.yaml").read_text())
    assert cfg["roles"]["acceptor"] == "bob"


def test_non_interactive_role_not_in_pool_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main([
        "init", "--engine", "mock", "--workspace", "mock-workspace",
        "--planner", "ghost",  # 不在 alice/bob/charlie 池
        "--orchestrator", "bob",
        "--workers", "charlie", "--reviewers", "alice",
    ])
    assert code == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "ghost" in err and "agent 池" in err
    assert not (tmp_path / ".omac" / "config.yaml").exists()


# ==================== --check 增强 ====================

def test_check_flags_role_not_in_pool(tmp_path, monkeypatch, capsys):
    """角色不在 agent 池 → exit 5 并指出缺谁(验收标准)。"""
    monkeypatch.chdir(tmp_path)
    # 直接落一份 roles 不合法的配置
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    main(["config", "set", "roles.workers", '["zoe"]'])  # zoe 不在池
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "zoe" in err and "agent 池" in err


def test_check_flags_workspace_not_exist(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "no-such-ws"])
    main(["config", "set", "roles.workers", '["alice"]'])
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.VALIDATION
    assert "no-such-ws" in capsys.readouterr().err


# ==================== project 必填(multica)====================

def test_resolve_engine_settings_multica_requires_project():
    """multica 缺 project → ValidationError(不 fallback);mock 不要求。"""
    from omac.core import config as cfg_mod
    from omac.errors import ValidationError

    with pytest.raises(ValidationError):
        cfg_mod.resolve_engine_settings({"engine": "multica", "workspace": "ws"})
    et, ws, pid = cfg_mod.resolve_engine_settings(
        {"engine": "multica", "workspace": "ws", "project": "p1"})
    assert (et, ws, pid) == ("multica", "ws", "p1")
    et, ws, pid = cfg_mod.resolve_engine_settings({"engine": "mock", "workspace": "ws"})
    assert (et, ws, pid) == ("mock", "ws", None)


def test_multica_create_work_item_passes_project(monkeypatch):
    """config.project_id 有值时,issue create 命令带 --project <id>。"""
    from omac.engines import multica as m

    seen = {}

    class _R:
        returncode = 0
        stderr = ""
        def __init__(self, out): self.stdout = out

    def fake_run(cmd, capture_output=False, text=False):
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

    def fake_run(cmd, capture_output=False, text=False):
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
    assert "multica CLI 不在 PATH" in err


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


def test_interactive_main_path(tmp_path, monkeypatch, capsys):
    """交互式:回车用缺省,主路径生成 config 且 --check 通过。"""
    monkeypatch.chdir(tmp_path)
    # 回答顺序:engine(回车=mock)→ workspace(回车=第一个)→ planner(回车=1)
    # → orchestrator(回车=1)→ workers(回车=1)→ reviewers(回车=1)→ acceptor(回车跳过)
    monkeypatch.setattr(builtins, "input", _answers([
        "",       # engine → mock
        "",       # workspace → 首个(mock-workspace)
        "",       # planner → 序号1=alice
        "",       # orchestrator → 序号1=alice
        "",       # workers → 序号1=alice
        "",       # reviewers → 序号1=alice
        "",       # acceptor → 跳过
    ]))
    assert main(["init"]) == exit_codes.OK
    import yaml
    cfg = yaml.safe_load((tmp_path / ".omac" / "config.yaml").read_text())
    assert cfg["engine"] == "mock"
    assert cfg["roles"]["planner"] == "alice"
    assert cfg["roles"]["workers"] == ["alice"]
    assert "acceptor" not in cfg["roles"]

    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.OK


def test_interactive_type_name(tmp_path, monkeypatch, capsys):
    """交互式输入 agent 名(非序号)也能映射。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "input", _answers([
        "mock",            # engine
        "mock-workspace",  # workspace by id
        "alice",           # planner by name
        "bob",             # orchestrator
        "charlie",         # workers
        "bob,charlie",     # reviewers
        "",                # acceptor skip
    ]))
    assert main(["init"]) == exit_codes.OK
    import yaml
    cfg = yaml.safe_load((tmp_path / ".omac" / "config.yaml").read_text())
    assert cfg["roles"]["reviewers"] == ["bob", "charlie"]


def test_interactive_bad_role_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "input", _answers([
        "mock", "mock-workspace", "ghost",  # planner 不在池
    ]))
    assert main(["init"]) == exit_codes.VALIDATION
    assert "ghost" in capsys.readouterr().err

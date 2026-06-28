"""
engines/models.py + base.py + __init__.py 覆盖率提升测试（Lane 4）
只新建测试，不改源码。
"""
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines import (
    EngineFactory,
    create_engine_from_config,
    create_engine_from_env,
    EngineConfig,
    WorkItem,
    WorkItemStatus,
    CollaborationEngine,
)
from engines.models import WorkspaceInfo
import engines as engines_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_item(status):
    return WorkItem(
        id="item-1",
        workspace_id="ws",
        title="t",
        description="d",
        status=status,
        dag_key="k",
    )


class _ConcreteEngine(CollaborationEngine):
    """最小可实例化子类，记录 update_status 调用以便断言便捷方法。"""

    def __init__(self, config):
        super().__init__(config)
        self.status_calls = []
        self._members = ["alice"]

    def get_required_env_vars(self):
        return []

    def list_members(self, workspace_id):
        return list(self._members)

    def create_work_item(self, **kw):
        return _make_item(WorkItemStatus.TODO)

    def get_work_item(self, item_id):
        return _make_item(WorkItemStatus.TODO)

    def update_work_item_metadata(self, **kw):
        return _make_item(WorkItemStatus.TODO)

    def list_work_items(self, **kw):
        return []

    def add_comment(self, item_id, comment):
        pass

    def update_status(self, item_id, status):
        self.status_calls.append((item_id, status))

    def assign_work_item(self, item_id, assignee, role):
        pass


# ---------------------------------------------------------------------------
# models.py: WorkItem 便捷方法
# ---------------------------------------------------------------------------


class TestWorkItemStatusChecks:
    def test_is_completed_true(self):
        assert _make_item(WorkItemStatus.DONE).is_completed() is True

    def test_is_completed_false(self):
        assert _make_item(WorkItemStatus.IN_PROGRESS).is_completed() is False

    def test_is_in_progress_true(self):
        assert _make_item(WorkItemStatus.IN_PROGRESS).is_in_progress() is True

    def test_is_in_progress_false(self):
        assert _make_item(WorkItemStatus.TODO).is_in_progress() is False

    def test_is_failed_true(self):
        assert _make_item(WorkItemStatus.FAILED).is_failed() is True

    def test_is_failed_false(self):
        assert _make_item(WorkItemStatus.DONE).is_failed() is False

    def test_is_blocked_true(self):
        assert _make_item(WorkItemStatus.BLOCKED).is_blocked() is True

    def test_is_blocked_false(self):
        assert _make_item(WorkItemStatus.TODO).is_blocked() is False


# ---------------------------------------------------------------------------
# models.py: EngineConfig.from_env 分支
# ---------------------------------------------------------------------------


class TestEngineConfigFromEnv:
    def test_multica_workspace_id(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "multica", "MULTICA_WORKSPACE_ID": "ws-m"})
        assert cfg.engine_type == "multica"
        assert cfg.workspace_id == "ws-m"
        assert cfg.squad_id is None

    def test_multica_default_workspace_empty(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "multica"})
        assert cfg.workspace_id == ""

    def test_github_workspace_id(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "github", "GITHUB_REPO": "owner/repo"})
        assert cfg.workspace_id == "owner/repo"

    def test_github_default_workspace_empty(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "github"})
        assert cfg.workspace_id == ""

    def test_mock_workspace_id(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "mock", "MOCK_WORKSPACE_ID": "mock-ws"})
        assert cfg.workspace_id == "mock-ws"

    def test_mock_default_workspace(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "mock"})
        assert cfg.workspace_id == "mock-workspace"

    def test_unknown_engine_type(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "zzz"})
        assert cfg.workspace_id == ""

    def test_polling_interval_custom(self):
        cfg = EngineConfig.from_env(
            {"ENGINE_TYPE": "mock", "POLLING_INTERVAL": "60",
             "POLLING_INTERVAL_MIN": "15", "POLLING_INTERVAL_MAX": "400"}
        )
        assert cfg.polling_interval == 60
        assert cfg.polling_interval_min == 15
        assert cfg.polling_interval_max == 400

    def test_polling_interval_defaults(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "mock"})
        assert cfg.polling_interval == 30
        assert cfg.polling_interval_min == 10
        assert cfg.polling_interval_max == 300

    def test_squad_id_set(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "multica", "MULTICA_SQUAD_ID": "squad-9"})
        assert cfg.squad_id == "squad-9"

    def test_squad_id_absent_is_none(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "multica"})
        assert cfg.squad_id is None

    def test_extra_captures_env(self):
        cfg = EngineConfig.from_env({"ENGINE_TYPE": "mock", "FOO": "bar"})
        assert cfg.extra["FOO"] == "bar"


# ---------------------------------------------------------------------------
# models.py: WorkspaceInfo
# ---------------------------------------------------------------------------


class TestWorkspaceInfo:
    def test_defaults(self):
        info = WorkspaceInfo(id="w", name="n")
        assert info.description is None
        assert info.member_count == 0


# ---------------------------------------------------------------------------
# base.py: classmethod
# ---------------------------------------------------------------------------


class TestEngineClassmethods:
    def test_get_common_env_vars(self):
        names = [v["name"] for v in _ConcreteEngine.get_common_env_vars()]
        assert "ENGINE_TYPE" in names
        assert "POLLING_INTERVAL" in names

    def test_get_recommended_polling_interval_default(self):
        assert _ConcreteEngine.get_recommended_polling_interval() == 30

    def test_get_rate_limit_info(self):
        info = _ConcreteEngine.get_rate_limit_info()
        assert "requests_per_hour" in info
        assert "requests_per_minute" in info


# ---------------------------------------------------------------------------
# base.py: 便捷方法（通过 _ConcreteEngine）
# ---------------------------------------------------------------------------


class TestEngineConvenienceMethods:
    def test_check_member_exists_true(self):
        eng = _ConcreteEngine(EngineConfig(engine_type="mock", workspace_id="ws"))
        assert eng.check_member_exists("ws", "alice") is True

    def test_check_member_exists_false(self):
        eng = _ConcreteEngine(EngineConfig(engine_type="mock", workspace_id="ws"))
        assert eng.check_member_exists("ws", "bob") is False

    @pytest.mark.parametrize("method,expected", [
        ("mark_in_progress", WorkItemStatus.IN_PROGRESS),
        ("mark_done", WorkItemStatus.DONE),
        ("mark_failed", WorkItemStatus.FAILED),
        ("mark_blocked", WorkItemStatus.BLOCKED),
        ("mark_in_review", WorkItemStatus.IN_REVIEW),
    ])
    def test_mark_methods_delegate_to_update_status(self, method, expected):
        eng = _ConcreteEngine(EngineConfig(engine_type="mock", workspace_id="ws"))
        getattr(eng, method)("item-7")
        assert eng.status_calls == [("item-7", expected)]


# ---------------------------------------------------------------------------
# __init__.py: EngineFactory
# ---------------------------------------------------------------------------


class TestEngineFactory:
    def test_list_available_engines(self):
        avail = EngineFactory.list_available_engines()
        assert "multica" in avail
        assert "github" in avail
        assert "mock" in avail

    def test_create_unknown_raises_valueerror(self):
        cfg = EngineConfig(engine_type="unknown", workspace_id="ws")
        with pytest.raises(ValueError, match="未知的引擎类型"):
            EngineFactory.create(cfg)

    def test_get_engine_class_known(self):
        from engines.multica import MulticaEngine
        assert EngineFactory.get_engine_class("multica") is MulticaEngine

    def test_get_engine_class_nonexistent_returns_none(self):
        assert EngineFactory.get_engine_class("nonexistent") is None

    def test_get_engine_class_case_insensitive(self):
        assert EngineFactory.get_engine_class("MOCK") is not None


# ---------------------------------------------------------------------------
# __init__.py: create_engine_from_config
# ---------------------------------------------------------------------------


class TestCreateEngineFromConfig:
    def test_mock_engine(self):
        eng = create_engine_from_config("mock", "mock-ws")
        assert eng.config.engine_type == "mock"
        assert eng.config.workspace_id == "mock-ws"

    def test_multica_engine_config_assembled(self):
        eng = create_engine_from_config("multica", "ws-m")
        assert eng.config.engine_type == "multica"
        assert eng.config.workspace_id == "ws-m"


# ---------------------------------------------------------------------------
# __init__.py: create_engine_from_env
# ---------------------------------------------------------------------------


class TestCreateEngineFromEnv:
    _KEYS = ("ENGINE_TYPE", "MULTICA_WORKSPACE_ID", "GITHUB_REPO",
             "MOCK_WORKSPACE_ID", "ORCH_GIT_SYNC")

    def test_env_path_optional_missing_falls_back(self, tmp_path, monkeypatch):
        """.env 可选：不存在不再报错；无任何配置时回退默认引擎且 workspace 空 -> 报缺 workspace。"""
        for k in self._KEYS:
            monkeypatch.delenv(k, raising=False)
        missing = tmp_path / "no-such.env"
        with pytest.raises(RuntimeError, match=r"缺少 workspace"):
            create_engine_from_env(missing)

    def test_no_dotenv_with_engine_override_ok(self, tmp_path, monkeypatch):
        """无 .env，但命令行 --engine mock 覆盖 -> mock 有默认 workspace，正常建。"""
        for k in self._KEYS:
            monkeypatch.delenv(k, raising=False)
        eng = create_engine_from_env(tmp_path / "none.env", engine_type="mock")
        assert eng.config.engine_type == "mock"

    def test_cli_workspace_overrides_dotenv(self, tmp_path, monkeypatch):
        """命令行 --workspace 覆盖 .env 里的 workspace。"""
        for k in self._KEYS:
            monkeypatch.delenv(k, raising=False)
        env = tmp_path / ".env"
        env.write_text("ENGINE_TYPE=multica\nMULTICA_WORKSPACE_ID=from-file\n")
        eng = create_engine_from_env(env, workspace_id="from-cli")
        assert eng.config.workspace_id == "from-cli"

    def test_export_overrides_dotenv(self, tmp_path, monkeypatch):
        """进程环境(export) 优先于 .env 文件。"""
        for k in self._KEYS:
            monkeypatch.delenv(k, raising=False)
        env = tmp_path / ".env"
        env.write_text("ENGINE_TYPE=mock\nMOCK_WORKSPACE_ID=from-file\n")
        monkeypatch.setenv("MOCK_WORKSPACE_ID", "from-export")
        eng = create_engine_from_env(env)
        assert eng.config.workspace_id == "from-export"

    def test_parses_mock_env(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(textwrap.dedent("""\
            # comment line
            ENGINE_TYPE=mock
            MOCK_WORKSPACE_ID=from-env-ws
            POLLING_INTERVAL=45
        """))
        eng = create_engine_from_env(env)
        assert eng.config.engine_type == "mock"
        assert eng.config.workspace_id == "from-env-ws"
        assert eng.config.polling_interval == 45

    def test_strips_quotes_and_skips_blank_lines(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(textwrap.dedent('''\

            ENGINE_TYPE="github"
            GITHUB_REPO='owner/repo'
        '''))
        eng = create_engine_from_env(env)
        assert eng.config.engine_type == "github"
        assert eng.config.workspace_id == "owner/repo"


# ---------------------------------------------------------------------------
# __init__.py: __getattr__ 延迟导入
# ---------------------------------------------------------------------------


class TestModuleGetattr:
    def test_multica_engine_lazy(self):
        from engines.multica import MulticaEngine
        assert engines_mod.MulticaEngine is MulticaEngine

    def test_github_engine_lazy(self):
        from engines.github import GithubEngine
        assert engines_mod.GithubEngine is GithubEngine

    def test_mock_engine_lazy(self):
        from engines.mock import MockEngine
        assert engines_mod.MockEngine is MockEngine

    def test_nonexistent_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="has no attribute"):
            engines_mod.NonExistentThing

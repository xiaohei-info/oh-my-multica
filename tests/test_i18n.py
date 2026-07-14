"""语言配置与运行时文案的回归覆盖。"""
import re

import pytest

from omac.cli import exit_codes
from omac.cli.main import main
from omac.errors import ValidationError
from omac.i18n import resolve_language, t, ui


def test_language_defaults_to_english_when_missing():
    assert resolve_language({}) == "en"
    assert t("config.language.prompt", language="en") == "Language (en/cn)"


def test_language_uses_chinese_when_configured():
    assert resolve_language({"language": "cn"}) == "cn"
    assert t("config.language.prompt", language="cn") == "语言（en/cn）"


def test_ui_selects_complete_user_visible_copy():
    assert ui("Ready", "就绪", language="en") == "Ready"
    assert ui("Ready", "就绪", language="cn") == "就绪"


def test_language_rejects_unknown_value():
    with pytest.raises(ValidationError, match="language"):
        resolve_language({"language": "jp"})


def test_config_set_language_validates_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert main(["config", "set", "language", "cn"]) == exit_codes.OK
    assert main(["config", "set", "language", "jp"]) == exit_codes.VALIDATION


def test_default_english_runtime_messages_have_no_chinese(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)

    assert main(["config", "set", "defaults.max_parallel", "4"]) == exit_codes.OK
    assert main(["web", "--host", "0.0.0.0"]) == exit_codes.VALIDATION

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert re.search(r"[\u4e00-\u9fff]", rendered) is None


def test_non_interactive_init_persists_english_language(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert main([
        "init",
        "--engine", "mock",
        "--workspace", "mock-workspace",
        "--planner", "alice",
        "--orchestrator", "bob",
        "--workers", "charlie",
        "--reviewers", "alice",
    ]) == exit_codes.OK

    from omac.core import config as config_mod
    assert config_mod.load_config()["language"] == "en"


def test_non_interactive_init_default_output_is_english(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)

    assert main([
        "init",
        "--engine", "mock",
        "--workspace", "mock-workspace",
        "--planner", "alice",
        "--orchestrator", "bob",
        "--workers", "charlie",
        "--reviewers", "alice",
    ]) == exit_codes.OK

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert "Configuration written" in rendered
    assert re.search(r"[\u4e00-\u9fff]", rendered) is None


def test_init_engine_prompt_uses_selected_language(monkeypatch):
    import builtins
    from types import SimpleNamespace

    from omac.cli.commands import init_cmd

    prompts = []
    monkeypatch.setattr(builtins, "input", lambda prompt: prompts.append(prompt) or "")

    assert init_cmd._select_engine(SimpleNamespace(engine=None), language="en") == "mock"
    assert prompts == ["Choose engine [mock]: "]


def test_root_help_defaults_to_english(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main([]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "Deterministic CLI orchestration" in out
    assert "确定性 CLI" not in out


def test_init_help_defaults_to_english(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        main(["init", "--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Check configuration without writing files" in out
    assert "体检模式" not in out


@pytest.mark.parametrize("command", [
    "plan", "dag", "node", "work", "init", "config", "guide", "web",
])
def test_command_help_has_no_chinese_when_language_is_english(
    command, tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])

    assert exc.value.code == 0
    assert re.search(r"[\u4e00-\u9fff]", capsys.readouterr().out) is None

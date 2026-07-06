"""cli:退出码契约、help、guide、config get/set、stub 行为。"""
import os

import pytest

from omac.cli import exit_codes
from omac.cli.main import main


def test_no_args_prints_help_exit_ok(capsys):
    assert main([]) == exit_codes.OK
    out = capsys.readouterr().out
    for group in ("CORE", "WORK", "SETUP", "GUIDE", "WEB"):
        assert group in out


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "omac" in capsys.readouterr().out


def test_unknown_command_teaches(capsys):
    with pytest.raises(SystemExit) as e:
        main(["nope"])
    assert e.value.code == exit_codes.GENERIC
    err = capsys.readouterr().err
    assert "Error" in err and "<command>" in err  # 报错即教学:带 usage/help


def test_stub_commands_exit_generic(capsys):
    # dag run 已在 P1 实现(omac dag run),不再是 stub
    # `omac web` 在 P5.1 实现,不再是 stub(默认启动本地服务)。
    # plan create 已在 P3.3 实现;无配置时走 engine 校验门 exit 5。
    assert main(["plan", "create", "--name", "x"]) == exit_codes.VALIDATION


def test_web_nonlocal_without_token_exits_validation(capsys):
    # P5.1:对外暴露无 token → exit 5(校验失败,cli.main 统一捕获)。
    assert main(["web", "--host", "0.0.0.0"]) == exit_codes.VALIDATION
    assert "token" in capsys.readouterr().err


def test_guide_lists_and_reads_topics(capsys):
    assert main(["guide"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "workflow" in out and "recovery" in out

    assert main(["guide", "workflow"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "omac init" in out and "dag run" in out


def test_config_set_get_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["config", "set", "engine", "mock"]) == exit_codes.OK
    assert main(["config", "set", "defaults.max_parallel", "8"]) == exit_codes.OK
    capsys.readouterr()

    assert main(["config", "get", "defaults.max_parallel"]) == exit_codes.OK
    assert capsys.readouterr().out.strip() == "8"

    assert main(["config", "get", "nope.key"]) == exit_codes.VALIDATION
    assert os.path.exists(".omac/config.yaml")


def test_config_get_without_file_is_validation_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["config", "get"]) == exit_codes.VALIDATION
    assert "omac init" in capsys.readouterr().err


def test_init_check_reports_problems(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--check"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "config.yaml" in err

    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    main(["config", "set", "roles.workers", '["alice"]'])
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.OK
    assert "体检通过" in capsys.readouterr().out

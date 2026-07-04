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
    assert main(["dag", "run", "m.yaml"]) == exit_codes.GENERIC
    assert "P1" in capsys.readouterr().err
    assert main(["plan", "create", "--name", "x"]) == exit_codes.GENERIC
    assert "P3" in capsys.readouterr().err
    assert main(["web"]) == exit_codes.GENERIC
    assert "P5" in capsys.readouterr().err


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
    assert os.path.exists(".orchestrator/config.yaml")


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

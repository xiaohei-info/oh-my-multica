"""cli:退出码契约、help、guide、config get/set、stub 行为。"""
import json
import inspect
import os

import pytest

from omac.cli import exit_codes
from omac.cli.main import build_parser, main


def test_no_args_prints_help_exit_ok(capsys):
    assert main([]) == exit_codes.OK
    out = capsys.readouterr().out
    for group in ("CORE", "WORK", "SETUP", "GUIDE", "WEB"):
        assert group in out


def test_parser_internal_hook_accepts_python_patch_version_arguments():
    parser = build_parser()
    parameters = inspect.signature(parser._parse_known_args).parameters.values()
    assert any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in parameters)


def test_work_and_guide_help_have_explicit_agent_audience(capsys):
    with pytest.raises(SystemExit) as work_exit:
        main(["work", "--help"])
    assert work_exit.value.code == 0
    work_help = capsys.readouterr().out
    assert "Agent" in work_help
    assert "默认 JSON" in work_help
    assert build_parser().parse_args(["work", "show", "issue-1"]).output == "json"

    with pytest.raises(SystemExit) as guide_exit:
        main(["guide", "--help"])
    assert guide_exit.value.code == 0
    guide_help = capsys.readouterr().out
    assert "Agent" in guide_help
    assert "实例事实" in guide_help
    assert "omac work show" in guide_help


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


@pytest.mark.parametrize(
    "argv, expected_message",
    [
        (
            ["work", "submit", "issue-1", "--verdict", "not-a-verdict"],
            "invalid choice",
        ),
        (
            ["work", "submit", "issue-1", "--unknown-option"],
            "unrecognized arguments",
        ),
    ],
)
def test_work_parse_errors_default_to_agent_json(capsys, argv, expected_message):
    with pytest.raises(SystemExit) as exc:
        main(argv)

    assert exc.value.code == exit_codes.GENERIC
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert payload["action"] == "submit"
    assert payload["issue_id"] == "issue-1"
    assert payload["error"]["type"] == "ArgumentError"
    assert payload["error"]["exit_code"] == exit_codes.GENERIC
    assert expected_message in payload["error"]["message"]
    assert "usage: omac work submit" in payload["help"]


@pytest.mark.parametrize(
    "invalid_args, expected_message",
    [
        (["--verdict", "not-a-verdict"], "invalid choice"),
        (["--unknown-option"], "unrecognized arguments"),
    ],
)
def test_work_parse_error_table_mode_keeps_human_help(
    capsys, invalid_args, expected_message
):
    with pytest.raises(SystemExit) as exc:
        main(["work", "submit", "issue-1", "--output", "table", *invalid_args])

    assert exc.value.code == exit_codes.GENERIC
    err = capsys.readouterr().err
    assert err.startswith("Error:")
    assert expected_message in err
    assert "usage: omac work submit" in err
    assert "CORE COMMANDS" not in err
    assert "--verdict" in err and "--report-file" in err


@pytest.mark.parametrize("value", ["work", "show", "submit"])
def test_global_parse_error_does_not_route_argument_value_to_work(capsys, value):
    with pytest.raises(SystemExit) as exc:
        main(["--log-format", value])

    assert exc.value.code == exit_codes.GENERIC
    err = capsys.readouterr().err
    assert err.startswith("Error: argument --log-format: invalid choice")
    assert not err.lstrip().startswith("{")
    assert "usage: omac " in err


@pytest.mark.parametrize("later_token", ["show", "submit"])
def test_invalid_work_action_does_not_route_later_token_as_action(
    capsys, later_token
):
    with pytest.raises(SystemExit) as exc:
        main(["work", "nope", later_token])

    assert exc.value.code == exit_codes.GENERIC
    payload = json.loads(capsys.readouterr().err)
    assert payload["action"] is None
    assert payload["issue_id"] is None
    assert "usage: omac work " in payload["help"]
    assert "usage: omac work show" not in payload["help"]


@pytest.mark.parametrize(
    "outputs, expect_json",
    [
        (["--output", "table", "--output", "json"], True),
        (["--output", "json", "--output", "table"], False),
    ],
)
def test_work_parse_error_uses_last_output_value(capsys, outputs, expect_json):
    with pytest.raises(SystemExit) as exc:
        main(["work", "submit", "issue-1", *outputs, "--unknown-option"])

    assert exc.value.code == exit_codes.GENERIC
    err = capsys.readouterr().err
    if expect_json:
        assert json.loads(err)["error"]["type"] == "ArgumentError"
    else:
        assert err.startswith("Error: unrecognized arguments")
        assert "usage: omac work submit" in err


def test_work_show_unknown_argument_uses_show_json_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["work", "show", "issue-1", "--unknown-option", "--output", "json"])

    assert exc.value.code == exit_codes.GENERIC
    payload = json.loads(capsys.readouterr().err)
    assert payload["action"] == "show"
    assert payload["issue_id"] == "issue-1"
    assert "usage: omac work show" in payload["help"]


def test_work_parse_error_uses_parsed_issue_id_when_options_come_first(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["work", "show", "--output", "json", "issue-1", "--bad"])

    assert exc.value.code == exit_codes.GENERIC
    payload = json.loads(capsys.readouterr().err)
    assert payload["action"] == "show"
    assert payload["issue_id"] == "issue-1"


def test_work_parse_error_ignores_output_tokens_after_double_dash(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["work", "show", "issue-1", "--", "--output", "table"])

    assert exc.value.code == exit_codes.GENERIC
    payload = json.loads(capsys.readouterr().err)
    assert payload["action"] == "show"
    assert payload["issue_id"] == "issue-1"


def test_work_parse_error_stops_at_first_invalid_output_value(capsys):
    with pytest.raises(SystemExit) as exc:
        main([
            "work", "show", "issue-1",
            "--output", "xml", "--output", "table",
        ])

    assert exc.value.code == exit_codes.GENERIC
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["type"] == "ArgumentError"
    assert "invalid choice: 'xml'" in payload["error"]["message"]


def test_work_parse_error_supports_equals_output_syntax(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["work", "show", "issue-1", "--output=table", "--bad"])

    assert exc.value.code == exit_codes.GENERIC
    err = capsys.readouterr().err
    assert err.startswith("Error: unrecognized arguments")
    assert "usage: omac work show" in err


def test_dag_accepts_trailing_log_flags(capsys):
    # 文档承诺的用户路径:日志 flag 放在 dag 子命令参数之后也应被接受。
    assert main(["dag", "run", "nope.yaml", "--json-logs"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "manifest 文件不存在" in err
    assert "unrecognized arguments" not in err

    assert main(["dag", "run", "nope.yaml", "--log-format", "json"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "manifest 文件不存在" in err
    assert "unrecognized arguments" not in err


def test_stub_commands_exit_generic(capsys):
    # dag run 已在 P1 实现(omac dag run),不再是 stub
    # `omac web` 在 P5.1 实现,不再是 stub(默认启动本地服务)。
    # plan create 已在 P3.3 实现;无配置时走 engine 校验门 exit 5。
    assert main(["plan", "create", "--name", "x"]) == exit_codes.VALIDATION


def test_web_nonlocal_without_token_exits_validation(capsys):
    # P5.1:对外暴露无 token → exit 5(校验失败,cli.main 统一捕获)。
    assert main(["web", "--host", "0.0.0.0"]) == exit_codes.VALIDATION
    assert "token" in capsys.readouterr().err


def test_guide_lists_grouped_topics(capsys):
    assert main(["guide"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "Agent" in out
    assert "omac work show" in out
    assert "实例事实" in out
    assert "omac guide workflow" in out
    assert "omac guide role planner" in out
    assert "omac guide artifact design" in out
    assert "omac guide worker" not in out

    assert main(["guide", "workflow"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "omac init" in out and "dag run" in out


def test_guide_reads_role_and_artifact_topics(capsys):
    assert main(["guide", "role", "planner"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "planner" in out
    assert "设计方案" in out
    assert "验收文档" in out

    assert main(["guide", "artifact", "design"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "schema: omac.design/v1" in out
    assert "Markdown" in out


def test_orchestrator_guide_requires_max_parallel_minimal_pr_units(capsys):
    assert main(["guide", "role", "orchestrator"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "最大化并行开发" in out
    assert "最小独立 PR 单元" in out
    assert "独立开发、独立验证、独立提交 PR、独立 review" in out
    assert "还能拆出另一个独立 PR/test/review" in out
    assert "主要代码归属范围" in out
    assert "必要配套文件" in out

    assert main(["guide", "artifact", "manifest"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "每个节点是最小独立 PR/test/review 单元" in out
    assert "不能继续独立拆分" in out
    assert "不是穷举文件白名单" in out

    assert main(["guide", "role", "reviewer"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "decompose review" in out
    assert "还能拆出独立 PR/test/review 单元" in out
    assert "不因必要配套文件" in out


def test_guide_rejects_old_flat_role_topics(capsys):
    assert main(["guide", "worker"]) == exit_codes.GENERIC
    err = capsys.readouterr().err
    assert "未知 guide topic" in err
    assert "omac guide role worker" in err


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

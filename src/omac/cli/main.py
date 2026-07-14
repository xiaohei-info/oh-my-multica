"""omac CLI 入口 — 名词-动词命令树,gh 风格分组 help,稳定退出码。

纪律(设计文档 §2):
- 报错即教学:参数错误时打印错误 + 该命令完整 help;
- 业务层 raise OmacError 子类,这里统一转退出码,不散落 sys.exit。
"""
from __future__ import annotations

import argparse
import re
import sys

from .. import __version__
from ..core import config as config_mod
from ..core.logsetup import configure_logging, resolve_log_format
from ..errors import NeedsDecision, OmacError
from ..i18n import resolve_language, t, ui
from . import exit_codes
from .commands import COMMAND_GROUPS

PROG = "omac"

_EN_SUBCOMMAND_HELP = {
    "create": "Start the design, acceptance, and DAG decomposition pipeline",
    "confirm": "Approve a pending design or acceptance gate",
    "resume": "Resume an existing plan pipeline by its stable ID",
    "check": "Validate an existing artifact",
    "show": "Show current facts without advancing state",
    "run": "Run in the foreground until convergence or exit 20",
    "status": "Show a snapshot without advancing state",
    "tick": "Advance one round and return exit 0, 10, or 20",
    "retry": "Reset a node to todo, optionally with another worker",
    "accept": "Accept a known risk and mark the node done",
    "abandon": "Abandon a node and release non-hard-dependent work",
    "submit": "Validate and submit one structured deliverable",
    "get": "Read the full configuration or one key",
    "set": "Write one configuration key",
}

_EN_ARGUMENT_HELP = {
    "log_format": "Progress-event format: text for humans, json for machines and CI",
    "manifest": "Manifest file path",
    "node_key": "Manifest node ID",
    "engine": "Engine override; otherwise read project configuration",
    "workspace": "Workspace override; otherwise read project configuration",
    "project": "Project ID",
    "worker": "Replacement worker agent",
    "name": "Stable plan or manifest name",
    "goal": "Request text used by the planner when --doc is absent",
    "goal_file": "Request document path; mutually exclusive with --goal",
    "doc": "Existing design document path; skips planner authoring",
    "no_review": "Skip review for this invocation",
    "no_acceptance": "Skip acceptance-document generation for this invocation",
    "no_confirm": "Skip the human confirmation gate for this invocation",
    "dag_key": "Exact stage DAG key",
    "plan_id": "Stable plan pipeline ID",
    "max_parallel": "Maximum parallel tasks for this run",
    "max_rounds": "Stop after this many rounds",
    "max_minutes": "Stop after this many minutes",
    "port": "Dashboard port",
    "host": "Dashboard bind address",
    "open": "Open the dashboard in a browser after startup",
    "refresh": "Browser polling interval in seconds",
    "token": "Bearer token required when binding beyond localhost",
    "issue_id": "Platform issue or work-item ID",
    "topic": "Guide topic",
    "key": "Dotted configuration key",
    "value": "Configuration value parsed as YAML",
    "output": "Output format",
}


def _has_han(text) -> bool:
    return isinstance(text, str) and re.search(r"[\u4e00-\u9fff]", text) is not None


def _localize_parser_help(parser: argparse.ArgumentParser, language: str) -> None:
    """Replace remaining argparse help strings after command registration."""
    if language != "en":
        return
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                choice.help = _EN_SUBCOMMAND_HELP.get(
                    choice.dest, choice.dest.replace("_", " ").capitalize())
            for name, child in action.choices.items():
                if _has_han(child.description):
                    child.description = _EN_SUBCOMMAND_HELP.get(name, name.capitalize())
                _localize_parser_help(child, language)
            continue
        if _has_han(action.help):
            action.help = _EN_ARGUMENT_HELP.get(
                action.dest, action.dest.replace("_", " ").capitalize())


class _HelpOnErrorParser(argparse.ArgumentParser):
    """参数错误时打印错误 + 完整 help(报错即教学),exit 1。"""

    _active_parser = None
    _active_namespace = None

    def parse_args(self, args=None, namespace=None):
        invocation = list(sys.argv[1:] if args is None else args)
        type(self)._active_parser = self
        type(self)._active_namespace = namespace
        return super().parse_args(invocation, namespace)

    def _parse_known_args(self, arg_strings, namespace, *extra):
        """记录 argparse 的实际 parser 与已解析 Namespace,不重复解释 argv。"""
        type(self)._active_parser = self
        type(self)._active_namespace = namespace
        return super()._parse_known_args(arg_strings, namespace, *extra)

    def error(self, message):
        target = type(self)._active_parser or self
        renderer = getattr(target, "_parse_error_renderer", None)
        if renderer is not None and renderer(
            target, message, type(self)._active_namespace
        ):
            raise SystemExit(exit_codes.GENERIC)
        print(f"Error: {message}\n", file=sys.stderr)
        (target if renderer is not None else self).print_help(sys.stderr)
        raise SystemExit(exit_codes.GENERIC)


def build_parser() -> argparse.ArgumentParser:
    language = resolve_language(config_mod.load_config())
    group_keys = (
        "cli.group.core", "cli.group.work", "cli.group.setup",
        "cli.group.guide", "cli.group.web",
    )
    epilog_lines = []
    for group_key, (_, commands) in zip(group_keys, COMMAND_GROUPS):
        epilog_lines.append(t(group_key, language=language))
        for mod in commands:
            epilog_lines.append(
                f"  {mod.NAME:<9}{t(f'cli.command.{mod.NAME}', language=language)}")
    parser = _HelpOnErrorParser(
        prog=PROG,
        description=t("cli.root.description", language=language),
        epilog="\n".join(epilog_lines),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    # 进度事件流(走 stderr,不污染 stdout 数据线):默认人类文本,机器/CI 用 json。
    # 优先级:--log-format > --json-logs > OMAC_LOG_FORMAT 环境变量 > 默认 text。
    parser.add_argument(
        "--log-format", choices=("text", "json"), default=None,
        help="进度事件格式:text 给人看(默认)/ json 给机器/CI 解析(等价 OMAC_LOG_FORMAT)")
    parser.add_argument(
        "--json-logs", dest="log_format", action="store_const", const="json",
        help="--log-format json 的简写:进度事件出 JSON-lines(stderr)")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for _, commands in COMMAND_GROUPS:
        for mod in commands:
            sub = subparsers.add_parser(
                mod.NAME,
                help=t(f"cli.command.{mod.NAME}", language=language),
                description=t(f"cli.description.{mod.NAME}", language=language),
                formatter_class=argparse.RawDescriptionHelpFormatter)
            mod.register(sub)
            sub.set_defaults(_run=mod.run, _parser=sub)
    _localize_parser_help(parser, language)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 进度事件流一次性配置(事件走 stderr);须在任何命令产出事件前完成。
    configure_logging(resolve_log_format(getattr(args, "log_format", None)))

    if not getattr(args, "command", None):
        parser.print_help()
        return exit_codes.OK

    try:
        code = args._run(args)
        return exit_codes.OK if code is None else code
    except NeedsDecision as e:
        # 结构化报告已由命令层输出到 stdout;这里补一句 stderr 引导。
        print(ui(
            f"Caller decision required: {e}",
            f"需要调用者决策: {e}"), file=sys.stderr)
        return e.exit_code
    except OmacError as e:
        print(f"Error: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print(ui(
            "Interrupted. State remains in the manifest and platform; rerun to resume.",
            "已中断(状态在 manifest + 平台,重跑即续跑)"), file=sys.stderr)
        return exit_codes.GENERIC


def entry():  # pragma: no cover - console_script 包装
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

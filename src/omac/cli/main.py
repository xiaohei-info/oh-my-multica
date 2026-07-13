"""omac CLI 入口 — 名词-动词命令树,gh 风格分组 help,稳定退出码。

纪律(设计文档 §2):
- 报错即教学:参数错误时打印错误 + 该命令完整 help;
- 业务层 raise OmacError 子类,这里统一转退出码,不散落 sys.exit。
"""
from __future__ import annotations

import argparse
import sys

from .. import __version__
from ..core.logsetup import configure_logging, resolve_log_format
from ..errors import NeedsDecision, OmacError
from . import exit_codes
from .commands import COMMAND_GROUPS

PROG = "omac"


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
    epilog_lines = []
    for group_title, commands in COMMAND_GROUPS:
        epilog_lines.append(f"{group_title}")
        for mod in commands:
            epilog_lines.append(f"  {mod.NAME:<9}{mod.SUMMARY}")
    parser = _HelpOnErrorParser(
        prog=PROG,
        description="oh-my-multica — 确定性 CLI 驱动的多 Agent 并行开发编排",
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
                mod.NAME, help=mod.SUMMARY, description=mod.DESCRIPTION,
                formatter_class=argparse.RawDescriptionHelpFormatter)
            mod.register(sub)
            sub.set_defaults(_run=mod.run, _parser=sub)
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
        print(f"需要调用者决策: {e}", file=sys.stderr)
        return e.exit_code
    except OmacError as e:
        print(f"Error: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("已中断(状态在 manifest + 平台,重跑即续跑)", file=sys.stderr)
        return exit_codes.GENERIC


def entry():  # pragma: no cover - console_script 包装
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

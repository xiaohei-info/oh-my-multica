"""omac CLI 入口 — 名词-动词命令树,gh 风格分组 help,稳定退出码。

纪律(设计文档 §2):
- 报错即教学:参数错误时打印错误 + 该命令完整 help;
- 业务层 raise OmacError 子类,这里统一转退出码,不散落 sys.exit。
"""
from __future__ import annotations

import argparse
import sys

from .. import __version__
from ..errors import NeedsDecision, OmacError
from . import exit_codes
from .commands import COMMAND_GROUPS

PROG = "omac"


class _HelpOnErrorParser(argparse.ArgumentParser):
    """参数错误时打印错误 + 完整 help(报错即教学),exit 1。"""

    def error(self, message):
        print(f"Error: {message}\n", file=sys.stderr)
        self.print_help(sys.stderr)
        raise SystemExit(exit_codes.GENERIC)


def build_parser() -> argparse.ArgumentParser:
    epilog_lines = []
    for group_title, commands in COMMAND_GROUPS:
        epilog_lines.append(f"{group_title}")
        for mod in commands:
            epilog_lines.append(f"  {mod.NAME:<9}{mod.SUMMARY}")
    parser = _HelpOnErrorParser(
        prog=PROG,
        description="oh-my-agent-cluster — 确定性 CLI 驱动的多 Agent 并行开发编排",
        epilog="\n".join(epilog_lines),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")

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

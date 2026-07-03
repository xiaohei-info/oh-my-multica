"""骨架期的统一 stub:命令已注册、协议已写进 help,实现按路线图推进。"""
from __future__ import annotations

import sys

from .. import exit_codes


def not_implemented(command: str, phase: str) -> int:
    print(
        f"`omac {command}` 尚未实现 —— 规划于 {phase}(见 docs/omac-cli-design.md §10.3)。\n"
        f"命令契约与流程说明:omac {command.split()[0]} --help / omac guide workflow",
        file=sys.stderr,
    )
    return exit_codes.GENERIC

"""输出层(设计文档 §5.2)。

纪律:
- stdout 出数据(--output json|table);
- stderr 出「下一步提示」类引导,不污染 stdout 数据流。
"""
from __future__ import annotations

import json
import sys

JSON = "json"
TABLE = "table"
OUTPUT_CHOICES = (TABLE, JSON)


def add_output_flag(parser, *, default=TABLE):
    """给子命令挂统一的 --output flag。"""
    parser.add_argument(
        "--output", choices=OUTPUT_CHOICES, default=default,
        help=f"输出格式:json 给 Agent/Web,table 给人类调试(默认:{default})",
    )
    return parser


def print_json(data, stream=None):
    stream = stream or sys.stdout
    json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
    stream.write("\n")


def print_table(headers: list, rows: list, stream=None):
    """简单对齐表格(对标 multica CLI 的 tabwriter 风格)。"""
    stream = stream or sys.stdout
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    stream.write(fmt.format(*[str(h) for h in headers]).rstrip() + "\n")
    for row in rows:
        stream.write(fmt.format(*[str(c) for c in row]).rstrip() + "\n")


def hint(message: str):
    """「下一步提示」走 stderr。"""
    print(message, file=sys.stderr)

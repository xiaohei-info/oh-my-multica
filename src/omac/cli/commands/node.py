"""omac node — exit 20 之后的决策工具(重试是显式决策)。"""
from __future__ import annotations

from ._stub import not_implemented
from ..output import add_output_flag

NAME = "node"
SUMMARY = "exit 20 后的决策工具(show/retry/abandon)"
DESCRIPTION = """异常处理闭环:dag run 以 exit 20 退出后,由调用者决策。

子命令:
  show     单节点完整证据链:contract、验证命令输出、评审 report(含评审目标)、
           env_setup、PR / 平台 issue 链接、回退计数
  retry    显式重置节点为 todo(可 --worker 换人),下次 dag run 生效。
           重试不会自动发生——这是设计原则(§2.4)
  abandon  放弃节点:标 abandoned,不硬依赖它的下游解锁

决策后重跑 `omac dag run`:已 done 节点复用,从决策后的状态继续推进。
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    show = sub.add_parser("show", help="单节点完整证据链")
    show.add_argument("manifest")
    show.add_argument("node_key")
    add_output_flag(show)

    retry = sub.add_parser("retry", help="显式重置节点为 todo(可换人)")
    retry.add_argument("manifest")
    retry.add_argument("node_key")
    retry.add_argument("--worker", help="改派给另一个 worker")

    abandon = sub.add_parser("abandon", help="放弃节点,解锁非硬依赖下游")
    abandon.add_argument("manifest")
    abandon.add_argument("node_key")


def run(args) -> int:
    return not_implemented(f"node {args.action}", "P1")

"""omac dag — 确定性 loop 执行。"""
from __future__ import annotations

from ._stub import not_implemented
from ..output import add_output_flag

NAME = "dag"
SUMMARY = "确定性 loop 执行(run/status/tick)"
DESCRIPTION = """确定性编排循环:sync(回收结果)→ decide(就绪节点)→ dispatch(派发)。

子命令:
  run      前台循环直到收敛或需决策。exit 0 = 全部节点 done 且总控验收 pass;
           exit 20 = 无法继续推进,stdout 输出结构化报告(失败节点、证据摘要、
           受阻下游、可执行的下一步动作命令)。
           循环幂等:任意中断后重跑即续跑,done 节点复用。
           节点生命周期:todo → in_progress → ci_check* → in_review → merging* → done
           (* 由 config 的 ci/merge 决定;三类回退一律转回 worker,各有界 ≤3 次)
  status   随时查看快照(reconcile + 各节点状态),不推进
  tick     单轮推进后立即退出:exit 0 收敛 / 10 推进中 / 20 需决策(调试用)

有界运行:--max-rounds N / --max-minutes N(给不想长阻塞的 agent 调用者分段跑)
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    run_p = sub.add_parser("run", help="前台 loop 直到收敛或 exit 20")
    run_p.add_argument("manifest", help="manifest 文件路径")
    run_p.add_argument("--engine", help="引擎类型覆盖(缺省读 config/env)")
    run_p.add_argument("--workspace", help="workspace 覆盖(缺省读 config/env)")
    run_p.add_argument("--max-parallel", type=int, help="并发上限覆盖")
    run_p.add_argument("--max-rounds", type=int, help="最多跑 N 轮后退出(分段跑)")
    run_p.add_argument("--max-minutes", type=int, help="最多跑 N 分钟后退出(分段跑)")
    add_output_flag(run_p)

    status = sub.add_parser("status", help="查看快照,不推进")
    status.add_argument("manifest", help="manifest 文件路径")
    add_output_flag(status)

    tick = sub.add_parser("tick", help="单轮推进后退出(exit 0/10/20)")
    tick.add_argument("manifest", help="manifest 文件路径")
    add_output_flag(tick)


def run(args) -> int:
    return not_implemented(f"dag {args.action}", "P1")

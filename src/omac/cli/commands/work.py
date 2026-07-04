"""omac work — 被派发 agent 的统一执行接口(5 类 issue × 产出/评审阶段)。"""
from __future__ import annotations

from ._stub import not_implemented

NAME = "work"
SUMMARY = "统一执行接口(5 类 issue × 产出/评审阶段)"
DESCRIPTION = """被派活的 agent 永远只需要两个命令。

issue 的范围是一个完整阶段:产出、评审、回退往返都在同一条 issue 时间线上;
当前阶段与承担者由 issue metadata + assignee 表达,交接 = 转派(assign)。

  show     按(issue 类型 × 当前阶段 × 你的身份)输出任务上下文与执行协议
  submit   按同一维度校验并提交交付物(左移校验:缺什么当场打回,exit 5)

issue 类型与交付参数:
  plan              产出: --plan-file           review: --verdict --report-file
  acceptance        产出: --acceptance-file      review: 同上
  decompose         产出: --manifest-file        review: 同上
  develop           产出: --pr-url --verification-file(env 依赖时须含 env_setup)
                                                 review: 同上(report 必含评审目标)
  final-acceptance  产出: --acceptance-results-file(逐项 pass/fail,无 review 阶段)

硬约束:
  - 唯一写入口:交付物只经 `work submit` 写入,禁止手搓 metadata set(会冒出 dotted key /
    prose / JSON 三套口径,引擎读不到就误判 blocked、甚至失败隔离整条 DAG)。
  - 证据门:submit 时左移校验,verification 必须覆盖 contract.verification_commands 与
    contract.integration_gates,缺什么当场 exit 5 并精确告知;不写入、不转状态。
  - 改动分支覆盖硬门槛(缺省 90%):`diff-cover` 退出码非 0 = 不达标,不得转 in_review;
    不接受"先合后补"。
  - 收活铁律(reviewer):先 `git diff` 看真实改动,再独立复跑测试,绝不只凭 worker 自述;
    改动分支覆盖 < gate 阈值 = Blocker。
  - 只读共享态:reviewer 用 `git diff`/`git show` 审阅,绝不在共享主工作树 reset/checkout/merge。
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    show = sub.add_parser("show", help="取任务上下文与该类型×阶段的执行协议")
    show.add_argument("issue_id")

    submit = sub.add_parser("submit", help="提交交付物(左移校验)")
    submit.add_argument("issue_id")
    submit.add_argument("--plan-file")
    submit.add_argument("--acceptance-file")
    submit.add_argument("--manifest-file")
    submit.add_argument("--pr-url")
    submit.add_argument("--verification-file")
    submit.add_argument("--verdict", choices=["pass", "pass-with-nits", "reject"])
    submit.add_argument("--report-file")
    submit.add_argument("--acceptance-results-file")


def run(args) -> int:
    return not_implemented(f"work {args.action}", "P2")

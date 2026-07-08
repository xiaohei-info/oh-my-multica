"""omac guide — 知识分发通道之一(设计文档 §9)。骨架期即可用。"""
from __future__ import annotations

import sys

from ...guide import TOPICS, load_topic
from .. import exit_codes

NAME = "guide"
SUMMARY = "工作流指南(workflow/manifest/roles/worker/reviewer/recovery)"
DESCRIPTION = """按需阅读的工作流知识(随包分发,omac guide)。

  omac guide             列出全部 topic
  omac guide workflow    整体工作流:init → plan → dag run → 异常处理闭环
  omac guide manifest    manifest DAG 的拆解方法论与 contract 字段
  omac guide roles       角色模型与配置
  omac guide worker      worker 执行协议(TDD、证据、env_setup)
  omac guide reviewer    reviewer 评审协议(独立复跑、评审目标)
  omac guide recovery    exit 20 之后的恢复手册

角色别名(软上下文,返回 0):planner→workflow, orchestrator→manifest,
  architect/acceptor→roles。未知 topic 仍返回非 0,避免吞掉真实拼写错误。
"""

ROLE_TOPIC_ALIASES = {
    "planner": "workflow",
    "orchestrator": "manifest",
    "architect": "roles",
    "acceptor": "roles",
}


def register(parser):
    parser.add_argument("topic", nargs="?",
                        help="要阅读的 topic 或角色别名;缺省列出全部")


def run(args) -> int:
    if not args.topic:
        print("可用 topic:")
        for name in TOPICS:
            print(f"  omac guide {name}")
        print("\n角色别名:")
        for role, topic in sorted(ROLE_TOPIC_ALIASES.items()):
            print(f"  omac guide {role}  ->  omac guide {topic}")
        return exit_codes.OK

    topic = args.topic
    if topic in ROLE_TOPIC_ALIASES:
        mapped = ROLE_TOPIC_ALIASES[topic]
        print(f"{topic} 是角色别名,已打开 {mapped}。可用 topic 见 `omac guide`。\n")
        topic = mapped

    if topic not in TOPICS:
        valid = ", ".join(sorted(TOPICS))
        aliases = ", ".join(sorted(ROLE_TOPIC_ALIASES))
        print(
            f"未知 guide topic 或角色别名: {args.topic}\n"
            f"可用 topic: {valid}\n"
            f"可用角色别名: {aliases}\n"
            "先运行 `omac guide` 列表,不要猜 topic。",
            file=sys.stderr,
        )
        return exit_codes.GENERIC

    print(load_topic(topic))
    return exit_codes.OK

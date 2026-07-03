"""omac guide — 知识分发通道之一(设计文档 §9)。骨架期即可用。"""
from __future__ import annotations

from ...guide import TOPICS, load_topic
from .. import exit_codes

NAME = "guide"
SUMMARY = "工作流指南(workflow/manifest/roles/worker/reviewer/recovery)"
DESCRIPTION = """按需阅读的工作流知识——原 skill 内容的迁移目的地。

  omac guide             列出全部 topic
  omac guide workflow    整体工作流:init → plan → dag run → 异常处理闭环
  omac guide manifest    manifest DAG 的拆解方法论与 contract 字段
  omac guide roles       角色模型与配置
  omac guide worker      worker 执行协议(TDD、证据、env_setup)
  omac guide reviewer    reviewer 评审协议(独立复跑、评审目标)
  omac guide recovery    exit 20 之后的恢复手册
"""


def register(parser):
    parser.add_argument("topic", nargs="?", choices=sorted(TOPICS),
                        help="要阅读的 topic;缺省列出全部")


def run(args) -> int:
    if not args.topic:
        print("可用 topic:")
        for name in TOPICS:
            print(f"  omac guide {name}")
        return exit_codes.OK
    print(load_topic(args.topic))
    return exit_codes.OK

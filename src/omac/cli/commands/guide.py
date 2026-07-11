"""omac guide — 知识分发通道之一(设计文档 §9)。骨架期即可用。"""
from __future__ import annotations

import sys

from ...guide import (
    ARTIFACT_TOPICS,
    ROLE_TOPICS,
    TOPICS,
    load_artifact_topic,
    load_role_topic,
    load_topic,
)
from .. import exit_codes

NAME = "guide"
SUMMARY = "Agent 按 guide_refs 最小加载的稳定工作流知识"
DESCRIPTION = """Agent 按需读取的稳定静态知识(随包分发,omac guide)。

处理 work item 时先运行:
  omac work show <id> --output json

以返回的当前实例事实为准,再只按 guide_refs 最小加载所需 topic;不要预读全部 Guide。
Guide 只描述跨实例稳定协议,不能覆盖当前任务、阶段、身份、权威顺序或精确 submit 命令。
若两者不一致,实例事实优先。

稳定 topic 路径:
  omac guide                         列出全部 topic
  omac guide workflow                整体工作流:init → plan → dag run → 异常处理闭环
  omac guide roles                   生命周期角色索引与职责边界
  omac guide role planner            planner/architect 设计方案与验收文档协议
  omac guide role orchestrator       decompose / 增量 decompose 协议
  omac guide role worker             develop authoring 协议
  omac guide role reviewer           review phase 通用协议
  omac guide role acceptor           final-acceptance 协议
  omac guide artifact design         设计文档格式与设计方法
  omac guide artifact acceptance     验收文档格式
  omac guide artifact manifest       manifest DAG 与 contract schema
  omac guide artifact evidence       verification/review/acceptance-results schema
  omac guide recovery                exit 20 之后的恢复手册
"""


def register(parser):
    parser.add_argument(
        "topic", nargs="*",
        help="要阅读的 topic: workflow|roles|recovery 或 role/artifact 分组",
    )


def _print_index() -> None:
    print("Agent Guide 索引(稳定静态知识):")
    print("  先运行: omac work show <id> --output json")
    print("  再按 guide_refs 最小加载;实例事实优先,Guide 不覆盖当前任务事实。")
    print("\n可用 topic:")
    for name in TOPICS:
        print(f"  omac guide {name}")
    print("\n角色协议:")
    for name in ROLE_TOPICS:
        print(f"  omac guide role {name}")
    print("\n产物格式:")
    for name in ARTIFACT_TOPICS:
        print(f"  omac guide artifact {name}")


def _unknown(requested: str) -> int:
    valid = ", ".join([
        *(f"{name}" for name in TOPICS),
        *(f"role {name}" for name in ROLE_TOPICS),
        *(f"artifact {name}" for name in ARTIFACT_TOPICS),
    ])
    print(
        f"未知 guide topic: {requested}\n"
        f"可用 topic: {valid}\n"
        "示例: omac guide role worker / omac guide artifact manifest\n"
        "先运行 `omac guide` 列表,不要猜 topic。",
        file=sys.stderr,
    )
    return exit_codes.GENERIC


def run(args) -> int:
    parts = list(args.topic or [])
    if not parts:
        _print_index()
        return exit_codes.OK

    if len(parts) == 1 and parts[0] in TOPICS:
        print(load_topic(parts[0]))
        return exit_codes.OK

    if len(parts) == 2 and parts[0] == "role":
        role = parts[1]
        if role in ROLE_TOPICS:
            print(load_role_topic(role))
            return exit_codes.OK
        return _unknown(" ".join(parts))

    if len(parts) == 2 and parts[0] == "artifact":
        artifact = parts[1]
        if artifact in ARTIFACT_TOPICS:
            print(load_artifact_topic(artifact))
            return exit_codes.OK
        return _unknown(" ".join(parts))

    return _unknown(" ".join(parts))

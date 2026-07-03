"""omac init — 交互式配置 / --check 体检。骨架期实现 --check 的本地部分。"""
from __future__ import annotations

import os
import shutil
import sys

from ...core import config as config_mod
from .. import exit_codes
from ._stub import not_implemented

NAME = "init"
SUMMARY = "交互式配置 / --check 体检"
DESCRIPTION = """一次性配置:选定 workspace → 列出全量 agent → 完成角色映射,
固化进 .orchestrator/config.yaml(不引入小队/分组等平台特有概念)。

  omac init            交互式生成配置(规划于 P1)
  omac init --check    体检:multica CLI 是否在 PATH / 配置文件是否存在且含
                       engine·workspace·roles / 各角色 agent 是否在工作空间内
"""


def register(parser):
    parser.add_argument("--check", action="store_true", help="体检模式,不写任何文件")


def _check() -> int:
    problems = []
    cfg = config_mod.load_config()
    if not cfg:
        problems.append(f"配置文件不存在: {config_mod.CONFIG_PATH} —— 运行 `omac init` 生成")
    else:
        for key in ("engine", "workspace", "roles"):
            if not cfg.get(key):
                problems.append(f"配置缺少 `{key}` 字段(见 omac guide roles)")
        if cfg.get("engine") == "multica" and shutil.which("multica") is None:
            problems.append("multica CLI 不在 PATH —— 安装并登录后重试")

    if problems:
        print("体检未通过:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return exit_codes.VALIDATION
    print(f"体检通过:{config_mod.CONFIG_PATH} 就绪(engine={cfg.get('engine')}, "
          f"workspace={cfg.get('workspace')})")
    return exit_codes.OK


def run(args) -> int:
    if args.check:
        return _check()
    return not_implemented("init", "P1")

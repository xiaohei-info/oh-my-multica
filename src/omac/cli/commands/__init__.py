"""命令注册表 — 分组顺序即 root help 的展示顺序(设计文档 §5)。

每个命令模块约定导出:
    NAME: str            命令名
    SUMMARY: str         一行摘要(root help 用)
    DESCRIPTION: str     完整描述(<command> --help 用,承载协议知识)
    register(parser)     挂子命令参数
    run(args) -> int     执行,返回退出码(None 视为 0);业务错误 raise OmacError
"""
from . import config_cmd, dag, guide, init_cmd, node, plan, web, work

COMMAND_GROUPS = [
    ("CORE COMMANDS(调用者/驱动侧)", [plan, dag, node]),
    ("WORK COMMANDS(被派发 agent 侧)", [work]),
    ("SETUP COMMANDS", [init_cmd, config_cmd]),
    ("GUIDE COMMANDS", [guide]),
    ("WEB COMMANDS", [web]),
]

"""omac web —— 本地只读可视化面板(设计文档 §13)。

薄路由层 + 进程内调用 CLI 命令函数(P5.1)。"""
from .server import WebServer, build_server, require_token_if_exposed, _is_local_host

__all__ = ["WebServer", "build_server", "require_token_if_exposed", "_is_local_host"]

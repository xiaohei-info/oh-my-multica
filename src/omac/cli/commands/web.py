"""omac web —— 本地只读可视化面板(设计文档 §13)。"""
from __future__ import annotations

import sys

from .. import exit_codes
from ...web.server import WebServer, require_token_if_exposed

NAME = "web"
SUMMARY = "本地只读可视化面板(选 manifest,看进度与证据链)"
DESCRIPTION = """启动本地 web 面板 —— CLI 的第三种调用者,不是第二套系统:
每个 API 端点与一条 CLI 命令一一对应,响应体就是该命令 --output json 的原样输出。

  omac web [--port 8321] [--host 127.0.0.1] [--open] [--refresh 10]

一期只读(仪表盘不是方向盘):异常面板给出可复制的下一步命令,处置回终端。
默认只绑 127.0.0.1;--host 0.0.0.0 对外暴露时强制要求 --token(无 token 直接 exit 5)。
"""


def register(parser):
    parser.add_argument("--port", type=int, default=8321)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--refresh", type=int, default=10, help="前端轮询间隔(秒)")
    parser.add_argument("--token", help="对外暴露(--host 非本机)时必填的 Bearer token")


def run(args) -> int:
    # 非本机 host 却未给 token → exit 5(校验失败),cli.main 统一捕获 → 退出码 5。
    require_token_if_exposed(args.host, args.token)
    if args.refresh < 1:
        from ...errors import ValidationError
        raise ValidationError(f"--refresh 必须 ≥ 1,得到 {args.refresh}")
    server = WebServer(
        host=args.host, port=args.port,
        token=args.token, refresh=args.refresh,
        open_browser=args.open,
    )
    return server.serve_forever()

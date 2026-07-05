"""omac web 服务端 —— 薄路由层(设计文档 §13)。

职责:
- 把 URL 路径 + 查询参数解析为对 api.py 端点的一次调用(Bearer token 校验);
- 决定 HTTP 状态码(api 层的 OmacError/退出码 → 合适的 HTTP 状态);
- 决定内容类型(JSON/HTML);
- 对 dag/status 应用 TTL 缓存。

不在本层做的事:
- 任何业务逻辑、engine 调用、数据二次加工 —— 全部委派给 api.py,api.py 委派给命令层。
- 任何写操作(一期只读)。
"""
from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from omac.cli import exit_codes
from omac.core import config as config_mod
from omac.errors import OmacError, ValidationError

from . import api

# 一期 Bearer token 未携带 / 不匹配时的教学式提示(报错即教学)。
_UNAUTHORIZED_HINT = (
    "需在请求头中携带 Bearer token。示例:\n"
    "  GET /api/dag/status?manifest=...\n"
    "  Authorization: Bearer <token>"
)


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>omac web — 本地可视化面板</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 24px; line-height: 1.5; }}
  code {{ background: rgba(127,127,127,.18); padding: 1px 5px; border-radius: 4px; font-size: .9em; }}
  h1 {{ font-size: 1.2rem; }}
  ul {{ padding-left: 1.2rem; }}
  code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
</style>
</head>
<body>
<h1>omac web — 本地只读可视化面板</h1>
<p>本期只读 API(后端只做了「解析参数 → 调命令函数 → 返回 JSON」):</p>
<ul>
  <li><code>GET /api/manifests</code> — 扫 <code>.orchestrator/*.yaml</code>(排除 config),带进度摘要</li>
  <li><code>GET /api/config</code> ← <code>config get --output json</code></li>
  <li><code>GET /api/dag/status?manifest=&lt;path&gt;</code> ← <code>dag status --output json</code>(TTL 缓存 = poll_interval)</li>
  <li><code>GET /api/node/&lt;key&gt;?manifest=&lt;path&gt;</code> ← <code>node show --output json</code></li>
  <li><code>GET /api/plan/acceptance?manifest=&lt;path&gt;</code> — 验收文档</li>
  <li><code>GET /api/meta</code> — 前端配置(refresh 等)</li>
</ul>
<p>一期只读,所有处置动作回终端。</p>
</body>
</html>
"""


def _load_static_html() -> str:
    """优先读取 wheel 随包的 static/index.html;缺则退回内置 HTML_PAGE 提示页。"""
    try:
        from importlib import resources
        candidate = resources.files("omac.web").joinpath("static/index.html")
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except Exception:
        pass
    return HTML_PAGE


STATIC_HTML = _load_static_html()


# 静态资源 MIME 表(按扩展名):仅列 SPA 手边会用到的最小集合,未知扩展按 octet-stream 兜底。
_STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _safe_relative(rel: str) -> str | None:
    """拒绝路径穿越:只允许不含 ".." 且不含绝对/反斜杠的纯相对路径。"""
    if not rel:
        return None
    norm = rel.replace("\\", "/")
    if norm.startswith("/") or any(seg == ".." for seg in norm.split("/")):
        return None
    return norm


def _resolve_static(rel: str):
    """把 a/relative/path 解析为 static 目录下的 importlib.resources 节点;不存在/越界返回 None。"""
    norm = _safe_relative(rel)
    if norm is None:
        return None
    try:
        from importlib import resources
        candidate = resources.files("omac.web").joinpath("static", norm)
        if candidate.is_file():
            return candidate
    except Exception:
        pass
    return None


def _mime_for(rel: str) -> str:
    r = rel.lower()
    for ext, mime in _STATIC_MIME.items():
        if r.endswith(ext):
            return mime
    return "application/octet-stream"


def _is_local_host(host: str) -> bool:
    """默认只绑本机视为 local,其余视为对外暴露需 token。"""
    h = host.strip().lower()
    return h in {"127.0.0.1", "::1", "localhost"}


def require_token_if_exposed(host: str, token: str | None) -> None:
    """--host 非本机却未给 --token → 调用方应在启动前拦截(exit 5)。"""
    if not _is_local_host(host) and not token:
        raise ValidationError(
            f"--host {host!r} 对外暴露时必须配合 --token。\n"
            "  内网主机可信赖,对外暴露则应校验 Bearer token 防未授权访问。\n"
            "  启动示例: omac web --host 0.0.0.0 --token <secret>")


def _error_response(status: int, message: str, hint: str | None = None) -> dict:
    body: dict[str, Any] = {"error": message}
    if hint:
        body["hint"] = hint
    return body


class _JSONResponder:
    """把 api 端点结果 + 异常映射为 HTTP 响应。"""

    def __init__(self, handler: BaseHTTPRequestHandler, cache: api.StatusCache,
                 refresh: int):
        self.handler = handler
        self.cache = cache
        self.refresh = refresh

    # --------------- 状态码映射 ---------------

    @staticmethod
    def _status_for_error(e: BaseException) -> int:
        if isinstance(e, api._CommandFailed):
            rc = e.rc
            if rc == exit_codes.VALIDATION:
                return 400
            if rc == exit_codes.NEEDS_DECISION:
                # needs_decision 仍是合法状态,带结构化报告 200。
                return 200
            return 500
        if isinstance(e, ValidationError):
            return 400
        if isinstance(e, OmacError):
            return 400 if _looks_client(e) else 500
        return 500

    def _send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self.handler.send_response(status)
        self.handler.send_header("Content-Type", "application/json; charset=utf-8")
        self.handler.send_header("Content-Length", str(len(data)))
        self.handler.send_header("Cache-Control", "no-store")
        self.handler.end_headers()
        self.handler.wfile.write(data)

    def _fail(self, status: int, e: BaseException) -> None:
        payload = _error_response(status, str(e))
        if isinstance(e, ValidationError):
            # 报错即教学:hint 直接来自异常消息(命令层已给完整指引)。
            payload["hint"] = str(e)
        self._send_json(status, payload)

    # --------------- 具体端点 ---------------

    def handle(self, method: str, path: str, query: dict[str, list[str]],
               orchestrator_dir: Path) -> None:
        static_match = re.fullmatch(r"/static/(.+)", path)
        if static_match:
            rel = static_match.group(1)
            asset = _resolve_static(rel)
            if asset is None:
                return self._fail(404, OmacError(f"未找到静态资源: {path}"))
            try:
                data = asset.read_bytes()
            except Exception as e:
                return self._fail(500, OmacError(f"读取静态资源失败: {e}"))
            ctype = _mime_for(rel)
            return self._send_blob(200, ctype, data)
        if path in ("/", "/index.html"):
            return self._send_html(200, STATIC_HTML)
        if path == "/api/meta":
            return self._send_json(200, {"refresh": self.refresh, "version": _version()})
        if path == "/api/manifests":
            return self._send_json(200, api.get_manifests(orchestrator_dir))
        if path == "/api/config":
            try:
                return self._send_json(200, api.get_config())
            except BaseException as e:
                return self._fail(self._status_for_error(e), e)
        if path == "/api/dag/status":
            manifest = _first(query.get("manifest"))
            if not manifest:
                return self._fail(400, ValidationError("缺少查询参数 manifest"))
            try:
                value, _hit = self.cache.get_or_compute(
                    manifest, lambda: api.dag_status(manifest))
                return self._send_json(200, value)
            except BaseException as e:
                return self._fail(self._status_for_error(e), e)
        node_match = re.fullmatch(r"/api/node/([^/]+)", path)
        if node_match:
            node_key = node_match.group(1)
            manifest = _first(query.get("manifest"))
            if not manifest:
                return self._fail(400, ValidationError("缺少查询参数 manifest"))
            try:
                return self._send_json(200, api.node_show(manifest, node_key))
            except BaseException as e:
                return self._fail(self._status_for_error(e), e)
        if path == "/api/plan/acceptance":
            manifest = _first(query.get("manifest"))
            if not manifest:
                return self._fail(400, ValidationError("缺少查询参数 manifest"))
            return self._send_json(200, api.get_plan_acceptance(orchestrator_dir, manifest))
        self._fail(404, OmacError(f"未找到端点: {path}"))

    def _send_blob(self, status: int, ctype: str, data: bytes) -> None:
        self.handler.send_response(status)
        self.handler.send_header("Content-Type", ctype)
        self.handler.send_header("Content-Length", str(len(data)))
        self.handler.send_header("Cache-Control", "no-store")
        self.handler.end_headers()
        self.handler.wfile.write(data)

    def _send_html(self, status: int, html: str) -> None:
        data = html.encode("utf-8")
        self.handler.send_response(status)
        self.handler.send_header("Content-Type", "text/html; charset=utf-8")
        self.handler.send_header("Content-Length", str(len(data)))
        self.handler.end_headers()
        self.handler.wfile.write(data)


def _looks_client(e: OmacError) -> bool:
    # 边界说明:本层(api/server)当前所有面向客户端的校验错误都是 ValidationError
    # (缺参数 / manifest 不存在 / 命令校验失败),统一视为 4xx。
    # 其它 OmacError(平台/网络/内部)归为 5xx,避免把服务端抖动暴露为客户端错误。
    if isinstance(e, ValidationError):
        return True
    return False


def _version() -> str:
    from omac import __version__
    return __version__


def _first(vals: list[str] | None) -> str | None:
    return vals[0] if vals else None


class _Handler(BaseHTTPRequestHandler):
    """单一 server 实例复用,以类属性挂载配置(避免依赖 HTTPServer 子类化工厂)。"""

    server_version = "omac-web/0.1"
    token: str | None = None
    orchestrator_dir: Path = Path(".orchestrator")
    cache: api.StatusCache = api.StatusCache()  # 默认;start() 会覆盖为合适的 poll_interval
    refresh: int = 10
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 抑制默认 stderr 请求日志,保持输出干净(生产环境仍可重写这里落盘)。
        pass

    # --------------- token 入口校验 ---------------

    def _authorize(self) -> bool:
        if not self.token:
            return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not _constant_time_eq(auth[7:], self.token):
            responder = _JSONResponder(self, self.cache, self.refresh)
            responder._send_json(401, _error_response(
                401, "Unauthorized", _UNAUTHORIZED_HINT))
            return False
        return True

    # --------------- 路由派发(PUT/POST/DELETE 一期 405) ---------------

    def do_GET(self):
        if not self._authorize():
            return
        parsed = urlparse(self.path)
        try:
            _JSONResponder(self, self.cache, self.refresh).handle(
                "GET", parsed.path, parse_qs(parsed.query), self.orchestrator_dir)
        except Exception as e:
            # 兜底:任何未被捕获异常 → 500(不应发生,各 endpoint 已 try)。
            data = _error_response(500, f"内部错误: {e}")
            raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def do_POST(self):
        return self._method_not_allowed()

    def do_PUT(self):
        return self._method_not_allowed()

    def do_DELETE(self):
        return self._method_not_allowed()

    def _method_not_allowed(self):
        data = _error_response(405, "Method Not Allowed",
                               "一期只读,仅支持 GET。")
        raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _constant_time_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a.encode("utf-8"), b.encode("utf-8")):
        r |= x ^ y
    return r == 0


class WebServer:
    """组合器:server 实例 + 启动/停止。缓存以 poll_interval 为 TTL。"""

    def __init__(self, host: str, port: int, *,
                 token: str | None = None, refresh: int = 10,
                 poll_interval: int | None = None,
                 open_browser: bool = False,
                 handler_cls: type[BaseHTTPRequestHandler] = _Handler):
        self.host = host
        self.port = port
        self.token = token
        self.refresh = refresh
        self.open_browser = open_browser
        self.poll_interval = poll_interval if poll_interval is not None else api._poll_interval()
        self.handler_cls = handler_cls
        self._server: ThreadingHTTPServer | None = None

    def _build(self) -> ThreadingHTTPServer:
        require_token_if_exposed(self.host, self.token)
        cache = api.StatusCache(ttl=self.poll_interval)
        # 把运行时配置注入 handler 类(供所有连接复用)。
        self.handler_cls.token = self.token
        self.handler_cls.refresh = self.refresh
        self.handler_cls.cache = cache
        self.handler_cls.orchestrator_dir = Path(config_mod.CONFIG_DIR)
        return ThreadingHTTPServer((self.host, self.port), self.handler_cls)

    def serve_forever(self) -> int:
        self._server = self._build()
        host, port = self._server.server_address

        if self.open_browser:
            try:
                import webbrowser
                webbrowser.open(f"http://{host}:{port}/")
            except Exception:
                pass
        print(
            f"omac web 已启动 → http://{host}:{port}/  "
            f"(token={'on' if self.token else 'off'}, refresh={self.refresh}s, "
            f"cache_ttl={self.poll_interval}s)",
            file=sys.stderr,
        )
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            print("\n已中断,停服务。", file=sys.stderr)
        finally:
            self._server.server_close()
        return exit_codes.OK

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()


_server_lock = threading.Lock()


def build_server(host: str, port: int, **kwargs) -> WebServer:
    with _server_lock:
        return WebServer(host, port, **kwargs)

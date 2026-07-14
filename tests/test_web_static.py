"""P5.2 web 前端 SPA —— 资源存在性 + CSP 自检 + 结构 sanity(不依赖外网)。

三条防线(对应用户验收标准):
1. wheel 安装后资源随包可达 —— 通过 importlib.resources 读取 static/index.html。
2. 页面无外部网络请求 —— 源码级 CSP 自检:无 <script src=/>link href=http/https/外部 url()。
3. 五个区域 + 8 态着色齐全 —— 静态解析 <section>/<div> id 与 state-token 是否齐备。
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request

import pytest
import yaml

import omac.web.server as web_srv
import omac.web.api as web_api
from omac.cli import exit_codes
from omac.cli.main import main as cli_main


# ----------------------- 资源读取 -----------------------

def _static_dir():
    """omac.web.static 在源码与 wheel 下都能定位(前提:pyproject 注册 package-data)。"""
    from importlib import resources
    return resources.files("omac.web").joinpath("static")


def _read_index() -> str:
    return _static_dir().joinpath("index.html").read_text(encoding="utf-8")


# ----------------------- fixtures(与 test_web_api.py 同形) -----------------------

@pytest.fixture
def orch(tmp_path):
    d = tmp_path / ".omac"
    d.mkdir()
    with open(d / "config.yaml", "w") as f:
        yaml.dump({
            "engine": "mock", "workspace": "ws",
            "defaults": {"poll_interval": 2, "max_parallel": 2},
            "roles": {"workers": ["alice", "bob"]},
        }, f)
    return d


def _write_manifest(dir_path, name, nodes):
    p = dir_path / f"{name}.yaml"
    with open(p, "w") as f:
        yaml.dump({"meta": {"name": name}, "nodes": nodes}, f,
                  default_flow_style=False, allow_unicode=True, sort_keys=False)
    return p


@pytest.fixture
def simple_manifest(orch):
    return _write_manifest(orch, "demo", [
        {"id": "a", "worker": "alice"},
        {"id": "b", "worker": "bob", "blocked_by": ["a"]},
        {"id": "c", "worker": "alice", "blocked_by": ["a"]},
    ])


class _Server:
    """127.0.0.1:0 启动真实 WebServer 后台线程(复用 test_web_api.py 的模式)。"""

    def __init__(self, *, token=None, refresh=2, poll_interval=None,
                 orch_subpath=".omac"):
        self.token = token
        self.refresh = refresh
        self.poll_interval = poll_interval
        self.orch_subpath = orch_subpath
        self._httpd = None
        self._thread = None

    def __enter__(self):
        self._httpd = web_srv.ThreadingHTTPServer(
            ("127.0.0.1", 0), web_srv._Handler)
        self.host, self.port = self._httpd.server_address
        web_srv._Handler.token = self.token
        web_srv._Handler.refresh = self.refresh
        web_srv._Handler.orchestrator_dir = self.orch_subpath
        pi = self.poll_interval if self.poll_interval is not None else 2
        web_srv._Handler.cache = web_srv.api.StatusCache(ttl=pi)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def get(self, path, with_headers=False):
        url = f"http://{self.host}:{self.port}{path}"
        req = urllib.request.Request(url, method="GET")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8")
                return (resp.status, body, ctype) if with_headers else (resp.status, body)
        except urllib.error.HTTPError as e:
            ctype = e.headers.get("Content-Type", "") if e.headers else ""
            body = e.read().decode("utf-8")
            return (e.code, body, ctype) if with_headers else (e.code, body)


# ==================== 1. 资源存在性 ====================

def test_static_index_html_exists():
    """wheel 安装后,omac.web.static.index.html 由 package-data 随包可达。"""
    idx = _static_dir().joinpath("index.html")
    assert idx.is_file(), f"缺 static index.html: {idx}"
    assert idx.stat().st_size > 2000, "index.html 过小(预期嵌入样式+脚本,大于 2KB)"


def test_static_index_has_doctype_and_utf8():
    html = _read_index()
    assert re.search(r"<!doctype\s+html>", html, re.IGNORECASE), "必须 <!doctype html>"
    assert "charset=\"utf-8\"" in html.replace(" ", ""), "必须声明 utf-8"
    assert "<html lang=\"en\">" in html
    assert "const COPY" in html
    assert "language: \"en\"" in html


# ==================== 2. CSP 自检 ==================

# 任意以 http://,https://,// 开头且不是 inlined data: 的 src/href 都是外部请求。
_EXT = re.compile(
    r"""(?i)(?:src|href|action)\s*=\s*["']((https?:)?//[^"']+|http://[^"']+)["']"""
)


def test_index_no_external_resource_urls():
    html = _read_index()
    matches = _EXT.findall(html)
    assert not matches, f"检测到外部资源 URL(违反 CSP): {matches}"


def test_index_no_inline_external_import():
    """禁止 <script src=> / <link rel=stylesheet href=> / @import url(http 等。"""
    html = _read_index()
    assert not re.search(r"(?i)<\s*script[^>]\bsrc\s*=", html), "禁止外部 <script src=>"
    assert not re.search(r"(?i)<\s*link[^>]\bhref\s*=", html), "禁止外部 <link href=>"
    assert not re.search(r"""(?i)@import\s+(?:url\()?["']?http""", html), "禁止 @import 外部"


def test_index_no_eval():
    """不依赖 new Function/eval CSP 安全基线。"""
    html = _read_index()
    assert "new Function(" not in html.replace(" ", ""), "禁止 new Function("
    # eval( 调用(排除属性名含 eval 的边界):允许在字符串里作为单词时手动排除。
    assert not re.search(r"(?<![\w])eval\s*\(", html), "禁止 eval("


# ==================== 3. 五区域 + 8 态 =================-

def _regions_present() -> dict[str, bool]:
    html = _read_index()
    return {
        "manifest-selector": id_present(html, "manifest-selector"),
        "dag-canvas": id_present(html, "dag-canvas"),
        "node-detail": id_present(html, "node-detail"),
        "static-info": id_present(html, "static-info"),
        "anomaly-panel": id_present(html, "anomaly-panel"),
    }


def id_present(html: str, needle: str) -> bool:
    return bool(re.search(rf'\bid\s*=\s*["\']{re.escape(needle)}["\']', html))


def test_all_five_regions_present():
    regions = _regions_present()
    missing = [k for k, v in regions.items() if not v]
    assert not missing, f"缺区域(应俱全): {missing}"


@pytest.mark.parametrize("state", [
    "todo", "in_progress", "ci_check", "in_review", "merging",
    "done", "blocked", "abandoned",
])
def test_state_token_present_in_index(state):
    """8 态着色:索引中需出现每个状态 token(用于 CSS / 图例 / 着色逻辑)。"""
    html = _read_index()
    assert state in html, f"缺状态 token: {state}"


# ==================== 4. HTTP 集成:index 默认进入 SPA ====================

def test_root_serves_spa_not_bulletin(orch, simple_manifest, monkeypatch):
    """GET / 进入单页面板(不再是旧版提示页),且包含 SPA 必备 root 节点。"""
    monkeypatch.chdir(orch.parent)
    html = _read_index()
    with _Server(orch_subpath=str(orch)) as s:
        status, body, ctype = s.get("/", with_headers=True)
    assert status == 200
    assert ctype == "text/html; charset=utf-8"
    # 与 wheel 同源的 index.html 应一致(未经服务端改写)。
    assert id_present(body, "dag-canvas"), "GET / 响应必须含 SPA dag-canvas 节点"
    assert _static_body_equals_served(body), "GET / 必须分发与 wheel 同源的 index.html"


def _static_body_equals_served(body: str) -> bool:
    """GET / 的响应体必须严格等于 importlib 读取的 static/index.html(同源、未被改写)。"""
    src = _read_index().strip()
    return body.strip() == src


def test_index_polls_meta_for_refresh(orch, simple_manifest, monkeypatch):
    """meta refresh(秒)≥ 1 必须出现在响应里,作为轮询依据。"""
    monkeypatch.chdir(orch.parent)
    with _Server(refresh=7, orch_subpath=str(orch)) as s:
        st, body = s.get("/api/meta")
    assert st == 200
    data = json.loads(body)
    assert data["refresh"] == 7


def test_static_index_uses_the_manifest_api(orch, simple_manifest, monkeypatch):
    """SPA 源码引用了 /api/manifests 与 /api/dag/status(前端消费真实端点)。"""
    monkeypatch.chdir(orch.parent)
    html = _read_index()
    assert "/api/manifests" in html, "SPA 必须消费 /api/manifests"
    assert "/api/dag/status" in html, "SPA 必须消费 /api/dag/status"
    assert "/api/meta" in html, "SPA 必须消费 /api/meta(轮询间隔)"
    assert "/api/node/" in html, "SPA 必须消费 /api/node/<key>"


# ==================== 5. /static/<path> 通配路由(nit 修复:单一入口→静态资源通配) ==================

def test_static_asset_route_serves_index_html_via_static_path(orch, simple_manifest, monkeypatch):
    """GET /static/index.html 应通过新通配路由同源分发 MIME 正确的文件。"""
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body, ctype = s.get("/static/index.html", with_headers=True)
    assert status == 200, f"expected 200, got {status}: {body[:120]}"
    assert "text/html" in ctype, f"expected text/html, got {ctype}"
    src = _read_index().strip()
    assert body.strip() == src, "/static/index.html 应与 SOURCE index.html 同源"


def test_static_asset_route_404_on_missing(orch, simple_manifest, monkeypatch):
    """GET /static/no-such-asset.js → 404."""
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get("/static/no-such-asset.js")
    assert status == 404, f"expected 404 for missing asset, got {status}"


@pytest.mark.parametrize("bad_path", [
    "/static/../../../etc/passwd",
    "/static/..%2f..%2fsecret",
    "/static/.%2e/.%2e/pyproject.toml",
    "/static/",
])
def test_static_asset_route_blocks_traversal(orch, simple_manifest, monkeypatch, bad_path):
    """目录穿越防护:含 ".." 或空相对路径的请求应被拒(404/400),不能读到包外。"""
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, _ = s.get(bad_path)
    assert status in (400, 404), f"expected 4xx for {bad_path}, got {status}"

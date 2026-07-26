"""P5.2 web 前端 SPA —— 资源存在性 + CSP 自检 + 结构 sanity(不依赖外网)。

三条防线(对应用户验收标准):
1. wheel 安装后资源随包可达 —— 通过 importlib.resources 读取 static/index.html。
2. 页面无外部网络请求 —— 源码级 CSP 自检:仅引用同源 app.css/app.js。
3. 五个区域 + 8 态着色齐全 —— 静态解析 <section>/<div> id 与 state-token 是否齐备。
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import urllib.error
import urllib.request

import pytest
import yaml

import omac.web.server as web_srv


# ----------------------- 资源读取 -----------------------

def _static_dir():
    """omac.web.static 在源码与 wheel 下都能定位(前提:pyproject 注册 package-data)。"""
    from importlib import resources
    return resources.files("omac.web").joinpath("static")


def _read_index() -> str:
    return _static_dir().joinpath("index.html").read_text(encoding="utf-8")


def _read_asset(name: str) -> str:
    return _static_dir().joinpath(name).read_text(encoding="utf-8")


def _project_dag(nodes, options=None):
    """通过 Node 运行浏览器实际加载的纯投影模块，避免复制 JavaScript 逻辑到测试。"""
    module = _static_dir().joinpath("dag-projection.js")
    if not module.is_file():
        pytest.fail("collapsible DAG projection module is missing")
    program = """
const fs = require('fs');
const vm = require('vm');
const context = {};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context,
  { filename: process.argv[1] });
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dag = context.OMACDag;
const result = dag.projectDag(input.nodes, input.options || {});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", program, str(module)],
        input=json.dumps({"nodes": nodes, "options": options or {}}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _dag_module_value(expression: str):
    module = _static_dir().joinpath("dag-projection.js")
    if not module.is_file():
        pytest.fail("collapsible DAG projection module is missing")
    program = """
const fs = require('fs');
const vm = require('vm');
const context = {};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context,
  { filename: process.argv[1] });
process.stdout.write(JSON.stringify(%s));
""" % expression
    completed = subprocess.run(
        ["node", "-e", program, str(module)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


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
    """wheel 安装后，SPA HTML shell 由 package-data 随包可达。"""
    idx = _static_dir().joinpath("index.html")
    assert idx.is_file(), f"缺 static index.html: {idx}"
    assert idx.stat().st_size > 2000, "index.html 过小，缺少预期 SPA 结构"


def test_static_index_has_doctype_and_utf8():
    html = _read_index()
    js = _read_asset("app.js")
    assert re.search(r"<!doctype\s+html>", html, re.IGNORECASE), "必须 <!doctype html>"
    assert "charset=\"utf-8\"" in html.replace(" ", ""), "必须声明 utf-8"
    assert "<html lang=\"en\">" in html
    assert "const COPY" in js
    assert "language: \"en\"" in js


# ==================== 2. CSP 自检 ==================

# 任意以 http://,https://,// 开头且不是 inlined data: 的 src/href 都是外部请求。
_EXT = re.compile(
    r"""(?i)(?:src|href|action)\s*=\s*["']((https?:)?//[^"']+|http://[^"']+)["']"""
)


def test_index_csp_uses_same_origin_css_and_js_without_inline_code():
    html = _read_index()
    csp = re.search(
        r'<meta[^>]+http-equiv="Content-Security-Policy"[^>]+content="([^"]+)"',
        html,
    )
    assert csp, "index must declare Content-Security-Policy"
    policy = csp.group(1)
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "'unsafe-inline'" not in policy
    assert not re.search(r"(?is)<style(?:\s[^>]*)?>.*?</style>", html)
    assert not re.search(r"(?is)<script(?![^>]+\bsrc=)[^>]*>.*?</script>", html)
    assert '<link rel="stylesheet" href="/static/app.css" />' in html
    assert '<script src="/static/app.js" defer></script>' in html


def test_strict_csp_assets_do_not_use_inline_style_attributes_or_cssom_style_writes():
    """style-src 'self' 下所有展示状态必须通过同源 CSS class 表达。"""
    html = _read_index()
    js = _read_asset("app.js")
    assert not re.search(r"\sstyle\s*=", html, re.IGNORECASE)
    assert ".style." not in js
    assert not re.search(r"\sstyle=", js, re.IGNORECASE)
    assert re.search(r"\.tab-body-wrap\s*\{[^}]*position:absolute", _read_asset("app.css"))


def test_same_origin_css_and_js_are_packaged_and_contain_spa_bootstrap():
    css = _read_asset("app.css")
    js = _read_asset("app.js")
    assert ":root" in css
    assert "#dag-canvas" in css
    assert 'api("/api/manifests")' in js
    assert "async function loadManifests()" in js
    assert "init();" in js


def test_index_no_external_resource_urls():
    html = _read_index()
    matches = _EXT.findall(html)
    assert not matches, f"检测到外部资源 URL(违反 CSP): {matches}"


def test_index_references_only_declared_same_origin_assets():
    """允许严格 CSP 的同源资源，禁止其它脚本、样式与网络 import。"""
    html = _read_index()
    css = _read_asset("app.css")
    scripts = re.findall(r'(?i)<script[^>]+src=["\']([^"\']+)', html)
    styles = re.findall(
        r'(?i)<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']([^"\']+)',
        html,
    )
    assert scripts == ["/static/dag-projection.js", "/static/app.js"]
    assert styles == ["/static/app.css"]
    assert not re.search(r"""(?i)@import\s+(?:url\()?["']?http""", css)


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
    "done", "failed", "blocked", "abandoned", "unknown",
])
def test_state_token_present_in_spa_assets(state):
    """8 态着色 token 可位于同源 CSS/JS，不要求内联在 HTML。"""
    assets = _read_asset("app.css") + _read_asset("app.js")
    assert state in assets, f"缺状态 token: {state}"


# ==================== 4. HTTP 集成:index 默认进入 SPA ====================

def test_root_serves_spa_not_bulletin(orch, simple_manifest, monkeypatch):
    """GET / 进入单页面板(不再是旧版提示页),且包含 SPA 必备 root 节点。"""
    monkeypatch.chdir(orch.parent)
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
    """SPA 通过命令一致的 API 读取运行状态与项目配置。"""
    monkeypatch.chdir(orch.parent)
    js = _read_asset("app.js")
    assert "/api/manifests" in js, "SPA 必须消费 /api/manifests"
    assert "/api/dag/status" in js, "SPA 必须消费 /api/dag/status"
    assert "/api/meta" in js, "SPA 必须消费 /api/meta(轮询间隔)"
    assert 'const config = await api("/api/config");' in js
    assert 'applyLanguage(config.language||"en");' in js
    assert "/api/node/" in js, "SPA 必须消费 /api/node/<key>"


def test_spa_wires_collapsible_dag_controls_to_the_pure_projection():
    html = _read_index()
    js = _read_asset("app.js")
    css = _read_asset("app.css")

    for control in [
        "collapse-btn", "expand-all-btn", "focus-active-btn", "focus-anomaly-btn",
    ]:
        assert id_present(html, control), f"缺少 DAG 控件: {control}"
    assert "OMACDag.projectDag" in js
    assert "aggregate" in js
    assert "presentState" in js
    assert ".aggregate" in css
    assert ".node.state-failed" in css


# ==================== 4.5 可折叠分层 DAG 纯投影 ====================

def test_dag_projection_assigns_deterministic_depth_without_duplication():
    nodes = [
        {"key": "root-b", "status": "todo"},
        {"key": "merge", "status": "todo", "blocked_by": ["root-b", "root-a"]},
        {"key": "root-a", "status": "todo"},
        {"key": "tail", "status": "todo", "blocked_by": ["merge"]},
    ]

    projection = _project_dag(nodes)

    assert projection["depths"] == {
        "root-a": 1, "root-b": 1, "merge": 2, "tail": 3,
    }
    assert [node["key"] for node in projection["nodes"]].count("merge") == 1
    assert projection["edges"] == [
        {"from": "merge", "to": "tail"},
        {"from": "root-a", "to": "merge"},
        {"from": "root-b", "to": "merge"},
    ]


def test_dag_projection_defaults_to_three_layers_and_expands_one_boundary_at_a_time():
    nodes = [
        {"key": "one", "status": "todo"},
        {"key": "two", "status": "todo", "blocked_by": ["one"]},
        {"key": "three", "status": "todo", "blocked_by": ["two"]},
        {"key": "four", "status": "todo", "blocked_by": ["three"]},
        {"key": "five", "status": "todo", "blocked_by": ["four"]},
    ]

    initial = _project_dag(nodes)
    assert [node["key"] for node in initial["nodes"]] == ["one", "two", "three"]
    assert initial["aggregates"] == [{
        "source": "three", "hidden_count": 2,
        "status_summary": [{"status": "todo", "count": 2}],
    }]

    expanded = _project_dag(nodes, {"expanded": ["three"]})
    assert [node["key"] for node in expanded["nodes"]] == ["one", "two", "three", "four"]
    assert expanded["aggregates"] == [{
        "source": "four", "hidden_count": 1,
        "status_summary": [{"status": "todo", "count": 1}],
    }]
    assert _dag_module_value("context.OMACDag.collapseBranches(['three'])") == []


@pytest.mark.parametrize("important_state", [
    "in_progress", "ci_check", "in_review", "merging", "failed", "blocked",
])
def test_dag_projection_reveals_every_important_state_with_full_ancestor_path(important_state):
    nodes = [
        {"key": "root", "status": "todo"},
        {"key": "middle", "status": "todo", "blocked_by": ["root"]},
        {"key": "deep", "status": "todo", "blocked_by": ["middle"]},
        {"key": "important", "status": important_state, "blocked_by": ["deep"]},
    ]

    projection = _project_dag(nodes)

    assert [node["key"] for node in projection["nodes"]] == [
        "root", "middle", "deep", "important",
    ]


def test_dag_projection_reports_hidden_count_and_status_summary_per_boundary():
    nodes = [
        {"key": "root", "status": "todo"},
        {"key": "middle", "status": "todo", "blocked_by": ["root"]},
        {"key": "boundary", "status": "todo", "blocked_by": ["middle"]},
        {"key": "done-child", "status": "done", "blocked_by": ["boundary"]},
        {"key": "todo-child", "status": "todo", "blocked_by": ["boundary"]},
    ]

    projection = _project_dag(nodes)

    assert projection["aggregates"] == [{
        "source": "boundary", "hidden_count": 2,
        "status_summary": [
            {"status": "done", "count": 1},
            {"status": "todo", "count": 1},
        ],
    }]


def test_dag_projection_supports_active_and_anomaly_focus_without_losing_paths():
    nodes = [
        {"key": "root", "status": "todo"},
        {"key": "active", "status": "in_progress", "blocked_by": ["root"]},
        {"key": "failure", "status": "failed", "blocked_by": ["root"]},
        {"key": "ordinary", "status": "todo", "blocked_by": ["root"]},
    ]

    active = _project_dag(nodes, {"focus": "active"})
    anomaly = _project_dag(nodes, {"focus": "anomaly"})

    assert [node["key"] for node in active["nodes"]] == ["root", "active"]
    assert [node["key"] for node in anomaly["nodes"]] == ["root", "failure"]


def test_dag_projection_state_presentation_covers_known_states_and_unknown_fallback():
    presentation = _dag_module_value("context.OMACDag.statePresentation")

    for status in [
        "todo", "in_progress", "ci_check", "in_review", "merging", "done",
        "failed", "blocked", "abandoned",
    ]:
        assert presentation[status]["icon"]
        assert presentation[status]["marker"]
    assert presentation["failed"]["strong_border"] is True

    unknown = _dag_module_value("context.OMACDag.presentState('vendor_pending')")
    assert unknown == {
        "icon": "?", "marker": "unknown", "strong_border": False,
        "safe_fallback": True, "status": "vendor_pending",
    }


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


@pytest.mark.parametrize("asset,expected_type,needle", [
    ("app.css", "text/css", ":root"),
    ("app.js", "text/javascript", "loadManifests"),
])
def test_same_origin_spa_assets_return_200(
        orch, simple_manifest, monkeypatch, asset, expected_type, needle):
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body, ctype = s.get(f"/static/{asset}", with_headers=True)
    assert status == 200
    assert expected_type in ctype
    assert needle in body


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

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


def _layout_dag(nodes, options=None):
    """运行浏览器同源模块中的布局函数，验证实际 SVG 几何输入。"""
    module = _static_dir().joinpath("dag-projection.js")
    program = """
const fs = require('fs');
const vm = require('vm');
const context = {};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context,
  { filename: process.argv[1] });
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dag = context.OMACDag;
const projection = dag.projectDag(input.nodes, input.options || {});
process.stdout.write(JSON.stringify(dag.layoutDag(projection)));
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


def _run_app_dom_scenario(scenario: str):
    """在最小 DOM 中运行实际 app.js，验证交互状态而不是源码文本。"""
    app = _static_dir().joinpath("app.js")
    projection = _static_dir().joinpath("dag-projection.js")
    program = r"""
const fs = require("fs");
const vm = require("vm");

class ClassList {
  constructor(){ this.values = new Set(); }
  add(...names){ names.forEach(name => this.values.add(name)); }
  remove(...names){ names.forEach(name => this.values.delete(name)); }
  contains(name){ return this.values.has(name); }
  toggle(name, force){
    const enabled = force === undefined ? !this.values.has(name) : !!force;
    if(enabled) this.values.add(name); else this.values.delete(name);
    return enabled;
  }
  set(value){ this.values = new Set(String(value).split(/\s+/).filter(Boolean)); }
}

class Element {
  constructor(document, id){
    this.ownerDocument = document; this.id = id || ""; this.children = [];
    this.dataset = {}; this.classList = new ClassList(); this.attributes = {};
    this.listeners = {}; this.value = ""; this.textContent = ""; this.title = "";
    this._innerHTML = ""; this._outerHTML = "";
  }
  get firstChild(){ return this.children[0] || null; }
  get innerHTML(){ return this._innerHTML; }
  set innerHTML(value){
    this._innerHTML = String(value); this.children = [];
    this.ownerDocument.registerMarkup(this, this._innerHTML);
  }
  get outerHTML(){ return this._outerHTML || this._innerHTML; }
  set outerHTML(value){
    this._outerHTML = String(value); this.ownerDocument.outerHTMLWrites.push(this._outerHTML);
  }
  appendChild(child){ child.parentNode = this; this.children.push(child); return child; }
  removeChild(child){ this.children.splice(this.children.indexOf(child), 1); return child; }
  setAttribute(name, value){
    const text = String(value); this.attributes[name] = text;
    if(name === "class") this.classList.set(text);
    if(name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = text;
  }
  addEventListener(name, handler){ (this.listeners[name] = this.listeners[name] || []).push(handler); }
  querySelectorAll(selector){ return this.ownerDocument.querySelectorAllWithin(this, selector); }
}

class Document {
  constructor(){ this.elements = {}; this.documentElement = new Element(this, "html"); this.title = ""; this.outerHTMLWrites = []; }
  make(id){ const element = new Element(this, id); this.elements[id] = element; return element; }
  getElementById(id){ return this.elements[id] || this.make(id); }
  createElementNS(){ return new Element(this, ""); }
  registerMarkup(parent, markup){
    for(const match of String(markup).matchAll(/\bid=["']([^"']+)["']/g)){
      const element = this.make(match[1]); element.parentNode = parent;
    }
  }
  querySelectorAll(selector){ return this.querySelectorAllWithin(null, selector); }
  querySelectorAllWithin(root, selector){
    if(selector.includes("[data-i18n]") || selector.includes("[data-copy]") || selector.includes("footer.tabs")) return [];
    const matches = selector.split(",").map(item => item.trim());
    const nodes = [];
    const visit = node => { node.children.forEach(child => { nodes.push(child); visit(child); }); };
    if(root) visit(root); else Object.values(this.elements).forEach(node => nodes.push(node));
    return [...new Set(nodes)].filter(node => matches.some(match =>
      match.startsWith(".") && node.classList.contains(match.slice(1))));
  }
}

const document = new Document();
[
  "theme-select", "toast", "manifest-selector", "collapse-btn", "expand-all-btn",
  "focus-active-btn", "focus-anomaly-btn", "fit-btn", "dag-canvas", "dag-legend",
  "detail-empty", "detail-content", "progress-badge", "poll-ts", "tick-state",
  "anomaly-empty", "anomaly-content", "reload-static", "tab-static", "static-content",
].forEach(id => document.make(id));
const storage = new Map();
const context = {
  console, document, localStorage:{getItem:key => storage.get(key) || null, setItem:(key, value) => storage.set(key, String(value))},
  navigator:{clipboard:{writeText:async () => {}}}, setInterval:() => 1, clearInterval:() => {},
  setTimeout:() => 1, clearTimeout:() => {}, getComputedStyle:() => ({getPropertyValue:() => "#888"}),
  matchMedia:() => ({matches:false, addEventListener:() => {}}),
};
context.window = context; context.globalThis = context;
const jsonResponse = value => ({ok:true, headers:{get:name => name === "content-type" ? "application/json" : null}, json:async () => value, text:async () => JSON.stringify(value)});
const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return {promise, resolve, reject};
};
let fetchHandler = path => {
  if(path === "/api/meta") return jsonResponse({refresh:10});
  if(path === "/api/config") return jsonResponse({language:"en"});
  if(path === "/api/manifests") return jsonResponse([]);
  throw new Error("unexpected request: "+path);
};
context.fetch = path => fetchHandler(path);
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context, {filename:process.argv[2]});
let source = fs.readFileSync(process.argv[1], "utf8");
const marker = "init();\n})();";
if(!source.includes(marker)) throw new Error("app bootstrap marker missing");
source = source.replace(marker, "globalThis.__omacApp = {state, selectManifest, selectNode};\ninit();\n})();");
vm.runInNewContext(source, context, {filename:process.argv[1]});
const flush = async (rounds = 1) => { for(let index = 0; index < rounds; index++) await Promise.resolve(); };
(async () => {
  await flush(8);
  const scenario = __SCENARIO__;
  const result = await scenario({
    app:context.__omacApp, document, elements:document.elements, setFetchHandler:handler => { fetchHandler = handler; },
    jsonResponse, deferred, flush,
  });
  process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
""".replace("__SCENARIO__", scenario)
    completed = subprocess.run(
        ["node", "-e", program, str(app), str(projection)],
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


def test_aggregate_hidden_label_has_english_and_chinese_translations():
    """聚合控件不能把 hidden 固定为英文，语言切换必须覆盖该标签。"""
    js = _read_asset("app.js")

    assert 'hidden:"hidden"' in js
    assert 'hidden:"已隐藏"' in js
    assert 'aggregate.hidden_count+" "+copy("hidden")' in js


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
        "source": "three", "hidden_count": 1,
        "status_summary": [{"status": "todo", "count": 1}],
    }]

    expanded = _project_dag(nodes, {"expanded": ["three"]})
    assert [node["key"] for node in expanded["nodes"]] == ["one", "two", "three", "four"]
    assert expanded["aggregates"] == [{
        "source": "four", "hidden_count": 1,
        "status_summary": [{"status": "todo", "count": 1}],
    }]
    assert _dag_module_value("context.OMACDag.collapseBranches(['three'])") == []


def test_dag_projection_aggregate_describes_its_complete_reveal_closure():
    """聚合摘要必须包含点击时补入的跨分支依赖祖先，且不预报更深后代。"""
    nodes = [
        {"key": "root", "status": "todo"},
        {"key": "left", "status": "todo", "blocked_by": ["root"]},
        {"key": "boundary", "status": "todo", "blocked_by": ["left"]},
        {"key": "other-1", "status": "todo", "blocked_by": ["root"]},
        {"key": "other-2", "status": "todo", "blocked_by": ["other-1"]},
        {"key": "other-parent", "status": "abandoned", "blocked_by": ["other-2"]},
        {
            "key": "revealed-child", "status": "done",
            "blocked_by": ["boundary", "other-parent"],
        },
        {"key": "later-child", "status": "todo", "blocked_by": ["revealed-child"]},
    ]

    initial = _project_dag(nodes)
    aggregate = next(item for item in initial["aggregates"] if item["source"] == "boundary")
    expanded = _project_dag(nodes, {"expanded": ["boundary"]})

    assert aggregate == {
        "source": "boundary",
        "hidden_count": 2,
        "status_summary": [
            {"status": "abandoned", "count": 1},
            {"status": "done", "count": 1},
        ],
    }
    assert {node["key"] for node in expanded["nodes"]} - {
        node["key"] for node in initial["nodes"]
    } == {"other-parent", "revealed-child"}


def test_dag_projection_fails_closed_for_unknown_dependencies_and_cycles():
    """无效图不能被伪装为可布局 DAG，前端必须得到明确只读错误。"""
    unknown = _project_dag([
        {"key": "root", "status": "todo"},
        {"key": "child", "status": "todo", "blocked_by": ["missing", "root"]},
    ])
    cycle = _project_dag([
        {"key": "alpha", "status": "todo", "blocked_by": ["charlie"]},
        {"key": "bravo", "status": "todo", "blocked_by": ["alpha"]},
        {"key": "charlie", "status": "todo", "blocked_by": ["bravo"]},
    ])
    js = _read_asset("app.js")
    css = _read_asset("app.css")

    assert unknown == {
        "depths": {}, "nodes": [], "edges": [], "aggregates": [],
        "error": {
            "code": "unknown_dependency",
            "dependencies": [{"node": "child", "dependency": "missing"}],
        },
    }
    assert cycle == {
        "depths": {}, "nodes": [], "edges": [], "aggregates": [],
        "error": {"code": "cycle", "nodes": ["alpha", "bravo", "charlie"]},
    }
    assert "renderDagError" in js
    assert "projection.error" in js
    assert ".dag-error" in css


def test_spa_resets_dag_view_state_when_switching_manifests():
    """切换 manifest 不得继承上一个图的展开、聚焦、选中或详情状态。"""
    js = _read_asset("app.js")

    assert "function resetDagView()" in js
    assert "state.expanded = [];" in js
    assert 'state.focus = "default";' in js
    assert "state.selected = null;" in js
    assert "clearNodeDetail();" in js
    assert re.search(
        r"async function selectManifest\(path\)\{.*?"
        r"const isNewManifest = state\.current !== path;.*?"
        r"if\(isNewManifest\)\{.*?resetDagView\(\);.*?\}.*?"
        r"state\.current = path;",
        js,
        re.DOTALL,
    )


def test_spa_closes_selection_and_detail_after_every_dag_redraw():
    """redraw 后仅保留仍可见的选择；异步旧详情也不得回写。"""
    js = _read_asset("app.js")

    assert "function syncSelectedProjection(projection)" in js
    assert "const visibleKeys = new Set(projection.nodes.map(node => node.key));" in js
    assert "clearSelection();" in js
    assert "dimForSelected();" in js
    assert re.search(
        r"projection\.aggregates\.forEach\(aggregate => \{.*?\}\);\s*"
        r"syncSelectedProjection\(projection\);",
        js,
        re.DOTALL,
    )


def test_manifest_switch_clears_old_dom_surfaces_before_the_new_request_fails():
    """新 manifest 请求失败时，旧 SVG、面板、进度和详情也必须保持清空。"""
    result = _run_app_dom_scenario(r"""
async ({app, document, elements, setFetchHandler, jsonResponse, deferred, flush}) => {
  const status = {
    nodes:[{key:"task", status:"failed", blocked_by:[]}],
    progress:{done:0, total:1},
    needs_decision:{failed_nodes:[{key:"task", status:"failed", reason:"old anomaly"}]},
  };
  let pending;
  setFetchHandler(path => {
    if(path.includes("/api/dag/status?manifest=one")) return jsonResponse(status);
    if(path.includes("/api/node/task?manifest=one")) return jsonResponse({
      contract:{}, evidence:null, rollback_count:0, comments:"old detail",
    });
    if(path.includes("/api/dag/status?manifest=two")) {
      pending = deferred(); return pending.promise;
    }
    throw new Error("unexpected request: "+path);
  });
  await app.selectManifest("one");
  app.selectNode("task");
  await flush(8);
  elements["tick-state"].textContent = "old tick";
  const before = {
    svg:elements["dag-canvas"].children.length,
    legend:elements["dag-legend"].innerHTML,
    anomaly:elements["anomaly-content"].innerHTML,
    progress:elements["progress-badge"].textContent,
    detail:elements["detail-content"].innerHTML,
  };
  const request = app.selectManifest("two");
  await flush();
  const immediate = {
    svg:elements["dag-canvas"].children.length,
    legend:elements["dag-legend"].innerHTML,
    anomaly:elements["anomaly-content"].innerHTML,
    anomalyEmpty:elements["anomaly-empty"].classList.contains("is-hidden"),
    progressVisible:!elements["progress-badge"].classList.contains("is-hidden"),
    progress:elements["progress-badge"].textContent,
    poll:elements["poll-ts"].textContent,
    tick:elements["tick-state"].textContent,
    detail:elements["detail-content"].innerHTML,
    detailEmpty:elements["detail-empty"].classList.contains("is-hidden"),
    statusIsNull:app.state.status === null,
    nodeCount:Object.keys(app.state.nodes).length,
    selected:app.state.selected,
  };
  pending.reject(new Error("two unavailable"));
  await request;
  await flush(4);
  return {before, immediate, afterFailure:{
    svg:elements["dag-canvas"].children.length,
    legend:elements["dag-legend"].innerHTML,
    anomaly:elements["anomaly-content"].innerHTML,
    progressVisible:!elements["progress-badge"].classList.contains("is-hidden"),
    detail:elements["detail-content"].innerHTML,
    statusIsNull:app.state.status === null,
  }};
}
""")

    assert result["before"]["svg"] > 0
    assert result["before"]["legend"]
    assert result["before"]["anomaly"]
    assert result["before"]["progress"]
    assert result["before"]["detail"]
    assert result["immediate"] == {
        "svg": 0, "legend": "", "anomaly": "", "anomalyEmpty": False,
        "progressVisible": False, "progress": "", "poll": "—", "tick": "—",
        "detail": "", "detailEmpty": False, "statusIsNull": True,
        "nodeCount": 0, "selected": None,
    }
    assert result["afterFailure"] == {
        "svg": 0, "legend": "", "anomaly": "", "progressVisible": False,
        "detail": "", "statusIsNull": True,
    }


def test_newest_detail_request_wins_after_same_node_is_deselected_and_reselected():
    """同节点重选后，先发出的详情响应不得覆盖最后一次选择的 DOM。"""
    result = _run_app_dom_scenario(r"""
async ({app, elements, setFetchHandler, jsonResponse, deferred, flush}) => {
  const requests = [];
  setFetchHandler(path => {
    if(path.includes("/api/dag/status?manifest=one")) return jsonResponse({
      nodes:[{key:"task", status:"todo", blocked_by:[]}], progress:{done:0, total:1},
    });
    if(path.includes("/api/node/task?manifest=one")) {
      const request = deferred(); requests.push(request); return request.promise;
    }
    throw new Error("unexpected request: "+path);
  });
  await app.selectManifest("one");
  app.selectNode("task");
  await flush(2);
  app.selectNode("task");
  app.selectNode("task");
  await flush(2);
  requests[1].resolve(jsonResponse({
    contract:{objective:"newest detail"}, evidence:null, rollback_count:0, comments:"",
  }));
  await flush(8);
  requests[0].resolve(jsonResponse({
    contract:{objective:"stale detail"}, evidence:null, rollback_count:0, comments:"",
  }));
  await flush(8);
  return {selected:app.state.selected, detailWrites:document.outerHTMLWrites};
}
""")

    assert result["selected"] == "task"
    assert "newest detail" in result["detailWrites"][-1]
    assert "stale detail" not in result["detailWrites"][-1]


def test_spacer_layout_is_shared_by_header_toolbar_and_footer():
    """三处布局均使用 .spacer，样式不能只限定在 header。"""
    css = _read_asset("app.css")

    assert re.search(r"\.spacer\s*\{[^}]*flex\s*:\s*1", css)
    assert "header .spacer" not in css


def test_dag_projection_manual_expansion_keeps_multi_parent_ancestor_closure():
    """手动露出一个多父 child 时，所有真实入边的祖先也必须同时露出。"""
    nodes = [
        {"key": "root", "status": "todo"},
        {"key": "left", "status": "todo", "blocked_by": ["root"]},
        {"key": "boundary", "status": "todo", "blocked_by": ["left"]},
        {"key": "other-1", "status": "todo", "blocked_by": ["root"]},
        {"key": "other-2", "status": "todo", "blocked_by": ["other-1"]},
        {"key": "other-parent", "status": "todo", "blocked_by": ["other-2"]},
        {
            "key": "manual-child", "status": "todo",
            "blocked_by": ["boundary", "other-parent"],
        },
    ]

    projection = _project_dag(nodes, {"expanded": ["boundary"]})

    assert [node["key"] for node in projection["nodes"]] == [
        "root", "left", "other-1", "boundary", "other-2", "other-parent",
        "manual-child",
    ]
    assert projection["edges"] == [
        {"from": "boundary", "to": "manual-child"},
        {"from": "left", "to": "boundary"},
        {"from": "other-1", "to": "other-2"},
        {"from": "other-2", "to": "other-parent"},
        {"from": "other-parent", "to": "manual-child"},
        {"from": "root", "to": "left"},
        {"from": "root", "to": "other-1"},
    ]


def test_dag_layout_places_aggregates_in_separate_non_overlapping_slots():
    """聚合控件必须避开自动可见的重要节点，也不能彼此抢占点击区域。"""
    nodes = [
        {"key": "root", "status": "todo"},
        {"key": "middle", "status": "todo", "blocked_by": ["root"]},
        {"key": "boundary", "status": "todo", "blocked_by": ["middle"]},
        {"key": "important", "status": "in_progress", "blocked_by": ["boundary"]},
        {"key": "hidden", "status": "todo", "blocked_by": ["boundary"]},
        {"key": "right-middle", "status": "todo", "blocked_by": ["root"]},
        {"key": "right-boundary", "status": "todo", "blocked_by": ["right-middle"]},
        {"key": "right-hidden", "status": "todo", "blocked_by": ["right-boundary"]},
    ]

    layout = _layout_dag(nodes)
    aggregate_positions = layout["aggregatePositions"]
    important = layout["pos"]["important"]
    boundary = aggregate_positions["boundary"]
    right_boundary = aggregate_positions["right-boundary"]

    assert boundary["y"] >= important["y"] + layout["nodeHeight"]
    assert boundary != right_boundary
    assert boundary["x"] + layout["aggregateWidth"] <= right_boundary["x"]


def test_dag_layout_width_uses_projected_depth_for_shallow_focuses():
    """深层普通分支不能把 active/anomaly 聚焦视图的 SVG 变宽。"""
    nodes = [
        {"key": "root", "status": "todo"},
        {"key": "active", "status": "in_progress", "blocked_by": ["root"]},
        {"key": "failure", "status": "failed", "blocked_by": ["root"]},
        {"key": "deep-1", "status": "todo", "blocked_by": ["root"]},
        {"key": "deep-2", "status": "todo", "blocked_by": ["deep-1"]},
        {"key": "deep-3", "status": "todo", "blocked_by": ["deep-2"]},
        {"key": "deep-4", "status": "todo", "blocked_by": ["deep-3"]},
        {"key": "deep-5", "status": "todo", "blocked_by": ["deep-4"]},
    ]

    active = _layout_dag(nodes, {"focus": "active"})
    anomaly = _layout_dag(nodes, {"focus": "anomaly"})

    expected_width = active["padX"] * 2 + 2 * active["colW"]
    assert active["W"] == expected_width
    assert anomaly["W"] == expected_width


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

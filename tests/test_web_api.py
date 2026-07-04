"""P5.1 web 服务端 —— API 层 + 一致性纪律 + 缓存与安全。

三层测试:
1. 一致性测试(mock):逐端点断言 HTTP 响应 == CLI 同命令 --output json 输出。
2. 缓存命中测试:TTL 窗口内多次请求 dag/status 命中缓存,实际命令调用只触发一次;
   TTL 过期后再次触发。
3. 安全测试:无 token 对外 bind → exit 5;错 token → 401;缺 token → 401;正确 token → 200。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

import omac.web.server as web_srv
import omac.web.api as web_api
from omac.cli import exit_codes
from omac.cli.main import main as cli_main


# ==================== fixtures ====================

@pytest.fixture
def orch(tmp_path):
    """.orchestrator 工作目录(mock 引擎)。poll_interval=2 方便 TTL 测试过窗口。"""
    d = tmp_path / ".orchestrator"
    d.mkdir()
    with open(d / "config.yaml", "w") as f:
        yaml.dump({
            "engine": "mock", "workspace": "ws",
            "defaults": {"poll_interval": 2, "max_parallel": 2},
            "roles": {"workers": ["alice", "bob"]},
        }, f)
    return d


def _write_manifest(dir_path: Path, name: str, nodes: list[dict]) -> Path:
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
    ])


@pytest.fixture
def acceptance_doc(orch):
    acc = orch / "demo.acceptance.yaml"
    with open(acc, "w") as f:
        yaml.dump({"flows": [
            {"id": "f1", "name": "主干流程",
             "actions": [{"step": "触发", "how": "curl", "expected": "200"}]},
        ]}, f)
    return acc


# ==================== HTTP 测试工具 ====================

class _Server:
    """在 127.0.0.1:0 启动真实 WebServer 后台线程。"""

    def __init__(self, *, token=None, refresh=2, poll_interval=None,
                 orch_subpath=".orchestrator"):
        self.token = token
        self.refresh = refresh
        self.poll_interval = poll_interval
        self.orch_subpath = Path(orch_subpath)
        self._httpd = None
        self._thread = None

    def __enter__(self):
        self._httpd = web_srv.ThreadingHTTPServer(
            ("127.0.0.1", 0), web_srv._Handler)
        self.host, self.port = self._httpd.server_address
        web_srv._Handler.token = self.token
        web_srv._Handler.refresh = self.refresh
        web_srv._Handler.orchestrator_dir = self.orch_subpath
        self._httpd.RequestHandlerClass = web_srv._Handler  # 确保类型
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

    def get(self, path: str, with_headers: bool = False):
        url = f"http://{self.host}:{self.port}{path}"
        req = urllib.request.Request(url, method="GET")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8")
                if with_headers:
                    return resp.status, body, ctype
                return resp.status, body
        except urllib.error.HTTPError as e:
            ctype = e.headers.get("Content-Type", "") if e.headers else ""
            body = e.read().decode("utf-8")
            if with_headers:
                return e.code, body, ctype
            return e.code, body


def _cli_json(args, cwd):
    """在 cwd 下执行 CLI 命令并解析 stdout JSON。"""
    buf_out = io.StringIO()
    with contextlib.redirect_stdout(buf_out):
        code = cli_main(args)
    return code, json.loads(buf_out.getvalue())


# ==================== 一致性测试 ====================

def test_manifests_lists_yaml_and_progress(orch, simple_manifest, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get("/api/manifests")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        names = {m["name"] for m in data}
        assert "demo" in names
        assert "config" not in names
        demo = next(m for m in data if m["name"] == "demo")
        assert demo["total"] == 2
        assert demo["done"] == 0


def test_config_endpoint_equals_cli(orch, monkeypatch):
    monkeypatch.chdir(orch.parent)
    code, cli_data = _cli_json(["config", "get", "--output", "json"], cwd=None)
    assert code == exit_codes.OK
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get("/api/config")
        assert status == 200
        api_data = json.loads(body)
    assert api_data == cli_data


def test_dag_status_endpoint_equals_cli(orch, simple_manifest, monkeypatch):
    monkeypatch.chdir(orch.parent)
    code, cli_data = _cli_json(
        ["dag", "status", str(simple_manifest), "--output", "json"], cwd=None)
    assert code == exit_codes.OK
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get(f"/api/dag/status?manifest={simple_manifest}")
        assert status == 200
        api_data = json.loads(body)
    assert api_data == cli_data
    assert set(api_data.keys()) == {"manifest", "progress", "nodes", "needs_decision"}
    assert api_data["progress"]["total"] == 2
    assert api_data["nodes"][0]["key"] == "a"


def test_node_show_endpoint_equals_cli(orch, simple_manifest, monkeypatch):
    monkeypatch.chdir(orch.parent)
    code, cli_data = _cli_json(
        ["node", "show", str(simple_manifest), "b", "--output", "json"], cwd=None)
    assert code == exit_codes.OK
    assert cli_data["node_key"] == "b"
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get(f"/api/node/b?manifest={simple_manifest}")
        assert status == 200
        api_data = json.loads(body)
    assert api_data == cli_data
    assert api_data["node_key"] == "b"
    assert "contract" in api_data


def test_plan_acceptance_endpoint(orch, simple_manifest, acceptance_doc, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get(f"/api/plan/acceptance?manifest={simple_manifest}")
        assert status == 200
        data = json.loads(body)
        assert data["_meta"]["found"] is True
        assert len(data["flows"]) == 1
        assert data["flows"][0]["id"] == "f1"


def test_plan_acceptance_missing_returns_empty_flows(orch, simple_manifest, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get(
            f"/api/plan/acceptance?manifest={orch / 'ghost.yaml'}".replace("\\", "/"))
        assert status == 200
        data = json.loads(body)
        assert data["_meta"]["found"] is False
        assert data["flows"] == []


# ==================== 缓存命中测试 ====================

def test_status_cache_counts_compute_calls(orch, simple_manifest, monkeypatch):
    """TTL 窗口内多次请求 -> compute 只触发一次,过期后再次触发。"""
    monkeypatch.chdir(orch.parent)
    # 计数:替代 api.dag_status 并计数调用次数
    compute_calls = {"n": 0}
    real_dag_status = web_srv.api.dag_status

    def counting(path):
        compute_calls["n"] += 1
        return real_dag_status(path)

    monkeypatch.setattr(web_srv.api, "dag_status", counting)

    # TTL 很小,便于测试过期。我们直接用 StatusCache 单独断言。
    web_api.reset_status_cache()
    cache = web_srv.api.get_status_cache(ttl=1)  # 1s TTL
    cache.invalidate()

    v1, hit1 = cache.get_or_compute(str(simple_manifest),
                                    lambda: counting(str(simple_manifest)))
    assert hit1 is False
    assert compute_calls["n"] == 1

    v2, hit2 = cache.get_or_compute(str(simple_manifest), lambda: counting("x"))
    assert hit2 is True  # 命中缓存,未触发 compute
    assert compute_calls["n"] == 1
    assert v2 == v1

    # 等 TTL 过期:把 _now 拨到超过 expires。
    import omac.web.api as api_mod

    # 记录第一次 compute 后的 expires 边界,确保 fake now 超过它。
    entry = cache._store[str(Path(simple_manifest).resolve())]
    boundary = entry[1]  # expires

    fake_state = {"now": boundary + 10.0}
    monkeypatch.setattr(api_mod, "_now", lambda: fake_state["now"])
    v3, hit3 = cache.get_or_compute(str(simple_manifest),
                                    lambda: counting(str(simple_manifest)))
    assert hit3 is False
    assert compute_calls["n"] == 2


def test_status_cache_through_http(orch, simple_manifest, monkeypatch):
    """真实 HTTP 请求路径:TTL 内第一次触发命令,第二次命中缓存。"""
    monkeypatch.chdir(orch.parent)
    invocations = {"n": 0}
    real = web_srv.api.dag_status

    def counting(path):
        invocations["n"] += 1
        return real(path)

    monkeypatch.setattr(web_srv.api, "dag_status", counting)

    with _Server(orch_subpath=str(orch), poll_interval=1) as s:
        st1, b1 = s.get(f"/api/dag/status?manifest={simple_manifest}")
        assert st1 == 200
        st2, b2 = s.get(f"/api/dag/status?manifest={simple_manifest}")
        assert st2 == 200
        # 两次都 200 且返回同样内容;第二次命中缓存 => 命令只调用一次。
        assert json.loads(b1) == json.loads(b2)
    # 关闭连接后检查调用次数。
    assert invocations["n"] == 1


# ==================== 安全测试 ====================

def test_nonlocal_host_without_token_exits_5(tmp_path, monkeypatch):
    """CLI 层:对外暴露且无 token → exit 5(校验失败)。"""
    monkeypatch.chdir(tmp_path)
    code = cli_main(["web", "--host", "0.0.0.0"])
    assert code == exit_codes.VALIDATION


def test_wrong_token_returns_401(orch, simple_manifest, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(token="right", orch_subpath=str(orch)) as s:
        # 临时取消构造 req 中的 token:会用 Bearer 错误的值。
        # 直接构造请求。
        url = f"http://{s.host}:{s.port}/api/dag/status?manifest={simple_manifest}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer wrong-token")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 401


def test_missing_token_returns_401(orch, simple_manifest, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(token="right", orch_subpath=str(orch)) as s:
        url = f"http://{s.host}:{s.port}/api/dag/status?manifest={simple_manifest}"
        req = urllib.request.Request(url)  # 不带 Authorization
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 401


def test_correct_token_returns_200(orch, simple_manifest, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(token="right", orch_subpath=str(orch)) as s:
        # Server.get 在初始化时已传 token。
        st, body = s.get(f"/api/dag/status?manifest={simple_manifest}")
        assert st == 200
        data = json.loads(body)
        assert "progress" in data


def test_local_bind_without_token_works(orch, simple_manifest, monkeypatch):
    """默认 127.0.0.1 无 token → 正常工作。"""
    monkeypatch.chdir(orch.parent)
    # CLI 不应因此退出。
    from omac.web.server import require_token_if_exposed
    # 不应抛
    require_token_if_exposed("127.0.0.1", None)

    with _Server(token=None, orch_subpath=str(orch)) as s:
        st, body = s.get(f"/api/dag/status?manifest={simple_manifest}")
        assert st == 200


def test_unknown_endpoint_returns_404(orch, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body = s.get("/api/does-not-exist")
        assert status == 404


def test_non_get_method_returns_405(orch, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        url = f"http://{s.host}:{s.port}/api/config"
        req = urllib.request.Request(url, method="POST", data=b"")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 405


def test_root_serves_html(orch, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(orch_subpath=str(orch)) as s:
        status, body, ctype = s.get("/", with_headers=True)
        assert status == 200
        assert ctype == "text/html; charset=utf-8"
        assert "<!doctype html>" in body.lower().replace("\n", " ")


def test_meta_endpoint(orch, monkeypatch):
    monkeypatch.chdir(orch.parent)
    with _Server(refresh=7, orch_subpath=str(orch)) as s:
        status, body = s.get("/api/meta")
        assert status == 200
        data = json.loads(body)
        assert data["refresh"] == 7

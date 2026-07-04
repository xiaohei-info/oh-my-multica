"""Web 路由层:只做「解析参数 → 调命令函数 → 原样返回 JSON」(设计文档 §13.2)。

纪律:
- 绝不直接读 manifest / 调 engine / 二次加工数据 —— 那些都是命令层的职责。
- 每个 API 端点与一条 CLI 命令一一对应,响应体就是该命令 --output json 的原样输出。
- 一致性测试手段:每个端点通过 _run_cli 调用真正的命令函数并捕获 stdout ——
  所以 API 的字节流天然等于 ``omac <cmd> --output json`` 的 stdout。
"""
from __future__ import annotations

import contextlib
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import yaml

from omac.core import config as config_mod
from omac.errors import OmacError

# 单例 TTL 缓存,整个 web 进程共享,多请求复用 dag status 的 reconcile。
_status_cache = None



def _cmd():
    from omac.cli import commands as c
    return c


def _exit_codes():
    from omac.cli import exit_codes as e
    return e


def _now() -> float:
    """单调时钟,可测试覆盖以驱动 TTL。"""
    return time.monotonic()


def _poll_interval(cfg: dict | None = None) -> int:
    cfg = cfg if cfg is not None else config_mod.load_config(config_mod.CONFIG_PATH)
    return int(cfg.get("defaults", {}).get(
        "poll_interval", config_mod.DEFAULTS["poll_interval"]))


def get_status_cache(ttl: int | None = None, cfg: dict | None = None) -> "StatusCache":
    """获取/构造进程级的 dag status TTL 缓存。

    ttl 用 poll_interval 秒(前端轮询间隔内共享同一次 reconcile)。
    """
    global _status_cache
    if _status_cache is None:
        _status_cache = StatusCache(ttl=ttl, cfg=cfg)
    return _status_cache


def reset_status_cache() -> None:
    """测试辅助:清缓存并重建。"""
    global _status_cache
    _status_cache = None


def _ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def _run_cli(run_fn: Callable, args) -> str:
    """执行命令函数并捕获其 stdout,返回原样文本。

    命令若 raise OmacError 则向上抛,由 server 层转为 HTTP 状态码。
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = run_fn(args)
    except SystemExit as e:
        raise OmacError(f"命令异常退出: {e}")
    if rc not in (_exit_codes().OK, None):
        raise _CommandFailed(int(rc))
    return buf.getvalue()


class _CommandFailed(OmacError):
    """命令函数返回非零退出码(视为失败)。"""

    def __init__(self, rc: int):
        super().__init__(f"命令返回退出码 {rc}")
        self.rc = rc


def _cli_json(run_fn: Callable, args) -> Any:
    """执行命令并解析其 stdout JSON。"""
    text = _run_cli(run_fn, args)
    if not text.strip():
        raise OmacError("命令未输出数据")
    return json.loads(text)


# ==================== 端点(每个对应一条 CLI 命令) ====================


def get_manifests(orchestrator_dir: Path) -> list[dict]:
    """GET /api/manifests:扫 .orchestrator/*.yaml(排除 config),带进度摘要。"""
    if not orchestrator_dir.is_dir():
        return []
    results = []
    from omac.core.manifest import load_manifest
    for p in sorted(orchestrator_dir.glob("*.yaml")):
        if p.name == "config.yaml":
            continue
        try:
            m = load_manifest(str(p))
        except Exception:
            continue  # 解析失败的文件跳过,不阻塞其它 manifest
        total = len(m.nodes)
        done = sum(1 for n in m.nodes.values() if n.status == "done")
        abandoned = sum(1 for n in m.nodes.values() if n.status == "abandoned")
        results.append({
            "path": str(p),
            "name": p.stem,
            "total": total,
            "done": done,
            "abandoned": abandoned,
            "done_ratio": done / total if total else 0.0,
            "undone": total - done - abandoned,
        })
    return results


def get_config() -> Any:
    """GET /api/config ← config get --output json。"""
    args = _ns(action="get", key=None, output="json")
    return _cli_json(_cmd().config_cmd.run, args)


def dag_status(manifest_path: str) -> Any:
    """GET /api/dag/status?manifest= ← dag status --output json。"""
    args = _ns(manifest=manifest_path, output="json", engine=None, workspace=None)
    # 需要先确认 manifest 存在(命令层检查并 raise ValidationError → exit 5),
    # 这里直接调命令函数,让它处理路径不存在的情况。
    return _cli_json(_cmd().dag.status, args)


def node_show(manifest_path: str, node_key: str) -> Any:
    """GET /api/node/{key} ← node show --output json。"""
    args = _ns(action="show", manifest=manifest_path, node_key=node_key, output="json")
    return _cli_json(_cmd().node.run, args)


def get_plan_acceptance(orchestrator_dir: Path, manifest_path: str) -> Any:
    """GET /api/plan/acceptance:加载对应 <manifest-stem>.acceptance.yaml 并原样返回 JSON。

    对应设计文档 §13.3 静态信息页中的「验收文档逐条清单」。
    """
    acceptance = orchestrator_dir / (Path(manifest_path).stem + ".acceptance.yaml")
    meta = {"acceptance_file": str(acceptance)}
    if not acceptance.exists():
        meta["found"] = False
        return {"flows": [], "_meta": meta}
    with open(acceptance, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {"flows": []}
    meta["found"] = True
    if isinstance(data, dict):
        data.setdefault("_meta", {})
        data["_meta"].update(meta)
    return data


# ==================== TTL 缓存 ====================


class StatusCache:
    """dag status 的 TTL 缓存,key = manifest 绝对路径。

    多请求共享一次 reconcile,TTL = poll_interval(秒,缺省 30)。
    """

    def __init__(self, ttl: int | None = None, cfg: dict | None = None):
        self.ttl = ttl if ttl is not None else _poll_interval(cfg)
        self._store: dict[str, tuple[Any, float]] = {}

    def get_or_compute(self, manifest_path: str, compute: Callable[[], Any]) -> tuple[Any, bool]:
        """返回 (value, cache_hit)。cache_hit=True 表示未触发 compute。"""
        key = str(Path(manifest_path).resolve())
        now = _now()
        entry = self._store.get(key)
        if entry is not None:
            value, expires = entry
            if now < expires:
                return value, True
        value = compute()
        self._store[key] = (value, now + self.ttl)
        return value, False

    def invalidate(self, manifest_path: str | None = None) -> None:
        if manifest_path is None:
            self._store.clear()
            return
        key = str(Path(manifest_path).resolve())
        self._store.pop(key, None)

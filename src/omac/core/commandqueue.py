"""本机写命令队列(设计 §4.5):运行中的 `dag run` 持有 manifest 写锁时,人的写命令入队,由 runner 在 tick 边界应用。

只做存储与崩溃语义,不知道任何命令:应用函数由调用方注入。队列是本机运行时数据(与写锁同目录,不进 git):

- 请求 ``req-<纳秒时间戳>-<id>.json``:原子写入,按文件名排序即 FIFO;
- 回执 ``rcpt-<id>.json``:``applying``(意图,应用前先落盘)→ ``done``(终态)。

调用方必须持有 manifest 写锁再调用 ``drain``:应用仍由唯一写者完成,单写者不变。
崩溃语义(未知结果绝不重放):看到 ``applying`` 却没有终态的请求,说明上次应用到一半被中断,
结果未知——写 ``unknown`` 回执并丢弃请求,不重放;由人用 `omac node show` 核对后决定。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Callable

from .manifest import _atomic_write, local_runtime_file

_RECEIPT_TTL_SECONDS = 24 * 3600   # 命令侧没来取的回执(--no-wait / 命令被杀)一天后清理


def spool_dir(manifest_path: str) -> str:
    return local_runtime_file(manifest_path, "queue")[: -len(".json")]


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _receipt_path(manifest_path: str, request_id: str) -> str:
    return os.path.join(spool_dir(manifest_path), f"rcpt-{request_id}.json")


def enqueue(manifest_path: str, command: str, args: dict, cwd: str) -> str:
    request_id = uuid.uuid4().hex
    name = f"req-{time.time_ns():020d}-{request_id}.json"
    body = {"id": request_id, "command": command, "args": args, "cwd": cwd}
    _atomic_write(os.path.join(spool_dir(manifest_path), name), json.dumps(body, sort_keys=True).encode("utf-8"))
    return request_id


def pending(manifest_path: str) -> list[tuple[str, dict]]:
    """(请求文件路径, 请求) 的 FIFO 列表;损坏的请求文件被丢弃(原子写不会产生半截文件,损坏即外部篡改)。"""
    directory = spool_dir(manifest_path)
    try:
        names = sorted(n for n in os.listdir(directory) if n.startswith("req-") and n.endswith(".json"))
    except OSError:
        return []
    found = []
    for name in names:
        path = os.path.join(directory, name)
        request = _read(path)
        if isinstance(request, dict) and request.get("id"):
            found.append((path, request))
        else:
            _unlink(path)
    return found


def read_receipt(manifest_path: str, request_id: str):
    return _read(_receipt_path(manifest_path, request_id))


def consume_receipt(manifest_path: str, request_id: str) -> None:
    _unlink(_receipt_path(manifest_path, request_id))


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _write_receipt(manifest_path: str, request_id: str, state: str, result: dict | None = None) -> None:
    body = {"id": request_id, "state": state, "result": result or {}}
    _atomic_write(_receipt_path(manifest_path, request_id), json.dumps(body, sort_keys=True).encode("utf-8"))


def _prune_receipts(manifest_path: str) -> None:
    directory = spool_dir(manifest_path)
    try:
        names = os.listdir(directory)
    except OSError:
        return
    cutoff = time.time() - _RECEIPT_TTL_SECONDS
    for name in names:
        path = os.path.join(directory, name)
        try:
            if name.startswith("rcpt-") and os.stat(path).st_mtime < cutoff:
                _unlink(path)
        except OSError:
            pass


def drain(manifest_path: str, apply: Callable[[dict], dict]) -> int:
    """按 FIFO 应用所有待处理请求,返回本次实际调用 ``apply`` 的个数。调用方须持有 manifest 写锁。

    ``apply(request)`` 返回结果字典(写入回执);它自己负责把命令失败转成结果,这里不吞异常——
    ``applying`` 回执留在原地,下一次 drain 会把它判为 ``unknown`` 而不是重放。
    """
    applied = 0
    for path, request in pending(manifest_path):
        request_id = request["id"]
        receipt = read_receipt(manifest_path, request_id)
        if receipt and receipt.get("state") == "done":        # 回执已落盘、请求没来得及删:不重放
            _unlink(path)
            continue
        if receipt and receipt.get("state") == "applying":    # 上次应用中途被中断:结果未知,绝不重放
            _write_receipt(manifest_path, request_id, "done", {
                "exit_code": 1, "stdout": "", "stderr": "", "error": {
                    "class": "unknown",
                    "message": "outcome unknown: the runner stopped while applying this request; it was not replayed. "
                               "Check the node with `omac node show` and re-issue the command if needed.",
                    "report": {}}})
            _unlink(path)
            continue
        _write_receipt(manifest_path, request_id, "applying")
        result = apply(request)
        _write_receipt(manifest_path, request_id, "done", result)
        _unlink(path)
        applied += 1
    _prune_receipts(manifest_path)
    return applied

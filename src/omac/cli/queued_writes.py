"""写命令的受控通道(设计 §4.5):`node retry|abandon|accept|accept-nits|hold|release`、`defect report|grant|resolve`。

manifest 写锁空闲时,命令照旧直接应用。写锁被运行中的 `dag run` 持有时,命令不再失败,而是写入本机队列
(core/commandqueue.py),由 runner 在下一个 tick 边界、持同一把锁、调用同一个命令函数应用并写回执;命令等回执
并原样重放其 stdout / stderr / 退出码。写者始终只有一个(持锁者);前置条件在应用时由命令自己的判定表重新校验。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import time
from contextlib import ExitStack

from . import exit_codes
from .output import hint, print_json
from ..core import commandqueue, logsetup
from ..core.manifest import manifest_write_lock
from ..errors import ManifestLockedError, NeedsDecision, OmacError
from ..i18n import ui

log = logsetup.get_logger(__name__)

DEFAULT_WAIT_SECONDS = 600      # 命令等 runner 应用的上限(设计 §6 `--wait-timeout`)
_POLL_SECONDS = 0.2
_LOCK_RETRY_SECONDS = 1.0       # 等待期间每隔多久自己试一次写锁:runner 已退出时由命令自己应用


class QueuedWriteError(OmacError):
    """排队命令在 runner 侧失败:带回原退出码与消息。"""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def add_queue_flags(parser) -> None:
    parser.add_argument("--no-wait", action="store_true", help=ui(
        "If a `dag run` holds the manifest lock, queue the write and return at once (exit 0) instead of waiting for the "
        "runner to apply it at its next tick boundary",
        "`dag run` 持有 manifest 写锁时,写入队列后立即返回(exit 0),不等 runner 在下一个 tick 边界应用"))
    parser.add_argument("--wait-timeout", type=int, default=DEFAULT_WAIT_SECONDS, help=ui(
        f"Seconds to wait for the runner to apply a queued write (default {DEFAULT_WAIT_SECONDS}); exit 10 on timeout, "
        "the write stays queued",
        f"等待 runner 应用排队写入的秒数(默认 {DEFAULT_WAIT_SECONDS});超时 exit 10,写入仍留在队列中"))


@contextlib.contextmanager
def _chdir(path: str):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _handler(command: str):
    from .commands import defect, node   # 惰性:两个命令模块都 import 本模块
    return {"node": node, "defect": defect}[command].apply_locked


def apply_request(request: dict) -> dict:
    """以持锁者身份应用一条排队请求,捕获其输出。命令失败变成回执里的错误,不让 runner 崩溃。"""
    out, err, error, code = io.StringIO(), io.StringIO(), None, exit_codes.OK
    args = argparse.Namespace(**request["args"])
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), _chdir(request["cwd"]):
            code = _handler(request["command"])(args) or exit_codes.OK
    except NeedsDecision as exc:
        code, error = exc.exit_code, {"class": "NeedsDecision", "message": str(exc), "report": exc.report}
    except OmacError as exc:
        code, error = exc.exit_code, {"class": "OmacError", "message": str(exc), "report": {}}
    except Exception as exc:  # shortcut: 命令内的 bug 只让这一条请求失败,不拖垮 runner
        code, error = exit_codes.GENERIC, {"class": "OmacError", "message": f"{type(exc).__name__}: {exc}", "report": {}}
    log.info(logsetup.EVT_QUEUED_WRITE, command=request["command"], action=request["args"].get("action"),
             node=request["args"].get("node_key"), exit_code=code)
    return {"exit_code": code, "stdout": out.getvalue(), "stderr": err.getvalue(), "error": error}


def drain_queued_writes(manifest_path: str) -> int:
    """应用队列中的所有写命令;调用方须持有 manifest 写锁。返回应用个数(>0 时调用方要重新读 manifest)。"""
    return commandqueue.drain(manifest_path, apply_request)


def _serialisable(args, command: str) -> dict:
    data = {k: v for k, v in vars(args).items()
            if not k.startswith("_") and isinstance(v, (str, int, float, bool, type(None)))}
    data["manifest"] = os.path.abspath(args.manifest)
    if data.get("report_file"):
        data["report_file"] = os.path.abspath(data["report_file"])
    return data


def _replay(receipt: dict) -> int:
    result = receipt["result"]
    sys.stdout.write(result.get("stdout", ""))
    sys.stderr.write(result.get("stderr", ""))
    error = result.get("error")
    if error and error.get("class") == "NeedsDecision":
        raise NeedsDecision(error["message"], error.get("report"))
    if error:
        raise QueuedWriteError(error["message"], result["exit_code"])
    return result["exit_code"]


def _try_drain(manifest_path: str) -> None:
    try:
        with manifest_write_lock(manifest_path):
            drain_queued_writes(manifest_path)
    except ManifestLockedError:
        pass


def _wait(args, request_id: str, command: str) -> int:
    deadline = time.monotonic() + max(args.wait_timeout, 0)
    next_lock = time.monotonic() + _LOCK_RETRY_SECONDS
    while True:
        receipt = commandqueue.read_receipt(args.manifest, request_id)
        if receipt and receipt.get("state") == "done":
            commandqueue.consume_receipt(args.manifest, request_id)
            return _replay(receipt)
        now = time.monotonic()
        if now >= deadline:
            print_json({"queued": True, "request_id": request_id, "state": "pending"})
            hint(ui(
                "The runner has not applied it yet; it stays queued and is applied at the next tick boundary. "
                "Do not re-issue it; check with `omac node show` / `omac defect list`.",
                "runner 尚未应用;写入仍在队列中,会在下一个 tick 边界应用。请勿重复下达,用 `omac node show` / `omac defect list` 核对。"))
            return exit_codes.IN_PROGRESS
        if now >= next_lock:
            next_lock = now + _LOCK_RETRY_SECONDS
            _try_drain(args.manifest)
            continue
        time.sleep(_POLL_SECONDS)


def run_write(args, command: str, apply_locked) -> int:
    """锁空闲:先应用更早排队的写入(FIFO),再直接应用本命令;锁被占用:入队并等回执(--no-wait 则立即返回)。"""
    with ExitStack() as stack:
        try:
            stack.enter_context(manifest_write_lock(args.manifest))
        except ManifestLockedError:
            pass
        else:
            drain_queued_writes(args.manifest)
            return apply_locked(args)
    request_id = commandqueue.enqueue(args.manifest, command, _serialisable(args, command), os.getcwd())
    if getattr(args, "no_wait", False):
        print_json({"queued": True, "request_id": request_id, "state": "pending"})
        hint(ui(
            "A `dag run` holds the manifest lock: the write is queued and applied at its next tick boundary.",
            "已有 `dag run` 持有 manifest 写锁:写入已排队,将在其下一个 tick 边界应用。"))
        return exit_codes.OK
    hint(ui(
        "A `dag run` holds the manifest lock: queued; waiting for it to apply the write at its next tick boundary "
        f"(up to {args.wait_timeout}s; --no-wait returns at once).",
        f"已有 `dag run` 持有 manifest 写锁:已排队,等待其在下一个 tick 边界应用(最多 {args.wait_timeout}s;--no-wait 立即返回)。"))
    return _wait(args, request_id, command)

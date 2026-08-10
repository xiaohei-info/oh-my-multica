import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest

import omac.engines.multica as multica_module
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.engines.multica import (
    MulticaStore, _transient_read_failure, _TransientReadFailure,
)
from omac.errors import AuthError, PlatformError


def _store(sleeper, *, attachment_cache_capacity=None):
    kwargs = {}
    if attachment_cache_capacity is not None:
        kwargs["attachment_cache_capacity"] = attachment_cache_capacity
    return MulticaStore(
        EngineConfig(engine_type="multica", workspace_id="ws"),
        sleeper=sleeper,
        **kwargs,
    )


def _load_attachment(store: MulticaStore) -> str | None:
    return store._load_payload_comment("issue-1", "review-report", {
        "attachment_id": "attachment-1",
        "filename": "review.yaml",
    })


def _load_cached_attachment(
    store: MulticaStore,
    *,
    attachment_id: str,
    body: bytes,
    filename: str = "review.yaml",
    declared_bytes: int | None = None,
) -> str | None:
    return store._load_payload_comment("issue-1", "review-report", {
        "attachment_id": attachment_id,
        "filename": filename,
        "sha256": sha256(body).hexdigest(),
        "bytes": len(body) if declared_bytes is None else declared_bytes,
    })


def test_attachment_body_cache_reuses_validated_immutable_body(monkeypatch):
    store = _store(lambda _delay: None)
    body = b"verdict: pass\n"
    downloads = 0

    def run(args, capture=True):
        nonlocal downloads
        downloads += 1
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_bytes(body)

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_cached_attachment(
        store, attachment_id="attachment-1", body=body,
    ) == body.decode()
    assert _load_cached_attachment(
        store, attachment_id="attachment-1", body=body,
    ) == body.decode()
    assert downloads == 1


def test_attachment_body_cache_does_not_reuse_different_expected_digest(
        monkeypatch):
    store = _store(lambda _delay: None)
    bodies = [b"first\n", b"second\n"]
    downloads = 0

    def run(args, capture=True):
        nonlocal downloads
        body = bodies[downloads]
        downloads += 1
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_bytes(body)

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_cached_attachment(
        store, attachment_id="attachment-1", body=bodies[0],
    ) == bodies[0].decode()
    assert _load_cached_attachment(
        store, attachment_id="attachment-1", body=bodies[1],
    ) == bodies[1].decode()
    assert downloads == 2


def test_attachment_body_cache_does_not_reuse_different_declared_size(
        monkeypatch):
    store = _store(lambda _delay: None)
    body = b"verdict: pass\n"
    downloads = 0

    def run(args, capture=True):
        nonlocal downloads
        downloads += 1
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_bytes(body)

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_cached_attachment(
        store,
        attachment_id="attachment-1",
        body=body,
        declared_bytes=len(body),
    ) == body.decode()
    assert _load_cached_attachment(
        store,
        attachment_id="attachment-1",
        body=body,
        declared_bytes=len(body) + 1,
    ) == body.decode()
    assert downloads == 2


def test_attachment_body_cache_evicts_least_recently_used_entry(monkeypatch):
    store = _store(lambda _delay: None, attachment_cache_capacity=2)
    bodies = {
        "attachment-1": b"one\n",
        "attachment-2": b"two\n",
        "attachment-3": b"three\n",
    }
    downloads = []

    def run(args, capture=True):
        attachment_id = args[2]
        downloads.append(attachment_id)
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_bytes(bodies[attachment_id])

    monkeypatch.setattr(store, "_run_multica", run)

    for attachment_id in ("attachment-1", "attachment-2", "attachment-1",
                          "attachment-3", "attachment-2"):
        assert _load_cached_attachment(
            store,
            attachment_id=attachment_id,
            body=bodies[attachment_id],
        ) == bodies[attachment_id].decode()

    assert downloads == [
        "attachment-1", "attachment-2", "attachment-3", "attachment-2",
    ]


def test_attachment_body_cache_coalesces_concurrent_same_key_downloads(
        monkeypatch):
    store = _store(lambda _delay: None)
    body = b"verdict: pass\n"
    downloads = 0
    downloads_lock = threading.Lock()

    def run(args, capture=True):
        nonlocal downloads
        with downloads_lock:
            downloads += 1
        time.sleep(0.05)
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_bytes(body)

    monkeypatch.setattr(store, "_run_multica", run)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _index: _load_cached_attachment(
                store, attachment_id="attachment-1", body=body,
            ),
            range(8),
        ))

    assert results == [body.decode()] * 8
    assert downloads == 1


def test_attachment_body_cache_does_not_cache_failed_read(monkeypatch):
    store = _store(lambda _delay: None)
    body = b"verdict: pass\n"
    downloads = 0

    def run(args, capture=True):
        nonlocal downloads
        downloads += 1
        if downloads == 1:
            raise PlatformError("unexpected deterministic failure")
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_bytes(body)

    monkeypatch.setattr(store, "_run_multica", run)

    with pytest.raises(PlatformError, match="unexpected deterministic failure"):
        _load_cached_attachment(
            store, attachment_id="attachment-1", body=body,
        )
    assert _load_cached_attachment(
        store, attachment_id="attachment-1", body=body,
    ) == body.decode()
    assert downloads == 2


def test_attachment_body_cache_does_not_cache_digest_mismatch(monkeypatch):
    store = _store(lambda _delay: None)
    expected = b"verdict: pass\n"
    bodies = [b"tampered\n", expected]
    downloads = 0

    def run(args, capture=True):
        nonlocal downloads
        body = bodies[downloads]
        downloads += 1
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_bytes(body)

    monkeypatch.setattr(store, "_run_multica", run)

    with pytest.raises(PlatformError, match="digest does not match"):
        _load_cached_attachment(
            store, attachment_id="attachment-1", body=expected,
        )
    assert _load_cached_attachment(
        store, attachment_id="attachment-1", body=expected,
    ) == expected.decode()
    assert downloads == 2


def test_attachment_download_retries_timeout_then_succeeds(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(args, capture=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PlatformError(
                "Request timed out: the server did not respond in time. "
                "You can raise the limit with MULTICA_HTTP_TIMEOUT."
            )
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_text("verdict: pass\n")

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_attachment(store) == "verdict: pass\n"
    assert attempts == 2
    assert delays == [1.0]


def test_attachment_download_retries_unreachable_then_succeeds(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(args, capture=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PlatformError(
                "Could not reach the Multica server. Check your network connection."
            )
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_text("verdict: pass\n")

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_attachment(store) == "verdict: pass\n"
    assert attempts == 2
    assert delays == [1.0]


@pytest.mark.parametrize("message", [
    "Could not connect to the Multica server. Make sure the server address is correct and reachable.",
    "write: connection reset by peer",
    "Could not resolve the Multica server address. Check your network connection.",
    "temporary failure in name resolution",
    "Too many requests. Please wait a moment and try again.",
    "HTTP 429 Too Many Requests",
    "HTTP 502 Bad Gateway",
    "HTTP 503 Service Unavailable",
    "HTTP 504 Gateway Timeout",
    "The Multica service is temporarily unavailable (server error). Please try again later.",
])
def test_attachment_download_retries_supported_transient_cli_messages(
        message, monkeypatch):
    store = _store(lambda _delay: None)
    attempts = 0

    def run(args, capture=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PlatformError(message)
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_text("ok\n")

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_attachment(store) == "ok\n"
    assert attempts == 2


def test_attachment_download_exhausts_bounded_transient_retries(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(_args, capture=True):
        nonlocal attempts
        attempts += 1
        raise PlatformError(
            "Could not reach the Multica server. Check your network connection."
        )

    monkeypatch.setattr(store, "_run_multica", run)

    with pytest.raises(PlatformError, match="Could not reach"):
        _load_attachment(store)

    assert attempts == 3
    assert delays == [1.0, 2.0]


@pytest.mark.parametrize("error", [
    AuthError("multica authentication failed; run `multica login`"),
    PlatformError("You do not have permission to access this resource."),
    PlatformError("The requested resource was not found. Check the ID."),
    PlatformError("attachment not found: attachment-1"),
    PlatformError("attachment 503 not found"),
    PlatformError("The request was invalid. Run the command with --help."),
    PlatformError(
        "Could not establish a secure connection to the Multica server "
        "(TLS/certificate error)."
    ),
    PlatformError("unexpected deterministic failure"),
])
def test_attachment_download_does_not_retry_deterministic_errors(
        error, monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(_args, capture=True):
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr(store, "_run_multica", run)

    with pytest.raises(type(error), match=re.escape(str(error))):
        _load_attachment(store)

    assert attempts == 1
    assert delays == []


def test_attachment_download_uses_deterministic_exponential_backoff(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(args, capture=True):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PlatformError("connection refused")
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_text("ok\n")

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_attachment(store) == "ok\n"
    assert delays == [1.0, 2.0]


def test_attachment_download_isolates_partial_files_between_attempts(monkeypatch):
    store = _store(lambda _delay: None)
    attempt_dirs = []

    def run(args, capture=True):
        output_dir = Path(args[args.index("--output-dir") + 1])
        attempt_dirs.append(output_dir)
        target = output_dir / "review.yaml"
        if len(attempt_dirs) == 1:
            target.write_text("partial")
            raise PlatformError("connection reset by peer")
        assert not target.exists()
        target.write_text("complete")

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_attachment(store) == "complete"
    assert len(attempt_dirs) == 2
    assert attempt_dirs[0] != attempt_dirs[1]
    assert all(not path.exists() for path in attempt_dirs)


def test_attachment_download_retry_logs_operation_attempt_delay_and_reason(
        monkeypatch):
    store = _store(lambda _delay: None)
    attempts = 0
    events = []
    monkeypatch.setattr(
        "omac.engines.multica._log_retry",
        lambda event, **fields: events.append((event, fields)),
    )

    def run(args, capture=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PlatformError("Could not reach the Multica server.")
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / "review.yaml").write_text("ok\n")

    monkeypatch.setattr(store, "_run_multica", run)

    assert _load_attachment(store) == "ok\n"
    assert events == [("multica_read_retry", {
        "operation": "attachment download",
        "attempt": 1,
        "max_attempts": 3,
        "delay": 1.0,
        "reason": "network_unreachable",
    })]


def test_issue_control_read_retries_timeout_then_succeeds(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(args, capture=True):
        nonlocal attempts
        attempts += 1
        assert args == ["issue", "get", "issue-1", "--output", "json"]
        if attempts == 1:
            raise PlatformError(
                "Request timed out: the server did not respond in time."
            )
        return {"id": "issue-1"}

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(
        store,
        "_issue_to_control_projection",
        lambda issue, workspace_id: (issue, workspace_id),
    )

    assert store.observe_work_item_control("issue-1") == (
        {"id": "issue-1"}, "ws")
    assert attempts == 2
    assert delays == [1.0]


def test_issue_control_read_does_not_retry_deterministic_error(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(_args, capture=True):
        nonlocal attempts
        attempts += 1
        raise PlatformError("The requested resource was not found.")

    monkeypatch.setattr(store, "_run_multica", run)

    with pytest.raises(PlatformError, match="not found"):
        store.observe_work_item_control("issue-1")

    assert attempts == 1
    assert delays == []


def test_multica_write_operation_is_never_retried(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def run(_args, capture=True):
        nonlocal attempts
        attempts += 1
        raise PlatformError(
            "Request timed out: the server did not respond in time."
        )

    monkeypatch.setattr(store, "_run_multica", run)

    with pytest.raises(PlatformError, match="timed out"):
        store.update_status("issue-1", WorkItemStatus.DONE)

    assert attempts == 1
    assert delays == []


# ==================== P0: _run_multica 子进程硬超时 ====================
#
# multica CLI 在 TCP 连接卡死(SYN_SENT)时会永久挂起;_run_multica 的
# subprocess.run 必须有进程级硬超时,且超时文案必须被
# _transient_read_failure 归类为可重试的网络超时——幂等读经
# _run_idempotent_read 自动重试;写路径从不经过重试包装,超时按
# Unknown 直接上抛(fail-closed,不盲重试)。


def test_run_multica_defines_bounded_subprocess_timeout_constant():
    timeout = multica_module._MULTICA_SUBPROCESS_TIMEOUT

    assert isinstance(timeout, (int, float))
    # 必须严格大于 multica CLI 自身 HTTP 超时(缺省约 30s,可经
    # MULTICA_HTTP_TIMEOUT 调高),让正常慢响应由 CLI 自己的超时文案
    # 被分类,而不是被进程级硬超时误杀。
    assert timeout > 30


def test_run_multica_passes_timeout_to_subprocess_and_maps_expiry(monkeypatch):
    store = _store(lambda _delay: None)
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(multica_module.subprocess, "run", fake_run)

    with pytest.raises(PlatformError) as excinfo:
        store._run_multica(["issue", "get", "issue-1", "--output", "json"])

    assert captured["timeout"] == multica_module._MULTICA_SUBPROCESS_TIMEOUT
    # 挂起的调用必须归类为可重试的网络超时。
    assert _transient_read_failure(str(excinfo.value)) is (
        _TransientReadFailure.NETWORK_TIMEOUT)


def test_issue_control_read_retries_subprocess_timeout_then_succeeds(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def fake_run(cmd, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise subprocess.TimeoutExpired(
                cmd=cmd, timeout=kwargs.get("timeout"))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"id": "issue-1"}), stderr="")

    monkeypatch.setattr(multica_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        store,
        "_issue_to_control_projection",
        lambda issue, workspace_id: (issue, workspace_id),
    )

    assert store.observe_work_item_control("issue-1") == (
        {"id": "issue-1"}, "ws")
    assert attempts == 2
    assert delays == [1.0]


def test_issue_control_read_exhausts_retries_on_subprocess_timeout(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def fake_run(cmd, **kwargs):
        nonlocal attempts
        attempts += 1
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(multica_module.subprocess, "run", fake_run)

    with pytest.raises(PlatformError) as excinfo:
        store.observe_work_item_control("issue-1")

    assert attempts == 3
    assert delays == [1.0, 2.0]
    assert _transient_read_failure(str(excinfo.value)) is (
        _TransientReadFailure.NETWORK_TIMEOUT)


def test_write_path_subprocess_timeout_is_not_retried(monkeypatch):
    delays = []
    store = _store(delays.append)
    attempts = 0

    def fake_run(cmd, **kwargs):
        nonlocal attempts
        attempts += 1
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(multica_module.subprocess, "run", fake_run)

    with pytest.raises(PlatformError, match="timed out|超时"):
        store.update_status("issue-1", WorkItemStatus.DONE)

    assert attempts == 1
    assert delays == []

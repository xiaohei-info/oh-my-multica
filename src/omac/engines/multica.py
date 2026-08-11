"""Multica 引擎 — 调用 multica CLI 实现双接口(现有资产平移,去 squad 概念)。

参考实现映射见设计文档 §12.3:
- MulticaStore:issue create/get/metadata set/list/comment/update/assign
- MulticaRuntime:assign 即唤醒(wake 为确认性 no-op)

认证通常由 multica CLI 自管(~/.multica)。需要精确 UUID 原子更新时，使用
同一配置中的 token 执行幂等 PUT；token 不进入日志、事件或持久化数据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

import yaml

from ..core import logsetup
from ..core.taskmeta import (
    AMENDMENT_ATTEMPT_KEY, BOUNCE_BASELINE_KEY, CI_BOUNCE_KEY, CONTRACT_REF_KEY,
    DECISION_REQUIRED_KEY, DELIVERY_IDENTITY_KEY, DELIVERABLE_KEY,
    DELIVERABLE_REF_KEY, KIND_KEY, MERGE_BOUNCE_KEY, PHASE_KEY,
    MACHINE_FEEDBACK_REF_KEY, PROJECT_RULES_KEY, PROJECT_RULES_REF_KEY, REVIEW_BOUNCE_KEY,
    REVIEW_CONTINUATION_KEY, REVIEW_GENERATION_KEY,
    REVIEW_LEDGER_GENERATION_KEY, REVIEWER_RUN_BASELINE_KEY,
    REVIEW_LEDGER_REF_KEY, REVIEW_OBLIGATIONS_KEY,
    REVIEW_OBLIGATIONS_REF_KEY,
    REVIEW_REPORT_REF_KEY,
    REVIEW_SUBJECT_DIGEST_KEY,
    SOURCE_REFS_KEY, DeliveryIdentity, ReviewerRunBaseline, TaskKind, TaskPhase,
    VERIFICATION_REF_KEY, WORKER_BOUNCE_KEY, WORKER_HANDOFF_KEY,
    WorkerHandoffIntent, parse_bounces, parse_delivery_identity, parse_kind,
    parse_phase, parse_reviewer_run_baseline, parse_worker_handoff,
)
from ..errors import (
    AuthError, PlatformError, ValidationError, WorkItemNotFoundError,
)
from ..i18n import ui
from .models import (
    AgentInfo, AgentProvisionSpec, AgentRunObservation, EngineConfig,
    ProjectInfo, RuntimeTarget, MergeCommandResult,
    PullRequestCheckResult, PullRequestObservation,
    PullRequestReadiness, PullRequestReadinessFailure, RuntimeCapabilities,
    PullRequestReadinessFailureKind, PullRequestState,
    SkillPackage, VerificationAttachmentObservation, WorkItem,
    WorkItemControlProjection, WorkItemHydrationPlan, WorkItemPayload,
    WorkItemStatus, WorkspaceInfo,
)
from ..core.machine_feedback import (
    dump_machine_feedback, is_machine_feedback, parse_machine_feedback,
)
from .metadata_policy import (
    assert_metadata_write_allowed, encode_metadata_value, parse_payload_text,
)
from .runtime import AgentRuntime
from .store import WorkItemStore

MULTICA_PR_VIEW_FIELDS = (
    "state,mergedAt,autoMergeRequest,mergeStateStatus,headRefOid"
)
_MULTICA_READ_MAX_ATTEMPTS = 3
_MULTICA_READ_INITIAL_DELAY = 1.0
# 单次 multica CLI 子进程调用的硬超时(秒)。
# multica CLI 自带 HTTP 超时(缺省约 30s,可经 MULTICA_HTTP_TIMEOUT 调高),
# 超时会以可分类的文本输出("Request timed out: ...")返回;此进程级上限只
# 兜底 CLI 在其 HTTP 超时之前/之外挂死的情形(如 TCP SYN_SENT 卡死),否则
# 一条挂起连接会永久占住一个 reconcile worker 槽。90s 对 CLI 缺省超时保留
# >3x 余量,正常慢响应仍由 CLI 自己的超时文案被分类,不会被这里误杀。
# subprocess.run 在 timeout 到期时会 kill 子进程(Python 语义),无残留进程
# 需要清理。
_MULTICA_SUBPROCESS_TIMEOUT = 90.0
_ATTACHMENT_BODY_CACHE_CAPACITY = 64
# Current Multica task states plus legacy aliases still returned by older APIs.
_ACTIVE_RUN_STATUSES = {
    "queued", "pending", "dispatched", "running", "dispatching",
    "waiting_local_directory", "deferred",
}
_RERUNNABLE_DIRECT_RUN_STATUSES = {"failed", "cancelled", "completed"}
_KNOWN_RUN_STATUSES = _ACTIVE_RUN_STATUSES | _RERUNNABLE_DIRECT_RUN_STATUSES
_KNOWN_WORK_ITEM_METADATA_KEYS = {
    "dag_key", "worker", "reviewer", "blocked_by", "wave", "artifacts",
    "verification", "review_verdict", "review_comment", "review_report",
    "contract",
    AMENDMENT_ATTEMPT_KEY, BOUNCE_BASELINE_KEY, CI_BOUNCE_KEY, CONTRACT_REF_KEY,
    DECISION_REQUIRED_KEY, DELIVERABLE_KEY, DELIVERABLE_REF_KEY, KIND_KEY,
    MACHINE_FEEDBACK_REF_KEY, MERGE_BOUNCE_KEY, PHASE_KEY, PROJECT_RULES_KEY,
    PROJECT_RULES_REF_KEY, REVIEW_BOUNCE_KEY, REVIEW_CONTINUATION_KEY,
    REVIEW_GENERATION_KEY, REVIEW_LEDGER_GENERATION_KEY,
    REVIEWER_RUN_BASELINE_KEY,
    REVIEW_LEDGER_REF_KEY, REVIEW_OBLIGATIONS_KEY, REVIEW_OBLIGATIONS_REF_KEY,
    REVIEW_REPORT_REF_KEY, REVIEW_SUBJECT_DIGEST_KEY, SOURCE_REFS_KEY,
    VERIFICATION_REF_KEY, WORKER_BOUNCE_KEY, WORKER_HANDOFF_KEY,
    DELIVERY_IDENTITY_KEY,
}
_KNOWN_ISSUE_FIELDS = {
    "id", "identifier", "title", "description", "status", "metadata",
    "created_at", "updated_at", "assignee_id", "assignee", "comments",
    "attachments", "project_id", "project", "workspace_id", "workspace",
    "creator_id", "creator", "created_by_id", "created_by", "url", "web_url",
}
_READ_ONLY_ISSUE_ENVELOPE_FIELDS = frozenset({
    "assignee_type", "creator_type", "due_date", "labels", "number",
    "parent_issue_id", "position", "priority", "stage", "start_date",
})
_EMPTY_DEFAULT_ISSUE_ENVELOPE_FIELDS = {
    "properties": {},
}

_ReadResult = TypeVar("_ReadResult")
_AttachmentCacheKey = tuple[str, str, str, Optional[int]]

_REVIEW_CLEAR_METADATA = (
    ("review_comment", ""),
    (MACHINE_FEEDBACK_REF_KEY, "{}"),
    (REVIEW_REPORT_REF_KEY, "{}"),
    (DECISION_REQUIRED_KEY, "{}"),
    (REVIEWER_RUN_BASELINE_KEY, "{}"),
    ("review_verdict", ""),
)
_EMPTY_TEXT_METADATA = frozenset({
    "review_comment", "review_verdict", REVIEW_SUBJECT_DIGEST_KEY,
})
_EMPTY_OBJECT_METADATA = frozenset({
    MACHINE_FEEDBACK_REF_KEY, REVIEW_REPORT_REF_KEY,
    DECISION_REQUIRED_KEY, REVIEWER_RUN_BASELINE_KEY,
})
_INVALID_OBJECT_METADATA_SCHEMA = "omac.invalid-object-metadata/v1"


def _decode_json_metadata_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {"raw": value} if value else None


def _project_object_metadata(value: Any) -> Any:
    decoded = _decode_json_metadata_value(value)
    if decoded in (None, {}):
        return None
    if isinstance(decoded, dict):
        return decoded
    return {
        "schema": _INVALID_OBJECT_METADATA_SCHEMA,
        "decoded_type": type(decoded).__name__,
        "value": decoded,
    }


class _AttachmentBodyCache:
    """Bounded in-process LRU with one loader per immutable attachment key."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("attachment_cache_capacity must be at least 1")
        self._capacity = capacity
        self._entries: OrderedDict[_AttachmentCacheKey, bytes] = OrderedDict()
        self._inflight: Dict[_AttachmentCacheKey, Future[Optional[bytes]]] = {}
        self._lock = threading.Lock()

    def get_or_load(
        self,
        key: _AttachmentCacheKey,
        load: Callable[[], Optional[bytes]],
        *,
        cacheable: Callable[[bytes], bool],
    ) -> Optional[bytes]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future

        if not owner:
            return future.result()

        try:
            body = load()
        except BaseException as exc:
            with self._lock:
                self._inflight.pop(key, None)
            future.set_exception(exc)
            raise

        should_cache = body is not None and cacheable(body)
        with self._lock:
            self._inflight.pop(key, None)
            if should_cache:
                self._entries[key] = body
                self._entries.move_to_end(key)
                while len(self._entries) > self._capacity:
                    self._entries.popitem(last=False)
        future.set_result(body)
        return body


class _TransientReadFailure(str, Enum):
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK_DNS = "network_dns"
    NETWORK_REFUSED = "network_refused"
    NETWORK_UNREACHABLE = "network_unreachable"
    HTTP_429 = "http_429"
    HTTP_502 = "http_502"
    HTTP_503 = "http_503"
    HTTP_504 = "http_504"
    SERVER_UNAVAILABLE = "server_unavailable"


def _transient_read_failure(message: str) -> Optional[_TransientReadFailure]:
    """Classify only known transient Multica CLI output.

    Auth, permission, not-found, validation, TLS/certificate, and unknown errors
    deliberately fall through so callers fail immediately.
    """
    text = message.lower()
    status_match = re.search(
        r"\b(?:http(?: status)?|status(?: code)?|server returned)"
        r"\s*[:=]?\s*(429|502|503|504)\b",
        text,
    )
    if status_match is None:
        status_match = re.search(
            r'"status(?:_code)?"\s*:\s*(429|502|503|504)\b',
            text,
        )
    if status_match:
        return _TransientReadFailure(f"http_{status_match.group(1)}")
    if "too many requests" in text or "请求过于频繁" in text:
        return _TransientReadFailure.HTTP_429
    if (
        "request timed out" in text
        or "context deadline exceeded" in text
        or "i/o timeout" in text
        or "client.timeout exceeded" in text
        or "请求超时" in text
    ):
        return _TransientReadFailure.NETWORK_TIMEOUT
    if (
        "could not resolve the multica server address" in text
        or "no such host" in text
        or "temporary failure in name resolution" in text
        or "server misbehaving" in text
        or "name resolution" in text
        or "无法解析 multica 服务器地址" in text
    ):
        return _TransientReadFailure.NETWORK_DNS
    if (
        "could not connect to the multica server" in text
        or "connection refused" in text
        or "无法连接到 multica 服务器" in text
    ):
        return _TransientReadFailure.NETWORK_REFUSED
    if (
        "could not reach the multica server" in text
        or "connection reset" in text
        or "network is unreachable" in text
        or "host is unreachable" in text
        or "broken pipe" in text
        or "connection aborted" in text
        or "无法访问 multica 服务器" in text
    ):
        return _TransientReadFailure.NETWORK_UNREACHABLE
    if (
        "multica service is temporarily unavailable (server error)" in text
        or "multica 服务暂时不可用（服务器错误）" in text
    ):
        return _TransientReadFailure.SERVER_UNAVAILABLE
    return None


def _log_retry(event: str, **fields: Any) -> None:
    logsetup.get_logger(__name__).warning(event, **fields)


def _latest_run(runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not runs:
        return None
    indexed = list(enumerate(runs))
    return max(
        indexed,
        key=lambda pair: (
            pair[1].get("created_at") or pair[1].get("started_at")
            or pair[1].get("dispatched_at") or "",
            -pair[0],
        ),
    )[1]


def _latest_direct_run(runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    direct_runs = [run for run in runs if (run.get("kind") or "direct") == "direct"]
    return _latest_run(direct_runs)


def _direct_run_ids(runs: List[Dict[str, Any]]) -> set[str]:
    return {
        str(run["id"])
        for run in runs
        if (run.get("kind") or "direct") == "direct" and run.get("id")
    }


def _run_trigger_kind(run: Dict[str, Any]) -> str | None:
    attribution = run.get("attribution")
    if not isinstance(attribution, dict):
        return None
    evidence = attribution.get("evidence")
    if not isinstance(evidence, dict) or not evidence.get("kind"):
        return None
    return str(evidence["kind"])


def _is_manual_rerun(run: Dict[str, Any]) -> bool:
    return _run_trigger_kind(run) == "rerun"


def _is_not_found(message: str) -> bool:
    text = message.lower()
    return "not found" in text or "does not exist" in text or "404" in text


class MulticaStore(WorkItemStore):
    """数据面:全部经 multica CLI。"""

    def __init__(
        self,
        config: EngineConfig,
        sleeper: Callable[[float], None] = time.sleep,
        *,
        attachment_cache_capacity: int = _ATTACHMENT_BODY_CACHE_CAPACITY,
    ):
        super().__init__(config)
        self._sleep = sleeper
        self._pending_assignment_wakes: set[str] = set()
        self._attachment_bodies = _AttachmentBodyCache(
            attachment_cache_capacity)

    def _mark_assignment_wake_pending(self, item_id: str) -> None:
        self._pending_assignment_wakes.add(item_id)

    def _consume_assignment_wake_pending(self, item_id: str) -> bool:
        if item_id not in self._pending_assignment_wakes:
            return False
        self._pending_assignment_wakes.remove(item_id)
        return True

    # ==================== 内部工具 ====================

    def _run_idempotent_read(
        self,
        operation: str,
        read: Callable[[], _ReadResult],
    ) -> _ReadResult:
        """Retry one explicitly idempotent read; writes never call this helper."""
        for attempt in range(1, _MULTICA_READ_MAX_ATTEMPTS + 1):
            try:
                return read()
            except PlatformError as exc:
                reason = _transient_read_failure(str(exc))
                if reason is None:
                    raise
                exhausted = attempt == _MULTICA_READ_MAX_ATTEMPTS
                delay = 0.0 if exhausted else (
                    _MULTICA_READ_INITIAL_DELAY * (2 ** (attempt - 1))
                )
                _log_retry(
                    "multica_read_retry_exhausted" if exhausted
                    else "multica_read_retry",
                    operation=operation,
                    attempt=attempt,
                    max_attempts=_MULTICA_READ_MAX_ATTEMPTS,
                    delay=delay,
                    reason=reason.value,
                )
                if exhausted:
                    raise
                self._sleep(delay)
        raise AssertionError("bounded read retry loop must return or raise")

    def is_transient_transport_error(self, error: PlatformError) -> bool:
        """Classify only the existing strict Multica transport allowlist."""
        return _transient_read_failure(str(error)) is not None

    def _run_multica(self, args: List[str], capture=True) -> Any:
        """调用 multica CLI。

        workspace 通过全局 flag `--workspace-id` 注入(位于 multica 与子命令之间),
        与 multica CLI 约定一致——子命令本身不接受 --workspace-id。
        """
        cmd = ["multica"]
        if self.config.workspace_id:
            cmd += ["--workspace-id", self.config.workspace_id]
        cmd += args
        try:
            result = subprocess.run(
                cmd, capture_output=capture, text=True,
                timeout=_MULTICA_SUBPROCESS_TIMEOUT)
        except FileNotFoundError:
            raise AuthError(ui(
                "multica CLI is not on PATH. Install it and sign in: "
                "brew install multica-ai/tap/multica && multica login",
                "multica CLI 不在 PATH —— 先安装并登录:brew install multica-ai/tap/multica && multica login"))
        except subprocess.TimeoutExpired:
            # subprocess.run 超时即 kill 子进程(Python 语义),无残留进程需清理。
            # 文案包含 _transient_read_failure 识别的 "request timed out"/
            # "请求超时":幂等读经 _run_idempotent_read 自动重试;写路径从不
            # 经过该重试包装,超时按 Unknown 直接上抛(fail-closed,不盲重试)。
            raise PlatformError(ui(
                "multica request timed out after "
                f"{int(_MULTICA_SUBPROCESS_TIMEOUT)}s "
                f"(the CLI process was killed): {' '.join(cmd)}",
                f"multica 请求超时({int(_MULTICA_SUBPROCESS_TIMEOUT)} 秒,"
                f"已终止 CLI 进程): {' '.join(cmd)}"))
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if result.returncode == 3 or "auth" in stderr.lower() or "login" in stderr.lower():
                raise AuthError(ui(
                    f"multica authentication failed; run `multica login`: {stderr}",
                    f"multica 认证失败(先 multica login): {stderr}"))
            if args[:2] == ["issue", "get"] and _is_not_found(stderr):
                raise WorkItemNotFoundError(ui(
                    f"Multica issue not found: {args[2]}",
                    f"Multica issue 不存在: {args[2]}"))
            raise PlatformError(ui(
                f"multica command failed: {' '.join(cmd)}\n{stderr}",
                f"multica 调用失败: {' '.join(cmd)}\n{stderr}"))
        if capture and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return result.stdout.strip()
        return None

    def _run_multica_with_text_file(self, args: List[str], flag: str, content: str, capture=True) -> Any:
        """长文本经 --x-file 传递(规避 shell 转义与编码问题)。"""
        fd, path = tempfile.mkstemp(prefix="omac-", suffix=".md", text=True)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content or "")
            return self._run_multica(
                args + [flag, path, "--allow-external-file"],
                capture=capture,
            )
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _put_issue_description_direct(
        self,
        item_id: str,
        description: str,
    ) -> None:
        """绕过 CLI 的 issue resolver，用精确 UUID 幂等修复 description。"""
        self._put_issue_fields_direct(
            item_id,
            {"description": description},
            operation="description recovery",
        )

    def _put_issue_fields_direct(
        self,
        item_id: str,
        fields: Dict[str, Any],
        *,
        operation: str,
    ) -> None:
        """Use one exact-identity PUT for an atomic issue projection update."""
        try:
            parsed_id = uuid.UUID(item_id)
        except ValueError as exc:
            raise PlatformError(ui(
                f"Direct {operation} requires a canonical issue UUID: {item_id}",
                f"直接执行 {operation} 需要完整 issue UUID：{item_id}")) from exc
        if str(parsed_id) != item_id.lower():
            raise PlatformError(ui(
                f"Direct {operation} requires a canonical issue UUID: {item_id}",
                f"直接执行 {operation} 需要完整 issue UUID：{item_id}"))

        config_path = os.path.expanduser(
            os.environ.get("MULTICA_CONFIG_PATH", "~/.multica/config.json"))
        try:
            with open(config_path, encoding="utf-8") as fh:
                cli_config = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise PlatformError(ui(
                f"Could not read Multica auth config for {operation}: {exc}",
                f"无法读取 Multica 认证配置以执行 {operation}：{exc}")) from exc

        token = str(cli_config.get("token") or "").strip()
        server_url = str(
            os.environ.get("MULTICA_SERVER_URL")
            or cli_config.get("server_url")
            or ""
        ).rstrip("/")
        if not token or not server_url:
            raise PlatformError(ui(
                f"Multica token or server_url is missing for {operation}",
                f"{operation} 缺少 Multica token 或 server_url"))

        header_fd, header_path = tempfile.mkstemp(
            prefix="omac-multica-headers-", text=True)
        body_fd, body_path = tempfile.mkstemp(
            prefix="omac-multica-body-", suffix=".json", text=True)
        try:
            with os.fdopen(header_fd, "w", encoding="utf-8") as fh:
                fh.write(
                    f"Authorization: Bearer {token}\n"
                    "Content-Type: application/json\n"
                    "Accept: application/json\n"
                    f"X-Workspace-ID: {self.config.workspace_id}\n"
                )
            with os.fdopen(body_fd, "w", encoding="utf-8") as fh:
                json.dump(fields, fh, ensure_ascii=False)
            try:
                result = subprocess.run(
                    [
                        "curl",
                        "--silent",
                        "--show-error",
                        "--fail",
                        "--request", "PUT",
                        "--header", f"@{header_path}",
                        "--data-binary", f"@{body_path}",
                        "--output", os.devnull,
                        "--max-time", "30",
                        f"{server_url}/api/issues/{item_id}",
                    ],
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError as exc:
                raise PlatformError(ui(
                    f"curl is required for direct Multica {operation}",
                    f"直接执行 Multica {operation} 需要 curl")) from exc
            if result.returncode != 0:
                raise PlatformError(ui(
                    f"Direct Multica {operation} failed: "
                    f"{(result.stderr or '').strip()}",
                    f"直接执行 Multica {operation} 失败："
                    f"{(result.stderr or '').strip()}"))
        finally:
            for path in (header_path, body_path):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    def _status_to_multica(self, status: WorkItemStatus) -> str:
        mapping = {
            WorkItemStatus.TODO: "todo",
            WorkItemStatus.IN_PROGRESS: "in_progress",
            WorkItemStatus.IN_REVIEW: "in_review",
            WorkItemStatus.DONE: "done",
            WorkItemStatus.FAILED: "blocked",
            WorkItemStatus.BLOCKED: "blocked",
        }
        return mapping.get(status, "todo")

    def _multica_to_status(self, multica_status: str) -> WorkItemStatus:
        mapping = {
            "todo": WorkItemStatus.TODO,
            "in_progress": WorkItemStatus.IN_PROGRESS,
            "in_review": WorkItemStatus.IN_REVIEW,
            "done": WorkItemStatus.DONE,
            "failed": WorkItemStatus.FAILED,
            "blocked": WorkItemStatus.BLOCKED,
            "cancelled": WorkItemStatus.BLOCKED,
        }
        return mapping.get(multica_status, WorkItemStatus.TODO)

    @staticmethod
    def _json_metadata(metadata: Dict, key: str):
        value = metadata.get(key)
        if key in _EMPTY_OBJECT_METADATA:
            return _project_object_metadata(value)
        return _decode_json_metadata_value(value)

    @staticmethod
    def _optional_text_metadata(metadata: Dict, key: str) -> Optional[str]:
        value = metadata.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _payload_markers(key: str) -> tuple[str, str]:
        return (f"<!-- omac-{key}-begin -->", f"<!-- omac-{key}-end -->")

    def _unassign_before_system_comment(self, item_id: str) -> None:
        """让 OMAC 系统评论只写数据，不被 Multica 解释为新的 agent 输入。"""
        issue = self._run_multica([
            "issue", "get", item_id, "--output", "json",
        ])
        if not isinstance(issue, dict):
            return
        if issue.get("assignee_id") or issue.get("assignee"):
            self._run_multica(["issue", "assign", item_id, "--unassign"])

    def _publish_payload_comment(
        self, item_id: str, key: str, content: str, suffix: str,
    ) -> Dict[str, Any]:
        """发布较长文档:comment 只做附件索引,正文由 attachment 承载。

        Multica 会把发给已分配 agent 的评论解释为新输入并创建 comment run。
        payload 是阶段交接数据，不是新的执行指令，因此发布前解除当前分配；
        后续 worker/reviewer 由 pipeline 的 assign + wake 显式派发。
        """
        body = content or ""
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        filename = f"omac-{key}-{sha[:12]}{suffix}"
        size = len(body.encode("utf-8"))
        comment = self._payload_comment(key, sha, size, filename)

        with tempfile.TemporaryDirectory(prefix="omac-payload-") as td:
            comment_path = os.path.join(td, f"comment-{key}.md")
            attachment_path = os.path.join(td, filename)
            with open(comment_path, "w", encoding="utf-8") as f:
                f.write(comment)
            with open(attachment_path, "w", encoding="utf-8") as f:
                f.write(body)
            self._unassign_before_system_comment(item_id)
            result = self._run_multica([
                "issue", "comment", "add", item_id,
                "--content-file", comment_path,
                "--attachment", attachment_path,
                "--allow-external-file",
                "--output", "json",
            ])

        comment_id = result.get("id") if isinstance(result, dict) else None
        attachments = result.get("attachments") if isinstance(result, dict) else None
        attachment = attachments[0] if attachments else {}
        return {
            "comment_id": comment_id,
            "attachment_id": attachment.get("id"),
            "sha256": sha,
            "bytes": size,
            "filename": attachment.get("filename") or filename,
        }

    @staticmethod
    def _payload_comment(key: str, sha: str, size: int, filename: str) -> str:
        title = {
            "contract": ui("Node contract file", "节点 contract 文件"),
            "deliverable": ui("Stage delivery file", "阶段交付文件"),
            "project-rules": ui("Project rules file", "项目级开发规范文件"),
            "verification": ui("Verification evidence file", "验证证据文件"),
            "review-report": ui("Review report file", "评审报告文件"),
            "review-obligations": ui("Review obligations file", "评审义务文件"),
            "machine-feedback": ui("Machine feedback file", "机器反馈文件"),
        }.get(key, ui("Handoff file", "交接文件"))
        ref_key = {
            "contract": CONTRACT_REF_KEY,
            "deliverable": DELIVERABLE_REF_KEY,
            "project-rules": PROJECT_RULES_REF_KEY,
            "verification": VERIFICATION_REF_KEY,
            "review-report": REVIEW_REPORT_REF_KEY,
            "review-obligations": REVIEW_OBLIGATIONS_REF_KEY,
            "machine-feedback": MACHINE_FEEDBACK_REF_KEY,
        }.get(key, f"{key}_ref")
        return ui(
            f"## omac {key}\n"
            f"{title} was uploaded as an attachment.\n\n"
            f"- attachment: {filename}\n"
            f"- sha256: {sha}\n"
            f"- bytes: {size}\n"
            f"- metadata: `{ref_key}`\n\n"
            "Later Agents should read handoff context through `omac work show <issue-id> --output json`; "
            "programmatic references are stored in issue metadata.\n",
            f"## omac {key}\n"
            f"{title}已作为附件上传。\n\n"
            f"- attachment: {filename}\n"
            f"- sha256: {sha}\n"
            f"- bytes: {size}\n"
            f"- metadata: `{ref_key}`\n\n"
            "后续 Agent 应通过 `omac work show <issue-id> --output json` 读取交接上下文；"
            "程序化引用见 issue metadata。\n")

    def _download_attachment_bytes(
        self,
        attachment_id: str,
        filename: Optional[str],
        *,
        label: str,
        expected_sha256: str = "",
        expected_bytes: Optional[int] = None,
    ) -> Optional[bytes]:
        def download() -> Optional[bytes]:
            with tempfile.TemporaryDirectory(prefix="omac-attachment-") as td:
                self._run_multica([
                    "attachment", "download", attachment_id,
                    "--output-dir", td,
                ], capture=True)
                candidates = []
                if filename:
                    candidates.append(os.path.join(td, filename))
                candidates.extend(os.path.join(td, p) for p in os.listdir(td))
                for path in candidates:
                    if os.path.isfile(path):
                        with open(path, "rb") as f:
                            return f.read()
            return None

        expected_sha256 = expected_sha256.strip().lower()
        key = (attachment_id, expected_sha256, filename or "", expected_bytes)
        return self._attachment_bodies.get_or_load(
            key,
            lambda: self._run_idempotent_read(label, download),
            cacheable=lambda body: bool(expected_sha256) and (
                hashlib.sha256(body).hexdigest() == expected_sha256
            ),
        )

    def _load_payload_comment(self, item_id: str, key: str, ref: Optional[Dict[str, Any]]) -> Optional[str]:
        if not ref:
            return None
        attachment_id = ref.get("attachment_id")
        comment_id = ref.get("comment_id")
        if not attachment_id:
            if not comment_id:
                return None
            comments = self._run_multica([
                "issue", "comment", "list", item_id,
                "--thread", comment_id,
                "--output", "json",
                "--full",
            ])
            if not isinstance(comments, list):
                return None
            begin, end = self._payload_markers(key)
            for comment in comments:
                if comment.get("id") != comment_id:
                    continue
                content = comment.get("content") or ""
                if begin in content and end in content:
                    return content.split(begin, 1)[1].split(end, 1)[0].strip("\n")
                filename = ref.get("filename")
                for attachment in comment.get("attachments") or []:
                    if not filename or attachment.get("filename") == filename:
                        attachment_id = attachment.get("id")
                        break
        if not attachment_id:
            return None
        filename = ref.get("filename")
        declared_sha = str(ref.get("sha256") or "").strip()
        body = self._download_attachment_bytes(
            str(attachment_id),
            filename,
            label="attachment download",
            expected_sha256=declared_sha,
            expected_bytes=(
                ref.get("bytes") if isinstance(ref.get("bytes"), int) else None
            ),
        )
        if body is None:
            return None
        actual_sha = hashlib.sha256(body).hexdigest()
        if declared_sha and actual_sha != declared_sha:
            raise PlatformError(
                f"Downloaded {key} attachment digest does not match declared "
                f"SHA-256 for work item {item_id}")
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PlatformError(
                f"Downloaded {key} attachment is not valid UTF-8 for work item "
                f"{item_id}") from exc

    def observe_verification_attachment(
        self, item_id: str, ref: Dict[str, Any],
    ) -> VerificationAttachmentObservation:
        attachment_id = str(ref.get("attachment_id") or "").strip()
        comment_id = str(ref.get("comment_id") or "").strip()
        if not attachment_id or not comment_id:
            raise PlatformError(
                f"Verification attachment identity is incomplete for work item {item_id}")
        comments = self._run_multica([
            "issue", "comment", "list", item_id,
            "--thread", comment_id,
            "--output", "json",
            "--full",
        ])
        if not isinstance(comments, list):
            raise PlatformError(
                f"Verification comment observation is unavailable for work item {item_id}")
        attachment: Optional[Dict[str, Any]] = None
        for comment in comments:
            if not isinstance(comment, dict) or str(comment.get("id") or "") != comment_id:
                continue
            for candidate in comment.get("attachments") or []:
                if (
                    isinstance(candidate, dict)
                    and str(candidate.get("id") or "") == attachment_id
                ):
                    attachment = candidate
                    break
            if attachment is not None:
                break
        if attachment is None:
            raise PlatformError(
                f"Verification attachment {attachment_id} is not bound to comment "
                f"{comment_id} for work item {item_id}")
        declared_sha = str(ref.get("sha256") or "").strip()
        body = self._download_attachment_bytes(
            attachment_id,
            str(attachment.get("filename") or ref.get("filename") or "") or None,
            label="verification attachment observation",
            expected_sha256=declared_sha,
            expected_bytes=(
                ref.get("bytes") if isinstance(ref.get("bytes"), int) else None
            ),
        )
        if body is None:
            raise PlatformError(
                f"Verification attachment bytes are unavailable for work item {item_id}")
        actual_sha = hashlib.sha256(body).hexdigest()
        if declared_sha and declared_sha != actual_sha:
            raise PlatformError(
                f"Verification attachment digest mismatch for work item {item_id}")
        return VerificationAttachmentObservation(
            attachment_id=attachment_id,
            comment_id=comment_id,
            sha256=actual_sha,
            content=body,
            uploader_id=(
                str(attachment.get("uploader_id"))
                if attachment.get("uploader_id") else None
            ),
            uploader_type=(
                str(attachment.get("uploader_type"))
                if attachment.get("uploader_type") else None
            ),
            task_id=(
                str(attachment.get("task_id"))
                if attachment.get("task_id") else None
            ),
            created_at=(
                str(attachment.get("created_at"))
                if attachment.get("created_at") else None
            ),
        )

    def _issue_to_control_projection(
        self, issue_data: Dict, workspace_id: str,
    ) -> WorkItemControlProjection:
        """Map one Issue envelope without downloading attachment-backed bodies."""
        metadata = issue_data.get("metadata", {})
        unknown_persisted_fields = {
            f"metadata.{key}": value
            for key, value in metadata.items()
            if key not in _KNOWN_WORK_ITEM_METADATA_KEYS
        }
        for key, value in issue_data.items():
            if key in _KNOWN_ISSUE_FIELDS:
                continue
            if key in _READ_ONLY_ISSUE_ENVELOPE_FIELDS:
                continue
            if (
                key in _EMPTY_DEFAULT_ISSUE_ENVELOPE_FIELDS
                and value == _EMPTY_DEFAULT_ISSUE_ENVELOPE_FIELDS[key]
            ):
                continue
            unknown_persisted_fields[f"issue.{key}"] = value
        raw_assignee = issue_data.get("assignee_id") or issue_data.get("assignee")
        if isinstance(raw_assignee, dict):
            raw_assignee = raw_assignee.get("id") or raw_assignee.get("identifier")
        platform_assignee_id = str(raw_assignee) if raw_assignee else None

        blocked_by = metadata.get("blocked_by", [])
        if isinstance(blocked_by, str):
            try:
                blocked_by = json.loads(blocked_by)
            except Exception:
                blocked_by = []

        wave = metadata.get("wave")
        if isinstance(wave, str):
            try:
                wave = int(wave)
            except Exception:
                wave = None

        deliverable_ref_declared = DELIVERABLE_REF_KEY in metadata
        deliverable_ref = self._json_metadata(metadata, DELIVERABLE_REF_KEY)
        deliverable = None if deliverable_ref_declared else metadata.get(DELIVERABLE_KEY)

        project_rules_ref_declared = PROJECT_RULES_REF_KEY in metadata
        project_rules_ref = self._json_metadata(metadata, PROJECT_RULES_REF_KEY)
        project_rules = (
            None if project_rules_ref_declared else metadata.get(PROJECT_RULES_KEY)
        )

        verification_ref_declared = VERIFICATION_REF_KEY in metadata
        review_report_ref_declared = REVIEW_REPORT_REF_KEY in metadata
        verification_ref = self._json_metadata(metadata, VERIFICATION_REF_KEY)
        review_report_ref = self._json_metadata(metadata, REVIEW_REPORT_REF_KEY)
        review_ledger_ref = self._json_metadata(metadata, REVIEW_LEDGER_REF_KEY)
        review_generation = self._optional_text_metadata(
            metadata, REVIEW_GENERATION_KEY)
        review_ledger_generation = self._optional_text_metadata(
            metadata, REVIEW_LEDGER_GENERATION_KEY)
        bounce_baseline = self._json_metadata(metadata, BOUNCE_BASELINE_KEY)
        review_obligations_ref = self._json_metadata(
            metadata, REVIEW_OBLIGATIONS_REF_KEY)
        review_continuation = self._json_metadata(
            metadata, REVIEW_CONTINUATION_KEY)
        reviewer_run_baseline = self._json_metadata(
            metadata, REVIEWER_RUN_BASELINE_KEY)
        machine_feedback_ref = self._json_metadata(
            metadata, MACHINE_FEEDBACK_REF_KEY)
        decision_required = self._json_metadata(metadata, DECISION_REQUIRED_KEY)
        amendment_attempt = self._json_metadata(metadata, AMENDMENT_ATTEMPT_KEY)
        review_obligations = self._json_metadata(metadata, REVIEW_OBLIGATIONS_KEY)
        contract_ref = self._json_metadata(metadata, CONTRACT_REF_KEY)
        source_refs = self._json_metadata(metadata, SOURCE_REFS_KEY)
        verification = None
        if not verification_ref_declared:
            legacy_verification = self._json_metadata(metadata, "verification")
            verification = legacy_verification if isinstance(legacy_verification, dict) else None

        review_report = None
        if not review_report_ref_declared:
            legacy_report = self._json_metadata(metadata, "review_report")
            review_report = legacy_report if isinstance(legacy_report, dict) else None

        legacy_contract = self._json_metadata(metadata, "contract")
        contract = legacy_contract if isinstance(legacy_contract, dict) else None
        deferred_payloads = frozenset(
            payload for payload, ref in (
                (WorkItemPayload.DELIVERABLE, deliverable_ref),
                (WorkItemPayload.PROJECT_RULES, project_rules_ref),
                (WorkItemPayload.VERIFICATION, verification_ref),
                (WorkItemPayload.REVIEW_REPORT, review_report_ref),
                (WorkItemPayload.REVIEW_LEDGER, review_ledger_ref),
                (WorkItemPayload.REVIEW_OBLIGATIONS, review_obligations_ref),
                (WorkItemPayload.MACHINE_FEEDBACK, machine_feedback_ref),
                (WorkItemPayload.CONTRACT, contract_ref),
            )
            if isinstance(ref, dict) and ref
        )

        item = WorkItem(
            id=issue_data["id"],
            workspace_id=workspace_id,
            title=issue_data.get("title", ""),
            description=issue_data.get("description", ""),
            status=self._multica_to_status(issue_data.get("status", "todo")),
            identifier=issue_data.get("identifier"),
            dag_key=metadata.get("dag_key", ""),
            worker=metadata.get("worker"),
            reviewer=metadata.get("reviewer"),
            blocked_by=blocked_by if isinstance(blocked_by, list) else [],
            wave=wave,
            artifacts=self._json_metadata(metadata, "artifacts"),
            verification=verification,
            verification_ref=(
                verification_ref if isinstance(verification_ref, dict) and verification_ref
                else None),
            review_verdict=self._optional_text_metadata(metadata, "review_verdict"),
            review_comment=self._optional_text_metadata(metadata, "review_comment"),
            machine_feedback=None,
            machine_feedback_ref=(
                machine_feedback_ref),
            review_report=review_report,
            review_report_ref=(
                review_report_ref),
            review_subject_digest=self._optional_text_metadata(
                metadata, REVIEW_SUBJECT_DIGEST_KEY),
            review_obligations=(
                review_obligations if isinstance(review_obligations, list) else []),
            review_obligations_ref=(
                review_obligations_ref
                if isinstance(review_obligations_ref, dict) and review_obligations_ref
                else None),
            review_ledger=None,
            review_ledger_ref=(
                review_ledger_ref
                if isinstance(review_ledger_ref, dict) and review_ledger_ref
                else None),
            review_generation=review_generation,
            review_ledger_generation=review_ledger_generation,
            bounce_baseline=(
                bounce_baseline
                if isinstance(bounce_baseline, dict) and bounce_baseline
                else None),
            review_continuation=(
                review_continuation
                if isinstance(review_continuation, dict) and review_continuation
                else None),
            reviewer_run_baseline=parse_reviewer_run_baseline(
                reviewer_run_baseline),
            worker_handoff=parse_worker_handoff(
                self._json_metadata(metadata, WORKER_HANDOFF_KEY)),
            delivery_identity=parse_delivery_identity(
                self._json_metadata(metadata, DELIVERY_IDENTITY_KEY)),
            decision_required=decision_required,
            amendment_attempt=(
                amendment_attempt if isinstance(amendment_attempt, dict) else None),
            contract=contract,
            contract_ref=contract_ref if isinstance(contract_ref, dict) else None,
            source_refs=source_refs if isinstance(source_refs, list) else [],
            kind=parse_kind(metadata.get(KIND_KEY)),
            phase=parse_phase(metadata.get(PHASE_KEY)),
            bounces=parse_bounces(metadata),
            deliverable=deliverable,
            deliverable_ref=(
                deliverable_ref if isinstance(deliverable_ref, dict) and deliverable_ref
                else None),
            project_rules=project_rules,
            project_rules_ref=(
                project_rules_ref
                if isinstance(project_rules_ref, dict) and project_rules_ref
                else None),
            created_at=issue_data.get("created_at"),
            updated_at=issue_data.get("updated_at"),
            platform_assignee_id=platform_assignee_id,
            unknown_persisted_fields=unknown_persisted_fields,
        )
        return WorkItemControlProjection(item, deferred_payloads)

    def hydrate_work_item_evidence(
        self,
        projection: WorkItemControlProjection,
        plan: WorkItemHydrationPlan,
    ) -> WorkItem:
        item = projection.work_item
        requested = plan & projection.deferred_payloads
        updates: Dict[str, Any] = {}

        def load(payload: WorkItemPayload, label: str, ref_name: str) -> Optional[str]:
            if payload not in requested:
                return None
            ref = getattr(item, ref_name)
            return self._load_payload_comment(item.id, label, ref)

        deliverable = load(
            WorkItemPayload.DELIVERABLE, "deliverable", "deliverable_ref")
        if WorkItemPayload.DELIVERABLE in requested:
            updates["deliverable"] = deliverable

        project_rules = load(
            WorkItemPayload.PROJECT_RULES, "project-rules", "project_rules_ref")
        if WorkItemPayload.PROJECT_RULES in requested:
            updates["project_rules"] = project_rules

        verification_text = load(
            WorkItemPayload.VERIFICATION, "verification", "verification_ref")
        if WorkItemPayload.VERIFICATION in requested:
            updates["verification"] = parse_payload_text(verification_text)

        report_text = load(
            WorkItemPayload.REVIEW_REPORT, "review-report", "review_report_ref")
        if WorkItemPayload.REVIEW_REPORT in requested:
            updates["review_report"] = parse_payload_text(report_text)

        ledger_text = load(
            WorkItemPayload.REVIEW_LEDGER, "review-ledger", "review_ledger_ref")
        if WorkItemPayload.REVIEW_LEDGER in requested:
            review_ledger = parse_payload_text(ledger_text)
            if not isinstance(review_ledger, dict):
                raise PlatformError(ui(
                    "Could not load the review ledger attachment referenced by "
                    f"work item {item.id}. Restore a valid YAML/JSON review ledger, "
                    f"then rerun `omac work show {item.id} --output json`.",
                    f"无法读取或解析工作单元 {item.id} 引用的 review ledger 附件。"
                    "请恢复合法的 YAML/JSON 台账，然后重新执行 "
                    f"`omac work show {item.id} --output json`。"))
            updates["review_ledger"] = review_ledger

        obligations_text = load(
            WorkItemPayload.REVIEW_OBLIGATIONS,
            "review-obligations",
            "review_obligations_ref",
        )
        if WorkItemPayload.REVIEW_OBLIGATIONS in requested:
            try:
                review_obligations = yaml.safe_load(obligations_text)
            except yaml.YAMLError:
                review_obligations = None
            if not isinstance(review_obligations, list):
                raise PlatformError(ui(
                    "Could not load the review obligations attachment referenced by "
                    f"work item {item.id}. Restore a valid YAML/JSON review obligations "
                    f"list, then rerun `omac work show {item.id} --output json`.",
                    f"无法读取或解析工作单元 {item.id} 引用的 review obligations "
                    "附件。请恢复合法的 YAML/JSON obligations 列表，然后重新执行 "
                    f"`omac work show {item.id} --output json`。"))
            updates["review_obligations"] = review_obligations

        feedback_text = load(
            WorkItemPayload.MACHINE_FEEDBACK,
            "machine-feedback",
            "machine_feedback_ref",
        )
        if WorkItemPayload.MACHINE_FEEDBACK in requested:
            machine_feedback = parse_payload_text(feedback_text)
            expected_sha = (item.machine_feedback_ref or {}).get("sha256")
            digest_matches = (
                not expected_sha
                or (
                    isinstance(feedback_text, str)
                    and hashlib.sha256(feedback_text.encode("utf-8")).hexdigest()
                    == expected_sha
                )
            )
            if not is_machine_feedback(machine_feedback) or not digest_matches:
                raise PlatformError(ui(
                    "Could not load the machine feedback attachment referenced by "
                    f"work item {item.id}. Restore a valid omac.machine-feedback/v1 "
                    f"JSON attachment, then rerun `omac work show {item.id} --output json`.",
                    f"无法读取或解析工作单元 {item.id} 引用的 machine feedback "
                    "attachment。请恢复合法的 omac.machine-feedback/v1 JSON 附件，"
                    f"然后重新执行 `omac work show {item.id} --output json`。"))
            updates["machine_feedback"] = machine_feedback

        contract_text = load(
            WorkItemPayload.CONTRACT, "contract", "contract_ref")
        if WorkItemPayload.CONTRACT in requested:
            contract = parse_payload_text(contract_text)
            if contract is not None:
                updates["contract"] = contract

        return replace(item, **updates) if updates else item

    def evidence_hydration_parallelism(self, requested: int) -> int:
        """Multica payload reads use isolated subprocesses and temp dirs."""
        return max(1, requested)

    def control_observation_parallelism(self, requested: int) -> int:
        """Multica Issue reads use isolated CLI subprocesses."""
        return max(1, requested)

    def _issue_to_work_item(self, issue_data: Dict, workspace_id: str) -> WorkItem:
        projection = self._issue_to_control_projection(issue_data, workspace_id)
        return self.hydrate_work_item_evidence(
            projection, frozenset(WorkItemPayload))

    def _resolve_agent_id(self, agent_name: str) -> str:
        """agent 名 → id(assign 需要 id)。"""
        agents = self._run_multica(["agent", "list", "--output", "json"])
        if isinstance(agents, list):
            for agent in agents:
                if agent.get("name") == agent_name:
                    return agent.get("id")
        raise PlatformError(
            f"agent '{agent_name}' not found in workspace {self.config.workspace_id}")

    def resolve_agent_id(self, agent_name: str) -> str:
        return self._resolve_agent_id(agent_name)

    # ==================== 成员池 ====================

    def list_members(self, workspace_id: str) -> List[str]:
        """工作空间全量 agent(设计决策:不使用小队/分组等平台特有概念)。"""
        agents = self._run_multica(["agent", "list", "--output", "json"])
        if isinstance(agents, dict):
            agents = agents.get("agents") or []
        if not isinstance(agents, list):
            return []
        return [a.get("name") for a in agents if isinstance(a, dict) and a.get("name")]

    # ==================== 工作空间发现 ====================

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """multica workspace list --output json → WorkspaceInfo 列表。

        init 配置 / --check 体检用;认证失败或 CLI 缺失由 _run_multica 抛
        AuthError/PlatformError(调用方降级为本地体检)。
        """
        result = self._run_multica(["workspace", "list", "--output", "json"])
        if isinstance(result, dict):
            items = result.get("workspaces") or result.get("data") or []
        elif isinstance(result, list):
            items = result
        else:
            items = []
        infos: List[WorkspaceInfo] = []
        for w in items:
            if not isinstance(w, dict):
                continue
            wid = w.get("id")
            if not wid:
                continue
            infos.append(WorkspaceInfo(
                id=str(wid),
                name=w.get("name") or str(wid),
                description=w.get("description"),
                member_count=int(w.get("member_count") or 0),
            ))
        return infos

    # ==================== 项目发现 / 创建 ====================

    @staticmethod
    def _project_to_info(p: Dict) -> Optional[ProjectInfo]:
        pid = p.get("id")
        if not pid:
            return None
        repos: List[str] = []
        for r in (p.get("resources") or []):
            if not isinstance(r, dict) or r.get("type") not in (None, "github_repo"):
                continue
            ref = r.get("resource_ref") if isinstance(r.get("resource_ref"), dict) else {}
            url = r.get("url") or ref.get("url")
            if url:
                repos.append(url)
        return ProjectInfo(id=str(pid), title=p.get("title") or str(pid), repos=repos)

    def list_projects(self, workspace_id: str) -> List[ProjectInfo]:
        """multica project list --output json → ProjectInfo 列表。"""
        result = self._run_multica(["project", "list", "--output", "json"])
        if isinstance(result, dict):
            items = result.get("projects") or result.get("data") or []
        elif isinstance(result, list):
            items = result
        else:
            items = []
        infos: List[ProjectInfo] = []
        for p in items:
            if isinstance(p, dict):
                info = self._project_to_info(p)
                if info:
                    infos.append(info)
        return infos

    @staticmethod
    def _repo_url(entry: Any) -> Optional[str]:
        if isinstance(entry, str):
            return entry
        if not isinstance(entry, dict):
            return None
        ref = entry.get("resource_ref") if isinstance(entry.get("resource_ref"), dict) else {}
        return entry.get("url") or ref.get("url")

    def _workspace_repo_urls(self) -> set[str]:
        result = self._run_multica(["repo", "list", "--output", "json"])
        if isinstance(result, dict):
            items = result.get("repos") or result.get("repositories") or result.get("data") or []
        elif isinstance(result, list):
            items = result
        else:
            items = []
        return {url for url in (self._repo_url(item) for item in items) if url}

    def _ensure_workspace_repos(self, repo_urls: Optional[List[str]]) -> None:
        urls = [url for url in (repo_urls or []) if url]
        if not urls:
            return
        existing = self._workspace_repo_urls()
        missing = [url for url in urls if url not in existing]
        if missing:
            self._run_multica(["repo", "add", *missing, "--output", "json"])

    def create_project(
        self, workspace_id: str, title: str,
        repo_urls: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> ProjectInfo:
        """multica project create --repo + workspace repo registry ensure。"""
        args = ["project", "create", "--title", title, "--output", "json"]
        for url in (repo_urls or []):
            args += ["--repo", url]
        if description:
            args += ["--description", description]
        result = self._run_multica(args)
        if not isinstance(result, dict) or not result.get("id"):
            raise PlatformError(ui(
                f"Could not create project: {result}", f"创建 project 失败: {result}"))
        info = self._project_to_info(result)
        if info is None:
            raise PlatformError(ui(
                f"Project creation response is missing id: {result}",
                f"创建 project 返回缺少 id: {result}"))
        self._ensure_workspace_repos(repo_urls)
        if repo_urls:
            info.repos = list(repo_urls)
        return info

    # ==================== 工作单元 CRUD ====================

    def create_work_item(
        self,
        workspace_id: str,
        title: str,
        description: str,
        dag_key: str,
        worker: str,
        reviewer: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        wave: Optional[int] = None,
        initial_status: WorkItemStatus = WorkItemStatus.TODO,
        kind: TaskKind = TaskKind.DEVELOP,
    ) -> WorkItem:
        create_args = [
            "issue", "create",
            "--title", f"[DAG:{dag_key}] {title}",
            "--status", self._status_to_multica(initial_status),
            "--output", "json",
        ]
        if self.config.project_id:
            create_args += ["--project", self.config.project_id]
        result = self._run_multica_with_text_file(
            create_args, "--description-file", description)

        if not isinstance(result, dict) or "id" not in result:
            raise PlatformError(ui(
                f"Could not create issue: {result}", f"创建 issue 失败: {result}"))
        issue_id = result["id"]

        self._set_metadata(issue_id, "dag_key", dag_key)
        self._set_metadata(issue_id, "worker", worker)
        self._set_metadata(issue_id, KIND_KEY, kind.value)
        if reviewer:
            self._set_metadata(issue_id, "reviewer", reviewer)
        if blocked_by:
            self._set_metadata(issue_id, "blocked_by", blocked_by)
        if wave is not None:
            self._set_metadata(issue_id, "wave", str(wave))

        return self.get_work_item(issue_id)

    def _set_metadata(self, item_id: str, key: str, value: Any):
        # capture 默认开:吃掉 multica 的确认表格,不漏进编排者终端(进度靠事件流)。
        assert_metadata_write_allowed(key, value)
        encoded = encode_metadata_value(value)
        self._run_multica([
            "issue", "metadata", "set", item_id,
            "--key", key, "--value", encoded,
        ])

    def _read_issue_metadata(self, item_id: str) -> tuple[Dict, Dict]:
        issue = self._run_idempotent_read(
            "issue get",
            lambda: self._run_multica([
                "issue", "get", item_id, "--output", "json",
            ]),
        )
        if not isinstance(issue, dict) or not isinstance(issue.get("metadata"), dict):
            raise PlatformError(ui(
                f"Could not read metadata for issue {item_id}",
                f"无法读取 issue {item_id} 的 metadata",
            ))
        return issue, issue["metadata"]

    @staticmethod
    def _metadata_projection_matches(metadata: Dict, key: str, target: Any) -> bool:
        current = metadata.get(key)
        if key in _EMPTY_TEXT_METADATA:
            current = "" if current in (None, "") else current
            target = "" if target in (None, "") else target
            return current == target
        if key in _EMPTY_OBJECT_METADATA:
            if (
                key == REVIEW_REPORT_REF_KEY
                and key not in metadata
                and metadata.get("review_report") not in (None, {}, "")
            ):
                return False
            canonical_target = _project_object_metadata(target)
            if canonical_target is None:
                return _project_object_metadata(current) is None
            return encode_metadata_value(current) == encode_metadata_value(target)
        if key == PHASE_KEY:
            return parse_phase(current) == parse_phase(target)
        return encode_metadata_value(current) == encode_metadata_value(target)

    def _apply_metadata_projection(
        self,
        item_id: str,
        target: tuple[tuple[str, Any], ...],
        *,
        metadata: Optional[Dict] = None,
    ) -> None:
        if metadata is None:
            _, metadata = self._read_issue_metadata(item_id)
        for key, value in target:
            if self._metadata_projection_matches(metadata, key, value):
                continue
            self._set_metadata(item_id, key, value)
            metadata[key] = value

    def get_work_item(self, item_id: str) -> WorkItem:
        projection = self.observe_work_item_control(item_id)
        return self.hydrate_work_item_evidence(
            projection, frozenset(WorkItemPayload))

    def observe_work_item_control(
        self, item_id: str,
    ) -> WorkItemControlProjection:
        result = self._run_idempotent_read(
            "issue get",
            lambda: self._run_multica([
                "issue", "get", item_id, "--output", "json",
            ]),
        )
        if not isinstance(result, dict):
            raise PlatformError(ui(
                f"Could not get issue {item_id}", f"获取 issue {item_id} 失败"))
        return self._issue_to_control_projection(result, self.config.workspace_id)

    def control_batch_observation_supported(self) -> bool:
        """Return whether the configured project enables a true list-batch read."""
        return bool(self.config.project_id)

    def observe_work_item_controls(
        self, item_ids: List[str],
    ) -> Dict[str, WorkItemControlProjection]:
        """Batch-read project Issue envelopes without hydrating attachments.

        A project-scoped list is authoritative only for its returned records:
        every requested ID missing from that list is fetched individually so a
        pagination/indexing gap cannot be treated as a deleted work item.
        """
        if not item_ids:
            return {}
        if not self.config.project_id:
            return super().observe_work_item_controls(item_ids)

        issues = self._list_issues_paginated([
            "--project", self.config.project_id,
        ])
        listed_by_id = {
            str(issue["id"]): issue
            for issue in issues
            if isinstance(issue, dict) and issue.get("id")
        }
        observations: Dict[str, WorkItemControlProjection] = {}
        for item_id in item_ids:
            issue = listed_by_id.get(item_id)
            if issue is not None:
                observations[item_id] = self._issue_to_control_projection(
                    issue, self.config.workspace_id)
                continue
            try:
                observations[item_id] = self.observe_work_item_control(item_id)
            except WorkItemNotFoundError:
                continue
        return observations

    def set_authoring_identity(
        self, item_id: str, *, dag_key: str, kind: TaskKind,
    ) -> WorkItem:
        self._set_metadata(item_id, "dag_key", dag_key)
        self._set_metadata(item_id, KIND_KEY, kind.value)
        return self.get_work_item(item_id)

    def update_work_item_metadata(
        self,
        item_id: str,
        worker: Optional[str] = None,
        reviewer: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        review_verdict: Optional[str] = None,
        review_comment: Optional[str] = None,
        machine_feedback: Optional[Dict[str, Any]] = None,
        machine_feedback_source: Optional[str] = None,
        verification: Optional[Dict[str, Any]] = None,
        verification_source: Optional[str] = None,
        review_report: Optional[Dict[str, Any]] = None,
        review_report_source: Optional[str] = None,
        review_subject_digest: Optional[str] = None,
        review_obligations: Optional[List[Dict[str, Any]]] = None,
        review_ledger: Optional[Dict[str, Any]] = None,
        review_ledger_source: Optional[str] = None,
        review_generation: Optional[str] = None,
        review_ledger_generation: Optional[str] = None,
        bounce_baseline: Optional[Dict[str, int]] = None,
        review_continuation: Optional[Dict[str, Any]] = None,
        reviewer_run_baseline: Optional[
            ReviewerRunBaseline | Dict[str, Any]
        ] = None,
        worker_handoff: Optional[WorkerHandoffIntent | Dict[str, Any]] = None,
        delivery_identity: Optional[DeliveryIdentity | Dict[str, Any]] = None,
        decision_required: Optional[Dict[str, Any]] = None,
        amendment_attempt: Optional[Dict[str, Any]] = None,
        phase: Optional[TaskPhase] = None,
        worker_bounce: Optional[int] = None,
        ci_bounce: Optional[int] = None,
        review_bounce: Optional[int] = None,
        merge_bounce: Optional[int] = None,
        deliverable: Optional[str] = None,
        project_rules: Optional[str] = None,
        source_refs: Optional[List[Dict[str, Any]]] = None,
        description: Optional[str] = None,
    ) -> WorkItem:
        # 正文修复必须最先执行。旧版 OMAC 可能已把大型上游产物内联到
        # description；Multica 的 metadata API 会解析并返回整条 issue，
        # 若先写 metadata，恢复流程会在有机会压缩正文前超时。
        if description is not None:
            try:
                self._run_multica_with_text_file(
                    ["issue", "update", item_id], "--description-file", description)
            except PlatformError as exc:
                error = str(exc).lower()
                resolver_timeout = (
                    "context deadline exceeded" in error
                    or "client.timeout" in error
                )
                if not resolver_timeout:
                    raise
                self._put_issue_description_direct(item_id, description)
        if worker is not None:
            self._set_metadata(item_id, "worker", worker)
        if reviewer is not None:
            self._set_metadata(item_id, "reviewer", reviewer)
        if blocked_by is not None:
            self._set_metadata(item_id, "blocked_by", blocked_by)
        if artifacts is not None:
            self._set_metadata(item_id, "artifacts", artifacts)
        if machine_feedback is not None or machine_feedback_source is not None:
            if machine_feedback_source is None and machine_feedback:
                machine_feedback_source = dump_machine_feedback(machine_feedback)
            if machine_feedback_source:
                parsed = parse_machine_feedback(machine_feedback_source)
                if parsed is None or (
                    machine_feedback is not None and machine_feedback != parsed
                ):
                    raise ValidationError(ui(
                        "Machine feedback must use schema omac.machine-feedback/v1",
                        "machine feedback 必须使用 omac.machine-feedback/v1 schema"))
                ref = self._publish_payload_comment(
                    item_id, "machine-feedback", machine_feedback_source, ".json")
                self._set_metadata(item_id, MACHINE_FEEDBACK_REF_KEY, ref)
            else:
                self._set_metadata(item_id, MACHINE_FEEDBACK_REF_KEY, "{}")
        if review_comment is not None:
            self._set_metadata(item_id, "review_comment", review_comment)
        if verification is not None and verification_source is None:
            verification_source = json.dumps(verification, ensure_ascii=False, indent=2)
        if verification_source is not None:
            ref = self._publish_payload_comment(
                item_id, "verification", verification_source, ".yaml")
            self._set_metadata(item_id, VERIFICATION_REF_KEY, ref)
        if review_report is not None and review_report_source is None:
            review_report_source = json.dumps(review_report, ensure_ascii=False, indent=2)
        if review_report_source is not None:
            ref = self._publish_payload_comment(
                item_id, "review-report", review_report_source, ".yaml")
            self._set_metadata(item_id, REVIEW_REPORT_REF_KEY, ref)
        if review_obligations is not None:
            source = yaml.safe_dump(
                review_obligations, allow_unicode=True, sort_keys=False)
            ref = self._publish_payload_comment(
                item_id, "review-obligations", source, ".yaml")
            self._set_metadata(item_id, REVIEW_OBLIGATIONS_REF_KEY, ref)
        if review_ledger is not None and review_ledger_source is None:
            review_ledger_source = json.dumps(
                review_ledger, ensure_ascii=False, indent=2)
        if review_ledger_source is not None:
            ref = self._publish_payload_comment(
                item_id, "review-ledger", review_ledger_source, ".yaml")
            self._set_metadata(item_id, REVIEW_LEDGER_REF_KEY, ref)
        if review_generation is not None:
            self._set_metadata(item_id, REVIEW_GENERATION_KEY, review_generation)
        if review_ledger_generation is not None:
            self._set_metadata(
                item_id, REVIEW_LEDGER_GENERATION_KEY,
                review_ledger_generation)
        if bounce_baseline is not None:
            self._set_metadata(item_id, BOUNCE_BASELINE_KEY, bounce_baseline)
        if review_continuation is not None:
            self._set_metadata(
                item_id, REVIEW_CONTINUATION_KEY, review_continuation)
        if reviewer_run_baseline is not None:
            value = (
                reviewer_run_baseline.as_dict()
                if isinstance(reviewer_run_baseline, ReviewerRunBaseline)
                else reviewer_run_baseline
            )
            self._set_metadata(item_id, REVIEWER_RUN_BASELINE_KEY, value)
        if worker_handoff is not None:
            value = (
                worker_handoff.as_dict()
                if isinstance(worker_handoff, WorkerHandoffIntent)
                else worker_handoff
            )
            self._set_metadata(item_id, WORKER_HANDOFF_KEY, value)
        if delivery_identity is not None:
            value = (
                delivery_identity.as_dict()
                if isinstance(delivery_identity, DeliveryIdentity)
                else delivery_identity
            )
            self._set_metadata(item_id, DELIVERY_IDENTITY_KEY, value)
        if review_subject_digest is not None:
            self._set_metadata(
                item_id, REVIEW_SUBJECT_DIGEST_KEY, review_subject_digest)
        if decision_required is not None:
            self._set_metadata(item_id, DECISION_REQUIRED_KEY, decision_required)
        if amendment_attempt is not None:
            self._set_metadata(item_id, AMENDMENT_ATTEMPT_KEY, amendment_attempt)
        if worker_bounce is not None:
            self._set_metadata(item_id, WORKER_BOUNCE_KEY, str(worker_bounce))
        if ci_bounce is not None:
            self._set_metadata(item_id, CI_BOUNCE_KEY, str(ci_bounce))
        if review_bounce is not None:
            self._set_metadata(item_id, REVIEW_BOUNCE_KEY, str(review_bounce))
        if merge_bounce is not None:
            self._set_metadata(item_id, MERGE_BOUNCE_KEY, str(merge_bounce))
        delivery_refs = []
        if deliverable is not None:
            delivery_refs.append((
                DELIVERABLE_REF_KEY,
                self._publish_payload_comment(
                    item_id, "deliverable", deliverable, ".md"),
            ))
        if project_rules is not None:
            delivery_refs.append((
                PROJECT_RULES_REF_KEY,
                self._publish_payload_comment(
                    item_id, "project-rules", project_rules, ".md"),
            ))
        for key, ref in delivery_refs:
            self._set_metadata(item_id, key, ref)
        if source_refs is not None:
            self._set_metadata(item_id, SOURCE_REFS_KEY, source_refs)
        if phase is not None:
            self._set_metadata(item_id, PHASE_KEY, phase.value)
        # verdict 是终态可见信号；所有报告和 ledger 证据必须先持久化。
        if review_verdict is not None:
            self._set_metadata(item_id, "review_verdict", review_verdict)
        return self.get_work_item(item_id)

    def restore_authoring_generation(
        self,
        item_id: str,
        contract: Any,
        review_generation: str,
        bounce_baseline: Optional[Dict[str, int]] = None,
    ) -> WorkItem:
        """Publish the contract, then reset the issue control projection.

        Not one-shot atomic: one issue PUT fixes the dispatch fields
        (status/assignee/suppress_run) first so the issue becomes
        non-dispatchable, then each recovery metadata key is persisted
        through ``issue metadata set``.  The real Multica server silently
        ignores a ``metadata`` field in the issue PUT body (metadata is a
        separate KV sub-resource), so recovery metadata must go through the
        metadata CLI path or the read-back never converges.  Recovery runs
        with the DAG runner stopped; a partial failure leaves todo status
        with stale metadata, and re-running this restore skips
        already-matching keys and converges.
        """
        from ..core.manifest import _dump_contract

        payload = _dump_contract(contract) if not isinstance(contract, dict) else contract
        source = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        contract_ref = self._publish_payload_comment(
            item_id, "contract", source, ".yaml")
        _issue, metadata = self._read_issue_metadata(item_id)
        controls = (
            *_REVIEW_CLEAR_METADATA,
            (REVIEW_SUBJECT_DIGEST_KEY, ""),
            (REVIEW_OBLIGATIONS_KEY, []),
            (REVIEW_OBLIGATIONS_REF_KEY, "{}"),
            (REVIEW_CONTINUATION_KEY, "{}"),
            (WORKER_HANDOFF_KEY, "{}"),
            (DELIVERY_IDENTITY_KEY, "{}"),
            (PHASE_KEY, TaskPhase.AUTHORING.value),
            (REVIEW_GENERATION_KEY, review_generation),
            (BOUNCE_BASELINE_KEY, bounce_baseline or {}),
            (CONTRACT_REF_KEY, contract_ref),
            ("reviewer", ""),
        )
        self._put_issue_fields_direct(
            item_id,
            {
                "status": "todo",
                "assignee_type": None,
                "assignee_id": None,
                "suppress_run": True,
            },
            operation="authoring-generation recovery",
        )
        self._apply_metadata_projection(item_id, controls, metadata=metadata)
        self._pending_assignment_wakes.discard(item_id)
        return self.get_work_item(item_id)

    def set_node_contract(self, item_id: str, contract: Any):
        from ..core.manifest import _dump_contract
        payload = _dump_contract(contract) if not isinstance(contract, dict) else contract
        source = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        ref = self._publish_payload_comment(item_id, "contract", source, ".yaml")
        self._set_metadata(item_id, CONTRACT_REF_KEY, ref)

    # multica issue list 服务端单页上限 100;更大的 --limit 会被静默截断。
    # 实际页大小取 30:每条 issue 携带大体量 metadata 时,满 100 条的响应体
    # 会超过 multica CLI 客户端的读取超时(context deadline exceeded),
    # 小页读取可稳定完成。
    _LIST_PAGE_SIZE = 30

    def _list_issues_paginated(self, extra_args: List[str]) -> List[Dict]:
        issues: List[Dict] = []
        offset = 0
        while True:
            result = self._run_multica([
                "issue", "list",
                "--limit", str(self._LIST_PAGE_SIZE),
                "--offset", str(offset),
                "--output", "json",
            ] + extra_args)
            if isinstance(result, dict) and "issues" in result:
                page = result["issues"]
            elif isinstance(result, list):
                page = result
            else:
                page = []
            issues.extend(page)
            if len(page) < self._LIST_PAGE_SIZE:
                break
            offset += len(page)
        return issues

    def list_work_items(
        self,
        workspace_id: str,
        status: Optional[WorkItemStatus] = None,
    ) -> List[WorkItem]:
        extra_args: List[str] = []
        if self.config.project_id:
            extra_args += ["--project", self.config.project_id]
        if status is not None:
            extra_args += ["--status", self._status_to_multica(status)]
        issues = self._list_issues_paginated(extra_args)
        work_items = [self._issue_to_work_item(i, workspace_id) for i in issues]
        # 服务端按平台态过滤后,再按业务态精确收口(多对一映射的兜底)
        if status is not None:
            work_items = [i for i in work_items if i.status == status]
        return work_items

    def add_comment(self, item_id: str, comment: str):
        self._unassign_before_system_comment(item_id)
        self._run_multica_with_text_file(
            ["issue", "comment", "add", item_id],
            "--content-file", comment)

    # ==================== 状态和分配 ====================

    def update_status(self, item_id: str, status: WorkItemStatus):
        self._run_multica([
            "issue", "update", item_id,
            "--status", self._status_to_multica(status),
        ])

    def cancel_work_item(self, item_id: str) -> None:
        """Multica 原生 cancelled 态:从活跃列表移除(区别于 blocked)。"""
        self._run_multica(["issue", "status", item_id, "cancelled"])

    def reset_review(self, item_id: str):
        self._apply_metadata_projection(item_id, (
            *_REVIEW_CLEAR_METADATA,
            (REVIEW_SUBJECT_DIGEST_KEY, ""),
            (PHASE_KEY, TaskPhase.AUTHORING.value),
        ))

    def prepare_review_cycle(self, item_id: str, subject_digest: str) -> WorkItem:
        issue, metadata = self._read_issue_metadata(item_id)
        current = self._issue_to_control_projection(
            issue, self.config.workspace_id).work_item
        if (
            current.phase == TaskPhase.REVIEW
            and current.review_subject_digest == subject_digest
        ):
            return self.get_work_item(item_id)
        self._apply_metadata_projection(item_id, (
            *_REVIEW_CLEAR_METADATA,
            (REVIEW_SUBJECT_DIGEST_KEY, subject_digest),
            (PHASE_KEY, TaskPhase.REVIEW.value),
        ), metadata=metadata)
        return self.get_work_item(item_id)

    def assign_work_item(
        self,
        item_id: str,
        assignee: str,
        role: str,
        *,
        start_run: bool = True,
    ):
        agent_id = self._resolve_agent_id(assignee)
        current = None
        if start_run:
            current = self._run_multica([
                "issue", "get", item_id, "--output", "json",
            ])
        if role == "worker":
            self.update_work_item_metadata(item_id, worker=assignee)
        elif role == "reviewer":
            self.update_work_item_metadata(item_id, reviewer=assignee)
        if not start_run:
            self._pending_assignment_wakes.discard(item_id)
            self._put_issue_fields_direct(
                item_id,
                {
                    "assignee_type": "agent",
                    "assignee_id": agent_id,
                    "suppress_run": True,
                },
                operation="suppressed assignment",
            )
            return

        current_assignee_id = (
            str(current.get("assignee_id"))
            if isinstance(current, dict) and current.get("assignee_id")
            else None
        )
        self._run_multica(["issue", "assign", item_id, "--to", agent_id])
        # 改派到不同 agent 时，Multica assignment 会创建 run，随后的 wake
        # 只需确认，避免再 rerun 一次。同一 assignee 的 assign 是幂等更新，
        # 不会创建 run；此时保留 wake 的终态检查，让它对旧 run 执行一次 rerun。
        if current_assignee_id != agent_id:
            self._mark_assignment_wake_pending(item_id)

    def clear_assignment(self, item_id: str) -> None:
        self._run_multica(["issue", "assign", item_id, "--unassign"])
        self._set_metadata(item_id, "reviewer", "")

    def normalize_confirmed_merge(self, item_id: str) -> None:
        self._put_issue_fields_direct(
            item_id,
            {
                "status": "done",
                "assignee_type": None,
                "assignee_id": None,
                "suppress_run": True,
            },
            operation="confirmed-merge normalization",
        )

    def request_pull_request_merge(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> MergeCommandResult:
        try:
            proc = subprocess.run(
                command.replace("{pr_url}", pr_url), shell=True,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            output = "".join(
                stream.decode("utf-8", errors="replace")
                if isinstance(stream, bytes) else stream or ""
                for stream in (exc.stdout, exc.stderr))
            return MergeCommandResult(False, None, output, timed_out=True)
        except FileNotFoundError as exc:
            return MergeCommandResult(False, None, str(exc))
        return MergeCommandResult(
            proc.returncode == 0, proc.returncode,
            (proc.stdout or "") + (proc.stderr or ""))

    def observe_pull_request(self, pr_url: str) -> PullRequestObservation:
        try:
            proc = subprocess.run(
                ["gh", "pr", "view", pr_url, "--json",
                 MULTICA_PR_VIEW_FIELDS],
                capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return PullRequestObservation(PullRequestState.UNKNOWN, detail=str(exc))
        if proc.returncode != 0:
            return PullRequestObservation(
                PullRequestState.UNKNOWN, detail=(proc.stderr or proc.stdout or "").strip())
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            return PullRequestObservation(PullRequestState.UNKNOWN, detail=str(exc))
        state = str(payload.get("state") or "").upper()
        merged_at = payload.get("mergedAt")
        if state == "MERGED":
            return PullRequestObservation(PullRequestState.MERGED, merged_at=merged_at)
        if state == "OPEN":
            merge_state = str(payload.get("mergeStateStatus") or "").upper()
            if payload.get("autoMergeRequest") or merge_state == "QUEUED":
                return PullRequestObservation(PullRequestState.PENDING)
            if merge_state == "UNKNOWN" or not merge_state:
                return PullRequestObservation(
                    PullRequestState.UNKNOWN,
                    detail="missing or unknown mergeStateStatus")
            return PullRequestObservation(PullRequestState.OPEN)
        if state == "CLOSED":
            return PullRequestObservation(PullRequestState.CLOSED_UNMERGED)
        return PullRequestObservation(PullRequestState.UNKNOWN, detail=f"unexpected PR state: {state}")

    def check_pull_request(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> PullRequestCheckResult:
        try:
            proc = subprocess.run(
                command.replace("{pr_url}", pr_url), shell=True,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            output = "".join(
                stream.decode("utf-8", errors="replace")
                if isinstance(stream, bytes) else stream or ""
                for stream in (exc.stdout, exc.stderr))
            return PullRequestCheckResult(False, None, output, timed_out=True)
        except FileNotFoundError as exc:
            return PullRequestCheckResult(False, None, str(exc))
        return PullRequestCheckResult(
            proc.returncode == 0, proc.returncode,
            (proc.stdout or "") + (proc.stderr or ""))

    def read_pull_request_readiness(
        self, pr_url: str,
    ) -> PullRequestReadiness | PullRequestReadinessFailure:
        try:
            proc = subprocess.run(
                ["gh", "pr", "view", pr_url, "--json", "isDraft,state,headRefOid"],
                capture_output=True, text=True, timeout=30)
        except FileNotFoundError as exc:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MISSING_CLI, str(exc))
        except subprocess.TimeoutExpired as exc:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.TIMEOUT, str(exc))
        if proc.returncode != 0:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.COMMAND,
                (proc.stderr or proc.stdout or "").strip())
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MALFORMED, str(exc))
        if not isinstance(payload, dict):
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MALFORMED, "readiness payload is not an object")
        is_draft = payload.get("isDraft")
        state = payload.get("state")
        head_sha = payload.get("headRefOid")
        if (
            not isinstance(is_draft, bool)
            or not isinstance(state, str) or not state
            or not isinstance(head_sha, str) or not head_sha
        ):
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MALFORMED,
                "readiness payload is missing typed isDraft/state/headRefOid fields")
        return PullRequestReadiness(is_draft, state, head_sha=head_sha)


class MulticaRuntime(AgentRuntime):
    """执行面:默认 assignment 由 Multica 启动 Run；静默 assignment 则由 wake
    在确认没有活跃 Run 后通过 issue rerun 显式启动。"""

    def __init__(
        self,
        store: MulticaStore,
        *,
        active_observation_attempts: int = 4,
        active_observation_interval: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if active_observation_attempts < 1:
            raise ValueError("active_observation_attempts must be at least 1")
        if active_observation_interval < 0:
            raise ValueError("active_observation_interval must not be negative")
        self._store = store
        self._active_observation_attempts = active_observation_attempts
        self._active_observation_interval = active_observation_interval
        self._sleeper = sleeper

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(stable_direct_run_identity=True)

    def _issue_runs(self, item_id: str) -> List[Dict[str, Any]]:
        runs = self._store._run_multica([
            "issue", "runs", item_id, "--output", "json",
        ])
        if not isinstance(runs, list):
            raise PlatformError("Malformed Multica run payload: expected a list")
        validated = []
        for run in runs:
            if not isinstance(run, dict):
                raise PlatformError("Malformed Multica run payload: expected objects")
            run_id = run.get("id")
            status = run.get("status")
            if not isinstance(run_id, str) or not run_id:
                raise PlatformError("Malformed Multica run payload: missing run id")
            if not isinstance(status, str) or status.lower() not in _KNOWN_RUN_STATUSES:
                raise PlatformError(
                    f"Malformed Multica run payload: unknown status {status!r} "
                    f"for {run_id}")
            validated.append(run)
        return validated

    @staticmethod
    def _has_active_run(runs: List[Dict[str, Any]]) -> bool:
        return any(
            (run.get("status") or "").lower() in _ACTIVE_RUN_STATUSES
            for run in runs
        )

    @staticmethod
    def _has_active_direct_run_for_agent(
        runs: List[Dict[str, Any]], agent_id: str,
    ) -> bool:
        return any(
            (run.get("kind") or "direct") == "direct"
            and str(run.get("agent_id") or "") == agent_id
            and (run.get("status") or "").lower() in _ACTIVE_RUN_STATUSES
            for run in runs
        )

    @staticmethod
    def _has_foreign_active_direct_run(
        runs: List[Dict[str, Any]], agent_id: str,
    ) -> bool:
        return any(
            (run.get("kind") or "direct") == "direct"
            and str(run.get("agent_id") or "") != agent_id
            and (run.get("status") or "").lower() in _ACTIVE_RUN_STATUSES
            for run in runs
        )

    def wake(self, item_id: str, agent: str, role: str) -> None:
        if self._store._consume_assignment_wake_pending(item_id):
            return None
        strict_reviewer_handoff = role == "reviewer"
        expected_agent_id: str | None = None
        if strict_reviewer_handoff:
            resolved_agent_id = self._store._resolve_agent_id(agent)
            if not resolved_agent_id:
                raise PlatformError(f"Could not resolve Multica agent {agent}")
            expected_agent_id = str(resolved_agent_id)
        runs = self._issue_runs(item_id)
        latest = _latest_direct_run(runs)
        if strict_reviewer_handoff:
            if self._has_active_direct_run_for_agent(
                runs, expected_agent_id,
            ):
                return None
            for _attempt in range(1, self._active_observation_attempts):
                if (
                    latest
                    and (latest.get("status") or "").lower()
                    in _RERUNNABLE_DIRECT_RUN_STATUSES
                    and not self._has_foreign_active_direct_run(
                        runs, expected_agent_id)
                ):
                    break
                self._sleeper(self._active_observation_interval)
                runs = self._issue_runs(item_id)
                if self._has_active_direct_run_for_agent(
                    runs, expected_agent_id,
                ):
                    return None
                latest = _latest_direct_run(runs)
            if self._has_foreign_active_direct_run(
                runs, expected_agent_id,
            ):
                raise PlatformError(
                    f"Reviewer handoff for {item_id} is still waiting for the prior "
                    "direct Run to finish")
        else:
            if self._has_active_run(runs):
                return None
            if not latest or (
                (latest.get("status") or "").lower()
                not in _RERUNNABLE_DIRECT_RUN_STATUSES
            ):
                return None
            for _attempt in range(1, self._active_observation_attempts):
                self._sleeper(self._active_observation_interval)
                runs = self._issue_runs(item_id)
                if self._has_active_run(runs):
                    return None
                latest = _latest_direct_run(runs)
                if not latest or (
                    (latest.get("status") or "").lower()
                    not in _RERUNNABLE_DIRECT_RUN_STATUSES
                ):
                    return None
        if not latest or (
            (latest.get("status") or "").lower()
            not in _RERUNNABLE_DIRECT_RUN_STATUSES
        ):
            return None
        direct_run_ids = _direct_run_ids(runs)
        try:
            rerun = self._store._run_multica([
                "issue", "rerun", item_id, "--output", "json",
            ])
        except PlatformError as rerun_error:
            try:
                observed_runs = self._issue_runs(item_id)
            except PlatformError:
                raise rerun_error from None
            candidates = [
                run for run in observed_runs
                if (run.get("kind") or "direct") == "direct"
                and run.get("id")
                and str(run["id"]) not in direct_run_ids
                and _is_manual_rerun(run)
            ]
            if len(candidates) != 1:
                raise rerun_error from None
            if expected_agent_id is None:
                try:
                    resolved_agent_id = self._store._resolve_agent_id(agent)
                except PlatformError:
                    raise rerun_error from None
                if not resolved_agent_id:
                    raise rerun_error from None
                expected_agent_id = str(resolved_agent_id)
            candidate_agent_id = candidates[0].get("agent_id")
            if (
                candidate_agent_id
                and str(candidate_agent_id) == expected_agent_id
            ):
                return None
            raise rerun_error from None
        if not strict_reviewer_handoff:
            return None
        if not isinstance(rerun, dict) or not rerun.get("id"):
            raise PlatformError(
                f"Multica rerun for {item_id} did not return a task identity")
        task_id = str(rerun["id"])
        response_agent_id = rerun.get("agent_id")
        if (
            response_agent_id
            and str(response_agent_id) != expected_agent_id
        ):
            raise PlatformError(
                f"Multica rerun {task_id} targets unexpected agent "
                f"{response_agent_id}; expected {expected_agent_id}")
        for attempt in range(self._active_observation_attempts):
            observed_runs = self._issue_runs(item_id)
            observed = next(
                (run for run in observed_runs if str(run.get("id") or "") == task_id),
                None,
            )
            if observed is not None:
                if (
                    (observed.get("kind") or "direct") != "direct"
                    or str(observed.get("agent_id") or "") != expected_agent_id
                    # 成功路径的 Run 身份已由 rerun 响应返回的精确 id 证明。
                    # 真实 multica 服务端把经 assignment 管道落地的 rerun
                    # 标记为 issue_assignment，从不产出 trigger kind "rerun"；
                    # 只认 "rerun" 会让 reviewer 续跑 read-back 永远失败。
                    # 无归因（None）仍 fail-closed。
                    or _run_trigger_kind(observed)
                    not in {"rerun", "issue_assignment"}
                ):
                    raise PlatformError(
                        f"Multica rerun {task_id} is not the expected fresh "
                        f"direct Run for agent {expected_agent_id}")
                return None
            if attempt + 1 < self._active_observation_attempts:
                self._sleeper(self._active_observation_interval)
        raise PlatformError(
            f"Multica rerun {task_id} for {item_id} is not observable")

    def cancel(self, item_id: str) -> bool:
        runs = self._store._run_multica(["issue", "runs", item_id, "--output", "json"])
        latest = _latest_direct_run(runs if isinstance(runs, list) else [])
        cancelled = False
        if latest and (
            (latest.get("status") or "").lower() in _ACTIVE_RUN_STATUSES
        ):
            task_id = latest.get("id")
            if task_id:
                self._store._run_multica([
                    "issue", "cancel-task", str(task_id),
                    "--issue", item_id, "--output", "json",
                ])
                cancelled = True

        return cancelled

    def is_active(self, item_id: str) -> bool:
        return self._has_active_run(self._issue_runs(item_id))

    def list_runs(self, item_id: str) -> List[AgentRunObservation]:
        runs = self._issue_runs(item_id)
        return [
            AgentRunObservation(
                id=str(run.get("id")),
                kind=str(run.get("kind") or "direct").lower(),
                status=str(run.get("status") or "").lower(),
                agent_id=(
                    str(run.get("agent_id")) if run.get("agent_id") else None
                ),
                created_at=(
                    str(run.get("created_at")) if run.get("created_at") else None
                ),
                updated_at=(
                    str(run.get("updated_at") or run.get("completed_at"))
                    if run.get("updated_at") or run.get("completed_at") else None
                ),
                error=(str(run["error"]) if run.get("error") else None),
                retry_of_run_id=(
                    str(run.get("retry_of_task_id") or run.get("parent_task_id"))
                    if run.get("retry_of_task_id") or run.get("parent_task_id")
                    else None
                ),
                trigger_kind=_run_trigger_kind(run),
            )
            for run in runs
            if isinstance(run, dict) and run.get("id")
        ]

    @staticmethod
    def _items(payload, key: str) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            items = payload.get(key) or payload.get("data") or []
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def list_targets(self) -> List[RuntimeTarget]:
        payload = self._store._run_multica(["runtime", "list", "--output", "json"])
        targets = []
        for item in self._items(payload, "runtimes"):
            runtime_id = item.get("id")
            if not runtime_id:
                continue
            targets.append(RuntimeTarget(
                id=str(runtime_id),
                name=str(item.get("name") or runtime_id),
                type=str(
                    item.get("runtime_type") or item.get("type")
                    or item.get("provider") or item.get("runtime_mode") or ""),
                status=str(item.get("status") or "unknown"),
            ))
        return targets

    @staticmethod
    def _write_skill_archive(skill: SkillPackage, destination: str) -> None:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in skill.files:
                archive.write(path, path.relative_to(skill.path).as_posix())

    def _ensure_skill_ids(self, skills: List[SkillPackage]) -> List[str]:
        payload = self._store._run_multica(["skill", "list", "--output", "json"])
        current = {
            str(item.get("name")): str(item.get("id"))
            for item in self._items(payload, "skills")
            if item.get("name") and item.get("id")
        }
        for skill in skills:
            if skill.name in current:
                continue
            fd, archive_path = tempfile.mkstemp(prefix=f"omac-{skill.name}-", suffix=".zip")
            os.close(fd)
            try:
                self._write_skill_archive(skill, archive_path)
                result = self._store._run_multica([
                    "skill", "import", "--file", archive_path,
                    "--on-conflict", "skip", "--output", "json",
                ])
            finally:
                try:
                    os.unlink(archive_path)
                except FileNotFoundError:
                    pass
            if isinstance(result, dict) and result.get("id"):
                current[skill.name] = str(result["id"])

        missing = [skill.name for skill in skills if skill.name not in current]
        if missing:
            payload = self._store._run_multica(["skill", "list", "--output", "json"])
            current.update({
                str(item.get("name")): str(item.get("id"))
                for item in self._items(payload, "skills")
                if item.get("name") and item.get("id")
            })
            missing = [name for name in missing if name not in current]
        if missing:
            raise PlatformError(ui(
                f"Could not resolve Skill IDs after upload: {', '.join(missing)}. "
                "Run `multica skill list` to inspect them.",
                f"Skill 上传后仍无法解析 ID:{', '.join(missing)} —— 运行 `multica skill list` 检查"))
        return [current[skill.name] for skill in skills]

    def provision_agent(self, spec: AgentProvisionSpec) -> AgentInfo:
        if not spec.name.strip():
            raise ValidationError(ui("Agent name cannot be empty", "Agent 名称不能为空"))
        agents = self._items(
            self._store._run_multica(["agent", "list", "--output", "json"]), "agents")
        if any(item.get("name") == spec.name for item in agents):
            raise ValidationError(ui(
                f"Agent '{spec.name}' already exists. Choose it or use another name.",
                f"Agent '{spec.name}' 已存在 —— 请选择已有 Agent 或换一个名称"))

        skill_ids = self._ensure_skill_ids(spec.skills)
        result = self._store._run_multica([
            "agent", "create",
            "--name", spec.name,
            "--description", spec.description,
            "--instructions", spec.instructions,
            "--runtime-id", spec.runtime_id,
            "--visibility", "workspace",
            "--output", "json",
        ])
        if not isinstance(result, dict) or not result.get("id"):
            raise PlatformError(ui(
                "Agent creation response is missing id. Run `multica agent list --output json`.",
                "Agent 创建成功响应缺少 id —— 运行 `multica agent list --output json` 检查"))
        agent = AgentInfo(id=str(result["id"]), name=str(result.get("name") or spec.name))
        if skill_ids:
            self._store._run_multica([
                "agent", "skills", "set", agent.id,
                "--skill-ids", ",".join(skill_ids),
                "--output", "json",
            ])
        return agent

    def describe(self) -> str:
        return ui(
            "multica: assign wakes the daemon-managed agent CLI; wake is a confirming no-op",
            "multica: assign 即唤醒(daemon 认领并拉起 agent CLI),wake 为确认性 no-op")

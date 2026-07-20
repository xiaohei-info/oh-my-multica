"""Multica 引擎 — 调用 multica CLI 实现双接口(现有资产平移,去 squad 概念)。

参考实现映射见设计文档 §12.3:
- MulticaStore:issue create/get/metadata set/list/comment/update/assign
- MulticaRuntime:assign 即唤醒(wake 为确认性 no-op)

认证由 multica CLI 自管(~/.multica),本实现不触碰 token。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import zipfile
from typing import Any, Dict, List, Optional

import yaml

from ..core.github import canonical_github_pr_url
from ..core.taskmeta import (
    CI_BOUNCE_KEY, CONTRACT_REF_KEY, DECISION_REQUIRED_KEY, DELIVERABLE_KEY,
    DELIVERABLE_REF_KEY, KIND_KEY, MERGE_BOUNCE_KEY, MERGE_INTENT_KEY, PHASE_KEY,
    PROJECT_RULES_KEY, PROJECT_RULES_REF_KEY, REVIEW_BOUNCE_KEY,
    REVIEW_REPORT_REF_KEY,
    SOURCE_REFS_KEY, TaskKind, TaskPhase, VERIFICATION_REF_KEY, WORKER_BOUNCE_KEY,
    parse_bounces, parse_kind, parse_phase,
)
from ..errors import AuthError, PlatformError, ValidationError
from ..i18n import ui
from .models import (
    AgentInfo, AgentProvisionSpec, DeliveryCommandOutcome,
    DeliveryCommandResult, EngineConfig, ProjectInfo, PullRequestSnapshot,
    RuntimeTarget, SkillPackage, WorkItem, WorkItemStatus, WorkspaceInfo,
)
from .metadata_policy import assert_metadata_write_allowed, parse_payload_text
from .runtime import AgentRuntime
from .store import WorkItemStore


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


_GH_AUTH_MARKERS = (
    "gh auth login",
    "not logged into",
    "authentication failed",
    "authentication required",
    "requires authentication",
    "bad credentials",
    "http 401",
    "status 401",
    "resource not accessible by integration",
    "resource not accessible by personal access token",
)

_GH_CI_FAILURE_SUMMARIES = (
    "some checks were not successful",
    "checks failed",
)

_GH_CI_FAILURE_STATES = {
    "action_required",
    "cancel",
    "cancelled",
    "fail",
    "failure",
    "startup_failure",
    "timed_out",
}

_GH_MERGE_RETRYABLE_MARKERS = (
    "merge conflict",
    "conflicts must be resolved",
    "head branch was modified",
    "head commit does not match",
    "head sha does not match",
    "head oid does not match",
)


def _tail(text: str, limit: int = 2000) -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    output = ""
    for stream in (exc.stdout, exc.stderr):
        if isinstance(stream, bytes):
            output += stream.decode("utf-8", errors="replace")
        elif isinstance(stream, str):
            output += stream
    return output


def _is_known_ci_failure(detail: str) -> bool:
    lowered = detail.lower()
    if any(marker in lowered for marker in _GH_CI_FAILURE_SUMMARIES):
        return True
    for line in detail.splitlines():
        columns = line.split("\t")
        if len(columns) > 1 and columns[1].strip().lower() in _GH_CI_FAILURE_STATES:
            return True
    return False


def _is_known_delivery_failure(operation_kind: str, detail: str) -> bool:
    if operation_kind == "ci":
        return _is_known_ci_failure(detail)
    if operation_kind == "merge":
        lowered = detail.lower()
        return any(marker in lowered for marker in _GH_MERGE_RETRYABLE_MARKERS)
    return False


def _is_shell_assignment(token: str) -> bool:
    name, separator, _ = token.partition("=")
    return bool(
        separator
        and name
        and (name[0].isalpha() or name[0] == "_")
        and all(char.isalnum() or char == "_" for char in name[1:])
    )


def _is_gh_executable(token: str) -> bool:
    return token == "gh" or os.path.basename(token) == "gh"


def _looks_like_gh_command(tokens: List[str]) -> bool:
    return any(
        _is_gh_executable(token)
        or token.startswith("gh ")
        or " gh " in token
        or token.endswith(" gh")
        for token in tokens
    )


def _unwrap_supported_gh_wrappers(tokens: List[str]) -> List[str]:
    tokens = list(tokens)
    while tokens and _is_shell_assignment(tokens[0]):
        tokens.pop(0)

    while tokens:
        if tokens[0] == "env":
            tokens.pop(0)
            while tokens and tokens[0] in {"-i", "--ignore-environment"}:
                tokens.pop(0)
            if tokens[:1] == ["--"]:
                tokens.pop(0)
            while tokens and _is_shell_assignment(tokens[0]):
                tokens.pop(0)
            continue
        if tokens[0] == "command":
            tokens.pop(0)
            if tokens[:1] == ["--"]:
                tokens.pop(0)
            continue
        if tokens[0] == "timeout":
            if len(tokens) < 2 or not re.fullmatch(
                r"\d+(?:\.\d+)?[smhd]?", tokens[1], re.IGNORECASE,
            ):
                return tokens
            tokens = tokens[2:]
            continue
        break
    return tokens


def _is_github_cli_delivery_command(command: str, operation_kind: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        if "gh" in command:
            raise ValidationError(ui(
                f"Unsupported GitHub CLI command syntax: {exc}. Use a direct "
                "`gh pr checks/merge` command or the supported env, command, "
                "or timeout wrapper.",
                f"不支持的 GitHub CLI 命令语法: {exc}。请直接使用 "
                "`gh pr checks/merge`，或使用受支持的 env、command、timeout wrapper。",
            )) from exc
        return False

    unwrapped = _unwrap_supported_gh_wrappers(tokens)
    subcommand = "checks" if operation_kind == "ci" else "merge"
    if (
        len(unwrapped) >= 3
        and _is_gh_executable(unwrapped[0])
        and unwrapped[1:3] == ["pr", subcommand]
        and not any(char in command for char in ";|&<>`\n\r")
        and "$(" not in command
    ):
        return True
    if _looks_like_gh_command(tokens):
        raise ValidationError(ui(
            "Unsupported GitHub CLI wrapper. Use a direct `gh pr checks/merge` "
            "command, optional NAME=value assignments, or the supported env, "
            "command, and timeout wrappers.",
            "不支持的 GitHub CLI wrapper。请直接使用 `gh pr checks/merge`，"
            "可选 NAME=value 环境变量，或受支持的 env、command、timeout wrapper。",
        ))
    return False


class MulticaStore(WorkItemStore):
    """数据面:全部经 multica CLI。"""

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._pending_assignment_wakes: set[str] = set()

    def _mark_assignment_wake_pending(self, item_id: str) -> None:
        self._pending_assignment_wakes.add(item_id)

    def _consume_assignment_wake_pending(self, item_id: str) -> bool:
        if item_id not in self._pending_assignment_wakes:
            return False
        self._pending_assignment_wakes.remove(item_id)
        return True

    def inspect_pull_request(self, pr_url: str) -> PullRequestSnapshot:
        try:
            pr_url = canonical_github_pr_url(pr_url)
        except ValueError as exc:
            raise ValidationError(ui(
                f"Invalid GitHub PR URL: {exc}",
                f"GitHub PR URL 非法: {exc}",
            )) from exc
        try:
            proc = subprocess.run(
                [
                    "gh", "pr", "view", pr_url, "--json",
                    "url,isDraft,state,headRefOid",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            raise PlatformError(ui(
                "GitHub PR checks require gh CLI. Install it, sign in, and retry: "
                "brew install gh && gh auth login",
                "GitHub PR 检查需要 gh CLI。请安装并登录后重试: "
                "brew install gh && gh auth login"))
        except subprocess.TimeoutExpired:
            raise PlatformError(ui(
                f"GitHub PR check timed out: {pr_url}. Verify network and GitHub access.",
                f"GitHub PR 检查超时: {pr_url}。请确认网络/GitHub 可达后重试。"))
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            error_type = AuthError if (
                proc.returncode == 4
                or any(marker in detail.lower() for marker in _GH_AUTH_MARKERS)
            ) else PlatformError
            raise error_type(ui(
                f"GitHub PR check failed: {pr_url}\n{detail}",
                f"GitHub PR 检查失败: {pr_url}\n{detail}"))
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            raise PlatformError(ui(
                f"GitHub PR check returned non-JSON output: {pr_url}\n"
                f"{(proc.stdout or '').strip()}",
                f"GitHub PR 检查返回非 JSON: {pr_url}\n"
                f"{(proc.stdout or '').strip()}"))
        if not isinstance(payload, dict):
            raise PlatformError(ui(
                f"GitHub PR check must return a JSON object: {pr_url}",
                f"GitHub PR 检查必须返回 JSON object: {pr_url}"))
        head_revision = payload.get("headRefOid")
        if not isinstance(head_revision, str) or not head_revision.strip():
            raise PlatformError(ui(
                f"GitHub PR check did not return headRefOid: {pr_url}",
                f"GitHub PR 检查未返回 headRefOid: {pr_url}"))
        canonical_url = payload.get("url")
        if not isinstance(canonical_url, str) or not canonical_url.strip():
            raise PlatformError(ui(
                f"GitHub PR check did not return canonical URL: {pr_url}",
                f"GitHub PR 检查未返回 canonical URL: {pr_url}"))
        return PullRequestSnapshot(
            url=canonical_url,
            is_draft=payload.get("isDraft") is True,
            state=str(payload.get("state") or ""),
            head_revision=head_revision,
        )

    def _run_pull_request_command(
        self,
        command: str,
        timeout_minutes: int,
        operation: str,
        operation_kind: str,
        github_cli: bool,
    ) -> DeliveryCommandResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout_minutes)) * 60,
            )
        except FileNotFoundError as exc:
            raise PlatformError(ui(
                f"{operation} command is unavailable: {exc}",
                f"{operation} 命令不可用: {exc}")) from exc
        except subprocess.TimeoutExpired as exc:
            output = _timeout_output(exc)
            return DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.TIMED_OUT,
                exit_code=None,
                output=output,
                summary=_tail(output) or ui("(no output)", "(无输出)"),
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        detail = output.strip()
        if proc.returncode == 0:
            return DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.PASSED,
                exit_code=0,
                output=output,
                summary=_tail(output) or ui("(no output)", "(无输出)"),
            )

        if proc.returncode == 127:
            raise PlatformError(ui(
                f"{operation} command is unavailable: {detail}",
                f"{operation} 命令不可用: {detail}"))

        if not github_cli:
            return DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.FAILED,
                exit_code=proc.returncode,
                output=output,
                summary=_tail(output) or ui("(no output)", "(无输出)"),
            )

        lowered = detail.lower()
        if (
            proc.returncode == 4
            or any(marker in lowered for marker in _GH_AUTH_MARKERS)
        ):
            raise AuthError(ui(
                f"{operation} authentication failed; run `gh auth login`: {detail}",
                f"{operation} 认证失败，请先运行 `gh auth login`: {detail}"))
        if _is_known_delivery_failure(operation_kind, detail):
            return DeliveryCommandResult(
                outcome=DeliveryCommandOutcome.FAILED,
                exit_code=proc.returncode,
                output=output,
                summary=_tail(output) or ui("(no output)", "(无输出)"),
            )
        raise PlatformError(ui(
            f"{operation} platform call failed: {detail}",
            f"{operation} 平台调用失败: {detail}"))

    def run_ci_check(
        self, pr_url: str, command: str, timeout_minutes: int,
    ) -> DeliveryCommandResult:
        try:
            canonical_url = canonical_github_pr_url(pr_url)
        except ValueError as exc:
            raise ValidationError(ui(
                f"Invalid GitHub PR URL for CI: {exc}",
                f"CI 使用的 GitHub PR URL 非法: {exc}")) from exc
        rendered = command.replace("{pr_url}", canonical_url)
        return self._run_pull_request_command(
            rendered,
            timeout_minutes,
            "GitHub CI check",
            "ci",
            _is_github_cli_delivery_command(command, "ci"),
        )

    def merge_pull_request(
        self,
        pr_url: str,
        delivered_revision: str,
        command: str,
        timeout_minutes: int,
    ) -> DeliveryCommandResult:
        try:
            canonical_url = canonical_github_pr_url(pr_url)
        except ValueError as exc:
            raise ValidationError(ui(
                f"Invalid GitHub PR URL for merge: {exc}",
                f"merge 使用的 GitHub PR URL 非法: {exc}")) from exc
        rendered = (
            command.replace("{pr_url}", canonical_url)
            .replace("{delivered_revision}", delivered_revision)
        )
        return self._run_pull_request_command(
            rendered,
            timeout_minutes,
            "GitHub merge",
            "merge",
            _is_github_cli_delivery_command(command, "merge"),
        )

    # ==================== 内部工具 ====================

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
            result = subprocess.run(cmd, capture_output=capture, text=True)
        except FileNotFoundError:
            raise AuthError(ui(
                "multica CLI is not on PATH. Install it and sign in: "
                "brew install multica-ai/tap/multica && multica login",
                "multica CLI 不在 PATH —— 先安装并登录:brew install multica-ai/tap/multica && multica login"))
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if result.returncode == 3 or "auth" in stderr.lower() or "login" in stderr.lower():
                raise AuthError(ui(
                    f"multica authentication failed; run `multica login`: {stderr}",
                    f"multica 认证失败(先 multica login): {stderr}"))
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
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {"raw": value} if value else None
        return value

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
        }.get(key, ui("Handoff file", "交接文件"))
        ref_key = {
            "contract": CONTRACT_REF_KEY,
            "deliverable": DELIVERABLE_REF_KEY,
            "project-rules": PROJECT_RULES_REF_KEY,
            "verification": VERIFICATION_REF_KEY,
            "review-report": REVIEW_REPORT_REF_KEY,
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

    def _load_payload_comment(self, item_id: str, key: str, ref: Optional[Dict[str, Any]]) -> Optional[str]:
        if not ref:
            return None
        comment_id = ref.get("comment_id")
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
        attachment_id = ref.get("attachment_id")
        for comment in comments:
            if comment.get("id") != comment_id:
                continue
            content = comment.get("content") or ""
            if begin in content and end in content:
                return content.split(begin, 1)[1].split(end, 1)[0].strip("\n")
            if not attachment_id:
                filename = ref.get("filename")
                for attachment in comment.get("attachments") or []:
                    if not filename or attachment.get("filename") == filename:
                        attachment_id = attachment.get("id")
                        break
        if not attachment_id:
            return None
        with tempfile.TemporaryDirectory(prefix="omac-attachment-") as td:
            for attempt in range(2):
                try:
                    self._run_multica([
                        "attachment", "download", attachment_id,
                        "--output-dir", td,
                    ], capture=True)
                    break
                except PlatformError as exc:
                    if attempt == 1 or "timed out" not in str(exc).lower():
                        raise
            filename = ref.get("filename")
            candidates = []
            if filename:
                candidates.append(os.path.join(td, filename))
            candidates.extend(os.path.join(td, p) for p in os.listdir(td))
            for path in candidates:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read()
        return None

    def _issue_to_work_item(self, issue_data: Dict, workspace_id: str) -> WorkItem:
        metadata = issue_data.get("metadata", {})

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

        deliverable_ref = self._json_metadata(metadata, DELIVERABLE_REF_KEY)
        deliverable = metadata.get(DELIVERABLE_KEY)
        if not deliverable and isinstance(deliverable_ref, dict):
            deliverable = self._load_payload_comment(issue_data["id"], "deliverable", deliverable_ref)

        project_rules_ref = self._json_metadata(metadata, PROJECT_RULES_REF_KEY)
        project_rules = metadata.get(PROJECT_RULES_KEY)
        if not project_rules and isinstance(project_rules_ref, dict):
            project_rules = self._load_payload_comment(
                issue_data["id"], "project-rules", project_rules_ref)

        verification_ref = self._json_metadata(metadata, VERIFICATION_REF_KEY)
        review_report_ref = self._json_metadata(metadata, REVIEW_REPORT_REF_KEY)
        contract_ref = self._json_metadata(metadata, CONTRACT_REF_KEY)
        source_refs = self._json_metadata(metadata, SOURCE_REFS_KEY)
        verification = None
        if isinstance(verification_ref, dict):
            verification_text = self._load_payload_comment(issue_data["id"], "verification", verification_ref)
            verification = parse_payload_text(verification_text)
        if verification is None:
            legacy_verification = self._json_metadata(metadata, "verification")
            verification = legacy_verification if isinstance(legacy_verification, dict) else None

        review_report = None
        if isinstance(review_report_ref, dict):
            report_text = self._load_payload_comment(issue_data["id"], "review-report", review_report_ref)
            review_report = parse_payload_text(report_text)
        if review_report is None:
            legacy_report = self._json_metadata(metadata, "review_report")
            review_report = legacy_report if isinstance(legacy_report, dict) else None

        contract = None
        if isinstance(contract_ref, dict):
            contract_text = self._load_payload_comment(issue_data["id"], "contract", contract_ref)
            contract = parse_payload_text(contract_text)
        if contract is None:
            legacy_contract = self._json_metadata(metadata, "contract")
            contract = legacy_contract if isinstance(legacy_contract, dict) else None

        return WorkItem(
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
            verification_ref=verification_ref if isinstance(verification_ref, dict) else None,
            review_verdict=self._optional_text_metadata(metadata, "review_verdict"),
            review_comment=self._optional_text_metadata(metadata, "review_comment"),
            review_report=review_report,
            review_report_ref=review_report_ref if isinstance(review_report_ref, dict) else None,
            decision_required=self._json_metadata(metadata, DECISION_REQUIRED_KEY),
            merge_intent=self._json_metadata(metadata, MERGE_INTENT_KEY),
            contract=contract,
            contract_ref=contract_ref if isinstance(contract_ref, dict) else None,
            source_refs=source_refs if isinstance(source_refs, list) else [],
            kind=parse_kind(metadata.get(KIND_KEY)),
            phase=parse_phase(metadata.get(PHASE_KEY)),
            bounces=parse_bounces(metadata),
            deliverable=deliverable,
            deliverable_ref=deliverable_ref if isinstance(deliverable_ref, dict) else None,
            project_rules=project_rules,
            project_rules_ref=(
                project_rules_ref if isinstance(project_rules_ref, dict) else None),
        )

    def _resolve_agent_id(self, agent_name: str) -> str:
        """agent 名 → id(assign 需要 id)。"""
        agents = self._run_multica(["agent", "list", "--output", "json"])
        if isinstance(agents, list):
            for agent in agents:
                if agent.get("name") == agent_name:
                    return agent.get("id")
        raise PlatformError(
            f"agent '{agent_name}' not found in workspace {self.config.workspace_id}")

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
        encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self._run_multica([
            "issue", "metadata", "set", item_id,
            "--key", key, "--value", encoded,
        ])

    def get_work_item(self, item_id: str) -> WorkItem:
        result = self._run_multica(["issue", "get", item_id, "--output", "json"])
        if not isinstance(result, dict):
            raise PlatformError(ui(
                f"Could not get issue {item_id}", f"获取 issue {item_id} 失败"))
        item = self._issue_to_work_item(result, self.config.workspace_id)
        if item.status == WorkItemStatus.IN_PROGRESS:
            latest_run_status = self._inactive_latest_run_status(item_id)
            if latest_run_status == "failed":
                item.status = WorkItemStatus.FAILED
            elif latest_run_status == "completed":
                item.agent_run_finished_without_submit = True
        return item

    def _inactive_latest_run_status(self, item_id: str) -> Optional[str]:
        """没有 active run 时返回最新 run 状态;查询失败/仍在跑返回 None。

        agent runtime 失败不会总是同步更新 issue status;如果没有任何 active run,
        且最新可见 run 是 failed,编排侧不能无限等待。completed 但 issue 仍
        in_progress 则表示 worker 没有 submit,由上层回退到 worker 继续处理。
        """
        try:
            runs = self._run_multica(["issue", "runs", item_id, "--output", "json"])
        except PlatformError:
            return None
        if not isinstance(runs, list) or not runs:
            return None
        latest = _latest_run(runs)
        if not latest:
            return None
        active = {"queued", "pending", "running", "dispatching"}
        if any((run.get("status") or "").lower() in active for run in runs):
            return None
        return (latest.get("status") or "").lower() or None

    def update_work_item_metadata(
        self,
        item_id: str,
        worker: Optional[str] = None,
        reviewer: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        review_verdict: Optional[str] = None,
        review_comment: Optional[str] = None,
        verification: Optional[Dict[str, Any]] = None,
        verification_source: Optional[str] = None,
        review_report: Optional[Dict[str, Any]] = None,
        review_report_source: Optional[str] = None,
        decision_required: Optional[Dict[str, Any]] = None,
        merge_intent: Optional[Dict[str, str]] = None,
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
        if worker is not None:
            self._set_metadata(item_id, "worker", worker)
        if reviewer is not None:
            self._set_metadata(item_id, "reviewer", reviewer)
        if blocked_by is not None:
            self._set_metadata(item_id, "blocked_by", blocked_by)
        if artifacts is not None:
            self._set_metadata(item_id, "artifacts", artifacts)
        if review_verdict is not None:
            self._set_metadata(item_id, "review_verdict", review_verdict)
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
        if decision_required is not None:
            self._set_metadata(item_id, DECISION_REQUIRED_KEY, decision_required)
        if merge_intent is not None:
            self._set_metadata(item_id, MERGE_INTENT_KEY, merge_intent)
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
        if description is not None:
            self._run_multica_with_text_file(
                ["issue", "update", item_id], "--description-file", description)
        return self.get_work_item(item_id)

    def set_node_contract(self, item_id: str, contract: Any):
        from dataclasses import asdict, is_dataclass
        payload = asdict(contract) if is_dataclass(contract) else contract
        source = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        ref = self._publish_payload_comment(item_id, "contract", source, ".yaml")
        self._set_metadata(item_id, CONTRACT_REF_KEY, ref)

    # multica issue list 服务端单页上限 100;更大的 --limit 会被静默截断。
    _LIST_PAGE_SIZE = 100

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
        self._set_metadata(item_id, "review_verdict", "")
        self._set_metadata(item_id, "review_comment", "")
        self._set_metadata(item_id, DECISION_REQUIRED_KEY, "{}")
        self._set_metadata(item_id, PHASE_KEY, TaskPhase.AUTHORING.value)

    def assign_work_item(self, item_id: str, assignee: str, role: str):
        agent_id = self._resolve_agent_id(assignee)
        current = self._run_multica(["issue", "get", item_id, "--output", "json"])
        current_assignee_id = (
            str(current.get("assignee_id"))
            if isinstance(current, dict) and current.get("assignee_id")
            else None
        )
        if role == "worker":
            self._set_metadata(item_id, "worker", assignee)
        elif role == "reviewer":
            self._set_metadata(item_id, "reviewer", assignee)
        self._run_multica(["issue", "assign", item_id, "--to", agent_id])
        # 改派到不同 agent 时，Multica assignment 会创建 run，随后的 wake
        # 只需确认，避免再 rerun 一次。同一 assignee 的 assign 是幂等更新，
        # 不会创建 run；此时保留 wake 的终态检查，让它对旧 run 执行一次 rerun。
        if current_assignee_id != agent_id:
            self._mark_assignment_wake_pending(item_id)


class MulticaRuntime(AgentRuntime):
    """执行面:Multica 的「assign 即唤醒」——issue 被 assign 后,agent 所在机器的
    daemon 自动认领任务并以 issue 内容为 prompt 拉起 agent CLI。

    因此 wake 是确认性 no-op:只需数据面 assign 已生效(设计文档 §12.3)。
    阶段交接(评审/回退)= 同一 issue 转派新 assignee,天然支持接力棒传递。
    """

    def __init__(self, store: MulticaStore):
        self._store = store

    def wake(self, item_id: str, agent: str, role: str) -> None:
        if self._store._consume_assignment_wake_pending(item_id):
            return None
        runs = self._store._run_multica(["issue", "runs", item_id, "--output", "json"])
        latest = _latest_direct_run(runs if isinstance(runs, list) else [])
        if latest:
            status = (latest.get("status") or "").lower()
            if status in {"failed", "cancelled", "completed"}:
                self._store._run_multica(["issue", "rerun", item_id, "--output", "json"])
        return None

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

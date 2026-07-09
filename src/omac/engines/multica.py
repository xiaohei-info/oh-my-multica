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
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from ..core.taskmeta import (
    CI_BOUNCE_KEY, DECISION_REQUIRED_KEY, DELIVERABLE_KEY, DELIVERABLE_REF_KEY, KIND_KEY,
    MERGE_BOUNCE_KEY, PHASE_KEY, REVIEW_BOUNCE_KEY, REVIEW_REPORT_REF_KEY,
    TaskKind, TaskPhase, VERIFICATION_REF_KEY,
    parse_bounces, parse_kind, parse_phase,
)
from ..errors import AuthError, PlatformError
from .models import EngineConfig, ProjectInfo, WorkItem, WorkItemStatus, WorkspaceInfo
from .metadata_policy import assert_metadata_write_allowed, parse_payload_text
from .runtime import AgentRuntime
from .store import WorkItemStore


class MulticaStore(WorkItemStore):
    """数据面:全部经 multica CLI。"""

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
            raise AuthError(
                "multica CLI 不在 PATH —— 先安装并登录:brew install multica-ai/tap/multica && multica login")
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if result.returncode == 3 or "auth" in stderr.lower() or "login" in stderr.lower():
                raise AuthError(f"multica 认证失败(先 multica login): {stderr}")
            raise PlatformError(f"multica 调用失败: {' '.join(cmd)}\n{stderr}")
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
            return self._run_multica(args + [flag, path], capture=capture)
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

    def _publish_payload_comment(
        self, item_id: str, key: str, content: str, suffix: str,
    ) -> Dict[str, Any]:
        """发布较长文档:comment 只做附件索引,正文由 attachment 承载。"""
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
            result = self._run_multica([
                "issue", "comment", "add", item_id,
                "--content-file", comment_path,
                "--attachment", attachment_path,
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
            "deliverable": "阶段交付文件",
            "verification": "验证证据文件",
            "review-report": "评审报告文件",
        }.get(key, "交接文件")
        ref_key = {
            "deliverable": DELIVERABLE_REF_KEY,
            "verification": VERIFICATION_REF_KEY,
            "review-report": REVIEW_REPORT_REF_KEY,
        }.get(key, f"{key}_ref")
        return (
            f"## omac {key}\n"
            f"{title}已作为附件上传。\n\n"
            f"- attachment: {filename}\n"
            f"- sha256: {sha}\n"
            f"- bytes: {size}\n"
            f"- metadata: `{ref_key}`\n\n"
            "后续 agent 应通过 `omac work show` 读取交接上下文；程序化引用见 issue metadata。\n"
        )

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
            self._run_multica([
                "attachment", "download", attachment_id,
                "--output-dir", td,
            ], capture=True)
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

        verification_ref = self._json_metadata(metadata, VERIFICATION_REF_KEY)
        review_report_ref = self._json_metadata(metadata, REVIEW_REPORT_REF_KEY)
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

        return WorkItem(
            id=issue_data["id"],
            workspace_id=workspace_id,
            title=issue_data.get("title", ""),
            description=issue_data.get("description", ""),
            status=self._multica_to_status(issue_data.get("status", "todo")),
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
            contract=self._json_metadata(metadata, "contract"),
            kind=parse_kind(metadata.get(KIND_KEY)),
            phase=parse_phase(metadata.get(PHASE_KEY)),
            bounces=parse_bounces(metadata),
            deliverable=deliverable,
            deliverable_ref=deliverable_ref if isinstance(deliverable_ref, dict) else None,
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
            raise PlatformError(f"创建 project 失败: {result}")
        info = self._project_to_info(result)
        if info is None:
            raise PlatformError(f"创建 project 返回缺少 id: {result}")
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
            raise PlatformError(f"创建 issue 失败: {result}")
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
            raise PlatformError(f"获取 issue {item_id} 失败")
        return self._issue_to_work_item(result, self.config.workspace_id)

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
        phase: Optional[TaskPhase] = None,
        ci_bounce: Optional[int] = None,
        review_bounce: Optional[int] = None,
        merge_bounce: Optional[int] = None,
        deliverable: Optional[str] = None,
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
        if ci_bounce is not None:
            self._set_metadata(item_id, CI_BOUNCE_KEY, str(ci_bounce))
        if review_bounce is not None:
            self._set_metadata(item_id, REVIEW_BOUNCE_KEY, str(review_bounce))
        if merge_bounce is not None:
            self._set_metadata(item_id, MERGE_BOUNCE_KEY, str(merge_bounce))
        if deliverable is not None:
            ref = self._publish_payload_comment(item_id, "deliverable", deliverable, ".md")
            self._set_metadata(item_id, DELIVERABLE_REF_KEY, ref)
        if phase is not None:
            self._set_metadata(item_id, PHASE_KEY, phase.value)
        if description is not None:
            self._run_multica_with_text_file(
                ["issue", "update", item_id], "--description-file", description)
        return self.get_work_item(item_id)

    def set_node_contract(self, item_id: str, contract: Any):
        from dataclasses import asdict, is_dataclass
        payload = asdict(contract) if is_dataclass(contract) else contract
        self._set_metadata(item_id, "contract", payload)

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
        if status is not None:
            extra_args += ["--status", self._status_to_multica(status)]
        issues = self._list_issues_paginated(extra_args)
        work_items = [self._issue_to_work_item(i, workspace_id) for i in issues]
        # 服务端按平台态过滤后,再按业务态精确收口(多对一映射的兜底)
        if status is not None:
            work_items = [i for i in work_items if i.status == status]
        return work_items

    def add_comment(self, item_id: str, comment: str):
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
        self._run_multica(["issue", "assign", item_id, "--to", agent_id])
        if role == "worker":
            self.update_work_item_metadata(item_id, worker=assignee)
        elif role == "reviewer":
            self.update_work_item_metadata(item_id, reviewer=assignee)


class MulticaRuntime(AgentRuntime):
    """执行面:Multica 的「assign 即唤醒」——issue 被 assign 后,agent 所在机器的
    daemon 自动认领任务并以 issue 内容为 prompt 拉起 agent CLI。

    因此 wake 是确认性 no-op:只需数据面 assign 已生效(设计文档 §12.3)。
    阶段交接(评审/回退)= 同一 issue 转派新 assignee,天然支持接力棒传递。
    """

    def __init__(self, store: MulticaStore):
        self._store = store

    def wake(self, item_id: str, agent: str, role: str) -> None:
        # assign 即唤醒;此处不做额外平台调用,保持幂等。
        return None

    def describe(self) -> str:
        return "multica: assign 即唤醒(daemon 认领并拉起 agent CLI),wake 为确认性 no-op"

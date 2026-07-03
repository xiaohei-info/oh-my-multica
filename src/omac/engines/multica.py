"""Multica 引擎 — 调用 multica CLI 实现双接口(现有资产平移,去 squad 概念)。

参考实现映射见设计文档 §12.3:
- MulticaStore:issue create/get/metadata set/list/comment/update/assign
- MulticaRuntime:assign 即唤醒(wake 为确认性 no-op)

认证由 multica CLI 自管(~/.multica),本实现不触碰 token。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from ..errors import AuthError, PlatformError
from .models import EngineConfig, WorkItem, WorkItemStatus
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
            verification=self._json_metadata(metadata, "verification"),
            review_verdict=metadata.get("review_verdict"),
            review_comment=metadata.get("review_comment"),
            review_report=self._json_metadata(metadata, "review_report"),
            contract=self._json_metadata(metadata, "contract"),
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
    ) -> WorkItem:
        result = self._run_multica_with_text_file([
            "issue", "create",
            "--title", f"[DAG:{dag_key}] {title}",
            "--status", self._status_to_multica(initial_status),
            "--output", "json",
        ], "--description-file", description)

        if not isinstance(result, dict) or "id" not in result:
            raise PlatformError(f"创建 issue 失败: {result}")
        issue_id = result["id"]

        self._set_metadata(issue_id, "dag_key", dag_key)
        self._set_metadata(issue_id, "worker", worker)
        if reviewer:
            self._set_metadata(issue_id, "reviewer", reviewer)
        if blocked_by:
            self._set_metadata(issue_id, "blocked_by", json.dumps(blocked_by))
        if wave is not None:
            self._set_metadata(issue_id, "wave", str(wave))

        return self.get_work_item(issue_id)

    def _set_metadata(self, item_id: str, key: str, value: str):
        self._run_multica([
            "issue", "metadata", "set", item_id,
            "--key", key, "--value", value,
        ], capture=False)

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
        review_report: Optional[Dict[str, Any]] = None,
    ) -> WorkItem:
        if worker is not None:
            self._set_metadata(item_id, "worker", worker)
        if reviewer is not None:
            self._set_metadata(item_id, "reviewer", reviewer)
        if blocked_by is not None:
            self._set_metadata(item_id, "blocked_by", json.dumps(blocked_by))
        if artifacts is not None:
            self._set_metadata(item_id, "artifacts", json.dumps(artifacts))
        if review_verdict is not None:
            self._set_metadata(item_id, "review_verdict", review_verdict)
        if review_comment is not None:
            self._set_metadata(item_id, "review_comment", review_comment)
        if verification is not None:
            self._set_metadata(item_id, "verification", json.dumps(verification))
        if review_report is not None:
            self._set_metadata(item_id, "review_report", json.dumps(review_report))
        return self.get_work_item(item_id)

    def set_node_contract(self, item_id: str, contract: Any):
        from dataclasses import asdict, is_dataclass
        payload = asdict(contract) if is_dataclass(contract) else contract
        self._set_metadata(item_id, "contract", json.dumps(payload))

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
            "--content-file", comment, capture=False)

    # ==================== 状态和分配 ====================

    def update_status(self, item_id: str, status: WorkItemStatus):
        self._run_multica([
            "issue", "update", item_id,
            "--status", self._status_to_multica(status),
        ], capture=False)

    def assign_work_item(self, item_id: str, assignee: str, role: str):
        agent_id = self._resolve_agent_id(assignee)
        self._run_multica(["issue", "assign", item_id, "--to", agent_id], capture=False)
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

"""
Multica 引擎实现
"""
import json
import os
import subprocess
import tempfile
from typing import List, Dict, Any, Optional
from .base import CollaborationEngine
from .models import WorkspaceInfo, WorkItem, WorkItemStatus, EngineConfig


class MulticaEngine(CollaborationEngine):
    """Multica 平台引擎实现

    调用 multica CLI 实现所有接口
    """

    # ==================== 环境变量管理 ====================

    @classmethod
    def get_required_env_vars(cls) -> List[Dict[str, str]]:
        """Multica 引擎需要的环境变量

        workspace 走 env（顶层空间，定位在哪建 issue/找成员）；
        squad 走 env（可选）：manifest 未指定 squad 时回退到此值，使「clone → setup → run」
        这条 onboarding 路径成立（manifest 由 orchestrator 自动生成，clone 时尚不存在）。
        优先级见 run_dag.start_new_run：manifest.meta.squad 优先，缺失才回退 MULTICA_SQUAD_ID。
        """
        return [
            {
                'name': 'MULTICA_WORKSPACE_ID',
                'description': 'Multica 工作空间 ID',
                'prompt': '请输入 multica workspace ID (可通过 `multica workspace list` 查看)',
                'validator': lambda x: len(x) > 0
            },
            {
                'name': 'MULTICA_SQUAD_ID',
                'description': 'Multica 默认派发小队 ID（可选）',
                'prompt': '请输入默认 squad ID (可通过 `multica squad list` 查看；可留空，'
                          '由各 manifest 的 meta.squad 指定)',
                'optional': True,
                'validator': lambda x: True  # 可空：留空表示不设默认 squad
            }
        ]

    @classmethod
    def get_recommended_polling_interval(cls) -> int:
        # Multica 有实时通知能力，可以更频繁
        return 15

    @classmethod
    def get_rate_limit_info(cls) -> Dict[str, int]:
        return {
            "requests_per_hour": 10000,
            "requests_per_minute": 200
        }

    # ==================== 内部工具方法 ====================

    def _run_multica(self, args: List[str], capture=True) -> Any:
        """调用 multica CLI

        workspace 通过全局 flag `--workspace-id` 注入（位于 multica 与子命令之间），
        与 multica CLI 约定一致——子命令本身不接受 --workspace-id。
        """
        cmd = ["multica"]
        if self.config.workspace_id:
            cmd += ["--workspace-id", self.config.workspace_id]
        cmd += args
        result = subprocess.run(cmd, capture_output=capture, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"multica 调用失败: {' '.join(cmd)}\n{result.stderr}")
        if capture and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return result.stdout.strip()
        return None

    def _run_multica_with_text_file(self, args: List[str], flag: str, content: str, capture=True) -> Any:
        fd, path = tempfile.mkstemp(prefix="parallel-dev-", suffix=".md", text=True)
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
        """将业务状态转换为 multica status"""
        mapping = {
            WorkItemStatus.TODO: "todo",
            WorkItemStatus.IN_PROGRESS: "in_progress",
            WorkItemStatus.IN_REVIEW: "in_review",
            WorkItemStatus.DONE: "done",
            WorkItemStatus.FAILED: "blocked",
            WorkItemStatus.BLOCKED: "blocked"
        }
        return mapping.get(status, "todo")

    def _multica_to_status(self, multica_status: str) -> WorkItemStatus:
        """将 multica status 转换为业务状态"""
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
        """将 multica issue 转换为 WorkItem"""
        metadata = issue_data.get('metadata', {})

        # 解析 blocked_by（可能是 JSON 字符串）
        blocked_by = metadata.get('blocked_by', [])
        if isinstance(blocked_by, str):
            try:
                blocked_by = json.loads(blocked_by)
            except:
                blocked_by = []

        artifacts = self._json_metadata(metadata, 'artifacts')
        verification = self._json_metadata(metadata, 'verification')
        review_report = self._json_metadata(metadata, 'review_report')

        # 解析 wave
        wave = metadata.get('wave')
        if isinstance(wave, str):
            try:
                wave = int(wave)
            except:
                wave = None

        return WorkItem(
            id=issue_data['id'],
            workspace_id=workspace_id,
            title=issue_data.get('title', ''),
            description=issue_data.get('description', ''),
            status=self._multica_to_status(issue_data.get('status', 'todo')),
            dag_key=metadata.get('dag_key', ''),
            worker=metadata.get('worker'),
            reviewer=metadata.get('reviewer'),
            blocked_by=blocked_by if isinstance(blocked_by, list) else [],
            wave=wave,
            artifacts=artifacts,
            verification=verification,
            review_verdict=metadata.get('review_verdict'),
            review_comment=metadata.get('review_comment'),
            review_report=review_report
        )

    def _resolve_agent_id(self, agent_name: str) -> str:
        """从 agent 名解析到 agent id（workspace 由全局 flag 注入）"""
        agents = self._run_multica([
            "agent", "list",
            "--output", "json"
        ])

        if isinstance(agents, list):
            for agent in agents:
                if agent.get("name") == agent_name:
                    return agent.get("id")

        raise ValueError(f"agent '{agent_name}' not found in workspace {self.config.workspace_id}")

    # ==================== 第一组：成员池 ====================

    def list_members(self, workspace_id: str) -> List[str]:
        """列出成员池

        派发作用域是 manifest 的 squad（小队）：
        - 配了 squad_id → 列该小队成员（`multica squad member list <squad-id>`）
        - 未配 squad_id → 退化为整个 workspace 的全部 agent（`multica agent list`）

        注：参数 workspace_id 是接口历史命名（实为「作用域 id」），squad 优先取 config.squad_id。
        """
        squad_id = self.config.squad_id or workspace_id

        if squad_id:
            members = self._run_multica([
                "squad", "member", "list", squad_id,
                "--output", "json"
            ])
            names = self._extract_member_names(members)
            if names:
                return names
            # 小队取不到成员时回退到 workspace 全员，避免 lint 误杀

        agents = self._run_multica([
            "agent", "list",
            "--output", "json"
        ])
        return self._extract_member_names(agents)

    @staticmethod
    def _extract_member_names(data: Any) -> List[str]:
        """从 squad member list / agent list 的 JSON 中提取成员名"""
        if isinstance(data, dict):
            data = data.get("members") or data.get("agents") or []
        if isinstance(data, list):
            names = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                # squad member 可能是 {name} 或嵌套 {agent: {name}}
                name = item.get("name") or (item.get("agent") or {}).get("name")
                if name:
                    names.append(name)
            return names
        return []

    # ==================== 第二组：工作单元 CRUD ====================

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
        initial_status: WorkItemStatus = WorkItemStatus.TODO
    ) -> WorkItem:
        """创建工作单元"""
        # 1. 创建 issue
        multica_status = self._status_to_multica(initial_status)
        result = self._run_multica_with_text_file([
            "issue", "create",
            "--title", f"[DAG:{dag_key}] {title}",
            "--status", multica_status,
            "--output", "json"
        ], "--description-file", description)

        if not isinstance(result, dict) or 'id' not in result:
            raise RuntimeError(f"创建 issue 失败: {result}")

        issue_id = result['id']

        # 2. 写入元数据
        self._run_multica([
            "issue", "metadata", "set", issue_id,
            "--key", "dag_key",
            "--value", dag_key
        ], capture=False)

        self._run_multica([
            "issue", "metadata", "set", issue_id,
            "--key", "worker",
            "--value", worker
        ], capture=False)

        if reviewer:
            self._run_multica([
                "issue", "metadata", "set", issue_id,
                "--key", "reviewer",
                "--value", reviewer
            ], capture=False)

        if blocked_by:
            self._run_multica([
                "issue", "metadata", "set", issue_id,
                "--key", "blocked_by",
                "--value", json.dumps(blocked_by)
            ], capture=False)

        if wave is not None:
            self._run_multica([
                "issue", "metadata", "set", issue_id,
                "--key", "wave",
                "--value", str(wave)
            ], capture=False)

        # 3. 返回创建的 WorkItem
        return self.get_work_item(issue_id)

    def get_work_item(self, item_id: str) -> WorkItem:
        """获取工作单元详情"""
        result = self._run_multica([
            "issue", "get", item_id,
            "--output", "json"
        ])

        if not isinstance(result, dict):
            raise RuntimeError(f"获取 issue {item_id} 失败")

        # workspace_id 从当前配置获取
        workspace_id = self.config.workspace_id
        return self._issue_to_work_item(result, workspace_id)

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
        review_report: Optional[Dict[str, Any]] = None
    ) -> WorkItem:
        """更新工作单元的元数据"""
        if worker is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "worker",
                "--value", worker
            ], capture=False)

        if reviewer is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "reviewer",
                "--value", reviewer
            ], capture=False)

        if blocked_by is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "blocked_by",
                "--value", json.dumps(blocked_by)
            ], capture=False)

        if artifacts is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "artifacts",
                "--value", json.dumps(artifacts)
            ], capture=False)

        if review_verdict is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "review_verdict",
                "--value", review_verdict
            ], capture=False)

        if review_comment is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "review_comment",
                "--value", review_comment
            ], capture=False)

        if verification is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "verification",
                "--value", json.dumps(verification)
            ], capture=False)

        if review_report is not None:
            self._run_multica([
                "issue", "metadata", "set", item_id,
                "--key", "review_report",
                "--value", json.dumps(review_report)
            ], capture=False)

        return self.get_work_item(item_id)

    # multica issue list 服务端单页上限 100；`--limit 1000` 会被静默截断为 100。
    _LIST_PAGE_SIZE = 100

    def _list_issues_paginated(self, extra_args: List[str]) -> List[Dict]:
        """分页拉取 issue list 全集。

        服务端单页封顶 100 条，旧实现 `--limit 1000` 会被静默截断——大 workspace 下
        只能拿到前 100 条。这里用 `--offset` 逐页翻，直到某页不足一整页为止。
        """
        issues: List[Dict] = []
        offset = 0
        while True:
            result = self._run_multica([
                "issue", "list",
                "--limit", str(self._LIST_PAGE_SIZE),
                "--offset", str(offset),
                "--output", "json",
            ] + extra_args)

            if isinstance(result, dict) and 'issues' in result:
                page = result['issues']
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
        status: Optional[WorkItemStatus] = None
    ) -> List[WorkItem]:
        """列出工作单元。

        status 给定时把过滤下推到服务端（`--status`），减少传输量并让取消/完成等
        终态 issue 不必整表拉回客户端再筛——同时配合分页拿全集，不再被 100 条上限截断。
        """
        extra_args: List[str] = []
        if status is not None:
            extra_args += ["--status", self._status_to_multica(status)]

        issues = self._list_issues_paginated(extra_args)

        work_items = [
            self._issue_to_work_item(issue, workspace_id)
            for issue in issues
        ]

        # 服务端按 multica 态过滤后，再按业务态精确收口（多对一映射的兜底）
        if status is not None:
            work_items = [item for item in work_items if item.status == status]

        return work_items

    def add_comment(self, item_id: str, comment: str):
        """添加评论"""
        self._run_multica_with_text_file([
            "issue", "comment", "add", item_id
        ], "--content-file", comment, capture=False)

    # ==================== 第三组：状态和分配 ====================

    def update_status(self, item_id: str, status: WorkItemStatus):
        """更新工作单元状态"""
        multica_status = self._status_to_multica(status)
        self._run_multica([
            "issue", "update", item_id,
            "--status", multica_status
        ], capture=False)

    def assign_work_item(
        self,
        item_id: str,
        assignee: str,
        role: str
    ):
        """分配任务给协作者"""
        # 1. Multica assign
        agent_id = self._resolve_agent_id(assignee)
        self._run_multica([
            "issue", "assign", item_id,
            "--to", agent_id
        ], capture=False)

        # 2. 更新 metadata 记录当前角色
        if role == "worker":
            self.update_work_item_metadata(item_id, worker=assignee)
        elif role == "reviewer":
            self.update_work_item_metadata(item_id, reviewer=assignee)

    # ==================== 第四组：查询 ====================
    # (find_work_item_by_dag_key 已删除——manifest.work_item_id + get_work_item 精准取代)

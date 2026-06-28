"""
GitHub 引擎实现
"""
import json
import subprocess
import yaml
from typing import List, Dict, Any, Optional
from .base import CollaborationEngine
from .models import WorkspaceInfo, WorkItem, WorkItemStatus, EngineConfig


class GithubEngine(CollaborationEngine):
    """GitHub 平台引擎实现

    使用 gh CLI 实现所有接口

    概念映射：
    - workspace → GitHub Repository (owner/repo)
    - work_item → GitHub Issue
    - status → labels: status:todo, status:in-progress, status:done
    - metadata → Issue body 中的 YAML frontmatter
    - worker/reviewer → assignees + body YAML + labels
    """

    # ==================== 环境变量管理 ====================

    @classmethod
    def get_required_env_vars(cls) -> List[Dict[str, str]]:
        """GitHub 引擎需要的配置项（仅非敏感配置）。

        认证不在此配置：用 `gh auth login` 让 gh CLI 自己管理 token。
        （若环境里已有 GITHUB_TOKEN，引擎会透传给 gh，但不在向导里索取。）
        """
        return [
            {
                'name': 'GITHUB_REPO',
                'description': 'GitHub 仓库 (格式: owner/repo)',
                'prompt': '请输入 GitHub 仓库 (例如: microsoft/vscode)',
                'validator': lambda x: '/' in x and len(x.split('/')) == 2
            },
        ]

    @classmethod
    def get_recommended_polling_interval(cls) -> int:
        # GitHub API 有限额，需要更保守
        return 60

    @classmethod
    def get_rate_limit_info(cls) -> Dict[str, int]:
        return {
            "requests_per_hour": 5000,
            "requests_per_minute": 60
        }

    # ==================== 内部工具方法 ====================

    def _run_gh(self, args: List[str], capture=True) -> Any:
        """调用 gh CLI"""
        cmd = ["gh"] + args

        # 设置 token (如果有)
        env = None
        if self.config.extra.get('GITHUB_TOKEN'):
            import os
            env = os.environ.copy()
            env['GITHUB_TOKEN'] = self.config.extra['GITHUB_TOKEN']

        result = subprocess.run(cmd, capture_output=capture, text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"gh 调用失败: {' '.join(cmd)}\n{result.stderr}")
        if capture and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return result.stdout.strip()
        return None

    def _status_to_label(self, status: WorkItemStatus) -> str:
        """将业务状态转换为 GitHub label"""
        mapping = {
            WorkItemStatus.TODO: "status:todo",
            WorkItemStatus.IN_PROGRESS: "status:in-progress",
            WorkItemStatus.IN_REVIEW: "status:in-review",
            WorkItemStatus.DONE: "status:done",
            WorkItemStatus.FAILED: "status:failed",
            WorkItemStatus.BLOCKED: "status:blocked"
        }
        return mapping[status]

    def _labels_to_status(self, labels: List[str]) -> WorkItemStatus:
        """从 GitHub labels 推断业务状态"""
        for label in labels:
            if label == "status:in-progress":
                return WorkItemStatus.IN_PROGRESS
            elif label == "status:in-review":
                return WorkItemStatus.IN_REVIEW
            elif label == "status:done":
                return WorkItemStatus.DONE
            elif label == "status:failed":
                return WorkItemStatus.FAILED
            elif label == "status:blocked":
                return WorkItemStatus.BLOCKED
        return WorkItemStatus.TODO

    def _build_issue_body(
        self,
        description: str,
        dag_key: str,
        worker: str,
        reviewer: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        wave: Optional[int] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        review_verdict: Optional[str] = None,
        review_comment: Optional[str] = None,
        verification: Optional[Dict[str, Any]] = None,
        review_report: Optional[Dict[str, Any]] = None,
        contract: Optional[Dict[str, Any]] = None
    ) -> str:
        """构建包含 YAML frontmatter 的 issue body"""
        metadata = {'dag_key': dag_key, 'worker': worker}
        if reviewer:
            metadata['reviewer'] = reviewer
        if blocked_by:
            metadata['blocked_by'] = blocked_by
        if wave is not None:
            metadata['wave'] = wave
        if artifacts:
            metadata['artifacts'] = artifacts
        if review_verdict:
            metadata['review_verdict'] = review_verdict
        if review_comment:
            metadata['review_comment'] = review_comment
        if verification:
            metadata['verification'] = verification
        if review_report:
            metadata['review_report'] = review_report
        if contract:
            metadata['contract'] = contract

        frontmatter = yaml.dump(metadata, default_flow_style=False, allow_unicode=True)
        return f"---\n{frontmatter}---\n\n{description}"

    def _parse_issue_body(self, body: str) -> tuple[str, Dict[str, Any]]:
        """解析 issue body，提取 YAML frontmatter 和描述"""
        if not body or not body.startswith('---'):
            return body, {}

        parts = body.split('---', 2)
        if len(parts) < 3:
            return body, {}

        try:
            metadata = yaml.safe_load(parts[1]) or {}
            description = parts[2].strip()
            return description, metadata
        except:
            return body, {}

    def _issue_to_work_item(self, issue_data: Dict, workspace_id: str) -> WorkItem:
        """将 GitHub issue 转换为 WorkItem"""
        # 解析 body
        body = issue_data.get('body', '')
        description, metadata = self._parse_issue_body(body)

        # 解析 labels
        labels = []
        for label in issue_data.get('labels', []):
            if isinstance(label, dict):
                labels.append(label['name'])
            else:
                labels.append(label)

        status = self._labels_to_status(labels)

        return WorkItem(
            id=str(issue_data['number']),
            workspace_id=workspace_id,
            title=issue_data.get('title', ''),
            description=description,
            status=status,
            dag_key=metadata.get('dag_key', ''),
            worker=metadata.get('worker'),
            reviewer=metadata.get('reviewer'),
            blocked_by=metadata.get('blocked_by', []),
            wave=metadata.get('wave'),
            artifacts=metadata.get('artifacts'),
            verification=metadata.get('verification'),
            review_verdict=metadata.get('review_verdict'),
            review_comment=metadata.get('review_comment'),
            review_report=metadata.get('review_report'),
            contract=metadata.get('contract')
        )

    # ==================== 第一组：工作空间 ====================

    def list_members(self, workspace_id: str) -> List[str]:
        """列出仓库协作者"""
        try:
            owner, repo = workspace_id.split('/')
            result = self._run_gh([
                "api", f"repos/{owner}/{repo}/collaborators",
                "--jq", ".[].login"
            ])

            if isinstance(result, str):
                return [name.strip() for name in result.strip().split('\n') if name.strip()]
            return []
        except:
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
        """创建 issue"""
        # 构建 body（包含 YAML frontmatter）
        body = self._build_issue_body(
            description, dag_key, worker, reviewer, blocked_by, wave
        )

        # 构建标签
        labels = [self._status_to_label(initial_status)]
        if wave is not None:
            labels.append(f"wave:{wave}")

        args = [
            "issue", "create",
            "--repo", workspace_id,
            "--title", f"[DAG:{dag_key}] {title}",
            "--body", body
        ]

        # 添加 labels
        for label in labels:
            args.extend(["--label", label])

        # gh issue create 返回 issue URL
        result = self._run_gh(args)

        # 从 URL 提取 issue number
        if isinstance(result, str) and '/issues/' in result:
            issue_number = result.split('/issues/')[-1].strip()

            # 分配给 worker（触发通知）
            self._run_gh([
                "issue", "edit", issue_number,
                "--repo", workspace_id,
                "--add-assignee", worker
            ], capture=False)

            return self.get_work_item(issue_number)

        raise RuntimeError(f"创建 issue 失败: {result}")

    def get_work_item(self, item_id: str) -> WorkItem:
        """获取 issue 详情"""
        result = self._run_gh([
            "issue", "view", item_id,
            "--repo", self.config.workspace_id,
            "--json", "number,title,body,labels,state"
        ])

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
        review_report: Optional[Dict[str, Any]] = None
    ) -> WorkItem:
        """更新工作单元的元数据"""
        # 先获取当前 issue
        current = self.get_work_item(item_id)

        # 合并元数据
        new_metadata = {
            'dag_key': current.dag_key,
            'worker': worker if worker is not None else current.worker,
            'reviewer': reviewer if reviewer is not None else current.reviewer,
            'blocked_by': blocked_by if blocked_by is not None else current.blocked_by,
            'wave': current.wave,
            'artifacts': artifacts if artifacts is not None else current.artifacts,
            'review_verdict': review_verdict if review_verdict is not None else current.review_verdict,
            'review_comment': review_comment if review_comment is not None else current.review_comment,
            'verification': verification if verification is not None else current.verification,
            'review_report': review_report if review_report is not None else current.review_report,
            # contract 全量重建 frontmatter 时必须透传，否则后续任何 metadata 更新都会冲掉它
            'contract': current.contract
        }

        # 重新构建 body
        new_body = self._build_issue_body(
            current.description,
            **new_metadata
        )

        # 更新 issue
        self._run_gh([
            "issue", "edit", item_id,
            "--repo", self.config.workspace_id,
            "--body", new_body
        ], capture=False)

        return self.get_work_item(item_id)

    def set_node_contract(self, item_id: str, contract: Any):
        """把节点 contract 下发到 issue frontmatter（单一事实源）。

        contract 可为 Contract dataclass 或 dict；统一转 dict 合并进 frontmatter，
        worker 读回后用同一套 validator 自校验。
        """
        from dataclasses import asdict, is_dataclass
        payload = asdict(contract) if is_dataclass(contract) else contract
        current = self.get_work_item(item_id)
        new_body = self._build_issue_body(
            current.description,
            dag_key=current.dag_key,
            worker=current.worker,
            reviewer=current.reviewer,
            blocked_by=current.blocked_by,
            wave=current.wave,
            artifacts=current.artifacts,
            review_verdict=current.review_verdict,
            review_comment=current.review_comment,
            verification=current.verification,
            review_report=current.review_report,
            contract=payload
        )
        self._run_gh([
            "issue", "edit", item_id,
            "--repo", self.config.workspace_id,
            "--body", new_body
        ], capture=False)

    def list_work_items(
        self,
        workspace_id: str,
        status: Optional[WorkItemStatus] = None
    ) -> List[WorkItem]:
        """列出 issues"""
        args = [
            "issue", "list",
            "--repo", workspace_id,
            "--json", "number,title,body,labels,state",
            "--limit", "1000",
            "--state", "all"
        ]

        # 按状态过滤（通过 label）
        if status:
            status_label = self._status_to_label(status)
            args.extend(["--label", status_label])

        result = self._run_gh(args)

        if not isinstance(result, list):
            return []

        return [
            self._issue_to_work_item(issue, workspace_id)
            for issue in result
        ]

    def add_comment(self, item_id: str, comment: str):
        """添加评论"""
        self._run_gh([
            "issue", "comment", item_id,
            "--repo", self.config.workspace_id,
            "--body", comment
        ], capture=False)

    # ==================== 第三组：状态和分配 ====================

    def update_status(self, item_id: str, status: WorkItemStatus):
        """更新工作单元状态"""
        new_label = self._status_to_label(status)

        # 获取当前 labels
        current = self.get_work_item(item_id)

        # 移除旧的 status labels
        for old_status in WorkItemStatus:
            old_label = self._status_to_label(old_status)
            if old_label != new_label:
                try:
                    self._run_gh([
                        "issue", "edit", item_id,
                        "--repo", self.config.workspace_id,
                        "--remove-label", old_label
                    ], capture=False)
                except:
                    pass  # 标签可能不存在

        # 添加新标签
        self._run_gh([
            "issue", "edit", item_id,
            "--repo", self.config.workspace_id,
            "--add-label", new_label
        ], capture=False)

    def assign_work_item(
        self,
        item_id: str,
        assignee: str,
        role: str
    ):
        """分配任务给协作者"""
        # 1. 使用 assignees（触发 GitHub 通知）
        self._run_gh([
            "issue", "edit", item_id,
            "--repo", self.config.workspace_id,
            "--add-assignee", assignee
        ], capture=False)

        # 2. 添加 role label（便于过滤）
        role_label = f"{role}:{assignee}"
        self._run_gh([
            "issue", "edit", item_id,
            "--repo", self.config.workspace_id,
            "--add-label", role_label
        ], capture=False)

        # 3. 更新 body YAML（持久化元数据）
        if role == "worker":
            self.update_work_item_metadata(item_id, worker=assignee)
        elif role == "reviewer":
            self.update_work_item_metadata(item_id, reviewer=assignee)

    # ==================== 第四组：查询 ====================
    # (find_work_item_by_dag_key 已删除——manifest.work_item_id + get_work_item 精准取代)

"""
CollaborationEngine 抽象接口 — manifest 驱动，纯业务语义

子类实现者须读：每个方法的 docstring 描述了编排层对该方法的**契约保证**，
即子类必须满足什么条件，编排器才能正常工作。只看接口定义 + docstring 即可
知道如何根据当前引擎的平台特性来实现。
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from .models import WorkspaceInfo, WorkItem, WorkItemStatus, EngineConfig


class CollaborationEngine(ABC):
    """协作引擎抽象接口 — 子类只需实现 9 个抽象方法。

    设计原则：
    1. 接口定义业务语义，不暴露技术细节（labels/body/state）
    2. 实现方式由各引擎内部决定（metadata 存哪、status 怎么表达）
    3. 引擎只管状态和元数据，不管执行（执行由外部 worker 负责）

    核心数据流（编排器视角）：
        create_work_item -> 返回 WorkItem.id（= manifest 中的 work_item_id）
        get_work_item(work_item_id) -> 每轮轮询调用，检测节点是否 done
        update_status / update_work_item_metadata / assign_work_item -> 改状态
        get_work_item(work_item_id) -> 必须能读到上述变更

    因此最重要的契约是：**写后读一致性**——任何 update_* / assign 后，
    紧接着的 get_work_item 必须返回更新后的值。
    """

    def __init__(self, config: EngineConfig):
        self.config = config

    # ==================== 环境变量管理 ====================

    @classmethod
    @abstractmethod
    def get_required_env_vars(cls) -> List[Dict[str, str]]:
        """声明引擎需要的环境变量（供 setup.py 交互式配置）。

        契约：返回的每个 dict 至少含 'name' 和 'description'；
        可选 'prompt'/'default'/'choices'/'validator' 用于交互引导。
        setup.py 会读这些声明来提示用户输入，结果写入 .env。

        返回格式示例：
            [{'name': 'YOUR_PLATFORM_WORKSPACE_ID',
              'description': '工作空间 ID',
              'prompt': '请输入 workspace ID:',
              'validator': lambda x: len(x) > 0}]
        """
        pass

    @classmethod
    def get_common_env_vars(cls) -> List[Dict[str, str]]:
        """通用环境变量（所有引擎共享，子类一般不覆盖）。"""
        return [
            {
                'name': 'ENGINE_TYPE',
                'description': '引擎类型',
                'prompt': '选择引擎类型',
                'choices': ['multica', 'github', 'mock']
            },
            {
                'name': 'POLLING_INTERVAL',
                'description': '轮询间隔（秒）',
                'prompt': '请输入轮询间隔（秒，默认 30）',
                'default': '30',
                'validator': lambda x: x.isdigit() and 10 <= int(x) <= 300
            }
        ]

    @classmethod
    def get_recommended_polling_interval(cls) -> int:
        """返回推荐的轮询间隔（秒）。子类按平台 API 限额覆盖。"""
        return 30  # 默认 30 秒

    @classmethod
    def get_rate_limit_info(cls) -> Dict[str, int]:
        """返回平台的 API 限额信息（信息性，不影响编排逻辑）。"""
        return {
            "requests_per_hour": 5000,
            "requests_per_minute": 100
        }

    # ==================== 第一组：工作空间（1 个）====================

    @abstractmethod
    def list_members(self, workspace_id: str) -> List[str]:
        """列出可分配任务的成员名称列表。

        契约：
        - 返回的名称必须与 manifest 中 worker/reviewer 字段的值**按字符串完全匹配**，
          否则 lint 会报 "worker not in squad pool"。
        - 如果平台用 id 而非 name 标识成员，内部做 name->id 映射，
          但此方法返回的是 **name**（与 manifest 对齐）。
        - workspace_id 参数是「作用域 id」：若引擎有小队/分组概念，
          config.squad_id 优先，否则用 workspace_id 退化为全员。

        实现示例：
        - multica: multica squad member list <squad_id> -> 提取 name
        - github:  gh api repos/{owner}/{repo}/collaborators -> 提取 login
        - mock:    返回内置 ['alice', 'bob', 'charlie']

        返回：['alice', 'bob', 'charlie']
        """
        pass

    # ==================== 第二组：工作单元 CRUD（5 个）====================

    @abstractmethod
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
        """在平台上创建一个工作单元，返回带确定性 id 的 WorkItem。

        **最重要的契约**：返回的 WorkItem.id 必须是一个**稳定且唯一**的标识符，
        后续 get_work_item(id) 用它能精准取回这个工作单元。
        编排层把这个 id 回填进 manifest 的 work_item_id 字段，
        之后所有查询都走 get_work_item(work_item_id)，不再全量扫描。

        平台如何存 metadata 由你决定（issue body YAML / metadata API / label 等），
        但存完后立刻调 get_work_item(id) 应能读回全部字段。

        参数：
            workspace_id: 作用域 id（通常 = config.squad_id 或 workspace_id）
            title: 任务标题（编排层会自动加 [DAG:{dag_key}] 前缀，你不必再加）
            description: 完整 issue body（worker 的上下文来源，须原样存入平台）
            dag_key: DAG 节点标识（存进 metadata，供 list_work_items 调试用）
            worker: 执行者名称（存进 metadata，不在此方法内 assign——assign 由编排层在派发时调 assign_work_item）
            reviewer: 审核者名称（可选，存进 metadata）
            blocked_by: 依赖的 DAG key 列表（存进 metadata）
            wave: 所属 wave（可选，存进 metadata）
            initial_status: 初始状态（默认 TODO，存进平台 status 字段/label）

        返回：WorkItem，其中 .id 是平台返回的确定性 id（int 转成 str 也可）。

        实现示例：
        - multica: issue create -> 拿 issue id -> metadata set 写各字段 -> get_work_item(id) 返回
        - github:  gh issue create（body 含 YAML frontmatter） -> 拿 issue number -> get_work_item(number) 返回
        """
        pass

    @abstractmethod
    def get_work_item(self, item_id: str) -> WorkItem:
        """按 item_id 精准取回工作单元的完整当前状态。

        **主查询接口**——编排层每个轮询周期都调此方法检测节点是否 done。
        这是 O(1) 精准查询，不扫描全量。

        契约：
        - 返回的 WorkItem 必须包含**全部**业务字段：
          status（反映 update_status 的最新值）、worker、reviewer、blocked_by、
          artifacts（worker 写入的产物）、review_verdict、review_comment。
        - **写后读一致性**：任何 update_status / update_work_item_metadata /
          assign_work_item 调用后，紧接着的 get_work_item 必须返回更新后的值。
        - item_id 在平台上不存在时应抛异常（编排层的 reconcile 据此清空 work_item_id 走新建）。

        实现示例：
        - multica: issue get <id> -> 从 metadata 解析各字段
        - github:  gh issue view <number> -> 从 body YAML frontmatter 解析

        返回：WorkItem（含全部业务字段）
        异常：item_id 不存在时抛 RuntimeError 或同类异常
        """
        pass

    @abstractmethod
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
        """更新工作单元的业务元数据（不改 status）。

        契约：
        - 参数为 None 的字段**不更新**（保持原值），只更新显式传入的字段。
        - 写入后立刻 get_work_item(item_id) 必须能读回新值。
        - 返回更新后的 WorkItem（方便调用方确认）。

        何时被调用：
        - worker 完成 -> 编排层写 artifacts（如 {"pr": "https://..."}）
        - reviewer 完成 -> 编排层写 review_verdict + review_comment
        - assign_work_item 内部也会调此方法同步 metadata

        实现示例：
        - multica: issue metadata set <id> --key worker --value alice（逐字段调）
        - github:  读当前 body YAML -> 合并新字段 -> gh issue edit --body 新body

        返回：更新后的 WorkItem
        """
        pass

    @abstractmethod
    def list_work_items(
        self,
        workspace_id: str,
        status: Optional[WorkItemStatus] = None
    ) -> List[WorkItem]:
        """列出工作单元（供进度查看/调试，非主查询路径）。

        契约：
        - 编排层的主查询走 get_work_item(work_item_id) 精准取，不走此方法。
        - 此方法主要用于调试、进度汇总、人工查看。
        - status 过滤是可选优化（不传则返回全部）。
        - 返回的每个 WorkItem 须含完整字段（同 get_work_item 契约）。

        实现示例：
        - multica: issue list --output json
        - github:  gh issue list --json number,title,body,labels,state

        返回：List[WorkItem]
        """
        pass

    @abstractmethod
    def add_comment(self, item_id: str, comment: str):
        """向工作单元追加一条评论（进度报告/通知）。

        契约：追加写入，不覆盖已有评论。失败不应中断编排（编排层会 catch）。

        实现示例：
        - multica: issue comment <id> --message <comment>
        - github:  gh issue comment <number> --body <comment>
        """
        pass

    # ==================== 第三组：状态和分配（2 个）====================

    @abstractmethod
    def update_status(self, item_id: str, status: WorkItemStatus):
        """更新工作单元状态（todo/in_progress/in_review/done/failed/blocked）。

        契约：
        - 写入后立刻 get_work_item(item_id) 必须返回新 status。
        - 状态是**排他**的（同一时刻只有一个状态），不是叠加。
        - 平台如何表达状态由你决定（status 字段 / label / state），
          但 get_work_item 解析时必须还原为 WorkItemStatus 枚举。

        何时被调用（编排层状态机）：
        - 派 worker 前:  -> IN_PROGRESS
        - worker done, 有 reviewer: -> IN_REVIEW
        - reviewer pass: -> DONE
        - worker fail / reviewer reject: -> BLOCKED

        实现示例：
        - multica: issue update <id> --status in_progress
        - github:  移除旧 status:xxx label + 添加新 status:xxx label
        """
        pass

    @abstractmethod
    def assign_work_item(
        self,
        item_id: str,
        assignee: str,
        role: str  # "worker" | "reviewer"
    ):
        """将工作单元分配给指定协作者，并同步 metadata。

        契约：
        - assignee 是**成员名称**（与 list_members 返回的格式一致），
          若平台用 id 标识，内部做 name->id 解析。
        - 必须做两件事：① 平台侧 assign（触发通知）② 同步 metadata
          （role=="worker" -> update_work_item_metadata(worker=assignee)，
           role=="reviewer" -> update_work_item_metadata(reviewer=assignee)）
          这样 get_work_item 才能读到当前分配。
        - role 只影响写哪个 metadata 字段，不影响平台 assign 逻辑。

        何时被调用：
        - 派发 worker 时: assign_work_item(id, worker_name, "worker")
        - 派发 reviewer 时: assign_work_item(id, reviewer_name, "reviewer")
        - 失败重派时: 再次 assign 同一 item 给新 worker

        实现示例：
        - multica: resolve agent name -> id -> issue assign --to <id> + metadata set worker/reviewer
        - github:  gh issue edit --add-assignee <login> + 更新 body YAML 的 worker/reviewer 字段
        """
        pass

    # ==================== 便捷方法（基类实现，子类不用覆盖）====================

    def check_member_exists(self, workspace_id: str, member_name: str) -> bool:
        """检查成员是否存在（基类实现）。"""
        members = self.list_members(workspace_id)
        return member_name in members

    def mark_in_progress(self, item_id: str):
        """便捷方法：update_status(IN_PROGRESS)。"""
        self.update_status(item_id, WorkItemStatus.IN_PROGRESS)

    def mark_done(self, item_id: str):
        """便捷方法：update_status(DONE)。"""
        self.update_status(item_id, WorkItemStatus.DONE)

    def mark_failed(self, item_id: str):
        """便捷方法：update_status(FAILED)。"""
        self.update_status(item_id, WorkItemStatus.FAILED)

    def mark_blocked(self, item_id: str):
        """便捷方法：update_status(BLOCKED)。"""
        self.update_status(item_id, WorkItemStatus.BLOCKED)

    def mark_in_review(self, item_id: str):
        """便捷方法：update_status(IN_REVIEW)。"""
        self.update_status(item_id, WorkItemStatus.IN_REVIEW)

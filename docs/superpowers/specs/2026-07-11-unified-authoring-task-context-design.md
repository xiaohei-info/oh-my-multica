# Unified Authoring Task Context Design

## 核心判断

当前问题值得修复。`final-acceptance` 和验收失败后的增量 `decompose` 直接把
内部 payload 以 YAML 写入 issue body，绕过了普通任务已经稳定使用的
`render_issue_body`、结构化 `source_refs` 和可复制 OMAC 命令。这不是展示层小问题，
而是会让 Agent 缺失引擎定位、仓库、集成分支和上游来源，真实运行中已经导致
acceptor 连续试错并通过全 workspace 搜索恢复上下文。

## 目标

1. 所有 authoring issue 使用同一条创建路径和同一套正文模板。
2. `final-acceptance` 可以直接从 issue body 或 `omac work show` 获得：
   - 完整 OMAC 命令环境；
   - 验收文档；
   - 唯一集成分支；
   - 当前项目仓库 URL；
   - 设计、验收、拆解和 closeout issue 链。
3. 增量 `decompose` 可以直接获得失败验收项、当前 Manifest 和触发它的
   `final-acceptance` issue。
4. metadata 只保存稳定结构化引用，不保存长正文或无法固定解析的报告文本。
5. Reviewer evidence guide 给出可直接通过 validator 的完整
   `integration_gate_mapping` 示例。

## 非目标

- 不修改 Multica AgentRuntime 的环境变量注入机制。OMAC 通过正文中的完整命令提供
  确定性入口，不扩展平台专有运行时接口。
- 不把验收文档、Manifest 或 review report 内联进 metadata。
- 不移除 contract/deliverable 附件评论；它们是附件承载机制，不是阶段交接评论。
- 不改变 final acceptance 的轮次、退出码、增量合并和重试语义。

## 方案比较

### 方案 A：只在 `acceptance.py` 拼更完整的 Markdown

改动最小，但会复制 `render_issue_body`、环境变量前缀、source issue 链和 metadata
写入规则。后续模板变更仍会再次分叉。

### 方案 B：让 `acceptance.py` 直接调用完整 `run_task`

复用度高，但 `run_task` 同时承担 review 往返、guard 和 human gate；final acceptance
只需要创建、唤醒和等待终态。直接复用会把不需要的状态机概念带入外层验收。

### 采用方案：抽取统一 authoring issue 创建原语

从 `pipeline/tasks.py` 抽取一个小而明确的创建原语，负责：

1. 创建占位 issue；
2. 用真实 issue id 调用 `render_issue_body`；
3. 发布 contract 附件；
4. 写入结构化 `source_refs`；
5. 返回创建后的 `WorkItem`。

`run_task`、`final-acceptance` 和增量 `decompose` 都调用该原语，各自保留现有等待和
状态机逻辑。这样消除特殊创建路径，但不合并不同生命周期。

## 数据结构

### AuthoringTaskSpec

新增内部 dataclass，集中描述创建 authoring issue 所需的稳定输入：

```python
@dataclass
class AuthoringTaskSpec:
    kind: TaskKind
    title: str
    dag_key: str
    assignee: str
    description: str = ""
    contract: Any = None
    source_refs: list[dict] = field(default_factory=list)
    source_of_truth: dict[str, str] = field(default_factory=dict)
```

它不包含轮询、reviewer、重试或平台字段，只描述“创建什么任务”。

### Contract 承载规则

- develop 节点继续使用 Manifest `Contract`。
- final acceptance 使用稳定字典：

```yaml
acceptance_doc: <完整验收文档对象>
flows: [ACC-001, ACC-002]
pr_base: main
repo_urls:
  - git@github.com:owner/repo.git
```

- 增量 decompose 使用稳定字典：

```yaml
mode: incremental
failed_items: [ACC-003]
pr_base: main
repo_urls:
  - git@github.com:owner/repo.git
manifest: <当前 Manifest 对象>
```

这些长内容只进入 contract 附件；metadata 只保存 `contract_ref`。

## 数据来源

### 集成分支

按以下顺序解析：

1. `manifest.meta.pr_base`；
2. 所有非空 `node.contract.pr_base` 的唯一值。

没有值时抛 `NeedsDecision`，并给出重新生成/修复 Manifest 的命令提示；存在多个不同值
时同样阻断，因为最终验收不能猜测集成分支。Snake 当前所有节点均为 `main`，因此会
稳定推导为 `main`，不再产生 `pr_base: null`。

### 仓库 URL

通过 `WorkItemStore.list_projects(workspace_id)` 找到
`store.config.project_id` 对应的 `ProjectInfo.repos`。取不到仓库时不阻断任务创建，但
正文和 `work show` 明确显示“仓库未登记”，提示运行 `omac init --check`；不允许
pipeline 直接调用 `git`、`gh` 或 Multica CLI。

### 上游 issue

final acceptance 的 source refs 按顺序组成：

1. `manifest.meta.source_issues`：设计方案、验收文档、任务拆解；
2. `manifest.meta.closeout_node` 对应节点的 `work_item_id`：最终开发交付。

增量 decompose 在以上引用后追加触发本轮失败的 final acceptance issue。

引用继续使用 `{issue_id, label, url}` 小对象，并通过 Store 写入 `source_refs` metadata。

## Issue 正文

统一调用 `render_issue_body`。为支持字典 contract，`_contract_summary` 同时读取 dataclass
属性和 mapping key。

final acceptance 正文包含：

- 可复制的 `omac guide role acceptor`；
- 带 `OMAC_ENGINE/OMAC_WORKSPACE_ID/OMAC_PROJECT_ID` 的 `work show/submit`；
- 人类可读的轮次、flows、集成分支和仓库；
- Markdown 上游 issue 链；
- “只按验收文档逐项 pass/fail”的硬约束。

正文不再内联完整验收 YAML。完整数据由 contract 附件和 `work show` 提供。

增量 decompose 正文包含失败 flow id、增量模式说明、集成分支、仓库和上游 issue 链；
完整 Manifest 仅通过 contract 附件和 `work show` 提供。

## Work Show

`work show` 保持 contract 为 source of truth，并增加通用展示字段：

- `contract.pr_base`
- `contract.repo_urls`
- `context.source_issues`

不从 issue body 反向解析任何数据。issue body 与 `work show` 都由创建时的结构化输入生成。

## Guide 修订

`omac guide artifact evidence` 的 reviewer 示例改为包含一个完整 gate：

```yaml
integration_gate_mapping:
  - gate: auth-e2e
    status: pass
    commands:
      - { cmd: "python3 -m pytest tests/e2e/test_login.py", exit_code: 0 }
    metrics: {}
    artifacts: []
```

字段名必须与 `validate_review_evidence` 一致，避免 Agent 只能搜索 OMAC 源码理解 schema。

## 错误处理

- 集成分支缺失或冲突：在创建 final acceptance 前抛 `NeedsDecision`，不创建残缺 issue。
- 项目仓库未登记：任务仍可创建，但正文包含 `omac init --check` 的修复提示。
- source issue 缺少 work item id：忽略该单条引用，不复制上游正文。
- contract 发布失败：沿用 Store 的 PlatformError，不继续 assign/wake。

## 向后兼容

- 不改变 `WorkItemStore` 和 `AgentRuntime` 公共接口。
- 旧 issue 无 `source_refs`、`repo_urls` 或 `pr_base` 时，`work show` 继续容错读取。
- 既有 develop/plan/acceptance/decompose 正文快照保持原语义，只统一内部创建实现。
- DAG exit code 和 final acceptance 外层循环行为保持不变。

## 测试策略

### 单元测试

1. final acceptance body 包含完整 OMAC 环境命令、`pr_base`、repo URL 和 source refs。
2. final acceptance contract 附件包含验收文档，但 metadata 不含验收正文。
3. 增量 decompose body 包含失败项和触发验收 issue 链，contract 含 Manifest。
4. `_contract_summary` 对 dataclass 和 dict 行为一致。
5. reviewer evidence guide 示例能通过实际 validator。

### 边界测试

1. Manifest 无任何 `pr_base` 时在派发前失败。
2. Manifest 节点出现两个不同 `pr_base` 时失败。
3. Project 没有 repo 时正文给出修复提示但流程可运行。
4. source issue 缺失时不生成错误 Markdown 链接。

### 回归测试

1. `test_delivery_acceptance.py` 的首轮通过、失败增量、轮次耗尽路径保持通过。
2. `test_tasks.py`、`test_dispatch.py` 和 `test_cli_work.py` 保持通过。
3. 完整 `python3 -m pytest tests/` 全绿。

## 完成标准

- final acceptance Agent 首次执行 issue body 中的命令即可成功 `work show`。
- Agent 无需搜索 plan id、猜 workspace/project 或回溯其他 issue 才能找到仓库。
- final acceptance 与增量 decompose 的 metadata 只包含稳定结构化字段和附件引用。
- 不新增 pipeline/cli 对平台 CLI 的直接调用。

# Agent 角色索引

本页只用于选择角色 guide，不包含各角色的完整协议。具体任务仍以
`omac work show <issue-id> --output json` 返回的实例事实、身份和 `guide_refs` 为准。

| 机制角色 | 何时出现 | 主要产出 | 读取命令 |
|---|---|---|---|
| planner | plan / acceptance 产出阶段 | 设计方案、验收文档 | `omac guide role planner` |
| orchestrator | 方案与验收过门后；总控验收 fail 后 | manifest DAG、增量 fix 节点 | `omac guide role orchestrator` |
| worker | develop 产出阶段 | PR、verification | `omac guide role worker` |
| reviewer | plan / acceptance / decompose / develop 的 review 阶段 | verdict、review report | `omac guide role reviewer` |
| acceptor | DAG 内层收敛后 | final acceptance results | `omac guide role acceptor` |

## 选择规则

1. 先读 `work show.task.identity` 与 `work show.task.phase`。
2. 再执行 `work show.guide_refs` 中列出的命令。
3. role guide 与实例上下文冲突时，以实例事实和 contract 为准。

## 职责边界

- planner 写设计方案和验收文档，不拆 DAG，不写业务代码。
- orchestrator 拆 manifest，不实现业务。
- worker 按 contract 开发，不自审自放行。
- reviewer 独立复核，不替产出者修改交付物。
- acceptor 按验收文档做端到端走查，不绕过未验证项。

## architect 能力画像

architect 不是第六个机制角色。它是 Agent 能力画像，适合配置为 planner、orchestrator
或架构 reviewer。承担 planner 时遵循 `omac guide role planner`；承担 orchestrator 时遵循
`omac guide role orchestrator`。

architect 重点关注模块边界、数据流向、依赖方向、跨模块契约、ADR 和架构漂移，
但仍受当前 issue 的实例事实与角色边界约束。

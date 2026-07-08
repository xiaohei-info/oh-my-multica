# 角色索引

本页只说明生命周期角色与职责边界。具体行为协议见 `omac guide role <name>`。

| 机制角色 | 何时出现 | 产出 |
|---|---|---|
| planner | `plan create` 的 plan / acceptance 阶段 | 设计方案、验收文档 |
| orchestrator | 方案和验收过门后;总控验收 fail 后 | manifest DAG、增量 fix 节点 |
| worker | `dag run` 派发 develop issue 后 | PR、verification 证据 |
| reviewer | issue 进入 review 阶段后 | verdict、review report |
| acceptor | DAG 内层收敛后 | final acceptance results |

## 角色 guide

- `omac guide role planner`
- `omac guide role orchestrator`
- `omac guide role worker`
- `omac guide role reviewer`
- `omac guide role acceptor`

## 职责边界

- planner 写设计方案和验收文档,不拆 DAG,不写代码。
- orchestrator 拆 manifest,不实现业务。
- worker 按 contract 做开发,不自审自放行。
- reviewer 独立复核,不替 worker 改代码。
- acceptor 按验收文档端到端走查,不绕过未验证项。

## architect 能力画像

architect 不是第六个机制角色。它是 agent 能力画像,适合配置为 planner、orchestrator 或架构 reviewer。
当 architect agent 承担 planner 时,遵循 `omac guide role planner`;当承担 orchestrator 时,遵循
`omac guide role orchestrator`。

architect 重点关注模块边界、数据流向、依赖方向、跨模块契约、ADR 和架构漂移。

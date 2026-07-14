# Agent 模板

本目录提供可直接导入 Multica 的 Agent 能力预设。模板只描述 Instructions 与 Skills，
不限制 Agent 名称、Runtime 类型，也不声明它最终必须承担哪个 OMAC 角色；这些都由用户在
`omac init` 中自由选择。

## 目录契约

- `_shared/instructions.md`：所有模板共用的工程纪律与 OMAC 协作协议。
- `<template>/instructions.md`：该能力模板的角色方法、边界和输出契约。
- `<template>/skills/<skill>/`：完整 Skill 目录，包含 `SKILL.md` 及其引用的文件。
- 不使用嵌套 `AGENTS.md`、`CLAUDE.md` 或 `SOUL.md`，避免项目被某个 Harness 打开时自动
  加载模板内容；创建 Agent 时才由 OMAC 拼接并注入 Instructions。

## 通用内容与环境内容的边界

模板保留了本机 Hermes Profiles、Codex `AGENTS.md`、Claude Code `CLAUDE.md` 中可跨
Harness 复用的核心内容：好品味、向后兼容、实用主义、简洁、数据结构优先、TDD、独立
验证、授权边界，以及 planner / orchestrator / worker / reviewer / acceptor / architect /
backend / frontend / pm 各自稳定的工作方法。

以下内容不会进入模板：本机绝对路径、Profile/Agent 实例名、模型与 Provider 参数、凭据、
个人工作区约定、Harness 启动命令，以及只对某一台机器成立的工具位置。OMAC 特有的
`work show/submit` 协议会明确保留，因为这些模板就是为 OMAC 团队协作提供的；它不会把
模板锁死到某个 Runtime。

## Skill 分配来源

Skill 组合按当前 Multica 实际分配固化，不根据角色名称猜测：

| 模板 | 当前来源 | Skill 数量 |
|---|---|---:|
| `architect`、`planner` | `hermes-architect` | 40 |
| `backend` | `hermes-backend-eng` | 13 |
| `frontend` | `hermes-frontend-eng-grok` | 13 |
| `worker` | 当前 Codex/Claude 工程 Agent 公共组合 | 13 |
| `pm`、`acceptor` | `hermes-pm` | 7 |
| `orchestrator` | `hermes-orchestrator` | 0 |
| `reviewer` | `hermes-reviewer` | 0 |

模板 Skill 是创建时的快照。已有 workspace Skill 会按名称复用，缺失 Skill 才上传；OMAC
不会覆盖已有 Agent 的 Instructions 或 Skill 分配。

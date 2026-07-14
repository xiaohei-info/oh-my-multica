# Agent 模板与初始化引导设计

## 目标

为尚未配置好 Agent 的用户提供九个可直接创建的 Agent 模板：`planner`、
`orchestrator`、`worker`、`reviewer`、`acceptor`、`architect`、`backend`、
`frontend`、`pm`。

模板只定义 Agent 的能力内容：Instructions 与完整 Skill 文件。Agent 名称、使用哪个
Runtime、以及最终映射到哪个 OMAC 生命周期角色，都由用户在 `omac init` 中选择。

模板是可选的初始化增强。OMAC 的正确运行仍以 `omac work show/submit`、内置 guide、
manifest 与证据校验为准，不依赖模板或 Skill 触发。

## 目录约定

```text
agents/
├── _shared/
│   └── instructions.md
├── planner/
│   ├── instructions.md
│   └── skills/<skill-name>/...
├── orchestrator/
├── worker/
├── reviewer/
├── acceptor/
├── architect/
├── backend/
├── frontend/
└── pm/
```

- 目录名就是模板 ID，不增加 `agent.yaml`。
- `_shared/instructions.md` 与模板自己的 `instructions.md` 拼接后，原样注入 Multica
  Agent 的 Instructions。
- 每个 `skills/<skill-name>/` 保存完整 Skill 目录，至少包含 `SKILL.md`，并保留其引用的
  `references/`、`scripts/`、`assets/` 等文件。
- Skill 组合以当前 Multica Agent 的实际绑定为准，不根据本机 Hermes Profile 的全部
  enabled Skill 推测或扩张。

## 当前 Skill 映射口径

| 模板 | Multica 事实来源 |
|---|---|
| `architect` | `hermes-architect` |
| `planner` | snake 示例中的 planner=`hermes-architect` |
| `backend` | `hermes-backend-eng` |
| `frontend` | 已归档的 `hermes-frontend-eng-grok` |
| `worker` | `codex-ubuntu-newapi`（Codex/Claude 当前使用相同 13-Skill 包） |
| `pm` | `hermes-pm` |
| `acceptor` | snake 示例中的 acceptor=`hermes-pm` |
| `orchestrator` | `hermes-orchestrator`，当前无绑定 Skill |
| `reviewer` | `hermes-reviewer`，当前无绑定 Skill |

## 初始化流程

```text
选择 engine/workspace/project
→ 发现现有 Runtime、Agent、Skill
→ 用户决定是否从模板创建 Agent
→ 选择一个或多个模板
→ 为每个模板输入 Agent 名称并选择 Runtime
→ 上传缺失 Skill，复用同名现有 Skill
→ 创建 Agent、注入 Instructions、绑定完整 Skill 集
→ 将新旧 Agent 合并成统一候选池
→ 用户自由映射 planner/orchestrator/workers/reviewers/acceptor
→ 写入 .omac/config.yaml
→ omac init --check
```

使用已有 Agent 时不修改其 Instructions 或 Skill。模板创建只新增 Agent；同名 Agent 或
同名但内容不同的 Skill 不静默覆盖。

## 引擎边界

CLI 层不直接执行 `multica`。模板文件的发现与 Instructions 拼接属于 OMAC 自身逻辑；
平台操作经 `AgentRuntime`：

- 列出可用 Runtime；
- 列出当前 Agent 与 Skill；
- 上传缺失 Skill 及其附属文件；
- 创建 Agent；
- 为新 Agent 设置 Skill。

`MulticaRuntime` 封装 `multica runtime list`、`multica skill list/import`、
`multica agent create` 与 `multica agent skills set`。
Mock runtime 提供确定性内存实现，供 init 和端到端测试使用。

## 失败与恢复

- 没有可用 Runtime：停止创建并给出安装/启动 Runtime 的明确提示。
- Skill 缺少 `SKILL.md`、名称非法或包含符号链接：在任何平台写操作前失败。
- 同名 Agent 已存在：不覆盖，提示改名或直接选择已有 Agent。
- Skill 上传成功但后续创建失败：保留已上传 Skill；流程幂等，重试时直接复用。
- Agent 创建成功但 Skill 绑定失败：报告 Agent ID 与可复制的修复命令，不删除 Agent。

## 验证

- 模板目录发现只返回九个模板，不把 `_shared` 当模板。
- Instructions 按 shared → role 顺序拼接。
- Skill 文件递归完整发现，拒绝路径逃逸和缺少 `SKILL.md`。
- Multica 命令参数与 JSON 解析有单元测试，不在测试中访问真实平台。
- `omac init` 覆盖只使用已有 Agent，以及创建模板 Agent 后与已有 Agent 混合映射的路径；
  模板目录测试独立覆盖全部九种模板及其 Skill 数量。
- `python3 -m pytest tests/` 全绿，并验证 wheel 中包含模板文件。

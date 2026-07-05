# AGENTS.md — 本仓开发约定

本文件是 `oh-my-agent-cluster`(omac CLI)本仓的开发护栏。跨项目通用工程纪律不在这里重复,
只放与本仓实现直接相关的硬约束。

## 红线

**pipeline / cli 层只调 engines 的 `WorkItemStore` / `AgentRuntime` 接口,绝不直接 shell out 平台 CLI。**
平台 CLI(multica / gh 等)的调用封装在 engines/ 适配层内。这条纪律是未来接入
Linear / Jira 只增适配器、不动 pipeline 的基础(code review 红线)。

web 路由层只做「解析参数 → 调命令函数 → 原样返回 JSON」,禁止在 web 层直接读
manifest、调 engine、二次加工数据。三种调用者(人 / agent / Web)看到的永远是同一个事实。

## 退出码契约(§5.1 — 稳定,调用方可脚本分支)

| 码 | 含义 |
|---|---|
| `0` | 成功 / DAG 收敛全部 done |
| `1` | 通用错误 |
| `2` | 平台/网络错误 |
| `3` | 认证错误(平台 CLI 未登录等) |
| `5` | 校验失败(lint / 证据 schema) |
| `10` | 推进中(仅单轮 tick 模式) |
| `20` | 需要调用者决策(附结构化报告) |

业务层 `raise OmacError` 子类转入对应退出码,不散落 `sys.exit`。退出码不可破坏。

## 术语(§10.2 — 面向用户输出与文档一律用通用说法)

| 统一术语 | 禁止用法 |
|---|---|
| 结果回收(collect_results) | harvest、收割 |
| 进行中节点(running_nodes) | in-flight、在飞 |
| 就绪节点(ready_nodes) | frontier |

代码标识符、报错文案、guide 文本与设计文档一律用统一术语,不出现硬翻译行话。

## TDD

1. **先写测试,再实现**:新行为有新验证,改旧行为有回归验证。至少覆盖主路径、边界、已知风险点。
2. **交付 = 代码 + 测试 + 必要文档**:`python3 -m pytest tests/` 全绿是完成的必要条件。
3. **完成前独立验证**:「代码写完」不等于完成。完成必须附客观证据(测试 / 构建 / 命令实际运行输出)。

## 报错即教学

面向用户的输出与报错遵循「报错即教学」:缺什么、怎么补,给出可复制命令。参数错误时打印
错误 + 该命令完整 help,agent 用错一次即自纠错。

## 完成定义

一个改动算「完成」,需同时满足:

- [ ] 落在计划范围内,未越界
- [ ] 只消费 `WorkItemStore` / `AgentRuntime` 接口,未直接 shell out 平台 CLI
- [ ] 有新增/回归验证且实际全绿,`python3 -m pytest tests/` 通过
- [ ] 文档/guide 在必要时同步更新

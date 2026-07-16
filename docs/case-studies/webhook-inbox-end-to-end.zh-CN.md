# Webhook Inbox：一次真实的端到端交付

2026 年 7 月 16 日，oh-my-multica 从一个带有生产约束的需求开始，经过设计、开发、独立评审、
合并和最终验收，交付了一个可运行的 Webhook Inbox。

完整结果公开在
[demo 仓库](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox)中。
它是一个刻意保持小型的 FastAPI + SQLite 服务，但交付约束是真实的：HMAC-SHA256 认证、按原始
字节保证幂等、事务安全的数据库去重、请求体大小限制、稳定错误响应、带 Hash 的依赖锁定、
Python 3.10–3.13 CI，以及带 Healthcheck 的非 root 容器。

这不是预先写好路径的 mock run。Multica 承载工作项和 Coding Agent Runtime；oh-my-multica
持续规划和控制交付，直到集成后的默认分支通过已经批准的验收文档。

## 从一个目标到交付 DAG

输入只有一份提交到仓库的[交付目标](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/blob/main/GOAL.md)。
Planner 和 Orchestrator Agent 检查当前仓库，形成设计与验收定义，并动态生成包含五个节点的
[manifest DAG](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/blob/main/.omac/webhook-inbox.yaml)：

| 节点 | 交付边界 | 公开结果 |
| --- | --- | --- |
| 共享地基 | 领域类型、配置、错误模型、质量基线 | [PR #2](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/2) |
| HTTP API | 原始 Body 有界读取、Header、稳定错误、健康检查 | [PR #3](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/3) |
| 持久化与去重 | 先验签后解析、事务安全的 SQLite 去重 | [PR #4](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/4) |
| 交付资产 | Hash 固定依赖、CI 矩阵、Docker 镜像、运维文档 | [PR #5](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/5) |
| 集成验收 | 全链路 Harness 与跨 Track 收口 | [PR #6](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/6) |

依赖关系来自真实架构。共享合同完成后，API 与持久化 Track 可以并行；交付资产依赖两条实现
Track，集成验收则等待服务完成组装。早期的基础实现 PR #1 被后续方案替代并关闭，没有被算作
成功交付。

## 角色与模型分工

仓库内的配置记录了这次交付的角色分工：

- `codex-ubuntu` 负责规划、编排和最终验收。
- 三个 `newapi` Worker Runtime 组成高性价比实现池。
- `hermes-reviewer` 是主要独立 Reviewer，同时配置了另一名独立 Reviewer 作为备选。
- Loop 最多并行执行三个节点，并明确限制 Worker、CI、Review、Merge 重试次数和验收轮次。

这正是动态规划与确定性推进分工的实际价值。更强的推理能力可以集中在架构、拆解、评审和验收；
数量更多、边界清楚的编码与测试任务可以交给便宜 Runtime，又不让这些 Worker 决定整个项目是否完成。

## Loop 控制了什么

设计、验收文档、项目规则和 manifest 通过门禁后，外层交付 Loop 由确定性程序接管：

```text
结果收集 → 计算 ready nodes → 分发 → 验证证据 → 评审
→ 有界返工 → 合并 → 最终验收 → 仅在收敛后停止
```

Worker 在任务合同内部保留自由，可以阅读代码、选择实现、运行测试并提交 Pull Request。Loop 则
控制依赖、证据要求、评审交接、重试上界、合并资格、恢复入口和最终退出码。

## 第一轮最终验收失败了

当时所有实现 PR 都已合并，生产服务自带的验收 Harness 也已经通过，但项目在第一轮最终验收时
仍然没有收敛。

经过评审的验收文档启动了刻意保留的最小 `src.api:app` Stub，生产 Composition Root 实际是
`compose:app`。Acceptor 严格执行验收文档，记录了 2 个 flow 通过、9 个 flow 失败；它没有用
Worker 的成功总结替换这份证据，也没有推测集成服务“应该没问题”。

验收源在
[commit `56daf00`](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/commit/56daf007c2cd6fc1b25c03e22ad4e957d18ea2a3)
中完成修正。随后完整验收从头执行，11 个 flow 全部通过，包括同 ID 并发投递、重启后持久化、
Body 限制、认证失败、冲突、查询和数据库健康。控制器只在第二轮通过后返回 exit 0。

这次失败比一条干净的 demo 路径更有价值。代码生成已经结束，但证据来源与生产入口并不一致。
Loop 保留失败结果、拒绝完成，并要求先修正事实来源，再重新执行验收。

## 最终证据

| 证据 | 实际结果 |
| --- | ---: |
| DAG 收敛 | 5/5 节点 `done` |
| 评审后的改动 | 5 个 Pull Request 合并 |
| 测试 | 86 tests 通过 |
| 覆盖率 | 97.18% |
| CI 兼容性 | Python 3.10、3.11、3.12、3.13 |
| 容器交付 | 非 root 镜像、Healthcheck、签名 Webhook 冒烟测试 |
| 最终验收 | 11/11 flows 通过 |
| 控制器 | exit 0 |

[验收文档](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/blob/main/.omac/webhook-inbox.acceptance.yaml)、
manifest、源代码、测试、CI 历史、Pull Request，以及先失败再修正的提交历史都已公开。demo README
中提供了可以复制执行的复现命令。

## 这个案例能说明什么

这个案例说明，oh-my-multica 可以针对一个小型生产风格服务动态规划交付链，把工作拆成可独立验证
的任务，使用多个 Agent Runtime，保留失败证据，并且只在集成验收通过后收敛。

它不代表每个 Agent 第一次执行都会正确，也不代表所有仓库都需要五个节点，更不代表确定性控制
可以自动修复错误需求。它说明的是一个更窄、也更实用的能力：当实现工作被大规模委托出去时，
项目是否完成不必由最后一个仍在运行的 Agent 凭感觉判断。


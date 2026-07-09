# exit 20 之后的恢复手册

`omac dag run` 以 exit 20 退出 = 引擎处理不了,需要你决策。stdout 已有结构化报告:
失败节点、证据摘要、受阻下游、可执行的下一步动作命令。

## 决策流程

1. `omac dag status <manifest> --output json` —— 全景快照
2. `omac node show <manifest> <key>` —— 单节点完整证据链
   (验证命令输出 / reviewer report / PR / 平台 issue 链接 / 回退计数)
3. 三选一:
   - `omac node retry <manifest> <key> [--worker 换人]` —— 重置为 todo
   - `omac node accept <manifest> <key>` —— 人工确认接受已知风险,标 done
   - `omac node abandon <manifest> <key>` —— 放弃,解锁非硬依赖下游
   - 直接改 manifest(改契约/拆任务),必要时 `omac dag check` 过门禁
4. `omac dag run <manifest>` —— 重跑即续跑,done 节点复用

## abandoned 语义(§7.5)

`abandon` 不是「失败」,是**显式决策**:该节点不再推进,但**上游 abandoned 视同依赖已满足**,
不硬依赖它的下游可继续推进(下轮 tick 进入就绪集)。

这意味着:
- 下游节点照常派发、照常产出;只是它依赖的 abandoned 上游的交付物**不会被等待**。
- 报告中会对「经过 abandoned 上游的节点」加注记,提醒你这些节点的验收范围可能因上游缺失而不完整。
- 若事后反悔,用 `omac node retry` 把 abandoned 节点重置回 todo 即可续跑。

适用场景:某节点反复失败且价值有限,或上游被放弃后下游仍可独立交付(如实验性功能、可选集成)。

## 常见退出原因

- 证据不达标 / reviewer reject / CI / merge 回退耗尽(≤3 次)→ 节点 blocked 或 needs_decision
- reviewer pass-with-nits 默认回到 worker 处理建议项,不消耗 review_bounce,不进入 needs_decision
- 总控验收外层循环耗尽(acceptance.max_rounds)仍有 fail → 未通过项清单在报告里

## exit 20 决策表

| 报告信号 | 含义 | 先看什么 | 推荐动作 |
|---|---|---|---|
| `reviewer reject` | reviewer 找到 blocker | report.blockers、PR diff、失败命令 | 修复同一节点:`omac node retry <manifest> <key>` |
| `CI` 失败 | worker 证据过门后 CI 未过 | CI 日志、verification.commands | 修 CI 后 retry;若不是卡内问题,拆新节点或改 contract |
| `merge` 回退耗尽 | PR 无法自动合并或冲突反复出现 | PR base、冲突文件、集成分支状态 | 换 worker retry 或手工解冲突后重跑 |
| `acceptance.max_rounds` 耗尽 | 总控验收多轮增量修复仍 fail | 验收 fail 清单、增量 manifest | 降范围、补新节点,或明确部分放弃 |

`accept` 只用于显式接受已知风险;不是跳过失败验证。
`retry` 会把节点重新放回 todo;`abandon` 会让下游不再等待该节点交付物。

## 失败处理(编排器视角)

如果某节点 failed:
- **失败隔离**: 引擎自动标记该节点的下游为 blocked(不再派发)
- **其它分支继续**: 独立 track 不受影响
- **你的决策**:
  1. 分析失败原因(从 issue comment / PR / run messages 读)
  2. 调整 manifest:
     - 换 worker(换个擅长的 agent)
     - 拆小(一个节点拆成 2–3 个小节点)
     - 降范围(砍非核心需求)
  3. 重跑 `omac dag run`(幂等:已 done 不重派,非 done 重置重派)
  4. 或接受部分失败,汇总给用户

**失败示例与调整**:
```yaml
# 原 manifest(jwt-service failed)
nodes:
  jwt-service:
    worker: frontend-agent  # 失败了
    blocked_by: [oauth-setup]

# 调整 manifest(换 worker + 拆小)
nodes:
  jwt-core:
    worker: backend-agent  # 换擅长后端的
    blocked_by: [oauth-setup]
  jwt-middleware:
    worker: frontend-agent
    blocked_by: [jwt-core]  # 拆小,先做核心
```

## 重试铁律

- 重试是**显式决策**,不自动发生 —— 这是设计原则
- 失败隔离**不可绕过**:blocked 节点不会自动重置,必须经 `omac node retry` 显式决策
- 防假收尾:汇报"完成"前必须核对 manifest,有非终态节点 + 无活跃 `omac dag run` = 未在监督

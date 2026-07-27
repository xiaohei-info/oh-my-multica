# exit 20 恢复协议（Controller Agent）

`omac dag run` 以 exit 20 退出，表示确定性引擎需要调用者决策。它不是成功，也不是可以
静默重试的普通错误。stdout 中的结构化报告是本次恢复的实例事实。

## 指令优先级

1. exit 20 报告与 `omac dag status <manifest> --output json`。
2. `omac node show <manifest> <key>` 的节点证据链。
3. 若节点已有 issue，`omac work show <issue-id> --output json` 的实例上下文。
4. manifest contract 与 previous review。
5. 本恢复 guide。

## 决策流程

1. 运行 `omac dag status <manifest> --output json` 获取全景快照。
2. 对每个未决节点运行 `omac node show <manifest> <key>`，读取验证输出、reviewer report、
   PR、平台 issue 链接和回退计数。
3. 选择一个显式动作：
   - `omac node retry <manifest> <key> [--worker 换人]`：重置为 todo。
   - `omac node accept <manifest> <key>`：人工接受已知风险并标记 done。
   - `omac node abandon <manifest> <key>`：放弃该节点，解锁非硬依赖下游。
   - 修改 manifest：调整 contract、换人或拆小；必要时先运行 `omac dag check`。
4. 重新运行 `omac dag run <manifest>`。已 done 节点会复用，其余节点续跑。

### 阶段级恢复与 merge 观察

- 恢复以 issue 的真实 `phase` 为准：authoring 只恢复 worker；reviewer run 失败或结束但未提交 verdict 时，保留同一 issue、worker 的 PR/verification 与 review subject，在 `review` 阶段重新派发 reviewer；不得错误退回 worker。
- `merging` 只观察已经持久化的 merge intent/request。GitHub/平台返回 `UNKNOWN` 或临时读取失败时，节点留在 `merging`，不消耗 `retry.merge`、不回退 worker、也不再发送第二次 merge 请求。
- 只有明确的 `CLOSED_UNMERGED` 或已知 merge 命令失败才进入 merge 失败/回退语义。恢复期间仍必须以远端 `MERGED + mergedAt` 作为 done 的唯一事实。

## plan / decompose review 耗尽后的继续决策

当 `omac plan create/resume` 因 plan、acceptance 或 decompose 的 review 轮次耗尽而
exit 20 时，先读取报告中的 `item_id`、`rounds`、`last_opinion` 和 `next_action`，再由
Human 或获授权的 operator 明确决定是否增加一轮：

```bash
omac plan continue-review --dag-key decompose-p-xxxx \
  --reason "Human approved one additional review round"
omac plan resume --plan-id p-xxxx
```

- `continue-review` 在同一 work item 上写入小型 `review_continuation` decision，绝对上限
  只增加一轮；它不清零 `review_bounce`，未消费上一轮授权时也拒绝继续叠加。
- exhausted reject 会通过 OMAC `reset_review` 和 status 恢复到 authoring/todo，让 producer
  先更新交付；final pass-with-nits 已有新交付时只开放下一 Reviewer round。
- 该命令不修改 `.omac/config.yaml` 或 `retry.review`，因此不会为了单次人工决策制造项目
  Git revision 漂移。`retry.review` 仍是新任务和旧自动化入口的默认预算。
- 命令只读检查 active Agent；发现活跃 run 时拒绝，不会自动 cancel。等待当前 run 结束后
  再执行，除非调用者另行选择现有的显式取消/重启流程。

## 动作选择

| 报告信号 | 先检查 | 推荐动作 |
|---|---|---|
| `reviewer reject` | report.blockers、真实 diff、失败命令 | 修复同一节点后 `omac node retry` |
| CI 失败 | CI 日志、verification.commands | 修 CI 后 retry；若 contract 不合理则改 contract 或拆新节点 |
| merge 回退耗尽 | PR base、冲突文件、集成分支 | 换 worker retry，或手工解决冲突后重跑 |
| `acceptance.max_rounds` 耗尽 | fail 清单、增量 manifest | 降范围、补节点，或显式 accept/abandon |

`accept` 只用于接受已知风险，不是跳过失败验证。`retry` 必须有新的事实或方案，
不能原样重复已失败的尝试。

## Agent 可决策与 Human 决策边界

Controller Agent 可以在不改变目标、contract 和风险接受程度时执行 retry，例如换用更合适的
worker、按现有 blocker 修复，或把过粗节点拆成语义等价的小节点。

以下情况必须先请求 Human：

- 使用 `node accept` 接受未通过验证或已知风险。
- 使用 `node abandon` 放弃用户可见能力，或会使下游验收范围不完整。
- 删除验收 flow、放宽 `non_goals`、降低 coverage/集成门或改变产品范围。
- 两种修复方案会产生不同的兼容性、成本、数据迁移或安全后果。
- 缺少凭证、外部授权或业务决策，Agent 无法从实例事实中确定答案。

请求决策时报告：未决节点、失败事实、已执行命令、受阻下游、可选动作、每个动作的风险，
以及推荐项。不要只说“需要确认”。

## abandoned 语义

`abandon` 是显式决策：该节点不再推进，但上游 abandoned 视为依赖已满足，
不硬依赖其交付物的下游可在下一轮进入就绪节点集合。

- 下游会继续派发，但不会等待 abandoned 节点的交付物。
- 报告会标注经过 abandoned 上游的节点，提醒验收范围可能不完整。
- 若反悔，可用 `omac node retry` 把该节点恢复为 todo。

适用于价值有限、反复失败的可选能力，或上游放弃后仍能独立交付的实验性集成。

## 常见退出原因

- 证据不达标、reviewer reject、CI 或 merge 回退耗尽：节点 blocked 或 needs_decision。
- pass-with-nits 默认回到 worker 处理建议项，不消耗 review bounce，不进入 needs_decision。
- 总控验收超过 `acceptance.max_rounds` 仍有 fail：报告保留未通过项清单。

## 失败隔离

- 失败节点的硬依赖下游自动 blocked，不再派发。
- 独立分支继续推进，不被单点失败拖停。
- Controller Agent 可换 worker、拆成 2–3 个更小节点、降低范围或接受部分失败。

调整示例：

```yaml
# 原节点反复失败
nodes:
  jwt-service:
    worker: frontend-agent
    blocked_by: [oauth-setup]

# 换擅长的 Agent，并按可独立验证边界拆小
nodes:
  jwt-core:
    worker: backend-agent
    blocked_by: [oauth-setup]
  jwt-middleware:
    worker: frontend-agent
    blocked_by: [jwt-core]
```

## 完成条件

- 每个 exit 20 未决节点都有明确决策和理由。
- manifest 通过 `omac dag check`（若有修改）。
- 已重新运行 `omac dag run`，或明确向 Human 报告为什么暂不续跑。
- 汇报完成前核对 manifest；存在非终态节点且没有活跃 `dag run`，就不是完成。

## 禁止事项

- 禁止自动重试。重试必须是显式决策。
- 禁止绕过失败隔离直接推进 blocked 节点。
- 禁止在未读取实例事实和证据链时凭猜测 accept 或 abandon。
- 禁止把 exit 20 报告成成功。
- 禁止为了单次 plan review 继续而修改 `retry.review` 并污染被评审 revision；使用
  `omac plan continue-review`。

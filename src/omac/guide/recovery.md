# exit 20 之后的恢复手册

dag run 以 exit 20 退出 = 引擎处理不了,需要你决策。stdout 已有结构化报告:
失败节点、证据摘要、受阻下游、可执行的下一步动作命令。

## 决策流程

1. `omac dag status <manifest> --output json` —— 全景快照
2. `omac node show <manifest> <key>` —— 单节点完整证据链
   (验证命令输出 / reviewer report / PR / 平台 issue 链接 / 回退计数)
3. 三选一:
   - `omac node retry <manifest> <key> [--worker 换人]` —— 重置为 todo
   - `omac node abandon <manifest> <key>` —— 放弃,解锁非硬依赖下游
   - 直接改 manifest(改契约/拆任务),必要时 `omac plan check` 过门禁
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

- 证据不达标 / reviewer reject / CI·merge 回退耗尽(≤3 次)→ 节点 blocked
- 总控验收外层循环耗尽(acceptance.max_rounds)仍有 fail → 未通过项清单在报告里

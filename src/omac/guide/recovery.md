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

## 常见退出原因

- 证据不达标 / reviewer reject / CI·merge 回退耗尽(≤3 次)→ 节点 blocked
- 总控验收外层循环耗尽(acceptance.max_rounds)仍有 fail → 未通过项清单在报告里

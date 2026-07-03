# manifest DAG 与 contract

manifest(.orchestrator/<name>.yaml)是状态机载体:节点、依赖、contract、
work_item_id、status 全部在此,进 git。

## 节点结构

```yaml
nodes:
  - id: user-api
    title: Implement user API
    worker: backend-agent          # 必填,须在 agent 池内
    reviewer: review-agent         # 可选,必须 ≠ worker
    blocked_by: [shared-contracts] # 依赖
    contract:                      # 硬合同,lint 强制
      objective: 一句话目标
      source_of_truth: [docs/design.md#user-api]
      acceptance: [ ... ]          # 须锚定验收文档条目
      non_goals: [ ... ]
      verification_commands: [ ... ]
      integration_gates: [ ... ]   # 每个 gate 须有 source_of_truth/delivery_goal/covers/acceptance_refs/commands
      pr_base: feature/v1          # PR 基线,防打错分支
      coverage_gate: 90
```

## lint 口径

objective/acceptance/non_goals/verification_commands/integration_gates/pr_base
必填且非空;coverage_gate 0-100;required_contracts 路径必须存在;
reviewer ≠ worker;DAG 无环;worker/reviewer 在 agent 池内。

字段支持 `${ENV_VAR:-默认值}` 展开,id 类值不必硬写进文件。

(P3 迁移完整拆解方法论)

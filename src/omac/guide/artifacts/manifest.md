# manifest DAG 与 contract

manifest 是 `.omac/<name>.yaml`,承载 DAG 节点、依赖、contract、work_item_id 和 status。

## 节点结构

```yaml
nodes:
  - id: user-api
    title: Implement user API
    worker: backend-agent
    reviewer: review-agent
    blocked_by: [shared-contracts]
    contract:
      objective: 一句话目标
      source_of_truth: [docs/design.md#user-api]
      acceptance: [flow-login]
      non_goals: [不碰支付流程]
      scope_paths: [src/auth/**]
      verification_commands: [python3 -m pytest tests/auth]
      integration_gates:
        - name: auth-e2e
          source_of_truth: [docs/design.md#auth-flow]
          delivery_goal: 登录主链路可用
          covers: [login]
          acceptance_refs: [flow-login]
          commands: [python3 -m pytest tests/e2e/test_login.py]
      pr_base: feature/login
      coverage_gate: 90
```

## lint 口径

`objective/source_of_truth/acceptance/non_goals/verification_commands/integration_gates/pr_base`
必填且非空。`acceptance` 必须锚定验收文档 flow id。DAG 必须无环,worker/reviewer 必须在 agent 池内。

## 防跑偏原则

- 契约即代码:共享类型只 import,不重定义。
- 单一事实源:description 只放设计文档锚点,不复制正文。
- CI 抓接口/边界漂移,reviewer 抓语义漂移。

orchestrator 通过 `omac work submit <issue-id> --manifest-file <file>` 交付 manifest。

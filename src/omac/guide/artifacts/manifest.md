# manifest DAG 与 contract

manifest 是 `.omac/<name>.yaml`,承载 DAG 节点、依赖、contract、work_item_id 和 status。

## 拆分粒度

每个节点是最小独立 PR/test/review 单元:一个 worker 能独立开发,能独立运行
`verification_commands`,能独立提交 PR,reviewer 能只看该节点交付物与 contract 判定
pass/reject。

拆分目标是最大化并行开发。节点必须拆到不能继续独立拆分为止:如果一个节点还能拆出
另一个有独立 contract、独立测试命令、独立 PR 和明确下游能力的任务,就继续拆。

停止拆分的边界:

- 再拆只剩纯文件搬运、纯类型补丁、单个样式微调等无独立验收价值的微任务。
- 再拆会把同一事务一致性边界拆散,导致两个 PR 都无法独立验证。
- 再拆会制造明显 merge 冲突,且无法通过先抽 shared contract/API 消除。

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
- 并行优先:用稳定 contract/API 切开任务,减少 `blocked_by`;只有真正运行前置才写硬依赖。
- CI 抓接口/边界漂移,reviewer 抓语义漂移。

## 执行可读性

后续 worker 可能是低推理预算模型。每个节点 contract 必须能独立指导开发,
不能依赖执行者自行补全隐含上下文。

- `objective` 必须描述可交付结果。
- `source_of_truth` 必须指到包含数据结构、边界条件和模块边界的章节。
- `non_goals` 必须列出相邻但不该做的范围。
- `verification_commands` 必须能直接复制运行。
- `integration_gates` 必须说明交付目标、验收映射和命令。

orchestrator 通过 `omac work submit <issue-id> --manifest-file <file>` 交付 manifest。

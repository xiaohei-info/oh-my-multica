# manifest 产物合同

## 使用场景

本合同用于 `decompose` 产出或评审阶段，把已批准的设计与验收文档拆成可并行推进、可独立验证的
manifest DAG。manifest 通常保存为 `.omac/<name>.yaml`。

第一步必须运行：

```bash
omac work show <issue-id> --output json
```

以返回的 task、context、authority、guide_refs 和 submit 为当前实例事实，并从实例上下文读取
可用 agent pool。本文是静态 guide，不得覆盖实例事实、contract、已有 manifest 或增量拆解上下文。

## 最小合法示例

以下示例列出完整 contract 形状；`worker` 和 `reviewer` 必须替换为实例 agent pool 中的不同成员：

```yaml
meta:
  name: login-renewal
nodes:
  - id: auth-renewal
    title: Implement session renewal
    worker: backend-agent
    reviewer: review-agent
    blocked_by: []
    contract:
      objective: 会话过期时续期并最多重放一次原请求
      source_of_truth:
        - docs/design.md#跨模块契约
      required_contracts: []
      acceptance:
        - flow-login-renewal
      non_goals:
        - 不修改支付流程
      verification_commands:
        - python3 -m pytest tests/test_auth_renewal.py
      integration_gates:
        - name: auth-renewal-e2e
          layer: L1
          source_of_truth:
            - docs/design.md#验收映射
          delivery_goal: 登录续期主链路可用
          covers:
            - session-renewal
          acceptance_refs:
            - flow-login-renewal
          commands:
            - python3 -m pytest tests/test_auth_renewal_e2e.py
          required_metrics: {}
          artifacts: []
      quality:
        required_outcomes:
          - id: renewal-replays-once
            source_ref: acceptance#flow-login-renewal.renew-session
        business_tests:
          - id: renewal-e2e
            outcome_refs:
              - renewal-replays-once
            command: python3 -m pytest tests/test_auth_renewal_e2e.py
            level: e2e
            real_dependencies:
              - real auth service test environment
            must_fail_on_base: true
        runtime_data_policy: real-or-error
      pr_base: feature/login-renewal
      coverage_gate: 90
      acceptance_doc: null
      scope_paths:
        - src/auth/**
```

## 字段语义

### DAG 粒度

每个节点是最小独立 PR/test/review 单元：一个 worker 能独立开发、独立运行
`verification_commands`、独立提交 PR，reviewer 也能只依据该节点交付物与 contract 作出结论。

拆分目标是最大化并行开发，节点必须拆到不能继续独立拆分为止。只要还能拆出具有独立 contract、
测试命令、PR 和明确下游能力的任务，就继续拆；以下情况停止：

- 再拆只剩纯文件搬运、纯类型补丁或单个样式微调，没有独立验收价值。
- 再拆会破坏同一事务一致性边界，使各 PR 无法独立验证。
- 再拆会制造明显合并冲突，且无法先用稳定共享 contract/API 消除。

`blocked_by` 只表示节点开始执行前真实必需的前置节点。优先用稳定 contract/API 解耦，减少硬依赖；
引用的节点必须存在，整图必须无环。

### 防跑偏原则

- 契约即代码：共享类型只 import，不在不同节点重复定义。
- 单一事实源：节点只引用设计与验收锚点，不复制权威正文。
- 并行优先：先用稳定 contract/API 切开任务，再声明真正的运行前置。
- CI 捕获接口和边界漂移，reviewer 判断目标、验收和非目标是否发生语义漂移。

### 节点字段

| 字段 | 语义 |
|---|---|
| `id` | manifest 内唯一、稳定的节点标识。 |
| `title` / `description` | 简短说明；`description` 只放事实源锚点，不复制设计正文。 |
| `worker` / `reviewer` | 必须来自实例 agent pool，且 reviewer 不得与 worker 相同。 |
| `blocked_by` | 真实运行前置节点 id 列表；无前置时使用空列表。 |
| `work_item_id` / `status` | 运行时回填的工作项和状态；authoring 时不要凭空伪造。 |
| `contract` | 节点唯一实施与评审合同。 |

### contract 全字段

| 字段 | 语义 |
|---|---|
| `objective` | 一句话描述可交付结果。 |
| `source_of_truth` | 指向包含数据结构、边界条件、模块边界和契约的权威章节。 |
| `required_contracts` | 开始前必须存在的共享合同路径；非空路径会由 lint 检查存在性。 |
| `acceptance` | 引用验收文档中的稳定 flow id。 |
| `non_goals` | 相邻但明确禁止扩张的范围。 |
| `verification_commands` | worker 可直接复制运行的节点验证命令。 |
| `integration_gates` | 节点交付后必须通过的跨模块或端到端门。 |
| `quality.required_outcomes` | 必须完整实现的业务结果；每项用稳定 `id` 和 `acceptance#flow.action` 锚定真实验收动作。 |
| `quality.business_tests` | 证明业务结果的 integration/e2e 测试；声明覆盖结果、精确命令、真实依赖和基线失败要求。 |
| `quality.runtime_data_policy` | 固定为 `real-or-error`：生产路径只能返回真实结果或暴露真实错误，禁止 fake/mock/synthetic 数据兜底。 |
| `pr_base` | PR 必须基于的集成分支。 |
| `coverage_gate` | 0 到 100 的数字，默认 90。 |
| `acceptance_doc` | 可选的验收文档结构上下文；仅在实例 contract 需要时填充。 |
| `scope_paths` | 可选的主要代码归属范围，用于表达稳定模块边界和降低并行冲突。 |

每个 `integration_gates` 条目必须给出 `name`、`layer`、`delivery_goal`，以及非空的
`source_of_truth`、`covers`、`acceptance_refs`、`commands`。`required_metrics` 若出现必须是
object，`artifacts` 若出现必须是列表。worker verification 和 reviewer report 必须复现 contract
中的 gate 名称、命令、事实源与交付目标。

后续 worker 可能是低推理预算模型。每个 contract 必须独立可执行，不能依赖隐含上下文；
边界条件、禁止范围、验证入口和集成结果都要显式写出。

`quality` 是完整交付合同，不是覆盖率装饰：`required_outcomes` 必须覆盖节点承诺的所有业务结果；
每个 outcome 至少被一个 `business_tests` 条目覆盖；测试命令必须同时出现在
`verification_commands` 或某个 integration gate 中；`level` 只能是 `integration` 或 `e2e`；
`real_dependencies` 必须明确测试依赖的真实系统、容器、数据库或确定性的本地实现。
不得把“可运行骨架”、临时实现、未完成分支或计划以后补齐的功能写成已交付节点。

`scope_paths` 是主要代码归属范围，不是穷举文件白名单。完成 contract 所必需的必要配套文件，
例如测试、锁文件、migration、生成物或构建配置，可以随节点修改；worker 必须在 PR 或
verification 中说明原因。reviewer 应判断这些改动是否服务于 contract、违反 `non_goals` 或破坏
并行边界，不能只因文件未列入 `scope_paths` 就 reject。

## 校验硬门

1. YAML 必须可解析；每个节点必须有 `id` 和 `worker`。
2. worker/reviewer 必须在 agent pool 内，且 reviewer 与 worker 不同。
3. `blocked_by` 只能引用有效节点，完整 DAG 不得有环；增量节点 id 不得与既有节点冲突。
4. contract 的 `objective`、`source_of_truth`、`acceptance`、`non_goals`、
   `verification_commands`、`integration_gates`、`pr_base` 必须非空。
5. 每个 integration gate 的必填标量与列表都必须非空；metrics/artifacts 类型必须正确。
6. `quality` 必须存在；outcome/test id 唯一，source_ref 指向真实 action，每个 outcome 被业务测试覆盖，测试命令已声明，且 runtime data policy 为 `real-or-error`。
7. `coverage_gate` 必须是 0 到 100 的数字；`required_contracts` 中的路径必须存在。
8. 提供验收文档时，每个 `contract.acceptance` 必须锚定真实 flow id。
9. `meta.closeout_node` 若存在，必须引用 manifest 中的节点。

## 常见错误 → 修正

| 常见错误 | 修正 |
|---|---|
| 一个节点同时包含多个可独立交付能力 | 按稳定 contract/API 拆成独立 PR/test/review 单元。 |
| 为了表达顺序感而增加 `blocked_by` | 只保留真实运行前置，其余通过合同解耦。 |
| contract 只写目标，没有验证入口 | 补齐全部必填字段和至少一个完整 integration gate。 |
| 用单元测试或只断言 schema 的测试充当业务验收 | 增加真实 integration/e2e 测试，并映射到具体 required outcome。 |
| 节点只交付骨架、临时实现或 fake 数据兜底 | 继续拆分或完成真实业务实现；运行时失败必须真实暴露。 |
| `acceptance` 使用自然语言摘要 | 改为验收文档中的稳定 flow id。 |
| 把 `scope_paths` 当拒绝其他文件的依据 | 允许必要配套文件，并要求在 PR 或 verification 中解释。 |
| 复制整段设计到 `description` | 只保留 `source_of_truth` 锚点，维持单一事实源。 |

## 提交

提交前重新读取 `work show`，使用其返回的精确 submit 命令。`decompose` 产出的常见形状是：

```bash
omac work submit <issue-id> --manifest-file <file>
```

解析或 lint 失败会以校验错误返回；按错误逐项修正后重试，不要绕过校验或手动改平台状态。

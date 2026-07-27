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

## plan 到 DAG 的规范化交接

decompose Reviewer 审查作者提交的 manifest deliverable。评审通过后，
`omac plan create/resume` 使用与 `omac dag run` 相同的 `Manifest` / `Contract`
执行模型写出 canonical 文件：

- 显式默认值 `coverage_gate: 90` 可以省略，重新加载时恢复为 90；非默认覆盖率门槛
  必须保留在 canonical 文件中。
- 空 `required_contracts: []` 和节点上的 null `acceptance_doc` 可以省略，重新加载后
  分别恢复为空列表和 None 的执行语义。
- 已评审的权威 acceptance 产物只写入一次，由 `meta.acceptance_file` 指向同目录文件；
  manifest 同时补充 `acceptance_required`、`plan_id`、`source_issues`，不会在每个节点
  contract 中复制第二份可能漂移的验收正文。
- `work_item_id` 与 `status` 是运行态事实；`omac dag run` 派发和推进节点时再补充，
  不改变节点的可执行 contract。

这是语义规范化，不是第二个评审对象。若 `acceptance_required` 为 true 但
`acceptance_file` 缺失，DAG 执行会失败关闭，并提示如何恢复已评审的 acceptance 产物。

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
      acceptance_contributions:
        - flow_id: flow-login-renewal
          action_ids:
            - ACT-LOGIN-RENEWAL-01
            - ACT-LOGIN-RENEWAL-02
      acceptance_refs:
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
      pr_base: feature/login-renewal
      coverage_gate: 90
      acceptance_doc: null
      scope_paths:
        - src/auth/**
  - id: auth-renewal-e2e
    title: Verify session renewal journey
    worker: integration-agent
    reviewer: review-agent
    blocked_by: [auth-renewal]
    contract:
      objective: 独立执行并证明完整登录续期 flow
      source_of_truth: [docs/design.md#验收映射]
      acceptance_claims: [flow-login-renewal]
      acceptance_refs: [flow-login-renewal]
      non_goals: [不重新实现会话续期组件]
      verification_commands: [python3 -m pytest tests/e2e/test_auth_renewal.py]
      integration_gates:
        - name: auth-renewal-flow
          layer: L2
          source_of_truth: [docs/design.md#验收映射]
          delivery_goal: 完整登录续期 flow 可独立复验
          covers: [session-renewal-flow]
          acceptance_refs: [flow-login-renewal]
          commands: [python3 -m pytest tests/e2e/test_auth_renewal.py]
      pr_base: feature/login-renewal
```

## 字段语义

### DAG 粒度

每个节点是完整、生产可用、可独立验收的最小 PR/test/review 单元：一个 worker 能独立开发、独立运行
`verification_commands`、独立提交 PR，reviewer 也能只依据该节点交付物与 contract 作出结论。禁止把目录空壳、
接口骨架、固定返回值、占位实现或生产假数据兜底声明为节点完成；如果节点必须等待后续补丁才获得 objective
声称的价值，应重新划分 contract。

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
| `acceptance_claims` | 当前节点负责执行并独立证明的完整 flow；无需重复枚举该 flow 的 Action。 |
| `acceptance_contributions` | 当前节点实现归属的精确 `{flow_id, action_ids}`；只能引用验收文档中 `kind: business-action` 的 Action。 |
| `acceptance_refs` | 仅用于需求追溯的 flow id；不产生 Worker 或 Reviewer 验收义务。 |
| `acceptance` | 历史完整 flow claim 字段；保持原语义可读取，不得与新字段混用。 |
| `non_goals` | 相邻但明确禁止扩张的范围。 |
| `verification_commands` | worker 可直接复制运行的节点验证命令。 |
| `integration_gates` | 节点交付后必须通过的跨模块或端到端门。 |
| `pr_base` | PR 必须基于的集成分支。 |
| `coverage_gate` | 0 到 100 的数字，默认 90。 |
| `acceptance_doc` | 可选的验收文档结构上下文；仅在实例 contract 需要时填充。 |
| `scope_paths` | 可选的主要代码归属范围，用于表达稳定模块边界和降低并行冲突。 |

每个 `integration_gates` 条目必须给出 `name`、`layer`、`delivery_goal`，以及非空的
`source_of_truth`、`covers`、`acceptance_refs`、`commands`。`required_metrics` 若出现必须是
object，`artifacts` 若出现必须是列表。worker verification 和 reviewer report 必须复现 contract
中的 gate 名称、命令、事实源与交付目标。worker verification 还必须通过成功命令下的
`business_tests` 将每条完整 flow claim 和 Action contribution 映射到具体业务测试；trace ref 不需要测试映射。

整张 DAG 必须形成显式责任矩阵：每个 flow 恰有一个 canonical full owner；每个
`business-action` 至少有一个 contribution owner；full owner 必须等于或传递依赖该 flow 的所有
contribution owners。`flow-step` 由 full owner 执行，不写入 manifest contributions。这样上游
bootstrap 无法冒充完整 UJ owner，final closeout 也无需复制近千个步骤 ID。

后续 worker 可能是低推理预算模型。每个 contract 必须独立可执行，不能依赖隐含上下文；
边界条件、禁止范围、验证入口和集成结果都要显式写出。

`scope_paths` 是主要代码归属范围，不是穷举文件白名单。完成 contract 所必需的必要配套文件，
例如测试、锁文件、migration、生成物或构建配置，可以随节点修改；worker 必须在 PR 或
verification 中说明原因。reviewer 应判断这些改动是否服务于 contract、违反 `non_goals` 或破坏
并行边界，不能只因文件未列入 `scope_paths` 就 reject。

## 校验硬门

1. YAML 必须可解析；每个节点必须有 `id` 和 `worker`。
2. worker/reviewer 必须在 agent pool 内，且 reviewer 与 worker 不同。
3. `blocked_by` 只能引用有效节点，完整 DAG 不得有环；增量节点 id 不得与既有节点冲突。
4. contract 的 `objective`、`source_of_truth`、至少一种验收责任、`non_goals`、
   `verification_commands`、`integration_gates`、`pr_base` 必须非空。
5. 每个 integration gate 的必填标量与列表都必须非空；metrics/artifacts 类型必须正确。
6. `coverage_gate` 必须是 0 到 100 的数字；`required_contracts` 中的路径必须存在。
7. 提供验收文档时，claim/ref 必须锚定真实 flow，contribution 必须锚定真实 `business-action`；每个 flow 恰有一个完整 owner、业务 Action 贡献闭包完整，且 full owner 的依赖闭包覆盖全部 contribution owners。
8. `meta.closeout_node` 若存在，必须引用 manifest 中的节点。

## 常见错误 → 修正

| 常见错误 | 修正 |
|---|---|
| 一个节点同时包含多个可独立交付能力 | 按稳定 contract/API 拆成独立 PR/test/review 单元。 |
| 节点只交付骨架、占位或假数据兜底 | 将节点改成自身完整、可运行、可验收的真实基础能力；否则与后续实现合并成一个完整 contract。 |
| 为了表达顺序感而增加 `blocked_by` | 只保留真实运行前置，其余通过合同解耦。 |
| contract 只写目标，没有验证入口 | 补齐全部必填字段和至少一个完整 integration gate。 |
| 普通组件把 flow id 写成完整 claim | 组件只声明业务 Action contribution；完整 owner 放在依赖所有贡献节点的集成/closeout 节点。 |
| full owner 再复制该 flow 的全部 Action ID | 删除重复 contributions；full owner 通过 DAG 依赖闭包承接实现结果并执行完整 flow。 |
| 只需追溯需求却声明验收义务 | 使用 `acceptance_refs`；trace ref 不要求完整 UJ 或业务测试。 |
| 旧 `acceptance` 与新字段混用 | 将旧值明确迁移到 `acceptance_claims`，再补 contributions/refs；禁止静默改义。 |
| 把 `scope_paths` 当拒绝其他文件的依据 | 允许必要配套文件，并要求在 PR 或 verification 中解释。 |
| 复制整段设计到 `description` | 只保留 `source_of_truth` 锚点，维持单一事实源。 |

## 提交

提交前重新读取 `work show`，使用其返回的精确 submit 命令。`decompose` 产出的常见形状是：

```bash
omac work submit <issue-id> --manifest-file <file>
```

解析或 lint 失败会以校验错误返回；按错误逐项修正后重试，不要绕过校验或手动改平台状态。

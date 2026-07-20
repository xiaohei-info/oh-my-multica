# evidence 产物合同

## 使用场景

本合同覆盖三种结构化证据：worker verification、reviewer report 和 final acceptance results。
它们由左移门与权威门共用，缺项会在 `omac work submit` 当场失败。每个 issue 只提交
`work show` 指定的那一种形状。

第一步必须运行：

```bash
omac work show <issue-id> --output json
```

以返回的 task、context、contract、authority、guide_refs 和 submit 为当前实例事实。本文是静态
guide，不得覆盖实例事实、contract、评审对象或精确提交命令。

## 最小合法示例

以下三种证据互不替代；字段值必须按当前实例 contract 填写。

### worker verification

```yaml
commands:
  - cmd: "python3 -m pytest tests/auth"
    exit_code: 0
    summary: "passed"
    business_tests:
      - { acceptance: "flow-login", test: "tests/auth/test_login.py::test_user_can_login" }
integration_gates:
  - name: auth-e2e
    source_of_truth: [docs/design.md#auth-flow]
    delivery_goal: 登录主链路可用
    commands:
      - { cmd: "python3 -m pytest tests/e2e/test_login.py", exit_code: 0 }
    metrics: {}
    artifacts: []
pr_base: feature/login
coverage: 92
env_setup:
  - "docker compose up -d db"
```

## reviewer report

```yaml
review_goals:
  - acceptance 全覆盖且逐条可验证
diff_reviewed: true
tests_rerun: true
integration_tests_rerun: true
coverage_checked: true
full_review_completed: true
acceptance_mapping:
  - { acceptance: "flow-login", evidence: "tests/e2e/test_login.py", status: pass }
integration_gate_mapping:
  - gate: auth-e2e
    status: pass
    source_of_truth: [docs/design.md#auth-flow]
    delivery_goal: 登录主链路可用
    commands:
      - { cmd: "python3 -m pytest tests/e2e/test_login.py", exit_code: 0 }
    metrics: {}
    artifacts: []
blockers: []
nits: []
```

## final acceptance results

```yaml
- id: flow-login
  status: pass
- id: flow-payment
  status: fail
  notes: 支付成功页未展示订单号
```

## 字段语义

### worker verification 字段

| 字段 | 语义 |
|---|---|
| `commands` | contract `verification_commands` 的实际运行结果；`cmd` 文本必须精确匹配，`exit_code` 必须为 0。 |
| `commands[].business_tests` | 当前成功命令实际执行的具体业务测试索引；每项包含 contract 中的 `acceptance` 和稳定的 `test` 标识。承载命令必须有非空 `cmd`，且 `exit_code` 必须是整数 `0`；supporting command 可以不含该字段。 |
| `integration_gates` | 按 gate 名称记录命令、指标、产物、事实源和交付目标。 |
| `pr_base` | 必须与 contract `pr_base` 完全一致。 |
| `coverage` | 数字覆盖率，必须达到 contract `coverage_gate`。 |
| `env_setup` | 可复跑的环境准备步骤；contract 声明 integration gates 时必须是非空字符串列表。 |

PR URL 不写入 verification YAML，而是通过 submit 的 `--pr-url` 单独提交。

### reviewer report 字段

| 字段 | 语义 |
|---|---|
| `review_goals` | 非空评审目标列表，说明本轮独立验证什么。 |
| `diff_reviewed` / `tests_rerun` / `coverage_checked` | 必须为 `true`，表示已看 diff、独立复跑测试并检查覆盖率。 |
| `full_review_completed` | 必须为 `true`，表示发现问题后仍完成整个评审范围，并一次性报告本轮全部已发现问题。 |
| `integration_tests_rerun` | contract 有 integration gates 时必须为 `true`。 |
| `acceptance_mapping` | 逐项映射 contract `acceptance` 到证据和 `pass/fail` 状态。 |
| `integration_gate_mapping` | 按 gate 名称记录独立复跑结果，字段必须与 contract 对齐。 |
| `blockers` | pass 类 verdict 时必须为空；reject 时必须非空并给出可执行阻塞原因。 |
| `nits` | 不阻塞通过的改进建议。 |

verdict 不写入 report YAML，而是通过 submit 的 `--verdict` 提交；合法值为 `pass`、
`pass-with-nits` 或 `reject`。

### final acceptance results 字段

| 字段 | 语义 |
|---|---|
| `id` | 验收文档中的 flow id。 |
| `status` | 只能是 `pass` 或 `fail`。 |
| `notes` | `fail` 时必填，写明可复现失败现象；`pass` 时可省略。 |

## 校验硬门

### worker verification

1. submit 必须同时提供 PR URL 和 verification 文件；GitHub PR 必须可交付且不是 draft。
2. `commands` 与每条 integration gate 的 `commands` 必须覆盖 contract 中的精确命令，且退出码为 0。
3. contract 中每条 acceptance 必须被普通命令或 integration gate 成功命令下的具体 `business_tests` 覆盖；不得引用 contract 外的 acceptance。
4. gate 的 `source_of_truth` 和 `delivery_goal` 必须与 contract 完全一致。
5. metrics 必须达到 contract 阈值，contract 要求的 artifacts 必须全部出现。
6. contract 声明 integration gates 时，`env_setup` 必须非空且每项都是非空字符串。
7. `pr_base` 必须匹配，`coverage` 必须是数字且不低于 coverage gate。

### reviewer report

1. `review_goals` 和 `acceptance_mapping` 必须非空，并覆盖 contract 的每个 acceptance。
2. 基础复核标志和 `full_review_completed` 必须为 `true`；有 integration gates 时还必须独立复跑集成测试。
3. integration gate mapping 必须覆盖每个 gate，且命令、指标、产物、事实源和交付目标通过校验。
4. pass 或 pass-with-nits 不得有 blockers；reject 必须有 blockers。

### final acceptance results

1. 顶层必须是列表，id 不得重复。
2. 必须逐项、且只能覆盖验收文档中的全部 flow id；漏项和多项都会失败。
3. status 只能为 `pass/fail`，每个 fail 都必须有非空 notes。

## 常见错误 → 修正

| 常见错误 | 修正 |
|---|---|
| command 文本与 contract 近似但不完全相同 | 复制 contract 原命令执行并原样记录。 |
| 只写“测试通过”摘要 | 记录每条命令、退出码，以及 gate 的指标和产物。 |
| verification 没有 `business_tests`，或只用 coverage/mock 调用证明功能 | 在实际成功命令下列出逐 acceptance 的具体业务测试；Reviewer 查看测试代码确认其验证真实业务结果。 |
| reviewer 复用 worker 声明，没有独立复跑 | reviewer 按 env_setup 重建环境并记录自己的 mapping。 |
| reviewer 发现一个 blocker 就提交 reject | 记录该问题后继续完成整个评审范围，设置 `full_review_completed: true`，一次性提交本轮全部 blockers 和 nits。 |
| pass verdict 仍保留 blockers | 清空 blockers；若确有阻塞则提交 reject。 |
| reject 没有可执行阻塞原因 | 在 blockers 写明失败事实、影响和修正入口。 |
| 最终验收漏掉一个 flow 或额外创造 id | 严格按 acceptance 文档逐项生成结果。 |
| fail 只有状态没有说明 | 补充非空 notes，写清可复现现象。 |

## 提交

提交前重新读取 `work show`，使用其返回的精确 submit 命令。常见形状如下：

```bash
# worker verification
omac work submit <issue-id> --pr-url <pr-url> --verification-file <file>

# reviewer report
omac work submit <issue-id> --verdict pass --report-file <file>

# final acceptance results
omac work submit <issue-id> --acceptance-results-file <file>
```

校验失败时按结构化错误逐项修正后重试；不要手动推进平台状态。

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
  - { cmd: "python3 -m pytest tests/auth", exit_code: 0, summary: "passed" }
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
quality:
  delivered_revision: def456
  outcome_mapping:
    - outcome: login-succeeds
      implementation: [src/auth/login.py]
      tests: [tests/e2e/test_login.py]
  regression_proof:
    - test_id: login-e2e
      base_ref: abc123
      base_exit_code: 1
      head_ref: def456
      head_exit_code: 0
  runtime_fallbacks: []
  known_gaps: []
  evidence_origin: real
```

## reviewer report

```yaml
reviewed_revision: def456
review_goals:
  - acceptance 全覆盖且逐条可验证
diff_reviewed: true
tests_rerun: true
integration_tests_rerun: true
coverage_checked: true
review_scope:
  changed_files: [src/auth/login.py, tests/e2e/test_login.py]
  all_changed_files_reviewed: true
  all_outcomes_reviewed: true
  all_business_tests_rerun: true
  runtime_fallback_audit_completed: true
findings: []
outcome_mapping:
  - { outcome: "login-succeeds", status: pass }
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
| `integration_gates` | 按 gate 名称记录命令、指标、产物、事实源和交付目标。 |
| `pr_base` | 必须与 contract `pr_base` 完全一致。 |
| `coverage` | 数字覆盖率，必须达到 contract `coverage_gate`。 |
| `env_setup` | 可复跑的环境准备步骤；contract 声明 integration gates 时必须是非空字符串列表。 |
| `quality.delivered_revision` | Worker 本次交付对应的精确 PR head revision；必须与提交时平台读取到的当前 PR head 一致。 |
| `quality.outcome_mapping` | contract 每个 required outcome 对应的真实实现文件和真实业务测试文件。 |
| `quality.regression_proof` | 每个 business test 的基线/当前 revision 与退出码；要求基线按合同失败、当前 revision 通过。 |
| `quality.runtime_fallbacks` / `known_gaps` | 必须为空；存在 fake/mock/synthetic 运行时兜底或未完成需求时不得提交完成。 |
| `quality.evidence_origin` | 生产交付必须为 `real`；mock engine 证据只用于 OMAC 自身状态机测试。 |

PR URL 不写入 verification YAML，而是通过 submit 的 `--pr-url` 单独提交。

### reviewer report 字段

| 字段 | 语义 |
|---|---|
| `review_goals` | 非空评审目标列表，说明本轮独立验证什么。 |
| `reviewed_revision` | 本次完整评审对应的精确 revision；revision 改变后必须重新完整评审。 |
| `diff_reviewed` / `tests_rerun` / `coverage_checked` | 必须为 `true`，表示已看 diff、独立复跑测试并检查覆盖率。 |
| `integration_tests_rerun` | contract 有 integration gates 时必须为 `true`。 |
| `review_scope` | 非空 changed files，以及“全部文件、全部 outcomes、全部业务测试、运行时兜底审计”四个完成标志。 |
| `findings` | 本 revision 一次完整扫描发现的全部问题；每项包含 id、severity、category、location、evidence、impact、required_fix。 |
| `outcome_mapping` | 逐项映射 quality required outcomes；通过类 verdict 必须全部 pass。 |
| `acceptance_mapping` | 逐项映射 contract `acceptance` 到证据和 `pass/fail` 状态。 |
| `integration_gate_mapping` | 按 gate 名称记录独立复跑结果，字段必须与 contract 对齐。 |
| `blockers` | blocker finding id 的精确列表。 |
| `nits` | nit finding id 的精确列表。 |

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

1. submit 必须同时提供 canonical GitHub PR URL（`https://github.com/<owner>/<repo>/pull/<number>`）和 verification 文件；PR 必须可交付且不是 draft。`artifacts.pr_url` 是唯一 PR 字段，禁止 `artifacts.pr`。返工必须继续使用同一个 canonical PR。
2. `commands` 与每条 integration gate 的 `commands` 必须覆盖 contract 中的精确命令，且退出码为 0。
3. gate 的 `source_of_truth` 和 `delivery_goal` 必须与 contract 完全一致。
4. metrics 必须达到 contract 阈值，contract 要求的 artifacts 必须全部出现。
5. contract 声明 integration gates 时，`env_setup` 必须非空且每项都是非空字符串。
6. `pr_base` 必须匹配，`coverage` 必须是数字且不低于 coverage gate。
7. `quality.delivered_revision` 必须等于当前 PR head，且每条 regression proof 的 `head_ref` 必须等于该 revision。
8. outcome mapping、regression proof 和 integration gate evidence 必须完整且每个 id/name 只出现一次；重复、未知或畸形 gate 均拒绝。`runtime_fallbacks`、`known_gaps` 为空，`evidence_origin` 为 `real`。

### reviewer report

1. `reviewed_revision`、`review_goals` 和 Worker `quality.delivered_revision` 必填；`reviewed_revision` 必须同时等于 Worker revision 和当前 PR head；review scope 必须列出 changed files，四个完整性标志全部为 true。
2. Reviewer 必须对该 revision 一次性完成全部 changed files、outcomes、真实业务测试和 fake/runtime fallback 审计，提交一个完整问题批次，不得发现一个就提前停止。
3. 每个 finding 结构完整且 id 唯一；`blockers`、`nits` 必须精确等于对应 severity 的 finding id。
4. outcome、acceptance 和 integration gate mapping 必须对 contract 项逐项且仅映射一次；重复项、未知项、非法状态和缺项均拒绝。命令、指标、产物、事实源和交付目标必须通过校验。
5. `pass` 必须零 findings；`pass-with-nits` 只能有 nit findings；`reject` 至少有一个 blocker finding。
6. `pass-with-nits` 沿用既有流程：只回到 worker 一次，不再进行第二轮 reviewer；Worker 必须提交一个不同于已评审 revision 的新 PR revision 和完整新证据，因此任何功能、契约、数据完整性、安全或验证问题都必须 reject。
7. merge command 必须同时包含 `{pr_url}` 与 `{delivered_revision}`；默认 GitHub merge 使用 `--match-head-commit`，只允许合并 Worker 证据门确认的当前交付 revision。普通 pass 下它与 Reviewer revision 相同；pass-with-nits 返工下它是 Worker 的新 revision。
8. CI/merge command 按参数列表执行，不经过 shell。模板值只会替换为单个参数；可使用环境变量赋值以及受支持的 `env`、`command`、`timeout` wrapper，但不得依赖管道、重定向、命令替换或其他 shell 运算符。

### final acceptance results

1. 顶层必须是列表，id 不得重复。
2. 必须逐项、且只能覆盖验收文档中的全部 flow id；漏项和多项都会失败。
3. status 只能为 `pass/fail`，每个 fail 都必须有非空 notes。

## 常见错误 → 修正

| 常见错误 | 修正 |
|---|---|
| command 文本与 contract 近似但不完全相同 | 复制 contract 原命令执行并原样记录。 |
| 只写“测试通过”摘要 | 记录每条命令、退出码，以及 gate 的指标和产物。 |
| reviewer 复用 worker 声明，没有独立复跑 | reviewer 按 env_setup 重建环境并记录自己的 mapping。 |
| Worker 用假数据让失败路径返回成功 | 删除运行时兜底并暴露真实错误；`runtime_fallbacks` 只能为空。 |
| Reviewer 发现一个问题就停止 | 完成本 revision 的全部范围扫描，一次提交完整 findings 批次。 |
| Worker 或 Reviewer 复用旧 commit 的证据 | 重新读取当前 PR head；Worker 更新 `delivered_revision` 与 regression `head_ref`，Reviewer 仅评审并填写该同一 revision。 |
| Worker 返工时改用另一个 PR | 继续使用平台解析后的同一个 canonical PR；原 PR 无法继续时报告阻塞，不得替换后绕过已有评审。 |
| 使用 PR 编号、分支名或 `artifacts.pr` | 提交完整 canonical GitHub PR URL，并只写入 `artifacts.pr_url`。 |
| Worker gate 同名结果覆盖失败项 | 每个 contract gate 只提交一次，删除重复与未知 gate。 |
| mapping 用重复或未知 id 凑齐数量 | 每个 contract id 只映射一次，删除重复项和未知项，并使用合法状态。 |
| merge command 只包含 PR URL | 同时加入 `{delivered_revision}`，并让平台在 head 已变化时拒绝合并。 |
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

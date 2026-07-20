# Agent 开发与 Reviewer 完整评审质量门设计

## 状态

已实现，待最终交付。

## 背景

OMAC 已经要求 Worker 使用 TDD、提交结构化 verification，并要求 Reviewer 独立复跑测试、提交 review report。现有硬门能够证明命令执行成功、coverage 达标、acceptance 与 integration gate 已被映射，但仍留下四个真实缺口：

1. 测试可能只为满足覆盖率或当前实现而存在，没有验证真实业务行为。
2. Worker 可能只交付骨架、占位或临时实现，却将节点声明为完成。
3. 生产路径可能在真实依赖失败时返回 fake/mock 数据，掩盖应当暴露的错误。
4. Reviewer 可能发现第一个 blocker 后提前结束，导致 Worker 多轮逐个修复问题。

本设计直接升级现有流程，不提供 legacy schema 或兼容模式。升级后的 verification 和 review report 是唯一合法形状。

## 设计原则

- 只把机器能够客观判断的事实放进 schema。
- 语义质量由独立 Reviewer 判断，不让 Worker 通过自我声明证明自己完整。
- 不创建平行质量系统；扩展现有 contract、verification 和 review report。
- 不预设问题 category、固定审查维度或问题枚举，避免限制 Reviewer 对未知问题的发现能力。
- 测试替身可以隔离测试中的不可控外部依赖，但不能替代关键业务行为验证，也不能进入生产失败路径。
- Validator 应一次返回全部可发现的 schema 问题，避免逐个失败、逐轮修复。

## 目标

- 将每条 contract acceptance 绑定到实际成功执行的具体业务测试。
- 明确禁止骨架、占位、临时实现和“后续补充”式交付。
- 明确禁止生产代码使用合成成功数据掩盖真实错误。
- 要求 Reviewer 完成整个评审范围后，一次性提交全部已发现问题。
- 保持 pipeline 状态机、WorkItemStore 和 AgentRuntime 边界不变。

## 非目标

- 不使用静态正则或语言相关 AST 规则自动判断测试是否有业务价值。
- 不维护问题分类、严重性枚举或固定 Reviewer checklist。
- 不要求 Worker 提交 implementation refs、完整性声明、占位清单或 fallback 自查表。
- 不禁止测试代码中的 fake、mock、stub 或其他 test double。
- 不增加新的流程阶段、平台状态或平台 CLI 直接调用。

## 核心数据设计

### Worker verification

现有普通命令和 integration gate 命令使用相同的命令证据结构。命令可以新增 `business_tests`：

```yaml
commands:
  - cmd: "python3 -m pytest tests/auth"
    exit_code: 0
    summary: "12 passed"
    business_tests:
      - acceptance: "flow-session-refresh"
        test: "tests/auth/test_refresh.py::test_refreshes_expired_session"
```

`business_tests` 是测试索引，不是新的交付树：

- `acceptance` 精确引用当前节点 `contract.acceptance` 中的一项。
- `test` 是本次命令实际执行的具体测试标识，例如 pytest node id、测试文件与用例名、浏览器场景名或仓库现有测试框架采用的等价稳定标识。
- 同一命令可以覆盖多个 acceptance；同一 acceptance 也可以由多个测试覆盖。
- 没有业务测试映射的 supporting command 仍然合法，例如 lint、类型检查或构建命令。
- `integration_gates[].commands[]` 支持完全相同的 `business_tests` 结构。

不新增独立 `quality_evidence`。contract、命令和实现清单不在 verification 中重复保存。

### Reviewer report

保留现有字段：

- `review_goals`
- `diff_reviewed`
- `tests_rerun`
- `integration_tests_rerun`
- `coverage_checked`
- `acceptance_mapping`
- `integration_gate_mapping`
- `blockers`
- `nits`

只新增一个必填字段：

```yaml
full_review_completed: true
```

该字段表示 Reviewer 已经完成当前任务事实所要求的完整评审，而不是只检查首个失败点。它是正式的 Reviewer 声明，与现有 `diff_reviewed`、`tests_rerun` 等证据标志处于同一层级。

`blockers` 和 `nits` 继续使用自由文本列表，不增加 category、ID 或固定枚举。Reviewer 协议要求每个 blocker 用简洁自然语言说明：

1. 已确认的事实或证据；
2. 对需求、用户或系统的影响；
3. Worker 可以执行的修复方向。

自然语言格式由 Reviewer guide 约束，不在 schema 中制造脆弱的文本结构。

## Validator 规则

### Worker evidence validator

Validator 遍历：

- `verification.commands[]`
- `verification.integration_gates[].commands[]`

并收集所有 `business_tests`。校验规则如下：

1. `business_tests` 出现时必须是列表。
2. 每项必须是 object，且 `acceptance`、`test` 都是非空字符串。
3. `acceptance` 必须精确存在于当前 `contract.acceptance`。
4. 承载该映射的命令必须具有非空 `cmd`，且 `exit_code` 必须是严格的整数 `0`；布尔值、浮点值和缺失值均不合法。
5. 当前 `contract.acceptance` 的每一项至少被一个合法业务测试覆盖。
6. 重复映射合法，不增加去重或数量门槛。
7. 所有错误一次性收集并返回，不在第一个缺失 acceptance 或非法映射处提前返回。

Validator 不尝试判断测试代码是否真实、是否只断言 mock 调用，或测试标识是否对应某种特定语言路径。这些属于 Reviewer 的语义判断。

### Reviewer evidence validator

在现有规则上增加：

1. `full_review_completed` 必须严格为 `true`。
2. reject 仍要求 `blockers` 非空。
3. pass 和 pass-with-nits 仍要求 `blockers` 为空。
4. 原有 diff、测试、coverage、acceptance 与 integration gate 规则保持不变。
5. 缺少 acceptance mapping 时不提前返回，继续检查其余字段并一次性返回全部错误。

## Agent 行为设计

### Worker 与执行型 Agent

更新共享 instructions、backend-eng、frontend-eng、data-rd，以及运行时 worker role guide。规则统一表达为：

- 测试必须证明用户可观察结果、业务状态变化、对外 contract 或明确失败语义。
- 只验证 mock 调用次数、函数存在、固定返回值、覆盖率数字或当前内部实现形状的测试，不能单独作为业务功能测试。
- 新行为必须先观察测试因缺少目标行为而失败，再实现并观察其通过。
- 节点交付必须完整落实 objective、source of truth 和全部 acceptance。
- 禁止以骨架、TODO、占位分支、临时返回值、未接线组件或“后续节点补齐”声明当前节点完成。
- 测试替身只允许位于测试边界，用于隔离不可控依赖；关键业务结果仍必须通过真实领域逻辑或相应集成边界验证。
- 生产依赖失败必须暴露真实错误，或执行设计和 contract 明确规定的降级语义；禁止生成 fake/mock 成功数据让流程表面通过。
- 无法满足这些条件时应暴露失败或 blocked，不得伪造可执行结果。

### Planner 与 Orchestrator

更新 planner/orchestrator role guides 中的“骨架”和 mock/fake 表述：

- Wave 0 可以包含共享合同、迁移、测试基础设施、CI gate 等真实前置能力。
- Wave 0 节点本身必须是完整、可独立验证、能够被后续节点直接消费的基础能力。
- 禁止创建仅有目录、接口空壳、固定返回值或假数据兜底的“骨架节点”。
- 如果某个节点必须依靠后续补丁才具备其 contract 声称的价值，它不是合法的独立交付节点，应重新划分 contract。
- planner 可以设计测试替身边界，但不能把 test double 当成生产降级方案或关键集成验收的替代品。

### Reviewer Agent 模板

更新 `agents/reviewer/instructions.md` 及英文镜像，形成长期审查原则：

- 第一处 blocker 只是记录点，不是停止点。
- 必须继续检查完整 diff、相关实现、测试、配置、迁移和必要文档。
- 检查需求与设计是否全部落实，而不是只验证已实现部分是否自洽。
- 检查业务测试是否验证真实行为，而不是为覆盖率、当前实现或 mock 调用服务。
- 检查是否存在骨架、占位、临时实现、未接线能力或遗漏需求。
- 检查生产失败路径是否暴露真实错误，是否存在 fake/mock 数据掩盖失败。
- 一次 report 提交本轮能够发现的全部 blockers 和 nits。
- 不使用固定问题 category 或机械 checklist 限制审查范围；根据实际 diff 和风险自适应扩展审查。

### Reviewer 运行时 role guide

更新中英文 reviewer role guide 的执行步骤、完成条件、返工路径、禁止事项和错误示例：

- 提交 verdict 前必须完成整个当前评审范围，并设置 `full_review_completed: true`。
- 发现 blocker 后继续审查剩余改动，不能立即提交 reject。
- blockers 必须一次包含本轮全部已发现问题，并说明事实、影响和修复方向。
- 无法访问完整交付物、验证环境或关键范围时，不得声明完整审查或提交 pass。
- 返工时验证旧 blockers 已关闭，同时重新检查完整新 diff、相关测试和可能的新回归。
- 禁止只检查上一轮问题、只看 Worker 摘要，或把部分审查包装成完整审查。

## 错误语义

- Worker verification 缺少业务测试映射时，提交失败并列出所有未覆盖 acceptance。
- business test 引用非法 acceptance、空测试标识或失败命令时，提交失败并逐项列出。
- Reviewer report 缺少 `full_review_completed: true` 时，提交失败并明确要求完成全量审查后重新提交。
- schema 校验不得用默认值自动补齐新字段，也不得让 mock/fake 数据替代缺失证据。
- CLI 继续使用现有 validation exit code `5`，不增加新的退出码。

## Mock engine 边界

OMAC 的 mock engine 是仓库测试基础设施，可以继续模拟平台状态、运行和证据提交。它必须生成符合新 schema 的 verification 和 review report，以验证 OMAC 流程本身。

mock engine 生成的证据只用于 OMAC 自身测试，不能被文档或实现描述成生产业务真实性证明。live/integration 验证仍负责真实平台边界。

## 文档更新

同步更新中英文：

- worker role guide
- reviewer role guide
- planner role guide
- orchestrator role guide
- evidence artifact guide
- manifest artifact guide 中与节点完整性相关的说明
- workflow 中 Worker/Reviewer 的完成语义

Agent 模板与运行时 guide 必须使用一致术语，避免一层允许骨架或 fake fallback，另一层禁止。

## 测试策略

所有可执行行为按 TDD 实现。

### Worker evidence 单元测试

- 缺少全部 business test 映射时，列出所有未覆盖 acceptance。
- 普通命令能够覆盖 acceptance。
- integration gate 命令能够覆盖 acceptance。
- 同一 acceptance 被多个测试覆盖时合法。
- business test 引用未知 acceptance 时失败。
- `business_tests` 不是列表、entry 不是 object、字段为空时分别失败。
- 映射位于失败命令下时失败。
- supporting command 没有 `business_tests` 时合法。
- 多个错误同时存在时一次全部返回。

### Reviewer evidence 单元测试

- 缺少或错误设置 `full_review_completed` 时失败。
- `full_review_completed: true` 与合法 pass/reject report 组合通过。
- acceptance mapping 缺失时仍继续返回其他 report 错误。
- 原有 verdict、blockers、integration gate 和 coverage 规则不回退。

### Mock engine 与 CLI 集成测试

- mock worker 自动交付生成合法 business test 映射。
- mock reviewer 自动交付包含 `full_review_completed: true`。
- `omac work submit` 对旧 verification/report 直接返回 validation failure。
- 合法的新 schema 能继续推进现有 develop、review、CI、merge 状态流。

### 模板与文档测试

- 中英文 Agent 模板包含新的完整实现、真实业务测试和禁止生产 fake fallback 规则。
- 中英文 Reviewer 模板和 role guide 包含“发现 blocker 后继续完整审查”和“一次报告全部问题”。
- guides 的示例全部符合新 schema。
- 禁止重新引入“骨架节点可作为功能完成”或“用 mock/fake 让流程通过”的表述。

### 完整验证

实现完成后必须运行：

```bash
python3 -m pytest tests/
```

并运行 `git diff --check`。文档、示例、mock engine 和所有 fixture 必须与唯一的新 schema 同步，不能保留会误导 Agent 的旧合法示例。

## 影响范围

预计修改集中在：

- `src/omac/core/evidence.py`
- `src/omac/engines/mock.py`
- `src/omac/agents/_shared/instructions*.md`
- `src/omac/agents/backend-eng/instructions*.md`
- `src/omac/agents/frontend-eng/instructions*.md`
- `src/omac/agents/data-rd/instructions*.md`
- `src/omac/agents/reviewer/instructions*.md`
- `src/omac/guide/roles/{worker,reviewer,planner,orchestrator}.md`
- 对应英文 role guides
- `src/omac/guide/artifacts/{evidence,manifest}.md`
- 对应英文 artifact guides
- `src/omac/guide/workflow.md` 及英文镜像
- evidence、mock engine、CLI、guide 和 template 相关测试及 fixture

pipeline 和 CLI 仍只调用 `WorkItemStore` 与 `AgentRuntime`，不会直接执行 `multica`、`gh` 或其他平台 CLI。

## 完成判据

- 每条 contract acceptance 必须映射到成功执行的具体业务测试。
- Reviewer report 必须声明完整评审已完成。
- Worker、Reviewer、Planner、Orchestrator 的中英文模板和 guides 对完整交付、真实业务测试与 fake fallback 边界表达一致。
- Validator 一次返回全部可发现错误。
- mock engine、所有示例和 fixture 使用新 schema。
- 完整测试集通过，且不存在旧 schema 被接受的回归路径。

# Kernel Contract Schema v1

本文是平台中立的 Kernel 公共线协议 Schema Reference。它负责固定 `oac.kernel-contract/v1` 的机器可读 profile、`InvocationRequest` 有界信封、规范化与摘要规则，以及 Kernel 公共类型之间的证据/派发关系；它不定义 OAC 的数据库、Kubernetes 写入、Agent/Runtime 解析、开发审批流程或外部编排平台协议。Planning contract additions are published under the explicit source release `oac.kernel-planning-extension/2026-08-14`; they do not broaden the governed v1 wire root set.

机器权威文件为 [`contracts/spec/kernel/kernel-contract-v1.json`](../../../contracts/spec/kernel/kernel-contract-v1.json)。该文件的 RFC 8785 JCS UTF-8 规范化结果由 `source_release` 与 profile 一起绑定；当前 `oac.kernel-planning-extension/2026-08-14` release 的规范化长度为 **3374 bytes**，SHA-256 为 **`e70b4e444ce38b4872ce59bd24d2f7c053b3883e8d362106f3690102eb3a9461`**；任何值、字段、顺序语义或约束调整都必须使用新的 Kernel contract version，不能原地修改 v1。Planning extension source changes must update the release-bound source identities and every compatibility consumer in the same change.

## 1. 边界与接入对象

上游是 Kernel Runtime/Host 依据不可变 Workflow、WorkUnit、输入、Observation 和 Decision 快照组装的公共 DTO；下游是 `DispatchExecution`、AgentWorkChannel/`oacok work show`、独立 Executor Hook，以及保存同一信封的产品 Run。Schema 不读取存储，不解析 `latest`，也不携带 AgentDefinition、Runtime、Session、Workspace、Secret 或开发控制平台身份。

一次派发的主路径固定为：

1. 调用方提交唯一受支持的 `kernel_contract_version=oac.kernel-contract/v1`；其他版本在创建 Run 之前拒绝。
2. Assembler 组装完整、闭合的 `InvocationRequest`；所有大内容保留在既有不可变存储中，请求仅携带有业务类型的身份、摘要、媒体类型和受限说明。
3. Validator 递归拒绝未知字段、重复对象名和未被 Schema 明确允许的 `null`，并检查每个字段与数量上限。
4. 对省略 `request_digest` 的完整请求执行 RFC 8785 JCS，取 UTF-8 bytes 的 SHA-256 小写十六进制值。
5. 将摘要写回后再次执行 RFC 8785 JCS，并独立检查最终信封不超过 65536 bytes。
6. `DispatchExecution` 嵌入完整不可变请求；禁止以可变或 detached request reference 替代。

失败路径只有一个：任一解析、闭合性、版本、上限、摘要或最终大小检查失败，整个请求拒绝，不创建部分 Run，不截断，也不回退到宽松解析。

## 2. 受治理根类型

v1 固定治理 13 个 Kernel 公共根类型：

| 根类型 | 运行时语义 |
| --- | --- |
| `InvocationRequest` | 一次主执行或独立责任执行的完整不可变控制信封。 |
| `InputResource` | 可按精确 `resource_key` 和 `content_digest` 读取的输入身份及显示说明。 |
| `ContractReference` | 工作或结果合同的稳定键与内容摘要。 |
| `ResponsibilityRequirement` | 当前执行承担的通用责任标签，不是产品角色枚举。 |
| `BindingReference` | Guide/Sensor 等绑定的身份、内容摘要与配置摘要。 |
| `Guide` | 有序的 Computational 与 Inferential Guide 绑定集合。 |
| `Conclusion` | 同一任务线可交接的结构化执行结论。 |
| `SubmissionContract` | 结果合同、必需输出、Conclusion/Question 许可和提交通道。 |
| `Observation` | 对精确 subject 的不可变结构化事实。 |
| `Decision` | 对精确 subject 的不可变正式决定。 |
| `WorkSubmission` | 与 invocation identity 和 request digest 绑定的统一执行提交。 |
| `Question` | Executor 提交的结构化阻塞问题。 |
| `GateResult` | Required Gate 的闭合判定结果。 |

字段名、JSON tag、字段类型与 `omitempty` 行为以 `contracts/kernel/contracts.go` 的已发布公共 wire contract 为兼容基线；v1 governed roots remain closed, while planning-only additions are bound to the named extension source release. `omitempty` 只表示 Go JSON marshaling 时可省略零值，不能替代 Kernel `Validate()` 的语义必需性。例如 `Display.description` 带 `omitempty`，但 `Display.Validate()` 仍要求非空。验证器因此明确分离三层：递归 closed wire shape、Kernel `Validate()` 语义、以及本 profile 额外规定的 InvocationRequest 必需字段/摘要/大小门槛。

## 3. InvocationRequest 闭合字段集

`InvocationRequest` 是 closed object。除下表字段外出现任何成员都必须拒绝。

| 字段 | 必需 | 类型 | 中文释义与执行语义 |
| --- | --- | --- | --- |
| `invocation_id` | 是 | string | 本次不可变调用身份。 |
| `request_digest` | 是 | 64 位小写 hex | 省略本字段后的完整规范化请求摘要。 |
| `workflow_key` | 是 | `WorkflowKey` | 宿主无关的 Workflow 定位键。 |
| `work_unit_id` | 是 | `WorkUnitID` | 当前 WorkUnit 身份。 |
| `iteration` | 是 | integer | 当前业务执行/返工轮次。 |
| `executor_key` | 是 | `ExecutorKey` | Kernel 已匹配的逻辑执行器。 |
| `responsibility` | 是 | `ResponsibilityRequirement` | 当前执行责任。 |
| `objective` | 是 | string | 当前任务目标，UTF-8 最多 2048 bytes。 |
| `contract` | 是 | `ContractReference` | 当前 WorkUnit 工作合同。 |
| `guide` | 是 | `Guide` | 当前节点固定 Guide；对象本身闭合。 |
| `submission_contract` | 是 | `SubmissionContract` | 当前结果提交合同。 |
| `inputs` | 否 | `InputResource[]` | 最多 16 个精确输入资源。 |
| `prior_conclusions` | 否 | `Conclusion[]` | 最多 8 个同任务线前序结论。 |
| `feedback` | 否 | `Observation[]` | 最多 16 个获准用于本轮的精确反馈。 |
| `allowed_actions` | 否 | `string[]` | 最多 32 个经 Contract、Harness 与 Policy 求交后的动作。 |

`guide` 只允许可选字段 `computational` 与 `inferential`，两者分别最多 8 项。缺省表示该类 Guide 未提供；显式 `null` 不等于缺省，并在 v1 中拒绝。

## 4. 固定限制

v1 的十二个限制不可配置、不可调高，也不能静默截断：

| 名称 | 固定值 | 适用对象 |
| --- | ---: | --- |
| `max_canonical_request_bytes` | 65536 | 包含 `request_digest` 的最终 JCS UTF-8 请求。 |
| `max_objective_bytes` | 2048 | `objective` UTF-8 bytes。 |
| `max_display_name_bytes` | 128 | 通用显示名称 UTF-8 bytes。 |
| `max_display_description_bytes` | 1024 | 通用显示说明 UTF-8 bytes。 |
| `max_inputs` | 16 | `inputs` 元素数。 |
| `max_input_display_name_bytes` | 128 | 输入资源显示名称 UTF-8 bytes。 |
| `max_input_display_description_bytes` | 512 | 输入资源显示说明 UTF-8 bytes。 |
| `max_computational_guides` | 8 | Computational Guide 数量。 |
| `max_inferential_guides` | 8 | Inferential Guide 数量。 |
| `max_prior_conclusions` | 8 | 前序 Conclusion 数量。 |
| `max_feedback` | 16 | Feedback Observation 数量。 |
| `max_allowed_actions` | 32 | 允许动作数量。 |

最终 65536-byte gate 是所有字段级和数量级检查通过后的独立硬门槛；字段均未超限不代表最终请求必然合格。

## 5. 规范化与 request_digest

规范化算法固定为 **RFC 8785 JCS**，编码固定为 **UTF-8**：

- object member 完全按 RFC 8785 排序和序列化；
- array 保留声明顺序；
- string 保留原 Unicode 内容，不执行 Unicode normalization；
- 在规范化前拒绝重复 object name；
- closed schema 未明确允许的 `null` 一律拒绝；省略与 `null` 不同。

`request_digest` 的 preimage 是完整 closed `InvocationRequest` 删除且仅删除顶层 `request_digest` 后的 JCS UTF-8 bytes。所有被引用内容的 digest 必须留在原字段和原数组位置。摘要算法固定为 SHA-256，wire 格式固定为 64 个小写十六进制字符，不带算法前缀。

伪代码：

```text
parsed = parse_json_rejecting_duplicate_names(input)
validate_closed_schema_recursively(parsed)
validate_field_and_count_limits(parsed)
preimage = jcs_utf8(remove_top_level_member(parsed, "request_digest"))
require parsed.request_digest == lowercase_hex(sha256(preimage))
require len(jcs_utf8(parsed)) <= 65536
```

## 6. 七个证据与派发关系

1. **Question submission persists as Observation**：`WorkSubmission.questions[]` 中的问题先按 closed `Question` schema 校验，再持久化为不可变 Question Observation。
2. **Answer Decision requires Question Observation schema**：Answer Decision 只接受已持久化且 structured payload 符合 closed Question schema 的 Question Observation。
3. **Answer Decision binds exact Question subject**：Decision 必须绑定该 Question Observation 的精确 subject identity 与 digest；禁止 latest 或无版本查询。
4. **GateResult closed outcomes**：结果集合仅为 `Pass`、`AwaitDecision`、`Rework`、`Reject`、`Error`，未知值拒绝。
5. **DispatchExecution embeds InvocationRequest**：执行 Action 携带完整不可变请求，不接受 detached mutable reference。
6. **request_digest covers envelope and content digests**：摘要覆盖完整请求信封，并保留每个 content digest 的字段与数组位置。
7. **unknown version rejected**：除 `oac.kernel-contract/v1` 外的 `kernel_contract_version` 在 Run 创建前拒绝。

Answer 不是独立 DTO；它是绑定 Question Observation 的正式 `Decision`。Question、Observation、Decision 与 GateResult 的持久化和授权属于宿主，Kernel Contract 只固定平台中立 wire 语义。

## 7. 验证与兼容性证明

- `scripts/contracts/validate_kernel_contract_schema.py` 校验 profile 的精确 JCS bytes/digest、固定限制、根类型、闭合字段集、版本/重复名/null/未知字段规则，以及 Reference 和 Kernel 公共源的一致性；其可复用函数分别执行 wire shape、Kernel semantic requiredness、request digest 与最终大小验证。
- 同一脚本拥有共享 deterministic fixture corpus：覆盖全部 13 个 governed root types，并通过 `Display`、`SubjectKey`、`Output` 等可达 closed object。每个 fixture 固定合法或非法预期，避免 Python 与 Go 各自维护一套样例。
- `scripts/contracts/verify_kernel_v1_compatibility.py` 先独立要求 profile 的 RFC 8785 JCS UTF-8 结果与 `source_release` 绑定，再要求命令传入的 `contracts.go` 与 `validation.go` SHA-256 分别匹配该 release 的已完成公共源身份；随后把这两个原始 bytes 写入隔离临时 Go module，严格拒绝 unknown field/null，编译并调用 completed Kernel 的真实 `Validate()` 方法，再把同一 fixture corpus 交给 Python schema/semantic validator。profile 或源摘要漂移、两端判定不一致、Go 编译/执行失败、validation source 为空或 receiver 不完整时均非零退出。未定义 `Validate()` 的 governed types 仍执行严格 Go decode，并由 closed schema 约束其额外语义。
- 兼容性命令必须实际执行且通过七个命名 case：`invocation-fields`、`invocation-tags`、`invocation-types`、`question-observation`、`answer-decision`、`gate-result`、`unknown-version`。零 case、部分 case、未知 case 或任一 case 失败时命令非零退出。
- `compatibility.json` 必须记录 `contracts.go` 与 `validation.go` 的 SHA-256、fixture corpus digest、全部 root/closed type coverage、Go decode/Validate 非零执行计数，以及 `validation_lineage_verified=true`。这些字段只在真实 Go harness 与 Python/Go parity 全部通过后生成。

该兼容检查是对已发布 Kernel 公共 wire 与 validation source 的只读、内容绑定证明，不替代 Kernel 自身 Go test，也不允许为满足 Schema 修改 `contracts/kernel/**`。临时 Go module 在命令结束后删除，不产生产品 artifact 或第五个权威源文件。

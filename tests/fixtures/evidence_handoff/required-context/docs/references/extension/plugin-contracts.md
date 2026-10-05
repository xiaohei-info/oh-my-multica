# Harness Extension SDK 契约参考

共同记法见 [Reference 共同约定](../conventions.md)，Package 生命周期见 [Package 与 Extension 治理参考](component-governance.md)，领域边界见 [Package 与 Extension 详细设计](../../design/detailed/08-component-and-extension-detailed-design.md)。

术语边界：`HarnessDefinition` 是不可执行的精确装配清单；Harness Extension 是实现 `harness.oac.dev/v1` 的 ExtensionPackage。Solution 引用 HarnessDefinition，Extension 提供能力，Harness Host 按 LockedComponentSet 解析并调用。任何一方都不能自行注册生命周期动作或直接推进 Gate。

## 1. ExtensionPackage 与 Describe

第三方只需要发布一个 `package_type=extension`、`interface_api=harness.oac.dev/v1` 的 ExtensionPackage。Package Envelope 提供 ID、版本、来源和入口；验证后的 ComponentInstall 保存生效 trust tier 与 lifecycle policy。扩展能力通过 SDK 的 `Describe()` 返回，不再维护一份与接口重复的第二 Descriptor。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| interface_api | 是 | Harness Extension API 版本 | 固定为平台支持的 `harness.oac.dev/v1` 等精确接口版本 |
| entrypoint | 是 | ExtensionPackage 启动入口 | 由 ExtensionManager 安全启动，不允许路径逃逸 |
| execution_mode | 是 | service 或受信任 in-process | 默认 service；in-process 只允许管理员批准的受信任实现 |
| extensions[] | 是 | `Describe()` 返回的 Extension 列表 | ID 在当前 Package 内唯一；平台不接受只写 Manifest 但运行时不提供的能力 |

ExtensionPackage 不描述业务状态机、依赖求解、隐式优先级、Marketplace 信息和 Gate bypass。

## 2. Extension

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| extension_id | 是 | Package 内稳定身份 | 同一 ExtensionPackage 内唯一 |
| display | 是 | Extension 本地化名称与能力说明 | 编辑页、详情页和运行追踪复用同一说明；不参与解析身份 |
| extension_point | 是 | 固定扩展点 | 平台不支持时拒绝安装 |
| effect_mode | 是 | `pure`、`read-only` 或 `side-effecting` | 用于权限、恢复和用户提示；Guide/Sensor 不得声明 `side-effecting` |
| permissions[] | 是 | 当前 Extension 所需最小权限 | 其他 Extension 不自动继承 |
| input_schema | 视类型 | SDK 返回的输入 Schema | 不能接收 Repository 或框架对象 |
| output_schema | 视类型 | SDK 返回的输出 Schema | 输出先持久化再进入 Kernel |

允许的普通 Extension Point：

- harness.guide.computational；
- harness.guide.inferential；
- executor；
- harness.sensor.computational；
- harness.sensor.inferential；
- trigger；
- event-handler。

Deployment SDK 不在本列表中。实现 `deployment.oac.dev/v1` 的 DeploymentDriver 用于已完成 Delivery 上的单次外部 Kubernetes/CI-CD Deployment；它不进入 HarnessDefinition、Harness Host、WorkUnit execution 或 ComponentRun。企业多阶段发布必须显式建模为 Workflow/DAG，不能把流程隐藏在一个 DeploymentDriver 中。

只有管理员安装的受信任 in-process 模块可以注册纯 Gate Evaluator。Service Extension 不能通过 RPC 提供 Gate Evaluator。每个 GateEvaluator 注册项同样必须提供本地化 `display.name` / `display.description`、精确 Package release 与 digest，供统一 Harness 行展示；其副作用语义固定为 pure。

平台根据 `extension_point` 固定它在用户可见执行生命周期中的位置，Extension 不声明额外 phase：

| Extension Point / Binding | 中文生命周期位置 | 调用语义 |
| --- | --- | --- |
| `harness.guide.computational` | 执行准备 · 程序约束 | 在主执行前装配 Schema、Policy、Capability 和允许动作 |
| `harness.guide.inferential` | 执行准备 · 推理上下文 | 在主执行前装配当前阶段 Instructions、显式方法资源、InputResource、Review 和 Decision；不复制执行器自身的稳定 Instructions/Skills |
| `executor` | 主执行 | 提供一个可被 WorkUnit `execution.kind=executor` 匹配的 Extension-backed Executor；必要时通过受限 `agent.invoke` 创建一个 AgentRun，但不进入 HarnessDefinition |
| `child-workflow` ExecutionBinding | 主执行 | 由平台 `InvokeChildWorkflow` Action 执行，不绑定 Extension Executor，也不进入 HarnessDefinition |
| `harness.sensor.computational` | 观察与评估 · 程序验证 | 主执行后产生测试、Build、扫描、digest、探针和 Usage Observation |
| `harness.sensor.inferential` | 观察与评估 · 推理分析 | 主执行后由 Extension 产生影响分析、规则化推理或外部评审结果 Observation；Extension 不能调用 Agent |
| `responsibility-run` Inferential Sensor Binding | 观察与评估 · 独立责任评审 | 由 Controller/Host 显式创建 Reviewer 等独立判断 Run request；不属于 Extension Point；多步骤 E2E Acceptance 使用普通 WorkUnit 主执行 |
| trusted GateEvaluator Binding | Gate 判定 | 对已持久化事实做纯确定性判断；全部 Required Evaluator 通过才可推进 |
| `trigger` | Workflow / 外部事件入口 | 发生在 WorkUnit 生命周期之前，不插入某个 WorkUnit 的 Guide/execution/Sensor/Gate 列表 |
| `event-handler` | 持久化事件后的外围响应 | 响应已持久化 Event，不成为 WorkUnit 生命周期步骤，也不能直接推进 Gate |

`effect_mode=side-effecting` 的 Executor 或 EventHandler 必须通过 Harness SDK 接口检查证明幂等与恢复语义；副作用 Executor 仍必须支持 `execute`、`observe` 和 `cancel`。阶段名称、顺序和这些恢复要求均由平台合同决定，不能由 Extension 配置覆盖。

## 3. Scoped Core Services

Harness Host 只按当前 Extension、Component Run 和 subject 暴露最小 Core Services。

可以声明：

- workflow.read；
- artifact.read；
- artifact.write；
- event.read；
- observation.submit；
- agent.invoke；
- workspace.use；
- credential.use；
- decision.request。

`agent.invoke` 只允许当前 WorkUnit `execution` 已由 Kernel 匹配的 Extension-backed `executor` 声明。请求必须绑定当前 Workflow、WorkUnit、iteration、ComponentRun 和 subject；Platform Core 只为该显式内部调用执行作用域受限的 Agent Selection、Authorization 和 Execution Admission，并为一个 ComponentRun invocation 最多创建一个 AgentRun。该内部选择不得替换 WorkUnit 的 `executor_key` 或隐藏多 Agent 协调。其他 Extension Point 不能调用；独立 Reviewer 由 HarnessDefinition 的 `responsibility-run` Binding 和 Kernel `StartReview` 显式派发，不经过 Sensor Extension；E2E 或独立业务验收必须建模为普通 WorkUnit，其他多 Agent 工作必须拆成多个 WorkUnit 或 child Workflow。

不存在：

- workflow.write；
- work_unit.write；
- agent_run.write；
- gate.pass；
- lifecycle_action.emit；
- database.access；
- crd.write；
- secret.list。

有效权限是 `Describe()` 请求、Extension Point 上限、Trust Tier、平台策略、Project 策略、Solution 策略、Workflow 固定 Package 集合和当前调用范围的交集。

## 4. Harness Extension Binding

HarnessDefinition 使用显式有序列表固定 Extension。每个 `HarnessExtensionBinding` 包含 Binding ID、实现 `harness.oac.dev/v1` 的精确 ExtensionPackage、Extension ID、config digest，以及必要的配置 Artifact ID。

相同 HarnessDefinition 来源、`harness_definition_id + content_digest + harness_binding_id` 必须解析为相同装配语义；Binding 内固定的 ExtensionPackage SemVer、Extension ID、artifact digest 和 config digest 均参与规范化摘要。配置值变化必须形成新的 config digest；Extension 实现变化必须发布新的 ExtensionPackage release，package-local Harness 内容变化必须发布新的 Solution release，不能按 Project 隐式切换。

| 位置 | 数量 | 组合规则 |
| --- | ---: | --- |
| computational_guide | 0..N | 要求取并集，允许动作取交集，冲突时解析失败 |
| inferential_guide | 0..N | 只追加精确不可变上下文，按 digest 去重 |
| computational_sensor | 0..N | 可以并行，只追加 Observation |
| inferential_sensor | 0..N | 可以是 Extension Sensor Binding 或宿主派发的 `responsibility-run` Binding；都只能追加 Observation，不能直接推进状态 |
| gate_evaluators | 1..N | 所有 Required Evaluator 均通过才可推进 |

WorkUnit 的主执行不属于 HarnessExtensionBinding：`execution.kind=executor` 由 Kernel Runtime 匹配 ExecutorDescriptor，`execution.kind=child-workflow` 由平台固定的子 Workflow Action 执行。

`HarnessExtensionBinding` 只覆盖 Harness Extension。`responsibility-run` 不是 Extension Binding，不保存 ExtensionPackage ID、Extension ID 或 config digest；它保存责任要求、Guide 和输出合同，由 WorkflowOrchestrationHost 映射为独立 Run request。两者可以出现在同一 Harness 生命周期视图中，但平台必须明确显示其来源类型，不能把 Reviewer Agent 伪装成 Sensor Extension，也不能让 Sensor Extension 间接调用 Agent。

Solution Studio、Harness 详情和 Workflow 运行追踪使用同一组信息与同一生命周期顺序：

| 英文字段或来源 | 中文字段释义 | 值的来源与作用 |
| --- | --- | --- |
| `phase` | 生命周期阶段 | Harness 行根据 Binding 类型或 `extension_point` 推导；主执行行根据 WorkUnit `execution` 推导；不是持久化字段，也不允许用户填写 |
| `extension_point` | 技术扩展点 | 来自精确 ExtensionPackage 的 SDK `Describe()`，用于说明能力接入合同 |
| `display.name` / `display.description` | 能力名称与说明 | 来自 Extension display，解释该能力做什么 |
| `extension_package` | ExtensionPackage 精确身份 | 显示名称、SemVer 和 artifact digest；编辑时选择，详情与运行时只读 |
| `order` | 同阶段执行顺序 | 来自 HarnessDefinition 对应有序数组的位置，不再保存第二个顺序字段 |
| `required` | 是否必需 | 来自 Binding；Required 失败阻塞，Optional 跳过产生 Observation |
| `input` / `output` | 输入与输出说明 | 来自输入/输出 Schema、配置 Artifact 和 Contract 的标题与描述 |
| `permissions[]` | 最小权限 | 来自 SDK `Describe()`，并显示最终权限求交结果 |
| `effect_mode` | 副作用模式 | 来自 SDK `Describe()`，用于解释纯计算、只读或外部写入 |
| `failure_and_recovery` | 失败与恢复方式 | 根据 Required/Optional、effect mode、Executor `observe/cancel` 能力和 Harness rework/convergence policy 组装说明 |

编辑页允许在这些固定位置选择精确 Extension、调整同阶段数组顺序和编辑配置；详情页以同一字段、同一分组只读展示；Workflow 运行追踪仍复用同一行结构，只附加 ComponentRun、AgentRun、human Run、Observation 和 GateResult 状态。平台不创建独立预览对象、另一套详情 Schema 或第二份展示数据模型。

不允许：

- latest；
- 隐式 priority；
- 静默替换；
- 后写覆盖前写；
- Extension 自行生成生命周期动作。

## 5. Component Run

只有需要独立幂等、来源、外部结果、错误或恢复语义的调用才创建 Component Run。纯 Catalog 查询、Renderer 和子 Workflow 创建不创建伪调用记录。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| component_run_id | 是 | 稳定调用身份 | Retry 不修改原记录 |
| extension_package | 是 | 精确 ExtensionPackage，包含 Package 类型、ID、SemVer 和 artifact digest | 不自动切换 package release |
| extension_id | 视 Harness Extension | 具体 Extension 身份 | 与固定 Binding 一致 |
| harness_binding_id | 是 | 当前解析的 Harness Extension Binding 身份 | 调用期间不可变 |
| subject | 是 | 当前处理对象的 `SubjectKey` | 旧主体 ID/digest 的结果不能进入当前 Gate |
| idempotency_key | 是 | 逻辑副作用身份 | 不使用当前时间单独生成 |
| status | 是 | Pending、Running、Succeeded、Failed、Unknown、Cancelling、Cancelled | 一个枚举一种含义 |
| external_operation | 否 | 外部操作 tagged union，包含系统类型、external_id 和可选 URI | 用于 Observe 和恢复 |
| result | 否 | 结构化结果 tagged union，显式区分 Artifact、Observation、Report 和外部结果 | 先持久化后消费 |
| error | 否 | 结构化错误 | 说明是否可重试和结果是否已知 |

副作用 Executor 必须支持 execute、observe 和 cancel。结果 Unknown 时必须先 Observe，不能自动重复创建 PR、Merge、Release 或 Deployment。

Unknown 恢复合同固定如下：平台按有界退避自动触发 `observe`，用户可以请求立即 Observe；每次结果作为不可变 Observation/ComponentRun report 追加，原 ComponentRun 不被覆盖。只有 Observe 明确证明副作用未发生，或 Harness SDK 接口检查已证明相同逻辑副作用身份重复调用安全时，才允许创建新的 Retry ComponentRun。部分完成必须返回可恢复外部事实，不能伪装成“未发生”。Cancel 后继续 Observe，直到确认 Cancelled、Succeeded、Failed 或仍 Unknown。观察期限耗尽只产生 Waiting/人工升级，不自动 Retry 或推断 Failed。

Harness SDK 接口检查必须覆盖：执行响应丢失但外部成功、外部未创建、部分创建、Observe 仍 Unknown、Cancel 结果 Unknown、重复 Observe 幂等、以及安全/不安全 Retry 两类 Fixture。测试场景由 SDK 固定，ExtensionPackage 不能通过 Manifest 选择或关闭。

ComponentRun 结果由 Harness Host 通过 Platform Core 的专用报告合同提交，AgentRun event/completion API 不处理该结果。完整 Artifact、Observation、Report 或 Event 先持久化，Workflow CR 只保存紧凑摘要；Platform Core 不把 ComponentRun 终态解释为 WorkUnit 终态。

## 6. Extension Failure

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| extension_package | 是 | 实现 Harness SDK 的精确 ExtensionPackage，包含 ID、SemVer 和 artifact digest |
| extension_id | 是 | Extension ID；Extension Point 从 SDK `Describe()` 校验 |
| component_run_id | 是 | 当前 ComponentRun 身份 |
| config_digest | 是 | 当前解析配置 |
| subject | 是 | 当前处理对象的 `SubjectKey` |
| error_class | 是 | 配置、超时、授权、外部失败等分类 |
| retryable | 是 | 是否具备重试前提 |
| outcome_known | 是 | 外部副作用是否已知 |
| external_operation | 否 | 已知外部操作的系统类型、external_id 和可选 URI |

只有 Harness 明确声明为 Optional 的 Extension 才能跳过，并追加 ExtensionSkipped Observation。Required Extension 失败必须显式阻塞。

## 7. SDK 测量树与 Host 权限边界

SDK 将执行记录和 `ExecutionReceipt` 统一表示为不可变、仅含数据的递归测量树。每个有序节点绑定精确 Package release/artifact、entrypoint、Extension/binding/config、operation、owner、subject、调用 scope、request/service-payload digest、outcome、实现调用数、provenance/source；Gate 节点还绑定 ComponentRun、policy revision、evaluator registration 与 evaluator ID。节点摘要覆盖有序子节点摘要和父节点 lineage。`ExecutionReceipt` 的既有公开字段及摘要兼容性保持不变，但它只是私有逐节点 SDK seal 的公开投影；修改自身字段、子节点、顺序或嵌套 lineage 都会使校验失败。

数据测量树或 SDK conformance runner 的普通实现结果不构成 Host authority，也不能转换为 `RequestScope`、Gate handoff、binding 或 authoritative receipt。JSON 解码、复制公开 DTO、重算摘要或复制 conformance record 都不会重建私有 seal；每个节点都必须有自身生产签发的 seal，根节点不能替未封存子节点背书。已封存子树可在同一规范父身份下复用，但绑定到另一父身份必须拒绝；nil 与 empty 子列表沿用历史规范化并保持相同摘要。SDK consumer 只消费 Harness Host 附带的 opaque、已验证 handoff，不接受 caller-selected signer/verifier/trust pair。Host producer 必须按包中立 ingress 合同绑定精确 release、artifact、entrypoint、binding/config、operation、owner、subject、Workflow/WorkUnit/iteration/ComponentRun scope、request 与 service-payload digest；Gate 还绑定 policy revision 和 evaluator release/ID。SDK conformance/evidence 必须绑定同一已提交 source revision；安装器仍对精确 Package 执行自己的固定 suite，调用方提交的 Verification Report 本身不构成认证。签发信任、有效期/轮换和 replay/restart 防护由 Harness Host 验证与保管，SDK 不宣称这些 Host 正向保证已经通过。

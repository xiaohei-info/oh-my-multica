# 06. Agent Runtime 详细设计

> 上级文档：[云原生 Open Agent Cluster 概要设计](../open-agent-cluster-overview-design.md)
> 契约参考：[Agent Runtime 契约参考](../../references/execution/agent-runtime.md)
> 物理执行：[Kubernetes-native 控制面详细设计](05-kubernetes-native-control-plane-detailed-design.md)

本文定义可独立复用的 Runtime SDK，以及 Open Agent Cluster 如何由 AgentRun Job 调用 RuntimeDriver、实时消费结构化事件流并通过 Platform Core 持久化和展示。Runtime SDK 与 RuntimeDriver 都是 Job 内代码边界，不是长期运行 Service；Platform API 调用、事件上传、重试、ACK 和产品归属始终留在 Agent Job 与 Platform Core，不能进入 SDK。

在系统三类扩展面中，Agent Runtime SDK 负责隔离不同 Agent Runtime 的会话、消息、思考摘要、Tool、Shell、文件、Memory、Usage、错误和终态差异。第三方实现 `runtime.oac.dev/v1` 后通过 ExtensionPackage 分发 RuntimeDriver；“Runtime SDK 扩展面”描述的是稳定接口边界，不是第四种 Package，也不是一个常驻 Runtime Service。

![Runtime Session, Turn, and Event Lifecycle](../../assets/diagrams/platform/runtime-session-lifecycle.svg)


## 1. 领域边界

Runtime SDK 只拥有：

- Runtime；
- RuntimeRequest；
- RuntimeSession；
- Turn；
- Capability；
- RuntimeInstructionSet（运行时指令集合启动 DTO）；
- PermissionRequest；
- RuntimeEvent；
- RuntimeResult；
- Process Lifecycle。

Runtime SDK 不包含：

- Project、Workflow、WorkUnit；
- AgentRun；
- Artifact、Observation、Gate 和 Decision；
- TeamBindingSnapshot；
- 产品授权；
- Kubernetes、数据库和 CRD。

产品侧不再建立 `Runtime Bridge` 或 `Runtime Runner` 架构组件。Platform Core 的 AgentRun 用例负责固定执行事实并接收结构化事件/结果；Execution Host 只负责物理 Job、Workspace、Secret 和基础设施观察；AgentRun Job 主程序把已挂载的启动数据映射为 SDK `RuntimeRequest`，调用 RuntimeDriver 一次并循环消费 `RuntimeSession.Events`。该主程序只是 Job 入口代码，不是独立 Service、Host、领域对象或可安装扩展。

## 2. Agent 物化

Agent 物化是 Sparse Engineering 的执行边界：逻辑 Agent、AgentDefinition 和 TeamBindingSnapshot 可以长期存在，但只有当前 WorkUnit 已 Ready、Kernel 已匹配唯一 `executor_key`、治理与资源检查通过时，系统才创建一个有界 AgentRun。等待、阻塞、人工 Decision 或外部状态观察期间不维持模型会话；返工、复审或后继责任需要新的认知执行时，再按显式 Lineage/Session 规则物化下一次 Run。

当 Kernel 产生 DispatchExecution LifecycleAction 时：

1. Kernel Runtime 根据 WorkUnit `execution` 和 HostCapabilitiesSnapshot 匹配逻辑 `executor_key`，解析 typed input bindings，并组装不可变 InvocationRequest；
2. Workflow Controller 根据 Kernel 结果确认 WorkUnit Ready，并提交携带同一 `executor_key + invocation_id + request_digest` 的稳定 Run request；
3. Platform Core 将逻辑 Executor 映射为 Workflow 初始 TeamBindingSnapshot 或有效 Workflow-local Executor Decision 中的精确 AgentDefinition，并一次性选择 RuntimeBinding、ResolvedModelSelection、MCP Server 配置和 ResourceRequirements；
4. Authorization 与 Execution Admission 检查资源、安全和策略，把完整的有界 InvocationRequest 控制信封原样固定到 `AgentRun.request`，并根据当前精确 AgentDefinition、Kernel 协议、Workflow 快照和 RuntimeBinding 形成非敏感启动数据；
5. Execution Host 通过 Workspace Adapter 解析 Workspace、Skill/输入挂载和临时 Credential，创建标准 Kubernetes Job/PVC，并把启动数据作为只读 Job 输入交给 AgentRun Job；
6. AgentRun Job 主程序读取启动数据，映射为 SDK `RuntimeRequest`，只调用一次 `RuntimeDriver.Execute(ctx, request)`；Driver 在进程内启动或连接厂商 Runtime，并返回一个 `RuntimeSession`；
7. AgentRun Job 循环消费 `RuntimeSession.Events`，短窗口批量调用 Platform Core `AppendAgentRunEvents`；SDK 生成的 `event_id + stream_id + sequence` 原样保留，Platform Core 根据 run-scoped 身份补充 `agent_run_id + received_at`、幂等持久化并返回已确认 sequence；
8. `RuntimeEvent.started` 必须回显三个指令层摘要及同一 `instruction_set_digest`；Platform Core AgentRun 用例逐项校验通过后才把当前 Run 显示为 Runtime 已接受；
9. 事件流关闭后，AgentRun Job 从 `RuntimeSession.Result` 读取唯一终态结果，先确认关键尾事件已经持久化，再调用 `CompleteAgentRun`。Platform Core 持久化 AgentRunEvent、Artifact、Observation 和 Usage，更新对应 Run `status/report` 后触发下一轮 Reconcile。

Platform Core AgentRun 用例和 Execution Host 共同准备以下分层内容；RuntimeDriver 只消费最终 SDK 输入，不回读产品目录：

- AgentDefinition 中跨任务稳定的 Agent Instructions 和通用 Skills；
- Orchestrator Kernel 固定的最小协作 bootstrap instruction，明确要求 Agent 使用 `oacok work show/guide/read/submit`；
- 当前 `AgentRun.request` 对应的 run-scoped AgentWorkChannel：精确目标、typed inputs、WorkUnit Contract、Computational/Inferential Guide、前序协作结论、本轮反馈、允许动作、输出/提交合同；
- Workflow.spec 已固定的模型 Provider/MCP 配置 ID、revision、configuration digest 与非敏感值，以及实际模型名称；
- Workspace Access；
- Runtime 厂商参数。

Platform Core AgentRun 用例必须先把指令类内容规范化为一个 `RuntimeInstructionSet`（运行时指令集合），再作为 SDK `RuntimeRequest` 的嵌套值交给 RuntimeDriver；不为不同厂商设计平行的 Platform Core 请求类型：

| 英文字段 | 中文字段释义 | 内容来源与约束 |
| --- | --- | --- |
| `agent_instructions` | Agent 稳定角色指令 | 必需；直接复制当前精确 AgentDefinition 的 `language + content + digest`，不重新读取 AgentTemplate 或包内文件 |
| `collaboration_bootstrap` | Agent 协作启动指令 | 必需；由当前 Kernel 协议版本确定，要求 Agent 先使用 `oacok work show`，并说明 `guide/read/submit` 入口 |
| `workspace_instructions` | 当前工作空间适用规则 | 可选；Platform Core AgentRun 用例只在当前 InvocationRequest 通过 typed input/Guide 明确选择了已批准 ProjectRules 等规则内容时映射生成，Runtime SDK 不依赖 OAC ProjectRules 类型，也不能扫描 Repository 猜测 |
| `instruction_set_digest` | 指令集合摘要 | 必需；按 `agent_instructions → collaboration_bootstrap → workspace_instructions` 的固定字段顺序、显式缺省标记和规范化内容计算，用于接口检查、启动幂等和运行证据，不是独立 Version 或领域对象 |

AgentTemplate 包中的 `instructions.md` 或 `instructions_path` 只是内容创作格式。Package 安装/Agent 实例化阶段已经把文件解析成不可变 `AgentDefinition.instructions`；Runtime SDK 不接收模板路径，也不暴露 `agents_md_path`、`claude_md_path` 或其他厂商文件名。

RuntimeDriver 在 start/resume 内部按 Runtime 原生能力物化同一个 `RuntimeInstructionSet`：

| RuntimeDriver 类型 | 首选物化方式 | Repository 文件边界 |
| --- | --- | --- |
| Codex RuntimeDriver | 使用每次 Run 的 `developer_instructions` 或 App Server 等价 developer-instruction 输入承载 Agent Instructions 与 `oacok` bootstrap | Codex 可以自行读取 Repository 现有 `AGENTS.md`；Driver 不创建、不覆盖该文件。只有校验其受管 Project Rules 内容 digest 与当前 `workspace_instructions` 一致时，才可由原生文件加载满足该层 |
| Claude Code-compatible RuntimeDriver | 使用每次 Run 的 system-prompt append、`--append-system-prompt-file` 或 SDK 等价输入 | Runtime 可以自行读取既有 `CLAUDE.md`；Driver 不创建、不覆盖该文件。OAC ProjectRules 若未由 Runtime 原生文件精确承载，则通过 append 输入注入 |
| 其他 RuntimeDriver | 使用 Runtime 原生 system/developer instruction API | 没有原生文本接口时，允许在 Runtime 专用临时目录创建只读 ephemeral instruction file；文件必须位于 Repository/Workspace Git 树之外，并在 Run 结束后删除 |

Repository 中的 `AGENTS.md`、`CLAUDE.md` 和同义文件属于项目/团队长期规则来源，不属于 Agent 角色定义。Runtime 自身可以按原生语义读取它们，但 OAC 不用修改这些文件来注入 AgentDefinition；无法证明原生加载内容与当前批准规则一致时必须通过结构化指令层补充或 Fail Closed，不能静默采用漂移内容。指令只指导 Agent 行为，不能替代 Authorization、Execution Admission、Runtime Tool 权限、WorkUnit Contract、PlanningRules、Sensor、Gate 或 WorkSubmission 校验。

固定字段顺序只定义摘要和呈现顺序，不定义“后一层覆盖前一层”。RuntimeDriver 必须保留所有存在的层，不能按厂商 Prompt 或文件加载规则静默删除、覆盖或重排 OAC 语义。

RuntimeDriver 只有在完整集合已被接受和物化后才能发出 `RuntimeEvent.started`，并回显 `agent_instructions_digest`、`collaboration_bootstrap_digest`、条件性的 `workspace_instructions_digest` 和 `instruction_set_digest`。Platform Core AgentRun 用例必须将四者与当前启动输入比较；缺失或不一致时拒绝该事件，不把 AgentRun 显示为 Runtime 已接受，也不允许终态通过。该回执沿用已有 started 事件，不创建 `InstructionReceipt`、Version 或第二套启动状态机。

Project Policy、Project Rules、Artifact、Observation、Review、GateResult 和 Decision 只有被当前 WorkUnit Contract/Harness 与 input bindings 精确选入时才进入 InvocationRequest。Platform Core、Execution Host、AgentRun Job 和 RuntimeDriver 都不得再扫描并拼接“可能有用的上下文”；冲突约束必须使请求组装失败，不能通过后写覆盖前写猜测优先级。

Codex 等 Runtime 自带 Tool 属于 Runtime Capability，首版不建立独立 Tool Catalog，也不要求用户在 Agent 上逐 Tool 选择。MCP 管理只负责外部 MCP Server；Harness Extension Executor、Runtime Tool 和 MCP Server 是三个不同边界，不能因为界面都显示“工具能力”就合并为同一个执行接口。

### 2.1 AgentRun InvocationRequest 与 `oacok` 协作协议

`AgentRun.request` 是一次 AgentRun 的不可变 InvocationRequest，也是 `oacok work show` 展示工作事实的唯一来源；系统不再创建独立 WorkPackage。`oacok` 是 Orchestrator Kernel 随附的标准 Agent 协作 CLI，而不是 OAC Platform 管理 CLI，也不是模块名称。

创建顺序固定为：

```text
Kernel Runtime
  → 匹配 executor_key
  → 解析 typed input_bindings
  → 组装 InvocationRequest
Workflow Controller
  → 创建稳定 Run request
Platform Core
  → 映射精确 AgentDefinition + Authorization/Admission
  → 不可变保存 AgentRun.request
  → 建立 run-scoped AgentWorkChannel
  → 规范化 RuntimeInstructionSet 与非敏感启动数据
Execution Host
  → 挂载启动数据、Workspace、Skills、输入和临时 Credential
AgentRun Job
  → RuntimeDriver.Execute 一次
  → 循环消费 RuntimeSession.Events 并调用 Platform Core Event API
RuntimeDriver
  → 使用原生 prompt/developer instruction，或隔离临时文件完成物化
Agent
  → oacok work show / guide / read
  → oacok work submit
  → 按 submission_contract 提交 WorkSubmission
```

InvocationRequest 至少包含：

| 英文字段 | 中文字段释义 | 内容来源与作用 |
| --- | --- | --- |
| `invocation_id` | 本次调用身份 | Kernel 在产品 Run 创建前稳定生成；本次 AgentRun 唯一，返工、复审或新 Run 创建新身份 |
| `request_digest` | 调用请求内容摘要 | `oacok work show` 与结果提交必须匹配，防止运行中漂移 |
| `objective` | 当前任务目标 | 来自 WorkUnit 描述和精确 subject，说明这次要完成什么 |
| `inputs[]` | 精确输入资源 | 仅来自依赖闭包内 typed input bindings 的已确认结果；每项包含 resource key、description、media type 和 digest，正文使用 `oacok work read` 获取 |
| `contract` | 工作合同 | 输入/输出槽位、完成要求、禁止事项和结果 Schema |
| `guide.computational[]` | 程序约束指南 | Schema、Policy、允许动作、资源与确定性约束 |
| `guide.inferential[]` | 推理工作协议 | 当前 WorkUnit/Harness 的阶段 Instructions、显式方法资源和领域方法；AgentDefinition 的稳定 Instructions/Skills 通过 RuntimeInstructionSet/RuntimeRequest 单独物化，不复制进 InvocationRequest |
| `prior_conclusions[]` | 前序协作结论 | Producer 结论、Reviewer 结论和显式交接摘要；只选入当前责任需要的结构化内容 |
| `feedback[]` | 本轮反馈 | 与当前 WorkUnit、subject 和 iteration 精确关联且当前责任获准使用的编译诊断、Review/失败反馈和明确 Decision；可来自独立 Reviewer Lineage |
| `allowed_actions[]` | 允许动作 | execution 要求、Contract 输入输出资源范围、Harness 与 Policy 的交集，Agent 无权扩大；不是 Contract 内的用户配置字段 |
| `submission_contract` | 提交合同 | 必需 conclusion、输出、问题/阻塞报告、结果 Schema 与提交通道说明；回调 token 单独注入，不写入请求 |

最终 Agent 工作内容的权威顺序固定为：平台安全/治理规则 > 当前 Run/WorkUnit 精确事实与 Contract > 已确认输入和本轮反馈 > 阶段 Inferential Guide > AgentDefinition 中继承或定制的稳定 Instructions。Kernel 只产出 InvocationRequest 中的动态工作层；Platform Core AgentRun 创建用例解析精确 AgentDefinition 后，才把稳定 Instructions、Skills、Kernel collaboration bootstrap、可选 Workspace Instructions 与该请求合成 RuntimeInstructionSet/RuntimeRequest。低优先级内容冲突时必须阻塞，不靠 Prompt 后写覆盖。

AgentWorkChannel 固定提供四个操作：`show()`、`guide(topic?)`、`read(resource_key)` 和 `submit(work_submission)`。`oacok` 是该接口的 CLI Adapter；Channel endpoint、短期 token 和 invocation binding 由 Platform Core/Execution Host 作为 AgentRun 环境单独注入，Runtime SDK 与 RuntimeDriver 不实现、不代理也不持久化 Platform API。系统不再同时暴露工作包 YAML 路径和另一套 getter，避免两个入口语义漂移。不能执行 CLI 的 Runtime 由 AgentRun Job 主程序从同一 Channel 获取 `show()` 等价结构化初始输入，再映射到 SDK 请求；仍使用同一个 request digest 和 submission contract。

Agent 协作终态 WorkSubmission 必须包含结构化 conclusion，至少有 summary，并可声明 completed、remaining、risks 和相关 resource keys；Platform Core AgentWorkChannel 用例将其持久化为 `run-conclusion` Observation。Reviewer 使用独立 InvocationRequest：固定被审 subject ID/digest、精确输入、Review Contract、Producer 的结构化结论、自己的历史反馈和干净 Workspace，不包含作者隐藏 Session。显式 Acceptance WorkUnit 的主执行者则从该节点的 typed input bindings 获得固定 Release、Deployment、AcceptancePlan、suite digest 和 Preview 资源，并按 AcceptanceResult Contract 提交结果；内置默认流程不在其后再创建 Acceptor responsibility-run。Reviewer Reject 必须提交包含 verdict、summary、findings、nits 和 evidence 的完整 ReviewResult；返工 Run 的新 InvocationRequest 必须带上该完整结果，而不只是 rejected 状态。

Runtime/Session 恢复后仍使用同一 InvocationRequest 和同一 `instruction_set_digest`；进入正式返工、复审或显式新 Run 时生成新的 invocation_id/request_digest，并根据本次精确 AgentDefinition、Kernel bootstrap 和可选 ProjectRules 重新计算指令集合。AgentTemplate Instructions、InvocationRequest、Channel Credential 和运行凭证分开注入，Credential 明文绝不进入请求或 Agent 可见历史。

### 2.2 从 Settings 到 AgentRun 的固定链路

```text
Settings / Skills
→ Content Catalog / SkillPackage release
→ AgentDefinition.skills

Settings / Model Providers
→ mutable ModelProviderConnection + revision
→ DiscoverModels or AddManualModel
→ ModelCatalogSnapshot
→ AgentDefinition.model_policy

Settings / MCP
→ mutable McpServerDefinition + revision
→ MCP Probe / Capability Snapshot
→ AgentDefinition.mcp_server_definition_ids

Settings / Credentials
→ CredentialBinding
→ Provider/MCP/Repository typed configuration

Executor Materialization
→ ResolvedModelSelection + exact MCP configuration snapshots
→ AgentRun launch input
→ Execution Host / AgentRun Job
→ RuntimeDriver
```

设置和测试只建立可选择目录，不直接启动 Workflow。内置 Software Delivery Solution 已提供默认 AgentTemplate 与 Skill；平台/Scope 已有可用模型目录时，用户无需重复进入 Settings。只有所选 Agent、Solution 或外部资源存在能力缺口时，才补充模型、MCP 或 Credential。AgentDefinition 固定可使用的稳定连接/Server 身份和 RuntimeBinding；Project Policy 可以继续收紧；根 Workflow 创建时解析并固化 Provider/MCP 非敏感配置。Kernel 匹配 `executor_key` 后，Executor Materialization 只在该精确 AgentDefinition 已声明且策略允许的集合中固定本次模型和 Runtime；AgentRun Job 只消费最终启动数据。任何一层都不能用 `latest`、运行时自动扫描或隐式环境变量改变已经确定的选择。

### 2.3 模型 Provider 管理

模型 Provider 连接由 Platform Core 的 Agent Capability Catalog Module 管理，具体 HTTP/SDK 调用由 `ModelProviderConnectorPort` 的外层实现完成。首版 API Type：

- `openai-responses`：使用 Responses 风格的消息与响应合同；
- `openai-chat-completions`：用于仍采用 Chat Completions 兼容合同的 Provider；
- `anthropic-messages`：使用 Anthropic Messages 合同。

API Type 是协议，不是厂商名称。用户只配置名称、说明、`base_uri`、API Type、条件性的 CredentialBinding 和可选非敏感 Header。Provider 预设可以确定 API Type 并预填 Base URI；Scope、稳定连接 ID、CAS revision、configuration digest 与审计字段由系统产生。

模型列表端点、请求方法、分页和响应解析由 API Type 对应的 `ModelProviderConnector` 封装，不能进入通用领域对象或普通页面。TLS、超时、代理、重试和出站限制由 Connector 默认值与 Platform/Scope Policy 提供。这样的边界允许增加新的模型协议或适配特殊服务，而不要求所有用户理解连接器内部细节。

兼容服务若只是不提供标准模型发现，直接使用 `AddManualModel` 补充目录；只有企业确实需要自动发现非标准协议时，才注册新的 API Type/Connector。平台不通过每条连接开放任意模型路径来模拟新的协议。

目录与测试流程为：

1. 用户执行 `DiscoverModels`（获取模型），Agent Capability Catalog 按稳定连接 ID 和 expected revision 读取当前 ModelProviderConnection，并把规范化配置及 configuration digest 交给 Connector；
2. Connector 按 API Type 发起发现请求，规范化返回项，并发布不可变 `ModelCatalogSnapshot`；
3. 发现失败、返回空目录或缺少企业别名时，用户执行 `AddManualModel`（手动添加模型），只填写 `external_model_id`、可选展示名和必填说明；
4. 平台创建新的目录快照，并把每个模型的 `source` 推导为 `discovered`、`manual` 或 `mixed`；用户不选择发现模式；
5. 用户选择一个目录模型执行 `SendTestMessage`，记录成功、时延和脱敏错误；
6. Agent Center 只从已发布目录选择模型。

Agent Center 不要求用户先选择 Provider 再进入第二个模型字段，而是查询跨连接的可用模型聚合视图，按 Provider 分组并直接选择 Provider/Model 组合。展示 Label 只用于 UI；AgentDefinition 保存 `model_provider_connection_id + external_model_id`，并可记录选择来源目录快照。根 Workflow 和后续 ResolvedModelSelection 再保存实际连接 revision/configuration digest 与 API Type。

Model Provider Connection 不保存 API Key。创建或编辑页面把 Secret 一次性交给内部 CredentialStore，只保存 `credential_binding_id`。自动发现和测试时，Platform Core 完成 Authorization，CredentialBroker 只为 `model-provider-api` 用途解析短期材料，Connector 在受控进程内使用并立即释放。

失败与恢复口径保持简单：发现失败不产生部分 Snapshot，也不删除上一份有效目录；页面展示脱敏错误并允许重试或手动添加。找不到 API Type 对应 Connector 时连接不能通过就绪检查。手动添加相同 `external_model_id` 时更新来源证据并创建新 Snapshot，不能在原 Snapshot 上覆盖。测试消息失败只形成 Probe 并阻止该连接通过当前预检，不把目录条目静默删除。

运行时模型选择的兼容条件为：

```text
AgentDefinition.model_policy.allowed_models
∩ Project Model Policy
∩ RuntimeDriver.supported_model_api_types
∩ Workflow capability snapshot contains the selected ModelProviderConnection revision/digest
∩ Authorization / Execution Admission
```

交集为空时 AgentRun 不创建。Platform Core AgentRun 用例和 AgentRun Job 都不得自行切换 Provider，也不能因为首选模型不可用而选择未在 `ordered-fallback` 中声明的模型。

### 2.4 MCP 管理

MCP 设置支持两种首版标准 Transport：

- `stdio`：命令必填，参数与非敏感环境变量可选；Workspace 工作目录缺省为根目录，仅在高级配置中覆盖；敏感环境变量使用显式 Credential-to-Environment Binding；
- `streamable-http`：Endpoint URI 必填，非敏感 Header 可选；敏感 Header 使用显式 Credential-to-Header Binding。

平台支持导入常见 `mcpServers` JSON 配置并规范化 `command`、`args`、`env`、`url` 和 `headers`。来源类型由导入或手动配置入口确定，不需要用户填写通用 Manifest。导入器发现疑似 Secret 时必须要求用户就地创建或选择 CredentialBinding。TLS、超时、代理、重试和出站限制由 Platform/Scope Policy 与 MCP Client 默认值提供，不作为普通用户字段。`stdio` Server 只能在 Runtime/Workspace 镜像中启动，不能由 Platform Core 或 Web Server 在宿主机直接执行。

测试流程执行 MCP `initialize`，并在 Server 支持时读取 Tools、Resources 和 Prompts 列表，形成 McpCapabilitySnapshot。Snapshot 只用于展示、诊断和兼容预检，不形成逐 Tool 权限表。用户在 Agent Center 选择稳定 `mcp_server_definition_id`；根 Workflow 创建时再固定该 Server 当时的 revision/configuration digest 和非敏感配置。该 Server 暴露的具体 Tool 首版不再逐项勾选，实际调用仍受 Runtime Capability、当前 Harness allowed actions、Project 网络策略和 ExecutionSecurityRequirements 约束。

创建 AgentRun 时必须同时满足：MCP 配置处于可用状态、Transport 被 RuntimeDriver 支持、Credential 用途匹配、出站策略允许、Project 没有禁用该 Server。Platform Core 只固定规范化配置，RuntimeDriver 负责把它转换成 Codex、Hermes 等具体 Runtime 所需格式；这种厂商映射不能散落在 Web、Controller、AgentRun Job 主程序或每个业务 Solution 中。

## 3. AgentDefinition 与 RuntimeBinding

AgentDefinition 描述逻辑 Agent 能做什么，是正式发布后不可变的记录；RuntimeBinding 描述如何把它物化到某个 Runtime，是 AgentDefinition 内的不可变嵌入值，不单独建立版本聚合。

RuntimeBinding 固定：

- RuntimeDriver；
- Runtime 镜像；
- Runtime Capability；
- 支持的模型 API Type；
- 支持的 MCP Transport；
- 厂商参数 Schema；
- Session 策略；
- CPU、内存、GPU 和架构；
- Kubernetes 调度约束；
- artifact/config digest。

Codex 和 Hermes 随 Workload 镜像交付。平台不扫描宿主机上偶然安装的 Agent 程序。

必需 Capability 缺失时不启动 AgentRun；可选降级必须形成结构化 Observation。

## 4. Runtime 选择

谁承担 Product/Requirement、DAG Orchestrator、Developer、Reviewer 或显式 Acceptance WorkUnit 由当前 WorkUnit 的责任要求和 HostCapabilitiesSnapshot 决定。OAC 的 Agent 候选来自 Workflow 初始 TeamBindingSnapshot 与有效 Workflow-local Executor Decision；Solution 不在顶层穷举责任角色清单，内置软件交付 Setup 也不要求 `acceptor` 候选池。

用户在 Agent Center 发布 AgentDefinition 时选择允许的、实现 `runtime.oac.dev/v1` 的精确 ExtensionPackage，并形成一个或多个 RuntimeBinding；Project Runtime Policy 可以继续收紧可用集合。普通项目用户只选择已安装 RuntimeDriver。模型与 MCP 配置来自统一 Settings Catalog，不由 Runtime SDK 或 ExtensionCatalog 提供。只有企业需要接入私有 Agent Runtime、新会话协议、新事件模型或现有 Driver 无法满足 Required Capability 时，才引用 Runtime SDK 开发新的 RuntimeDriver ExtensionPackage。

系统不硬编码：

- Codex 必须开发；
- Hermes 必须评审；
- 某个 Agent Template 必须承担固定责任；
- 不同责任必须使用不同 Runtime 厂商。

相同 Agent 或相同 Runtime 可以承担不同责任，但 Lineage、Session 和 Workspace 必须隔离。

Runtime 物化必须遵守 Agent Team 的模型分层原则，但不负责按岗位自动路由模型。目标理解、架构设计和 DAG 编排通常使用高能力模型，独立 Review/验收使用具备独立判断能力的模型，大规模执行可以使用经过评测的成本效率型模型；Schema、测试、Digest、Probe 和状态计算则优先由确定性程序完成。具体选择始终来自 AgentDefinition 的显式 `fixed` 或 `ordered-fallback` 策略、Project Policy 与 Workflow 固定快照的交集。RuntimeDriver 只能执行已经解析的选择，不能依据角色名称、负载或价格自行换模。

## 5. 资源调度

候选池是授权集合，不是调度优先级列表。Kernel Executor Matcher 根据：

- 当前 WorkUnit 的责任要求与 Project 责任标签；
- Agent Capability；
- accepted contracts；
- 当前显式可用性；

从 HostCapabilitiesSnapshot 中匹配唯一 `executor_key`。随后 Platform Core Executor Materialization 只在该精确 AgentDefinition 内根据：

- Runtime Capability；
- Resource Requirements；
- Project Policy；
- Execution Security；

固定允许的 RuntimeBinding、模型、MCP 和资源组合。Kubernetes 根据 requests/limits、节点资源和拓扑选择物理节点。Platform Core 不得借 Runtime/资源选择重新替换 AgentDefinition。

零个匹配 Agent 时尚未创建 AgentRun：WorkUnit 保持 `Waiting`，用户通过 MissingExecutor Observation 对应的 Answer Decision 选择合格 Agent，或要求重编排。该 Decision 通过后才进入本节物化流程。资源不足发生在精确 AgentRun 已物化之后，由 Kubernetes 等待物理调度，不视为 WorkUnit 业务失败，也不能替换已固定 AgentDefinition。

## 6. 无人值守执行

Open Agent Cluster 启动 Runtime 时使用 YOLO、Bypass 或 Never-Confirm 模式：

- Runtime 不把普通工具调用升级为用户 Decision；
- Agent 可以在自己的 Workspace 中读写文件、执行命令、安装依赖和运行测试；
- 人类只处理业务 Artifact Review、风险和最终验收。

AgentRun 不获得：

- 产品数据库凭证；
- Controller 或 CRD Status 写权限；它只能使用 run-scoped 凭证调用 Platform Core 当前 AgentRun 的事件、终态与 AgentWorkChannel 接口；
- 其他 AgentRun Workspace；
- 宿主机文件系统；
- cluster-admin；
- 当前责任不需要的生产凭证。

Runtime SDK 可以为其他消费者保留 PermissionRequest，但 Open Agent Cluster 的 Runtime 接口检查要求 Runtime 能无人值守运行。

## 7. Session 与 Lineage

- Lineage 表示同一 WorkUnit、责任和任务范围的连续时间线，不绑定 Agent 或 Runtime；
- 新的独立责任默认创建新 Lineage 和新 Session；
- 同一责任返工优先恢复原 Session；
- Reviewer 只能恢复 Reviewer 自己的 Session；
- 同一责任显式交接给另一 Agent 时可以延续 Lineage，但必须创建新 AgentRun 和新 Session；
- AgentDefinition 或 RuntimeBinding 不兼容时默认创建新 Session；
- Session 丢失时注入结构化 Artifact、Observation、Review 和 Decision；
- Session 恢复只是效率优化；
- AgentRun 每次执行不可变。

原 Session 只有在同一 AgentDefinition、兼容 RuntimeBinding 和相同安全上下文满足时才可恢复。任务线连续性决定注入哪些结构化历史，执行兼容性决定是否复用物理 Session；二者不能混为一谈。

RuntimeDriver 必须报告 Session 创建、恢复和丢失结果，不能伪造已恢复。

## 8. Runtime SDK 调用与实时事件流

AgentRun Job 主程序只调用一次 Runtime SDK；它不能用轮询反复调用 Driver，也不能把 Platform API Client 传入 SDK：

```text
RuntimeDriver.Execute(ctx, RuntimeRequest)
  → RuntimeSession {
      stream_id,
      events: RuntimeEvent stream,
      result: one RuntimeResult
    }
```

概念接口：

```go
type RuntimeDriver interface {
    Execute(ctx context.Context, request RuntimeRequest) (*RuntimeSession, error)
}

type RuntimeSession struct {
    StreamID string
    Events   <-chan RuntimeEvent
    Result   <-chan RuntimeResult
}
```

`RuntimeSession` 只是一次 SDK 调用返回的内存对象，不是 AgentRun、领域聚合、Version 或持久化 Session 表。正常成功时，`Events` 持续产生结构化事件并在 Runtime 结束时关闭，`Result` 随后只返回一个终态值；Driver 必须先确认子进程退出及所属 `Wait`，并验证 clean EOF 与 terminal。取消和超时由调用方传入的 `ctx` 传播到 Driver 和厂商进程。取消后 SDK 停止向调用方转发普通事件，但继续消费 Driver 的事件与终态通道；Driver 仍持有未确认的子进程 `Wait` 时，SDK 不发布终态或 `Result`，Job 保留原 Wait owner。Wait 确认后，SDK 使用现有 20 秒通道关闭期限；`Events` 或 `Result` 仍未关闭时，SDK 发布 `driver-channels-not-closed` unknown 终态，Job 不调用 `CompleteAgentRun`。AgentRun Job 在事件持久化失败后继续等待 SDK 结果；30 秒 drain 到期不能让 Job 在 Driver 的 `Wait` 仍未确认时退出。

`RuntimeRequest` 只包含 Runtime 执行所需的协议中立值：

| 英文字段 | 中文字段释义 | 关键约束 |
| --- | --- | --- |
| `instructions` | 运行时指令集合 | 使用完整 RuntimeInstructionSet；不携带 AgentDefinition、ProjectRules 类型或来源文件路径 |
| `workspace` | 工作空间输入 | 只包含根路径、读写范围、基线摘要和已挂载输入，不包含 Workspace 领域对象 |
| `skills[]` | Skill 物化输入 | 精确内容摘要、说明和只读挂载路径；不让 Driver重新解析 Package Catalog |
| `model` | 模型调用输入 | API Type、实际模型名称和已解析非敏感配置；不包含 ModelProviderConnection 领域对象 |
| `mcp_servers[]` | MCP Server 输入 | 已规范化 Transport、非敏感配置和运行时可见的临时注入位置 |
| `session` | 会话策略 | `new\|resume` tagged union；恢复时携带厂商 Session ID，不携带 OAC Lineage 类型 |
| `driver_parameters` | Driver 原生参数 | 必须先通过当前 RuntimeDriver 的 Schema 校验 |
| `limits` | 执行限制 | Timeout、最大轮次和其他 Runtime 级限制；Kubernetes 资源限制仍由 Job 承接 |

Runtime SDK 不接收 `agent_run_id`、Workflow/WorkUnit、Platform endpoint、Platform token、HTTP Client、数据库句柄、Kubernetes 客户端、ACK 或重试策略。AgentRun Job 主程序从已挂载的 OAC 启动数据映射出 RuntimeRequest；其他项目可以从自己的数据模型映射同一请求并独立使用 SDK。

### 8.1 RuntimeEvent 通用信封

RuntimeDriver 只需要把厂商原生事件转换为类型化事件草稿；Runtime SDK Core 统一补齐所有调用方都会需要的通用信封，避免每个 Driver 和调用方重复实现身份、排序与关联：

| 英文字段 | 中文字段释义 | 生成方与约束 |
| --- | --- | --- |
| `event_id` | Runtime 事件全局唯一身份 | Runtime SDK Core 生成；同一事件重放时保持不变 |
| `stream_id` | 本次 Runtime 事件流身份 | Runtime SDK Core 在 Execute 时生成；等于 RuntimeSession.StreamID |
| `sequence` | 事件流内严格递增序号 | Runtime SDK Core 从 1 开始分配；用于排序和缺口检测 |
| `schema_version` | RuntimeEvent Schema 版本 | Runtime SDK Core 生成；不支持主版本时调用方拒绝消费 |
| `occurred_at` | Runtime 观察时间 | Driver 从原生事件提供，SDK 规范化；缺失时只能标记 unknown，不能冒充平台接收时间 |
| `event_type` | 结构化事件类型 | Driver 映射为受支持 tagged union |
| `correlation` | Session、Turn、Message、Tool Call 等关联身份 | Driver 尽可能保留原生稳定 ID；不可证明的字段保持缺省 |
| `caused_by_event_id` | 直接因果事件身份 | 可选；只关联当前 Runtime stream 内已知事件 |
| `payload` | 类型化事件载荷 | 由 event_type 决定 Schema；普通大小正文优先内联 |
| `extensions` | 厂商扩展字段 | 无法映射但允许保真的非敏感原始字段；不得改变通用字段语义 |

RuntimeEvent 至少覆盖：

- `started`：Runtime 已接受输入，包含三层指令摘要和 `instruction_set_digest`；
- `message`：Agent 文本或可见输出分块，使用稳定 `message_id`；
- `thinking`：仅 Runtime 明确暴露的 reasoning summary，不采集或推断隐藏思维链；
- `tool_call` / `tool_result`：使用稳定 `tool_call_id` 关联调用与结果；
- `command`：Shell 命令、stdout/stderr 分块、退出码和耗时；
- `file_change`：文件创建、修改、删除和可用 Diff；
- `memory`：Runtime 明确暴露的记忆读取、写入、搜索或删除事实；包含 `operation`、原生 memory identity/scope、outcome 和经授权脱敏的摘要，不读取或推断隐藏 Session memory；
- `mcp`：MCP 连接与调用事实；
- `usage`：模型、Tool 或 Runtime 使用事实；
- `status` / `heartbeat` / `error` / `terminal`。

一个 AgentRun 必须恰好对应一次 RuntimeDriver.Execute、一个 RuntimeSession 和一个 stream_id。SDK stream 的第一个事件必须是 `sequence=1` 的 `started`；Platform Core 在接受首批事件时使用 Run revision CAS，把该 `stream_id` 固定到当前 AgentRun report。此后所有 Append 和 Complete 都必须匹配已绑定 stream，不同 stream 返回 Conflict。AgentRun Job 进程失败时，该 Run 进入明确失败或未知处理；任何重新执行都创建新的 AgentRun 和 RuntimeSession，不能在原 Run 中静默启动第二条从 1 开始的事件流。恢复同一个厂商 Session ID 只表示上下文连续性，不复用旧 AgentRun 的 stream_id 或 sequence。

### 8.2 AgentRun Job 事件消费与 Platform Core 写入

AgentRun Job 主程序负责 OAC 特有的事件上传，但这段代码不属于 Runtime SDK：

```text
RuntimeSession.Events
→ AgentRun Job bounded batch
→ Platform Core AppendAgentRunEvents
→ persist AgentRunEvent / UsageRecord / large payload
→ publish persisted cursor notification
→ return accepted stream_id + sequence
```

AgentRun Job 只做以下工作：

1. 循环读取 RuntimeSession.Events；
2. 普通文本/Thinking 使用短窗口或大小阈值批量，Tool 边界、错误和 terminal 立即 flush；
3. 使用 Job 持有的 run-scoped 凭证调用 `AppendAgentRunEvents`，凭证和 URI 不进入 SDK；
4. 保留未确认批次，按相同 `event_id + stream_id + sequence` 重试；缓冲区达到上限时暂停继续读取，形成有界背压；
5. 正常成功时，Events 关闭后读取唯一 RuntimeResult，等待关键尾事件全部确认，再调用 `CompleteAgentRun`。取消失败路径只有在 SDK 已确认 Driver 的 owning `Wait` 后才进入 20 秒通道关闭期限；期限届满则记录 unknown 并停止，不调用 `CompleteAgentRun`。如果 owning `Wait` 仍未确认，Job 与 adapter 保留该 owner 并继续等待。

Platform Core 从认证路径和 run-scoped 凭证确定 `agent_run_id`，并在接收时生成 `received_at`；调用方不能改写这两个字段。产品 `AgentRunEvent` 采用扁平结构保存 RuntimeEvent 的通用字段，再附加 OAC 归属/接收字段，不嵌套第二份 `runtime_event`，也不重新生成 event_id 或 sequence：

| OAC 附加字段 | 中文字段释义 | 关键约束 |
| --- | --- | --- |
| `agent_run_id` | 所属 AgentRun 身份 | 从 run-scoped 凭证与请求路径推导，不能由事件正文指定其他 Run |
| `received_at` | Platform Core 接收时间 | 由服务端时钟生成，用于接收延迟和恢复诊断 |
| `critical` | 是否要求终态前确认持久化 | 由 Platform Core 根据 event_type 和当前合同推导，不属于 Runtime SDK 字段 |

Platform Core 先执行 one-stream CAS：未绑定时只接受以 `started@sequence=1` 开始的首批事件并固定 `stream_id`；已绑定时只接受同一 stream。随后按 `(agent_run_id, event_id)` 和 `(agent_run_id, stream_id, sequence)` 双重唯一性幂等写入。相同身份相同 digest 返回原确认；相同身份不同 digest 或不同 stream 返回 Conflict；乱序或缺口显式等待/拒绝。普通大小 payload 内联存入 AgentRunEvent；只有超过传输/存储限制的 Tool 输出、命令输出、Diff 或原始厂商载荷才先保存到 Artifact Storage，并在事件中保存语义明确的 Artifact ID 与 digest。

事件、Usage、Artifact/Observation 元数据先持久化，再发布 SSE/WebSocket 游标通知。Web 的实时 AgentRun 详情按 sequence 展示 message、thinking、Tool/MCP、Shell、文件变化、Runtime 明确上报的 memory 操作、Usage、错误和终态；断线后使用 `stream_id + after_sequence` 从数据库补齐。Pod stdout/stderr 只属于基础设施诊断，不能重建 AgentRunEvent。

RuntimeResult 只表达 Runtime 执行终态、厂商 Session ID、最终可见输出摘要和错误分类。业务 WorkSubmission 仍通过 run-scoped AgentWorkChannel / `oacok work submit` 进入 Platform Core，并由 Submission Validator 校验；`CompleteAgentRun` 不能使用 RuntimeResult 绕过 WorkSubmission、Contract、Sensor、Gate 或 Decision。

### 8.3 Usage 采集主链

Usage 不依赖日志、Prompt 长度估算或外部遥测回读。RuntimeDriver 在模型调用、Tool 调用或 Runtime 执行边界结束时，从 Provider 响应和 Runtime 原生结构化事件提取事实，产生一个类型化 `RuntimeEvent.usage`；流式 Token delta 只属于实时输出事件，不能逐 Token 写 UsageRecord。

```text
Provider / Runtime native response
→ RuntimeDriver typed mapping
→ Runtime SDK Core envelope
→ RuntimeEvent.usage
→ AgentRun Job AppendAgentRunEvents
→ Platform Core OAC attribution + immutable UsageRecord
→ Usage Analytics query/read model
```

Platform Core 以 run-scoped 身份固定的 Workflow/WorkUnit/iteration/AgentRun 为边界，并从已创建 AgentRun 固定事实补充 AgentDefinition、responsibility、RuntimeDriver、ModelProviderConnection 与请求模型；它不读取当前 Project/Agent/Provider 配置猜测归因。一个 `source_event_id` 只能创建一个 UsageRecord，重复 event/sequence/digest 返回原结果，不产生重复 Token 或成本。

模型调用必须在完成或明确失败时形成一次终态 Usage；Runtime 崩溃且 Provider Usage 未返回时，可以保存带已知时间/outcome、Token 为 unknown 的部分记录，但不能根据 Prompt 字符数或历史平均值估算。Tool 与 Runtime Usage 同理。`started_at`、`first_output_at`、`completed_at` 缺失时，对应 total latency 或 TTFT 图表必须降低 coverage，而不是补零。

成本优先接受 Provider 明确返回值。Provider 未返回时，Governance Usage 归一化层只有在当前 AgentRun 已从不可变 ModelCatalogSnapshot 固定精确 pricing basis，且 response model 与该 basis 匹配时才允许计算，并把使用过的 basis 固定进 UsageRecord；否则成本为 unknown。RuntimeDriver 不读取当前价格表，也不承担财务计算。

## 9. RuntimeDriver 与厂商原生协议

RuntimeDriver 是 `runtime.oac.dev/v1` Runtime SDK 的实现角色，不额外建立专属 Package 类型、Registry、Host 或长期 Service。它以 ExtensionPackage 安装；对 Runtime 接口而言，现有 `entrypoint` 必须解析为一个固定 digest 的 AgentRun Job OCI 镜像与启动命令。该镜像由薄的 OAC AgentRun Job 主程序和 RuntimeDriver 实现链接在同一进程，主程序调用 SDK `Execute`，Driver 再启动厂商子进程或连接厂商 Runtime。平台可以提供这段主程序的构建模板，但它只是调用方代码，不是公共 Runtime SDK、独立组件或新 Package 类型。平台不在 AgentRun Job 与 Driver 之间增加 OAC JSON-RPC、Service URL、服务发现、Replica 或 HA 协议。

RuntimeDriver 负责：

- discover/start/connect/close Runtime；
- create/resume/cancel 厂商 Session；
- 在 Execute 时接收并原生物化 RuntimeInstructionSet；
- execute Turn；
- 把厂商原生事件转换为 RuntimeEvent draft，并保留 Message/Tool/Command/File/Usage 关联身份；
- Capability 协商；
- 声明并物化支持的模型 API Type 与 MCP Transport；
- process lifecycle 与允许的 raw event 保真。

首版协议落点固定为：

| RuntimeDriver | 厂商连接方式 | 生命周期映射 | 首版定位 |
| --- | --- | --- | --- |
| Codex RuntimeDriver | 启动 `codex app-server`，使用默认 stdio 上的 Codex App Server JSONL 消息协议 | `initialize → initialized → thread/start\|thread/resume → turn/start`；原生通知按固定 Driver release 的生成 Schema/Fixture 映射 RuntimeEvent | 内置 Driver |
| Hermes RuntimeDriver | 启动 `hermes acp`，使用 ACP JSON-RPC over stdio | initialize、Session create/resume、prompt、update、cancel 等能力按固定 ACP/Driver release 的 Fixture 校验 | 内置 Driver |
| Claude Code-compatible RuntimeDriver | 启动 `claude -p --input-format stream-json --output-format stream-json --verbose`，解析 stream JSON/NDJSON；不得标成 JSON-RPC | 新执行或 `--resume`，稳定 Instructions 使用 CLI 支持的系统提示通道映射 RuntimeInstructionSet | 官方接口 Fixture/可安装 Driver 目标 |
| OpenClaw RuntimeDriver | 启动 `openclaw acp`，使用 Gateway-backed ACP over stdio | 映射 Session、Agent 输出、thinking、Tool、Usage 与取消；原生未提供或只提供近似值的字段必须标为 unknown/unavailable | 可安装 Driver 目标 |

Builtin Codex/Hermes 和第三方 RuntimeDriver 都通过 ExtensionManager 安装并完成同一 Runtime SDK 接口检查，再由 ExtensionCatalog 按 `runtime.oac.dev/v1` 索引。RuntimeDriver ExtensionPackage 不暴露 `execution_mode=service|trusted-in-process` 用户选项；Runtime 的本地、Gateway 或远程连接差异由 Driver 内部实现，不改变 SDK 和 AgentRun Job 合同。

Execution Host 启动的是该精确 RuntimeDriver release 已验证的 AgentRun Job entrypoint，而不是从控制面动态下载代码到任意现有 Pod，也不是先启动一个通用 Job 再通过网络发现 Driver。Job 镜像 digest、启动命令和 Driver package provenance 固定在 AgentRun launch input；第三方 Driver 更新必须发布新的 ExtensionPackage release，并只影响随后创建的 Workflow/AgentRun。

RuntimeDriver 不拥有 Model Catalog、Credential Store 或可编辑的 Model/MCP 配置。它只接收 Platform Core 已固定并由 Execution Host 安全物化的 SDK 输入，再把模型/MCP 配置转换为具体 Runtime 的启动与会话参数。

RuntimeDriver 同样不拥有 AgentTemplate、AgentDefinition、ProjectRules、AgentWorkChannel 或 Repository 指令文件。它只消费 RuntimeInstructionSet 的内容与摘要：不得按路径回读模板，不得把 Agent 角色持久写入 Repository，不得把临时指令文件放入 Git 工作树，也不得调用 Platform API 或根据厂商文件加载顺序重新解释 OAC 的指令优先级。

Builtin Codex、Hermes 以及每个支持的模型 API Type 都必须提供独立字段映射 Fixture，明确哪些字段是 Required、Optional 或 unavailable。RuntimeDriver 可以参考 OpenTelemetry GenAI Semantic Conventions，但首版不加载 OTel SDK、启动 Collector、接收 OTLP 或把 OTel Span/Metric 暴露给领域层。未来 OTLP Export 只能作为 Platform Core 之外的异步外层 Adapter，从已经提交的 OAC 事实导出。

## 10. Runtime SDK 与 Agent Job 接口检查

Runtime SDK 同时发布 RuntimeDriver 接口、事件 Core 和一个检查入口。第三方开发者在本地/CI 与平台安装时运行同一套检查；Package 不能选择或跳过必需检查。至少验证：

- `Execute` 只调用一次并返回一个 RuntimeSession；Events 关闭后 Result 恰好返回一次；
- SDK 不依赖 OAC API Client、AgentRun/Workflow/Project 类型、Kubernetes、数据库、Platform endpoint/token、ACK 或重试；
- SDK Core 自动生成稳定 `event_id + stream_id + sequence + schema_version`，sequence 从 1 严格递增，重放保持同一 event_id；
- RuntimeDriver 只生成类型化事件草稿，不允许覆盖 SDK 已生成的身份和顺序；
- 一个 AgentRun 只对应一个 RuntimeSession/stream；Job 进程失败后的重新执行使用新 AgentRun，不能静默复用旧 sequence；
- Session 创建、厂商 Session 恢复和恢复失败显式可观察；
- 文本/Thinking、Tool call/result、Command/stdout/stderr/exit code、文件变化、显式 memory 操作、MCP、heartbeat、error 和 terminal 事件完整关联；
- Thinking 只承载 Runtime 明确暴露的 reasoning summary，不输出隐藏思维链；
- 取消通过 ctx 终止 Driver 和厂商进程；
- Usage 每次模型调用至多形成一个终态 model-call 事件，重复 Runtime/terminal 事件不重复计量；
- Provider request/response model、Token 总量与 cache/reasoning 子集、TTFT/总时长、Tool/MCP 时长/outcome 和 unknown 语义映射正确；
- Prompt/Response 正文、Secret、私有推理和敏感 Provider 错误不进入 Usage；
- `openai-responses`、`openai-chat-completions`、`anthropic-messages` 支持声明与不兼容拒绝；
- MCP `stdio` 与 `streamable-http` 物化、初始化测试和 Capability Snapshot；
- MCP 导入时内联 Secret 拒绝，Credential 注入不进入 RuntimeEvent、Result 或日志；
- Runtime SDK Execute 使用同一个结构化 RuntimeInstructionSet，公共合同不出现 `agents_md_path`、`claude_md_path` 或厂商专用指令文件字段；
- Codex App Server、Hermes ACP、Claude stream-json 和 OpenClaw ACP Fixture 使用各自精确原生协议；Hermes/OpenClaw 可以复用 ACP codec，但仍保留独立 Driver、能力声明和事件映射，不以 ACP 冒充所有 Runtime 的统一行为；
- `RuntimeEvent.started` 回显与本次输入一致的三个层摘要和 `instruction_set_digest`，且事件不包含指令正文、厂商文件路径或 Secret；
- Repository 既有 `AGENTS.md`、`CLAUDE.md` 及其内容 digest 在 AgentRun 前后保持不变；文件回退只使用 Repository 外只读临时文件并在 Run 结束后删除；
- AgentRun Job 的 Platform Client 位于 SDK 外部，能够按 SDK sequence 批量上传、处理 ACK、重试未确认事件和施加有界背压；
- Platform Core 从 run-scoped 身份推导 agent_run_id、生成 received_at、扁平保存 AgentRunEvent，并拒绝跨 Run、重复不同 digest、乱序和缺口；
- 事件先持久化再发布 SSE/WebSocket；断线后可按 stream_id + after_sequence 完整补齐 message、Tool、Command、File、Usage 和 terminal；
- 关键尾事件确认后才允许 `CompleteAgentRun`，且 RuntimeResult 不能绕过 AgentWorkChannel WorkSubmission、Contract、Sensor、Gate 或 Decision；
- 与 Execution Host 的基础设施报告并发应用时不丢字段；未安装 OTel 时完整 Runtime/Usage/实时展示主链仍通过。

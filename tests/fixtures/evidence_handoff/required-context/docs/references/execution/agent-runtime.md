# Agent Runtime 契约参考

本文定义 Agent Execution 领域中 `RuntimeBinding`、模型 Provider 连接、Model Catalog、MCP Server 配置、Runtime SDK、AgentRun Job 集成与 AgentRunEvent 的公共契约。Runtime SDK 不包含 Project、Workflow、WorkUnit、授权、Platform API Client 或 Kubernetes 产品模型。

共同记法见 [Reference 共同约定](../conventions.md)，设计边界见 [Agent Runtime 详细设计](../../design/detailed/06-agent-runtime-detailed-design.md)。

## 1. RuntimeBinding

`RuntimeBinding` 描述逻辑 Agent 如何被物化到一个精确 Runtime。它是不可变 AgentDefinition 内的嵌入值，不单独形成可编辑对象或版本聚合；它不定义 Agent 的业务责任，也不代表一次实际执行。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| runtime_binding_id | 是 | 当前 AgentDefinition 内的局部 Binding 身份 | 只需在该 AgentDefinition 内唯一 |
| runtime_driver | 是 | 实现 `runtime.oac.dev/v1` 的精确 ExtensionPackage | 必须来自 Workflow 的 LockedComponentSet |
| runtime_artifact | 是 | Runtime 镜像或可执行制品及 digest | 不扫描宿主机偶然安装的软件 |
| capabilities[] | 是 | Session、Turn、MCP、工具、文件、Usage、取消等能力 | 必须由 Runtime SDK 接口检查证明 |
| supported_model_api_types[] | 是 | RuntimeDriver 可消费的模型接口类型 | 例如 `openai-responses`、`openai-chat-completions`、`anthropic-messages`；用于兼容性检查，不提供模型目录 |
| supported_mcp_transports[] | 否 | RuntimeDriver 可物化的 MCP Transport | 首版标准值为 `stdio`、`streamable-http` |
| driver_config_digest | 是 | 规范化驱动配置摘要 | Secret 只通过 CredentialBinding ID 使用，不进入正文 |
| resource_requirements | 是 | 硬件与调度要求 | 创建 AgentRun 前必须可满足 |
| binding_digest | 是 | 完整 RuntimeBinding 内容摘要 | Runtime 或配置变化时发布新的 AgentDefinition |

同一个 `AgentDefinition` 可以嵌入多个 RuntimeBinding。Kernel Runtime 先从 HostCapabilitiesSnapshot 匹配唯一 `executor_key`；Platform Core Executor Materialization 再按该 key 一一解析精确 AgentDefinition，并在其已声明且策略允许的集合中固定一个 RuntimeBinding、模型、MCP Server 与 ResourceRequirements。它不得重新选择 AgentDefinition。AgentRun Job 和 RuntimeDriver 只消费该固定结果，不重新选择或回退。Codex 等 Runtime 自带工具不进入独立 Tool Catalog，首版也不在 AgentDefinition 中逐工具选择；它们由 Runtime Capability、Harness 允许动作、Project Policy 与 ExecutionSecurityRequirements 约束。

## 2. ModelProviderConnection

`ModelProviderConnection` 是一个可直接保存修改的稳定配置对象。每次成功修改递增内部 `revision`、重算 `configuration_digest` 并写入 AuditEvent，不创建新的 Version 聚合。API Type 表达协议合同，不等同厂商品牌；同一厂商可以提供多个 API Type，同一兼容 API 也可以来自不同服务商。

下表中的“必需”表示完整领域对象是否必须具备该事实，不表示用户必须逐项填写。普通表单只要求用户提供名称、说明、连接协议与地址；其余字段由当前上下文、系统、Credential 流程或 Connector 产生。

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| model_provider_connection_id | 是 | 模型 Provider 连接稳定身份 | system | 首次创建时生成；AgentDefinition 持久化该身份 |
| revision | 是 | 当前内部 CAS 修订号 | system | 每次保存递增；只用于冲突检测和执行来源证明，不是用户可见版本 |
| scope_id | 是 | 连接所属治理范围 | context | 从当前 Settings Scope 继承，决定谁可查看、测试和使用 |
| display.name | 是 | 连接名称 | user | 提供用户可识别名称，不能只展示 Base URI |
| display.description | 是 | 连接说明 | user | 说明用途、所属团队或环境，后续目录与选择页面持续展示 |
| api_type | 是 | 模型接口类型 | user / preset | 首版支持 `openai-responses`、`openai-chat-completions`、`anthropic-messages`；Provider 预设可以直接确定该值 |
| base_uri | 是 | Provider 基础地址 | user / preset | 模型服务根 URI；Provider 预设可以提供默认地址，用户可按权限修改 |
| credential_binding_id | 视连接 | API 凭证绑定身份 | user / context | 需要鉴权时选择或就地创建；匿名或本地免鉴权端点可以省略，正文绝不保存 API Key |
| non_secret_headers | 否 | 非敏感请求头 | user | 高级配置，只允许租户标识等非 Secret 值；敏感 Header 必须进入 CredentialBinding |
| configuration_digest | 是 | 规范化配置摘要 | system | 不包含 Secret 内容；每次保存后与当前 revision 对应 |
| created_by / created_at / updated_by / updated_at | 是 | 创建与最后修改来源 | context / system | 用于审计，不参与隐式选择 |

模型发现端点、请求形状、分页和响应解析属于按 `api_type` 注册的 `ModelProviderConnector`，不是 Provider Connection 的公开配置字段。TLS、超时、代理、重试和出站网络限制由 Connector 默认值与 Platform/Scope Policy 决定；普通用户不选择“发现模式”，也不填写模型列表路径或连接策略。

模型 API Key 不属于 Provider Connection 正文。Web 只把用户一次性提交的 Secret 交给内部 CredentialStore，并把返回的 `credential_binding_id` 写入当前连接。

说明性 YAML：

```yaml
modelProviderConnectionId: corp-openai-gateway
# modelProviderConnectionId：模型 Provider 连接的稳定身份。

revision: 3
# revision：平台维护的内部 CAS 修订号；用户不编辑，也不把它当业务版本选择。

scopeId: local-organization
# scopeId：谁可以查看、测试和使用该连接的治理范围。

display:
  # display：页面展示信息；名称和说明都必须由配置者填写。
  name: 公司 OpenAI Gateway
  # name：用户可读连接名称。
  description: 用于软件交付 Agent 的生产模型网关
  # description：说明连接用途，避免用户仅凭 Base URI 猜测。

apiType: openai-responses
# apiType：调用时采用的模型 API 协议合同，不是厂商品牌名称。

baseUri: https://llm-gateway.example.com
# baseUri：模型服务基础 URI；不包含 API Key。

credentialBindingId: credential-model-gateway-prod
# credentialBindingId：需要鉴权时绑定的 Credential 身份；匿名或本地免鉴权端点可以省略。

configurationDigest: sha256:...
# configurationDigest：系统生成的不含 Secret 的规范化配置摘要，用户不填写。
```

## 3. Model Catalog 与连通性测试

Platform Core 的 Agent Capability Catalog Module 提供三个领域 ApplicationCommand，并通过窄 `ModelProviderConnectorPort` 调用按 `api_type` 实现的外层 Connector。Domain 不导入 HTTP Client 或厂商 SDK 类型。

| 英文命令 | 中文命令释义 | 输入 | 输出 | 约束 |
| --- | --- | --- | --- | --- |
| `DiscoverModels` | 获取模型 | `model_provider_connection_id` | ModelCatalogSnapshot 或结构化错误 | 服务端读取当前连接并记录所用 revision/digest；Connector 根据 `api_type` 自行选择发现请求、端点、分页和响应解析，并通过 CredentialBroker 使用短期凭证 |
| `AddManualModel` | 手动添加模型 | 连接 ID、`external_model_id`、可选展示名、必填说明 | 新的 ModelCatalogSnapshot | 由 Catalog Use Case 直接校验和发布，不调用外部 Connector；不需要先切换配置模式 |
| `SendTestMessage` | 发送测试消息 | 连接 ID、`external_model_id`、最小测试输入 | ModelProviderProbe | 服务端读取当前连接并记录 revision/digest；调用 Connector 按准确 API Type 发送最小消息，不把测试正文或 Secret 作为目录事实 |

`ModelProviderConnectorPort` 只暴露发现模型和发送测试消息所需的窄操作，并负责把 Provider 原始错误转换为脱敏错误分类；它不保存目录、不接受生命周期命令，也不处理人工模型。`DiscoverModels` 与 `AddManualModel` 都是目录命令，不是连接配置模式。用户点击“获取模型”时无需知道厂商路径；若发现失败或没有返回所需模型，页面直接提供“手动添加模型”。后续再次获取模型时，平台创建新快照并保留仍有效的人工条目；同一 `external_model_id` 同时被发现和人工维护时合并来源证据，不生成两个可混淆选项。

`ModelCatalogSnapshot` 是一次不可变目录结果：

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用 |
| --- | --- | --- | --- | --- |
| model_catalog_snapshot_id | 是 | 模型目录快照身份 | system | 每次成功获取或人工变更目录时创建新快照 |
| model_provider_connection_id | 是 | 来源连接稳定身份 | context | 与下列 revision/digest 共同证明历史来源 |
| model_provider_connection_revision | 是 | 发现时使用的连接修订号 | system | 不从当前连接重新解释历史结果 |
| model_provider_configuration_digest | 是 | 发现时使用的连接配置摘要 | system | 不包含 Secret；防止内容漂移 |
| models[] | 是 | 可选模型列表 | connector / user / system | 每项至少包含 `external_model_id`、展示名和来源；人工条目必须有说明，能力元数据来自发现或探测，不要求用户声明 |
| created_at | 是 | 快照创建时间 | system | 明确目录新鲜度，不隐式当成永久事实 |
| content_digest | 是 | 目录内容摘要 | system | 对脱敏规范化目录计算，用于发现漂移和幂等，不保存敏感原始响应 |

`models[]` 中每项的最小字段为：

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用 |
| --- | --- | --- | --- | --- |
| external_model_id | 是 | Provider 侧模型名称 | connector / user | 后续 API 请求实际发送的模型值 |
| display_name | 是 | 模型展示名称 | connector / user / system | Provider 或用户提供；缺省时系统使用 `external_model_id` |
| description | 否 | 模型说明 | connector / user | 人工添加时必填；发现结果未提供说明时页面明确显示“Provider 未提供说明” |
| source | 是 | 模型来源 | system | `discovered`、`manual` 或 `mixed`，由目录命令推导，用户不选择 |
| observed_capabilities[] | 否 | 已观察能力 | connector | 来自 Provider 元数据或 Probe，只用于兼容预检，不由用户手工宣称 |
| pricing_basis | 否 | Usage 成本计算依据 | connector / system | 包含 currency、按 input/output/cache/reasoning 等类别的 Decimal rate、unit、effective_at、source 和 digest；只用于可重放成本计算，不是模型身份或普通用户必填字段 |

`ModelProviderProbe` 至少保存 `model_provider_connection_id`、`model_provider_connection_revision`、`model_provider_configuration_digest`、`external_model_id`、`api_type`、`outcome`、`latency`、`sanitized_error` 和 `tested_at`。Probe 是连接观察，不创建新的生命周期状态机；失败只会阻止该连接通过当前预检或被新 Agent 选择。

Agent Center 只从有效 Model Catalog 选择模型。创建 AgentRun 时，Platform Core 固定一个 `ResolvedModelSelection`：

| 字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| model_provider_connection_id | 是 | 实际连接稳定身份 | 由 AgentDefinition 的模型选择得到 |
| model_provider_connection_revision | 是 | Workflow 创建时解析的连接修订号 | 运行中不切换 Base URI 或非敏感配置 |
| model_provider_configuration_digest | 是 | Workflow 固定的连接配置摘要 | 与 Workflow.spec 中配置快照一致 |
| model_catalog_snapshot_id | 否 | 选择时使用的目录快照身份 | 只证明模型从哪次发现或人工登记中选出，不参与模型身份 |
| external_model_id | 是 | 实际模型名称 | RuntimeDriver 原样映射到对应模型 API |
| api_type | 是 | 实际模型接口类型 | 必须被所选 RuntimeDriver 支持 |
| pricing_basis | 否 | 本次 Run 可用的成本计算依据 | 从选择时不可变 ModelCatalogSnapshot 的模型条目复制；不发送给 Provider，响应模型不匹配时不得套用 |

## 4. McpServerDefinition

MCP 管理统一承接外部 MCP Server 配置。它不管理 Codex 等 Runtime 自带工具，也不把 MCP Server 伪装成 Harness Extension。`McpServerDefinition` 是可直接保存修改的稳定配置对象；每次保存递增内部 `revision`、重算 `configuration_digest` 并写入 AuditEvent。

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| mcp_server_definition_id | 是 | MCP Server 稳定身份 | system | 首次创建时生成；AgentDefinition 持久化该身份 |
| revision | 是 | 当前内部 CAS 修订号 | system | 每次保存递增；用于冲突检测和执行来源证明 |
| scope_id | 是 | 配置所属治理范围 | context | 从当前 Settings Scope 继承，决定可见性、测试和使用权限 |
| display.name | 是 | MCP Server 名称 | user | 供用户在目录中识别 |
| display.description | 是 | MCP Server 能力说明 | user | 说明该 Server 提供什么能力以及供谁使用 |
| transport | 是 | MCP 传输配置 tagged union | user / import | 首版标准类型为 `stdio`、`streamable-http`；导入时由配置结构解析 |
| configuration_digest | 是 | 规范化配置摘要 | system | Secret 不参与正文摘要；与当前 revision 对应 |
| created_by / created_at / updated_by / updated_at | 是 | 创建与最后修改来源 | context / system | 支持审计和恢复 |

`transport.type = stdio` 时：

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| command | 是 | 启动命令 | user / import | 只能在 Runtime/Workspace 镜像内执行，不允许平台控制面直接启动任意宿主机进程 |
| args[] | 否 | 命令参数 | user / import | 保持有序；不得内嵌 Secret |
| working_directory | 否 | 工作目录 | user / system | 缺省时使用 Workspace 根目录；仅作为高级配置展示，并且必须位于允许路径内 |
| environment | 否 | 非敏感环境变量 | user / import | Secret 值禁止写入；敏感变量使用 `credential_environment_bindings[]` |
| credential_environment_bindings[] | 否 | 凭证到环境变量的绑定 | context / user | 只在存在敏感环境变量时生成；每项明确变量名、credential_binding_id 和用途 |

`transport.type = streamable-http` 时：

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| endpoint_uri | 是 | MCP 远程端点 | user / import | 一个端点承接 MCP POST/GET 交互；必须满足 TLS 与出站策略 |
| headers | 否 | 非敏感请求头 | user / import | 不得保存 Token、Cookie 或 API Key |
| credential_header_bindings[] | 否 | 凭证到 Header 的绑定 | context / user | 只在存在敏感 Header 时生成；每项明确 Header 名、credential_binding_id 和用途 |

MCP 的 TLS、超时、代理、重试和出站网络限制由 Platform/Scope Policy 与 MCP Client 默认值提供，不作为普通用户连接表单。设置页支持导入业内常见的 `mcpServers` JSON 结构，识别 `command`、`args`、`env`、`url` 和 `headers` 并规范化为上述模型。导入器发现疑似 Secret 的内联环境变量或 Header 时必须停止发布，要求用户就地创建或选择 CredentialBinding 后再完成映射；不能把原值直接保存到 Catalog。

说明性 YAML：

```yaml
mcpServerDefinitionId: github-readonly
# mcpServerDefinitionId：MCP Server 配置的稳定身份。

revision: 2
# revision：平台维护的内部 CAS 修订号；用户不编辑。

scopeId: local-organization
# scopeId：从当前 Settings Scope 继承的治理范围，用户不重复填写。

display:
  # display：页面展示信息；名称和说明都必须填写。
  name: GitHub Read-only MCP
  # name：用户可读名称。
  description: 为调研和 Review Agent 提供只读 Repository 查询
  # description：说明能力与使用边界。

transport:
  # transport：MCP 传输 tagged union，必须先根据 type 解释其余字段。
  type: streamable-http
  # type：使用标准 Streamable HTTP Transport。
  endpointUri: https://mcp.example.com/github
  # endpointUri：远程 MCP 单一端点，不包含 Token。
  headers:
    # headers：可以持久化的非敏感 Header。
    X-Tenant: engineering
  credentialHeaderBindings:
    # credentialHeaderBindings：把敏感 Header 映射到 CredentialBinding。
    - headerName: Authorization
      # headerName：运行时需要注入的请求头名称。
      credentialBindingId: credential-github-mcp-read
      # credentialBindingId：只允许 mcp-http-auth 用途的凭证绑定身份。

configurationDigest: sha256:...
# configurationDigest：不包含 Secret 的规范化 MCP 配置摘要。
```

`sse` 可以作为旧配置导入兼容值给出迁移提示，但首版不把它作为新建配置的标准 Transport。MCP 配置只选择 Server，不提供逐 Tool 勾选；Server 成功连接后暴露的 Tool、Resource 和 Prompt 通过 Capability Snapshot 展示，实际允许动作仍受 Runtime、Harness、Project Policy 和 Execution Security 约束。

## 5. MCP 测试与 Runtime 物化

MCP Test Use Case 至少执行 `initialize` 握手，并按 Server 声明尝试 `tools/list`、`resources/list` 和 `prompts/list`。测试结果形成不可变 `McpCapabilitySnapshot`：

| 字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| mcp_capability_snapshot_id | 是 | MCP 能力快照身份 | 每次成功测试创建新快照 |
| mcp_server_definition_id | 是 | 来源 MCP Server 稳定身份 | 与下列 revision/digest 共同证明历史来源 |
| mcp_server_definition_revision | 是 | 测试时使用的配置修订号 | 历史结果不跟随当前配置变化 |
| mcp_server_configuration_digest | 是 | 测试时使用的配置摘要 | 不包含 Secret |
| protocol_version | 是 | 协商后的 MCP 协议版本 | 用于兼容性和诊断 |
| server_info | 是 | Server 名称与版本 | 只保存握手返回的非敏感元数据 |
| tools[] / resources[] / prompts[] | 否 | Server 暴露能力摘要 | 用于展示和诊断，不形成平台 Tool Catalog 或权限白名单 |
| outcome / latency | 是 | 测试结果与耗时 | 失败时保存脱敏错误分类 |
| tested_at | 是 | 测试时间 | 明确能力快照的新鲜度 |

AgentDefinition 只保存 MCP Server 稳定 ID。根 Workflow 创建时，Platform Core 校验当前配置、Project Policy 与 RuntimeDriver 的 `supported_mcp_transports[]`，并把每个实际可用 Server 的 revision、configuration digest 和非敏感规范化配置固化到 `Workflow.spec`；AgentRun 从该快照选择并复制事实，不读取当前 Settings 值。CredentialBroker 只为当前 Run 和当前 MCP 用途解析短期凭证；RuntimeDriver 将规范化配置映射到具体 Runtime，不重新发现 Server、不选择 `latest`，也不把 Secret 写入 RuntimeEvent、Artifact 或日志。

## 6. ResourceRequirements

| 字段 | 含义 | 使用位置 |
| --- | --- | --- |
| cpu | CPU request/limit | Kubernetes 调度和容量展示 |
| memory | 内存 request/limit | Kubernetes 调度和 OOM 风险控制 |
| gpu | GPU 数量、类型或能力 | 仅在 Runtime 或模型确实需要时声明 |
| architecture | amd64、arm64 等允许架构 | 防止制品与节点不兼容 |
| node_selector | 节点标签约束 | 硬件、拓扑或隔离放置 |
| tolerations | Toleration 集合 | 专用或受保护节点 |
| storage | 临时与持久存储需求 | WorkspacePort 内置实现选择与调度 |

资源不足时 AgentRun 等待调度，不转化为业务失败，也不能选择 Team 候选池或 LockedComponentSet 之外的 Runtime 绕过约束。

## 7. Runtime SDK

Runtime SDK 是通用、进程内代码接口，不是 Service。它只拥有 Runtime、RuntimeRequest、RuntimeSession、Turn、Capability、RuntimeInstructionSet、PermissionRequest、RuntimeEvent、RuntimeResult 与进程生命周期；不包含 AgentRun、Workflow、Project、Platform API Client、Kubernetes、数据库、持久化、ACK、重试或产品授权。

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

调用方只调用一次 `Execute`。正常成功时，`Events` 持续输出事件并在 Runtime 结束时关闭，`Result` 随后只返回一个终态值；取消和超时只通过调用方提供的 `ctx` 传播。取消后 SDK 停止向调用方转发普通事件，但继续消费 Driver 的事件与终态通道。Driver 启动子进程时，必须由唯一 owner 等待子进程退出；该 `Wait` 未确认时，SDK 不发布终态或 `Result`，调用方保留原 owner。`Wait` 确认后，SDK 在现有 20 秒期限内等待通道关闭；期限届满则发布 `driver-channels-not-closed` unknown 终态，调用方不得报告成功或调用 `CompleteAgentRun`。RuntimeSession 是内存对象，不是 Version、数据库表或产品 Session 聚合。

`RuntimeRequest` 字段：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `instructions` | 运行时指令集合 | 是 | 使用 RuntimeInstructionSet；不携带 OAC 领域类型或来源文件路径 |
| `workspace` | 工作空间输入 | 是 | 根路径、读写范围、基线摘要和已挂载输入 |
| `skills[]` | Skill 物化输入 | 否 | 精确内容摘要、说明和只读挂载路径 |
| `model` | 模型调用输入 | 是 | API Type、实际模型名称和已解析非敏感配置 |
| `mcp_servers[]` | MCP Server 输入 | 否 | 已规范化 Transport、非敏感配置与临时注入位置 |
| `session` | 会话策略 | 是 | `new\|resume` tagged union；恢复时可携带厂商 Session ID |
| `driver_parameters` | Driver 原生参数 | 否 | 必须通过当前 RuntimeDriver Schema 校验 |
| `limits` | Runtime 执行限制 | 是 | Timeout、最大轮次等；Kubernetes 资源限制不进入 SDK |

Open Agent Cluster 以 YOLO、bypass 或 never-confirm 模式启动 Runtime。PermissionRequest 保留为 SDK 面向其他消费者的通用能力，但 OAC 不为每个工具调用创建人工审批状态机。

### 7.1 RuntimeInstructionSet

RuntimeRequest.instructions 使用一个 RuntimeInstructionSet。它是一次 Runtime 启动的嵌套不可变 DTO，不是 AgentDefinition、Artifact、Version、Repository 文件或独立持久化对象。

`InstructionContent`：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `language` | 指令语言 | 是 | 用于展示和 Runtime 编码选择，不用于推断模型能力 |
| `content` | 指令正文 | 是 | 规范化文本；不能为空，不包含 Secret |
| `digest` | 指令内容摘要 | 是 | 用于防漂移、接口检查和运行证据 |

`RuntimeInstructionSet`：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `agent_instructions` | Agent 稳定角色指令 | 是 | 来自本次 Run 固定的 AgentDefinition.instructions；SDK 不理解该来源类型 |
| `collaboration_bootstrap` | Agent 协作启动指令 | 是 | 告知 Agent 使用 `oacok work show/guide/read/submit`；SDK 不调用这些 Platform API |
| `workspace_instructions` | 当前工作空间适用规则 | 否 | 由 OAC 调用方从明确选择的规则内容映射；SDK 不扫描 Repository 猜测 |
| `instruction_set_digest` | 指令集合摘要 | 是 | 按固定字段顺序、显式缺省标记和规范化内容计算 |

RuntimeDriver 必须完整物化每个存在的层。Codex 使用每次 Run 的 developer instructions/App Server 等价输入；Claude Code-compatible Driver 使用 system-prompt append 或等价输入；其他 Runtime 使用原生 system/developer instruction API，确无文本 API 时才可在 Repository 外运行时临时目录使用只读文件。Driver 不得创建或覆盖 Repository `AGENTS.md`、`CLAUDE.md`，也不得把厂商文件路径放入公共 SDK。

`RuntimeEvent.started` 必须回显 `agent_instructions_digest`、`collaboration_bootstrap_digest`、条件性的 `workspace_instructions_digest` 和 `instruction_set_digest`，但不得回传正文、临时文件路径或 Secret。OAC Platform Core 在接受事件时与当前 AgentRun 启动输入逐项比较。

## 8. RuntimeEvent 与 RuntimeSession

RuntimeDriver 把厂商原生事件转换为事件草稿，Runtime SDK Core 统一生成通用信封：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `event_id` | Runtime 事件全局唯一身份 | 是 | SDK Core 生成；同一事件重放时保持不变 |
| `stream_id` | 本次 Runtime 事件流身份 | 是 | SDK Core 在 Execute 时生成；等于 RuntimeSession.StreamID |
| `sequence` | 事件流内严格递增序号 | 是 | SDK Core 从 1 开始分配 |
| `schema_version` | RuntimeEvent Schema 版本 | 是 | SDK Core 生成；不支持主版本时拒绝消费 |
| `occurred_at` | Runtime 观察时间 | 是/可 unknown | Driver 提供并由 SDK 规范化；不能冒充平台接收时间 |
| `event_type` | 结构化事件类型 | 是 | `started\|message\|thinking\|tool_call\|tool_result\|command\|file_change\|memory\|mcp\|usage\|status\|heartbeat\|error\|terminal` |
| `correlation` | Session、Turn、Message、Tool Call 关联身份 | 否 | 尽可能保留厂商稳定 ID，不可证明则缺省 |
| `caused_by_event_id` | 直接因果事件身份 | 否 | 只指向当前 stream 中已知事件 |
| `payload` | 类型化事件载荷 | 是 | event_type 决定 Schema；普通大小正文优先内联 |
| `extensions` | 厂商扩展字段 | 否 | 只保留允许的非敏感原始字段，不改变通用语义 |

Thinking 只表示 Runtime 明确暴露的 reasoning summary，不采集或推断隐藏思维链。Tool call/result 使用稳定 tool_call_id；message 分块使用稳定 message_id；command 事件必须保留命令、stdout/stderr 分块、退出码和耗时可用性。`memory` 只记录 Runtime 原生明确暴露的 `read|write|search|delete` 操作、memory identity/scope、outcome 与经授权脱敏的摘要；Runtime 不支持时保持 unavailable，不能扫描隐藏 Session 状态补造事件。

一个 OAC AgentRun 恰好对应一次 Execute、一个 RuntimeSession 和一个 stream_id。首个事件必须是 `started@sequence=1`；Platform Core 以 Run revision CAS 固定该 stream，后续 Append/Complete 必须匹配，不同 stream 返回 Conflict。AgentRun Job 进程失败后重新执行必须创建新 AgentRun/RuntimeSession；恢复同一厂商 Session ID 只表达上下文连续性，不复用旧 stream_id 或 sequence。

## 9. OAC Agent Job 与 AgentRunEvent

AgentRun Job 主程序属于 OAC 产品执行代码，不属于 Runtime SDK。它读取已挂载启动数据、映射 RuntimeRequest、调用 Execute 一次，并循环消费 RuntimeSession.Events：

```text
RuntimeSession.Events
→ bounded batch
→ Platform Core AppendAgentRunEvents
→ persisted AgentRunEvent / UsageRecord / Artifact
→ persisted cursor notification
→ accepted stream_id + sequence
```

AgentRun Job 持有 run-scoped Platform 凭证并负责批处理、网络重试、ACK 和有界背压。SDK、RuntimeDriver 与 RuntimeEvent 中不能出现 Platform endpoint/token、HTTP Client、agent_run_id、received_at 或 ACK。Tool 边界、error、terminal 立即 flush；文本/Thinking 可以按短时间或大小阈值合并传输，但不能丢失顺序和关联身份。事件持久化失败时，Job 取消 Runtime 并在有界事件 drain 到期后将结果标记为 unknown；Job 仍等待 SDK 结果，以免在 Driver 的 owning `Wait` 未确认时退出。若 `Wait` 已确认但 Driver 通道超过 SDK 20 秒关闭期限仍保持打开，Job 返回 unknown 且不调用 `CompleteAgentRun`；pending Wait 仍由原 owner 持有。

Platform Core 从认证路径和 run-scoped 凭证推导 agent_run_id，并生成 received_at。AgentRunEvent 扁平复用 RuntimeEvent 通用字段，再增加：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `agent_run_id` | 所属 AgentRun 身份 | 是 | 服务端推导，事件正文不能指定其他 Run |
| `received_at` | Platform Core 接收时间 | 是 | 服务端时钟生成 |
| `critical` | 是否要求终态前确认持久化 | 是 | Platform Core 根据事件类型和当前合同推导，不属于 SDK |

产品持久化不嵌套第二份 `runtime_event`，也不重新生成 event_id、stream_id 或 sequence。Platform Core 先把首个 `started@sequence=1` 的 stream_id CAS 绑定到 AgentRun；唯一约束为 `(agent_run_id,event_id)` 和 `(agent_run_id,stream_id,sequence)`。相同身份相同 digest 幂等，相同身份不同 digest 或不同 stream Conflict，乱序/缺口显式等待或拒绝。

普通 payload 内联保存；只有超过限制的 Tool/Command 输出、Diff 或原始厂商载荷才保存到 Artifact Storage，事件中保存语义明确的 Artifact ID 和 digest。事件和相关 Usage/Artifact/Observation 元数据先持久化，再发布 SSE/WebSocket 游标通知；断线后按 `stream_id + after_sequence` 查询历史事实。

RuntimeSession.Result 只表达 Runtime 终态、厂商 Session ID、最终可见输出摘要和错误分类。业务 WorkSubmission 通过 AgentWorkChannel / `oacok work submit` 进入 Platform Core，不能由 RuntimeResult 绕过 Submission Validator、Contract、Sensor、Gate 或 Decision。

## 10. RuntimeDriver 原生协议

RuntimeDriver 是实现 `runtime.oac.dev/v1` 的 ExtensionPackage 角色，不是长期 Service。该 ExtensionPackage 的通用 `entrypoint` 对 Runtime 接口固定解析为精确 AgentRun Job OCI 镜像与启动命令；镜像内薄的 OAC Job 主程序与 Driver 实现链接在同一进程，再由 Driver 启动厂商子进程或连接厂商 Runtime。OAC 不增加 Driver RPC、Service URL、服务发现、Replica 或 HA 配置，也不建立新的 Runtime Host/Runner/Bridge 概念。

| RuntimeDriver | 原生连接协议 | 生命周期映射 | 首版定位 |
| --- | --- | --- | --- |
| Codex | `codex app-server`；默认 stdio 上的 Codex App Server JSONL 消息协议 | `initialize → initialized → thread/start\|thread/resume → turn/start`；精确方法与事件 Schema 由固定 Driver release 的生成 Schema/Fixture 校验 | 内置 |
| Hermes | `hermes acp`；ACP JSON-RPC over stdio | initialize、Session create/resume、prompt、update、cancel 等能力按固定 ACP/Driver release 的检查 Fixture 验证，不在平台领域合同里复制易变方法清单 | 内置 |
| Claude Code-compatible | `claude -p --input-format stream-json --output-format stream-json --verbose`；stream JSON/NDJSON，不是 JSON-RPC | 新执行或 `--resume`；RuntimeInstructionSet 映射、事件类型和恢复能力由固定 CLI/Driver release 检查 | 接口 Fixture/可安装目标 |
| OpenClaw | `openclaw acp`；Gateway-backed ACP over stdio | initialize、Session、prompt、stream update、cancel 和可用 Usage/Tool 事件按固定 OpenClaw/Driver release 检查；缺失或近似字段保持 `unknown` | 可安装目标 |

RuntimeDriver ExtensionPackage 不提供 `execution_mode=service|trusted-in-process` 用户选择。Runtime 的本地、Gateway 或远程连接差异由 Driver 内部实现；Driver 不拥有 AgentWorkChannel、Model Catalog、Credential Store、AgentDefinition、ProjectRules、Repository 指令文件或 Platform API Client。

## 11. UsageEvent

Usage 作为 `event_type=usage` 的 RuntimeEvent 进入 AgentRunEvent，并一对一形成 Governance UsageRecord。RuntimeDriver 从 Provider/Runtime 原生结构化事实映射；AgentRun Job 只上传；Platform Core 从当前 AgentRun 固定事实补充 OAC 归因。不得按每个流式 Token delta 写数据库，也不得从日志或 Prompt 长度估算。

### 11.1 Common Usage Envelope

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `usage_kind` | 使用类型 | 是 | `model-call\|tool-call\|runtime` 三选一；Runtime SDK 不产生 Component/resource/external-service 类型 |
| `source_event_id` | 来源事件身份 | 是 | 缺省使用当前 AgentRunEvent `event_id`；同一 Run 内幂等 |
| `source_kind` | 数据来源类型 | 是 | `provider-response\|runtime-native\|runtime-measured`；OpenTelemetry Instrumentation 只能映射为其中一种来源，不形成第二权威来源 |
| `observed_at` | 观察时间 | 是 | 由 Runtime/Adapter 显式提供 |
| `payload` | 类型化使用载荷 | 是 | 由 `usage_kind` 决定 Schema；Vendor 原始字段只进入受控 extensions/raw |

Platform Core AgentRun event use case 从当前 AgentRun 固定事实补充 Project、Workflow、WorkUnit、iteration、AgentDefinition、responsibility、RuntimeDriver 和模型连接归因；RuntimeDriver 与 Runtime SDK 不接收或理解这些产品对象。

### 11.2 Model Call Usage

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `model_call_id` | 本次模型调用身份 | 是 | 在当前 Run 内稳定；一次 Provider 请求只产生一次终态 Usage |
| `provider_request_id` | Provider 请求身份 | 否 | Provider 返回时保存；不作为 OAC 主键 |
| `request_model_id` | 请求模型名称 | 是 | RuntimeDriver 实际发送值 |
| `response_model_id` | 实际响应模型名称 | 否 | Provider 未返回时为 unknown，不复制 request model 冒充 |
| `api_type` | 模型 API 接口类型 | 是 | 例如 openai-responses、anthropic-messages |
| `started_at` | 调用开始时间 | 是 | 用于总延迟 |
| `first_output_at` | 首个输出到达时间 | 否 | 非流式或 Runtime 不暴露时为 unknown，用于 TTFT |
| `completed_at` | 调用完成时间 | 是 | 错误调用也必须有终止观察时间 |
| `input_tokens_total` | 输入 Token 总量 | 否 | Provider 未报告时 unknown |
| `input_tokens_cache_read` | 缓存读取输入 Token | 否 | 是 input total 子集，不能重复相加 |
| `input_tokens_cache_write` | 缓存写入输入 Token | 否 | 是 input total 子集，不能重复相加 |
| `output_tokens_total` | 输出 Token 总量 | 否 | Provider 未报告时 unknown |
| `output_tokens_reasoning` | Reasoning 输出 Token | 否 | 是 output total 子集，不能重复相加 |
| `modality_tokens[]` | 多模态 Token 明细 | 否 | 明确 text/audio/image、input/output 与数量 |
| `retry_count` | Runtime 内重试次数 | 是 | 无重试为 0；只统计当前模型调用内部重试 |
| `outcome` | 调用结果 | 是 | succeeded、failed 或 cancelled |
| `error_type` | 低基数错误类型 | 失败必需 | 不保存敏感错误正文 |
| `finish_reasons[]` | Provider 停止原因 | 否 | 保留结构化枚举/字符串，不推导业务成功 |
| `provider_cost` | Provider 报告成本 | 否 | Decimal amount + currency；Provider 未报告时不猜测 |

`model_provider_connection_id` 等 OAC 产品归因由 Platform Core 从当前 AgentRun 固定事实补充到 UsageRecord，不能放入 Runtime SDK payload。TTFT 只由 `first_output_at - started_at` 计算；Provider latency、Runtime overhead 或模型吞吐只有在相应边界时间可证明时才展示。Prompt、Response 正文、Agent Instructions、私有推理和 Secret 不属于 UsageEvent。

### 11.3 Tool Call Usage

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `tool_call_id` | Tool 调用身份 | 是 | 与对应 tool call/result RuntimeEvent 关联 |
| `tool_kind` | Tool 类型 | 是 | `runtime-native\|mcp\|external` |
| `tool_name` | Tool 名称 | 是 | 用户可读但不包含输入内容 |
| `mcp_server_definition_id` | MCP Server 身份 | MCP 必需 | Runtime-native Tool 不伪造 MCP 身份 |
| `started_at / completed_at` | 开始与完成时间 | 是 | 用于 Tool duration |
| `outcome / error_type` | 调用结果与错误类型 | 是/失败必需 | 不把 Tool 成功等同 WorkUnit 成功 |
| `external_units[]` | 外部计量单位 | 否 | 只保存 Provider 可证明的单位和数量 |

### 11.4 Runtime Usage

Runtime Usage 只记录 Runtime 启动、ready、结束时间和 outcome，用于启动延迟与总执行时间；模型与 Tool 子调用继续由各自 UsageEvent 记录，不能把其 Token、成本或 duration 再复制进 Runtime Usage。

### 11.5 Mapping Priority 与 OpenTelemetry 边界

数据优先级固定为：Provider 原始 Usage/响应字段 > Runtime 原生结构化事件 > RuntimeDriver 可证明的时间测量。字段缺失时保持 unknown，不从日志、当前价格、Prompt 字符数或另一个模型估算。

首版不依赖 OpenTelemetry SDK、Collector 或 OTLP。RuntimeDriver 可以参考 GenAI Semantic Conventions 设计字段映射，但必须输出上述 RuntimeEvent.usage；未来外层 OTLP Export Adapter 只能从已经提交的 OAC Event/UsageRecord 异步导出，失败不影响 AgentRun，且 OTel Span/Metric 不回写为第二条 UsageRecord。需要接收原生 OTLP 的第三方 Runtime 属于后续显式 Runtime SDK 能力，仍必须经过 Agent Job 事件上传和 Platform Core 的 sequence、idempotency 与 critical-tail 规则。

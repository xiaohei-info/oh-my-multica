# Reference 共同约定

本文定义 Open Agent Cluster References 的权威边界、公共记法、领域命名和字段说明规则。完整导航由 [docs/README.md](../README.md) 维护。

## 1. 文档层级

- [业务解决方案设计](../open-agent-cluster-business-solution-design.md)说明业务问题、目标用户、业务能力、业务闭环、范围和价值；
- [概要设计](../design/open-agent-cluster-overview-design.md)说明领域边界、功能架构、系统架构、模块协作和非功能目标；
- `docs/design/detailed/` 下的文档说明各领域内部模型、接口、流程、失败和恢复；
- 本目录说明跨模块共享字段、枚举、Schema 语义、幂等、版本和兼容约束；
- `docs/deploy-ops/` 说明物理拓扑、部署、升级、监控、故障恢复和数据保护责任边界；
- `docs/delivery/` 说明项目实施、风险、验收和公开验证；
- `docs/archive/` 只保存只读历史，不是当前设计依赖。

正文与 Reference 不一致时，应视为文档缺陷并同时修正，不能依赖隐式优先级猜测实现。

## 2. 领域命名

术语必须先说明所属领域，再说明对象所处阶段。跨领域正文不得单独使用无法判断归属的 `Spec`、`Proposal`、`Revision`、`Record` 或 `Snapshot`。

| 领域 | 推荐术语示例 |
| --- | --- |
| Solution Definition | SolutionPackage、BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务节点/Gate、声明式领域体验、ComponentBundle |
| Project & Team | Project、ProjectSolutionSetup、AgentTemplate、AgentDefinition、TeamBindingSnapshot |
| Agent Capability Catalog | ModelProviderConnection、ModelCatalogSnapshot、ModelProviderProbe、McpServerDefinition、McpCapabilitySnapshot |
| Workflow | Workflow、WorkUnit、Run、Artifact、Observation、GateResult、Decision、ChildWorkflowLink |
| Orchestration | PlanningRules、PlanDraft、ValidatedPlan、PlanReviewResult、Plan、LifecycleAction |
| Agent Execution | AgentRun、RuntimeBinding、Session、Turn、AgentRunEvent |
| Workspace | WorkspaceRequest、WorkspaceHandle、WorkspacePort、Capability |
| Extension | PackageEnvelope、ComponentInstall、SolutionPackage、ContentPackage、ExtensionPackage、ExtensionCatalog、LockedComponentSet、ComponentRun |
| Governance | RequestContext、AuthorizationDecision、ExecutionAdmissionDecision、AuditExportTarget、CredentialBinding、CredentialRotationRecord、AuditEvent、UsageRecord |
| Software Delivery | Requirement、AcceptanceCriteria、AcceptancePlan、ProjectRules、SolutionDesign、TechnicalOverview、DetailedDesign、ReviewResult、AcceptanceResult、Release、Deployment |

命名规则：

- `Draft` 表示尚未通过本领域校验的输入；
- `Validated` 表示已通过静态校验但尚未生效；
- `Version` 只用于可独立安装发布包的语义版本，或 `api_version`、`schema_version`、`protocol_version` 等兼容性标量；
- `Revision` 表示可变对象的内部 CAS 修订号，不是用户可管理的业务版本，也不单独形成聚合；
- 提交或批准后不可变的业务记录直接使用对象名，例如 `Artifact`、`Plan`、`AgentDefinition`，通过自身 ID、digest 和可选前序 ID 表达历史；
- `Result` 表示程序、Agent 或人对明确 subject 的结构化判断；
- `Decision` 只表示授权用户或系统 Policy 作出的正式决定；
- `Action` 表示 Kernel 输出、由 Host 应用的固定生命周期动作；
- `Run` 表示一次具有独立幂等、状态和恢复语义的执行；
- `Snapshot` 只表示某一边界已复制并固化的值，例如 `Workflow.spec.team_binding_snapshot`，不是另一个可独立维护的配置对象；
- package/module 命名空间提供最终归属，例如 `orchestration.PlanDraft` 与 `workflow.Artifact`。

## 3. 通用记法

### 3.1 四类变化机制

| 类别 | 适用对象 | 身份与变化规则 | 例子 |
| --- | --- | --- | --- |
| 可安装发布包 | 可被独立安装、校验、并行升级和复用的内容 | 稳定 package ID + SemVer + artifact digest；更新发布新版本 | SolutionPackage、ContentPackage、ExtensionPackage；AgentTemplate 与 SkillPackage 属于 ContentPackage，RuntimeDriver 等公开接口实现属于 ExtensionPackage |
| 不可变业务记录 | 正式提交、评审或批准后必须精确追溯的记录 | 每次正式提交创建新 ID，保存 content digest 和可选 previous/parent ID；不要求 SemVer，也不使用 `Version` 后缀 | Artifact、Plan、AgentDefinition、Decision、ReviewResult |
| 可变配置 | 用户期望直接“保存修改”的当前配置 | 稳定 ID 或所属 Scope identity + 内部 `revision` + 适用摘要 + AuditEvent；CAS 防止覆盖，并可从审计历史恢复 | Project、ModelProviderConnection、McpServerDefinition、CredentialBinding、AuthenticationMethod、Policy Binding、AuditExportTarget |
| 执行快照 | 一次 Workflow 为确保执行与重放稳定而固化的全部输入 | 根 Workflow 创建时解析、复检并复制到 `Workflow.spec` 的不可变创建时快照字段；后续配置变化不回写这些字段 | Solution release、Project 配置、Team bindings、LockedComponentSet、OrchestrationDefinition digest |

只有第一类使用用户可见的语义版本。第二类的“创建新记录”发生在正式提交或发布边界，不发生在每次编辑、自动保存或字段修改。第三类直接更新当前对象并增加内部修订号。第四类是执行边界，不再要求上游每个配置对象都先生成不可变版本。

OAC 系统发行版是部署发行物，不属于上述产品领域对象，也不进入 Package & Extension Governance。Installer CLI 的 `target_release` 必须固定精确 SemVer 与 artifact digest；系统发行版可以同时包含 OAC 服务镜像、部署清单/CRD、数据库迁移、同步安装工具和嵌入镜像的 Kernel/SDK。它不得被用来表达 SolutionPackage、ContentPackage、ExtensionPackage、Software Delivery Release、Project 配置、Workflow 快照或外部 SDK 依赖版本。文档中出现裸 `release` 时必须通过所属模块消歧，例如 `OAC system release`、`Software Delivery Release` 或 `package release`。

活动 Workflow 不存在“替换执行快照”操作。若同一业务目标必须采用新的 Project 配置、Solution、Harness/Policy 语义或 Package release，系统创建一个具有新身份和新 `Workflow.spec` 创建时快照的后继根 Workflow；前序 Workflow 的创建时快照保持原样。动态 WorkUnit 只因当前没有合格执行者而进入 Waiting 时，用户可以通过绑定精确 `MissingExecutor` Observation 的 `Answer` Decision 选择一个满足既有要求的 AgentDefinition；Platform Core 只向 `Workflow.spec.decisionIds` 追加精确 Decision ID，不改写创建时快照或 TeamBindingSnapshot，也不要求创建后继 Workflow。后继关系只表达来源与审计，不是父子 Workflow 控制关系，也不形成迁移状态机。

### 3.2 记法

| 记法 | 含义 |
| --- | --- |
| `ID<T>` | 某类对象的稳定标识；不预先限定 UUID、ULID 或物理格式 |
| `ExactComponentRef<T>` | 固定一个可安装发布包的精确版本值；包含 package 稳定身份、SemVer 和 artifact digest，公开字段必须使用目标命名，例如 `solution: ExactComponentRef<Solution>` |
| `Revision` | 可变对象的内部单调 CAS 修订号；只用于并发控制和说明某次执行实际读取了哪次配置，不是业务版本 |
| `ArtifactID` | 一个不可变 Artifact 的稳定标识；成果、正文和正式规范使用目标明确的 `*_artifact_id` 表达 |
| `BindingID<T>` | 已解析、已授权或已配置绑定的稳定标识，例如 `credential_binding_id`、`harness_binding_id` |
| `Locator<T>` | 指向外部位置的可审计地址；按协议使用 `*_uri`、`*_path` 或 `*_endpoint`，不能伪装为领域对象身份 |
| `Handle<T>` | 内部实现拥有的运行时不透明句柄，例如 `workspace_handle`、`execution_handle`；上层不得解释其内部格式 |
| `WorkflowKey` | 跨 Kubernetes 对象边界定位 Workflow 的结构化身份，固定包含 `namespace` 与 `name`；在已知 Namespace 的 CR 内仍直接使用 `metadata.name` |
| `SubjectKey` | 多态业务主体值，至少包含 `kind`、`id`，需要时包含 `digest`、`revision` 或 package release；用于 Review、Decision、Gate 等必须精确说明主体类型的场景 |
| `Causation` | 因果来源值，至少包含 `kind` 和 `id`；用于表达命令、Action、Observation、Decision 或前序执行的来源 |
| `SemVer` | 语义化版本，例如 `1.2.0` |
| `Digest` | 内容摘要，通常带算法前缀，例如 `sha256:...` |
| `Timestamp` | 带时区的时间点；物理表示在详细设计或实现 Schema 中确定 |
| `Enum` | 有限且语义唯一的值集合，不能由多个布尔字段拼装隐藏状态机 |
| `List<T>` | 有序列表；顺序是否有业务语义由字段说明明确 |
| `Map<K,V>` | 键值映射；键和值的业务语义由字段说明明确 |
| 可空 | 合法对象可以缺省；缺省与空值必须有一致、明确的语义 |

公开领域模型、API DTO、CRD 字段和示例 YAML 不使用通用 `*_ref`、`*_refs`、`*Ref` 或 `*Refs` 字段。内部实现可以定义泛型辅助类型，但不能让它取代业务字段本身的含义。

字段命名规则：

- 稳定对象身份使用 `*_id`，复数使用 `*_ids`；Kubernetes Workflow 在同一 Namespace 内直接使用 `metadata.name`，不再复制 `workflow_id`；
- 不可变业务记录也直接使用 `*_id`，例如 `artifact_id`、`plan_id`、`agent_definition_id`；历史关系使用 `previous_*_id` 或 `parent_*_id`；
- 需要同时固定 package 稳定身份、语义版本和 artifact digest 时，使用目标命名的 `ExactComponentRef<T>` 值对象，例如 `solution`、`extension_package`、`runtime_driver`，而不是并列字段或“通用指针 + digest”；WorkUnit 主执行只保存逻辑 `execution` 要求，不使用 `component_executor` 指针；
- 可变对象统一使用 `revision` 作为 CAS 字段；执行或审计记录需要证明实际读取值时，使用目标明确的 `*_revision` 与 `*_configuration_digest`，不得把 revision 包装成新的 Version 对象；
- package 内部、只随父 package 一起发布的内容使用局部 `*_id + content_digest`，例如 `baseline_plan_template_id`；只有真正独立安装和跨 package 复用的内容才提升为独立 Package 内容，并使用 `ExactComponentRef<T>`；
- 已解析配置或授权关系使用 `*_binding_id`，不得用通用指针字段隐藏它是配置、授权还是运行时选择；
- Package 内文件使用 `*_path`，外部地址使用 `*_uri`，外部系统稳定身份使用 `*_external_id`，Provider 私有运行资源使用 `*_handle`；
- 多态关系使用带类型标签的语义对象，例如 `subject`、`caused_by`、`inputs[]`、`outputs[]` 和 `provenance_inputs[]`，不能退化成无法判断元素类型的通用引用字段；
- 字段必须体现被指向对象的业务类型。不得仅用 `result_id` 表示 Artifact、Observation、Report 或外部结果；应分别使用 `result_artifact_id`、`observation_id`、`report_id` 或明确的 tagged union；
- `api_version`、`schema_version`、`protocol_version`、`compiler_version` 和 Kubernetes `resourceVersion` 是兼容或并发控制标量，不代表新的业务聚合；
- 不允许 package 使用 `latest`、版本范围、隐式默认版本或只有展示名称的解析。需要精确 package 内容时必须固定 SemVer 与 digest。

`ExactComponentRef<T>` 是一个通用值对象，不是新的统一 Component 接口，也不为每个目标再创建一套别名类型。公开字段名负责表达目标，例如 `solution`、`extension_package`、`runtime_driver`；泛型参数负责表达值类型。例如：

```yaml
solution:
  solutionId: software-delivery
  # solutionId：行业 Solution 的稳定身份。
  version: 1.0.0
  # version：本次固定的语义版本。
  artifactDigest: sha256:...
  # artifactDigest：安装包内容摘要，用于防止同版本内容漂移。
```

`SubjectKey` 和 `Causation` 必须使用 tagged union 或等价的判别字段。消费者必须先判断 `kind`，不能根据 ID 前缀猜测对象类型。

## 4. 字段说明规则

每个字段表应回答：

1. 字段表达什么业务事实；
2. 为什么不能临时推导或猜测；
3. 谁产生或固定；
4. 谁消费；
5. 何时必需、何时可空；
6. 版本、幂等、安全和一致性约束是什么。

字段在领域对象中“必需”，不等于用户必须在表单中手工填写。面向配置与目录对象的文档必须区分以下生产责任：

| 生产责任 | 中文说明 | 设计要求 |
| --- | --- | --- |
| `user` | 用户输入 | 只保留无法从上下文、预设、外部响应或策略中可靠得到的业务选择 |
| `context` | 当前操作上下文继承 | 例如当前 Scope、Project、创建入口和目标对象；页面展示但通常不重复输入 |
| `system` | 平台生成 | 例如稳定 ID、CAS revision、digest、审计时间、状态和来源证明；不得伪装成普通配置项 |
| `connector / provider` | 专用 Adapter 或 Provider 推导 | 例如按模型 API Type 选择发现方式、解析外部能力、生成 Secret handle 和脱敏探测结果；不得泄漏为通用领域配置 |
| `policy` | 平台、Scope 或 Project Policy 提供 | 例如 TLS、超时、出站网络和轮换默认值；只有确有场景时才开放受控高级覆盖 |

普通表单遵循“最少必要输入”：先应用预设与上下文，再请求用户补齐仍无法确定的字段。详细设计若同时展示完整持久化对象与用户表单，必须分别标明字段生产责任，不能让实现者根据字段是否存在猜测它是否需要用户填写。

Reference 不是数据库 DDL、CRD OpenAPI Schema、正式 API Schema 或 SDK 类型定义。实现可以选择物理类型和序列化格式，但不能改变已确认的语义与所有权。

## 5. 文档引用规则

- 每组跨模块字段只有一个权威 Reference；
- 设计正文解释边界、协作、取舍、失败和恢复；
- Reference 解释字段、枚举、Schema、幂等与兼容；
- 正文只保留说明设计所需的最小 YAML，完整结构放到对应 Reference；
- 跨 Reference 使用精确链接，不复制定义；
- Reference 开头必须说明负责和不负责的边界。

# 08. Package 与 Extension 详细设计

> 上级文档：[云原生 Open Agent Cluster 概要设计](../open-agent-cluster-overview-design.md)
> 契约参考：[Package 与 Extension 治理](../../references/extension/component-governance.md) · [Harness Extension SDK](../../references/extension/plugin-contracts.md)

![Package Envelope, SDK-Owned Interface Checks, Exact Resolution, and Lifecycle](../../assets/diagrams/ecosystem/component-lifecycle.svg)


## 1. 领域责任

Extension 领域负责可安装 Package 的身份、分发、验证、生命周期、版本锁定和权限。它不拥有 Workflow 控制状态，也不把“一个扩展接口”建模成一整套新的 Component Category。

平台只保留三种 Package：

- `SolutionPackage`：声明行业流程、配置、终态业务路径和可选的声明式领域体验；
- `ContentPackage`：承载 Skill、AgentTemplate 等不可执行内容；
- `ExtensionPackage`：实现一个 OAC 公开扩展接口的可执行扩展。

这里必须区分两个容易混淆的“三”：

- **三种 Package** 是 Application Package family 内的分发格式：Solution、Content、Extension；
- **三类扩展面** 是系统隔离变化的方向：Application Package family、Agent Runtime SDK、Workspace SDK / Port。

两者不是一一对应关系。RuntimeDriver 实现 Runtime SDK 后通过 ExtensionPackage 分发；Workspace 首版只有内部 Port 和内置 Adapter，不是 ExtensionPackage。Package 类型回答“如何发布、安装和固定”，扩展面回答“哪一类变化被什么稳定合同隔离”。

三种 Package 共享最小 Package Envelope、安装生命周期和精确版本治理。`ExtensionPackage` 只声明其实现的 `interface_api`（扩展接口及版本）和 `entrypoint`（启动入口）；接口方法、数据结构、能力发现与安装检查由对应 SDK 提供。平台使用一个 `ExtensionManager` 完成 Verify、Install、Activate、Disable、Revoke 和 Remove，并使用一个 `ExtensionCatalog` 按接口索引已安装实现。

统一的是安装和发现，不是运行调用。Runtime、Harness、Authentication 和 Deployment 仍通过各自类型化 SDK/Port 调用，不创建 `extension.invoke(any)` 或 `component.invoke(any)`。

## 2. 三种 Package 与扩展接口边界

Package 类型回答“如何分发和安装”，扩展接口回答“安装后能够做什么”。两者不能混为一组不断增长的 Component Category。

| Package 类型 | 解决的问题 | 用户通常何时使用 | 是否执行代码 |
| --- | --- | --- | --- |
| `SolutionPackage` | 行业流程、Project/Workflow 配置、Baseline、显式 DAG Orchestration WorkUnit、终态业务节点/Gate 和声明式领域体验如何定义 | 创建行业方案、选择精确 Solution release | 否 |
| `ContentPackage` | Skill、AgentTemplate 等可独立复用内容如何发布和固定 | Settings、Agent Center 或 Solution 引用内容时 | 否 |
| `ExtensionPackage` | 第三方如何实现 OAC 已公开的类型化扩展接口 | 现有实现无法满足 Runtime、Harness、认证或部署接入需求时 | 是，且只能通过所实现接口运行 |

首版正式承诺兼容性的公开 Extension API 只有：

- `harness.oac.dev/v1`：Guide、Sensor、Executor、Trigger 和 EventHandler 等 Harness 扩展；
- `runtime.oac.dev/v1`：Agent Runtime 的 Session、Turn、Event、Cancel 和恢复；
- `authentication.oac.dev/v1`：Local/OIDC/SAML/LDAP 等认证协议实现；
- `deployment.oac.dev/v1`：外部 Kubernetes/CI-CD 的 `start/observe/cancel`。

Workspace、Audit Export 和 Credential Storage 首版保留为平台内部类型化 Port，由内置实现承接，但不作为公开可安装 Extension API。内部存在 Port 不等于平台已经承诺第三方兼容性；只有真实需求和测试基线成熟后才公开对应 SDK。

AuthorizationEngine 与 ExecutionAdmissionEngine 始终是 OAC 内置唯一实现，不属于 Package 或 Extension API。外部系统只能通过平台已经定义的窄端口提供事实，不能替换最终判断。

### 2.1 Solution 与 Extension 的三个层次

| 层次 | 主要定义 | 是否可执行 | 允许改变 | 明确禁止 |
| --- | --- | --- | --- | --- |
| L1 Industry Definition | Solution、Project/Workflow 配置元数据、package-local BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务节点/Gate、WorkUnit Contract、HarnessDefinition，以及独立 ContentPackage 的精确引用 | 否 | 行业成果、固定批准基线、动态编排入口、终态路径和设置体验 | 生命周期状态、LifecycleAction、任意副作用、私有状态机或第二套完成判断 |
| L2 Execution and Harness Extension | WorkUnit `execution` 对 Executor requirement 的定义，HarnessDefinition 对 Harness Extension、独立责任 Run 和受信任纯 GateEvaluator 的精确装配，以及少数领域拥有的窄直接执行 Port | ExtensionPackage 只能按其实现的公开 SDK 执行；独立责任 Run 由 Host 派发 | 企业主执行、工具接入、检查、独立 Review/Acceptance、观察、上下文补充，以及无需编排的单一领域副作用实现 | 直接写 Workflow/WorkUnit、Gate bypass、Harness 隐藏主执行、扩展隐藏调用多 Agent、万能 `invoke(any)` |
| L3 Solution Experience | SolutionPackage 内的声明式表单、页面布局、Renderer 元数据和读模型映射 | 否 | 领域术语、输入体验、结果展示和查询组合 | 任意前端脚本、数据库/CRD/Kernel/Extension Host 访问、第二写路径 |

三层都通过所属 Package 使用统一 Package Governance：Package Envelope、Verify/Install/Activate/Disable/Revoke、精确版本与 digest、Trust、权限求交和 LockedComponentSet。只有 L2 的可执行 ExtensionPackage 实现进入 ExtensionCatalog 并运行对应接口检查；L1/L3 随 SolutionPackage 治理，不单独注册。该底座不表达行业流程，因此不是第四层 Extension。

L3 不是 ExtensionPackage，也不存在 `solution-application` 接口族。领域体验作为 `SolutionPackage.experience` 的不可执行声明内容随精确 Solution release 固定，由平台 Web 内置 `ApplicationHost` 渲染；`ApplicationHost` 不进入 Package Catalog、ExtensionCatalog、LockedComponentSet 或 ComponentRun，也不允许第三方上传可执行前端/后端代码。

AgentTemplate、Skill 等独立 ContentPackage 内容是三层可以固定使用的不可执行 package release；Contract、Guide 和 Renderer Schema 默认作为 Solution package-local 内容按局部 ID/digest 固定，确需跨 Solution 独立安装复用时才放入 ContentPackage。两者都不构成新的控制层或 Component Category。

统一 Settings 中的 Model Provider Connection、Model Catalog、MCP Server Definition 和 CredentialBinding 也不构成第四个可插拔技术边界。它们分别是 Agent Execution Catalog 与 Governance 的配置对象：Platform Core 在 AgentRun 创建时固定模型/MCP 非敏感事实，Execution Host 安全物化 Workspace/Credential，RuntimeDriver 在 AgentRun Job 内转换为厂商原生配置；它们不进入 Harness Host，也不能定义 Workflow 生命周期。

### 2.2 HarnessDefinition 与 Harness Extension

两者不是重复概念：

- `HarnessDefinition` 是围绕 WorkUnit 主执行的声明式装配清单，回答“执行前使用哪些 Guide、执行后运行哪些 Sensor/独立责任 Run、如何 Gate 和返工”；一个 WorkUnit 只固定一个 `harness` 内容绑定；
- Harness Extension 是实现 `harness.oac.dev/v1` 的 ExtensionPackage，回答“具体能力由谁执行”；
- 一个 Harness Extension 可以提供多个 Extension Point；一个 HarnessDefinition 可以组合多个 Extension，并可以包含多个由 Host 派发的 `responsibility-run` Binding；
- HarnessDefinition 本身不可执行，Extension 也不能自行把自己加入 Harness；
- `solution release + harness_definition_id + content/config digests + exact Package releases` 相同的绑定必须产生同样的解析语义，不允许按企业或环境隐藏改变行为。

主执行不属于 HarnessDefinition。PlanDraft 与 WorkUnit Definition 使用唯一 `execution=executor|child-workflow`；HarnessDefinition 只包含四象限、Gate 与返工/收敛。Studio 可以在同一个生命周期编辑器里连续展示两者，但保存到不同字段，不能互相投影或覆盖。

企业只填写 Harness 固定 Schema 已允许的运行参数或 CredentialBinding 时，直接更新当前 Project `solution_setup` 并递增 revision；Workflow 创建时再解析 LockedComponentSet 和执行快照。若改变 package-local Extension 装配、责任 Run、Guide、输出合同、顺序、Required/Optional、配置合同、GateEvaluator、Baseline 拓扑、终态业务路径、审批边界或 WorkUnit Contract，必须发布新的 Solution release；真正独立发布的 Harness/Content 则发布其自己的新 release。任何一类都不能伪装成同一 release 下的环境差异。

### 2.3 单一 Harness 生命周期呈现

Harness 不再额外定义“预览对象”或另一套展示 Schema。Solution Studio 编辑页、不可变 Harness 详情页和 Workflow WorkUnit 运行追踪都从同一个 `HarnessDefinition`、精确 Extension 描述与已有 Run/Observation/Gate 查询结果组装，并按以下固定顺序展示：

```text
执行准备
  → Computational Guide
  → Inferential Guide
主执行
  → WorkUnit.execution：Executor requirement 或 Child Workflow
观察与评估
  → Computational Sensor
  → Inferential Sensor Extension 或 responsibility-run
Gate 判定
  → Required Gate Evaluators
后续处理
  → Kernel rework_policy / convergence_policy
```

生命周期阶段由字段、Binding 类型或 `extension_point` 推导，不保存 `invocation_phase`，也不允许 Extension 或用户自行声明。主执行行从 WorkUnit `execution` 读取，不从 HarnessDefinition 读取。Trigger 和 EventHandler 在同一 Extension 目录中显示自己的“外部事件入口”或“事件响应”位置，但不插入某个 WorkUnit 的 Harness 主顺序。

三个页面复用相同信息集合：能力名称与说明、Extension Point、ExtensionPackage 精确版本与 digest、同阶段顺序、Required/Optional、输入输出说明、最小权限、副作用模式、失败与恢复方式。编辑页只是在相同位置增加选择、排序和配置控件；详情页全部只读；运行追踪只附加 ComponentRun、AgentRun、human Run、Observation 与 GateResult 的实际状态，不改变行的身份和含义。

`responsibility-run` 不是 Harness Extension。它只声明对当前主执行结果进行独立评审/判断的责任要求、Guide 和结构化输出合同，由 Kernel `StartReview` 与 WorkflowOrchestrationHost 显式派发。它在同一生命周期编辑器中显示“责任 Run”来源、Required、责任说明、独立性、输入输出和返工规则，不显示虚构的 Extension ID。Sensor Extension 仍不得调用 `agent.invoke`；Reviewer `executor_key` 由 Kernel Runtime 从 Workflow 固定候选快照匹配，Platform Core 只负责精确对象解析、物化与治理。需要执行完整 E2E 业务旅程或企业独立验收时，应创建普通显式 Acceptance WorkUnit，以自己的主 `execution` 产出 AcceptanceResult；不得把多步骤验收隐藏成 Acceptor `responsibility-run`。

## 3. Extension API 与实现

公开 Extension API 是平台承诺兼容性的类型化接口，不是新的 Package 类别。第三方引用对应 SDK，实现接口后发布一个 `ExtensionPackage`。

| Extension API | SDK 调用方 | 第三方实现角色 | 安装时检查来源 |
| --- | --- | --- | --- |
| `harness.oac.dev/v1` | Harness Host | Harness Extension | Harness SDK 自带检查套件 |
| `runtime.oac.dev/v1` | AgentRun Job | RuntimeDriver | Runtime SDK 自带检查套件 |
| `authentication.oac.dev/v1` | Governance Authentication Host | AuthenticationDriver | Authentication SDK 自带检查套件 |
| `deployment.oac.dev/v1` | Deployment Host | DeploymentDriver | Deployment SDK 自带检查套件 |

`RuntimeDriver` 是 Runtime SDK 的实现，不是 Runtime SDK 的替代品。接入新的 Agent Runtime 时，第三方引用 Runtime SDK 并实现 `Execute(ctx, RuntimeRequest) → RuntimeSession`；只有新的能力能够形成跨 Runtime 的通用语义时，才升级 Runtime SDK 本身。AgentRun Job 在进程内调用 Driver 一次并消费 RuntimeSession.Events，Driver 再使用 Codex App Server、Hermes ACP、Claude stream-json、OpenClaw ACP 等厂商原生协议。Runtime SDK 只传结构化 RuntimeInstructionSet 和协议中立运行输入；第三方实现不能要求 Platform Core 提供 `agents_md_path`、`claude_md_path`，不能改写用户 Repository 的指令文件，也不能包含 Platform API Client。

RuntimeDriver ExtensionPackage 复用通用 `entrypoint` 字段，但对 `runtime.oac.dev/v1` 的解释固定为“精确 AgentRun Job OCI 镜像 + 启动命令”：镜像内薄的 OAC AgentRun Job 主程序与 RuntimeDriver 实现链接在同一进程，Driver 再连接厂商 Runtime。平台可提供主程序构建模板，但不把它注册成 Extension API、Host、Service 或 Package 类型。该入口不暴露 `execution_mode=service|trusted-in-process` 用户选项，不配置 Service URL、服务发现、Replica 或 HA；Runtime 的 local/gateway/remote 差异由 Driver 内部处理，不改变 AgentRun Job 与 SDK 合同。SDK Core 统一生成 RuntimeEvent 的 `event_id + stream_id + sequence + schema_version`，调用方不重复实现；AgentRun Job 只在 SDK 外负责 Platform 上传、ACK、重试和背压。

`DeploymentDriver` 只实现 `start/observe/cancel`，用于用户已确认的单次外部 Kubernetes/CI-CD Deployment。普通 Project 用户只配置目标，不直接选择 Package ID、版本或 digest。Platform Core 的 `StartPromotion` Use Case 在命令提交时解析并固定精确 Driver，Deployment Host 在 `oac-execution-host` 内执行；该调用不进入 Workflow `LockedComponentSet`，不创建 WorkUnit/ComponentRun，也不调用 Harness Host、Workflow Controller 或 Kernel。

Deployment SDK 的接口检查必须证明：同一 `deployment_id + request_digest + external_run_id` 的 `observe` 不产生新部署副作用；`cancel` Accepted 不冒充终态；无法证明终态时报告 `Unknown`；只有不存在未确认活动副作用时才报告 `Failed`；`Pending|Running|Unknown|Cancelling` 期间拒绝同一 target concurrency key 的新 start。Retry 和部署旧 Release 不由 Driver 内部循环完成，而是由上层以新的不可变 Deployment 再次调用 `start`。

ModelProviderConnection 和 McpServerDefinition 是 Platform Core 的可变 Agent Capability Catalog 配置对象，不是 Package，也不进入 LockedComponentSet。模型发现/测试 Connector 与 MCP Probe 是 Agent Execution 模块的内部 Adapter，不作为 ExtensionPackage。

## 4. Package Envelope

三种 Package 共享最小 Envelope。Package 作者只提交：

- `package_type`：包类型，固定为 `solution|content|extension`；
- `package_id`：稳定包身份；
- `package_version`：精确语义版本；
- `descriptor_path`：Solution/Content 的包内描述文件路径，Extension 可省略；
- `interface_api`：ExtensionPackage 实现的接口及版本，其他类型省略；
- `entrypoint`：ExtensionPackage 启动入口，其他类型省略；
- `origin` 与 `package_source`：来源声明；
- `signature/provenance`：签名与来源证明。

SkillPackage、承载 AgentTemplate 的发布包都是 `package_type=content` 的类型化 ContentPackage；RuntimeDriver、AuthenticationDriver、DeploymentDriver 都是 `package_type=extension` 的类型化 ExtensionPackage 实现。具体名称用于领域合同和 `ExactComponentRef<T>` 的类型约束，不增加新的 Package 类型、Catalog 或安装生命周期。

`artifact_digest` 由安装工具对完整 Package 计算并验证，不要求作者手工填写或猜测。Platform Core 验证后在 ComponentInstall 中分配生效 `trust_tier` 与 `lifecycle_policy`；Package 不能自行声明或提升这些治理结果。

## 5. Package 与 Bundle

Package 是单个 Solution、Content 或 Extension 的分发制品。ComponentBundle 是多个 Package 的原子分发容器；它不是第四种 Package，也不是运行时组件类别。

Bundle 只负责：

- 成员清单；
- Package 完整性；
- 安装顺序；
- 原子成功或整体失败；
- 来源和签名。

Bundle 不拥有：

- Runtime 权限；
- Component 状态；
- 调用接口；
- 依赖求解；
- Workflow 绑定；
- 统一生命周期状态机。

安装成功后所有成员进入统一 Package Catalog；其中 ExtensionPackage 额外按 `interface_api` 建立 ExtensionCatalog 索引。

## 6. Package 生命周期

| 操作 | 语义 |
| --- | --- |
| Install | 校验并保存 Package，但不一定允许新绑定 |
| Activate | 允许新 Workflow 解析和调用 |
| Disable | 可逆地阻止新解析和新调用 |
| Revoke | Fail Closed，拒绝创建、恢复和 Retry，并尝试取消活动调用 |
| Remove | 在无引用和策略允许时删除安装 |
| Upgrade | 新版本并排安装，不原地替换 |

Disable 与 Revoke 必须分开。Disable 用于正常治理；Revoke 用于安全撤销。

上述表格是 Component Governance 的内部生命周期，不是要求用户依次执行的向导。公开 Web/API/`oactl` 默认只暴露一个 `InstallAndActivatePackage`（安装并启用 Package）用例：管理员选择一个精确 Package/ComponentBundle 后确认一次，平台内部顺序执行 Verify、Install 和 Activate，并把每个阶段显示为进度与诊断。第三方 Package 和内置 Embedded Bundle 使用同一个用例。

Solution 作者同时具有 `solution.publish`（发布 Solution）和 `package.activate`（启用 Package）权限时，可以调用 `PublishAndActivateSolutionPackage`（发布并启用 Solution）。Platform Core 必须在任何写入前校验两项权限，再执行 `PublishSolutionPackage → InstallAndActivatePackage`；前端不得自己串联两个私有接口。发布已经成功而后续安装失败时，Package release 保持不可变且 inactive，不回滚或覆盖其 SemVer/digest。ComponentBundle 的 Required 成员必须全部通过后才原子进入 Active，禁止出现一部分成员已可选、另一部分失败的半激活状态。

活动 Workflow 固定 LockedComponentSet。Package 升级、禁用或替换不修改既有集合；同一目标需要采用新集合时，必须经过影响分析和 Decision，再创建后继根 Workflow 与新的 LockedComponentSet。

## 7. ExtensionManager、Catalog 与类型化调用

平台只维护一条安装治理链：

- `ExtensionManager`：验证 Package、运行接口检查并控制安装生命周期；
- `ExtensionCatalog`：按 `interface_api + package_id + package_version + artifact_digest` 索引已安装实现；
- Package Resolver：为 Project、Workflow、AgentDefinition 或直接 Deployment 固定精确 Package；
- 各公开 SDK：拥有自己的类型化接口、能力发现和检查套件。

运行调用不经过 ExtensionManager 的万能入口。AgentRun Job、Harness Host、Authentication Host 和 Deployment Host 分别通过对应 SDK 获得类型化实现；ExtensionManager 只负责安装、解析和生命周期，不理解 Session、Harness、认证或部署业务语义。

Builtin 通过 Embedded Bundle 安装，并使用与第三方相同的：

- Verify；
- Install；
- Activate；
- Resolve；
- Interface Verification。

运行链路中不允许 if builtin 分支。Builtin 的不可禁用或不可卸载只由 Lifecycle Policy 表达。

### 7.1 Skill Import 与 Content Catalog

Skill 管理是 ContentPackage 的用户入口，不新建 `SkillPlugin` 或独立执行 Host。导入链路固定为：

```text
GitHub Repository / Local Upload
→ Skill Import Use Case
→ static validation and digest
→ ContentPackage Envelope
→ Verify / Install / Activate
→ Content Catalog
→ AgentDefinition or Harness Guide exact selection
```

GitHub 导入只要求 `repository_uri`；`requested_revision` 与 `subdirectory_path` 都是可选输入，未指定 revision 时 Importer 解析 Repository 默认分支。无论使用默认分支、Branch 还是 Tag，发布前都必须固定为精确 Commit SHA；私有仓库通过 `credential_binding_id` 调用 CredentialBroker，Token 不进入 Package。Local Upload 必须限制文件数量、大小和归档展开规模，拒绝路径逃逸与不允许的符号链接。两种导入都只读取和校验内容，不能执行 `postinstall`、脚本、二进制或包内代码。

Importer 先读取 Manifest/Frontmatter，并按“Manifest → 系统默认 → 用户补充”的顺序确定 Skill 稳定身份、语义版本、名称、说明、入口和兼容性。入口默认检测包根 `SKILL.md`；只有缺少名称/说明/版本或入口无法唯一确定时才暂停导入请求用户处理。来源类型、Commit SHA、文件树 digest、Provenance、版本身份和审计字段由系统产生，不作为普通表单字段。

版本规则：

- Skill 稳定身份与语义版本优先由 Package Manifest 声明，缺失时才由用户确认或补充；
- 同一 `skill_package_id + version` 只能对应一个 content digest；
- 修改 Skill package 内容必须发布新的 SemVer release，旧 release 并排保留；
- AgentTemplate 声明的 `skills[]` 是该模板的完整精确内容依赖，不是供用户重新挑选的推荐列表；每项必须固定 Skill 稳定身份、精确 SemVer 和 artifact digest；
- ComponentBundle 发布 AgentTemplate 前必须解析并校验其完整 Skill 依赖闭包。任一 Skill 未包含、未 Active、digest 不一致或发生稳定身份多版本冲突时，Bundle/模板验证失败，不能只安装可用部分；
- Solution 的 Baseline WorkUnit 不直接引用 AgentTemplate 或 Skill。Solution 作者只在责任岗位选择推荐 AgentTemplate；Resolver 据此解析模板所在精确 ContentPackage 及其 Skill 依赖闭包，不要求作者在 WorkUnit 或 `package_dependencies[]` 中重复列出每个 Skill；
- 普通模板实例化由 Platform Core 根据精确 AgentTemplate package release 重新解析并复制全部 `skills[]`，不能信任浏览器回传的模板 Skill 列表；用户只提交额外 Skill 或显式高级自定义差异；
- AgentDefinition 固定 `ExactComponentRef<SkillPackage>`，不会随目录“最新 release”漂移；
- Disable/Revoke 阻止新选择或执行，但不改写历史 Workflow 的来源证明；
- Skill 只提供不可执行 Instructions/流程知识；若需要网络、文件或外部副作用，必须通过 Harness Extension、Runtime 或其他专用执行边界。

## 8. `ExactComponentRef<Package>` 与 LockedComponentSet

`ExactComponentRef<Package>` 必须包含：

- Package 类型；
- ID；
- 精确版本；
- artifact digest。

Descriptor 路径/API/digest 属于 ComponentInstall；调用配置属于 Harness、Runtime、Workspace 或治理 Binding/Decision。它们都不重复进入 Package 内容固定值。

不允许：

- latest；
- 版本范围；
- 隐式 fallback；
- 隐藏依赖求解；
- 同版本不同内容。

创建 Workflow 时 Resolver 形成不可变 LockedComponentSet。它记录实际参与当前 Workflow 的所有 Package 和 Binding，后续调用只使用该集合。

## 9. Harness Extension 定位

Harness Extension 是实现 `harness.oac.dev/v1` 的 ExtensionPackage，可以提供：

- Computational Guide；
- Inferential Guide；
- WorkUnit Executor Provider（位于 HarnessDefinition 外，由 `execution` 选择）；
- Computational Sensor；
- Inferential Sensor；
- Trigger；
- EventHandler。

每个 Harness Extension 必须通过 SDK 的 `Describe()` 返回本地化名称/说明、Extension Point 和 `effect_mode`。`effect_mode` 只允许 `pure`、`read-only` 或 `side-effecting`；Guide/Sensor 不得声明外部副作用，副作用 Executor/EventHandler 必须满足幂等、Observe、Cancel 与恢复接口检查。页面从 Extension Point 固定推导其 Harness 生命周期位置，Extension 不能声明自定义 phase。

管理员安装的受信任 in-process 模块可以提供纯 Gate Evaluator。Gate Evaluator：

- 无 I/O；
- 相同输入返回相同结果；
- 不访问网络、文件、数据库和 Secret；
- 失败时 Fail Closed；
- 不能产生 LifecycleAction。

## 10. Harness Host

Harness Host 位于 Orchestrator Kernel 外部。Workflow Controller 通过 WorkflowOrchestrationHost 应用需要扩展执行的 LifecycleAction 时：

1. 从 LockedComponentSet 解析实现 `harness.oac.dev/v1` 的精确 ExtensionPackage；
2. 验证 Package 状态和 Trust；
3. 解析 Harness Binding；
4. 计算有效权限；
5. 必要时创建 ComponentRun；
6. 对本地纯扩展直接调用；对异步或有副作用扩展通过 Execution Host 调用；
7. 通过 Platform Core 持久化 Artifact、Observation、Report 或 Event，并在存在 ComponentRun 时提交专用 ComponentRun report command；
8. 将结果交给下一轮 Kernel Reconcile。

Kernel 不导入 ExtensionPackage、MCP、HTTP、OCI 和 Kubernetes。

Harness Host 只承接 Workflow 中的 Harness Extension。外部 Promotion 由独立 Deployment Host 通过 Deployment SDK 调用；SolutionPackage 的领域页面只能提交 Platform Core `StartPromotion` 命令，不能把直接领域操作伪装成 Trigger/EventHandler、WorkUnit Executor 或 Harness Sensor。

Harness Host 到 SDK 的包中立 ingress 必须绑定同一不可变调用事实：精确 ExtensionPackage ID/version/artifact digest、entrypoint、Extension ID/binding/config digest、operation、owner ID/revision、subject ID/digest、Workflow/WorkUnit/iteration/ComponentRun 与 `RequestContext` scope，以及 canonical request 和 service-payload digest。Gate handoff 另外绑定 policy revision、ComponentRun、精确 evaluator registration/release 与 evaluator ID。Host 独占 issuer/trust 配置、签名验证、expiry/key rotation 和跨 receiver/restart replay enforcement；SDK 只消费 Host 附带的 opaque 已验证 handoff，不暴露调用者选择 signer/verifier/trust 的 API。

## 11. Scoped Core Services

Harness Host 只暴露 Extension 专用、当前调用范围内的 Core Services。

Extension-backed Executor 可以声明受限 `agent.invoke`。该能力只能绑定当前 Workflow、WorkUnit、iteration、ComponentRun 和 subject，由 Platform Core 仅为该显式内部调用完成作用域受限的 Agent Selection、Authorization 与 Execution Admission，并且一个 ComponentRun invocation 最多创建一个 AgentRun；它不得替换 WorkUnit 的 Kernel-selected `executor_key`。Guide、Sensor、Trigger 和 EventHandler 不能调用；多 Agent 协作必须拆成多个 WorkUnit 或 child Workflow。

不能暴露：

- Domain Repository；
- 数据库和 CRD；
- 任意状态写入；
- LifecycleAction 生成；
- Gate 批准；
- Secret 枚举；
- 其他 Workflow 和 ComponentRun 的写入。

有效权限是以下集合的交集：

    Interface request
    ∩ SDK maximum
    ∩ Trust tier
    ∩ Platform policy
    ∩ Project policy
    ∩ Solution policy
    ∩ LockedComponentSet
    ∩ current invocation scope

Builtin origin 不增加权限。

SDK 用同一不可变递归 measurement tree 表示普通执行结果与收据 lineage。节点固定完整 HostScopeRequest、operation、service-payload digest、policy/evaluator identity、outcome、implementation-call count、provenance/source、ordered children 与 parent digest；树复制和收据投影均深拷贝，避免调用方修改共享子结构。`ExecutionReceipt` 保持既有公开 DTO/摘要格式，但逐节点私有 seal 必须与全部公开字段、子节点顺序和递归 lineage 一致，否则拒绝。JSON decode、公开字段复制或重算数据摘要都不能恢复 seal；每个子节点必须独立封存，sealed subtree 只能在同一规范父身份下复用，改绑到不同父身份必须拒绝。nil 与 empty children 沿用既有 canonical normalization。开发/安装固定 suite 的 evidence 与 source bundle 必须绑定同一已提交 SDK revision，安装器仍对精确 Package 执行自己的 suite，调用方提交的 Verification Report 不是认证。data-only measurement/conformance tree 不产生 `RequestScope`、Gate 权限或 authoritative receipt；Host 正向认证仍由 Harness Host 单独验收。

## 12. Harness Extension Binding

Harness Definition 显式固定：

- Extension 顺序；
- ExtensionPackage ID 和精确版本；
- artifact digest；
- config digest；
- Binding ID；
- Required/Optional；
- 当前 subject。

Solution Studio 不要求作者额外维护展示副本。页面通过 HarnessDefinition 数组位置得到同阶段顺序，通过 SDK `Describe()` 得到名称、说明、Extension Point、权限和副作用模式，通过输入/输出 Schema 与配置 Artifact 得到输入输出说明。发布后详情页使用同一组字段；普通 Project 用户不选择本节的精确 Binding，只填写固定 Harness 合同明确要求的业务参数与 CredentialBinding。替换 ExtensionPackage、顺序、Required/Optional 或配置合同必须形成新的 package-local HarnessDefinition ID/config digest，并发布新的 Solution release。

组合规则：

- Computational Guide 只能收紧约束；
- Inferential Guide 只能追加精确不可变对象引用或 Package release 引用；
- HarnessDefinition 不包含 Executor；一个 WorkUnit 的唯一主执行由 `execution` 单独定义；
- Sensor 只追加 Observation；
- 所有 Required Gate Evaluator 均通过才可推进；
- 不使用隐式优先级、字段覆盖和 latest。

## 13. ComponentRun

只有调用需要独立幂等、来源、外部结果、错误或恢复语义时才创建 ComponentRun。

不需要 ComponentRun：

- Catalog 查询；
- 纯 Renderer；
- 静态 Schema 校验；
- 子 Workflow 创建。

需要 ComponentRun：

- Git PR、Merge；
- CI、Build；
- Workflow 内的 Release、Preview Deployment；
- 外部 API 写入；
- 需要 Observe/Cancel 的长任务。

已完成 Delivery 之后的外部 Kubernetes/CI-CD Promotion 是明确例外：它不是 WorkUnit 调用，因此不创建 ComponentRun。Software Delivery Application 创建不可变 Deployment 请求，Deployment Host 通过 `deployment.oac.dev/v1` 调用 DeploymentDriver，并以 `SubmitDeploymentReport` 追加结果。不要为了复用 ComponentRun 而放宽其 Workflow/WorkUnit/iteration/subject 绑定。

副作用 Executor 支持：

- execute；
- observe；
- cancel。

Workflow 内 ComponentRun 的结果为 Unknown 时进入固定恢复链，不能盲目 Retry：

1. Workflow ComponentRun 由 Harness Host 按平台有界退避策略自动调用同一精确 Executor release/config 的 `observe`；Controller 使用显式 `RequeueAfter`/`nextReconcileAt` 调度，不依赖 Kernel 隐式时钟；
2. 用户页面的 Observe 表示“立即重新检查”，复用同一 ComponentRun，并追加新的结构化 Observation/report，不创建新的副作用执行；
3. Observe 确认成功时保存外部引用和结果，随后由普通 Sensor/Gate 链路继续；
4. Observe 确认失败且证明副作用未发生，或 Executor 幂等合同明确证明重复调用安全时，页面才开放 Retry；Retry 创建新的 ComponentRun/执行记录，原记录保持不可变并保存 causation；
5. Observe 发现部分完成或已存在外部对象时进入恢复、补偿或 Remediation，不能重新创建；
6. Cancel 调用 Executor `cancel` 后仍必须 Observe 最终外部状态，Cancel 请求被接受不等于已经取消；
7. 超过观察窗口仍 Unknown 时保持 WorkUnit Waiting 并升级人工 Attention，不自动 Retry，也不因超时自行推断 Failed。

平台恢复策略只决定 Observe 的有界退避和人工升级时点，不能授权自动重试副作用、替换 Package release 或绕过 Authorization、Admission、Credential 与当前 Workflow 状态。

直接 Deployment 不复用上述 ComponentRun Retry 条件。它由 Deployment Host 使用 `next_observe_at` 与延迟 Outbox 独立调度；自动/手动 Observe 都复用原 Deployment。`Unknown|Cancelling` 始终禁止新 start，只有 `Failed|Cancelled` 后才由 Software Delivery Application 通过新的 `StartPromotion` 创建恢复 Deployment。直接路径没有 WorkUnit Waiting、Retry Run 或 Kernel Reconcile。

同步 Harness Host 获得返回结果后调用 Platform Core 的 `SubmitComponentRunReport`；异步 Executor 使用只绑定当前 ComponentRun 的受控报告入口提交同一类型合同。完整 Artifact/Observation/Report/Event 先持久化，Workflow CR 只保存紧凑 ComponentRun 摘要。AgentRun event/completion API 不处理 ComponentRun，Platform Core 也不把 ComponentRun 成功解释为 WorkUnit 成功。

Deployment Host 使用专用 `SubmitDeploymentReport`，不得调用 `SubmitComponentRunReport` 或写 Workflow CR。ComponentRun 与 Deployment 的报告合同都采用稳定 subject、单调 sequence、request digest、幂等和结构化 Unknown，但它们属于不同执行边界，不能合并成万能 RunReport。

## 14. 失败语义

Required Extension 失败显式阻塞。Optional Extension 跳过必须追加结构化 Observation。

失败至少记录：

- Component 和 Extension 精确身份；
- artifact/config digest；
- Binding；
- ComponentRun；
- subject ID/digest；
- error class；
- retryable；
- outcome known；
- external operation ID and optional URI；
- 建议恢复动作。

不可用 ExtensionPackage 不自动切换 package release。只能恢复原精确 release，或经影响分析和 Decision 创建后继根 Workflow，并形成新的 LockedComponentSet。

## 15. Solution Definition 边界

SolutionPackage 的行业定义、声明式领域体验和内容引用由 Solution Definition 详细设计负责。

Extension 领域只负责：

- 安装和生命周期；
- 精确引用；
- Trust 和权限；
- ExtensionCatalog；
- Package 与接口检查；
- Package/Bundle。

它不解释行业目标、方法阶段和领域页面。

Project Setup 的在线预检查也不扩展 Harness Extension 合同。需要哪些在线检查由 `FormField.field_type` 的平台类型定义自动派生，Solution 作者不声明 Probe Capability 数组，也不能提供脚本或把 `harness.sensor.computational` 当成 Setup Probe。Platform Core 只调用已内置的 Repository、Credential、Environment/Target、Artifact 等窄 Probe/Policy Port；这些调用不经过 Workflow Harness、不创建 ComponentRun，也不进入 LockedComponentSet。某字段类型需要在线检查但平台没有受支持的窄端口时，SolutionPackage 发布必须失败。

## 16. Runtime 与 Workspace 边界

RuntimeDriver 只通过 Runtime SDK 调用。Workspace 首版由平台内置 Workspace Port/实现承接，不进入 ExtensionCatalog。

Runtime SDK 的接口检查与 RuntimeDriver 的接入检查使用同一 RuntimeRequest、RuntimeInstructionSet、RuntimeSession 和 RuntimeEvent Fixture，验证 Execute 单次调用、事件通用信封、实时 Message/Thinking/Tool/Command/File/Usage 流、Agent Instructions、`oacok` bootstrap、可选 Project Rules、digest、防漂移、Repository 文件不变和临时文件清理。检查只证明 Driver 精确呈现了指令内容和 Runtime 事实；Platform API 上传、ACK、网络重试和业务完成不属于 SDK，是否允许工具、网络、凭证和业务完成仍由平台治理、Runtime 权限、Contract、Sensor 与 Gate 判定。

Harness Extension 不能注册 RuntimeDriver 或 Workspace 实现，也不能通过通用 Core Service 获得 Runtime/Workspace 句柄。

Agent 执行可以由当前主 Extension Executor 通过受限 `agent.invoke` 请求平台创建一个 AgentRun，但 Extension 不直接调用 Runtime SDK、选择 Runtime、操作具体 Session 或调用 AgentRun Event API。Platform Core 固定 AgentRun 与 RuntimeBinding，Execution Host 创建 Job，AgentRun Job 再调用精确 RuntimeDriver。

## 17. Governance 强制边界

AuthenticationDriver 是实现 `authentication.oac.dev/v1` 的 ExtensionPackage；它只返回协议中立的 AuthenticatedSubject，不拥有 Principal、Session、Scope 或 Authorization。

Audit Export 首版使用平台内部 `AuditExportPort` 和内置输出实现。它不能接管本地 AuditEvent 写入、查询、Retention、脱敏或证据组装，也不能成为 Audit Explorer 的数据源。用户只配置普通 Governance `AuditExportTarget`，不安装第三方审计导出 Package。

Credential Storage 首版使用平台内部 `CredentialStorePort` 和一个内置实现。普通 CredentialBinding 表单不选择存储后端，第三方凭证存储 Package、跨后端迁移和自动回退均不属于首版范围。未来只有在接口、安全隔离和检查套件成熟后，才把该 Port 发布为 Extension API。

Harness Extension 不能决定这些强制链路是否执行，也不能通过 Harness 替换或跳过它们。

Authorization 是明确例外：OAC 只提供一个位于 Platform Core/Governance 内部的 AuthorizationEngine。它不使用 Package Envelope、ExtensionCatalog 或 ExtensionPackage；企业差异只能通过类型化 AuthorizationPolicy 和本地访问关系表达，不能安装第二套授权算法。

Authentication 进一步区分两层：

- `AuthenticationDriver` 是安装、验证、激活和升级的 ExtensionPackage，例如 Local、OIDC、SAML 或 LDAP Driver；
- `AuthenticationMethod` 是 Governance 中一个普通可变配置实例，保存名称、说明、精确 Driver、非敏感配置、CredentialBinding 身份、CAS revision 和配置摘要；多个 Method 可以复用同一个 Driver，不创建 Method Version；
- Driver 只返回协议中立的 `AuthenticatedSubject`，Governance 通过 `ExternalIdentityLink` 解析本地 Principal；Driver 不拥有 Principal、Session、Scope、Authorization 或 Identity Provisioning；
- Authentication 在 Principal/Scope 产生前由平台或未来显式 organization/realm 入口选择，不能按 Project、Solution、Workflow 或 Harness 解析；认证后先固定执行 OAC AuthorizationEngine，执行创建再固定执行 OAC ExecutionAdmissionEngine；
- Audit Exporter 只根据当前 Scope 的 AuditExportTarget 调用内部 AuditExportPort；Project、Solution、Workflow、Harness 和 Agent 都不能选择输出实现、发送事件或读回外部 SIEM。

## 18. Package 与接口检查

安装过程只向用户展示两个结果：

1. **Package 检查**：验证 Envelope、签名、来源、文件边界、精确版本和 digest；
2. **接口检查**：ExtensionManager 根据 `interface_api` 调用对应 SDK 自带的检查套件，验证接口结构和必要行为。

第三方开发时和平台安装时运行同一套 SDK 检查。ExtensionPackage 不能选择测试 ID、关闭必需检查、修改通过标准或自行提交成功结论。Builtin 实现也运行同一套接口检查，不存在私有旁路。

Harness SDK 的接口检查验证：

- Describe、输入输出和权限；
- Extension Point；
- Guide 单调组合；
- Sensor 只追加；
- Executor execute/observe/cancel；
- Unknown 结果；
- Required/Optional Failure；
- Gate Evaluator 纯函数和 Fail Closed；
- 无状态写入旁路。

Authentication SDK 的接口检查验证：

- `DescribeAuthenticationMethod`、`StartAuthentication`、`CompleteAuthentication` 和 `ProbeAuthenticationMethod` 使用统一 Port，内置 Local 不得走私有旁路；
- 方法展示只产生平台支持的有类型 interaction，Generic Web 不需要硬编码 OIDC、SAML、LDAP 或厂商按钮；
- `CompleteAuthentication` 只返回 AuthenticatedSubject，不创建 Principal、Session、Scope 或授权结果；
- Secret 只能经授权的 CredentialBinding 提供，配置 Schema 中不得接收 Client Secret、Bind Password 或私钥明文；
- AuthenticationFlow 具有短期有效、配置摘要固定、回调校验和单次消费语义；
- 同一 `authentication_method_id + issuer_key + subject_key` 产生稳定身份事实，email、display name 和 group 变化不得改变主体或自动合并身份；
- Driver 不依赖 Web 框架、ORM、平台用户表、Project、Solution、Workflow、Harness 或 OAC AuthorizationEngine 的内部实现；
- Provider 测试、启用、禁用和切换不要求修改 Platform Core，且禁用后的 Session 处理遵守显式 Session Policy。

SolutionPackage 的发布验证还必须证明：

- Solution、Content 和 Extension 只通过公开合同协作；
- SolutionPackage 不执行副作用，声明式领域体验不拥有状态；
- 同一精确 ExtensionPackage release、artifact digest 和 config digest 的解析结果稳定；
- 配置差异形成新 config digest；Extension 实现变化发布新 package release，Solution package-local 合同或流程变化形成新局部 ID/digest 并发布新 Solution release；
- Project Setup 中每个资源字段类型要求的在线检查都能自动映射到平台支持的类型化窄 Port，且不通过 Harness Extension 或领域页面任意执行；
- 没有领域专用页面时 Generic Web 仍可完成 Project Setup、Workflow 创建、Decision 和验收。

## 19. 延后能力

首版不建设：

- 通用依赖求解器；
- 远程 Marketplace；
- 版本范围；
- 活动 Workflow 热升级；
- 任意前端代码分发；
- Service Extension Gate Evaluator；
- 万能 Component Context；
- 统一 Component invoke 接口。
- 第三方 Workspace、Audit Export 和 Credential Storage Extension API。

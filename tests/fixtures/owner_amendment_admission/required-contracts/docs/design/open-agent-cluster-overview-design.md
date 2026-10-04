# 云原生 Open Agent Cluster 概要设计

> 上游文档：[云原生 Open Agent Cluster 业务解决方案设计](../open-agent-cluster-business-solution-design.md)
> 文档阶段：概要设计
> 日期：2026-07-28

本文承接上游业务解决方案，将其中的产品目标和业务约束转换为领域边界、功能架构、系统架构和模块协作关系。字段、状态枚举、YAML、接口载荷、幂等键和恢复算法由各领域详细设计负责。

> 架构结论：OAC 以“核心通用平台 + 可扩展行业 Solution”交付产品能力，以 Sparse Engineering、Loop Engineering 和 Harness Engineering 组织 Agent Cluster 协作，并通过“1 个云原生底座、2 个能力核心、3 类扩展面、3 个操作入口”实现规模化运行。

## 0. 文档定位与设计权威

本文是 Open Agent Cluster 跨模块技术架构的权威设计，负责系统级领域边界、功能分解、组件职责、控制路径、数据所有权和共同不变量。它不重复业务解决方案中的业务论证，也不展开详细设计中的字段、接口载荷、状态转换表和实现算法。

当前文档体系按设计问题分工，而不是按“基线、索引、补充说明”叠加第二套内容：

| 文档层级 | 权威责任 | 不承担的责任 |
| --- | --- | --- |
| [业务解决方案](../open-agent-cluster-business-solution-design.md) | 业务问题、用户、价值、业务闭环、范围与非目标 | 系统组件、字段和实现算法 |
| 本概要设计 | 跨模块技术架构、共同产品模型、系统控制路径与不变量 | 模块内部字段和逐条用户操作 |
| [领域详细设计](detailed/) | 单一模块内部的数据结构、接口、状态、算法、失败与恢复 | 重新定义跨模块边界 |
| [契约 Reference](../references/) | 确实被多个实现方独立消费的精确 Schema、枚举、协议和幂等语义 | 第二套架构说明或通用阅读入口 |
| [验收与公开验证](../delivery/acceptance-and-validation.md) | 用户可见行为、端到端路径、失败分支与验收证据 | 代替模块单测或内部实现设计 |
| [部署与运行设计](../deploy-ops/deployment-and-operations-design.md) | 安装、物理拓扑、运行、升级与外部数据保护责任 | 产品业务状态机 |
| [前端设计](frontend/README.md) | Web 交互、视觉、可访问性与页面级实现约束 | 重新定义 Platform API 或领域状态 |

开发某个模块时，先读取本文涉及该模块的边界，再读取对应详细设计；只有修改共享契约、用户行为、部署行为或前端时，才继续读取相应 Reference、验收、部署或前端文档。发现冲突时应修改拥有该问题的文档和受影响实现，不新增“最终基线”“口径索引”或局部副本来覆盖冲突。

## 1. 设计目标与约束

### 1.1 业务目标的技术承接

业务解决方案已经说明，OAC 不是为了启动更多 Agent，而是为了让可以持续扩容的 Agent Cluster 在长期复杂工作中仍然保持有效并行、全局方向、责任连续、质量控制和企业治理。概要设计把这些要求落到领域边界、控制状态、模块职责和恢复机制上。

| Agent Cluster 的规模问题 | 顶层技术承接 |
| --- | --- |
| 有效并行，而不是盲目增加 Agent | Plan 与 WorkUnit 硬依赖定义可并行边界，Lifecycle Core 只释放 Ready WorkUnit，Executor Matcher 从合格候选中解析执行者 |
| 保持全局方向 | Workflow 固定 Goal、Active Plan、确认事实和 Outcome；Agent 不能绕过 Planning Gate、GateResult 或 Decision 改写生命周期 |
| 控制错误传播 | Harness 把 Contract、Guide、Sensor、独立 Review 和 Gate 放在成果进入下游之前 |
| 保持责任连续 | Workflow、Lineage、Run、Artifact、Observation 和 Decision 独立于 Agent、Runtime、Session、Pod 与节点 |
| 让人类监督可以扩展 | 程序处理可计算事实，Agent 处理可委派语义判断，人只在显式责任和 Decision Boundary 介入 |
| 把 Agent 变成企业可运营资源 | Kubernetes 承载规模与隔离，Platform Core 把身份、授权、准入、凭证、审计、Usage、归档和恢复带入主链 |

系统需要同时满足：

- 个人、小型团队和大型企业使用同一产品模型；
- 用户只提交目标，平台组织 Agent Team 持续推进到可验收结果；
- Workflow 承担长期业务责任，Agent Session 只保存一次执行所需的运行上下文；
- 人类管理目标、可承担有边界的 Review/Acceptance Run、作出正式 Decision 并确认最终 Outcome，但不管理模型会话与执行进程；
- 动态计划可以由 Agent 生成，但必须经过确定性校验和独立评审；
- 不同责任可以绑定不同的精确 Agent、模型、Runtime、工具和资源配置；
- Agent、Runtime、Session、Workspace 和 Kubernetes 资源相互解耦；
- 上游未通过时下游不得执行；
- 长周期 Workflow 能够在进程、Pod、Runtime 或节点故障后恢复；
- 软件交付作为默认场景交付，平台内核仍使用跨行业的 Workflow、WorkUnit、Artifact、Gate 和 Decision 语义；
- 内置与第三方能力使用相同扩展和治理边界；
- 企业高级能力可以替换实现，但不能改变强制治理链路。

每项目标都要找到明确的技术落点：

| 业务要求 | 技术承接 |
| --- | --- |
| 长期目标和责任不依赖 Session | 一个 Workflow CR 保存期望状态、活动计划、WorkUnit 与 Outcome；Platform Core 保存不可变事实和历史 |
| 用户无需预先编写完整任务图 | 显式 DAG Orchestration WorkUnit 调用具备 `orchestration.dag` 的 Executor，根据目标、当前状态和已确认事实生成 PlanDraft |
| Agent 计划不能直接进入执行 | Plan Compiler、独立 Plan Review 与 Planning Gate 共同形成不可变 Plan |
| 并行工作具有真实边界 | WorkUnit 固定 Contract、硬依赖、责任要求、Harness、Gate 和输出 |
| 生命周期不依赖长期 Agent 临场判断 | Lifecycle Core 统一计算 Ready、状态迁移、返工、已声明重新编排分支的激活、取消和终态收敛 |
| 等待期间不持续占用模型 | Workflow 保存状态，只在需要认知能力时创建有边界的 AgentRun |
| 作者不能批准自己的结果 | Producer、Verifier、独立 Reviewer、显式 Acceptance WorkUnit 和最终 Decision 使用独立责任边界 |
| 结果可以检查和追溯 | Artifact、Observation、Report、GateResult、Decision 与 Usage 形成结构化事实链 |
| 进程、Runtime 或节点故障后继续工作 | 无状态 Controller 重新协调 Workflow，Run 从 Lineage、Artifact、Observation、报告和 Workspace 恢复 |
| 外部副作用不会盲目重试 | Executor 分离 execute、observe 和 cancel；未知结果先观察真实外部状态 |
| Agent Team 可以按责任组合不同能力 | Project Team Bindings、不可变 AgentDefinition、嵌入式 RuntimeBinding 与 Workflow TeamBindingSnapshot 固定初始候选；缺少执行者时通过显式 Workflow Decision 补充，不跟随 Project 静默漂移 |
| 行业方案可安装但不能改变平台生命周期 | SolutionPackage 通过 BaselinePlanTemplate、显式 DAG Orchestration WorkUnit 和终态业务 Gate 接入；Workflow 成功始终使用 Kernel 固定 DAG 收敛规则，Harness Extension 只实现公开 SDK，声明式领域体验仍使用通用 Platform API |
| 主动执行始终受治理 | RequestContext、Authorization、Admission、Credential、Audit 和 Usage 进入命令与执行主链 |
| 执行容量可以复用 Kubernetes 扩展 | Workflow Controller、Execution Host 与标准 Job、Pod、PVC 承载控制和物理执行 |

### 1.2 顶层系统架构：1 个底座、2 个核心、3 类扩展面、3 个入口

后续的模块、数据、控制路径和部署设计都围绕以下结构展开：

| 顶层结构 | 组成 | 系统责任 |
| --- | --- | --- |
| 1 个云原生底座 | Kubernetes/K3s、Workflow CRD、Controller、Job/Pod/PVC、PostgreSQL、Artifact Storage | 保存活动控制对象，触发协调，调度和隔离物理执行，并在进程或节点变化后恢复 |
| 2 个能力核心 | Platform Core、Orchestrator Kernel | 前者拥有产品事实、治理和唯一写入边界；后者拥有计划、DAG、协作协议、连续性和确定性生命周期 |
| 3 类扩展面 | Application Package family、Agent Runtime SDK、Workspace SDK / Port | 分别隔离行业方法与内容、Runtime 厂商差异、Workspace 实现差异，使变化不侵入核心控制 |
| 3 个操作入口 | Web、`oactl`、Platform API | 为人、外部 Agent 和自动化提供不同交互方式，同时复用同一产品用例、权限、审计和事实模型 |

云原生底座提供运行条件。Kubernetes 保存 Workflow 资源、分发 Watch、调度 Job 和 Pod；PostgreSQL 保存目录、不可变事实、历史与查询模型；Artifact Storage 保存大体积成果。Pod 成功、日志输出或 Kubernetes Event 都属于运行事实，业务是否完成仍由 Gate 和 Decision 判断。

两个能力核心共同守住状态和方向。Platform Core 定义统一产品模型、命令、目录、治理、查询和不可变历史，也是 Workflow CR 的唯一物理写入者；Orchestrator Kernel 编译 PlanningRules 与 PlanDraft，计算 Ready、状态迁移、返工、已声明 DAG 修订分支的激活和终态收敛，并提供 Agent 协作协议与任务线连续性。它们通过 Workflow Controller 内部的 `WorkflowOrchestrationHost` 装配，但产品写入规则和生命周期规则各自只有一份实现。

三类扩展面把变化留在核心之外。Application Package family 使用 SolutionPackage、ContentPackage 和 ExtensionPackage 交付行业方法、复用内容与公开扩展实现；Agent Runtime SDK 用统一 RuntimeDriver 契约归一化不同 Runtime；Workspace SDK / Port 隔离本地目录、Pod/PVC 和未来其他 Workspace 实现。三类扩展面是解耦方向，不等于三种都可安装：RuntimeDriver 通过 ExtensionPackage 分发，Workspace 首版仍由平台内部 Port 和内置实现承接。

Web、`oactl` 和 Platform API 是同一产品的三个入口。Solution 的声明式领域体验可以增强 Web，但不能创建第二套状态、权限或写入路径。OAC 内部执行 Agent 使用 `oacok` 和 run-scoped AgentWorkChannel，它不是第四个北向入口。

### 1.3 设计原则

| 原则 | 在本系统中的含义 |
| --- | --- |
| 领域归属优先 | 概念由所属领域解释，跨领域名称不依赖全局后缀制造唯一性 |
| 语义所有权与物理写入分离 | Controller、Platform Core AgentRun event/completion use case、Harness Host 和 Execution Host 各自拥有字段语义，Platform Core 统一校验并物理写入 Workflow CR |
| 动态规划、确定性控制 | Agent 提出计划和语义判断，Kernel 校验合法性、依赖、Gate 和生命周期 |
| 结果先持久化再决策 | 执行、程序检查、评审和用户操作先形成结构化事实，再触发状态变化 |
| 物理执行与业务完成分离 | Run、Job、Pod 或模型调用结束只代表一次执行完成，业务成功还需要成果、Observation、GateResult 和 Decision 满足合同 |
| 执行能力可替换 | RuntimeDriver、平台内置 Workspace、Harness Extension 和外部系统通过外层合同接入，不进入领域内核 |
| 内置能力没有旁路 | 内置 Package 与公开 Extension API 实现仍经过安装、权限、类型化调用和检查链路；平台内部 Workspace/Audit/Credential 实现不进入 ExtensionCatalog，但不能绕过授权、准入和审计 |
| Kubernetes 管运行，OAC 管业务语义 | Kubernetes 保存和调度资源，OAC 判断责任何时可执行、成果是否通过以及 Workflow 何时结束 |
| 保持最小必要设计 | 当前版本不建设任意 Workflow DSL、隐式依赖求解器、内核级模型路由、多租户计费和复杂预算状态机 |

### 1.4 主要复杂度

| 复杂度 | 概要设计应对方式 |
| --- | --- |
| Agent 输出不稳定 | Harness 约束输入并观察输出，Gate 决定是否推进 |
| 计划动态变化 | Plan Compiler 校验 Draft，只有通过评审的 Plan 才能激活 |
| 多责任并行 | WorkUnit 硬依赖，每次 Run 独立的责任与 Lineage，以及隔离 Workspace |
| 外部副作用不确定 | 执行、观察和取消分离，未知结果先观察再重试 |
| Runtime 厂商差异 | 独立 Runtime SDK、可安装 RuntimeDriver 与薄的 AgentRun Job 调用代码 |
| Workspace 实现差异 | 平台内部 WorkspacePort、Workspace Adapter 与内置实现 |
| 行业差异 | Solution Definition 与 Extension 领域承载可变内容 |
| 企业治理演进 | 固定治理顺序，认证、授权、准入、审计和凭证使用独立端口 |

### 1.5 Agent 协作架构：Sparse、Loop 与 Harness

Open Agent Cluster 是 Sparse Engineering、Loop Engineering 和 Harness Engineering 面向大规模 Agent Cluster 的工程落地。三者不是独立功能，而是同一 Workflow 控制模型的三个侧面：Sparse 控制认知执行的物化，Loop 控制动态工作的持续收敛，Harness 控制每轮执行的输入、反馈和推进条件。

| 设计思想 | 解决的问题 | 架构落点 |
| --- | --- | --- |
| Sparse Engineering | Agent 规模扩大后，怎样避免会话、上下文和模型成本随注册能力同比增长 | Workflow CR 保存长期活动状态；Lifecycle Core 只为 Ready WorkUnit 创建有界 Run；InvocationRequest 只装配当前责任所需上下文；可计算事实不创建 AgentRun |
| Loop Engineering | 开放目标和计划变化怎样持续推进，又不依赖长期 Agent 临场控制 | 显式 DAG Orchestration WorkUnit 动态生成 PlanDraft；Plan Compiler、独立 Review 和 Planning Gate 激活不可变 Plan；Lifecycle Core 根据 Observation、GateResult 与 Decision 产生下一步 LifecycleAction |
| Harness Engineering | Agent 的开放执行怎样被约束、观察和验证，避免错误进入下游 | Guide 固定输入、方法和允许动作；Sensor 追加计算事实与语义判断；Gate 根据当前 Plan 的明确要求决定通过、返工、等待、拒绝或错误 |

Sparse 在四个维度上生效：等待期间不运行模型的时间稀疏，只激活 Ready WorkUnit 所需执行者的空间稀疏，只装配当前责任输入的上下文稀疏，以及把 Schema、测试、Digest、Probe、调度和状态计算交给程序的认知稀疏。它不限制有效并行；依赖允许且资源充足时，系统仍可同时物化大量 Run。

Loop 由动态规划和确定性推进共同构成。DAG Orchestrator 可以根据 Goal、已批准 Artifact、Review、Decision 和 PlanningRules 产生或修订 PlanDraft，但 Draft 必须经过 Compiler、独立 Review 和 Planning Gate 才能成为 Active Plan。执行事实回写 Workflow 后，原责任问题进入有界返工；目标、范围、风险或依赖变化只能沿 Active Plan 已声明的 DAG Orchestration / Remediation 路径生成后继 Plan。Plan 耗尽、Agent 自报完成或隐藏会话内容都不能创建图外工作。

Harness 使用 Guide/Sensor × Computational/Inferential 四象限：

| | Computational | Inferential |
| --- | --- | --- |
| Guide | 精确 Contract/Schema 身份、Package release、Capability、Policy 和允许动作 | 当前阶段 Instructions、显式方法资源、InputResource、语义上下文、既有 Review 和 Decision；执行器自身稳定 Instructions/Skills 单独物化 |
| Sensor | Event、测试、CI、Digest、Probe、外部状态和 Usage | 结构化 Review、验收、影响分析和人工判断 |

HarnessDefinition 固定适用象限、必需 Sensor 与省略原因。WorkUnit 的主执行单独由 `execution=executor|child-workflow` 定义，不属于 Harness 四象限。Guide 只装配当前责任需要的输入，Sensor 只追加 Observation；GateResult 或 Decision 只能满足当前 Plan 已声明的 Requirement、选择已编译分支或成为后续 InvocationRequest 输入。

三项理念汇合在同一条控制循环中：Workflow 保存长期责任，Sparse 按需物化 Run，Harness 让 Run 产生结构化事实，Loop 消费这些事实并决定继续、返工、等待、重新规划或结束。Agent、Runtime、Session、Workspace 或节点可以退出，下一轮仍从 Workflow、Lineage 和已确认事实继续。

## 2. 方案比较与采用判断

技术方案首先要决定长期状态和下一步判断由谁负责。业务解决方案中的几类现有产品，落到技术上可以归纳为三种路线：程序 Workflow Engine 持有流程、长期 Agent Orchestrator 持有认知控制，或由 Workflow Controller 和独立 Kernel 持有确定性生命周期。

### 2.1 方案 A：使用通用 Workflow Engine 作为业务控制面

Argo、Tekton 或 Temporal 擅长执行已经定义好的步骤。重试、超时、补偿和 Activity 恢复都有成熟机制，适合构建、扫描、数据处理和系统集成。

Open Agent Cluster 的计划会由 Agent 根据新事实动态提出，还要处理独立评审、责任连续性、成果 Gate 和用户验收。若把这些语义分别放进 Workflow 模板、业务数据库和旁路 Controller，活动状态就会出现多个所有者。通用 Workflow Engine 因此适合作为某些 WorkUnit 的执行能力，不适合作为产品业务控制面。

### 2.2 方案 B：由长期 Agent Orchestrator 持有认知控制循环

这类方案由一个长期 Agent Orchestrator 理解目标、拆解任务、启动有边界的 Worker 或 Validator、解释验证结果并调整计划。Worker 和 Validator 可以按需运行，规划也能快速适应开放环境中的新信息。

它的代价是 Mission 的推进、状态解释和收敛仍围绕 Orchestrator 的认知循环。Orchestrator 重启或更换 Runtime 后，系统需要重新建立整个 Mission 的理解；字段所有权、不可变成果、治理和故障恢复还要由外部控制模型补齐。若关键事实主要留在对话或私有状态中，恢复和审计仍依赖模型重新解释。

Open Agent Cluster 仍然使用 Product/Requirement、Architect、DAG Orchestrator、Reviewer 等 Agent 完成不同认知工作。长期状态由 Workflow 保存，Ready、Gate 和收敛由 Kernel 计算；Agent 提交成果、计划和专业判断，这些输出持久化并通过校验后才影响生命周期。

### 2.3 方案 C：Kubernetes-native Workflow Controller 与独立 Orchestrator Kernel

本项目采用 Kubernetes-native Workflow Controller 与独立 Orchestrator Kernel：

- `Workflow` CRD 承载活动期望状态和观察状态；
- 无状态 Workflow Controller 观察资源变化并调用独立 Orchestrator Kernel；
- Platform Core 提供用户命令、目录、治理、不可变事实、历史与查询，不运行编排循环；
- Execution Host 将执行请求转换为标准 Kubernetes Job/PVC；
- RuntimeDriver 由 AgentRun Job 通过 Runtime SDK 进程内执行；平台内置 Workspace 和 Harness Extension 分别通过 Workspace Port 与 Harness Host 执行；
- 行业方案只定义领域成果、方法和扩展绑定。

这条路线把职责分开：Kubernetes 保存活动资源并调度执行，Kernel 根据显式事实计算生命周期，Platform Core 管理产品语义和写入边界，Agent 只在需要认知能力时参与。Controller 或 Runtime 重启后，系统重新加载 Workflow 即可继续，不需要恢复某个监督 Agent 的完整隐藏上下文。

该方案的实现复杂度高于直接使用 Workflow Engine，也比单一 Agent Orchestrator 多出显式状态与治理边界。它换来的是一个权威活动状态、可测试的生命周期、跨 Runtime 恢复能力，以及行业 Solution 无法绕过的统一控制规则。

## 3. 领域边界与统一语言

### 3.1 统一产品模型

Platform Core 定义一套跨行业共享的产品模型。行业 Application 可以提供领域名称和视图，所有命令仍然回到同一组控制对象、状态机和写入路径。

| 对象 | 技术语义 | 活动状态与历史位置 |
| --- | --- | --- |
| `Project` | 多个 Workflow 共享的长期工作空间与治理边界；`solution_setup` 直接保存当前 Solution、Project 配置和 Team bindings | PostgreSQL 保存当前值、CAS revision、configuration digest 与 AuditEvent 历史；Workflow 创建时复制执行快照 |
| `AgentDefinition` | 某个逻辑 Agent 一次正式发布后的不可变定义 | PostgreSQL 保存精确 ID、previous ID 与 content digest；编辑草稿不创建新定义 |
| `SkillPackage` | 经过静态校验、SemVer 和 Content 安装治理的可独立发布 Skill package | Package/Artifact Storage 保存内容，PostgreSQL 保存目录与安装事实 |
| `ModelProviderConnection` | 模型 API Type、Base URI、条件性的 CredentialBinding 和非敏感设置的当前连接配置；发现细节由 Connector 封装 | PostgreSQL 保存稳定 ID、revision、configuration digest 与审计；Workflow 固定解析快照 |
| `ModelCatalogSnapshot` | Connector 获取或用户显式补充后形成的不可变模型目录快照 | PostgreSQL 保存模型来源、列表、时间与 digest |
| `McpServerDefinition` | 外部 MCP Server 的当前 stdio 或 Streamable HTTP 配置 | PostgreSQL 保存稳定 ID、revision、configuration digest 与审计；Workflow 固定解析快照 |
| `CredentialBinding` | 带 Scope、用途、subject、可选过期时间和内部 store handle 的稳定凭证授权身份 | PostgreSQL 只保存元数据与 CAS revision；Secret 由内部 CredentialStore 保存，轮换形成独立记录与审计 |
| `Workflow` | 从业务目标到终态 Outcome 的唯一活动控制对象 | Kubernetes Workflow CR 保存活动 `spec/status`；PostgreSQL 保存历史与读模型 |
| `WorkUnit` | 带 Contract、依赖、责任要求、唯一 `execution`、Harness 和直接状态的持久工作单元；Harness 围绕主执行提供 Guide/Sensor/Gate/返工 | 定义和活动状态嵌入 `Workflow.status`；Plan 与历史记录保存在 PostgreSQL |
| `ChildWorkflowLink` | 父 WorkUnit iteration 到其拥有子 Workflow 的不可变组合关系 | 嵌入父 WorkUnit；子 Workflow 使用独立 Workflow CR 保存控制状态 |
| `Artifact` | 正式提交后带类型、Provenance、previous ID 和 Digest 的不可变输入或结果 | PostgreSQL 保存元数据，Artifact Storage 保存大内容；不要求 SemVer |
| `Plan` | 通过 Planning Gate 后创建的不可变可执行计划 | PostgreSQL 保存内容和来源；Workflow.status 只保存唯一 `activePlanId` |
| `Observation` | 程序、Agent、人或外部系统提交的结构化事实 | PostgreSQL 保存不可变记录；活动控制使用精确引用或有界摘要 |
| `AgentRun` | 一个 Agent 在 WorkUnit 责任 Lineage 中的一次执行 | 当前有界状态和报告位于 Workflow Run 子树；事件、Usage 和历史保存在 PostgreSQL |
| `ComponentRun` | Workflow/WorkUnit 中需要幂等、来源、外部结果或恢复语义的组件调用 | 当前报告位于 Workflow Run 子树；不可变记录和历史保存在 PostgreSQL |
| `Deployment` | Software Delivery 将精确 Release 部署到精确目标的一次记录；Preview 来源可绑定 WorkUnit，外部 Promotion 来源为直接应用命令 | 请求和追加式报告保存在 PostgreSQL；外部直接 Promotion 不写 Workflow CR，也不创建 ComponentRun |
| `GateResult` | 对必需 Observation、策略和 Decision 的明确评估结果 | Workflow 保存当前 Gate 摘要，PostgreSQL 保存不可变结果与历史 |
| `Decision` | 关联精确主体 ID/digest 的人类授权、业务选择或验收决定 | PostgreSQL 保存不可变记录，Workflow 保存决定引用与控制影响 |
| `UsageRecord` | 对模型、组件和执行资源实际使用的可归因记录 | PostgreSQL 按 Project、Workflow、WorkUnit、责任和 Run 保存 |
| `LockedComponentSet` | Workflow 固定使用的 SolutionPackage、Harness ExtensionPackage、RuntimeDriver ExtensionPackage、ContentPackage，以及 package-local 内容与配置摘要集合；Workspace 使用平台内部 Port，不进入该集合 | Workflow.spec 内嵌完整不可变集合用于恢复；PostgreSQL 保存内容相同的记录与解析报告用于查询历史 |

这套模型刻意把“用户要完成什么”“当前要推进哪一块工作”和“某次执行发生了什么”分开。用户提交的目标由 Workflow 承担；计划拆出的业务节点由 WorkUnit 承担；Agent、组件或外部程序的一次实际调用由 Run 承担；执行产生的内容和判断再分别沉淀为 Artifact、Observation、GateResult 与 Decision。

例如，一个“完成版本发布并部署到预览环境”的目标只创建一个 Workflow。需求确认、代码修改、测试、发布和 Preview 部署是不同 WorkUnit；Codex 执行代码修改是一次 AgentRun；Workflow 内创建 Release 或 Preview 资源可以产生 ComponentRun；测试报告和部署地址是 Artifact 或 Observation。即使某个 Job 已经成功退出，也只能说明这一轮物理执行结束，只有必需的测试、Review、外部状态和用户 Decision 都满足合同，Workflow 才能继续推进。最终验收后用户另行点击外部 Promotion 时，平台创建独立 Deployment 并直接调用 DeploymentDriver，不重新打开该 Workflow。

因此，`Workflow` 是唯一活动控制文档。其他对象各自保存目录、不可变事实、执行记录或历史，它们为 Workflow 提供判断依据，但不再维护另一套“当前进行到哪里”的状态。

### 3.2 领域边界

领域名称是文档、代码包和接口命名的首要上下文。下面的边界是 DDD 限界上下文和模块边界，不要求物理拆成独立微服务。

| 领域 | 核心责任 | 主要术语 |
| --- | --- | --- |
| Solution Definition | 定义顶层 Project Setup Schema、责任岗位、Workflow Input Schema、批准基线、显式动态编排入口、终态业务路径和声明式领域体验 | ComponentBundle、SolutionPackage、FormField、ResponsibilitySlot、BaselinePlanTemplate、DAG Orchestration WorkUnit |
| Project & Team | 管理长期 Project 当前配置、逻辑 Agent、正式定义和责任候选池 | Project、ProjectSolutionSetup、AgentTemplate、AgentDefinition、TeamBindingSnapshot |
| Agent Capability Catalog | 管理模型 Provider 连接、模型目录、MCP Server 配置与测试快照 | ModelProviderConnection、ModelCatalogSnapshot、ModelProviderProbe、McpServerDefinition、McpCapabilitySnapshot |
| Workflow | 管理目标、工作项、成果、依赖、Run、质量门和人工决定 | Workflow、WorkUnit、Run、Artifact、Observation、GateResult、Decision |
| Orchestration | 生成规划规则、校验计划、评审计划并推进生命周期 | PlanningRules、PlanDraft、ValidatedPlan、Plan、LifecycleAction |
| Agent Execution | 将逻辑 Agent 物化到 Runtime 并管理执行连续性 | AgentRun、Runtime、Session、Turn、RuntimeBinding |
| Workspace | 提供执行目录、存储、隔离、资源和回收 | Workspace、Provider、Capability、Retention |
| Extension | 安装三种 Package，按公开 Extension API 解析可执行实现，并管理 Harness ComponentRun | Package、ComponentInstall、ExtensionCatalog、SkillPackage、LockedComponentSet、ComponentRun |
| Governance | 强制执行身份、授权、准入、审计、凭证和 Usage | Principal、Authorization、Admission、Audit、Credential、Usage |
| Software Delivery | 将通用能力表达为生产级软件交付 | Delivery、Requirement、Design、Task、Review、Release、Deployment |

### 3.3 变更机制与命名规则

系统只保留四种有明确用途的变化方式。是否需要不可变历史由业务语义决定，不能因为对象曾经修改过就统一创建 `Version` 聚合。

| 变化方式 | 适用对象 | 身份与更新语义 |
| --- | --- | --- |
| 可安装发布 | SolutionPackage、ContentPackage、ExtensionPackage，以及随发布携带的 AgentTemplate、SkillPackage、RuntimeDriver 等内容或实现 | 稳定 package identity + SemVer + artifact digest；安装或升级产生新的精确 release |
| 不可变业务记录 | Artifact、Plan、AgentDefinition、Decision、ReviewResult 等正式提交或批准结果 | 每次正式提交创建新 ID + content digest，可选 previous/parent ID；不要求用户管理 SemVer，也不增加 `Version` 后缀 |
| 普通可变配置 | Project setup、ModelProviderConnection、McpServerDefinition、CredentialBinding 和策略绑定等 | 保持稳定对象/Owner Scope 身份，以 CAS `revision` + configuration digest 更新，并写入不可变 AuditEvent；Credential 轮换另记 CredentialRotationRecord |
| Workflow 创建快照 | 根 `Workflow.spec` 中创建时解析出的 Solution、Project、Team、Component、Provider/MCP 等执行输入 | 创建时固定并在活动 Workflow 内保持不可变；用户命令只通过类型化字段更新 desired state 或追加精确 Decision，不重写创建快照 |

因此，包发布版本、正式业务记录、普通配置修订和活动 Workflow 快照必须使用不同语义。新的长期目标若必须采用更新后的 Project、Solution、Harness、Policy 或 Component 配置，应创建后继根 Workflow，而不是把当前 Workflow 静默切换到新配置。

- 在业务解决方案中只使用业务名词，例如“计划”“成果”“评审”和“Release”；
- 在概要设计中使用领域限定名称，例如 `Orchestration Plan`、`Workflow Artifact`；
- 在详细设计和代码中由 package/module 提供命名空间，例如 `orchestration.PlanDraft`；
- `Draft`、`Validated` 和 `Active` 只描述对象阶段；`Version` 仅用于可安装 package 的 SemVer 或 API/Schema/Protocol 兼容版本，不作为所有不可变记录的统一后缀；
- 不在跨领域正文中单独使用 `Spec`、`Proposal`、`Revision`、`Record` 或 `Snapshot`。

## 4. 产品功能架构：核心通用平台与可扩展行业 Solution

![Open Agent Cluster Functional Architecture](../assets/diagrams/overview/open-agent-cluster-functional-architecture.svg)

产品功能架构首先分开“所有行业共同需要的长期工作能力”和“某个行业独有的方法与体验”。核心通用平台提供统一产品对象、协作闭环、执行、恢复、治理和操作入口；行业 Solution 只定义领域目标、责任、方法、成果、终态路径和声明式体验。

| 功能层 | 主要内容 | 架构约束 |
| --- | --- | --- |
| 核心通用平台 | Project、Agent Team、Workflow、Plan、WorkUnit、Run、Artifact、Observation、Review、Gate、Decision、Usage、Outcome，以及设置、Agent Center、Solution Studio、归档和查询 | 控制状态、生命周期、治理和写入路径由平台统一拥有，不能因行业增加而复制 |
| 可扩展行业 Solution | BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、责任岗位、领域 Artifact/Schema、Harness/Gate、终态业务路径、本地化术语与声明式体验 | 可以改变行业语言和工作方法，不能增加私有控制状态、绕过 Gate 或要求 Generic Web 无法表达的第二写路径 |

功能架构再从用户使用路径展开。用户先检查当前 Scope 已有能力是否满足目标：内置 Solution 可以直接提供 AgentTemplate 与 Skill，平台级模型目录也可以复用；只有存在缺口时，用户才在统一 Settings 补充模型 Provider、MCP Server 或 Credential。随后用户在 Agent Center 组装逻辑 Agent，创建 Project、选择精确 Solution，并按 Solution 要求完成 Agent 岗位、项目配置与执行环境绑定，最后在 Project 中创建一个 Workflow。平台完成规划、校验和评审后，逐步派发 WorkUnit；每次执行都经过 Harness、治理和资源边界，结果回到 Gate，再决定继续、返工、等待、重新规划还是结束。

这条路径既适用于软件交付，也适用于调研、内容生产、运营分析等行业方案。变化的是目标、成果合同、责任组合和扩展绑定，Workflow、WorkUnit、Run、Artifact、Observation、Gate 与 Decision 的控制关系保持一致。

### 4.1 方案与项目管理

Solution 决定“这类目标需要哪些 Project 配置、应该产出什么、按什么方法推进、由哪些责任共同完成”；Project 则保存一个团队长期使用的 Agent、策略、组件和目标环境。用户不需要为每次 Workflow 重新拼装全部技术参数，而是在已治理的 Project 配置基线之上提交本次目标。

- Unified Settings：作为能力缺口检查和补充入口，在一个页面 Shell 中分别管理 Skill 导入与 package release、Model Provider 与 Model Catalog、MCP Server，以及 CredentialBinding 的创建、轮换、禁用和撤销；内置 CredentialStore 随系统安装自动就绪，用户不配置存储后端或 CredentialPolicy；它不要求每个项目重复配置全部类别，底层仍由 Content Catalog、Agent Capability Catalog 和 Governance 分域承接；
- Agent Center：从 AgentTemplate 创建时自动继承模板的全部精确 Skills，用户只按需追加企业 Skill；在按 Provider 分组的统一模型选择器中直接选择 Provider/Model 组合，再配置 MCP、Runtime Binding、能力和资源要求并发布不可变 AgentDefinition；
- Solution Catalog / Studio：安装、选择或创建行业 Solution；作者发布不可变 release，管理员通过一个“安装并启用”操作使其进入当前 Scope 的可选目录，同时拥有两种权限时可以一次“发布并启用”；Verify、Install 和 Activate 只作为后台进度显示。BaselinePlanTemplate 使用普通节点/边编辑器，每个节点固定一个完整 HarnessDefinition，并可在同一生命周期编辑器中启用独立责任 Review；
- 创建 Project 时填写名称与说明，并从 Solution Catalog 选择行业方案；页面只在唯一 package release 候选或 Policy 指定推荐精确 release 时默认选中，多个无推荐 release 时要求用户明确选择；此时 Project 已创建但 `solution_setup` 仍为空；
- 根据 Solution 顶层 `project_setup_schema` 选择或就地创建必须由用户决定的 RepositoryBinding、CredentialBinding 和条件性外部目标，并按 `responsibility_slots[]` 选择 Agent 候选；Generic Form Engine 可以引导直接输入 URI，但采用值只保存平台对象身份。Project Rules 在 Workflow 批准基线阶段形成；Solution 固定核心定义、Harness 装配、平台托管 Preview 与默认 Runtime/Workspace/Governance Policy 只读展示；替换 Harness Extension 时由 Solution 作者发布新的精确 Solution 版本；
- 根据责任岗位提示配置尚未保存的责任候选池；
- 在统一的“检查与采用”区域自动或手动提交尚未保存的 `ProjectSetupCandidate`；Platform Core 对候选值归一化、计算 `candidate_digest`，并协调组件、Repository、Agent、Runtime、Workspace、环境与治理检查，返回瞬时 `ProjectSetupEvaluation`；
- 当前候选检查通过后，以 `expected_revision` 原子替换 `Project.solution_setup`、递增 Project revision、重算 configuration digest 并写入 AuditEvent。预检查不持久化候选、Evaluation 或 Component Resolution Preview，保存和创建 Workflow 时仍重新校验；创建 Workflow 时再把 Project 配置、初始 Team bindings、组件、模型和 MCP 非敏感配置固化到 `Workflow.spec`。当前配置更新只影响之后创建的根 Workflow；既有目标需要采用新的 Solution、Harness/Policy、组件或其他执行基线时，平台在影响分析和 Decision 后创建新的后继根 Workflow。仅为满足既有 WorkUnit 责任与能力要求补充一个合格 Agent 时，由活动 Workflow 内的显式 Decision 承接，不替换执行快照，也不创建后继 Workflow。
- Project 与逻辑 Agent 支持按类型归档和恢复；归档只影响后续创建、选择与编辑，不改写既有 Workflow 快照或取消运行中的 AgentRun。硬删除只允许从未形成不可变业务引用的空 Project 或未被任何 Project/Workflow/Run 引用的 Agent，并由资源模块与 Governance 在事务内重新校验；平台不提供通用回收站或强制解除历史引用。

### 4.2 Workflow 管理

Workflow 管理直接呈现业务进展：目标、工作项、依赖、成果、风险、等待原因和验收决定。Runtime 会话、Pod 和 Workspace 属于执行细节；平台更换 Agent、重建 Pod 或恢复 Workspace 时，用户仍然沿着原来的目标和责任继续查看进度。

- 创建、暂停、恢复、取消和观察 Workflow；
- 管理目标、工作项、成果、依赖和质量门；
- 管理父子 Workflow；
- 管理用户问题、风险授权和最终验收；
- 展示运行阶段、关键路径、风险和 Usage。
- 终态 Workflow 可以归档或恢复显示；归档只写 PostgreSQL 历史可见性元数据，不进入 Workflow state、Kernel 或 Controller。普通用户不能硬删 Workflow 及其 Artifact、Observation、Decision、Plan、Run、Usage 和 Audit 历史。

### 4.3 编排与 Harness

编排能力负责把开放目标变成可以持续推进的工作结构。具备 `orchestration.dag` 能力的 DAG Orchestrator 可以根据当前事实调整计划，但草案必须先通过静态编译和独立评审；计划激活后，Lifecycle Core 只根据显式依赖、Observation、GateResult 和 Decision 计算下一步，不依赖某个监督 Agent 一直在线。

Harness 则把每个 WorkUnit 的执行前后边界补齐。Guide 告诉执行者可以使用哪些输入、工具和动作，Sensor 把测试、外部状态、评审和 Usage 转成结构化事实。这样，Agent 的灵活判断发生在清晰的合同内，结果是否足够仍由 Gate 决定。

用户呈现保持单一生命周期结构：编辑、详情和运行追踪都按“执行准备 → 主执行 → 观察与评估 → Gate 判定 → Kernel 后续处理”展示；执行准备/观察/Gate 来自一个完整 HarnessDefinition，主执行来自 WorkUnit `execution`。页面连续展示但数据分别归属，不能从 Harness 投影主执行，也不建立独立预览对象或第二套字段。

- 根据 OrchestrationDefinition、当前 Plan 和显式 DAG Orchestration WorkUnit 产生 PlanningRules；
- 像普通节点一样派发依赖和类型化 `activation` 已满足的 DAG Orchestration WorkUnit；GateResult/Decision 只能选择当前 Plan 已编译的可见分支，不能在图外创建 DAG 编排节点；
- 根据逻辑责任、能力和合同匹配 Executor，解析 typed inputs，组装不可变 InvocationRequest，并校验 WorkSubmission；
- 静态校验 PlanDraft；
- 调度独立 Plan Reviewer；
- 通过 Planning Gate 形成不可变 Plan，并经 CAS 更新 Workflow `activePlanId`；
- 计算 Ready WorkUnit；
- 处理返工、激活 Active Plan 中已声明的重新编排路径、等待用户和终态收敛；
- 通过 Guide/Sensor × Computational/Inferential 四象限约束不确定执行。

### 4.4 Agent 执行与 Workspace

Agent Team 在这里被解析成一次次可审计的执行选择。Solution 只在具体 WorkUnit 声明 PM/Architect/DAG Orchestrator/Producer/Verifier/Reviewer 等责任和能力，Project 为必需责任配置候选 Agent；WorkflowOrchestrationHost 先把满足 Workflow 固定候选、独立性、安全、合同、资源和可用性要求的每个候选映射成一一对应的 ExecutorDescriptor，Kernel Runtime 匹配一个逻辑 `executor_key`，Platform Core 只能按该 key 解析精确 AgentDefinition，再物化其允许的 Runtime、模型、MCP Server、用途明确的凭证与 Workspace，不能二次选择 Agent。软件交付中的 E2E Acceptance 是普通 WorkUnit 主执行，不要求额外的 Project `acceptor` 岗位。

例如，开发和 Review 可以使用不同 AgentDefinition，并运行在相互隔离的 Session 与 Workspace 中。返工时可以延续原责任的 Lineage 和可恢复 Workspace，但不能把作者的隐藏会话直接交给 Reviewer，也不能用隐式降级悄悄更换模型或 Runtime。

Agent 执行还统一使用 Orchestrator Kernel 的 AgentWorkChannel：Runtime 注入最小 bootstrap instruction，Agent 通过 `oacok work show` 查看当前固定请求，通过 `oacok guide` 理解协作协议，通过 `oacok work read` 读取精确资源，通过 `oacok work submit` 提交结构化结论。Producer 结论、Reviewer 完整评审结果和返工反馈作为 Observation/ReviewResult 回到同一 WorkUnit 任务线；换 Agent 可以换 Session，但不能丢失结构化交接。

- BaselinePlanTemplate 和 PlanDraft WorkUnit 声明责任标签、能力要求和独立性约束；
- Project 通过 `solution_setup.team_bindings` 为基线责任配置初始授权候选池；根 Workflow 将其复制成 TeamBindingSnapshot；动态责任没有匹配项时，WorkUnit 进入 Waiting，用户可用绑定精确 MissingExecutor Observation 的 Decision 补充一个 Workflow-local 候选；
- 平台根据能力、安全边界、独立性和资源条件选择精确 AgentDefinition；
- AgentDefinition 直接保存稳定 Instructions、精确 SkillPackage release、结构化 ModelSelectionPolicy、MCP Server 稳定 ID 和嵌入式 RuntimeBinding；
- RuntimeBinding 声明具体 RuntimeDriver ExtensionPackage release、Runtime artifact、支持的模型 API Type、MCP Transport、能力和启动参数；
- Platform Core AgentRun 创建用例把 AgentDefinition Instructions、Kernel `oacok` bootstrap 和当前任务明确选择的 Project Rules 规范化为结构化 RuntimeInstructionSet；Execution Host 将其挂载给 AgentRun Job，RuntimeDriver 只负责映射到原生 developer/system instruction 或 Repository 外临时文件，不改写项目 `AGENTS.md`、`CLAUDE.md`；
- 根 Workflow 创建时固定模型 Provider/MCP 配置的 stable ID、revision、configuration digest 和非敏感值；每次 Run 从该快照固定 Agent、Runtime、模型名称、按用途解析的 Credential 和资源配置，并记录选择结果与 Usage；
- Codex 等 Runtime 自带 Tool 首版不建立独立 Tool Catalog，也不在 Agent 上逐项选择；
- 候选解析输出精确 AgentDefinition 与 RuntimeBinding，不使用 `latest`、隐式降级或隐藏默认团队；
- 创建或恢复 Session；
- 创建隔离 Workspace；
- 传输结构化事件、结果和 Usage；
- 按 CPU、内存、GPU、存储和拓扑调度 Kubernetes Workload。

Usage 的权威采集路径是 `RuntimeDriver → Runtime SDK RuntimeEvent.usage → AgentRun Job → Platform Core AgentRun event use case → immutable UsageRecord`。RuntimeDriver 直接解析 Provider/Runtime 结构化响应，SDK Core 补齐事件身份与顺序，AgentRun Job 原样上传，Platform Core 补充精确 Project/Workflow/WorkUnit/Run/Agent 归因；查询侧再关联 Review/Gate/Outcome 形成 Agent 效率、模型和 Token 图表。缺失字段保持 unknown，父级汇总不写回原始记录。OpenTelemetry 不是首版依赖或默认 Workload，只允许未来外层 Adapter 从同一已提交事实异步导出 OTLP。

### 4.5 扩展与治理

行业能力、Runtime、Workspace 和企业治理都通过专用扩展合同接入。平台允许替换实现，却不允许扩展重新定义 Workflow 生命周期、跨过 Gate 或直接改写控制状态。组件安装时确定来源、版本、Digest、信任等级和权限，Workflow 启动时再固定本次运行使用的精确组件集合。

治理不是执行完成后的附加记录。用户命令、Extension 调用、AgentRun 和 Workspace 创建都要经过相应的身份、授权、准入和凭证链路；审计记录谁在什么 Scope 下、依据哪个策略版本作出了什么操作。内置与第三方 ExtensionPackage 遵循同一条安装、解析、类型化调用和接口检查路径。

治理查询使用一个统一 Audit Explorer：Platform Core 从本地不可变 AuditEvent 组装经授权、脱敏的列表与详情证据链，资源详情页只通过预设筛选跳转到同一入口。它不为 Authorization、Admission、Credential use 等类别建立第二套审计对象，也不从外部接收端反向读取平台历史。可选 AuditExportTarget 通过平台内部 AuditExportPort 异步导出；首版每个 Scope 最多一个目标，未配置不影响平台就绪。

- 通过一个公开“安装并启用”操作触发内部 Verify、Install 与 Activate，并继续支持禁用、撤销和并排升级 Package；
- 安装 SolutionPackage、ContentPackage 和 ExtensionPackage；
- 解析 Workflow 固定使用的精确组件集合；
- 强制执行认证、授权、执行准入、凭证和审计；
- 对内置和第三方实现执行相同一致性与兼容性测试。

技术架构只保留三种 Package 与一组公开 Extension API：

1. **SolutionPackage**：直接定义 `project_setup_schema`、`responsibility_slots[]`、`workflow_input_schema`、BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务节点/Gate、WorkUnit Contract、精确 Harness/Content 引用和声明式领域体验；不执行副作用，也不定义第二套完成判断。
2. **ContentPackage**：独立发布 SkillPackage、AgentTemplate 等不可执行内容。
3. **ExtensionPackage**：实现一个公开 SDK；首版接口为 `harness.oac.dev/v1`、`runtime.oac.dev/v1`、`authentication.oac.dev/v1` 和 `deployment.oac.dev/v1`。

ExtensionManager 统一 Verify/Install/Activate/Disable/Revoke，ExtensionCatalog 按接口发现精确实现。HarnessDefinition 固定 Harness ExtensionPackage 及其配置；RuntimeBinding 固定 RuntimeDriver；AuthenticationMethod 固定 AuthenticationDriver；直接 Deployment 固定 DeploymentDriver。接口执行仍由各自类型化 Host/SDK 承接，不合并成万能调用。Workspace、Audit Export 与 Credential Storage 首版只使用内部 Port 和内置实现。

### 4.6 内置 Solution 应用

内置 Solution 要跑通完整业务闭环，同时保持平台内核的通用语义。软件交付方案会把 Workflow、WorkUnit 和 Artifact 组装为 Delivery、Task、Requirement、Release 等领域视图；Preview Deployment 仍在 Workflow 控制链内。最终验收后的外部 Promotion 是独立的直接 Deployment 操作，不为一次确定性外部调用建立 Workflow。调研学习方案则展示问题拆解、来源、观点冲突和结论确认。

- 生产级软件交付 Solution 提供需求、设计、验收标准、开发、Review、测试、Release、Preview、用户验收和 Promotion；
- 内置 Software Delivery Bundle 提供 Product/Requirement、Architect、DAG Orchestrator、Developer、Reviewer、Quality、Release Operations 和可选 Acceptance Executor 等 AgentTemplate；模板只固定稳定 Instructions、Skills 和基础 Capability，阶段目标、输入、反馈和方法由 InvocationRequest 与 Harness Guide 按 Run 提供，平台不静默创建默认 Agent Team；
- Software Delivery SolutionPackage 的声明式体验将 Workflow 展示为 Delivery、WorkUnit 展示为 Task、选定 Observation 展示为 Evidence；这些视图不创建第二套控制状态；
- Requirement、Design 和 Release 通过通用 Artifact、Observation 与 GateResult 进入 Workflow 控制闭环；Preview Deployment 通过 WorkUnit/ComponentRun 进入同一闭环；外部 Promotion 通过 `StartPromotion`、不可变 Deployment 请求、Deployment SDK、DeploymentDriver 和追加式报告直接执行；
- Release Gate 通过前，软件交付页面只通过可重建的 `ReleaseReadinessView` 投影精确集成、构建、验证和 Gate 证据；它没有候选 ID、Version 或状态机。Gate Pass 后才形成不可变 Release，默认不增加一次隐藏的发布审批；需要人工业务取舍时使用显式 Decision Boundary；
- Preview 最终验收使用普通单主体 Decision：`SubjectKey.kind=software-delivery.preview-acceptance`、`id=deployment_id`，digest 固定当前 Release、实际部署摘要、Required health Observation 和 AcceptanceResult。该主体由现有事实确定性组装，不创建 PreviewAcceptance 聚合；任何输入变化都会使旧页面与旧 Decision 对当前 Gate 失效；
- 最终 `approve` 使最终验收 Gate 通过；当活动 DAG 中所有已选择节点均成功、互斥未选择节点均 Obsolete 后，Delivery 按 Kernel 固定规则收敛为 Succeeded，Release 显示为 Promotable。用户不需要再次点击“保持可推广”。后续 Kubernetes/CI-CD Promotion 不经过 Workflow Controller、Kernel、WorkUnit、ComponentRun 或 Harness；只有企业多步骤发布流程才显式使用 Workflow/DAG；
- 最终 `request_changes` 选择 Delivery Plan 中预先校验的条件分支，先进入复用现有 `architecture-design` Capability 的 Architect 技术变更设计、独立 Review 和用户 Decision，再进入后续 DAG Orchestration；Kernel 不判断业务返工位置，也不要求单独的返工或 ChangeImpact Capability；
- 调研学习参考 Solution 提供问题拆解、资料收集、来源校验、Critic Review 和用户确认；
- Generic Workflow Web/API/CLI 是所有可激活 Solution 的强制可运行基线，必须覆盖 Project Setup、Workflow 创建、运行观察、Review、返工、Decision、恢复与完成结果；
- SolutionPackage 可以提供声明式领域页面、术语和查询视图，但不拥有控制状态，也不能引入 Generic Web 无法表达的必需私有输入或写路径；
- 领域展示定义缺失、无法解释或渲染失败时，同一 Project/Workflow 直接使用 Generic Web，通用入口始终保留。

## 5. 系统架构：云原生底座、双核心与类型化扩展

![Open Agent Cluster System Architecture](../assets/diagrams/overview/open-agent-cluster-system-architecture.svg)

系统架构把第 1.2 节的“1-2-3-3”结构落实为组件和控制路径：Kubernetes-native 控制面承载长期活动与物理执行，Platform Core 和 Orchestrator Kernel 分别守住产品事实与生命周期，Application Package、Runtime SDK 和 Workspace Port 隔离变化，Web、`oactl` 和 Platform API 共享同一产品边界。

| 顶层抽象 | 主要实现位置 | 必须保持的不变量 |
| --- | --- | --- |
| 云原生底座 | Workflow CRD、Workflow Controller、Execution Host、Job/Pod/PVC、PostgreSQL、Artifact Storage | Kubernetes 管运行，OAC 管业务语义；Workflow CR 是唯一活动控制文档 |
| Platform Core | Platform API、应用用例、目录、治理、历史、读模型和 Workflow Command API | Platform Core 是 Workflow CR 唯一物理写入者，不计算生命周期 |
| Orchestrator Kernel | Plan Compiler、Lifecycle Core、Kernel Runtime Facade、AgentWorkChannel / `oacok` | Kernel 只依据显式事实产生确定性动作，不持久化产品状态或执行外部副作用 |
| 三类扩展面 | Package/Extension Management、Runtime SDK/Driver、WorkspacePort/Adapter | 每类变化使用类型化契约，不能演化为万能插件或第二套控制模型 |
| 三个操作入口 | Generic Web、`oactl`、Platform API | 所有入口调用同一组用例并经过相同授权、审计、幂等和 CAS 规则 |

系统包含两条明确且不混用的控制路径。需要依赖、Gate、返工或多 Agent 协作的目标进入 Workflow：用户命令先进入 Platform Core，经过治理后写入 Workflow；Workflow Controller 观察变化并调用 Kernel；物理结果写回所属 Run 后再次触发协调。无需编排的单次领域外部操作则由对应 ApplicationCommand 直接投递到窄 Host，例如 `StartPromotion → Deployment Host → Deployment SDK → DeploymentDriver`；它不创建 Workflow，也不能借直接端口隐藏多步骤流程。

### 5.1 三个操作入口与交互层

Generic Workflow Web 与 `oactl` 是同一个公开 Platform API 的两类客户端：人类用户主要通过 Web 操作，外部 Agent、自动化程序、服务身份和高级用户通过 `oactl` 或直接 API Client 操作。它们面向所有 Solution，并调用同一组 Platform Core Use Case：

| 入口 | 概要职责 |
| --- | --- |
| 公开网站与技术文档 | SSG 只读入口；使用稳定页面身份关联 `zh-CN`/`en-US` 内容，展示产品、行业方案、案例与安装文档，并把访客分流到安装或产品 Console；不进入 Platform Core 或 Kernel |
| Generic Web | 公开 Platform API 的人类交互客户端；提供首次管理员初始化、邀请式用户准入、登录/退出、Scope 进入、Unified Settings、Agent Center、Project/Solution Setup、Platform Operations 与 Workflow Operations，展示平台健康、活动告警、执行容量、目标、DAG、Artifact、Observation、Gate、Decision、Run、Usage 和恢复状态，并提交用户命令、人工 Run 结果与正式 Decision |
| Platform API | 唯一公开北向业务入口；统一提供 Command、Query、Read Model、人工 Run 提交、Decision 与持久化事件通知接口，命令接受结果与状态收敛结果分开表达 |
| `oactl` | 公开 Platform API 的外部 Agent/自动化客户端；按 `identity`、`operations`、`skill`、`model-provider`、`mcp`、`credential`、`project`、`agent`、`team`、`solution`、`workflow`、`workunit`、`artifact`、`component`、`decision`、`run`、`usage` 和 `audit` 领域组织命令 |
| Solution 领域体验 | SolutionPackage 内的声明式表单、Renderer 元数据和只读视图映射，例如软件交付的 `delivery`、`task`、`release` 和 `deployment` 入口；只能增强 Generic Web，不能成为 Solution 运行依赖 |

Web Session、OIDC/SAML/LDAP、本地凭证与 `oactl` 用户 Token、服务身份或 Workload Identity 只在 AuthenticationDriver 输入和交互方式上不同。实现 `authentication.oac.dev/v1` 的 ExtensionPackage 负责协议，普通可变 `AuthenticationMethod` 负责一个实际配置入口；Driver 统一输出 AuthenticatedSubject，Governance 再按精确 `authentication_method_id + issuer_key + subject_key` 通过 ExternalIdentityLink 解析 Principal、创建 Session/RequestContext。认证发生在 Scope/Project 之前，Project、Solution 和 Workflow 不能选择登录方式。认证成功后的入口都复用同一 Authorization、Execution Admission、幂等、CAS、事务、AuditEvent、Outbox 和读模型；要求 human Principal 的 Decision 仍拒绝不满足资格的外部 Agent 服务身份。Web BFF 和 CLI 只负责交互适配，不拥有领域规则，也不能直接访问数据库、CRD、Controller 或 Kernel。

首版本地身份采用受控邀请而不是公开注册：安装器生成一次性 Bootstrap Code，Web 原子创建首个本地管理员和唯一 Local Scope；普通用户由管理员邀请并自行设置密码，Invitation 已表达准入，不再经过第二次审批。配置邮件时发送一次性链接，未配置时由管理员复制只展示一次的链接/Code。唯一授权 Scope 在登录后自动进入；身份支持记录、Browser Session 和恢复 Challenge 属于 Governance/Authentication 边界，不进入 Workflow 或 Kernel，也不创建 Version 聚合。

公开网站和技术文档位于产品业务入口之前。它们通过静态内容清单维持同一页面的 `page_key`、当前 `locale` 与翻译可用状态；语言切换保持页面身份，缺失翻译明确回退到同一页面的英文内容。该表面不需要 AuthenticationDriver、RequestContext 或 Platform API，也不创建平台对象；用户选择“进入控制台”后才进入认证路径，选择“开始安装”则进入部署文档与安装路径。

Generic Web 通过 SolutionPackage 的声明式 Project/Workflow Schema、本地化展示元数据、标准 `allowed_actions[]` 和安全 Artifact Renderer 提供完整通用体验。SolutionPackage 发布验证必须先证明该体验可运行；领域展示定义无法解释或渲染失败时回退到同一对象的 Generic Web，不产生第二套业务状态或恢复流程。

OAC 内部执行 Agent 不属于该北向交互路径。它在 Execution Host 创建的 AgentRun Job 中运行，只通过 Orchestrator Kernel 随附的 `oacok` 和 run-scoped AgentWorkChannel 读取当前 InvocationRequest、Guide、精确资源并提交 WorkSubmission；内部 Agent 不需要也不获得 `oactl` 的 Platform Principal 或 Token。

交互层把讨论和正式操作分开。评论、聊天和 Agent 消息可以帮助人理解情况，但暂停、取消、风险授权、成果接受和最终验收必须形成 ApplicationCommand 或 Decision，并关联精确的 Workflow、WorkUnit 或 Artifact ID/digest。

平台内置 `ApplicationHost` 位于 Web 交互层，只解释精确 `SolutionPackage.experience` 中受支持的表单、视图、布局、Renderer 和术语，并把页面动作绑定到同一公开 Platform API / Platform Core Use Case。它不是独立 SolutionApplication、Package、Extension 或后端服务。软件交付领域页面可以把通用对象展示成 Delivery、Task、Release 和 Deployment，却不能直接访问数据库、CRD、Kernel 或执行 Provider。这样，无论人类从 Generic Web/行业页面，还是外部 Agent 从 `oactl` 操作，同一条命令都经过相同的授权、审计和状态收敛过程。

### 5.2 Platform Core

Platform Core 是产品语义、治理和写入边界。用户创建 Workflow、提交 Decision、安装组件或查询历史时，面对的都是这里定义的应用用例和统一产品模型。逻辑上包含：

- Solution Definition Module；
- Project & Team Module；
- Workflow Module；
- Extension Management Module；
- Agent Capability Catalog Module；
- Governance Module；
- Settings Application Facade / Read Model；
- Query/History Module。

Settings Application Facade 只聚合 Skills、Model Providers、MCP 和 Credentials 的查询、导航与可重建能力就绪视图；它不拥有统一 Settings 表，也不接受绕过各模块的万能写命令。Extension Management 管 Skill Content，Agent Capability Catalog 管 Provider/Model/MCP 配置与 Probe，Governance 管 CredentialBinding 和 Secret 使用。字段值由用户、当前上下文、系统、Connector/Provider 或 Policy 分别产生，Facade 不把完整持久化对象反向生成成巨型用户表单。

Query/History Module 还提供只读 `QueryPlatformOperations` 用例。它通过内层定义的窄查询端口并行读取 OAC 服务健康、Kubernetes 调度与容量、数据/存储 Probe、当前产品运行事实和可选外层可观测来源，组装瞬时 `PlatformOperationsView`。该视图只包含综合健康、数据新鲜度、活动告警、执行容量和修复导航，不创建 PlatformHealth、Alert、CapacitySnapshot、Dashboard 或第二套监控权威。缺失或过期事实保持 `unknown`；Web 与 `oactl` 使用同一公开 Query，Kernel、Workflow Controller 和 Platform Core 写路径均不参与其计算。

Project Setup 预检查由 Platform Core 的 `EvaluateProjectSolutionSetup` 应用用例承接。Solution Definition Module 先根据 Generic Form Field Type Catalog 规范化 `project_setup_schema` 值；字段类型自动决定需要调用的窄检查端口，例如 Repository Probe、Credential readiness/Policy 和 Environment/Target Probe，Solution 作者不另填 Probe Capability 数组。Project & Team Module 定义瞬时 `ProjectSetupCandidate` 与 `ProjectSetupEvaluation` 合同；用例负责授权、候选摘要、并行调用 Solution/Package Resolver、Agent Capability、类型化资源检查、Runtime/Workspace 与 Policy 端口，再汇总五组检查、blockers 与 warnings。各端口只负责自己的事实，Web 不复制校验规则，Kernel 与 Workflow Controller 不参与该流程。该用例不写 Project 配置版本、Workflow CR 或执行资源；Credential 使用仍按治理要求留下审计记录。

物理上的 `Deployment/oac-platform-core` 是无状态 API 服务，也是 Workflow CR 的唯一物理写入者。它接收用户命令和各语义所有者提交的类型化资源命令，校验字段归属、幂等身份、授权和版本条件，再精确更新 `spec`、`status` 或 `finalizer`。需要先提交 PostgreSQL 事实再更新 Workflow 的业务命令通过 Transactional Outbox 交付，Outbox 只负责可靠送达已经作出的决定。

Workflow 是否 Ready、某个报告是否通过 Gate、失败后进入返工还是终态，都由 Controller 调用 Kernel 计算。Platform Core 不监听 Workflow 变化，不创建 Job，也不把报告解释成生命周期决定。这个分工让产品模型只有一个写入口，同时避免 API 服务和 Controller 各自维护一套编排逻辑。

### 5.3 Orchestrator Kernel

正式模块名是 `Orchestrator Kernel`；`oacok` 仅为随模块交付的 Agent 协作 CLI。该模块必须同时承接三个核心能力：

- 编排：接受已有 DAG，或从自然语言 Goal、已有不可变设计文档生成 DAG，并推进 Harness、Gate、Review、返工与收敛；
- 协作：通过 AgentWorkChannel 与 `oacok work show/guide/read/submit` 提供统一 Agent 协作协议；
- 连续性：通过稳定 WorkUnit、iteration、Lineage、Run conclusion 和 ReviewResult 保持多 Agent 交接的同一任务线。

Orchestrator Kernel 把明确 DAG、显式无图 Goal 或已有文档输入转换成可验证、可执行的工作图，并根据显式事实推进通用生命周期。它作为深层可复用模块分为：

- Kernel Core：PlanningRules Compiler、Plan Compiler 和 Lifecycle Core；
- Kernel Runtime Facade：Executor Matcher、typed Input Resolver、InvocationRequest Assembler、Submission Validator 和有限步 Coordinator；
- Agent Collaboration Protocol：传输无关 AgentWorkChannel 与标准 `oacok` CLI Adapter；
- 可选 Driver：Controller Driver 或 Local `runUntilBlocked` Driver。

PlanningRules Compiler 先把通用约束、当前计划、Workflow 状态和已确认事实编译成机器可读规则；Plan Compiler 把 DAG Orchestrator 产生的 PlanDraft 当作不可信输入；Lifecycle Core 负责 Ready、Gate、返工、激活 Active Plan 已声明的 DAG 修订路径、Plan 切换和收敛。Kernel Runtime Facade 从 HostCapabilitiesSnapshot 匹配逻辑 Executor，只解析 typed input bindings 指向的确认输出，组装不可变 InvocationRequest，并校验 WorkSubmission。`WorkflowInput.kind=plan|goal|document` 分别表示已有 DAG、从自然语言目标生成 DAG、从已有不可变文档生成 DAG；后两者都先形成一个可见且经同一 Plan Compiler 校验的 bootstrap DAG Orchestration WorkUnit。普通项目直接使用 `registerExecutor + advance/runUntilBlocked`，不需要手工串联 Compiler 或实现完整 HostPort。

Kernel 不持有产品数据库、Agent 句柄、Runtime、Workspace 或 Kubernetes Client。它只看逻辑 `executor_key`、能力、合同、effect/recovery 和显式 availability；OAC Platform Core 再映射为精确 AgentDefinition、Extension 实现、程序或人工执行。它以内嵌库形式运行在 Workflow Controller 中，因此不需要独立部署，也不会经过 Harness Host 调用。

### 5.4 Kubernetes-native Workflow 控制面

活动控制由 Workflow CR 和无状态 Controller 共同完成。Workflow CR 保存“当前事实是什么”，Controller 负责在资源变化后重新计算“接下来应该做什么”：

- `Workflow` CRD：`metadata.name` 是 Workflow 身份；`spec` 保存期望状态和固定输入，`status` 保存 Workflow 状态、Active Plan、WorkUnit 定义与直接状态、每次 Run 的有界摘要、Conditions 和 Outcome；
- `Deployment/oac-workflow-controller`：运行 `controller-runtime` Manager 与 OAC `WorkflowReconciler`，只 Watch Workflow，每次事件调用 Kernel Runtime 的一次有限 `advance`/`reconcileOnce`，并向 Platform Core 提交 Controller-owned 类型化命令。

`WorkflowOrchestrationHost` 位于 Reconciler 内部，负责把产品 Workflow 状态及固定执行目录映射为 Kernel DTO/HostCapabilitiesSnapshot，再把 LifecycleAction 映射为 Platform Core 命令或外部执行请求。计划校验、Executor 匹配、input 解析、InvocationRequest 组装、Ready、Gate、返工和收敛规则只存在于 Kernel；Host 只做产品边界装配。

Controller 本身不保存恢复所需的进程内状态。进程或节点故障后，新实例重新列举 Workflow，读取现有 Run 报告、Observation、Decision 和定时条件即可继续协调。首个版本默认单副本运行，External Cluster 可以启用 Leader Election；未来多 Active 分片仍以 Project 为主要分区边界。

### 5.5 Execution Host 与执行边界

当 LifecycleAction 需要真正调用 Agent、程序或外部系统时，Execution Host 负责把已经确定的产品执行选择物化为 Kubernetes Workload。Kernel Action 已携带逻辑 `executor_key` 和不可变 InvocationRequest；Platform Core 在进入 Execution Host 前把它解析为精确 Agent、Runtime、组件、资源和幂等身份。Execution Host 不在执行阶段重新做业务选择或重组工作协议。AgentRun 还获得 run-scoped AgentWorkChannel binding，`oacok` 只通过该 Channel 工作。

- `Deployment/oac-execution-host` 是无状态执行服务，接收幂等的 start、observe、cancel 请求，并使用 Kubernetes API 创建标准 Job、PVC 和必要的执行资源；
- Platform Core AgentRun 创建用例负责把产品执行事实固定为协议中立启动输入，AgentRun Job 负责把该输入映射为一次 Runtime SDK 调用；
- Workspace Adapter 负责产品责任和资源需求到内部 WorkspacePort 的唯一映射；
- AgentRun Job、Harness Host、Authentication Host 与 Deployment Host 分别通过对应 SDK 调用扩展能力；其中 RuntimeDriver 只由 AgentRun Job 进程内调用；
- AgentRun Job 将 Runtime SDK 事件/终态提交 Platform Core AgentRun API；Platform Core 先持久化 RuntimeEvent 映射、Artifact、Usage 和报告，再只更新所属 AgentRun 的 `status/report` 子树；
- Harness Host 解释并提交当前 ComponentRun 的专用报告，不经 AgentRun API；
- Execution Host 内的 Job Observer 观察 Job、Pod、PVC，并向 Platform Core 提交只针对所属 Run 的 `infrastructure` 命令；
- Execution Host 不写 PostgreSQL 或 WorkUnit 业务状态；Runtime Pod 不持有 Execution Host 的高权限 ServiceAccount，也不能直接访问 Workflow CRD；
- 外部系统只能返回结果、事件、成果或外部引用，不能直接推进 Workflow 控制状态。

Harness Host 向 SDK 提供包中立、opaque 的 invocation handoff，固定精确 ExtensionPackage release/artifact、entrypoint、binding/config、operation、owner、subject、Workflow/WorkUnit/iteration/ComponentRun scope 与 request/service-payload digest；Gate handoff 还固定 policy revision、evaluator registration/release 和 evaluator ID。Harness Host 持有 issuer/trust、有效期/轮换及 replay/restart 验证责任。SDK 将操作测量与 sealed `ExecutionReceipt` 共用不可变递归 lineage tree；树形数据和普通 SDK conformance 结果本身不授予 Host 权限。JSON 解码或复制收据投影不会恢复私有 seal，每个节点须独立封存；子树仅能在同一规范父身份下复用，nil/empty 子列表保持历史 canonical digest 兼容。

这组边界在故障场景中尤其重要。假设部署请求已经发往外部平台，但调用方在收到响应前中断，ComponentRun 会保留“结果未知”，Executor 下一次先 observe 外部状态，再决定补报成功、继续等待或重新执行。平台不会因为 Job 重启就重复创建部署，也不会让外部系统直接宣布整个 WorkUnit 成功。

Software Delivery 的直接外部 Promotion 复用同一个物理 `oac-execution-host`，但不复用 Workflow Run 合同。Platform Core 在 `StartPromotion` 事务中保存不可变 Deployment、AuditEvent 和 Outbox；Execution Host 内的 Deployment Host 读取固定的 Release、target configuration digest、精确 DeploymentDriver 和 request digest，通过 Deployment SDK 调用 `start/observe/cancel`，再通过 `SubmitDeploymentReport` 追加结果。该路径不经过 Workflow Controller、WorkflowOrchestrationHost、Kernel、Harness Host 或 ComponentRun。外部结果 Unknown 时由 Deployment Host/应用恢复路径继续观察，不触发 Workflow Reconcile。

直接 Deployment 的恢复不增加第二套状态机：自动或人工 Observe 始终复用原 Deployment；`Unknown` 和 `Cancelling` 继续占用目标并发键，禁止重试或部署旧 Release；`Failed`、`Cancelled` 才表示已知终态。用户重试同一 Release 或选择旧 Release 时，Software Delivery Application 重新调用 `StartPromotion` 创建一条新的不可变 Deployment，并以 causation 串联历史。原 Deployment、来源 Workflow 和 Release 都不被覆盖，也不存在 `RolledBack` 状态。

### 5.6 基础设施

基础设施层分别保存活动控制、不可变事实和大体积成果。三类存储承担不同职责，恢复时相互补充，但不复制同一份活动状态：

- Kubernetes API/etcd：活动 Workflow `spec/status`、资源 Watch 和声明式控制对象；
- Kubernetes 内置控制器、Scheduler 与 Kubelet：Job/Pod/PVC 生命周期、节点放置和容器运行；
- PostgreSQL：Project、Team、Solution、Component 目录，不可变 Decision/Audit/Event/Usage、历史与查询模型；
- Artifact Storage：大体积成果与内容；
- 外部 Runtime、模型、Git、CI/CD、Registry、部署目标和企业 Provider。

活动控制元数据完整保存在 Workflow CR 中，Controller 可以只依赖 API Server 重建协调上下文。PostgreSQL 提供目录、审计、历史和查询，Artifact Storage 保存不适合进入 CR 或数据库行的大内容。超大 DAG 通过拥有型 Child Workflow 分解，每个子 Workflow 管理自己的活动状态；首个版本不把活动控制状态外置到 PostgreSQL，也不为 WorkUnit 建立影子 CRD。

## 6. 关键模块协作

模块之间通过几条可重复执行、故障后可以继续的协作链连接起来。本章展开计划如何成为活动版本、WorkUnit 如何从 Ready 走到 Gate，以及用户决定和子 Workflow 如何进入同一套循环。

### 6.1 计划生成、校验和评审

![Orchestration Plan Lifecycle](../assets/diagrams/overview/orchestration-plan-lifecycle.svg)


用户提交的是目标，不是可信的执行图。根 Workflow 创建时，Platform Core 先把精确 Solution 的 BaselinePlanTemplate 实例化并通过通用 Plan Compiler 静态校验，形成带 `fixed-baseline` provenance 的初始 Plan；该固定结构已经随 SolutionPackage 通过确定性发布检查，不再为每个 Workflow 伪造初始 DAG Orchestrator/Plan Reviewer。后续 DAG Orchestrator 提出的动态图扩展仍按“编译 → 独立评审 → Gate → 激活”处理：

1. 当前 Plan 中显式 DAG Orchestration WorkUnit 的依赖、可选 `activation` 和输入绑定满足后，Lifecycle Core 将其计算为 `Ready`；运行中需要重新编排时，也必须由当前 Plan 预先声明对应的 Decision/Gate 分支及后续 DAG Orchestration WorkUnit；
2. Kernel Runtime 按该 WorkUnit 的责任、`orchestration.dag` 能力与合同匹配逻辑 Executor，只从依赖闭包解析 `input_bindings[]` 对应的已确认输出，并组装不可变 InvocationRequest；
3. Platform Core 把逻辑 Executor 映射为精确执行对象；DAG Orchestrator 根据其不可变 Instructions/Skills、PlanningRules、WorkUnit Contract、该节点普通 Inferential Guide 和 OrchestrationContext 产生 PlanDraft，WorkUnit 从 `InProgress` 进入 `InVerification`；
4. Plan Compiler 执行 Schema、DAG、依赖、成果覆盖、权限边界和收敛检查；
5. 编译失败时进入 `VerificationFailed`，诊断结果进入 DAG Orchestrator 下一轮 InvocationRequest feedback，再转入 `InRework`；不启动 Reviewer；
6. 编译通过后进入 `InReview`，由独立 Reviewer 检查业务完整性、拆分质量、依赖合理性和风险；Reject 明确进入 `ReviewRejected` 再返工，不回到 `Ready`；
7. Planning Gate 根据编译结果、PlanReviewResult 和必要用户 Decision 作出判断；
8. Rework/Reject 创建新的 DAG Orchestration WorkUnit iteration，并优先恢复 DAG Orchestrator 自己的 Session；
9. Pass 后形成 `parent_plan_id` 指向当前 Plan 的新不可变完整图快照，再以 expected state revision/resourceVersion CAS 更新 `activePlanId`；竞争失败的 Plan 保留为未激活历史候选；
10. UI 依据稳定 WorkUnit identity 把 Plan 链展示成一张持续扩展的 DAG；运行时需要调整计划时，通过另一个显式 DAG Orchestration WorkUnit 重复同一链路。

DAG Orchestration WorkUnit 与普通 WorkUnit 使用相同的依赖、execution、Harness、Review、Gate、返工和 Lineage 机制，并且只能由已校验、已激活的 Plan 显式声明。类型化 GateResult/Decision 只满足该 Plan 中已有节点的 `activation`；Plan 耗尽、活动图未成功收敛或 `activePlanId` 本身都不能创建节点或触发图外规划。这样，每份 Plan 都有独立 ID/digest、生产者、验证结果和前序 Plan 关系，而不是一段覆盖旧内容的 Agent 输出。

### 6.2 WorkUnit 执行闭环

计划激活后，平台不会一次性把所有节点都交给 Agent。Lifecycle Core 只释放依赖已经满足的 WorkUnit，并在每轮执行结束后重新读取事实。一个典型 WorkUnit 会经历以下过程：

1. Lifecycle Core 根据硬依赖和 Gate 计算 Ready WorkUnit；
2. Kernel Runtime 根据 WorkUnit `execution` 和 HostCapabilitiesSnapshot 匹配逻辑 Executor，解析 typed inputs，并组装不可变 InvocationRequest；
3. Platform Core 把 `executor_key` 映射为 Workflow 固定目录中的精确 AgentDefinition/Component/程序或人工资格要求，并执行 Authorization 与 Admission；
4. Agent、组件或程序分支由 Workflow Controller 调用对应 Host 物化执行，Agent Runtime 注入 `oacok` bootstrap instruction 与 Channel binding；人工分支只保存 `kind=human` Run request 和 `principal_requirement`，由“我的工作”/Attention 读模型向合格用户开放，不创建 Job、Session 或 Workspace；
5. Platform Core AgentRun event/completion use case、Harness Host、Platform Core human-result use case 和 Execution Host 分别把已持久化的 AgentRun、ComponentRun、人工结构化结果与基础设施结果写入所属 Run 子树；
6. Workflow 资源变化触发下一轮 Reconcile；Gate 计算 Pass、Rework、AwaitDecision、Reject 或 Error；
7. Lifecycle Core 产生下一步 LifecycleAction，Controller 向 Platform Core 提交对应的状态命令；
8. 上游通过后下游才可能进入 Ready。

例如，开发 Agent 提交代码并正常退出，只会形成 AgentRun 结果和代码 Artifact。测试 Sensor、Review Result 或必要的用户 Decision 尚未到达时，该 WorkUnit 仍停留在验证、评审或等待状态；依赖它的 Release WorkUnit 也不会提前进入 Ready。

### 6.3 Human-in-the-loop

平台区分两类人工参与：

- **人工责任执行**：人作为 Reviewer、显式人工 Acceptance WorkUnit 执行者或其他逻辑 Executor 完成一个有边界 Run。系统创建 `kind=human` Run request，所有满足 `principal_requirement` 的授权用户都能看到同一固定 InvocationRequest；首个通过 request digest、output contract、独立性、幂等和 WorkUnit revision CAS 的结构化提交成功。ReviewResult/AcceptanceResult 先成为 Observation，再由 Gate 判断，不新增 HumanTask/HumanRun 聚合或领取/转派状态机。
- **正式业务 Decision**：人在风险授权、范围选择、成果接受和最终验收等位置，针对精确 Workflow、WorkUnit 和 Subject ID/digest 作出 approve、request_changes、answer、reject 或 abandon。Decision 是下一轮状态判断的显式输入。

两者不能互相替代。人工 Reviewer 的 pass 仍不能代替要求用户批准的 Decision；用户点击最终批准也不能伪造缺失的 ReviewResult 或 AcceptanceResult。评论、聊天和临时消息可用于讨论，但不会改变控制状态。

### 6.4 父子 Workflow

当一项工作本身需要独立编排、多个责任协作和自己的验收闭环时，父 WorkUnit 可以通过 `execution.kind=child-workflow` 创建子 Workflow。子 Workflow 拥有自己的控制状态、计划、Agent Team 绑定和执行上下文；父级只提供约定输入，并等待结构化 WorkflowOutcome 返回。

父级不会读取子级内部 WorkUnit 来推断完成情况，子级也不能直接修改父级状态。首个版本只允许同一 Project 内的拥有型子 Workflow，以保持授权、组件解析和恢复边界清晰。

### 6.5 一个用户任务如何运行

用户口中的“一个任务”在平台顶层对应一个 Workflow；动态计划拆出的执行节点对应 WorkUnit；某个 WorkUnit iteration 按 `execution=executor|child-workflow` 匹配逻辑 Executor 或创建 ChildWorkflowLink。Executor 分支统一创建 `kind=agent|component|human` 的 Run 摘要，Agent/Component 再关联各自专用记录，human 只使用嵌入值。各层对象分开后，用户目标、业务工作和物理执行不会被混成同一个状态机。

#### 6.5.1 逻辑视角

1. 用户检查所选 Solution/AgentTemplate 的能力依赖；内置 Skill 与已有模型目录直接复用，只有缺少必需能力时才在 Settings 导入 Skill、配置并测试 Model Provider/MCP 或创建 CredentialBinding。随后用户在 Agent Center 发布 AgentDefinition，在 Project Setup 中选择 Solution、填写长期参数和 Agent 岗位，并让 Platform Core 对尚未保存的候选配置执行 `EvaluateProjectSolutionSetup`。当前 `candidate_digest` 对应的检查通过后，平台以一个 CAS 命令原子更新 `Project.solution_setup`。用户通过 Web、API 或 `oactl` 提交本次最小目标和可选上下文后，Platform Core 再次校验，固定精确 Solution release、Project 配置、Agent Team、组件以及模型/MCP 非敏感配置，实例化并编译 BaselinePlanTemplate，然后创建已带初始 `activePlanId` 的 Workflow。
2. Kernel 根据固定基线 Plan、依赖和既有 GateResult 计算当前 Ready WorkUnit；上游没有通过时，下游保持等待。
3. 当图中的显式 DAG Orchestration WorkUnit Ready 时，Kernel Runtime 匹配 `orchestration.dag` Executor并组装 InvocationRequest；DAG Orchestrator 通过 `oacok` 获取目标、精确文档/上游资源和协作协议后产生 PlanDraft，Kernel 静态编译，独立 Reviewer 评审，Planning Gate 决定是否形成并激活新的不可变 Plan 快照。
4. 新 Plan 通过 `parent_plan_id` 延续旧图，`activePlanId` 只切换当前快照；前端把它展示为同一 DAG 的扩展。
5. Kernel Runtime 为 Ready WorkUnit 解析 `execution`、匹配逻辑 Executor、解析输入并组装 InvocationRequest；Platform Core 再解析精确 Agent/Extension/程序/人工执行对象。若选中 Extension-backed Executor，受限 `agent.invoke` 最多为当前 ComponentRun 创建一个平台治理的 AgentRun；多 Agent 协作必须拆成多个 WorkUnit 或 child Workflow。Producer、Reviewer、返工责任和显式 Acceptance WorkUnit 的 Agent/程序/人工结果通过结构化 Observation、ReviewResult 或 AcceptanceResult 连续交接。
6. 执行结果先形成 Event、Artifact、Observation、Review Result 和 Usage；Agent 自己声明“完成”不能直接改变 WorkUnit 状态。
7. Gate 根据程序检查、独立判断和必要的用户 Decision 得出 Pass、Rework、AwaitDecision、Reject 或 Error；Kernel 据此产生下一步 LifecycleAction。
8. Rework 沿原责任时间线继续，范围变化重新进入规划链；全部必要结果通过后，Workflow 才形成最终 Outcome 并进入用户验收或终态。

#### 6.5.2 物理视角

1. Web、API 或 `oactl` 请求进入 `Deployment/oac-platform-core`。服务完成授权、Admission 和审计后，通过类型化资源命令创建或更新 Workflow。需要保证 PostgreSQL 事实与 CR 最终一致的持久业务命令使用现有 Outbox；心跳和进度等瞬时信号限频、幂等、CAS 写入但不逐条进入 Outbox。Outbox 只发送已决定命令，不驱动编排循环。
2. Kubernetes API Server 保存资源变化，`controller-runtime` Watch/WorkQueue 调用 `Deployment/oac-workflow-controller` 中的 `WorkflowReconciler.Reconcile`。
3. Reconciler 通过内部 `WorkflowOrchestrationHost` 从完整的 `Workflow.spec/status` 组装 Kernel DTO 和 HostCapabilitiesSnapshot，再调用一次有限 `advance`/`reconcileOnce`。Kernel 完成纯编译、Executor 匹配、typed input 解析、InvocationRequest 组装和生命周期计算，不创建 Pod、不连接 Runtime，也不直接写产品存储。
4. Workflow Controller 幂等应用 LifecycleAction：纯控制动作转换为 Platform Core 的 Controller-owned 命令；需要目录、治理或不可变记录时调用对应 Platform Core Use Case；Agent/Component/程序外部执行调用相应 Host；人工执行只创建 human Run request。
5. 非人工分支按需由 Execution Host、内置 Workspace Port、AgentRun Job/RuntimeDriver 或 Harness Host 物化。Execution Host 使用 Kubernetes API 创建标准 Job/PVC；Kubernetes 内置控制器完成 Pod 创建与调度。
6. Runtime 在隔离 Workspace 中执行；Harness Extension 经 Harness Host 调用程序或外部系统；人工用户在 Web/API 中读取同一 Run.request；`child-workflow` 绑定创建独立子 Workflow。
7. AgentRun Job 调用 Platform Core AgentRun event/completion API；Harness Host、human-result use case 和 Job Observer 分别提交自己拥有的 Run report/infrastructure。人工提交先校验 exact Run、Principal、独立性、request digest、output contract、幂等和 CAS，再保存 Observation/Audit。它们都不能直接推进 WorkUnit 或替代 Gate/Decision。
8. Run 子树、Decision、desiredState 或定时字段发生变化后，Workflow Watch 再次触发协调。Controller 重启只需重新列举 Workflow；Pod phase 或 Job Condition 必须先被 Execution Host 投影，且本身不能宣布业务成功。

## 7. 数据所有权与一致性边界

多条执行链会同时向同一个 Workflow 汇报：Controller 推进 WorkUnit，Platform Core AgentRun event/completion use case 汇报 AgentRun，Harness Host 汇报 ComponentRun，Platform Core human-result use case 汇报人工 Run，Execution Host 汇报基础设施状态。数据所有权需要把三个问题说清楚：每个字段由谁解释，通过哪条命令写入，发生冲突时依据什么规则处理。

| 数据 | 所有者 |
| --- | --- |
| Project、Agent、Team、Solution 与 Component 目录 | Platform Core 对应领域模块；PostgreSQL 持久化 |
| SkillPackage release 与 Content 安装状态 | Extension Management Module / Content Catalog |
| ModelProviderConnection、ModelCatalogSnapshot、McpServerDefinition 与 Probe/Snapshot | Agent Capability Catalog Module |
| Settings 聚合导航与展示 | Settings Application Facade / rebuildable Read Model；不拥有跨域写入 |
| Workflow 期望状态 | `Workflow.spec`；Platform Core 拥有用户命令语义，Controller 拥有子 Workflow 系统命令语义；均由 Platform Core 物理写入 |
| Workflow 状态、Active Plan、WorkUnit 定义与直接状态、Gate、Outcome | `Workflow.status`；Workflow Controller 拥有语义，Platform Core 物理写入 |
| WorkUnit Run request 与 handled sequence | `Workflow.status.workUnits[*].runs[*]`；Workflow Controller 拥有语义，Platform Core 物理写入 |
| AgentRun 业务结果、报告与事件摘要 | 同一 Run 的 `status/report`；Platform Core AgentRun event/completion use case 拥有该子树语义并物理写入 |
| ComponentRun 业务结果 | 同一 Run 的 `status/report`；Harness Host 拥有语义，Platform Core 物理写入 |
| human Run 业务结果 | 同一 Run 的 `status/report`；Platform Core human-result use case 拥有语义并物理写入，ReviewResult/AcceptanceResult 另存为 Observation |
| Run 基础设施观察 | 同一 Run 的 `infrastructure`；Execution Host 拥有语义，Platform Core 物理写入 |
| Artifact、Observation、GateResult、Decision、Event、Audit、Usage 与历史 | Platform Core 对应记录服务；PostgreSQL/Artifact Storage 持久化 |
| Project、逻辑 Agent 和终态 Workflow 的归档元数据 | 对应 Project & Team / Workflow History Application；PostgreSQL 持久化，查询侧生成 allowed actions、引用摘要与删除 blockers |
| PlanDraft、ValidatedPlan、Plan、InvocationRequest、AgentWorkChannel 和 LifecycleAction 语义 | Orchestrator Kernel 定义；活动引用与 Run.request 由 Workflow Controller 保存 |
| AgentRun 和产品执行事件 | Platform Core AgentRun application use cases；AgentRun Job 只负责上传 SDK 事件与终态 |
| Runtime Session、Turn 和 RuntimeEvent | Runtime SDK / RuntimeDriver |
| Workspace 生命周期和内置实现状态 | WorkspacePort / Workspace Adapter |
| ComponentInstall、LockedComponentSet 和 ComponentRun | Extension Management Module |
| Authorization、Admission、Audit、Credential 和 Usage | Governance Module |
| Release 等 Workflow 业务视图 | Software Delivery Application；底层仍映射到通用成果和结果 |
| Deployment 请求、追加式报告和当前读模型 | Software Delivery Application；Preview 来源关联 WorkUnit/ComponentRun，外部 Promotion 来源关联 StartPromotion command；Platform Core 物理持久化，Deployment Host 只提交专用报告 |

每个字段只有一个语义所有者，Platform Core 统一承担物理写入。`status.workUnits` 与嵌套 `runs` 使用按 ID 合并的 Kubernetes map-list 语义；Platform Core 根据命令来源使用独立 field manager 和精确 Patch，只更新该来源拥有的字段。AgentRun event/completion use case 可以更新当前 AgentRun 报告，human-result use case 可以更新当前 human Run 报告，Execution Host 可以报告 Pod/Job 观察结果，但都不能覆盖 WorkUnit 状态、Gate 或 Outcome；Controller 可以推进生命周期，却不能伪造任何执行结果。

活动控制以 Kubernetes Workflow 资源为准。PostgreSQL 保存目录、不可变事实、历史和查询模型，为恢复、审计和查询提供证据，但不维护另一个可变 Workflow 状态机。Pod phase、日志和 Kubernetes Event 只说明基础设施发生了什么；它们必须先被投影到所属 Run，再由 Gate 结合业务合同判断影响。

## 8. 部署架构概览

产品提供 Compact 和 External Cluster 两种部署形态。它们改变的是基础设施规模、企业集成和扩缩容方式，不改变 Workflow 模型、Kernel 行为、组件合同或治理顺序。首版在 Linux、macOS 和 Windows 上统一通过安装器 CLI 完成检测、预检和部署，安装完成后统一使用同一个 Web 产品界面，不交付第二套 Desktop App 或桌面业务前端。用户可以从单机开始验证方案，之后迁移到现有 Kubernetes 集群，而不需要把业务流程重写成另一种产品模型。

### 8.1 Compact

Compact 面向个人、小型团队和方案验证环境。Linux 可以在没有现成集群时由安装器 CLI 安装本机 K3s；macOS 和 Windows 首版由同一 CLI 检测并复用已有兼容 Kubernetes，缺少环境时给出明确安装指引并停止，不静默安装虚拟机或 Desktop 产品。部署就绪后，用户通过浏览器中的 Web、公开 API 或 `oactl` 创建 Project、选择 Solution 并启动 Workflow。

- 面向个人和小型团队；
- 单台 Linux、macOS、Windows 宿主机或小型集群，统一使用安装器 CLI；
- Linux 本地模式可以安装 K3s；macOS 和 Windows 复用已有兼容 Kubernetes；
- 内置 PostgreSQL、单副本 Platform Core、Workflow Controller、Execution Host 和默认 Pod/PVC Workspace 实现，并从目标集群解析默认存储与入口能力；
- 内置 Codex 与 Hermes RuntimeDriver ExtensionPackage；
- 安装后提供 Generic Web、Platform API 和 `oactl`；
- 不交付 Desktop App；桌面系统与 Linux 使用同一 Web UI；
- 单机验收基线为至少 3 个并发物理 Run；
- 不强制对象存储、复杂队列或 Agent Sandbox。

### 8.2 External Cluster

External Cluster 面向已有 Kubernetes 基础设施的团队和大型企业。它复用组织现有的节点、存储、入口、Secret、身份和可观测体系，并允许执行面根据不同 Agent Workload 的资源需求独立扩展。

- 面向已有 Kubernetes 的团队和大型企业；
- 复用用户节点池、StorageClass、Ingress、Secret 和可观测体系；普通安装默认自动发现，只有不存在唯一安全默认值时才从候选项中选择；
- Platform Core、Execution Host 和 Web 可以独立扩缩容；
- 接入外部 PostgreSQL 和 Artifact Storage；
- 安装企业 AuthenticationDriver ExtensionPackage，并配置统一 AuthorizationPolicy；内置 CredentialStore 默认就绪，本地审计始终内置，可选 AuditExportTarget 使用系统内置导出类型，Execution Admission 使用 OAC 内置引擎并自动读取集群资源、安全和外部检查事实；
- Agent Workload 根据 CPU、内存、GPU、架构、存储和拓扑调度；
- 可以启用 Workflow Controller Leader Election。

例如，Compact 可以在 Linux K3s 或受支持的本地 Kubernetes 上并行运行三个代码、评审或调研 Run；External Cluster 则可以把 GPU 推理、普通代码执行和高隔离任务调度到不同节点池，配置企业 AuthorizationPolicy，接入企业 AuthenticationDriver、按需配置内置类型的 AuditExportTarget，并把集群资源或外部策略检查结果作为 Execution Admission 显式事实。两种模式都默认具备内置 CredentialStore，Workflow 在页面和 API 上具有相同语义。完整拓扑、安装、升级、监控、故障恢复和数据保护责任边界见[部署与运行设计](../deploy-ops/deployment-and-operations-design.md)。

### 8.3 OAC 系统发行版生命周期

OAC 自身的升级与回滚由产品控制面之外的统一 Installer CLI 承担。一个 OAC 系统发行版固定控制面/执行面/Web 镜像、Kubernetes 清单与 CRD、数据库迁移、安装管理工具，以及编译进系统镜像的 Orchestrator Kernel、公开 Extension SDK 和内部 WorkspacePort。它不等同于独立 Package release，不升级 Kubernetes、节点、外部数据服务、Project/Agent/Workflow 数据、Software Delivery Release/Deployment，也不替外部项目修改独立 SDK 依赖。

升级必须选择精确 SemVer + artifact digest 和精确 Kubernetes Context，先展示兼容性、迁移、回滚能力、活动 Workflow 与受影响 Workload 的瞬时摘要，再按“兼容扩展 → Platform Core → Execution Host → Workflow Controller → Web”滚动执行。首版维持 N/N-1 兼容，禁止不可逆迁移；回滚只允许恢复紧邻的上一精确系统发行版。整个路径不经过 Platform API、Workflow、Package & Extension Governance 或 Orchestrator Kernel，且不会改写活动 Workflow 的固定执行快照。

## 9. 非功能目标与演进边界

非功能目标围绕长期运行展开。系统不仅要在正常路径上完成一次演示，还要在重启、重复回调、外部结果未知、组件升级和资源紧张时保持同一套业务判断。

### 9.1 可靠性

可靠性的基准是“中断之后知道已经发生了什么，并能安全地决定下一步”。因此，恢复依赖 Workflow、不可变事实、幂等身份和外部观察，而不是要求原 Agent Session、Controller 进程或 Pod 永远存在。

- 所有控制动作具备稳定幂等身份；
- 外部结果未知时先 Observe 再 Retry；
- Convergence Policy 规定最大循环次数、等待条件和终态出口，返工不能无限持续；
- 关键运行事件确认持久化后才允许 AgentRun 进入终态；
- Workflow Controller 不保存恢复所需的进程内状态，重启后通过 API Server 重新列举并协调；
- Session 和 PVC 只提升恢复效率，不承担唯一正确性。

### 9.2 扩展性

扩展性并不意味着所有能力都塞进一个通用调用接口。三种 Package 共享安装治理，Harness、Runtime、Authentication 和 Deployment 通过各自 SDK 演进；Workspace、Audit Export 与 Credential Storage 首版保持内部 Port。

- SolutionPackage、ContentPackage 和 ExtensionPackage 使用同一 Package Envelope；ExtensionPackage 通过 `interface_api` 选择类型化 SDK；
- 内置 Package 与公开 Extension API 实现走相同生命周期；平台内部 Port 实现随系统发行版管理，不伪装成可安装 Extension；
- 活动 Workflow 通过 `LockedComponentSet` 固定精确 Package release 与内容 digest，升级不原地替换；同一目标需要采用新 release 时创建后继根 Workflow；
- 新行业方案不能增加私有控制状态和绕过 Gate。

### 9.3 安全与治理

无人值守执行会放大凭证和外部副作用风险，因此治理必须进入每次命令和主动执行的主路径。身份只回答“谁在请求”，授权、执行准入、凭证解析和审计分别回答“能不能做、当前资源与风险是否允许、可以拿到什么、最后发生了什么”。

- 外部请求和内部 LifecycleAction 都携带请求者、Scope 和因果信息；
- Authentication、Authorization、Execution Admission、Credential 和 Audit 保持独立边界；
- AuthenticationDriver 在 Principal/Scope 建立前按平台或未来显式 organization/realm 入口解析；认证后唯一 AuthorizationEngine 使用当前 Scope 的 AuthorizationPolicy，执行创建固定调用唯一 ExecutionAdmissionEngine，可选 AuditExportTarget 与内置 CredentialStore 再按适用治理上下文运行。首版不存在 CredentialPolicy、凭证后端选择或跨后端迁移。Authorization 记录 Policy provenance，Execution Admission 记录 rules/facts provenance，Authentication 记录精确 Driver release/digest；治理实现不固定到 Workflow；
- 审计读取按 Scope/Project 服务端授权并统一脱敏；普通成功读取不递归产生审计事件，拒绝、导出和受控高敏感元数据展示必须审计；
- Agent 默认无人值守执行，但只获得隔离 Workspace 和当前责任需要的凭证；
- Provider 不能满足必需隔离或网络能力时拒绝执行。

### 9.4 认知与资源效率

Sparse Engineering 的直接结果是：模型只在需要规划、生成、判断或评审时运行。等待 CI、定时条件、用户决定或外部部署状态时，Workflow 保留责任和进度，AgentRun 可以结束；新事实到达后再创建下一次有边界的认知执行。

模型选择按决策影响、任务风险和执行规模分层，而不是由平台维护一个统一默认模型：

| 工作类型 | 架构建议 | 原因 |
| --- | --- | --- |
| 目标理解、设计与 DAG 编排 | 使用旗舰级或同等复杂推理能力的模型 | 调用次数较少，但错误会影响全部下游 Plan 与 WorkUnit |
| 独立 Review 与验收 | 使用具备独立判断能力的次旗舰级以上模型，并保持 Session/Workspace 隔离 | 需要发现作者遗漏的边界、风险和验收缺口 |
| 大规模实现与操作 | 在 Contract、Guide、Sensor 和 Gate 清楚时使用成本效率型模型 | 任务数量和 Token 消耗最大，适合通过工程约束换取规模效率 |
| Schema、测试、Digest、Probe 和外部状态 | 确定性程序优先 | 可计算事实不应消耗模型预算，也不应引入额外不确定性 |

这张表是资源配置原则，不是模型白名单或 Kernel 内建路由规则。具体模型由 Project 的 AgentDefinition 与 Workflow 固定快照决定，Harness 不能把能力不足的模型自动提升为高能力模型。

- Workflow 等待外部事件、时间条件或人工 Decision 时不创建 AgentRun；
- 可确定计算的依赖、状态、检查和过滤由程序完成，不为可计算事实调用模型；
- Guide 只装配当前责任需要的合同、成果引用、Observation 和 Decision，隐藏 Session 不能补充未持久化的业务事实；
- 大量工具结果优先在程序内筛选和聚合，只有决策相关信息进入模型上下文；
- Usage 归因到 Project、Workflow、WorkUnit、责任和 Run；
- 资源效率不能削弱必要的程序验证、独立评审、审计和质量门。

### 9.5 容量验收基线

容量数字用于验证部署是否达到最低可用水平，不进入领域模型，也不成为编排层的固定“槽位”。实际并发由 Kubernetes requests/limits、Quota、节点资源和调度策略决定。

- Compact 在 Linux K3s 或受支持的本地 Kubernetes 上支持至少 3 个并发物理 Run；
- External Cluster 使用 3 个 Worker 节点、每节点按资源规划容纳 3 个 Run，验证至少 9 个并发物理 Run；
- 这些数字是最低支持容量和验收基线，不是 Kernel、CRD 或 Platform Core 的硬编码并发上限；
- Platform Operations 只读展示 Kubernetes 当前 allocatable、requests、Quota、运行/等待执行和调度失败原因；没有 Metrics 来源时实时 CPU/内存使用率显示为 unknown，但不能影响请求量和调度事实展示；
- 容量压力可以使平台展示为 degraded，但只有 API 或关键控制/数据依赖不可服务时才展示 unavailable；
- Controller 默认单副本，可配置 Leader Election；
- Usage 只记录已发生使用；预算、Reservation 和自动停机不属于当前产品边界。

### 9.6 关键保证及实现位置

架构承诺需要同时能被用户感知、被测试验证。下表把主要承诺与实现机制对应起来，详细设计和验收用例沿同一关系继续下钻。

| 对外保证 | 概要设计中的实现机制 |
| --- | --- |
| 目标不会隐式漂移 | Workflow `spec` 执行快照、精确 package release、不可变 Artifact，以及显式 Decision/重新规划 |
| 活动状态只有一个权威来源 | Workflow CR 是活动控制文档；PostgreSQL 只保存目录、不可变事实、历史和读模型 |
| Agent 不能自报业务成功 | Agent 输出先形成 Artifact、Observation 或 Report，再由 GateResult 与 Decision 判断 |
| 计划不能直接执行 | PlanDraft 经过 Plan Compiler、独立 Review 和 Planning Gate 后形成不可变 Plan |
| 上游失败不会污染下游 | WorkUnit 使用硬依赖和直接状态，Gate 未通过时下游不能进入 Ready |
| 作者不能批准自己的成果 | Producer、Verifier、独立 Reviewer、显式 Acceptance WorkUnit 与最终 Decision 责任分离，并使用必要的隔离 Session/Workspace |
| 返工不会无限循环 | Convergence Policy、Usage 可见性、Waiting 和终态出口共同限制循环 |
| 中断后可以恢复 | Workflow、Run、Lineage、Artifact、Observation、幂等 Action 和可恢复 Workspace 保存连续性 |
| 外部副作用不会盲目重试 | Executor 分离 execute、observe 和 cancel，未知结果先观察 |
| Extension 结果和收据可完整追溯且不可伪造 | SDK 共享不可变递归测量树；逐节点私有 seal 校验完整公开 receipt、子节点顺序和 parent lineage，Host handoff 仍由 Harness Host 验证 |
| 执行者不能绕过状态所有权 | Agent、Extension、Runtime 和执行资源只提交所属报告；Platform Core 是唯一 Workflow CR 写入者 |
| 高风险执行始终受治理 | RequestContext、Authorization、Admission、Credential、Audit 和 Decision 进入主链 |
| 内置能力没有特殊通道 | 内置与第三方 ExtensionPackage 使用相同安装、解析、调用、失败和接口检查路径 |
| 行业 Solution 不能污染 Kernel | Solution 只声明方法、Contract 和扩展绑定，不能增加状态、LifecycleAction 或 Gate bypass |
| 结果可以完整追溯 | Outcome 可以追溯到 Plan、Run、Artifact、Observation、GateResult、Decision 和 Usage |
| 资源整理不会破坏历史引用 | Project/Agent/Workflow 使用类型化归档；只有无业务引用的空 Project/Agent 可硬删，Workflow 与不可变历史由 Retention/Legal Hold 保护 |

## 10. 后续设计与验证入口

序号表示推荐的模块依赖下钻顺序；评审者也可以根据关注领域直接选择对应文档。精确共享契约从对应详细设计链接进入 Reference，不要求所有开发者通读全部 Reference。用户可见行为以[验收与公开验证](../delivery/acceptance-and-validation.md)中的 61 个唯一用户旅程为准；物理安装和运行行为以[部署与运行设计](../deploy-ops/deployment-and-operations-design.md)为准。

| 序号 | 领域 | 详细设计 |
| --- | --- | --- |
| 01 | Solution Definition | [Solution Definition 详细设计](detailed/01-solution-definition-detailed-design.md) |
| 02 | Project & Team | [Project 与 Agent Team 详细设计](detailed/02-project-and-agent-team-detailed-design.md) |
| 03 | Workflow | [Workflow Platform Core 详细设计](detailed/03-workflow-platform-core-detailed-design.md) |
| 04 | Orchestration | [Orchestrator Kernel 详细设计](detailed/04-orchestration-kernel-detailed-design.md) |
| 05 | Cloud Native | [Kubernetes-native 控制面详细设计](detailed/05-kubernetes-native-control-plane-detailed-design.md) |
| 06 | Agent Execution | [Agent Runtime 详细设计](detailed/06-agent-runtime-detailed-design.md) |
| 07 | Workspace | [Workspace 详细设计](detailed/07-workspace-detailed-design.md) |
| 08 | Extension | [Package 与 Extension 详细设计](detailed/08-component-and-extension-detailed-design.md) |
| 09 | Governance | [治理与安全详细设计](detailed/09-governance-and-security-detailed-design.md) |
| 10 | Interfaces | [Web、API 与 CLI 详细设计](detailed/10-web-api-cli-detailed-design.md) |
| 11 | Software Delivery | [软件交付详细设计](detailed/11-software-delivery-detailed-design.md) |

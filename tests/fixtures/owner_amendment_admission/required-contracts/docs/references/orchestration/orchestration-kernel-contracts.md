# Orchestrator Kernel 契约参考

共同记法见 [Reference 共同约定](../conventions.md)，架构边界见 [Orchestrator Kernel 详细设计](../../design/detailed/04-orchestration-kernel-detailed-design.md)。正式模块名固定为 `Orchestrator Kernel`；`oacok` 只表示该模块附带的 Agent 协作 CLI 命令。

## 1. 公共接口

普通项目只使用 Kernel Runtime Facade，有两种常用接入形态。

事件驱动宿主调用：

    advance(workflow_input, definition, host_snapshot, state, observations, decisions)
      -> OrchestrationDecision

`advance` 只计算有限、确定的下一步动作。调用方提供快照并应用返回动作，不需要注册调用 Hook；OAC Controller 使用这一形态。

嵌入式本地运行调用：

    registerExecutor(executor_descriptor, invoke_hook)
      -> RegistrationResult

    runUntilBlocked(runtime_state)
      -> LoopOutcome

Local Driver 使用已注册 Hook 反复调用同一个 `advance`，直到遇到异步执行、人工 Decision、外部等待或终态。`registerExecutor` 不是 Controller 模式的必需步骤，Hook 也不进入确定性 HostCapabilitiesSnapshot。

Agent Executor 使用同一个传输无关协作端口：

    show() -> AgentWorkView
    guide(topic?) -> AgentGuideView
    read(resource_key) -> ResourceContent
    submit(work_submission) -> SubmissionAck

该端口命名为 `AgentWorkChannel`。Kernel Distribution 提供标准 `oacok` CLI Adapter；OAC、嵌入式本地 Driver 或其他宿主只需实现该 Channel 的传输与资源访问适配，不需要另行发明工作协议。

以下接口属于 Kernel 内部或高级适配 SPI，普通项目不需要手工串联：

    compilePlanningRules(definition, active_plan, dag_orchestration_work_unit, state, observations, decisions)
      -> PlanningRules

    renderOrchestrationContext(rules, context_items)
      -> OrchestrationContext

    compilePlan(planning_rules, base_plan?, draft)
      -> CompileResult<ValidatedPlan>

    reconcileOnce(workflow_key, orchestration_host_port)
      -> LoopOutcome

相同 WorkflowInput、Definition、HostCapabilitiesSnapshot、Plan、State、Observation 和 Decision 必须产生相同 PlanningRules、CompileResult、InvocationRequest、OrchestrationDecision 和 LifecycleAction。Plan Compiler 直接消费权威 PlanningRules，不重新解释 Definition/Observation，也不增加 `PlanningCompilationInput` 包装对象。

## 2. 公共输入边界

### 2.1 WorkflowInput

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `kind` | 是 | Workflow 输入类型 | `plan` 表示已经提供明确 DAG；`goal` 表示根据自然语言目标生成 DAG；`document` 表示根据一个或多个已有不可变文档生成 DAG；不允许通过“缺少 Active Plan”隐式猜测 |
| `plan` | kind 为 `plan` 时必需 | 初始或当前 DAG/Plan | 可以来自 OAC BaselinePlanTemplate 编译结果，也可以来自任意外部系统已经确认的 DAG |
| `objective` | kind 为 `goal` 时必需；kind 为 `document` 时可选 | DAG 编排目标 | `goal` 模式直接以该自然语言目标生成图；`document` 模式可用它补充“基于这些文档要形成什么执行图”，省略时以文档声明的目标为准 |
| `documents[]` | kind 为 `document` 时必需 | 已有文档输入 | 每项都是不可变 `InputResource`，至少包含 `resource_key`（资源键）、`display.name`（资源名称）、`display.description`（用途说明）、`media_type`（内容类型）和 `content_digest`（内容摘要）；Agent 通过同一 AgentWorkChannel 精确读取，Kernel 不理解文件系统、Artifact 或对象存储类型 |
| `input_digest` | 是 | 输入内容摘要 | 相同规范化输入得到相同摘要，用于重放和幂等 |

OAC 根 Workflow 始终以 `kind=plan` 进入 Kernel：Platform Core 已经把精确 Solution 的 BaselinePlanTemplate 编译为 fixed-baseline Plan。`kind=goal` 和 `kind=document` 是 Kernel 面向独立或嵌入式项目提供的显式无图入口，不改变 OAC 固定基线设计。OAC 活动图内部若要根据已批准设计文档扩展 DAG，仍由图中显式 DAG Orchestration WorkUnit 通过 typed input bindings 消费这些文档，而不是切换根 WorkflowInput。

`goal-bootstrap` 和 `document-bootstrap` 都不是“发现没有 Plan”后的补救逻辑。它们只在调用方本次明确提交对应 kind 时创建，使用普通 PlanDraft 节点/边 Schema，经同一个 Plan Compiler 静态校验后作为初始 Active Plan。Plan 只包含一个可见 DAG Orchestration WorkUnit：主执行固定为 `execution.kind=executor` 且要求 `orchestration.dag`；输出合同固定为 PlanDraft；Computational Guide/Sensor 由 Kernel 契约、PlanningRules 和 Plan Compiler 形成；Inferential Guide 分别携带显式 objective，或不可变文档资源及可选 objective；是否需要独立 Plan Review 来自 `planning_constraints`。Host 没有兼容 Executor、文档资源无法按 digest 读取、编译失败或必要 Harness 输入不完整时，bootstrap 失败且不产生部分 Plan。

### 2.2 OrchestrationDefinition

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `kernel_contract_version` | 是 | Kernel 契约版本 | 只用于 DTO 和固定生命周期词汇兼容，不是业务版本，也不允许调用方选择自定义状态机 |
| `planning_constraints` | 是 | DAG 编排机器约束 | 定义允许的 Contract、Harness、ExecutionBinding、依赖和变更边界；不包含 Solution、BaselinePlanTemplate、Agent 或产品级规划 Guide 类型 |
| `definition_digest` | 是 | 编排定义内容摘要 | 对规范化内容计算，作为 Workflow 执行快照的一部分；不再创建额外 `definition_id` |

`OrchestrationDefinition` 是一个小型机器合同，不是完整行业流程。Solution 的 BaselinePlanTemplate 在 Workflow 创建阶段由外层 Resolver 直接实例化为固定基线图；动态规划通过图中的显式 DAG Orchestration WorkUnit 表达。Solution、模板、Agent Instructions/Skills 和 Harness Guide 都不进入 Kernel Definition。

### 2.3 HostCapabilitiesSnapshot 与 ExecutorDescriptor

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `executors[]` | 是 | 当前可用执行器目录快照 | 只包含 Kernel 匹配所需的通用描述，不包含 Agent、Runtime、ExtensionPackage 或框架句柄 |
| `snapshot_digest` | 是 | Host 能力快照摘要 | 保证一次 `advance` 使用同一组候选和可用性事实 |
| `executor_key` | 每个 Executor 必需 | 执行器稳定逻辑键 | Host 内唯一；OAC 可映射到某个 AgentDefinition、Component Executor、程序或人工责任，但 Kernel 不解释该身份 |
| `capabilities[]` | 每个 Executor 必需 | 执行器能力集合 | WorkUnit `execution.capability_requirements[]` 必须是该集合的子集；无图启动要求包含 `orchestration.dag` |
| `accepted_contracts[]` | 每个 Executor 必需 | 可接受合同集合 | 防止能力名称匹配但输入/输出合同不兼容 |
| `effect_mode` | 每个 Executor 必需 | 副作用模式 | `pure`、`read-only` 或 `side-effecting`；副作用执行器必须声明 Observe/Cancel 能力 |
| `availability` | 是 | 当前可用性事实 | 必须作为显式输入；人工 Executor 表示存在满足资格的授权提交路径，不表示某个浏览器在线；Kernel 不自行探测外部资源或读取隐式时钟 |

Executor 的调用 Hook 属于 Driver，不进入纯快照。OAC 的 WorkflowOrchestrationHost 从初始 TeamBindingSnapshot、有效 Workflow-local Executor Decision、LockedComponentSet、程序/组件能力事实和当前治理策略建立该快照。只有 Governance 能针对当前责任、合同、Scope 和独立性生成非空 `principal_requirement` 时，Host 才加入 human ExecutorDescriptor；其 availability 表示存在受控提交路径，不表示某个具体用户在线。Platform Core 仍负责把匹配结果解析为精确产品对象并执行 Authorization、Admission 和审计。Project 后续编辑不能静默进入该快照。

Plan Compiler 不消费 HostCapabilitiesSnapshot。它只检查 PlanDraft 中的责任、能力、合同和独立性要求是否合法；当前没有匹配 Executor 不使 Plan 非法。WorkUnit 到达派发点后，Kernel Runtime 使用以下固定结果：

| 匹配结果 | Kernel 结果 | Host 后续行为 |
| --- | --- | --- |
| 唯一匹配 | `DispatchExecution` 或 `StartReview` | 使用已匹配 `executor_key` 创建不可变 Run request |
| 零匹配 | `WaitForDecision`，convergence=`Waiting` | 保存产品侧 MissingExecutor Observation，等待显式 Executor 绑定；只有 Active Plan 已包含对应条件分支时，Decision 才可激活已有 DAG Orchestration/Remediation WorkUnit |
| 多匹配且无法确定 | `WaitForDecision`，convergence=`Waiting` | 请求宿主或授权用户明确选择，禁止随机挑选 |

执行器解析载荷至少包含 `work_unit_id`（WorkUnit 身份）、`responsibility_requirement`（责任要求）、`capability_requirements[]`（能力要求）、`accepted_contracts[]`（兼容合同）、`independence_constraints[]`（独立性约束）、`executor_requirement_digest`（规范化执行要求摘要）和 `match_diagnostics[]`（匹配诊断）。这些都是 Kernel DTO 值，不创建新的持久化聚合；OAC 是否使用 Observation/Decision 表示，由 Host 决定。

## 3. PlanningRules

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| orchestration_definition_digest | 是 | 当前 OrchestrationDefinition 内容身份 | 必须匹配 Workflow.spec 中固定的 digest |
| planning_work_unit_id | 运行时规划必需 | 发起本轮图扩展的显式 DAG Orchestration WorkUnit | 固定基线编译不创建 OrchestrationContext，因而不使用该字段 |
| planning_work_unit_plan_node_id | 运行时规划必需 | 规划 WorkUnit 在当前 Active Plan 中的节点身份 | 必须与 WorkflowState、Active Plan 中同一 WorkUnit 的节点身份一致 |
| planning_work_unit_subject | 运行时规划必需 | 规划 WorkUnit 的不可变 SubjectKey | 必须与 WorkflowState 和 Active Plan 中的节点 SubjectKey 完全一致 |
| state_revision | 运行时规划必需 | 编译所依据的 WorkflowState CAS 修订号 | PlanDraft、ValidatedPlan 和 Planning Gate 必须绑定同一修订号 |
| base_plan_id | 运行时规划必需 | 本轮扩展所基于的当前 Active Plan | 必须匹配当前 `activePlanId`；固定基线编译时为空 |
| base_plan_digest | 运行时规划必需 | 当前 Active Plan 的规范化内容摘要 | 必须与 `base_plan_id` 成对匹配；同 ID 不同 digest 视为不同基线 |
| planning_constraints | 是 | Definition 编译出的机器规划边界 | OrchestrationContext 和 Guide 不能扩大 |
| allowed_execution_kinds[] | 是 | 允许的主执行类型 | 只允许 `executor` 或 `child-workflow` 等 Kernel 固定类型；不携带具体执行对象，也不授予宿主权限 |
| allowed_dependency_rules[] | 是 | 可建立的依赖 | Plan Compiler 可确定性检查 |
| immutable_subjects[] | 是 | Running、终态和已确认对象 | PlanDraft 不能修改 |
| decision_boundaries[] | 是 | 必须请求用户的变化及其条件 | 每项声明稳定 boundary key、可选类型化 predicate 和允许 outcome；DAG Orchestrator 不能伪造批准 |
| review_requirements[] | 是 | Plan Review 必须检查的内容 | Open Agent Cluster 软件交付为 Required |
| plan_draft_schema_version | 是 | PlanDraft 输出 Schema 兼容版本 | OrchestrationContext 不能替代 |
| context_items[] | 否 | `OrchestrationContextItem` tagged union，显式区分 InputResource、Observation、Review、Diagnostic 和 Decision | 反馈项使用稳定 `item_id`、kind、SubjectKey、item digest 的完整身份；Artifact 等产品成果先由 Host 规范化为 InputResource；只保留最小必要集合 |
| rules_digest | 是 | 规划输入摘要 | PlanDraft 必须回传并匹配 |

`decision_boundaries[]` 只声明“哪类候选变化必须由用户决定”，不表示每个 Plan 都需要人工批准。每个 boundary 的 predicate 只能从 Kernel 固定集合中选择，例如 `plan-changed`、`node-added`、`node-removed`、`node-changed`、`edge-changed` 和 `node-obsoleted`；省略 predicate 时按 `plan-changed` 处理。Planning Gate 必须根据当前 ValidatedPlan 的编译事实判断是否命中；未命中时直接继续 Gate，命中时返回 `AwaitDecision`，不接受 Planner 或 Reviewer 通过省略 requested decision 来关闭边界。Reviewer 的 `required_decisions[]` 只是结构化判断输入，不能自带批准，也不能扩大 PlanningRules 允许的边界。

## 4. OrchestrationContext

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| planning_rules_digest | 是 | 权威 PlanningRules 内容摘要 | 必须与本轮传入 Plan Compiler 的规则完全一致 |
| instructions | 是 | 目标、领域说明和输出要求 | 不扩大允许集合 |
| method_resources[] | 否 | 当前节点显式绑定的方法资源 | 使用通用 InputResource 身份、用途说明和 digest；不携带 AgentDefinition、AgentTemplate 或 SkillPackage 类型 |
| input_resources[] | 否 | 必要业务输入资源 | 只来自当前 DAG Orchestration WorkUnit 的 typed input bindings |
| compiler_diagnostic_ids[] | 否 | 上轮静态校验错误身份 | 不覆盖原 Diagnostic |
| plan_review_ids[] | 否 | 上轮 Reviewer 意见身份 | 只属于同一 DAG Orchestration Lineage |
| decision_ids[] | 否 | 已完成用户 Decision 身份 | 不跨 subject 自动继承 |
| feedback_items[] | 否 | 本轮实际渲染的 Observation、Review、Diagnostic 和 Decision typed identity | 每项必须逐字段匹配 `PlanningRules.context_items[]`，不能只提交 ID；必须拒绝未知、重复、跨 kind、过期或 digest 不匹配项 |
| context_digest | 是 | 渲染内容摘要 | 相同输入和 Renderer 版本结果相同 |

OrchestrationContext 只为显式 DAG Orchestration WorkUnit 生成，并且只包含当前节点的目标、普通 Inferential Guide、显式方法资源、typed input bindings 解析出的业务输入和本轮反馈。`RenderOrchestrationContext` 只能渲染 `PlanningRules.context_items[]` 已准入的资源和反馈身份；资源的 key、subject、media type、display 和 content digest 必须完全匹配，反馈 ID 不能未知、重复或跨 kind 使用。所选 Executor 自身的稳定执行方法不进入该对象：OAC 在 Kernel 匹配 `executor_key` 后，由 Platform Core 通过精确 AgentDefinition 的 RuntimeInstructionSet 与 RuntimeRequest 物化 Instructions/Skills；其他 Kernel 宿主则由已注册 invoke hook 自己承接。Kernel 不读取 AgentDefinition/AgentTemplate/SkillPackage，也不把全部上游历史自动塞入上下文。

## 5. PlanDraft

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| plan_draft_id | 是 | 一次计划草案身份 | 内容修订使用新 ID |
| planning_rules_digest | 是 | 使用的 PlanningRules | 不匹配直接拒绝 |
| state_revision | 运行时规划必需 | 产生草案时使用的 WorkflowState 修订号 | 必须与 PlanningRules 和当前权威 State 完全一致 |
| base_plan_id | 运行时规划必需 | 图扩展基线 Plan 身份 | 必须匹配当前 Active Plan；平台固定基线草案编译时为空 |
| base_plan_digest | 运行时规划必需 | 图扩展基线 Plan 摘要 | 必须与 `base_plan_id` 绑定并匹配当前 Active Plan 内容 |
| planner_lineage_id / planner_run_id | 运行时规划必需 | 规划责任时间线和本次 Run 身份 | 用于把草案绑定到当前 Planner；不得由 Reviewer 复用同一 Lineage；增量草案缺少任一字段必须拒绝 |
| nodes[] | 是 | 候选 WorkUnit | 不包含控制代码；可携带受限的类型化 `activation` 条件 |
| edges[] | 是 | 候选硬依赖和数据引用 | 必须可规范化且无环 |
| obsolete_nodes[] | 否 | 明确从基线移出的未执行节点 | 只能引用基线中的节点；Running、终态或已确认节点不能被标记为 obsolete |
| decision_requirements[] | 否 | 当前 Plan 声明的 Decision Requirement | 每项绑定同一 Plan 内的 source node、requirement key 和完整 allowed outcomes |
| change_reason | 是 | 固定基线来源或本次图扩展原因 | 只用于来源和 Review |
| assumptions[] | 否 | 尚未确认的假设 | 转为 WorkUnit、Observation 或 Decision |
| requested_decisions[] | 否 | 请求用户裁决的事项 | 不携带批准结果 |
| produced_at | 是 | 产生时间 | 显式事实，不参与隐式当前时间判断 |
| plan_draft_digest | 是 | 规范化草案摘要 | Review 和编译使用同一内容 |

### 5.1 PlanNode 条件激活

Plan 可以预先表达互斥业务分支，但不引入通用表达式语言。节点的 `activation` 是可选 tagged union；省略表示依赖满足后正常参与 Ready 计算。首版只允许引用同一 Plan 中已经声明的类型化 Gate 或 Decision Requirement：

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `kind` | 是 | 激活来源类型 | `decision` 或 `gate`；不能由 Solution 增加枚举 |
| `requirement_key` | 是 | 条件来源键 | 必须解析到同一 Plan 中一个已声明的 Decision/Gate Requirement，不使用产品数据库 ID 或自由文本 |
| `source_plan_node_id` | Decision 必需；Gate 在同 key 多来源时必需 | Requirement 的来源节点 | 用同一 Plan 的节点身份限定 requirement namespace；不允许跨 Plan 或依赖全局 key 猜测 |
| `outcome` | 是 | 激活结果 | 例如 `approve`、`request_changes` 或 Gate 的固定 outcome；必须属于来源合同允许集合 |

条件未决时节点保持 `Pending` 且不派发；条件满足后，Kernel 再按硬依赖和输入绑定计算 `Ready`；同一互斥来源已经选择其他 outcome 时，该节点确定性进入 `Obsolete`。Plan Compiler 必须校验引用存在、outcome 合法、互斥分支无歧义、未选择分支可收敛，以及每条可选择的终态业务路径可达。`activation` 不允许脚本、布尔表达式、Webhook、时间判断或任意产品字段访问。

`DecisionRequirement.allowed_outcomes[]` 是该 Plan 的封闭结果集合。每个 Decision activation 必须引用同一 Plan 中声明的 requirement、使用其 source node，并选择集合中的 outcome；每个允许 outcome 都必须有可达且最终收敛的分支，允许多个节点从同一 outcome 扇出。GateEvaluatorBinding 也按 `source_plan_node_id` 形成 Plan-scoped namespace，同 key 的不同来源只有在 source 明确时才可并存。

这使宿主可以把行业返工路径预先表达为普通 DAG。例如软件交付的最终验收 `request_changes` 分支首先激活 Architect 技术变更设计节点，之后才到 DAG Orchestration 节点；Kernel 不需要理解 Architect、技术方案或返工类型，也不会因看到 `request_changes` 自动创建 DAG Orchestration WorkUnit。

## 6. PlanDiagnostic

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| code | 是 | Schema、Cycle、UnknownTarget、IllegalAction 等稳定错误码 | 机器可判断 |
| severity | 是 | error 或 warning | error 阻止 ValidatedPlan |
| subject_path | 否 | Draft 中出错位置 | 不依赖宿主数据库路径 |
| message | 是 | 缺失内容、失败原因和修正方式 | 不能只有异常堆栈 |
| related_subjects[] | 否 | 相关 Contract、Observation 或其他不可变对象的 `SubjectKey` | 精确说明对象类型、ID 和 digest |

## 7. ValidatedPlan

ValidatedPlan 是静态校验通过、尚未经过 Planning Gate 的待评审计划。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| validated_plan_id | 是 | 待评审计划身份 | 不等同 Active Plan |
| source_plan_draft_id | 是 | 来源 PlanDraft 身份 | Draft 和 digest 保留 |
| source_plan_draft_digest | 是 | 来源 PlanDraft 的规范化摘要 | Gate 必须重新编译该精确 Draft，而不是信任调用方自报的 ValidatedPlan |
| planning_rules_digest | 是 | 编译依据 | 用于重放 |
| state_revision | 运行时规划必需 | 编译所依据的 WorkflowState 修订号 | 激活前必须仍是当前权威修订 |
| base_plan_id / base_plan_digest | 否 | 增量基线 Plan 身份和摘要 | 激活前必须与当前 Active Plan 的 ID、digest 和规范化快照完全一致 |
| planner_lineage_id / planner_run_id | 运行时规划必需 | 产生该候选的 Planner 时间线和 Run | 供 Gate 验证候选来源并与 Reviewer Lineage 隔离 |
| nodes[] | 是 | 规范化 WorkUnit | 排序和默认值已确定 |
| edges[] | 是 | 规范化硬依赖 | 无环 |
| obsolete_nodes[] | 否 | 从基线移出的未执行节点 | 保留完整图快照及合法替代/收敛关系 |
| decision_requirements[] | 否 | 计划声明的 Decision 分支合同 | 保留 source node 与 allowed outcomes |
| change_facts | 是 | Compiler 根据基线与候选计算的变更事实 | Gate 的 rule-owned boundary predicate 只读取这些事实 |
| warnings[] | 否 | 非阻断 Diagnostic | Reviewer 可读取 |
| compiler_version | 是 | Plan Compiler 版本 | 可重放定位 |
| validated_plan_digest | 是 | 规范化结果摘要 | Reviewer 和 Gate 固定同一内容 |

ValidatedPlan 不能被 Lifecycle Core 当作 Active Plan 执行。

ValidatedPlan 是否需要用户 Decision 不是它自身的固定生命周期属性。Gate 使用其精确 ID/digest、来源 Draft、`planning_rules_digest`、当前 `state_revision`、当前 `base_plan_id + base_plan_digest`、Compiler 产生的 `change_facts`、PlanReviewResult 和已确认 Observation 计算本轮边界；任何一个输入变化都必须重新计算，旧 Decision 不能自动应用到新的候选计划。Gate 会用同一 Rules 和权威基线重新编译来源 Draft，并要求得到的 ValidatedPlan ID、digest 和完整内容逐字段一致，防止替换节点、边或 obsolete history。

## 8. PlanReviewResult

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| plan_review_id | 是 | 一轮计划评审身份 | 每轮 Review 新建 |
| validated_plan_id | 是 | 被审 ValidatedPlan 身份 | 使用其固定 digest，不使用可变当前计划 |
| validated_plan_digest | 是 | 被审 ValidatedPlan 的规范化摘要 | Review 只能绑定这一个精确候选 |
| reviewer_run_id | 是 | Reviewer AgentRun、程序执行或人工执行身份 | 与 DAG Orchestrator Run 身份隔离 |
| reviewer_lineage_id | 是 | Reviewer 的独立责任时间线 | 不能与 Planner Lineage 相同；独立性由 verifier-owned lineage evidence 绑定 owner、责任、候选和当前 snapshot 证明，不是通过两个不同字符串自报 |
| verdict | 是 | pass、rework、reject | 用户 Decision 是否必要由 Planning Gate 根据 `decision_boundaries[]` 判断，不属于 Reviewer verdict |
| findings[] | 是 | 结构化问题、风险和建议 | 可进入下一轮 OrchestrationContext |
| required_decisions[] | 否 | 需要用户裁决的事项 | Reviewer 不能自带批准 |
| produced_at | 是 | 评审时间 | 显式记录 |
| review_digest | 是 | 规范化评审摘要 | Planning Gate 固定 |

`PlanningLineageEvidence` 是 Host/verifier 产生的不可变证据，分别以 `planner` 或 `reviewer` responsibility 绑定 owner、lineage、run、verifier、PlanDraft、ValidatedPlan、PlanningRules、WorkflowState revision 和 base Plan ID/digest，并携带其规范化 evidence digest。Reviewer evidence 还必须携带 `PlanReviewResult.review_digest`；该 digest 纳入规范化 evidence digest，使 Host 签名同时绑定 reviewer 的 verdict、findings 和 required decisions。`EvidenceDigest` 只是规范化 claims 的内容摘要，不能单独证明签发者；Host 必须用自己的 Ed25519 private key 签署包含该 digest 的 `LineageEvidenceAttestation`，Planning Gate 由 Host-owned public key 和 verifier identity 校验签名后，才校验 evidence digest、候选/快照绑定、review digest 和 planner/reviewer owner、lineage、run 的独立性。调用方自报的普通字符串和 opaque reference 不能替代该签名证据。`PlanningGateInput` 只携带待核对 claims、opaque evidence reference 和 Host attestation，不携带 resolver 或 private key；Host 集成通过公开的 `NewHostLineageEvidenceVerifier(verifierID, publicKey)`、`NewLineageEvidenceAttestation(evidence, privateKey)` 与 `EvaluatePlanningGateWithHostEvidence` API 建立信任边界，untrusted planner input 不能选择 evidence authority。

## 9. Plan

Plan 是不可变的可执行图快照。它不使用 SemVer，也不带 `Version` 后缀；只有 Workflow `activePlanId` 表示激活事实，且该字段只负责指向当前快照，不负责触发 Planning。CAS 竞争失败的 Plan 可以保留为未激活历史候选。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| plan_id | 是 | 不可变计划身份 | 创建后不修改 |
| parent_plan_id | 否 | 前一 Active Plan 身份 | 初始计划为空 |
| provenance | 是 | `fixed-baseline`、`goal-bootstrap`、`document-bootstrap` 或 `dag-orchestration-work-unit` tagged union 来源 | 固定基线记录模板/编译/Package 发布检查摘要；两类 bootstrap 记录 WorkflowInput/Definition/输入资源/编译摘要；动态图扩展记录 PlanningRules、PlanDraft、ValidatedPlan、Review 与 Planning Gate 精确身份 |
| nodes[] | 是 | 可执行 WorkUnit | 不包含宿主对象；保留已编译的可选 `activation` 条件 |
| edges[] | 是 | 硬依赖 | 无环 |
| obsolete_nodes[] | 否 | 被新计划替代的未执行 WorkUnit | Running 和终态不能进入 |
| compiler_version | 是 | 编译器版本 | 可定位 |
| plan_digest | 是 | 规范化计划与来源摘要 | 同内容得到同摘要 |

## 10. WorkflowState 与 WorkUnitState

| DTO | 主要内容 | 关键约束 |
| --- | --- | --- |
| WorkflowState | workflow identity、state revision、Active Plan、WorkUnitState、Observation、Decision 和显式 Policy 输入 | 不连接数据库、不读取隐式时钟 |
| WorkUnitState | identity、来源 Plan/node、Contract、subject identity/digest、direct state、iteration、可选 `activation`、`execution`、Harness、依赖、Run 摘要和已确认输出 | Ready 由 Lifecycle Core 根据 activation、依赖和输入共同计算；不能由 purpose、phase 或多个布尔值拼出隐藏状态 |

`direct state` 使用 Kernel 固定枚举：`Pending`、`Ready`、`InProgress`、`InVerification`、`VerificationFailed`、`InReview`、`ReviewRejected`、`NitsRequired`、`InRework`、`Waiting`、`Cancelling`、`Succeeded`、`Failed`、`Cancelled`、`Abandoned`、`Obsolete`。CoreLifecycle 是这些状态之间的纯转换规则，不是另一个可持久化字段。`VerificationFailed`、`ReviewRejected` 和 `NitsRequired` 不得转回 `Ready`。

每个 WorkUnit 都必须携带已激活 `plan_id + plan_node_id`。DAG Orchestration、Remediation、Replacement 等只是节点用途，不形成不同创建来源或权限。`request_changes` 等结果只有在 Active Plan 已声明匹配 outcome 的 `activation` 分支时，才会使相应节点参与普通 Ready 计算；图中没有预声明路径时，Kernel 返回 Plan/Solution 设计缺口。

## 11. 固定 DAG 收敛规则

Kernel 不接收调用方自定义的完成条件、完成表达式或终态求值器。Workflow 成功完全由活动 DAG 的规范化状态决定：

```text
所有 activation 已匹配、因而被选中的可达 WorkUnit = Succeeded
AND 所有互斥未选择的 WorkUnit = Obsolete
AND 不存在非终态、Waiting、Unknown、Cancelling 或活动 Child Workflow
→ CompleteWorkflow

存在 Failed / Cancelled / Abandoned WorkUnit
→ 对应非成功 Workflow Outcome

图已静止但仍有未成功、未 Obsolete 或无法解析 activation 的 WorkUnit
→ Waiting 或 Failed，并返回结构化 Plan/Solution 设计缺口
```

WorkUnit 的 `Succeeded` 已经代表该节点的主执行、必需输出、Harness、程序验证、独立 Review、Gate 和 Decision 义务全部通过；Workflow 层不能再重复解释领域证据。Plan Compiler 负责验证 DAG 无环、依赖/activation 合法、互斥分支能够确定性 Obsolete，以及每条结构上可选择的路径能够收敛；Plan Review 负责判断计划在语义上是否足以完成用户目标。

Kernel 不因 Active Plan 耗尽、图未成功收敛或 `activePlanId` 存在与否而发起 DAG 编排。新的图扩展只能来自当前 Plan 中显式的 DAG Orchestration WorkUnit。

## 12. HarnessDefinition

一个 WorkUnit 的 `harness` 固定一份完整 HarnessDefinition，而不是一个执行实现 ID，也不是多个 Harness 的多选结果。该定义只装配围绕主执行的执行前 Guide、执行后 Sensor、Gate、返工与收敛规则；主执行只存在于 WorkUnit `execution`。Harness 内部各象限可以包含多个有序 Binding。默认形式是精确 Solution release 内的 `harness_definition_id + content_digest + configuration_digest`；独立发布时由 OAC 外层固定精确 ContentPackage，Kernel 只接收其规范化内容和摘要。

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| harness_definition_id / content_digest / configuration_digest | 是 | HarnessDefinition 在来源 package 内的局部身份、内容摘要和配置摘要 |
| display | 是 | Harness 本地化名称与说明；用于编辑、详情、审计和运行追踪，不参与控制身份 |
| computational_guide_bindings[] | 否 | 固定顺序的 Computational Guide Binding，包含 package-local 内容 ID 或 ExactComponentRef、内容摘要和配置摘要 |
| inferential_guide_bindings[] | 否 | 固定顺序的 Inferential Guide Binding，包含当前阶段 Instructions、显式方法资源、InputResource、历史 Review 和 Decision；不复制执行器自身的稳定 Instructions/Skills |
| computational_sensor_bindings[] | 否 | 固定顺序的 Computational Sensor Binding，产生程序检查、Event、digest、探针和 Usage Observation |
| inferential_sensor_bindings[] | 否 | 固定顺序的 Inferential Sensor Binding；可以调用推理型 Sensor Extension，或要求宿主派发独立责任 Run 形成 Review、影响分析等独立判断 Observation |
| gate_evaluator_bindings[] | 是 | Required GateEvaluator Binding；全部必须通过 | 冻结 Kernel v1 Harness Binding 使用 `binding_id + content_digest`，并可声明 `allowed_outcomes[]`；`configuration_digest` 属于 additive `GateEvaluatorSet` authoring envelope，连同 `source_plan_node_id` 在其中绑定精确 Plan/WorkUnit scope；未声明 outcome 时使用 Kernel 固定 Gate outcome 集合；同一 Harness 内 binding ID 不得重复 |
| rework_policy | 是 | 同一责任返工规则 |
| convergence_policy | 是 | 最大循环、等待和终止出口 |

WorkUnit `execution` 与 HarnessDefinition 都由 PlanDraft/PlanNode 明确携带并分别校验。Plan Compiler 只证明 `execution.kind` 属于 PlanningRules 允许集合、`executor` 的责任/能力/输入输出合同/独立性要求结构合法，或 `child-workflow` 合同完整；它不读取 HostCapabilitiesSnapshot，也不证明当前 Host Catalog 恰好存在匹配 Executor。当前可执行性只由派发时的 Executor Matcher 判断。同时独立校验 Harness 四象限、Gate 和返工/收敛规则；Harness 不得声明、覆盖或隐式选择 Executor。

统一生命周期视图固定为“执行准备 → 主执行 → 观察与评估 → Gate 判定 → 后续处理”。其中执行准备来自 Harness Guide，主执行来自 WorkUnit `execution`，观察与评估来自 Harness Sensor，Gate/后续处理来自 Harness 与 Kernel。该视图只是对两个正交字段的连续呈现，不是新的领域对象、读模型或持久化结构。生命周期阶段从字段或 Sensor/Guide 类型推导，Solution 作者和外层执行实现不能另行填写或重排平台阶段。

`rework_policy` 与 `convergence_policy` 显示在同一 Harness 详情的 Gate 后续处理区域，但它们由 Kernel 固定语义执行，不是外层 Extension，也不能被伪装成额外生命周期步骤。

### 12.1 InferentialSensorBinding

`inferential_sensor_bindings[]` 使用 tagged union，明确区分“调用通用 Sensor Executor”和“由宿主派发独立责任 Run”。两者都只能先形成不可变 Observation，再由 Gate 和 Lifecycle Core 决定下一步。

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `binding_id` | 是 | Harness 内绑定身份 | 在当前 HarnessDefinition 内稳定且唯一，用于运行追踪、幂等和审计 |
| `kind` | 是 | 推理观察执行方式 | `sensor-execution` 表示调用满足当前 Sensor requirement 的通用 Executor；`responsibility-run` 表示宿主按责任要求派发独立 Run |
| `required` | 是 | 是否为必需观察 | Required 缺失或失败时 Gate Fail Closed；Optional 跳过必须产生结构化 Observation |
| `sensor_execution` | kind 为 sensor-execution 时必需 | 推理 Sensor 执行要求 | 固定逻辑 executor requirement、输入/输出合同、effect/recovery 与 binding digest；不得携带 Agent、Runtime、ExtensionPackage 或 Host 句柄，也不能请求 `agent.invoke` |
| `responsibility_requirement` | kind 为 responsibility-run 时必需 | 独立执行责任要求 | 只声明责任标签和 Capability Requirement，不保存 Agent、Runtime、Session 或 Workspace 身份 |
| `guides[]` | kind 为 responsibility-run 时可选 | 独立责任 Run 的推理指导内容 | 固定 package-local 内容 ID/digest 或 ExactComponentRef，以及允许读取的 Artifact/Observation 说明 |
| `output_contract` | kind 为 responsibility-run 时必需 | 结构化独立判断输出合同内容绑定 | 例如 ReviewResult 或 ImpactAssessment；结果必须绑定精确 subject ID/digest |

`responsibility-run` 固定表达独立观察责任：首次执行必须与主执行责任使用不同 Lineage；当宿主物化为 AgentRun 时使用新 Session 和干净 Workspace，程序或人工实现也不得读取主执行者隐藏上下文。复审只延续 Reviewer 自己的 Lineage。Kernel Runtime 使用与主执行相同的 ExecutorDescriptor 匹配机制选择一个逻辑执行器；零匹配时输出 `WaitForDecision`，匹配成功后才输出 `StartReview`。它不导入 Agent、Runtime、HumanTask 或人员类型。WorkflowOrchestrationHost 再依据初始 Workflow `team_binding_snapshot`、有效 Workflow-local Executor Decision 和治理快照把逻辑 `executor_key` 解析为精确产品执行对象；人工实现只需要把同一 InvocationRequest 暴露给满足资格的 Principal。

需要执行一组业务旅程、访问 Preview、运行 Acceptance Bundle 并形成 AcceptanceResult 的验收不是“观察主执行结果的一次责任评审”，必须建模为普通 WorkUnit 主 `execution`。需要企业独立验收时，同样增加一个显式下游 WorkUnit；Kernel 不把它隐藏成 `StartReview` 或 `responsibility-run`。

当 `inferential_sensor_bindings[]` 中存在 `required=true` 且 `kind=responsibility-run` 的项时，程序验证通过后必须进入 `InReview`。不存在该项时可以直接进入 Gate。系统不得再持久化一个平行的 `review_required` 布尔值；UI 的“启用独立 Review”只是创建、删除或配置该 Binding 的创作操作。正式用户批准不是该 Sensor Binding 的隐式结果，必须作为单独的 Decision Requirement 进入 Gate。

## 13. InvocationRequest 与 WorkSubmission

`InvocationRequest` 是 Kernel Runtime 为一次主执行或独立责任 Run 组装的不可变调用请求，也是 `oacok work show` 的权威数据源。它直接嵌入 `DispatchExecution.request`；产品宿主可以原样复制到 `Run.request`。它没有独立聚合身份、仓储、版本或生命周期，不再创建 WorkPackage 对象。

`InvocationRequest` 必须是有界控制信封，而不是内容归档包。`objective`、显示说明、结构化摘要和槽位元数据只允许在 Kernel Contract Schema 的长度与数量上限内内联；输入正文、较大的 Contract/Schema、Guide 方法资源、Review evidence、日志、代码和二进制内容继续保存在各自既有的不可变权威存储中，并在请求里用有明确业务类型的 `resource_key + subject/id + digest + media_type` 描述。`show()` 返回固定信封，`guide()` 与 `read(resource_key)` 只能按该信封中的精确身份和 digest 读取内容。`request_digest` 同时覆盖规范化信封和这些内容 digest，因此读取正文不改变调用身份。不得增加含义模糊的 `request_payload_ref`、把最新内容重新拼入请求，或静默截断字段。

InvocationRequest Assembler 必须在产生 `DispatchExecution` 前执行 Schema 与整体大小检查。超限时不产生 Run，并在 `OrchestrationDecision.diagnostics[]` 返回 `code=InvocationRequestTooLarge`；这不是 PlanDiagnostic。OAC Host 将 WorkUnit 置为 `Waiting`，记录 `blocking_cause.kind=invocation-request-too-large`，同时令 Workflow `Blocked=True`，且不自动重试。修复方式只能是由 Active Plan 中已声明的 Remediation/DAG Orchestration 路径形成可替代计划，或创建采用新计划/输入的后继 Workflow；缺少这种路径就是显式 Plan/Solution 缺陷。用户或 Solution 不能调高控制面限制。具体上限随 `kernel_contract_version` 受兼容性测试约束，不形成业务配置字段。

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `invocation_id` | 是 | 本次调用身份 | Kernel 根据 Workflow、WorkUnit、iteration、责任和 Dispatch Action 稳定生成；返工、复审或新 Run 创建新身份 |
| `request_digest` | 是 | 调用请求内容摘要 | `oacok work show`、Runtime 注入和 WorkSubmission 必须匹配，防止执行期间上下文漂移 |
| `workflow_key` | 是 | 所属 Workflow 通用键 | Kernel 不解释 Kubernetes 或数据库身份格式 |
| `work_unit_id` / `iteration` | 是 | 所属工作单元与轮次 | 旧轮次不能提交到当前轮次 |
| `executor_key` | 是 | 已匹配的逻辑执行器 | 来自 HostCapabilitiesSnapshot；OAC 外层再映射为精确 AgentDefinition、Component 或其他执行对象 |
| `responsibility` | 是 | 本次责任要求 | 说明当前调用承担生产、评审、验收或其他通用责任；不是 Kernel 写死的行业角色枚举 |
| `objective` | 是 | 当前任务目标 | 来自 WorkUnit 描述和当前 subject，不要求执行器重新推断“自己要做什么” |
| `inputs[]` | 否 | 精确输入资源 | 只包含 `input_bindings[]` 解析得到的已确认资源；每项提供 `resource_key`、本地化 `display.name` 与 `display.description`、subject、media type 和 digest，正文通过 `oacok work read` 或等价 Channel 操作读取，不注入全部上游历史。宿主可从固定 SolutionPackage 的 FormField/Artifact 元数据解析展示语义，但 Kernel 不读取表单 Schema |
| `contract` | 是 | 工作合同 | 固定当前 WorkUnit 的输入槽位、输出槽位及其数据合同；节点成功仍由 Required 输出、Harness、验证、Review、Gate 和 Decision 判断 |
| `guide.computational[]` | 否 | 程序约束指南 | Schema、Policy、允许动作、资源和精确组件/合同要求 |
| `guide.inferential[]` | 否 | 推理工作协议 | 当前节点的阶段 Instructions、显式方法资源和行业方法；来自已固定 WorkUnit/Harness 内容，不复制 AgentDefinition/AgentTemplate 的稳定 Instructions/Skills，也不由 Kernel 临时创作 |
| `prior_conclusions[]` | 否 | 同一任务线的前序结论 | 只包含当前责任需要的生产结论、评审结论和显式交接摘要；不包含其他 Agent 的隐藏 Prompt、私有推理或完整工具日志 |
| `feedback[]` | 否 | 本轮允许使用的反馈 | 只包含与当前 WorkUnit、subject 和 iteration 精确关联且当前责任获准使用的编译诊断、Review/失败反馈和明确 Decision；可以来自独立 Reviewer Lineage，但不能混入无关任务历史 |
| `allowed_actions[]` | 否 | 允许动作集合 | 由 execution 要求、Contract 输入输出资源范围、Harness 和 Policy 求交，不能被执行器扩大；Contract 本身不保存可编辑动作列表 |
| `submission_contract` | 是 | 结果提交合同 | 说明结果 Schema、必需输出、是否要求结构化 conclusion、问题/阻塞报告和提交通道；Secret/token 通过运行环境单独注入 |

最终 Agent 工作内容的权威顺序固定为：平台安全与治理规则 > 当前 Run/WorkUnit 精确事实和 Contract > 已确认输入与本轮反馈 > 阶段 Inferential Guide > AgentDefinition 中继承或定制的稳定 Instructions。Kernel 只负责生成中间三层所在的 InvocationRequest，不读取 AgentDefinition、AgentTemplate 或 SkillPackage；OAC Platform Core 在创建 AgentRun 时才把 Kernel 请求与精确 AgentDefinition/RuntimeInstructionSet 合成并检查冲突。其他宿主在调用自己的 Executor hook 前承担同一合成责任。任何冲突都必须阻塞，不能依靠 Prompt 后写覆盖前写。

WorkSubmission 是所有 Executor 提交当前 InvocationRequest 结果的统一合同，也是 `oacok work submit` 的结构化载荷：

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `invocation_id` / `request_digest` | 是 | 本次调用身份与请求摘要 | 只能提交当前 InvocationRequest 的结果；旧请求或漂移请求拒绝 |
| `outcome` | 是 | 执行结果 | `succeeded`、`failed`、`waiting` 或 `unknown`；不直接声明 WorkUnit 成功 |
| `conclusion` | Agent 协作终态提交必需 | 本次执行结论 | 至少包含 `summary`；可包含 completed、remaining、risks 和相关 resource keys。宿主持久化为结构化 run-conclusion Observation，供下一责任 Run 读取 |
| `outputs[]` | 否 | 结构化输出 | 必须满足 WorkUnit output contract，并在进入 Gate 前持久化 |
| `observations[]` | 否 | 执行观察 | 测试、问题、风险、外部状态等结构化事实 |
| `questions[]` | 否 | 阻塞性问题 | 形成 Question Observation，等待精确 Answer Decision |
| `submission_digest` | 是 | 提交内容摘要 | 重复提交同一摘要幂等；同身份不同摘要拒绝 |

WorkSubmission 不限定执行者必须是 Agent。人工 Web/API 适配器也提交同一 DTO，并在进入 Submission Validator 前由宿主校验 Principal、Scope、责任资格、与 Producer 的独立性、当前 iteration、request digest、幂等键和 CAS revision。Kernel 只验证请求/合同一致性；它不持有人员目录、领取状态或并发锁。

当当前责任是独立 Review 时，output contract 必须要求结构化 ReviewResult，至少包含 `verdict`（评审结论）、`summary`（总体说明）、`findings[]`（必须处理的问题）、`nits[]`（非阻塞建议）和 `evidence[]`（依据）。Reject 时 `findings[]` 不能为空；返工 Run 的 InvocationRequest 必须携带该完整 ReviewResult，而不只是一个 rejected 状态。

Submission Validator 只验证提交与 InvocationRequest/Contract 的一致性；它不替代 Computational Sensor、Independent Review 或 Gate。

### 13.1 AgentWorkChannel 与 `oacok`

`AgentWorkChannel` 是 Orchestrator Kernel 的 Agent 协作协议边界，标准 CLI 命令名固定为 `oacok`：

| CLI 命令 | 中文作用 | Channel 操作与约束 |
| --- | --- | --- |
| `oacok work show` | 查看当前工作与协作上下文 | 调用 `show()`，返回当前 InvocationRequest、字段中文释义、前序结论、反馈和提交合同；不重新读取“最新 Workflow”改写请求 |
| `oacok guide [topic]` | 查看协作协议或当前工作指南 | 调用 `guide(topic)`；通用 workflow/recovery/metadata 协议由 Kernel 提供，行业阶段 Guide 来自当前 InvocationRequest 的固定内容 |
| `oacok work read <resource-key>` | 读取一个精确输入资源 | 调用 `read(resource_key)`，按授权返回内容并校验 content digest；不能任意浏览宿主存储 |
| `oacok work submit --file <path>` | 提交本次结构化结果 | 调用 `submit()`；必须匹配 invocation_id、request_digest、iteration 和 submission contract |

Host 在 Agent 启动时只注入最小 bootstrap instruction，明确要求 Agent 先运行 `oacok work show`，需要协议说明时运行 `oacok guide`，读取资源时使用 `oacok work read`，完成后使用 `oacok work submit`。Channel endpoint、短期 token 和本次调用绑定通过运行环境单独注入，不进入 InvocationRequest、Artifact、Observation 或日志。

YAML、JSON、HTTP、Unix socket、内存调用或只读文件都只是 Channel Adapter 的实现方式。Agent 面向的公开协议只有 `oacok` 命令及其结构化输出，不再同时维护“协议文件路径”和另一套 getter 语义。

## 14. GateResult

| 结果 | 语义 |
| --- | --- |
| Pass | Required 条件满足 |
| AwaitDecision | 等待用户业务决定 |
| Rework | 原责任时间线继续修改 |
| Reject | 只按 Harness 或 Active Plan 中已经声明的返工/条件分支处理；缺少路径时返回设计缺口 |
| Error | 输入缺失、Evaluator 失败或不可验证 |

## 15. LifecycleAction

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| action_id | 是 | 稳定逻辑动作身份 | 相同输入重放相同 |
| action_type | 是 | Kernel 注册动作 | Draft、Solution 和外层 Extension 实现不能新增 |
| subject | 是 | Workflow 或 WorkUnit 的 `SubjectKey` | 不包含数据库对象 |
| parameters[] | 否 | `LifecycleActionParameter` tagged union，显式区分 Plan、Execution、Harness 和 Decision 输入 | 不嵌入脚本和 Secret |
| idempotency_key | 是 | Host 重复 apply 的逻辑身份 | 不使用当前时间单独生成 |
| caused_by | 是 | Draft、Observation、Decision 或前序 Action 的 `Causation` | 形成完整因果链 |
| action_digest | 是 | 规范化业务动作摘要 | 不包含 expected state 或 Kubernetes resourceVersion；同 ID 不同摘要拒绝 |

expected Workflow state revision 与 Kubernetes resourceVersion 是 Host apply 的独立 CAS 前置条件，不属于 LifecycleAction 业务身份或摘要。前置条件失效时 Host 返回 Conflict，Coordinator 重新加载并让 Kernel 重新计算，不能通过替换并发令牌制造一个“新动作”。

### 15.1 WorkUnit lifecycle transition v1

`LifecycleTransitionPayload` 是 `oac.lifecycle-transition/v1` 的公开、可重放动作载荷，机器可读合同位于 [`contracts/spec/kernel/lifecycle-transition-v1.json`](../../../contracts/spec/kernel/lifecycle-transition-v1.json)。其 Go DTO 与 contextual validator 位于 additive `contracts/kernel/lifecyclev1` package；它们只 import 冻结 `contracts/kernel` 类型，不改变 `oac.kernel-contract/v1` 的字段、限制或 digest 语义。

载荷绑定 `state_revision`、Workflow/Plan/PlanNode/WorkUnit identity、iteration、subject 和不可变证据 digest。Gate evaluator 必须先以 `oac.gate-evaluator-set/v1` 的 `GateEvaluatorSet` authoring envelope 绑定完整 Harness/Plan scope、`harness_definition_id` 以及每个 evaluator 的 `binding_id + content_digest + configuration_digest`；context map 只能作为缓存，不能替代该权威 envelope。Gate reference 也必须携带当前 Harness evaluator 的三元 identity，并按完整 scope 派生 storage key；裸 binding key、重复 evaluator ID、未在 active Harness 声明的 evaluator、旧配置或跨 WorkUnit 复用一律拒绝。Gate Rework 在 `MaxIterations` 之前只能 `Succeeded → InRework` 并精确递增一次；只有恰好达到上限时才可保留 iteration 进入 `Failed`，超过上限 fail closed。

Terminal Observation 使用闭合 kind/outcome 映射：`run-succeeded/v1 → Succeeded`、`run-failed/v1 → Failed`、`run-cancelled/v1 → Cancelled`、`run-abandoned/v1 → Abandoned`、`run-waiting/v1 → Waiting`、`run-in-verification/v1 → InVerification`、`run-verification-failed/v1 → VerificationFailed`；Reviewer Lineage 只能使用 `review-succeeded/v1 → Succeeded` 或 `review-failed/v1 → Failed`。Progress、heartbeat、unknown 和不匹配的 Run kind 不得授权 LifecycleAction。每个 active Run binding 必须同时提供非空 `invocation_id + request_digest`，并与当前 InvocationRequest 无条件相等。

### 15.2 Executor-resolution evidence v1

`kernel.executor-resolution-evidence/v1` 使用闭合 `MissingExecutorEvidenceEnvelope` 与 `ExecutorSelectionEvidenceEnvelope`；payload 始终是嵌套对象。Envelope/payload/record/action DTO 拒绝 unknown field、duplicate field、显式 `null`、invalid UTF-8 和 trailing value，并在 retention 前验证 SHA-256 canonical identity。`answer_requirement_key` 使用规范化 tuple digest，不能用可碰撞分隔符串联。`oac.lifecycle-action/v1` 的 `LifecycleActionEnvelope` 覆盖完整 tagged parameter union；`LifecycleAction.Validate` 同时强制 transition action type 与 branch/gate/terminal matrix、required decision requirement key、nested Decision identity 和 canonical action digest。

`ExecutorSelectionEvidence` 在 Dispatch/StartReview 边界必须绑定当前 Workflow/Plan/PlanNode/WorkUnit/iteration/subject/responsibility、当前 capability snapshot、已持久化 MissingExecutor、不可变 InvocationRequest 和适用的 active Run/Lineage；pre-Run resolution 不得携带 Run/Lineage，且 authoritative WorkUnit 已有 `RunSummary` 时不得伪装成 pre-Run。责任来源由 action kind 固定：`DispatchExecution` 只能匹配 `WorkUnit.execution.executor` 与 `WorkUnit.contract`；`StartReview` 必须唯一匹配 Harness 中 `required=true`、`kind=responsibility-run` 的绑定，并将其 `responsibility_requirement` 与 `output_contract` 精确绑定到 InvocationRequest，即使 Review Run 尚未创建也不能退回到主执行责任。Selection Decision 必须是 typed payload，且 `decision_id + digest` 必须由该 payload 派生；Decision 还必须出现在当前 Workflow 的持久 Decision identity 集合中，并重新通过 candidate/capability/accepted-contract matcher；裸、伪造或过期 Decision 不能授权 LifecycleEvidenceRecord。新 DTO 与 validator 通过 `ValidateKernelDispatchAction`、`ValidateKernelStartReviewAction`、`LifecycleAction.ValidateAgainst` 和 `LifecycleEvidenceRecord.ValidateAgainst` 提供给 Platform adapter 使用。

`branch-convergence` 必须验证目标 PlanNode.Activation 与当前 WorkUnitState.Activation 完全一致；其 `SourcePlanNodeID` 必须解析为不同的 active source PlanNode，Decision/GateResult 必须绑定该 source WorkUnit 的精确 iteration 并已持久化。`CompleteWorkflow` 必须以 action subject digest 绑定版本化 completion authority，并校验当前 Workflow/Plan revision、所有 WorkUnit 与 active/obsolete Plan node 的一对一终态身份、成功 WorkUnit 的 Gate Pass 和 branch Activation 所需的 persisted Decision/Gate 事实；Pending、Waiting、Failed、Cancelled、Abandoned 或缺少必要证据均不得完成。`StoreValidatedPlan` 与 `ActivatePlan` 必须分别绑定当前 Workflow revision 的精确 ValidatedPlan 身份/摘要和待激活 Plan 身份/摘要；`CancelOperation` 必须绑定当前 Workflow、action subject 与 exact operation ID。`WaitForDecision` 使用 `DecisionWaitBinding` 绑定当前 Workflow/Plan/PlanNode/WorkUnit/iteration、typed Decision requirement 和 Decision subject。当前 Workflow 中尚无匹配的 persisted Decision 时，action 不得携带 Decision；若已有匹配 Decision，binding 与 action 必须携带完全一致的 persisted Decision（或 typed SelectionDecision 投影）。Observation/GateResult 等等待依据必须与当前绑定及持久状态完全匹配，并拒绝互不兼容的 payload 混合。

## 16. OrchestrationDecision

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| state_revision | 是 | 本轮使用的 Workflow State CAS 修订号 |
| active_plan_id | 否 | 当前 Active Plan 身份 |
| actions[] | 是 | 规范化、有序 LifecycleAction |
| diagnostics[] | 否 | 生命周期诊断 |
| convergence | 是 | Running、Blocked、Waiting 或 Terminal |
| decision_digest | 是 | 本轮输入输出摘要 |

## 17. OrchestrationHostPort

| 操作 | 输入 | 输出 | 语义 |
| --- | --- | --- | --- |
| load | workflow_key | StateBundle | 读取指定 Workflow 的 State、Active Plan、Observation、Decision 和待处理 Draft |
| loadHostCapabilities | workflow_key | HostCapabilitiesSnapshot | 读取显式 Executor 能力与可用性快照；不返回 Agent/Runtime/ExtensionPackage 句柄 |
| storeValidatedPlan | ValidatedPlan、expected state revision | StoreResult | 记录待评审计划 |
| activatePlan | Plan、expected state revision/resourceVersion | ActivateResult | 保存 Gate-approved Plan，并 CAS 更新唯一 `active_plan_id` |
| apply | LifecycleAction、expected state revision/resourceVersion | ApplyResult | 通过 Host Use Case 幂等应用；并发条件与动作身份分离 |
| observe | workflow_key、cursor | ObservationBatch | 获取指定 Workflow 的新执行和外部结果 |
| cancel | operation_id | CancelResult | 请求取消指定 Host 操作 |

该 Port 是 Driver 和高级集成使用的 SPI。普通嵌入式项目优先使用 `registerExecutor + advance/runUntilBlocked` Facade；OAC 只在 Controller 内部实现 WorkflowOrchestrationHost。

## 18. 稳定幂等身份

LifecycleAction 的幂等身份至少由以下内容生成：

    action-type
    + workflow-identity
    + work-unit-id or planning-lineage-id
    + iteration
    + subject-id-and-digest
    + execution-or-plan-digest

不能使用 expected state/resourceVersion、Pod 名、当前时间、随机 UUID 或一次 HTTP Request ID 作为唯一业务幂等依据。

# Workflow 与执行契约参考

共同记法见 [Reference 共同约定](../conventions.md)，领域边界见 [Workflow Platform Core 详细设计](../../design/detailed/03-workflow-platform-core-detailed-design.md)，Kubernetes 字段见 [Kubernetes 资源契约参考](../cloud-native/kubernetes-resources.md)。

## 1. Workflow

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| workflow_name | 是 | Workflow 稳定产品身份 | 直接使用 CR `metadata.name` |
| project_id | 是 | 所属 Project 身份 | 首版父子 Workflow 必须同 Project |
| goal_artifact_id | 是 | 当前根目标 Artifact 身份 | 重新正式提交目标会创建新的 Artifact ID |
| solution | 是 | 当前行业 `ExactComponentRef<Solution>` | 固定 Solution ID、SemVer 和 artifact digest |
| project_configuration_snapshot | 是 | 创建时规范化的 Project 执行配置快照 | 包含来源 Project revision/digest、Workflow 所需参数、Repository、系统/Policy 环境和能力配置；排除完成后由直接操作读取的 `external_targets[]`，活动 Workflow 不跟随后续修改 |
| team_binding_snapshot | 是 | 创建时责任到候选 AgentDefinition 的初始快照 | 候选使用精确 AgentDefinition ID；活动 Workflow 不跟随后续 Project 修改，但可通过精确 Observation/Decision 显式补充当前 Workflow 的执行器候选而不改写该字段 |
| locked_component_set | 是 | 固定的完整 LockedComponentSet 值 | 内含集合 ID/digest 与全部精确 Package/Binding/config digest；活动 Workflow 不重新解析或原地升级 |
| orchestration_definition | 是 | Kernel 使用的 OrchestrationDefinition 内容和 digest | 由精确 Solution release、配置与解析结果编译；DTO 自身不再重复设置 ID |
| input_artifact_ids[] | 否 | 创建时选择的不可变输入 Artifact 身份 | 可以复用前序 Workflow 的已确认成果；只传精确 ID/digest |
| decision_ids[] | 否 | 当前 Workflow 已接受的正式 Decision 身份 | 创建时可包含显式来源；运行中只能由 Platform Core 类型化命令追加精确 Decision ID，不能改写创建时配置快照或自动替代其他 subject 的 Gate/批准 |
| desired_state | 是 | Running、Paused 或 Cancelled | 映射到 `Workflow.spec` |
| active_plan_id | 是 | 当前已激活 Plan 身份 | 根 Workflow 创建时已激活固定基线 Plan；这是唯一激活事实，但不触发 Planning |
| state | 是 | Workflow 当前状态 | Workflow Controller 写 |
| state_revision | 是 | 活动控制 CAS 修订号 | Action apply 前校验；不是业务版本 |
| predecessor_workflow_name | 后继根 Workflow 必需 | 直接前序根 Workflow 的 `metadata.name` | 只表达来源和审计；不建立父子控制关系，不传播 Pause/Cancel |
| parent_child_workflow_link_id | 否 | 父 WorkUnit 到本 Workflow 的 ChildWorkflowLink 身份 | 根 Workflow 为空 |

根 Workflow 创建方在持久化前必须调用 `Workflow.ValidateCreationSnapshots`，传入可信存储读取的不可变创建时 `project.Project`、该 Project 的精确持久化 digest 和创建时解析出的 `CapabilityConfiguration` 集合。它校验 Project 身份与 revision、完整 Project digest、采用的 `solution_setup`、Workflow 执行配置投影、团队候选快照和能力快照；该值校验器不认证调用方自建的 Project 或 digest。`Workflow.Validate()` 仍只验证资源自身形状，不能替代这项来源证明。

`project_configuration_snapshot.project_revision` 在 JSON 输入中必须存在且非 `null`；`uint64` 的显式 `0` 是合法修订号，不能作为缺失标记。`project_id`、`project_revision` 和 `project_digest` 是来源身份与持久化记录证明，不进入执行配置摘要。`project_digest` 是精确持久化 Project 的摘要，`configuration_digest` 是独立的执行配置摘要。后者对规范化 `ProjectSolutionSetup` 执行投影计算：先使用 Project 契约的 canonicalization 约定摘要化 `solution`、`project_parameters`、`repositories`、`environment_bindings`、`team_bindings` 和 `policy_overrides`，不包含 `external_targets[]`；再将该投影摘要与按 capability identity 排序的精确 capability、revision 和 configuration digest 组成 canonical JSON 并计算摘要。自身 `configuration_digest` 不参与计算。来源 Project 的 `configuration_digest` 仍证明完整已采用 `solution_setup`，与 Project 持久化 digest 和 Workflow 执行配置摘要互不替代。

物理映射：

- 固定基线、输入身份与 `desired_state` 放入 Kubernetes `Workflow.spec`；
- Workflow state、active plan、WorkUnit definition/status、Run 摘要和 Condition 放入 `Workflow.status`；
- 完整 Artifact、Observation、Decision、Event、Audit、Usage 和历史内容保存在 PostgreSQL/Artifact Storage；
- PostgreSQL 不保存一份自行推进的可变 Workflow/WorkUnit 状态。

后继根 Workflow 是一个普通的新根 Workflow：它有新的 `metadata.name`、Goal Artifact 和不可变执行快照。创建后继本身不会关闭、重写或接管前序 Workflow；若前序仍存在可能重复的外部副作用，创建或启动后继 Workflow 前必须由影响分析与 Decision 明确处理，必要的 Pause、Cancel 或 Abandon 仍是独立显式命令。前序 Observation 如需复用，应作为新输入 Artifact 的类型化来源记录，而不是复制旧 WorkUnit 状态。

## 2. WorkUnit

WorkUnit 是 Workflow 聚合中的独立协调单位，不是独立 CRD。一个 WorkUnit 包含一份不可变 Definition 和一份直接可读的 Status。

### 2.1 Definition

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| work_unit_id | 是 | WorkUnit 稳定身份 | iteration 不改变 ID |
| plan_id | 是 | 来源 Plan 身份 | 每个 WorkUnit 都必须来自已校验并激活的 Plan |
| plan_node_id | 是 | 来源 Plan 节点身份 | 与 `plan_id` 一起提供确定性追溯和幂等创建 |
| type | 是 | Plan 节点提供的领域工作标签 | 只用于合同、展示和查询，不注册控制状态或要求 Solution 穷举类型 |
| work_unit_contract | 是 | 已解析 WorkUnit Contract 的来源、局部 ID 和 digest | 默认来自精确 Solution package 内内容；独立复用时使用 ExactComponentRef |
| subject | 否 | 当前处理对象的 `SubjectKey` | Review/Gate 使用相同主体 ID/digest |
| activation | 否 | 节点条件激活要求 | 只允许引用同一 Plan 中已有的类型化 Gate/Decision Requirement 和固定 outcome；条件未决保持 Pending，未选择的互斥分支进入 Obsolete |
| dependencies[] | 否 | 硬依赖 | 只有上游 Succeeded 才满足 |
| input_bindings[] | 否 | 将当前 WorkUnit 输入合同槽位绑定到上游 WorkUnit 输出槽位 | 只能引用依赖闭包；派发前解析为已确认输出的精确 ID/digest，缺失、歧义或类型不匹配时阻塞 |
| responsibility_requirement | `executor` 执行必需 | 主执行责任要求 | 描述责任、最低能力和独立性要求；不绑定具体 AgentDefinition、Runtime 或 ExtensionPackage |
| execution | 是 | 唯一主执行绑定 | tagged union：`executor` 或 `child-workflow`；是主执行的唯一来源 |
| harness | 是 | 完整 HarnessDefinition 的已解析来源、局部 ID、内容/config digest 和装配 | 单值固定执行前 Guide、执行后 Sensor、Gate 与返工/收敛；不包含也不复制主执行 |

`execution` 与 `harness` 是两个正交字段：前者回答“由谁/通过什么能力完成主任务”，后者回答“主任务执行前后如何被约束、观察、评审和判定”。页面可以把两者放在同一生命周期视图中连续展示，但保存时不能互相投影、复制或覆盖。Package 内 HarnessDefinition 使用 `harness_definition_id + content_digest`；只有作为 ContentPackage 内容独立发布和复用的 Harness 才使用 `ExactComponentRef<Content>`。

### 2.2 Status

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| state | 是 | WorkUnit 直接状态 | 不由其他字段组合推导 |
| iteration | 是 | 当前业务返工轮次 | 每轮执行对象不可变 |
| state_revision | 是 | WorkUnit 控制 CAS 修订号 | Kernel Action apply 前校验；不是业务版本 |
| active_run_id | 否 | 当前活动 Run 身份 | 不代表历史全部 Run |
| last_transition | 否 | from/to/reason/action/time | 支持恢复和审计 |
| blocking_cause | 否 | 当前失败、返工或等待原因的 tagged union | 必须说明来源类型与稳定身份，不能依靠 ID 前缀猜测 |
| runs[] | 否 | `kind=agent\|component\|human` 的紧凑 Run 摘要 | 每个 Run 独立写入子树；human 是嵌入值，不建立 HumanRun 聚合 |
| gates[] | 否 | Gate 状态与 GateResult ID | Gate 通过才可推进 |
| outputs[] | 否 | `OutputBinding` 列表，显式区分 Artifact、Observation 和 Decision | 只接受已确认的精确输出 |
| child_workflow_name | 否 | 同 Namespace 内子 Workflow 的 `metadata.name` | 仅 child-workflow binding |

DAG Orchestration、普通业务执行和 Remediation 使用同一个 WorkUnit 数据结构与状态机，并且都来自已激活 Plan。显式 DAG Orchestration WorkUnit 也通过 `input_bindings[]` 消费已批准的上游成果，不获得“读取整个 Workflow 上下文”的特殊权限。

## 3. ExecutionBinding

ExecutionBinding 是 WorkUnit `execution` 字段使用的 tagged union。Kernel 只理解逻辑执行要求，不理解 Agent、Runtime、ExtensionPackage、Kubernetes 或具体外部系统。

| `kind` 值 | 英文字段 | 中文字段释义 | 语义与约束 |
| --- | --- | --- | --- |
| `executor` | `capability_requirements[]` | 主执行能力要求 | Host 必须从当前 Workflow 已固定的 Executor Catalog Snapshot 中选择满足全部要求的一个逻辑 Executor；找不到或存在无法裁决的歧义时阻塞 |
| `executor` | `binding_digest` | 执行要求摘要 | 对责任、能力和合同要求规范化计算；用于幂等和防漂移，不是用户填写的版本号 |
| `child-workflow` | `child_solution` | 子行业方案精确身份 | OAC 产品层使用 `ExactComponentRef<Solution>` 创建一个拥有型子 Workflow；Kernel 边界只接收规范化后的子 Workflow 定义键和输入输出合同 |
| `child-workflow` | `binding_digest` | 子 Workflow 执行摘要 | 固定子级定义和输入输出合同，用于幂等创建 |

两个 kind 不能同时存在。`executor` 不直接保存 AgentDefinition、RuntimeBinding、ExtensionPackage ID、Credential 或人员；OAC 的 WorkflowOrchestrationHost 根据初始 TeamBindingSnapshot、有效 Workflow-local Executor Decision、LockedComponentSet、程序/Extension 能力事实，以及 Governance 可生成的人工 `principal_requirement` 建立 Executor Catalog。Kernel Runtime 完成通用能力匹配，Platform Core 再把选中的 `executor_key` 映射为精确产品执行对象。改变执行方式必须由新 Plan 或新 WorkUnit iteration 表达；为既有执行要求补充合格 Agent 不改变执行方式。

## 4. ChildWorkflowLink

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| child_workflow_link_id | 是 | 拥有关系身份 | 幂等创建 |
| parent_workflow_name | 是 | 父 Workflow 的 `metadata.name` | 与子级同 Namespace、同 Project |
| parent_work_unit_id | 是 | 唯一拥有 WorkUnit 身份 | 一个子级只有一个父 WorkUnit |
| parent_iteration | 是 | 触发时 iteration | 旧 iteration 不能接管 |
| child_workflow_name | 是 | 子 Workflow 的 `metadata.name` | 子级拥有控制状态 |
| child_solution | 是 | 子级 `ExactComponentRef<Solution>` | 固定 Solution ID、SemVer 和 artifact digest |
| input_bindings[] | 是 | 父级已确认输入 | 不共享可变 Workspace |
| expected_outcomes[] | 是 | 父级等待的子级结果 | 由 WorkflowOutcome 返回 |
| caused_by | 是 | 指向 InvokeChildWorkflow Action 的 `Causation` | 重放可追踪 |
| idempotency_key | 是 | 重复创建身份 | 重复调用返回同一子级 |

`child_workflow_link_id` 必须由 `parent_workflow_name + parent_work_unit_id + parent_iteration` 确定性生成，只用于关系定位、幂等和审计。ChildWorkflowLink 是父 WorkUnit iteration 内的不可变值，不建立独立表、Repository、聚合或状态机。

## 5. WorkflowOutcome

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| child_workflow_name | 是 | 被观察子 Workflow 的 `metadata.name` | 精确身份 |
| terminal_state_revision | 是 | 子级终态 CAS 修订号 | 与 terminal outcome 一同记录，不把修订号当业务版本 |
| terminal_outcome | 是 | Succeeded、Failed、Cancelled 或 Abandoned | 父级不能改写 |
| output_artifact_ids[] | 否 | 子级输出 Artifact 身份 | 精确不可变记录 |
| output_observation_ids[] | 否 | 子级输出 Observation 身份 | 只追加不可变事实 |
| terminal_plan_id | 是 | 子级进入终态时的不可变 Active Plan 身份 | 用于审计实际收敛的图，不引入第二套完成依据 |
| produced_at | 是 | 观察时间 | 只追加 |

## 6. Run 与 Lineage

一个 Run 表示一次不可变执行；Lineage 表示同一 WorkUnit、同一责任和同一任务范围的连续工作。Agent 可以通过显式交接改变，AgentDefinition、已解析 RuntimeBinding、Session 和 Workspace 仍属于每个 Run。一个 WorkUnit 可以包含 Producer、Verifier、Reviewer 等多个 Run，因此责任和 Lineage 属于 Run，而不是整个 WorkUnit。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| run_id | 是 | 一次执行身份 | Retry/Rework 创建新 Run |
| kind | 是 | `agent`、`component` 或 `human` | 决定报告合同；不能靠可选字段组合猜测类型 |
| lineage_id | 是 | 连续责任时间线 | Product/Requirement、DAG Orchestrator、Developer、Reviewer 等责任分离 |
| work_unit_id | 是 | 所属 WorkUnit 身份 | 不能写其他 WorkUnit |
| work_unit_iteration | 是 | 当前返工轮次 | 与 subject 对齐 |
| responsibility_id | 是 | 当前 Run 使用的责任标签 | 来自 WorkUnit `responsibility_requirement`；具体执行实现可以来自初始 team binding、有效 Workflow-local Executor Decision、组件/程序目录或 Governance human descriptor |
| previous_run_id | 否 | 同 Lineage 上一 Run 身份 | 第一轮为空 |
| agent_definition_id | AgentRun 必需 | 逻辑 Agent 的不可变定义身份 | 精确 ID 与 content digest |
| runtime_binding | AgentRun 必需 | 从 AgentDefinition 选择并固化的 RuntimeBinding 值 | 包含 RuntimeDriver 精确 ExtensionPackage release、image/config/resource digest |
| resolved_capability_configuration | AgentRun 必需 | 本 Run 实际采用的模型与 MCP 配置来源 | 记录 Provider/MCP 稳定 ID、revision 和 configuration digest，不在运行中漂移 |
| runtime_session_id | 否 | 可恢复 Session 身份 | 丢失时允许重建 |
| workspace_id | 视执行方式 | 当前 Workspace 身份 | Reviewer 使用干净 Workspace |
| subject | 是 | 当前执行或评审对象的 `SubjectKey` | Gate 关联同一主体 ID/digest |
| request | 是 | 当前 Run 固定的有界不可变 InvocationRequest | 由 Kernel 的 `DispatchExecution.request` 原样复制；保存精确输入/Guide/结论/反馈的 typed identity、digest 与受限摘要，不内联大正文；不可由 Runtime 改写，也不创建独立 WorkPackage |
| principal_requirement | human 必需 | 人工提交者资格要求 | Host/Platform Core 根据责任、Scope、授权策略和独立性生成；Kernel 不选择具体人员 |
| submitted_by | human 提交后 | 实际人工提交者 Principal | 创建时为空；成功提交后不可改写 |
| result_observation_id | human 提交后 | 结构化结果 Observation 身份 | 指向 ReviewResult、AcceptanceResult 或合同允许的结果 |
| submission_digest | human 提交后 | 人工提交内容摘要 | 幂等与并发冲突依据 |
| status | 否 | 各 Run 专用合同报告的 phase/sequence/result | human 首版 phase 仅 `Pending\|Submitted\|Cancelled`；只能写当前 Run |
| infrastructure | 否 | Execution Host 报告的物理执行摘要 | 不推进 WorkUnit |
| handled_report_sequence | 是 | Controller 已处理最大 sequence | 重复报告不重复推进 |

Platform Core AgentRun event/completion use case 只拥有 `kind=agent` report 语义，Harness Host 只拥有 `kind=component` report 语义，Platform Core human-result use case 只拥有 `kind=human` report 语义，Execution Host 只拥有条件性 infrastructure 语义，Workflow Controller 只拥有请求、handled sequence 和生命周期语义。Platform Core 是唯一物理 Workflow CR 写入者。

Workflow CR 中的 Run 集合是有界活动投影。非终态 Run、当前 `active_run_id` 和当前 iteration 的 Gate/返工所需 Run 保留完整有界 `request`；终态 report 已被处理且完整请求与所有结构化结果已经进入不可变历史后，较旧 Run 可以压缩为固定终态摘要，只保留 Run/Lineage/责任/subject、`request_digest`、终态、结果身份/digest 和 handled sequence。Controller 使用内部 `CompactTerminalRunSummaries` 类型化资源命令提交可压缩 Run 与预期摘要，Platform Core 仅在完整历史和 digest/终态/handled sequence 一致时 CAS 应用；失败只保留原条目供以后重试。历史查询必须仍返回原完整请求并校验同一 digest。该压缩不是 Kernel Action、删除 Run、归档 Workflow、状态迁移或活动状态 offload，也不能让 Controller 从 PostgreSQL 历史计算下一步。

Run 创建时，Kernel Runtime 已完成通用 input binding 解析、Executor 匹配和 InvocationRequest 组装；OAC Host 对源自 Project/Workflow FormField 的输入只解析固定 SolutionPackage 中的名称与说明，并把它们作为 InputResource `display` 与资源身份/digest 一起提供，不把表单 Schema 交给 Kernel。Platform Core 只补充产品治理与物化事实，并把同一有界控制信封完整保存到 `Run.request`；AgentDefinition、RuntimeBinding、模型/MCP 配置和 Session/Workspace 作为并列 Run 事实保存，不写入 Kernel 请求。Agent 通过 run-scoped AgentWorkChannel 和 `oacok work show` 读取该请求，通过 `guide/read` 按请求固定的 identity/digest 读取大内容；人工用户通过 Web/API 读取完全相同的固定请求，不需要 `oacok`。两种提交都必须匹配同一 `invocation_id + request_digest`，任何入口都不得重新组装当前 Workflow 最新内容。超出 Kernel Contract 或 Workflow CR 控制面上限时必须在创建 Run 前以类型化诊断阻塞，禁止静默截断或用通用 payload ref 绕过。

human Run 不包含 AgentDefinition、RuntimeBinding、Session、Workspace、模型、MCP 或 Kubernetes infrastructure，也不增加 `WaitingForHuman` WorkUnit 状态。主执行使用 `InProgress`，独立 Review 使用 `InReview`。首版不提供领取、转派、锁定或租约：所有满足 `principal_requirement` 的授权用户都能看到任务，首个通过 `expected_work_unit_revision + request_digest` CAS 的有效提交成功；同一 idempotency key 和 submission digest 返回原结果，其他并发提交返回 Conflict。`AttentionItem` 只是可重建读模型。

Session 是否可恢复与 Lineage 是否连续分开判断。只有同一 AgentDefinition、兼容的已解析 RuntimeBinding 和相同安全上下文满足时才恢复原 Session；Agent 交接或执行绑定变化默认创建新 Session，并用 Artifact、Observation、Review 和 Decision 重建上下文。

每个通过 Agent 协作协议提交终态结果的 Run 必须产生结构化 `run-conclusion` Observation，至少包含 summary，并可包含 completed、remaining、risks 和相关 resource keys。人工提交通过同一个 Kernel Submission Validator 校验后，直接形成合同规定的 ReviewResult、AcceptanceResult 或其他 Observation。Run outcome 表示责任执行是否完成，不等同业务 verdict：例如 Reviewer 成功完成并提交 Reject 时，Run 可以 Succeeded/Submitted，而 ReviewResult.verdict 为 reject。独立 Reviewer 的 ReviewResult 至少包含 verdict、summary、findings、nits 和 evidence；Reject 时 findings 不能为空。Reviewer Run 的 request 必须包含 Producer 的精确输出与 run conclusion；返工 Run 的 request 必须包含完整 ReviewResult，而不是只包含 `ReviewRejected` 状态。

WorkUnit、iteration、Lineage、Run、run-conclusion、ReviewResult、Artifact、Decision 和 GateResult 可以投影为连续任务时间线。该时间线仅是可重建 Read Model，不创建 Issue、Comment、WorkPackage 或 Timeline 聚合。换 Agent 时可以保持 Lineage，但必须新建不兼容 Session，并从上述结构化记录恢复任务上下文；隐藏 Prompt、私有推理和全量工具日志不参与交接正确性。

产品观察界面在此基础上区分三种粒度：当前 Active Plan 与 WorkUnit status 形成只读全局 DAG；跨 WorkUnit 的不可变事件形成 Workflow 全局时间线；上述 Run/Lineage 关系形成节点级连续任务线。三者通过 Workflow、WorkUnit、Run、Subject ID/digest 和事件游标互相定位，但都不是新的领域对象。Kernel 只拥有节点级连续性和生命周期语义；跨节点时间排序、权限裁剪和页面组装属于 Platform Core Query 与 Web。

Agent 通过 `oacok work show` 读取的是当前 Run 固定 InvocationRequest 中的最小必要任务线切片，而不是用户界面的完整全局时间线。它可以包含前序 conclusion、完整 ReviewResult、feedback、Artifact、Observation 和 Decision，但不能包含其他 Agent 的隐藏 Session、私有推理或无关全量事件。

独立 Review 的派发链固定为：HarnessDefinition 中存在 Required `responsibility-run` Inferential Sensor Binding → 程序验证通过后 Kernel Runtime 根据 HostCapabilitiesSnapshot 匹配 Reviewer `executor_key`；零匹配时进入 Waiting 和 MissingExecutor 处理 → 匹配成功后产生 `StartReview` → Workflow Controller 创建独立 Run request → Platform Core 把逻辑执行器解析为精确 Agent、程序或人工实现并完成 Authorization/Admission。Agent 分支由 Execution Host 创建 Reviewer AgentRun Job，并在 Job 内调用 RuntimeDriver；人工分支保存 `kind=human` Run request，并向满足 `principal_requirement` 的用户开放同一请求。两者都提交与 InvocationRequest 绑定的 WorkSubmission，ReviewResult 作为 Observation 回到同一 WorkUnit Gate。Platform Core 不重新选择 Reviewer，Sensor Extension 也不得使用 `agent.invoke` 代替这条链路。Reviewer Pass 不能代替需要的用户 Decision。

### 6.1 Decision 与澄清

每个 Decision 只绑定一个精确 subject，不支持一个 Decision 内放入多个主体。

| 字段 | 必需 | 中文字段释义 | 关键约束 |
| --- | --- | --- | --- |
| `decision_id` | 是 | 正式决定身份 | 创建后不可变；重复幂等请求返回同一身份 |
| `workflow_name` | 是 | 所属 Workflow 名称 | 使用 Kubernetes `metadata.name` |
| `work_unit_id` | 视场景 | 所属 WorkUnit 身份 | Workflow 级 Decision 可以为空 |
| `subject` | 是 | 被决定对象 | 精确 `SubjectKey`，必须包含对象类型、ID 和 digest |
| `decision_type` | 是 | 决定类型 | `approve`、`request_changes`、`answer`、`reject`、`abandon` 等是否可用由当前 Contract/Harness 决定，不是所有页面统一暴露 |
| `structured_result` | 按类型 | 结构化决定内容 | Answer 必须满足 Question Schema；风险接受/风险例外必须结构化声明被接受的风险、范围和约束；普通 Approve 可按 Contract 省略 |
| `principal` | 是 | 作出决定的主体 | 必须满足当前 ApproverRequirement 与 Authorization |
| `reason` | 按类型 | 原因或反馈 | 普通 Approve 与 Answer 可选；Request Changes、Reject、Abandon、风险接受和风险例外必填，且不能替代 `decision_type`、`structured_result` 和精确 subject |
| `caused_by` | 是 | 触发来源 | 指向 Question Observation、Artifact、Gate 或前序命令 |
| `created_at / decision_digest` | 是 | 创建时间与内容摘要 | 支持审计、幂等和冲突判断 |

一个用户动作需要同时批准多个主体时，应用层可以执行 `SubmitDecisionsAtomically`：请求携带 `expected_work_unit_revision`、`items[]` 和幂等键；每个 item 仍生成一个普通单主体 Decision。Platform Core 在一个事务中保存全部 Decision、CommandRecord、AuditEvent 和一个 Outbox 命令，并让 Workflow 一次性观察完整 Decision ID 集合。任一主体过期、授权失败或 CAS 冲突时整体失败；不创建 Decision Batch/Bundle 领域对象，也不暴露部分批准。

Decision Requirement 可以把多个既有精确事实规范化为一个单主体 `SubjectKey` 值。例如某次 Preview 最终验收可以使用 Deployment 身份作为 `subject.id`，并把当前 Release、实际部署摘要、健康 Observation 和 AcceptanceResult 的精确 ID/digest 共同计算为 `subject.digest`。这只是一次确定性的 Decision 主体装配，不创建新的业务聚合、Artifact、Version 或持久化聚合视图。Query 可以展示该值，Command 必须在提交时重新读取权威事实、按同一规范化算法计算并比较 digest；任何输入变化都使旧页面主体过期。

Agent 或程序需要澄清时先追加类型化 Question Observation，WorkUnit 进入 `Waiting`。用户的 `answer` Decision 精确绑定该 Observation；条件满足后创建同一 Lineage、同一 business iteration 的后续 Run，并从 `Waiting` 返回 `InProgress`。只有正式提交结果被打回时才进入 `InRework` 并增加返工 iteration。

缺少执行器时使用同一不可变记录链：Host 根据 Kernel 的零匹配结果追加 `MissingExecutor` Observation，至少固定 `work_unit_id`（工作单元身份）、`responsibility_requirement`（责任要求）、`capability_requirements[]`（能力要求）、`accepted_contracts[]`（兼容合同）、`executor_requirement_digest`（规范化要求摘要）和 `match_diagnostics[]`（匹配诊断）。用户提交 `answer` Decision，并在 `structured_result.agent_definition_id` 中选择精确 AgentDefinition。该 Decision 只补充当前 Workflow 中相同要求摘要的未启动工作，不修改 TeamBindingSnapshot；下一 Run 必须记录 Decision ID/digest 与实际 AgentDefinition。

Decision 字段校验由当前 Decision Requirement、Question Schema 或风险合同给出机器可读规则，Platform Core 统一执行，Web/API/CLI 不得各自维护不同必填逻辑：

| Decision 场景 | `structured_result`（结构化决定内容） | `reason`（决定理由） |
| --- | --- | --- |
| 普通 `approve` | 按当前 Contract，可选 | 可选 |
| `answer` | 必填，并满足 Question Schema | 可选 |
| `request_changes` | 按 Contract 可选 | 必填，必须给出可执行修改原因 |
| `reject` | 按 Contract 可选 | 必填 |
| `abandon` | 可选记录终止分类 | 必填 |
| 风险接受或风险例外 | 必填，明确风险、影响范围、约束和授权选项 | 必填 |

用户评论、聊天和未正式提交的表单草稿不构成 Decision。Decision 一旦创建不可编辑或删除；修正错误决定只能对当前仍有效的精确 subject 提交 Contract 允许的新 Decision 或通过正式恢复流程处理，不能覆盖历史记录。

## 7. Workflow 状态

| 状态 | 语义 | 终态 |
| --- | --- | --- |
| Created | 固定基线 Plan 已编译并激活，尚未派发首个 Ready WorkUnit | 否 |
| Planning | DAG Orchestration WorkUnit 正在推进；保留状态值以表达图编排阶段 | 否 |
| Running | Active Plan 正在推进 | 否 |
| Waiting | 等待 Decision、资源或外部结果 | 否 |
| Paused | 用户软暂停；停止新派发，已启动操作继续安全收敛 | 否 |
| Cancelling | 正在取消 | 否 |
| Succeeded | 所有已选择且可达的 WorkUnit 均 Succeeded、互斥未选择节点均 Obsolete，且不存在活动/Unknown 子 Workflow 或执行 | 是 |
| Failed | 无法恢复 | 是 |
| Cancelled | 取消完成 | 是 |
| Abandoned | 用户放弃 | 是 |

## 8. WorkUnit 状态与主转换

| 状态 | 语义 | 终态 |
| --- | --- | --- |
| Pending | 依赖未满足 | 否 |
| Ready | 首次执行条件满足，可以派发 | 否 |
| InProgress | 主执行正在运行 | 否 |
| InVerification | 程序验证正在运行 | 否 |
| VerificationFailed | 验证失败，等待返工/等待/终止判断 | 否 |
| InReview | 独立评审正在运行 | 否 |
| ReviewRejected | 评审拒绝，等待返工/等待/终止判断 | 否 |
| NitsRequired | 有条件通过，等待一次有限修复 | 否 |
| InRework | 原责任 Lineage 正在返工 | 否 |
| Waiting | 等待用户、资源或外部结果 | 否 |
| Cancelling | 正在取消活动执行 | 否 |
| Succeeded | Contract 与 Required Gate 满足 | 是 |
| Failed | 已确认无法继续 | 是 |
| Cancelled | 取消完成 | 是 |
| Abandoned | 用户放弃 | 是 |
| Obsolete | 新计划不再需要且从未执行 | 是 |

主转换：

```text
Pending -> Ready -> InProgress -> InVerification
InProgress --clarification required--> Waiting
Waiting --Answer satisfies clarification--> InProgress
InVerification --pass + required responsibility-run review binding--> InReview
InVerification --pass + no review + Decision pending--> Waiting
InVerification --pass + no review + no Decision pending--> Succeeded
InVerification --fail--> VerificationFailed
VerificationFailed --rework allowed--> InRework
VerificationFailed --decision required--> Waiting
VerificationFailed --unrecoverable--> Failed
InReview --pass + Decision pending--> Waiting
InReview --pass + no Decision pending--> Succeeded
InReview --reject--> ReviewRejected
InReview --pass-with-nits--> NitsRequired
ReviewRejected --rework allowed--> InRework
ReviewRejected --decision required--> Waiting
ReviewRejected --unrecoverable--> Failed
NitsRequired --> InRework
InRework --result submitted--> InVerification
Waiting --Approve satisfies all Gate requirements--> Succeeded
Waiting --Request Changes + local rework requirement--> InRework
Waiting --Abandon--> Abandoned
```

`Ready` 不承载返工语义。Nits 修复完成后重跑完整程序验证；验证通过后直接 Succeeded，不再进行第二次 Review。

`request_changes` 是 Decision 类型，不是全局写死的“当前 WorkUnit 返工”指令。当前 Decision Requirement 声明本地成果返工时，才进入 `InRework` 并增加 iteration；如果 Active Plan 为该 outcome 声明了节点级 `activation` 分支，Kernel 保留已完成 WorkUnit，激活该显式后继分支，并把同一互斥来源的未选择节点标记为 `Obsolete`。例如最终 Preview 验收的 `request_changes` 不重新执行 E2E Acceptance WorkUnit，而是激活 Architect 技术变更设计节点；后续设计 Review、用户批准和 DAG Orchestration 都由图中普通节点承接。

## 9. AgentRun 状态

| 状态 | 语义 | 终态 |
| --- | --- | --- |
| Pending | 已创建未启动 | 否 |
| Starting | Runtime/Workspace 准备中 | 否 |
| Running | Runtime 执行中 | 否 |
| Cancelling | 正在取消 | 否 |
| Succeeded | Runtime 正常结束且结构化结果已提交 | 是 |
| Failed | Runtime 或执行失败 | 是 |
| Cancelled | 取消完成 | 是 |
| Lost | 无法确认 Runtime 终态 | 是 |

AgentRun.Succeeded 不等于 WorkUnit.Succeeded。Platform Core AgentRun completion use case 只记录 Runtime 终态并更新当前 Run；Kernel 和 Workflow Controller 决定 WorkUnit 的下一状态。

## 10. Pause、Cancel 与恢复

Pause 通过 `spec.desiredState=Paused` 触发，并使用固定软暂停语义：不创建新 Run、Review、返工、Workspace、外部执行或子 Workflow；已经启动的 Run 和外部操作不被强杀，继续到成功、失败、取消或 Unknown 等可确认结果。宿主仍提交其报告和 Observation，Kernel 可以记录结果，但暂停期间不能派发下游动作。Pause 沿拥有关系传播给 Child Workflow；页面使用 Condition 展示仍在收敛的活动执行，不增加 `Pausing` 状态，也不允许 Solution/Policy 改写这条通用语义。

Resume 将 `spec.desiredState` 恢复为 `Running`，Controller 和 Kernel 从暂停期间保存的最新 Plan、Workflow/WorkUnit State、Observation、Decision 和 Run 结果继续；不会回滚、重放已完成 Run 或恢复旧快照。

Cancel 通过 `spec.desiredState=Cancelled` 触发：WorkUnit 进入 Cancelling，Controller 请求 Execution Host 或子 Workflow 取消；确认完成后进入 Cancelled。

Abandon 是要求非空 reason 的业务 Decision，不是另一个 desiredState 值。它表示目标不再追求并进入 Abandoned，同时执行停止新派发、取消活动执行和观察 Unknown 外部结果的收敛流程；Cancel 只表示操作层面终止执行。

Controller 重启后只需重新列举 Workflow CR。Platform Core AgentRun use case 和 Execution Host 分别把 Run 业务结果与基础设施结果写入对应子树；Controller 不直接 Watch Job/Pod/PVC。

## 11. 数据所有权

| 数据 | 权威来源 |
| --- | --- |
| Workflow identity | Kubernetes `metadata.name` |
| 根目标、期望状态和固定输入 | Kubernetes `Workflow.spec` |
| Workflow/WorkUnit 活动状态、Plan 与 Run 摘要 | Kubernetes `Workflow.status` |
| Artifact、Plan、Observation、GateResult、Decision、Event、Audit、Usage 与历史 | PostgreSQL/Artifact Storage 中的不可变记录 |
| AgentRunEvent | 结构化事件存储 |
| Runtime Session | Runtime Provider |
| Workspace 实际状态 | WorkspacePort 内置实现 |
| Pod/Job/Node | Kubernetes |
| Git、CI、Registry、Deployment | 外部系统 |

Workflow CRD 是活动控制状态的单一事实来源；外部存储保存不可变内容与历史，不自行推进 Workflow 或 WorkUnit。

## 12. Workflow 归档与历史保留

终态 Workflow 的归档信息属于 PostgreSQL Workflow history/catalog metadata，不进入 `Workflow.spec` 或 `Workflow.status`：

| 英文字段 | 必需 | 中文字段释义 | 关键约束 |
| --- | --- | --- | --- |
| workflow_key | 是 | Workflow 精确身份 | 在 OAC 产品内对应 Namespace + `metadata.name`；不能另造 `spec.workflowId` |
| archived_at | 否 | Workflow 归档时间 | 只允许终态 Workflow；非空时默认从普通历史列表隐藏 |
| archived_by | archived_at 非空时是 | Workflow 归档操作人 | 保存精确 Principal；恢复时与 `archived_at` 一起清除 |

`ArchiveWorkflow` 与 `RestoreWorkflow` 只改变查询可见性。恢复不会重建 Kubernetes 资源、重开 WorkUnit、改写 Outcome 或恢复为 Running。子 Workflow 和所属历史记录随父/自身 Workflow 的授权可见性投影展示；共享 Artifact 不能因某个 Workflow 归档而失去其他引用。

Workflow 查询可以返回以下瞬时字段，但不持久化通用处置对象：

| 英文字段 | 中文字段释义 | 值的作用 |
| --- | --- | --- |
| `allowed_actions[]` | 当前允许操作集合 | 终态且有权限时可以包含 `archive` 或 `restore`；不包含普通用户 `delete` |
| `reference_summary` | 引用摘要 | 说明父子 Workflow、Artifact、Deployment、Audit 或其他历史关联 |
| `deletion_blockers[]` | 删除阻塞原因 | 对普通用户明确返回 Workflow/history 受 Retention 与不可变追溯保护，而不是展示无效删除按钮 |

普通用户不能硬删 Workflow、Artifact、Observation、GateResult、Decision、Plan、Run、UsageRecord 或 AuditEvent。Governance Retention/Legal Hold 可以在策略允许时清理大载荷，但必须保留最小 identity、digest、Outcome、时间和审计 tombstone；该过程不改变 Kernel lifecycle，也不形成用户可操作的回收站或删除状态机。

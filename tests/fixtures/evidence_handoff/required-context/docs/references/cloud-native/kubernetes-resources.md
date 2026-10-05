# Kubernetes 资源契约参考

共同记法见 [Reference 共同约定](../conventions.md)，实现边界见 [Kubernetes-native 控制面详细设计](../../design/detailed/05-kubernetes-native-control-plane-detailed-design.md)。

本文集中定义首版 `Workflow` CRD 的字段、状态与写入边界。Workflow CR 是活动控制状态的唯一 Kubernetes 资源；WorkUnit 与 AgentRun 不单独定义 CRD。表中的“写入者”指语义所有者；Platform Core 是唯一物理 Workflow CR 写入者，并代表命令来源使用独立 field manager。

## 1. Workflow TypeMeta

| 字段 | 值 | 含义 |
| --- | --- | --- |
| apiVersion | `orchestration.oac.dev/v1alpha1` | 首版 CRD API |
| kind | `Workflow` | 活动 Workflow 控制对象 |
| scope | Namespaced | 物理部署边界；不等同 Project/Tenant |
| status subresource | enabled | `spec` 与 `status` 使用独立写入边界 |

## 2. Metadata 与标签

| 字段/标签 | 必需 | 含义 | 约束 |
| --- | --- | --- | --- |
| `metadata.name` | 是 | Workflow 稳定身份 | 使用 DNS 合法的 ULID/slug；不再复制到 `spec.workflowId` |
| `metadata.namespace` | 是 | Kubernetes 物理范围 | Project 不等同 Namespace |
| `oac.dev/project-id` | 按部署需要 | Project 查询与 Controller 分区索引 | 由 `spec.projectId` 派生，不授予权限 |
| `app.kubernetes.io/managed-by` | 是 | 固定为 `open-agent-cluster` | 资源筛选 |
| `oac.dev/parent-workflow` | 子级按需 | 父 Workflow 查询索引 | 权威关系仍是 `spec.parent` |
| `oac.dev/parent-work-unit` | 子级按需 | 拥有父 WorkUnit 查询索引 | 权威关系仍是 `spec.parent` |
| `finalizers` | 活动副作用存在时 | 删除前观察/取消 OAC 拥有副作用 | 不能无限阻塞删除 |

`metadata.name` 是 Workflow 的产品身份；`metadata.uid` 只表示该 Kubernetes 对象的物理实例。标签是派生索引，不是第二权威来源。

## 3. WorkflowSpec

| 字段 | 必需 | 含义 | 写入者与约束 |
| --- | --- | --- | --- |
| `projectId` | 是 | 所属 Project 身份 | 首版父子 Workflow 同 Project |
| `goalArtifactId` | 是 | 根目标 Artifact 身份 | 精确不可变 ID/digest |
| `solution` | 是 | `ExactComponentRef<Solution>` | 固定 Solution ID、SemVer 与 artifact digest |
| `projectConfigurationSnapshot` | 是 | 创建时复制的 Project 执行配置 | 包含来源 Project revision/digest、Workflow 所需参数、Repository、系统/Policy 环境和能力配置；不复制完成后由直接操作读取的 `externalTargets`，活动 Workflow 不隐式采用更新 |
| `teamBindingSnapshot` | 是 | 责任到候选 AgentDefinition 的固定快照 | 候选使用精确 AgentDefinition ID；活动 Workflow 不隐式切换 |
| `lockedComponentSet` | 是 | 完整不可变 LockedComponentSet 值 | 内含稳定 ID、`setDigest`、全部精确 Package/Binding/config digest 和解析报告身份；不使用 `latest` 或版本范围，不在恢复时重新解析 |
| `orchestrationDefinition` | 是 | Kernel 使用的 OrchestrationDefinition 内容与 digest | 只包含 Kernel 协议版本、通用规划约束和摘要；DTO 不重复设置 ID，也不携带 Solution 自定义完成表达式 |
| `inputArtifactIds[]` | 否 | 创建 Workflow 时选择的正式 Artifact 输入 | 只放精确不可变 ID；活动 Workflow 不追加或改写该数组 |
| `decisionIds[]` | 否 | 已提交正式 Decision 身份 | 每个 Decision 固定 subject ID/digest |
| `predecessorWorkflowName` | 后继根 Workflow 必需 | 直接前序根 Workflow 的 `metadata.name` | 只用于来源、导航和审计；不是 `parent`，不传播控制状态 |
| `desiredState` | 是 | 用户/系统期望 | `Running`、`Paused`、`Cancelled` |
| `parent` | 子级必需 | `ParentWorkflowBinding`，包含 parentWorkflowName、parentWorkUnitId、iteration 和 causedBy | 根 Workflow 为空 |
| `retentionPolicy` | 否 | 创建时求值后的终态资源保留要求及来源 Policy ID/revision/digest | 活动 Workflow 不因默认策略变化而改变已承诺的资源处理语义 |

`spec` 不包含：

- `workflowId` 或 `inputRevision`；
- WorkUnit 定义和状态；
- PlanDraft 或 Plan 全文；
- AgentRunEvent、日志、Prompt、Skill 内容；
- Kubernetes Job/Pod phase；
- Secret 明文；
- 任意可执行控制代码。

字段或固定输入发生真实变化时，Kubernetes Watch 已能触发协调，不使用额外 `inputRevision` 制造第二套变化标记。

## 4. desiredState

| 值 | 含义 |
| --- | --- |
| `Running` | 创建新工作并持续协调；也用于从 Paused 恢复 |
| `Paused` | 固定软暂停：不创建新的执行；已启动操作继续到可确认结果并回写事实，暂停期间不派发下游 |
| `Cancelled` | 请求取消拥有的活动执行与子 Workflow |

`Succeeded`、`Failed`、`Cancelled` 等观察结果属于 `status.state`，不能写入 `desiredState` 伪造终态。

## 5. WorkflowStatus

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| `observedGeneration` | 是 | 已处理的 `metadata.generation` | Condition 必须对齐 |
| `state` | 是 | Workflow 当前状态 | Workflow Controller 拥有语义，Platform Core 物理写入 |
| `stateRevision` | 是 | 活动控制状态 CAS 修订号 | 单调递增；不是业务版本 |
| `activePlanId` | 根 Workflow 首次可观察状态后必需 | 当前激活 Plan 身份；唯一激活事实 | Workflow 创建时固定基线 Plan；该字段只是快照指针，不触发 Planning |
| `workUnits[]` | 否 | 当前 Workflow 的 WorkUnit 定义与活动状态 | keyed map-list |
| `conditions[]` | 是 | Workflow 级可观察状态与阻塞原因 | 遵循 Kubernetes Condition 语义 |
| `outcomeId` | 终态必需 | 最终 WorkflowOutcome 身份 | 不嵌入大对象 |
| `nextReconcileAt` | 否 | 下一次定时协调 | 显式 RFC3339 时间 |

首版不定义 `planningState`、`activeRuns`、`childWorkflows` 或活动状态外置字段。DAG Orchestration 是普通 WorkUnit；Run 与子 Workflow 信息内聚在所属 WorkUnit；活动控制元数据直接保存在 CRD 中。

## 6. Workflow 状态

| 值 | 语义 | 终态 |
| --- | --- | --- |
| `Created` | 资源已创建，尚未完成首次协调 | 否 |
| `Planning` | DAG Orchestration WorkUnit 正在推进；状态值保留为通用图编排阶段 | 否 |
| `Running` | Active Plan 正在推进 | 否 |
| `Waiting` | 等待 Decision、资源或外部结果 | 否 |
| `Paused` | 已收敛到暂停状态 | 否 |
| `Cancelling` | 正在取消活动执行与子 Workflow | 否 |
| `Succeeded` | 当前活动 DAG 按固定规则成功收敛 | 是 |
| `Failed` | 无法安全恢复或 Policy 决定失败 | 是 |
| `Cancelled` | 取消完成 | 是 |
| `Abandoned` | 用户放弃 | 是 |

## 7. WorkUnit

每个 WorkUnit 使用一个稳定 `workUnitId`，并包含 `definition` 与 `status` 两部分。

### 7.1 WorkUnitDefinition

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| `workUnitId` | 是 | WorkUnit 稳定身份 | iteration 不改变 ID |
| `planId` | 是 | 来源 Plan 身份 | 每个 WorkUnit 都必须来自已校验并激活的 Plan |
| `planNodeId` | 是 | 来源 Plan 节点身份 | 与 `planId` 一起提供确定性追溯和幂等创建 |
| `type` | 是 | Solution 定义的 WorkUnit 类型 | 不注册新的控制状态 |
| `workUnitContract` | 是 | WorkUnit Contract 的来源、局部 ID 和 digest | 默认来自固定 Solution release；独立发布时使用 ExactComponentRef |
| `subject` | 否 | 当前处理对象的 `SubjectKey` | Review/Gate 关联同一主体 ID/digest |
| `dependencies[]` | 否 | 硬依赖 WorkUnit ID | 仅 `Succeeded` 满足依赖 |
| `inputBindings[]` | 否 | 当前输入合同槽位到上游输出槽位的 typed mapping | 只能引用依赖闭包；派发前解析为已确认输出的精确 ID/digest |
| `execution` | 是 | `executor` 或 `child-workflow` | tagged union；主执行唯一来源，`executor` 保存逻辑能力要求 |
| `harness` | 是 | HarnessDefinition 的来源、局部 ID、content/config digest 和装配 | 固定 Guide、Sensor、Gate 与返工/收敛，不包含主执行 |

WorkUnitDefinition 由 Workflow Controller 根据 Kernel 认可并激活的 Plan 创建。Running 或终态 WorkUnit 的 Definition 不原地修改。

### 7.2 WorkUnitStatus

| 字段 | 必需 | 含义 | 写入者 |
| --- | --- | --- | --- |
| `state` | 是 | 单一、直接可读的 WorkUnit 状态 | Workflow Controller 语义 |
| `iteration` | 是 | 当前执行/返工轮次 | Workflow Controller 语义 |
| `stateRevision` | 是 | WorkUnit 控制 CAS 修订号 | Workflow Controller 语义 |
| `activeRunId` | 否 | 当前活动 Run 身份 | Workflow Controller 语义 |
| `lastTransition` | 否 | from/to/reason/action/time | Workflow Controller 语义 |
| `blockingCause` | 否 | 当前失败、返工或等待原因的 tagged union | Workflow Controller 语义 |
| `runs[]` | 否 | 当前与历史 Run 的紧凑控制摘要 | 多字段写入者，见第 8 节 |
| `gates[]` | 否 | Gate 状态与精确 `gateResultId` | Workflow Controller 语义 |
| `outputs[]` | 否 | `OutputBinding` 列表，显式区分 Artifact、Observation 和 Decision | Workflow Controller 语义 |
| `childWorkflowName` | 否 | `child-workflow` 绑定创建的子 Workflow `metadata.name` | Workflow Controller 语义 |

### 7.3 WorkUnit 状态

| 值 | 语义 | 终态 |
| --- | --- | --- |
| `Pending` | 已创建但硬依赖未满足 | 否 |
| `Ready` | 首次执行条件满足，可以派发 | 否 |
| `InProgress` | Product/Requirement、DAG Orchestrator、Developer 等主执行正在运行 | 否 |
| `InVerification` | 静态编译、测试、CI 或其他程序验证正在运行 | 否 |
| `VerificationFailed` | 验证已失败，等待返工、人工处理或终止判断 | 否 |
| `InReview` | 独立 Reviewer 或人工评审正在运行 | 否 |
| `ReviewRejected` | Review 已拒绝，等待返工、人工处理或终止判断 | 否 |
| `NitsRequired` | Review 已有条件通过，等待一次有限修复 | 否 |
| `InRework` | 原责任 Lineage 正在执行返工 | 否 |
| `Waiting` | 等待 Decision、资源或外部结果 | 否 |
| `Cancelling` | 正在取消活动 Run 或子 Workflow | 否 |
| `Succeeded` | Contract 与 Required Gate 满足 | 是 |
| `Failed` | 已确认无法继续 | 是 |
| `Cancelled` | 取消完成 | 是 |
| `Abandoned` | 用户放弃 | 是 |
| `Obsolete` | 新 Plan 不再需要且从未执行 | 是 |

`Ready` 只用于首次依赖满足后的派发。Verification 失败和 Review Reject 必须先进入各自明确状态，再由 Kernel 决定 `InRework`、`Waiting` 或终态 `Failed`。WorkUnit 状态不能由 Run role、purpose、布尔值或 UI 展示字段组合推导。

## 8. Run 摘要

WorkUnit 可以包含多个 `kind=agent|component|human` Run。Product/Requirement、DAG Orchestrator、Developer、Reviewer、Acceptance Executor 等责任和 Lineage 属于 Run，不属于整个 WorkUnit。human Run 是内嵌控制值，不创建 HumanRun CRD 或聚合。

| 字段 | 必需 | 含义 | 写入者 |
| --- | --- | --- | --- |
| `runId` | 是 | 一次不可变执行身份 | Workflow Controller 语义 |
| `kind` | 是 | `agent`、`component` 或 `human` | Workflow Controller 语义 |
| `iteration` | 是 | 所属 WorkUnit iteration | Workflow Controller 语义 |
| `responsibilityId` | 是 | 当前 Run 使用的 Project 责任标签身份 | Workflow Controller 语义 |
| `lineageId` | 是 | 同一责任连续时间线 | Workflow Controller 语义 |
| `request` | 是 | Kernel 产生的有界不可变 InvocationRequest 控制信封 | Workflow Controller 语义；只保存 typed resource/subject identity、digest 与受限摘要，不混入产品物化字段或大内容 |
| `agentDefinitionId` / `agentDefinitionDigest` | agent 必需 | 本 Run 固定的 AgentDefinition 身份与内容摘要 | Platform Core 解析后由 Workflow Controller 创建请求时保存；不进入 Kernel InvocationRequest |
| `runtimeBinding` | agent 必需 | 本 Run 固定的 RuntimeBinding 值 | 来自 AgentDefinition；包含精确 RuntimeDriver release 与配置摘要 |
| `resolvedCapabilityConfiguration` | agent 必需 | 实际模型 Provider/MCP 配置来源 | 固定稳定身份、revision 与 configuration digest，不随目录更新 |
| `runtimeSessionId` / `workspaceId` | agent 视需要 | Runtime Session 与 Workspace 身份 | 每 Run 事实；不属于 InvocationRequest |
| `principalRequirement` | human 必需 | 人工提交者资格要求 | Workflow Controller 保存 Host/Platform Core 解析结果；不包含具体人员 |
| `submittedBy` | human 提交后 | 实际提交者 Principal | Platform Core human-result use case |
| `resultObservationId` | human 提交后 | ReviewResult/AcceptanceResult 等 Observation 身份 | Platform Core human-result use case |
| `submissionDigest` | human 提交后 | 提交内容摘要 | Platform Core human-result use case；幂等与冲突依据 |
| `status` | 否 | Run phase、`reportSequence`、结构化 result、digest、terminal time；agent 另含 `runtimeStreamId + lastRuntimeEventSequence` | agent: Platform Core AgentRun event/completion use case；component: Harness Host；human: Platform Core human-result use case |
| `infrastructure` | 否 | executionHandle、Job observed state、failure reason、observedAt | Execution Host 语义 |
| `handledReportSequence` | 是 | Controller 已消费的最大 Run status sequence | Workflow Controller 语义 |

AgentRun Job 只能使用短期执行凭证调用绑定 `workflow/workUnit/iteration/runId` 的 AgentRun event/completion API；Platform Core AgentRun use case 校验后只写入该 Run 的 report 子树。Harness Host 只能提交当前 ComponentRun report command；Platform Core human-result use case 只能提交已校验的当前 human Run report；Execution Host 只能提交同一外部执行 Run 的 infrastructure command。任何报告方都不能修改 WorkUnit `state`、Plan、Gate、其他 Run 或 `spec`。

`request` 的 Schema 与整体大小限制由 Kernel Contract 和 OAC Workflow Resource Command 校验共同强制。输入正文、较大的 Guide/Contract/Review evidence、日志、代码和二进制内容保存在现有不可变存储中，AgentWorkChannel 只能按请求内固定的 resource key/subject identity/digest 读取。超限命令必须拒绝并记录明确 blocker；禁止截断、读取 latest、把正文塞入 Condition，或增加通用 `requestPayloadRef`。

`runs[]` 的压缩规则固定且由 Workflow Controller 语义拥有：非终态、`activeRunId` 指向以及当前 iteration 的 Gate/返工仍需要的条目不得压缩；终态 report 已由 `handledReportSequence` 确认处理，且完整 request/结果已写入不可变历史后，Controller 可提交 `CompactTerminalRunSummaries` 类型化资源命令。命令为每个 `runId` 携带预期 `requestDigest`、terminal state 与 handled sequence；Platform Core 验证不可变历史和结果身份一致并通过 CAS 后，旧条目才可移除 `request` 正文、详细 `status` 和 `infrastructure`，同时必须保留 `runId`、`kind`、`iteration`、`responsibilityId`、`lineageId`、subject、`requestDigest`、terminal state、结果 IDs/digests 与 handled sequence。旧请求详情由 Platform Core 历史查询返回，不由 Controller 回读；NotReady/Conflict 只延后压缩，不改变业务状态。

`RuntimeEvent.sequence` 是 Runtime SDK stream 内的严格递增序号；`status.lastRuntimeEventSequence` 只是其当前已持久化高水位。`status.reportSequence` 是 Run report 子树的产品级单调更新序号，Workflow Controller 只用它与 `handledReportSequence` 判断是否出现新报告。三者不得复用一个含义模糊的 `sequence` 字段。

## 9. Condition

Condition 使用 Kubernetes 标准字段：`type`、`status`、`observedGeneration`、`lastTransitionTime`、`reason`、`message`。

首版稳定 Type：

| Type | 含义 |
| --- | --- |
| `Ready` | Workflow 是否已经成功收敛并可作为已完成结果读取 |
| `Progressing` | 是否正在规划、执行、验证、评审、返工或取消 |
| `Blocked` | 是否因输入、Decision、Provider、Policy 或错误阻塞 |
| `Degraded` | 是否存在可恢复的基础设施或外部能力问题 |
| `Accepted` | spec 与固定输入快照是否已通过校验 |

不要为每个 WorkUnit 错误创建顶层 Condition；WorkUnit 细节保存在自己的直接状态和结构化 result/blockingCause 中。

## 10. Job 命名与标签

| 字段/标签 | 含义 | 约束 |
| --- | --- | --- |
| Job name | `oac-run-<stable-suffix>` | 由 run identity 确定性生成 |
| `oac.dev/workflow-name` | 所属 Workflow | 必需 |
| `oac.dev/work-unit-id` | 所属 WorkUnit | 必需 |
| `oac.dev/run-id` | AgentRun/ComponentRun | 必需且唯一；human Run 不创建 Job，因此没有此标签 |
| `oac.dev/action-id` | LifecycleAction | 支持幂等与因果追踪 |
| `oac.dev/input-digest` | 执行输入摘要 | AlreadyExists 时必须匹配 |
| `oac.dev/runtime-driver` | RuntimeDriver 精确 ExtensionPackage 身份 | AgentRun 必需 |
| `oac.dev/workspace-implementation` | WorkspacePort 内置实现精确身份 | 使用 Workspace 时必需；不是第三方 Package 身份 |

AgentRun Job 固定使用 `restartPolicy=Never` 和 `backoffLimit=0`：一个 AgentRun 只能对应一个 RuntimeDriver.Execute、一个 RuntimeSession 和一个 SDK event stream。进程失败时当前 Run 显式失败；任何重新执行使用新的 AgentRun，而不是由 Kubernetes 在同一 Run 下静默创建第二条 sequence 从 1 开始的事件流。

Execution Host 负责观察 Job/Pod/PVC，并通过 Platform Core 把需要业务处理的基础设施结果写入 Run `infrastructure`。Workflow Controller 不直接 Watch 这些资源。

## 11. Execution Host 请求

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `apiVersion` | 是 | 内部接口版本 |
| `executionId` | 是 | 稳定执行 identity |
| `idempotencyKey` | 是 | 重复 start/cancel/observe 身份 |
| `workflowKey` | 是 | Workflow `{namespace, name}` 业务定位键 |
| `workflowUid` | 是 | 当前 Kubernetes Workflow 对象 UID；用于防止同名资源删除重建后误接管旧执行 |
| `workUnitId` / `iteration` | 是 | WorkUnit 身份与业务轮次 |
| `runId` | 是 | 被物化为 Kubernetes 执行的 ComponentRun 或 AgentRun 身份；human Run 不调用本接口 |
| `actionId` / `actionDigest` | 是 | LifecycleAction 身份与业务摘要 |
| `runtimeBinding` | AgentRun 必需 | 从 AgentDefinition 选择并固定的 RuntimeBinding 值和 digest |
| `workspaceRequest` | 视需要 | 通用 WorkspacePort 请求 |
| `resourceRequirements` | 是 | CPU、内存、GPU、存储和拓扑 |
| `securityRequirements` | 是 | ServiceAccount、网络、Secret delivery 等 |
| `inputs[]` | 是 | `ExecutionInput` tagged union，显式区分 Artifact、Guide、Contract 与 Decision |
| `inputDigest` | 是 | 规范化执行输入摘要 |
| `agentRunApiUri` / `runCredentialHandle` | AgentRun 必需 | AgentRun Job 调用当前 Run Event、Complete 与 AgentWorkChannel API 的地址和短期不透明凭证句柄；Runtime SDK 不接收 |

响应只能表示请求和物理执行结果，不能直接宣称 WorkUnit 通过。

## 12. Field Manager 与列表合并

| Field manager | 允许字段 |
| --- | --- |
| `oac-platform-core` | 根 Workflow `spec` 用户期望、不可变输入身份与执行快照 |
| `oac-workflow-controller` | Workflow/WorkUnit state、Definition、Run request、handled sequence、Gate、Condition、Outcome、finalizer、子 Workflow 系统字段 |
| `oac-platform-core-agent-run` | 当前 AgentRun 的 `status` 子树；只由 AgentRun Event/Complete 用例使用 |
| `oac-harness-host` | 当前 ComponentRun 的 `status` 子树；只由 Harness Host report command 使用 |
| `oac-platform-core-human-result` | 当前 human Run 的 `status` 子树；只由已校验人工提交用例使用 |
| `oac-execution-host` | 当前 Run 的 `infrastructure` 子树；Job/PVC/执行资源 spec 与 labels |
| Kubernetes 内置控制器 | Job/Pod/PVC status 与系统字段 |

CRD OpenAPI Schema 必须将：

- `status.workUnits` 定义为 `x-kubernetes-list-type: map`，key 为 `workUnitId`；
- `status.workUnits[].status.runs` 定义为 `x-kubernetes-list-type: map`，key 为 `runId`。

写入使用精确 Patch/Server-Side Apply 和稳定 field manager。Kubernetes RBAC 负责资源级授权；应用端口、run-scoped 凭证和字段校验负责 Run 级授权。禁止使用全对象 Update 或共享 field manager 掩盖边界冲突。

## 13. 容量、API 版本与兼容

- CRD 只保存紧凑控制元数据、状态、不可变身份与执行快照，不保存日志、消息流、大型报告或二进制成果；
- Platform Operations 的容量读模型直接从 Kubernetes Node allocatable、Workload requests/limits、ResourceQuota、运行状态与 Pending 调度原因组装；这些字段不复制进 Workflow CRD，也不形成 CapacitySnapshot；
- 实时 CPU/Memory/GPU observed usage 来自可选 Metrics API 或外部可观测 Adapter。来源缺失、过期或无权限时显示 unknown，不能写成 0 或阻止其他容量事实查询；
- 最低并发验收数字不进入 CRD、Kernel 或 Scheduler 语义；平台不创建 Run Slot、分布式信号量或自定义调度器；
- 首版通过 Child Workflow 对超大 DAG 分区，不建设活动状态 Offload 或影子 WorkUnit CRD；
- `v1alpha1` 允许在首个稳定发布前调整，但每次变更必须同步文档、Schema、迁移判断和验收；
- 发布后的字段删除、语义改变或枚举收窄需要新 API version 与迁移计划；
- Status 新增可选字段应保持旧 Controller 可忽略；
- spec 中未知必需语义不能静默忽略；
- Workflow 固定组件和方案精确版本，升级 Controller 不能原地替换活动 Workflow 的业务基线。

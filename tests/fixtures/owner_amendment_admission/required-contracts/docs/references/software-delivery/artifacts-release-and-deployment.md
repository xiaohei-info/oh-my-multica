# 软件交付成果、Release 与 Deployment 契约参考

本文定义 Software Delivery 领域的核心成果和外部发布记录。Requirement、Design、Acceptance Plan、Project Rules 等都是通用不可变 `Artifact` 的类型化领域视图，不建立第二套表、版本聚合或控制状态。

共同记法见 [Reference 共同约定](../conventions.md)，流程与边界见 [生产级软件交付详细设计](../../design/detailed/11-software-delivery-detailed-design.md)。

## 1. 共同 Artifact 语义

Requirement、Solution Design、Acceptance Criteria、Acceptance Plan、Technical Overview、Detailed Design 和 Project Rules 在正式提交后共享以下不可变 Artifact 语义。编辑草稿或自动保存不创建 Artifact；返工后再次正式提交才创建新的 `artifact_id`。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| artifact_id | 是 | 不可变成果身份 | 正式提交时创建；创建后不修改 |
| artifact_type | 是 | 领域成果类型 | Schema 由精确 Software Delivery Solution release 内的内容绑定确定 |
| subject | 是 | 所属 Project、Workflow 或 WorkUnit 的 `SubjectKey` | 不跨主体复用可变内容 |
| previous_artifact_id | 否 | 上一份同类 Artifact 身份 | 形成线性或显式分支历史，不是版本号 |
| content_uri / content_digest | 是 | 成果正文的存储地址与摘要 | Gate 针对精确 digest；URI 不作为业务身份 |
| schema | 是 | 领域 Schema 的 package-local ID/digest 或独立 ExactComponentRef | 不支持时拒绝消费 |
| provenance_inputs[] | 是 | `ProvenanceInput` tagged union，显式区分 Artifact、Observation、Decision 和外部事实 | 只接受精确不可变输入 |
| author_lineage_id | 是 | 作者责任时间线身份 | 不等同 Runtime |
| review_result_ids[] | 否 | 独立 ReviewResult 身份 | 必须关联同一 Artifact ID/digest |
| gate_result_id | 否 | 当前 Artifact 质量 GateResult 身份 | 未通过不得作为下游 Required 输入 |
| created_at | 是 | 创建时间 | 不作为隐式选择规则 |

## 2. Requirement

| 字段或内容组 | 含义 | 关键约束 |
| --- | --- | --- |
| business_goal | 用户要达成的业务结果 | 不能退化成实现任务列表 |
| users_and_scenarios[] | 目标用户与使用场景 | 每个主要场景可验收 |
| scope / non_goals | 范围与非目标 | 已批准后变更需新 Artifact 和 Decision |
| constraints[] | 时间、平台、合规和技术限制 | 约束来源可追溯 |
| open_questions[] | 已知但允许随批准基线保留的非阻塞问题 | 阻塞性问题必须先形成 Question Observation 并得到 Answer Decision，不能把未回答问题藏在正式 Artifact 中 |
| acceptance_direction | 验收方向 | 由 AcceptanceCriteria 细化 |

## 3. AcceptanceCriteria

| 字段或内容组 | 含义 | 关键约束 |
| --- | --- | --- |
| scenarios[] | 前置条件、操作、可观察预期与失败判据 | 不以 Agent 自报代替观察 |
| business_approvers[] | `ApproverRequirement` 列表，显式区分 Principal ID 与 Responsibility ID | Decision 需授权 |
| design_constraints[] | 设计必须满足的业务验收约束 | 不规定具体测试实现 |

Acceptance Criteria 回答“什么结果才算业务成功”。它不定义测试环境、测试数据、执行顺序、Acceptance Bundle 或 suite digest；这些属于基于已批准 Design 形成的 AcceptancePlan。

### 3.1 Requirement Baseline 批准合同

Requirement Baseline 是一个 WorkUnit 的用户可见审核单元，不是新的 Artifact、聚合或 Version。该 WorkUnit 正式输出一个 Requirement Artifact 和一个 AcceptanceCriteria Artifact；两者保持独立身份，便于后续 Design、Acceptance Plan、Release 和审计精确引用。

阻塞性澄清使用类型化 Question Observation。用户的 `answer` Decision 精确绑定 Question Observation ID/digest，Product/Requirement 责任 Agent 在同一 Lineage 和同一 business iteration 中继续；回答问题不创建返工 iteration。

内置 Software Delivery Solution 的 Requirement Baseline Harness 默认包含程序完整性/覆盖检查和用户 Decision Requirement，不包含 Required 独立 Reviewer。企业发布的定制 Solution 可以在 Harness 中增加 `responsibility-run`，但 Project 用户不能在运行页面临时改变该流程。

用户点击一次“批准需求基线”时，Software Delivery Application 调用 `SubmitDecisionsAtomically`，以一个事务分别创建：

- 绑定当前 Requirement Artifact ID/digest 的 `approve` Decision；
- 绑定当前 AcceptanceCriteria Artifact ID/digest 的 `approve` Decision。

每个 Decision 仍是单主体记录；系统不创建 RequirementBaseline Artifact、DecisionBundle、审批 Version 或部分批准状态。任一主体过期、授权失败或 WorkUnit revision 冲突时整体失败。Gate 只有同时观察到当前两份 Artifact 的 Approve Decision 才能 Pass。

“要求修改”可以指向 Requirement、AcceptanceCriteria 或两者，但不会批准未被指出的一份。Product/Requirement 责任 Agent 进入 `InRework`；发生变化的成果正式提交新 Artifact ID/digest，未变化的成果可以继续复用原身份，当前组合随后重新经过程序检查和一次原子批准。内置需求基线页面不提供含义重叠的通用 `reject`；可修正问题使用 `request_changes`，停止整个目标使用 `abandon`。

## 4. Architecture Design

内置 Software Delivery Solution 的 `Architecture Design` 是页面阶段分组，不是 Artifact 类型、WorkUnit、Version 或审批聚合。固定 BaselinePlanTemplate 按顺序包含：

1. Solution Design WorkUnit，输出 `SolutionDesign` Artifact；
2. Technical Overview WorkUnit，输出 `TechnicalOverview` Artifact；
3. Detailed Design WorkUnit，输出 `DetailedDesign` Artifact。

前一 WorkUnit 的程序检查、Required Independent Review 和用户 Decision 全部满足后，下一 WorkUnit 才能进入 `Ready`。每份正式设计成果使用共同 Artifact 字段，并分别绑定自己的 ReviewResult、GateResult 和用户 Decision；不存在 ArchitectureDesign Artifact、跨三份成果的部分批准或一次性模糊批准。

DetailedDesign Artifact 可以是一份包含多个章节、图和附件的内容包，`content_digest` 覆盖完整提交。固定基线阶段不动态创建 DetailedDesign WorkUnit DAG；后续 Delivery DAG Orchestration 产生的任务级局部设计使用普通动态 WorkUnit 和 Artifact 表达。

Reviewer `reject` 或用户 `request_changes` 都使当前 Architect WorkUnit 进入 `InRework`。`pass-with-nits` 只允许不改变语义的有限修正，并要求重跑程序检查；实质变化必须重新完整 Review。返工后正式提交新的 Artifact ID/digest，旧 ReviewResult 和 Decision 不能自动继承。

用户 `approve` Decision 只绑定当前一份 Design Artifact 的精确 ID/digest。用户可以在意见中定位章节，但系统不创建章节级批准状态；页面不提供与“要求修改”含义重叠的通用 `reject`。

## 5. AcceptancePlan

| 字段或内容组 | 含义 | 关键约束 |
| --- | --- | --- |
| acceptance_plan_artifact_id | 不可变验收方案 Artifact 身份 | 返工后重新正式提交形成新 ID |
| requirement_artifact_id | 已批准 Requirement Artifact 身份 | 必须是精确 ID/digest |
| acceptance_criteria_artifact_id | 已批准 AcceptanceCriteria Artifact 身份 | 每条 Criteria 必须被追踪 |
| design_artifact_ids[] | 已批准 Solution/Technical/Detailed Design 的 Artifact 身份 | 验收方案不得基于未批准设计 |
| criteria_traceability[] | Criteria 到用户级 E2E 场景、步骤和 Evidence 的映射 | 不允许未覆盖 Required Criteria |
| business_journeys[] | 面向用户的端到端业务验收旅程 | 覆盖主要成功路径、关键失败分支和人工确认点；不包含开发单元测试或组件内部测试 |
| environment_requirements | 环境、数据、外部依赖和前置状态 | 无法满足时不得伪造通过 |
| acceptance_bundle_artifact_id | 自动验收程序的 Artifact 身份 | Developer 可运行但不能修改已批准内容 |
| suite_digest | 验收程序摘要 | Preview/Release Acceptance 必须使用同一 digest |
| manual_steps[] | 必需人工操作和可观察结果 | 不能用自由文本成功声明代替 Evidence |
| required_evidence[] | 必需 Observation、Artifact、外部证据和 Decision | Gate 只消费显式类型和精确身份 |
| failure_routing | Rework、Remediation、DAG Re-orchestration 和终态边界 | 不允许验收执行者自行改变 Criteria |
| review_result_ids[] | 独立 ReviewResult 身份 | Reviewer 与 Acceptance Planning Lineage 隔离 |
| approval_decision_id | 用户对精确 Artifact ID/digest 的批准 Decision 身份 | Reviewer Pass 不能代替用户 Decision |

Acceptance Plan 回答“如何从用户角度证明 Acceptance Criteria 在已批准设计和目标环境中端到端成立”。正式提交后的 Artifact 正文只读，用户审批入口只创建绑定精确 ID/digest 的 `approve` 或 `request_changes` Decision，不提供直接内容更新命令。它不定义单元、组件或实现内部集成测试；这些由后续 Developer WorkUnit 的 TDD 与 Computational Sensor 形成。Reviewer Reject 或用户 Request Changes 进入返工；返工后由 Acceptance Planning 责任 Agent 正式提交新的 Artifact，并重新执行程序检查和独立 Review。旧 ReviewResult 和 Decision 不能批准新 Artifact。

## 6. ProjectRules

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| project_rules_artifact_id | 是 | 项目规范 Artifact 身份 | 批准后不可变；返工重新提交新 Artifact |
| project_id | 是 | 所属 Project 身份 | 不跨 Project 隐式继承 |
| rules_content_digest | 是 | 已批准规则正文摘要 | 正文就是当前 ProjectRules Artifact 内容，不再额外引用另一 Artifact |
| provenance_inputs[] | 是 | Requirement、Acceptance Criteria、Design、Acceptance Plan、仓库事实和用户输入的 `ProvenanceInput` | 全部固定 Artifact ID/digest 或稳定外部身份 |
| approval_decision_id | 是 | 用户批准 Decision 身份 | 批准前不能激活 |
| managed_file_path | 是 | 首版固定为仓库根 AGENTS.md | 不允许任意路径 |
| managed_block_id | 是 | 受管区块稳定身份 | 区块外内容归用户所有 |
| base_repository_commit_sha | 是 | 生成时仓库 commit SHA | 并发变化时 Fail Closed |
| expected_block_digest | 是 | 预期受管区块摘要 | Git Sensor 必须确认 |
| applied_repository_commit_sha | 应用后 | 实际 commit SHA | 不以 Agent 自报填充 |
| observed_file_digest | 应用后 | 最终完整文件摘要 | 支持审计和漂移检测 |

`approval_decision_id` 是当前精确 ProjectRules Artifact 与拟应用受管区块的唯一用户授权。平台观察该 Decision 后自动执行 Git 应用与 Sensor 校验，不再创建第二个“应用确认”记录；Decision 已存在也不能把仓库应用状态推断为成功，最终状态必须由 `applied_repository_commit_sha`、完整文件 digest、受管区块 digest 和 Project Rules Gate 共同证明。

`AGENTS.md` 不存在、存在但没有受管区块、已有一个合法受管区块是三种正常输入；分别创建文件、保留原文后追加区块、只替换合法区块。标记缺失、重复、倒序、嵌套或 base 已变化时不得写入。相同输入重复执行必须幂等；外部提交结果未知时先 Observe。base 冲突需要基于新仓库事实形成新的 ProjectRules Artifact 并重新批准，未改变精确输入的瞬时执行失败可以复用原批准按恢复策略重试。

## 7. ReviewResult

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| review_result_id | 是 | 一次 Review 结果身份 | 每轮新建 |
| subject | 是 | 被评审精确 Artifact/Plan/其他记录的 `SubjectKey` | 必须包含 ID/digest；旧结果不能批准新记录 |
| review_run_id | 是 | 产生本结果的 Run 身份 | 可以是 agent、component 或 human Run；必须与当前 subject/iteration 对齐 |
| reviewer_lineage_id | 是 | Reviewer 连续时间线身份 | 与作者 Lineage 隔离 |
| reviewer_principal | human Run 必需 | 实际人工评审者身份 | 来自认证 RequestContext；Agent/程序分支由各自 Run provenance 证明 |
| verdict | 是 | pass、reject 或 pass-with-nits | 其他状态不得隐式映射 |
| summary | 是 | 总体评审结论说明 | 下游和返工执行者无需读取隐藏会话即可理解结果 |
| findings[] | 是 | 严重级别、位置、原因和修复要求 | reject 必须可执行 |
| nits[] | 否 | 不阻塞但需有限修正的建议 | 只允许非语义性小修，不能承载范围/安全/合同问题 |
| evidence_items[] | 是 | `EvidenceItem` tagged union，显式区分 Artifact、Observation、程序报告和外部证据 | 可重放 |
| produced_at | 是 | 结果时间 | 不参与优先级 |

Agent Reviewer 与人工 Reviewer 使用同一 ReviewResult Schema。人工结果由 `SubmitHumanRunResult` 形成 Observation；`reviewer_principal` 证明实际提交者，但不能把 ReviewResult 当作用户批准 Decision。`pass-with-nits` 只允许一次有限修复，修复后必须重跑完整程序验证；范围、Contract、安全、数据和程序失败不能归为 nits。

## 8. Release

Release 只在 Integration、Build、Verification 和 Release Gate 都针对同一组不可变输入通过后形成。Gate 评估候选输入；Release 创建时固定该 GateResult，不存在“先创建可变 Release 再补 Gate”的循环。

### 8.1 ReleaseReadinessView

页面中的“Release 候选”只是用户可读名称。技术上由 Software Delivery Query 形成可重建、只读的 `ReleaseReadinessView`（发布就绪视图），不创建 `ReleaseCandidate` 聚合、ID、Version、数据库记录、CRD 或状态机。视图只投影已经存在的 Artifact、Observation、GateResult、Workflow/WorkUnit 状态和外部构建事实；刷新或输入变化时重新计算。

| 英文字段 | 中文字段释义 | 值与用途 |
| --- | --- | --- |
| workflow_key | 来源 Workflow 定位键 | `{namespace, name}`；定位当前 Delivery，不复制 Workflow identity |
| requirement_artifact_id | 已批准需求成果身份 | 当前 Requirement 的精确 ID/digest |
| acceptance_criteria_artifact_id | 已批准验收标准成果身份 | 当前 Acceptance Criteria 的精确 ID/digest |
| design_artifact_ids[] | 已批准设计成果身份集合 | Solution Design、Technical Overview 和 Detailed Design 的精确 ID/digest |
| project_rules_artifact_id | 已批准项目规则成果身份 | 当前 Project Rules 的精确 ID/digest |
| integration_commit_sha | 集成提交 SHA | 当前候选使用的唯一 Git commit；不接受浮动 Branch |
| build_definition | 构建定义 | 精确 Artifact ID/digest 或 ContentPackage release；说明如何重放构建 |
| artifact_ids[] | 构建制品身份集合 | 镜像、包或二进制的精确 Artifact ID/digest；不接受 tag-only |
| verification_observation_ids[] | 验证证据身份集合 | 针对同一 commit 和制品 digest 的 CI、质量、安全等 Observation |
| acceptance_plan_artifact_id | 验收方案成果身份 | 固定后续 Preview 必须执行的已批准 AcceptancePlan ID/digest |
| suite_digest | 验收套件摘要 | 固定 Acceptance Bundle，防止 Preview 验收期间静默替换 |
| locked_component_set_id | 锁定组件集合身份 | 证明构建和验证使用的精确 SolutionPackage、Harness ExtensionPackage、RuntimeDriver 等实现集合 |
| release_gate_result_id | 当前发布门结果身份 | 可暂缺；存在时必须绑定同一 `release_input_digest`，页面据此展示 Pass、失败、等待或错误原因 |
| release_input_digest | 发布输入摘要 | 服务端对上述规范化精确输入计算的摘要，用于 Gate 绑定、过期检测和形成 Release；不是 Release ID 或版本号 |

Query 必须在授权范围内提供从每个字段下钻到精确 Artifact、Observation、构建报告和 GateResult 的链接。页面可以派生“收集中”“存在阻塞”“Gate 未通过”或“Release 已形成”等中文展示结论，但这些不是持久化状态。

当任一输入变化时，旧 `release_input_digest` 立即过期，旧 GateResult 不能用于新输入。Gate 未 Pass 时不得创建 Release，也不显示默认“确认发布”按钮；用户只能查看证据并返回对应 Build、Verification、Observe、Remediation 或上游 WorkUnit 处理问题。Gate Pass 后，Software Delivery 应用路径以同一精确输入、摘要和 GateResult 正式提交一次不可变 Release Artifact。若 Solution 确实要求发布时人工业务取舍，必须通过显式 Decision Boundary 创建普通 Decision，不能在该视图中增加隐藏审批字段。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| release_id | 是 | Release 稳定身份 | 与 digest 一一对应 |
| workflow_key | 是 | 来源 Delivery 对应 Workflow 的 `{namespace, name}` | Delivery 是 Workflow 的领域视图，不建立第二身份 |
| solution | 是 | 生效软件交付 `ExactComponentRef<Solution>` | 精确 SemVer 和 artifact digest |
| locked_component_set_id | 是 | 构建和验证使用的 LockedComponentSet 身份 | 不原地修改 |
| release_input_digest | 是 | Release Gate 通过时的发布输入摘要 | 必须与 GateResult 绑定的摘要一致；不是 Release identity 或用户版本号 |
| requirement_artifact_id | 是 | 已批准 Requirement Artifact 身份 | 精确 ID/digest |
| acceptance_criteria_artifact_id | 是 | 已批准 AcceptanceCriteria Artifact 身份 | 精确 ID/digest |
| acceptance_plan_artifact_id | 是 | 已批准 AcceptancePlan Artifact 身份 | 精确 ID/digest 并固定 suite digest |
| design_artifact_ids[] | 是 | 生效 Solution/Technical/Detailed Design Artifact 身份 | 精确 ID/digest |
| project_rules_artifact_id | 是 | 生效 ProjectRules Artifact 身份 | 与仓库事实一致 |
| integration_commit_sha | 是 | 最终 Integration commit SHA | 不接受浮动 branch |
| build_definition | 是 | Build 定义 Artifact ID/digest 或独立 ContentPackage 内容的 `ExactComponentRef<Content>` | 可重放，不能用模糊版本字符串 |
| artifact_ids[] | 是 | 镜像、包或二进制 Artifact 身份及 digest | 不允许 tag-only |
| verification_observation_ids[] | 是 | CI、质量和安全 Observation 身份 | 必须针对相同 commit/digest |
| release_gate_result_id | 是 | Release GateResult 身份 | Pass 后才可部署 |
| created_at | 是 | 形成时间 | 重新 Build 必须形成新 Release |

## 9. DeploymentTarget

`DeploymentTarget` 是 Software Delivery Application 使用的类型化目标值。`execution_origin=workflow-component` 的当前集群 Preview 目标由 Workflow 固定的系统 `EnvironmentBinding` 投影；`execution_origin=direct-promotion` 的外部目标由命令提交时 Project 当前 `solution_setup.external_targets[]` 中的 `ExternalTargetBinding` 投影。两种来源共享执行合同，但不能互相替代：外部目标不进入来源 Workflow 快照，Preview 也不要求用户在 Project Setup 填写 Kubernetes Namespace 或 DeploymentDriver。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| deployment_target_id | 是 | 目标稳定身份 | 不包含凭证正文 |
| target_type | 是 | current-cluster、kubernetes 或 cicd | 决定 Deployment SDK 合同与 Driver 候选 |
| environment | 是 | preview、staging、production 等 | 参与授权与 Admission |
| endpoint_uri | 是 | Cluster、API 或 Pipeline 地址 | 精确且可审计；URI 不作为领域身份 |
| credential_binding_id | 视目标 | CredentialBroker 可解析的 CredentialBinding 身份 | 不进入 Artifact 或日志 |
| deployment_driver | 是 | 实际执行部署的精确 DeploymentDriver ExtensionPackage | 由目标类型、当前有效 Policy 和 ExtensionCatalog 解析；普通用户不填写原始 Package ID/版本/digest |
| configuration_digest | 是 | 目标非敏感规范化配置摘要 | `StartPromotion` 提交时重新计算，用于过期检测和执行固定 |
| concurrency_key | 是 | 目标串行化身份 | 防止并发覆盖同一环境 |
| security_requirements | 是 | 网络、数据和放置要求 | Driver 或执行环境无法满足时拒绝 |

## 10. Deployment

`Deployment` 表示一次把精确 Release 部署到精确目标的逻辑执行。它不是 Workflow、WorkUnit、ComponentRun、Artifact Version 或第二套编排状态机。其请求头创建后不可变，外部执行进展通过带单调 `report_sequence` 的追加式报告和 Observation 记录；页面中的当前 `outcome` 是从这些记录组装的读模型。

同一数据合同支持两种明确来源，使用 `execution_origin` tagged union 表达，不能用一组互相排斥的可选字段猜测：

- `workflow-component`：当前集群 Preview 等本来就在 Delivery DAG 中的部署，由现有 WorkUnit/ComponentRun 执行并进入该 Workflow 的 Sensor/Gate；
- `direct-promotion`：用户在已验收 Release 上点击外部 Promotion 后，由 Software Delivery Application 通过 `StartPromotion → deployment.oac.dev/v1 / DeploymentDriver` 直接执行，不创建 Workflow、Plan、WorkUnit、ComponentRun，也不调用 Orchestrator Kernel。

### 10.1 不可变 Deployment 请求

| 英文字段 | 必需 | 中文字段释义 | 关键约束 |
| --- | --- | --- | --- |
| `deployment_id` | 是 | 一次逻辑部署身份 | 由服务端生成；同一幂等键和同一请求摘要返回原身份 |
| `project_id` | 是 | 发起部署的 Project 身份 | 用于 Scope、授权、目标解析和审计 |
| `source_workflow_key` | 是 | 产生 Release 的来源 Delivery/Workflow 定位键 | 只用于追溯，不重新打开或修改来源 Workflow |
| `release_id` | 是 | 被部署 Release 身份 | 必须绑定该 Release 的不可变 content digest，不接受 tag、branch 或 latest |
| `deployment_target_id` | 是 | DeploymentTarget 身份 | 授权、Admission 和目标重读通过后固定 |
| `target_configuration_digest` | 是 | 本次采用的目标配置摘要 | 必须等于提交时服务端重新计算的 `DeploymentTarget.configuration_digest` |
| `deployment_driver` | 是 | 本次固定的精确 DeploymentDriver ExtensionPackage | 执行、Observe 和 Cancel 始终使用同一 ID、SemVer 与 artifact digest |
| `execution_origin` | 是 | 执行来源 tagged union | `workflow-component` 固定 Workflow/WorkUnit/ComponentRun；`direct-promotion` 固定 StartPromotion command/RequestContext causation |
| `expected_digests[]` | 是 | 预期制品摘要 | 直接从 Release 固定制品得到，不由用户填写 |
| `request_digest` | 是 | 规范化部署请求摘要 | 覆盖 Release、目标快照、Driver、预期摘要与 causation；用于幂等和报告绑定 |
| `caused_by` | 是 | 初始 Promotion 命令或前序 Deployment 的 `Causation` | 重试和部署旧 Release 都创建新的 Deployment，不覆盖旧记录，也不需要额外 operation kind |
| `created_at` | 是 | 部署请求形成时间 | 由服务端产生，不作为执行顺序之外的身份 |

### 10.2 追加式 Deployment 报告

| 英文字段 | 必需 | 中文字段释义 | 关键约束 |
| --- | --- | --- | --- |
| `deployment_id` | 是 | 所属部署身份 | 必须匹配不可变请求 |
| `report_sequence` | 是 | 当前 Executor 单调递增报告序号 | 重复序号和摘要幂等；旧序号不能覆盖新结果 |
| `request_digest` | 是 | 报告绑定的部署请求摘要 | 不匹配时拒绝，防止旧回调写入新部署 |
| `outcome` | 是 | Pending、Running、Succeeded、Failed、Unknown、Cancelling 或 Cancelled | 使用下表的明确语义，不把 Pod/Job/Pipeline phase 直接映射为业务结果 |
| `external_run_id` | 外部执行已创建时 | 外部 Job、Pipeline 或 Rollout 稳定身份 | 用于 Observe、Cancel 和恢复 |
| `actual_digests[]` | 观察后 | 实际运行摘要 | 与预期不一致时记录 drift，不能宣称成功 |
| `health_observation_ids[]` | 否 | 健康、URL 和外部状态 Observation 身份 | Observation 只追加并绑定精确目标/外部执行 |
| `error` | 失败或 Unknown 时 | 结构化错误 | 解释失败或未知原因；`Unknown` 不能通过 error 标记绕过恢复规则 |
| `next_observe_at` | 非终态时 | 下一次自动观察时间 | 由 Deployment Host 明确给出并通过持久化 Outbox 调度，不依赖进程内定时器 |
| `observed_at` | 是 | 本次外部观察时间 | 外部时间作为显式事实，不由 Kernel 读取隐式时钟 |

直接 Deployment 的 outcome 语义固定如下：

| outcome | 中文语义 | 是否占用 `concurrency_key` | 允许的恢复操作 |
| --- | --- | --- | --- |
| `Pending` | 请求已可靠接受，尚未确认外部执行已启动 | 是 | Observe；Executor 支持时可 Cancel |
| `Running` | 外部执行已确认进行中 | 是 | Observe；Executor 支持时可 Cancel |
| `Unknown` | 无法证明外部操作是否成功、失败或仍在执行 | 是 | 只能 Observe 或请求 Cancel；禁止 Retry 和部署旧 Release |
| `Cancelling` | Cancel 已请求，但外部终态尚未确认 | 是 | 继续 Observe |
| `Succeeded` | 外部执行已明确成功且实际摘要满足合同 | 否 | 无恢复动作；可从 Release 页面另行发起其他目标部署 |
| `Failed` | 外部执行已明确终止且失败，不存在仍未确认的活动副作用 | 否 | 可显式重试同一 Release，或选择旧 Release 创建新 Deployment |
| `Cancelled` | 外部执行已明确取消并终止 | 否 | 可显式重试同一 Release，或选择旧 Release 创建新 Deployment |

`workflow-component` 的报告继续通过原 WorkUnit 的 ComponentRun、Observation 和 Gate 进入 Workflow；`direct-promotion` 的报告通过 Platform Core 的专用 `SubmitDeploymentReport` 用例写入 Deployment 记录链和 AuditEvent，不写 Workflow CR，也不能改变原 Delivery 的 Succeeded/Promotable 结论。外部 CI/CD 或 Kubernetes 对象都不能直接写平台状态。

自动 Observe 和用户点击“立即重新检查”都作用于原 Deployment，固定复用 `deployment_id + request_digest + external_run_id`，不得调用 `start`。手动 Observe 只把当前 Deployment 的下一次观察提前，并按 `deployment_id + 最新 report_sequence` 合并重复请求。Cancel 命令被接受不等于 `Cancelled`；Deployment Host 调用同一 Driver 的 `cancel` 后仍必须 Observe 到明确终态。

重试和部署旧 Release 共用 `StartPromotion`。恢复页面隐藏携带可选 `caused_by_deployment_id`；服务端把它规范化为新 Deployment 的 `caused_by`，并校验前序 Deployment 属于同一 Project/目标且已是 `Failed` 或 `Cancelled`。新请求重新读取 Project 当前目标、Credential 和 DeploymentDriver，生成新的 `deployment_id` 与 request digest。相同 Release 表示重试；不同的已知可用精确 Release 表示恢复到旧版本，不需要 `RetryDeployment`、`RollbackDeployment` 聚合、`operation_kind` 或 `RolledBack` 状态。

## 11. AcceptanceResult

AcceptanceResult 记录显式 Acceptance WorkUnit 的程序、Agent 或人工主执行者针对同一 Release/Deployment/Preview subject 形成的结构化业务验收结果。它是 Observation 内容合同，不是最终用户 Decision。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| acceptance_result_id | 是 | 一次验收结果身份 | 每次执行新建，不覆盖历史 |
| subject | 是 | 被验收的精确 Release/Deployment/Preview `SubjectKey` | 必须包含 ID/digest，与 Gate 主体一致 |
| acceptance_run_id | 是 | 验收执行 Run 身份 | 可以是 agent、component 或 human Run，必须是显式 Acceptance WorkUnit 的主执行 Run |
| acceptance_lineage_id | 是 | 验收执行责任时间线身份 | 与 Producer/Developer/Reviewer Lineage 隔离 |
| acceptance_principal | human Run 必需 | 实际人工验收执行者身份 | 来自认证 RequestContext；自动分支由 Run provenance 证明 |
| acceptance_plan_subject | 是 | 已批准 AcceptancePlan 的精确 ID/digest 与 suite digest | 执行者不得修改验收标准或 suite |
| verdict | 是 | pass、fail 或 blocked | 不把未知/缺证据隐式映射为 pass |
| summary | 是 | 总体验收结论 | 供 Gate、用户最终验收和返工读取 |
| step_results[] | 是 | 每个业务验收步骤/Criteria 的执行结果 | 包含 step/criteria 身份、expected、actual、result 和证据引用 |
| findings[] | 否 | 失败、阻塞或偏差说明 | fail/blocked 时必须足以路由 Remediation 或上游返工 |
| evidence_items[] | 是 | 页面、日志、程序报告、Artifact、Observation 或外部证据 | 必须可追溯到固定 Release/Deployment 和环境 |
| produced_at | 是 | 结果形成时间 | 不参与优先级 |

显式人工 Acceptance WorkUnit 的主执行者通过 `SubmitHumanRunResult` 提交该合同，首个有效 CAS 结果获胜。默认程序或 Agent E2E 执行也由同一 WorkUnit 的主 Run 直接提交该合同，不再追加二次 Acceptor responsibility-run。AcceptanceResult 不能改写 Acceptance Criteria、Acceptance Plan、suite digest 或失败路由；是否可以最终交付仍由 Gate 与单独的用户验收 Decision 决定。

## 12. 最终 Preview 验收 Decision 主体

最终验收不新建 `PreviewAcceptance`、`AcceptanceApproval` 或 Version 聚合。Software Delivery Application 从既有精确事实组装一个普通 `SubjectKey`，供 `GetPreviewAcceptance` 展示并由 `SubmitDecision` 写入：

| 英文字段 | 中文字段释义 | 值与约束 |
| --- | --- | --- |
| `kind` | 验收主体类型 | 固定为 `software-delivery.preview-acceptance`，用于说明 digest 的业务语义 |
| `id` | 验收主体身份 | 直接使用当前 Preview 的 `deployment_id`（部署身份），不创建新的主体 ID |
| `deployment_id` | Preview 部署身份 | 必须与 `id` 相同，且 Deployment outcome 已确认成功 |
| `release_subject` | 被部署 Release 精确主体 | 包含 `release_id` 与 Release content digest |
| `actual_digests[]` | Preview 实际运行制品摘要集合 | 必须已确认并与 Release 预期摘要一致；规范化计算时排序 |
| `health_observation_subjects[]` | 必需健康观察精确主体集合 | 每项包含 Observation ID/digest；Unknown、失败或缺失时不能生成可批准主体；规范化计算时排序 |
| `acceptance_result_subject` | E2E 验收结果精确主体 | 包含 AcceptanceResult ID/digest，且 verdict 必须为 pass |
| `subject_digest` | 最终验收主体摘要 | 对以上字段的 canonical JSON 计算 `sha256`；页面展示值和提交值必须一致 |

`SubjectKey` 最终写为：

```yaml
kind: software-delivery.preview-acceptance # 验收主体类型：说明这是当前 Preview 的最终业务验收
id: <deployment_id>                        # 验收主体身份：直接复用 Preview Deployment 身份
digest: <subject_digest>                   # 主体摘要：固定 Release、实际部署、健康和 AcceptanceResult
```

`subject_digest` 是运行时计算的精确决策输入，不是 Deployment 版本号，也不单独持久化为业务记录。Platform Core Query 只能从当前 Gate Requirement 指定的精确记录组装它，不能选择“最新 Release”“最新健康结果”或“最新 AcceptanceResult”。用户提交 `approve` 或 `request_changes` 时，Command 必须重新读取同一组权威事实并重算摘要；不一致返回 `StaleSubject`，不得保存 Decision。健康、实际 digest 或 AcceptanceResult 后续发生变化时，既有 Decision 仍保留历史，但不能满足新主体的 Gate。

## 13. 最终验收 Request Changes

最终验收 `request_changes` 与 `approve` 绑定同一个精确 Preview 验收主体，但作用不同：`approve` 满足最终用户 Decision Requirement；`request_changes` 选择 Active Plan 中显式声明的技术变更设计分支。该 Decision 的 `reason` 必填，并作为后续 Architect InvocationRequest 的精确反馈输入。

技术变更设计不创建新的领域成果家族。它复用 Architect 现有 `architecture-design` Capability，并按实际影响正式提交一个或多个既有 Design Artifact 类型：

- `solution-design`：业务解决方案或系统职责边界需要调整；
- `technical-overview`：系统级技术结构、模块关系或主要技术决策需要调整；
- `detailed-design`：实现细节、数据、接口或关键流程需要调整。

每个变化后的 Artifact 使用 `previous_artifact_id` 指向被修订成果，继续使用共同 Artifact Schema、独立 Review 和用户 Decision。未变化的批准 Artifact 原样复用。若 Requirement、Acceptance Criteria 或 Acceptance Plan 也受影响，它们仍按自己的现有 Artifact/Review/Decision 合同修订；Architect 不能通过技术方案内容直接覆盖这些批准基线。全部受影响基线通过后，后续 DAG Orchestration WorkUnit 才能读取它们并形成增量 Plan。

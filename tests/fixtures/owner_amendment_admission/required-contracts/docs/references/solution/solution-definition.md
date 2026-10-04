# SolutionPackage Definition 契约参考

共同记法见 [Reference 共同约定](../conventions.md)，领域边界见 [Solution Definition 详细设计](../../design/detailed/01-solution-definition-detailed-design.md)。

## 1. Solution

SolutionPackage 是一个可安装、不可执行、声明式的行业方案。它只定义一个固定过程抽象：`BaselinePlanTemplate`，用于从用户目标形成经过批准的执行基线。若后续交付需要动态规划，模板必须包含一个普通、显式的 DAG Orchestration WorkUnit；该节点与其他节点一样声明输入、执行要求、WorkUnit Contract、Harness、依赖和后续激活，不再引入第二个 Solution 级规划对象。

BaselinePlanTemplate 是每个精确 Solution 发布包固定的 package-local 核心内容，不是 Project 用户可选择的“默认项”。最终业务完成条件必须通过图中的显式终态 WorkUnit、确认输出、Gate 和 Decision 表达；Solution 不再携带第二套完成合同或终态求值器。

- Solution 作者在发布时必须固定 BaselinePlanTemplate 及其 package-local 内容的局部 ID 和 content digest；显式 DAG Orchestration WorkUnit 与终态业务路径作为模板节点/边随模板摘要一起固定；
- Project Setup 与 New Workflow 只读展示其名称、说明和所属 Solution release，不提供替换控件；
- 任何一项发生语义变化都必须发布新的 Solution package release，不能在同一 SemVer release 下按 Project、环境或用户静默改写；
- 只有当一项内容确实需要跨 Solution 独立安装、复用和升级时，才作为 ContentPackage 内容独立发布，并使用 `ExactComponentRef<Content>`；
- Project 只可以提供固定 Harness 所需的类型化业务参数和用途明确的 CredentialBinding；替换 Harness ExtensionPackage 身份、顺序、Required/Optional 或配置合同必须发布新的 Solution release，或更新其精确引用的独立 Harness ContentPackage，不得在 Project 中形成第二份装配来源。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| `solution_id` | 是 | 行业 Solution 的稳定机器身份 | 不包含版本；不用展示名解析 |
| `version` | 是 | Solution package 的精确 SemVer | Workflow 不允许使用版本范围或 `latest` |
| `artifact_digest` | 是 | Solution package 制品摘要 | 同一 ID/SemVer 下内容不得漂移 |
| `display` | 是 | 本地化名称与描述 | 只用于 Catalog、Web、CLI 和审计展示，不参与控制身份 |
| `project_setup_schema` | 是 | Project 长期配置表单定义 | 顶层声明式 Schema；字段集合可以为空。只收集 Project 用户必须决定的长期差异，不重复暴露 Solution 固定核心定义、平台默认值或原始 Extension Binding |
| `responsibility_slots[]` | 是 | Project Setup 中需要配置的责任岗位提示 | 顶层集合；可以为空。只定义责任、最低能力和推荐模板，不形成角色白名单、不绑定具体 Agent，也不承载运行时独立性规则 |
| `workflow_input_schema` | 是 | 每次创建 Workflow 时需要补充的行业输入表单定义 | 顶层声明式 Schema；字段集合可以为空。平台固定 Goal 仍由 Platform Core 提供，Schema 不进入 BaselinePlanTemplate |
| `baseline_plan_template` | 是 | 初始批准基线的 package-local BaselinePlanTemplate 内容绑定 | 包含局部 ID 和 digest；使用普通 Plan 节点/边结构，不能注册阶段状态机 |
| `package_contents[]` | 否 | 随 Solution 一起发布的局部内容 | 每项包含 kind、content_id、content_digest 和 package path；不单独分配 SemVer |
| `package_dependencies[]` | 否 | 独立安装 Package 依赖 | 每项使用精确 Package 引用；适用于 Harness ExtensionPackage、AgentTemplate 或 SkillPackage |
| `verification_fixtures[]` | 是 | 发布/安装时运行的 package-local 验证案例内容绑定 | 至少覆盖固定基线编译、显式 DAG Orchestration 扩图、返工、等待、恢复和终态 |
| `experience` | 否 | 声明式领域体验 | 只包含平台支持的 forms、views、layouts、renderers 和本地化术语；不得包含任意代码或私有写路径 |

展示元数据只附着在用户需要理解的语义对象上：Solution、Template、Plan Node、Guide、Harness、Contract、输出和 Gate 必须提供本地化 `display.name` 与 `display.description`。Package release 的 ID、SemVer 和 digest，以及 package-local 内容的 ID 和 digest，保持明确的单值或值对象字段；Web 通过固定 Solution release 解析展示元数据，不要求用户为同一个对象重复填写说明。

### 1.1 SolutionCatalogItem

`SolutionCatalogItem` 是按逻辑 Solution 聚合的可重建读模型，用于 Project 创建页选择行业方案。它不持久化用户选择，也不成为 Solution 或 Project 状态。

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| solution_id | 是 | Solution 稳定身份 | 把同一逻辑 Solution 的并行版本归为一张卡片 |
| display | 是 | Solution 本地化名称与说明 | 解释行业方案用途，不参与版本解析 |
| eligible_releases[] | 是 | 当前 Principal 与 Scope 可选择的精确 Solution package release | 每项固定完整 `ExactComponentRef<SolutionPackage>`、来源、ComponentInstall 状态与验证摘要；只包含允许且 Active 的候选 |
| recommended_solution | 否 | Policy 推荐的精确 `ExactComponentRef<Solution>` | 必须属于 `eligible_releases[]`；只作为明确可解释的默认值，不等于 `latest` |
| setup_summary | 是 | 后续 Project Setup 要求摘要 | 汇总配置分组、Required/Recommended Agent 岗位和最低能力，帮助用户预判配置工作量 |

选择规则固定为：只有一个 `eligible_releases[]` 元素时直接选中；存在 `recommended_solution` 时默认选中并明确标记；多个候选且没有推荐值时不预选。页面提交和服务端校验都使用最终精确 `ExactComponentRef<Solution>`，不能只提交 `solution_id`、展示名或版本范围。

说明性 YAML：

```yaml
solutionId: software-delivery
# solutionId：机器使用的稳定身份。
# software-delivery：表示这是完整软件工程交付行业方案。

version: 1.0.0
# version：Solution package 的精确 SemVer。
# 1.0.0：活动 Workflow 固定该版本，不会自动切换到后续版本。

artifactDigest: sha256:...
# artifactDigest：Solution package 的制品摘要。

display:
  # display：面向用户的展示内容，不参与状态判断。

  name:
    zh-CN: 软件工程交付
    en-US: Software Delivery
    # name：Catalog、Web 和 CLI 展示的名称。

  description:
    zh-CN: 从需求、设计和验收基线推进到实现、发布和最终验收。
    en-US: Delivers software from approved baselines through implementation, release, and acceptance.
    # description：解释该 Solution 解决什么问题，避免用户根据 ID 猜测。

projectSetupSchema:
  # projectSetupSchema：Project 长期配置表单定义；直接属于 SolutionPackage，不套 projectSetup 包装对象。
  fields:
    - fieldKey: primary-repository
      # fieldKey：字段的稳定机器键，由 Solution Studio 生成，作者不手工填写。

      display:
        name:
          zh-CN: 主代码仓库
          en-US: Primary Repository
        description:
          zh-CN: 选择或创建本项目进行软件交付时使用的主代码仓库绑定。
          en-US: Selects or creates the primary repository binding used by this project.
        # display：字段名称与说明；说明为必填，并随 Web、CLI 和 Agent 上下文持续可解析。

      fieldType: repository_binding
      # fieldType：平台 Generic Form Engine 提供的字段类型。
      # repository_binding：页面选择或就地创建 RepositoryBinding，最终只保存平台对象身份，不保存明文凭证。

      required: true
      # required：该字段是否必须填写。

responsibilitySlots:
  # responsibilitySlots：Project Setup 中需要配置的责任岗位提示；不限制动态 DAG 创建新的责任要求。
  - slotKey: architect
    # slotKey：稳定责任键，由 Solution Studio 生成，作者不手工填写。

    display:
      name:
        zh-CN: 架构师
        en-US: Architect
      description:
        zh-CN: 负责形成并返工业务方案、概要设计和详细设计。
        en-US: Produces and revises solution, overview, and required detailed designs.
      # display：向用户解释岗位作用；不参与 Agent 身份选择。

    setupRequirement: required
    # setupRequirement：Project Setup 是否要求提前配置该岗位。
    # required：没有合格候选 AgentDefinition 时，Project Setup 不能采用。

    capabilityRequirements: [architecture-design]
    # capabilityRequirements：候选 AgentDefinition 必须满足的基础能力条件。
    # WorkUnit 可以增加或收紧能力要求，但不能削弱这里的基础要求。

    recommendedAgentTemplates:
      - componentId: software-architect
        version: 1.0.0
        artifactDigest: sha256:...
    # recommendedAgentTemplates：用户可一键实例化的精确 AgentTemplate package release，不是强制绑定。

workflowInputSchema:
  # workflowInputSchema：每次创建 Workflow 时补充的行业输入表单；不属于 BaselinePlanTemplate。
  fields:
    - fieldKey: delivery-context
      # fieldKey：字段稳定键，由系统生成。

      display:
        name:
          zh-CN: 交付背景
          en-US: Delivery Context
        description:
          zh-CN: 补充本次交付特有的业务背景；平台固定 Goal 仍单独填写。
          en-US: Adds delivery-specific context while the platform Goal remains separate.

      fieldType: text
      # fieldType：text 表示普通多行文本，由 Generic Form Engine 负责渲染和校验。

      required: false
      # required：false 表示该行业输入可以留空。

baselinePlanTemplate:
  contentId: baseline_software_delivery
  contentDigest: sha256:...
  # baselinePlanTemplate：Workflow 创建时实例化并编译的固定基线图；内置软件交付方案形成 Requirement Baseline、Solution Design、Technical Overview、Detailed Design、Acceptance Plan、Project Rules 和显式 Delivery DAG Orchestration 等节点。

verificationFixtures:
  - contentId: fixture_snake_game_golden_case
    contentDigest: sha256:...
  # verificationFixtures：发布和安装时执行的 package-local 可重复验证案例。
```

Solution 不得：

- 定义 Intent 白名单；
- 定义平台 WorkUnit 结构或穷举允许的 WorkUnit 类型；
- 增加 Workflow/WorkUnit 状态或 LifecycleAction；
- 提供状态转换脚本、Webhook、任意控制代码或 Gate bypass；
- 创建 CRD、数据库表、Controller、API Route 或私有状态；
- 直接创建 AgentRun、Workspace、ComponentRun 或子 Workflow；
- 直接安装或调用 ExtensionPackage、RuntimeDriver 或 Workspace 内部实现；
- 绕过 Plan Compiler、Package 验证或精确 Package 解析，直接把 `BaselinePlanTemplate` 当作已验证活动计划。

## 2. Project 与 Workflow 配置定义

SolutionPackage 直接声明 `project_setup_schema`（Project 长期配置表单）、`responsibility_slots[]`（责任岗位集合）和 `workflow_input_schema`（Workflow 行业输入表单）三个顶层值，不增加 `project_setup` 包装聚合，也不创建独立 Schema Version、Artifact、数据库表或生命周期。Solution 草稿只使用 CAS revision 保存；发布时三个值随精确 SolutionPackage 一起不可变，并由整个 Package artifact digest 防漂移。

`BaselinePlanTemplate` 不属于上述三个配置值。Generic Web 可以只读解释固定基线、显式 DAG 编排节点和终态业务路径将如何作用，但 Project 用户不能覆盖其固定内容。Workflow 内固定 Harness 需要的 Repository、CredentialBinding、外部系统资源或其他平台绑定通过类型化表单字段收集；完成后直接 Promotion 的 `external_targets[]` 只在 Project Setup 按需配置并在 Release 页面选择，不进入 New Workflow 表单或来源 Workflow 快照。原始 ExtensionPackage ID、Extension ID、版本、顺序、Required/Optional 和 digest 不进入普通 Project 或 Workflow 表单。

### 2.1 通用 FormField

`project_setup_schema.fields[]` 与 `workflow_input_schema.fields[]` 复用同一个最小 `FormField` 合同：

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `field_key` | 是 | 字段稳定键 | 由 Solution Studio 自动生成，作者不手工填写；重命名展示文案不改变机器引用 |
| `display.name` | 是 | 字段名称 | 用户在 Web、CLI 和 Agent 上下文中看到的名称 |
| `display.description` | 是 | 字段说明 | 必须解释字段作用和取值含义；不能只给 Value 让使用者猜测 |
| `field_type` | 是 | 字段类型 | 从平台 Generic Form Engine 的受支持类型目录选择；决定渲染、规范化、授权和类型化检查 |
| `required` | 是 | 是否必填 | 只表达输入完整性，不新增业务状态 |
| `default_value` | 否 | 默认值 | 必须符合字段类型和校验；资源字段不得用默认值嵌入明文 Secret |
| `validation` | 否 | 静态校验 | 结构由对应 `field_type` 定义，不是无类型键值包；只使用该类型支持的最小声明式规则，不接受脚本、Webhook 或自由表达式 |
| `options[]` | 条件必需 | 选项集合 | 仅 `choice` 使用；每项必须同时提供 `value`、`display.name` 和 `display.description` |

首版字段类型目录至少包含：

| `field_type` 值 | 中文作用 | 值和平台行为 |
| --- | --- | --- |
| `text` | 文本输入 | 保存规范化文本；只做 Schema 与 Policy 校验 |
| `number` | 数值输入 | 保存规范化数值；校验范围等类型化规则 |
| `boolean` | 布尔开关 | 保存 `true/false`；页面不要求输入内部枚举 |
| `choice` | 单项选择 | 保存所选 option `value`；页面始终展示选项名称与说明 |
| `repository_binding` | Repository 绑定 | 选择或就地创建平台 RepositoryBinding，保存其稳定身份；平台自动执行 Repository Probe |
| `credential_binding` | Credential 绑定 | 选择或就地创建 CredentialBinding，保存其稳定身份；平台自动执行凭证就绪与 Policy 检查，绝不返回或保存明文凭证 |
| `deployment_target_binding` | 外部部署目标绑定 | 选择或就地创建 Project `ExternalTargetBinding`，保存其稳定绑定键；平台自动执行 Target Probe。平台托管 Preview 属于系统 `EnvironmentBinding`，不通过该字段要求用户重复配置 |
| `artifact_input` | 不可变成果输入 | 选择精确 Artifact ID/digest；平台自动校验存在性、类型、授权和可读性 |

每个 `field_type` 由平台提供一个完整类型定义，统一拥有 Value Schema、Renderer、Normalizer、静态 Validation Schema、授权要求和条件性的 Probe Port 映射；FormField 不再携带第二个通用行为配置包。需要在线检查的行为由该定义自动派生，Solution 作者不再填写 `required_component_capabilities[]`、`required_extension_capabilities[]` 或同义 Probe 能力数组。普通标量/选项字段只运行 Schema 与 Policy；资源字段调用 Platform Core 已支持的窄类型化端口。某字段类型要求的在线检查无法映射到受治理端口时，SolutionPackage 发布必须失败，不能用领域页面、脚本、Webhook 或 Harness Extension 私自补做检查。

资源 FormField 的值只保存资源或绑定身份。对于 Project Setup 中允许就地创建且内嵌于 `Project.solution_setup` 的 Repository/DeploymentTarget Binding，Generic Form Renderer 在同一个 Candidate 中产生类型化 Binding，系统生成其稳定键，字段值只引用该键；URI、Endpoint 等规范化配置留在对应 Binding 值中，Secret 明文始终只进入 CredentialStore，不进入字段值、SolutionPackage、Project 或 Workflow。

Project Setup 提交后，字段值被规范化并写入当前 `Project.solution_setup`；Workflow 创建时，相关 Project 配置和本次 `workflow_input_schema` 值一起复制到 `Workflow.spec` 的不可变创建时快照字段。字段名称与说明仍可从该 Workflow 固定的精确 SolutionPackage 解析，并与相关输入一起进入 Agent `InvocationRequest`；Orchestrator Kernel 只消费规范化输入资源，不读取或解释表单 Schema。

### 2.2 ResponsibilitySlot

每个 `responsibility_slot` 只包含 Project Setup 真正需要的责任提示：

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `slot_key` | 是 | 责任岗位稳定键 | 由 Solution Studio 自动生成，作者不手工填写；后续映射为 Project Team Binding 的责任键 |
| `display.name` | 是 | 岗位名称 | 用户可读名称 |
| `display.description` | 是 | 岗位职责说明 | 必须解释该岗位负责什么，并在 Project Setup 与 Agent 选择中持续展示 |
| `setup_requirement` | 是 | Setup 要求 | `required` 表示采用 Setup 前必须有合格候选；`recommended` 只提示，不阻塞 |
| `capability_requirements[]` | 否 | 基础能力要求 | 用于过滤候选 AgentDefinition；具体 WorkUnit 只能增加或收紧，不能削弱该基础要求 |
| `recommended_agent_templates[]` | 否 | 推荐 AgentTemplate | 精确 Package 引用；只提供快捷创建入口，不自动创建、绑定或授权 Agent |

岗位不包含 `independence_from_slots[]`、`source_node_ids[]`、Runtime、模型、MCP、Credential 或“可返工能力”。独立 Reviewer、Lineage/Session/Workspace 隔离和返工规则属于具体 WorkUnit Contract、Harness 与 Platform Core Admission；岗位来源可由 BaselinePlanTemplate 的实际引用重建，不需要复制。增量 PlanDraft 仍可提出未出现在 `responsibility_slots[]` 中的新责任，因此该集合不是角色白名单。Plan Compiler 只校验责任、能力、合同、独立性和 PlanningRules；当前没有匹配 Executor 时，WorkUnit 到达派发点后进入 Waiting，并等待显式执行器绑定 Decision，或由 Active Plan 已声明的补救/DAG 修订节点另行处理，Kernel 不临时创建路径。

### 2.3 WorkflowInputSchema

`workflow_input_schema` 只声明某个行业方案在每次创建 Workflow 时额外需要的领域输入。平台固定的 `goal.title`、`goal.description`、可选背景、已知约束和输入 Artifact 仍由 Platform Core 统一提供，Solution 作者不重复定义；Plan、DAG、WorkUnit、交付范围、Review 轮次和 LifecycleAction 也不属于用户输入。该 Schema 与 `project_setup_schema` 使用同一个 FormField 合同，但值只进入本次 Workflow 快照，不回写 Project 长期配置。

## 3. BaselinePlanTemplate

BaselinePlanTemplate 是参数化的普通 PlanDraft 模板，不是新的 Stage DSL。根 Workflow 创建时，Platform Core 把根 Goal、Workflow 固定 Project 配置与显式输入 Artifact 绑定到模板，产生平台固定基线草案，并使用通用 Plan Compiler 校验 LockedComponentSet、Contract、execution、Harness、依赖、分支闭合和终态路径可达性；Project Setup 与 Workflow 创建用例另行保证内置 Solution 声明的 Required 基线责任具有初始候选。模板结构已经随 SolutionPackage 通过确定性发布检查，因此不再为每个 Workflow 伪造一次 DAG Orchestrator、Plan Review 或 Planning Gate；校验通过后形成带 `fixed-baseline` provenance 的初始 Plan，并写入 `activePlanId`。

如果行业交付不能完全由固定节点完成，BaselinePlanTemplate 必须包含至少一个从批准基线可达的显式 DAG Orchestration WorkUnit。该节点通过普通依赖和 `input_bindings[]` 消费前序已确认输出，`execution.kind=executor` 且能力要求包含 `orchestration.dag`。DAG Orchestrator 的规划方法来自所选 AgentDefinition 的不可变 Instructions 与 Skills；精确业务输入、历史结论和本轮反馈来自 InvocationRequest；输出边界来自 WorkUnit Contract；机器约束来自 PlanningRules；节点特有补充指引可使用普通 Harness Inferential Guide。Plan 耗尽不是替代入口。

内置软件交付 Solution 把 `Architecture Design` 作为页面阶段分组，而不是 PlanNodeTemplate。其固定模板包含顺序依赖的 Solution Design、Technical Overview 和 Detailed Design 三个节点；每个节点拥有独立 WorkUnit Contract、Harness、Artifact、Review 和用户 Decision。Detailed Design 可以包含多个章节、图和附件，但不在批准基线阶段动态生成 DetailedDesign DAG；实现任务的局部设计由后续 Delivery DAG Orchestration 节点扩展。

内置软件交付的动态 Delivery Plan 还必须保留一条显式最终验收返工路径：最终 `request_changes` 只激活新的 Technical Change Design WorkUnit，该节点复用 Architect 岗位现有的 `architecture-design` Capability，并通过本轮 Inferential Guide、精确用户反馈和输出 Contract 表达“针对性变更设计”。它经过独立 Review 和用户 Decision；受影响的 Requirement、Acceptance Criteria 或 Acceptance Plan 先走各自既有修订闭环，之后才允许显式 DAG Orchestration WorkUnit 生成增量 Plan。该路径不增加返工 Capability、ChangeImpact Agent 岗位、技术变更聚合或新的生命周期状态。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| `template_id / content_digest` | 是 | 模板在 Solution package 内的局部身份和内容摘要 | 内容变化要求发布新的 Solution release |
| `display` | 是 | 模板名称与描述 | 只用于展示和审计 |
| `nodes[]` | 是 | 初始候选 WorkUnit 节点 | 使用平台标准 PlanNodeTemplate |
| `edges[]` | 是 | 节点硬依赖 | 必须无环 |

每个 `PlanNodeTemplate` 至少包含：

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| `node_id` | 是 | 模板内稳定节点身份 | 实例化后映射为 WorkUnit identity |
| `display` | 是 | 用户可读名称和描述 | 不作为程序身份 |
| `work_unit_contract` | 是 | 该工作必须满足的 WorkUnit Contract 内容绑定 | 默认是 package-local ID + digest；独立复用时使用 ExactComponentRef |
| `responsibility_requirement` | 是 | 承担该工作的责任/能力要求 | 不绑定具体 Agent 或 Runtime |
| `input_bindings[]` | 否 | 当前节点输入合同槽位到上游节点输出槽位的映射 | 只能引用依赖闭包；运行时解析为已确认输出的精确 ID/digest |
| `activation` | 否 | 节点条件激活要求 | 只允许引用计划中已有的类型化 Gate/Decision Requirement 与明确 outcome；不接受脚本或表达式。条件成立后才能 Ready，互斥条件已选择其他分支时该节点进入 Obsolete |
| `execution` | 是 | `executor` 或 `child-workflow` 主执行 | tagged union；是主执行唯一来源。`executor` 保存能力要求，`child-workflow` 保存子 Solution/合同；不进入 HarnessDefinition |
| `harness` | 是 | 完整 HarnessDefinition 内容绑定 | 单值固定执行准备 Guide、程序/推理 Sensor、独立责任评审、Gate 与返工/收敛；默认是 package-local ID + digest，独立发布时使用 ExactComponentRef |

已经存在兼容且经过 Gate 确认的 Artifact 时，它可以作为显式 Workflow 输入绑定给相应基线节点，由该节点 Harness 与 Gate 判断是否可复用。Template 不通过隐藏条件、省略节点、跳过 DSL 或隐式优先级改变固定图结构。

### 3.1 BaselinePlanTemplate 创作输入

Solution Studio 创建 BaselinePlanTemplate 时，用户只编辑能够表达行业批准基线的内容；package-local ID 和摘要由系统产生。编辑中的内容随 Solution 草稿保存，发布 Solution 时整体固定，不单独生成模板版本号。

| 英文字段 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- |
| `display.name` | 模板名称 | user | 解释这套批准基线的用途，例如“软件交付批准基线” |
| `display.description` | 模板说明 | user | 说明模板将形成哪些经过批准的输入，不参与状态判断 |
| `nodes[]` | 基线工作节点 | user | 每个节点填写名称、说明、WorkUnit Contract、责任要求、必要输入绑定，并在同一生命周期编辑器中分别配置 WorkUnit execution 与 Harness；动态交付必须放置显式 DAG Orchestration 节点 |
| `edges[]` | 节点硬依赖 | user | 用可视依赖关系表达先后条件；系统校验无环、引用有效和可达性 |
| `template_id` | 模板局部身份 | system | 在 Solution package 内稳定，不要求用户猜 ID |
| `content_digest` | 模板内容摘要 | system | 对规范化内容计算，用于安装、解析、审计与不漂移验证 |
| `node_id` | 模板内节点身份 | system | 根据 Studio 中的稳定节点键生成；重命名显示文案不改变程序引用 |
| `harness` | 节点使用的完整 Harness 内容绑定 | system from user selection | 用户在同一生命周期编辑器中选择已有可复用 Harness ContentPackage 内容，或创建 package-local HarnessDefinition；最终节点只保存一份来源和 digest |

每个节点的统一生命周期编辑固定展示：执行准备 → 主执行 → 观察与评估 → Gate 判定 → Kernel 后续处理。主执行只配置一次并保存为 PlanNodeTemplate/WorkUnit `execution`；HarnessDefinition 不保存该值。用户可以勾选“启用独立 Review”；该操作会在“观察与评估”区域创建一个 Required `responsibility-run` Inferential Sensor Binding，并要求配置 Reviewer 责任、评审 Guide 与 ReviewResult Contract。取消勾选会删除该 Binding；系统不另存 `review_required` 布尔值。

Solution 需要执行 E2E 业务旅程、调用外部环境或形成 AcceptanceResult 时，作者应在 BaselinePlanTemplate 或动态 Plan 中创建一个普通 Acceptance WorkUnit，并配置自己的主 `execution`、输入输出 Contract、Harness 和依赖。需要职责分离时再增加一个下游独立 Acceptance WorkUnit；Studio 不把这类多步骤工作折叠成 `responsibility-run`。

启用独立 Review 后，Studio 必须在同一页面清楚展示运行语义：程序验证通过后进入 `InReview`；Kernel Runtime 从 HostCapabilitiesSnapshot 匹配 Reviewer `executor_key`，Platform Core 再解析为 Agent、程序或人工实现并完成准入；Agent Reviewer 使用独立 Lineage、新 Session 和干净 Workspace，人工 Reviewer 使用独立 Lineage、同一固定 Run request 与结构化提交合同，不创建 Session/Workspace/Job；Reject 回到原作者 `InRework`，随后重新程序验证和 Review；Pass-With-Nits 按 Harness rework policy 执行；如果还要求用户批准，则在 Gate 区域单独配置 Decision Requirement。BaselinePlanTemplate 只保存责任要求，不保存具体 Agent 或人员。

### 3.2 WorkUnit Contract 与 Agent 内容绑定

Baseline 固定 DAG 的每个节点必须通过 `work_unit_contract` 引用一个精确 Contract 内容。Contract 默认是当前 Solution 的 package-local 内容；跨 Solution 独立复用时才使用 `ExactComponentRef`。其最小结构为：

| 英文字段 | 必需 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- | --- |
| `content_id` | 是 | Contract 包内内容身份 | 系统生成；只在来源 package 内稳定 |
| `display.name` | 是 | 工作合同名称 | 用户可读，不参与控制身份 |
| `display.description` | 是 | 工作合同说明 | 解释工作边界和结果 |
| `input_slots[]` | 否 | 输入槽位集合 | 每项包含系统生成 `slot_key`、名称、说明、`required` 和 `data_contract`；由节点 `input_bindings[]` 连接上游输出 |
| `output_slots[]` | 是 | 输出槽位集合 | 每项包含同样字段；Required 输出必须确认后才能进入 Gate |
| `content_digest` | 是 | Contract 内容摘要 | 系统对规范化内容计算，发布后不可漂移 |

`data_contract` 是平台支持的类型化数据结构绑定，只用于检查上下游资源类型与结构是否兼容，不执行脚本。Contract 不包含完成表达式、权限规则、AgentTemplate 或 Skill；标准 Agent `WorkSubmission.conclusion`、问题和阻塞报告属于 Kernel 协作协议，不由每个 Solution 重复配置。

节点只保存 `responsibility_requirement` 和 `execution` 的能力/合同要求。`recommended_agent_templates[]` 只位于 `ResponsibilitySlot`，每个 AgentTemplate 自行固定完整精确 `skills[]`；选择模板时 Resolver 自动解析其 Skill 依赖闭包。Project Setup 最终绑定实际 AgentDefinition，Workflow 创建时固定 TeamBindingSnapshot，Kernel 运行时只匹配 ExecutorDescriptor，不读取 AgentTemplate 或 SkillPackage。

SolutionPackage 不提供节点级 `agent_template`、节点级 `skills[]` 或万能 `content_refs[]`。Guide、ReviewResult Contract、Schema 和 Renderer 等内容必须进入各自明确的 WorkUnit Contract、HarnessDefinition、Form 或 Experience 字段。内置 Software Delivery Solution 的固定责任岗位必须提供可开箱实例化的推荐 AgentTemplate 和完整 Skills；通用第三方 Solution 的推荐模板可以为空，但不会因此放宽 WorkUnit 的责任、能力和合同要求。

### 3.3 Harness outcome definitions and branch closure

`HarnessDefinition.gateRequirements[]` 和 `decisionRequirements[]` 的每个 `OutcomeRequirement` 使用类型化 outcome 对象，而不是 string 数组。作者必须为每个 outcome 声明其值和固定 DAG 处置：

```json
{
  "requirementId": "quality-gate",
  "outcomes": [
    { "value": "passed", "disposition": "continuation" },
    { "value": "failed", "disposition": "terminal_non_success" }
  ]
}
```

`disposition` 只能是 `continuation`、`terminal_success` 或 `terminal_non_success`。`value` 是领域键；处置语义完全由 `disposition` 决定。`continuation` 表示该 outcome 必须选择至少一条可收敛的后继路径；`terminal_success` 表示该 outcome 以成功结束固定图；`terminal_non_success` 表示该 outcome 以非成功结果结束固定图。

发布检查按照固定 Plan 内全部已选择 source node 的 Gate/Decision requirement outcome 的可满足组合验证分支闭合。每个 continuation outcome 都独立产生后继义务：必须选择至少一条直接后继路径，其 `activation` 明确引用该 requirement outcome，并到达成功叶子或下游 `terminal_success` assignment；由同一节点另一 requirement outcome 选择的无关成功路径不能代偿该义务。`terminal_non_success` 可以结束该组合，但不能满足成功收敛义务。后继的全部相关 `activation` 条件必须同时匹配该组合。每个 terminal outcome 必须以同一 source requirement 的互斥 activation 条件排除全部图后继；无条件后继或只受无关 requirement 条件限制的后继均不合法。

同一已选择节点的多个 Gate/Decision requirement 按下表聚合；terminal 绝不吞掉 continuation 义务：

| 该节点 assignment 中的处置 | 分支闭合结果 |
| --- | --- |
| 只有 `continuation` | 每个 continuation 必须有选中的成功后继路径 |
| 只有 `terminal_success` | 该节点成功终止；它可以满足上游 continuation 路径 |
| 只有 `terminal_non_success` | 该节点非成功终止，不能满足上游 continuation 路径 |
| `continuation` 与任一 terminal 同时出现 | 无效；continuation 仍需要选中的后继路径，而 terminal 必须排除后继 |
| `terminal_success` 与 `terminal_non_success` 同时出现 | 无效；同一节点不能同时声明相反的终止语义 |

一次完整 assignment 中，所有 `Selected` 节点的 terminal disposition 还必须全局一致：出现 `terminal_success` 和 `terminal_non_success` 两者时，固定图没有唯一 Outcome，发布检查必须失败。求解器对每个 WorkUnit 显式区分 `Selected`、可由不匹配 activation 或已 Obsolete 的硬依赖确定的 `Obsolete`，以及无法从未选择 source 获取 outcome 的 `Unresolved`。图静止时任何 `Unresolved` activation 都是 Plan/Solution 设计缺口，不能被另一条成功分支掩盖。成功叶子按 Selected 图判断：一个 Selected 节点没有 Selected 后继、没有 `terminal_non_success` 且没有未满足 continuation 义务时，即使它的结构后继均为 Obsolete，仍是成功叶子。

为保证发布检查可预测，单次固定 Plan 的分支闭合验证最多检查 1,024 个可满足 outcome 组合；该预算覆盖全部同时已选择的因果组件，不按单一 source node 或组件分别重置。超过该复杂度预算的 package 必须以稳定验证错误失败；验证器不得为该拒绝路径预先分配完整笛卡尔积。

## 4. 固定 DAG 收敛规则

Solution 不定义 Workflow 完成表达式。Orchestrator Kernel 使用唯一、固定的 DAG 收敛规则：

```text
所有 activation 已匹配、因而被选中的可达 WorkUnit = Succeeded
AND 所有互斥未选择的 WorkUnit = Obsolete
AND 不存在非终态、Waiting、Unknown、Cancelling 或活动 Child Workflow
→ CompleteWorkflow

存在 Failed / Cancelled / Abandoned WorkUnit，或图静止但仍有未成功节点
→ 不得成功；返回对应终态或结构化 Plan/Solution 设计缺口
```

WorkUnit 只有在主执行、必需输出、Harness、程序验证、独立 Review、Gate 和 Decision 均满足后才能进入 `Succeeded`，因此 Workflow 层不再重复列举这些业务证据。若需要扩展计划，当前 Plan 必须已经声明带因果链的 DAG Orchestration WorkUnit 及类型化 `activation`；GateResult/Decision 只能选择该既有分支。未声明合法后继时必须报告 Plan/Solution 设计缺口，Kernel 不因图耗尽而隐式规划或创建节点。

Plan Compiler 与 SolutionPackage 发布检查必须验证每条可选择分支都能收敛：成功路径可以使所有已选择节点 Succeeded，互斥分支可以确定性 Obsolete，失败/取消/放弃路径不会被伪装成成功。Generic Web 直接根据 WorkUnit、Gate、Decision 和 Run 状态展示“当前还未完成的节点与原因”，不读取另一份完成要求列表。

## 5. 声明式领域体验

`experience` 是 SolutionPackage 内的可选声明式领域体验。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| `views[]` | 否 | Workflow、WorkUnit、Artifact 和 Observation 的领域读模型 | 可重建，不能驱动 Controller |
| `forms[]` | 否 | 创建 Workflow 和提交 Decision 的表单 Schema | 最终转为通用 ApplicationCommand |
| `layouts[]` | 否 | Generic Web 支持的页面布局和区域编排 | 不能隐藏通用入口或必需状态 |
| `renderers[]` | 否 | 已知 Artifact/Observation 的安全 Renderer | 不加载任意远程脚本 |
| `terminology` | 否 | 领域术语到通用对象的展示映射 | 只改变显示，不改变对象身份和状态 |

领域体验不是 Solution 的运行依赖。Generic Web 只解释平台支持的声明式字段；定义缺失、无法解释或渲染失败时直接回退到通用布局，不改变 Project、Workflow、WorkUnit、Artifact、Decision 或 Run。

平台内置 `ApplicationHost` 负责解释上述声明值并复用 Generic Web 的组件、标准 Read Model 和公开 Platform API。它不是可安装组件，也没有 `SolutionApplication` identity、Package、SDK、`interface_api`、独立版本或生命周期。Solution 作者不能通过 `experience` 提交任意前端脚本、后端 API Facade、私有查询或状态管理代码。

Solution 自身的 `verification_fixtures[]` 必须覆盖 Generic Operability：通用表单能够表达全部必需 Project/Workflow 输入，通用页面能够显示节点、Gate 和未完成原因，全部用户动作能够映射到标准 ApplicationCommand/Review/Decision，Artifact 至少存在安全内置 Renderer 或元数据加受控下载的 fallback，并且每条可选业务分支都能按固定 DAG 规则收敛且不依赖私有状态或写接口。无法满足任一项的 Solution release 不得通过发布或安装验证。

Solution Studio 的“运行发布检查”只对当前草稿返回瞬时诊断。它不创建独立 Reviewer、ReviewRequest、ReviewResult、验证报告 Artifact 或发布审批状态。正式发布必须重新读取当前草稿并运行同一组必需检查，再计算不可变 `artifact_digest`；任何草稿修改都不能继承旧检查结论。

## 6. ComponentBundle 组合约束

完整行业方案通过 Extension 领域的通用 `ComponentBundle` 分发。Bundle 必须固定每个成员的 Package 类型、稳定身份、SemVer 和 artifact digest，并满足：

- 至少包含一个 SolutionPackage；
- SolutionPackage 的声明式领域体验可选且只能增强体验；定义缺失或无法渲染时仍必须通过 Generic Web/API/CLI 完整运行，且通用入口不能被禁用；
- `project_setup_schema`、`responsibility_slots[]` 和 `workflow_input_schema` 直接固定在 SolutionPackage；BaselinePlanTemplate、WorkUnit Contract 与 HarnessDefinition 默认作为 package-local 内容交付；动态 DAG 规划通过模板中的显式 DAG Orchestration WorkUnit、责任/能力要求、Contract、typed inputs 与 Harness 表达，DAG Orchestrator 推荐 AgentTemplate 只位于责任岗位且模板自行固定完整 Skills；最终业务完成通过显式终态节点/Gate 表达；SkillPackage、AgentTemplate 和其他可独立安装内容使用精确 Package release；
- Harness、Runtime、Authentication 和 Deployment 的第三方实现统一使用 ExtensionPackage，并通过 `interface_api` 选择对应 SDK；Workspace、Audit Export 与 Credential Storage 首版使用平台内部 Port 和内置实现；本地 AuditEvent/Audit Explorer、AuthorizationEngine 与 ExecutionAdmissionEngine 不属于 Package；
- Bundle 只负责原子分发，不拥有调用、权限、状态、依赖求解或 Workflow 生命周期。

任何 Required 成员失败时整体不激活；安装成功后不存在统一 `ComponentBundle.invoke` 接口。

Solution 作者只负责发布不可变 SolutionPackage release；Package 激活属于当前平台管理员权限。产品入口不要求用户分别操作 Verify、Install 和 Activate：管理员使用一次 `InstallAndActivatePackage`，同时拥有发布和激活权限的作者可以使用一次 `PublishAndActivateSolutionPackage`。前者内部完成 Package/接口检查、成员安装和 Required 成员原子激活；后者只是先发布再复用前者。失败时不得出现部分 Active 成员，已经发布的 release 仍保持原 SemVer/digest 且不进入 Solution Catalog 的可选版本集合。

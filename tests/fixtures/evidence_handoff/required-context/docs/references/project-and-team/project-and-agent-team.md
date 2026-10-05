# Project 与 Agent Team 契约参考

共同记法见 [Reference 共同约定](../conventions.md)，领域边界见 [Project 与 Agent Team 详细设计](../../design/detailed/02-project-and-agent-team-detailed-design.md)。

## 1. Project

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| project_id | 是 | Project 稳定身份 | 不等同 Repository、Namespace 或 Tenant |
| owner_scope_id | 是 | Project 所属 Scope 身份 | 首版也必须显式保存 |
| display.name | 是 | Project 用户可读名称 | 用户填写；不作为程序身份 |
| display.description | 是 | Project 用途与目标说明 | 用户填写；Project 列表、详情、审计和 Setup 页面持续展示 |
| solution_setup | 否 | 当前 Project 用于新建根 Workflow 的 Solution、长期配置和 Team 候选绑定 | 整体原子更新；缺失表示尚未配置完成，不增加状态枚举 |
| revision | 是 | Project 当前内部修订号 | 每次成功保存后递增；仅用于 CAS、防止覆盖和审计，不是用户可见业务版本 |
| configuration_digest | 是 | 当前 Project 可执行配置摘要 | 覆盖 `solution_setup` 的规范化内容，用于变更判断和 Workflow 快照来源证明 |
| archived_at | 否 | Project 归档时间 | 非空时禁止新建 Workflow、直接 Promotion 和配置修改；不是 Workflow 状态或通用资源状态枚举 |
| archived_by | archived_at 非空时是 | Project 归档操作人 | 保存精确 Principal 身份；恢复时与 `archived_at` 一起清除 |
| created_by | 是 | 创建 Principal | 不代表后续权限 |
| created_at | 是 | 创建时间 | 不参与隐式策略判断 |

`revision` 是 Project 内部 `uint64` 修订号，显式 `0` 合法；Workflow 创建快照的 JSON 边界要求 `project_revision` 成员存在且非 `null`，因此缺失不会折叠成合法零。根 Workflow 创建方必须使用可信存储提供的不可变创建时 Project、精确持久化 Project digest 和创建时解析出的 capability 配置调用 `Workflow.ValidateCreationSnapshots`，证明 Workflow 的 Project 身份、revision、`solution_setup` 摘要及执行投影与来源一致。该值校验器不认证调用方构造的 Project 或 digest；`configuration_digest` 证明完整采用配置，持久化 Project digest 证明精确来源记录，二者不能互换。Workflow 执行快照不包含 `external_targets[]`。

普通 Web/API 入口使用 `BeginProjectSetup`（开始 Project 配置）Application Use Case。该用例的输入生产责任如下：

| 英文字段 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- |
| display.name | Project 名称 | user | 必填；例如“贪吃蛇项目” |
| display.description | Project 说明 | user | 必填；说明业务目标、团队用途或长期边界 |
| owner_scope_id | Project 所属治理范围 | context / user | 单一 Scope 时由当前上下文继承；存在多个有权 Scope 时由用户选择 |
| solution | 本次进入 Setup 使用的精确 `ExactComponentRef<Solution>` | user / policy / system | 用户选择 Solution 卡片；页面与服务端解析精确 ID、SemVer 和 artifact digest，但在 Setup 完成前不写入 Project |

`project_id`、`revision`、`configuration_digest`、`created_by` 和 `created_at` 由 system 或 context 产生，不作为普通表单字段。当前允许使用哪些 Solution 和 Component，由每次命令执行时解析的有效 Scope/Project Policy 决定；Project 不固定一组可能过时的 Policy revision/digest。

`BeginProjectSetup` 的固定语义为：

1. 查询当前 Principal 与 Owner Scope 可用且 Active 的 SolutionPackage 发布版本，并校验 ComponentInstall、artifact digest 和 Package 检查结果；
2. 当只有一个合格精确 package release 时直接选中；当 Policy 指定一个推荐精确 release 时默认选中并明确展示；多个 release 且没有 Policy 推荐时必须由用户显式选择，不能根据最高 SemVer、发布时间或 `latest` 猜测；
3. 校验通过后创建基础 Project 与 AuditEvent，并返回 `project_id + solution + project_setup` 编辑上下文；
4. 只持久化 Project 基础字段，不持久化 `selected_solution`、半成品 `solution_setup` 或独立 Setup 草稿对象；
5. 用户离开未完成的 Setup 后，Project 仍存在且 `solution_setup` 为空。首版重新进入时重新选择并校验 Solution，不建设 Project 专属草稿或 Setup 状态机。

Project 列表可以把 `solution_setup` 为空派生显示为“未配置”，但该文案不是持久化状态枚举。无论页面是否保留当前路由状态，后续 Project Setup Query 与保存命令都必须携带并重新校验精确 `solution`，不能信任浏览器缓存。

`solution_setup` 是嵌入 Project 的原子值，不是独立聚合。它包含：

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| solution | 是 | `ExactComponentRef<Solution>`，包含 Solution ID、SemVer 和 artifact digest | 保存和创建 Workflow 时都必须满足当前有效 Policy |
| project_parameters | 否 | 按 Solution Project Setup Schema 规范化后的类型化参数 | Secret 只保存 CredentialBinding ID，不保存明文 |
| repositories[] | 否 | RepositoryBinding 列表 | 直接保存 URI、业务用途和条件性的 CredentialBinding |
| environment_bindings[] | 否 | Workflow 内使用的系统/Policy 环境绑定 | 首版主要承载平台托管 Preview/Observation；不表达完成后的直接 Promotion 目标，也不要求用户填写 Kubernetes 内部信息 |
| external_targets[] | 否 | 后续直接外部操作或 Promotion 目标 | 只在 Solution 要求或用户显式启用时存在；由未来 `StartPromotion` 等命令读取，不进入来源 Workflow 快照 |
| team_bindings[] | 是 | 责任标签到候选 AgentDefinition 的绑定 | Required 责任不得为空；不保存隐藏默认团队 |
| policy_overrides | 否 | 用户有权设置的企业策略收紧项 | 只能收紧当前平台/Scope Policy，不能扩大权限 |

`AdoptProjectSolutionSetup` 必须携带 `expected_revision`，在同一事务内重新校验完整 Candidate、原子替换整个 `solution_setup`、递增 `revision`、重算 `configuration_digest` 并写入 AuditEvent。它不是 `Draft/Ready/Active` 状态机。历史值通过 AuditEvent/change payload 追溯；恢复旧配置是把历史内容重新写成当前值并再次递增 `revision`，不是重新激活旧 Version 对象。更新只影响随后创建的根 Workflow；既有活动 Workflow 不被原地切换。已完成 Delivery 上的 `StartPromotion` 是新的直接应用操作，它按命令提交时的 `expected_project_revision` 读取当前外部目标并固定到 Deployment，不属于旧 Workflow 热切换。

## 2. Project Solution Setup 配置值

Project 的长期配置直接位于当前 `solution_setup` 中。用户点击“保存并采用”时更新当前 Project；只有根 Workflow 创建时，平台才把执行所需值复制到 `Workflow.spec` 的不可变创建时快照字段。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| project_id | 是 | 所属 Project 身份 | 来自命令上下文，不允许客户端改写 |
| expected_revision | 保存时是 | 客户端基于的 Project 修订号 | 不匹配时返回冲突并保留用户输入 |
| solution | 是 | 本配置面向的 `ExactComponentRef<Solution>` | 保存和 Workflow 创建时重新校验 |
| project_parameters | 否 | Solution Project Setup Schema 规范化值 | 直接可读；正式执行快照不依赖另一个 Artifact 身份 |
| repositories[] | 否 | `RepositoryBinding` 列表，直接保存 Repository URI、业务用途和条件性的单一 CredentialBinding | 不依赖 Repository Catalog；软件交付可指定一个可写 Primary Repository |
| environment_bindings[] | 否 | `EnvironmentBinding` 列表，包含 Workflow 内环境 ID 与 Preview/Observation 用途 | 由系统或 Policy 解析；平台托管 Preview 不需要用户 Credential，不承载完成后的直接 Promotion |
| external_targets[] | 否 | `ExternalTargetBinding` 列表，表达后续直接外部操作和 Promotion 目标 | 只在 Solution 要求或用户显式启用时存在；不复制到来源 Workflow 的执行快照 |
| team_bindings[] | 是 | Project 当前责任候选池 | 保存前校验 Agent 能力与独立性 |
| policy_overrides | 否 | Runtime、Workspace 或执行治理的收紧项 | 默认策略不要求用户填写；无法满足时 Fail Closed |

说明性 YAML：

```yaml
projectId: snake-game
# projectId：要保存配置的 Project 稳定身份。

expectedRevision: 12
# expectedRevision：页面读取配置时看到的内部修订号；服务端只用它做 CAS 冲突检测。

solutionSetup:
  # solutionSetup：一次原子保存的完整 Project 配置值。
  solution:
    # solution：本 Project 采用的精确行业 Solution 发布包。
    solutionId: software-delivery
    # solutionId：Solution 的稳定机器身份。
    version: 1.0.0
    # version：该可安装发布包的 SemVer。
    artifactDigest: sha256:...
    # artifactDigest：安装包内容摘要，防止同版本内容漂移。
  projectParameters:
    # projectParameters：按 Solution Schema 规范化后的直接可读参数。
    primaryLanguage: typescript
    # primaryLanguage：项目主要开发语言示例。
  repositories:
    # repositories：Project 可用 Repository 及其业务用途。
    - bindingKey: primary-repository
      # bindingKey：系统生成的 Repository 绑定稳定键；Project 用户不填写。
      repositoryUri: https://github.com/example/snake-game.git
      # repositoryUri：用户直接填写并经系统规范化的 Repository 地址。
      access: primary-write
      # access：该 Repository 在 Project 中承担的用途；primary-write 表示唯一主写仓库。
      credentialBindingId: github-app-snake-game
      # credentialBindingId：私有仓库访问使用的稳定 CredentialBinding 身份。
      providerType: github
      # providerType：系统根据 URI 和 Probe 识别的 Provider 类型，只读保存。
      defaultBranch: main
      # defaultBranch：系统探测得到的默认分支。
  environmentBindings:
    # environmentBindings：Project 可用环境及其用途。
    - environmentId: preview-cluster-default
      # environmentId：平台托管 Preview 环境稳定身份。
      usages: [preview]
      # usages：该环境允许承担的业务用途；平台托管 Preview 不要求用户部署凭证。
  teamBindings:
    # teamBindings：责任标签到候选 AgentDefinition 的当前绑定。
    - responsibilityId: architect
      # responsibilityId：Project 内稳定责任标签。
      candidateAgentDefinitionIds: [agentdef_architect_01]
      # candidateAgentDefinitionIds：允许承担该责任的不可变 AgentDefinition 身份。
```

Repository 或外部目标需要 Credential 时，Credential 必须放在对应 `RepositoryBinding` 或 `ExternalTargetBinding` 上，而不是在 Project 根部维护一袋通用凭证。`EnvironmentBinding` 是系统/Policy 给 Workflow 的环境事实，首版主要用于平台托管 Preview，不复用外部 Promotion 凭证。Repository 不建立独立 Catalog：Solution 的 `repository_binding` 字段类型允许用户直接输入 URI，在同一个 ProjectSetupCandidate 中创建带系统稳定键的类型化 Binding；FormField 值只引用该键。Platform Core 在保存配置时通过受控 Repository Probe 进行规范化、Provider 识别、默认分支读取和最小访问检查。

| 对象字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| binding_key | RepositoryBinding 必需 | Repository 绑定稳定键 | 系统生成、用户不填写；供 `project_setup_schema` 字段值和同一 `solution_setup` 内稳定引用，不是独立 Catalog ID |
| repository_uri | RepositoryBinding 必需 | Repository 地址 | 用户直接填写；保存时规范化，URI 变化更新 Project 并递增 `revision` |
| access | RepositoryBinding 必需 | 仓库业务用途 | Primary Repository 由页面固定为 `primary-write`；附加仓库通常为 `secondary-read`，只有 Solution 明确允许时才开放其他用途 |
| credential_binding_id | 私有 Repository 或需要认证时 | Repository 凭证绑定 | 用户只选择一个稳定绑定；CredentialBroker 根据 Clone/Fetch/Review/Push/PR/Release 的当前 subject、责任和用途签发短期最小权限 lease |
| provider_type | 系统必需 | Repository Provider 类型 | 根据 URI/Probe 得到，例如 `github`、`gitlab` 或 `generic-git`；不要求用户填写 |
| default_branch | Probe 成功时 | Repository 默认分支 | 系统读取并保存，用于明确 Planning 与 Git 基线；不能靠 `main/master` 猜测 |
| display | 系统必需 | Repository 展示信息 | 从 URI 和 Probe 结果生成名称、Owner/Path 等只读信息，不参与凭证解析 |
| environment_id | EnvironmentBinding 必需 | Workflow 环境稳定身份 | 首版标识平台托管 Preview 等系统环境；不作为直接 Promotion 目标身份 |
| usages[] | EnvironmentBinding 必需 | Workflow 环境允许用途 | 首版例如 `preview`、`observation`；不包含 `promotion` |
| binding_key | ExternalTargetBinding 必需 | 外部目标绑定稳定键 | 系统生成、用户不填写；同一 `solution_setup` 内稳定定位，服务端可投影为 `deployment_target_id` |
| display.name | ExternalTargetBinding 必需 | 外部目标名称 | 用户填写，用于 Release/Deployment 页面选择和审计 |
| target_type | ExternalTargetBinding 必需 | 外部目标类型 | 用户从平台支持的类型中选择，例如 `kubernetes` 或 `cicd`；不选择 Driver Package |
| environment | ExternalTargetBinding 必需 | 目标环境语义 | 用户选择或填写 `staging`、`production` 等业务环境，不是 Kubernetes Namespace |
| endpoint_uri | ExternalTargetBinding 必需 | Cluster、API 或 Pipeline 地址 | 用户填写并由 Target Probe 规范化；URI 不作为领域身份 |
| credential_binding_id | 目标需要认证时 | 外部目标凭证绑定 | 只允许当前目标和部署用途，不能用于模型、MCP 或 Repository |
| deployment_driver | ExternalTargetBinding 系统必需 | 精确 DeploymentDriver ExtensionPackage | 由目标类型、ExtensionCatalog 和当前 Policy 解析；页面只读展示名称、版本和来源，普通用户不填写 Package ID/digest |
| target_configuration_digest | ExternalTargetBinding 系统必需 | 目标规范化配置摘要 | 覆盖非敏感 Endpoint、environment、CredentialBinding 身份、DeploymentDriver 和安全约束；`StartPromotion` 提交时重新计算 |
| concurrency_key | ExternalTargetBinding 系统必需 | 目标并发串行化键 | 防止两个直接 Deployment 同时覆盖同一环境；由系统规范化生成或按 Policy 固定 |

单一 `credential_binding_id` 不等于单一宽权限 Token。推荐 GitHub Repository 使用 GitHub App：内部 CredentialStore 保存 App 私钥或安装关系，CredentialBroker 在每次操作时签发短期 Installation Token，并按操作范围收紧权限。若用户只能提供无法降权的静态 PAT，平台不得把原始 PAT 注入 Reviewer/Quality Workspace；应通过受控 Git Credential Helper/Proxy 执行允许的只读操作，或在能力不满足时 Fail Closed。Credential 使用、lease 身份、用途、subject 和结果都必须写入 AuditEvent。

Project Setup 页面只收集 Solution 顶层 `project_setup_schema` 中确实需要用户决定的值。Primary Repository 的类型化 Renderer 默认只要求 `repository_uri`，私有或受限 Repository 才显示一个 `credential_binding_id`；附加 Repository 按需添加。需要调用哪些在线检查由字段类型自动派生，作者不填写 Probe Capability 数组。Solution 固定的 BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务路径和 Harness 装配不进入表单；平台托管 Preview、默认 Runtime/Workspace/Governance Policy 只读展示来源与约束。Project Rules 内容也不在 Setup 中预填，而是在 Workflow 的批准基线阶段根据已批准 Requirement、Design 与 Acceptance Plan 生成、Review 和用户确认。只有外部 Promotion、固定 Harness 所需外部系统参数，或有权用户提交不能扩大平台/Scope Policy 的执行策略收紧项时，页面才追加相应控件。外部目标的 DeploymentDriver 是系统解析结果，不是用户填写的 Harness Extension Binding；该目标只在 Release Promotable 后的直接 Promotion 页面选择，不进入 New Delivery 表单或来源 Workflow 快照。替换 package 内 Harness 装配必须发布新的 SolutionPackage release；引用独立 Harness ContentPackage 时则固定新的精确 ContentPackage release。

Project `solution_setup` 不保存 `harness_extension_bindings[]`。固定 ExtensionPackage、顺序、Required/config digest 属于 SolutionPackage 内的 HarnessDefinition，或属于被 Solution 精确引用的独立 Harness ContentPackage；Workflow 创建时由 Resolver 复制到不可变 LockedComponentSet。Project 的类型化参数直接保存在规范化 `project_parameters` 中，Credential 仍挂在 Repository、Environment 或其他明确用途对象上。这样 Project 配置与 Harness 装配不会形成两份可漂移来源。

## 3. AgentTemplate

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| template_id | 是 | 模板 package 稳定身份 | 与 SemVer、artifact digest 共同定位精确发布包 |
| version | 是 | 模板 package 的 SemVer | 更新发布新的 package release，不修改既有 AgentDefinition |
| display_name / description | 是 | Catalog 展示内容 | 不作为程序身份 |
| instructions | 是 | 模板自带的稳定 Instructions 值 | 直接保存内容、语言和摘要；物理大对象卸载是存储 Adapter 细节 |
| skills[] | 否 | 模板内置的 `ExactComponentRef<SkillPackage>` 列表 | 某些模板可以为空；一旦声明，完整精确列表就是模板内容基线，普通实例化必须全部继承 |
| capability_labels[] | 是 | 适合承担的逻辑能力 | 不等同 Runtime Capability |
| required_runtime_capabilities[] | 是 | 物化所需 Runtime 能力 | Project 配置时必须可满足 |
| content_digest | 是 | 模板运行内容摘要 | 与 Package artifact digest 分工明确 |

内置 AgentTemplate 的 Skills 不是“推荐用户重新选择”的目录提示，而是模板自身携带的精确 package 内容。Embedded ComponentBundle 必须先按普通 Content 治理路径 Verify、Install、Activate 所有被引用 SkillPackage release，再发布引用这些精确 SemVer 和 digest 的 AgentTemplate。模板 Skill 列表变化必须发布新的 AgentTemplate package release；不能在同一 SemVer release 下替换 Skill 内容。

普通 `InstantiateAgentTemplate`（实例化 Agent 模板）Use Case 的 Skill 规则固定为：

```text
AgentTemplate.skills[]
→ 校验每个 ExactComponentRef<SkillPackage> 已安装、Active 且 digest 一致
→ 完整复制到 AgentDefinition.skills[]
→ 合并用户显式追加的 additional_skills[]
→ 拒绝同一 Skill 稳定身份对应多个版本
→ 发布不可变 AgentDefinition
```

普通实例化不接受“移除模板 Skill”的输入。用户确需删除时必须进入显式高级自定义流程；最终仍生成普通 AgentDefinition，并保留来源 AgentTemplate 的精确 package release 和 digest 供审计。模板一致性由读模型根据来源模板和最终 Skills 派生，不新增 Agent 生命周期状态。任何模板 Skill 缺失、不可用或 digest 不符时，本次发布失败且不产生部分 AgentDefinition。

`instructions` 是一个直接可读的不可变值：

| 字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| language | 是 | 指令语言 | 例如 `zh-CN`、`en-US`，用于编辑和展示，不用于推断模型能力 |
| content | 是 | 稳定指令正文 | 保存 Agent 跨任务长期遵循的行为边界；用户不需要先创建 Artifact ID |
| digest | 是 | 指令内容摘要 | 用于内容校验、审计和可重放；正文变化后必须发布新模板 release 或新 AgentDefinition |

模板 Package 可以使用 `instructions_path` 指向包内文件，但它只属于 Package 创作格式；安装或实例化后必须解析为上面的 `instructions` 值，Runtime SDK 和 AgentRun 都不保存该路径。只有当一组 Instructions 确实需要被多个对象独立发布和升级时，才额外建模为 Content Package；这不是创建 Agent 的默认前置步骤。

## 4. AgentDefinition

`Agent` 是长期逻辑身份；`AgentDefinition` 是一次正式发布、提交后不可变的 Agent 定义。编辑器草稿或自动保存不创建新定义，只有用户执行“发布”才创建新的 `agent_definition_id`。它不需要 SemVer；前后关系由 `previous_agent_definition_id` 和 `content_digest` 表达。

逻辑 Agent 的 Catalog identity 使用普通稳定身份和 CAS revision 管理可读信息与归档，不把每次编辑或归档创建为 Version：

| 字段 | 必需 | 中文字段释义 | 关键约束 |
| --- | --- | --- | --- |
| agent_id | 是 | 逻辑 Agent 稳定身份 | 关联该 Agent 的多份不可变 AgentDefinition；不能用某个 Definition ID 代替 |
| owner_scope_id | 是 | Agent 所属治理范围 | 决定目录可见性和管理权限 |
| display.name / display.description | 是 | Agent 名称与说明 | 直接解释长期用途，不作为执行能力来源 |
| revision | 是 | Agent Catalog 当前修订号 | 用于归档、恢复、删除和可读信息更新的 CAS；不是用户版本 |
| archived_at | 否 | Agent 归档时间 | 非空时默认从新选择和新发布入口隐藏；不改变既有 AgentDefinition 或 AgentRun |
| archived_by | archived_at 非空时是 | Agent 归档操作人 | 恢复时与 `archived_at` 一起清除 |
| created_by / created_at | 是 | 创建来源 | AuditEvent 仍是变更历史权威 |

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| agent_definition_id | 是 | 不可变 AgentDefinition 身份 | AgentRun 固定精确 ID |
| agent_id | 是 | 逻辑 Agent 稳定身份 | 一个 Agent 可以有多次已发布定义 |
| previous_agent_definition_id | 否 | 同一 Agent 的上一份已发布定义 | 用于历史导航，不表示可变对象版本号 |
| source_agent_template | 否 | 创建来源 AgentTemplate 的 `ExactComponentRef` | 从零创建时为空；运行时不重新读取模板 |
| instructions | 是 | 最终稳定 Instructions 值 | 直接保存 `language`、`content`、`digest`；Platform Core 固定到 AgentRun 的 RuntimeInstructionSet，AgentRun Job 交给 RuntimeDriver 原生物化，不写入 Repository 指令文件 |
| skills[] | 否 | 最终通用 `ExactComponentRef<SkillPackage>` 列表 | 从模板创建时默认完整复制模板 Skills，再合并用户追加项；不包含 WorkUnit 阶段 Skill，每项固定精确 SemVer 和内容摘要 |
| capability_labels[] | 是 | 可承担的逻辑业务能力 | 用于候选池匹配 |
| model_policy | 是 | `ModelSelectionPolicy`，定义允许模型及选择策略 | 只能从平台 Model Catalog 选择；不保存 API Key，也不记录实际 Usage |
| runtime_bindings[] | 是 | 嵌入定义的不可变 RuntimeBinding 值 | 实际 Run 固定其中一个 binding 及其 digest，不建立独立版本聚合 |
| mcp_server_definition_ids[] | 否 | Agent 可连接的 MCP Server 稳定配置身份 | 选择 MCP Server，不逐个选择其 Tool；Workflow/Run 记录实际解析到的 revision 和 digest |
| resource_requirements | 是 | CPU、内存、GPU、架构和调度需求 | 必须与 Runtime 和集群兼容 |
| content_digest | 是 | 完整 AgentDefinition 内容摘要 | 防止同一 ID 下内容漂移 |
| created_by / created_at | 是 | 创建来源 | 内容不按时间隐式变化 |

`capability_labels[]` 只描述跨任务稳定能力，不描述当前生命周期情形。首次执行、Review 打回、返工、补救和影响分析由 WorkUnit Guide、精确输入、反馈与 output contract 表达，不应各自制造 Capability Label。例如内置 Architect 只需要 `architecture-design`：它同时覆盖首次设计、设计返工和最终验收后的针对性技术变更设计；平台不得要求同一 Agent 再声明 `rework`、`change-impact` 或同义标签。

`ModelSelectionPolicy` 最小字段如下：

| 字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| allowed_models[] | 是 | 允许模型列表 | 每项以稳定 `model_provider_connection_id + external_model_id` 表达；可附带选择时目录快照作为来源证明 |
| selection_strategy | 是 | 模型选择策略 | 首版只支持 `fixed`（固定单模型）与 `ordered-fallback`（按明确顺序回退） |

`allowed_models[]` 中的每个元素是一个结构化模型选择值，不使用 `provider.model`、`provider/model` 或其他拼接字符串作为持久化身份：

| 英文字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| model_provider_connection_id | 是 | 模型 Provider 连接稳定身份 | Runtime 执行前解析并记录实际 `revision + configuration_digest` |
| external_model_id | 是 | Provider 实际模型名称 | RuntimeDriver 调用模型接口时使用的准确值 |
| selected_from_model_catalog_snapshot_id | 否 | 选择时模型目录快照身份 | 只证明用户当时看到了什么，不参与模型身份解析 |

Agent Center 可以把 Provider 名称和 Model 名称组合成用户可读 Label，但该 Label 只属于 ViewModel。Provider 只作为模型选择器的分组、筛选和说明信息，不是用户必须先完成的独立选择步骤。

AgentDefinition 不保存通用工具绑定字段或一袋无类型 Credential 身份。Codex 等 Runtime 自带工具由 Runtime Capability、当前 Harness 允许动作、Project Policy 和 ExecutionSecurityRequirements 共同约束，首版不在 Agent Center 逐工具勾选。Credential 必须出现在有明确用途的配置中，例如 ModelProviderConnection、McpServerDefinition、RepositoryBinding 或 Deployment Target；不能先宽泛授权给 Agent 再由运行时猜用途。

## 5. Project Team Bindings 与 Workflow TeamBindingSnapshot

Project Team Bindings 是 `Project.solution_setup` 内的当前可编辑值，不是独立聚合。根 Workflow 创建时，Platform Core 把当时的候选 AgentDefinition 精确 ID 和约束复制成不可变 `TeamBindingSnapshot`；它是该 Workflow 的初始自动候选来源，活动 Workflow 不跟随 Project 后续修改。运行期间为既有责任补充一个合格 Agent 时，通过精确绑定 MissingExecutor Observation 的 Workflow-local Decision 承接，不改写 Snapshot。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| responsibility_bindings[] | 是 | Project 责任标签到 Agent 候选集合 | 候选集合无序；不要求 Solution 顶层预枚举全部责任 |
| selection_constraints | 否 | 当前 Project 的附加自动选择收紧项 | 自动选择不能越过初始候选池；Workflow-local Decision 仍必须满足这些约束且不能扩大有效 Policy |

每个 `responsibility_binding` 至少包含：

| 英文字段 | 必需 | 中文字段释义 | 值的来源与约束 |
| --- | --- | --- | --- |
| `responsibility_id` | 是 | Project 内稳定责任标签 | 来自 Solution 岗位或 PlanDraft WorkUnit，只读展示；Project 内唯一 |
| `candidate_agent_definition_ids[]` | 是 | 候选 AgentDefinition 精确身份列表 | Project 用户唯一需要为岗位选择的核心值；一个候选表示精确绑定，多个候选表示无序授权池，Required 岗位不得为空 |
| `required_capabilities[]` | 否 | 当前责任最低能力 | 来自 Solution/WorkUnit 要求和平台校验，不要求普通用户重复填写；所有候选必须满足 |

Agent 岗位页面不重复配置 Agent 的 Instructions、Skills、模型、MCP、Runtime 或资源。这些内容已经固定在所选 AgentDefinition 中；页面只读显示能力与兼容性摘要，并在不满足时链接回 Agent Center 发布新定义。岗位稳定键、名称、说明、Required/Recommended、基础能力要求和推荐模板来自 Solution 顶层 `responsibility_slots[]`，采用时规范化为 Team Binding；岗位间独立性不属于 Solution Slot，而由具体 WorkUnit Contract、Harness 与 Admission 在每次 Run 上强制，因此不会形成 Team Bindings 的第二份可编辑元数据。

内置 Software Delivery Solution 的 Required 岗位不包含 `acceptor`。可选 Acceptance Executor AgentTemplate 只提供兼容 AgentDefinition 的创建入口；显式 Acceptance WorkUnit 仍只声明责任/能力要求，并通过普通执行器匹配选择实际 AgentDefinition。默认程序化 E2E 验收不要求用户预先绑定该岗位。

### 5.1 Workflow-local Executor Binding

Plan Compiler 不消费 HostCapabilitiesSnapshot，因此不会因为“当前没有 Agent”而拒绝一个责任、能力和合同均合法的动态 Plan。节点到达派发点后，Kernel Executor Matcher 若找不到候选，则让 WorkUnit 进入 `Waiting`；OAC 把以下事实保存为类型化 `MissingExecutor` Observation：

| 英文字段 | 必需 | 中文字段释义 | 值的来源与约束 |
| --- | --- | --- | --- |
| `work_unit_id` | 是 | 等待执行器的 WorkUnit 身份 | 来自当前 Workflow status |
| `responsibility_requirement` | 是 | 责任要求 | 来自不可变 WorkUnit definition，不能在处理页修改 |
| `capability_requirements[]` | 是 | 最低能力要求 | 来自 ExecutionBinding，候选必须全部满足 |
| `accepted_contracts[]` | 是 | 兼容合同要求 | 来自 WorkUnit Contract 和执行合同 |
| `executor_requirement_digest` | 是 | 规范化执行要求摘要 | 覆盖责任、能力、合同和独立性，用于判断后续 WorkUnit 是否可复用同一绑定 |
| `match_diagnostics[]` | 是 | 匹配失败诊断 | 说明无候选、能力不足、合同冲突、独立性冲突、不可用或策略拒绝 |

用户提交普通 `Answer` Decision，`subject` 绑定该 Observation 的精确 ID/digest，`structured_result.agent_definition_id`（所选 AgentDefinition 身份）必填，`reason` 可选。Platform Core 在写入前重新执行 Agent 可见范围、Capability、Contract、独立性、Authorization、Admission、Runtime 和 Policy 校验。通过后，WorkflowOrchestrationHost 将初始 TeamBindingSnapshot 与这些不可变 Decision 合并成当前 HostCapabilitiesSnapshot；绑定只适用于同一 Workflow、同一 `executor_requirement_digest` 和尚未开始的 WorkUnit。

该流程不创建 TeamAmendment、BindingVersion 或第二套状态机，不修改 Project、Workflow.spec、TeamBindingSnapshot 和既有 Run。若用户希望未来 Workflow 默认采用该 Agent，应另行更新 Project Team Bindings；若只是解决当前执行容量，则不需要影响分析或后继 Workflow。

## 6. Project Setup 预检查契约

`EvaluateProjectSolutionSetup`（评估 Project 配置）接受一个尚未保存的内联 `ProjectSetupCandidate`（Project 配置候选），返回一个瞬时 `ProjectSetupEvaluation`（Project 配置评估结果）。二者都是 API/Application DTO，不是聚合、Artifact、数据库表或 Project 状态。

### 6.1 ProjectSetupCandidate

| 英文字段 | 必需 | 中文字段释义 | 值的来源与作用 |
| --- | --- | --- | --- |
| `project_id` | 是 | 当前 Project 身份 | 页面路由与已授权上下文提供；Project 必须存在且允许配置 |
| `solution` | 是 | 当前评估的精确行业 Solution | 页面显式选择的 `ExactComponentRef<Solution>`；包含 ID、SemVer 和 artifact digest，不允许 `latest` |
| `project_parameters` | 否 | Project 类型化业务参数 | 用户按 Solution 顶层 `project_setup_schema` 填写；Platform Core 通过 Generic Form Field Type Catalog 在计算摘要前完成校验和规范化 |
| `repositories[]` | 否 | Repository 配置候选 | 包含直接 URI、业务用途和条件性的单一 CredentialBinding；Probe 结果不由客户端伪造 |
| `external_targets[]` | 否 | 外部系统或 Promotion 目标候选 | 只包含 Solution 明确要求或用户显式选择的目标；平台托管 Preview 不要求用户重复提交 |
| `responsibility_bindings[]` | 是 | Agent 岗位候选绑定 | 每项包含责任岗位和一个或多个候选 AgentDefinition；Solution 没有岗位时数组可为空，Required 岗位不得缺失或为空 |
| `policy_overrides` | 否 | 企业执行策略收紧项 | 只在用户有权且默认 Runtime/Workspace 策略不满足时提交；不能扩大平台或 Scope Policy |

客户端必须把一次评估所需的全部候选值内联提交，不能先创建专属 Draft 或其他中间聚合再传 ID。Credential 只传 `credential_binding_id`，不得传 Secret。

### 6.2 ProjectSetupEvaluation

| 英文字段 | 必需 | 中文字段释义 | 值的来源与作用 |
| --- | --- | --- | --- |
| `project_id` | 是 | 当前 Project 身份 | 回显本次评估对象 |
| `solution` | 是 | 已实际评估的精确 Solution | 服务端重新解析并校验后的 `ExactComponentRef<Solution>` |
| `candidate_digest` | 是 | 规范化候选内容摘要 | 由服务端基于完整规范化 Candidate、精确 Solution/Schema 和有效 Policy revision/digest 计算；Web 把它与发起该请求的本地表单 revision 关联，不复制服务端哈希算法 |
| `readiness` | 是 | 配置就绪结论 | 仅允许 `ready` 或 `blocked`；瞬时查询结果，不是 Project 状态 |
| `checks[]` | 是 | 五组检查摘要 | 固定分为 Solution/Package、Repository/External、Agent Slots、Runtime/Workspace/Environment、Authorization/Governance；每组返回状态和中文摘要 |
| `blockers[]` | 否 | 阻止保存和采用的问题 | Required 缺失、失败、Unknown 或超时进入此列表 |
| `warnings[]` | 否 | 不阻止继续但需要用户知晓的问题 | 与 blockers 使用同一结构化 Finding 合同 |
| `resolved_packages[]` | 否 | 本次精确 Package 解析摘要 | 只在响应内说明 Package 类型、ID、版本、digest、安装和检查结果；不是 LockedComponentSet 或持久化 Preview |
| `evaluated_at` | 是 | 本次评估时间 | 只用于展示；不能作为缓存长期有效的依据 |

每个 `checks[]` 元素使用同一个最小结构：

| 英文字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| `group` | 是 | 检查分组 | 只允许 `solution_package`（Solution 与 Package）、`repository_external`（Repository 与外部系统）、`agent_slots`（Agent 岗位）、`runtime_workspace_environment`（Runtime、Workspace 与环境）、`authorization_governance`（授权与治理） |
| `status` | 是 | 分组结论 | 只允许 `passed`（通过）、`warning`（有提示但不阻塞）、`blocked`（存在阻塞项）、`not_applicable`（当前 Solution 不适用） |
| `summary` | 是 | 中文摘要 | 说明本组检查了什么以及当前结论；不得泄露 Secret 或 Provider 原始错误 |

`resolved_packages[]` 的每个元素至少返回 `package_type`（Package 类型）、`package_id`（Package 稳定身份）、`version`（精确 package SemVer）、`artifact_digest`（制品摘要）、`install_status`（安装状态）和 `verification_status`（Package/接口检查状态）。这些值只用于解释本次解析结果，不形成可被后续命令引用的 Preview 身份。

每个 `blockers[]` 或 `warnings[]` Finding 至少包含：

| 英文字段 | 必需 | 中文字段释义 | 值的作用 |
| --- | --- | --- | --- |
| `code` | 是 | 稳定问题代码 | 用于测试、国际化和定位处理器；不能把整段文案当身份 |
| `group` | 是 | 所属检查分组 | 决定页面归入五个固定区域中的哪一组 |
| `subject` | 是 | 受影响对象 | 指向具体 Repository、岗位、组件、Runtime、Workspace、环境或授权对象 |
| `message` | 是 | 中文问题说明 | 明确说明当前值为什么不能通过或需要注意 |
| `remediation` | 是 | 修复建议 | 告诉用户需要修改什么或补充哪项能力 |
| `fix_action` | 否 | 直达修复动作 | 只允许受控 `focus_field`（定位字段）、`open_route`（打开平台固定路由）或 `rerun_check`（重新检查）动作；不得携带任意 URL、脚本或可执行代码 |

`subject`（受影响对象）使用最小可定位结构：`kind`（对象类别）、`candidate_path`（候选中的字段路径）、可选 `resource_id`（已有平台资源身份）和 `display`（用户可读名称）。未保存 Repository 或外部目标没有资源 ID 时，只使用 `candidate_path`，不得为了报错提前创建对象。

稳定 `code` 至少按以下命名空间组织，具体后缀由对应领域拥有：

| Code 前缀 | 中文含义 | 示例用途 |
| --- | --- | --- |
| `solution.*` | Solution 问题 | 未 Active、版本或 digest 不一致 |
| `package.*` / `extension.*` | Package 或扩展问题 | Required Package 缺失、Package 检查失败或所需 SDK 接口检查失败 |
| `repository.*` | Repository 问题 | URI 无效、不可访问、默认分支未知 |
| `external_target.*` | 外部目标问题 | 必需目标 Unknown、超时或不可用 |
| `agent_slot.*` | Agent 岗位问题 | Required 岗位为空、候选能力不满足或独立性不满足 |
| `runtime.*` / `workspace.*` | Runtime 或 Workspace 问题 | API Type、隔离、网络、存储或资源能力不足 |
| `authorization.*` / `credential.*` | 授权或凭证问题 | 当前用途不允许、Binding 已禁用/过期或无法签发受限凭证 |

### 6.3 固定语义

1. Platform Core 先执行 Authorization，加载 Project、Solution 顶层 `project_setup_schema`、Generic Form Field Type Catalog 和当前 Policy，对 Candidate 归一化后计算 `candidate_digest`；
2. 字段类型自动决定是否调用 Repository Probe、Credential readiness/Policy、Environment/Target Probe 或 Artifact 校验；这些检查可与组件解析、Agent Capability、Runtime/Workspace Capability 和治理检查并行执行。某项缺少前置值时不启动无意义 Probe，并由对应检查组返回 blocker 或 `not_applicable`；
3. 必需外部检查返回 Unknown、超时或无法确认 Credential 可用性时，必须生成 blocker，不能降级为 warning；
4. 评估本身不持久化 Candidate、Evaluation 或 Component Resolution Preview，不创建 LockedComponentSet、Workflow、ComponentRun、AgentRun、Workspace 或 Job；强制 Authorization 与 Credential 使用审计仍按 Governance 合同记录；
5. Candidate 任意相关输入变化后，Web 必须将旧结果标记为 stale，并以新 Candidate 重新评估；
6. 保存/采用和创建 Workflow 必须重新执行同等或更严格的权威校验，不能把 `candidate_digest + ready` 当成授权令牌；
7. 已采用 Project 的就绪展示从当前 `Project.solution_setup` 重建 Candidate，再调用同一用例，不增加第二套 Read Model 或状态机。
8. Solution 作者不声明 Probe Capability 数组；字段类型需要但平台没有受治理窄端口时，SolutionPackage 发布失败，不能把任意脚本、Webhook、Harness Extension 或领域 Renderer 当作替代检查器。

### 6.4 配置更新与后继根 Workflow

Project 当前配置、Team bindings、Model Provider/MCP 配置和 Policy 绑定都按各自稳定身份、CAS `revision` 与 AuditEvent 更新；新的 AgentDefinition、SolutionPackage、ContentPackage 或 ExtensionPackage 则按各自不可变记录或 package release 规则发布。内置 Workspace 作为系统发行能力随 OAC 升级，不作为 Project 可选 Package。上述变化都不会静默改变已经创建的 Workflow。活动 Workflow 只有在用户针对精确 MissingExecutor Observation 作出 Agent 绑定 Decision 后，才会显式采用该精确 AgentDefinition。

当用户只希望后续任务采用变更时，到此结束。仅补充一个满足既有 WorkUnit 要求的执行者时，使用上一节的 Workflow-local Decision，不属于新基线。若一个既有业务目标必须采用新的 Project 配置、Solution、Harness/Policy 或 Component 基线，则使用以下固定流程：

1. Platform Core 比较前序 Workflow 的创建时执行快照与当前 Project/组件解析结果，形成带来源 ID/digest 的影响分析 Artifact；
2. 用户或 Policy 对该 Artifact 提交正式 Decision；存在未确认的外部副作用、撤销组件、能力缺口或不兼容输入时必须阻塞；
3. 通过普通根 Workflow 创建用例创建新的后继 Workflow，重新解析并固定 Project 配置、TeamBindingSnapshot、LockedComponentSet 和 OrchestrationDefinition；
4. 前序 Workflow 的 `spec/status`、Active Plan、WorkUnit 状态、GateResult 和审批结果保持不变；用户可以显式选择其中的不可变 Artifact 和 Decision 作为新 Workflow 输入，但新 Workflow 必须按自己的 PlanningRules 与 Gate 重新判断是否满足条件。

该流程不创建 Project 配置 Version、Workflow Migration 聚合、迁移状态机或可变“基线对象”。后继关系只是新 Workflow 上的来源字段和 AuditEvent 因果信息。

## 7. AgentRun Lineage

Lineage 只表示同一 WorkUnit、责任和任务范围的连续时间线。AgentDefinition、解析后的 RuntimeBinding、Session 和 Workspace 属于每个不可变 AgentRun；显式 Agent 交接可以通过同一 Lineage 的 `previous_run_id` 和结构化 Artifact、Observation、Decision 表达，不继承旧 Agent 的隐藏 Session。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| lineage_id | 是 | 同一 WorkUnit 和责任的连续时间线 | 不跨责任共享 |
| previous_run_id | 否 | 紧邻上一轮 AgentRun 身份 | 只能属于同一 Lineage |
| runtime_session_id | 否 | 可恢复 Session 身份 | 丢失时使用结构化历史重建 |
| workspace_id | 是 | 当前执行 Workspace 身份 | Reviewer 必须使用干净 Workspace |
| subject | 是 | 当前执行或评审对象的 `SubjectKey` | Gate 必须关联同一精确主体 ID/digest |
| responsibility_id | 是 | 当前 AgentRun 使用的责任标签 | 来自 WorkUnit responsibility requirement；AgentDefinition 来源可以是初始 TeamBindingSnapshot 或有效 Workflow-local Decision |

Session 恢复要求同一 AgentDefinition、兼容的已解析 RuntimeBinding 和相同安全上下文。跨 AgentDefinition 或不兼容 RuntimeBinding 的交接默认创建新 Session，但不因执行实现变化自动切断任务 Lineage。

## 8. 归档、恢复与删除资格

归档字段属于具体资源；以下三个值只存在于 Query DTO，不持久化为通用资源聚合：

| 英文字段 | 中文字段释义 | 值的作用 |
| --- | --- | --- |
| `allowed_actions[]` | 当前允许操作集合 | 服务端根据资源类型、当前事实、引用、Authorization、Retention 和 Legal Hold 返回 `archive\|restore\|delete` 的实际可用子集 |
| `reference_summary` | 引用摘要 | 按 Project binding、Workflow snapshot、Run、Deployment、Artifact 等类型汇总仍受保护的引用，帮助用户理解为什么不能删除 |
| `deletion_blockers[]` | 删除阻塞原因 | 每项包含稳定 code、中文说明、精确 subject 和修复建议；客户端不得自行推断删除资格 |

Project 的类型化规则：

- `ArchiveProject` 只在没有非终态 Workflow 和活动直接 Deployment 时成功；归档后不允许新建 Workflow、直接 Promotion 或修改 Setup；
- `RestoreProject` 清除归档字段并递增 revision，但新建 Workflow 前必须重新执行 Project Setup readiness 检查；
- `DeleteProject` 只允许删除从未形成 Workflow、Deployment、保留 Project Artifact 或其他不可变业务引用的空 Project。已使用 Project 只能归档，不能解除 Workflow 中固定的 Project identity/revision/configuration snapshot。

逻辑 Agent 的类型化规则：

- `ArchiveAgent` 只影响 Catalog 新选择和新定义发布；已经固定 AgentDefinition 的活动 Workflow 与 AgentRun 继续运行；
- `RestoreAgent` 清除归档字段，但目录可用性仍由当前 Definition、Runtime、Provider、MCP、Capability 和 Policy 检查决定；
- `DeleteAgent` 只在没有 Project team binding、Workflow TeamBindingSnapshot、AgentRun 或其他保留业务引用时，原子删除逻辑 Agent identity 与其未被引用的 AgentDefinition。

以上删除均立即且不可逆，不进入 `deletion_scheduled`、Recently Deleted 或后台用户删除队列。资源模块在事务内重新检查引用；AuditEvent 及其最小 subject tombstone 按 Governance 保留，并不被计为阻止本次空资源删除的业务引用。

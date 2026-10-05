# Package 与 Extension 治理契约参考

本文定义 Solution、Content 和 Extension 三种 Package 的共同 Envelope、安装生命周期、精确版本固定、Workflow 锁定集合、调用记录、权限求交和接口检查。公开扩展接口由对应 SDK 定义，不再为每个接口复制一套专属类别、Registry、Host 和检查模型。

共同记法见 [Reference 共同约定](../conventions.md)，架构边界见 [Package 与 Extension 详细设计](../../design/detailed/08-component-and-extension-detailed-design.md)。SolutionPackage 见 [Solution Definition 契约参考](../solution/solution-definition.md)，Harness Extension 见 [Harness Extension SDK 契约参考](plugin-contracts.md)。

## 1. Package 类型与 Extension API

| Package 类型 | 内容 | Catalog / 调用方 | 明确不负责 |
| --- | --- | --- | --- |
| `SolutionPackage` | BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务节点/Gate、配置元数据和声明式领域体验 | Solution Catalog / Resolver | 运行状态、执行副作用、新生命周期和第二套完成判断 |
| `ContentPackage` | SkillPackage、AgentTemplate 等不可执行内容 | Content Catalog | Core Service 和副作用 |
| `ExtensionPackage` | 一个公开 Extension API 的实现 | ExtensionCatalog；运行时由该 SDK 的类型化调用方使用 | 未声明接口、万能调用和平台状态直写 |

首版公开 Extension API：

| `interface_api` | 中文含义 | 调用方 | 实现角色 |
| --- | --- | --- | --- |
| `harness.oac.dev/v1` | Harness 扩展接口 | Harness Host | Harness Extension |
| `runtime.oac.dev/v1` | Agent Runtime 接入接口 | AgentRun Job | RuntimeDriver |
| `authentication.oac.dev/v1` | 认证协议接入接口 | Authentication Host | AuthenticationDriver |
| `deployment.oac.dev/v1` | 外部部署接入接口 | Deployment Host | DeploymentDriver |

Workspace、Audit Export 与 Credential Storage 首版只保留平台内部 Port 和内置实现，不进入 ExtensionCatalog。AuthorizationEngine 与 ExecutionAdmissionEngine 是 OAC 内置唯一实现，永远不是 Package 或 Extension API。

一个 ExtensionPackage 只实现一个 `interface_api`。一个接口内部可以通过 `Describe()` 暴露多个能力，例如 Harness Extension 同时提供 Guide、Sensor 和 Executor；Package 不得跨接口族聚合 Runtime、Credential 和 Harness 权限。

## 2. Package Envelope

共同 Package Envelope 只描述身份、制品和来源。生效信任与生命周期策略属于验证后的 ComponentInstall，Package 不能自行授予。

```yaml
apiVersion: packages.oac.dev/v1alpha1
# apiVersion：共同 Package Envelope 的 Schema 版本。
kind: Package
# kind：固定资源类型；具体语义由 spec.packageType 决定。
metadata:
  # metadata：Package 的稳定身份和精确版本。
  id: oac/software-delivery
  # id：同一 Package 类型内的稳定身份。
  version: 1.0.0
  # version：精确语义版本；不接受 latest 或版本范围。
spec:
  # spec：Package 类型、内容入口和来源声明。
  packageType: solution
  # packageType：包类型；只允许 solution、content 或 extension。
  descriptor:
    # descriptor：Solution/Content 在 Package 内的描述文件；ExtensionPackage 改用 interface 字段。
    apiVersion: solutions.oac.dev/v1alpha1
    # apiVersion：描述文件的 Schema 版本。
    path: solution.yaml
    # path：Descriptor 在 Package 内的相对路径；禁止路径逃逸。
  origin:
    # origin：可审计分发来源；来源本身不授予信任或权限。
    type: embedded
    # type：embedded 表示由内置 ComponentBundle 分发，但仍走统一安装链路。
    bundleId: oac-default-components
    # bundleId：来源 ComponentBundle 的稳定身份。
    bundleVersion: 1.0.0
    # bundleVersion：来源 Bundle 的精确版本。
```

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| apiVersion | 是 | Envelope Schema 版本 | 不支持主版本时拒绝 Verify |
| kind | 是 | 固定为 Package | 不代替 Package 类型校验 |
| metadata.id | 是 | Package 稳定身份 | 同 Package 类型内唯一 |
| metadata.version | 是 | 精确语义版本 | 不允许范围或 latest |
| spec.packageType | 是 | Package 类型 | 只允许 solution、content、extension |
| spec.descriptor.apiVersion | Solution/Content 必需 | 描述文件 Schema 版本 | 由对应静态校验器验证 |
| spec.descriptor.path | Solution/Content 必需 | Package 内 Descriptor 路径 | 禁止路径逃逸 |
| spec.interface.apiVersion | Extension 必需 | 实现的 Extension API 及版本 | 必须是平台公开且支持的 SDK 版本 |
| spec.interface.entrypoint | Extension 必需 | 扩展启动入口 | 必须位于 Package 内并通过安全检查 |
| spec.origin | 是 | embedded、local、oci、helm 等来源 | 来源不授予权限 |

`artifact_digest`、Descriptor digest 和 Provenance 由安装工具计算并记录，不要求 Package 作者手工填写。Package 内容不携带可自行生效的 trust tier 或 lifecycle policy；Platform Core 根据管理员授权、组织策略和验证结果生成 `ComponentInstall.trust_tier` 与 `ComponentInstall.lifecycle_policy`。

内置 Package 通过 Embedded ComponentBundle 交付，但仍走相同 Verify、Install、Resolve 和接口检查路径。实现中禁止 `if builtin` 旁路。

`SkillPackage` 和“承载 AgentTemplate 的 package release”是 `packageType=content` 的类型化 ContentPackage 形态；`RuntimeDriver`、`AuthenticationDriver` 与 `DeploymentDriver` 是 `packageType=extension` 的类型化 ExtensionPackage 实现。它们可以在领域类型和 `ExactComponentRef<T>` 泛型参数中使用更具体的名称，但序列化 Envelope、Catalog 生命周期和安装类型始终只有 `solution|content|extension` 三种，禁止再创建 `skill`、`agent-template`、`runtime` 等第四类 `packageType` 或平行安装链。

### 2.1 SkillPackage

Skill 是 `packageType=content` 的类型化 ContentPackage，可作为不可执行 SemVer package 独立发布。设置页允许从 GitHub Repository 或本地文件导入，但导入结果仍必须转换为标准 ContentPackage Envelope，经过 Verify、Install、Activate 和 Content Catalog 发布；导入不是绕过 Package 治理的第二条写路径。

`SkillPackage` 是发布后的完整不可变 package release，不是导入表单。导入入口决定来源类型，Manifest 优先提供名称、说明与 SemVer，平台负责解析入口、生成稳定身份、digest 和 Provenance；只有无法可靠推导时才要求用户补充或确认。

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| skill_package_id | 是 | 技能包稳定身份 | manifest / system / user-confirm | 优先读取 Manifest；缺失时系统生成候选稳定身份并让用户确认 |
| version | 是 | 技能包语义版本 | manifest / user | 优先读取 Manifest；缺失时才要求用户填写，不允许 `latest` 或范围 |
| display.name | 是 | Skill 名称 | manifest / user | 优先读取 Manifest；缺失时要求用户补充 |
| display.description | 是 | Skill 说明 | manifest / user | 说明作用、适用场景与限制；缺失时要求用户补充，后续页面随版本展示 |
| source | 是 | 导入来源 tagged union | context / system | 由“从 GitHub 导入”“本地上传”或 Embedded 安装入口确定，用户不填写 `source.type` |
| entrypoint_path | 是 | Skill 入口文件路径 | system / user-confirm | 默认检测包根 `SKILL.md`；只有缺失或存在多个候选时才要求用户选择 |
| compatibility | 否 | 兼容性声明 | manifest / system | 从 Manifest 读取；缺失时按平台基线执行兼容检查，不要求普通用户编写能力清单 |
| artifact_digest | 是 | 完整 Skill package 制品摘要 | system | 同一 Skill ID 与 version 对应不同 digest 时拒绝发布 |
| content_digest | 是 | 规范化 Skill 内容摘要 | system | 用于内容校验与运行上下文证明 |
| provenance | 是 | 来源与导入证明 | system | 记录解析后的来源、导入 Principal 和校验结果，不包含 Secret |
| created_by / created_at | 是 | 创建人和创建时间 | context / system | 用于审计，不参与隐式版本选择 |

`source.type = github` 时：

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| repository_uri | 是 | GitHub Repository 地址 | user | 使用标准 URI；不把 Token 拼进地址 |
| requested_revision | 否 | 请求的 Branch、Tag 或 Commit | user | 缺省时由 GitHub Importer 解析 Repository 默认分支；只表达导入请求，不作为发布后的可重放身份 |
| resolved_commit_sha | 是 | 实际解析的 Commit SHA | connector | 发布前必须把默认分支、Branch 或 Tag 固定到不可变 Commit |
| subdirectory_path | 否 | Skill 所在子目录 | user | 缺省时从仓库根目录查找；必须执行路径逃逸与符号链接校验 |
| credential_binding_id | 私有仓库必需 | GitHub 凭证绑定身份 | user / context | 公开仓库省略；私有仓库可选择或就地创建，CredentialBroker 只为本次只读导入解析 |

`source.type = upload` 时：

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| original_file_name | 是 | 用户上传文件名 | system | 从上传内容取得，只用于展示和审计，不作为路径或身份 |
| upload_content_digest | 是 | 上传原始内容摘要 | system | 用于幂等、恶意替换检测和审计 |

GitHub 导入说明性 YAML：

```yaml
skillPackageId: software-architecture-review
# skillPackageId：平台优先从 Manifest 读取；缺失时生成候选值并由用户确认。

version: 1.2.0
# version：本次发布的精确语义版本；不能使用 latest。

display:
  # display：Settings 和 Agent Center 中的展示信息。
  name: Software Architecture Review
  # name：用户可读 Skill 名称。
  description: 指导 Reviewer 检查模块边界、依赖方向和演进风险
  # description：说明 Skill 的具体作用，后续页面始终随版本展示。

source:
  # source：导入来源 tagged union。
  type: github
  # type：表示从 GitHub Repository 导入。
  repositoryUri: https://github.com/example/agent-skills.git
  # repositoryUri：Repository 地址；不得把 Token 拼入 URI。
  resolvedCommitSha: 0123456789abcdef0123456789abcdef01234567
  # resolvedCommitSha：Importer 根据默认分支或用户指定 revision 解析得到的不可变 Commit SHA。
  subdirectoryPath: skills/software-architecture-review
  # subdirectoryPath：Skill 在 Repository 中的相对子目录。
  credentialBindingId: credential-github-skill-read
  # credentialBindingId：私有 Repository 只读导入使用的凭证绑定身份；公开仓库可省略。

entrypointPath: SKILL.md
# entrypointPath：系统默认检测的包内 Skill 入口；只有无法唯一确定时才要求用户选择。

contentDigest: sha256:...
# contentDigest：完整规范化文件树摘要；内容变化必须创建新版本。
```

导入流程必须执行：文件数量与大小限制、归档解压炸弹防护、路径逃逸检查、符号链接策略、入口文件和 Frontmatter 校验、Manifest/元数据解析、完整文件树摘要、兼容性校验，以及“不执行任何导入脚本或代码”的静态处理。只有名称、说明、版本或入口无法唯一得到时，流程才暂停并请求用户补充。GitHub Branch 或 Tag 后续变化不会修改已发布 release；用户重新导入时发布新的 SkillPackage SemVer。删除或禁用目录 release 不改写已经固定该 release 的历史 Workflow。

## 3. ComponentInstall

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| component_install_id | 是 | 安装记录身份 | 稳定请求键保证幂等 |
| package_release | 是 | `ExactComponentRef<Package>`，包含 Package 类型、ID、精确 SemVer 和 artifact digest | 与 Envelope 一致；字段直接表达被安装的精确 Package release |
| descriptor_path / descriptor_api_version / descriptor_digest | Solution/Content 必需 | 已验证描述文件的 Package 内路径、Schema 版本和摘要 | Catalog 只加载这一确定内容 |
| interface_api / entrypoint | Extension 必需 | 已验证 Extension API 版本和启动入口 | ExtensionCatalog 只注册这一接口实现 |
| origin | 是 | 可审计来源 | 不包含凭证正文 |
| trust_tier | 是 | 生效信任层级 | 撤销时进入 Revoke |
| lifecycle_policy | 是 | 安装时治理策略 | 变化形成可审计新决定 |
| status | 是 | Installed、Active、Disabled、Revoked、Removed | 一个枚举表达生命周期 |
| conditions[] | 否 | Unavailable、Degraded、Incompatible 等观察 | 不代替 status |
| verification_report_artifact_id | 是 | Package 检查和接口检查的统一报告身份 | 任一必需检查未通过都不能 Active |

## 4. 生命周期

| 操作 | 对新解析/调用 | 对活动调用 | 可逆性 |
| --- | --- | --- | --- |
| Verify | 无 | 无 | 是 |
| Install | 尚不可解析 | 无 | 是 |
| Activate | 可被新 Workflow 精确选择 | 不改变既有绑定 | 是 |
| Disable | 阻止新解析和新调用 | 已运行可结束，后续依赖点阻塞 | 是 |
| Revoke | 拒绝新建、恢复和重试 | 尝试取消；结果未知时 Fail Closed | 否 |
| Remove | Catalog 不再可见 | 不删除被历史引用的制品与审计 | 可重新安装 |
| Upgrade | 新版本独立进入生命周期 | 活动 Workflow 不静默采用 | 后继 Workflow 显式采用 |

该生命周期表用于 Platform Core 和 Component Governance 实现，不对应多个用户按钮。公开入口统一使用：

- `InstallAndActivatePackage`：要求 Package 激活权限，对精确 Package/Bundle 一次确认；内部执行 Verify、Install 和 Activate；
- `PublishAndActivateSolutionPackage`：要求同时具备 Solution 发布与 Package 激活权限，先形成不可变 SolutionPackage release，再复用 `InstallAndActivatePackage`；
- `PublishSolutionPackage`：仅发布不可变 release，不让它自动出现在当前 Scope 的 Solution 可选目录。

组合用例在任何写入前检查全部所需权限和幂等键。若发布成功而安装失败，release 仍存在但保持 inactive；若 ComponentBundle 任一 Required 成员失败，所有成员都不得进入 Active。用户不填写 `component_install_id`、`trust_tier`、`lifecycle_policy`、安装顺序或 `artifact_digest`，这些值由平台生成、校验或按治理策略决定。

## 5. `ExactComponentRef<Package>`

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| kind | 是 | Package 类型 | 只允许 solution、content、extension |
| id | 是 | 稳定身份 | 不按展示名解析 |
| version | 是 | 精确 SemVer | 不允许范围和 latest |
| artifact_digest | 是 | Package 摘要 | 防止同版本漂移 |

`ExactComponentRef<Package>` 只固定可安装 Package 内容，不携带 Descriptor 位置或调用配置。`kind` 只表示 `solution|content|extension` 三种 Package 类型，不存在 Component Category。Descriptor/Interface 事实属于 ComponentInstall；Harness、Runtime 或治理调用的配置由各自 Binding/Decision 中的 `config_digest` 固定，避免把安装身份与使用方式混成一个值。

## 6. LockedComponentSet

`LockedComponentSet` 是根 Workflow 创建时形成的不可变 Package 解析结果。它保存 SolutionPackage、Harness ExtensionPackage、RuntimeDriver ExtensionPackage 和 ContentPackage 的精确 release/digest，以及 package-local 内容和 Extension 配置 digest。Authentication 在用户登录时解析；Workspace、Audit Export 和 Credential Storage 使用平台内置 Port，因此不进入该集合。AuthorizationEngine 也不进入该集合。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| locked_component_set_id | 是 | 锁定集合身份 | 不原地修改 |
| set_digest | 是 | 完整锁定集合摘要 | 对全部精确 Package、Binding、配置与解析策略规范化计算；Workflow.spec 必须固定同一值 |
| workflow_key | 是 | 所属 Workflow 的 `{namespace, name}` | 不跨 Workflow 隐式复用 |
| solution | 是 | `ExactComponentRef<SolutionPackage>` | 必须 Active、授权且通过 Package 验证 |
| harness_extension_bindings[] | 否 | 从 Workflow 固定 Solution release 内 HarnessDefinition 解析出的 ExtensionPackage 精确绑定 | 包含 extension、artifact 和 config digest；ValidatedPlan 只能引用该固定集合内的 Harness |
| runtime_drivers[] | 是 | 可供 Agent Team 使用且实现 `runtime.oac.dev/v1` 的精确 ExtensionPackage | 实际 Run 固定 AgentDefinition 内一个 RuntimeBinding |
| content_members[] | 否 | `ExactComponentRef<Content>` 列表，显式区分独立发布的 SkillPackage、AgentTemplate 和其他类型化 ContentPackage 内容 | Solution package-local 内容由 Solution artifact digest 与 OrchestrationDefinition digest 固定，不重复列入 |
| policy_digest | 是 | Package 解析时生效策略摘要 | 只约束 Package 解析，不固定 AuthorizationPolicy |
| resolution_report_artifact_id | 是 | 解析、冲突和兼容性报告的 Artifact 身份 | 可重放 |

根 Workflow 的 `spec.lockedComponentSet` 内嵌上述完整不可变值，保证 Controller/Kernel Host 在 API Server 中即可恢复活动执行；PostgreSQL 保存内容相同的不可变记录与解析报告用于查询、审计和历史下钻，但不能要求活动 Workflow 通过稳定 ID 重新解析当前组件。两份内容的 `locked_component_set_id + set_digest` 必须一致。

同一业务目标需要采用新集合时，必须执行影响分析和用户或管理员 Decision，再创建新的后继根 Workflow 与新的 LockedComponentSet；不得原地改写活动 Workflow 的旧集合。

`LockedComponentSet` 只固定 Workflow 执行使用的 Package。已完成 Delivery 上的直接外部 Promotion 不创建 Workflow，因此 `StartPromotion` 在 Deployment 请求中单独固定当前目标解析出的、实现 `deployment.oac.dev/v1` 的精确 ExtensionPackage 和 target configuration digest；它不能复用或改写来源 Workflow 的 LockedComponentSet。

AuthenticationDriver 不进入 Workflow `LockedComponentSet`。它在 Principal/Scope 建立前按平台或未来显式 organization/realm 认证入口解析，Project、Solution、Workflow 和 Harness 不能选择它；认证后 OAC 固定执行唯一 AuthorizationEngine，执行创建固定执行唯一 ExecutionAdmissionEngine。Authorization 记录 Policy provenance，Execution Admission 记录 rules/facts provenance；外部准入事实适配器不能注册为 ExtensionPackage 或决定整体 Admit/Reject。

Audit Export 只通过平台内部 `AuditExportPort` 执行 `probe + deliver_batch`。它不能写入、修改、删除或查询本地 AuditEvent，不能接管 Audit Explorer、Retention 或脱敏，也不能被 Project、Solution、Workflow、Harness 或 Agent 选择。AuditExportTarget 不属于 Package、LockedComponentSet、Policy 或 Version 家族。

Credential Storage 只通过平台内部 `CredentialStorePort` 执行 Secret 材料的写入、轮换、解析、撤销和类型化验证。安装器自动准备一个内置实现；普通用户和 Project/Solution/Workflow/Harness 都不能选择后端。第三方凭证存储 Package、跨后端迁移和隐式回退不属于首版范围。

## 7. ComponentRun

只有 Workflow/WorkUnit 中需要独立幂等、来源、外部结果、错误或恢复语义的 Component 调用才创建 `ComponentRun`。Catalog 查询、纯 Renderer、子 Workflow 创建，以及 Software Delivery 已完成后的直接外部 Promotion 都不创建伪调用记录。后者使用不可变 Deployment 请求和追加式 `DeploymentReport`，不能为了复用本合同而移除 ComponentRun 的 Workflow/WorkUnit/iteration 约束。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| component_run_id | 是 | 一次逻辑调用身份 | Retry 创建新 attempt，不覆盖历史 |
| extension_package | 是 | `ExactComponentRef<ExtensionPackage>` | 运行期间不切换 package release |
| extension_id | 视 Harness Extension | 具体 Extension 身份 | 与 Harness Binding 一致 |
| harness_binding_id | 是 | 当前解析的 Harness Extension Binding 身份 | 不从 latest 重新解析 |
| subject | 是 | 被处理对象的 `SubjectKey` | 旧主体 ID/digest 的结果不能进入当前 Gate |
| idempotency_key | 是 | 逻辑副作用身份 | 重放不产生重复外部动作 |
| attempt | 是 | 技术重试序号 | 不等同业务返工 iteration |
| status | 是 | Pending、Running、Succeeded、Failed、Unknown、Cancelling、Cancelled | 一个枚举一种含义 |
| external_operation | 否 | 外部操作的 tagged union，包含系统类型、external_id 和可选 URI | 用于 Observe 和恢复 |
| result | 否 | 结构化结果 tagged union，显式区分 Artifact、Observation、Report 和外部结果 | 先持久化再消费 |
| error | 否 | 结构化错误 | 明确 retryable 与 outcome_known |

Workflow 副作用 Executor 必须提供 `execute`、`observe` 和 `cancel`。结果为 Unknown 时先观察外部状态，不能直接重试创建 PR、Merge、Release 或 Preview Deployment。DeploymentDriver 使用 Deployment SDK 的 `start`、`observe` 和 `cancel`，并通过 `SubmitDeploymentReport` 报告，不进入 ComponentRun。其 `Unknown` 必须持续占用目标并发键并禁止新 start；`Failed` 只能表示外部操作已明确终止。重试同一 Release 或部署旧 Release 由新的 `StartPromotion` 创建新 Deployment，不能由 Driver 在一次调用内隐藏循环、回滚或第二套状态机。

Harness Host 通过 Platform Core 的专用 `SubmitComponentRunReport` 合同提交当前 ComponentRun 结果；同步和异步 Executor 使用相同幂等、sequence/digest 和 run-scoped 约束。AgentRun 事件与终态只通过独立的 `AppendAgentRunEvents`/`CompleteAgentRun` 用例处理。完整结果先持久化，Workflow CR 只保存紧凑活动摘要。

### 7.1 SDK 测量树与收据权限

SDK 执行测量与 `ExecutionReceipt` 共用一份不可变递归树：节点保存完整 invocation identity/scope、operation、request/service-payload digest、outcome、实现调用数、provenance/source、Gate policy/evaluator identity 及有序子节点和父节点 lineage。既有 `ExecutionReceipt` 公共投影和 child-digest 算法保持兼容；接受收据要求每个节点的私有 SDK seal 与完整公开投影一致，改变节点字段、子节点顺序或嵌套 lineage 均拒绝。

该树是数据，不是权限。SDK-local conformance measurement 不签发 Host authority，不能构造 binding、authoritative receipt 或 evaluator invocation。JSON decode 或复制公开 DTO 不会生成任何节点私有 seal；根 seal 不能授权未封存子节点。子树在同一规范父身份下可复用，改绑到不同父身份必须拒绝；nil/empty children 遵循兼容的历史规范化。安装器必须针对精确 Package 执行自己的固定 suite，调用方提供的 Verification Report 不能仅凭摘要或内部完整性获得认证。source-bound conformance evidence 和打包源必须绑定同一已提交 revision。Harness Host 是唯一负责配置并验证 issuer/trust、有效期/轮换和 replay/restart 的生产者；其包中立 handoff 固定精确 Package release/artifact、entrypoint、Extension/binding/config、operation、owner、subject、Workflow/WorkUnit/iteration/ComponentRun scope 与 request/service-payload digest。Gate handoff 还固定 policy revision、evaluator registration/release 与 evaluator ID。SDK consumer 只接受 Host 附带的 opaque 已验证 handoff，不接受调用方自选 verifier 或 trust。

## 8. 权限求交

有效权限是以下集合的交集：

```text
Interface request
∩ SDK maximum
∩ Trust tier
∩ Platform policy
∩ Project policy
∩ Solution policy
∩ LockedComponentSet
∩ current invocation scope
```

内置来源不获得旁路。Harness Host 和各类型化 SDK 调用方只暴露当前调用需要的窄 Port，不暴露 Repository、数据库、CRD、任意状态写入、Gate 通过或 Secret 枚举。

## 9. ComponentBundle

ComponentBundle 只负责原子分发多个 Package。它不拥有运行权限、状态、调用、依赖求解或统一 Host。Bundle 安装失败时不得部分激活 Required 成员。

## 10. Package 与接口检查

安装时只形成一个统一 Verification Report，其中包含：

1. `package`：Envelope、签名、来源、路径边界、精确版本和 digest 检查；
2. `interface`：ExtensionPackage 对应 SDK 的结构和行为检查；Solution/Content 则执行各自静态发布验证。

第三方开发者在本地/CI 与平台安装时运行同一个 SDK 检查入口。Package 不能选择检查项、关闭必需检查、修改通过标准或自行提交成功结论。任一必需检查失败或 Unknown 都必须阻止 Activate；Builtin 也执行同一检查入口。

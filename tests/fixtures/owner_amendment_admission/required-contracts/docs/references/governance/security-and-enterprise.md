# 治理、安全与企业扩展契约参考

本文定义 Governance 领域的身份上下文、授权、执行准入、审计、凭证、安全要求与 Usage。首版只有 Authentication 通过公开 Extension API 扩展；Audit Export 与 Credential Storage 使用平台内部 Port 和内置实现。Authorization、Execution Admission 与本地审计始终使用 OAC 唯一内置实现。Project 与 Agent Team 字段见 [Project 与 Agent Team 契约参考](../project-and-team/project-and-agent-team.md)。

共同记法见 [Reference 共同约定](../conventions.md)，设计边界见 [治理与安全详细设计](../../design/detailed/09-governance-and-security-detailed-design.md)。

## 1. PrincipalIdentity、ScopeIdentity 与 RequestContext

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| principal_id | 是 | 已认证 Principal 的稳定身份 | 认证只证明是谁，不证明能做什么 |
| scope_id | 是 | 请求所在组织或治理范围身份 | 首版本地 Scope 也必须显式保存 |
| request_id | 是 | 外部请求身份 | 全链路传播并支持幂等 |
| causation_id | 是 | 直接原因 | 内部 Action 必须可追溯到上游 |
| correlation_id | 是 | 跨模块关联身份 | 不作为授权依据 |
| authentication_context | 是 | 认证方式、时间和强度 | 不包含凭证正文 |
| source | 是 | Web、API、CLI、Controller、AgentRun Job、Harness Host 等来源 | 来源本身不授予权限 |
| policy_context_revision / policy_context_digest | 是 | 评估时的治理上下文修订号与摘要 | revision 是 CAS/来源证明，digest 保证可重放；不形成 RequestContext Version 聚合 |

Authentication 成功后，每个外部请求和内部 `ApplicationCommand` 都携带 `RequestContext`。认证前的身份材料只是 AuthenticationDriver 输入，不构造缺少 Principal 的半成品 RequestContext。后台恢复不能伪造匿名管理员上下文，应沿 causation 恢复原始主体或使用明确的受限服务主体。

`authentication_context`（认证上下文）至少包含：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_method_id` | 认证方式身份 | 固定实际使用的配置实例；普通配置更新不能改写已签发上下文 |
| `authentication_driver` | AuthenticationDriver 精确扩展实现 | 固定认证时实际执行的 ExtensionPackage release/digest，供审计和故障定位 |
| `external_identity_link_id` | 外部身份绑定身份 | 固定本次 AuthenticatedSubject 到 Principal 的精确映射；不向普通客户端暴露其他人的 Link |
| `authentication_strength` | 认证强度 | 来自已验证 AuthenticatedSubject；Authorization 可以据此收紧要求，不能由客户端覆盖 |
| `authenticated_at` | 认证完成时间 | 显式时钟输入；用于 Session 与策略判断 |

## 2. AuthenticationDriver

AuthenticationDriver 是实现 `authentication.oac.dev/v1` 的 ExtensionPackage，只负责验证外部身份材料并产生标准化 `AuthenticatedSubject`。它不直接产生或修改 `PrincipalIdentity`，不执行资源授权、执行准入、业务 Gate、Extension 权限求交、身份建号、Session 签发或 Scope 选择。

内置本地认证与未来 OIDC、SAML、LDAP 等企业实现使用同一 Authentication SDK、ExtensionCatalog 和接口检查；内置实现不走例外通道。

### 2.1 Authentication SDK 与 AuthenticationMethod

Authentication SDK 固定提供以下窄操作：

| 操作 | 中文用途 | 关键约束 |
| --- | --- | --- |
| `DescribeAuthenticationMethod` | 产生登录方式展示描述 | 只返回平台支持的有类型交互描述，不返回 Secret、Principal 或授权结论 |
| `StartAuthentication` | 开始一次认证交互 | 必须绑定精确 AuthenticationMethod、配置摘要和一次性 AuthenticationFlow |
| `CompleteAuthentication` | 完成认证协议 | 只返回 `AuthenticatedSubject`；不得写 Principal、Session 或权限 |
| `ProbeAuthenticationMethod` | 测试认证配置 | 返回脱敏诊断；成功不自动启用方式，也不能签发业务 Session |

`AuthenticationMethod`（认证方式）是一个已配置的实际登录入口，不是 Package，也不使用 SemVer。多个 AuthenticationMethod 可以复用同一个 AuthenticationDriver ExtensionPackage。

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_method_id` | 认证方式稳定身份 | 系统生成；普通编辑不改变该身份 |
| `name` | 认证方式名称 | 管理员填写，展示给最终用户 |
| `description` | 认证方式说明 | 管理员填写，说明适用人群和作用 |
| `authentication_driver` | AuthenticationDriver 精确扩展实现 | 必须指向 ExtensionCatalog 中已安装、Active 且通过 Authentication SDK 接口检查的精确 ExtensionPackage release/digest |
| `interaction_kind` | 登录交互类型 | Adapter 声明的 `credentials-form`、`browser-redirect` 或 `non-browser` 等平台支持类型；不是协议名 |
| `configuration` | 非敏感认证配置 | 必须符合 Adapter 提供且带字段说明的 Schema；不得包含 Client Secret、Bind Password 或私钥 |
| `credential_binding_ids[]` | 敏感凭证绑定身份列表 | 可选；只允许绑定被当前方法和 Driver 明确声明用途的 CredentialBinding |
| `enabled` | 是否允许新认证 | 禁用后不得出现在可用登录方法中，也不能开始新 Flow |
| `revision` | 配置修订号 | CAS 并发控制；不是语义版本 |
| `configuration_digest` | 规范化配置摘要 | 覆盖非敏感配置、CredentialBinding 身份和精确 Driver，用于测试与激活防陈旧 |

同一方法允许更新不改变身份含义的连接参数与 CredentialBinding；协议、issuer/directory 身份域或 subject 规范变化必须创建新的 AuthenticationMethod，不能在稳定身份下偷换身份命名空间。已有 ExternalIdentityLink、BrowserSession 或 AuditEvent 引用的方法只能禁用/归档，不能硬删除。Driver 升级只有在 Authentication SDK 的 identity-stability 检查通过或完成显式迁移时才能沿用原方法。

`AuthenticationMethodDescriptor`（认证方式展示描述）是瞬态 Query DTO，不创建数据库聚合：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_method_id` | 认证方式身份 | 登录开始请求的唯一选择值 |
| `name` | 展示名称 | 来自 AuthenticationMethod |
| `description` | 展示说明 | 来自 AuthenticationMethod，必须与名称一起展示 |
| `interaction` | 有类型交互描述 | `credentials-form` 可携带受限输入 Schema，`browser-redirect` 只需要标准开始动作，`non-browser` 不在 Web 人类登录页展示 |

`AuthenticationFlow`（认证流程记录）是短期、单次消费的认证 ceremony 支持记录，不是 Product Core 聚合、Workflow、Decision 或 Version：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_flow_id` | 认证流程身份 | 系统生成；关联开始与完成 |
| `authentication_method_id` | 认证方式身份 | 固定到开始时选择的方法 |
| `method_configuration_digest` | 认证方式配置摘要 | 完成时发现配置已变化必须拒绝旧 Flow 并重新开始 |
| `purpose` | 流程用途 | `login`、`link` 或 `test`；`test` 不能产生业务 Session |
| `state_token_digest` | 回调状态 Token 摘要 | 验证 state 或等价一次性 Token，不保存明文 |
| `protected_state` | 受保护协议状态 | 加密或等价保护的 nonce、PKCE verifier 等 Adapter 状态；不得通过 Query、日志或 Audit 返回 |
| `return_path` | 完成后返回路径 | 仅允许已验证站内路径，禁止开放重定向 |
| `expires_at` | 过期时间 | 短期有效，过期必须重启认证 |
| `consumed_at` | 消费时间 | 非空后不得重复完成 |

`AuthenticatedSubject`（已认证外部主体）是 Adapter 的协议中立输出：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_method_id` | 认证方式身份 | 标识本次结果来自哪个配置实例 |
| `issuer_key` | 身份签发方稳定键 | 使用规范化 OIDC issuer、SAML entity、LDAP directory 或 Local 域键 |
| `subject_key` | 签发方内主体稳定键 | 使用协议稳定 subject；不得使用邮箱、显示名称或可修改登录名代替 |
| `display_name` | 外部显示名称 | 可选展示属性，不参与身份唯一性 |
| `verified_email` | 已验证邮箱 | 可选属性，不得据此自动合并本地 Principal |
| `attributes` | 允许传递的归一化属性 | 仅包含 Adapter 声明 allowlist；claim/group 只作为后续 Policy 输入 |
| `authentication_strength` | 认证强度 | Adapter 根据已验证事实产生，客户端不能自报 |
| `authenticated_at` | 认证完成时间 | 显式时钟输入，用于会话和审计 |

`ExternalIdentityLink`（外部身份绑定）负责把协议身份映射到平台 Principal；Local 认证也必须使用该模型：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `external_identity_link_id` | 身份绑定稳定身份 | 系统生成；不是 Version |
| `authentication_method_id` | 认证方式身份 | 区分不同企业身份源 |
| `issuer_key` | 身份签发方稳定键 | 必须与 AuthenticatedSubject 精确匹配 |
| `subject_key` | 外部主体稳定键 | 与方法和 issuer 共同唯一 |
| `principal_id` | 本地 Principal 身份 | 映射后的唯一平台主体 |
| `linked_at` | 建立绑定时间 | 由受控初始化、邀请、管理员绑定或显式 JIT Policy 写入 |
| `disabled_at` | 停用时间 | 非空后禁止该身份继续登录，历史引用保持可读 |

身份解析只允许按 `authentication_method_id + issuer_key + subject_key` 精确匹配。缺少绑定时默认拒绝；仅显式 Identity Provisioning Policy 才能创建 Principal 或 Link。Email、display name 和 group 不得隐式合并身份或直接授权。Local Adapter 使用 `issuer_key=oac.local` 和不可变本地账号 subject，登录名与邮箱都不是稳定身份键。

认证方法候选在 Principal/Scope 建立前按平台或未来显式 organization/realm 入口解析；首版不创建 AuthenticationRealm 聚合。Project、Solution、Workflow 和最近访问记录不得选择认证方式。方法配置按“保存 → Probe → 测试登录 → CAS 启用”生效；默认撤销被禁用方法已签发的 Browser Session，任何保留到期行为必须来自显式 Session Policy。

### 2.2 本地身份初始化与用户邀请

首版本地认证固定使用“首个管理员一次性初始化 + 管理员邀请普通用户”的准入方式。公开自助注册默认不存在；Invitation 已经表达管理员准入，不再创建 Pending Approval 审批状态机。

Governance Identity Use Case 至少暴露：

| 用例 | 中文用途 | 关键约束 |
| --- | --- | --- |
| `BootstrapLocalAdministrator` | 创建首个本地管理员 | 必须绑定安装器生成的一次性 `bootstrap_code`；原子创建 Principal、Local ExternalIdentityLink、首个 Local Scope、安全默认 AuthorizationPolicy 与管理员授权并消费 Code |
| `InviteLocalUser` | 邀请普通本地用户 | 需要当前管理员授权；管理员不能设置用户密码；同一邀请仅有一个有效 Token |
| `AcceptLocalUserInvitation` | 接受邀请并创建账号 | 原子创建 Principal、Local ExternalIdentityLink、认证材料和 Scope 访问关系并消费 Invitation；接受后直接 Active |
| `AuthenticateLocalUser` | 本地账号登录 | Local Adapter 返回 AuthenticatedSubject，经 ExternalIdentityLink 解析 Principal 后创建 Browser Session；不执行资源授权 |
| `LogoutBrowserSession` | 退出当前浏览器会话 | 服务端撤销 Session 后清除 Cookie，不修改业务对象 |
| `IssueLocalRecovery` | 签发本地身份恢复挑战 | 管理员 Web 路径或安装器 break-glass 路径；不接收新密码 |
| `CompleteLocalRecovery` | 使用一次性恢复挑战设置新密码 | 消费 Token、替换密码摘要并撤销既有 Session |

`LocalUserInvitation`（本地用户邀请记录）字段：

| 英文字段 | 中文字段释义 | 值的来源与约束 |
| --- | --- | --- |
| `invitation_id` | 邀请稳定身份 | 系统生成；用于幂等、撤销、审计和接受绑定，不是语义版本 |
| `scope_id` | 目标治理范围身份 | 从管理员当前 RequestContext 继承；首版固定为唯一 Local Scope |
| `invited_email` | 被邀请邮箱 | 管理员填写；接受页面只读固定，不能用 Token 改绑其他邮箱 |
| `display_name_hint` | 显示名称建议 | 管理员可选填写；用户接受时可以确认或修改 |
| `access_profile_key` | 初始访问权限模板键 | 管理员可选；必须存在于当前 AuthorizationPolicy，缺省采用 `default_access_profile_key`，不在 Core 写死角色枚举 |
| `token_digest` | 邀请 Token 摘要 | 系统对明文 Token 做单向摘要；明文仅在发送或创建成功响应中出现一次 |
| `expires_at` | 邀请过期时间 | 当前 Scope Policy 产生；超时后不能接受 |
| `invited_by` | 邀请管理员身份 | 从 RequestContext 的 `principal_id` 产生，用于审计 |
| `accepted_at` | 接受时间 | 成功创建账号时系统写入；非空即表示已消费 |
| `revoked_at` | 撤销时间 | 管理员撤销或重新签发时系统写入；非空后旧 Token 永久失效 |
| `created_at` | 创建时间 | 系统时钟显式输入，用于过期和审计 |

邀请展示状态不单独持久化：`accepted_at != null` 为 `Accepted`，否则 `revoked_at != null` 为 `Revoked`，否则当前时间超过 `expires_at` 为 `Expired`，其余为 `Pending`。接受、撤销和重新签发都对当前 Token 摘要及未接受/未撤销条件执行单行 CAS，不向用户暴露 Invitation revision 字段，避免两个链接并发创建两个 Principal。

### 2.3 Browser Session 与 Scope 选择

Browser Session 是 Governance Session Adapter 的服务端支持记录，不归 AuthenticationDriver 所有，也不是 Platform Core 产品聚合、Workflow 状态或客户端权限缓存。

| 英文字段 | 中文字段释义 | 值的来源与约束 |
| --- | --- | --- |
| `session_id` | 浏览器会话身份 | Session Adapter 生成；浏览器 Cookie 只携带不可猜测的该身份或等价不透明值 |
| `principal_id` | 当前登录主体身份 | Identity Resolver 通过精确 ExternalIdentityLink 解析成功后写入 |
| `authentication_method_id` | 认证方式身份 | 固定实际使用的配置实例；协议与 Adapter package provenance 进入认证上下文和 Audit，不授予权限 |
| `issued_at` | 签发时间 | 系统时钟显式输入 |
| `expires_at` | 会话过期时间 | Authentication Policy 产生；过期后必须重新认证 |
| `revoked_at` | 撤销时间 | 用户退出、管理员强制退出或身份恢复时写入 |
| `selected_scope_id` | 当前选择的治理范围 | 唯一授权 Scope 时自动产生；多个授权 Scope 时必须由用户显式选择，并在每次请求重新校验 |

Browser Session Cookie 必须使用 HttpOnly、Secure 和适当 SameSite 策略，不携带密码、权限列表、Secret 或完整 RequestContext。后续请求根据有效 Session 和当前授权事实重建 RequestContext；Session 中的 Scope 选择不能替代 Authorization。

### 2.4 Recovery Challenge

`LocalRecoveryChallenge`（本地身份恢复挑战）与 Invitation 使用相同的一次性 Token 原则，但只能作用于一个已存在的精确 Principal：

| 英文字段 | 中文字段释义 | 值的来源与约束 |
| --- | --- | --- |
| `recovery_id` | 恢复挑战身份 | 系统生成；用于幂等与审计 |
| `principal_id` | 待恢复主体身份 | 管理员 Web 路径或安装器 break-glass 路径显式选择 |
| `token_digest` | 恢复 Token 摘要 | 服务端只保存摘要，明文只交付一次 |
| `expires_at` | 恢复挑战过期时间 | Policy 产生，必须短期有效 |
| `issued_by` | 签发来源 | 精确管理员 Principal，或受限 Installer break-glass 身份 |
| `consumed_at` | 消费时间 | 成功修改密码后写入；非空后不能重用 |
| `revoked_at` | 撤销时间 | 重新签发或管理员撤销时写入 |

`CompleteLocalRecovery` 不接受管理员代填的密码，只允许 Token 持有人在受控 Web 页面设置新密码。完成后必须撤销该 Principal 的既有 Browser Session。Invitation、Browser Session 和 Recovery Challenge 都不使用 SemVer、不创建 `Version` 聚合，也不进入 Workflow 或 Kernel。

## 3. OAC AuthorizationEngine

OAC 只有一个内置 `AuthorizationEngine`（统一授权引擎）。代码内部可以保留一个稳定的 Authorization 调用接口，供 Platform Core Use Case、Controller Application Service 和治理 Query Service 依赖；该接口不注册外部实现，不属于 ExtensionPackage 或公开 SDK 扩展点。

### AuthorizationPolicy

每个已建立的 Scope 恰好有一个当前 AuthorizationPolicy。它是普通可变配置，不使用 SemVer，也不创建 Version 聚合。

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `authorization_policy_id` | 授权策略稳定身份 | 是 | 系统生成；Scope 生命周期内稳定 |
| `scope_id` | 所属治理范围身份 | 是 | 一个 Scope 只解析一个当前策略 |
| `name` | 授权策略名称 | 是 | 管理员填写，用于管理和审计展示 |
| `description` | 授权策略说明 | 是 | 管理员填写，说明组织授权约定 |
| `default_access_profile_key` | 默认访问权限模板键 | 是 | 必须指向当前 `access_profiles[]` 内存在的模板；供 Invitation 等未显式选择时使用 |
| `access_profiles[]` | 访问权限模板列表 | 是 | key 在策略内唯一；被现有访问关系引用时不能无影响检查删除 |
| `revision` | 策略修订号 | 是 | CAS 并发控制，不是语义版本 |
| `policy_digest` | 规范化策略摘要 | 是 | 系统根据完整内容计算；Decision 固定实际使用值 |

`access_profiles[]`（访问权限模板）元素：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `access_profile_key` | 访问权限模板稳定键 | 是 | 当前策略内唯一；成员/Project 访问关系保存该 key |
| `name` | 模板名称 | 是 | 用户可读 |
| `description` | 模板说明 | 是 | 解释适用对象和权限边界 |
| `grants[]` | 允许授权项列表 | 是 | 只表达 Allow；无匹配默认 Deny，不支持优先级、覆盖顺序或脚本 |

`grants[]`（允许授权项）元素：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `grant_key` | 授权项稳定键 | 是 | 当前模板内唯一；用于解释和审计 |
| `description` | 授权项说明 | 是 | 解释授予原因 |
| `action_keys[]` | 允许操作键列表 | 是 | 只能从 OAC Action Catalog 选择 |
| `subject_kinds[]` | 适用对象类型列表 | 是 | 只能从 OAC Subject Kind Catalog 选择；实际范围仍受本地访问关系约束 |

`QueryAuthorizationCatalog`（查询授权目录）返回瞬态只读 DTO，不创建 Catalog 聚合、表或 Version：

| 英文字段 | 中文字段释义 | 关键约束 |
| --- | --- | --- |
| `actions[].key` | 操作稳定键 | 由已发布 OAC Application Use Case 在代码与 API Schema 中声明；Policy 只保存该值 |
| `actions[].name` | 操作名称 | 用户可读并支持本地化 |
| `actions[].description` | 操作说明 | 解释操作影响和适用对象，不能只展示 key |
| `subject_kinds[].key` | 对象类型稳定键 | 来自 OAC 规范 SubjectKey 类型；SolutionPackage/Harness Extension 不能动态增加平台授权对象类型 |
| `subject_kinds[].name` | 对象类型名称 | 用户可读并支持本地化 |
| `subject_kinds[].description` | 对象类型说明 | 解释该类型包含的资源边界 |

目录来自 OAC 发布代码和 API Schema，不是外部 Provider 或数据库配置。改变已有 key 语义属于 API 兼容性变更。

固定 OAC invariants 不进入 Policy：默认拒绝、Scope 隔离、禁用主体、human-only、职责独立性、精确 subject、Secret/敏感数据保护以及 Project/SolutionPackage/ExtensionPackage 不得扩大权限。外部 group/claim 只能经受控 Identity Mapping 转为本地访问关系，不能由 AuthorizationEngine 隐式信任。

### AuthorizationRequest

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `request_context` | 当前主体与请求链路 | 是 | 包含 Principal、Scope、认证和因果信息 |
| `action_key` | 请求操作键 | 是 | 必须来自 OAC Action Catalog；调用方不能自定义语义 |
| `subject` | 被操作对象 | 是 | Project、Workflow、Component、Artifact 等精确 `SubjectKey` |
| `requested_scope` | 请求影响范围 | 是 | 必须与 RequestContext 和 subject 归属一致 |
| `attributes` | 显式授权事实 | 否 | 数据分类、环境、风险、责任、独立性等类型化事实 |
| `requirements` | 当前请求资格要求 | 否 | human-only、认证强度或 WorkUnit 责任等固定要求；只能收紧 |

### AuthorizationDecision

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `authorization_decision_id` | 授权决定身份 | 是 | 不可变 |
| `effect` | 授权结果 | 是 | `Allow`（允许）或 `Deny`（拒绝）；不支持 Abstain 后隐式放行 |
| `reason_code` | 稳定原因码 | 是 | 供用户提示、审计和自动化定位 |
| `authorization_policy_id / policy_revision / policy_digest` | 生效策略身份、修订号与内容摘要 | 是 | 可审计、可重放；策略是当前可变配置，不为每次修改创建 Version 聚合 |
| `matched_grants[]` | 匹配的授权来源列表 | 否 | 每项同时包含 `access_profile_key + grant_key`，避免两组平行数组错位；Allow 时用于解释权限来源，Deny 可以为空 |
| `obligations[]` | 后续附加要求 | 否 | 只能收紧，不能扩大原请求 |
| `evaluated_at` | 评估时间 | 是 | 作为显式输入 |

统一引擎使用显式的 AuthorizationRequest、当前 Policy、本地访问关系、固定 invariants 和评估时间计算结果。同样输入必须得到同样结果。策略缺失/损坏、未知 Action/Subject、事实不完整或内部错误全部 Deny；调用方不能选择 Policy 或替换引擎。

`EvaluateAuthorizationPolicyCandidate`（评估授权策略候选）只返回瞬态检查结果，不持久化候选；`AdoptAuthorizationPolicy`（采用授权策略）必须使用 `expected_revision + candidate_digest`（预期策略修订号与候选内容摘要）重新校验并原子替换当前内容。至少阻止未知目录项、重复 key、孤儿 access profile、最后管理主体锁死和固定规则绕过。采用后新请求立即使用新 revision/digest，历史 AuthorizationDecision 不改写。

## 4. OAC ExecutionAdmissionEngine

Authorization 通过后、创建 ComponentRun、直接 Deployment、ChildWorkflowLink、AgentRun、human Run request 或 Workspace 前调用唯一内置 `ExecutionAdmissionEngine`。代码内部可以保留稳定的 Execution Admission Port 以维持依赖方向，但该 Port 不注册外部决策实现，不属于 ExtensionPackage 或公开 SDK 扩展点。

### ExecutionAdmissionRequest

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `request_context` | 已授权请求上下文 | 是 | 固定 Principal、Scope、请求和因果事实 |
| `execution_kind` | 执行种类 | 是 | `component-run\|direct-deployment\|child-workflow\|agent-run\|human-run\|workspace` |
| `subject` | 精确执行对象 | 是 | 包含必要 ID/digest/revision |
| `owner_context` | 执行归属上下文 | 是 | tagged union；精确表达 Project、Workflow/WorkUnit/iteration 或 Deployment 归属 |
| `resolved_execution` | 已解析执行实现 | 视种类 | tagged union；仅携带兼容性判断需要的逻辑实现事实，不携带外部 Provider 句柄 |
| `resource_requirements` | 资源要求 | 否 | CPU、内存、GPU、存储、架构和拓扑 |
| `security_requirements` | 执行安全要求 | 是 | 网络、数据、凭证、身份、隔离和副作用要求 |
| `usage_context` | 使用量观察上下文 | 是 | 首版只读，不隐式触发 Budget 自动暂停 |
| `idempotency_key` | 创建请求幂等身份 | 是 | 只能绑定同一逻辑物化请求 |

Platform Core 从当前平台/Scope/Project 状态、执行器能力、资源容量、ExecutionSecurityRequirements、Credential readiness、外部副作用并发事实和可选外部检查事实组装瞬态 `ExecutionAdmissionFacts`。它没有独立 ID、Repository、Version 或生命周期。外部事实端口只能返回单项 `pass|fail|unknown + reason_code + evidence_digest + observed_at`，不能返回整体准入结果。必需事实缺失、Unknown、超时、过期或相互矛盾全部 Reject。

`human-run` 不携带 Runtime、Workspace 或基础设施实现。其 `resolved_execution` 保存宿主生成的 `principal_requirement`（提交者资格要求）摘要和 output contract 身份；ExecutionAdmissionEngine 只判断当前 Scope/Project 是否允许形成该人工执行入口。实际提交仍必须在 `SubmitHumanRunResult` 中重新执行 Authorization，并校验 exact Workflow/WorkUnit/Run、subject、责任资格、独立性、`invocation_id + request_digest`、expected WorkUnit revision、幂等键与 submission digest。首版不建立领取、转派、租约或在线状态授权模型。

### ExecutionAdmissionDecision

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `execution_admission_decision_id` | 执行准入决定身份 | 是 | 不可变 |
| `request_digest` | 精确请求摘要 | 是 | 不同请求不能复用 |
| `effect` | 准入结果 | 是 | `Admit` 或 `Reject`；没有 Abstain 后隐式放行 |
| `reason_code` | 稳定原因码 | 是 | 容量、安全、凭证、并发、兼容性或事实未知原因 |
| `evaluated_checks[]` | 已评估检查列表 | 是 | 每项固定 check key、`pass\|fail\|unknown`、原因和可选证据来源 |
| `constraints[]` | 附加收紧约束 | 否 | 调度、并发、资源等限制；不能改变 WorkUnit 业务语义 |
| `rules_digest` | OAC 准入规则摘要 | 是 | 不是 AdmissionPolicy ID、Provider 或 Version |
| `facts_digest` | 显式事实摘要 | 是 | 环境事实变化后必须重新评估 |
| `evaluated_at` | 评估时间 | 是 | 显式输入，用于新鲜度判断和审计 |

首版不建立 AdmissionProvider、Registry、Binding、用户管理 AdmissionPolicy 或独立配置页面。Admit 后应用 Use Case 在同一持久化边界保存 Decision、AuditEvent 和执行创建命令；Reject 只能保存 Decision/AuditEvent，不得创建执行对象。未来 Quota、Budget、Reservation 和 Stop-loss 只作为普通类型化设置和事实进入同一引擎，无需修改 Workflow Lifecycle。

## 5. AuditEvent、AuditExportTarget 与内部 AuditExportPort

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| audit_event_id | 是 | 审计事件身份 | 只追加 |
| request_context_id | 是 | 已持久化 RequestContext 身份 | 可还原 causation |
| category | 是 | authentication、authorization、admission、decision、credential、state-write 等 | 不用普通日志代替 |
| action | 是 | 被尝试或完成的动作 | 语义稳定 |
| subject | 是 | 被操作对象的 `SubjectKey` | 精确到必要版本 |
| outcome | 是 | allowed、denied、succeeded、failed | 不隐藏失败 |
| evaluated_controls[] | 是 | `EvaluatedControl` 列表；Authorization 项包含 Policy ID/revision/digest 和匹配 grant，Execution Admission 项包含 rules/facts digest、检查结果和约束，采用 Package 的其他治理类别另外包含 Provider 精确 package release/digest | 同一事件内 `(kind, control_id)` 必须唯一；不同 `control_id` 的 Provider 检查可以并存。不能把不同类别压成无类型 ID 数组，也不能为内置引擎伪造 Provider 来源 |
| change_summary | 否 | 状态写入前后摘要 | 不写 Secret 或完整敏感内容 |
| occurred_at | 是 | 事件时间 | 显式输入 |

`change_summary` 使用受控 `SafeSummary` wire DTO，而不是自由文本：`code` 仅允许 `state-write`、`export-probe`、`target-lifecycle` 或 `agent-definition-publish`；`fields[].name` 仅允许 `action`、`count`、`format`、`operation`、`reason`、`resource`、`result`、`revision`、`scope`、`status`、`target`，每个名称最多出现一次；`fields[].value` 仅允许 `completed`、`deployment completed`、`succeeded`、`failed`、`created`、`updated`、`enabled`、`disabled`、`accepted` 或 `rejected`。`AuditExportProbePayload.summary` 使用同一矩阵，未知 code、字段、重复字段或值均在 Validate 与 canonical JSON 边界拒绝。

Audit detail 的 `request_context_summary` 同时保留 persisted `request_context_id` 与外部 `request_id`；二者不是同一身份，详情校验只把前者与 AuditEvent 绑定。详情 authority 只接受由持久化 authority 签发的 opaque store binding；binding 的 proof 与事件状态不暴露给调用方，不能把调用方自选的原始事件直接升级为 binding。Go contract 仅接收平台/store wiring 通过 internal capability boundary 交付的 receipt，不暴露可注入的 authority callback 或 trust root。

本地 AuditEvent 是权威来源。同一存储中的状态与 AuditEvent 使用同一事务。任何先提交 PostgreSQL 事实再更新 Workflow CR 的持久业务命令，都通过同事务 CommandRecord/AuditEvent/Outbox 由 Platform Core 幂等送达 `spec/status/finalizer`；Workflow Controller 只提交携带稳定 LifecycleAction identity/digest 的类型化命令。心跳、进度和 Pod Running 等瞬时信号限频、幂等、CAS 写入，但不为每次采样创建 Outbox/AuditEvent。可选内部 AuditExportPort 只导出已提交事件；目标故障不能回滚业务提交，也不能删除本地记录。

治理实现和 Policy 不固定到 Workflow。AuthenticationDriver 在 Principal/Scope 建立前根据平台或未来显式 organization/realm 认证入口解析，Project、Solution 和 Workflow 不得选择它；认证成功后，唯一 AuthorizationEngine 使用当前 Scope 的 AuthorizationPolicy，执行创建固定调用唯一 ExecutionAdmissionEngine。可选 AuditExportTarget 与内置 CredentialStore 再按当前 Governance 配置运行。Authorization 记录 Policy provenance，Execution Admission 记录 rules/facts provenance，Authentication 记录精确 Driver release/digest。系统不创建治理绑定版本聚合，也不把治理配置放入 `LockedComponentSet`。

### 5.1 AuditExportTarget

首版每个 Scope 最多存在一个活动 AuditExportTarget；没有目标是合法默认状态。

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `audit_export_target_id` | 审计导出目标稳定身份 | 是 | 系统生成；不是 ExtensionPackage ID |
| `scope_id` | 所属治理范围 | 是 | 从管理上下文确定；不能跨 Scope 外发 |
| `name` | 导出目标名称 | 是 | 用户可读 |
| `description` | 导出目标说明 | 是 | 说明接收系统、责任团队和目的 |
| `export_type` | 内置导出类型 | 是 | 从平台支持的 `webhook`、`syslog` 或未来系统发行版内置类型中选择；不是第三方 Package 身份 |
| `configuration` | 非敏感目标配置 | 是 | 由 `export_type` 的内置 Schema 定义并为每个字段提供名称和说明；不得保存 Secret；创建后不可原地修改 |
| `credential_binding_id` | 可选凭证绑定身份 | 否 | 仅当前导出类型明确需要鉴权时出现；用途和 subject 必须匹配当前目标；改绑另一个 ID 需要替换目标 |
| `enabled` | 是否启用外部导出 | 是 | 只控制外发，不控制本地记录或查询 |
| `revision` | 配置修订号 | 是 | CAS；不是语义版本 |
| `configuration_digest` | 规范化投递配置摘要 | 是 | 只覆盖导出类型、规范化非敏感目标配置和 CredentialBinding 身份；名称、说明与 enabled 不改变该摘要 |

首版不提供外发事件过滤字段。启用目标时导出该 Scope 内全部仍保留的 AuditEvent。目标创建后只允许原地更新 name、description 和 enabled；改变 export type、configuration 或 CredentialBinding ID 必须创建新目标。CredentialBinding 内部 Secret 轮换不改变目标身份。

### 5.2 内部 AuditExportPort 与投递 checkpoint

`AuditExportPort` 是平台内部窄接口，只提供两个操作；它不作为 ExtensionPackage API 公开：

| 操作 | 中文作用 | 输入 | 输出与约束 |
| --- | --- | --- | --- |
| `probe` | 测试外部目标 | 内联目标候选、脱敏测试载荷、超时 | 类型化成功/失败；不保存目标、不发送真实 AuditEvent |
| `deliver_batch` | 异步投递一批事件 | `AuditExportBatch` | 返回最高连续确认游标或类型化失败；必须按 target+delivery ID 幂等 |

`AuditExportBatch` 至少包含：

| 英文字段 | 中文字段释义 | 关键约束 |
| --- | --- | --- |
| `audit_export_target_id` | 导出目标身份 | 必须匹配当前启用目标 |
| `target_configuration_digest` | 目标投递配置摘要 | 固定导出类型、非敏感目标配置和 CredentialBinding 身份，防止旧请求误投到另一个目标 |
| `delivery_id` | 批次投递幂等身份 | Retry 原样复用 |
| `start_cursor / end_cursor` | 本批起止游标 | 对应本地稳定 AuditEvent 顺序 |
| `events[]` | 脱敏审计事件列表 | 保持本地顺序；不含 Secret、Prompt、私有推理或完整工具日志 |

系统 checkpoint 只保存目标身份、最后连续确认游标、重试计划、重试次数、最近成功时间和脱敏错误码。它是可恢复运维状态，不是 AuditEvent、证据权威、业务聚合、Version 或外部系统镜像。导出目标故障不影响本地事务和查询；恢复后从 checkpoint 继续。成功投递本身不递归产生 AuditEvent。

### 5.3 Audit Explorer 查询合同

平台只提供一个统一 Audit Explorer。Authorization、Admission、Credential use、Decision 和状态写入不是五套页面或五套审计模型，而是通过 `category`、`action`、`subject` 和因果字段查询同一 AuditEvent 来源。

`QueryAuditEvents`（查询审计事件）输入如下：

| 英文字段 | 中文字段释义 | 必需 | 关键约束 |
| --- | --- | --- | --- |
| `time_range` | 查询时间范围 | 是 | 使用治理策略提供的默认与最大范围；不得执行无界扫描 |
| `project_id` | Project 身份筛选 | 否 | 只能查询当前 Principal 在当前 Scope 内获准读取的 Project |
| `category` | 审计类别筛选 | 否 | 例如 authorization、admission、credential、decision、state-write |
| `action` | 操作名称筛选 | 否 | 使用稳定 Application Use Case 或治理动作名 |
| `outcome` | 结果筛选 | 否 | allowed、denied、succeeded、failed |
| `principal_id` | 操作者身份筛选 | 否 | 由关联 RequestContext 提供；不允许借此跨 Scope 枚举身份 |
| `subject` | 被操作对象筛选 | 否 | 使用精确 `SubjectKey` 的 kind、id 和可选 digest |
| `correlation_id` | 跨模块关联身份筛选 | 否 | 只用于追踪，不作为授权依据 |
| `cursor` | 分页游标 | 否 | 服务端不透明值，固定到上一页最后一条的时间和事件身份 |
| `page_size` | 每页数量 | 否 | 受服务端上限约束 |

结果按 `occurred_at DESC, audit_event_id DESC` 稳定排序，返回 `items[]`（审计事件摘要列表）和 `next_cursor`（下一页游标）。新事件并发写入时不得造成已翻页结果重复或跳过。

`GetAuditEventDetail`（获取审计事件详情）返回瞬时 `AuditEventDetailView`（审计事件详情聚合视图）：

| 英文字段 | 中文字段释义 | 关键约束 |
| --- | --- | --- |
| `event` | 当前不可变审计事件 | 本地 AuditEvent 是锚点和权威来源 |
| `request_context_summary` | 脱敏后的请求上下文 | 同时展示 persisted `request_context_id` 与外部 `request_id`、Principal、Scope、来源、causation/correlation，不返回认证材料 |
| `evaluated_controls[]` | 本次评估过的治理控制 | Authorization 展示 Policy ID/revision/digest 和匹配 grant；采用 Package 的其他类别展示 Provider 精确 package release/digest 和结果 |
| `related_governance_records[]` | 相关治理支持记录 | 可包含精确 Authorization、Admission、Credential-use 记录；只读且按权限脱敏 |
| `related_subjects[]` | 相关业务对象链接 | 指向精确 Command、Decision、Run、Deployment 或其他 Subject，不复制其生命周期 |

`AuditEventDetailView` 只在查询时组装，不持久化、不分配业务身份，也不建立 AuditEvidence、AuditSession 或第二套因果链表。资源详情页的“查看审计记录”只携带预设筛选条件跳转到同一 Audit Explorer。

### 5.4 权限、脱敏与导出

- `audit.read`（读取审计）按当前 Scope 和可选 Project 授权；拥有业务资源读取权不自动等价为拥有审计读取权；
- `platform.operations.read`（读取平台运行状态）只允许读取当前 Scope 的瞬时健康、活动告警和容量视图；它不自动授予 Kubernetes 原始诊断、Audit、Credential、业务正文或修复命令权限，服务端必须逐项脱敏；
- Secret、Token、Authorization Header、Credential 明文、Prompt、私有推理和完整工具日志永不进入查询结果；Credential 证据只展示受控 Binding 身份、`sha256:` 指纹别名或摘要、受限目标标识和 `succeeded|failed` 结果；含凭证形态、控制字符或未闭合结果的值在 Validate 与 JSON 序列化边界拒绝；
- 普通成功的 `QueryAuditEvents` 与 `GetAuditEventDetail` 不再生成新的 AuditEvent，避免“读取审计产生无限审计噪声”；拒绝的审计访问必须记录；
- `ExportAuditEvents`（导出审计事件）复用同一筛选、授权和脱敏规则，只支持受控大小的 CSV 或 JSONL 流式导出；超过上限时要求缩小时间范围，不创建 Export 聚合、后台任务或状态机；
- 导出和策略定义的高敏感字段展示分别要求 `audit.export`（导出审计）和 `audit.sensitive.read`（读取高敏感审计元数据），并产生独立 AuditEvent；即使具有这些权限也不能读取 Secret 明文；
- 外部接收端只消费本地已提交事件。Audit Explorer、查询 API 和导出都不从 SIEM、Webhook 或 Syslog 反向重建平台审计历史。

## 6. CredentialBinding、内部 CredentialStore 与 CredentialBroker

Credential 管理的核心不是“保存一段字符串”，而是把 Secret 材料、可读配置和使用授权分开：

- `CredentialStorePort` 是平台内部保存、轮换和销毁 Secret 材料的窄接口；
- `CredentialBinding` 是平台可引用的稳定授权对象，只保存元数据和内部 Store 产生的不透明 handle；
- `CredentialBroker` 在一次明确用途内校验并解析短期使用权；
- Model Provider、MCP、GitHub Repository、Deployment Target 等业务配置只保存 `credential_binding_id`。

首版安装时自动启用一个内置 CredentialStore。普通用户和治理管理员都只管理 CredentialBinding，不配置或选择存储后端，也不编辑 CredentialPolicy；首版不存在第三方凭证存储 ExtensionPackage、后端 Catalog、默认后端选择、跨后端迁移或回退逻辑。

`CredentialStorePort` 的固定操作如下：

| 操作 | 中文作用 | 输入 | 输出与约束 |
| --- | --- | --- | --- |
| `store` | 保存新材料 | 已授权 Binding 创建上下文、类型、Secret、类型元数据 | 不透明 handle、fingerprint、可选 expires_at、脱敏验证摘要；失败清理临时材料 |
| `validate` | 类型化验证 | handle、类型与受控目标上下文 | 脱敏结果；普通 API Key 等可要求由目标 Connector Probe |
| `resolve` | 解析一次短期使用权 | handle、用途、subject、lease 约束 | 仅供 Broker/Materializer 消费的短期 lease 或材料化描述；不向普通调用方返回明文 |
| `rotate` | 写入并切换新材料 | 旧 handle、新 Secret 或类型专用重新授权结果 | 新 handle、fingerprint、可选 expires_at、验证摘要；失败保留旧材料 |
| `revoke` | 撤销材料 | handle、幂等身份 | 类型化撤销结果 |

该 Port 不提供 Secret 枚举、通用取值、导出、授权判断或后端选择，也不作为 ExtensionPackage API 公开。

### 6.1 CredentialBinding

`CredentialBinding` 是完整授权对象，不是普通创建表单。普通用户只输入名称、说明、Credential 类型、该类型需要的 Secret 材料、类型专用非敏感元数据和条件性的到期时间；Scope、Project、用途、subject、handle、fingerprint 和状态由创建入口、当前上下文、内部 CredentialStore 或系统产生。

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用与约束 |
| --- | --- | --- | --- | --- |
| credential_binding_id | 是 | 凭证绑定稳定身份 | system | 业务配置引用该 ID；Secret 轮换不要求重写所有引用 |
| scope_id | 是 | 所属治理范围 | context | 从当前 Settings Scope 继承，决定管理、查看元数据和使用授权边界 |
| project_id | 否 | 可选 Project 限定 | context | 从 Project 内创建时继承；存在时不能跨 Project 使用 |
| display.name | 是 | 凭证名称 | user | 提供可识别名称，不展示 Secret |
| display.description | 是 | 凭证说明 | user | 说明服务的外部系统和用途，避免只靠 ID 猜测 |
| credential_type | 是 | 凭证类型 | user | 例如 `api-key`、`github-app`、`github-user-token`、`github-fine-grained-pat`、`ssh-key`、`oauth`、`bearer-token`、`header-set` |
| secret_handle | 是 | 内部 CredentialStore 拥有的不透明句柄 | system | Platform Core 其他模块、Web、Agent、Extension 不解释其格式，也不能据此枚举 Secret |
| allowed_usages[] | 是 | 允许用途列表 | context / system | 从创建入口推导，例如模型连接得到 `model-provider-api`，MCP Header 得到 `mcp-http-auth` |
| subject_constraints[] | 否 | 可使用对象约束 | context / system | 默认绑定当前 Model Provider Connection、MCP Server、Repository、环境或其他精确 `SubjectKey`；GitHub App 等类型可以由类型处理器返回只读范围 |
| expires_at | 否 | 凭证或授权过期时间 | user / system | Credential 类型本身有到期时间时读取或要求填写；过期后 Fail Closed |
| fingerprint | 是 | 脱敏指纹 | system | 只保存哈希、Key ID 或末四位，用于识别，不可恢复 Secret |
| status | 是 | 当前管理状态 | system | `active`、`disabled`、`revoked` 或 `expired`；不能用多个布尔值拼装 |
| revision | 是 | CAS 修订号 | system | 轮换、停用、撤销或迁移成功后递增；不是语义版本，也不由用户填写 |
| created_by / created_at | 是 | 创建人和创建时间 | context / system | 用于审计，不授予后续权限 |

创建入口负责提供最小权限上下文：

| 创建入口 | 系统推导的 `allowed_usages[]`（允许用途） | 系统推导的 `subject_constraints[]`（对象约束） |
| --- | --- | --- |
| Model Provider 表单 | `model-provider-api` | 当前 ModelProviderConnection |
| MCP Streamable HTTP Header | `mcp-http-auth` | 当前 McpServerDefinition |
| MCP stdio Environment | `mcp-stdio-env` | 当前 McpServerDefinition |
| 私有 Skill GitHub 导入 | `skill-import`、`github-repository-read` | 当前 GitHub Repository |
| Repository 读写配置 | 用户选择的只读或读写动作映射为 `github-repository-read` / `github-repository-write` | 当前 Repository |
| Deployment Target | `deployment` | 当前目标环境 |

独立 Credentials 页面负责查询、轮换、禁用、撤销和查看引用，不提供通用 Scope、用途或 subject 扩权编辑器。需要用于另一个目标时，从该目标上下文创建新的最小权限 Binding；具有自包含范围协议的 GitHub App 等类型可以在类型化向导中创建，但范围由受控类型处理器读取并只读展示。

CredentialBinding 是稳定授权别名。内部 CredentialStore 可以在同一 Binding 下保存多个 Secret 材料修订并原子切换当前材料；每次轮换形成不可变 `CredentialRotationRecord` 和 AuditEvent。这样，API Key、GitHub App Private Key 或 PAT 轮换时，ModelProviderConnection、McpServerDefinition 和 RepositoryBinding 不需要重建，但正在执行的短期 lease 不会被延长或扩大权限。系统发行版升级必须保持 handle 兼容，或通过受控数据迁移完成转换；用户不参与后端选择或迁移。

### 6.2 Secret 写入与读取边界

创建或轮换流程固定为：

1. Web 从当前 Scope、Project 和调用入口构造受控创建上下文，只要求用户提交名称、说明、Credential 类型、该类型需要的 Secret、类型专用非敏感元数据和条件性的到期时间；表单不回填 Secret，也不提供“显示原值”。
2. Platform Core 执行 Authentication、Authorization 和输入校验，根据入口推导最小 `allowed_usages[]` 与 `subject_constraints[]`。
3. 内部 CredentialStore 把 Secret 写入受控存储，返回 `secret_handle` 与脱敏 fingerprint。
4. Platform Core 只把 CredentialBinding 元数据、handle 和 AuditEvent 写入产品数据库。
5. 后续 Query 只返回名称、类型、派生用途、状态、过期时间、fingerprint、最后轮换时间和适用的验证结果。

Credential 页面不提供伪通用测试。GitHub App、云身份等有自包含验证协议的类型可以由受控类型处理器验证；普通 API Key、MCP Header 等由实际 Model Provider、MCP 或目标系统 Probe 验证。无论验证入口在哪里，服务端都必须脱敏，且产品数据库、Artifact Storage、Prompt、Event、Observation 和日志不得保存 Secret 明文。

### 6.3 默认存储

Compact 安装自动启用内置 CredentialStore，使用平台主密钥进行信封加密；External Cluster 可使用系统发行版内受控的 Kubernetes Secret 实现，并要求启用 encryption at rest、最小 RBAC 与审计。两种部署形态都通过同一内部 CredentialStorePort、CredentialBroker 与 Audit 路径运行。

普通用户和治理管理员都不选择存储后端。未来若 Vault/KMS 集成成为明确生产需求，应先作为系统发行版内部实现验证接口、安全隔离和数据迁移；只有内部合同稳定且存在真实第三方生态需求时，才另行评估公开 Credential Storage Extension API。

### 6.4 CredentialBroker

| 操作 | 输入 | 输出 | 约束 |
| --- | --- | --- | --- |
| resolve | 已授权 credential_binding_id、当前 subject、调用范围和明确用途 | 短期 credential_handle / lease | 校验 Scope、Project、用途、subject、状态和过期；不返回 Secret 列表 |
| materialize | 短期句柄和目标 Connector、Runtime 或 Workspace | 进程内请求头、临时环境变量、只读文件或 Workload Identity | 只进入当前调用边界，结束后清理，不进入持久化正文 |
| revoke | 短期句柄或 credential_binding_id | 撤销结果 | 幂等且可审计；撤销 Binding 阻止新解析并尽力终止活动 lease |

CredentialBroker 不提供 `listSecrets` 或通用 `getSecret`，也不允许 Extension、Agent 或浏览器读取明文。Platform 侧 Model Provider Connector、Skill GitHub Importer 等 Adapter 可以在受控进程内消费材料，但只能请求当前 Use Case 声明的用途；每次解析、使用、失败、轮换和撤销都产生 AuditEvent。

### 6.5 GitHub Credential Profile

GitHub 接入按以下优先级管理：

1. **GitHub App**：组织级自动化和长期集成的默认选择。用户在 GitHub 创建 App、选择最小 Repository/Organization Permissions、安装到明确 Repository，并把 `app_id`、`installation_id` 和 API Base URI 作为非敏感元数据填写到平台；下载的 Private Key 只上传一次给内部 CredentialStore。Broker 使用 Private Key 生成短期 App JWT，再换取 Installation Access Token；实际 GitHub API 或 Git 操作只使用该短期 Token，并以 Provider 返回的 `expires_at` 控制缓存和续期。
2. **GitHub App User Access Token**：需要明确代表某个用户执行操作时使用 OAuth 授权与 Refresh Token；用户身份和 App 安装权限共同限制实际权限。Broker 应在 Provider 内部根据返回的 `expires_at` 刷新短期 User Access Token，而不是把短期 Token 或某个外部服务当前的固定有效时长暴露给业务配置。
3. **Fine-grained Personal Access Token**：个人或小团队的简化回退。用户在 GitHub 创建带到期时间、最小 Repository 范围和最小 Permission 的 Token，再一次性粘贴到平台；不作为大型组织长期自动化的默认方案。
4. **SSH Deploy Key**：只需要单 Repository Git 传输时可选；私钥进入内部 CredentialStore，公钥由用户配置到目标 Repository，写权限必须显式开启。

`credential_type = github-app` 的非敏感元数据至少包含：

| 英文字段 | 必需 | 中文字段释义 | 产生方 | 值的作用 |
| --- | --- | --- | --- | --- |
| github_app_id | 是 | GitHub App 身份 | user | Broker 用它生成 App JWT |
| github_installation_id | 是 | App 安装身份 | user | 决定 Token 可以访问哪个 App Installation |
| github_api_base_uri | 是 | GitHub API 基础地址 | preset / user | GitHub.com 使用预设；企业实例才要求用户修改，不包含 Token |
| repository_selection | 是 | Repository 范围摘要 | provider | 测试连接时从 GitHub Installation 读取 All/Selected 与实际 Repository 范围，用户只读确认 |
| granted_permissions | 是 | 已授予权限摘要 | provider | 测试连接时从 GitHub 返回值读取，用于预检和审计，用户不能在平台中扩大 GitHub 侧权限 |

普通 GitHub App 表单不要求用户手工抄写 Repository 范围或 Permission 列表。CredentialStore/Broker 在最终创建 Binding 前执行受控连通性测试，读取并持久化脱敏摘要；若 App 未安装、Installation 不匹配或权限不足，创建命令失败并清理本次临时材料，不留下一个需要用户理解的半成品 Binding。

说明性 YAML：

```yaml
credentialBindingId: credential-github-app-engineering
# credentialBindingId：业务配置长期引用的稳定凭证绑定身份。

scopeId: local-organization
# scopeId：从当前 Settings Scope 继承的治理范围，普通表单不重复填写。

projectId: snake-game
# projectId：从 Project 内创建时继承的可选限定；存在时不能跨 Project 使用。

display:
  # display：页面可读信息，不包含 Secret。
  name: Engineering GitHub App
  # name：用户可读凭证名称。
  description: 用于 snake-game Repository 的 Skill 导入、PR 和 Release 自动化
  # description：说明具体系统与用途，避免仅凭 ID 猜测。

credentialType: github-app
# credentialType：决定需要收集的非敏感元数据和 Secret 类型。

secretHandle: opaque-store-handle
# secretHandle：内部 CredentialStore 返回的不透明句柄；由系统生成，平台其他模块不得解释或展示其内部格式。

allowedUsages:
  # allowedUsages：创建入口推导出的明确用途，普通用户不手工编写列表。
  - skill-import
  - github-repository-read
  - github-repository-write

subjectConstraints:
  # subjectConstraints：创建入口推导出的精确外部对象约束。
  - kind: github-repository
    # kind：约束对象类型。
    id: example/snake-game
    # id：允许访问的精确 Repository 身份。

githubAppId: "123456"
# githubAppId：GitHub App 身份；不是 Secret。

githubInstallationId: "987654"
# githubInstallationId：GitHub App 安装身份，决定已安装 Repository 范围。

githubApiBaseUri: https://api.github.com
# githubApiBaseUri：GitHub API 基础地址，不包含 Token。

status: active
# status：系统维护的当前凭证管理状态；一个枚举表达，不由多个布尔值推导。

revision: 3
# revision：系统维护的 CAS 修订号；轮换、停用或撤销成功后递增，不是用户管理的版本号。
```

GitHub App Installation Token 是短期材料，Broker 应按调用按需生成、严格按 Provider 返回的 `expires_at` 控制缓存并在审计中记录 Token 身份摘要，不把 Token 回写 CredentialBinding。GitHub App Private Key 不依赖短期 Token 的自动过期机制，因此平台必须支持添加新 Key、验证、切换当前材料并撤销旧 Key 的轮换流程。Fine-grained PAT 到期或撤销后，用户在同一 CredentialBinding 下提交新材料即可完成轮换；旧材料立即停止用于新调用。

私有 Skill 导入、Repository Clone、创建 PR 或 Release 等调用流程一致：业务对象提供 `credential_binding_id` 和明确用途 → Authorization → CredentialBroker 校验范围 → GitHub App 动态签发 Token 或解析 PAT/SSH Key → 只向当前 Importer/Executor 临时注入 → 完成后清理 → 写入 AuditEvent。Credential 不能直接推进 Workflow，只能让已授权操作具备访问外部系统的能力。

## 7. ExecutionSecurityRequirements

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| isolation_level | 是 | process、container、strong-sandbox 等最低隔离 |
| network_mode | 是 | denied、restricted 或 allowed |
| allowed_endpoints[] | 视模式 | 允许访问的精确端点或类别 |
| credential_binding_ids[] | 否 | 允许使用的 CredentialBinding 身份 |
| data_classification | 是 | 执行数据等级 |
| dedicated_placement | 是 | 是否要求专用节点或隔离域 |
| retention_policy | 是 | Workflow 创建时求值后的保留要求、来源 Policy ID/revision 和 digest |

RuntimeDriver 或内置 Workspace 实现无法证明满足 Required 能力时，执行创建必须失败，不能静默降级。

## 8. UsageRecord

`UsageRecord` 是一次实际使用事实，不是预聚合 Dashboard 行。一个来源事件只形成一条幂等记录；Project、Workflow、Agent 或模型汇总全部由查询侧计算。

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| usage_record_id | 是 | 一次 Usage 记录身份 | 由来源事件幂等生成 |
| scope_id / project_id | 是 | 治理与项目归属 | 聚合不改变原始记录 |
| workflow_key / work_unit_id / work_unit_iteration | 视来源 | Workflow、WorkUnit 与业务返工轮次归属 | 父子 Workflow 只在实际发生位置记录一次 |
| run_kind / run_id | 视来源 | `agent\|component\|human` Run 归属 | human Run 没有模型使用时不得伪造零 Token 记录 |
| agent_definition_id / responsibility | Agent 来源必需 | 实际 Agent 定义与责任归因 | 从当前 Run 固定事实补充，不按当前 Project 配置回查 |
| runtime_driver | Runtime 来源必需 | 实际 RuntimeDriver 精确 ExtensionPackage release | 使用精确 ID、SemVer 与 digest |
| usage | 是 | 一个 tagged union 使用载荷 | `model-call\|tool-call\|runtime\|component\|resource\|external-service` 六选一，不使用大量 Optional 字段模拟类型 |
| cost_details[] | 否 | 成本明细 | 每项包含 category、Decimal amount、currency、source 与可选 pricing basis；不同币种不直接求和 |
| source_event_id | 是 | 权威来源事件身份 | 支持追溯与去重 |
| observed_at | 是 | 观察时间 | 不作为唯一幂等键 |

### 8.1 Tagged Usage Payload

| `usage.kind` | 中文含义 | 最小内容 |
| --- | --- | --- |
| `model-call` | 一次模型调用 | model call ID、Provider connection、请求/实际模型、API type、开始/首输出/完成时间、Token 总数与可用明细、retry、outcome、error、finish reasons |
| `tool-call` | 一次 Tool/MCP 调用 | tool call ID、runtime-native/MCP/external 类型、Tool 名称、条件性的 MCP Server ID、开始/完成时间、outcome 与 error |
| `runtime` | 一次 Runtime 执行用量 | Runtime 启动、就绪、结束时间与 outcome；不重复模型或 Tool 子调用 Token |
| `component` | 一次 Extension-backed ComponentRun 使用量 | 精确 ExtensionPackage release、调用单位、数量和 outcome |
| `resource` | 一次可证明的资源计量 | CPU/GPU/内存/存储等数量与明确单位；ResourceRequirements 不能当作实际使用量 |
| `external-service` | 一次外部服务计量 | 服务身份、计量单位、数量和 outcome |

#### ErrorCode 与失败语义

`model-call` 与 `tool-call` 的 `error_code` 只允许以下闭集值：`error`、`unknown`、`timeout`、`rate-limited`、`unauthorized`、`invalid-request`、`authentication-failed`、`validation-failed`、`content-filtered`、`overloaded`、`transport-error`。Provider、网络库或外部工具返回的原始错误文本不得写入 Usage payload、Summary 或 JSON；实现必须先将其分类为上述代码，无法安全分类时留空并以 `unknown_fields` 的 `field=error_code` 和 `reason=not_reported|unsupported|unverifiable` 表示未知/不可验证，或使用闭集的 `unknown` 代码表示已明确报告但类别未知。`error_code` 只能与 `outcome=failed` 同时出现；成功调用必须省略该代码，失败调用若没有安全代码则保留结构化未知原因。

模型 Token 总量和细分量具有包含关系：`input_total` 可以包含 cache read/write，`output_total` 可以包含 reasoning。查询侧只能在字段完整时计算互斥桶，不能把总量与子集直接相加。缺失、Provider 不支持或无法验证的字段使用 `unknown`，不能写 0；每个 `unknown=true` 的分析指标还必须携带 closed `unknown_reason`（`not-reported`、`unsupported`、`unverifiable`、`insufficient-data` 或 `redacted`），已知值不得携带该字段。

`cost_details[].source` 固定为 `provider-reported|platform-calculated|unavailable`。Provider 实际报告优先；平台计算必须把当时使用的模型、费率、币种、生效时间和摘要内联固定为 `pricing_basis`，历史成本不得使用当前价格重算。首版不建立 PricingVersion、Budget、Reservation 或自动暂停状态机。

### 8.2 查询与聚合

- Usage 可按 Scope、Project、Workflow、WorkUnit、iteration、Run、AgentDefinition、责任、RuntimeDriver、Provider connection、请求模型、实际模型、usage kind 和时间聚合；
- Agent 效率、Review Reject、返工次数、成功 WorkUnit、WorkflowOutcome 等指标在查询时关联不可变 Workflow/Run/Review/Gate 事实，不复制进 UsageRecord；
- 父级 `exclusive` 只统计自身实际记录，`inclusive` 在查询时包含子 Workflow，二者必须同时标明且不能重复计量；
- 原始明细通过 `source_event_id` 下钻到精确 AgentRunEvent/来源记录；Dashboard、排行、时间序列和覆盖率都是可重建读模型；
- OpenTelemetry 字段可以作为 RuntimeDriver 映射参考，未来也可由外层 OTLP Export Adapter 导出；OTel Span/Metric、Collector 和 OTLP 类型不进入领域模型，也不是本地 Usage 查询权威。

## 9. Workflow Controller Partition 与企业演进

- Project ID 是默认 Controller 分区键；
- 首版可运行一个 Active Controller，未来多 Active 分片不改变领域模型、幂等、授权、Admission 或 Audit 语义；
- 身份、授权、Execution Admission、Audit、Credential 和 Execution Security 使用独立窄 Port，不合并为万能 Governance Extension；
- AuthenticationDriver 使用统一 ExtensionPackage、ExtensionCatalog 和 `authentication.oac.dev/v1`；Audit Export、Credential Storage、AuthorizationEngine 与 ExecutionAdmissionEngine 不属于公开 Extension；
- 未来跨 Scope 授权必须通过显式 Delegation，不得由 Project ID、Namespace 或外部 Token 隐式推导；
- 企业网络隔离、资源、配额或预算系统可以通过内部窄端口贡献类型化准入事实；审计外发与凭证存储由系统发行版内置实现承接。任何外部系统都不能替换 ExecutionAdmissionEngine、本地 AuditEvent 或统一 Audit Explorer，也不能让强制执行变成可选步骤。

## 10. Resource Deletion Protection

资源处置 Query 可以使用以下瞬时合同，帮助 Web、API Client 和 `oactl` 统一解释归档与删除资格；这些字段不形成持久化 Governance 聚合：

| 英文字段 | 中文字段释义 | 最小内容与约束 |
| --- | --- | --- |
| `allowed_actions[]` | 当前允许操作集合 | 由资源所属模块的生命周期/引用规则与 Governance 的授权、Retention、Legal Hold 结果求交；客户端只用于展示 |
| `reference_summary` | 引用摘要 | 按业务引用类型给出数量和可授权下钻的精确 subject；由资源模块产生，不由 Governance 猜测 |
| `deletion_blockers[]` | 删除阻塞原因 | 每项包含 `code`（稳定错误代码）、`message`（中文说明）、`subject`（受影响主体）、`source=reference\|authorization\|retention\|legal-hold`（来源）和可选 `remediation`（修复建议） |

删除类命令的共同输入至少包含精确 subject、资源自己的 expected revision/CAS 条件、RequestContext 和 idempotency key。资源所属 Application Use Case 在事务内重新读取引用并调用以下治理判断：

| 判断 | 中文作用 | Fail Closed 语义 |
| --- | --- | --- |
| `AuthorizeResourceDisposition` | 校验当前 Principal 是否可归档、恢复或删除该精确资源 | Unknown/Denied 都拒绝 |
| `EvaluateRetention` | 判断当前资源类型、数据分类和已满足保留期限 | 未知 Policy、未到期或要求仅保留时拒绝硬删 |
| `EvaluateLegalHold` | 判断资源或 Scope 是否受保全、调查或安全事件冻结 | Unknown/Active 都拒绝硬删 |

Governance 返回约束事实，不直接执行资源删除，不提供 `DeleteResource`，也不保存 `DeletionRequest`、`DeletionVersion` 或回收站状态。删除成功后，本地 AuditEvent 至少保留原 subject identity、删除前 digest/revision、操作者、发生时间、引用检查摘要、Policy/hold 来源和 outcome；允许清理正文时仍保留最小 tombstone。该 tombstone 是既有 AuditEvent/历史索引中的字段集合，不是独立对象或表。外部审计接收端继续只是导出目的地，不能成为删除证明的唯一权威。

本合同不要求每个对象支持 archive：Project、逻辑 Agent 和终态 Workflow 使用各自定义的归档语义；Component 使用 Disable/Revoke/受保护卸载，Credential 使用 Rotate/Disable/Revoke，Provider/MCP 配置按其引用规则选择 Disable 或删除。不得为了统一 UI 给所有资源复制 `archived_at`、`disposition` 或删除状态机。

# 09. 治理与安全详细设计

> 上级文档：[云原生 Open Agent Cluster 概要设计](../open-agent-cluster-overview-design.md)
> 契约参考：[安全、治理与企业扩展参考](../../references/governance/security-and-enterprise.md)

本文定义 Usage、认证、授权、Execution Admission、审计、凭证和执行安全。认证协议通过公开 Authentication SDK 扩展；Audit Export 与 Credential Storage 首版使用平台内部 Port 和内置实现。本地 AuditEvent/Audit Explorer、Authorization 与 Execution Admission 始终由 OAC 内置能力负责。所有强制检查点、调用顺序和 Fail Closed 语义都不能被替换或绕过。

![Governance Enforcement Chain](../../assets/diagrams/platform/governance-enforcement-chain.svg)


## 1. RequestContext

Authentication 成功后，所有外部请求、用户 ApplicationCommand、Decision 和内部 LifecycleAction 都携带类型化 RequestContext。认证前的外部身份材料只是 AuthenticationDriver 输入，不是缺少 Principal 的半成品 RequestContext：

- PrincipalIdentity；
- ScopeIdentity；
- request identity；
- causation identity；
- authentication source；
- policy ID、revision 与 digest；
- correlation information。

RequestContext 是治理上下文，不是第二套 Workflow 状态。

Generic Web 的本地凭证或联邦认证响应，与外部 Agent/自动化通过 `oactl` 使用的用户 Token、服务身份或 Workload Identity，都只是 AuthenticationDriver 输入。Driver 产生 AuthenticatedSubject，Governance 解析 ExternalIdentityLink 后才形成 Principal 与相同结构的 RequestContext，并进入同一 Authorization、Execution Admission 和 Audit 链；客户端类型不能成为放宽权限或选择另一套 Use Case 的依据。要求 human Principal 的人工 Decision、Review 或 Acceptance 不允许由普通服务身份经 CLI 冒充提交。OAC 内部执行 Agent 使用 run-scoped AgentWorkChannel/`oacok`，不获得 `oactl` 的 Platform Principal 或管理 Token。

## 2. 强制治理链路

    Authentication
    → RequestContext
    → Authorization
    → Execution Admission（执行创建路径）
    → Credential Resolution（需要凭证时）
    → Platform Core Command Use Case / Workflow Controller Action Use Case / direct Deployment dispatch
    → Platform Core typed Workflow Resource Command API（需要写 CR 时）
    → Local AuditEvent
    → Optional internal Audit Export

治理边界必须保持独立：

- Authentication 只确认“是谁”；
- Authorization 判断“是否允许对当前 subject 执行当前 Use Case”；
- Execution Admission 判断“已授权执行现在是否允许创建资源”；
- Credential Broker 解析显式授权的凭证引用；
- Audit 证明发生了什么。
不能合并成一个大量 Optional 字段的万能 Governance Extension。

## 3. Authentication

首版提供内置 Local AuthenticationDriver，并通过 `authentication.oac.dev/v1` 为 OIDC、SAML、LDAP 和其他企业身份协议提供同一扩展边界。SSO 是用户看到的一次登录体验，不是一个需要写入平台核心的协议枚举；OIDC 或 SAML 等具体协议由 Driver 实现。

AuthenticationDriver 是实现 Authentication SDK 的 ExtensionPackage，不是某个企业身份源的配置实例。它：

- 只验证一种协议的身份材料并产生标准化 `AuthenticatedSubject`；
- 不直接授权；
- 不写领域状态；
- 不创建 Workflow 和执行资源；
- 不依赖 Project、Solution、Workflow、Web 框架、ORM 或本地用户表；
- 通过统一 ExtensionManager 安装，并运行 Authentication SDK 自带接口检查；调用时记录精确 package release/digest。

账号初始化、邀请、身份绑定、会话、恢复和授权由 Governance Use Case 承接，不塞入 AuthenticationDriver。Driver 不能因为“当前还没有用户”而把第一个访问 Web 的人自动升级成管理员，也不能把外部 claims 或 group 直接解释成平台权限。

### 3.1 AuthenticationDriver 与 AuthenticationMethod

`AuthenticationMethod`（认证方式）是管理员配置的一个实际登录入口。多个 AuthenticationMethod 可以复用同一个精确 AuthenticationDriver ExtensionPackage，例如同一个 OIDC Driver 同时连接企业生产 IdP 和合作伙伴 IdP。它是普通可变配置，使用稳定身份、CAS `revision` 和配置摘要；编辑不会创建 `AuthenticationMethodVersion`。

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_method_id` | 认证方式稳定身份 | 系统生成；登录、身份绑定、会话和审计都使用该身份，不随普通配置修改而变化 |
| `name` | 认证方式名称 | 管理员填写；展示在管理页和登录页，例如“公司账号” |
| `description` | 认证方式说明 | 管理员填写；解释适用人群和作用，避免用户只看到协议名猜测用途 |
| `authentication_driver` | AuthenticationDriver 精确 ExtensionPackage | 从 ExtensionCatalog 中实现 `authentication.oac.dev/v1`、Active 且通过接口检查的 Driver 中选择；包含稳定身份、精确 SemVer 和 artifact digest |
| `interaction_kind` | 登录交互类型 | 由 Driver 声明并由平台支持；使用 `credentials-form`（凭证表单）、`browser-redirect`（浏览器跳转）或 `non-browser`（非浏览器身份）等有类型值，不把 OIDC/SAML 当成 UI 流程硬编码 |
| `configuration` | 非敏感认证配置 | 按 Driver 提供且带字段说明的 Schema 校验，例如 issuer、directory address 或 client ID；不是平台定义的任意字段包 |
| `credential_binding_ids[]` | 敏感凭证绑定身份列表 | 可选；Client Secret、Bind Password、证书私钥等只通过 CredentialBinding 提供，不能写入 `configuration` |
| `enabled` | 是否允许新认证 | 管理员显式控制；禁用后不再出现在可用入口中，也不能开始新 AuthenticationFlow |
| `revision` | 配置修订号 | 系统维护的 CAS 值，防止管理员并发覆盖；不是语义版本 |
| `configuration_digest` | 规范化配置摘要 | 系统根据非敏感配置、CredentialBinding 身份和精确 Driver 计算，用于测试、激活和审计防止陈旧结果 |

普通连接参数、证书或 Client Secret 绑定可以在同一 AuthenticationMethod 上更新；会改变身份命名空间或 subject 解释的内容不能原地修改。切换协议、issuer/directory 身份域或不兼容的 subject 规范时必须创建新的 AuthenticationMethod，再迁移或重新建立 ExternalIdentityLink。已经被 Link、Session 或 Audit 引用的方法不能硬删除，只能禁用并归档；兼容 Driver 升级必须先通过 Authentication SDK 的 identity-stability 检查。

AuthenticationDriver 对 Governance 暴露四个窄操作：

| 操作 | 中文用途 | 输出与边界 |
| --- | --- | --- |
| `DescribeAuthenticationMethod` | 生成认证方式展示描述 | 返回名称、说明和平台支持的有类型交互描述；不能返回 Secret 或授权结论 |
| `StartAuthentication` | 开始一次认证交互 | 基于精确 AuthenticationMethod 和一次性 AuthenticationFlow 返回凭证表单挑战、浏览器跳转或非浏览器挑战 |
| `CompleteAuthentication` | 完成认证协议 | 校验响应并返回 `AuthenticatedSubject`；不能直接创建 Principal、Session 或权限 |
| `ProbeAuthenticationMethod` | 测试认证配置 | 返回脱敏的连接、协议和配置诊断；测试成功不自动激活认证方式或创建业务 Session |

登录页使用瞬态 `AuthenticationMethodDescriptor`（认证方式展示描述），只展示当前认证入口允许且已启用的方法：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_method_id` | 认证方式身份 | 用户选择后提交给统一认证入口；不能由前端拼接 Provider URL |
| `name` | 展示名称 | 来自 AuthenticationMethod |
| `description` | 展示说明 | 来自 AuthenticationMethod，必须随名称一起显示 |
| `interaction` | 有类型交互描述 | `credentials-form` 携带受限输入 Schema，`browser-redirect` 只呈现开始按钮，`non-browser` 不出现在人类登录页；前端不识别具体 Provider 私有协议 |

### 3.2 认证、身份绑定与会话收敛

所有人类登录方法使用同一条收敛链路：

```text
Authentication Entry Context
→ QueryEnabledAuthenticationMethods
→ AuthenticationMethodDescriptor
→ StartAuthentication
→ single-use AuthenticationFlow
→ AuthenticationDriver.complete
→ AuthenticatedSubject
→ ResolveExternalIdentityLink
→ PrincipalIdentity
→ BrowserSession
→ Scope selection
→ RequestContext
```

认证发生在 Principal 和 Scope 建立之前。首版根据当前平台安装的认证入口列出可用方法；未来组织级登录入口可以通过显式 organization/realm entry key 限定候选，但首版不为此创建 `AuthenticationRealm` 聚合。Project、Solution、Workflow 和最近访问记录都不能选择或覆盖 AuthenticationMethod。

`AuthenticationFlow`（认证流程记录）只支撑一次认证 ceremony、重放防护和回调关联，不是产品聚合、Workflow、Version 或审批状态机：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_flow_id` | 认证流程身份 | 系统生成；绑定开始与完成请求 |
| `authentication_method_id` | 使用的认证方式身份 | 固定到开始时的 Method 和配置摘要，完成时不能切换 Provider |
| `method_configuration_digest` | 认证方式配置摘要 | 固定开始时已测试的配置；完成时配置已变化则该 Flow 失效并要求重新开始 |
| `purpose` | 流程用途 | `login`（登录）、`link`（绑定身份）或 `test`（测试）；测试流程不能签发业务 Session |
| `state_token_digest` | 回调状态 Token 摘要 | 用于定位并验证浏览器返回的 state 或等价一次性 Token；服务端不保存其明文 |
| `protected_state` | 受保护协议状态 | Adapter 需要继续协议时保存的加密或等价受保护值，例如 nonce、PKCE verifier；查询 API、日志和 Audit 均不可返回 |
| `return_path` | 完成后返回路径 | 只能是平台校验过的站内路径，禁止开放重定向 |
| `expires_at` | 过期时间 | 短期有效，超时后必须重新开始 |
| `consumed_at` | 消费时间 | 首次成功或终结性失败后写入；非空后拒绝重复完成 |

Adapter 返回的 `AuthenticatedSubject`（已认证外部主体）是协议中立的瞬态值：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authentication_method_id` | 认证方式身份 | 证明本次结果来自哪一个配置实例 |
| `issuer_key` | 身份签发方稳定键 | OIDC issuer、SAML entity、LDAP directory 或 Local 域的规范化稳定值 |
| `subject_key` | 签发方内主体稳定键 | 使用协议稳定 subject；不能用可修改的邮箱、显示名称或登录名替代 |
| `display_name` | 外部显示名称 | 可选展示属性，不参与唯一身份判断 |
| `verified_email` | 已验证邮箱 | 可选属性，只用于展示或显式绑定流程，禁止仅凭邮箱自动合并 Principal |
| `attributes` | 允许传递的归一化属性 | Adapter 按声明的 allowlist 输出；group/claim 只能成为后续策略输入，不能直接授予权限 |
| `authentication_strength` | 认证强度 | 由 Adapter 根据已验证协议事实归一化，用于策略判断，不由客户端自报 |
| `authenticated_at` | 认证完成时间 | 显式时钟输入，用于会话和审计 |

`ExternalIdentityLink`（外部身份绑定）把协议身份稳定映射到本地 Principal，Local 认证也使用同一模型：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `external_identity_link_id` | 身份绑定稳定身份 | 系统生成；用于审计和禁用，不是 Version |
| `authentication_method_id` | 认证方式身份 | 防止两个 IdP 中相同 subject 被误认为同一人 |
| `issuer_key` | 签发方稳定键 | 与 AuthenticatedSubject 精确一致 |
| `subject_key` | 外部主体稳定键 | 与 AuthenticatedSubject 精确一致；三元组必须唯一 |
| `principal_id` | 本地 Principal 身份 | 身份解析后的唯一平台主体 |
| `linked_at` | 建立绑定时间 | 由受控初始化、邀请、管理员绑定或明确 JIT Policy 写入 |
| `disabled_at` | 停用时间 | 非空后该外部身份不能继续登录，但历史审计仍可解析 |

不存在 ExternalIdentityLink 时默认拒绝登录并给出可操作说明。未来若允许 Just-in-Time 建号或 group 映射，必须由显式 Identity Provisioning Policy 与 Authorization Policy 决定；AuthenticationDriver 仍只提供事实。Local Driver 使用固定 `issuer_key=oac.local` 和不可变本地账号 subject，登录名和邮箱都不是身份主键。

AuthenticationMethod 更新采用“保存配置 → Probe → 测试登录 → CAS 激活”的普通配置流程。切换或禁用方法不修改 Platform Core 代码，不创建 Workflow，也不重写历史 Session；默认撤销由被禁用方法签发的现有 Session，若企业需要保留到期必须由显式 Session Policy 决定。平台必须阻止管理员在没有已验证替代方法或受控 break-glass 路径时禁用最后一个可用的人类管理登录方式。

### 3.3 首个本地管理员

安装器 CLI 在首次安装时生成高熵一次性 `bootstrap_code`（初始化验证码），只向执行安装的平台运维展示一次，并把摘要写入受限的安装 Secret。Web 的首次初始化页面提交 `BootstrapLocalAdministrator`：

1. 校验验证码摘要、有效期、目标安装和未消费状态；
2. 收集管理员登录名、显示名称和密码；
3. 在一个事务中创建首个本地 Principal、Local AuthenticationMethod 对应的 ExternalIdentityLink、首个 Local Organization Scope、安全默认 AuthorizationPolicy、管理员授权关系和本地认证材料；
4. 消费初始化验证码并记录 AuditEvent；
5. 创建 Browser Session，自动进入唯一 Local Scope。

相同幂等键和相同提交摘要返回同一结果；不同内容的并发初始化只有首个有效 CAS 成功。初始化完成后 `/bootstrap` 只能重定向到登录页，不能重新生成第二个首任管理员。密码明文只进入 Governance Identity Use Case 的受控 `LocalCredentialStorePort`（本地认证材料存储接口），Local AuthenticationDriver 只通过对应验证 Port 校验；平台数据库和 AuditEvent 只保存不可逆密码摘要、fingerprint 或脱敏事实。初始化完成后的自动登录仍经过统一 BrowserSession、Scope 解析、RequestContext 和 Authorization 链，不存在 Bootstrap 专属会话旁路。

### 3.4 普通本地用户邀请

首版关闭自助注册，不提供“申请注册后等待管理员审批”的第二套账号生命周期。普通人类账号统一由已授权管理员通过 Web 或同一公开 Platform API 执行 `InviteLocalUser`：

- 管理员填写邮箱、可选显示名称和初始 `access_profile_key`（访问权限模板键）；未显式选择时使用当前 AuthorizationPolicy 的 `default_access_profile_key`，首版唯一 Local Scope 从当前 RequestContext 继承；
- 管理员不填写、查看或传递用户密码；
- Governance Identity Use Case 创建一个短期 `LocalUserInvitation`（本地用户邀请记录），保存邀请对象、Scope、Token 摘要、邀请人、过期和接受/撤销事实；
- 配置 Notification Adapter 时发送一次性邀请链接；未配置邮件时只在创建成功响应中展示一次可复制链接或邀请码，服务端不保存明文 Token；
- 重新发送必须创建新 Token 并使旧 Token 失效，不能让多个有效链接并行存在。

用户执行 `AcceptLocalUserInvitation` 时确认固定邮箱，设置登录名、显示名称和密码。系统必须在一个事务中校验 Token、邀请摘要、过期/撤销/接受状态和登录名唯一性，创建本地 Principal、Local ExternalIdentityLink、认证材料和当前 Scope 的成员/访问授权，并消费邀请。邀请本身已经是管理员准入决定，接受后直接激活账号，不再进入 Pending Approval 或二次审批。

`LocalUserInvitation` 是 Authentication/Governance 的短期支持记录，不是 Platform Core 产品聚合、Workflow、WorkUnit、Decision、Artifact 或 Version。其 `Pending|Accepted|Revoked|Expired` 仅由 `accepted_at`、`revoked_at`、`expires_at` 与当前时间推导，不额外持久化一个可漂移的状态字段。

### 3.5 登录、Browser Session 与 Scope

本地用户从 Web 提交登录名和密码，企业用户可以选择已启用的浏览器联邦登录方式；对应 AuthenticationDriver 返回 AuthenticatedSubject 后，Identity Resolver 必须通过精确 ExternalIdentityLink 得到 Principal，Session Adapter 才能创建服务端 Browser Session。浏览器只保存不可猜测的 Session Cookie；Cookie 必须使用 HttpOnly、Secure 和合适的 SameSite 策略，不能承载密码、权限列表或完整 RequestContext。

登录成功后解析当前 Principal 有权访问的 Scope：

- 恰好一个 Scope：自动进入并在账户区域只读展示当前 Scope；首版本地部署走此路径；
- 多个 Scope：未来企业模式要求用户明确选择，不能按最后访问 Project 或客户端缓存静默扩大上下文；
- 没有 Scope：认证成功但拒绝进入产品，展示联系管理员的明确说明。

每个后续请求仍根据有效 Session 重建 `PrincipalIdentity + ScopeIdentity + authentication_context`，再进入 Authorization。用户退出时服务端先撤销当前 Session，再清除浏览器 Cookie 并返回登录页；退出不修改 Project、Workflow、Run、Decision 或其他业务记录。管理员可以在安全事件中撤销一个用户的全部 Session，但不能通过前端删除其历史身份引用。

### 3.6 身份恢复

身份恢复沿用一次性 Token，而不是让管理员直接设置或读取他人的密码：

- 普通本地用户由已授权管理员在 Users 页面发起 Recovery，系统发送邮件或返回一次性可复制恢复链接；
- 所有管理员均不可用时，具备宿主机或 Kubernetes 管理权限的平台运维可以通过安装器 CLI 为一个精确本地管理员生成 break-glass Recovery Code；
- Recovery Token 只保存摘要，具有短有效期、单次消费和精确 Principal 绑定；
- 用户在 Web 中设置新密码后，系统消费 Token、撤销该用户既有 Browser Session 并记录 AuditEvent；
- CLI、管理员页面、日志和 AuditEvent 都不能接收或展示新密码明文。

服务身份不使用人类 Invitation、Browser Session 或密码恢复流程。OIDC、SAML、LDAP 等外部身份的账号建立和生命周期由 Governance Identity Provisioning/Link Use Case 与 Policy 负责，AuthenticationDriver 不拥有本地 Principal 生命周期；本地自助注册也不会因为安装外部 Driver 而自动开启。

## 4. Authorization

Authorization 是 OAC 自己的产品安全语义，由 Platform Core/Governance 中唯一内置的 `AuthorizationEngine`（统一授权引擎）执行。它不是 Package、Provider、Extension、Host 或企业可替换实现，也不存在 Authorization Provider Registry、Provider Binding 或按 Project 选择授权机制。

每个公开 Query/Command、人工结果提交、Decision、内部 ApplicationCommand 和受保护的 LifecycleAction 都在读取敏感信息或产生副作用前进入同一个授权入口。Web、`oactl`、Solution 领域页面、Controller 或后台服务只能改变认证载体和调用来源，不能改变授权算法。

### 4.1 AuthorizationPolicy

每个已建立的 Scope 恰好有一个当前 `AuthorizationPolicy`（授权策略）。安装或首个 Scope 初始化时创建安全默认策略，普通用户不需要先配置才能使用平台。它是普通可变配置：使用稳定身份、CAS `revision` 和 `policy_digest`，编辑不创建 `AuthorizationPolicyVersion`。

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `authorization_policy_id` | 授权策略稳定身份 | 系统生成；Scope 生命周期内保持稳定，供 AuthorizationDecision 与 AuditEvent 引用 |
| `scope_id` | 所属治理范围身份 | 系统从管理入口和 RequestContext 确定；一个 Scope 只能有一个当前策略 |
| `name` | 授权策略名称 | 管理员填写；用于管理页和审计展示 |
| `description` | 授权策略说明 | 管理员填写；解释策略适用范围和组织约定 |
| `default_access_profile_key` | 默认访问权限模板键 | 管理员选择；必须指向当前 `access_profiles[]` 中存在的模板，供 Invitation 等未显式选择时使用 |
| `access_profiles[]` | 访问权限模板列表 | 管理员配置的命名权限集合；Invitation、成员关系或 Project 访问关系只保存其中的稳定 key |
| `revision` | 策略修订号 | 系统维护的 CAS 值；防止并发覆盖，不是语义版本 |
| `policy_digest` | 规范化策略摘要 | 系统根据完整策略内容计算；授权结果固定本次实际使用的摘要 |

每个 `access_profiles[]`（访问权限模板）包含：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `access_profile_key` | 访问权限模板稳定键 | 在当前 Scope 策略内唯一；已被成员关系使用时，改名按删除旧 key、新增新 key 处理并进入影响检查 |
| `name` | 模板名称 | 管理员填写，例如面向成员、审计或管理职责的可读名称 |
| `description` | 模板说明 | 管理员填写；说明该模板适用对象和边界 |
| `grants[]` | 允许授权项列表 | 只表达 Allow；没有匹配项默认 Deny，不支持 Deny/Allow 优先级、顺序覆盖或脚本 DSL |

每个 `grants[]`（允许授权项）包含：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `grant_key` | 授权项稳定键 | 在当前访问模板内唯一；用于影响分析、Decision 解释和审计，不是全局对象 ID |
| `description` | 授权项说明 | 管理员填写；说明为什么授予这一组操作 |
| `action_keys[]` | 允许操作键列表 | 只能从 OAC 发布的稳定 Action Catalog 中选择，例如 Project 读取、Workflow 创建或 Audit 查询；不能填写任意字符串 |
| `subject_kinds[]` | 适用对象类型列表 | 只能从 OAC 发布的 Subject Kind Catalog 中选择；实际对象仍受访问关系的 Scope/Project 范围约束 |

Action Catalog 和 Subject Kind Catalog 是 OAC 随已发布 Application Use Case 与规范 SubjectKey 类型生成的只读元数据，不是数据库聚合、Package、Provider、Version 或用户配置。`QueryAuthorizationCatalog` 返回瞬态目录，每个 action/subject kind 至少包含稳定 `key`（键）、`name`（名称）和 `description`（说明）；前端必须同时展示名称和说明，持久化 Policy 时只保存稳定 key。新增或改变键的语义属于 OAC API 兼容性变更，不能由 Solution/Extension 动态注册。

`platform.operations.read`（读取平台运行状态）是独立稳定 Action。它允许查询当前 Scope 的瞬时 PlatformOperationsView，但不自动授予 Kubernetes 诊断细节、业务资源正文、Audit、Credential 或任何修复命令权限。服务端对每个 health/alert subject 和 infrastructure detail 继续执行字段级授权与脱敏；无诊断权限时返回可读平台结论和类型化修复入口，不把底层原始对象完整交给前端隐藏。

AuthorizationPolicy 只定义“某个访问模板可以对哪些对象类型执行哪些操作”。Principal 获得哪个模板以及作用于 Scope 还是精确 Project，由现有身份成员关系和资源访问关系承接，不复制进 Policy。OIDC group、LDAP group 或其他外部 attribute 只能经过显式 Identity Provisioning/Mapping Use Case 转为受审计的本地访问关系，不能直接成为隐式权限。

### 4.2 统一评估与策略更新

应用层先解析显式输入，再调用确定性的统一引擎：

```text
RequestContext
+ action_key
+ exact SubjectKey
+ requested_scope
+ current local access relations
+ current AuthorizationPolicy
+ fixed OAC invariants
+ request-specific requirements
→ AuthorizationEngine.evaluate
→ AuthorizationDecision(Allow | Deny)
```

固定 OAC invariants（平台不可覆盖规则）至少包括：默认拒绝、禁止跨 Scope 越权、禁用主体不可继续、human-only 操作必须由合格人类提交、职责独立性必须满足、subject ID/digest 必须精确、Project/Solution/Extension 只能收紧不能扩大平台权限。它们由 OAC 代码拥有，不出现在用户可编辑 Policy 中。

AuthorizationRequest 至少包含：

- RequestContext；
- `action_key`（操作键）；
- `subject`（包含 kind、id 和必要 digest 的精确对象）；
- `requested_scope`（请求影响范围）；
- `attributes`（数据分类、环境、风险、责任和独立性等显式事实）；
- `requirements`（当前 Use Case 或 WorkUnit 要求满足的固定资格）。

调用方不能传入或选择 Policy；Governance 根据当前 Scope 解析唯一当前策略和本地访问关系。引擎返回 `AuthorizationDecision`，至少包含 Allow/Deny、稳定原因码、`matched_grants[]`（每项同时固定 access profile key 与 grant key）、Policy ID/revision/digest、可选收紧 obligations 和评估时间。缺少策略、策略损坏、未知 Action/Subject、输入事实不完整或引擎内部异常都返回 Deny。

管理员在授权管理页编辑一个瞬态 `AuthorizationPolicyCandidate`（授权策略候选），不持久化草稿或创建第二套策略状态机：

1. `EvaluateAuthorizationPolicyCandidate` 校验 Action/Subject Catalog、key 唯一性、现有访问关系是否引用缺失模板、当前管理员是否会失去 `governance.authorization.manage`、是否仍存在至少一个可恢复的管理主体，以及是否尝试覆盖固定 invariants；
2. 页面展示 blockers、warnings、受影响访问关系和代表性 Allow/Deny 对照；评估不改变当前策略；
3. `AdoptAuthorizationPolicy` 使用 `expected_revision + candidate_digest`（预期策略修订号与候选内容摘要）重新校验并原子替换当前 Policy 内容、增加 revision、写 AuditEvent；旧内容不形成 Version 聚合；
4. 后续授权立即使用新 revision/digest，活动 Workflow 不固定旧 Policy；既有 AuthorizationDecision 和 AuditEvent 保持不可变并继续引用当时策略摘要。

允许结果不能跨越 Identity、访问关系、Policy revision/digest、精确 subject 或请求要求变化继续复用。Project 配置、Solution、Harness、Agent 或外部 Provider 都不能绕过该入口。

### 4.3 人工责任执行授权

人工 Review、Acceptance 或其他责任执行复用普通 Run 合同，不建立 HumanTask 权限模型。Host 构建 Executor Catalog 时，Platform Core/Governance 根据 WorkUnit 的 `responsibility_requirement`（责任要求）、output contract、Scope、数据分类、环境、Review 独立性和当前 Policy 尝试生成 `principal_requirement`（提交者资格要求）。只有该要求非空且存在受控授权路径时，Host 才注册 human ExecutorDescriptor；否则走普通 MissingExecutor 分支。它描述“哪些 Principal 可以提交”，不预先选择、领取或锁定某个人，也不以浏览器在线状态决定 availability。

`SubmitHumanRunResult`（提交人工执行结果）必须重新校验：

- `workflow_name`（Workflow 名称）、`work_unit_id`（WorkUnit 身份）、`run_id`（Run 身份）和当前 iteration；
- `invocation_id + request_digest`（调用身份与固定请求摘要）；
- 当前 Principal 是否满足 Scope、责任、权限和与 Producer 的独立性；
- `expected_work_unit_revision`（预期 WorkUnit 修订号）、idempotency key 与 submission digest；
- ReviewResult/AcceptanceResult 是否满足当前 output contract。

所有满足资格的用户都可看到同一待办；首个有效 CAS 提交获胜。同一幂等键和摘要返回原结果，不同内容的并发提交返回 Conflict，并展示已接受结果摘要。实际 `submitted_by`、AuthorizationDecision、Observation 和 AuditEvent 必须可追溯。人工结果只写当前 human Run 与 Observation，不能直接推进 WorkUnit，也不能替代后续用户 Decision。

## 5. Execution Admission

Execution Admission 回答的是“已经通过 Authorization 的执行请求，在当前明确资源、安全和外部状态下是否允许真正物化”。它由 Platform Core/Governance 中唯一内置的 `ExecutionAdmissionEngine`（统一执行准入引擎）负责，不是 Package、Provider、Extension、Harness、Host 或企业可替换实现，也不存在 Admission Provider Registry、Provider Binding 或按 Project/Solution 选择准入机制。

Execution Admission 发生在创建以下对象之前：

- ComponentRun；
- 直接 Deployment；
- Child Workflow；
- AgentRun；
- human Run request；
- Workspace。

Kernel 只输出通用 LifecycleAction 和逻辑执行要求，不参与产品级准入。Platform Core 解析精确执行实现、收集显式事实并调用 ExecutionAdmissionEngine；引擎返回结果后，应用 Use Case 才决定是否创建产品对象或提交 Outbox。Web、`oactl`、Controller、AgentRun Job、RuntimeDriver、Harness Host 和直接 Deployment 路径都不能各自实现另一套判断。

### 5.1 请求、事实与决定

`ExecutionAdmissionRequest`（执行准入请求）至少包含：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `request_context` | 当前请求上下文 | 固定 Principal、Scope、请求、因果和认证事实；必须已通过 Authorization |
| `execution_kind` | 执行种类 | `component-run`、`direct-deployment`、`child-workflow`、`agent-run`、`human-run` 或 `workspace` |
| `subject` | 精确执行对象 | 包含必要 ID/digest/revision；不能只给模糊资源类型 |
| `owner_context` | 执行归属上下文 | 精确 Project、Workflow、WorkUnit、iteration 或 Deployment 归属；不适用字段不伪造 |
| `resolved_execution` | 已解析执行实现 | tagged union；只携带引擎判断兼容性所需的组件、Runtime、Workspace、人工资格或外部执行器事实，不暴露 Provider 句柄 |
| `resource_requirements` | 资源要求 | CPU、内存、GPU、存储、架构和拓扑等显式需求 |
| `security_requirements` | 执行安全要求 | 网络、数据分类、凭证用途、身份、隔离和外部副作用要求 |
| `usage_context` | 当前使用量观察上下文 | 首版只作为解释和未来扩展输入，不隐式触发 Budget 自动暂停 |
| `idempotency_key` | 创建请求幂等身份 | 将决定绑定到同一逻辑物化请求，不能跨不同请求复用 |

Platform Core 从现有权威来源组装瞬态 `ExecutionAdmissionFacts`（执行准入事实），不创建第二个聚合、表或状态机：

- Platform、Scope 和 Project 当前是否允许新执行；
- 精确 executor/runtime/workspace/component 是否可用且满足所需能力；
- 当前资源容量、并发占用与调度约束；
- ExecutionSecurityRequirements 是否可满足；
- 所需 CredentialBinding 是否存在、启用、未过期且用途/subject 匹配；
- 同一外部目标或副作用并发键是否安全；
- 可选外部系统返回的类型化 `pass|fail|unknown` 检查事实、证据摘要和观察时间。

这些事实必须来自已有权威边界，不能由页面或调用方自行声称：

| 准入事实 | 权威数据来源 | 关键语义 |
| --- | --- | --- |
| Platform/Scope/Project 是否允许新执行 | Platform Core 当前平台配置、Scope 状态与 Project 当前记录 | 禁用或归属不一致直接 Reject |
| 执行实现兼容性 | 已解析 AgentDefinition、ExtensionPackage/ComponentInstall、RuntimeDriver、内置 Workspace、DeploymentDriver 描述与能力事实 | 只判断精确实现是否满足要求，不重新做 Kernel executor matching |
| 资源、配额与拓扑 | Execution Host 从 Kubernetes API/资源配额/节点能力读取的显式事实 | 没有任何兼容节点、超过硬配额或违反拓扑约束时 Reject；只是暂时没有空闲资源时可以 Admit 并由 Kubernetes 保持 Pending，不伪装成业务失败 |
| 执行安全能力 | ExecutionSecurityRequirements 与精确 Runtime/Workspace/网络/身份能力描述 | 缺少必需隔离、网络或身份能力时 Reject |
| Credential readiness | CredentialBinding 元数据、CredentialBroker 类型化校验和必要 Provider 健康事实 | 不读取或复制 Secret；禁用、过期、用途或 subject 不匹配时 Reject |
| 外部副作用并发 | 当前 Deployment/ComponentRun 幂等与目标并发事实 | 已存在 Unknown/Running 同目标副作用时 Reject 新尝试，避免重复执行 |
| 企业外部检查 | 窄 Execution Admission 事实端口返回的签名/摘要化结果 | 只能贡献单项事实；Unknown、超时或证据过期不能放行 |

外部系统只能通过由内层定义的窄事实端口返回单项检查事实，不能返回整体 Admit、跳过平台检查、修改请求、创建执行对象或扩大约束。必需事实缺失、超时、Unknown、过期、相互矛盾或摘要不匹配都按 Reject 处理。首版不建立 Admission Extension API、Provider 选择或用户可编辑 AdmissionPolicy。

`ExecutionAdmissionDecision`（执行准入决定）是不可变治理支持记录：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `execution_admission_decision_id` | 执行准入决定身份 | 系统生成；用于审计和执行创建因果链 |
| `request_digest` | 精确请求摘要 | 固定本次被评估的 ExecutionAdmissionRequest；不同摘要不能复用 |
| `effect` | 准入结果 | 只有 `Admit`（允许物化）或 `Reject`（拒绝物化） |
| `reason_code` | 稳定原因码 | 表达禁用、兼容性、资源、安全、凭证、并发或事实未知等原因，不用自由文本代替 |
| `evaluated_checks[]` | 已评估检查列表 | 每项包含稳定 check key、`pass\|fail\|unknown`、原因、可选证据摘要和观察时间 |
| `constraints[]` | 附加收紧约束 | 例如资源上限、调度或并发限制；只能收紧，不能修改 WorkUnit 业务语义 |
| `rules_digest` | 内置准入规则摘要 | 固定本次 OAC 规则集合，不是用户管理的 Policy ID 或 Version |
| `facts_digest` | 显式事实摘要 | 固定本次所有输入事实；防止结果被用于已变化环境 |
| `evaluated_at` | 评估时间 | 显式输入；供事实新鲜度和审计使用 |

统一计算流程为：

```text
Authorized ExecutionAdmissionRequest
+ current ExecutionAdmissionFacts
+ fixed OAC admission rules
→ ExecutionAdmissionEngine.evaluate
→ ExecutionAdmissionDecision(Admit | Reject)
```

首版固定检查至少包括：

- 平台和 Project 是否启用；
- subject、revision/digest 与幂等身份是否精确；
- 已解析执行实现是否可用并满足所需能力；
- Resource Requirements 是否在当前资源与调度边界内；
- Execution Security Requirements 是否可满足；
- Credential 是否就绪且用途/subject 匹配；
- 外部副作用并发键是否冲突；
- 所有必需外部检查事实是否为有效 Pass。

Admit 后，应用 Use Case 在同一持久化边界内保存 Decision、AuditEvent 和待创建执行对象；跨存储执行通过既有 CommandRecord/Outbox 发送已经决定的命令。Reject 保存 Decision 和 AuditEvent，但不得创建 Run、Deployment、ChildWorkflowLink、Workspace、Job、Pod 或 PVC。Retry、恢复、后继 Workflow 和直接 Deployment 重试都必须针对当前请求与事实重新准入，不能复用旧 Admit。

`human-run` 不携带 Runtime、Workspace 或基础设施实现。准入只确认当前 Scope/Project 允许形成该人工执行入口并且存在非空 `principal_requirement`；实际人员提交时仍重新执行 Authorization、责任、独立性、请求摘要、WorkUnit revision 和 CAS 校验。

首版没有独立 Admission 配置页面。用户只在 Run、Workflow、Deployment 或 Audit Explorer 中看到准入结果、失败检查和修复指引；底层资源、Credential、Runtime、Workspace 或 Project 配置仍在各自页面维护。未来 Quota、Budget、Reservation 和自动止损可以作为普通类型化设置和事实进入同一引擎，但不得引入第二套决策机制，也不进入 Orchestrator Kernel。

## 6. Credential Management

统一 Settings 提供 Credential 管理入口，但 Credential 的权威边界仍在 Governance Module。页面只让用户理解和管理 `CredentialBinding`；底层职责保持分离：

- `CredentialBinding` 是稳定、可授权、可被业务配置引用的产品对象；
- `CredentialStorePort` 是平台内部保存、轮换和销毁 Secret 材料的窄接口；
- `CredentialBroker` 是按一次调用的 Principal、Project、subject 和用途解析短期使用权的治理服务。

首版安装时自动启用一个内置 CredentialStore，普通用户不配置或选择存储实现。系统不建立独立 `CredentialPolicy`、Credential Policy Version 或普通用户轮换策略编辑页。CredentialBinding 保存稳定 ID、CAS revision、名称、描述、类型、Scope、可选 Project、不透明 handle、允许用途、subject 约束、可选过期时间、fingerprint 和状态；它不保存 Provider 身份或 `rotation_policy`。Scope/Project 从当前上下文继承，用途和 subject 由创建入口推导，handle/fingerprint/status/revision 由内部 CredentialStore 或系统生成。类型固定安全规则、显式过期时间、人工轮换以及不可变 CredentialRotationRecord/AuditEvent 已足够承接首版安全要求。

CredentialBinding 不保存 API Key、Private Key、PAT、OAuth Token 或 SSH Private Key 明文。每次实际使用都按当前 Scope/Project、用途和 subject 重新授权，并通过内部 CredentialStorePort 解析该 Binding 的不透明 handle。系统发行版升级必须保持 handle 兼容或提供受控数据迁移；首版不存在用户选择存储后端、跨存储后端迁移或隐式回退。

`CredentialStorePort` 保持窄接口：

| 操作 | 中文作用 | 输入 | 输出与约束 |
| --- | --- | --- | --- |
| `store` | 保存新凭证材料 | 已授权 Binding 创建上下文、Credential 类型、Secret、类型元数据 | 不透明 handle、fingerprint、可选 expires_at 和脱敏验证摘要；失败必须清理临时材料 |
| `validate` | 执行类型化验证 | handle、Credential 类型和受控目标上下文 | 脱敏成功/失败；不承诺所有类型都可脱离目标系统独立验证 |
| `resolve` | 为一次授权用途解析材料 | handle、用途、subject、lease 约束 | 仅供 Broker/Materializer 消费的短期 lease 或材料化描述；不得向 Web、Agent 或 Extension 返回通用明文 |
| `rotate` | 写入并切换新材料 | 旧 handle、新 Secret 或类型专用重新授权结果 | 新 handle、fingerprint、可选 expires_at 和验证摘要；失败时旧材料保持有效 |
| `revoke` | 撤销材料 | handle、幂等身份 | 类型化结果；阻止之后新解析并尽力终止活动 lease |

CredentialStorePort 不提供 `listSecrets`、`getSecret`、`exportSecret`、授权判断或后端选择，也不作为 ExtensionPackage 接口公开。

### 6.1 创建、查询和轮换

创建流程：

1. 用户通常在模型、MCP、Repository、私有 Skill 导入或部署目标等就地流程中填写名称、说明、Credential 类型、该类型需要的一次性 Secret、类型专用非敏感元数据，以及 PAT 等类型确实需要的到期时间；
2. Secret 通过 TLS 一次性提交；前端不把它写入 URL、Local Storage、Query Cache 或遥测，并在请求结束后清空受控表单状态；
3. Platform Core 完成 Authorization 与字段校验，从当前 Scope/Project 和创建入口推导最小用途与 subject；
4. 内部 CredentialStore 按 Credential 类型固定安全规则写入受控 Secret Store，返回不透明 `secret_handle` 和 fingerprint；
5. Platform Core 保存 CredentialBinding 元数据并记录 AuditEvent；
6. 后续页面只能看到 fingerprint、派生用途、subject、状态、过期、最后轮换时间和适用的验证结果，不能读取原值。

创建上下文与授权约束的固定映射为：

| 创建上下文 | `allowed_usages[]`（允许用途） | `subject_constraints[]`（对象约束） |
| --- | --- | --- |
| Model Provider | `model-provider-api` | 当前 ModelProviderConnection |
| MCP HTTP Header | `mcp-http-auth` | 当前 McpServerDefinition |
| MCP stdio Environment | `mcp-stdio-env` | 当前 McpServerDefinition |
| 私有 Skill 导入 | `skill-import`、`github-repository-read` | 当前 GitHub Repository |
| Repository 配置 | 用户选择的只读或读写动作 | 当前 Repository |
| Deployment Target | `deployment` | 当前目标环境；实际使用时进一步绑定当前 `deployment_id + release_id + target configuration digest` |

首版独立 Credentials 页面不提供通用 Scope、用途或 subject 扩权编辑器。需要把同类凭证用于另一个目标时，应从该目标上下文创建新的最小权限 CredentialBinding；GitHub App 等本身能够通过 Provider 返回安装范围的类型，则把该范围作为只读 subject 约束保存，用户不能在平台内手工扩大。

模型连接或 MCP 配置中的就地新建凭证由目标领域 Use Case 编排，而不是由 Settings Facade 发起万能事务：目标领域先分配稳定 subject ID，再调用 Governance 的窄 `CreateCredentialBinding` Port；Governance 根据该 subject 与声明用途创建最小权限 Binding；目标配置保存成功后才完成命令。若目标配置保存失败，调用方撤销本次新建且尚未被其他对象引用的 Binding，内部 CredentialStore 清理材料并记录补偿 AuditEvent。选择已有 Binding 时只做授权与约束校验，失败不能撤销用户原有凭证。

轮换不要求用户修改所有 Model Provider、MCP、Repository 或 Deployment 配置。CredentialBinding 保持稳定，内部 CredentialStore 创建新的材料版本，测试成功后原子切换当前版本，再撤销旧材料，并形成 CredentialRotationRecord 与 AuditEvent。已签发的短期 lease 不会因为轮换获得更长有效期。

Credential 页面不提供一个伪装成通用能力的“测试所有凭证”。GitHub App、云身份等具有自包含验证协议的类型可以由对应类型处理器执行受控验证；普通 API Key、MCP Header 等是否有效，应由实际 Model Provider、MCP 或目标系统的 Test/Probe 用例验证，并把脱敏结果关联回 CredentialBinding。

### 6.2 运行时使用

```text
typed configuration with credential_binding_id
→ Authorization
→ CredentialBroker validates scope / purpose / subject / expiry
→ internal CredentialStore resolves current secret material
→ short-lived lease or in-process materialization
→ Model Connector / MCP / Git / Runtime / Preview Executor / DeploymentDriver
→ cleanup and AuditEvent
```

平台侧 Connector 可以在受控进程内把材料放入请求 Header；Runtime/Workspace 场景可以使用临时环境变量、只读文件、Projected Secret 或 Workload Identity。无论哪种方式，材料都只对当前调用可见，不能进入 Prompt、Artifact、Observation、AgentRunEvent、命令回显或日志。

### 6.3 GitHub Credential

GitHub 采用四种明确 Profile：

| Profile | 适用场景 | 平台保存的 Secret | 实际使用方式 |
| --- | --- | --- | --- |
| GitHub App | 组织自动化、多个 Repository、长期运行 | App Private Key | Broker 生成 App JWT，再换取短期 Installation Access Token |
| GitHub App User | 需要代表具体用户操作 | OAuth Refresh Token | Broker 刷新短期 User Access Token，用户权限与 App 权限取交集 |
| Fine-grained PAT | 个人、小团队或临时集成 | PAT | 只在显式 Repository/Permission/Expiry 范围内使用 |
| SSH Deploy Key | 单 Repository Git 传输 | SSH Private Key | 临时文件或 Agent 注入；公钥由用户配置到 GitHub Repository |

GitHub App 是组织集成的默认选择。用户需要在 GitHub 完成：创建 App、选择最小权限、安装到明确 Repository、下载 Private Key。平台普通表单只要求 `github_app_id`、`github_installation_id`、GitHub API Base URI（GitHub.com 使用预设，企业实例才修改）和 Private Key 一次性上传。CredentialStore/Broker 在最终创建 Binding 前测试连接并从 GitHub 读取 Repository 范围与权限摘要；失败时清理本次临时材料，不保存半成品 Binding。成功后页面只读展示范围，用户不在平台中手工抄写或扩大这些值。实际 Skill 私有仓库导入、Clone、创建 PR、Release 等操作使用按需签发的短期 Installation Token，不把它保存为新的 CredentialBinding。需要代表具体用户时，短期 User Access Token 的刷新逻辑留在 CredentialStore/Broker 内部。所有 Provider Token 的缓存和续期都必须以 Provider 返回的 `expires_at` 为准，平台不得把某个外部服务当前的固定时长写成领域常量。

GitHub App Private Key 不会自动过期，因此 Settings 必须提供“添加新 Key → 测试 → 切换 → 撤销旧 Key”的轮换动作。Fine-grained PAT 必须要求用户配置到期时间和最小权限，并在到期前提示轮换。大型组织长期自动化不默认使用 Classic PAT。

### 6.4 GitHub Credential 的调用示例

以私有 GitHub Skill 导入为例：

1. 用户在 Skill Import 表单填写 Repository URI，可选填写 Branch/Tag/Commit 与子目录；私有仓库选择或就地创建 CredentialBinding，平台自动把它限制为当前 Repository 的 `skill-import` / `github-repository-read` 用途；
2. Platform Core 校验当前用户对 Skill Catalog 和 CredentialBinding 的权限；
3. CredentialBroker 验证 Binding 的 Scope、Repository subject、用途、状态和过期时间；
4. GitHub App Profile 生成短期 Installation Token，PAT/SSH Profile 则解析当前材料；
5. GitHub Importer 只读获取 Repository，并把可变 revision 固定为 Commit SHA；
6. Token 或 Private Key 从当前调用边界清理，导入产物只保存 Commit SHA、内容 digest 和 CredentialBinding 身份；
7. AuditEvent 记录谁在何时以什么用途使用了哪个 Binding、访问了哪个 Repository，以及成功或失败。

Repository 写入、PR、Release 和 Promotion 使用同一链路，只是 `allowed_usage` 与 subject 更严格。Reviewer 或只读 Sensor 不能复用 Developer 的写凭证。

直接 Promotion 的 Credential 使用不经过 AgentRun、Workspace 或 ComponentRun。`StartPromotion` 只验证目标绑定可用并固定 `credential_binding_id`；Deployment Host 真正通过 Deployment SDK 调用精确 DeploymentDriver 的 `start/observe/cancel` 时，CredentialBroker 才按当前 Principal/Service identity、Project、`deployment_id`、精确 Release、目标、用途和 target configuration digest 签发短期 lease。自动/手动 Observe 与 Cancel 始终绑定原 Deployment 固定的目标和 CredentialBinding 身份，只能取得当前有效短期材料；新 Retry 或部署旧 Release 则作为新的 `StartPromotion` 重新执行 `release.promote` Authorization、Execution Admission、当前 Project revision、Credential readiness 和 target concurrency 校验。目标配置变更、Binding Disabled/Expired、CredentialStore 不可用或 subject 不匹配都必须 Fail Closed；不得回退到旧 Workflow 快照、宽权限 Token 或浏览器提交的 Secret。

`deployment.observe` 和 `deployment.cancel` 是独立窄权限；拥有查看权限不自动获得取消或重新部署权限。`Unknown` 时 Governance 必须阻止新的 `StartPromotion` 绕过目标并发键。恢复请求中的 `caused_by_deployment_id` 由服务端校验同 Project、同目标和已知终态，不能由浏览器关联任意 Deployment。

禁止：

- Secret 枚举；
- 把明文写入 Artifact、Observation、Prompt、Event 或日志；
- 浏览器持有生产 Secret；
- AgentRun 获得当前责任不需要的凭证；
- CredentialStore 不可用时回退到更宽权限凭证。

Runtime、Harness Extension 和 Deployment 只能获得短期句柄、挂载或 Workload Identity。

### 6.5 默认存储

Compact 安装时自动启用内置 CredentialStore：使用平台主密钥进行信封加密，Secret 密文与产品数据库逻辑隔离，主密钥来自安装时受控 Secret。External Cluster 可以使用启用了 etcd encryption at rest、最小 RBAC 与审计的 Kubernetes Secret 实现。两种部署形态都通过同一内部 CredentialStorePort、CredentialBroker 和 Audit 路径运行。

普通用户和治理管理员都不选择 Credential 存储后端。首版不建设第三方凭证存储 ExtensionPackage、后端 Catalog、默认后端选择、跨后端迁移或回退逻辑。未来若企业 Vault/KMS 集成成为明确需求，应先在系统发行版内提供受控实现；只有接口、安全隔离和合同测试稳定后，才评估公开 Extension API。

## 7. Execution Security

ExecutionSecurityRequirements 是值对象，不是 ExtensionPackage。

它可以表达：

- process isolation；
- network policy；
- data classification；
- dedicated node；
- filesystem policy；
- secret delivery mode；
- outbound allowlist；
- sandbox requirement。

内置 Workspace、RuntimeDriver 或执行 Extension 不能满足 Required Capability 时拒绝创建。

安全边界：

- Agent 在隔离 Workspace 中无人值守执行；
- 不挂载产品数据库和 Controller 凭证；
- 不访问其他 AgentRun Workspace；
- Reviewer 不获得修改正式被审对象的凭证；
- 人工 Reviewer 或显式人工 Acceptance WorkUnit 执行者只获得当前精确 subject 和 Run request 的读取/提交权限，不获得 Producer 的隐藏 Session、私有推理或无关 Artifact；
- 生产凭证只进入明确绑定的 DeploymentDriver 调用；
- Gate Evaluator 无网络、文件、数据库和凭证；
- Agent、Extension 和外部系统不能直接写控制状态；Deployment Host 只能通过 `SubmitDeploymentReport` 追加当前 Deployment 的专用报告，不能写 Workflow CR 或数据库。

## 8. Audit

OAC 的本地不可变 AuditEvent 写入链和统一 Audit Explorer 始终启用，是平台唯一审计权威。它们属于 Platform Core/Governance，不是 Package、Provider 或可关闭功能。不存在用于替换本地记录的 AuditProvider、按 Scope 切换本地审计实现、关闭本地审计的开关，也不允许在本地查询失败时回退到 SIEM 或外部系统。

### 8.1 本地权威审计

平台对以下操作产生不可变 AuditEvent：

- Authentication 结果；
- Authorization 允许和拒绝；
- Execution Admission；
- Decision；
- Credential use；
- 状态写入；
- Component lifecycle；
- ComponentRun、AgentRun、human Run request/result、直接 Deployment 和 Workspace 创建/取消；
- Plan 激活；
- Release 和 Deployment。

同一 AuditEvent 内，`(kind, control_id)` 是唯一 evaluated control 身份；不同 `control_id` 的 Provider 检查可以并存。

同一存储中的记录与本地 AuditEvent 使用同一事务。任何先提交 PostgreSQL 事实再更新 Workflow CR 或分发直接 Deployment 的持久业务命令，都以本地 AuditEvent/CommandRecord/Outbox 同事务提交；Platform Core 分别幂等送达 `spec/status/finalizer` 或 Deployment Host。Workflow Controller 只提交带稳定 LifecycleAction identity/digest 的类型化命令，不直接写 CR。Deployment Host 只执行已提交 Deployment，不把 Outbox 当作流程触发器。心跳、进度和 Pod Running 等瞬时信号限频、幂等、CAS 写入，但不为每次采样创建 Outbox/AuditEvent；不能依赖进程内日志证明状态变化。

普通日志、Kubernetes Event、AgentRunEvent、模型遥测和外部 SIEM 都不是审计权威。AuditEvent 创建发生在业务提交边界内，不依赖外部 Sink 健康；Audit Explorer、平台 API 和合规证据只读取本地记录。部署方使用标准数据库灾备工具保护这些本地记录，但外部 SIEM 不能成为恢复权威。

### 8.2 可选外部审计导出

Audit Exporter 通过平台内部 `AuditExportPort` 把已经提交的本地 AuditEvent 发送到 SIEM、日志平台或合规存储。首版输出实现随 OAC 系统发行版交付，不作为 ExtensionPackage 安装。任何输出实现都不能拦截、改写、删除、补造本地事件，也不能参与业务事务、Authorization、Execution Admission、Workflow 或 Kernel。

首版每个 Scope 支持零个或一个活动 `AuditExportTarget`（审计导出目标）。未配置目标是正常就绪状态，不影响任何平台功能。该对象是普通可变 Governance 配置，不创建 Version、Policy、Binding 家族或审批状态机：

| 英文字段 | 中文字段释义 | 值的作用与约束 |
| --- | --- | --- |
| `audit_export_target_id` | 审计导出目标稳定身份 | 系统生成；普通名称或 Credential 轮换不改变身份 |
| `scope_id` | 所属治理范围 | 从当前管理上下文确定；首版同一 Scope 最多一个活动目标 |
| `name` | 导出目标名称 | 管理员填写；用于页面、告警和审计展示 |
| `description` | 导出目标说明 | 管理员填写；说明接收系统、责任团队和使用目的 |
| `export_type` | 内置导出类型 | 从当前 OAC 发行版支持的明确类型中选择，例如 HTTPS Webhook、Syslog 或标准日志输出；用户不填写实现 ID、版本或 Package |
| `configuration` | 非敏感目标配置 | 按 `export_type` 对应的内置 Schema 渲染，例如 endpoint、index 或 tenant；不能包含 Secret；创建后改变该值必须替换目标 |
| `credential_binding_id` | 可选凭证绑定身份 | 只有该内置导出类型声明需要鉴权时选择；Secret 不在本对象保存或回填；改绑另一个 ID 必须替换目标 |
| `enabled` | 是否启用外部导出 | 表达管理员期望；不影响本地 AuditEvent 和 Audit Explorer |
| `revision` | 配置修订号 | 系统维护的 CAS 值，不是语义版本 |
| `configuration_digest` | 规范化投递配置摘要 | 系统只根据 `export_type`、规范化非敏感目标配置和 CredentialBinding 身份计算；名称、说明与 enabled 不改变该摘要 |

Audit detail 与 inline probe 的 `SafeSummary` 使用同一闭合集合：`code` 为 `state-write|export-probe|target-lifecycle|agent-definition-publish`；field name 为 `action|count|format|operation|reason|resource|result|revision|scope|status|target` 且唯一；field value 为 `completed|deployment completed|succeeded|failed|created|updated|enabled|disabled|accepted|rejected`。矩阵外字段或值在持久化 Validate 与 canonical wire 序列化时拒绝。

首版不提供按 category、Project、action、outcome 或 Principal 过滤外发事件的配置；启用后导出当前 Scope 内所有仍处于本地保留期的 AuditEvent，避免误配置形成不可见证据缺口。目标创建后只允许原地修改 name、description 和 enabled；改变 export_type、configuration 或 credential_binding_id 必须显式创建新的 AuditExportTarget 并停用旧目标。只轮换同一个 CredentialBinding 内的 Secret 材料可以保留目标身份和投递游标。

管理员配置流程保持最小：

1. 从当前系统发行版支持的内置导出类型中选择一种；
2. 填写名称、说明、非敏感配置，并在确有需要时选择 CredentialBinding；
3. `ProbeAuditExportTarget` 使用内联候选执行脱敏连接测试，不保存目标、不改变当前导出，也不发送真实 AuditEvent；测试动作本身形成一条本地 AuditEvent；
4. `SaveAuditExportTarget` 新建时直接接收候选，更新时使用 `expected_revision` 做 CAS；服务端归一化配置并计算新的 configuration digest，首次保存默认 Disabled；更新不得改变 export type、configuration 或 CredentialBinding 身份，必须走 `ReplaceAuditExportTarget`；
5. `EnableAuditExportTarget` 重新执行 Authorization、Schema/Credential/Probe 校验后启用，并从当前 Scope 最早仍保留的 AuditEvent 开始异步补发；
6. `DisableAuditExportTarget` 只停止外发并保留本地事件与系统 checkpoint；重新启用后从最后连续确认位置继续；
7. 改变导出类型、非敏感目标配置或 CredentialBinding 身份时调用 `ReplaceAuditExportTarget`，显式创建新目标并停用旧目标；新目标不继承旧 checkpoint，并按普通启用流程重新校验和补发本地仍保留事件。

投递主链为：

```text
committed local AuditEvent
→ Audit Exporter 按稳定游标读取一批事件
→ 服务端脱敏并组装 AuditExportBatch
→ internal AuditExportPort.deliverBatch
→ contiguous acknowledgement
→ 更新系统 delivery checkpoint
```

`AuditExportBatch` 至少固定 target ID、投递配置摘要、稳定 `delivery_id`、起止游标和按本地顺序排列的脱敏事件。AuditExportPort 实现必须以 `target + delivery_id` 幂等；返回值只能确认最高连续游标或报告类型化失败，不能修改事件或要求平台信任外部系统状态。内部实现可以处理外部 API 的部分成功，但只有连续、可证明的确认才能推进 OAC checkpoint。CSV `AuditExportStream` 的每个 chunk 是独立 framing，包含自己的 canonical header；消费端不得直接拼接 chunk bytes 形成单一 CSV 文档。

checkpoint 是系统运维支持记录，只保存目标身份、最后连续确认游标、下一次重试时间、重试次数、最近成功时间和脱敏错误码；它不是业务聚合、审计证据、外部事实权威或用户管理 Version。Sink 超时、拒绝、返回非法确认或不可用时采用有界退避重试并产生告警，不能回滚业务操作、阻塞本地查询或创建 Workflow/Run。成功投递每条事件不再创建新的 AuditEvent，避免递归放大；目标创建、测试、启用、禁用、替换和 Credential 使用仍正常审计。

### 8.3 统一 Audit Explorer

Platform Core 提供一个统一的审计查询入口，不为 Authentication、Authorization、Admission、Credential use、Decision 或状态写入分别建立页面、查询服务或持久化模型。用户通过时间、Project、类别、动作、结果、Principal、Subject 和 correlation 筛选同一组本地 AuditEvent；Project、Workflow、Agent、Run、Component 和 Deployment 详情页只提供带预设筛选条件的“查看审计记录”入口。

Audit Explorer 使用 Governance Data 页面模式：高密度表格用于扫描，右侧 Inspector 用于查看不可变详情。列表至少展示：

- `occurred_at`（发生时间）；
- `category + action`（类别与动作）；
- `principal_id`（操作者身份）；
- `subject`（被操作对象）；
- `outcome`（结果）；
- `project_id`（Project 身份，若存在）；
- `correlation_id`（跨模块关联身份）。

### 8.4 查询用例与证据链组装

Platform Core Query Application 提供三个窄用例：

| Use Case | 中文作用 | 输入 | 输出 |
| --- | --- | --- | --- |
| `QueryAuditEvents` | 查询审计事件 | 当前 RequestContext、受限时间范围、可选 Project/category/action/outcome/principal/subject/correlation 筛选、cursor、page size | 脱敏摘要列表和下一页游标 |
| `GetAuditEventDetail` | 查看单条审计详情 | 当前 RequestContext、`audit_event_id` | 瞬时 `AuditEventDetailView` |
| `ExportAuditEvents` | 导出当前筛选结果 | 当前 RequestContext、同一查询筛选、`csv\|jsonl` | 有界流式响应 |

`AuditEventDetailView` 以当前 AuditEvent 为锚点，按精确身份读取并组装：

```text
AuditEvent
→ RequestContext
→ evaluated Authorization / Admission controls
→ related Credential-use facts
→ exact Command / Decision / Run / Deployment subject
→ causation_id / correlation_id related links
```

`RequestContext.RequestID` 是外部请求身份，AuditEvent 的 `request_context_id` 是持久化上下文记录身份；`RequestContextSummary` 同时保留两个字段，详情校验只把 persisted `request_context_id` 与 AuditEvent 绑定，不把两个不同身份折叠。Audit detail binding 由持久化 authority 在自己的 store boundary 签发，binding 的 proof 与事件状态不暴露给查询方，查询方不能用自选原始事件伪造 authority。Go contract 只接受平台/store wiring 通过 internal capability boundary 交付的 receipt，不暴露可注入的 authority callback 或 trust root。

该 View 是请求期 DTO，不创建表、Repository、业务 ID、Version 或生命周期。关联记录缺失、已按 Retention 删除或当前用户无权查看时，详情明确返回 `unavailable|redacted|not_authorized` 的结构化原因，不使用空白字段伪装完整证据。

### 8.5 授权与脱敏

- `audit.read` 按当前 Scope 与可选 Project 执行服务端授权；前端筛选器不能扩大查询范围；
- 资源读取权限不自动授予审计读取权限，审计详情中的每个关联 Subject 仍按当前 Principal 做字段级脱敏和链接可见性判断；
- Secret、Token、Credential 材料、Authorization Header、Prompt、私有推理、完整工具日志和未授权 Artifact 正文不得进入 AuditEvent、详情 DTO、导出、日志或浏览器缓存；
- Credential use 只展示受控 `credential_binding_id`、`sha256:` 指纹别名或摘要、purpose、受限 target 标识和 `succeeded|failed` outcome；凭证形态、控制字符和未闭合结果在 Validate 与 JSON 序列化边界拒绝；
- 普通成功查询和详情读取不产生递归 AuditEvent。拒绝的审计访问、导出和策略控制的高敏感元数据展示必须审计；
- `audit.export` 与 `audit.sensitive.read` 是独立权限。高权限仍不能绕过 Secret 不可返回规则。

### 8.6 存储、分页与导出

本地 PostgreSQL AuditEvent 仍是查询权威来源。允许为查询性能维护只读、可重建的审计读模型或在 AuditEvent 物理表中冗余不可变索引列，但这些列不能成为第二套业务事实。至少建立以下访问路径：

- `(scope_id, occurred_at DESC, audit_event_id DESC)`：Scope 时间线和稳定游标；
- `(project_id, occurred_at DESC, audit_event_id DESC)`：Project 审计；
- `(subject_kind, subject_id, occurred_at DESC)`：资源详情跳转；
- `(principal_id, occurred_at DESC)`：操作者筛选；
- `(correlation_id, occurred_at DESC)`：跨模块追踪；
- `(category, outcome, occurred_at DESC)`：类别与异常结果筛选。

分页固定使用 `(occurred_at, audit_event_id)` 复合游标和降序排序，不使用 offset 作为长列表主路径。服务端 Policy 提供默认时间范围、最大时间范围、page size 和导出条数/字节上限。首版导出直接流式返回受控范围内的 CSV 或 JSONL；超过上限返回可操作 blocker，要求缩小时间范围，不创建异步 Export Job 或状态机。

外部导出中断不影响本地查询。恢复后按系统 checkpoint 继续外发，本地 Audit Explorer 不等待、查询或回读外部系统。本地 Retention 必须至少覆盖配置的最大重试窗口；backlog 接近保留边界时产生高优先级告警，但不能用无限保留或阻塞业务提交隐藏外部系统长期故障。

## 9. Usage

首版只记录已经发生且可证明的 Usage，不设置 Budget、Reservation、自动暂停或自动停机。Usage 查询是治理与运营读路径，不参与 Workflow Lifecycle。

### 9.1 权威来源与写入边界

`UsageRecord` 是唯一产品计量事实。首版 Agent 模型、Tool 和 Runtime Usage 由 RuntimeDriver 产生类型化 `RuntimeEvent.usage`，Runtime SDK Core 统一补齐 `event_id + stream_id + sequence + schema_version`；AgentRun Job 原样上传，Platform Core AgentRun event use case 完成 run-scoped 鉴权、stream/sequence/source_event_id 幂等、OAC 归因与持久化。Extension、资源或外部服务只有在对应 Host 能提交精确单位、数量和来源身份时才可以形成其他 tagged usage；ResourceRequirements、Pod phase、日志和估算值不能冒充实际 Usage。

同一模型或 Tool 调用只在实际发生位置记录一次。父 Workflow、Project、Agent 和模型汇总不写回 UsageRecord；父级 `exclusive` 与 `inclusive` 都在查询时计算。Usage 持久化失败会阻止依赖该 critical usage event 的 AgentRun 正常终结，但 Usage Explorer 查询失败不影响 Workflow 推进。

### 9.2 归因维度与指标

查询至少支持以下稳定维度：

- Scope、Project；
- Workflow、WorkUnit、iteration、Run；
- AgentDefinition、responsibility；
- RuntimeDriver 精确 package release；
- ModelProviderConnection、request model、response model、API type；
- usage kind、Tool kind、MCP Server；
- observed time。

模型调用指标包括：call count、success/failure、retry、input/output total Token、cache read/write Token、reasoning Token、modality Token、total latency、TTFT、finish reason 和可用成本。Tool/Runtime 指标包括调用数、success/failure、duration、启动延迟和可证明的外部单位。

#### Usage ErrorCode 闭集与 outcome 绑定

`model-call` 和 `tool-call` 的 `error_code` 只能取以下固定值：`error`、`unknown`、`timeout`、`rate-limited`、`unauthorized`、`invalid-request`、`authentication-failed`、`validation-failed`、`content-filtered`、`overloaded`、`transport-error`。RuntimeDriver、Provider 或外部工具的原始错误文本必须在进入契约前分类为闭集值，绝不能进入 payload、Summary 或 JSON；不能安全分类时留空，并通过 `unknown_fields` 的 `field=error_code` 与 `reason=not_reported|unsupported|unverifiable` 表示，或使用 `unknown` 表示已报告但类别未知。`error_code` 只与 `outcome=failed` 一起出现；成功调用必须省略它，失败调用可以用结构化未知原因代替安全代码。该规则同时适用于记录验证、脱敏 Summary 验证和 JSON 序列化。

Agent 效率指标不复制进 UsageRecord。`QueryUsageAnalytics` 在授权范围内关联不可变 WorkUnit state/iteration、Run outcome、ReviewResult、GateResult、Artifact 和 WorkflowOutcome，计算成功 WorkUnit 数、Review Reject、返工轮次、每个成功 WorkUnit/批准 Artifact 的 Token 与成本。它只形成瞬时 `UsageAnalyticsView`，不创建 UsageAggregate、AgentEfficiencyRecord 或第二套状态机。

### 9.3 Query Use Cases

Platform Core Query Application 提供两个主要接口：

| Use Case | 中文作用 | 结果 |
| --- | --- | --- |
| `QueryUsageAnalytics` | 查询 Usage 汇总、时间序列、分组、排行、分布、周期对比和覆盖率 | 瞬时 UsageAnalyticsView |
| `QueryUsageRecords` | 查询可分页原始 UsageRecord | 脱敏明细与 next cursor |

`QueryUsageAnalytics` 输入如下：

| 英文字段 | 中文字段释义 | 关键约束 |
| --- | --- | --- |
| `time_range` | 查询时间范围 | 服务端限定默认和最大范围 |
| `filters` | 归因筛选 | 只允许当前 Scope/Project 授权范围内的 Project、Workflow、WorkUnit、Agent、责任、Runtime、Provider、模型、Usage kind |
| `group_by[]` | 聚合维度 | 使用受控维度目录；高基数维度返回 Top-N + others，不做无界组合 |
| `bucket_width` | 时间桶粒度 | `auto\|minute\|hour\|day`；服务端按范围限制 |
| `metrics[]` | 指标集合 | 由内置页面预设产生，普通用户不手工编写 |
| `compare_with` | 对比周期 | none、previous-period 或明确时间范围 |

`QueryUsageRecords` 复用相同筛选，并增加基于 `(observed_at, usage_record_id)` 的稳定 cursor 与 page size。图表点击只把当前维度值加入筛选，然后通过同一 API 下钻，不创建另一套页面事实。

### 9.4 Usage Explorer 内置分析面

统一 Usage Explorer 至少提供六个视图：

1. **总览**：总 Token、模型调用、AgentRun、成功 WorkUnit、已知成本、P50/P95、缓存率、周期变化，以及 Token/成本/错误时间序列；
2. **模型与 Token**：Provider/请求模型/实际模型对比、互斥 Token 桶、cache/reasoning、多模态、TTFT、错误和 finish reason；
3. **Agent 效率**：Agent/责任排行、成功率、Review Reject、返工轮次、每成功 WorkUnit Token/成本，以及成本-时长散点图；
4. **Workflow 与 WorkUnit**：Project→Workflow→WorkUnit 分布、exclusive/inclusive、阶段排行和 DAG 节点 Usage badge；
5. **Runtime 与 Tool**：Runtime 启动/总时长、Tool/MCP 调用数、错误率、P95 duration；只显示有可靠来源的数据；
6. **原始明细**：精确 UsageRecord、source event、Run/WorkUnit/Workflow 下钻和数据来源。

首版提供丰富固定视图、全局筛选、图表联动、周期对比和筛选条件复用；筛选通过 URL 查询参数和可选浏览器本地偏好保存，不创建服务端 SavedFilter 聚合、表或共享状态机。首版不建设拖拽 Dashboard Builder、自定义 SQL 或任意图表 Schema。

### 9.5 数据覆盖率与成本

每个指标返回 `known_count`、`unknown_count` 和 `coverage_ratio`。页面必须显示 Token、Cost、TTFT、Reasoning、Cache 等覆盖率；unknown 不能进入 0 值平均数，也不能在排名中伪装为免费或零延迟。`unknown=true` 的读模型值必须带 `unknown_reason`（`not-reported`、`unsupported`、`unverifiable`、`insufficient-data` 或 `redacted`）；known 值不得带该字段，排行拒绝 unknown 值。

Token 总量与 cache/reasoning 子集不能直接相加。只有输入总量、cache read 和 cache write 全部已知时，才计算 `uncached_input = input_total - cache_read - cache_write`；只有输出总量和 reasoning 全部已知时，才计算互斥 visible/reasoning 输出桶。出现负值、Provider 口径冲突或单位不明时，记录 Finding 并把派生值标为 unknown。

成本遵循 `provider-reported > platform-calculated > unavailable`。平台计算只能使用 AgentRun 创建时从不可变 ModelCatalogSnapshot 固定的精确 pricing basis，且实际 response model 必须匹配；没有 basis、模型不匹配或单位不完整时为 unavailable。不同 currency 分开聚合。成本未知时不显示总成本或单位成本为 0。

### 9.6 存储与 OpenTelemetry 边界

PostgreSQL 保存不可变 UsageRecord，并至少提供 Scope/time、Project/time、Workflow/time、Agent/time、Provider+model/time、usage kind/time 与 source event 唯一访问路径。允许维护小时/天级可重建读模型加速 Dashboard，但它们不是业务聚合、不能作为原始明细或计费权威。

首版不依赖 OpenTelemetry SDK、Collector、OTLP Receiver 或 OTel 数据库。OAC 可以在 RuntimeDriver 中参考 GenAI Semantic Conventions，也可以在未来提供可选异步 OTLP Export Adapter，但 OTel Span/Metric 不进入领域代码、不回写 UsageRecord、不参与 Run 终态、不成为 Usage Explorer 查询来源。Compact 和 External Cluster 默认部署均不因 Usage 功能增加 OTel Workload。

用户可以从 Usage Explorer 跳转到 Workflow 手动 Pause/Cancel；Usage 查询本身不能调用 Workflow Controller 或 Orchestrator Kernel。

## 10. Governance 强制边界与扩展面

Governance 首版只有 Authentication 作为公开 Extension API：AuthenticationDriver 使用统一 ExtensionPackage 和 `authentication.oac.dev/v1`。Audit Export 与 Credential Storage 保持平台内部 Port 和内置实现。

AuthorizationEngine 和 ExecutionAdmissionEngine 是 OAC 唯一的内置治理能力，分别读取授权策略/访问关系/请求事实，以及精确执行请求/当前显式事实/固定准入规则；都不进入 ExtensionCatalog 或企业 Provider 配置。

它们：

- 不进入 Harness Host；
- 不进入 Workflow Harness；
- 不由 Solution 决定是否调用；
- AuthenticationDriver 使用 ExtensionCatalog 与 Authentication SDK 接口检查；Builtin Local 与第三方实现走相同调用路径；
- AuthenticationDriver 在 Principal/Scope 建立前，根据平台或未来显式 organization/realm 认证入口选择；Project、Solution 和 Workflow 不能选择登录实现；
- Principal/Scope 建立后，AuthorizationEngine 固定执行当前 Scope 的唯一 AuthorizationPolicy；执行对象创建固定调用 ExecutionAdmissionEngine；可选 AuditExportTarget 与内置 CredentialStore 再按适用的 Governance 配置运行；
- AuthorizationDecision/AuditEvent 记录 Policy ID/revision/digest 和匹配授权项；ExecutionAdmissionDecision/AuditEvent 记录 rules/facts digest、检查结果和约束；Authentication 调用记录精确 Driver release/digest；
- 不进入 Workflow LockedComponentSet，不创建版本化治理绑定聚合或 Workflow 治理基线。

## 11. Project 与 Scope

Project 使用 `owner_scope_id` 归属于 Scope。首版 Scope 指向一个本地组织。

未来可以增加 Organization、Tenant 和 DelegationGrant，但不能改变：

- Project 领域含义；
- Workflow 状态所有权；
- Authorization 调用方式；
- Usage 归属；
- Audit 因果链；
- 输入输出 Contract。

首版不支持跨 Project Child Workflow。

## 12. 安全 Gate

Solution 或 Project Policy 可以要求：

- Secret detection；
- dependency vulnerability scan；
- SAST；
- license check；
- infrastructure configuration scan；
- container image scan；
- data classification review。

高风险发现不能由执行 Agent 忽略，只能产生失败 GateResult 或等待授权 Decision。

风险接受或风险例外 Decision 必须同时具有满足风险合同的 `structured_result` 和非空 `reason`，并固定精确风险 subject ID/digest、影响范围、约束、Principal、ApproverRequirement 和 AuthorizationPolicy ID/revision/digest。该 Decision 只满足对应业务 Gate 条件，不能授予合同外权限，也不能绕过后续 Authorization、Execution Admission、CredentialBroker、Sensor 或外部结果 Observe。

## 13. Fail Closed

| 边界 | 无法确认时 |
| --- | --- |
| Authentication | 不进入 Use Case |
| Bootstrap / Invitation / Recovery | 不创建 Principal、不建立 Scope 访问关系、不修改密码；Token 未知状态不能重放 |
| Browser Session | 拒绝请求并要求重新认证；客户端 Cookie 不能作为继续授权依据 |
| Authorization | 拒绝 |
| Execution Admission | 不创建执行对象 |
| Credential | 阻止需要凭证的调用 |
| Execution Security | 不创建 Workspace/Runtime |
| Local AuditEvent | 不提交状态写入 |
| External Audit Export | 本地成功，异步重试导出 |

## 14. 治理验证

治理验证至少覆盖：

- 首个管理员 Bootstrap Code 单次消费、并发 CAS 和完成后入口关闭；
- 首版公开注册关闭，管理员 Invitation 与用户自行设置密码完整闭环；
- SMTP 可选时邮件与一次性复制链接两条交付路径等价，Token 明文不持久化；
- Invitation 接受/过期/撤销/重新签发、Browser Session 服务端退出、唯一 Scope 自动进入和 Recovery 后 Session 撤销；
- LocalUserInvitation、Browser Session 与 Recovery Challenge 不形成 Version、Workflow 或产品状态机；
- RequestContext 全链路传播；
- Authentication 与 Authorization 分离；
- 只有一个 OAC AuthorizationEngine，不存在 AuthorizationProvider Package、Registry、Binding 或按 Project/Solution 选择实现；
- AuthorizationPolicy 使用稳定身份、CAS revision 和 digest，Allow-only grants 与固定 invariants 共同默认拒绝；
- Policy Candidate 评估不持久化，采用时重新校验，未知 Action/Subject、孤儿 access profile、管理锁死和绕过固定规则必须阻塞；
- Authorization 和 Execution Admission 使用唯一内置引擎并 Fail Closed；
- Execution Admission 不建立 Provider/Registry/Binding、用户管理 Policy 或独立设置页，外部系统只能贡献类型化检查事实；
- 状态写入与 Audit 原子性；
- 统一 Audit Explorer 只读取本地 AuditEvent，并按 Scope/Project 授权、服务端脱敏和稳定游标查询；
- 普通成功审计读取不递归写事件，拒绝、导出与受控高敏感元数据展示可审计；
- AuditEventDetailView 可重建且不形成第二套聚合、表或状态机；
- 本地 AuditEvent/Audit Explorer 始终启用且不可替换，每个 Scope 最多一个可选 AuditExportTarget；
- AuditExportPort 只按脱敏批次和连续游标异步外发，无目标是合法默认状态，外部系统故障不能阻塞业务或本地查询；
- 同一 source event 只形成一个 tagged UsageRecord，重复 sequence/digest 不重复 Token 或成本；
- request/response model、Token total/cache/reasoning 子集、TTFT、Tool/MCP 和 unknown/coverage 语义；
- 父子 Workflow exclusive/inclusive、不同币种与 Agent 效率关联查询不重复计量；
- Usage Explorer 图表可下钻原始记录，且不创建 UsageAggregate、Budget 或自动控制路径；
- 无 OpenTelemetry 依赖时完整 Usage 主链可运行，可选 OTLP Export 失败隔离；
- Credential 不可枚举和不落明文；
- 内置 CredentialStore 默认就绪且走共同 Port；普通表单不选择存储实现、不保存 rotation policy，首版不存在第三方 Provider、跨后端迁移或回退；
- Execution Security Capability 拒绝；
- Authorization Policy provenance、Execution Admission rules/facts provenance，以及 AuthenticationDriver 适用时的精确 package release/digest 来源；
- 内置与第三方 AuthenticationDriver 运行同一 Authentication SDK 接口检查；
- Harness Extension 无法绕过强制链路。

## 15. 删除资格、引用保护与历史保留

Governance 不提供万能资源删除服务，也不解释 Project、Agent、Workflow、Artifact 或 Component 的业务引用。每个资源所属应用模块负责定义“什么可以归档、什么可以恢复、哪些引用阻止删除”；Governance 只提供所有删除类命令都必须经过的共同约束：

1. Authorization 校验当前 Principal 是否可以处置精确 Scope 与 subject；
2. Retention Policy 判断当前资源类型、数据分类和时间要求是否允许删除正文或仅允许归档；
3. Legal Hold 判断是否存在诉讼保全、调查、合规或安全事件保留要求；
4. Audit 在资源修改或删除的同一事务中记录命令、引用检查摘要、Policy/hold 结论和最终结果；
5. 删除后保留最小 subject tombstone，使既有 AuditEvent、Decision 因果和外部证据仍可解释。

这里的 tombstone 只是保留在既有 AuditEvent/历史索引中的最小字段集合，不具有独立 ID、Repository、生命周期或状态机。

资源查询可以组合所属模块返回的业务引用与 Governance 返回的 policy blockers，形成瞬时 `allowed_actions[]`、`reference_summary` 和 `deletion_blockers[]`。这些值不是可写字段、资源 Version、DeletionRequest 聚合或后台处置状态；客户端也不能把一次查询结果当成删除令牌。执行命令时，Platform Core 必须在同一事务中重新检查引用、expected revision、Authorization、Retention 和 Legal Hold，任一条件变化都整体拒绝。

普通用户删除采用“资格满足即立即删除，否则明确阻塞”的模型，不创建 `deletion_scheduled`、Recently Deleted、通用恢复窗口或用户可操作的异步清除 Workflow。需要长期物理清理大对象、日志或终态 Kubernetes 资源时，由运维 Retention 任务按类型化 Policy 执行；该任务不能改写 Workflow lifecycle、绕过业务引用保护或删除本地 AuditEvent 权威历史。

归档不是所有平台资源都必须实现的共同能力。SolutionPackage/ContentPackage/ExtensionPackage release 继续使用 Disable、Revoke 与受引用保护的卸载语义；CredentialBinding 使用 Rotate、Disable 与 Revoke；ModelProviderConnection 和 McpServerDefinition 仅在其领域规则允许且没有 Project/Workflow/AgentDefinition 使用引用时删除，否则使用各自现有的禁用能力。统一 Settings 可以呈现相似的操作位置，但不能为了界面一致而给所有对象增加 `archived_at` 或通用处置枚举。

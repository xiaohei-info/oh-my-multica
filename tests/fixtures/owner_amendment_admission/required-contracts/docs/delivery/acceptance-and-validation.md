# Open Agent Cluster 验收与公开验证

> 业务目标：[云原生 Open Agent Cluster 业务解决方案设计](../open-agent-cluster-business-solution-design.md)
> 技术基线：[云原生 Open Agent Cluster 概要设计](../design/open-agent-cluster-overview-design.md)
> 内置场景：[生产级软件交付详细设计](../design/detailed/11-software-delivery-detailed-design.md)

本文定义平台、内置软件交付方案、行业扩展和部署能力的可执行验收口径。Agent 的说明不能代替程序结果、外部事实、独立 Review 或用户 Decision。

本文是用户可见行为和端到端验收证据的权威文档。第 2.0 节保留此前逐项确认形成的 61 个唯一 `UJ-*` 用户旅程；历史 `OP-*` 对齐编号和讨论进度仅保存在[归档路线图](../archive/2026-07-24-global-user-operation-path-map.md)中，不再作为开发前置文档或第二套产品设计。

## 1. 文档与架构边界验收

- 业务解决方案只解释业务问题、用户、价值、业务闭环、范围和风险；
- 概要设计只解释领域边界、功能架构、系统架构、模块协作和非功能目标；
- 领域内部模型、接口、状态、失败和恢复位于详细设计；
- 字段、枚举、幂等和版本语义位于对应 Reference；
- 部署运行与实施验收不回流到业务解决方案正文；
- 文档、代码、API、CRD 和 Web 使用相同领域术语，不出现无法判断归属的裸 `Spec`、`Proposal`、`Revision` 或 `Record`。

## 2. 通用平台验收

### 2.0 用户旅程验收执行入口

用户旅程按已确认顺序逐段增加验收用例。开发、Review、QA 和最终用户验收都从本节执行，不得用模块单测或页面存在代替完整用户操作。

| 用户旅程段 | 对齐状态 | 验收用例 |
| --- | --- | --- |
| 浏览公开网站、技术文档和案例并切换语言 | 已确认 | `UJ-PUBLIC-ENTRY-001` |
| 通过统一安装器 CLI 部署 Compact/External Cluster 并进入 Web | 已确认 | `UJ-PLATFORM-INSTALL-001` |
| 通过 Installer CLI 升级或回滚 OAC 系统发行版 | 已确认 | `UJ-OAC-UPGRADE-001` |
| 初始化首个管理员、邀请普通用户、登录退出、恢复身份并进入授权 Scope | 已确认 | `UJ-IDENTITY-001` |
| 配置可替换 AuthenticationDriver 与 AuthenticationMethod | 已确认 | `UJ-AUTHENTICATION-METHOD-001` |
| 第三方按公开 SDK 开发 ExtensionPackage、执行同源检查并一键安装启用 | 已确认 | `UJ-EXTENSION-PACKAGE-001` |
| 配置 OAC 统一 AuthorizationPolicy | 已确认 | `UJ-AUTHORIZATION-POLICY-001` |
| 执行创建自动经过 OAC 统一 Execution Admission | 已确认 | `UJ-EXECUTION-ADMISSION-001` |
| 保持本地审计权威并按需配置内置类型的外部导出 | 已确认 | `UJ-AUDIT-EXPORT-001` |
| 使用内置 CredentialStore 管理最小权限 CredentialBinding | 已确认 | `UJ-CRED-001` |
| 查看平台综合健康、当前活动告警和执行容量 | 已确认 | `UJ-PLATFORM-OPERATIONS-001` |
| 能力准备与 Unified Settings | 已确认 | `UJ-CAP-001`、`UJ-SKILL-001`、`UJ-MODEL-001`、`UJ-MODEL-002`、`UJ-MCP-001` |
| Agent Center 创建逻辑 Agent | 已确认 | `UJ-AGENT-001`、`UJ-AGENT-002`、`UJ-AGENT-003`、`UJ-AGENT-004`、`UJ-AGENT-005`、`UJ-AGENT-006` |
| 创建 Project 与选择 Solution | 已确认 | `UJ-PROJECT-001`、`UJ-PROJECT-002`、`UJ-PROJECT-003` |
| Solution Studio 配置与输入定义、固定核心定义、Baseline 创作、Harness 装配、发布检查与发布启用 | 已确认 | `UJ-SOLUTION-001`、`UJ-SOLUTION-002`、`UJ-SOLUTION-003`、`UJ-SOLUTION-004`、`UJ-SOLUTION-005`、`UJ-SOLUTION-006` |
| Project 配置、Agent Slot、Setup 检查、原子采用与后继 Workflow | 已确认 | `UJ-PROJECT-004`、`UJ-PROJECT-005`、`UJ-PROJECT-006`、`UJ-PROJECT-007`、`UJ-PROJECT-008` |
| 创建 Workflow/Delivery 与显式 DAG Orchestration 扩图 | 已确认 | `UJ-WORKFLOW-001`、`UJ-WORKFLOW-002` |
| 独立 Orchestrator Kernel 从已有不可变设计文档生成并推进 DAG | 已确认 | `UJ-KERNEL-001` |
| 读取不可变工作协议并提交结构化结果 | 已确认 | `UJ-RUN-001` |
| Requirement 与 Acceptance Criteria 的澄清、原子批准和返工 | 已确认 | `UJ-REQUIREMENT-001` |
| Solution Design、Technical Overview 与 Detailed Design 的逐份 Review、批准和返工 | 已确认 | `UJ-DESIGN-001` |
| 用户级端到端 Acceptance Plan 的 Review、批准和返工 | 已确认 | `UJ-ACCEPTANCE-PLAN-001` |
| Project Rules 的 Review、一次批准、AGENTS.md 自动安全应用和冲突处理 | 已确认 | `UJ-PROJECT-RULES-001` |
| Implementation Plan 的条件性 Decision Boundary 审批、返工和自动激活 | 已确认 | `UJ-IMPLEMENTATION-PLAN-001` |
| 只读全局 DAG、Workflow 全局时间线与 WorkUnit 连续任务线联动 | 已确认 | `UJ-WORKFLOW-OBSERVABILITY-001` |
| 回答问题、要求修改、拒绝、放弃和风险授权的正式 Decision | 已确认 | `UJ-DECISION-001` |
| Pause 软暂停、Resume、Cancel 与 Abandon 的控制和收敛 | 已确认 | `UJ-WORKFLOW-CONTROL-001` |
| Workflow 内 ComponentRun 的 Unknown 外部结果、Observe、安全 Retry 与 Cancel | 已确认 | `UJ-EXTERNAL-UNKNOWN-001` |
| 动态责任缺少执行器时为当前 Workflow 选择 Agent 或显式重编排 | 已确认 | `UJ-EXECUTOR-RESOLUTION-001` |
| 人工作为 Reviewer，或作为显式 Acceptance WorkUnit 主执行者提交结构化结果 | 已确认 | `UJ-HUMAN-RESPONSIBILITY-001` |
| 查看只读 Release 候选及其固定证据 | 已确认 | `UJ-RELEASE-CANDIDATE-001` |
| 访问 Preview 并提交绑定当前部署精确事实的最终验收 Decision | 已确认 | `UJ-PREVIEW-FINAL-ACCEPTANCE-001` |
| 最终验收要求修改并进入 Architect 技术变更设计、Review、用户批准和后续 DAG Orchestration | 已确认 | `UJ-PREVIEW-REQUEST-CHANGES-001` |
| 查看 Promotable Release 并可直接 Promotion 到外部目标 | 已确认 | `UJ-DIRECT-PROMOTION-001` |
| 观察直接 Deployment、取消、失败重试与部署旧 Release | 已确认 | `UJ-DIRECT-DEPLOYMENT-RECOVERY-001` |
| 查询 AuditEvent、治理证据链并执行受控导出 | 已确认 | `UJ-AUDIT-001` |
| 使用图表、排行、对比和明细分析 Agent、模型与 Token Usage | 已确认 | `UJ-USAGE-001` |
| 人类 Web 与外部 Agent `oactl` 通过同一 Platform API 操作和观察平台 | 已确认 | `UJ-EXTERNAL-AGENT-CLI-001` |
| 通过 Generic Web 完整运行非软件行业 Solution，并在声明式领域体验不可用时安全回退 | 已确认 | `UJ-GENERIC-SOLUTION-001` |
| 归档、恢复和安全删除 Project、逻辑 Agent、终态 Workflow 与历史内容 | 已确认 | `UJ-RESOURCE-LIFECYCLE-001` |

#### `UJ-PUBLIC-ENTRY-001`：浏览公开网站、技术文档和案例并切换语言

- 前置条件：准备 `zh-CN` 与 `en-US` 两个已启用 locale；准备产品概览、行业方案/案例和安装文档的双语页面，每对页面共享相同 `page_key`（页面稳定标识）；另准备一个只有 `en-US` 内容的页面、一个重复 `page_key` Fixture、一个默认英文内容缺失 Fixture，以及可访问的产品 Console 入口。
- 用户操作：
  1. 清除登录状态和站点本地偏好，设置浏览器首选语言为 `zh-CN` 后访问无语言前缀入口，确认进入对应中文 Route；将浏览器语言改为不支持的 locale，确认进入 `en-US` fallback；
  2. 在产品概览页查看产品能力、行业方案/案例、技术文档和顶部语言入口，确认页面不要求登录，也不创建账户、Project、Workflow 或其他平台资源；
  3. 从 `zh-CN` 产品概览、案例和某一具体文档页面分别切换到 `en-US`，确认目标 Route 的 `page_key` 不变，只改变 `locale`，页面标题、当前章节和导航位置仍对应同一内容；再切回中文确认同样成立；
  4. 在目标中文翻译缺失的页面选择 `zh-CN`，确认系统保留该页面的中文 Route，渲染同一 `page_key` 的 `en-US` 内容，显示“该页面暂未提供所选语言，当前显示英文版本”之类的明确提示，并返回 `translation_status=fallback`；不得跳到首页、无关页面或无上下文 404；
  5. 显式选择一种语言后重新打开无前缀入口，确认浏览器本地偏好优先于浏览器自动语言；清除本地偏好后才重新使用浏览器语言。未登录偏好不写入服务端个人设置；
  6. 从公开导航点击“开始安装”，确认进入当前语言对应的 Compact/External Cluster 安装文档并衔接安装旅程；点击“进入控制台”，确认只跳转到目标产品 SPA，由后续登录与 Scope 流程接管；
  7. 使用键盘和屏幕阅读器操作语言切换，确认控件具有可访问名称、当前语言状态和完整焦点顺序，不使用国旗替代语言文本；
  8. 执行静态内容构建：目标翻译缺失允许以明确 fallback 发布；默认 `en-US` 内容缺失、同一 locale 内 `page_key` 重复、翻译映射冲突或站内链接无法解析时构建失败；
  9. 检查浏览器网络、Platform Core、PostgreSQL、Workflow CR 与 AuditEvent，确认普通浏览和语言切换不调用 Platform API，不创建 RequestContext、业务对象或审计事实。
- 用户可见预期：访客可以在不登录的情况下理解产品、案例和部署方式，并在中文与英文之间保持同页切换。翻译不完整时页面明确说明当前显示英文 fallback；“开始安装”和“进入控制台”分别衔接后续安装与登录旅程。
- 平台处理预期：SSG 构建与公开内容路由根据 `page_key + locale` 生成页面和语言映射；无前缀入口只使用浏览器本地偏好、浏览器语言和 `en-US` fallback。公开入口不进入 AuthenticationDriver、Platform API、Platform Core、Orchestrator Kernel 或 Workflow Controller。
- 不通过条件：使用标题、文件名或 URL slug 猜测翻译关系；切换语言后跳到首页或无关页面；缺少翻译直接 404 或静默混入另一语言；把 locale 保存成 Project/Workflow/Scope/Solution 属性；未登录浏览创建平台记录；默认英文页缺失仍允许发布；语言控件不可键盘操作或使用国旗表示语言；“开始安装”和“进入控制台”指向同一模糊入口。
- 所需证据：中英文产品页/案例/文档截图与 URL、同一 `page_key` 的构建清单、`available|fallback` 对照、浏览器本地偏好与 Accept-Language 组合测试、缺失翻译提示、键盘与屏幕阅读器记录、安装/控制台跳转、静态构建成功与失败日志、浏览器网络请求以及 Platform Core/数据库/CRD/Audit 无写入证明。

#### `UJ-PLATFORM-INSTALL-001`：通过统一安装器 CLI 部署并完成首次可用检查

- 前置条件：准备当前发布版的 Linux、macOS 和 Windows 安装器 CLI；准备一台没有 Kubernetes 的受支持 Linux 主机、一台已有兼容 Kubernetes 的 macOS 主机、一台已有兼容 Kubernetes 的 Windows 主机、一个没有兼容 Kubernetes 的 macOS/Windows 反例环境，以及一个可访问的 External Cluster。目标集群分别准备“存在唯一默认 StorageClass”“存在多个候选但没有唯一默认值”“没有满足要求的存储能力”三组 Fixture。
- 用户操作：
  1. 从公开安装文档分别获取对应操作系统的安装器 CLI，运行环境诊断，确认输出使用产品语言说明 CPU、内存、磁盘、网络、Kubernetes、权限、存储和入口检查，不要求用户先理解 Pod、CRD 或 Controller；
  2. 在没有 Kubernetes 的 Linux 主机选择 Compact，查看“将安装本机 K3s”的确认摘要后继续；不填写 StorageClass Name、Namespace 或 IngressClass；
  3. 在 macOS 和 Windows 主机运行同一安装流程，确认 CLI 只列出实际探测到的兼容 Kubernetes Context；存在唯一候选时推荐该候选，存在多个候选时要求用户明确选择，不自动切换；
  4. 在没有兼容 Kubernetes 的 macOS/Windows 反例环境运行安装，确认 CLI 在部署前阻塞，展示受支持环境和可执行安装指引，不下载或创建 Desktop App、WSL、虚拟机、托管集群或半完成 OAC 实例；
  5. 选择 External Cluster，确认 CLI 展示精确 Context、集群地址、权限检查和安装范围；拒绝错误 Context 或不可达集群后重新选择，不允许安装器静默改用其他集群；
  6. 在唯一默认 StorageClass 的集群确认自动采用；在候选不唯一的集群从 CLI 展示的名称、来源、用途和推荐项中选择；在没有满足要求候选的集群确认安装在创建业务 Workload 前阻塞；
  7. 确认安装摘要并等待部署收敛；安装完成后查看平台组件健康、产品 Web 地址和只展示一次的首任管理员 Bootstrap Code，确认 CLI 不收集管理员密码；执行“打开控制台”操作，确认系统默认浏览器打开同一个 OAC Web，身份初始化由下一验收段处理；
  8. 分别从 Linux、macOS、Windows 浏览器访问 Web，并查询公开健康/API 入口，确认三者没有 Desktop 专属页面、接口、认证状态或产品功能差异；
  9. 在安装命令响应丢失或本地等待超时后使用同一目标重新执行，确认安装器识别已有安装并继续观察或补齐，不创建第二套 Namespace、PostgreSQL、控制面或入口；
  10. 检查 Platform Core、PostgreSQL 和 Workflow CR，确认首次安装与打开 Web 尚未创建任何用户 Project、用户发布的 AgentDefinition、Project Solution Setup 或 Workflow；发行包内置 Component、Solution 和 AgentTemplate 可以由平台启动过程注册，相关系统安装审计不得伪装成用户业务操作。首次身份与 Scope 创建由下一段用户旅程处理。
- 用户可见预期：平台运维在三个操作系统上只学习一个安装流程。Linux 可以在没有集群时安装 K3s；macOS 和 Windows 复用已有环境，缺少环境时得到明确指引。安装完成后所有用户都进入同一个浏览器 Web，普通路径不暴露必须手工填写的 Kubernetes 内部名称。
- 平台处理预期：安装器 CLI 是产品控制面之外的部署工具，负责宿主检测、Kubernetes 预检、发行包应用、健康等待和 Web 地址输出；它不调用 Orchestrator Kernel，不复用 `oactl`/`oacok` 身份，不创建平台业务对象。唯一默认基础设施能力自动解析，歧义通过探测候选选择解决，未知或不满足要求时 Fail Closed。
- 不通过条件：要求安装 Desktop App；macOS/Windows 在没有 Kubernetes 时静默创建 VM/WSL；把 K3s 当作三个操作系统都能原生运行；要求普通用户手工输入 StorageClass Name、Namespace 或 IngressClass；自动切换到未确认 Context；安装失败后产生第二套控制面；安装完成后不同操作系统进入不同业务 UI/API；安装过程创建 Project/Workflow；用单元测试或 Helm 渲染成功代替浏览器首次可用验证。
- 所需证据：三种操作系统的 CLI 诊断和安装录屏、Linux K3s 创建记录、macOS/Windows 已有 Context 探测结果、缺少环境 blocker、External Context 确认摘要、StorageClass 自动解析与候选选择记录、控制面健康结果、Bootstrap Code 单次展示且 CLI 无密码输入证明、浏览器 Web 截图与 API 响应、幂等重试资源清单，以及没有用户 Project、用户 AgentDefinition、Project Solution Setup 或 Workflow 的证明；系统内置包注册和安装审计需能与用户业务操作区分。

#### `UJ-OAC-UPGRADE-001`：通过 Installer CLI 升级或回滚 OAC 系统发行版

- 前置条件：准备一个运行 OAC 系统发行版 N 的 Compact 或 External Cluster、一份签名和 digest 正确且声明支持从 N 升级的目标发行版 N+1、上一精确发行版 N 的可恢复镜像/清单、与目标兼容的 Installer CLI，以及以下 Fixture：错误 digest、`latest`/版本范围、错误 Kubernetes Context、不兼容 Kubernetes、CRD/数据库检查失败、不可逆数据库迁移、兼容检查 unknown、CLI 中断和 `rollback_supported=false`。平台中准备多个活动 Workflow、固定 SolutionPackage/ContentPackage/ExtensionPackage release、Project/AgentDefinition、Software Delivery Release/Deployment，以及一个独立使用 OAC SDK N 的外部测试客户端。
- 用户操作：
  1. 运行 Installer `status`，确认能够从实际 Workload 镜像 digest、发行元数据和数据库迁移级别解析 `current_release`（当前 OAC 系统发行版）；制造来源不一致时必须显示 partial/unknown 和精确差异，不能猜测一个绿色版本；
  2. 发起升级，只提供 `target_release`（目标 OAC 系统发行版，精确 SemVer + artifact digest）和安装器探测出的 `kubernetes_context`（Kubernetes 目标上下文）；分别尝试 `latest`、版本范围、仅 SemVer、错误 digest 和未确认/错误 Context，确认全部在变更前拒绝；
  3. 对合法目标查看升级摘要，确认完整展示 `current_release`（当前系统发行版）、`target_release`（目标系统发行版）、`compatibility_checks[]`（兼容性检查列表）、`migration_summary`（迁移摘要）、`rollback_supported`（是否支持安全回滚）、`active_workflow_summary`（活动 Workflow 摘要）和 `affected_workloads[]`（受影响 OAC 工作负载），且未确认 `confirm`（执行确认）时集群没有变化；
  4. 依次使用错误签名/digest、不兼容 Kubernetes、CRD/数据库不兼容、不可逆迁移、Required 外部依赖 unknown 和 N/N-1 协议不兼容 Fixture，确认摘要标出 blocker，Installer 不修改 CRD、数据库、Deployment 镜像或活动 Workflow；
  5. 对有效目标确认升级，观察执行顺序固定为：先应用向后兼容的 CRD/数据库扩展，再滚动 Platform Core，然后滚动 `oac-execution-host`（其中包含 Job Observer、Harness Host 与 Deployment Host 模块，并更新后续 AgentRun Job 使用的 Runtime SDK/调用代码镜像），再滚动 Workflow Controller，最后滚动 Web；每组 readiness 通过后才进入下一组，已经运行的 AgentRun Job 不做进程内热替换；
  6. 在 Platform Core、Execution Host 和 Workflow Controller 分别处于 N/N+1 混合阶段时持续执行公开 API、内部 Command/Report/Event 和活动 Workflow，确认相邻版本可互操作，不出现重复 Run、丢失报告、非法 Gate 或 Workflow 热迁移；
  7. 升级完成后检查系统服务和嵌入镜像中的 Orchestrator Kernel、Runtime SDK、内部 WorkspacePort 已随 N+1 更新；同时确认 Kubernetes/K3s、节点 OS、外部 PostgreSQL/对象存储版本、已安装 Package release、Project、AgentDefinition、Workflow spec/Plan/WorkUnit/Run/Decision/LockedComponentSet、Software Delivery Release/Deployment 和外部客户端依赖锁均未被修改；
  8. 让独立 SDK N 测试客户端调用 N+1 平台声明支持的接口，确认 N/N-1 兼容；再确认 Installer 不尝试更新该客户端代码、二进制或依赖文件；
  9. 在滚动过程中终止 CLI 或丢弃响应，重新运行 `status` 和同一精确目标的 `upgrade`，确认安装器读取 Kubernetes 实际状态、明确已完成/待完成组并幂等继续，不创建第二套产品级升级任务、Namespace、Workflow 或数据库升级状态机；如果数据库迁移由 Kubernetes Job 承载，重复执行必须复用同一迁移身份或确认既有结果，而不是形成第二个副作用；
  10. 升级后执行 `rollback`，确认目标只能是紧邻的上一精确 OAC 系统发行版 N，且只有摘要中的 `rollback_supported=true` 才能继续；尝试选择更早版本、任意 digest 或在 `rollback_supported=false` Fixture 上回滚必须被拒绝；
  11. 确认回滚按 Web、Workflow Controller、Execution Host、Platform Core 的兼容顺序恢复上一精确镜像和部署资源，保留新增但向后兼容的 CRD 字段与数据库 Schema，不执行破坏性 down migration；完成后原活动 Workflow 继续按同一 spec/status、Plan、Run 和 Decision 协调；
  12. 检查公开 Platform API、`oactl`、`oacok`、Workflow CR、PostgreSQL 产品表和 Kernel 调用追踪，确认升级/回滚没有创建 UpgradePlan、Version 聚合、Workflow、WorkUnit、Run、Decision、ComponentRun 或 Kernel action，也没有自动升级任何已安装 Package release 或触发用户外部 Deployment；
  13. 检查产品 Web、Installer 帮助和公开 Platform API，确认不存在“故障演练”用户页面、命令或业务对象；再由发布工程在受控环境执行部署运维文档中的故障清单，确认演练结果只作为系统发行版发布/运维交接证据。
- 用户可见预期：运维人员只需要选择一个带 digest 的精确 OAC 系统发行版、确认实际 Kubernetes Context、阅读清晰的兼容/迁移/影响摘要并确认执行。升级中断可以安全继续；只有明确支持时才能回到上一精确版本。用户不会把系统升级误解为已安装 Package release 升级、业务 Release 回滚或基础设施升级，活动 Workflow 不需要迁移或重启任务线。
- 平台处理预期：Installer CLI 在产品控制面之外校验发行物、组装瞬时摘要，并通过发布包管理、Kubernetes API 和数据库迁移工具执行有序滚动。CRD/数据库采用向后兼容扩展，Platform Core、Execution Host、Workflow Controller 和 Web 保持 N/N-1 窗口；当前状态每次从实际部署事实重建。Installer 不调用 Platform API、Workflow Controller 业务命令或 Orchestrator Kernel，也不持久化第二套升级领域模型。
- 不通过条件：允许 `latest`、版本范围、未固定 digest 或隐式 Context；兼容证据 unknown 仍继续；先升级 Controller 导致旧 Platform Core 无法处理；使用不可逆迁移后再声称可回滚；回滚任意历史版本；自动回滚掩盖未知状态；CLI 中断后创建第二套升级任务；改写活动 Workflow 或重跑 Agent；顺带升级已安装 Package release、基础设施、业务 Release/Deployment 或外部 SDK；通过 Web/Platform API/Workflow/Kernel 执行系统升级；把故障演练做成产品操作。
- 所需证据：Installer `status/upgrade/rollback` 录屏与结构化输出、精确 SemVer/digest/Context 拒绝记录、七项摘要字段及中文释义、签名和兼容性 Fixture、CRD/数据库迁移记录、各 Workload rollout 顺序与 readiness、N/N+1 混合阶段 API/Command/Report/Event 测试、活动 Workflow 前后 digest/对象对照、已安装 Package release/Project/Agent/业务 Release/Deployment/基础设施/外部 SDK 无变化证明、CLI 中断与幂等恢复、上一精确版本回滚与不支持回滚拒绝、数据库无 destructive down migration、无 UpgradePlan/Workflow/Kernel 对象计数，以及发布故障演练报告与产品入口全局检索。

#### `UJ-IDENTITY-001`：初始化管理员、邀请用户并进入授权 Scope

- 前置条件：准备一个刚完成安装且尚无本地 Principal/Scope 的实例及安装器一次性 `bootstrap_code`；准备“已配置 Notification Adapter”和“未配置邮件”两组实例；准备一个普通用户邮箱、一个重复邮箱、一个已过期 Invitation、一个已撤销 Invitation、两个并发接受请求、一个普通成员、一个具有用户邀请权限的管理员，以及“全部管理员失锁”的恢复 Fixture。首版实例只存在一个 Local Organization Scope。
- 用户操作：
  1. 首次打开产品 Web，确认只出现首任管理员初始化页；不输入 Code、输入错误 Code 或过期 Code 均不能创建账号，也不能自动把第一个访问者设为管理员；
  2. 输入安装器展示的有效 `bootstrap_code`、管理员登录名、显示名称和密码并提交；确认原子创建首个本地 Principal、Local ExternalIdentityLink、Local Scope、安全默认 AuthorizationPolicy 与管理员授权，Code 随即失效，用户通过统一 Session/RequestContext 链自动进入唯一 Scope；
  3. 并发提交另一份首任管理员信息，确认只有首个有效 CAS 成功；刷新或再次访问 `/bootstrap` 只进入登录页，不出现第二个首任管理员入口；
  4. 管理员进入 Administration / Users，点击“邀请用户”，只填写 `invited_email`（被邀请邮箱）、可选 `display_name_hint`（显示名称建议）和 `access_profile_key`（初始访问权限模板键）；未选择时使用当前 Policy 默认值，确认页面不要求管理员填写或生成普通用户密码；
  5. 在已配置 Notification Adapter 的实例确认一次性邀请发送到固定邮箱；在未配置邮件的实例确认页面仅在创建成功结果中显示一次可复制链接/Code，刷新后不再返回明文 Token；
  6. 直接访问 `/register` 或在登录页查找注册入口，确认首版明确关闭公开自助注册，不创建注册申请、Pending User 或管理员审批待办；
  7. 普通用户打开有效邀请，确认邮箱、Scope 和权限摘要固定，只填写或确认登录名、显示名称和密码；提交后原子创建 Principal、Local ExternalIdentityLink、认证材料与 Scope 访问关系并消费 Invitation，直接建立 Session，不等待第二次管理员审批；
  8. 分别使用已接受、已过期、已撤销和重新签发前的旧 Token 再次接受，确认全部拒绝且不创建重复 Principal；两个并发接受请求只有一个成功，相同幂等请求返回同一结果；
  9. 普通用户退出后重新登录，确认认证成功后自动进入唯一 Local Scope，账户区域持续展示当前 Scope；普通成员不能邀请用户，越权操作返回服务端 Authorization Deny；
  10. 点击退出，确认服务端先撤销 Browser Session，再清除 Cookie并返回登录页；使用旧 Cookie 调用 Platform API 被拒绝，Project、Workflow、Run 和 Decision 不发生变化；
  11. 管理员为普通用户发起一次性 Recovery，用户在 Web 中自行设置新密码，完成后旧 Session 全部失效；在全部管理员失锁 Fixture 中，由具备宿主/Kubernetes 管理权限的平台运维通过安装器 CLI 为精确管理员生成 break-glass Code，CLI 不接收新密码，用户仍在 Web 自行设置；
  12. 使用 Web 和一个具备相同用户邀请权限的 API Client 分别发起 Invitation，确认两者进入同一 Platform API、Governance Identity Use Case、Authorization、幂等和 Audit 路径；内部执行 Agent 的 `oacok` 不能访问身份管理入口；
  13. 检查身份存储、AuditEvent、Workflow CR、Orchestrator Kernel 与日志，确认密码和 Token 明文未落库/日志/Audit，Invitation/Session/Recovery 不创建 Workflow、WorkUnit、Version 或产品状态机。
- 用户可见预期：安装者可以安全创建唯一首任管理员；管理员以邀请方式批准普通用户加入，用户自己设置密码；没有 SMTP 也能通过一次性链接完成邀请。普通用户登录后直接进入唯一有权 Scope，退出和恢复结果明确，页面不存在公开注册和重复审批。
- 平台处理预期：Installer 只生成 Bootstrap/全管理员失锁 Recovery Code；日常身份管理通过同一 Platform API 的 Governance Identity Use Case。Invitation、Browser Session 与 Recovery Challenge 是短期认证支持记录；Local AuthenticationDriver 返回 AuthenticatedSubject，Governance 通过 ExternalIdentityLink 解析 Principal，Authorization 再决定后续操作权限。Invitation 接受与首任初始化均使用事务、幂等和条件 CAS，Token 只保存摘要，身份绑定、Scope 访问关系与 Principal 原子建立。
- 不通过条件：第一个访问者自动成为管理员；公开注册默认开启；注册申请形成 Pending Approval 状态机；管理员设置、读取或复制普通用户密码；未配置 SMTP 时无法邀请；邀请 Token 可被重复使用或刷新后再次读取明文；旧 Token 重新签发后仍有效；登录后客户端自行伪造 Scope；退出只清 Cookie而服务端 Session 仍有效；恢复由管理员或 CLI 代填新密码；身份流程创建 Workflow、Decision、Version 或调用 Kernel；只验证 AuthenticationDriver 单测而没有完成浏览器 E2E。
- 所需证据：安装器 Bootstrap/Recovery 输出、首任页面与并发初始化录屏、Users 邀请表单、SMTP 与无 SMTP 两种交付记录、公开注册关闭证明、Invitation 接受与各类失效 Token 响应、自动 Scope 展示、越权 Deny、退出前后 Session/API 结果、普通与 break-glass Recovery 记录、Web/API Client 等价请求、AuditEvent、脱敏日志，以及数据库/CRD 全局检查证明不存在明文 Secret、Pending Approval、身份 Version 或 Workflow/Kernel 路径。

#### `UJ-AUTHENTICATION-METHOD-001`：安装 Driver、配置认证方式并完成统一登录收敛

- 前置条件：准备已完成本地管理员初始化的实例；内置 Local AuthenticationDriver 与默认 Local AuthenticationMethod 已 Active；准备一个实现 `authentication.oac.dev/v1` 且通过 Authentication SDK 接口检查的 OIDC 风格测试 Driver、两个独立测试 IdP/issuer、一个保存 Client Secret 的最小权限 CredentialBinding、两个具有相同 verified email 但不同稳定 subject 的外部账号、一个未建立 ExternalIdentityLink 的账号、可制造过期/重放/配置摘要变化的 AuthenticationFlow Fixture，以及一名具有认证方式管理权限的管理员。
- 用户操作：
  1. 打开 Package & Extension Catalog，确认 Local 与外部 AuthenticationDriver 都使用共同 ExtensionPackage、ExtensionCatalog 和 Authentication SDK 接口检查；内置 Local 不存在私有注册、登录或授权旁路；
  2. 进入 `/administration/authentication`，创建两个复用同一精确 OIDC Driver 的 AuthenticationMethod，只填写 `name`（认证方式名称）、`description`（认证方式说明）、`authentication_driver`（精确 Driver ExtensionPackage）、Schema 驱动的非敏感 `configuration`（认证配置）和必要的 `credential_binding_ids[]`（凭证绑定身份）；确认页面不要求填写 Version、Project、Solution、Workflow 或明文 Client Secret；
  3. 分别使用错误和正确配置执行 `ProbeAuthenticationMethod`，确认返回脱敏诊断；再完成 `purpose=test` 的测试登录，确认测试成功仍不建立业务 Browser Session、不授予 Scope，也不自动启用方法；
  4. 使用当前 `revision + configuration_digest` CAS 启用两个方法，打开 `/login`，确认页面从 `QueryEnabledAuthenticationMethods` 动态展示各自名称、说明和有类型 interaction；前端没有按 OIDC、SAML、LDAP 或厂商名称硬编码按钮与回调；
  5. 使用 `browser-redirect` 方法完成登录，确认开始时创建短期 AuthenticationFlow，并固定 `authentication_method_id + method_configuration_digest`；分别重放已消费回调、提交过期 Flow、篡改 state、在 Method 配置变化后完成旧 Flow，确认全部拒绝且不创建 Session；
  6. 检查 Driver 输出，确认只有协议中立 AuthenticatedSubject，没有 Principal、Scope、角色或授权结果；服务端按 `authentication_method_id + issuer_key + subject_key` 精确解析 ExternalIdentityLink 后才创建 Browser Session 和 RequestContext；
  7. 使用两个 verified email 相同但来自不同 Method/issuer/subject 的账号登录，确认不会自动合并为同一 Principal；使用没有 Link 的账号确认默认拒绝并给出管理员绑定或 Provisioning Policy 提示，不以 email 猜测建号；
  8. 让外部 IdP 返回 group/claim，确认它们只进入归一化 attributes；随后访问受保护资源，确认最终 Allow/Deny 来自 OAC 唯一 AuthorizationEngine、当前 AuthorizationPolicy 和本地访问关系，而不是 AuthenticationDriver 直接授权；
  9. 更新同一 Method 的普通连接参数或 CredentialBinding，确认使用 CAS revision/digest；尝试把已有 Link 的 issuer/directory 身份域、协议或 subject 规范原地改成另一个身份源，确认服务端要求创建新 AuthenticationMethod。已有 Link/Session/Audit 引用的方法只能禁用/归档，不能硬删除；
  10. 禁用一个 AuthenticationMethod，确认它立即停止新 Flow，默认撤销该方法签发的现有 Session；若测试显式 Session Policy 保留到期，管理页必须提前展示影响。尝试在没有已验证替代方法或 break-glass 路径时禁用最后一个人类管理登录方式，确认服务端拒绝；
  11. 在 Project/Solution/Workflow 配置页和对象存储中检查，确认不存在认证 Provider 选择字段。再增加一个符合相同 Authentication SDK 的测试 Driver，确认只需安装 ExtensionPackage、配置 Method、Probe/Test/Enable，Platform Core、Project、Workflow 与登录页业务代码无需修改。
- 用户可见预期：管理员可以用一个简洁页面配置多个本地或企业登录入口，名称和说明原样出现在登录页；Secret 只选择 CredentialBinding；测试与启用分离，切换认证方式前能看到 Session 影响。最终用户只选择可理解的登录方式，不需要理解 Driver、OIDC endpoint 或 Project 配置。
- 平台处理预期：AuthenticationDriver ExtensionPackage 与 AuthenticationMethod 配置实例分离；Method 使用稳定 ID、CAS revision 和 configuration digest，不创建 Version 聚合。AuthenticationFlow 短期且单次消费；Driver 只产出 AuthenticatedSubject；ExternalIdentityLink 精确映射 Principal；Browser Session、Scope 解析、RequestContext、Authorization 和 Audit 在所有登录方式下复用同一链路。认证方法在 Principal/Scope 之前按平台或未来显式 organization/realm 入口选择，Project、Solution、Workflow、Harness 和 Orchestrator Kernel 均不参与。
- 不通过条件：每个企业 IdP 都要求修改 Platform Core 或新增专属登录页；内置 Local 绕过 ExtensionCatalog/Authentication SDK 接口检查；把一个配置实例也做成 ExtensionPackage 或 Version；在 Method 配置中保存明文 Secret；Probe/Test 自动启用或签发业务 Session；前端拼接 Provider URL；Flow 可重放、跨配置完成或开放重定向；Driver 直接返回角色/权限；按 email 自动合并身份；在同一 Method 下原地偷换 issuer/协议/subject 语义或硬删已引用方法；Project/Solution 选择登录 Provider；禁用最后一个管理入口导致无法恢复；旧 Session 行为未定义或静默变化。
- 所需证据：ComponentInstall、Package 检查与 Authentication SDK 接口检查报告，两个 Method 复用同一 Driver 的管理页与数据库记录、Schema/Description 展示、CredentialBinding 脱敏结果、Probe/Test/Enable 的 API 与 AuditEvent、动态登录页网络响应、AuthenticationFlow 过期/重放/state/配置变化测试、AuthenticatedSubject 与 ExternalIdentityLink 对照、相同 email 不合并和缺 Link 拒绝记录、Authorization 独立判断、Session 撤销或显式保留策略、最后入口保护、Project/Solution/Workflow Schema 全局检索，以及新增测试 Driver 前后 Platform Core/前端无代码分支证明。

#### `UJ-EXTENSION-PACKAGE-001`：第三方按 SDK 开发、检查并安装 ExtensionPackage

- 前置条件：准备一个 Extension 开发者、一个具有 Package 安装权限的管理员、公开的 Harness/Runtime/Authentication/Deployment SDK 与对应检查套件；准备一个合法测试 ExtensionPackage，以及缺少说明、接口版本不支持、入口越界、签名或 digest 不一致、接口方法缺失、必需行为失败和企图关闭必需检查的失败 Fixture。内置同接口 Driver/Extension 也必须可被同一流程检查。
- 用户操作：
  1. 开发者从公开 SDK 中选择一个 `interface_api`（扩展接口及版本），实现其类型化接口；确认不需要实现 Component Category、专属 Registry、通用 `invoke(any)` 或 OAC 数据库/CRD 接口；
  2. 构建 ExtensionPackage，只声明 `package_type=extension`（包类型）、`package_id`（稳定包身份）、`version`（精确语义版本）、`display.name`（名称）、`display.description`（说明）、`interface_api`（实现的公开接口版本）、`entrypoint`（包内入口）和来源；`artifact_digest`（制品摘要）、Provenance 与安装信任等级由平台产生或校验，不由包自授；
  3. 在开发环境运行该 SDK 随附的检查套件，确认输出 Package 检查与接口检查两类结果；修改或删除必需测试、提交自定义通过结论、仅声明能力但不满足行为时必须失败；
  4. 管理员在 Package & Extension Catalog 上传或引用同一制品，点击一次“安装并启用”；确认页面只把 Verify、Install 和 Activate 显示为后台进度，ExtensionManager 重算 digest、验证 Envelope/签名/来源/文件边界，并依据 `interface_api` 运行与开发端同源的固定检查套件；
  5. 检查失败时查看结构化 blocker，确认 Package 不会进入 Active，也不会出现在 Runtime、Harness、Authentication 或 Deployment 的候选选择器中；修复并发布新的精确 SemVer release 后重新安装；
  6. 检查通过后确认同一次操作把全部 Required 成员原子激活，ExtensionCatalog 按 `interface_api + package_id + version + artifact_digest` 建立索引；RuntimeDriver 只能由 AgentRun Job 通过 Runtime SDK 调用，其他实现只能由对应 Harness Host、Authentication Host 或 Deployment Host 通过该 SDK 的类型化接口调用，不经过 ExtensionManager 的万能运行入口；
  7. 对内置同类实现执行同一 Verify/Install/Resolve/接口检查路径，确认没有 `if builtin` 跳过检查；内置不可禁用或不可卸载只能由 lifecycle policy 表达；
  8. Disable、Revoke 或 Remove 测试 Package，确认新解析停止、活动 Workflow 继续使用已固定的精确快照或按既有恢复语义阻塞，历史 Run/Artifact/Audit 不被改写。
- 用户可见预期：第三方只需要“选 SDK、实现接口、填写最小 Package 元数据、本地检查、提交安装”。管理员只确认一次“安装并启用”，Verify、Install 和 Activate 作为后台阶段展示；管理页只展示 Package 检查和接口检查，不要求理解 Category、Host、Registry 或多层 Conformance 术语；名称和说明在安装、选择和运行详情中一致展示。
- 平台处理预期：系统只维护 SolutionPackage、ContentPackage、ExtensionPackage 三种 Package 类型。ExtensionManager 负责统一安装生命周期，ExtensionCatalog 按 `interface_api` 索引，公开 SDK 同时拥有类型化接口、能力描述和唯一检查套件。RuntimeDriver 由 AgentRun Job 进程内调用，Harness、Authentication、Deployment 分别由各自窄 Host 调用；Workspace、Audit Export 与 Credential Storage 首版保持内部 Port，不出现在第三方 Extension Catalog。
- 不通过条件：要求管理员分别点击 Verify、Install 和 Activate；任一 Required 成员失败后其他成员仍 Active；每新增接口都新增一整套 Category Registry/Host/Conformance；Package 自行选择测试 ID或关闭必需检查；本地测试与安装测试标准不同；Builtin 绕过安装或检查；运行调用经一个接收任意 JSON 的 ExtensionManager；ExtensionPackage 直接访问数据库、Workflow CR、Kernel 内部或扩大平台权限；把 Workspace、Audit Export、Credential Storage 在首版错误暴露为第三方 Package。
- 所需证据：四类公开 SDK 的接口文档与检查套件、合法与失败 Package、开发端和安装端同源检查输出、ComponentInstall/ExtensionCatalog 记录、Active 前后候选列表、类型化 Host 调用 trace、Builtin 同路径证明、Disable/Revoke/Remove 与历史快照结果，以及代码与 Schema 检索证明不存在按接口复制的 Category Registry、万能 invoke 或 Package 自选测试机制。

#### `UJ-AUTHORIZATION-POLICY-001`：配置统一授权策略并验证所有入口使用同一引擎

- 前置条件：准备已完成首个 Scope 初始化的实例，系统已创建安全默认 AuthorizationPolicy；准备一个具有 `governance.authorization.manage` 的管理员、一个普通成员、一个只读审计主体、一个服务身份、两个 Project、一个要求 human Principal 的最终验收 Decision、一个要求 Reviewer 与 Producer 独立的 human Run，以及可制造未知 Action、未知 Subject Kind、重复 key、孤儿 access profile、管理锁死、CAS 冲突和策略存储损坏的 Fixture。
- 用户操作：
  1. 不做任何额外授权配置，分别通过 Web 和 `oactl` 读取允许资源、尝试无权限写操作，确认安装时的默认 AuthorizationPolicy 已生效，普通用户不需要选择或安装授权引擎；
  2. 检查 Package & Extension Catalog、Project Setup、Solution Schema、Workflow.spec 和管理 API，确认不存在 AuthorizationProvider、Authorization Provider Registry、Provider Binding 或 Project/Solution 选择授权实现的字段；
  3. 管理员进入 `/administration/authorization`，确认页面只展示一个当前 Scope 的 Policy、只读 fixed invariants，以及 `name`（策略名称）、`description`（策略说明）、`default_access_profile_key`（默认访问权限模板键）、`access_profiles[]`（访问权限模板）、`access_profiles[].access_profile_key`（模板稳定键）、模板名称/说明、`access_profiles[].grants[]`（允许授权项）、`grant_key`（授权项稳定键）、授权项说明、`action_keys[]`（允许操作键）和 `subject_kinds[]`（适用对象类型）；页面不展示 Provider、Version、脚本、优先级或 Allow/Deny 覆盖顺序；
  4. 修改一个模板的 grants，执行 `EvaluateAuthorizationPolicyCandidate`，确认只返回 blockers、warnings、受影响访问关系和代表性 Allow/Deny 对照，当前 Policy revision/digest 和实际权限均未变化；
  5. 分别提交未知 Action/Subject、重复 key、删除仍被访问关系引用的 profile、让所有管理主体失去 `governance.authorization.manage`、尝试关闭默认拒绝/human-only/Scope 隔离等 fixed invariants 的候选，确认全部阻塞且不持久化；
  6. 使用有效候选执行 `AdoptAuthorizationPolicy`，确认服务端按 `expected_revision + candidate_digest`（预期策略修订号与候选内容摘要）重新评估并原子替换当前内容、增加 revision、计算新 policy_digest 和记录 AuditEvent；两个管理员并发采用时只有一个 CAS 成功，失败方必须重读而不能静默覆盖；
  7. 使用同一 Principal 分别从 Web、`oactl`、第三方 API Client 和一个受保护的内部 ApplicationCommand 执行相同 action/subject，确认都进入同一个 AuthorizationEngine，并在相同 RequestContext、访问关系、Policy 和请求事实下得到相同 Allow/Deny、`reason_code`（原因码）、`matched_grants[]`（匹配授权来源）和 `obligations[]`（附加收紧要求）；
  8. 让 OIDC/LDAP 返回未经映射的新 group/claim，确认权限不变化；随后通过显式 Identity Mapping/本地访问关系分配已有 `access_profile_key`，确认只有该受审计关系建立后才参与授权；
  9. 使用服务身份提交 human-only 最终验收、使用 Producer 本人提交要求独立 Reviewer 的 human Run，并让 Project/Solution 尝试扩大权限，确认即使 access profile 中存在宽泛 grant，fixed invariants 仍统一拒绝；
  10. 修改 Policy 后再次执行请求，确认新授权检查立即使用新 revision/digest，活动 Workflow.spec 不被改写；此前的 AuthorizationDecision/AuditEvent 保持不可变并继续显示旧 Policy 摘要和匹配授权项；
  11. 模拟当前 Policy 缺失、摘要不匹配、内容损坏或引擎内部异常，确认统一返回 Deny、产生安全审计或告警，不回退到另一套 Provider、缓存权限或前端判断；全流程不创建 Workflow、Plan、WorkUnit、Run、ComponentRun 或 Kernel action。
- 用户可见预期：平台安装后权限即可工作。管理员只维护一个组织级授权策略和可读访问模板，不需要理解或选择授权引擎；候选修改先看到影响和阻塞原因，采用后 Web 与 CLI 权限立即一致。普通用户只能看到自己被允许的操作，拒绝原因可定位但不泄露其他主体权限。
- 平台处理预期：Platform Core/Governance 内只有一个 AuthorizationEngine。每个 Scope 有一个普通可变 AuthorizationPolicy，使用稳定 ID、CAS revision 和 digest；策略采用 allow-only grants、无匹配默认 Deny，并与 OAC fixed invariants、本地 Scope/Project 访问关系和请求资格要求求交。调用方不能传入 Policy 或替换实现。Evaluation 瞬态不落库，Adopt 原子更新并审计；策略变化影响后续检查但不改写 Workflow 快照或历史 Decision。
- 不通过条件：存在可安装/可切换 AuthorizationProvider；Web、CLI、Controller 或内部服务各自判断权限；Project/Solution/Extension 能选择或绕过引擎；外部 group 直接成为权限；页面暴露任意策略脚本、优先级或多引擎合并；候选评估写入当前策略；可删除仍被引用的 profile；允许锁死所有管理员；Policy 缺失时放行或回退缓存；策略更新重写活动 Workflow 或历史 AuthorizationDecision。
- 所需证据：默认 Policy 记录与首次使用结果、Component/API/Schema 全局检索、授权管理页截图与字段说明、Action/Subject Catalog 响应、Candidate Evaluation 的无写入证明、各类 blocker、CAS 并发采用结果、Web/CLI/API/Internal 请求对照、AuthorizationDecision 的 reason/matched grants/Policy provenance、外部 group 映射前后对照、human-only/独立性/Project 扩权拒绝、策略变更前后历史记录、损坏策略 Fail Closed、AuditEvent，以及数据库/CRD/Kernel 检查证明不存在第二授权机制或额外 Workflow 对象。

#### `UJ-EXECUTION-ADMISSION-001`：执行对象创建自动经过统一准入并提供可解释失败结果

- 前置条件：准备一个已授权用户、一个服务身份、一个可运行 Project/Workflow、一个 Agent Run 请求、一个 ComponentRun 请求、一个 human Run request、一个 Workspace 请求和一个 Promotable Release 的直接 Deployment 请求；准备可分别制造 Project 禁用、执行器不兼容、资源不足、安全要求无法满足、Credential 过期、外部副作用并发冲突，以及外部检查 `pass|fail|unknown|timeout|stale` 的 Fixture。
- 用户操作：
  1. 浏览 Settings、Administration、Package & Extension Catalog、Project Setup、Solution Schema 和 Workflow 配置，确认没有 Admission Provider、AdmissionPolicy、Admission Version、Registry、Binding、准入引擎选择或独立 `/administration/admission` 页面；
  2. 在无需任何准入配置的情况下，从 Web 发起一个满足条件的 Agent 执行，确认 Authorization 通过后平台自动执行准入，并在 Admit 后才创建精确 AgentRun/Workspace；
  3. 分别从 Web、`oactl`、第三方 API Client 和内部 ApplicationCommand 发起等价执行请求，确认全部调用同一个 ExecutionAdmissionEngine，相同请求与事实得到相同 effect、reason、checks、constraints、rules digest 和 facts digest；
  4. 依次制造 Project 禁用、执行器/Runtime/Workspace 不兼容、没有任何兼容节点或超过硬配额、ExecutionSecurityRequirements 无法满足、CredentialBinding 禁用或过期、外部副作用并发键冲突，确认每次都 Reject，并在原页面显示可读原因、失败检查、观察时间和正确修复入口；再只制造暂时没有空闲 CPU/GPU 的调度压力，确认可 Admit 并由 Kubernetes 保持 Pending，而不是被误判为永久业务失败；
  5. 让可选外部事实端口分别返回 Pass、Fail、Unknown、超时、过期和摘要不匹配，确认外部系统只能贡献单项事实，不能直接返回整体 Admit；所有必需事实的 Unknown/超时/过期/矛盾均 Reject；
  6. 检查每个不可变 ExecutionAdmissionDecision，确认固定 `execution_admission_decision_id`（准入决定身份）、`request_digest`（请求摘要）、`effect`（结果）、`reason_code`（原因码）、`evaluated_checks[]`（检查列表）、`constraints[]`（收紧约束）、`rules_digest`（内置规则摘要）、`facts_digest`（事实摘要）和 `evaluated_at`（评估时间），且没有 provider/policy/version 字段；
  7. 对 Reject 路径检查 PostgreSQL、Workflow CR、Kubernetes 与外部目标，确认只保存 Decision/AuditEvent，不创建 AgentRun、ComponentRun、Deployment、ChildWorkflowLink、human Run request、Workspace、Job、Pod 或 PVC；
  8. 对 Admit 路径确认 Decision、AuditEvent 和执行创建命令使用同一事务或既有 CommandRecord/Outbox 边界；模拟提交中断和重复请求，确认幂等且不会形成无准入决定的孤立执行对象；
  9. 在一次 Admit 后改变资源、Credential 或并发事实，再执行 Retry/恢复/直接 Deployment 重试，确认平台重新准入并可以 Reject，不能缓存或复用旧 Admit；
  10. 创建 human Run request，确认准入只验证当前 Scope/Project 和非空 `principal_requirement`；人员提交结果时仍重新执行精确 Authorization、责任、独立性、request digest、WorkUnit revision 和 CAS，准入不能代替人员授权。
- 用户可见预期：用户不需要理解或配置准入引擎。执行成功时正常进入 Run/Deployment；执行被拒绝时能够看到明确原因、哪项检查失败以及应该去哪个真实配置页面修复，不出现选择 Provider、修改固定规则或“忽略继续”。
- 平台处理预期：Platform Core 在 Authorization 后解析精确执行实现、从权威来源组装瞬态 ExecutionAdmissionFacts，并调用唯一内置 ExecutionAdmissionEngine。外部端口只返回类型化检查事实；引擎用固定规则产生不可变 ExecutionAdmissionDecision。Reject 不产生执行对象，Admit 后才提交创建命令；整个过程不调用 Orchestrator Kernel，也不建立 AdmissionPolicy、Version、Extension API、Registry 或第二状态机。
- 不通过条件：任意入口自行判断准入；存在可安装或可切换 AdmissionProvider；Project/Solution/Extension 能选择或绕过准入实现；外部检查直接决定整体 Admit；Unknown/超时默认放行；首版要求用户填写资源策略才能使用；Reject 后仍出现执行对象；旧 Admit 在事实变化后被复用；页面提供绕过按钮；准入进入 Kernel 或 Workflow Lifecycle。
- 所需证据：全局 Schema/Component/UI 检索、Web/CLI/API/Internal 对照、各执行种类的 Admit 结果、七类 Reject Fixture、外部事实六种结果、ExecutionAdmissionDecision 字段、AuditEvent evaluated control、数据库/CRD/Kubernetes/外部目标对象计数、事务/Outbox 故障注入、幂等重放、事实变化后的重新准入、human Run 双重校验，以及 Kernel 调用追踪为空的证明。

#### `UJ-AUDIT-EXPORT-001`：保持本地审计权威并按需配置可恢复的外部导出

- 前置条件：准备一个刚完成初始化、尚未配置 AuditExportTarget 的 Scope；准备具有 `governance.audit-export.manage` 的管理员、无该权限的普通用户、系统发行版支持的 Webhook 或 Syslog 测试导出类型、一个符合目标要求的 CredentialBinding，以及可制造成功、拒绝、超时、非法确认、部分外部成功、重复请求、长时间中断和 Retention 临界 backlog 的 Fixture。本地已存在一组有序 AuditEvent。
- 用户操作：
  1. 不配置任何外部目标，产生新的业务、Authorization、Execution Admission、Credential use 和状态写入事件，再打开 `/governance/audit`，确认本地 AuditEvent 与 Audit Explorer 始终可用，能力就绪页不把“未配置外部导出”显示为 blocker；
  2. 检查 Platform API、Settings、Package & Extension Catalog、Project/Solution/Workflow Schema 和部署配置，确认不存在可替换本地审计的 AuditProvider、第三方 Audit Export ExtensionPackage、关闭本地 AuditEvent 的开关、从 SIEM 回读的查询 fallback 或让 Project/Solution 选择审计实现的字段；
  3. 管理员进入 `/administration/audit-export`，确认页面顶部明确区分“本地权威审计”和“可选外部导出”，并且每个 Scope 最多一个活动目标；普通用户直接访问同一路由或 API 必须被服务端拒绝；
  4. 检查表单只要求 `name`（目标名称）、`description`（目标说明）、`export_type`（内置导出类型）、Schema 驱动且带说明的 `configuration`（非敏感配置）和可选 `credential_binding_id`（凭证绑定身份）；页面不提供事件 category/Project/action/outcome/Principal 过滤器，也不要求填写 Package、ID、revision、digest、checkpoint 或 Secret；
  5. 填写候选并点击“测试连接”，确认 `ProbeAuditExportTarget` 不保存目标、不启用导出、不发送真实 AuditEvent，只发送脱敏测试载荷；测试动作自身形成一条本地 AuditEvent。分别制造成功、失败和超时，确认错误被脱敏且不会泄露 Header、Token 或外部完整响应；
  6. 保存目标，确认首次 `SaveAuditExportTarget` 创建稳定 `audit_export_target_id`，状态默认 Disabled，并保存内置 export type、非敏感配置、可选 CredentialBinding、CAS revision 与 configuration digest；并发更新只有一个成功；
  7. 点击“启用导出”，确认服务端重新完成 Authorization、内置类型/Schema/Credential/Probe 校验，然后从当前 Scope 最早仍保留的 AuditEvent 开始按本地稳定顺序异步补发，不遗漏任何 category；
  8. 检查 `AuditExportBatch`，确认固定 target ID/configuration digest、稳定 delivery ID、起止游标和脱敏 events；模拟请求重放、外部部分成功、超时和非法越界确认，确认内部 AuditExportPort 实现只向 OAC 返回最高连续确认游标，Retry 复用同一投递身份，checkpoint 不跳过未确认事件且外部不会重复计入；
  9. 中断外部接收端，继续执行业务并查询 Audit Explorer，确认业务、本地 AuditEvent、查询和导出文件均正常；页面显示 degraded、backlog、最近成功时间、连续失败和脱敏错误码，后台有界退避重试。backlog 接近 Retention 边界时产生高优先级告警，但不能无限阻塞本地 Retention 或业务提交；
  10. 停用目标并产生新事件，确认只停止外发；重新启用后从最后连续确认 checkpoint 继续。普通成功投递不能为每条事件再次产生 AuditEvent，避免递归；目标测试、启停、替换和 Credential 使用仍正常审计；
  11. 轮换同一个 CredentialBinding 的 Secret，确认目标身份和 checkpoint 保持不变；尝试原地改变 export_type、configuration 或 credential_binding_id，确认服务端要求显式 Replace，创建新的目标身份、停用旧目标，并清楚提示新目标会重新导出当前本地保留事件；
  12. 暂停、删除或篡改外部 SIEM 数据，再执行 Query/Detail/Export，确认所有平台审计结果仍只来自本地 AuditEvent；全流程不创建 Workflow、Plan、WorkUnit、Run、ComponentRun 或 Kernel action。
- 用户可见预期：默认安装不要求配置外部审计。管理员需要企业 SIEM 时只配置一个清晰的外部导出目标，并能看到连接测试、健康、backlog 和恢复位置；外部故障不会让用户失去本地审计，也不会影响正常业务。普通用户不会看到可以关闭或替换平台审计的入口。
- 平台处理预期：Platform Core 在业务提交边界写不可变 AuditEvent，本地 PostgreSQL 和统一 Audit Explorer 始终是权威。Audit Exporter 只读取已提交事件、服务端脱敏并按稳定游标组装批次，通过内部 AuditExportPort 的系统发行版实现异步投递。AuditExportTarget 是普通 CAS 配置；delivery checkpoint 只是系统恢复状态。外部接收端不能参与业务事务、回写事件、成为查询来源或进入 Workflow/Kernel。
- 不通过条件：未配置目标时平台显示不就绪；存在 AuditProvider、第三方 Audit Export ExtensionPackage 或关闭本地审计开关；外部 SIEM 成为查询/恢复来源；Project/Solution 选择导出实现；页面允许过滤掉部分审计类别；Probe 保存目标或发送真实事件；外部故障回滚业务或阻塞查询；checkpoint 跳过未确认事件；Retry 造成重复外部计入；成功投递递归生成事件；原地偷换物理目标；将 checkpoint、Export Job 或外部数据做成第二套审计状态机。
- 所需证据：默认无目标状态、统一 Audit Explorer 查询、全局 Schema/Package 检索、权限拒绝、配置页字段中文释义、Probe 请求与无持久化证明、CAS 创建/更新、内置 export type、完整保留事件补发、AuditExportBatch、重复/部分成功/非法确认 Fixture、checkpoint 与外部计数、外部中断期间业务及本地查询、Retention 告警、停用/恢复、Credential 轮换、目标替换、AuditEvent 递归数量检查，以及数据库/CRD/Kernel 对象计数。

#### `UJ-CAP-001`：复用内置能力并按缺口进入 Settings

- 前置条件：Software Delivery Embedded ComponentBundle 已安装并 Active；当前 Scope 已存在可用模型目录。
- 用户操作：
  1. 打开软件交付引导或 Agent Center；
  2. 查看当前能力就绪摘要；
  3. 在没有 blocker 时直接进入 Agent Center，不逐个打开 Skills、Model Providers、MCP 和 Credentials 完成形式化配置。
- 用户可见预期：内置 AgentTemplate 与模板 Skills 显示“已就绪”；已有模型目录显示可用；不需要 MCP/Credential 的能力不显示为必填任务。
- 平台处理预期：Settings Application Facade 只查询并聚合 Content Catalog、Agent Capability Catalog 和 Governance 读模型，不创建任何新业务对象或 Settings 聚合对象。
- 不通过条件：要求用户先完成四个 Tab；把没有被所选 Agent/Solution 使用的 MCP 或 Credential 显示为 blocker；查询动作产生写入。
- 所需证据：能力就绪页面截图、Settings 聚合查询响应、数据库/Audit 证明查询没有创建新业务记录。

#### `UJ-SKILL-001`：从 GitHub 导入 Skill

- 前置条件：存在一个公开 GitHub Repository，默认分支包含合法 Manifest/Frontmatter 和唯一 `SKILL.md`。
- 用户操作：
  1. 进入 Settings / Skills；
  2. 选择“从 GitHub 导入”；
  3. 只填写 `repository_uri`（GitHub 仓库地址），留空 `requested_revision`（指定 Branch/Tag/Commit）与 `subdirectory_path`（子目录）；
  4. 查看解析预览并发布。
- 用户可见预期：页面自动展示默认分支解析结果、Skill 名称、说明、版本、入口、最终 Commit SHA、文件树和 digest；只有元数据缺失或入口歧义时才要求补充。
- 平台处理预期：Importer 静态读取内容，不执行包内代码；发布精确 SkillPackage release、ComponentInstall 和 Provenance。
- 不通过条件：要求用户填写 `source.type`；默认分支无法解析却静默猜测；执行脚本；同一 Skill package ID/SemVer 用新 digest 覆盖既有 release。
- 所需证据：导入表单截图、解析预览、SkillPackage release 查询、Commit SHA、artifact/content digest、AuditEvent。

#### `UJ-MODEL-001`：创建 Provider、获取模型并测试

- 前置条件：当前用户有创建模型连接权限，并准备一个可用模型服务与条件性的 API Credential。
- 用户操作：
  1. 进入 Settings / Model Providers；
  2. 填写连接名称、说明、API Type、Base URI，需要鉴权时选择或就地创建 Credential；
  3. 保存连接；
  4. 点击“获取模型”；
  5. 从返回目录选择模型并发送测试消息。
- 用户可见预期：普通表单不出现发现模式、模型路径、分页、TLS、重试或代理字段；获取成功后展示模型名称、external model ID、说明、来源和能力；测试只展示结果、时延和脱敏错误。
- 平台处理预期：创建稳定 ModelProviderConnection，初始 `revision=1` 并计算 configuration digest；Connector 根据 API Type 自行选择发现协议；成功创建 ModelCatalogSnapshot 和 ModelProviderProbe，二者记录所用 connection ID/revision/digest；Secret 只通过 CredentialBroker 临时使用。
- 不通过条件：保存 API Key 明文；要求用户填写模型列表路径；测试错误泄漏 Header/Secret；失败时生成部分 Snapshot。
- 所需证据：连接详情截图、连接 ID/revision/digest 查询、Catalog Snapshot、Probe、Credential use AuditEvent、日志脱敏检查。

#### `UJ-MODEL-002`：发现失败后的手动模型补充

- 前置条件：存在一个连接正常但不支持自动发现，或未返回目标企业别名的 ModelProviderConnection。
- 用户操作：
  1. 点击“获取模型”并观察结构化失败或空结果；
  2. 点击“手动添加模型”；
  3. 填写 `external_model_id`（Provider 实际模型名称）、可选展示名和必填说明；
  4. 发布目录并发送测试消息。
- 用户可见预期：无需切换 automatic/manual/hybrid 模式；上一份有效目录仍可查看；新条目标记为 `manual`，自动发现也确认同一模型时显示 `mixed`。
- 平台处理预期：失败发现不产生部分 Snapshot；AddManualModel 创建新的不可变 ModelCatalogSnapshot，并按 external model ID 合并来源证据。
- 不通过条件：发现失败删除旧目录；允许无说明的人工模型；同一模型出现两个不可区分选项；原地修改旧 Snapshot。
- 所需证据：失败提示截图、前后 Snapshot 查询、模型来源、测试 Probe。

#### `UJ-MCP-001`：导入并测试 MCP Server

- 前置条件：准备一份合法 `mcpServers` JSON，包含一个 stdio 或 Streamable HTTP Server；可选包含需要迁移的 Secret 占位值。
- 用户操作：
  1. 进入 Settings / MCP；
  2. 导入 JSON；
  3. 查看规范化结果；
  4. 若检测到 Secret，就地创建或选择 CredentialBinding；
  5. 保存并执行 Test。
- 用户可见预期：stdio 工作目录缺省为 Workspace 根目录；连接策略不要求普通用户填写；测试展示 Server Info、Protocol Version、Tools、Resources 和 Prompts，只读且不提供逐 Tool 授权勾选。
- 平台处理预期：创建或更新稳定 McpServerDefinition，递增 revision 并计算 configuration digest；Secret 被替换为 Header/Environment Credential Binding；Probe 形成记录 source ID/revision/digest 的 McpCapabilitySnapshot；stdio 不在控制面宿主机执行。
- 不通过条件：内联保存 Token；控制面直接启动 stdio；把 Capability Snapshot 变成 Tool 权限目录；测试泄漏环境变量值。
- 所需证据：导入差异截图、MCP 配置 ID/revision/digest、Capability Snapshot、CredentialBinding、执行位置和脱敏日志。

#### `UJ-CRED-001`：使用内置 CredentialStore 管理最小权限 CredentialBinding

- 前置条件：准备一个刚完成安装的本地 Scope，安装器已自动准备内置 CredentialStore；准备具有模型/MCP/Repository/Deployment 目标配置权限的普通用户、治理管理员、无权限用户、一个既有 CredentialBinding，以及可制造 CredentialStore 不可用、类型化验证失败、并发轮换和目标保存失败的 Fixture。
- 用户操作：
  1. 普通用户进入 `/settings/credentials`，确认页面直接显示“内置凭证存储已就绪”，能力就绪页不要求先配置后端；普通用户和管理员页面都没有 CredentialPolicy、rotation policy、每凭证后端选择、默认后端或“企业存储后端”入口；
  2. 分别从 Model Provider、MCP、私有 Skill Import、Repository 或 Deployment 目标表单选择“新建凭证”，确认普通表单只显示 `display.name`（凭证名称）、`display.description`（凭证说明）、`credential_type`（凭证类型）、条件性的 `secret_input`（Secret 一次性输入）、`type_metadata`（类型专用非敏感元数据）和条件性的 `expires_at`（到期时间）；
  3. 保存后返回原目标表单，确认用户不填写 Scope、Project、`allowed_usages[]`、`subject_constraints[]`、storage backend、secret handle、fingerprint、status、revision 或 rotation policy；详情只显示脱敏 metadata、派生用途、subject、fingerprint、状态、过期、最后轮换、适用验证结果和引用位置，查询/API/页面刷新均不能读取 Secret 原值；
  4. 检查 Model Provider、MCP、Repository 等目标分别获得自己的最小用途和 subject。尝试从 Credentials 页面直接扩大 Scope、用途或 subject，确认不存在通用扩权编辑器；需要另一个目标时从该目标上下文创建新的 Binding。GitHub App 的 Repository 范围必须由内置类型处理器从 GitHub 读取并只读展示，用户不能手工扩大；
  5. 制造目标配置保存失败，确认只撤销本次新建且尚未被其他对象引用的 Binding、清理材料并写补偿 AuditEvent；选择已有 Binding 时发生目标保存失败不得撤销原 Binding；
  6. 检查 Credentials 页面没有伪通用“测试所有凭证”。GitHub App 或云身份可以执行类型化验证；普通模型 API Key、MCP Header 和部署凭证只能由对应目标 Test/Probe 验证，错误和外部响应必须脱敏；
  7. 在同一 CredentialBinding 上执行轮换，确认 Binding ID 和所有 Model Provider/MCP/Repository 引用保持不变；内部 CredentialStore 验证成功后原子切换新材料、递增 revision、形成 CredentialRotationRecord 与 AuditEvent，失败时旧材料继续有效。使用旧 expected revision 并发轮换必须冲突；
  8. 依次 Disable 和 Revoke CredentialBinding，确认新 lease、目标测试和需要该凭证的执行 Fail Closed；页面不能重新启用已 Revoke 的材料，也不能回退到其他宽权限 Credential；
  9. 检查 Package & Extension Catalog、Platform API、数据库和页面路由，确认首版不存在第三方 Credential Storage ExtensionPackage、后端 Catalog、`Get/Evaluate/AdoptCredentialProviderSelection`、`MigrateCredentialBinding` 或每个 Binding 的后端身份字段；
  10. 中断内置 CredentialStore，分别尝试创建、轮换、测试和运行时使用，确认统一返回可读 blocker并写审计，不把 Secret 落到数据库明文、浏览器或临时宽权限存储。恢复后重试同一业务动作；全流程不创建 Workflow、Plan、WorkUnit、Run、ComponentRun 或 Kernel action，除非目标业务本身在凭证检查成功后另行创建执行。
- 用户可见预期：默认安装后 Credential 立即可用，用户只理解和管理 CredentialBinding；Secret 只提交一次，轮换不要求修改全部引用。页面不要求用户理解 KMS、Vault、存储后端迁移或 Kubernetes Secret 实现。
- 平台处理预期：Governance Module 拥有 CredentialBinding、CredentialRotationRecord 和 CredentialBroker 规则；内部 CredentialStorePort 保存、验证、轮换、解析和撤销材料，Broker 每次使用重新校验并签发短期 lease。存储实现属于系统发行版内部能力，不是 Package、Project/Solution 字段、Workflow 输入或 LockedComponentSet 成员。
- 不通过条件：安装后必须先配置存储后端；普通或管理员表单选择后端或编辑 rotation policy；存在 CredentialPolicy、Credential Provider Catalog、默认后端或跨后端迁移页面/API；浏览器、业务数据库、日志、Artifact 或 Prompt 保存明文；查询接口返回 Secret/handle；通用 Credentials 页面可以扩大用途；目标失败时撤销用户已有 Binding；轮换改变 Binding ID；存储故障回退到明文或其他宽权限材料；Credential 管理进入 Workflow、Kernel 或 LockedComponentSet。
- 所需证据：默认就绪页、普通与管理员 Credentials 页面、五类上下文表单及字段中文释义、HTTP/浏览器存储/日志脱敏检查、CredentialBinding 元数据与 revision、最小用途/subject、目标保存失败补偿、类型化验证与目标 Probe、轮换前后引用与 RotationRecord、Disable/Revoke 结果、Package/API/数据库全局检索、CredentialStore 故障与无明文回退、AuditEvent，以及数据库/CRD/Kernel 对象计数。

#### `UJ-PLATFORM-OPERATIONS-001`：查看平台综合健康、当前活动告警和执行容量

- 前置条件：准备一个健康的 Compact 或 External Cluster、具有 `platform.operations.read` 权限的管理员/运维用户、无权限用户，以及可分别制造 Controller 降级、关键数据依赖不可用、CredentialStore 故障、Outbox 积压、Pod Pending、Quota/资源不足、Metrics 来源缺失、来源超时和重复告警条件的 Fixture。测试环境默认不安装 Prometheus、Alertmanager、Grafana、OpenTelemetry Collector 或独立时序数据库。
- 用户操作：
  1. 有权限用户进入 `/administration/operations`，确认只有一个“平台运行状态”页面，顶部同时展示 `overall_status`（平台综合健康状态）、`observed_at`（本次观测时间）与 `freshness`（数据新鲜度），主体固定展示健康分组、当前活动告警和执行容量；
  2. 在健康环境调用 Web 页面、公开 `QueryPlatformOperations` 和 `oactl operations show --output json`，确认三者进入同一 RequestContext/Authorization/Query 用例，返回相同 Scope、状态、检查项、告警与容量事实；无权限用户统一得到结构化拒绝，浏览器或 CLI 不直接访问 Kubernetes/监控后端；
  3. 展开控制面、执行面、数据与存储、外部依赖四组检查，核对每项 `check_key`（检查项稳定键）、`name`（检查项名称）、`description`（检查项说明）、`status`（当前检查状态）、`summary`（当前结论摘要）、`observed_at`（来源观测时间）、`subject`（受影响对象）与 `remediation`（修复建议和入口）均来自服务端，页面不自行推断总体状态；
  4. 分别制造非关键依赖故障、容量压力和关键数据依赖不可用，确认状态依次能够表达 `degraded` 与 `unavailable`；让 Required 来源超时、过期或产生矛盾事实，确认受影响检查与总体状态为 `unknown`，页面不能沿用上次绿色结果；
  5. 制造同一规则、同一 subject 的重复故障事实，确认 `active_alerts[]` 按 `alert_fingerprint`（告警指纹）去重，并展示 `severity`（严重程度）、标题、脱敏摘要、subject、来源可证明的首次/最近观测时间和修复入口；来源不能证明首次时间时显示 unknown，不使用页面打开时间伪造；
  6. 检查页面没有告警确认、静默、规则编辑、通知路由或 Incident 管理；恢复故障条件后相应告警从当前活动列表消失。需要长期历史或值班通知时只能下钻已配置的企业可观测系统，不在 OAC 中形成第二套告警状态；
  7. 在容量区域核对 `status`（容量状态）、`running_runs`（正在运行的物理 Run 数）、`pending_runs`（等待调度的物理 Run 数）、`resources[]`（资源容量明细）、`pending_reasons[]`（等待原因）和 `metrics_coverage`（实时利用率覆盖情况）。确认数据来自 Kubernetes allocatable、requests/limits、Quota、Workload 和调度结果，不存在平台 Run Slot；
  8. 移除 Metrics API 或企业实时指标来源，确认 observed CPU/Memory/GPU usage 显示 unknown 或 unavailable，但 allocatable、requested、Quota、运行数、等待数和 Pending 原因仍可查询，任何缺失指标都不能显示为 0；
  9. 普通视图确认只显示“控制面不可用”“执行容量不足”“存储未就绪”等产品语言；具有诊断权限时才可在 Inspector 展开 Namespace、Pod、PVC、Condition、reason 等精确基础设施事实，无诊断权限时服务端脱敏而不是由前端隐藏原始响应；
  10. 逐个点击告警或健康检查的 remediation，确认它只导航到真正负责修复的 Workflow、Component、Credential、Deployment、数据存储页面，或展示宿主/Kubernetes/云基础设施容量指引；Platform Operations 不直接修改资源，不提供“忽略并继续”、万能修复或一键扩容命令；
  11. 连续刷新页面并触发公开 Event/Watch 失效通知，确认通知只促使客户端重新执行 `QueryPlatformOperations`，不携带第二份权威健康状态，也不创建 Workflow、Plan、WorkUnit、Run、Deployment、Kubernetes Workload 或成功读取 AuditEvent；
  12. 全局检查数据库、CRD、API Schema、安装器 CLI 和 Kernel，确认不存在 PlatformHealth、Alert、CapacitySnapshot、DashboardSnapshot、Incident、告警确认/静默状态、run slot、ScalePlatform、NodePool、扩容 Workflow、自定义 Scheduler 或 Kernel operations action；在无 Prometheus/OpenTelemetry 组件时完整基础路径仍然通过。
- 用户可见预期：管理员在一个页面即可判断平台是否可服务、当前有哪些活动问题、执行容量是否充足，并能跳转到真实修复位置；unknown、数据过期和指标缺口均清晰可见，不需要理解 Kubernetes 才能读懂首层结论。
- 平台处理预期：公开 Platform API 的 `QueryPlatformOperations` 通过 `ServiceHealthSourcePort`、`ClusterCapacitySourcePort`、`ActiveAlertSourcePort` 和可选 `ResourceMetricsSourcePort` 并行读取当前事实，按固定状态口径组装瞬时 PlatformOperationsView。Kubernetes 继续拥有资源与调度权威，外部可观测系统只贡献查询事实；该路径不调用 Workflow Controller Reconcile、Execution Host 写操作或 Orchestrator Kernel。
- 不通过条件：Web 与 `oactl` 使用不同接口或不同状态计算；页面直接查询 Kubernetes/Prometheus；unknown 显示为绿色或 0；容量压力与服务不可用混为一个状态；没有 Metrics 就无法查看容量；平台创建 Run Slot、ScalePlatform、NodePool、扩容任务、自定义 Scheduler 或 PlatformHealth/Alert/Incident 聚合；OAC 内置告警确认/静默状态；修复按钮绕过真实领域或基础设施入口；查询触发 Kernel、Workflow 或执行资源。
- 所需证据：Web 与 `oactl` 请求/响应对照、Authorization 结果、PlatformOperationsView Fixture、四组健康检查、healthy/degraded/unavailable/unknown 状态测试、freshness 过期测试、活动告警去重与恢复、诊断权限脱敏、Kubernetes allocatable/requests/Quota/Pending 对照、Metrics 有/无两组结果、remediation 跳转、Event/Watch 重新查询、无强制可观测组件的部署清单，以及数据库/CRD/Kernel 禁止对象检索。

#### `UJ-AGENT-001`：实例化内置 AgentTemplate 并完整继承 Skills

- 前置条件：Software Delivery Bundle Active；选择一个 `skills[]` 非空的内置 AgentTemplate；所有模板 SkillPackage release 均 Active。
- 用户操作：
  1. 进入 Agent Center；
  2. 选择内置 AgentTemplate；
  3. 查看预填 Instructions 与“模板内置 Skills”；
  4. 不重新选择或编辑模板 Skills，继续配置模型和 Runtime；
  5. 发布 Agent。
- 用户可见预期：模板 Skills 自动出现并带来源/版本/digest；普通模式只读且没有逐项重新勾选要求。
- 平台处理预期：Platform Core 根据精确 AgentTemplate release 重新解析并完整复制全部 `skills[]` 到新的 AgentDefinition；保存来源模板 ExactComponentRef 与 digest。
- 不通过条件：漏掉任一模板 Skill；依赖浏览器回传模板 Skill 列表；静默选择 `latest`；模板 Skill 缺失时发布部分 AgentDefinition。
- 所需证据：模板详情与实例化页面截图、模板和最终 AgentDefinition 的 Skill package release 对比、AuditEvent。

#### `UJ-AGENT-002`：追加企业 Skill 与显式自定义移除

- 前置条件：已选择一个带模板 Skills 的 AgentTemplate，并存在一个可用企业 SkillPackage release。
- 用户操作：
  1. 在“企业追加”分组添加企业 Skill；
  2. 验证模板 Skills 保持不变；
  3. 尝试在普通模式删除模板 Skill并确认操作不可用；
  4. 进入“高级自定义模板”，选择移除一个模板 Skill，阅读影响说明并明确确认；
  5. 发布 AgentDefinition。
- 用户可见预期：追加项与模板项分组展示；移除模板项必须经过独立高级入口；发布后展示“已自定义”派生说明。
- 平台处理预期：追加和移除都只影响新 AgentDefinition，不修改 AgentTemplate；移除动作产生 AuditEvent；同一 Skill ID 多个 release 冲突必须阻止发布。
- 不通过条件：普通模式可误删；修改模板本身；用顺序静默覆盖 SkillPackage release；创建额外 Agent 生命周期状态机。
- 所需证据：普通/高级模式截图、前后模板与 Agent Skill 对比、冲突错误、AuditEvent。

#### `UJ-AGENT-003`：直接选择 Provider/Model 固定模型

- 前置条件：至少两个 Provider Connection 的 ModelCatalogSnapshot 中存在同名或相似模型；对应 Probe 可查询。
- 用户操作：
  1. 在 Agent Center 打开模型选择器；
  2. 不先进入独立 Provider 必选步骤；
  3. 在 Provider 分组中查看模型说明、external model ID、API Type、Probe 状态和测试时间；
  4. 选择一个 Provider/Model 组合并使用 `fixed` 策略；
  5. 发布 Agent。
- 用户可见预期：同名模型按 Provider 清晰区分；Provider 可以筛选但不是必经字段；选择结果显示可读 Label。
- 平台处理预期：AgentDefinition 保存 `model_provider_connection_id + external_model_id`，并可保存 `selected_from_model_catalog_snapshot_id` 作为来源证明；不保存或解析拼接 Label。
- 不通过条件：必须先保存 Provider 临时状态才能选模型；只保存模型名称；把 `provider.model` 当持久化 Key；Provider 或 Catalog 更新静默改变已发布 Agent。
- 所需证据：模型选择器截图、发布命令、AgentDefinition 查询、更新 Provider 后 AgentDefinition 内容不变的对比。

#### `UJ-AGENT-004`：跨 Provider 配置 ordered-fallback

- 前置条件：至少两个 Provider 各有一个通过兼容预检的模型。
- 用户操作：
  1. 选择 `ordered-fallback`；
  2. 从不同 Provider 添加多个模型；
  3. 使用鼠标和键盘调整顺序；
  4. 发布 Agent。
- 用户可见预期：列表逐项显示 Provider/Model；顺序清晰且可访问；重复项和不兼容项有明确错误。
- 平台处理预期：`allowed_models[]` 保持用户确认顺序，每项拥有独立结构化身份；Runtime Selection 只能按该顺序回退，不得选择列表外模型。
- 不通过条件：先锁定单一 Provider 导致无法跨 Provider；排序只存在前端但未持久化；失败时隐式选择未声明模型。
- 所需证据：排序前后截图、AgentDefinition 查询、兼容性错误和运行时选择记录。

#### `UJ-AGENT-005`：发布、失败与定义不漂移

- 前置条件：完成模板、Skills、模型、MCP、Runtime 和资源配置，并准备一个故意不兼容的配置分支。
- 用户操作：
  1. 使用不兼容配置点击发布并观察 blockers；
  2. 修正配置后重新发布；
  3. 更新来源 AgentTemplate、Skill 或 Provider Catalog；
  4. 查看已发布 AgentDefinition。
- 用户可见预期：失败时没有“已发布”假象；成功后显示精确 definition ID、来源与 digest；上游 package/Catalog 更新后旧定义保持原值，并提供“基于此定义发布新定义”入口。
- 平台处理预期：发布前执行模板/Skill digest、Model Catalog/Probe、Runtime API Type、MCP Transport、Capability、资源、Authorization 和 Component 状态预检；失败不产生部分定义；发布成功创建新 `agent_definition_id + previous_agent_definition_id + content_digest`，不要求 SemVer；创建 Agent 不启动 Workflow、AgentRun、Workspace 或 Kubernetes Job。
- 不通过条件：发布失败产生残缺定义；原地覆盖 AgentDefinition；把每次编辑/自动保存都当成正式发布；上游更新静默漂移；创建 Agent 启动物理执行资源。
- 所需证据：blocker 截图、发布前后 AgentDefinition 查询、AuditEvent、Kubernetes 资源无新增证明、上游更新后的不漂移对比。

#### `UJ-AGENT-006`：从空白创建 AgentDefinition 并用于 Project 责任岗位

- 前置条件：当前 Scope 已有至少一个可选模型、一个通过接口检查的 RuntimeDriver，以及按需可用的 SkillPackage/MCP Server；当前 Principal 具有 Agent 创建与发布权限，并有权编辑一个 Project Setup。
- 用户操作：
  1. 进入 Agent Center，选择“从空白创建”，不选择 AgentTemplate；
  2. 填写 Agent 名称、说明和直接可读的稳定 Instructions；确认页面不要求先创建 Instructions Artifact、Template 或 Version；
  3. 按需选择 Skill，在统一模型选择器直接选择 Provider/Model，选择 MCP Server，配置一个或多个 RuntimeBinding、Capability 与资源要求；确认 Credential 只由 Provider/MCP 等明确用途配置持有，不出现 Agent 通用 Secret 列表；
  4. 保存编辑草稿并刷新页面，确认自动保存只更新编辑态，不生成 AgentDefinition；
  5. 执行预检并正式发布，查看新的 `agent_definition_id`（Agent 定义身份）、`content_digest`（内容摘要）和空的 `source_agent_template`（模板来源）；
  6. 返回 Project Setup 的责任岗位，选择该 AgentDefinition 作为候选，确认岗位页只读展示其 Instructions/Skill/模型/MCP/Runtime/Capability 摘要，不复制这些配置。
- 用户可见预期：没有合适模板时仍能完整创建 Agent；模板只是快捷入口，不是平台运行前提。用户只配置真正属于 Agent 的稳定能力，发布后得到不可变定义，并可被任意合格 Solution 的责任岗位按能力选择。
- 平台处理预期：Agent Center 使用与模板实例化相同的 Agent Definition Application 服务；空白流程令 `source_agent_template` 为空，直接规范化 Instructions、Skills、ModelSelectionPolicy、MCP identities、内嵌 RuntimeBindings、Capabilities 和 ResourceRequirements。预检与发布规则不因没有模板而放宽；成功创建普通不可变 AgentDefinition/AuditEvent，Project Setup 只保存其精确 ID，且不启动 Workflow、AgentRun、Workspace 或 Job。
- 不通过条件：强制先创建 AgentTemplate；把空白 Agent 保存成新的 Package 类型；Instructions 必须先转为 Artifact；AgentDefinition 持有通用 Tool/Credential bag；岗位页复制并可修改 Agent 配置；自动保存产生不可变定义；发布失败仍可被 Project 选择；创建或选择 Agent 启动物理执行资源。
- 所需证据：空白创建页、草稿与正式发布请求、AgentDefinition 查询、`source_agent_template` 为空、预检正反 Fixture、Project 岗位候选查询与保存值、AuditEvent，以及 Package/数据库/Kubernetes 检索证明没有新增 Package 类型、Template 强依赖或执行资源。

#### `UJ-PROJECT-001`：创建基础 Project 并进入 Software Delivery Setup

- 前置条件：当前 Principal 有权在一个 Owner Scope 创建 Project；Software Delivery Solution 存在一个唯一合格 package release，或 Policy 已推荐一个精确 Active release；该 release digest 一致且 Package 检查通过。
- 用户操作：
  1. 进入 Projects，点击“创建 Project”；
  2. 填写 `display.name`（Project 名称）和 `display.description`（Project 说明）；
  3. 在单一 Scope 下确认 `owner_scope_id`（Project 所属治理范围）只读继承；
  4. 选择 Software Delivery Solution 卡片，查看名称、说明、来源、精确版本、digest、Package 检查和配置要求摘要；
  5. 点击“继续配置”。
- 用户可见预期：页面不要求填写 `project_id`、Allowed Solution Policy、Component Policy、状态、SemVer 或 digest；唯一/Policy 推荐 release 明确显示为已选中；成功后进入 Solution 定义的 Project Setup，Project 列表显示“未配置”。
- 平台处理预期：`BeginProjectSetup` 依次执行 Authorization、当前有效 Solution Policy、ComponentInstall Active、artifact digest 与 Package 检查校验；通过后创建含名称、说明、Owner Scope、初始 revision 和 configuration digest 的基础 Project 与 AuditEvent，返回 `project_id + solution + project_setup` 编辑上下文；`Project.solution_setup` 保持为空。
- 不通过条件：说明字段缺失；让用户手工填写内部 ID/策略/digest；校验失败仍创建 Project；把当前 Solution 写入 `selected_solution` 或部分 `solution_setup`；创建 Workflow、AgentRun、Workspace 或 Job。
- 所需证据：创建表单与 Solution 卡片截图、BeginProjectSetup 请求/响应、Project 查询、AuditEvent、Workflow 与 Kubernetes 资源无新增证明。

#### `UJ-PROJECT-002`：多个 Solution 版本的精确选择

- 前置条件：同一个逻辑 Solution 存在至少两个当前 Scope 允许、Active、digest 一致且 Package 检查通过的并行 package release；分别准备“Policy 推荐一个精确 release”和“无推荐 release”两组测试策略。
- 用户操作：
  1. 在有 Policy 推荐的场景打开 Solution 卡片；
  2. 查看默认选中的推荐版本，并展开版本选择器查看其他候选；
  3. 切换到另一个明确版本；
  4. 在无推荐场景重新打开创建页并选择版本；
  5. 点击“继续配置”。
- 用户可见预期：每个候选展示精确 SemVer、artifact digest 和 Package 检查结果；Policy 推荐项有清晰标记但允许选择其他合格 release；无推荐场景不预选任何一个 release 并要求用户明确决定。
- 平台处理预期：Catalog Query 返回经过 Policy 和组件治理过滤的精确候选；Platform API Adapter 提交结构化 `ExactComponentRef<Solution>`；服务端按用户最终选择重新校验，不使用 `latest`、版本排序或发布时间解析。
- 不通过条件：默认最高 SemVer；把“推荐”隐藏成不可解释默认值；只提交 Solution 展示名；用户切换 package release 后服务端仍使用旧 release；Catalog 更新静默改变当前已提交上下文。
- 所需证据：两组策略下的页面截图、Catalog Query、最终 BeginProjectSetup 请求、服务端解析记录和 AuditEvent。

#### `UJ-PROJECT-003`：离开未完成 Setup 后保持未绑定

- 前置条件：已通过 `UJ-PROJECT-001` 创建一个基础 Project，但尚未采用 `solution_setup`。
- 用户操作：
  1. 在进入 Project Setup 后不填写后续配置，直接离开页面；
  2. 在 Project 列表和详情查看该 Project；
  3. 重新进入 Project Setup；
  4. 重新选择 Solution 并继续。
- 用户可见预期：离开前显示未保存提示；列表/详情只显示派生的“未配置”，不显示已绑定 Solution 或“可创建 Workflow”；重新进入时要求重新选择 Solution，不伪造跨会话草稿。
- 平台处理预期：Project 持久化事实保持不变且 `solution_setup` 为空；没有 Project 专属 Draft/Ready/Active 状态、`selected_solution` 或部分配置值；重新选择后服务端重新执行授权、Policy、安装、digest 与 Package 检查校验。
- 不通过条件：浏览器缓存被当作平台事实；上次选择自动变成绑定；生成 Project Setup 状态机或专属草稿聚合；未配置 Project 可以创建 Workflow；退出动作删除已创建的基础 Project。
- 所需证据：离开提示、前后 Project 查询、数据库对象数量、重新进入页面、第二次校验记录和创建 Workflow 被拒绝的结构化 blocker。

#### `UJ-PROJECT-004`：直接配置 Repository URI 与单一 CredentialBinding

- 前置条件：已进入 Software Delivery Project Setup；准备一个公开 Git Repository、一个私有 GitHub Repository、一个可用于该私有仓库的 GitHub App CredentialBinding，以及满足其余预检的 TeamBinding fixture。
- 用户操作：
  1. 在 Primary Repository 输入 `repository_uri`（Repository 地址），不先创建或选择 Repository Catalog 项；
  2. 使用公开仓库执行连接检查，确认页面不显示 Credential 必填；
  3. 改为私有仓库，只选择一个 `credential_binding_id`（Repository 凭证绑定身份），不配置读、写两套凭证；
  4. 查看系统识别的 `provider_type`（Provider 类型）、`default_branch`（默认分支）和访问结果；
  5. 按需增加一个只读附加 Repository，并把这些值保留在当前未提交 Project Setup 候选中。
- 用户可见预期：页面直接接受 URI；Primary 用途显示为可写但不要求用户填写内部枚举；私有仓库只有一个 CredentialBinding 控件；没有 Repository Catalog、`read_credential_binding_id`、`write_credential_binding_id` 或 Project Rules 内容输入；平台托管 Preview 仍为只读说明。
- 平台处理预期：`repository_binding` 字段值只引用当前 Candidate 中系统生成 `binding_key`（Repository 绑定稳定键）的类型化 RepositoryBinding；同一 Candidate 保存规范化前的 `repository_uri + access + credential_binding_id`。Repository Probe 使用受控短期凭证读取 Provider、默认分支和访问能力，并把系统结果作为本次候选评估输入；公开仓库允许空 Credential。此阶段不修改 Project、不创建 Repository Catalog 或独立 Repository 聚合；后续采用时才把该 Binding 的规范化 URI、Provider、默认分支和展示信息写入当前 `solution_setup`。CredentialBroker 在 Clone/Review/Push/PR/Release 时按当前 subject、责任和用途签发最小权限 lease，并记录 AuditEvent；Reviewer/Quality 不获得写 lease。
- 不通过条件：必须先建立 Repository Catalog；把展示名称当仓库身份；要求读写两个 CredentialBinding；把同一原始 PAT 注入所有 Workspace；默认猜测 main/master；Project Setup 要求填写 Project Rules；URI/凭证校验失败仍被判断为可保存；预检查前修改 Project。
- 所需证据：公开/私有两种表单截图、Repository Probe 请求与脱敏结果、未提交 Candidate 请求、CredentialBinding/lease 与 AuditEvent、Reviewer 无写权限证明、Project revision/solution_setup 未变化证明，以及数据库/API Schema 检索证明不存在 Repository Catalog 依赖和读写双凭证字段。

#### `UJ-PROJECT-005`：按 Solution 岗位绑定候选 Agent

- 前置条件：已进入 Software Delivery Project Setup；Solution 提供 Required `architect`、`developer`、`reviewer` 等岗位；Agent Catalog 中存在能力满足、能力不满足和多个合格候选 AgentDefinition。
- 用户操作：
  1. 打开“Agent 岗位”，查看每个岗位名称、职责说明、Required/Recommended、基础能力和推荐模板；确认页面不要求 Project 用户填写岗位稳定键；
  2. 为 `architect` 选择一个候选 Agent，为 `reviewer` 选择多个候选 Agent；
  3. 尝试选择能力不满足的 Agent并查看原因；
  4. 清空一个 Required 岗位并查看 blocker；
  5. 恢复合法候选，并把责任绑定保留在当前未提交 Project Setup 候选中。
- 用户可见预期：用户不填写 `responsibility_id` 等内部身份；一个候选显示为精确绑定，多个候选显示为无序候选池且没有优先级排序；页面不重复编辑模型、Skills、MCP、Runtime 或资源，只读展示 AgentDefinition 摘要；缺少候选时提供跳转 Agent Center 的入口。
- 平台处理预期：Solution 顶层 Slot 的系统 `slot_key`、名称/说明、`setup_requirement`、`capability_requirements[]` 和推荐模板只读进入页面；当前 Candidate 的 `responsibility_bindings[]` 只接受用户选择的 `candidate_agent_definition_ids[]`，采用时把 Slot 规范化为 Team Binding 的 `responsibility_id + required_capabilities[]`。所有候选必须存在、digest 一致且满足基础能力和 Project Policy；具体 WorkUnit 的独立性、Review 与返工由其 Contract/Harness/Admission 校验，不从 Slot 推导。此阶段不修改 Project，系统不生成默认 Team。后续采用时写入当前 Project Team bindings，实际 Workflow 固化 TeamBindingSnapshot 后再从其无序池选择精确 AgentDefinition。
- 不通过条件：让作者或 Project 用户手填 `slot_key`/`responsibility_id`；把候选顺序当优先级；自动绑定推荐模板；在 Slot 中配置岗位来源、岗位间独立性、模型、Skills、MCP、Runtime、Credential 或返工 Capability；允许 Required 岗位为空却显示可继续；选择能力不满足或池外 Agent；修改 AgentTemplate 或既有 AgentDefinition；综合预检查前修改 Project；创建 Team 候选时启动 Workflow、AgentRun、Workspace 或 Job。
- 所需证据：岗位页面截图、Solution responsibility slot 查询、未提交 Candidate 请求、Project revision/solution_setup 未变化证明、能力不满足/空 Required blocker、Agent Center 往返、AuditEvent 和 Kubernetes 资源无新增证明。

#### `UJ-PROJECT-006`：对未保存 Project Setup Candidate 执行完整预检查

- 前置条件：基础 Project 已存在且 `solution_setup` 为空；已选择精确 Software Delivery Solution；Repository、外部参数、Required Agent 岗位和条件性执行策略已形成一组可通过的页面候选；另准备一个能力不满足的 Agent、一个不可用 Credential 和一个会超时的必需外部目标 Fixture。
- 用户操作：
  1. 在 Project Setup 查看 Solution 声明的业务参数，按字段名称和说明填写固定 Harness 确实需要的外部系统参数；
  2. 确认 BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务路径、Harness Extension 装配、平台托管 Preview、默认 Runtime/Workspace/Governance Policy 都只读展示来源与作用，不要求用户选择 ID、版本、顺序或实现；
  3. 先不配置外部 Promotion 目标，确认内置 Software Delivery Setup 仍可继续；再按需添加目标，只填写目标名称/类型、环境、Endpoint 和条件性 CredentialBinding，查看系统只读解析出的 DeploymentDriver 与目标配置摘要；
  4. 使用有权企业用户选择一个类型化 Runtime/Workspace/安全策略收紧项，确认普通用户不显示该控件，且任何扩大平台/Scope Policy 或选择 RuntimeDriver/WorkspaceProvider 实现的请求立即拒绝；
  5. 打开“检查与采用”区域，确认无需填写额外预检字段；
  6. 等待相关输入稳定后触发自动检查，并查看本次检查时间和候选摘要状态；
  7. 依次展开 Solution/Component、Repository/External、Agent Slots、Runtime/Workspace/Environment、Authorization/Governance 五组结果；
  8. 把 Reviewer 候选改成能力不满足的 Agent，确认旧结果立即变为“已过期”，并等待新检查返回 blocker；
  9. 依次制造 Credential 不可用和必需外部目标超时，查看受影响对象、中文原因、修复建议和直达修复动作；
  10. 修复所有 blocker，点击“重新检查”，确认当前 Candidate 返回 `ready`，然后离开页面但不执行保存与采用。
- 用户可见预期：页面只有一套 Project Setup 输入；可选外部 Promotion 目标缺省不阻止 Software Delivery，配置时用户不需要理解 DeploymentDriver、Namespace 或 Harness；平台托管 Preview 与外部 Promotion 目标明确分开。默认平台能力只读可解释，只有被授权且确有需要时才出现策略收紧项。自动检查与“重新检查”共用同一结果区；blockers 与 warnings 分开；每个问题都有稳定 code、中文说明、subject、修复建议和可用时的直达动作；Required 外部 Unknown/超时不能被忽略；输入变化后旧 Ready 不可继续使用；离开后 Project 仍显示“未配置”。
- 平台处理预期：Web 内联提交 `ProjectSetupCandidate`；Platform Core 执行 Authorization，按 Solution 顶层 `project_setup_schema` 和 Policy 归一化并计算 `candidate_digest`。Generic Form Field Type Catalog 根据实际字段类型自动路由 Repository Probe、Credential readiness/Policy、Environment/Target Probe 或 Artifact 校验，再并行调用 Package/Extension Resolver、Agent Capability、RuntimeDriver 接口索引、内部 Workspace Port 与 Policy；返回瞬时 `ProjectSetupEvaluation` 的 `readiness + checks[] + blockers[] + warnings[] + resolved_packages[] + evaluated_at`。响应不含 `component_resolution_preview_id`，不修改 Project，也不创建 LockedComponentSet、Workflow、ComponentRun、AgentRun、Workspace 或 Job；强制 Authorization/Credential 使用审计仍保留。
- 不通过条件：要求先保存 Project 或创建中间版本对象才能检查；强制配置可选 Promotion 目标；要求用户填写 Preview Namespace、DeploymentDriver/ExtensionPackage、Baseline、Harness、RuntimeDriver 或 WorkspaceProvider；允许策略扩大上级权限；让 Solution 作者填写 `required_component_capabilities[]`、`required_extension_capabilities[]` 或同义 Probe 数组；把每一组检查做成不同的持久化状态；Web 自己判断 Active、Capability 或权限；把外部超时降为 warning；通过 Harness Extension、脚本、Webhook 或领域 Renderer 执行任意 Setup Probe；字段类型所需在线检查没有平台窄类型化端口仍允许发布或显示 Ready；迟到响应覆盖较新的 Candidate；`ready` 被写成 Project 状态；离开页面后 Candidate、Evaluation 或 Preview 仍可从平台查询；预检查启动 Workflow 或执行资源。
- 所需证据：完整页面录屏或截图、两次不同 `candidate_digest` 的请求/响应、五组结果和 Finding 字段、迟到响应抑制记录、Provider/Registry/Probe 调用追踪、Authorization/Credential AuditEvent、数据库对象计数不变，以及 Kubernetes Workflow/Job/Pod/PVC 无新增证明。

#### `UJ-PROJECT-007`：原子保存并采用 Project Solution Setup

- 前置条件：`UJ-PROJECT-006` 的当前 Candidate 为 `ready`；记录当前 Project `revision`，并准备一个并发修改 Fixture 用于制造 stale revision。
- 用户操作：
  1. 点击唯一主操作“保存并采用”；
  2. 等待服务端重新校验并返回保存结果；
  3. 刷新 Project 详情，查看已采用 Solution、Repository、环境、Agent 岗位和配置摘要；
  4. 使用旧 `expected_revision` 重复提交另一份候选，观察并发冲突；
  5. 重新读取最新 Project 后再次编辑并保存。
- 用户可见预期：成功时一次完成保存与采用，不出现“先建配置版本、再建团队版本、最后绑定”的中间步骤；页面显示新的 Project revision、最后修改人/时间和配置摘要，但不要求用户命名或选择业务版本号。并发冲突保留用户输入，提示重新读取和比较，不伪造成功。
- 平台处理预期：`AdoptProjectSolutionSetup` 在命令内重新执行同等或更严格校验；同一事务原子替换完整 `Project.solution_setup`、递增 `revision`、重算 `configuration_digest` 并写入 AuditEvent。任何一步失败都不产生部分更新；系统不创建 Project 配置版本聚合、团队绑定版本聚合、LockedComponentSet、Workflow、Run、Workspace 或 Job。
- 不通过条件：先写部分 Repository/Team 再失败；依赖旧 `ready` 直接跳过校验；CAS 冲突覆盖他人修改；一次保存创建多个需要用户理解的 Version 对象；失败后 Project 被显示为已配置；保存 Project 时启动 Workflow 或执行资源。
- 所需证据：保存前后 Project 查询、revision/configuration digest 变化、完整 `solution_setup`、事务与 AuditEvent、并发冲突响应、失败注入下无部分写入证明，以及数据库/Schema 检索证明不存在 Project 配置版本和团队绑定版本聚合。

#### `UJ-PROJECT-008`：更新当前配置并为既有目标创建后继根 Workflow

- 前置条件：Project 已采用可运行的 `solution_setup`；存在一个活动根 Workflow，已固定旧 Project revision/digest、TeamBindingSnapshot 和 LockedComponentSet；准备需要作为未来默认团队成员的新 AgentDefinition，或新的 SolutionPackage/ContentPackage/ExtensionPackage release，以及一组仍有外部副作用未确认的阻塞 Fixture。仅用于解决当前 MissingExecutor 的 Workflow-local Agent 选择不属于本用例。
- 用户操作：
  1. 进入 Project Setup 编辑当前 Repository、Team bindings 或精确 Solution/Component 选择；
  2. 查看“只影响之后创建的 Workflow”提示，执行预检并用当前 `expected_revision` 保存；
  3. 返回原活动 Workflow，确认其 Solution、Project 配置快照、TeamBindingSnapshot、LockedComponentSet、Plan 和 WorkUnit 状态均未变化；
  4. 点击“按当前配置创建后继 Workflow”，查看旧快照与当前 Project 的精确差异、可复用 Artifact、能力变化、组件撤销/失效和外部副作用风险；
  5. 在无 blocker 场景对影响分析提交正式批准 Decision，选择需要复用的不可变 Artifact，并提交后继 Workflow；
  6. 在未确认外部副作用 Fixture 中尝试提交，观察系统要求先明确 Pause、Cancel、Abandon 或允许并行；
  7. 打开前序与后继 Workflow，核对身份、来源与各自独立的执行快照。
- 用户可见预期：Project 只显示新的内部 revision/digest，不要求命名业务 Version；AgentDefinition/package 发布或激活不会自动改写 Project；原 Workflow 没有“升级当前快照”操作。后继 Workflow 使用新 identity，并只读显示 `predecessor_workflow_name`（直接前序 Workflow 名称）、`impact_analysis_artifact_id`（影响分析 Artifact 身份）和 `approval_decision_id`（批准 Decision 身份）；旧 Plan、WorkUnit 状态、Gate 通过结果和审批不会出现在可复用清单中。
- 平台处理预期：`AdoptProjectSolutionSetup` 只原子更新当前 Project。`PrepareSuccessorWorkflow` 读取前序 Workflow 当前 `spec/status` 快照与当前 Project/组件解析结果，保存带精确来源 ID/digest 的影响分析 Artifact，不修改前序、不创建执行资源；Decision 必须绑定该 Artifact ID/digest。`CreateWorkflowFromProjectSetup` 重新校验当前 Project revision/digest、Decision 和组件可用性，创建新的 Goal Artifact、Workflow identity、Project 配置快照、TeamBindingSnapshot、LockedComponentSet 和小型 OrchestrationDefinition，实例化并编译当前 Solution 的 BaselinePlanTemplate，形成新的 `fixed-baseline` Plan 与初始 `activePlanId`，并在 `Workflow.spec.predecessorWorkflowName` 保存来源。前序 Artifact 可以成为显式输入；前序 Active Plan、WorkUnit、Run、GateResult 和批准状态不得复制。
- 不通过条件：修改 Project 后活动 Workflow 的 `spec` 或解析组件发生变化；发布新 AgentDefinition/package 后 Project 自动采用；创建 Project/Team 配置 Version 聚合；在旧 Workflow 内替换 LockedComponentSet；没有影响分析或 Decision 就创建后继；Decision subject/digest 已过期仍通过；复制旧 Gate/审批为已满足；未处理重复外部副作用仍启动执行；前序被静默 Cancel/Abandon。
- 所需证据：Project 更新前后查询与 AuditEvent、旧 Workflow 前后完整 `spec/status` 对比、影响分析 Artifact 与 Decision、后继创建请求、两个 Workflow identity 与 `predecessorWorkflowName`、新旧 LockedComponentSet/OrchestrationDefinition、输入 Artifact 列表、新 Workflow 的 fixed-baseline Plan/`activePlanId` 与首个 Ready WorkUnit、阻塞 Fixture 的 Admission 结果，以及数据库/API Schema 检索证明不存在 Workflow Migration/Project Configuration Version 聚合。

#### `UJ-SOLUTION-001`：固定核心定义并复用同一 Harness 生命周期视图

- 前置条件：当前 Principal 有 Solution 作者权限；至少准备一个 WorkUnit Contract、一个可被 `execution.kind=executor` 匹配的 Executor、一个带 `display.name`/`display.description` 的 Guide Extension、两个 Sensor Extension 和一个受信任 GateEvaluator；所有 Package release Active 且对应 Package/接口检查通过。
- 用户操作：
  1. 进入 Solution Studio，创建 Solution 身份、精确版本、名称和说明；
  2. 配置 `baseline_plan_template`（批准基线模板内容绑定），并在图中明确所有业务终态路径；需要动态规划时，在模板中放置显式 DAG Orchestration WorkUnit，并为其配置 `orchestration.dag` 责任/能力要求、typed inputs、PlanDraft Contract、PlanningRules 边界和普通 Harness Guide；DAG Orchestrator 推荐 AgentTemplate 只在对应责任岗位中配置，模板自行固定完整 Skills；
  3. 打开一个 WorkUnit 的统一生命周期编辑器，按“执行准备 → 主执行 → 观察与评估 → Gate 判定 → Kernel 后续处理”分别配置 WorkUnit `execution` 与 Harness Extension；
  4. 在同一页面查看 `phase`（生命周期阶段）、`extension_point`（技术扩展点）、能力名称/说明、ExtensionPackage 精确版本/digest、同阶段顺序、Required/Optional、输入输出、权限、`effect_mode`（副作用模式）和失败/恢复方式；
  5. 调整同阶段顺序、保存并发布 Solution Package；
  6. 打开不可变 Harness 详情，再运行黄金 Fixture 并从 Workflow WorkUnit 打开运行追踪。
- 用户可见预期：BaselinePlanTemplate、模板中的显式 DAG 编排节点和终态业务路径明确标记为当前 Solution package release 固定内容，不显示为 Project 可覆盖的“默认项”；Studio 不出现独立完成合同编辑器。主执行行明确标记“来源：WorkUnit.execution”，Guide/Sensor/Gate 行标记“来源：Harness”；编辑页与详情页具有完全相同的分组、行身份和字段，详情页只读；运行追踪只附加 ComponentRun、AgentRun、human Run、Observation 和 GateResult 状态；Trigger/EventHandler 显示在 WorkUnit Harness 之外的事件入口/响应位置。
- 平台处理预期：主执行 `phase` 由 WorkUnit `execution` 推导，Harness 行 `phase` 由 Binding 类型或 `extension_point` 推导，`order` 只由 HarnessDefinition 数组位置决定；HarnessDefinition 不保存 Executor。package-local BaselinePlanTemplate 与 HarnessDefinition 使用局部 ID + content/config digest，发布 Solution 时整体固定在一个精确 Solution release 中；Plan Compiler 校验所有可选择分支闭合且能够到达明确终态，Kernel 只使用固定 DAG 收敛规则判断成功。动态规划方法由精确 DAG Orchestrator Agent Instructions/Skills、InvocationRequest、WorkUnit Contract、PlanningRules 和普通 Harness Guide 组合，不创建独立 Solution 级 Guide。独立发布内容使用 ExactComponentRef。系统不创建独立完成对象、Harness 预览资源、第二套 Harness 详情 DTO、重复顺序字段或持久化展示副本。
- 不通过条件：允许 Project 用户替换 BaselinePlanTemplate、DAG 编排节点、终态业务路径或 execution/Harness 装配；出现 `completion_contract`、`completion_requirements[]` 或其他 Solution 自定义完成表达式；创建独立 Solution 级动态规划 Guide；HarnessDefinition 保存 Executor 或向 WorkUnit 投影 `execution`；Extension 自报任意生命周期 phase；编辑页和详情页字段、分组或顺序不一致；详情通过另一份可漂移配置生成；运行页重新发明生命周期步骤；原始 Extension Binding 被当作普通 Project 必填表单；修改核心定义或 Harness 后原地覆盖已发布 Solution release 内容。
- 所需证据：Solution Studio 编辑页、不可变详情页和 WorkUnit 运行页截图；三页逐行 identity/order 对照；发布前后 package-local HarnessDefinition ID/config digest 与 Solution release 查询；分支闭合和终态可达的 Plan Compiler 正反 Fixture；Extension Descriptor；Package/接口检查报告；数据库/API/Package Schema 检索证明不存在独立完成对象、完成表达式或 Harness 预览资源。

#### `UJ-SOLUTION-002`：创作 Baseline 节点并启用独立 Review/返工闭环

- 前置条件：当前 Principal 有 Solution 作者权限；当前 Solution 草稿已定义顶层 `workflow_input_schema`；已有 WorkUnit Contract、主 Executor、程序 Sensor、Review Guide、ReviewResult Contract、GateEvaluator；Project fixture 的 Team bindings 能分别解析作者和 Reviewer 责任，并可在 Workflow fixture 中固化 TeamBindingSnapshot。
- 用户操作：
  1. 创建 BaselinePlanTemplate，填写 `display.name`（模板名称）和 `display.description`（模板说明）；确认编辑器只读提示 Workflow 输入来自 SolutionPackage 顶层 `workflow_input_schema`，不再选择或复制 Schema；
  2. 添加 Requirement Baseline、Solution Design、Technical Overview、Detailed Design、Acceptance Planning、Project Rules 和显式 Delivery DAG Orchestration 等节点，配置节点说明、WorkUnit Contract、责任要求、硬依赖与必要 typed `input_bindings[]`；Delivery 节点的能力要求包含 `orchestration.dag`；
  3. 分别打开三个固定 Design 节点，在“主执行”区域配置唯一 WorkUnit `execution`，再选择完整 HarnessDefinition 查看四象限和 Gate/返工；确认 Studio 只把它们归组显示为 Architecture Design 阶段，不创建同名节点；
  4. 创建或编辑 package-local HarnessDefinition，在“观察与评估”勾选“启用独立 Review”，配置 `responsibility_requirement`（Reviewer 责任要求）、Review Guide 和 `output_contract`（ReviewResult 输出合同内容绑定）；
  5. 在 Gate 区域单独启用用户批准 Decision Requirement，并查看 Reject、Pass-With-Nits 和返工说明；
  6. 发布新的 Solution release，整体固定 HarnessDefinition、BaselinePlanTemplate 与其他 package-local 内容；运行 Fixture，让 Reviewer 首轮 Reject，作者返工后程序验证通过，再由 Reviewer Pass，最后由用户批准。
- 用户可见预期：节点只显示一个 `execution` 和一个完整 Harness 选择；主执行在同一生命周期编辑器中只配置一次，但明确不属于 Harness；不出现第二个 ExecutionBinding、Harness 多选或“选择 Reviewer Extension”。Architecture Design 只是三个顺序节点的显示分组；勾选 Review 后同一编辑器原位展开责任 Run；用户批准与 Reviewer Pass 分开显示。
- 平台处理预期：PlanNodeTemplate 分别保存唯一 `execution` 和一个精确 `harness` 内容绑定；三个 Design 节点分别保存自己的 Contract、execution、Harness 和顺序硬依赖，不存在 Architecture Design PlanNodeTemplate 或 DetailedDesign 动态基线 DAG；Plan Compiler 分别校验 execution 与 Harness，绝不从 Harness 生成主执行。Delivery DAG Orchestration 节点用 `input_bindings[]` 绑定批准基线输出，并固定 `orchestration.dag` 执行要求、PlanDraft Contract、PlanningRules 边界和普通 Harness Guide；稳定规划方法与 Skills 来自匹配的 DAG Orchestrator AgentDefinition。Harness 的 `inferential_sensor_bindings[]` 保存 Required `kind=responsibility-run`。程序验证通过后 Kernel 匹配 Reviewer Executor、组装独立 InvocationRequest 并产生 `StartReview`；Platform Core 再解析精确 Reviewer。发布后的模板形成 fixed-baseline Plan，不创建 hidden initial DAG authoring。
- 不通过条件：BaselineTemplate 绕过 Plan Compiler/Package 检查直接成为活动计划；用户手填模板/节点/digest ID；把 Architecture Design 保存成第四个节点；在批准基线阶段动态生成 DetailedDesign DAG；动态交付没有显式 DAG Orchestration 节点而依赖 Plan 耗尽触发；创建 Solution 级动态规划 Guide，或把 Agent/Harness 指引写入全局 OrchestrationDefinition；HarnessDefinition 保存主 Executor；一个节点保存多个 Harness ID；Sensor Extension 调用 `agent.invoke` 启动 Reviewer；Reviewer 与作者共享隐藏 Session/Workspace；Review Pass 自动代替用户批准；Reject 直接重置为 Ready；修改已发布 Harness 或 Template 后原地覆盖既有 Solution release 内容。
- 所需证据：Baseline 节点/边与 input binding 编辑页、Harness 编辑/详情/运行页截图；Solution release 内 BaselinePlanTemplate/HarnessDefinition、终态业务节点/Gate、DAG Orchestrator 责任岗位推荐 AgentTemplate 及模板 Skill 依赖闭包、fixed-baseline Plan 与 WorkUnit 查询；节点定义中不存在 AgentTemplate/Skill 引用，并且不存在 `delivery_planning_guide`、`review_required`、独立完成表达式和嵌套 `*_version_id`；LifecycleAction、InvocationRequest、Run request、TeamBindingSnapshot 解析、AgentRun、Workspace、Review Observation、GateResult、Decision 和完整返工状态轨迹。

#### `UJ-SOLUTION-003`：定义 Project Setup、责任岗位和 Workflow 行业输入

- 前置条件：当前 Principal 有 Solution 作者权限；已创建一个尚未发布的 Solution 草稿；平台 Generic Form Field Type Catalog 已提供 `text`（文本）、`number`（数值）、`boolean`（布尔）、`choice`（选择）、`repository_binding`（Repository 绑定）、`credential_binding`（凭证绑定）、`deployment_target_binding`（部署目标绑定）和 `artifact_input`（成果输入）；Catalog 中存在可推荐的 AgentTemplate。
- 用户操作：
  1. 打开 Solution Studio 的统一“配置与输入”页面，确认页面只包含 `project_setup_schema`（Project 长期配置表单）、`responsibility_slots[]`（责任岗位集合）和 `workflow_input_schema`（Workflow 行业输入表单）三个顶层分组，不出现 `project_setup` 包装层；
  2. 在 Project 配置分组新增普通字段和资源字段，填写 `display.name`（字段名称）、必需的 `display.description`（字段说明），选择 `field_type`（字段类型），并按需配置 `required`（是否必填）、`default_value`（默认值）与 `validation`（静态校验）；确认 `field_key`（字段稳定键）由系统生成且不要求作者填写；
  3. 新增 `choice` 字段，为每个 option 同时填写稳定 `value`、用户可读名称和说明；制造缺失说明、默认值类型不匹配和重复 option value 三种错误并查看原位诊断；
  4. 新增 Architect 责任岗位，填写岗位名称、职责说明、Required/Recommended、`capability_requirements[]`（基础能力要求）和推荐 AgentTemplate；确认 `slot_key`（岗位稳定键）由系统生成，编辑器没有岗位来源、岗位间独立性、具体 AgentDefinition、Runtime、模型、MCP、Credential 或返工 Capability 字段；
  5. 在 Workflow 输入分组添加行业特有字段，确认平台固定 Goal 不在这里重复定义，BaselinePlanTemplate 编辑器也不再选择 Workflow Schema；
  6. 查看资源字段说明：Repository 自动对应 Repository Probe，Credential 自动对应凭证就绪与 Policy 检查，部署目标自动对应 Environment/Target Probe，Artifact 自动对应身份/digest/类型/授权校验；确认页面没有 `required_component_capabilities[]`、`required_extension_capabilities[]`、脚本、Webhook 或 Harness Probe 配置；
  7. 保存和自动保存草稿，确认只递增草稿 CAS revision 并重算 draft digest，不创建 Schema Artifact、Schema Version 或可安装 Package；
  8. 依次制造明文 Secret 默认值、平台不支持的字段类型、字段类型缺少必需窄检查端口等失败条件，运行发布检查；修复后发布精确 SolutionPackage；
  9. 使用该 release 进入 Generic Project Setup 和 New Workflow，确认三类定义按相同名称与说明渲染；采用 Project 并创建 Workflow，检查 Project 长期值进入 `Project.solution_setup`，本次行业输入与 Project 快照进入 `Workflow.spec` 的不可变创建时快照字段，相关 Agent InvocationRequest 能看到字段语义，而 Kernel 输入不包含表单 Schema。
- 用户可见预期：作者只定义业务字段和岗位的可读语义，不需要理解或填写内部 ID、digest、Version、Probe Capability、Kernel 或数据库结构；字段与选项始终同时展示 Value 的含义；Project 与 Workflow 表单使用同一种字段设计语言；资源字段明确说明“选择/创建什么、最终保存什么、平台会检查什么”；岗位页只负责 Project 初始候选配置，不把动态 DAG 限制为固定角色集合。
- 平台处理预期：Solution Definition Module 调用 Generic Form Field Type Catalog 统一完成 Schema 规范化、类型校验、Renderer/Normalizer/Probe Port 解析和安全检查；责任岗位规范化器生成稳定 `slot_key` 并校验基础能力与推荐模板。草稿是带 CAS revision 的普通可编辑内容；发布时三个顶层值直接固定在 SolutionPackage artifact digest 中。Project & Team Module 消费 `project_setup_schema + responsibility_slots[]`，Workflow Module 消费 `workflow_input_schema`；字段类型自动路由 Platform Core 的窄类型化检查，资源值只保存平台绑定/成果身份且不含明文 Secret。Orchestrator Kernel 只接收规范化后的 WorkflowInput/InputResource，不依赖 Generic Form Engine 或 SolutionPackage Schema。
- 不通过条件：把三个定义嵌套进新的 ProjectSetupDefinition 聚合；把 `workflow_input_schema` 放进 BaselinePlanTemplate；要求作者手填字段键、岗位键、Schema ID、digest 或 Version；字段/选项只有 Value 没有名称和说明；为 Project 与 Workflow 发明两套不兼容 Schema；允许资源字段保存明文 Secret；要求作者声明 Probe Capability 数组；允许脚本/Webhook/Harness 代替受治理 Probe；字段类型缺少平台检查端口仍可发布；Slot 携带岗位来源、独立性、具体 Agent 或 Runtime 配置；把 Slot 当动态 DAG 责任白名单；草稿保存生成不可变业务版本；Kernel 解析表单定义。
- 所需证据：“配置与输入”编辑/只读详情、字段类型选择器与每类说明、自动生成 field/slot key、choice 选项、校验错误、责任岗位编辑器、Baseline 编辑器无 Workflow Schema 选择、草稿 CAS 请求与 revision/digest、发布检查报告、精确 SolutionPackage 内容/digest、Generic Project Setup/New Workflow 页面、Project.solution_setup、Workflow.spec、InvocationRequest、Kernel advance 输入，以及数据库/API/Package Schema 全局检索证明不存在包装聚合、Schema Version、作者侧 Probe 数组和重复字段语言。

#### `UJ-SOLUTION-004`：为 Baseline 固定 DAG 节点定义 WorkUnit Contract 并准备责任岗位推荐 AgentTemplate

- 前置条件：当前 Principal 有 Solution 作者权限；Solution 草稿已存在 BaselinePlanTemplate 和至少一个责任岗位；Content Catalog 中存在一个带完整精确 `skills[]` 的 AgentTemplate，另准备一个模板 Skill 缺失/撤销 Fixture；准备两个数据合同兼容和一个不兼容的上下游槽位 Fixture。
- 用户操作：
  1. 打开 BaselinePlanTemplate 的 Solution Design 固定节点，在 WorkUnit Contract 区域选择“在当前 Solution 中创建”；填写 `display.name`（工作合同名称）和 `display.description`（工作合同说明）；
  2. 新增 `input_slots[]`（输入槽位集合）和 `output_slots[]`（输出槽位集合），为每项填写名称、说明、Required/Optional，并选择 `data_contract`（数据合同）；确认 `content_id`、`slot_key` 和 `content_digest` 由系统生成且只读；
  3. 将当前节点输入槽位通过 typed `input_bindings[]` 连接到上游已确认输出，分别运行兼容和不兼容数据合同预检；确认页面没有完成规则、权限脚本、节点级 AgentTemplate、节点级 Skill 或万能内容引用字段；
  4. 为节点配置 `responsibility_requirement`（责任要求）和基础 Capability，不选择具体 Agent、AgentTemplate、Skill、模型或 Runtime；
  5. 返回对应 `responsibility_slots[]` 岗位，选择一个 `recommended_agent_templates[]`（推荐 AgentTemplate）；查看模板精确来源 ContentPackage、版本/digest、Instructions 摘要、Capabilities 和只读“模板内置 Skills”；
  6. 对显式 DAG Orchestration 固定节点重复上述操作：节点只要求 `orchestration.dag` 能力、PlanDraft 输出 Contract、typed inputs 和 Harness，DAG Orchestrator AgentTemplate 仍只配置在责任岗位；
  7. 保存草稿并运行 SolutionPackage 发布检查；再运行模板 Skill 缺失/撤销、模板 Capability 不满足岗位、节点直接携带模板/Skill 引用和使用 `latest` 的失败 Fixture。
- 用户可见预期：Solution 作者在固定 DAG 节点中只理解“输入什么、产出什么、需要什么责任”，不需要猜 ID/digest，也不会看到两套 Agent/Skill 选择。Contract 默认跟随当前 Solution release；真正跨 Solution 复用时才选择独立 ContentPackage。岗位页展示推荐模板及其完整 Skills，节点页只显示岗位和推荐模板摘要，不保存模板内容。
- 平台处理预期：Solution Definition Module 将 WorkUnit Contract 规范化为 package-local `content_id + content_digest`，校验槽位名称/说明、必需性、数据合同和 typed binding 兼容性；PlanNodeTemplate 只保存精确 `work_unit_contract` 内容绑定、`responsibility_requirement`、`execution` 和 Harness。Content Catalog/Resolver 从岗位推荐 AgentTemplate 解析精确 ContentPackage 与完整 Skill 依赖闭包，校验 Capability 后写入 SolutionPackage 依赖解析结果；不要求作者重复列举 Skills。Project Setup 后续选择或实例化实际 AgentDefinition，普通实例化自动复制模板全部 Skills；Workflow 创建时固定 TeamBindingSnapshot。Kernel 只消费 Contract、责任/能力要求和 HostCapabilitiesSnapshot，不读取 AgentTemplate、SkillPackage 或 Content Catalog。
- 不通过条件：WorkUnit Contract 可以包含完成表达式、权限规则、脚本或任意求值器；节点保存 `agent_template`、`skills[]` 或万能 `content_refs[]`；同一 Skill 在节点和模板形成两份来源；推荐模板绑定具体 AgentDefinition、模型或 Runtime；缺少名称/说明或 Required 输出仍可发布；上下游数据合同不兼容仍能连线；模板 Skill 依赖缺失、撤销、digest 漂移或 Capability 不满足岗位仍通过；浏览器回传模板 Skills 被当作权威；使用版本范围或 `latest`；修改 package-local Contract 后原地覆盖已发布 Solution release。
- 所需证据：固定 DAG 节点 Contract 编辑页与只读详情、责任岗位推荐模板页、自动生成 ID/key/digest、兼容/不兼容 typed binding 结果、精确 SolutionPackage/package-local 内容、AgentTemplate 与 Skill 依赖闭包、发布检查正反报告、Project Setup 模板实例化、AgentDefinition Skills、Workflow TeamBindingSnapshot、Kernel 输入，以及 Schema/源码全局检索证明 PlanNodeTemplate 不存在节点级 AgentTemplate/Skill/万能内容引用字段。

#### `UJ-SOLUTION-005`：运行 SolutionPackage 发布检查并修正问题

- 前置条件：当前 Principal 有 Solution 作者和发布权限；存在一份尚未发布的 Solution 草稿；准备字段说明缺失、Baseline 依赖成环、Contract 不兼容、Required 组件不可解析、Generic Web 无法闭环和全部检查通过的正反 Fixture。
- 用户操作：
  1. 在 Solution Studio 点击“运行发布检查”；
  2. 查看每个错误的中文说明、受影响编辑位置和修复建议，确认失败不会生成 Package；
  3. 逐项修正当前草稿并重新运行检查，直到页面显示当前草稿通过；
  4. 再修改任意草稿内容，确认旧检查结果立即显示为过期；
  5. 使用旧 draft digest 或伪造的“已通过”结果尝试发布，确认服务端忽略该结果并重新检查当前草稿；
  6. 确认整个页面和处理过程不存在 Solution 发布 Reviewer、评审队列、通过/打回、ReviewResult、返工任务或独立校验 Workflow。
- 用户可见预期：发布检查只是当前编辑页中的问题定位工具；作者修改同一份草稿后重新检查即可，不需要等待其他角色接手。检查通过只表示当前页面内容未发现阻断问题，正式发布仍以服务端对当前内容的复检为准。
- 平台处理预期：Solution Definition Application Module 对当前 draft revision/digest 调用统一发布校验服务，返回瞬时结构化 diagnostics；不持久化批准状态、验证报告 Artifact 或可复用准入令牌。发布用例重新读取当前草稿、复用同一规则目录、重新计算规范化内容摘要，并只在全部检查通过时继续形成不可变 Package。该路径不创建 Workflow、WorkUnit、Run、Harness 调用、Reviewer 或 ReviewResult。
- 不通过条件：发布前必须选择独立 Reviewer；检查结果形成新的领域对象、状态机或 Version；旧检查通过状态能够跳过发布复检；浏览器回传检查结论成为权威；检查失败仍生成部分 Package；Solution 发布检查误用 Kernel、Harness 或 Agent 执行；把 WorkUnit 内部可选的独立 Review 与 SolutionPackage 发布审批混为一谈。
- 所需证据：发布检查页面正反 Fixture、错误定位和过期提示截图；检查与发布请求/响应；同一校验规则被编辑期检查和正式发布复用的测试；发布失败后 Catalog/Package 存储无新增证明；数据库/API/Schema/事件检索证明不存在 Solution 发布 ReviewRequest、ReviewResult、验证报告 Artifact、Workflow、WorkUnit、Run 或发布审批状态。

#### `UJ-SOLUTION-006`：发布并在当前平台安装启用 SolutionPackage

- 前置条件：存在一份通过 `UJ-SOLUTION-005` 的 Solution 草稿；准备仅有 `solution.publish` 的作者、仅有 `package.activate` 的管理员、同时拥有两项权限的 Principal，以及 Required Content/Extension 成员成功、成员检查失败、重复请求和同 SemVer 不同 digest Fixture。
- 用户操作：
  1. 作者打开发布页，查看 `package_version`（Package 语义版本）、内容摘要预览和 Required 依赖摘要；确认页面不要求填写 artifact digest、成员安装顺序、ComponentInstall ID、trust tier 或 lifecycle policy；
  2. 仅有发布权限的作者点击“发布”，确认平台重新校验当前草稿并形成不可变 SolutionPackage/ComponentBundle；页面显示精确 SemVer/digest 和“等待管理员安装并启用”，该 release 尚不能被新 Project 选择；
  3. 管理员打开该精确 release，点击一次“安装并启用”；确认页面只把“验证制品、安装成员、检查接口、启用目录”显示为同一次操作的进度，不提供 Verify、Install、Activate 三个按钮；
  4. 使用同时拥有两项权限的 Principal 对另一版本点击一次“发布并启用”，确认调用同一个公开组合用例并在任何写入前校验两项权限；
  5. 运行成功 Fixture，确认所有 Required 成员原子进入 Active，Solution Catalog 出现精确 Solution release，创建 Project 页面可以选择该版本；
  6. 运行成员失败 Fixture，确认没有任何 Required 成员部分 Active，Solution 不进入可选目录；已经发布的 release 保持原 SemVer/digest 且 inactive，页面显示失败成员、阶段和修复入口，修复后可对同一 release 重试安装；
  7. 重放相同幂等键和请求摘要，确认返回原发布/安装结果；同键不同摘要以及同一 `package_id + SemVer` 不同 artifact digest 必须拒绝；
  8. 对内置 Embedded ComponentBundle 执行同一内部 Verify/Install/Activate 路径，确认没有 builtin 旁路；检查整个流程不创建 Workflow、Plan、WorkUnit、Run、Harness 或 Kernel action；
  9. 创建一个使用旧 Solution release 的活动 Workflow，再并排发布并启用新版本，确认旧 Workflow 的 LockedComponentSet 不变化，新 Project/后继 Workflow 才能显式选择新版本。
- 用户可见预期：作者最多点击一次完成发布，管理员最多点击一次完成安装启用；同一人具有两种权限时可以一次完成全部操作。用户能看到必要进度和失败原因，但不需要理解或操作 Verify、Install、Activate 的内部状态。成功后精确版本立即出现在 Solution Catalog；失败不会产生半激活行业方案。
- 平台处理预期：`PublishSolutionPackage` 由 Solution Definition Module 重新校验并形成不可变 release；`InstallAndActivatePackage` 由 Component Governance 验证精确 Package/Bundle、创建每个成员的 ComponentInstall、运行 Package/接口检查并原子激活 Required 成员；`PublishAndActivateSolutionPackage` 在 Platform Core 应用层先统一授权两项操作，再顺序复用前两个用例。发布成功但安装失败不覆盖或删除 release，只保持 inactive。Web、`oactl` 和第三方 Client 使用同一公开 API；Package 操作不经过 Workflow Controller 或 Orchestrator Kernel。
- 不通过条件：作者只有发布权限却能激活；管理员需要分别点击 Verify、Install、Activate；浏览器自行串联私有接口；同一人“发布并启用”绕过任一授权；失败后部分 Required 成员 Active；安装失败覆盖或删除已发布 release；Package 自动替换活动 Workflow 的 LockedComponentSet；要求用户填写系统 digest、安装顺序或信任等级；使用 Workflow/Kernel/Harness 承接 Package 生命周期。
- 所需证据：三种 Principal 的按钮与 `allowed_actions[]`、发布/安装/组合 API 请求响应、两项授权决策、Package artifact 与 ComponentInstall、后台阶段进度、Required 成员成功/失败和无半激活证明、Solution Catalog/Project 选择器、幂等与 SemVer/digest 冲突、Builtin 同路径 trace、旧 Workflow LockedComponentSet 对照，以及数据库/API/CRD 全局检索证明不存在 Package Workflow、WorkUnit、Run、Harness 或 Kernel action。

#### `UJ-WORKFLOW-001`：以最小 Goal 创建 Workflow/Delivery 并激活固定基线

- 前置条件：Project 已原子采用可运行的 Software Delivery `solution_setup`；精确 Solution release 的 BaselinePlanTemplate、显式终态业务路径、Harness、Package 与 Team bindings 均通过 Package/接口检查；另准备一个过期 Project revision Fixture。
- 用户操作：
  1. 打开 New Delivery，查看继承的 Project、Solution、Repository、Team、Preview 与执行策略只读摘要；
  2. 只填写 `goal.title`（交付标题）和 `goal.description`（自然语言目标）；
  3. 按需填写 `goal.context`（背景说明）、`known_constraints[]`（已知硬约束）和 `input_artifact_ids[]`（输入成果），不填写正式范围、非目标、Acceptance Criteria、Plan 或 DAG；
  4. 提交创建；
  5. 打开新 Delivery 的 DAG 和执行快照；再使用过期 `expected_project_revision` 重复提交一次。
- 用户可见预期：默认表单不要求目标用户、核心功能、范围、非目标、验收方向、Workflow ID、Plan ID、digest 或生命周期字段；只有多个 Repository 或其他确实参与本次 Workflow 的资源绑定需要选择时才显示对应控件，完成后直接 Promotion 的外部目标不在此处选择。成功后立即看到 Requirement Baseline、Solution Design、Technical Overview、Detailed Design、Acceptance Planning、Project Rules 和显式 Delivery DAG Orchestration 等固定基线节点，Web 可把三个 Design 节点归组为 Architecture Design 阶段；首个可执行节点进入 Ready/运行态，过期 Project 配置返回可修正的并发 blocker。
- 平台处理预期：`CreateWorkflowFromProjectSetup` 重新执行 Authorization、Project revision/digest、Solution/Component/Agent/Runtime/Workspace 与治理校验；创建不可变 Goal Artifact；把通用规划边界编译为仅含 `kernel_contract_version + planning_constraints + definition_digest` 的 OrchestrationDefinition；实例化并用 Plan Compiler 校验 BaselinePlanTemplate 的 Schema、依赖、分支闭合与终态可达性，记录 `fixed-baseline` provenance；创建 Workflow 时固定执行相关的 Project/Team/LockedComponentSet/Provider/MCP 快照和初始 `activePlanId`，明确排除 `Project.solution_setup.external_targets[]`。创建路径使用 `WorkflowInput.kind=plan`，不产生 hidden DAG Orchestrator/Reviewer Run、Workspace 或 Job；这些资源只能在后续 Ready WorkUnit 派发时出现。
- 不通过条件：要求用户先填写正式交付范围或验收方案；把用户标题当作 `metadata.name`；Workflow 创建后 `activePlanId` 为空并等待 Kernel“发现无 Plan”；为固定模板运行一次伪造 DAG Orchestrator/Plan Reviewer；OrchestrationDefinition 携带 BaselinePlanTemplate、Solution、Agent Instructions、Harness Guide 或额外 identity；静态编译失败仍留下可运行的部分 Workflow；过期 Project revision 仍创建成功。
- 所需证据：New Delivery 表单与只读摘要截图、CreateWorkflow 请求/响应、Goal Artifact、Workflow spec/status、OrchestrationDefinition 内容/digest、fixed-baseline Plan provenance、`activePlanId`、基线 WorkUnit 图、创建瞬间 AgentRun/Workspace/Job 零新增证明，以及过期 revision 的结构化 Conflict。

#### `UJ-WORKFLOW-002`：显式 DAG Orchestration WorkUnit 消费批准输出并扩展同一 DAG

- 前置条件：使用 `UJ-WORKFLOW-001` 创建的 Workflow；Requirement、Design、Acceptance Plan 和 Project Rules 已按各自 Gate 成功，Delivery DAG Orchestration WorkUnit 仍为 Pending；TeamBindingSnapshot 中至少有一个可解析为 `orchestration.dag` 的 Executor；另准备“活动 DAG 已无可推进动作，但仍有已选择节点未成功且不存在显式 Remediation 路径”的错误 Solution Fixture。
- 用户操作：
  1. 在 Workflow DAG 中观察 Project Rules 通过后 Delivery DAG Orchestration 节点自动进入 Ready；
  2. 查看该节点只列出它声明需要的 Requirement、Design、Acceptance Plan、Project Rules 和 Repository Observation；
  3. 查看 Kernel 匹配的 `executor_key` 和不可变 InvocationRequest，使用 `oacok work show/guide/read` 查看精确输入，再观察 DAG Orchestrator 生成 PlanDraft、程序编译、独立 Plan Review、返工或 Gate；
  4. Gate Pass 后刷新同一 DAG，查看实现、Integration、Release、Preview 和 Acceptance 节点追加到原图；
  5. 运行错误 Solution Fixture，观察图静止后的诊断。
- 用户可见预期：DAG Orchestration 是 DAG 中可见且可追踪的普通 WorkUnit，显示依赖、精确输入、DAG Orchestrator/Reviewer Run、InvocationRequest、Diagnostic、Review、Gate 和返工时间线；Plan 激活后用户仍看到一张连续扩展的 DAG。错误 Fixture 进入 Waiting/Failed 并明确提示“活动 DAG 未收敛且缺少显式 Remediation 路径”，不自动生成空计划，也不等待第二份完成合同。
- 平台处理预期：节点 Ready 时，Kernel Runtime 只在依赖闭包中解析 `input_bindings[]` 指向的已确认输出，按责任/`orchestration.dag`/合同匹配一个逻辑 Executor，并组装不可变 InvocationRequest；零个、多个、未确认、类型不匹配或 Executor 歧义均阻塞。PlanningRules 固定 DAG Orchestration WorkUnit、当前 `base_plan_id` 和 OrchestrationDefinition digest；PlanDraft 必须回传当前 `base_plan_id`。编译、Required Reviewer、必要 Decision 与 Planning Gate 通过后创建 `parent_plan_id` 指向旧 Plan 的新完整图快照，以 CAS 更新 `activePlanId`。
- 不通过条件：把全部上游 Artifact/对话无差别注入 DAG Orchestrator；Host/Platform Core 复制 Kernel input 解析或 InvocationRequest 组装算法；Project Rules 未通过时节点抢跑；`activePlanId` 或 Plan 耗尽触发隐藏 DAG authoring；Kernel 全局选择产品级规划 Guide 或 Agent 指令；PlanDraft 修改 Running/终态节点；Reviewer/Decision 未满足仍激活；CAS 失败覆盖已有 Plan。
- 所需证据：Plan A/Plan B 与 parent 关系、DAG Orchestration WorkUnit definition/status、typed input binding、HostCapabilitiesSnapshot、Executor 匹配结果、InvocationRequest、`oacok` 输出、OrchestrationContext、PlanDraft/Diagnostic/ValidatedPlan/PlanReviewResult/GateResult、CAS/Audit/Outbox、DAG 激活前后截图，以及错误 Fixture Condition。

#### `UJ-KERNEL-001`：从已有设计文档显式生成并推进 DAG

- 前置条件：准备一份已有且不可变的设计文档与 content digest；独立 Orchestrator Kernel Host 已注册一个具备 `orchestration.dag` 的 Executor 和 AgentWorkChannel；另准备文档缺失、digest 不匹配和 Executor 缺失 Fixture。
- 用户操作：
  1. 以 `WorkflowInput.kind=document` 提交文档 `resource_key`、中文 `display.name`/`display.description`、media type、content digest 和可选 objective；
  2. 查看 Kernel 先形成可见的单节点 `document-bootstrap` Plan，而不是直接在图外调用 Planner；
  3. Agent 运行 `oacok work show` 查看编排目标与文档清单，再运行 `oacok work read <resource-key>` 读取设计正文；
  4. Agent 使用 `oacok work submit` 提交 PlanDraft 与结构化 conclusion；
  5. 观察 Plan Compiler、必要独立 Review、Planning Gate 和后续 DAG 推进；
  6. 分别执行错误 Fixture。
- 用户可见预期：已有设计文档可以作为独立 Kernel 的正式无图输入；`document-bootstrap`、DAG Orchestrator Run、Review、Gate 和生成后的 DAG 全部可见。错误 Fixture 给出资源缺失、digest 不匹配或缺少 `orchestration.dag` Executor 的明确阻塞，不留下部分 Active Plan。
- 平台处理预期：`document` 与 `goal` 共用普通 bootstrap Plan/Plan Compiler/Lifecycle 路径，只替换 Inferential Guide 输入和 provenance；每份 InputResource 只能通过当前 AgentWorkChannel 精确读取。OAC 活动 Workflow 内部的设计文档扩图仍通过显式 WorkUnit typed input bindings，不切换根 WorkflowInput。
- 不通过条件：只支持 `plan|goal`；Kernel 隐式扫描本地目录或 Artifact 库找文档；在 Active Plan 缺失时自动猜测 document 模式；文档未校验 digest；DAG 在 Plan Compiler/Review/Gate 前直接激活。
- 所需证据：WorkflowInput、InputResource descriptors、input digest、`document-bootstrap` Plan/provenance、InvocationRequest、`oacok work show/read/submit` 记录、PlanDraft/Diagnostic/Review/Gate/Plan，以及三个错误 Fixture 的结构化结果。

#### `UJ-RUN-001`：实时执行 AgentRun，通过 `oacok` 协作并保持多 Agent 任务连续性

- 前置条件：一个 `execution.kind=executor` 的 WorkUnit 已 Ready；准备 Producer、独立 Reviewer、Review Reject 后返工和显式换 Agent Fixture；Platform Core 可以建立 run-scoped AgentWorkChannel 与 AgentRun event/completion 入口；当前用户有查看脱敏 Run 详情权限。准备 Codex App Server、Hermes ACP、Claude Code-compatible stream-json、OpenClaw ACP 四个 RuntimeDriver Fixture，以及只能使用 Repository 外临时文件物化指令的 Driver Fixture；准备 message、thinking summary、tool call/result、Shell stdout/stderr/exit、file diff、显式 memory read/write/search/delete、MCP、usage、error 和 terminal 原生事件，并准备一个不支持 memory 事件的 Runtime。另准备事件上传响应丢失、重复事件、sequence 缺口/乱序、Web 实时连接中断、AgentRun Job 进程异常和独立非 OAC Runtime SDK 调用方 Fixture，并在测试 Repository 中放置内容可识别的既有 `AGENTS.md` 与 `CLAUDE.md`。
- 用户操作：
  1. 打开 WorkUnit/AgentRun 详情，查看“执行请求”中的 `invocation_id`、`request_digest`、目标、责任、精确输入资源、Contract、Guide、前序结论、反馈、允许动作和 Submission Contract；
  2. 查看 AgentRun“运行时指令”摘要，确认列出 `agent_instructions_digest`（Agent 稳定角色指令摘要）、`collaboration_bootstrap_digest`（协作启动指令摘要）、条件性的 `workspace_instructions_digest`（当前工作空间规则摘要）及 `instruction_set_digest`（指令集合摘要），并且只有 `AgentRunEvent.started` 逐项回显相同摘要后才显示“Runtime 已接受”；页面不显示 Channel/执行凭证、Credential、Secret 或厂商/临时文件路径；
  3. 分别运行四个协议 Fixture：Codex 使用 `codex app-server` 默认 stdio，并完成 `initialize → initialized → thread/start|thread/resume → turn/start`；Hermes 使用 `hermes acp` 的 ACP JSON-RPC stdio；Claude Code-compatible 使用 `claude -p --input-format stream-json --output-format stream-json --verbose` 的 JSON/NDJSON；OpenClaw 使用 `openclaw acp` 的 Gateway-backed ACP stdio。每个 Driver 必须固定精确 Runtime/CLI release 和协议 Fixture；原生缺失或近似的事件/Usage 字段保持 unknown，不得伪造；
  4. 在 AgentRun 详情实时观察 message、thinking summary、tool call/result、command stdout/stderr/exit、file change/diff、memory read/write/search/delete、MCP、usage、status/error/terminal；memory 只显示明确 operation、scope、outcome 和授权后脱敏摘要，不显示隐藏 Session 内容，不支持该能力的 Runtime 显示 unavailable。中断 WebSocket/SSE 后产生更多事件，再按同一 `stream_id + after_sequence` 恢复，确认先前和新增事件都来自已持久化 AgentRunEvent；Pod 日志不参与补齐；
  5. 检查一次 AgentRun 只有一个 `stream_id`，第一个事件必须是 `started@sequence=1`，Platform Core 以 CAS 将 stream 绑定到当前 Run；随后 sequence 严格递增。重放同一 event_id/stream_id/sequence 和相同 digest 返回原确认；不同 stream、同身份不同 digest、缺口和无法恢复的乱序显式 Conflict/拒绝或等待，不静默改号。检查 AgentRun Job 使用 `restartPolicy=Never + backoffLimit=0`；模拟进程异常后必须创建新 AgentRun/new stream，不能在原 sequence 上继续；厂商 Session resume 仍可作为独立连续性事实存在；
  6. 检查 Runtime SDK 调用追踪：AgentRun Job 只调用一次 `RuntimeDriver.Execute(ctx, RuntimeRequest)`，持续消费一个 RuntimeSession.Events，在事件流关闭后只读取一个 RuntimeResult；SDK Core 自动生成 `event_id`、`stream_id`、`sequence`、`schema_version` 并规范化时间/关联，Driver 与调用方不重复生成这些通用字段；
  7. 让独立非 OAC 调用方直接使用同一 Runtime SDK，确认不需要 AgentRun、Workflow、Platform endpoint/token、HTTP Client、ACK、数据库或 Kubernetes 类型；再检查 OAC AgentRun Job 在 SDK 外完成事件短批次、即时边界 flush、上传重试、ACK 和有界背压；
  8. 比较 AgentRun 前后的 Repository `AGENTS.md`、`CLAUDE.md` 完整 digest，并检查临时指令文件只出现在 Repository 外且在 Run 结束后删除；
  9. 查看 Agent 首条 bootstrap instruction，确认明确要求先运行 `oacok work show`，并说明 `guide/read/submit`；在 Agent 环境依次执行 `oacok work show`、`oacok guide metadata` 和 `oacok work read <resource-key>`；
  10. Producer 使用匹配当前 request digest 的 `oacok work submit` 提交 conclusion、outputs、observations 和 outcome；再使用旧 digest、错误 iteration、缺失 conclusion 和合同外输出分别提交。确认 RuntimeResult 即使为 succeeded 也不能代替这次 WorkSubmission；
  11. 打开 Reviewer Run，确认其 InvocationRequest 固定被审 subject、Review Contract、Producer 输出和 run conclusion，但不包含作者隐藏 Session/Workspace；
  12. Reviewer 提交 Reject 与完整 verdict、summary、findings、nits、evidence，进入返工新 Run；确认返工 Agent 能读取完整 ReviewResult，而不是只看到 rejected 状态；
  13. 显式换一个 AgentDefinition 接手同一 Producer Lineage，确认新 Session 仍能通过结构化结论和反馈连续工作。
- 用户可见预期：页面和 `oacok work show` 展示同一个创建时请求；`oacok guide` 能解释协作流程和英文字段中文含义；四种 Runtime 使用不同原生协议，但 Agent 获得相同语义指令和协作入口。用户实时看到接近 Runtime 原生粒度的消息、思考摘要、工具、Shell、文件、MCP 与 Usage，并能在断线后无丢失恢复。Repository 指令文件不因 Agent 角色切换而变化；返工、复审、新 Run 和 Job 进程重执行具有清楚的 invocation/run/stream 边界。WorkUnit 时间线连续展示 Producer conclusion、Reviewer 评审、返工与最终结果；短期执行凭证和 Secret 不显示。Runtime 完成、业务提交被接受、WorkUnit 通过是三个不同结论。
- 平台处理预期：Kernel Runtime 完成 Executor 匹配、typed input 解析和 InvocationRequest 组装；Platform Core 把同一内容原样固定到 `AgentRun.request`，建立 run-scoped AgentWorkChannel，按精确 AgentDefinition、Kernel bootstrap 和 InvocationRequest 明确选择的 Project Rules 组成 RuntimeInstructionSet，并固定模型/MCP/Runtime 等非敏感启动输入。RuntimeDriver ExtensionPackage 的 `entrypoint` 解析为精确 AgentRun Job OCI image + command；镜像内薄 OAC Job 主程序与 Driver 链接在同一进程。Execution Host 只创建/观察/取消该 Job/PVC，挂载 Workspace、Skills、输入、临时凭证和启动数据。AgentRun Job 将其映射为 RuntimeRequest，调用 RuntimeDriver `Execute` 一次，循环消费 RuntimeSession.Events，并在 SDK 外调用 `AppendAgentRunEvents`；流关闭后读取唯一 RuntimeResult 并调用一次 `CompleteAgentRun`。Runtime SDK Core 统一生成通用事件信封，RuntimeDriver 只映射厂商事件草稿和原生关联；Platform Core 保留 SDK event_id/stream_id/sequence，增加服务端推导的 agent_run_id、received_at 与 critical 语义，先持久化 AgentRunEvent/Usage/Artifact/Observation，再发布游标通知并更新当前 AgentRun report。Driver 只在完整指令集合已接受后发送 started 事件，Platform Core 逐项校验摘要。公共 Runtime SDK 不导入 OAC ProjectRules/AgentRun 类型，也不包含 Platform API Client。Submission Validator 只在独立 AgentWorkChannel 上校验 `invocation_id + request_digest + iteration + submission_contract`；RuntimeResult 不绕过 WorkSubmission、Contract、Sensor、Gate 或 Decision。后续 Sensor/Gate 由新 Workflow Watch 驱动。
- 不通过条件：保留独立 Runtime Bridge/Runner 服务或要求 RuntimeDriver 配置 Service URL/Replica/HA；AgentRun Job 循环多次调用 Execute；SDK 内置 Platform API Client、上传、ACK、重试、持久化或 OAC 领域类型；Driver 与调用方各自生成 event_id/sequence；AgentRunEvent 嵌套第二份 runtime_event 或重写 SDK stream/sequence；同一 AgentRun 混入两个 Job 进程或多个 stream；Kubernetes 自动重启在原 Run 上续写；从 Pod 日志重建产品事件；WebSocket/SSE 成为历史权威；RuntimeResult 直接形成 WorkSubmission 或 WorkUnit 成功；把 `oacok` 当整个模块名称；`work show` 重新读取最新 Workflow；把全部上游历史或隐藏 Prompt 注入；Runtime SDK 暴露厂商指令文件路径；RuntimeDriver 创建或覆盖 Repository `AGENTS.md`/`CLAUDE.md`；临时文件进入 Git 树、跨 Run 复用或未清理；未选择 Project Rules 却扫描 Repository 注入；started 摘要缺失/错误仍被接受；Secret 出现在请求/事件；Reviewer 获得作者隐藏对话；返工 Agent 只看到状态而看不到完整 findings；换 Agent 后必须依赖旧 Session 才能继续。
- 所需证据：AgentRun.request、RuntimeInstructionSet 四字段及 digest、RuntimeDriver ExtensionPackage `entrypoint` 到 exact OCI image/command/digest 的解析结果、镜像内 Job main 与 Driver 同进程证明、RuntimeRequest 脱敏样本、RuntimeDriver.Execute 单次调用追踪、RuntimeSession Events/Result 关闭顺序、SDK Core 事件信封、四种原生协议收发 Fixture、memory 支持/不支持 Fixture 与脱敏结果、AgentRunEvent 与 SDK event_id/stream_id/sequence 对照、重复/冲突/缺口/乱序响应、短批次/即时 flush/ACK/背压记录、`AppendAgentRunEvents`/`CompleteAgentRun` 请求响应、critical tail 持久化确认、Web 实时展示与 `after_sequence` 断线恢复、Job `restartPolicy/backoffLimit` 清单、进程异常后的新 AgentRun/new stream、独立非 OAC SDK 调用样例与依赖扫描、Provider Usage 映射、逐项匹配指令摘要的 started 事件、Repository 文件前后 digest、临时目录创建/清理、页面与 `oacok work show` 相同 request digest、AgentWorkChannel show/guide/read/submit、WorkSubmission 成功/失败及 RuntimeResult 不可替代证明、run-conclusion/ReviewResult/Artifact/Run report、返工前后 InvocationRequest diff、Reviewer 与 Producer 独立 Lineage/Session/Workspace、Agent handoff 记录，以及 Gate 前后状态轨迹。

#### `UJ-REQUIREMENT-001`：澄清并原子批准 Requirement Baseline

- 前置条件：使用 `UJ-WORKFLOW-001` 创建的软件交付 Workflow；Requirement Baseline WorkUnit 已 Ready；内置 Harness 默认没有 Required Reviewer，并另准备一个企业定制 Reviewer Fixture、一个只需修改 Acceptance Criteria 的返工 Fixture，以及过期 Artifact/WorkUnit revision Fixture。
- 用户操作：
  1. Product/Requirement 责任 Agent 产生一个阻塞性 Question Observation，用户在需求基线页面查看问题、原因、关联 Goal 与当前假设并提交回答；
  2. 观察该责任 Agent 在同一 Lineage、同一 business iteration 中继续，而不是进入返工轮次；
  3. 该责任 Agent 正式提交 Requirement Artifact 与 AcceptanceCriteria Artifact，程序检查通过后打开需求基线评审；
  4. 查看原始 Goal、两份当前 Artifact、上一份 Diff、问题回答、假设、风险和检查结果，确认默认页面没有 Reviewer 区域和通用 Reject；
  5. 点击一次“批准需求基线”，观察两份成果同时批准且 Solution Design 进入 Ready；
  6. 在返工 Fixture 中只选择 Acceptance Criteria 并提交“要求修改”，让 Product/Requirement 责任 Agent 保留 Requirement Artifact、正式提交新的 AcceptanceCriteria Artifact，再次完成程序检查和一次批准；
  7. 使用过期 Artifact digest 或 `expected_work_unit_revision` 提交批准；
  8. 运行企业定制 Reviewer Fixture，确认只有 Harness 显式声明 Required `responsibility-run` 时才先进入独立 Review。
- 用户可见预期：一个页面、一项批准主操作，不要求分别批准两次，也不显示 RequirementBaseline Version/Decision Bundle。可用操作仅为“批准需求基线”“要求修改”“回答问题”“放弃 Delivery”；程序检查失败时页面不显示可批准状态。批准成功后两份成果同时显示已批准；冲突时保留用户输入并要求刷新当前内容。
- 平台处理预期：Question 使用不可变 Observation，Answer 使用绑定其精确 ID/digest 的单主体 Decision；Answer 后创建同一 Product/Requirement Lineage/iteration 的后续 Run。内置 Harness 的程序检查通过且用户 Decision 尚未满足时，WorkUnit 必须进入 `Waiting`，不能因“无 Reviewer”直接 Succeeded。`ApproveRequirementBaseline` facade 调用 `SubmitDecisionsAtomically`，在一个事务中创建分别绑定当前 Requirement 和 AcceptanceCriteria ID/digest 的两条 Approve Decision、Audit、Command 与单一 Outbox；Workflow 一次性观察完整 Decision ID 集合，Gate Pass 后 WorkUnit Succeeded，Solution Design Ready。Request Changes 不产生部分批准；变化成果创建新 Artifact ID/digest，未变化成果可以复用，当前组合重新批准。
- 不通过条件：把问题保存成正式 Requirement 版本；Answer 增加返工 iteration；程序检查失败仍允许批准；内置默认强制 Reviewer；一个 Decision 同时保存两个 subjects；产生 DecisionBundle/RequirementBaselineVersion；两条批准部分成功；只修改 Criteria 后强制复制 Requirement；旧 Decision 自动批准新 Artifact；页面显示通用 Reject；用户命令直接改写 WorkUnit 状态；过期 revision 仍成功。
- 所需证据：Question Observation、Answer Decision、Product/Requirement Run/Lineage/iteration 对照、两份 Artifact 及 Diff、程序检查 Observation、Requirement Baseline 页面截图、原子批准请求与两条 Decision、数据库事务/Audit/Outbox、Workflow Decision ID 更新、GateResult、Solution Design Ready 状态、返工前后 Artifact identity，以及默认/企业 Harness 的 Reviewer 分支对照。

#### `UJ-DESIGN-001`：逐份评审并批准 Architecture Design 阶段成果

- 前置条件：`UJ-REQUIREMENT-001` 已通过；fixed-baseline Plan 中存在按顺序依赖的 Solution Design、Technical Overview、Detailed Design 三个 WorkUnit；三个 Harness 均包含程序检查、Required Independent Reviewer 和用户 Decision Requirement；另准备程序检查失败、Reviewer Reject、Pass-With-Nits、用户 Request Changes 和过期 revision Fixture。
- 用户操作：
  1. 打开 Architecture Design 工作区，确认页面把三个固定 WorkUnit 显示为一个阶段列表，当前只有 Solution Design 可执行；
  2. 让 Architect 正式提交 SolutionDesign Artifact；先运行程序检查失败 Fixture，确认页面展示 blocker 且没有批准操作；
  3. 修正并重新提交后运行 Reviewer Reject Fixture，查看 ReviewResult 和自动返工轨迹，不提交用户 Decision；
  4. Architect 正式提交新的 Artifact，程序检查和 Reviewer Pass 后，查看当前内容、上一份 Diff、已批准上游输入、检查结果、ReviewResult、假设、风险和取舍；
  5. 点击“要求修改”，填写章节定位和原因，观察 Architect 沿原 Lineage 返工、新 Artifact 重新经过程序检查和独立 Review；
  6. 使用当前精确 Artifact ID/digest 点击“批准当前设计”，观察 Solution Design Succeeded、Technical Overview Ready；
  7. 对 Technical Overview 执行 Pass-With-Nits Fixture，确认只允许不改变语义的有限修正并重跑程序检查；随后批准 Technical Overview，再完成并批准 Detailed Design；
  8. 在任一设计页使用过期 `subject_digest` 或 `expected_work_unit_revision` 提交批准；
  9. Detailed Design Gate Pass 后，确认 Acceptance Planning Ready。
- 用户可见预期：Architecture Design 是一个统一工作区和阶段分组，内部明确展示三个顺序设计项及各自状态；每次只批准当前一份完整 Artifact，不出现 ArchitectureDesign Artifact/Version、章节级部分批准、一次性批准全部设计或动态 DetailedDesign DAG。可用正式操作为“批准当前设计”“要求修改”和条件性的“回答问题”；历史查看只读，Workflow 全局仍可 Abandon。Reviewer 未 Pass 或程序检查失败时不显示批准操作，Reviewer Reject 自动返工，页面不显示含义重叠的通用 Reject。
- 平台处理预期：BaselinePlanTemplate 固定三个普通 PlanNodeTemplate 和顺序 edges；每个实例 WorkUnit 分别拥有 Contract、Harness、Artifact、作者 Lineage、Reviewer Lineage、ReviewResult、GateResult 和单主体 Decision。DetailedDesign Artifact 的 `content_digest` 覆盖全部章节、图和附件，不展开基线子 DAG。`SubmitDecision` 校验 subject ID/digest、授权、当前 Decision Requirement、`expected_work_unit_revision` 和幂等键，再通过 Audit/Outbox 让 Workflow 观察 Decision；Kernel 根据程序检查、ReviewResult 和 Decision 推进状态，页面或 API 不直接改写 WorkUnit。`request_changes` 进入 `InRework`；新 Artifact 不继承旧 ReviewResult/Decision；`pass-with-nits` 只按 Harness 有限返工策略执行，实质变化重新完整 Review。
- 不通过条件：把 Architecture Design 保存成额外 WorkUnit、Artifact 或状态机；在基线阶段启动 DAG Orchestration WorkUnit 生成 DetailedDesign DAG；三份成果共用一个状态或模糊 Decision；Reviewer Reject 后仍让用户批准；程序检查失败仍开放批准；按章节部分批准；旧 Decision 自动应用到新 Artifact；Pass-With-Nits 修改 Contract、安全、数据或范围且不重新 Review；用户命令直接写 Succeeded；过期 digest/revision 仍成功；Detailed Design 通过前 Acceptance Planning 抢跑。
- 所需证据：fixed-baseline Plan 的三个节点和 edges、Architecture Design 工作区截图、三个 WorkUnit definition/status、Artifact 内容包与 digest、程序检查 Observation、Architect/Reviewer Lineage 与独立 Session/Workspace、ReviewResult、Nits 返工轨迹、Decision 请求/响应、Audit/Outbox、GateResult、冲突响应、每项批准后的下游 Ready 状态，以及不存在 ArchitectureDesign 聚合和 DetailedDesign 基线 DAG 的 Schema/Plan 检索。

#### `UJ-ACCEPTANCE-PLAN-001`：评审并批准用户级端到端验收方案

- 前置条件：Requirement、Acceptance Criteria、Solution Design、Technical Overview 和 Detailed Design 均已批准；Acceptance Planning WorkUnit 已 Ready；准备 Reviewer Reject、用户 Request Changes、过期 digest，以及“把单元测试写入 Acceptance Plan”的错误 Fixture。
- 用户操作：
  1. 打开 Acceptance Planning 工作区，查看已批准 Requirement/Criteria/Design 输入；
  2. 让 Acceptance Planning 责任 Agent 提交 AcceptancePlan Artifact，内容覆盖用户旅程、业务前置条件、环境/数据、自动与人工 E2E 步骤、Acceptance Bundle、suite digest、证据和失败出口；
  3. 运行程序完整性检查和独立 Review；先执行 Reviewer Reject，再完成返工；
  4. Reviewer Pass 后查看当前只读 Artifact、上一份 Diff、Criteria 追踪、ReviewResult 和可执行验收步骤，确认页面没有直接编辑正式正文的入口；
  5. 点击“要求修改”，填写章节定位和修改原因，观察同一 Acceptance Planning Lineage 返工、新 Artifact 重新检查和 Review；
  6. 使用当前精确 ID/digest 点击“批准验收方案”，观察 Project Rules 进入 Ready；
  7. 提交包含单元测试、组件内部测试或实现级集成测试作为主要内容的错误 Fixture；
  8. 使用过期 digest/revision 提交批准。
- 用户可见预期：页面清楚说明该 Artifact 是“从用户角度验证业务功能的端到端验收方案”，不要求用户评审开发单元测试清单。正式正文只读，当前成果操作仅为“批准验收方案”和“要求修改”；Reviewer Reject 时不开放批准。Reviewer Pass 与用户批准分开；返工后显示新 Artifact 和 Diff；过期内容不能被批准。
- 平台处理预期：AcceptancePlan Contract 只允许业务 E2E 场景、目标环境/数据、可观察预期、Evidence、suite digest、人工步骤和失败出口。单元/组件/实现内部集成测试由后续 Developer WorkUnit 的 TDD/Computational Sensor 产生，不进入当前用户审批 Gate。Web/API 只提交绑定当前 Artifact ID/digest 的 `approve` 或 `request_changes` Decision，不存在直接更新正式内容的 Use Case。Reviewer/用户打回创建新 Artifact ID/digest，旧 ReviewResult/Decision 不继承。
- 不通过条件：把 Acceptance Plan 设计成单元/组件测试计划；页面允许用户直接编辑并覆盖正式 Artifact；要求修改不经过责任 Agent、程序检查和独立 Review；允许 Developer 在执行时修改已批准 Criteria、suite digest 或判定标准；Reviewer Pass 自动代替用户批准；Request Changes 原地覆盖 Artifact；旧 Decision 批准新内容；Detailed Design 未通过就执行。
- 所需证据：Acceptance Planning InvocationRequest、AcceptancePlan Artifact/Contract、只读页面和操作集合截图、Criteria 追踪、业务 E2E suite、程序检查与错误 Fixture Diagnostic、Reviewer 独立 Run、用户 Decision、返工前后 Artifact/digest、不存在直接内容更新 API 的接口检索、Project Rules Ready 状态，以及后续 Developer TDD 证据与本 Artifact 的边界对照。

#### `UJ-PROJECT-RULES-001`：评审、一次批准并自动安全应用 Project Rules

- 前置条件：AcceptancePlan 已通过独立 Review 和用户批准，Project Rules WorkUnit 已 Ready；准备 `AGENTS.md` 不存在、文件存在但无受管区块、已有一个合法受管区块、标记重复/倒序/嵌套、base commit 变化、Credential/写入失败和外部提交结果未知等 Fixture。
- 用户操作：
  1. 打开 Project Rules 工作区，查看已批准来源、当前只读 ProjectRules Artifact、上一份 Diff、程序检查和独立 ReviewResult；
  2. 查看仓库根 `AGENTS.md` 当前内容、拟应用受管区块 Diff、`base_repository_commit_sha`（生成规则时的仓库基线提交）、`managed_block_id`（受管区块身份）和预期区块 digest；
  3. 先点击“要求修改”，填写定位与原因，观察责任 Agent 返工、新 Artifact 重新检查和 Review；
  4. 对当前精确 Artifact 点击一次“批准项目规则”，确认页面不再要求第二次“应用到 AGENTS.md”；
  5. 分别运行文件不存在、无区块和合法区块三种正常 Fixture，观察自动应用、commit、Sensor 校验和 Delivery DAG Orchestration WorkUnit 进入 Ready；
  6. 分别运行非法标记、base commit 变化、Credential/写入失败 Fixture，确认仓库没有部分修改且 Gate 不通过；
  7. 模拟提交返回结果未知，确认系统先 Observe 而不是盲目重试；随后确认已提交或未提交的外部事实并收敛状态。
- 用户可见预期：正式规则正文只读，正式操作只有“批准项目规则”“要求修改”和“查看历史”；批准一次后直接显示自动应用进度。成功必须显示实际 commit SHA、完整文件 digest 和受管区块 digest；冲突必须显示原因、未写入保证及重新形成 Artifact 的下一步；当前方案讨论中的临时协作规则不会作为 Project Rules 写入仓库。
- 平台处理预期：`approve` Decision 绑定当前 ProjectRules Artifact ID/digest，同时构成对已展示受管区块 Diff 的唯一用户授权。WorkflowReconciler 观察 Decision 后由 Kernel 推进已固定的 Git Executor 执行路径；Executor 对三种正常文件形态确定性创建、追加或替换受管区块，保留区块外内容并创建独立 commit。Git Sensor 确认实际 commit、完整文件 digest 和区块 digest后，Project Rules Gate 才能 Pass。base 冲突要求基于新仓库事实提交新 Artifact 并重新审批；未改变精确输入的瞬时故障恢复不要求重复批准。
- 不通过条件：批准 HTTP 成功即显示已应用；出现第二个“应用”按钮或第二条批准 Decision；允许用户直接编辑正式 Artifact 或覆盖完整 `AGENTS.md`；区块外内容变化；非法标记或 base 漂移仍写入；失败留下部分文件/commit；未知结果盲目重试；Git Sensor 未确认就放行 Delivery DAG Orchestration；把当前设计讨论的临时沟通规则写入项目规则。
- 所需证据：只读 Review 页面与三项操作截图、ProjectRules Artifact/ReviewResult/Decision、三种正常文件输入的前后 Diff、Git Executor report、实际 commit、完整文件和区块 digest、Git Sensor Observation、Project Rules GateResult、下游 Ready 状态、所有错误 Fixture 的未写入证明、Unknown→Observe 收敛轨迹，以及接口检索证明不存在直接内容更新或第二次应用命令。

#### `UJ-IMPLEMENTATION-PLAN-001`：仅在 Decision Boundary 审批 Implementation Plan

- 前置条件：Project Rules Gate 已通过，显式 Delivery DAG Orchestration WorkUnit 已 Ready；准备普通范围内计划、命中重大成本/外部副作用/风险例外边界的计划、Reviewer Reject、用户 Request Changes、过期 ValidatedPlan/Base Plan，以及试图修改已批准业务基线等 Fixture。
- 用户操作：
  1. 观察 DAG Orchestrator 通过 `oacok` 获取工作协议与精确输入，提交 PlanDraft，并经过 Plan Compiler 和独立 Plan Review；
  2. 对普通范围内计划不执行任何用户批准，观察 Planning Gate 自动激活新 Plan，并在同一 DAG 中出现新增 WorkUnit；
  3. 运行 Decision Boundary Fixture，在 Attention 队列打开 Implementation Plan Decision，查看 Base Plan、ValidatedPlan ID/digest、PlanningRules digest、计划 Diff、Compiler/Review、命中原因、影响、风险、成本和外部副作用；
  4. 点击“要求修改”，填写原因，观察 DAG Orchestrator 获得完整反馈后提交新的 PlanDraft，并重新编译和独立 Review；
  5. 对新的精确 ValidatedPlan 点击“批准候选计划”，观察 Planning Gate 重新计算、不可变 Plan 形成和 `activePlanId` CAS 更新；
  6. 使用旧 ValidatedPlan digest、旧 Base Plan 或旧 WorkUnit revision 提交 Decision，确认返回 Conflict 且 reason 不丢失；
  7. 提交试图改写 Requirement、Design、Acceptance Criteria、Acceptance Plan 或 Project Rules 的计划，确认 Compiler/Gate 拒绝该候选；只有 Active Plan 已预先声明对应基线修订与重新审批分支时才能沿该分支继续，否则明确报告 Plan/Solution 设计缺口。
- 用户可见预期：普通计划不打断用户，也不制造待审批记录；命中边界时页面说明“为什么必须由你决定”，并只展示批准、要求修改和查看历史。DAG 只读，新增/调整/Obsolete 节点清晰可见；Decision 被接受与 Plan 真正激活分开显示；未激活候选不会伪装成当前执行图。
- 平台处理预期：PlanningRules Compiler 产生机器可读 `decision_boundaries[]`，Plan Compiler 形成 ValidatedPlan，独立 Reviewer 产生 PlanReviewResult，Planning Gate 根据精确输入决定直接 Pass 或 `AwaitDecision`。Platform Core 只在 AwaitDecision 时暴露 Attention，并保存绑定当前 ValidatedPlan ID/digest 的单主体 Decision；WorkflowReconciler 再调用 Kernel，Gate Pass 后才产生 `ActivatePlan`。Platform Core 事务保存 Plan/Audit/Command/Outbox，再以 expected revision/resourceVersion CAS 更新 `activePlanId`；冲突候选保留为未激活历史记录。
- 不通过条件：所有计划都要求用户批准；没有命中边界却创建 Waiting/Decision；Reviewer 代替用户批准；用户可直接编辑并保存 DAG；批准 HTTP 成功即显示已激活；旧 Decision 应用于新 ValidatedPlan；CAS 冲突覆盖当前 Plan；Implementation Plan 越权改写已批准业务基线；Request Changes 只传状态、不传完整用户意见和 ReviewResult。
- 所需证据：普通与边界两组 PlanningRules、PlanDraft、Diagnostic、ValidatedPlan、PlanReviewResult、GateResult、Attention/Decision 页面截图、Decision/Audit/Outbox、返工 InvocationRequest feedback、Plan/parent 关系、`activePlanId` 更新、Conflict 响应、未激活候选历史，以及上游路由 Fixture。

#### `UJ-WORKFLOW-OBSERVABILITY-001`：联动查看 DAG、全局时间线和连续任务线

- 前置条件：准备两个当前 Principal 有权访问的 Project/Workflow，其中一个已从固定基线扩展出动态 WorkUnit，至少包含并行依赖、Waiting、VerificationFailed、ReviewRejected、返工、Agent handoff、Obsolete 节点、用户 Decision、子 Workflow、AgentRun/ComponentRun 和断线恢复等 Fixture；另准备一个无权 Project 的待处理事项。
- 用户操作：
  1. 打开 Workflow Operations Center Overview，查看当前 `activePlanId` 对应的完整只读 DAG；
  2. 缩放、平移、筛选并通过键盘选择节点，再使用 WorkUnit 列表替代视图完成相同定位；
  3. 查看节点责任、直接状态、硬依赖、关键路径、相对父 Plan 新增节点和 Obsolete 节点，确认不能拖拽修改 DAG；
  4. 在 Workflow 全局时间线按 WorkUnit、责任、事件类型、状态和时间过滤，点击 Reviewer Reject、Decision、Plan 激活和外部结果等事件，观察对应 DAG 节点被高亮；
  5. 从 DAG 节点进入 Evidence Workspace，查看 Artifact/Diff、Observation、GateResult、Harness 和 Run 摘要；
  6. 打开 WorkUnit 连续任务线，按 iteration、Lineage 和 Run 查看 Producer conclusion、程序验证、Reviewer 完整 ReviewResult、用户 Decision、返工与 Agent handoff；
  7. 下钻一个 AgentRun 的固定 InvocationRequest 和事件序列，再返回并确认原 DAG 节点、筛选和时间线位置保留；
  8. 断开并恢复 SSE/WebSocket，确认页面从上次事件游标重新查询；使用无正文权限账号确认敏感内容被裁剪但事件关系仍可理解。
  9. 打开统一 `/attention`，确认能够跨两个有权 Project 筛选 human Run、Decision、MissingExecutor、Unknown/Failed 和长时间 Waiting；切换到“我的工作”只保留 human Run，且无权 Project 不被枚举。
- 用户可见预期：DAG 回答“有哪些节点以及如何依赖”，全局时间线回答“整个 Workflow 发生了什么”，连续任务线回答“当前节点由谁接力以及为什么返工”。三种视图双向定位、状态一致；VerificationFailed 和 ReviewRejected 不被折叠；Agent handoff 后任务线不断裂；实时连接中断不显示为 Workflow 失败。
- 平台处理预期：Platform Core Query 从 Workflow.status、Active Plan、不可变 Domain Event/Plan/Artifact/Observation/GateResult/Decision、Run 摘要和 AgentRunEvent 组装可重建读模型，保留 Workflow/WorkUnit/Run/Subject identity、digest 和事件游标。`QueryAttentionItems` 从同一组现有事实瞬时组装跨 Project Attention，并在服务端执行授权、脱敏和游标分页；它不持久化 Inbox/Notification/Attention 对象。Kernel 只提供 WorkUnit 连续性与生命周期语义；Web 负责展示和本地交互状态；SSE/WebSocket 只发送游标通知。`oacok work show` 从同源事实中选择当前 Run 必需的前序结论与反馈，不读取完整用户时间线。
- 不通过条件：没有完整只读 DAG；只提供单节点依赖视图；允许拖拽保存 DAG；DAG、全局时间线与节点任务线状态不一致或不能互相定位；跨 Project Attention 由前端越权聚合或新增 Inbox/Notification/Attention 聚合、表、已读状态机；新增 Timeline/Issue/Comment/WorkPackage 聚合、表或 CRD；从 Pod 日志或浏览器事件推断业务历史；把整个 Workflow 时间线注入 Agent；隐藏 Reviewer findings 或 Agent handoff 后丢失前序结论；断线后从头重复或丢失关键事件。
- 所需证据：完整 DAG 与列表替代视图截图、Plan/parent/Obsolete 数据、三种状态分支、双向定位录屏或自动化测试、Evidence Workspace、连续任务线、AgentRun request/event、跨 Project Attention/“我的工作”筛选与越权结果、权限裁剪结果、事件游标与断线恢复记录、Query 来源对照，以及 Schema/数据库/CRD 检索证明不存在 Timeline、Inbox、Notification 或 Attention 聚合。

#### `UJ-DECISION-001`：提交合同约束的正式业务 Decision

- 前置条件：准备 Question Observation、普通 Approve、Request Changes、Reject、Abandon、风险接受/风险例外等 Decision Requirement；同时准备无权限 Principal、过期 subject digest、过期 WorkUnit revision、重复幂等请求、Schema 不匹配和评论文本 Fixture。
- 用户操作：
  1. 从 Attention 队列打开 Decision Inspector，查看精确 Subject、Workflow/WorkUnit/iteration、触发来源、Evidence、Review/Gate 摘要、可用动作和审计提示；
  2. 对普通 Approve 不填写 reason 并成功提交；
  3. 对 Question 填写满足 Question Schema 的结构化 Answer，不填写 reason，观察同一 WorkUnit、Lineage 和 business iteration 继续执行；
  4. 分别对 Request Changes、Reject 和 Abandon 留空 reason，确认页面和服务端阻止提交；补充 reason 后重新提交；
  5. 对风险接受/风险例外分别遗漏 structured_result 或 reason，确认失败；补全风险、影响范围、约束、授权选项和理由后提交；
  6. 观察 Request Changes 的完整 reason/结构化分类进入责任 Agent 下一轮 InvocationRequest feedback，而不是只出现状态值；
  7. 使用无权限、过期 digest/revision 和同幂等键不同内容 Fixture 提交，再以完全相同请求重复提交；
  8. 只发表评论或聊天，确认 Workflow 不推进；观察正式 Decision 被接受后仍等待 Reconcile/Gate 收敛。
- 用户可见预期：页面只展示当前合同允许的动作；普通批准不强迫填写理由；Answer 的结构化回答、返工/拒绝/放弃理由、风险结构化选择与理由均有明确中文说明和必填提示。提交成功显示“Decision 已接受”，不提前显示 WorkUnit/Gate 已通过；冲突时保留用户输入并要求刷新。
- 平台处理预期：Platform Core 根据 Decision Requirement、Question Schema 或风险合同统一验证字段，再校验 subject ID/digest、WorkUnit revision、ApproverRequirement、Authorization、当前 Gate 与幂等键；事务保存单主体不可变 Decision、AuditEvent、CommandRecord 和 Outbox。Workflow 更新触发 Reconcile，Kernel 才根据 Decision 推进。Answer 在同一 business iteration/Lineage 继续；Request Changes 的内容进入返工反馈；风险 Decision 不绕过治理链。一个手势需要多主体时只使用 `SubmitDecisionsAtomically` 创建多条普通 Decision。
- 不通过条件：前端与服务端必填规则不同；普通 Approve 强制无意义 reason；Request Changes/Reject/Abandon 允许空 reason；风险接受缺少 structured_result 或 reason；Answer 被算作返工 iteration；自由文本评论推进 Gate；Decision 可编辑、删除或覆盖；一个 Decision 存多个 subject；无权限或过期请求成功；重复请求创建多条记录；API 成功直接改写 WorkUnit 状态。
- 所需证据：六类 Decision 表单和服务端 Schema、Question/Answer 前后 WorkUnit/Lineage/iteration、Request Changes 返工 InvocationRequest、风险合同与治理检查、Decision/Audit/Command/Outbox、GateResult、授权拒绝、Conflict、幂等响应、评论不推进证明，以及数据库 Schema 检索证明不存在 DecisionBundle/可变 Decision。

#### `UJ-WORKFLOW-CONTROL-001`：软暂停、恢复、取消和业务放弃 Workflow

- 前置条件：准备包含活动 AgentRun、ComponentRun、Child Workflow、尚未派发下游节点和一个结果可能 Unknown 的外部副作用操作的 Running Workflow；另准备 Paused、Waiting 和终态 Workflow Fixture。
- 用户操作：
  1. 在 Workflow Toolbar 点击 Pause，观察命令接受、generation/observedGeneration 收敛和 Workflow 进入 Paused；
  2. 确认暂停后不再创建任何 AgentRun、ComponentRun、Review、返工 Run、Workspace、DAG Orchestration WorkUnit 或 Child Workflow；
  3. 让暂停前已启动的 Run 分别成功、失败和返回 Unknown，确认其报告、Observation 和时间线继续更新，但没有下游派发；
  4. 检查 Child Workflow 同样停止新派发，且页面不提供冻结 Pod/Agent 的伪功能；
  5. 点击 Resume，确认 Kernel 基于暂停期间保存的最新事实继续，不重放已完成 Run，并派发现在满足条件的后续工作；
  6. 再次运行并点击 Cancel，观察 Workflow/WorkUnit 进入 Cancelling、活动 Run 和 Child Workflow 被取消、Unknown 外部结果持续 Observe，最终进入 Cancelled；
  7. 在另一 Workflow 通过 Decision Inspector 提交带非空 reason 的 Abandon，确认进入 Abandoned，并执行必要取消与外部结果收敛；
  8. 在 Cancelled、Abandoned、Succeeded 和 Failed Workflow 上检查 Resume 不可用，并使用重复/过期命令 Fixture 验证幂等与 Conflict。
- 用户可见预期：Pause 明确说明“停止新派发，已启动操作继续安全收敛”；活动 Run 不会突然消失或被伪装为 Paused。Resume 从最新状态继续。Cancel 与 Abandon 均不可恢复，但页面分别解释“取消当前执行”和“业务目标不再追求”；所有命令均区分已接受与最终收敛。
- 平台处理预期：Pause/Resume/Cancel 由 Platform Core 类型化命令更新 `spec.desiredState`，Workflow Watch 触发 Controller/Kernel；Kernel 在 Paused 时消费报告但抑制所有新副作用 LifecycleAction。Resume 解除抑制。Cancel 传播到活动 Run 和 Child Workflow，并对 Unknown 外部结果保持 Observe。Abandon 保存不可变 Decision/Audit/Outbox，由 Kernel 进入 Abandoned 并执行同类清理。所有历史 Artifact、Observation、Decision、Run 和 Usage 保留。
- 不通过条件：Pause 强杀或冻结已启动执行；Pause 后仍创建新 Run/Review/返工/子 Workflow；暂停期间完成的结果丢失；Resume 重放已完成副作用；Solution/Policy 改变 Pause 核心语义；Cancel 在 Unknown 未确认时直接宣称完成；Abandon 不要求 reason 或被实现成普通 Cancel；API 成功立即显示已收敛；终态允许 Resume；增加 Pausing 状态或第二套控制状态机。
- 所需证据：Pause/Resume/Cancel command、Workflow spec generation 与 status observedGeneration、暂停前后资源数量、三类活动 Run 收敛报告、Child Workflow 传播、DAG/时间线截图、Resume 后派发记录、Cancel/Abandon 取消与 Observe 轨迹、Decision/Audit/Outbox、终态按钮权限、幂等/Conflict，以及状态 Schema 检索证明不存在 Pausing。

#### `UJ-EXTERNAL-UNKNOWN-001`：自动 Observe 并在安全证据后 Retry

- 前置条件：本用例只覆盖 Workflow 内由 WorkUnit/ComponentRun 承接的副作用执行，不覆盖最终验收后的直接 Promotion Deployment；后者由 `UJ-DIRECT-DEPLOYMENT-RECOVERY-001` 验收。准备 Harness Extension Executor 的 PR、Merge、Release、Preview Deployment 或外部 API Fixture，覆盖“外部成功但响应丢失”“外部未创建”“部分创建”“持续 Unknown”“Cancel 结果 Unknown”“幂等可安全重试”和“不可安全重试”；Extension 已通过 Harness SDK 的 execute/observe/cancel 接口检查。
- 用户操作：
  1. 执行副作用 ComponentRun 并模拟响应丢失，观察状态进入 Unknown、WorkUnit Waiting，且系统没有重复 execute；
  2. 查看最近观察、可能副作用、external operation、idempotency、retryable/outcome known 说明和自动 Observe 状态；
  3. 等待系统按有界退避自动 Observe，再点击“立即重新检查”提前触发一次 Observe，确认二者复用同一 ComponentRun；
  4. 分别观察外部已成功、未创建、部分创建和持续 Unknown 的分支；
  5. 在未创建或幂等安全分支查看 Retry 启用，点击后确认创建新的 ComponentRun 并关联原记录；在其他分支确认 Retry 禁用；
  6. 点击 Cancel，确认只显示“取消请求已接受”，随后继续 Observe，直到 Cancelled 或其他真实终态；
  7. 让观察窗口耗尽，确认停止自动定时、保持 Waiting 并进入人工 Attention，而不是自动 Retry 或 Failed。
- 用户可见预期：Unknown 明确表示“可能成功也可能失败”，不是普通错误。系统自动检查，用户可以立即重查；Retry 何时可用及原因清晰可见；原 ComponentRun 历史不被覆盖；Cancel 与真正 Cancelled 分开；观察期限耗尽后用户获得人工处理入口。
- 平台处理预期：Harness Host/Controller 使用显式 `nextReconcileAt`/RequeueAfter 调用同一精确 Extension 实现的 observe，观察结果先持久化为 Observation/report，再触发 Workflow Reconcile。Kernel 只消费显式时间和事实。安全 Retry 新建 Run 并重新执行 Authorization、Admission、Credential 与状态校验；部分完成进入恢复/Remediation。Cancel 后继续 Observe。所有操作保持稳定 idempotency、causation 和 Audit。
- 不通过条件：Unknown 自动变 Failed；超时自动 Retry；用户 Observe 创建第二次副作用；Retry 覆盖原 ComponentRun；部分创建被当成未发生；未重新治理校验就 Retry；Cancel API 成功即显示 Cancelled；Kernel 读取隐式时钟；持续 Unknown 无限后台轮询且没有人工升级；页面只显示通用错误而不说明可能副作用。
- 所需证据：七类 Harness SDK 行为 Fixture、ComponentRun 前后记录、external_operation/idempotency/error、自动与手动 Observe report、Timer/Requeue、Workflow Waiting/Attention、Retry 新 Run causation、治理检查、Cancel→Observe 轨迹、持续 Unknown 升级，以及外部系统实际状态对照。

#### `UJ-EXECUTOR-RESOLUTION-001`：动态责任缺少执行器时补充当前 Workflow Agent

- 前置条件：准备一个已完成批准基线的 Software Delivery Workflow，其初始 TeamBindingSnapshot 没有 `security-reviewer`；DAG Orchestrator 生成一个责任、Capability、Contract 和独立性均合法的 Security Review WorkUnit；同时准备一个能力不足 AgentDefinition、一个满足全部要求的已发布 AgentDefinition，以及“要求调整计划”分支。
- 用户操作：
  1. 提交包含 Security Review WorkUnit 的 PlanDraft，观察 Plan Compiler、Required Plan Review 和 Planning Gate 通过并激活新 Plan，而不是因当前无 Agent 拒绝 Plan；
  2. 等待该节点依赖满足，确认 WorkUnit 进入 `Waiting`，DAG、Attention 和节点 Inspector 显示 Missing Executor；
  3. 查看 `work_unit_id`（工作单元身份）、`responsibility_requirement`（责任要求）、`capability_requirements[]`（能力要求）、`accepted_contracts[]`（兼容合同）、`executor_requirement_digest`（规范化执行要求摘要）和 `match_diagnostics[]`（匹配失败诊断）；
  4. 选择能力不足 Agent，确认服务端拒绝且不创建 Decision、Run、Workspace 或 Job；
  5. 在无现成候选分支点击“创建 Agent”，进入 Agent Center 发布合格 AgentDefinition，返回同一 Attention 后明确选择该 Agent；
  6. 提交“选择 Agent 并继续”，观察页面先显示 Decision 已接受；下一次 Reconcile 重建 HostCapabilitiesSnapshot 后才创建 AgentRun，并让同一 WorkUnit、同一 iteration 从 Waiting 继续；
  7. 对比操作前后 Project、Workflow.spec 与 TeamBindingSnapshot，确认只有 `Workflow.spec.decisionIds` 追加了精确 Decision ID，创建时执行快照与 TeamBindingSnapshot 均未修改；检查 AgentRun 固定所选 AgentDefinition、Decision ID/digest、HostCapabilitiesSnapshot digest 和 InvocationRequest；
  8. 运行一个 Active Plan 已预先声明“要求调整计划”Decision Requirement、条件分支和 DAG Orchestration WorkUnit 的 Fixture，确认 Decision 只激活该可见分支；再运行未声明该分支的 Fixture，确认页面不提供该操作并报告 Plan/Solution 设计缺口；
  9. 可选更新 Project Team bindings，确认只影响未来 Workflow，不是恢复当前 Workflow 的必需步骤。
- 用户可见预期：页面明确区分“Plan 有效”和“当前没有执行者”；用户可以选择已有 Agent，或往返 Agent Center 创建 Agent。只有 Active Plan 已预先声明对应 Decision Requirement、条件分支和 DAG Orchestration/Remediation WorkUnit 时，页面才显示“要求调整计划”，并明确它只激活既有分支。补充 Agent 不出现影响分析或后继 Workflow 向导；Decision 被接受与真正创建 Run 分开显示。
- 平台处理预期：Plan Compiler 不消费 HostCapabilitiesSnapshot，只验证执行要求是否合法。节点派发时 Kernel Executor Matcher 零匹配，返回 `WaitForDecision` 和规范化要求；Controller 不创建执行资源，Platform Core 保存不可变 MissingExecutor Observation。`answer` Decision 精确绑定 Observation ID/digest，`structured_result.agent_definition_id` 必填，并重新执行 Scope、Capability、Contract、独立性、Authorization、Admission、Runtime 与 Policy 校验；提交成功后 Platform Core 只把精确 Decision ID 追加到 `Workflow.spec.decisionIds`。WorkflowOrchestrationHost 以“初始 TeamBindingSnapshot + 有效 Workflow-local Executor Decision + LockedComponentSet + 显式可用性”重建 HostCapabilitiesSnapshot；Kernel 复用同一匹配路径继续。Decision 只作用于同一 Workflow、相同 requirement digest 和未启动 WorkUnit。
- 不通过条件：当前无 Agent 导致合法 Plan 编译失败或无法激活；Kernel/Controller 随机选择 Agent；除追加精确 `decisionIds` 外改写 Workflow.spec 创建时快照或 TeamBindingSnapshot；读取 Project 最新 Team bindings 后静默继续；为补充合格 Agent 强制更新 Project、影响分析或创建后继 Workflow；绑定降低责任、能力、合同、独立性或 Policy；API 成功立即伪装成 Run 已启动；复用 Decision 到不同 requirement digest；创建 TeamAmendment、BindingVersion 或第二套状态机；“调整计划”直接修改 Active Plan。
- 所需证据：PlanDraft/ValidatedPlan/PlanReviewResult/GateResult/Plan 激活记录、零匹配 Kernel Diagnostic 与 `WaitForDecision`、MissingExecutor Observation、Attention/DAG/Inspector 截图、无效 Agent 校验结果、Agent Center 往返、Answer Decision/Audit/Outbox、Reconcile 前后 HostCapabilitiesSnapshot digest、AgentRun request/provenance、Project/Workflow.spec/TeamBindingSnapshot 前后对比、Kubernetes 资源创建时间，以及显式重编排分支的 causation。

#### `UJ-HUMAN-RESPONSIBILITY-001`：人工作为 Reviewer 或显式 Acceptance WorkUnit 主执行者提交结构化结果

- 前置条件：准备一个已产生 Producer Artifact 与 `run-conclusion` Observation 的 Workflow；Harness 含 Required Reviewer `responsibility-run` 和后续独立用户 Decision Requirement。另准备两个 Preview E2E Fixture：内置默认 Fixture 由程序或 Agent 作为显式 Acceptance WorkUnit 主执行者，企业定制 Fixture 把同一普通 WorkUnit 主执行解析为 `kind=human`。配置两个满足人工 Reviewer/Acceptance Executor 资格的用户、一个无权限用户，以及 Producer 本人用于独立性拒绝 Fixture。
- 用户操作：
  1. 让一个启用 Required Reviewer `responsibility-run` 的 WorkUnit 完成程序验证，确认 Kernel 匹配人工 Reviewer Executor，WorkUnit 进入 `InReview`，“我的工作”出现同一待办；
  2. 打开待办，查看 `invocation_id`（调用身份）、`request_digest`（请求摘要）、`objective`（目标）、`responsibility`（责任）、`subject`（精确主体）、`inputs[]`（精确输入）、`contract`（合同）、Guide、`prior_conclusions[]`（前序结论）、`feedback[]`（反馈）和 `submission_contract`（提交合同）；
  3. 确认页面直接读取固定 `Run.request`，不要求安装 `oacok`，并确认没有创建 AgentRun、Session、Workspace、Job、PVC、HumanTask 或 HumanRun 聚合；
  4. 让无权限用户和 Producer 本人分别尝试提交，确认 Authorization/独立性校验拒绝，且不写 Observation、Run result、Decision 或 WorkUnit state；
  5. 合格 Reviewer 提交 Reject，填写 verdict、summary、非空 findings、可选 nits 和 evidence；确认页面先显示“人工结果已接受”，随后 Workflow Watch/Reconcile 才进入 `ReviewRejected → InRework`；
  6. 打开返工 Run，确认新的 InvocationRequest 包含完整 ReviewResult、review_run_id、reviewer_lineage_id、reviewer_principal 和 evidence，而不只是 rejected 状态；
  7. 返工后让两个合格 Reviewer 并发提交同一 Pass 结果；确认首个通过 `expected_work_unit_revision + request_digest` CAS 的结果生效，另一个返回 Conflict、保留输入并展示已接受提交摘要；同一 idempotency key 和 submission digest 重试返回原结果；
  8. 确认 ReviewResult Pass 后仍等待独立用户 Decision，人工 Reviewer 不能自动批准 Artifact；
  9. 运行内置默认 Preview E2E Fixture，确认显式 Acceptance WorkUnit 的程序或 Agent 主 Run 直接提交 AcceptanceResult，且系统不会再创建第二个 Acceptor responsibility-run、AgentRun 或人工待办；
  10. 在企业定制 Preview Acceptance WorkUnit 中，以人工主执行者提交 AcceptanceResult，覆盖 verdict、summary、每个业务步骤/Criteria 的 expected/actual/result、findings 和 evidence，并固定 `acceptance_run_id`、`acceptance_lineage_id`、`acceptance_principal`、AcceptancePlan ID/digest 与 suite digest；
  11. 确认 AcceptanceResult 进入 Observation/Gate 后，最终用户验收仍使用单独 Decision；人工验收执行者不能修改 Acceptance Criteria、Acceptance Plan、suite digest 或失败路由，提交最终 Decision 也不会重新运行 Acceptance Harness。
- 用户可见预期：“我的工作”只展示当前用户有资格提交的 human Run，并给出完整固定上下文、结构化表单和中文字段释义；没有领取/转派/锁定/租约。提交被接受与 Gate 收敛分开显示；ReviewResult、AcceptanceResult 和最终用户 Decision 三者身份、作用和时间线展示清晰分离。
- 平台处理预期：Kernel 仍只根据 ExecutorDescriptor 匹配逻辑 Executor，输出带同一 InvocationRequest 的 `DispatchExecution`/`StartReview`，并使用同一 Submission Validator；不导入 Principal 或 HumanTask 类型。Workflow Controller 保存 `kind=human` Run request、Lineage、subject、`principal_requirement` 和 handled sequence，不调用 Execution Host。`SubmitHumanRunResult` 校验 exact Workflow/WorkUnit/Run/iteration、Principal、Scope、责任、独立性、invocation/request digest、output contract、idempotency 和 CAS，在一个事务中保存 Observation、AuditEvent、CommandRecord/Outbox，再提交当前 human Run report。下一次 Workflow Watch 才由 Kernel/Gate 推进生命周期。
- 不通过条件：新增 HumanTask/HumanRun 表、CRD、聚合、Version 或领取状态机；Kernel 选择具体用户或读取浏览器在线状态；人工分支创建 Job/Workspace/Session；页面重新组装最新 Workflow 上下文；无权限用户或 Producer 可提交独立 Review；API 成功直接改 WorkUnit state；ReviewResult/AcceptanceResult 被保存成 Decision；Reviewer Pass 自动满足用户批准；并发提交覆盖首个结果或丢失后提交者输入；内置 Project Setup 强制要求 `acceptor` 岗位；自动 E2E 后又创建 Acceptor responsibility-run；人工验收执行者可修改 AcceptancePlan/suite digest；返工只看到状态而没有完整 findings/evidence。
- 所需证据：HostCapabilitiesSnapshot/ExecutorDescriptor、`DispatchExecution`/`StartReview`、human Run request/status、`principal_requirement`、我的工作与详情截图、无 AgentRun/Job/PVC/Workspace 证明、授权和独立性拒绝响应、`SubmitHumanRunResult` 请求/响应、Kernel Submission Validator 结果、ReviewResult/AcceptanceResult Observation、Audit/Command/Outbox、并发 CAS 与幂等记录、返工 InvocationRequest、GateResult、独立用户 Decision 以及完整连续任务线。

#### `UJ-RELEASE-CANDIDATE-001`：查看只读 Release 候选及其固定证据

- 前置条件：准备一个已完成串行 Integration 的 Software Delivery Workflow，并固定同一组 Integration commit、Build definition、构建制品、质量/安全验证 Observation、已批准 AcceptancePlan/suite digest 和 LockedComponentSet；准备 Release Gate 未形成、Gate Error/Rework 和 Gate Pass 三个分支，以及一次重新 Build 导致输入摘要变化的分支。
- 用户操作：
  1. 在 Release Gate Pass 前打开 Delivery 的“Release 候选”页面；
  2. 查看已批准 Requirement、Acceptance Criteria、三份 Design 和 Project Rules 的精确身份，以及 `integration_commit_sha`（集成提交 SHA）、`build_definition`（构建定义）、`artifact_ids[]`（构建制品身份集合）、`verification_observation_ids[]`（验证证据身份集合）、`acceptance_plan_artifact_id`（验收方案成果身份）、`suite_digest`（验收套件摘要）、`locked_component_set_id`（锁定组件集合身份）、`release_gate_result_id`（发布门结果身份）和 `release_input_digest`（发布输入摘要）；
  3. 从每个字段下钻到精确 Artifact、Observation、构建报告和 GateResult，并返回原页面；
  4. 在 Gate 未形成或 Error/Rework 分支确认页面展示 blocker 和对应 WorkUnit 导航，不出现可编辑候选内容、`release_id` 或默认“确认发布”按钮；
  5. 重新 Build 并替换一个制品 digest，确认页面获得新的 `release_input_digest`，旧 GateResult 明确过期且不能继续显示为当前 Pass；
  6. 让 Release Gate 针对最新摘要 Pass，确认同一路由显示新形成的不可变 `release_id`、Release digest、形成时间和全部固定来源；
  7. 对比形成前后的数据库、API 和 Kubernetes Schema，确认不存在 ReleaseCandidate 表、CRD、聚合、Version、候选状态或隐藏审批记录；
  8. 在无发布 Decision Boundary 的内置方案中确认系统直接进入 Preview 后续步骤；在显式边界 Fixture 中确认普通 Decision 通过独立 Attention 出现，而不是成为候选字段。
- 用户可见预期：用户可以完整理解“准备发布什么、由哪些证据证明、当前为何不能发布”，但不需要重复填写交付范围或手工组装证据。Gate Pass 前只显示发布就绪事实，Gate Pass 后才显示正式 Release；最终业务验收仍留在 Preview 阶段。
- 平台处理预期：Software Delivery Query 从已有 Workflow/WorkUnit 状态、Artifact、Observation、GateResult 和外部构建事实实时组装 `ReleaseReadinessView`，服务端规范化精确输入并计算 `release_input_digest`；Query 不写入任何候选对象。Release Gate 必须绑定同一摘要；输入变化后旧结果失效。Gate Pass 后 Software Delivery facade 复用通用 Artifact 正式提交 Use Case 创建不可变 Release，并固定同一输入摘要和 GateResult。Kernel、Controller 和 Platform Core 不增加 ReleaseCandidate 生命周期或发布专用 Action。
- 不通过条件：页面要求用户填写发布范围或修改候选字段；Gate 前创建 Release；使用 Branch、tag-only、latest 或浮动组件；省略 AcceptancePlan/suite digest；旧 Gate Pass 自动继承到新制品；把 `release_input_digest` 当 ID/Version；查询动作落库；页面默认要求再批准一次发布；Gate Pass 后 Release 来源与候选摘要不一致；把最终 Preview 验收提前到当前页面。
- 所需证据：Gate 前后 Release 页面截图、`GetReleaseReadiness` 响应、字段中文释义与证据下钻、前后 `release_input_digest`、旧 Gate 过期结果、GateResult subject/digest、不可变 Release Artifact、数据库/CRD/API Schema 检索、Audit/Outbox、无默认 Decision 记录证明，以及显式 Decision Boundary 对照 Fixture。

#### `UJ-PREVIEW-FINAL-ACCEPTANCE-001`：访问 Preview 并提交绑定当前部署精确事实的最终验收 Decision

- 前置条件：准备一个 Release Gate 已 Pass 且不可变 Release 已形成的 Software Delivery Workflow；当前集群 Preview Deployment 已成功并产生 URL、与 Release 一致的 actual digest、Required health Observation；显式 E2E Acceptance WorkUnit 已直接产生 verdict=pass 的 AcceptanceResult。另准备页面打开后分别替换 actual digest、追加失败/Unknown health Observation、替换 AcceptanceResult 的过期分支，以及两个重复/并发批准请求。
- 用户操作：
  1. 从 Release 页面进入 Preview，打开当前 Preview URL 并返回最终验收 Inspector；
  2. 查看 `deployment_id`（Preview 部署身份）、`release_subject`（Release 精确主体）、`actual_digests[]`（实际运行摘要集合）、`health_observation_subjects[]`（必需健康观察精确主体集合）和 `acceptance_result_subject`（E2E 验收结果精确主体），并逐项下钻证据；
  3. 确认页面只读展示 `decision_subject.kind=software-delivery.preview-acceptance`（最终验收主体类型）、`decision_subject.id=deployment_id`（复用部署身份）和服务端生成的 `decision_subject.digest`（固定 Release、实际部署、健康与 AcceptanceResult 的主体摘要），用户不手工填写或选择这些值；
  4. 点击“验收通过”，提交 `decision_type=approve`、当前 `decision_subject`、`expected_work_unit_revision`、可选 reason 和 idempotency key；
  5. 确认响应先显示“最终验收 Decision 已接受”，等待 Workflow Watch/Reconcile 后最终 Gate 通过且整张活动 DAG 按固定规则收敛，不把 API 200/Accepted 直接显示为 Workflow Succeeded；
  6. 使用同一 idempotency key 与相同请求重试，确认返回原 Decision；使用同一 key 但不同内容时拒绝；
  7. 分别运行页面打开后 actual digest、Required health Observation 或 AcceptanceResult 发生变化的 Fixture，再提交旧 `decision_subject.digest`，确认返回 `StaleSubject`/Conflict、保留用户 reason、刷新证据且不创建 Decision；
  8. 针对刷新后的新主体重新确认并批准，确认新 Decision 绑定 `kind + deployment_id + 新 subject_digest`，旧 Decision 只保留历史且不能满足当前 Gate；
  9. 确认最终 approve 不重新运行 Acceptance Harness、不创建 PreviewAcceptance/AcceptanceApproval/Version，也不只绑定 Release；Gate 通过后才开放 Succeeded/Promotable 后续结果。
- 用户可见预期：用户看到的是一个完整、可下钻的“当前 Preview 到底部署了什么、是否健康、E2E 是否通过”的证据页面，只需要执行一次“验收通过”；摘要与内部身份只读。事实变化时页面明确提示验收证据已过期并要求重新确认，不静默沿用旧批准，也不丢失用户可选说明。
- 平台处理预期：`GetPreviewAcceptance` 只从当前 Decision Requirement 精确指定的 Deployment、Release、actual digests、Required health Observations 和 AcceptanceResult 组装瞬时 PreviewAcceptanceView；不按“最新”猜测，不落库。规范化算法对 Deployment ID、Release ID/digest、排序后的 actual digests、排序后的 health Observation ID/digest 和 AcceptanceResult ID/digest 计算 sha256。`SubmitDecision` 在授权、ApproverRequirement、WorkUnit revision、幂等校验后重新读取同一组权威事实并重算 subject digest；匹配才在一个事务中保存普通单主体 Decision、AuditEvent、CommandRecord 与 Outbox。后续 Reconcile/Gate 消费 Decision，Platform Core 不直接写成功状态。
- 不通过条件：Decision 只绑定 Release；用户手工选择 Deployment/AcceptanceResult 或填写 digest；Query 选择 latest；缺失、Unknown、失败或 drift 仍允许批准；摘要变化后旧 Decision 继续满足 Gate；API 成功直接标记 Succeeded；批准触发 Acceptance Harness 重跑；创建 PreviewAcceptance、AcceptanceApproval、DecisionVersion、审批状态机或持久化 View；`StaleSubject` 清空用户 reason；同一幂等键产生多个 Decision。
- 所需证据：Preview 与最终验收页面截图、`GetPreviewAcceptance` 响应与字段中文释义、规范化 digest Fixture、Release/Deployment/health Observation/AcceptanceResult 精确记录、`SubmitDecision` 请求/响应、approve Decision 的 SubjectKey、StaleSubject/Conflict 响应、幂等与并发记录、Audit/Command/Outbox、Reconcile 前后 GateResult、活动 Plan 与全部 WorkUnit 状态、无 Harness 新 Run 证明，以及数据库/CRD/API Schema 中不存在独立完成对象或新增聚合的检索结果。

#### `UJ-PREVIEW-REQUEST-CHANGES-001`：最终验收要求修改并进入技术方案闭环

- 前置条件：`UJ-PREVIEW-FINAL-ACCEPTANCE-001` 的 Preview 证据链处于 `readiness=ready`，但尚未提交最终 approve；Active Plan 已包含经 Plan Compiler 和 Plan Review 验证的互斥条件分支：`approve` 通向原 Workflow 完成并使 Release 可 Promotion，`request_changes` 通向 Technical Change Design WorkUnit，之后依次可达独立 Design Review、用户 Design Decision 和 DAG Orchestration WorkUnit。Project TeamBindingSnapshot 中 Architect 候选只声明既有 `architecture-design` Capability，没有 rework/change-impact Capability。
- 用户操作：
  1. 在最终验收 Inspector 点击“要求修改”，留空原因并提交，确认页面和服务端都阻止；
  2. 填写可执行修改原因，并提交 `decision_type=request_changes`、当前 `decision_subject`、`expected_work_unit_revision` 和幂等键；
  3. 确认命令先显示“最终验收 Decision 已接受”，不立即显示 Developer Remediation、DAG Orchestrator 已启动或 Workflow Failed；
  4. 等待 Workflow Reconcile，确认当前 E2E Acceptance WorkUnit、Release、Deployment 和 AcceptanceResult 保持原终态，`approve` 互斥分支进入 Obsolete，Technical Change Design WorkUnit 进入 Ready 或因执行器缺失进入 Waiting；
  5. 观察 Kernel 复用该节点已有的 Architect 责任与 `architecture-design` Capability 匹配 Executor，并为 Architect Run 组装包含本次 Decision、Preview 验收主体、AcceptanceResult findings/evidence、Deployment/health、Release、批准基线、当前 Plan 和必要上游结论的固定 InvocationRequest；
  6. 让 Architect 通过 `oacok work show/guide/read` 获取协作协议和精确输入，只修订受影响的 `solution-design`、`technical-overview` 或 `detailed-design` Artifact，并使用 `previous_artifact_id` 关联旧成果；
  7. 运行程序检查和独立 Design Review。Reviewer Reject 时确认完整 findings/evidence 进入 Architect 下一 iteration；Reviewer Pass 后确认仍等待用户针对新的精确 Design Artifact 作出 Decision；
  8. 用户再次 Request Changes 时确认继续 Architect 设计返工；用户 Approve 时确认只批准当前精确新 Artifact，旧 ReviewResult/Decision 不继承；
  9. 运行“不影响其他基线”分支，确认后续 DAG Orchestration WorkUnit 进入 Ready，DAG Orchestrator 读取已批准技术方案生成 PlanDraft，并经过 Compiler、独立 Plan Review 和 Planning Gate 激活新 Plan；
  10. 运行“影响 Requirement/Acceptance Criteria/Acceptance Plan”分支，确认先进入对应既有基线修订、Review 和用户批准流程，全部通过后才允许 DAG Orchestration；
  11. 检索 AgentDefinition、Solution slot、Plan、Workflow、数据库与 API Schema，确认不存在 `rework`/`change-impact` 专用 Capability、TechnicalChangeDesign 聚合、返工状态机或由 Kernel 写死的 Architect/Developer 角色。
- 用户可见预期：用户的修改意见沿同一 Workflow 全局时间线进入一条清晰路径：“最终验收意见 → Architect 针对性技术方案 → Reviewer 结论 → 用户设计批准 → 新 DAG 计划”。页面不要求用户选择返工到 Developer、Architect 或 Orchestrator，也不把技术反馈直接变成开发任务；所有英文身份、摘要和 Capability 都有中文说明。
- 平台处理预期：Platform Core 对同一 Preview 验收 SubjectKey 重算 digest 并原子保存单主体 request_changes Decision、AuditEvent、CommandRecord 和 Outbox。Kernel 不解释软件工程语义，只根据 PlanNode 的类型化 `activation` 选择分支、把未选择互斥节点标记 Obsolete，并按普通依赖匹配 Architect Executor。Technical Change Design 使用既有 `architecture-design` Capability 和设计 Artifact 合同；针对性来自 InvocationRequest 的 Guide、精确输入与反馈。Review/用户 Decision 完成后，显式 DAG Orchestration WorkUnit 才进入 Ready；Orchestrator 只把批准方案转换成增量 DAG。
- 不通过条件：Request Changes 直接启动 DAG Orchestrator 或 Developer；Kernel 根据自由文本猜测返工位置；重新打开终态 E2E WorkUnit、Release 或 Deployment；要求单独配置 rework/change-impact Capability；创建 TechnicalChangeDesign 表、CRD、Version 或状态机；Architect 绕过 Review/用户批准；Reviewer Pass 自动批准设计；受影响 Requirement/Acceptance Plan 未重新批准就规划实施；Plan 使用脚本或自由表达式决定分支；未选择分支一直 Pending 阻止收敛；旧 Artifact、ReviewResult 或 Decision 自动批准新内容。
- 所需证据：最终验收 Request Changes 表单及必填校验、同一 SubjectKey/digest 重算、Decision/Audit/Outbox、Reconcile 前后 Active Plan/WorkUnit 状态、PlanNode activation 与互斥 Obsolete 结果、Architect Executor 匹配诊断、固定 InvocationRequest 和 `oacok` 输出、Design Artifact previous linkage、ReviewResult/用户 Decision/返工时间线、受影响基线分支、DAG Orchestration PlanDraft/Compiler/Review/Gate/新 Plan，以及不存在专用返工 Capability 和新增聚合的全局检索结果。

#### `UJ-DIRECT-PROMOTION-001`：查看 Promotable Release 或直接部署到外部目标

- 前置条件：准备一个最终 Preview 验收已 approve、原 Software Delivery Workflow 已 `Succeeded`、不可变 Release 已显示 `Promotable` 的项目。Project 当前 `solution_setup` 配置一个标准 Kubernetes 目标和一个用户 CI/CD 目标；每个目标都已由系统解析出精确 `deployment_driver`（部署 Driver）、`target_configuration_digest`（目标配置摘要）、`concurrency_key`（目标并发键）和最小权限 CredentialBinding。另准备无外部目标、Project revision 过期、凭证不可用、重复请求及用户无发布权限的分支。
- 用户操作：
  1. 不配置外部目标完成一次 Delivery，确认最终 approve 后 Workflow 正常 `Succeeded`、Release 保持 `Promotable`，页面不要求用户再点击“保持可发布”或提交第二个 Decision；
  2. 在已配置目标的 Project 中，从 Promotable Release 页面点击“部署到外部环境”，查看当前可用目标；
  3. 选择一个目标并进入单一确认页，确认页面展示 Release 身份/digest、目标名称和类型、目标配置摘要、系统解析的 DeploymentDriver 以及影响说明；用户只选择目标，不填写 endpoint、namespace、Credential、Driver、ExtensionPackage、Harness、Workflow 或 WorkUnit 字段；
  4. 确认后提交 `StartPromotion`，客户端请求只包含 `release_id`（Release 身份）、`deployment_target_id`（部署目标身份）、隐藏的 `expected_project_revision`（预期 Project 修订号）和 `idempotency_key`（幂等键）；
  5. 确认响应只表示“部署请求已接受”，返回 `deployment_id`（Deployment 身份）与 `accepted_at`（接受时间），不把接受请求直接显示成部署成功；
  6. 使用相同幂等键和相同请求重试，确认返回同一 Deployment；相同键不同请求必须冲突，两个不同幂等键针对同一 `concurrency_key` 的并发开始请求只能有一个被接受；
  7. 在打开确认页后更新 Project 外部目标配置，再提交旧 `expected_project_revision`，确认服务端拒绝并要求刷新；禁用 Credential、撤销 Executor 或使用无权限主体时同样不创建 Deployment；
  8. 对 Kubernetes 与 CI/CD 两种目标分别执行一次成功 Fixture，确认 Deployment Host 在事务提交后消费 Outbox、按 Deployment 范围取得短期凭证，并通过 Deployment SDK 调用固定的 DeploymentDriver `start`；
  9. 查看 Deployment 详情，确认请求事实不可变，执行进度由递增 `report_sequence` 的类型化报告/Observation 追加形成，当前 outcome 只是可重建读模型；
  10. 检查源 Workflow、Plan、WorkUnit、Release 和最终验收 Decision 均未被改写，且本次默认直接 Promotion 没有创建新 Workflow、Plan、WorkUnit、ComponentRun、AgentRun、Workspace、Harness Run，也没有调用 Workflow Controller 或 Orchestrator Kernel；
  11. 安装一个确实需要多级审批、数据库迁移和灰度发布的企业 Solution Fixture，确认它可以显式定义独立 Workflow/DAG；但该能力不能让默认单步 Promotion 暗中转回 Kernel 路径。
- 用户可见预期：用户完成 Delivery 后立即得到 `Succeeded + Promotable`，是否部署到外部环境是可选的后续操作。发起部署时只需选择一个已配置目标并确认；页面明确区分“请求已接受”“执行中”“成功/失败/结果未知”，可以沿 Deployment 查看固定 Release、目标与执行报告，不需要理解 Kernel、Harness 或 ComponentRun。
- 平台处理预期：`StartPromotion` 是 Software Delivery 应用命令。Platform Core 在同一事务前校验 Release 的精确身份/digest 和 Promotable 事实、当前 Project revision、目标配置、精确 DeploymentDriver、Authorization/Admission、Credential readiness、并发键与幂等键；成功时原子写入不可变 Deployment 请求、CommandRecord、AuditEvent 和 Outbox。事务提交后，`oac-execution-host` 内的 Deployment Host 取得 Deployment 范围短期凭证，从 ExtensionCatalog 解析该 Deployment 固定的 Driver，通过 `deployment.oac.dev/v1` 调用，并以 `SubmitDeploymentReport` 追加报告。直接 Promotion 不经过 Workflow Controller 和 Orchestrator Kernel；DeploymentDriver 不是 WorkUnit Executor、Harness Extension 或万能 `component.invoke`。
- 不通过条件：没有外部目标就阻止 Workflow 完成；最终 approve 后还要求“保持 Promotable”Decision；在 Delivery 创建时强制选择 Promotion 目标；把外部 Promotion 编译成默认 WorkUnit/DAG；让 Kernel、Agent 或 Harness 执行单步部署；为了复用 ComponentRun 而伪造 Workflow/WorkUnit；用户填写 Executor、Credential、namespace 或 digest；请求 Accepted 直接显示成功；Deployment 执行期间读取更新后的 Project 目标或 Credential 身份而非固定请求；重复请求产生多个外部副作用；直接修改源 Workflow、Release 或最终 Decision；为直接 Promotion 创建第二套活动状态机。
- 所需证据：无外部目标与有外部目标的 Release 页面、单一确认页及字段中文释义、`StartPromotion` 请求/响应、Project revision/Stale/Authorization/Credential/Driver/并发失败 Fixture、Deployment 不可变请求和 `execution_origin=direct-promotion`、CommandRecord/AuditEvent/Outbox、Deployment Host 与 DeploymentDriver 调用记录、短期凭证使用记录、递增报告序列、幂等重放，以及全局检索证明默认路径没有新增 Workflow、Plan、WorkUnit、ComponentRun、AgentRun、Harness Run 或 Kernel action。

#### `UJ-DIRECT-DEPLOYMENT-RECOVERY-001`：观察、取消和恢复直接 Deployment

- 前置条件：通过 `UJ-DIRECT-PROMOTION-001` 创建一个 `execution_origin=direct-promotion` 的 Deployment，并准备七组 Executor Fixture：长期 Running、外部成功但响应丢失后首次为 Unknown、持续 Unknown、Cancel 支持、Cancel 不支持、明确 Failed、明确 Cancelled。Project 中另有至少一个同项目、目标兼容、精确 digest 的已知可用旧 Release。准备重复 Observe/Cancel、无 observe/cancel 权限、目标配置在恢复前变化、Credential 失效、Executor 被撤销和并发新 StartPromotion 请求。
- 用户操作：
  1. 打开 Deployment 详情，检查固定 Release/目标/Executor，以及 `deployment_id`（部署身份）、`outcome`（当前结果）、`external_run_id`（外部执行身份）、`expected_digests[]`（预期摘要）、`actual_digests[]`（实际摘要）、`report_sequence`（报告序号）、`observed_at`（最近观察时间）、`allowed_actions[]`（允许操作）、`eligible_releases[]`（可恢复旧 Release）和 `blockers[]`（阻塞原因）的中文说明；
  2. 在 Pending/Running 分支等待 `next_observe_at` 驱动的自动 Observe，再点击“立即重新检查”，确认两者都调用同一 `deployment_id + request_digest + external_run_id` 的 `observe`，只追加新报告，不创建新 Deployment，也不调用 `start`；
  3. 连续快速点击“立即重新检查”，确认服务端按 `deployment_id + 最新 report_sequence` 合并请求；使用无 `deployment.observe` 权限的用户确认被拒绝且不产生 Outbox；
  4. 运行“外部成功但响应丢失”Fixture，确认先显示 Unknown，Observe 后收敛为 Succeeded；Succeeded 不显示恢复操作；
  5. 运行持续 Unknown Fixture，确认只显示 Observe 和条件性 Cancel，“重新部署此版本”“部署历史版本”均禁用；直接换新幂等键调用 StartPromotion 时因同一 `concurrency_key` 仍被占用而拒绝；
  6. 点击“取消部署”，确认 `CancelDeployment` 响应只显示“取消请求已接受”，随后状态为 Cancelling、Unknown 或最新真实状态，并继续 Observe；只有 Executor 明确报告后才显示 Cancelled、Failed 或 Succeeded。Cancel 不受支持或用户无权限时按钮禁用并显示 blocker；
  7. 运行明确 Failed 与 Cancelled Fixture，确认只有这两个已知终态开放“重新部署此版本”和“部署历史版本”；Failed 的含义必须保证不存在仍未确认的活动副作用；
  8. 点击“重新部署此版本”，确认页面复用 Promotion 确认页、带入相同 Release、生成新幂等键并隐藏携带 `caused_by_deployment_id`（前序 Deployment 身份）；提交后得到新的 `deployment_id`，旧 Deployment 保持 Failed/Cancelled；
  9. 点击“部署历史版本”，从 `eligible_releases[]` 选择精确旧 Release，确认同样调用 StartPromotion 形成新 Deployment；新旧 Release 不同即可表达恢复到旧版本，不创建 RollbackDeployment、rollback Release 字段或 RolledBack 状态；
  10. 在恢复确认页打开后修改 Project 目标、禁用 Credential 或撤销 Executor，再提交，确认新的 StartPromotion 重新校验当前 Project revision、目标、Credential、Executor、Authorization、Admission 与并发键并拒绝过期请求；旧 Deployment 不受修改；
  11. 查看 Deployment 时间线，确认新记录通过通用 causation 关联前序记录，原 request、reports、outcome、来源 Workflow、Release 和最终验收 Decision 均未被改写；
  12. 全局检查 Observe、Cancel、Retry 和部署旧 Release 均未创建 Workflow、Plan、WorkUnit、ComponentRun、AgentRun、Workspace、Harness Run 或 Kernel action。
- 用户可见预期：一个 Deployment 始终使用同一详情页。用户能清楚区分“正在执行”“结果未知”“取消处理中”“明确失败”“已取消”和“成功”；Unknown 时只能检查或取消，不会被诱导重复部署。结果明确失败或取消后，用户可以重试当前版本或选择一个可读的历史版本，随后进入一条新的 Deployment，并在同一时间线看到前后关系。
- 平台处理预期：Platform Core 从不可变 Deployment 请求和追加式报告组装 `DeploymentDetailView` 与 `allowed_actions[]`，不保存第二套恢复对象。非终态报告中的 `next_observe_at` 在同一报告事务中形成持久化延迟 Outbox；手动 Observe 只插入或提前同一观察意图。Deployment Host 使用原请求固定的 Executor、target digest、request digest 与 external run identity 调用 observe/cancel，并通过 SubmitDeploymentReport 追加单调 sequence。`Pending|Running|Unknown|Cancelling` 持续持有 target concurrency key；只有 `Failed|Cancelled` 释放恢复入口。Retry/旧版本部署复用 StartPromotion，重新执行所有治理与当前配置校验，并创建带 causation 的新 Deployment。
- 不通过条件：Unknown 时开放 Retry 或旧版本部署；Observe 调用 start 或创建新 Deployment；Cancel Accepted 直接显示 Cancelled；Failed 仍可能存在未知活动副作用；自动重试创建新部署；原地覆盖 release_id、request、reports 或 outcome；增加 RolledBack、RetryDeployment、RollbackDeployment 聚合或恢复状态机；恢复请求沿用旧 Project target/Credential/Executor 而不重校验；新旧 Deployment 无 causation；任何恢复动作进入 Workflow Controller、Kernel、Harness 或 ComponentRun。
- 所需证据：Deployment 详情与各状态截图、字段中文释义、GetDeploymentDetail 响应、自动与手动 Observe Outbox、Observe/Cancel 授权失败、DeploymentDriver start/observe/cancel 调用计数、Unknown 并发冲突、Cancel 后持续 Observe、Failed/Cancelled 终态 Fixture、两次恢复 StartPromotion 请求、新旧 deployment_id/request digest/release digest/causation、Project revision/Credential/Driver 过期拒绝、递增报告序列、AuditEvent，以及数据库/CRD/API Schema 全局检索证明不存在新增恢复聚合和 Kernel/Workflow 路径。

#### `UJ-AUDIT-001`：查询 AuditEvent、治理证据链并执行受控导出

- 前置条件：在同一 Scope 下准备两个 Project，并产生 Authentication、Authorization Allow/Deny、Execution Admission Admit/Reject、Credential use success/failure、Decision、AgentRun/ComponentRun/human Run、Package lifecycle、Project 配置更新、Workflow 命令、直接 Deployment 和状态写入等 AuditEvent；每条事件具有可验证的 RequestContext、Subject、Authorization Policy provenance、Execution Admission rules/facts provenance、AuthenticationDriver provenance，以及 causation/correlation。准备 Scope Auditor、仅一个 Project 的 Project Auditor、无 `audit.read` 用户、只有 `audit.read` 无 `audit.export` 用户和具有导出权限的用户；配置可暂停的外部审计接收端 Fixture。
- 用户操作：
  1. 进入统一 `/governance/audit` 页面，确认不存在单独的 Authorization、Admission 或 Credential-use 审计页面；检查 `time_range`（查询时间范围）、`project_id`（Project 筛选）、`category`（审计类别）、`action`（操作名称）、`outcome`（操作结果）、`principal_id`（操作者身份）、`subject`（被操作对象）、`correlation_id`（跨模块关联身份）、`cursor`（分页游标）和 `page_size`（每页数量）的中文说明；
  2. 使用时间、Project、category、action、outcome、Principal、Subject 和 correlation 分别筛选，再组合筛选，确认结果始终受当前 Scope/Project 授权约束；
  3. 从 Project、Workflow、AgentRun、Component 和 Deployment 详情点击“查看审计记录”，确认都跳转同一 Audit Explorer，只预填当前 Project/Subject，不建立新的详情或事件来源；
  4. 选择一条 AuditEvent，在同页 Inspector 查看 `AuditEvent → RequestContext → evaluated controls → Credential-use facts → exact Command/Decision/Run/Deployment subject → causation/correlation links`；Authorization 控制显示精确 Policy ID/revision/digest 和匹配 grant，Execution Admission 控制显示 rules/facts digest、检查结果和约束，Authentication/Credential 等采用 Package 的控制显示精确 Provider release/digest；
  5. 检查 Credential 证据只显示 `credential_binding_id`（凭证绑定身份）、fingerprint（指纹）、purpose（用途）、target（目标）和 outcome（结果）；检查 Secret、Token、Authorization Header、Credential 明文、Prompt、私有推理、完整工具日志和未授权 Artifact 正文不出现在列表、Inspector、网络响应、浏览器缓存、日志或导出；
  6. 使用 Project Auditor 查询另一个 Project，确认服务端拒绝或过滤而不是依赖前端隐藏；使用无 `audit.read` 用户直接调用 Query/Detail API，确认拒绝请求自身被记录为 AuditEvent；
  7. 读取第一页后并发写入新 AuditEvent，再按 `next_cursor` 翻页，确认排序固定为 `occurred_at DESC, audit_event_id DESC`，既有结果不重复、不跳过；无界时间范围、超大 page size 和伪造 cursor 必须拒绝；
  8. 暂停外部审计接收端，继续产生并查询本地 AuditEvent，确认列表、详情和证据链仍可用；恢复后按游标继续外发，不改变本地事件；
  9. 使用只有 `audit.read` 的用户确认不显示导出或导出被拒绝；使用具有 `audit.export` 的用户按当前筛选流式导出 CSV 与 JSONL，确认内容沿用相同授权和脱敏，导出动作产生 AuditEvent；超过条数/字节上限时要求缩小时间范围，不创建 Export Job；
  10. 连续执行普通成功的 Query 和 Detail，确认不会为每次读取递归产生 AuditEvent；拒绝的读取、导出和策略控制的高敏感元数据展示会留下审计；
  11. 全局检查该流程没有创建 AuditExplorer、AuditEvidence、AuditSession、Export 聚合、Version、Workflow、Plan、WorkUnit、Run、ComponentRun 或 Kernel action。
- 用户可见预期：管理员或获授权的 Project Auditor 只在一个页面中查询所有治理类别，能够通过过滤快速定位事件，在同页看清“谁、在什么 Scope/Project、对哪个对象、依据什么 Policy/规则，以及适用时由哪个 AuthenticationDriver 处理、产生了什么结果和前因后果”。资源详情可直接带筛选进入该页面；敏感内容始终不可见；外部审计接收端故障不影响平台内查询。
- 平台处理预期：`QueryAuditEvents` 和 `GetAuditEventDetail` 由 Platform Core Governance Query Application 承接，服务端根据 RequestContext 执行 `audit.read`，从本地 PostgreSQL AuditEvent 权威来源读取，并按精确 ID 组装瞬时 `AuditEventDetailView`。查询使用 `(occurred_at, audit_event_id)` 复合游标、服务端限定时间范围/page size 和字段级脱敏。`ExportAuditEvents` 复用同一筛选、排序、授权与脱敏，要求 `audit.export` 并有界流式输出。普通成功读取不审计；拒绝、导出和受控高敏感元数据展示审计。查询不调用 Workflow Controller、Orchestrator Kernel、Execution Host 或外部审计接收端。
- 不通过条件：按治理类别建立多套页面或数据库模型；把日志、AgentRunEvent、Kubernetes Event 或外部 SIEM 当作审计权威；客户端决定 Scope；资源读取权隐式等于审计读取权；offset 导致翻页重复/跳过；返回 Secret、Token、Header、Prompt、私有推理或完整工具日志；普通成功读取无限递归写审计；拒绝访问或导出无审计；外部接收端故障导致本地查询不可用；导出创建第二套长期任务状态机；查询触发 Workflow/Kernel。
- 所需证据：统一 Audit Explorer 与资源详情跳转截图、字段中文释义、Query/Detail/Export 请求响应、各筛选组合、Scope/Project 越权 Fixture、服务端脱敏测试、网络与浏览器缓存检查、并发写入下游标分页记录、外部中断/恢复记录、CSV/JSONL 内容、导出超限响应、普通读取前后 AuditEvent 数量、拒绝/导出审计事件，以及数据库/API/CRD 全局检索证明不存在第二套审计聚合和 Workflow/Kernel 路径。

#### `UJ-USAGE-001`：使用图表、排行、对比和明细分析 Agent、模型与 Token Usage

- 前置条件：在同一 Scope 下准备两个 Project、一个父 Workflow 与两个子 Workflow、多个 WorkUnit/iteration，以及 Architect、Reviewer、Developer、DAG Orchestrator 四类责任的 AgentRun。准备以下 RuntimeDriver Fixture：完整模型调用（请求模型与不同的实际响应模型、input/output total、cache read/write、reasoning、first output、finish reason、Provider USD 成本）、部分模型调用（无 actual model/TTFT/cost）、失败并重试调用、runtime-native Tool、MCP Tool、Runtime 启动/结束；再准备重复 event_id/sequence、Review Reject→Rework→Pass、成功/失败 WorkUnit、USD/CNY 两种成本和无 `usage.read` 用户。测试环境不安装 OpenTelemetry SDK/Collector/OTLP Receiver。
- 用户操作：
  1. 打开统一 `/governance/usage`，确认不存在独立 Project Usage、Agent Usage 或 Model Usage 页面；检查 `time_range`（时间范围）、`project_id`（Project）、`workflow_key`（Workflow）、`work_unit_id`（WorkUnit）、`agent_definition_id`（Agent 定义）、`responsibility`（执行责任）、`runtime_driver`（RuntimeDriver 精确实现）、`model_provider_connection_id`（Provider 连接）、`request_model_id`（请求模型）、`response_model_id`（实际模型）、`usage_kind`（使用类型）和 `group_by`（聚合维度）的中文说明；
  2. 分别按 Project、Workflow、WorkUnit、Agent、责任、Runtime、Provider、请求模型、实际模型和 usage kind 筛选/分组，并执行上一周期对比；确认所有图表和明细共享同一筛选，点击图元生成可见筛选 Chip 且可撤销；
  3. 在总览查看总 Token、模型调用、AgentRun、成功 WorkUnit、已知成本、P50/P95、缓存率和周期变化；无权 Project 不得被前端或 API 枚举；
  4. 在模型与 Token 视图检查 input total、cache read、cache write、output total 和 reasoning 的包含关系。互斥图表只能使用可证明的 uncached/visible 派生桶；总量与子集不得重复相加，字段不全或冲突时显示 unknown/Finding；
  5. 比较请求模型与实际模型，确认 Provider 未返回实际模型时显示“未报告”，不能复制请求模型；查看完整 Fixture 的 total latency/TTFT、失败类型、retry 和 finish reason，部分 Fixture 的 TTFT coverage 必须下降而非显示 0；
  6. 在 Agent 效率视图按 Agent 和责任查看 Run 数、成功率、Review Reject、返工轮次、每成功 WorkUnit Token/成本及成本-时长散点图；核对这些结论来自 UsageRecord 与 Workflow/Run/Review/Gate 事实的查询关联，不写回 UsageRecord；
  7. 在 Workflow 与 WorkUnit 视图比较父 Workflow exclusive 与 inclusive，确认子 Workflow 原始 Usage 只计量一次；从 Workflow DAG 节点 Usage badge 下钻到同一 Usage Explorer；
  8. 在 Runtime 与 Tool 视图查看 Runtime 启动/总时长、runtime-native/MCP Tool 调用数、错误率和 duration；Tool 输入输出正文、MCP Secret 和完整错误响应不得出现；
  9. 在原始明细中打开 model-call、tool-call、runtime 三种 tagged UsageRecord，核对 source_event_id、AgentRun、WorkUnit、Workflow、AgentDefinition、responsibility、RuntimeDriver 精确 release、Provider/模型、类型化 payload 和成本来源；
  10. 重放相同 event_id/sequence/digest，确认返回原结果且 Token/成本不增加；同身份不同 digest 必须拒绝。确认一个模型调用不会因 Turn、terminal 和 usage 多种事件重复形成记录；
  11. 检查 USD/CNY 分开汇总；provider-reported 优先，platform-calculated 固定当时 pricing basis，unavailable 不显示为 0。不得使用当前价格重算历史成本；
  12. 查看 Token、Cost、TTFT、Reasoning、Cache 的 `known_count`（已知数量）、`unknown_count`（未知数量）和 `coverage_ratio`（覆盖率）；unknown 不进入 0 值平均和免费排行；
  13. 在未安装任何 OpenTelemetry 组件的环境完整执行采集、查询和图表路径；全局检查 Compact/External 默认清单没有 OTel Collector、OTLP Receiver、OTel 数据库或 OTel 领域对象；
  14. 使用无 `usage.read` 用户直接调用 `QueryUsageAnalytics`/`QueryUsageRecords`，确认服务端拒绝；原始 Prompt、Response、Instructions、私有推理、Secret 和完整 Tool 输入输出不进入响应；
  15. 全局检查该流程没有创建 UsageAggregate、DashboardSnapshot、PricingVersion、Budget、Workflow、Plan、WorkUnit、Run 或 Kernel action，也没有因查看 Usage 自动 Pause/Cancel。
- 用户可见预期：用户在一个页面中通过总览、模型与 Token、Agent 效率、Workflow 与 WorkUnit、Runtime 与 Tool、原始明细六个视图理解“消耗发生在哪里、由哪个 Agent/模型产生、是否成功、是否返工、数据是否完整”。每张图都能下钻到精确 UsageRecord 和执行对象；未知数据有清楚覆盖率，不会伪装为零；不同币种、父子 Workflow 和 Token 子集不会重复计算。
- 平台处理预期：RuntimeDriver 从 Provider/Runtime 原生结构化响应产生一次类型化 RuntimeEvent.usage，Runtime SDK Core 补齐 event_id/stream_id/sequence/schema_version，AgentRun Job 原样上传；Platform Core AgentRun event use case 通过短期执行凭证校验 Run、stream/sequence/source identity，补充固定 OAC 归因并幂等写入一个不可变 UsageRecord。`QueryUsageAnalytics` 从原始 UsageRecord 和授权可见的 Workflow/Run/Review/Gate 事实组装瞬时 UsageAnalyticsView，`QueryUsageRecords` 使用 `(observed_at, usage_record_id)` 游标返回原始记录。查询不调用 Runtime、Provider、Workflow Controller、Orchestrator Kernel 或 OpenTelemetry。首版没有 OTel 依赖；未来可选 OTLP Export 也只能异步读取已提交事实。
- 不通过条件：先画图后用日志/Prompt 长度估算数据；同一模型调用重复计量；把 total 与 cache/reasoning 子集相加；unknown 作为 0；复制请求模型冒充实际模型；不同币种直接求和；父子 Workflow rollup 重复；Agent 效率写回 UsageRecord；ResourceRequirements 冒充实际资源使用；前端决定 Scope；返回敏感内容；为图表创建持久化 UsageAggregate/DashboardSnapshot；把 OTel 设为默认依赖、查询来源或 Run 正确性链路；Usage 页面直接控制 Workflow。
- 所需证据：六个视图和联动筛选录屏、字段中文释义、完整/部分/失败/重复 Fixture 的 RuntimeEvent 与 UsageRecord、Provider 字段映射表、Token 互斥桶计算、request/response model 对比、TTFT/coverage、Agent Review/Rework 关联查询、父子 Workflow exclusive/inclusive、Tool/MCP 明细、USD/CNY 与成本来源、QueryUsageAnalytics/Records 响应和游标、越权/脱敏测试、未安装 OTel 的部署清单与成功执行证明，以及数据库/API/CRD 全局检索证明不存在第二套 Usage/预算/控制状态。

#### `UJ-EXTERNAL-AGENT-CLI-001`：Web 与外部 Agent 复用同一 Platform API

- 前置条件：准备一个 human Principal 与一个外部 Agent/service Principal，两者在同一 Scope 对普通 Project/Workflow 查询和创建操作拥有等价权限；另准备一个明确要求 `principal_requirement=human` 的最终验收 Decision、一个无权限服务身份、一个可制造 revision/subject digest 冲突的 Project/Workflow、一个支持持久化事件游标的 Workflow，以及一个由 Execution Host 创建并在 Job 内调用 RuntimeDriver 的内部 AgentRun Fixture。安装 `oactl`，配置 `server_url`（平台服务地址）、`auth_profile`（认证配置名称）、可选 `default_scope_id`（默认治理作用域）、`default_project_id`（默认 Project）和 `output=json`（默认 JSON 输出），认证材料不得以明文写入普通配置。
- 用户操作：
  1. 人类从 Generic Web 打开 Project、Workflow、Run、Artifact、Audit 和 Usage 页面；外部 Agent 使用 `oactl project/workflow/run/artifact/audit/usage ... --output json` 查询同一对象，核对资源 ID、digest、revision、状态、`allowed_actions[]`、blockers 和权限裁剪结果来自同一个 Query API/读模型；
  2. 使用浏览器开发者工具、API Gateway/Platform Core trace 和 CLI `request_id`（请求追踪身份）确认 Web 与 `oactl` 命中同一公开 Platform API operation/schema，没有 `/web/*` 与 `/cli/*` 两套业务 Endpoint；Web BFF 如存在只完成 Session/CSRF/SSR/转发，不拥有领域判断；
  3. 分别通过 Web 表单和 `oactl` 在隔离 Fixture 上提交同一种 ApplicationCommand，例如 Evaluate/Adopt Project Setup、创建 Workflow 或 Pause/Resume；确认两者携带相同业务 DTO，进入同一个 Platform Core Use Case，并产生同类 CommandRecord、AuditEvent、Outbox 或类型化 Workflow Resource Command；
  4. 使用 `oactl --output json` 确认 stdout 是公开 Platform API 响应 DTO，表格模式只是本地渲染；大段 Goal、Decision reason 或结构化参数分别通过 `--file` 和 stdin 提交，结果与 Web 上传/文本输入映射到同一服务端字段，Secret 不出现在命令行参数、stdout、stderr 或日志；
  5. 让 Web 和 CLI 使用同一过期 `expected_revision`、generation 或 subject digest 提交，确认都返回相同结构化 Conflict，至少包含 `code`（错误代码）、`message`（可读说明）、`retryable`（能否按原请求重试）、`hint`（修复建议）和 `request_id`，客户端不得静默重读后覆盖；
  6. 使用无权限 human/service Principal 调用同一 Query/Command，确认服务端 Authorization 拒绝语义一致，前端隐藏按钮不能代替服务端检查，CLI 不因 Agent 身份获得额外管理权限；
  7. 创建一个异步 Workflow 或 Deployment，确认 Web 和 `oactl` 首先只显示/返回 Accepted；Web 通过 Event API/SSE/WebSocket 或轮询观察，`oactl --wait --timeout` 通过同一个持久化事件游标或 Query 条件观察。CLI timeout 只停止本地等待，不取消、重提或改变服务端资源；
  8. 在 Command 已提交但响应丢失后，使用同一 `idempotency_key` 从 CLI 重试，确认返回原资源/Decision/Deployment；同键不同摘要拒绝。revision 冲突不得通过自动生成新幂等键绕过；
  9. 中断 Web/CLI 事件连接，在产生多个新持久化事件后使用原 cursor 恢复，确认两种客户端都从同一事件历史继续，不以浏览器缓存、终端连接状态或 Pod 日志作为权威来源；
  10. 使用外部 Agent/service Principal 尝试提交要求 human Principal 的最终验收 Decision，确认即使命令存在也被拒绝；随后由合格人类通过 Web 或其本人授权的 CLI 身份提交，并形成同一种 Decision；
  11. 检查内部 AgentRun 的 Runtime 环境，确认只注入 run-scoped AgentWorkChannel 和 `oacok work show/guide/read/submit` bootstrap，不注入 `oactl` Platform profile、管理 Token 或任意 Workflow 浏览权限；内部 Agent 的 WorkSubmission 仍只走 AgentWorkChannel；
  12. 全局检查 Web、`oactl`、SolutionPackage 声明式领域体验与第三方 Client 都未直接访问 PostgreSQL、Workflow CR、Controller、Orchestrator Kernel、ComponentRun、AgentRun 或 Kubernetes，也没有创建 CLI/Web 专属领域对象、状态机、Decision 语义或 API Schema。
- 用户可见预期：人类可以在 Web 中完成可视化操作，外部 Agent 可以通过稳定 JSON、Filter/cursor、文件/stdin、结构化错误和可选 `--wait` 完成相同公开 Use Case；两者看到同一个权威对象、同样的 Accepted/收敛区分和同样的权限结果。外部 Agent 明确知道自己是在操作 OAC 平台，不会获得内部 WorkUnit 执行协议；内部执行 Agent 只认识 `oacok`。
- 平台处理预期：公开 Platform API 是唯一北向业务入口；AuthenticationDriver 把 Web/CLI 的认证材料归一化为 AuthenticatedSubject，Governance 通过 ExternalIdentityLink 解析 Principal 并创建 RequestContext，随后统一执行 Authorization、Execution Admission、幂等、CAS、事务、AuditEvent、Outbox 和读模型查询。Web、CLI 与 SolutionPackage 声明式领域体验只做交互适配；Event API 只通知已有新持久化事实，断线后按 cursor 重查。内部 AgentWorkChannel/`oacok` 是独立的 Kernel 协作边界，不复用 `oactl` Platform 身份，也不暴露平台管理 Use Case。
- 不通过条件：Web 与 CLI 使用不同业务 Endpoint、Schema、校验或状态机；CLI 直接写数据库/CRD、调用 Kernel 或拼装 Workflow YAML；Web BFF 拥有业务规则；`oactl --output json` 改写服务端 DTO；CLI 自动覆盖 revision 冲突或 timeout 后重提；服务身份绕过 human Decision 要求；内部 Agent 获得 `oactl` Token 或平台浏览权限；Web/CLI 分别产生不一致 AuditEvent、Decision、Workflow.spec 或读模型；事件连接本身成为业务状态来源。
- 所需证据：Web 网络请求与 `oactl --output json` 对照、公开 API Schema/operation ID、Platform Core trace/request_id、同 Use Case 的 CommandRecord/AuditEvent/Outbox/Workflow Resource Command、Project/Workflow/Decision/Deployment 查询结果、Conflict/Unauthorized/Error JSON、幂等响应丢失重试、`--wait` timeout 与 cursor 恢复记录、human-only Decision 拒绝/成功对照、CLI Context 脱敏结果、内部 AgentRun 环境与 `oacok` bootstrap、BFF/CLI 源码依赖扫描，以及数据库/CRD/API 全局检索证明不存在第二套 Web/CLI 业务模型和直写路径。

#### `UJ-GENERIC-SOLUTION-001`：通过 Generic Web 完整运行非软件行业 Solution 并验证声明式体验回退

- 前置条件：安装并激活一个“调研学习”类外部 SolutionPackage；它直接声明自己的 `project_setup_schema`、`responsibility_slots[]`、`workflow_input_schema`、BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务节点/Gate、Review/返工和用户 Decision，并提供完整本地化展示元数据。准备安全可内联 Artifact、未知媒体类型 Artifact，以及三种领域体验 Fixture：未声明体验、声明兼容且合法的页面布局/Renderer 元数据、声明含不支持字段或渲染失败。
- 用户操作：
  1. 从 Solution Catalog 选择该外部 Solution，查看名称、说明、精确版本、Package 检查和配置要求摘要；
  2. 在 Generic Project Setup 中填写顶层 `project_setup_schema`（Project 长期配置表单）声明的必要参数，查看固定 BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务路径和 Harness 只读说明，并为 `responsibility_slots[]`（责任岗位集合）选择合格 AgentDefinition；
  3. 故意制造一个 Required Agent/Capability 缺口，确认页面给出 blocker 和进入 Settings/Agent Center 的修复入口；补齐后重新检查并原子采用 `solution_setup`（当前 Solution 完整配置）；
  4. 在 Generic New Workflow 中填写固定 Goal 字段和顶层 `workflow_input_schema`（Workflow 行业输入表单）要求的行业参数，不填写 Plan、DAG、WorkUnit 或 LifecycleAction；
  5. 打开 Workflow Operations Center，确认 DAG 节点、责任、Contract、Guide、Harness、Gate、分支选择和终态路径都使用该行业方案的名称与说明，并能下钻到精确输入输出和 digest；
  6. 让 Workflow 完成固定基线、显式 DAG Orchestration、动态执行、独立 Review、Reject/Request Changes、返工复审和用户 Decision，确认通用页面根据服务端 `allowed_actions[]`（当前允许操作集合）提供入口；
  7. 查看 Markdown/纯文本、JSON/YAML、表格、图片或 PDF Artifact 的内置安全渲染；查看未知类型时确认仍显示名称、说明、来源、媒体类型、大小、digest 和授权下载，不执行第三方脚本；
  8. 在 SolutionPackage 未声明领域体验时完成 Workflow，确认所有已选择且可达的 WorkUnit 成功、未选择互斥分支为 Obsolete，随后展示最终 Artifact、证据、Review、Decision、Usage 与 Outcome；
  9. 使用含合法声明式领域体验的精确 SolutionPackage release，确认平台内置 ApplicationHost 解释该 `experience`，领域页面与 Generic Web 读取同一对象并调用同一 Platform Core Use Case，页面始终保留“使用通用视图打开”；
  10. 模拟声明字段不受支持、Renderer 加载失败或精确 release 不兼容，确认直接回到同一 Generic Web，不创建新 Workflow、不改变运行状态、不丢失已提交事实；
  11. 对 SolutionPackage 发布/安装执行 Package 验证，确认不支持的必需表单字段、缺失展示说明、要求私有写接口、依赖私有页面状态、没有安全 Artifact fallback、存在未闭合分支或终态不可达都会阻止发布/激活。
- 用户可见预期：用户无需等待领域专用前端即可使用任意合格 Solution；Generic Web 能完成配置、创建、运行观察、Review、返工、Decision、恢复与结果查看。声明式领域体验只改善术语、布局和渲染，其缺失或故障不会把用户带到空白页，也不会中断正在执行的 Workflow。
- 平台处理预期：SolutionPackage 发布检查验证 Generic Operability；Generic Form Engine 和平台内置 ApplicationHost 只解释支持的声明式 Schema/experience；Workflow 页面使用精确 Solution release 的展示元数据和标准读模型；Artifact Renderer 采用安全 allowlist 与受控下载 fallback；所有动作进入公开 Platform API 和同一 Platform Core Use Case。领域体验不拥有可执行前端 Package、私有状态或第二写路径，fallback 只切换表现层。平台不存在独立 SolutionApplication 产品对象、Package、SDK、interface_api 或安装绑定。
- 不通过条件：必须创建或安装独立 SolutionApplication/第二个体验 Package才可填写必需输入或完成 Workflow；ApplicationHost 被放入 Platform Core 或 ExtensionCatalog；领域体验可以禁用 Generic Web；渲染故障导致 404/空白页、Workflow 暂停或对象迁移；Generic Web 根据机器 ID 猜语义；未知 Artifact 使页面崩溃或执行任意脚本；Web 自行发明 Review/Decision 状态；领域与通用页面写入不同 API、状态或审计事实；Package 检查放过不能在 Generic Web 闭环的 Solution。
- 所需证据：SolutionPackage 与 Package 检查报告、Catalog/Project Setup/New Workflow/Operations Center 截图、Schema 与本地化元数据、Project Setup blocker 与修复记录、Workflow/Plan/WorkUnit/Artifact/Observation/GateResult/Decision/Run/Usage 查询、已知与未知 Artifact 渲染对照、浏览器 CSP/脚本加载记录、Generic 与领域页面 API trace、未声明/合法/字段不支持/渲染故障四组回退结果、Workflow identity/spec/status 前后不变证明，以及 Package/API/Schema/源码检索证明不存在 SolutionApplication、Application SDK、独立 interface_api、安装记录或代码加载入口。

#### `UJ-RESOURCE-LIFECYCLE-001`：归档、恢复和安全删除 Project、逻辑 Agent、终态 Workflow 与历史内容

- 前置条件：准备一个含活动 Workflow 的 Project、一个含活动直接 Deployment 的 Project、一个全部执行已终态的 Project、一个从未形成任何业务引用的空 Project；准备一个已被 Project Team binding、Workflow TeamBindingSnapshot 和 AgentRun 引用的逻辑 Agent，以及一个从未被引用的逻辑 Agent；准备 `Succeeded|Failed|Cancelled|Abandoned` 四种终态 Workflow、一个非终态 Workflow、共享 Artifact、AuditEvent、UsageRecord，并配置一组允许删除和一组受 Retention/Legal Hold 阻塞的治理 Fixture。Web 与 `oactl` 使用具有等价权限的身份。
- 用户操作：
  1. 打开 Agent Catalog，确认默认列表不含已归档项，详情显示服务端返回的 `allowed_actions[]`（当前允许操作集合）、`reference_summary`（引用摘要）和 `deletion_blockers[]`（删除阻塞原因）；归档正在被活动 Workflow 使用的逻辑 Agent；
  2. 确认已创建的 AgentRun 和 Workflow 继续按固定 AgentDefinition 执行，没有自动 Cancel、Abandon、替换 Agent 或改写 TeamBindingSnapshot；同时确认该 Agent 从新 Project Agent Slot 和新 Definition 发布入口隐藏，引用它的 Project 对新建 Workflow 显示 readiness blocker；
  3. 恢复该 Agent，确认它重新进入目录检查；制造 Runtime、Provider 或 MCP 不可用条件，确认恢复本身不会把 Agent 伪装成 ready。条件恢复后无需重新发布原 AgentDefinition 即可再次选择；
  4. 对未被引用 Agent 执行 Delete，确认系统在事务内重新检查引用后立即且不可逆地删除逻辑身份及其未被引用定义，并保留 AuditEvent；对有任一 Project binding、Workflow snapshot、AgentRun 或保留引用的 Agent 执行同样操作，确认整体阻塞并建议归档，不提供强制解绑；
  5. 尝试归档仍有非终态 Workflow 或 `Pending|Running|Unknown|Cancelling` 直接 Deployment 的 Project，确认返回可下钻 blocker；相关执行终态后归档成功，Project 变为只读且不再显示 New Workflow、Promotion 和 Setup 编辑入口，既有 Workflow/Deployment/Artifact/Decision/Usage/Audit 不变；
  6. 恢复 Project，确认系统清除归档标记并重新执行 Project Setup readiness；在 Solution、组件、Agent、Credential 或 Policy 已漂移时继续阻止新建 Workflow，修复并重新检查后才开放创建；既有 Workflow.spec/status 不被修改；
  7. 删除从未创建 Workflow、Deployment、保留 Project Artifact 或其他不可变业务引用的空 Project，确认立即成功且 Audit/tombstone 保留；删除已使用 Project 时确认被引用保护阻塞，不能把历史 Workflow 的 Project identity/revision/configuration snapshot 置空；
  8. 对非终态 Workflow 确认没有 archive；对四种终态 Workflow 执行 archive，确认只从普通历史列表隐藏，精确链接、父子关系、DAG、Outcome、时间线、Artifact、Usage 和 Audit 仍可授权访问，`Workflow.spec/status` 与 Kubernetes 终态资源未改变；
  9. 使用“显示已归档”筛选定位并 restore 终态 Workflow，确认它只返回普通历史列表，不出现 Resume、不重建 CR、不重开 WorkUnit、不重新派发 Run；共享 Artifact 的其他引用仍有效；
  10. 全局确认普通用户没有 Delete Workflow、Artifact、Observation、GateResult、Decision、Plan、Run、UsageRecord 或 AuditEvent；触发 Retention/Legal Hold Fixture，确认治理策略只允许受控载荷清理或明确阻塞，并始终保留最小 identity、digest、Outcome、时间和审计 tombstone；
  11. 分别从 Web 与 `oactl project archive|restore|delete`、`oactl agent archive|restore|delete`、`oactl workflow archive|restore` 操作相同 Fixture，确认两者调用同一公开 Platform API、得到相同 allowed actions、blockers、幂等、CAS、Authorization、Retention/Legal Hold 和 Audit 结果；确认不存在通用 `resource delete`、Recently Deleted、`deletion_scheduled` 或用户删除后台 Workflow。
- 用户可见预期：资源整理遵循清晰且稳定的三条规则——可恢复的长期目录对象先归档；只有从未进入业务历史且没有引用的 Project/Agent 才能立即删除；Workflow 与不可变历史只能归档显示或按治理策略保留。页面始终解释当前为何能做或不能做某项操作，不要求用户猜测 ID 或内部引用。
- 平台处理预期：Project & Team Application Module 负责 Project/Agent 的类型化归档、恢复、引用检查和删除；Workflow History Application 只保存终态 Workflow 的归档可见性元数据；Governance 提供 Authorization、Retention、Legal Hold 和 Audit；Platform Core 在事务内重新校验并写入。Orchestrator Kernel、Workflow Controller、AgentRun Job、RuntimeDriver、Execution Host 和 Kubernetes finalizer 都不判断用户删除资格，活动 Workflow 不因目录归档而变化。
- 不通过条件：引入通用 `disposition` 状态、回收站或 `DeleteResource`；归档 Agent 自动取消活动 Run；归档 Project 改写既有 Workflow；恢复 Workflow 重新运行；删除已被 Workflow/Run 引用的 Project 或 Agent；把 Workflow archive 写入 `spec/status`；允许用户逐条删除不可变历史；只在前端判断引用；Web 与 CLI 走不同接口；删除绕过 CAS、Authorization、Retention、Legal Hold 或 Audit；清理后无法解释原 subject 和 Outcome。
- 所需证据：Project/Agent/Workflow Active 与 Archived 列表截图、详情 allowed actions/reference summary/blockers、Web 与 `oactl --output json` 对照、Platform API operation/schema、Project/Agent CAS 与事务 trace、活动 Workflow/AgentRun 前后对照、Project Setup readiness 重新检查、空资源删除成功与有引用删除失败、Workflow.spec/status/CR digest 前后不变、共享 Artifact 引用、Retention/Legal Hold 拒绝与允许清理记录、AuditEvent/tombstone、数据库与源码全局检索证明不存在通用处置聚合、回收站、DeleteWorkflow 和第二套 Web/CLI 规则。

### 2.1 Solution Definition

- Software Delivery 以 Embedded ComponentBundle 安装，并与第三方 Package 使用同一 Verify、Install、ExtensionCatalog Resolve 和 Package/接口检查路径；
- 用户可安装一个非软件交付 Solution，并通过 Generic Web/API/CLI 完成 Workflow；
- 替换 package-local BaselinePlanTemplate、DAG Orchestration WorkUnit 装配、终态业务路径或普通 Harness Guide 应通过新的 Solution release 完成；真正独立内容通过新的 ContentPackage release 完成，不需要修改 Kernel、Controller、CRD、数据库或 Generic API；
- BaselinePlanTemplate、显式 DAG Orchestration WorkUnit 和终态业务路径是精确 Solution package release 内的 package-local 固定内容。Project Setup 只读展示且不能覆盖；
- Solution 不包含 Intent 白名单、WorkUnit 类型目录、私有状态、任意代码、I/O、LifecycleAction 或 Gate bypass；
- Solution Studio 可以在统一“配置与输入”页面定义顶层 `project_setup_schema`、`responsibility_slots[]` 和 `workflow_input_schema`，再定义 BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务节点/Gate、WorkUnit Contract、HarnessDefinition 和可选声明式领域体验；三个配置值不使用包装聚合，Workflow Schema 不属于 BaselinePlanTemplate，也不提供独立完成合同编辑器；
- 两个 Schema 复用同一个 Generic FormField 合同；字段键和岗位键由系统生成，字段/选项/岗位说明必填，资源类型只保存平台对象身份并自动映射窄类型化 Probe/Policy 端口；作者不声明 Probe Capability 数组，不创建 Schema Version；
- BaselinePlanTemplate 节点分别保存唯一 WorkUnit `execution` 和一个完整 HarnessDefinition；Harness 不保存主执行。Studio 的独立 Review 选项必须映射为 Required `responsibility-run`，不能增加重复 `review_required` 字段或把 Reviewer 建模为可调用 Agent 的 Sensor Extension；
- 内置软件交付 BaselinePlanTemplate 必须把 Solution Design、Technical Overview、Detailed Design 表达为三个顺序固定节点；Architecture Design 只作为页面分组，批准基线阶段不得动态扩展 DetailedDesign DAG；
- 可安装内容只分为 SolutionPackage、ContentPackage、ExtensionPackage；行业定义与声明式领域体验位于 SolutionPackage，并由平台 Web 内置 ApplicationHost 解释；不存在独立 SolutionApplication、Application Package/SDK/interface_api。首版公开执行接口固定为 `harness.oac.dev/v1`、`runtime.oac.dev/v1`、`authentication.oac.dev/v1` 和 `deployment.oac.dev/v1`，Workspace 首版由内部 Port 承接；
- 岗位提示不构成 DAG Orchestrator 白名单或授权源；动态计划可以提出新责任，Plan Compiler 只校验要求是否合法，当前无 Executor 时 Plan 仍可激活，节点 Ready 后进入 Waiting 与显式执行器绑定流程；
- BaselinePlanTemplate 在 Workflow 创建时经同一 Plan Compiler 静态校验为 fixed-baseline Plan，不伪造 DAG Orchestrator/Review/Gate；动态交付由显式 DAG Orchestration WorkUnit 使用 PlanDraft、Compiler、Review、Gate 和 Plan 模型；
- PlanNode 可使用仅引用已有 Gate/Decision Requirement 与固定 outcome 的类型化 `activation` 表达互斥业务分支；条件未决保持 Pending，匹配后正常计算 Ready，未选择分支进入 Obsolete。不得引入脚本、自由表达式或由 Kernel 理解行业语义；
- Capability Label 只表达稳定能力，不表达首次执行、返工或补救情形。内置 Architect 的 `architecture-design` 必须同时覆盖首次设计、设计返工和最终验收后的技术变更设计，不得要求额外 rework/change-impact Capability；
- Generic Web 是每个可激活 Solution 的强制可运行基线；缺失、无效、故障或不兼容的声明式领域体验都不得影响完整 Workflow，也不能取消通用入口或引入通用 API 无法表达的必需状态与写路径。

### 2.2 Project 与 Agent Team

- Project 是多个 Workflow 的长期工作与治理边界，不等同 Repository、Namespace、Organization 或 Tenant；
- 创建基础 Project 时只收集 `display.name`（Project 名称）、`display.description`（Project 说明）和条件性的 Owner Scope 选择；Project ID、CAS `revision`（内部修订号）、`configuration_digest`（当前配置摘要）、状态与审计字段由系统、上下文或 Policy 产生；
- 用户从 Solution Catalog 选择逻辑 Solution；只有唯一合格 package release 或 Policy 推荐的精确 release 可以默认选中，多个无推荐 release 时必须明确选择，不允许 `latest` 或 SemVer 排序推断；
- 基础 Project 创建后可以暂时没有 `solution_setup`；但原子采用完整配置前，Solution 要求的必需岗位必须由 `Project.solution_setup.team_bindings[]` 显式解析到候选 AgentDefinition，系统不生成默认团队；
- AgentTemplate package release 与 AgentDefinition 都直接保存 `language + content + digest` 的稳定 Instructions，不要求用户先创建 Artifact ID；
- Agent Center 可以从空白或 AgentTemplate 创建不可变 AgentDefinition；从模板创建时必须自动完整继承模板固定的全部 SkillPackage release，普通流程只允许按需追加企业 Skill，不要求用户重新选择模板 Skill；
- 删除模板继承 Skill 只能进入显式高级自定义流程，必须显示影响说明并记录 AuditEvent；模板 Skill 缺失、digest 不一致或同一 Skill 稳定身份出现多个冲突 release 时，整个发布失败且不得形成部分 AgentDefinition；
- 模型通过一个按 Provider 分组和筛选的统一选择器直接选择 Provider/Model 组合，不设置“先选 Provider”必经步骤；`fixed` 固定一个组合，`ordered-fallback` 保存可跨 Provider 的有序组合；
- 模型展示 Label 只用于阅读；AgentDefinition 必须保存 `model_provider_connection_id`（模型 Provider 连接稳定身份）和 `external_model_id`（Provider 实际模型名称），可以另存 `selected_from_model_catalog_snapshot_id`（选择时目录快照身份）作为来源证明，但不得把快照身份当作模型身份，也不得保存或解析 `provider.model` 等拼接 Key；
- AgentDefinition 不含通用 Tool Binding 字段或无类型 Credential 列表；MCP 只保存稳定 `mcp_server_definition_id`，Runtime 自带 Tool 首版不建立独立 Tool Catalog，Credential 只出现在 Model Provider、MCP、Repository、Deployment 等明确用途配置中；
- Project Setup 根据 Solution 元数据只收集必须由用户决定的直接 Repository URI、固定 Harness 所需外部系统参数、Agent 岗位和条件性外部目标；Project Rules 在 Workflow 批准基线阶段形成；固定核心定义、Harness 装配、平台托管 Preview 与默认 Runtime/Workspace/Governance Policy 只读展示；私有 Repository 每个 Binding 只选择一个 CredentialBinding，运行时再按操作签发最小权限 lease；替换 Harness Extension 必须形成新的 package-local HarnessDefinition ID/config digest 并发布新的 Solution release；
- `Project.solution_setup` 不保存 `harness_extension_bindings[]`；Workflow Resolver 只能从精确 Solution release 所包含的 HarnessDefinition 集合解析 Extension Binding 并固定到 LockedComponentSet，ValidatedPlan 只能引用该集合内的 Harness；
- Setup 完成前选择的 Solution 只作为显式 Query/Application Context，不保存 `selected_solution` 或半成品绑定；离开后 Project 保持 `solution_setup` 为空并派生显示“未配置”；
- `EvaluateProjectSolutionSetup` 直接接收未保存的完整 ProjectSetupCandidate，返回带 `candidate_digest`、五组 checks、blockers、warnings 和内联 Package 解析摘要的瞬时 ProjectSetupEvaluation；不创建 Draft、Project 版本或持久化 Preview；
- 输入变化后旧 Evaluation 必须标记为过期；Required 外部状态 Unknown/超时必须阻塞；保存与创建 Workflow 都重新校验；
- `Project.solution_setup` 以一个 CAS 命令原子采用精确 Solution release、规范化 Project 参数、Repository/环境/外部目标绑定、责任到候选 AgentDefinition 的绑定和允许的 Policy 收紧项；缺失只表示配置未完成，不增加 Project 状态机；
- 根 Workflow 创建时把来源 Project ID/revision/digest、规范化 Project 配置、初始 TeamBindingSnapshot、精确 Solution release、LockedComponentSet、OrchestrationDefinition 和已解析能力配置复制到 `Workflow.spec` 的不可变创建时快照字段；后续 Project、Provider、MCP 或 Agent 修改不静默影响活动 Workflow；
- 既有目标采用新 Project 配置、Solution、Harness/Policy 或 Component 基线时，必须经过影响分析与 Decision 创建具有新 identity 和 `predecessorWorkflowName` 的后继根 Workflow；仅为既有合法 WorkUnit 补充合格 Agent 时使用 Workflow-local Answer Decision，不创建后继。原 Workflow 的 `spec/status`、Active Plan、WorkUnit、GateResult 和审批保持不变；
- Product/Requirement、DAG Orchestrator、Developer、Reviewer 等责任绑定逻辑 Agent，而不是绑定 Runtime 厂商；
- Codex 与 Hermes 首版均可承担任意责任，选择由用户候选池、能力和资源要求决定；
- AgentTemplate 实例化为不可变 AgentDefinition，模板更新不静默改变既有 AgentDefinition 或活动 Workflow。

### 2.3 Unified Settings 与 Agent Capability Catalog

- Web 在同一 Settings Shell 下提供 Skills、Model Providers、MCP 和 Credentials 四个独立路由；统一入口不产生万能 Settings 聚合、表或写接口；
- Settings 展示能力缺口而不是强制四类配置；内置 AgentTemplate/Skill 与当前 Scope 已有模型目录满足要求时，用户可直接进入 Agent Center；
- 领域对象必需字段与用户表单字段有明确生产责任；Scope、Project、稳定 ID、package SemVer 或配置 revision、digest、状态、审计信息和策略默认值不得伪装成普通用户必填项；
- GitHub Skill Import 只要求 Repository URI；revision 和子目录可选，缺省 revision 使用 Repository 默认分支，并在发布前固定精确 Commit SHA；
- Skill 名称、说明、版本和入口优先从 Manifest/Frontmatter 与默认 `SKILL.md` 解析，只有缺失或歧义时才要求用户补充；
- Local Skill Upload 校验大小、文件数量、归档展开、路径逃逸、符号链接、入口文件、Frontmatter、完整文件树和 digest；导入过程中不执行任何包内代码；
- 同一 Skill package ID/SemVer 不允许对应不同 digest；更新产生并排 SkillPackage release，Agent 固定精确 `ExactComponentRef<SkillPackage>`；
- Model Provider 普通表单只要求名称、说明、API Type、Base URI、条件性的 CredentialBinding 和可选非敏感 Header；
- ModelProviderConnection 是稳定可编辑配置，使用内部 `revision + configuration_digest` 做 CAS、审计和 Workflow 快照；它不保存发现模式、模型列表路径、手工模型列表或普通连接策略，API Type Connector 负责发现端点、分页和响应解析，平台/Scope Policy 负责 TLS、超时、代理、重试与出站限制；
- `DiscoverModels` 成功产生不可变 ModelCatalogSnapshot；失败、空目录或缺少模型时可直接执行 `AddManualModel`，无需切换配置模式；模型来源 `discovered`、`manual`、`mixed` 由系统推导；
- Send Test Message 使用精确 API Type 和模型，结果包含成功、时延与脱敏错误，API Key/Header 不进入结果、日志或前端缓存；
- Agent Center 只能从已发布 `ModelCatalogSnapshot` 选择模型，并在一个按 Provider 分组/筛选的选择器中直接选择 Provider/Model 组合；创建根 Workflow 时固定所引用 ModelProviderConnection 的实际 revision/digest/非敏感配置，创建 AgentRun 时再固定本次选择的 external model ID 与 API Type；
- MCP 新建支持 `stdio` 和 `streamable-http`，并可导入常见 `mcpServers` JSON；内联 Secret 必须迁移到 CredentialBinding 后才能发布；
- McpServerDefinition 是稳定可编辑配置，使用内部 `revision + configuration_digest` 做 CAS、审计和 Workflow 快照；AgentDefinition 只保存稳定 Server ID，运行时不得重新解析为更新后的配置；
- MCP `working_directory` 缺省为 Workspace 根目录，连接策略来自平台/Scope Policy；只有存在敏感 Header/Environment 时才要求 CredentialBinding；
- MCP Test 完成 initialize，并在支持时读取 tools/resources/prompts 形成 Capability Snapshot；Snapshot 不成为逐 Tool 权限表；
- `stdio` MCP 只在 Runtime/Workspace 内执行，不能在 Platform Core/Web 宿主机启动；RuntimeDriver 不支持配置所需 API Type/Transport 时 AgentRun 创建失败；
- 普通 Credential 表单只收集名称、说明、类型、条件性 Secret、类型专用非敏感元数据和条件性到期时间；Scope/Project 从上下文继承，用途/subject 从创建目标推导，handle/fingerprint/status/revision 由内部 CredentialStore 或系统生成；首版没有存储后端选择、CredentialPolicy 或 `rotation_policy` 字段；
- Settings Facade 只聚合 Read Model，Skill 写入由 Extension Management、模型/MCP 写入由 Agent Capability Catalog、Credential 写入由 Governance 各自承接。

### 2.4 Workflow Platform Core

- Workflow/WorkUnit 是唯一产品控制状态；Software Delivery 的 Delivery/Task 只是领域视图；
- 活动 Workflow 以 `Workflow` CRD 为权威控制对象，`spec/status` 的每个字段都有明确语义拥有者；所有变更通过类型化 Workflow Resource Command 提交，由 Platform Core 使用对应 field manager 作为唯一物理写入者应用；PostgreSQL 不维护竞争的活动状态机；
- 每个 WorkUnit 使用一个 tagged `execution`：Executor 或 Child Workflow，不能二者同时存在；主执行只存在于 `execution`，HarnessDefinition 不保存或投影 Executor；
- WorkUnit 状态词汇固定为 `Pending|Ready|InProgress|InVerification|VerificationFailed|InReview|ReviewRejected|NitsRequired|InRework|Waiting|Cancelling|Succeeded|Failed|Cancelled|Abandoned|Obsolete`；不得用 Run role、purpose、布尔字段或展示状态拼装第二套状态；
- 上游 Required Gate 未通过时，下游硬依赖 WorkUnit 不进入 Ready；
- ChildWorkflowLink ID 从父 Workflow、父 WorkUnit 和 iteration 确定性派生，与子 Workflow 幂等创建；Link 只作为嵌入引用与审计事实，不创建独立表、聚合或状态机，子 Workflow 独占自己的状态；
- 父级只消费结构化 WorkflowOutcome，再经过父 WorkUnit Gate；
- 首版父子 Workflow 必须同 Project、单一拥有者、默认最大深度 8；Pause/Resume/Cancel 沿 causation 传播；
- 终态 WorkUnit 不重新打开；后续缺陷只能由后续 Plan 或当前 Plan 预声明分支中的 Remediation/Replacement/Technical Change WorkUnit 承接。
- 每个 Decision 始终绑定一个精确 subject；一个用户动作可以通过 `SubmitDecisionsAtomically` 原子创建多条普通 Decision，但不得创建 DecisionBundle/DecisionSet 或让 Gate 观察部分结果；
- Question Request 是 Observation，Answer 是 Decision；Answer 可以让 Waiting WorkUnit 在同一 iteration/Lineage 中继续，只有正式提交结果被打回才进入 InRework；

### 2.5 Orchestrator Kernel

- Kernel 可作为独立库运行，不依赖 Open Agent Cluster、Agent、Runtime、Workspace、Kubernetes、数据库或 ExtensionPackage 类型；
- 正式模块名固定为 Orchestrator Kernel，`oacok` 只表示随模块交付的 Agent 协作 CLI；
- Kernel 验证必须同时证明三个核心能力：DAG 生成与 Harness 推进、AgentWorkChannel/`oacok` 协作协议、同一 WorkUnit 上 Producer/Reviewer/返工/Agent handoff 的连续任务线；
- 普通消费者使用 `registerExecutor + advance/runUntilBlocked` 深层 Facade，不需要手工串联 Compiler 或实现完整 OrchestrationHostPort；
- `WorkflowInput.kind=plan|goal|document` 显式区分已有 DAG、自然语言无图启动和已有文档无图启动；OAC 根 Workflow 使用 `plan`；`goal`/`document` 只在调用方明确请求且 Host 有兼容 `orchestration.dag` Executor 时成立，并且必须先形成经同一 Plan Compiler 校验的单节点 bootstrap Plan；
- `goal-bootstrap` / `document-bootstrap` Plan 的唯一节点、PlanDraft 合同、四象限 Harness 和 provenance 必须由 WorkflowInput/OrchestrationDefinition 确定性派生；文档模式必须按 resource key 与 digest 读取 InputResource；不得运行隐藏的图外 DAG 生成回调、依赖隐藏宿主绑定或在编译失败后留下部分 Active Plan；
- 相同显式输入产生相同 Executor 匹配、InvocationRequest、PlanningRules、PlanDiagnostic、Plan 和 LifecycleAction；
- 时间、随机数、资源可用性和外部状态都作为显式输入；
- Workflow 创建时由外层 Resolver 实例化 BaselinePlanTemplate，并使用同一 Plan Compiler 静态校验 fixed-baseline Plan；Kernel Definition 不包含模板或行业 Guide；
- OrchestrationContext/InvocationRequest 只为显式 DAG Orchestration WorkUnit 生成，并且只携带当前节点 Guide、显式方法资源、typed input bindings 解析的业务上下文和反馈；所选 Executor 的稳定规划方法与 Skills 由 OAC 在 Kernel 匹配后通过精确 AgentDefinition 的 RuntimeInstructionSet/RuntimeRequest 单独物化，不进入 Kernel DTO；
- InvocationRequest 是有界控制信封：`show` 返回固定目标、合同、typed resource/subject identity、digest 与受限结构化摘要，`guide/read` 按这些精确值读取既有不可变正文；输入正文、较大的 Guide/Contract/Review evidence、日志、代码和二进制内容不进入 Workflow CR。Schema/整体大小超限必须在 Dispatch 前写入 `OrchestrationDecision.diagnostics[].code=InvocationRequestTooLarge`，不创建 Run，并使 WorkUnit `Waiting`、`blocking_cause.kind=invocation-request-too-large`、Workflow `Blocked=True`；不得自动重试、静默截断、读取 latest 或增加通用 payload-ref，且缺少显式补救/重新编排路径时必须作为 Plan/Solution 缺陷暴露；
- Workflow CR `runs[]` 必须保持有界：非终态、active 和当前 iteration 的 Gate/返工所需 Run 保留完整有界请求；Run 创建先持久化相同 request/history，再通过 Outbox 写 CR；终态报告已处理且完整 request/结构化结果进入不可变历史后，Controller 只通过内部 `CompactTerminalRunSummaries` 提交可压缩 Run ID 与预期 digest/终态/handled sequence，Platform Core 验证历史并 CAS 压缩为固定终态摘要。压缩前后历史查询返回同一 request digest 和完整旧请求，NotReady/Conflict 只保留原条目，活动 Run/当前依赖不能被压缩，Controller 不能从历史存储回读并推进状态；
- 活动 Plan 无可推进动作但仍有已选择且可达的 WorkUnit 未成功时，Kernel 不创建隐藏 DAG Orchestration；缺少显式 DAG Orchestration/Remediation 路径时返回 Waiting/Failed 设计缺口；
- 只有活动 DAG 中全部已选择且可达的 WorkUnit 都为 Succeeded、互斥未选择分支都为 Obsolete，且不存在非终态、Waiting、Unknown 或 Cancelling 的 WorkUnit、Run、Child Workflow 时，Workflow 才能 Succeeded；失败、取消或放弃节点必须收敛为相应非成功 Outcome；
- Plan Compiler 对 fixed-baseline 草案允许 BasePlan 为空；显式 DAG Orchestration WorkUnit 的 `compilePlan(PlanningRules, BasePlan, PlanDraft)` 必须使用当前 Active Plan，不增加重新包装权威输入的 `PlanningCompilationInput`；
- PlanDraft 被视为不可信输入，Schema、DAG、依赖、可选择分支闭合、终态可达性、权限和收敛检查未通过时不产生部分 Plan；
- LifecycleAction 的稳定业务 identity/digest 不包含 expected state 或 resourceVersion；Host 应用时另传 CAS 条件，冲突不改变动作身份；
- `advance/reconcileOnce` 与 `runUntilBlocked` 使用同一 Compiler、Executor Matcher、Input Resolver、InvocationRequest Assembler、Submission Validator 和 Lifecycle Core，并产生等价结果；
- AgentWorkChannel 的 show/guide/read/submit 与 `oacok` 命令一一对应；通用协作与 metadata 字段说明由 Kernel 提供，行业 Guide 来自当前固定内容；
- Agent WorkSubmission 终态 conclusion、Reviewer 完整 ReviewResult、返工反馈和显式 handoff 必须形成可重放的连续任务线，不以隐藏 Session 为正确性来源；
- controller-runtime 只提供 Watch/WorkQueue 等机械能力；WorkflowReconciler 通过 WorkflowOrchestrationHost 调用 Kernel，Host 不复制 Plan、Executor 匹配、input 解析、InvocationRequest、Ready、Gate、Rework 或 convergence 规则。

### 2.6 DAG Orchestration WorkUnit

- DAG Orchestration 使用普通 WorkUnit、AgentRun、Lineage、Artifact、ReviewResult 和 GateResult；
- DAG Orchestration WorkUnit 必须由已校验并激活的当前 Plan 显式声明；类型化 GateResult/Decision 只能满足 Plan 中已有节点的 `activation`，Plan 耗尽和 `activePlanId` 不触发 DAG authoring；
- 该 WorkUnit 的 `execution.kind=executor` 且能力要求包含 `orchestration.dag`；Kernel Runtime 从 HostCapabilitiesSnapshot 匹配逻辑 Executor；
- `input_bindings[]` 只把当前输入槽位映射到依赖闭包中的上游输出槽位；派发前解析为已确认的精确 ID/digest，缺失、歧义、类型不匹配或未确认均阻塞；
- DAG Orchestrator 通过当前 InvocationRequest 和 `oacok` 产生 PlanDraft，编译失败时进入 `VerificationFailed → InRework|Waiting|Failed`，诊断进入下一轮 InvocationRequest feedback 且不启动 Reviewer；
- 编译通过后产生 ValidatedPlan，由独立 Reviewer 检查完整性、粒度、依赖、风险和可验证性；
- Planning Gate 可以 Pass、Rework、RequestDecision 或 Fail；
- Review Reject 进入 `ReviewRejected` 后再返工，不能返回 `Ready`；
- Rework 优先尝试恢复 DAG Orchestrator 自己的兼容 Session，Reviewer 复审也只能恢复 Reviewer 自己的兼容 Session；
- 显式 DAG Orchestration WorkUnit 产生的动态 Plan 只有在 Planning Gate Pass 后才形成；Platform Core 在同一数据库事务中保存 Plan、Audit、Command 与 Outbox，再由 Outbox CAS 更新 Workflow `activePlanId`。CAS 冲突时 Plan 保留为未激活历史候选。

### 2.7 Harness 与 ExtensionPackage

- 每个完整 Solution 声明 Computational/Inferential Guide 与 Sensor 的需要和合理省略；
- Sensor 只追加 Observation，不能直接推进状态；
- Harness ExtensionPackage 可以提供 Harness Guide/Sensor、WorkUnit Executor、Trigger 和 EventHandler；Executor Extension Point 位于 HarnessDefinition 外；
- 一个 WorkUnit 只有一个主 `execution`；Child Workflow 不绑定 Harness Extension Executor；
- `agent.invoke` 仅允许当前 `execution` 选中的 Extension-backed Executor 在当前 Workflow/WorkUnit/iteration/ComponentRun/subject 范围内调用，并由 Platform Core 完成 Agent 选择、授权与 Admission；一次 ComponentRun invocation 最多创建一个 AgentRun。Guide、Sensor、Trigger 和 EventHandler 不得调用；
- Required Extension 失败显式阻塞，Optional 跳过产生结构化 Observation；
- 副作用 Executor 支持 execute、observe、cancel；结果 Unknown 时先观察外部系统，不能盲目重试；
- Package Disable、Revoke、Upgrade 和 Builtin Parity 均运行 Package/接口检查；
- HarnessDefinition 与 ExtensionPackage 明确分离：前者只装配四象限、Gate 和返工/收敛，后者实现 Harness SDK；相同精确 ExtensionPackage release、artifact digest 和 config digest 必须得到相同解析语义。
- Harness 编辑、不可变详情和 WorkUnit 运行追踪使用同一生命周期顺序与字段集合；阶段从 Binding/Extension Point 推导，详情只读，运行追踪只叠加实际状态；不得创建独立 Harness 预览对象或第二套详情 Schema。

### 2.8 Agent Execution 与 Workspace

- Codex、Hermes 通过各自 RuntimeDriver 运行同一逻辑 Agent Contract；Hermes 的 Reviewer、Architect、PM 等通过不可变 AgentDefinition 参数化同一 Runtime；
- Lineage 表示同一 WorkUnit/责任/任务范围内的责任时间线，不绑定固定 Agent；显式 Agent 交接可以保持 Lineage，但每个 Run 固定自己的 AgentDefinition、解析后的 RuntimeBinding、Session 和 Workspace；
- 只有相同 AgentDefinition、兼容 RuntimeBinding 和相同安全上下文才允许恢复 Session；AgentDefinition 或 RuntimeBinding 改变时默认新建 Session，并用结构化历史、Artifact 与 Decision 恢复任务上下文；
- Developer 与 Reviewer 即使使用相同 Agent 和 Runtime，也必须保持不同责任时间线、首次新 Session 和隔离 Workspace；
- Runtime 以 YOLO、bypass 或 never-confirm 模式运行，不创建并发人工工具授权队列；
- 每个 AgentRun 固定一个不可变 InvocationRequest；页面和 `oacok work show` 返回同一 request digest；WorkSubmission 必须匹配 invocation/iteration/output contract；
- Platform Core AgentRun 创建用例将精确 AgentDefinition Instructions、`oacok` bootstrap 和当前 InvocationRequest 明确选择的可选 Project Rules 规范化为 RuntimeInstructionSet，Execution Host 再把它与 run-scoped AgentWorkChannel binding 分层挂载给 AgentRun Job；公共 Runtime SDK 不暴露 OAC 类型、Platform API 或厂商指令文件路径；
- RuntimeDriver 优先使用原生 developer/system instruction，只有缺少原生接口时才使用 Repository 外只读临时文件；AgentRun 前后 Repository `AGENTS.md`/`CLAUDE.md` digest 不变，临时文件在结束后清理；
- AgentTemplate 稳定 Instructions、阶段 Inferential Guide、InvocationRequest 动态事实、Skills 和 Credential/Runtime 参数分层处理，不允许 Secret 进入请求；Prompt 指令不替代 Authorization、Admission、Tool Permission、Contract、Sensor、Gate 或 Submission Validator；
- Producer 终态 conclusion 必须持久化为 run-conclusion Observation；Reviewer Reject 必须保存 verdict、summary、非空 findings、nits 和 evidence；返工 Agent 与显式接手的新 Agent 必须在新 Session 中读取这些结构化交接记录；
- AgentRun Job 对每个 AgentRun 只调用一次 RuntimeDriver.Execute；Runtime SDK Core 生成 event_id、stream_id、严格递增 sequence 和 schema_version，AgentRunEvent 原样保留这些字段；关键尾事件持久化确认后 AgentRun 才可终结，Job 进程重执行必须创建新 AgentRun/new stream；
- 默认 Pod/PVC Workspace 实现通过内部 WorkspacePort 完成首版能力；Kubernetes Agent Sandbox 若进入系统发行版，也通过相同内部合同接入；
- RuntimeDriver 或 Workspace 实现无法满足 Required 隔离、网络或放置能力时 Fail Closed。

### 2.9 Governance

- 首个本地管理员只能使用安装器生成的一次性 Bootstrap Code 创建；初始化必须原子创建 Principal、唯一 Local Scope 与管理员授权并消费 Code，不能由首个访问者自动领取；
- 首版关闭公开自助注册；普通人类账号通过管理员 Invitation 创建并由用户自行设置密码，Invitation 已表达准入，不形成 Pending Approval 或第二次审批；
- Notification Adapter 可选；没有 SMTP 时只展示一次可复制 Invitation/Recovery 链接或 Code，服务端只保存 Token 摘要；
- LocalUserInvitation、Browser Session 和 LocalRecoveryChallenge 是短期认证支持记录，不创建 Version、Workflow、WorkUnit 或产品状态机；
- 唯一授权 Local Scope 登录后自动进入；未来多个 Scope 必须显式选择，Session 中的选择不能替代每次请求 Authorization；
- Logout 必须服务端撤销 Session；Recovery 不允许管理员或 Installer 代填新密码，完成后撤销既有 Session；
- 每个外部请求与内部 ApplicationCommand 携带 RequestContext、PrincipalIdentity、ScopeIdentity、request_id 和 causation_id；
- AuthenticationDriver 在 Principal/Scope 建立前按平台或未来显式 organization/realm 认证入口解析；认证后固定执行 OAC 唯一 AuthorizationEngine 和当前 Scope 的 AuthorizationPolicy，执行创建固定执行 OAC 唯一 ExecutionAdmissionEngine。可选 AuditExportTarget 使用内部 AuditExportPort，Credential 使用内置 CredentialStore；首版不存在 CredentialPolicy、凭证后端选择或跨后端迁移。Authorization 记录 Policy provenance，Execution Admission 记录 rules/facts provenance，Authentication 记录精确 Driver release/digest；治理配置不进入 Workflow LockedComponentSet，也不创建版本化治理绑定聚合；
- Authentication、统一 AuthorizationEngine、统一 ExecutionAdmissionEngine、Audit、Credential 和 Execution Security 使用独立强制边界；两个内置引擎的内部接口都不是扩展点；
- Policy/授权事实或必需准入事实无法确认，以及外部系统错误、超时时默认拒绝，不允许 Extension 绕过强制链路；
- 状态变更与本地 AuditEvent 原子提交或使用 transactional outbox；外部审计接收端故障不丢失本地审计；
- 一个统一 Audit Explorer 通过 `QueryAuditEvents`、`GetAuditEventDetail` 和有界 `ExportAuditEvents` 查询本地 AuditEvent；按 Scope/Project 服务端授权、字段脱敏和稳定游标执行，不为治理类别建立第二套模型；
- 普通成功的审计读取不递归写 AuditEvent；拒绝、导出和受控高敏感元数据展示必须审计；查询不调用 Workflow Controller、Orchestrator Kernel 或外部审计接收端；
- RuntimeDriver/Runtime SDK/AgentRun Job/Platform Core AgentRun event use case 以一次来源事件一个 tagged UsageRecord 的方式采集 model/tool/runtime Usage，缺失字段保持 unknown；Prompt/Response/Secret 不进入 Usage；
- 一个统一 Usage Explorer 通过 `QueryUsageAnalytics` 和 `QueryUsageRecords` 展示六个固定丰富视图、图表联动、覆盖率和原始下钻；Token 子集、父子 Workflow 与币种不重复聚合；
- Agent 效率只在查询时关联 Workflow/Run/Review/Gate 事实，不写回 UsageRecord；首版无 Budget、自动控制和 OpenTelemetry 依赖；
- CredentialBroker 不支持 Secret 枚举，明文不进入 Artifact、Guide、Event、Observation 或日志；
- Credential 创建页面只接受一次 Secret 输入，保存后只返回 fingerprint、状态、用途、过期和引用位置；查询 API 不能取回原值；
- CredentialBinding 支持 Scope、可选 Project、allowed usages、subject constraints、可选过期、CAS revision 和 active/disabled/revoked/expired 单一状态，不保存每 Binding rotation policy；
- Secret 轮换保持 CredentialBinding ID 稳定，内部 CredentialStore 创建新材料版本、验证后切换并审计，Model Provider/MCP/Repository 配置不需要逐个重建；内置 Store 默认就绪，首版没有后端选择、迁移或回退；
- GitHub App 是组织自动化默认 Profile：Private Key 只保存在内部 CredentialStore，实际调用按需签发短期 Installation Token；Fine-grained PAT、GitHub App User Token 与 SSH Deploy Key 按适用场景支持；
- 私有 GitHub Skill 导入和 Repository 写入都必须校验 CredentialBinding 的用途与 Repository subject；Reviewer/只读 Sensor 不能使用 Developer 写凭证；
- Usage 可按 Scope、Project、Workflow、WorkUnit、Agent、Runtime、模型和时间查询；首版不实现预算或自动暂停；
- Project 可作为 Controller 分区键，单 Active 演进到多 Active 不改变 Authorization、Execution Admission、Audit、幂等和恢复语义。

## 3. 内置软件交付验收

### 3.1 黄金案例

输入一个网页版贪吃蛇需求，至少包含登录、积分、战绩排名和用户粘性功能。用户只提供需求和必要业务 Decision，不负责设计内部 Task、Repository 分支或技术实现细节。

系统必须完成：

1. Workflow 创建时由 SoftwareDeliveryBaselinePlanTemplate 经 Plan Compiler 静态校验形成的 `fixed-baseline` Plan A，包含显式 Delivery DAG Orchestration WorkUnit，且不伪造 initial DAG Orchestrator/Plan Review；
2. 通过 Question Observation/Answer Decision 完成澄清，并由一次用户操作原子批准、两条单主体 Decision 分别绑定的 Requirement Artifact 与 Acceptance Criteria Artifact；
3. Architecture Design 页面阶段分组下，按固定顺序分别完成程序检查、独立 Review、用户 Decision 和必要返工的 Solution Design、Technical Overview、Detailed Design 三个 WorkUnit；
4. 三份设计分别形成精确 Artifact；Detailed Design 可以包含多个章节、图和附件，但不在固定基线中展开动态 DetailedDesign DAG；
5. 基于已批准 Requirement、Acceptance Criteria 和 Design，经过程序检查、独立 Review 与用户 Decision 的用户级端到端 Acceptance Plan Artifact；它不包含开发单元测试、组件测试或实现内部集成测试；
6. Project Rules Artifact 与仓库根 AGENTS.md 受管区块；
7. Project Rules 通过后，显式 Delivery DAG Orchestration WorkUnit 通过 typed input bindings 消费批准基线，Kernel Runtime 匹配 `orchestration.dag` Executor 并组装 InvocationRequest；DAG Orchestrator 使用其稳定 Instructions/Skills、PlanDraft Contract、PlanningRules、普通 Harness Guide 和精确输入生成经编译、独立 Review 与 Planning Gate 的实现 Plan B；Plan B 以 `parent_plan_id` 延续 Plan A 并扩展同一 DAG；
8. Task 开发、程序验证、独立 Review、返工和串行 Integration；
9. 不可变 Release；
10. 当前集群 Preview、按批准 Acceptance Plan 执行的自动验收和用户最终验收；
11. 用户最终 approve 后，最终验收 Gate 通过；当活动 DAG 的全部已选择且可达节点成功、未选择互斥分支为 Obsolete 且不存在未决执行时，Kernel 按固定收敛规则把原 Workflow 置为 `Succeeded`，同一 Release 显示为 `Promotable`；
12. 用户可以立即或稍后从 Release 页面选择当前 Project 已配置的标准 Kubernetes 或用户 CI/CD 目标并发起独立 `StartPromotion`；该可选直接操作不改写源 Workflow，并保留完整 Deployment、Audit、Credential 使用和外部引用。

### 3.2 Review 与返工

- Requirement Baseline：内置方案默认没有 Required Reviewer；程序检查通过后等待用户一次批准当前 Requirement + Acceptance Criteria 组合，不得直接 Succeeded；
- Requirement Baseline：Request Changes 可以只指向一份 Artifact，但不形成部分批准；变化成果使用新 ID/digest，未变化成果可以复用，当前组合必须重新原子批准；
- Design：Architecture Design 只是三个固定顺序 WorkUnit 的页面分组；每份成果单独检查、Review 和批准，不创建同名 WorkUnit/Artifact 或动态 DetailedDesign 基线 DAG；
- Design：Reviewer Pass 后仍须等待用户 Decision；用户 Approve 后 WorkUnit 才能 Succeeded；
- Design：Reviewer Reject 或用户 Request Changes 都使 Architect 进入 `InRework`，返工后形成具有新 Artifact ID/digest 的不可变 Design Artifact，并必须重新检查和 Review；
- Design：用户可以在意见中定位章节，但批准始终针对当前完整 Artifact ID/digest；页面不提供章节级部分批准或通用 Reject；
- Acceptance Plan：必须依赖已批准 Requirement、Acceptance Criteria 和 Design，只覆盖用户级端到端业务验收，并经过独立 Review 与用户 Decision；
- Acceptance Plan：Reviewer 或用户打回后，Acceptance Planning 责任 Agent 沿自己的 Lineage 返工，旧 ReviewResult 和 Decision 不能批准新的 Acceptance Plan Artifact ID/digest；
- reject：Developer 在原 Lineage 返工，修复后 Reviewer 在自己的 Lineage 复审；
- pass-with-nits：只允许一次有限修复，不再二次 Review，但必须重跑完整程序验证；
- pass-with-nits 不适用于范围、Contract、安全、数据或程序失败；
- Task 拆分、依赖或范围变化必须重新 Planning；
- Agent 自报修复不构成通过证据。

### 3.3 Project Rules 与 Agent Template

- 用户对当前精确 ProjectRules Artifact 只批准一次；批准后自动触发 Git Executor，不再提供第二个“应用到 AGENTS.md”确认；
- AGENTS.md 不存在、无受管区块、已有合法区块三种路径都能确定性处理；
- 标记缺失、重复、倒序、嵌套或 base version 已变化时不修改仓库；
- 区块外内容保持不变，相同输入重复执行幂等；
- Git Sensor 确认 commit、完整文件 digest 和受管区块 digest；
- Platform Core 把已批准 Project Rules Artifact 的精确内容固定到 RuntimeInstructionSet，Execution Host 显式挂载给 AgentRun Job，不依赖 Runtime 自动发现；
- AgentTemplate 不绑定 Runtime、模型、凭证、Team 责任或 Project 上下文；
- Skill 包校验路径逃逸、符号链接、Frontmatter、完整文件树和 digest；
- GitHub Skill 导入记录 requested revision 与 resolved commit SHA，Package/Artifact/Audit 中不保存 Token；

### 3.4 Release 与 Deployment

- Release Gate 通过前只提供可重建 `ReleaseReadinessView`，不创建 ReleaseCandidate 聚合、ID、Version、状态或审批记录；
- `release_input_digest` 由服务端根据精确 Integration、Build、Artifact、Verification、Acceptance Plan/suite digest 和 LockedComponentSet 输入计算，只用于 Gate 绑定与过期检测，不作为领域身份；
- 任一输入变化后旧 GateResult 不得继续适用；Gate 未 Pass 时页面只显示 blocker 与证据导航，不显示默认“确认发布”按钮；
- Release 只在精确 Integration、Build 与 Verification 输入齐备且 Release Gate 通过后形成，并通过不可变 Artifact 固定 Requirement、Acceptance Criteria、Design、Acceptance Plan、Project Rules、Integration commit、构建定义、验证结果和制品 digest；
- Preview 默认部署到 Open Agent Cluster 当前集群；
- Preview 自动验收必须使用 Release 固定的 Acceptance Plan Artifact、Acceptance Bundle 和 suite digest；
- 用户验收通过后，原 Workflow 直接 `Succeeded` 且同一 Release 为 `Promotable`；无外部目标或用户暂不发布都不阻止闭环；
- 外部 Promotion 是独立 `StartPromotion` 应用命令，由 Deployment Host 通过 Deployment SDK 调用精确 DeploymentDriver；默认单步路径不得创建 Workflow、Plan、WorkUnit、ComponentRun 或调用 Orchestrator Kernel；
- Preview 的 Deployment Sensor 继续作为 Workflow Harness 的计算型 Sensor 验证 expected/actual digest、健康状态和 URL；直接 Promotion 则由 DeploymentDriver 的 `observe` 及 `SubmitDeploymentReport` 追加同类执行事实，不伪造 Harness Sensor；
- Kubernetes Pod/Job phase 不能直接作为 Delivery 业务状态；
- `Deployment.execution_origin` 必须区分 `workflow-component` Preview 与 `direct-promotion` 外部发布；两者共享 Deployment 业务语义，但执行入口和状态所有者不能混用；
- 直接 Promotion 的请求事实不可变，执行进度使用带单调 `report_sequence` 的类型化报告追加；接受请求不等于成功，当前 outcome 由报告重建；
- `Pending|Running|Unknown|Cancelling` 持续占用目标并发键；Unknown 只允许 Observe 或条件性 Cancel，不能 Retry 或部署旧 Release；
- 自动 Observe 和“立即重新检查”复用同一 Deployment，Cancel Accepted 后仍需 Observe 到真实终态；
- `Failed|Cancelled` 后可通过新的 StartPromotion 重试同一 Release，或创建一条 `release_id` 指向已知可用旧 Release 的新 Deployment；新记录通过 causation 关联原 Deployment，原记录保持原终态，不增加 `RolledBack`、RetryDeployment、RollbackDeployment 或专用回滚 Release 字段。

### 3.5 Agent 协作协议与连续任务线验收

- Settings、Agent Center、Project Setup、Plan、WorkUnit、Agent 协作协议、Guide 和 Web 各自必须只有一个权威领域落点；CLI、页面、Runtime 和 Host 只能适配同一合同，不得各自维护不同语义；
- `oacok work show` 必须通过 run-scoped AgentWorkChannel 返回创建时固定的 InvocationRequest 及字段中文释义；`oacok work read` 只能读取该请求声明的精确 resource key 并校验 digest；不得按最新 Workflow 状态重新拼装；
- `oacok work submit` 必须形成匹配当前 invocation_id、request_digest、iteration 和 submission contract 的 WorkSubmission；Agent 终态 conclusion 先持久化为 run-conclusion Observation，其他结果先持久化为 Artifact/Observation/Question/Run report，不能直接写 WorkUnit state、Gate、Plan 或 Workflow；
- `oacok guide workflow|roles|orchestrator|worker|reviewer|acceptor|recovery|metadata` 的等价效果由四层共同完成：Kernel 通用协作协议由 `oacok` 展示；AgentTemplate 稳定 Instructions 通过 RuntimeInstructionSet 注入；Solution/Harness 阶段 Guide 位于当前 InvocationRequest；动态任务事实由 `work show/read` 提供。Kernel 不临时创作行业 Guide，Runtime 不扫描隐藏 Session 补齐协议；
- Producer 结束后 Reviewer 必须能读取其结构化 conclusion；Reviewer Reject 后返工 Agent 必须能读取完整 verdict、summary、findings、nits 和 evidence；显式换 Agent 后新 Session 必须仍能沿同一 WorkUnit/Lineage 连续执行；
- DAG Orchestration 只能由已激活 Plan 中的显式 WorkUnit 进入图；GateResult/Decision 只选择已编译分支；一次 Workflow Watch/Reconcile 只执行有限 `advance`，不得建立 CLI、页面或 Runtime 驱动的第二套循环；
- 系统不得新增与 canonical Workflow/WorkUnit/Plan/Artifact/Observation/GateResult/Decision 并行的 Manifest、Issue 状态机、exit-code 控制流或其他兼容写路径；发现绕过权威对象的写入即判定失败；
- 贪吃蛇黄金案例必须提供从 fixed-baseline、逐份 Design/Acceptance Plan 审批、Delivery DAG Orchestration、InvocationRequest/`oacok`/WorkSubmission、Producer conclusion、实现 Review/返工、Preview E2E 验收、最终 Gate 到固定 DAG 收敛的完整证据链，并能逐项反查对应 `UJ-*`、详细设计和 Reference。

## 4. 云原生与规模验收

- Linux、macOS 和 Windows 使用同一个安装器 CLI，安装完成后使用同一个 Web 和 Platform API；首版没有 Desktop App 或桌面专属业务接口；
- `/administration/operations`、`oactl operations show` 与直接 API Client 必须复用同一 `QueryPlatformOperations`，展示综合健康、数据新鲜度、当前活动告警、Kubernetes 权威容量和 unknown coverage；基础路径不强制依赖 Prometheus/OpenTelemetry，也不创建 Alert、CapacitySnapshot、Run Slot 或 Kernel action；
- Linux 本地 Compact 可以安装 K3s；macOS/Windows 只复用用户确认的兼容 Kubernetes，缺少环境时必须在部署前阻塞并给出安装指引；
- 普通安装自动解析唯一默认 StorageClass 与入口能力；没有唯一默认值时从已探测候选中选择，不要求用户手工猜测 Kubernetes 资源名；
- Compact 与 External Cluster 使用同一 Workflow CRD、领域模型、Controller、API 和验收；
- Platform Core 是 Workflow CR 唯一物理写入者；Platform Core root command、Workflow Controller、Platform Core AgentRun event/completion use case、Harness Host 和 Execution Host 分别拥有 root spec、控制状态、AgentRun report、ComponentRun report 与 Run infrastructure 的语义写权限，只能通过对应类型化命令更新，任何一方都不能整对象覆盖；
- controller-runtime 只 Watch Workflow，并在 Controller 重启后通过 Workflow relist 自动继续协调；Job/Pod/PVC 变化必须先由 Execution Host 通过 `SubmitRunInfrastructureObservation` 写入匹配 Run；
- Execution Host 通过 Kubernetes API 创建确定性命名的标准 Job/PVC；重复 start 返回同一资源；
- Kubernetes 根据 CPU、内存、GPU、存储和拓扑调度；Job/Pod 成功不能绕过结构化结果和 Gate；
- Controller 默认单副本，External 可启用 Leader Election；失败后从完整 CR spec/status 和不可变事实收敛；
- PostgreSQL 保存目录、事件、审计、Usage 和历史，但不保存竞争的活动状态或驱动 Workflow Loop；
- AgentRun Job 使用独立低权限 ServiceAccount，不继承 Execution Host 凭证；
- AgentRun Job 只能使用 run-scoped 短期执行凭证调用当前 AgentRun 的 `AppendAgentRunEvents`、`CompleteAgentRun` 与 AgentWorkChannel；Platform Core AgentRun use case 只能更新该 AgentRun，Harness Host 只能提交当前 ComponentRun 的 `SubmitComponentRunReport`，任何一方都不能修改其他 Run 或 WorkUnit state；
- `VerificationFailed`、`ReviewRejected` 和 `NitsRequired` 使用明确路径，失败与返工不得返回 `Ready`；
- `status.workUnits`/`runs` 使用 keyed map-list；Platform Core 按语义拥有者选择独立 field manager，并发命令、重复回调与 resourceVersion 冲突不丢字段；
- Compact 在 Linux K3s 或受支持的本地 Kubernetes 上能同时运行至少 3 个物理 Run；External Cluster 使用 3 个 Worker 节点、每节点至少 3 个 Run，合计至少 9 个并发 Run，并在一个节点故障后恢复或重新调度；
- 上述容量是部署验收基线，不允许在 Kernel、Workflow 状态机或 Platform Core 中增加 run slot、分布式信号量或自定义调度器；
- 增加符合要求的计算节点后，并发容量可提升，不需要修改 Workflow 或 Agent Team 定义。

## 5. 外部行业方案验收

至少实现一个“调研学习”外部 Solution：

- 通过公开 Package 验证、安装和激活入口接入；
- 使用自己的目标、子问题、来源、Critic、引用 Gate 和综合报告术语；
- 复用 Codex/Hermes RuntimeDriver 与平台内置 Pod/PVC Workspace；
- 不修改 Kernel、Platform Core、Workflow Controller、Workflow CRD、数据库和 Generic API；
- 无声明式领域体验时仍能通过 Generic Web/API/CLI 完整运行；存在合法领域体验时通用入口仍可访问，声明无效、渲染故障或精确版本不兼容时安全回退到同一 Workflow；
- Package/流程 Fixture 必须覆盖通用 Project Setup、Workflow 创建、安全 Artifact fallback、Review/返工/Decision、固定 DAG 收敛以及领域体验故障回退；
- 验证显式 DAG Orchestration、Review、返工、Harness 四象限、恢复、分支闭合与固定 DAG 收敛；同时证明 Solution、OrchestrationDefinition 和 Workflow 中不存在第二套完成对象或表达式。

## 6. 公开证据

公开验证至少提供：

- SolutionPackage、ContentPackage、ExtensionPackage、Package Envelope、interface_api、digest、ComponentInstall、ExtensionCatalog、LockedComponentSet 和 Package/接口检查；
- Project 当前 revision/configuration digest、Workflow 不可变 ProjectConfigurationSnapshot、TeamBindingSnapshot、Workflow、WorkUnit、Plan、Artifact、Observation、GateResult 和 Decision；
- Product/Requirement、DAG Orchestrator、Reviewer、Developer Lineage、AgentRunEvent、Runtime/Workspace Capability 和恢复记录；
- RequestContext、Authorization/Admission Decision、AuditEvent、Credential 使用与 Usage；
- Requirement、Acceptance Criteria、Design、Acceptance Plan、Project Rules、Git revision、ReviewResult、CI、Release、Deployment 和用户验收；
- Embedded/Installed 对照、Extension/外部系统故障注入、Controller/节点故障和恢复结果。

平台扩展能力以“新增行业方案需要修改多少内核代码”为硬指标，目标为零。任何新增 Kernel/Controller 私有业务分支、第二套活动状态机、行业专用 Runtime 接入旁路或 Generic API 旁路都判定失败。

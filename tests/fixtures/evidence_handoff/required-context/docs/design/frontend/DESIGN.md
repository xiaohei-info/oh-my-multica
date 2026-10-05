---
version: alpha
name: Open Agent Cluster Interface System
description: Calm, evidence-first interfaces for governed agent workflows, platform operations, and technical content.
colors:
  primary: "#006EDB"
  white: "#FFFFFF"
  canvas: "#F5F5F7"
  surface: "#FFFFFF"
  surfaceSubtle: "#F0F1F3"
  surfaceRaised: "#FAFAFB"
  ink: "#1D1D1F"
  inkSecondary: "#5E6066"
  inkMuted: "#767981"
  divider: "#D9DADE"
  action: "#006EDB"
  actionHover: "#0058B5"
  actionSubtle: "#E8F2FF"
  success: "#18734A"
  successSubtle: "#E9F7EF"
  attention: "#A7500A"
  attentionSubtle: "#FFF1E5"
  danger: "#B4232B"
  dangerSubtle: "#FDECEF"
  review: "#5C4BB0"
  reviewSubtle: "#F0EDFF"
  info: "#006C82"
  infoSubtle: "#E6F6F8"
  focus: "#006EDB"
  canvasDark: "#111214"
  surfaceDark: "#1A1C1F"
  surfaceRaisedDark: "#23262A"
  inkDark: "#F5F5F7"
  inkSecondaryDark: "#B8BAC0"
  dividerDark: "#373A40"
  actionDark: "#6DB1FF"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI, sans-serif"
    fontSize: 2.5rem
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: "-0.035em"
  title-lg:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI, sans-serif"
    fontSize: 2rem
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "-0.028em"
  title-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: 1.5rem
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.018em"
  title-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: 1.125rem
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: 0.9375rem
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "-0.006em"
  body-compact:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: 0.8125rem
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "-0.002em"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: 0.8125rem
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.002em"
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: 0.75rem
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "0em"
  code:
    fontFamily: "SFMono-Regular, SF Mono, Cascadia Code, Roboto Mono, monospace"
    fontSize: 0.8125rem
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "-0.005em"
rounded:
  xs: 6px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 22px
  pill: 999px
spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
  3xl: 64px
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.white}"
    rounded: "{rounded.sm}"
    padding: 12px
    height: 36px
  button-primary-hover:
    backgroundColor: "{colors.actionHover}"
    textColor: "{colors.white}"
    rounded: "{rounded.sm}"
    padding: 12px
    height: 36px
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: 12px
    height: 36px
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.white}"
    rounded: "{rounded.sm}"
    padding: 12px
    height: 36px
  input-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: 10px
    height: 36px
  navigation-selected:
    backgroundColor: "{colors.actionSubtle}"
    textColor: "{colors.actionHover}"
    rounded: "{rounded.sm}"
    padding: 10px
  status-success:
    backgroundColor: "{colors.successSubtle}"
    textColor: "{colors.success}"
    rounded: "{rounded.pill}"
    padding: 8px
  status-attention:
    backgroundColor: "{colors.attentionSubtle}"
    textColor: "{colors.attention}"
    rounded: "{rounded.pill}"
    padding: 8px
  status-danger:
    backgroundColor: "{colors.dangerSubtle}"
    textColor: "{colors.danger}"
    rounded: "{rounded.pill}"
    padding: 8px
  surface-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: 16px
---

# Open Agent Cluster Frontend Design Foundations

## Overview

本文件是 Open Agent Cluster 所有前端表面的项目级权威设计规范。它同时服务设计人员、前端工程师、全栈工程师、SolutionPackage 领域体验作者和编码 Agent。

适用范围包括但不限于：

- 首次管理员初始化、普通用户邀请/接受、登录、退出、身份恢复、Scope 进入、错误和维护页面；
- Unified Settings、Skill Import、Model Provider、MCP Server、Credential、Agent Center、AgentTemplate、AgentDefinition 和 RuntimeBinding 配置；
- Project Setup、Agent 岗位、当前 `Project.solution_setup`、Workflow 初始 TeamBindingSnapshot 与显式 Executor 绑定 Decision 展示；
- Solution Catalog、Solution Studio，以及 SolutionPackage、ContentPackage、ExtensionPackage 的装配与发布；
- Generic Workflow Web；
- WorkUnit、Artifact、Observation、Gate、Decision、ComponentRun、AgentRun、human Run、统一 Attention Center 与“我的工作”页面；
- Package 安装、升级、禁用、撤销、Package 检查和接口检查页面；
- Governance、Audit、Usage 和平台设置；
- Software Delivery 等 SolutionPackage 的声明式领域体验；
- 项目官网、技术文档和公开示例页面；
- 后续新增的任何 Open Agent Cluster Web 表面。

本文件定义共同的体验原则、视觉 Token、页面模式、组件边界、数据语义、技术栈和质量门。它不要求所有页面使用相同布局。操作工作区、设置表单、技术文档和公开页面可以采用不同页面模式，但必须共享同一套设计语言和交互契约。

规范优先级固定为：

1. 本 `DESIGN.md`；
2. 已实现并通过验收的 OAC Design Token、UI Pattern 和 Storybook；
3. 页面模式规范；
4. 页面特定设计文档与原型。

页面特定文档只能补充该页面的任务、数据和交互，不能重新定义项目级颜色、字体、状态语义、组件层级或技术栈。发生冲突时，本文件优先；确需改变全局规则时，必须先更新本文件并说明迁移影响。

整体视觉姿态是：冷静、可信、证据优先、适度精密，并在 Agent 身份、空状态和引导性表面保留克制的温度。界面从文档式阅读节奏、现代生产力工具的信息密度和 Apple 式空间连续性中吸收原则，但不复制任何品牌产品、插画系统或创作者的独特风格。

## Colors

颜色承担语义，不承担装饰。

- `canvas` 是全局背景，提供安静、略带层次的工作环境。
- `surface` 是主要阅读与操作表面。
- `ink`、`inkSecondary` 和 `inkMuted` 形成三级文字层级。
- `action` 只表示可交互行为、选中导航或键盘焦点，不表示普通信息状态。
- `success` 只表示经过确认的成功或 Gate Pass。
- `attention` 表示等待处理、风险或可恢复警告，不表示失败。
- `danger` 表示拒绝、失败、撤销或破坏性动作。
- `review` 只用于 Review 责任、Review 状态和对应证据。
- `info` 表示中性运行信息和系统提示。

状态必须同时包含文本或图标标签。禁止只用颜色表达 `VerificationFailed`、`ReviewRejected`、`Waiting`、`Cancelled` 等权威状态。

深色模式使用独立语义 Token，不通过简单反相产生。暗色表面仍保持可辨识的层级，避免纯黑背景和发光霓虹色。

彩色大面积背景、彩虹状态、装饰性渐变和无业务含义的彩色图标不属于 OAC 视觉语言。公开营销页面可以使用受控的品牌光晕，但不能改变语义色含义。

Agent 身份插画可以在头像边界内使用低饱和角色色，但这些颜色只帮助人物识别，不表达 Ready、Waiting、失败、Responsibility、Capability 或权限。角色色不得扩散到卡片边框、状态 Badge、操作按钮或页面背景，也不得形成第二套语义色系统。

## Typography

默认使用系统字体栈，优先获得平台原生字形、快速加载和稳定的多语言回退。

- `display` 仅用于公开页面 Hero 或产品内极少数总览标题。
- `title-lg` 用于页面主标题。
- `title-md` 用于主区域标题和重要资源标题。
- `title-sm` 用于卡片、Inspector 和表单分组标题。
- `body` 用于正文、说明和文档内容。
- `body-compact` 用于高密度表格、时间线和元数据。
- `label` 用于按钮、字段标签、导航和状态文字。
- `caption` 用于时间、来源、次级计数和辅助说明。
- `code` 只用于 digest、sequence、版本、命令、字段名和精确 identity。

禁止把 monospace 扩散到普通导航、按钮和大段正文。英文大写标签只用于短小分组，不用于完整句子。中文界面不人为增加字间距。

正文默认宽度不超过 76 个拉丁字符或约 38 个汉字。技术文档主体推荐 680–760px 阅读宽度；操作页面不受该宽度限制，但长文本块仍需独立控制行长。

## Layout

空间系统以 4px 为最小单位，以 8px、12px、16px、24px 和 32px 为主要节奏。新组件优先使用已有间距 Token，不为单一页面增加 13px、19px 等任意值。

应用表面使用稳定的三层空间模型：

1. Application Chrome：全局导航、Project 上下文、搜索和账户入口；
2. Page Frame：标题、面包屑、主要动作和页面级筛选；
3. Work Surface：列表、文档、图表、时间线、表单或 Inspector。

默认最大内容宽度：

- 公开内容与文档：1200px 页面容器，680–760px 正文列；
- 普通资源页面：1440px；
- 操作工作区：允许使用完整视口宽度；
- 表单：主输入列 560–720px，说明列按需并排；
- Modal：仅用于必须阻断上下文的短任务，最大宽度通常为 560px。

页面应当先建立稳定骨架，再加载内容。Skeleton 保持最终尺寸，避免大面积 shimmer 和布局跳动。

## Elevation & Depth

深度只表达层级、临时性和空间归属。

- 主内容表面保持实色或高不透明度。
- 半透明材质只允许用于顶部 Toolbar、侧边导航、Inspector、Command Palette 和短暂浮层。
- 一个半透明表面上不得再叠加另一个半透明表面。
- Card 使用轻边框和极弱环境阴影；默认不使用“每个内容块都是浮卡片”的布局。
- Inspector 必须从触发侧进入并沿同一路径退出，使用户理解空间关系。
- Dialog 使用遮罩只因为任务需要阻断主上下文，不能把普通详情查看做成 Modal。

推荐环境阴影：`0 1px 2px rgba(0,0,0,0.04), 0 10px 28px rgba(0,0,0,0.07)`。浮层阴影可以更强，但不得形成厚重悬浮卡片墙。

在 `prefers-reduced-transparency: reduce` 下，所有玻璃材质切换为接近实色背景并移除 blur。

## Shapes

圆角表达组件尺度，而不是品牌装饰。

- 6px：小型控件、Code、Badge；
- 8px：Button、Input、Select、导航项；
- 12px：Card、Panel、Table 容器；
- 16px：大型 Inspector、Sheet、空状态表面；
- 22px：仅用于显著的大型欢迎或公开展示表面；
- Pill：状态标签、Segmented Control 和紧凑筛选项。

同一视觉层级内使用一致圆角。禁止在一个页面混用大量 4px、7px、11px、18px 等近似值。

## Components

组件分为五层，依赖只能由上层指向下层：

1. Foundations：颜色、字体、间距、圆角、深度、动效和无障碍规则；
2. Primitives：Button、Input、Dialog、Tooltip、Table、Tabs、Popover 等无领域原语；
3. Patterns：ApplicationShell、ResourceHeader、FilterBar、SplitView、Inspector、EmptyState、CommandFeedback 等跨页面组合；
4. Domain Features：Workflow、Project、Run、Gate、Decision 和 Package Governance 的明确领域组件；
5. Page Compositions：面向具体用户任务的页面组合。

推荐的稳定跨页面 Pattern：

- `ApplicationShell`：全局上下文、导航和主内容槽位；
- `ResourceHeader`：资源 identity、状态、元数据和主要动作；
- `CollectionToolbar`：查询、筛选、排序、密度和批量操作；
- `SplitView`：列表或正文与有界 Inspector；
- `AgentPortrait`：有界的 Agent 身份插画、首字母回退和可访问名称；
- `CommandFeedback`：已接受、收敛中、冲突、拒绝和失败；
- `StateSummary`：文字优先的状态、原因和下一步；
- `EvidenceBlock`：来源、subject ID/digest、结果和 provenance；
- `Timeline`：有序事实、sequence、责任和时间；
- `DestructiveAction`：原因确认、影响说明和不可逆提示。

OAC 自己拥有的领域组件至少包括：

- `AgentDefinitionSummary`；
- `ResponsibilitySlotBinding`；
- `ProjectSetupReadiness`；
- `SolutionExtensionLayers`；
- `WorkflowStageTrack`；
- `WorkUnitStateLabel`；
- `GateEvidenceList`；
- `DecisionInspector`；
- `RunTimeline`；
- `HumanResponsibilitySubmission`；
- `ComponentInstallState`；
- `LockedComponentSetSummary`；
- `UsageBreakdown`。

禁止创建带大量 Optional 字段的万能 `EntityCard`、`RunCard` 或 `StatusPanel`。`ComponentRun`、`AgentRun` 和 `kind=human` Run 必须保持不同组件合同；human Run 不创建 HumanTask/HumanRun 页面对象或领取状态机。`Workflow` 和 Software Delivery 的 `Delivery` 可以共享底层 Pattern，但不能创建第二套状态模型。

所有交互组件必须明确 Default、Hover、Focus Visible、Pressed、Disabled、Loading 和 Invalid 状态。Loading 不能抹去原按钮标签；提交后优先显示“命令已接受”而不是无限 Spinner。

## Do's and Don'ts

应当：

- 先显示权威状态、原因、证据和下一步，再显示装饰性摘要；
- 保持一页一个主要动作，次要动作降低视觉权重；
- 用直接可读的数据结构替代隐藏在 Tooltip 里的关键事实；
- 让用户在列表、详情和 Inspector 之间保留上下文；
- 对失败、拒绝、等待和并发冲突给出明确恢复路径；
- 在视觉密度与可扫描性之间保持稳定节奏；
- 用真实领域词汇，不创造无法映射到权威模型的 UI 状态。

禁止：

- 把 Pod phase、日志或实时连接状态显示成 Workflow 业务状态；
- 把 API 200 或命令 accepted 显示成 Workflow 已经完成；
- 把 `VerificationFailed` 或 `ReviewRejected` 直接折叠为普通返工；
- 用玻璃卡片、渐变、彩色阴影和持续动画制造“高级感”；
- 为每个页面复制一份新的 Token、Button 或 Empty State；
- 通过 SolutionPackage 声明式领域体验注入任意脚本或绕过公开 Platform API；
- 让前端直接访问数据库、Kubernetes、Controller、Kernel 或 Runtime 厂商类型；
- 在没有真实跨页面客户端状态问题前引入全局状态框架。

## Experience Principles

### 1. Evidence before decoration

OAC 的价值来自可验证的 Workflow，而不是任务卡片数量。页面优先呈现状态来源、Artifact ID/digest、Observation、GateResult、Decision、Run provenance 和恢复动作。

### 2. Calm density

信息可以密集，但不能喧闹。通过排版、分隔、对齐和 Progressive Disclosure 管理复杂度，不通过增加卡片、颜色和图标管理复杂度。

### 3. Explicit control semantics

一个状态词只有一个含义。派生摘要必须可追溯到权威对象，不得形成隐藏状态机。命令、Decision、Query 和 Event Subscription 保持不同交互合同。

### 4. Spatial continuity

详情、证据和有界操作优先使用 Split View 或 Inspector 保留上下文。页面跳转用于真正改变工作对象，Dialog 用于短暂且必须阻断的任务。

### 5. Progressive disclosure

默认显示当前任务需要的最少完整信息。高级 provenance、raw event、digest 和基础设施诊断按需展开，但不能把业务必需事实藏入二级页面。

### 6. Safe by default

破坏性操作、正式 Decision、Retry 和组件迁移必须显示 subject、影响和并发条件。外部结果未知时先 Observe，再允许 Retry。

### 7. Accessible without a special mode

键盘、屏幕阅读器、高对比度、减少动态效果和减少透明度是默认设计条件，不是发布前补丁。

## Page Archetypes

全项目使用以下页面模式。新增页面必须先选择最接近的模式，只有真实任务无法表达时才增加新模式。

| 页面模式 | 主要用途 | 稳定结构 |
| --- | --- | --- |
| Focus | 登录、引导、单一确认 | 聚焦主体、少量辅助信息、一个主要动作 |
| Collection | Project、Workflow、Package、Run 列表 | ResourceHeader、CollectionToolbar、列表或表格、分页 |
| Resource Detail | Project、Agent、Package、Workflow 摘要 | identity、状态、摘要、分区详情、关联资源 |
| Operations Workspace | Workflow、Artifact Review、Decision、Run 诊断 | Sidebar 或 Track、主工作面、Inspector、实时反馈 |
| Task Form | 创建、配置、安装、策略设置 | 单主列或双列说明、分组校验、离开保护 |
| Governance Data | Audit、Usage、Policy、Package Install 和接口检查 | 高密度表格、过滤、导出、不可变详情 |
| Documentation | 官方文档、Reference、教程 | 文档导航、680–760px 正文、目录、代码和版本提示 |
| Public | 项目介绍、案例和下载 | 清晰叙事、受控品牌表达、快速加载、静态优先 |
| Domain Experience | SolutionPackage 声明式领域页面 | 公开 Platform API、标准 Read Model、受控 Form/Layout/Renderer 元数据 |

同一个领域可以使用多个模式。例如 Workflow Collection 使用 Collection，Workflow 总览使用 Resource Detail，活动执行使用 Operations Workspace。

Audit 使用 Governance Data 模式，并且只提供一个 Audit Explorer。稳定结构为“FilterBar + 高密度结果表 + 同页 Inspector”：FilterBar 支持时间、Project、category、action、outcome、Principal、Subject 和 correlation；表格展示发生时间、类别/动作、操作者、Subject、结果、Project 和 correlation；Inspector 从 AuditEvent 展开脱敏 RequestContext、evaluated controls、Credential-use facts、精确业务 Subject 与 causation/correlation 链。Authorization、Admission 和 Credential use 只是筛选类别，不建立独立页面。

Authorization 管理只有一个 `/administration/authorization` Task Form，维护当前 Scope 的唯一 AuthorizationPolicy。页面只读展示 OAC fixed invariants，编辑命名 access profiles 与 allow-only grants，并通过 Candidate Evaluation 展示 blockers、warnings 和影响对照后再 CAS 采用；不得出现 Authorization Provider、ExtensionPackage、Version、脚本 DSL、规则优先级或多引擎选择。Action 与 Subject Kind 只能从服务端 Catalog 选择，Policy ID/revision/digest 只读。该页面不是 Package & Extension Governance 的子页面，所有实际 Allow/Deny 仍由同一 Platform API 和 OAC AuthorizationEngine 产生。

Execution Admission 不建立管理页或 Provider 选择。Run、Workflow、Deployment 与 Audit Inspector 复用一个只读 Admission Result 模式，展示 Admit/Reject、可读 reason、检查名称/说明/状态、约束、事实时间和只读 digest；Reject 时按原因导航到真正可修复的 Project、Runtime、Workspace、Credential、Target 或 Platform Operations 容量诊断。前端不能提供绕过按钮，也不能缓存旧 Admit 代替新的 Platform API 检查。

外部审计导出使用 `/administration/audit-export` Task Form。页面顶部始终展示本地 AuditEvent/Audit Explorer 已启用且不可替换；没有 AuditExportTarget 是正常状态。表单只收集名称、说明、内置 `export_type`、Schema 驱动的非敏感配置和可选 CredentialBinding，测试不持久化，首次保存默认 Disabled，启用时服务端重新测试。页面不提供第三方 Audit Export Package、本地 Audit Provider、事件过滤、checkpoint 编辑或关闭本地审计。运行卡片展示 enabled、health、backlog、最后连续游标、最近成功时间、连续失败和脱敏错误码；外部接收端故障时仍保留进入本地 Audit Explorer 的主入口。

Project、Workflow、Agent、Run、Component 与 Deployment Resource Detail 可以提供“查看审计记录”，但该动作只能跳转统一 Audit Explorer 并携带预设筛选。用户没有 `audit.read` 时不展示可造成误解的空结果；服务端拒绝后使用标准权限错误。导出只在具有 `audit.export` 时出现，并复用当前筛选；Secret、Token、Header、Prompt、私有推理和完整工具日志不得进入 DOM、下载文件、前端缓存或错误详情。

Usage 同样只提供一个 Usage Explorer。稳定结构为“全局 FilterBar + KPI 摘要 + 联动图表区域 + 原始明细表”，路由为 `/governance/usage`。内置视图固定为总览、模型与 Token、Agent 效率、Workflow 与 WorkUnit、Runtime 与 Tool、原始明细；Project、Workflow、WorkUnit、Agent 和 Run Resource Detail 只携带预设筛选跳转，不维护局部 Usage 数据副本。

图表组件至少覆盖折线/面积时间序列、堆叠柱状图、排行条形图、热力图、成本-时长散点图、Project→Workflow→WorkUnit 分布和高密度对比表。所有图表共享同一筛选状态，点击图元必须形成可见筛选 Chip，并可一键撤销；图表选择不创建持久化 Dashboard 对象。

Token 图只能堆叠互斥桶，不能把 total 与 cache/reasoning 子集相加。每个 KPI 和图表显示 coverage；unknown 使用缺口样式和说明，不画成 0。成本按币种分区并显示来源；实际模型缺失时展示“未报告”，不能复制请求模型。首版允许保存筛选条件，不建设拖拽图表编辑、自定义 SQL、预算进度条或自动停机交互。

Platform Operations 只提供一个 `/administration/operations` 只读页面，采用 Resource Detail 与 Governance Data 的组合模式。顶部展示综合健康和数据新鲜度，主体固定为控制面、执行面、数据与存储、外部依赖四组健康检查，下方展示当前活动告警和执行容量。页面只调用 `QueryPlatformOperations`，不维护本地健康状态、不直接访问 Kubernetes 或监控后端，也不创建 Dashboard、Alert、Incident 或 CapacitySnapshot。健康使用 `healthy|degraded|unavailable|unknown`，容量使用 `available|pressure|exhausted|unknown`，两组状态不得混成一个含义不清的颜色。unknown 必须使用缺口样式和来源说明，不能显示为绿色或 0。

活动告警固定展示严重程度、标题、受影响对象、首次/最近观测时间、脱敏摘要和修复入口；同一 `alert_fingerprint` 只显示一条。首版不提供确认、静默、规则编辑或通知路由。修复入口只能导航到真正拥有配置或恢复命令的 Project、Workflow、Component、Credential、Deployment、数据存储页面，或给出宿主/Kubernetes/云基础设施容量指引；Platform Operations 本身不出现“忽略并继续”、万能修复或一键扩容按钮。容量区域先用产品语言展示运行数、等待数和归一化阻塞原因，Kubernetes Namespace、Pod、Condition、requests/limits、Quota 等细节只在具有诊断权限时通过 Inspector 展开。实时利用率来源不存在时显示 unavailable，仍保留 allocatable、requested、Quota 与 Pending 事实。

OAC 系统发行版升级与回滚不建设 Web 页面，也不进入 Platform Operations 的写操作。运维人员通过统一 Installer CLI 查看瞬时兼容摘要并执行；Web 只在升级前后继续使用同一个只读健康页面验证系统是否收敛。独立 Package 升级仍属于 Package & Extension Governance 页面，Software Delivery 的 Release/Deployment 仍属于交付页面，三者不得共用一个“升级中心”。

Settings 使用稳定的“左侧设置导航 + 右侧 Collection/Task Form”模式。Skills、Model Providers、MCP 和 Credentials 是同一 Settings Shell 下的四个路由，不在一个巨型表单中同时编辑。Shell 可以聚合状态计数和跨目录搜索，但各 Tab 必须保留自己的 ViewModel、Command、校验和权限反馈。

Settings 首屏表达“当前能力是否满足所选 Agent/Solution”，而不是把四个 Tab 伪装成必须完成的向导。内置 Software Delivery AgentTemplate 与 Skill 已就绪时直接显示可用；平台/Scope 已存在模型目录时不要求重复配置；MCP 与 Credential 只在依赖它们的能力缺失时显示阻塞和修复入口。

表单使用渐进披露：默认只显示用户无法从上下文或预设得到的业务字段；Scope/Project、稳定 ID、package SemVer 或配置 revision、digest、来源、状态和审计信息只读展示；协议发现端点、分页、TLS、超时、重试、代理与轮换默认值由 Connector 或 Policy 隐藏。只有确有授权且业务上需要覆盖时，才进入单独的高级设置，不把内部实现参数混入普通创建表单。

Settings 的安全交互规则：

- Secret 输入只在创建或轮换表单中出现，提交后立即清空，详情页永不回填；
- API Key、PAT、Private Key 和 Token 只显示 fingerprint、末四位或 Key ID；
- Test 按钮必须说明将访问的 Base URI/Endpoint、使用的 CredentialBinding 和测试动作；
- Provider/MCP 测试错误先由服务端脱敏，前端不得展示 Authorization Header、环境变量值或完整敏感响应；
- Model Provider 页面不提供发现模式或模型列表路径；“获取模型”由 API Type 对应 Connector 完成，失败或缺少模型时就地提供“手动添加模型”；
- GitHub Skill 导入只要求仓库地址，revision 和子目录可选；Manifest 优先提供名称、说明和版本，发布前显示最终 Commit SHA；Local Upload 显示文件数量、大小、入口文件和 digest；
- MCP 工作目录缺省为 Workspace 根目录，连接策略继承平台/Scope Policy；只有发现 Secret 时才要求创建 Header/Environment CredentialBinding；
- 模型、MCP、Repository 等就地 Credential 表单只收集名称、说明、类型、条件性 Secret、类型专用元数据和条件性到期时间；Scope、用途与 subject 由上下文和目标推导，handle/fingerprint/status/revision 由内部 CredentialStore 或系统产生，首版没有存储后端选择、CredentialPolicy 或 `rotation_policy` 编辑项；
- `/settings/credentials` 默认展示内置 CredentialStore 已就绪，并用于查询、轮换、禁用、撤销和查看引用；不提供通用 Scope/用途/subject 扩权编辑器，也不提供伪通用“测试所有凭证”、企业后端、默认后端或迁移区域；CredentialStore 故障时 Fail Closed，并引导用户到 Platform Operations；
- Skill/Package Catalog 的 package release 列表并排展示精确 SemVer、digest、来源、状态和引用位置，不提供覆盖旧 release 的快捷动作；Model Provider 与 MCP 页面展示当前 revision 和 Audit/change history，不伪装成 package 版本列表；
- MCP Capability 列表是测试快照，不设计为逐 Tool 勾选权限表；Runtime 自带 Tool 不出现在首版 Settings。

Agent Center 使用“模板/Agent 列表 + 配置 Task Form + 预检 Inspector”模式，并遵循以下交互规则：

- Agent 列表、详情和选择器复用同一个 `AgentPortrait` Pattern。当前选定方向是原创的温暖手绘动画：成年职业人物、自然有机线条、轻水彩或水粉质感、低饱和角色色、克制表情和一致的肩部以上构图；不得直接模仿任何在世创作者、动画工作室、现有动漫 IP 或 StaffDeck 的人物系统；
- 头像只承担身份识别和界面亲和力，不编码在线状态、Workflow 状态、Responsibility、Capability、模型、Runtime、权限或执行健康。角色背景中的路径、方括号、校验、模块、盾牌和星点只能是低对比装饰，不产生 Tooltip、筛选条件或命令字段；
- 当前原型头像是展示资产，不为 AgentDefinition 增加 `avatar`、`avatar_url`、`portrait_style` 或生命周期字段。生产实现若尚无经过批准的展示合同，使用 OAC 内置角色预设或确定性的首字母回退，不让浏览器从任意外部 URL 加载身份图片；
- 在名称同时可见的卡片中，头像作为装饰图像使用空替代文本；头像单独作为选择目标时，Accessible Name 取 Agent 名称。图片加载失败不得抹去名称、状态或主要操作；
- 从 AgentTemplate 创建时，Instructions 和模板 `skills[]` 自动加载；模板 Skills 使用独立“模板内置”分组和只读来源标记，普通流程不要求用户重新选择；
- 用户追加的 Skills 放在“企业追加”分组，可以增删，但同一 Skill 稳定身份不得同时出现多个版本；
- 删除模板 Skill 只能通过“高级自定义模板”入口，必须展示移除清单、影响警告和明确确认；发布后显示“已自定义”派生标记，不创建新的持久化状态；
- 模型使用单一选择器，按 Provider 分组并允许筛选；Provider 不是必经的独立步骤，用户直接选择 Provider/Model 组合；
- 模型选项同时展示 Provider 名称、模型名称、external model ID、Description、API Type、最近 Probe 状态和测试时间；不能只展示一个无法区分来源的模型名称；
- `fixed` 模式只允许一个结构化模型选项；`ordered-fallback` 使用可键盘操作的有序列表，并允许跨 Provider 排序；
- 页面展示 Label 不进入命令。Platform API Adapter 必须把选择映射为 `model_provider_connection_id`、`external_model_id` 和可选 `selected_from_model_catalog_snapshot_id` 三个明确字段；
- 发布前 Inspector 展示模板/Skill、模型、MCP、Runtime、Capability、资源和权限 blocker；命令失败时保留用户当前编辑上下文，但不得伪造已发布 AgentDefinition；
- AgentTemplate/Skill package 更新、Provider/MCP 配置 revision 更新或 Model Catalog 刷新都不能静默改变已经发布的 AgentDefinition；Provider/MCP 变化只在创建后续 Workflow 执行快照时重新解析，并必须明确显示影响范围。

Solution Studio 使用“配置与输入 + WorkUnit 导航 + Harness 生命周期编辑器 + Package/接口检查 Inspector”模式，并遵循以下交互规则：

- `BaselinePlanTemplate`（批准基线模板）是当前精确 Solution release 内固定的 package-local 核心内容；需要动态规划时，其中直接包含显式 DAG Orchestration WorkUnit，最终业务完成通过显式终态 WorkUnit、输出、Gate 和 Decision 表达。Project 用户只读查看这些定义，不能在 Setup 中覆盖，Studio 不提供第二套完成条件编辑器；
- “配置与输入”使用同一页面的三个分组编辑顶层 `project_setup_schema`、`responsibility_slots[]` 和 `workflow_input_schema`，不增加 `project_setup` 包装层。Project/Workflow 字段复用同一个 FormField 组件：作者填写名称、必需说明、字段类型及条件性的默认值/校验/选项；choice 的每个 value 都带名称和说明。字段键与岗位键由系统生成，只读展示；
- 字段类型选择器必须解释实际保存值和自动检查。Repository、Credential、部署目标和 Artifact 资源字段选择或就地创建平台对象，只保存身份且不接收明文 Secret；所需 Probe/Policy 由字段类型自动派生，不展示作者侧 Capability 数组、脚本或 Webhook。责任岗位只编辑名称/说明、Required/Recommended、基础能力和推荐 AgentTemplate，不编辑独立性、来源节点、具体 Agent、Runtime、模型、MCP、Credential 或返工能力；
- BaselinePlanTemplate 编辑器只收集模板名称/说明，再用普通节点/边编辑器配置节点名称、说明、WorkUnit Contract、责任要求、可选类型化 `activation`、唯一 WorkUnit `execution`、完整 HarnessDefinition 与硬依赖；Workflow 行业输入来自 SolutionPackage 顶层 `workflow_input_schema`，不在模板中重复选择。`activation` 只能选择同一图已有 Gate/Decision Requirement 和允许 outcome，不提供脚本或表达式输入。“主执行”区域写入 WorkUnit，Harness 只写入四象限和 Gate/返工。系统生成 package-local 模板/节点 ID 和 digest，不要求作者填写内部身份；
- 每个节点只选择一个完整 HarnessDefinition。作者可以选择现有独立 Harness ContentPackage release，或在同一生命周期编辑器中创建 package-local HarnessDefinition；页面不得把 `harness` 解释成 ExtensionPackage 单选或 Harness 多选；
- WorkUnit 生命周期编辑页与不可变详情页使用同一个组件、同一字段集合和同一分组，固定顺序为“执行准备 → 主执行 → 观察与评估 → Gate 判定 → Kernel 后续处理”；主执行行明确显示“来源：WorkUnit.execution”，其他行显示“来源：Harness”；
- 每一行同时展示生命周期阶段、Binding 来源、能力名称与说明、同阶段顺序、Required/Optional、输入输出、权限、副作用模式和失败/恢复方式；Extension 行额外展示 Extension Point 与 ExtensionPackage 精确版本/digest，责任 Run 行展示责任要求、Guide、输出合同和独立性，不伪造 Extension 字段；
- 生命周期阶段从 WorkUnit 字段、Binding 类型或 `extension_point` 推导，页面不提供 phase 输入；同阶段 Harness 顺序直接对应 HarnessDefinition 数组顺序，不维护第二个排序字段；
- “观察与评估”区域必须暴露“启用独立 Review”选项。勾选后在原位置展开 Reviewer 责任、Guide、ReviewResult Contract、独立 Lineage/Session/Workspace 和 Reject/Nits 返工说明，并生成 Required `responsibility-run` Binding；取消后删除该 Binding，不保存第二个 `review_required` 布尔值；
- 用户批准位于 Gate 区域，是独立 Decision Requirement；Review Pass 不得在页面或命令中自动等价为用户批准；
- 责任 Run 可以在运行时解析为 Agent、程序或人工实现；显式 Acceptance WorkUnit 的主执行也可以解析为人工实现。页面直接展示同一不可变 Run request，并按当前 output contract 渲染 ReviewResult 或 AcceptanceResult 结构化提交表单；不要求用户运行 `oacok`，也不把人工结果显示成 Decision。内置软件交付流程不在自动 E2E 后追加 Acceptor 责任 Run；
- Preview 最终验收页面只读展示当前 Deployment、Release、实际部署摘要、健康 Observation 和 AcceptanceResult，并使用服务端计算的 `decision_subject` 提交普通 `approve` 或 `request_changes` Decision。用户不手填主体 ID/digest；`request_changes` 必填原因并在 Reconcile 后进入显式 Architect 技术变更设计 → Review → 用户 Decision → DAG Orchestration 路径，不直接进入 Developer 或 Orchestrator；主体过期时保留输入、刷新证据并重新确认，不创建 PreviewAcceptance 或审批 Version；
- 编辑态只在原位置增加选择、排序和配置控件；详情态只读；Workflow WorkUnit 详情继续复用相同行结构并附加 Run、Observation 与 Gate 状态；
- 不创建独立 Harness 预览对象、第二套详情 DTO 或独立展示状态；Trigger/EventHandler 明确标为 WorkUnit Harness 之外的事件入口/响应，不伪装成 WorkUnit 生命周期步骤；
- 普通 Project Setup 不出现原始 Harness Extension Binding，只显示固定 Harness Schema 要求的类型化业务参数和 CredentialBinding。替换 ExtensionPackage、顺序、Required/Optional 或配置合同必须在 Solution Studio 形成新的 package-local HarnessDefinition ID/config digest，并发布新的 Solution release。

Project Setup 使用“创建基础 Project → 选择 Solution → 配置并采用 Setup”的连续 Task Form，并遵循以下交互规则：

- 创建页只要求 `display.name`（Project 名称）与 `display.description`（Project 说明）；`owner_scope_id`（所属治理范围）在单一 Scope 时只读继承，多个有权 Scope 时才显示选择器；
- Solution 以逻辑卡片展示名称、说明、来源、精确版本、Package 检查和配置要求摘要。用户不手工填写 Solution ID、版本或 digest；
- 只有一个合格 Active package release 时直接选中；Policy 指定推荐精确 release 时默认选中并显示“推荐”；多个 release 且没有推荐项时必须展开 release 选择器，不使用 `latest`、最高 SemVer 或发布时间猜测；
- “继续配置”必须等待服务端完成 Authorization、Policy、ComponentInstall、digest 和 Package 检查校验。失败保留用户输入并显示具体 blocker，不创建空壳 Project；
- 成功后创建基础 Project，资源列表将 `solution_setup` 为空派生显示为“未配置”。“未配置”只是展示结论，不是新的 Project 状态；
- 当前选择的 Solution 只作为路由/表单编辑上下文。页面不能让用户误以为已经绑定，也不能提交 `selected_solution` 或半成品 `solution_setup`；
- 用户离开未完成 Setup 时显示未保存提示。首版重新进入时重新选择 Solution；页面可以恢复同一前端会话中的未提交控件值，但不得把浏览器缓存当作平台事实；
- Solution 固定的 BaselinePlanTemplate、显式 DAG Orchestration WorkUnit、终态业务路径、Harness 装配、平台托管 Preview 和默认 Runtime/Workspace/Governance Policy 以只读摘要展示，不伪装成用户必填项；
- `repository_binding` 字段不建设 Catalog 前置流程：类型化 Renderer 允许用户直接填写 `repository_uri` 并就地形成候选 Binding，私有仓库才显示一个 `credential_binding_id`，附加 Repository 按需增加；采用值保存 Binding 身份，Provider 类型、默认分支和连接结果由 Probe 后只读显示；Project Rules 不在 Setup 中填写；
- 单一 Repository CredentialBinding 在运行时仍由 CredentialBroker 按 Clone/Review/Push/PR/Release 用途签发最小权限短期 lease；页面不得暗示 Reviewer 会获得写权限，也不得回填 PAT/Token；
- Agent 岗位卡片的系统稳定键、名称、说明、Required/Recommended、基础能力和推荐模板来自 Solution；普通用户只选择一个或多个候选 AgentDefinition，一个表示精确绑定，多个表示无序池。具体 WorkUnit 的独立性由 Contract/Harness/Admission 决定，不在岗位卡片重复声明；模型、Skills、MCP、Runtime 和资源只读继承 AgentDefinition，不在岗位页面重复配置；内置 Software Delivery 不显示必填 `acceptor` 岗位，可选 Acceptance Executor 只在显式 Acceptance WorkUnit 需要 Agent 主执行时提示；
- 内置 Software Delivery 的 Preview 默认运行在平台当前 Kubernetes 集群并按 Workflow Namespace 隔离，普通用户不选择 Preview Credential；外部 Promotion、私有 Repository、固定 Harness 所需业务参数，或有权用户提交执行策略收紧项时才追加相应控件。Promotion 目标表单只收集目标类型、环境、Endpoint 和条件性 Credential，精确 DeploymentDriver 由系统解析并只读展示，不显示为 Harness Extension Binding；
- Repository、外部参数、Agent 岗位和条件性策略共同形成未保存的 `ProjectSetupCandidate`。页面使用一个“检查与采用”区域，不增加第二套输入表单；相关输入稳定后防抖自动预检，同时提供“重新检查”；
- 预检固定按 Solution/Component、Repository/External、Agent Slots、Runtime/Workspace/Environment、Authorization/Governance 五组展示。需要调用哪些资源检查由 `field_type` 自动派生，不要求作者或 Project 用户填写 Probe Capability；blocker 与 warning 分开，每个 blocker 必须显示稳定 code、中文说明、受影响对象、修复建议和直达修复动作；Required 外部结果 Unknown 或超时必须阻塞；
- 页面为表单变化维护本地候选 revision，并把服务端 `candidate_digest` 作为对应请求的只读摘要，不在浏览器复制规范化或哈希算法。任何相关输入变化都立即把旧结果标为“已过期”，并取消或忽略更早请求的迟到响应；不得把浏览器缓存、`ready` 或组件解析摘要当作平台事实；
- 当前 Candidate 的瞬时 `ProjectSetupEvaluation` 为 `ready` 后才允许进入保存与采用。保存端仍重新校验，随后以 `expected_revision` 原子替换完整 `Project.solution_setup`、递增 Project revision、重算 configuration digest 并写入 AuditEvent；在此之前不提供“创建 Workflow”主操作，也不提交 `component_resolution_preview_id`。
- 已采用 Project 的编辑继续复用同一 Task Form 和检查区域，并固定显示“不会热切换既有 Workflow”；不提供 Project 配置版本列表。Workflow 执行配置只影响之后创建的 Workflow；已完成 Delivery 上的新 `StartPromotion` 会在用户确认时读取并固定当前外部目标，但不会修改来源 Workflow；
- Workflow 详情需要采用新配置时只提供“按当前配置创建后继 Workflow”。页面先显示旧 `Workflow.spec` 与当前 Project/组件解析结果的影响分析，再提交正式 Decision；通过后复用 New Workflow 表单创建具有新 identity 和 `predecessorWorkflowName` 的普通根 Workflow；
- 后继页面只允许选择前序不可变 Artifact 作为输入，不复制 Active Plan、WorkUnit 状态、Gate 通过结果或审批。前序仍可能产生外部副作用时显示 blocker，并要求明确 Pause、Cancel、Abandon 或允许并行；不创建独立迁移向导或第二套 Workflow 详情。

Software Delivery 的 Release 页面使用只读 `ReleaseReadinessView` 展示已批准 Requirement、Acceptance Criteria、Design、Project Rules、固定集成提交、组件集合、构建定义、制品、验证 Observation、Acceptance Plan/suite digest、Release Gate 和 `release_input_digest`。前端不创建 `ReleaseCandidate` ViewModel 身份、Version、编辑表单或审批状态；Gate Pass 后同一路由切换为不可变 Release 详情。默认没有“确认发布”按钮，显式发布 Decision Boundary 继续使用通用 Decision Inspector。

最终验收 `approve` 经 Reconcile 使最终验收 Gate 通过，并确认活动 DAG 中所有已选择节点均成功、互斥未选择节点均 Obsolete 后，Release 详情直接显示 `Delivery: Succeeded` 与 `Release: Promotable`，不再要求用户点击“保持可推广”。“推广到外部环境”是独立可选操作：页面调用 `GetPromotionReadiness` 展示当前 Project 可用目标；用户只选择一个 `deployment_target_id` 并确认，`release_id`、`expected_project_revision` 和 `idempotency_key` 由页面携带。确认页只读展示 Release digest、环境、Endpoint 摘要、Executor 名称/版本和 Credential 就绪性，不暴露 Secret、Namespace、Component ID/digest 或 Harness。

`StartPromotion` 返回 `deployment_id + accepted_at` 后，页面先显示“部署命令已接受”，再订阅 Deployment 读模型。Pending、Running、Succeeded、Failed、Unknown、Cancelling 和 Cancelled 必须分别展示，不把 HTTP 2xx、Outbox delivered、Kubernetes Pod phase 或 CI/CD webhook accepted 当作业务成功。来源 Delivery 始终保持已完成；直接 Deployment 的失败或 Unknown 在独立部署详情处理，不在 Workflow DAG 中插入节点。页面与 API 中不得出现“Promotion Workflow”“Promotion WorkUnit”或 Kernel/Harness 运行步骤；企业多阶段发布使用单独的自定义 Workflow 体验。

Deployment 详情使用同一套信息区展示固定 Release/目标/Executor、`external_run_id`（外部执行身份）、expected/actual digests、最近 `report_sequence`（报告序号）、`observed_at`（观察时间）、结构化错误、恢复链和服务端计算的 `allowed_actions[]`（允许操作）。不为失败、重试和回退建立多套详情页。

| 当前 outcome | 页面主操作 | 明确禁止 |
| --- | --- | --- |
| Pending / Running | 自动刷新；“立即重新检查”；Executor 支持时“取消部署” | 创建第二个同目标部署 |
| Unknown | “立即重新检查”；条件性“取消部署” | “重新部署此版本”和“部署历史版本” |
| Cancelling | 展示取消处理中并继续自动刷新 | Retry、部署旧 Release、再次 Cancel |
| Succeeded | 查看结果与外部链接 | 恢复操作 |
| Failed / Cancelled | “重新部署此版本”；“部署历史版本” | 原地修改当前 Deployment |

“立即重新检查”只提交 `ObserveDeployment(deployment_id)`，重复点击由服务端合并，按钮不能暗示会重新 start。“取消部署”只提交 `CancelDeployment(deployment_id)`，响应后显示“取消请求已接受”，直到报告形成真实终态前不能显示 Cancelled。

“重新部署此版本”和“部署历史版本”共用现有 Promotion 确认页。前者只读带入相同 Release；后者只允许从服务端 `eligible_releases[]` 选择精确 ID/digest。页面为新请求生成新的 `idempotency_key`，隐藏携带原 `deployment_id` 作为 `caused_by_deployment_id`，然后再次调用 `StartPromotion`。成功返回新的 Deployment，页面时间线用 causation 串联前后记录；不展示或保存 `RolledBack`、RetryDeployment、RollbackDeployment 等对象。

## Interaction and Motion

动效只服务反馈、空间关系和状态解释。

- Button pointer-down：100–120ms，轻微 `scale(0.97)`；
- Tooltip 和小 Popover：125–180ms；
- Inspector 和 Sheet：可中断、无弹跳，约 240–360ms；
- 页面级共享元素只在 identity 连续时使用；
- 高频键盘导航、实时列表更新和批量数据加载不播放 stagger；
- 持续运行状态使用静态文字、轻量进度或低频变化，不使用循环发光；
- 优先动画 `transform` 和 `opacity`，避免布局抖动。

在 `prefers-reduced-motion: reduce` 下取消位移、缩放和 spring，只保留必要的短透明度过渡。用户操作必须在动画结束前即可继续，动画不能成为状态锁。

## Data and State Semantics

前端只展示 Platform Core 的权威对象或明确命名的只读 ViewModel。ViewModel 可以聚合扫描信息，但不能持久化为第二套领域事实。

状态与数据规则：

- Server State 由 Query Cache 管理；
- 导航、筛选、排序和可分享视图优先进入 URL；
- 瞬时展开、Hover 和未提交输入保留在组件本地；
- 只有出现真实、稳定的跨路由客户端状态时才引入额外 Store；
- EventSource 或 WebSocket 只通知“有新持久化事实”，历史重新从 Query API 获取；
- Optimistic UI 只用于可逆的本地偏好，不用于正式 Decision、Workflow command、Install、Revoke 或 Retry；
- Command API 返回 accepted identity，不代表状态已经收敛；
- 并发冲突必须重新读取最新权威对象、state/config revision 与 subject ID/digest，并让用户明确重审；
- Authorization、Admission、业务拒绝、并发冲突和基础设施错误使用不同反馈。

页面不得从多个布尔值拼装隐藏状态。必须直接展示固定枚举和对应原因。派生进度不得覆盖明确失败分支。

## Accessibility

最低目标是 WCAG 2.2 AA。

- 所有功能可通过键盘完成；
- `focus-visible` 清楚且不被阴影或 overflow 裁剪；
- Icon-only Button 必须有可访问名称和 Tooltip；
- 状态不能只依赖颜色；
- 普通文字对比度至少 4.5:1，大号文字至少 3:1；
- 触控环境目标尺寸至少 44×44px；桌面紧凑模式的可视控件可以更小，但可点击区域不得过小；
- Table 提供表头语义、排序状态和键盘可达操作；
- 动态更新使用有界的 live region，不朗读高频事件流；
- 图表必须有文字摘要、数据表或可访问替代；
- 错误信息与字段关联，并说明如何修复；
- 语言切换控件必须有可访问名称、当前语言状态和完整键盘操作；
- 布局支持文本扩展，不依赖固定语言宽度。

## Localization and Language Switching

首版正式支持 `zh-CN` 和 `en-US`。语言是用户展示偏好，不是 Project、Workflow、Scope 或 Solution 的业务属性。切换语言不得改变资源 identity、URL 中的业务主键、Query 条件、Workflow 状态、Decision subject 或用户输入。

### Locale resolution

产品应用按以下顺序解析语言：

1. 当前用户显式保存的语言偏好；
2. 未登录用户的本地偏好；
3. 浏览器首选语言；
4. `en-US` fallback。

显式选择永远优先于自动检测。用户登录后可以把未登录偏好迁移到个人设置，但组织和 Project 不能静默覆盖个人语言。

### Switch placement and behavior

- 登录、公开官网和技术文档在顶部导航直接提供语言入口；
- 登录后的产品应用在账户菜单中提供“语言”设置，Command Palette 也可以搜索该动作；
- 原型和设计验收页面可以直接显示紧凑的 `中 / EN` Segmented Control，方便比较；
- 不使用国旗表示语言；
- 切换产品语言时原地更新，不执行整页跳转；
- 保留当前 Route、Project、选中的资源、筛选、排序、滚动位置、Inspector 和未提交表单；
- 用户输入、Artifact 内容、日志、代码、命令输出和第三方原始内容不自动翻译。

### Routing strategy

产品 SPA 的资源路由不包含语言前缀。例如同一个 Workflow 始终使用同一个 URL；语言通过用户偏好解析。这样分享链接、浏览器历史、Resource identity 和授权判断不会因语言改变。

产品 Settings 使用以下语言无关资源路由：

```text
/settings/skills
/settings/model-providers
/settings/mcp
/settings/credentials
```

详情页在对应集合路由下使用稳定对象身份或版本身份。Scope、筛选、选中版本和 Inspector 状态优先进入 URL；Secret、测试输入和未提交表单不得进入 URL、浏览器历史或遥测。

公开官网与技术文档使用语言前缀：

```text
/zh-CN/docs/...
/en-US/docs/...
/zh-CN/...
/en-US/...
```

文档语言切换导航到同一文档 identity 的对应语言版本。对应翻译不存在时显示“该页面暂未提供此语言”，并允许查看 fallback 内容；不能返回无上下文 404，也不能静默跳转到无关页面。

公开官网、技术文档和案例使用同一份构建期页面清单。清单只服务静态内容路由，不进入 Platform Core：

| 字段 | 中文释义 | 约束 |
| --- | --- | --- |
| `page_key` | 页面稳定标识 | 同一内容的 `zh-CN` 与 `en-US` 版本共享；切换语言时按该值匹配，不从标题、文件名或 URL slug 猜测 |
| `locale` | 当前选择的页面语言 | 只允许已启用 locale；有语言前缀的 URL 是当前页面权威值 |
| `translation_status` | 翻译可用状态 | `available` 表示目标内容存在；`fallback` 表示显示相同 `page_key` 的 `en-US` 内容并明确提示 |

无语言前缀的首次入口按“未登录本地偏好 → 浏览器首选语言 → `en-US`”选择前缀 Route。进入带前缀页面后，URL locale 优先；显式切换更新本地偏好。目标语言缺失时保留目标语言 Route，渲染同一 `page_key` 的 `en-US` 内容，并显示“该页面暂未提供所选语言，当前显示英文版本”，不能返回首页、丢失当前章节或静默显示无关内容。

静态构建必须校验每个 `page_key` 的默认 `en-US` 内容存在、同一 locale 不重复、翻译映射唯一且站内链接可解析。目标翻译缺失可以发布并使用明确 fallback；默认内容缺失、重复 identity 或映射冲突必须阻止发布。

### Translation ownership

`localization` 模块统一拥有：

- Locale 解析与持久化；
- `changeLanguage`；
- 公共术语表；
- 日期、时间、相对时间、数字、百分比和单位 Formatter；
- 缺失 Key 检测；
- Product、Docs 和 SolutionPackage 领域体验的命名空间注册规则。

翻译资源按稳定边界拆分：

```text
locales/{locale}
├── common
├── navigation
├── settings
├── agent-capability
├── project-team
├── workflow
├── component-governance
├── governance
├── errors
└── documentation
```

禁止按页面文件建立大量零散翻译文件，也禁止把所有翻译放进一个全局巨型 JSON。Feature 只能使用自己的 namespace 和明确声明的公共 namespace。

### Canonical terminology

`Project`、`Workflow`、`WorkUnit`、`Artifact`、`Plan`、`Observation`、`GateResult`、`Decision`、`Run.kind`、`ComponentRun`、`AgentRun` 和 Component Kind 使用受版本控制的 Glossary。中文界面可以翻译说明和动作，但不能由页面自行发明近义词或改变对象含义。

状态显示使用固定映射，例如 `ReviewRejected` 可以显示为“Review 已拒绝”，同时在详情、复制和诊断位置保留 canonical enum。状态颜色、翻译文本和 canonical value 必须来自同一映射，不能各自维护。

### API and error boundary

- API 返回稳定的 machine-readable code、parameters 和 canonical facts，不返回仅适用于一种语言的业务文案；
- 前端根据 code 和 parameters 生成本地化消息；
- 未知 code 显示安全的 fallback 文案和可复制的 canonical code；
- Authorization、Admission、Conflict、Validation 和 Infrastructure Error 使用不同 namespace；
- 服务端生成的 Artifact、Observation 和 Decision 原因保持原始语言，不由 UI 伪造翻译。

### Formatting

日期、时间、相对时间、数字、百分比、货币、Token、CPU、内存和存储单位通过浏览器 `Intl` Formatter 输出。API 与 ViewModel 保留明确数值和单位，不传递预格式化字符串。

相对时间旁边必须可以查看精确时间。用户时区是独立于语言的偏好；切换语言不能改变时间点或默认时区。

### Fallback and completeness

- 生产 fallback 固定为 `en-US`；
- 开发环境缺失 Key 必须可见并记录，不允许静默显示空字符串；
- CI 检查两个 locale 的 Key 完整性、插值参数和复数规则；
- 发布页面不得出现无意的中英文混杂；
- 有意保留的 canonical term 不计为缺失翻译；
- 新 locale 只能通过完整 Glossary、关键流程和布局验证后启用。

### SolutionPackage 领域体验 localization

SolutionPackage 的声明式领域体验使用 `solution.{packageId}` namespace，并随精确 Package release 注册受控消息目录。它不能覆盖 `common`、`workflow`、`governance` 等平台 namespace，不能注入运行时代码加载器，也不能改变全局 fallback。

## Responsive and Density

响应式不是把桌面表格缩小到手机宽度，而是选择更适合当前宽度的表达。

- `>= 1440px`：完整应用壳层、多栏工作区和固定 Inspector；
- `1280–1439px`：标准桌面布局，允许可调整分栏；
- `900–1279px`：侧栏可折叠，Inspector 作为非模态 Sheet；
- `600–899px`：资源详情转为单主列，次级内容按 Section 展开；
- `< 600px`：聚焦关键读取和 Decision，复杂表格切换为分组列表。

Density 提供 `comfortable` 和 `compact` 两档。默认使用 comfortable；Audit、Usage、Run Timeline 等专业表面可以使用 compact。Density 只改变行高和间距，不改变信息架构、状态词或操作位置。

## Frontend Architecture

计划中的前端代码按业务能力和稳定边界组织，而不是按 React 文件类型组织：

```text
apps/web
├── app-shell
├── routes
└── composition-root

packages/frontend
├── design-foundations
├── localization
├── ui-primitives
├── ui-patterns
├── domain-view-models
├── platform-api
├── project-team-ui
├── settings-ui
├── agent-capability-ui
├── workflow-ui
├── component-governance-ui
├── governance-ui
└── solution-experience-host
```

依赖规则：

- `design-foundations` 不依赖 React、Astryx 或领域对象；
- `localization` 只拥有 Locale、Glossary、Formatter 和消息目录，不拥有领域状态或 API 调用；
- `ui-primitives` 只依赖 Foundations 和通过兼容性 Spike 后被精确锁定的 Astryx release，或同层唯一选定的 OAC-owned fallback；
- `ui-patterns` 只组合 Primitive，不拥有 OAC 领域状态；
- `domain-view-models` 是纯 TypeScript，不依赖 React、Astryx、HTTP 或生成 API 类型；
- `platform-api` 把 API DTO 映射为 ViewModel，并封装 Query、Command、Decision 和 Event Subscription；
- `settings-ui` 只提供统一 Settings Shell、Scope 切换、跨目录导航和聚合 Read Model，不拥有 Skill、模型、MCP 或 Credential 写入语义；
- `agent-capability-ui` 提供 Model Provider、Model Catalog 与 MCP 的领域 ViewModel 和表单；Skill 安装仍由 `component-governance-ui` 承接，Credential 仍由 `governance-ui` 承接；
- `solution-experience-host` 是平台内置 `ApplicationHost` 的前端实现，只解释精确 `SolutionPackage.experience` 并复用 `platform-api`、公共 ViewModel 与 UI Pattern；它不是可安装 Application、Extension 或第二套状态容器；
- 领域 UI 依赖 ViewModel 和公共 Pattern，不直接依赖数据库、Kubernetes、Controller、Kernel 或 Runtime 类型；
- Feature Route 组合 Use Case、领域 UI 和页面模式，不复制领域转换规则；
- Composition Root 是唯一装配 Router、Provider、API Adapter 和主题的地方；
- 组件依赖图必须无环。

不要为 Astryx 的每个 Primitive 制造一层一对一 Wrapper。`ui-primitives` 只维护受控导出、主题适配和确实需要稳定 OAC 合同的少数组件。复杂性必须被隐藏，而不是通过大量浅包装转移。

## Technology Selection

产品应用主栈固定为：

- React 19；
- TypeScript；
- Vite；
- React Router；
- TanStack Query；
- i18next 与 react-i18next；
- OAC CSS Variables 与 CSS Modules；
- Astryx core 作为首选 Primitive 和 Pattern 候选；当前公开状态为 Beta，因此必须先通过下述 Spike，不能被当作无条件稳定依赖；
- React Hook Form 与 Zod；
- Motion，仅用于空间连续性和可中断动效；
- 浏览器原生 EventSource 或 WebSocket；
- 浏览器原生 `Intl` Formatter；
- Storybook；
- Vitest、Testing Library、Playwright 和 axe。

Astryx 是外层设计系统实现，不是 OAC 架构。使用规则：

- 版本和 digest 精确锁定；
- 不使用 `@astryxdesign/lab`；
- 不使用只有 canary 版本的 Chart package；
- 不直接复制未标记 Ready 的页面模板；
- 先通过主题、键盘、屏幕阅读器、Bundle、SSR-free Vite 和升级 Spike；
- 需要改变组件内部实现时优先使用公开组合 API，只有必要时 swizzle 并由 OAC 接管源码；
- Astryx 类型不得进入 `domain-view-models`、Platform API 合同或 SolutionPackage 声明式体验 Schema。

M0 前端基础阶段必须用精确 Astryx release 完成一次可重复 Spike，输出依赖锁、Bundle、主题、键盘、屏幕阅读器、Vite、升级和必要组件覆盖结果。全部 Required 项通过后，首版统一采用该 release；任一 Required 项失败则统一切换到 shadcn/ui 风格的开放源码组件并由 OAC 持有实现，Mantine 只作为需要成熟完整组件集时的备选。一个构建不能同时维护两套 Primitive 来源；选择结果只影响 Primitive/Pattern 层，不得迫使领域 ViewModel 或 Platform API 重写。

首版不引入 Redux、Zustand、GraphQL、微前端 Runtime、任意前端脚本 Extension Loader 或 Next.js Server Component。只有真实需求证明现有结构不足时才增加概念。

## Delivery Forms and Routing

OAC 统一设计语言，但不强迫所有前端表面使用同一种渲染形式。

| 表面 | 长期交付形式 | 原因 |
| --- | --- | --- |
| 产品控制台 | React SPA | 长时间会话、客户端导航、Query Cache、实时通知和工作区连续性 |
| 登录与产品引导 | 产品 SPA Route | 与认证后上下文共享主题、语言和跳转合同 |
| SolutionPackage 声明式领域体验 | SPA 内受控 Route、Form/Layout/Renderer 槽位 | 复用 ApplicationShell、公开 Platform API 与标准 Read Model，不建立独立 Runtime 或可执行 Package |
| 项目官网 | SSG | 内容稳定、公开索引、快速首屏和低运行复杂度 |
| 技术文档 | SSG | Markdown/Reference 内容、语言前缀、版本化与搜索索引 |
| 设计原型 | 独立静态 HTML | 只验证视觉和交互，不代表正式 Runtime 或工程实现 |

产品 SPA 是长期交付方案，不是临时过渡。SPA 只表示产品 Route 在浏览器中进行客户端导航，不表示一个巨型 Bundle、没有 URL、把业务状态保存在浏览器或所有页面都必须客户端渲染。

产品 SPA 必须：

- 使用真实、可刷新和可分享的 Route；
- 按 Route 和高成本 Feature 拆分 Bundle；
- 让 Platform Core 保持 Server State 权威；
- 使用 Query Cache 而不是第二套状态机；
- 将可分享筛选和选择状态放入 URL；
- 在 Web Server 配置未知产品 Route 回退到应用入口；
- 将公开内容、文档 SEO 和静态页面留给 SSG 表面。

项目官网与技术文档共享顶部产品导航、语言切换和内容身份规则，但不共享产品 Console 的 Server State 或认证 Session。公开入口的主导航至少覆盖产品概览、行业方案/案例和技术文档；“开始安装”进入当前语言的安装文档，“进入控制台”只跳转到目标部署的产品 SPA，再由登录与 Scope 流程接管。浏览公开内容不会预创建账户、Project 或其他平台资源。

首版产品 SPA 不提供公开注册页面。平台尚未初始化时只显示受一次性 Bootstrap Code 保护的首个管理员页面；初始化完成后只显示登录、接受邀请和恢复入口。普通用户由管理员邀请并自行设置密码；配置邮件时发送链接，未配置时由管理员复制仅展示一次的链接或 Code。唯一 Local Scope 在登录后自动进入，账户菜单持续显示当前 Scope；未来多个授权 Scope 才增加显式选择页面。Users 管理页面不得让管理员设置或查看普通用户密码。首个 Scope 与安全默认 AuthorizationPolicy 原子创建；只有拥有 `governance.authorization.manage` 的管理员能进入统一授权策略页面，普通用户不需要配置授权引擎。

当前 `docs/design/frontend/prototypes/oac-frontend-system-demo.html` 是无 React、无 Router、无 API、无 Astryx Runtime 的静态高保真原型。它可以模拟 View 和语言切换，但不能作为生产 SPA 代码直接演进。

## SolutionPackage 声明式领域体验

SolutionPackage 领域体验是随精确 SolutionPackage 发布的受控声明式内容，不是独立前端 Package 或任意脚本扩展。

- 只能通过公开 Platform API、标准 Read Model 和受控 Form/Layout/Renderer Schema 工作；
- 使用公开的 OAC Foundations、Patterns 和 Host Context；
- 可以提供领域术语、表单、页面、聚合视图和 Artifact Renderer；
- 不得访问数据库、CRD、Kubernetes Client、Controller、Kernel、AgentRun Job 内部接口、Workspace Port 或 Harness Host；
- 不得拥有 canonical state 或建立第二套写路径；
- 不得注入任意脚本、全局 CSS、Router 或状态管理容器；
- 没有声明式领域体验的 Solution 必须仍可通过 Generic Workflow Web 完整运行；声明无效或渲染失败时必须回退到同一 Generic Web。

扩展点以明确槽位和 DTO 表达，例如 `resource-summary`、`artifact-renderer`、`workflow-secondary-panel` 和 `solution-settings-section`。禁止暴露万能 `render(anyContext)` 或整个应用对象。

## Testing and Quality Gates

每个组件和页面至少通过以下适用检查：

1. Token：没有无理由的硬编码颜色、间距和圆角；
2. Semantics：UI 状态能映射到权威对象或明确 ViewModel；
3. Accessibility：键盘路径、焦点、名称、对比度和减少动态效果；
4. Responsive：至少验证 1440px、1024px、768px 和 390px；
5. Async：Loading、Empty、Error、Stale、Accepted、Conflict 和 Retry；
6. Security：授权拒绝不泄露其他 Scope 或秘密；
7. Visual Regression：主要 Pattern 和页面模式保留基准截图；
8. Domain Verification：不创建第二状态机，不合并不同 Run 合同；
9. Performance：列表虚拟化按真实数据量启用，不默认引入重型 Data Grid；
10. Compatibility：Token 或 Pattern 变更提供迁移说明，不能静默破坏已发布 SolutionPackage 领域体验。
11. Localization：两个 locale 的关键流程、Key 完整性、文本扩展、Formatter、语言保持和 fallback 均通过。

页面验收必须基于用户任务，而不是“看起来像原型”。例如 Workflow 页面要验证用户能理解受阻原因、找到正式证据、完成 Decision 并看到命令后续收敛。

## Design Governance

本文件是设计决策的单一入口。后续实现应从本文件生成或维护 CSS Variables、Design Token 导出和 Storybook 文档，但生成物不能反向成为第二权威来源。

全局改变流程：

1. 说明真实问题和受影响页面；
2. 判断能否通过现有 Token、Pattern 或页面模式解决；
3. 评估对已发布页面和 SolutionPackage 领域体验的破坏性；
4. 更新本文件；
5. 运行结构、Token 和 WCAG 检查；
6. 更新 Storybook、原型和视觉回归；
7. 提供迁移或兼容策略。

页面特定设计评审只回答该页面新增的任务、数据和交互。重复讨论全局 Button、颜色和字体通常意味着没有复用本规范。

## Reference Implementations

参考原型用于验证本规范能覆盖不同页面类型，不是产品状态或 API 的第二权威来源。

- Workflow Operations Center：Operations Workspace 的参考实现；
- Agent Center：Collection、Agent 身份插画和 AgentDefinition 摘要的参考实现；
- Project Overview：ApplicationShell、Collection 和 Resource Detail 的参考实现；
- Package & Extension Governance：Governance Data 和 Task Form 的参考实现；
- Technical Documentation：Documentation 和 Public Pattern 的参考实现。

页面特定规范：[Workflow Operations Center Frontend Design](workflow-operations-center-design.md) 和 [Agent Center Visual Design](agent-center-visual-design.md)。这些文档必须服从本文件。

### Language Switching

产品 SPA 与技术文档使用相同 Glossary 和视觉 Token，但采用不同路由策略。以下对照验证语言切换不会改变页面结构、资源 identity 和操作位置。

| `zh-CN` 产品应用 | `en-US` 产品应用 |
| --- | --- |
| ![Project Overview zh-CN frontend prototype](prototypes/project.png) | ![Project Overview en-US frontend prototype](prototypes/project-en.png) |

| `zh-CN` 技术文档 | `en-US` 技术文档 |
| --- | --- |
| ![Technical Documentation zh-CN frontend prototype](prototypes/docs.png) | ![Technical Documentation en-US frontend prototype](prototypes/docs-en.png) |

### Project Overview

验证 ApplicationShell、Collection、Resource Detail、Attention Queue 和团队责任摘要。

![Project Overview frontend prototype](prototypes/project.png)

### Agent Center

验证 Collection Toolbar、Agent 卡片信息密度、`AgentPortrait` 的身份边界，以及头像缺失时名称和操作仍然可用。当前选定的温暖手绘动画方向只修改头像区域，不改变 AgentDefinition、状态、模型、Runtime、Skills、MCP 或 Runs 的语义与布局。

![Agent Center warm hand-drawn portrait prototype](prototypes/generated/oac-agent-center-anime-final.png)

### Workflow Operations

验证 Operations Workspace、WorkUnit 导航、Evidence 阅读、Agent/Component/human Run 下钻、跨 Project Attention、“我的工作”结构化提交和有界 Decision Inspector。人工提交 accepted 与 Gate converged 必须分开显示，并覆盖两名合格用户并发提交时首个有效 CAS 获胜、后提交者输入保留的场景。

![Workflow Operations frontend prototype](prototypes/workflow.png)

### Package & Extension Governance

验证 Governance Data、高密度表格、Lifecycle 语义和组件详情 Inspector。

![Package & Extension Governance frontend prototype](prototypes/governance.png)

### Platform Operations

验证综合健康、数据新鲜度、四组健康检查、当前活动告警、执行容量、unknown/partial coverage、诊断权限和修复导航。页面刷新或失效通知只能重新查询同一 Platform API；不得在浏览器内推断总体健康、缓存上次绿色结果、直接读取 Kubernetes/Prometheus，或提供告警确认与静默状态。

### Technical Documentation

验证 Documentation 页面模式、阅读宽度、技术图示、代码块和目录导航。

![Technical Documentation frontend prototype](prototypes/docs.png)

四个视图共享同一份可编辑原型：[OAC Frontend System Demo](prototypes/oac-frontend-system-demo.html)。通过 `?view=project`、`?view=workflow`、`?view=governance` 或 `?view=docs` 切换页面模式，通过 `?lang=zh-CN` 或 `?lang=en-US` 切换语言。

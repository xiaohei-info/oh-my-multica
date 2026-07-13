# OMAC V2 云原生自主软件交付 Agent Cluster 业务解决方案设计

> 文档状态：第二轮评审稿
>
> 文档日期：2026-07-13
>
> 方案范围：OMAC V2 独立产品体系
>
> 目标读者：产品负责人、架构负责人、研发负责人、平台运维负责人

---

## 1. 已确认决策摘要

本方案建立在以下已经确认的产品决策之上。

1. OMAC V2 不再依赖 Multica 或其他 Issue 系统完成任务分发、Agent 唤醒、状态流转和结果交接。
2. OMAC V2 的产品核心是自主软件交付 Agent Cluster，而不是通用多 Agent 聊天平台，也不是单机 CLI 编排工具。
3. 用户从一个初始需求开始，OMAC 负责组织 Agent Team 持续推进需求澄清、架构设计、任务规划、开发、评审、测试、安全、发布、部署和生产验收。
4. OMAC 的 Web、API、控制器、数据库和 Agent 执行任务统一运行在 Kubernetes 体系中。
5. 产品同时支持两种交付模式：
   - 内置轻量集群：面向单机和少量节点，采用 K3s。
   - 外部集群接入：面向已有基础设施，接入标准 Kubernetes。
6. 两种模式使用相同的产品镜像、领域模型、资源定义和业务验收，不维护两套产品实现。
7. 用户只通过 OMAC Web、API 和 CLI 使用产品，不需要进入外部 Issue 平台查看任务或操作状态。
8. Delivery 是一次完整软件交付的根对象；Workflow 只作为 Delivery 内部可选的任务组织方式，不再代表最终交付结果。
9. Task 是长期业务节点，AgentRun 是一次不可变执行；评审、测试、安全、部署和验收统一使用 Task、AgentRun、Evidence 和 GateResult 模型。
10. Manifest 不再承担运行时状态存储，只作为 Delivery 模板或任务图的导入、导出和版本化格式。
11. OMAC 首版只支持单组织，不引入多组织计费、跨组织隔离和跨组织协作模型。
12. 产品不强制安装对象存储。代码归 Git，结构化状态和证据归 OMAC，长期日志和大文件存储作为可选集成。
13. OMAC 的核心控制平面、开放 API、Web/CLI、集群资源定义、安装方案和参考 Runtime Adapter 以开源方式交付。
14. OMAC V2 不兼容 OMAC V1：
    - 不保留 Multica 兼容层。
    - 不支持旧 Manifest 运行状态迁移。
    - 不保持旧 CLI、API 和配置文件兼容。
    - 不设计 V1/V2 双写、双读或原地升级链路。
    - V1 文档和代码只作为业务经验参考，不约束 V2 领域模型。

---

## 2. 使命、愿景与产品定位

### 2.1 使命

让用户只需表达软件目标，就能依靠一个自主 Agent Team 完成从需求澄清到生产验收的完整软件工程交付。

OMAC 不以“生成了多少代码”作为成功标准，而以“是否交付了可部署、可验证、可审计、可回滚的生产结果”作为成功标准。

### 2.2 愿景

让个人和团队都能拥有一个开源、可私有部署、可持续扩展的软件工程 Agent Team。

用户增加机器、模型能力和 Token 预算后，OMAC 能够将这些资源转化为更高的软件交付吞吐，而不再受单机编排器、外部 Issue 引擎或单一 Coding Agent 工作方式限制。

最终形态不是“一个更聪明的 Coding Agent”，而是：

> 一个运行在用户自己基础设施中的自主软件交付组织。

### 2.3 产品定位

OMAC 的正式定位是：

> 开源、云原生、运行时无关的自主软件交付控制面。

英文定位：

> Kubernetes-native Autonomous Software Delivery Control Plane

OMAC 不与具体模型或 Coding Agent 比较代码生成能力。OMAC 负责组织异构 Agent Runtime，管理软件交付责任、质量制度、资源预算、返工边界和最终生产验收。

### 2.4 产品承诺边界

OMAC 承诺提供可控、可恢复和可验证的交付过程，不承诺任何需求都能完全无人介入，也不承诺生成的软件天然没有缺陷。

以下场景必须允许人工决策：

- 需求存在无法推断的产品选择。
- 变更涉及高风险数据迁移、兼容性或安全影响。
- 生产发布需要组织授权。
- 预算超限、返工超过上限或外部依赖长期不可用。
- 验收条件之间发生冲突。

---

## 3. 背景与行业需求

软件工程 Agent 市场已经从单一代码补全，发展到后台 Coding Agent、并行 Agent、Agent Team、软件生命周期 Agent 和云原生 Agent Runtime。

Factory Software Factory 已经将产品愿景定义为从信号、规划、执行、验证、发布到生产监控的完整软件生命周期；GitLab Duo Agent Platform 正在将多个 Agent 和 Flow 嵌入计划、开发、评审和安全环节；Devin 已经支持由一个协调者拆分大型任务并管理多个隔离执行的 Devin；MetaGPT 等开源项目很早就验证了“软件公司作为多 Agent 系统”的用户理解方式。

这些产品与 OMAC 愿景相近，不代表方向缺少独创性，反而说明以下行业诉求真实存在：

- 用户希望从目标出发，而不是逐条编写 Coding Prompt。
- 大型软件任务需要并行拆解、职责分工和持续协调。
- 代码生成只是中间环节，评审、测试、安全、发布和运行结果同样重要。
- 长周期 Agent 工作必须可恢复、可追踪、可停止和可重新规划。
- 企业和专业用户需要私有部署、权限、审计、预算和模型选择权。
- Agent 的最终价值必须与真实交付结果关联，而不是与 Token 或代码行数关联。

现有市场同时证明：单纯提供“多个 Agent”或“运行在 Kubernetes 上”已经不足以形成差异化。OMAC 必须将竞争力建立在完整软件交付闭环和开放云原生基础之上。

---

## 4. 现状问题

### 4.1 产品控制权不完整

现有任务是否被接收、何时启动、由哪个 Agent 执行以及如何交接，依赖外部 Issue 平台规则。OMAC 只能间接观察和推动任务，无法形成自己的控制闭环。

### 4.2 用户体验被外部系统割裂

用户需要在 OMAC、终端、Issue 页面和代码托管平台之间切换，无法在一个产品界面中看到需求、设计、任务、Agent、资源、成本、异常、部署和最终结果。

### 4.3 状态事实源不唯一

工作流定义、Manifest 运行状态、外部 Issue 状态和本地文件可能同时表达同一任务，容易产生状态漂移、并发覆盖和恢复歧义。

### 4.4 扩展能力受单机和外部平台限制

现有流程无法自然利用多节点资源，也不能根据 CPU、内存、GPU、Agent 能力和节点负载进行统一调度。增加机器并不等于增加可用 Agent 并发。

### 4.5 领域模型被 Issue 语义限制

任务被表达为一条会被反复分配、评论和修改状态的 Issue。一次任务的多次执行、评审、返工和验收没有独立、不可变的运行记录，不利于审计、成本分析和故障恢复。

### 4.6 完成定义停留在代码集成

现有体系的核心闭环集中在计划、开发、评审、CI、合并和总控验收。它还不能完整表达环境准备、发布候选、部署、生产冒烟、运行健康和回滚。

“代码已经合并”不等于“用户已经得到可生产使用的产品”。

### 4.7 模糊需求缺少正式澄清阶段

当前 Planner 假设输入已经足够形成设计和验收方案。真实用户经常只给出一句目标，系统必须先识别用户、场景、范围、约束、非目标和关键决策，不能直接开始拆任务。

---

## 5. 核心判断

### 【核心判断】

✅ 值得做：市场已经验证自主软件工程 Agent Team 的真实需求，但尚未形成一个同时满足开源、自托管、Kubernetes 原生、异构 Runtime、确定性质量控制和生产交付验收的通用产品。

OMAC 不应宣称发明了 Agent Team、Software Factory 或 Kubernetes Agent Platform。OMAC 的机会在于将这些已被验证的能力组合成一个用户可拥有、可审计、可扩展的软件交付控制面。

### 【关键洞察】

- 数据结构：核心关系是 Delivery → Task → AgentRun → Evidence → GateResult，Agent 只是执行者，不是事实来源。
- 复杂度：评审、测试、安全、部署和验收不需要各自拥有一套特殊引擎，它们都是具有不同合同和证据要求的 Task。
- 云原生边界：Kubernetes 调度机器，OMAC 调度软件交付责任。
- 差异化：开源与自托管只是进入市场的条件，真正壁垒是生产交付状态机、证据驱动质量门和长期自主收敛能力。
- 风险点：最大的风险是做成一个通用 Agent 基础设施平台，却没有证明能够比单一 Coding Agent 更稳定地交付生产产品。

### 【技术方案原则】

1. 第一步简化并重建领域数据结构。
2. 以 Delivery 作为唯一交付根对象和最终状态出口。
3. 使用统一 Task 模型消除评审、测试、部署等特殊执行分支。
4. 让正常执行、评审、返工和基础设施重试都产生新的不可变 AgentRun。
5. 使用确定性 Controller 管理状态、预算和质量门，LLM 不直接控制系统状态。
6. 复用 Kubernetes、Agent Runtime、Sandbox、Git 和 CI/CD，不自行重复实现成熟基础设施。
7. 不引入兼容层、双写链路和无明确收益的存储组件。

---

## 6. 业务目标与成功指标

### 6.1 产品目标

OMAC V2 交付后，用户安装或连接集群即可获得一个完整的自主软件交付 Agent Cluster：

- 用户在 Web 中提交一个初始软件需求。
- 系统完成需求澄清、设计、计划和验收条件建立。
- 系统自动组织不同能力的 Agent 形成交付团队。
- 系统根据任务依赖、Agent 能力、集群资源和预算并行推进工作。
- 每个阶段通过结构化证据和独立质量门决定是否继续。
- 失败自动形成有界返工，不覆盖历史，也不无限消耗 Token。
- 系统完成发布、部署、部署后验证和最终生产验收。
- 用户在一个控制台中查看全过程、成本、风险、决策和最终交付证据。
- 控制组件重启或工作节点故障后，未完成 Delivery 可以继续收敛。

“瓶颈只剩机器和 Token”作为产品方向成立，但不能被解释为绝对承诺。模型供应商限流、代码托管平台、网络质量、数据库容量、仓库工程质量和生产授权仍可能成为外部约束。V2 的目标是消除 OMAC 自身的单机编排和外部 Issue 引擎瓶颈。

### 6.2 建议成功指标

| 指标 | 目标 |
|---|---|
| 外部任务引擎依赖 | 任务分发和状态流转不依赖任何外部 Issue 系统 |
| 开箱即用 | 支持环境中，Compact 模式从安装开始到 Web 可用不超过 15 分钟 |
| 首次交付 | 安装完成后，用户可在 30 分钟内启动首个示例 Delivery |
| 状态可见性 | 所有 Delivery、Task、AgentRun、Evidence、GateResult 和 Decision 均可在 Web 查询 |
| 故障恢复 | 控制组件重启不丢失任务，未完成 Delivery 可继续推进 |
| 调度扩展 | AgentRun 不存在固定单机绑定，容量随集群可调度资源增加 |
| 成本治理 | 每次 AgentRun 记录模型、Token、金额、时长和预算结果 |
| 质量闭环 | Delivery 只有在必需质量门和生产验收通过后才能成功 |
| 状态一致性 | 重复事件和重复提交不得产生重复终态、重复账本或重复发布 |
| 可替换性 | 替换 Agent Runtime 或模型供应商不改变 Delivery 领域模型 |
| 可恢复性 | 任何非终态 Delivery 均可解释下一步、阻塞原因和恢复动作 |

### 6.3 产品价值指标

OMAC 的长期价值指标不是 Agent 数量，而是：

- 生产验收通过率。
- 从需求到生产的周期。
- 人类介入次数。
- 单次成功交付成本。
- 自动返工收敛率。
- 集成冲突率。
- 逃逸缺陷数量。
- 发布与回滚成功率。

---

## 7. 范围与非目标

### 7.1 本期范围

- 单组织下的项目、用户和权限管理。
- 需求澄清、架构设计、交付规划和验收条件建立。
- Delivery、Task、AgentRun、Evidence、GateResult 和 Decision。
- AgentProfile、Runtime Adapter 和能力匹配。
- Agent 任务调度、运行、停止、超时、心跳和结果提交。
- 开发、评审、测试、安全、集成、发布、部署和生产验收闭环。
- 有界返工、预算控制、风险接受和人工决策。
- Web 控制台、统一 API 和 CLI。
- 单机或少量节点 K3s 安装。
- 外部 Kubernetes 集群安装。
- 运行状态、历史记录、Token 账本和审计。
- Git、模型供应商、CI/CD、部署系统和通知系统的外部集成边界。
- 开源核心控制平面和参考 Runtime Adapter。

### 7.2 明确非目标

- 不兼容或迁移 OMAC V1。
- 不支持 Multica、Linear、Jira 等 Issue 平台作为任务事实源。
- 不建设多组织 SaaS 计费和跨组织数据隔离。
- 不在首版建设跨 Kubernetes 集群统一调度。
- 不把 OMAC 做成通用 CI/CD 平台。
- 不自研通用模型网关。
- 不自研通用 Agent 对话、记忆或无代码 Agent Builder 平台。
- 不自研容器调度器、批调度器或 Sandbox 内核。
- 不强制建设对象存储、制品中心或长期日志平台。
- 不要求 OMAC 接管 Git 仓库、PR、镜像仓库和生产集群的数据主权。
- 不以支持尽可能多的 Agent 框架作为首版目标。
- 不在首版建设通用连接器市场。

---

## 8. 业务角色与协作原则

### 8.1 用户角色

| 角色 | 主要职责 |
|---|---|
| 平台管理员 | 安装 OMAC、接入集群、配置全局资源、安全和 Runtime 策略 |
| 项目负责人 | 创建项目、配置仓库、环境、Delivery 策略和生产授权边界 |
| Delivery 发起人 | 提交目标、确认需求、启动交付、查看结果 |
| 决策处理人 | 处理范围冲突、高风险失败、预算超限、验收不通过和生产授权 |
| 审计/观察者 | 查看任务历史、Token 使用、操作记录、部署和验收证据 |
| OMAC 控制平面 | 维护 Delivery 状态、计算就绪任务、匹配 Agent、创建运行并执行状态转换 |
| Agent | 接收一次 AgentRun，读取授权上下文，执行任务并提交结构化结果 |

单组织不等于无权限。首版仍需区分平台管理、项目管理、操作、生产授权和只读权限。

### 8.2 Agent Team 能力角色

Agent Team 的角色不固化为独立服务，而通过 AgentProfile.capabilities 描述：

- 产品与需求。
- 系统架构。
- 前端工程。
- 后端工程。
- 数据工程。
- 代码评审。
- 测试与质量。
- 安全评审。
- 集成与发布。
- 部署与运维。
- 独立产品验收。

同一个 Runtime 可以提供多种能力，不同 Runtime 也可以提供同一种能力。OMAC 根据任务合同、能力、权限、成本和负载选择执行者。

### 8.3 协作原则

1. 每个 Delivery 只有一个根责任对象和一个最终交付出口。
2. Agent 不能直接修改 Delivery 或 Task 状态，只能通过 API 提交当前 AgentRun 的心跳、结果和证据。
3. 实现者不能作为自己工作的最终评审者或最终验收者。
4. Agent 内部同步调用的子 Agent 不自动成为正式 Task；只有需要独立追踪、异步执行、预算或验收的委派才进入任务图。
5. 任何 Agent 建议的任务新增、依赖变更或状态转换，都必须经过确定性规则校验。

---

## 9. 行业产品调研与竞争定位

### 9.1 调研结论

截至 2026-07-13，市场已经存在以下四类产品：

1. 软件公司式多 Agent 框架：MetaGPT、ChatDev 等。
2. 并行 Coding Agent 与 Agent Team：Factory、Devin、GitHub、Claude Code 等。
3. 软件生命周期 Agent 平台：Factory Software Factory、GitLab Duo Agent Platform。
4. 云原生 Agent Runtime 与编排平台：AgentTeams、kagent、OpenHands、Kubernetes Agent Sandbox 等。

没有必要否定这些产品的成熟能力。OMAC 应主动借鉴其已验证设计，并把研发资源集中到仍未被完整解决的软件交付控制问题。

### 9.2 主要对标产品

| 产品 | 已验证能力 | OMAC 借鉴点 | OMAC 差异化 |
|---|---|---|---|
| Factory Software Factory | 从信号到规划、执行、验证、发布和监控的完整 SDLC；模型无关；支持 SaaS、混合、私有和隔离部署 | 全生命周期产品叙事、质量门、结果指标、持续自动化 | 核心控制面开源；用户自有 K3s/K8s；开放数据模型；确定性 Delivery 状态机 |
| GitLab Duo Agent Platform | 多个 Agent/Flow 进入计划、开发、评审和安全；天然连接 Issue、仓库、MR 和 CI/CD | 生命周期上下文统一、复合身份、Agent/Flow Catalog、开发过程可视化 | 不绑定单一 DevOps 平台；以 OMAC Delivery 为事实源；可接任意 Git 与 CI/CD |
| Devin Managed Devins | 协调者拆解任务、启动多个隔离 VM、监控成本、解决冲突并汇总结果 | 子任务并行、隔离执行、协调者视图、每个子运行预算 | 异构 Agent Team；显式任务图；独立质量门；生产验收和开放部署 |
| AgentTeams | Kubernetes 原生 Worker、Team、Human、Manager 资源；声明式团队、协作审计和人工介入 | CRD + Reconcile、团队拓扑、Worker 生命周期、人类介入、开放协议 | 不以聊天 Room 作为运行事实；Delivery 状态机和软件工程证据是核心 |
| OpenAI Symphony | 持续领取工作、独立工作区、并发限制、重试、重新协调和权威调度状态 | 单一调度所有者、工作区生命周期、重试队列、运行恢复、配置热更新 | 不依赖外部 Issue；支持项目级 Delivery、多角色任务图和生产交付 |
| kagent | Kubernetes 原生 Agent CRD、多 Agent、开放协议、可观测性、AgentHarness | 声明式 Agent、BYO Agent、A2A/MCP、Runtime 生命周期、开放社区模式 | OMAC 不做通用 Agent 平台；专注软件交付领域控制面 |
| Kubernetes Agent Sandbox | Kubernetes 上隔离 Agent 环境和工作区生命周期 | Sandbox 声明式资源、隔离、暂停恢复、预热池 | 作为可选执行原语，不成为 OMAC 领域模型 |
| OpenHands | 开源、模型无关的 Coding Agent 平台；远程 Agent Server；私有部署和 Kubernetes 安装 | 成熟 Coding Agent Runtime、远程隔离工作区、SDK/API | OMAC 负责 Agent Team、任务依赖、质量门、预算和最终交付 |
| MetaGPT | 一行需求、产品经理/架构师/工程师等角色和软件公司 SOP | 角色化工程流程、需求到设计的用户理解方式 | 云原生持久运行、真实仓库集成、确定性质量门和生产验收 |

### 9.3 独创性判断

OMAC 不拥有以下概念的独创性：

- 多 Agent。
- Agent Team。
- AI 软件公司。
- 并行 Coding Agent。
- Kubernetes 原生 Agent。
- Software Factory。

OMAC 可以建立独特竞争力的组合是：

> 开源、自托管、Kubernetes 原生、异构 Agent Runtime、从模糊需求到生产验收、确定性推进、证据驱动、有界返工和硬预算决策。

### 9.4 产品路线比较

| 路线 | 优点 | 问题 | 判断 |
|---|---|---|---|
| 建设通用 Agent Cluster 平台 | 市场范围大，可承载多种 Agent 场景 | 与 kagent、AgentTeams、云平台正面竞争，范围巨大，软件交付差异弱 | 不采用 |
| 从调度、Sandbox、Runtime 到 CI/CD 全部自研 | 控制力最强 | 重复建设成熟基础设施，无法快速验证核心价值 | 不采用 |
| 建设自主软件交付控制面，复用开放运行底座 | 聚焦生产交付差异，可组合最强 Coding Agent，可持续替换底层能力 | 需要自行建立完整 Delivery 领域状态机和质量制度 | 采用 |

### 9.5 差异化原则

1. 开源不是宣传标签，而是用户能够审查控制逻辑、部署在自己环境并替换 Runtime 的产品权利。
2. 自托管不是企业附加项，而是 Compact 和 External Cluster 两种标准交付形态。
3. 模型和 Agent Runtime 必须可替换，避免任何单一供应商成为 OMAC 产品内核。
4. Agent 的自然语言结论不能直接成为完成事实，必须转化为结构化 Evidence 和 GateResult。
5. OMAC 的最终指标是生产结果，不是 Agent 数量、Session 数量、Token 数量或代码行数。

---

## 10. 云原生成熟方案复用判断

### 10.1 复用分层

| 层次 | 参考方案 | 复用内容 | OMAC 不重复实现 |
|---|---|---|---|
| 集群与资源调度 | Kubernetes / K3s | Pod/Job 调度、requests/limits、亲和性、优先级、配额、故障重建 | 节点调度器、容器生命周期 |
| Agent 隔离工作区 | Kubernetes Agent Sandbox 或等价实现 | 隔离环境、工作区生命周期、暂停恢复、预热 | Sandbox 内核、虚拟化隔离 |
| Agent Runtime | OpenHands、kagent AgentHarness、Codex、Claude、自定义容器 | Coding Agent、工具调用、远程执行、会话协议 | 通用 Coding Agent 能力 |
| 资源准入与队列 | Kubernetes 原生配额，必要时 Kueue | 高负载时的准入、队列、公平共享 | 自研批调度器 |
| 代码与协作 | Git 平台 | 仓库、分支、提交、PR、Review 原始事实 | 代码托管和版本控制 |
| 构建与发布 | 用户现有 CI/CD | 构建、测试、签名、镜像、部署流水线 | 通用 CI/CD |
| 可观测性 | Kubernetes 日志与开放可观测体系 | 运行日志、指标、事件和告警 | 自研长期日志平台 |

### 10.2 借鉴 AgentTeams

建议借鉴：

- 使用声明式资源表达 Agent 和团队期望状态。
- Controller 持续 Reconcile，而不是依赖一次性脚本。
- Worker 生命周期与逻辑身份分离。
- 人类介入是正式能力，不是异常旁路。
- Agent 间通信和操作过程需要可审计。

不直接采用：

- 不将聊天 Room 作为任务状态和协作事实源。
- 不要求所有交付内容进入共享对象存储。
- 不让 Manager Agent 成为唯一项目状态所有者。

### 10.3 借鉴 kagent

建议借鉴：

- Agent 作为 Kubernetes 一级工作负载。
- 使用 CRD、Controller、标准协议和开放 Runtime。
- AgentHarness 式远程 Coding Agent 生命周期。
- BYO Agent 和 BYO Model。
- 使用 Kubernetes RBAC、可观测性和 GitOps 习惯管理 Agent。

OMAC 与 kagent 的关系应保持可选集成：

- OMAC 可以把 kagent AgentHarness 作为一种 AgentRuntime。
- OMAC 不依赖 kagent 才能运行。
- OMAC 不复制 kagent 的通用 Agent 管理和 DevOps Agent 能力。

### 10.4 借鉴 OpenAI Symphony

建议借鉴：

- 只有一个调度所有者修改调度状态。
- 每个工作单元拥有隔离工作区。
- 运行、重试、停止和释放具有明确状态。
- 重新协调负责修复控制状态与实际运行状态的偏差。
- 配置变更失败时保留最后一个有效配置，不让控制器崩溃。

OMAC 需要超越：

- 任务事实不依赖外部 Issue。
- 一个 Delivery 可以包含动态任务图和多个角色。
- 生产发布和验收属于核心状态机。

### 10.5 借鉴 Kubernetes Agent Sandbox

Agent Sandbox 适合作为需要长生命周期工作区的 AgentRun 执行原语：

- 隔离代码执行。
- 保持工作区身份。
- 暂停和恢复。
- 预热执行环境。
- 对高风险 Runtime 使用更强隔离。

Compact 首版可以先使用 Kubernetes Job 和临时卷形成最小闭环。只有真实场景需要持续工作区、暂停恢复或高密度预热时，才启用 Agent Sandbox，避免首版被实验性基础设施绑死。

### 10.6 不采用强制工作流引擎

Argo、Tekton、Temporal、Dapr 等能够执行步骤或持久工作流，但不能直接提供 OMAC 的软件交付语义。

OMAC 首版不将其设为强制内核，原因是：

- Task 图会在评审、返工、需求变化和风险决策中动态演化。
- 软件返工不是简单的 Activity 重试。
- Delivery 状态必须理解需求、证据、质量门、预算和生产验收。
- 引入第二个工作流事实源会增加一致性复杂度。

后续如果控制器持久执行复杂度超过自有状态机可控范围，可以在不改变领域模型的前提下评估 durable execution 引擎。

---

## 11. 业务流程

### 11.1 从需求到生产的主流程

~~~mermaid
flowchart LR
    U[Delivery 发起人] --> A[提交初始需求]
    A --> B[需求澄清与决策]
    B --> C[架构与验收基线]
    C --> D[生成 Delivery 任务图]
    D --> E[组织 Agent Team 并执行]
    E --> F[独立评审、测试与安全验证]
    F -->|不通过| G[生成有界修复任务]
    G --> E
    F -->|通过| H[形成发布候选]
    H --> I[部署目标环境]
    I --> J[部署后验证]
    J -->|失败| K[回滚或修复决策]
    K --> E
    J -->|通过| L[独立生产验收]
    L -->|不通过| G
    L -->|通过| M[Delivery 完成]
    B -.高风险或信息不足.-> N[决策处理人]
    E -.预算或权限阻塞.-> N
    K -.生产风险.-> N
    N -->|批准、调整或终止| B
~~~

### 11.2 业务交接规则

业务交接不再表现为“把同一条任务转派给另一个成员”，而是：

1. 上一阶段提交结构化结果和证据。
2. GateResult 判断是否满足进入下一阶段的条件。
3. 控制平面根据任务合同创建下一次 AgentRun。
4. 下一执行者只获得完成当前职责所需的最小上下文和权限。
5. 每次开发、评审、返工、部署和验收都拥有独立运行历史。

### 11.3 需求澄清产物

RequirementRevision 至少包含：

- 目标用户。
- 核心用户旅程。
- 业务目标与成功指标。
- 范围与非目标。
- 功能要求。
- 非功能要求。
- 安全、数据和部署约束。
- 已确认决策。
- 未决问题。
- 验收映射。

未决问题不能被 Planner 静默猜测。它们必须形成 Decision，或被明确标记为已接受假设。

---

## 12. 业务解决方案架构

~~~mermaid
flowchart LR
    subgraph ENTRY[用户操作与治理入口]
        WEB[OMAC Web]
        API[OMAC API / CLI]
        HUMAN[需求确认、风险决策、生产授权]
    end

    subgraph CORE[自主软件交付控制面]
        DELIVERY[Delivery 管理]
        TEAM[Agent Team 组织]
        QUALITY[证据与质量门]
        RELEASE[发布、部署与生产验收]
        BUDGET[预算、权限与审计]
    end

    subgraph EXEC[云原生执行层]
        RUNTIME[可插拔 Agent Runtime]
        CLUSTER[K3s / Kubernetes 资源调度]
    end

    subgraph FACTS[事实与外部能力]
        STATE[运行状态与业务历史]
        GIT[Git / CI/CD / 部署系统]
        MODEL[模型供应商与工具]
    end

    WEB --> DELIVERY
    API --> DELIVERY
    HUMAN --> DELIVERY
    DELIVERY --> TEAM
    TEAM --> QUALITY
    QUALITY --> RELEASE
    RELEASE --> DELIVERY
    BUDGET -.约束.-> DELIVERY
    BUDGET -.约束.-> TEAM
    TEAM --> RUNTIME
    RUNTIME --> CLUSTER
    RUNTIME --> GIT
    RUNTIME --> MODEL
    DELIVERY <--> STATE
    QUALITY <--> STATE
    RELEASE <--> GIT
~~~

### 12.1 架构叙事

1. 用户通过 Web、API 或 CLI 提交软件目标。
2. Delivery 管理模块形成需求、设计、任务图和交付策略。
3. Agent Team 模块根据能力、资源、成本和职责隔离选择执行者。
4. 云原生执行层将每次 AgentRun 调度到集群。
5. Agent 通过外部 Git、CI/CD、部署系统、模型和工具完成工作。
6. 证据与质量门决定任务能否推进。
7. 发布与生产验收模块将代码结果推进到真实运行环境。
8. 预算、权限和审计贯穿全过程。
9. 用户始终在 OMAC 中看到一个统一的 Delivery 状态。

### 12.2 单一产品入口

- 用户日常操作不需要 Kubernetes 命令。
- 用户不需要进入外部 Issue 系统。
- Git、CI/CD 和部署系统仍然保留原始事实，但 OMAC Web 提供统一链接、状态和交付视图。
- Kubernetes API 是内部控制接口，不是普通用户的产品入口。

---

## 13. 领域模型

### 13.1 子域划分

| 子域 | 类型 | 核心职责 |
|---|---|---|
| Delivery 管理域 | 核心域 | 需求、设计、任务图、交付阶段和最终状态 |
| 质量与验收域 | 核心域 | 合同、证据、质量门、返工和生产验收 |
| Agent Team 域 | 核心域 | Agent 能力、职责隔离、执行选择和团队协作 |
| 运行执行域 | 支撑域 | AgentRun 生命周期、工作区和 Runtime Adapter |
| 资源与预算域 | 支撑域 | 并发、资源、Token、金额、限额和停止决策 |
| 平台治理域 | 支撑域 | 用户、权限、审计、通知和人工决策 |
| 外部集成域 | 通用域 | Git、CI/CD、部署、模型、工具和身份适配 |

### 13.2 上下文关系

~~~mermaid
flowchart LR
    D[Delivery 管理域] -->|任务合同与阶段要求| T[Agent Team 域]
    T -->|创建执行请求| R[运行执行域]
    R -->|结果与使用记录| Q[质量与验收域]
    Q -->|通过、返工或阻塞| D
    B[资源与预算域] -.准入和停止策略.-> D
    B -.运行预算.-> R
    G[平台治理域] -.权限、决策和审计.-> D
    I[外部集成域] -->|标准化外部事实| R
    I -->|构建、部署和运行证据| Q
~~~

外部 Git、CI/CD、部署系统和模型供应商使用防腐层适配，不允许其原生状态直接侵入 Delivery 领域状态机。

### 13.3 核心领域对象

~~~mermaid
classDiagram
    class Project {
      项目边界
      仓库与环境引用
      默认交付策略
    }

    class Delivery {
      初始目标
      当前阶段
      目标代码版本
      目标环境
      总体预算
      最终状态
    }

    class RequirementRevision {
      用户与场景
      范围与非目标
      成功指标
      约束与未决问题
      验收映射
    }

    class Task {
      kind
      objective
      dependencies
      contract
      requiredCapabilities
      currentState
    }

    class AgentRun {
      immutableRunId
      attempt
      runtime
      workspaceRef
      startAndFinish
      result
    }

    class Evidence {
      type
      source
      subjectRevision
      result
      integrity
    }

    class GateResult {
      gate
      decision
      reasons
      evidenceRefs
    }

    class Decision {
      subject
      options
      selectedAction
      operator
      reason
    }

    class AgentProfile {
      capabilities
      runtime
      modelPolicy
      permissionPolicy
      costPolicy
    }

    class UsageRecord {
      providerRequestId
      model
      tokens
      amount
      duration
    }

    Project "1" --> "*" Delivery
    Delivery "1" *-- "*" RequirementRevision
    Delivery "1" *-- "*" Task
    Task "1" *-- "*" AgentRun
    Task "1" *-- "*" Evidence
    Task "1" *-- "*" GateResult
    Delivery "1" *-- "*" Decision
    Delivery "1" *-- "*" UsageRecord
    AgentRun "*" --> "1" AgentProfile
    AgentRun "1" --> "*" Evidence
    AgentRun "1" --> "*" UsageRecord
~~~

### 13.4 最小必要模型

正式业务内核只保留五类关键概念：

1. Delivery：一次完整交付。
2. Task：可依赖、可执行、可验证的职责节点。
3. AgentRun：Task 的一次不可变执行。
4. Evidence / GateResult：客观结果及其推进结论。
5. Decision：系统不能自主决定的授权记录。

RequirementRevision、AgentProfile 和 UsageRecord 是必要支撑对象。

Review、Verification、SecurityScan、Deployment 和 Acceptance 不再分别建设独立执行引擎，它们通过 Task.kind、合同和 Evidence 类型表达。

### 13.5 聚合与一致性边界

- Delivery 聚合保护当前阶段、总体终态、目标版本、生产环境和最终验收结果。
- Task 聚合保护依赖、合同、当前状态、运行序号和终态唯一性。
- AgentRun 是不可变执行记录，完成后不被重置或覆盖。
- GateResult 是不可变判断记录，新证据只能产生新的判断。
- Decision 是不可变授权记录，必须包含操作者、理由和影响范围。
- UsageRecord 使用 providerRequestId 或等价幂等键，禁止重复结算。

---

## 14. 核心状态闭环

### 14.1 Delivery 状态

~~~mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Clarifying: 开始需求澄清
    Clarifying --> Designing: 需求基线满足
    Designing --> Planning: 架构与验收基线满足
    Planning --> Executing: 任务图通过校验
    Executing --> Validating: 实现与集成完成
    Validating --> Executing: 质量门失败并生成修复任务
    Validating --> Releasing: 必需质量门通过
    Releasing --> Deploying: 发布候选可部署
    Deploying --> Accepting: 部署后验证通过
    Deploying --> RollingBack: 部署或运行验证失败
    RollingBack --> Executing: 回滚成功并生成修复任务
    RollingBack --> NeedsDecision: 回滚失败或风险过高
    Accepting --> Executing: 生产验收失败并生成修复任务
    Accepting --> Succeeded: 生产验收通过

    Draft --> NeedsDecision: 输入冲突
    Clarifying --> NeedsDecision: 关键问题无法自主决定
    Executing --> NeedsDecision: 预算、权限或返工超限
    Validating --> NeedsDecision: 风险接受或质量门冲突
    Releasing --> NeedsDecision: 发布授权
    Deploying --> NeedsDecision: 生产风险

    NeedsDecision --> Clarifying: 补充需求
    NeedsDecision --> Executing: 批准修复或增加预算
    NeedsDecision --> Releasing: 批准发布
    NeedsDecision --> Deploying: 批准继续部署
    NeedsDecision --> Accepting: 处理生产验收决策
    NeedsDecision --> Abandoned: 终止交付
    NeedsDecision --> Failed: 确认无法恢复
    Accepting --> SucceededWithRisk: 验收完成并接受非阻断剩余风险

    Succeeded --> [*]
    SucceededWithRisk --> [*]
    Abandoned --> [*]
    Failed --> [*]
~~~

### 14.2 Task 状态

~~~mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Ready: 依赖和准入满足
    Ready --> Running: 创建 AgentRun
    Running --> Checking: AgentRun 提交结果
    Checking --> Succeeded: 合同和质量门通过
    Checking --> Ready: 生成新一轮 AgentRun
    Checking --> NeedsDecision: 返工、预算或风险超限
    Running --> Ready: 基础设施失败且未超过重试上限
    Running --> NeedsDecision: 重试超限、权限、安全或外部依赖阻塞
    NeedsDecision --> Ready: 人工批准继续
    NeedsDecision --> SucceededWithRisk: 授权豁免可豁免任务门
    NeedsDecision --> Abandoned: 放弃任务
    Succeeded --> [*]
    SucceededWithRisk --> [*]
    Abandoned --> [*]
~~~

### 14.3 有界自主返工

质量门失败后可以自动创建修复 Task 或新的 AgentRun，但必须满足：

- 最大返工轮次。
- 每轮 Token 和金额上限。
- 不能自动放宽原验收条件。
- 不能静默扩大或缩小产品范围。
- 涉及兼容性、安全、数据迁移或生产风险时进入 Decision。
- 每次返工保留原实现、失败证据和修复原因。
- 风险接受只能豁免项目策略明确标记为可豁免的质量门，不能绕过生产部署、数据安全和最终验收等硬门。

### 14.4 基础设施失败与业务返工分离

- Pod 启动失败、节点中断、Runtime 崩溃属于基础设施失败。
- 代码不满足合同、评审不通过、测试失败属于业务返工。
- 两类失败都创建新的 AgentRun，但使用不同失败原因、预算策略和告警等级。
- Kubernetes 自动重建 Pod 不得让同一 AgentRun 被重复结算或产生两个终态。

### 14.5 Delivery 完成定义

Delivery 只有同时满足以下条件才能进入 Succeeded 或 SucceededWithRisk：

- 需求与架构基线已确认。
- 目标代码版本明确。
- 必需单元、集成和端到端测试通过。
- 必需安全门通过。
- 发布候选与部署记录存在。
- 目标环境部署成功。
- 部署后冒烟和关键链路验证通过。
- 基础健康与可观测性检查通过。
- 回滚路径满足项目策略。
- 独立 Acceptor 根据原始需求完成生产验收。
- 证据链完整且可追溯。

SucceededWithRisk 只表示上述基础条件已经满足，同时存在经过授权接受的非阻断剩余风险；它不能用于从需求、开发或部署阶段提前结束 Delivery。

---

## 15. 控制平面与执行平面边界

### 15.1 OMAC 控制平面负责

- Delivery 和 Task 状态机。
- 任务依赖与关键路径。
- Agent 能力匹配。
- 运行准入和业务优先级。
- Token、金额和返工预算。
- 证据合同和质量门。
- 人工决策与生产授权。
- 结果回收、故障恢复和审计。

### 15.2 Kubernetes 负责

- 节点选择。
- Pod/Job 调度。
- CPU、内存和 GPU 资源。
- requests/limits、亲和性、污点容忍和优先级。
- 容器生命周期。
- 节点故障后的基础设施重建。

### 15.3 Runtime Adapter 负责

- 启动具体 Agent Runtime。
- 创建或关联隔离工作区。
- 注入最小必要上下文、工具和凭证。
- 采集心跳、日志、结果和用量。
- 将 Runtime 原生事件转换为 OMAC AgentRun 事件。
- 停止、超时和清理执行环境。

### 15.4 调度原则

OMAC 不是节点调度器。

OMAC 只决定：

- 哪个 Task 可以开始。
- 需要什么能力和权限。
- 可以消耗多少预算。
- 选择哪一种 Runtime/Profile。
- 使用什么资源请求和优先级。

Kubernetes 决定该 AgentRun 运行在哪台机器。

当真实生产场景出现大量排队、GPU 稀缺或公平共享需求时，再引入 Kueue 等资源准入组件。首版不预设复杂批调度。

---

## 16. 数据与资源所有权

### 16.1 数据所有权

| 数据 | 权威来源 | 说明 |
|---|---|---|
| Delivery/Task 活跃期望和控制状态 | OMAC 集群资源 | 支撑 Controller 收敛和实时观察 |
| AgentRun 活跃执行状态 | OMAC 集群资源 | 与实际运行工作负载关联 |
| 需求、合同、证据、质量门、决策和历史事件 | OMAC 业务数据库 | 支撑查询、审计、报表和长期历史 |
| Token、金额、时长和预算记录 | OMAC 业务数据库 | 独立幂等账本 |
| 源代码、提交、分支和 PR | Git 平台 | OMAC 保存引用和校验信息 |
| 构建、测试和部署原始结果 | 外部 CI/CD 或部署系统 | OMAC 保存结构化结果与原始链接 |
| 实时运行日志 | 标准日志输出 | 可由用户接入长期日志平台 |
| 大型截图、视频和二进制结果 | 可选外部存储 | 非 Compact 模式强制依赖 |

### 16.2 单一控制事实

Kubernetes 活跃资源和 PostgreSQL 历史数据不能分别独立决定下一状态。

设计原则：

- Controller 只根据受控状态和已确认事件推进 Delivery。
- PostgreSQL 保存大型内容、不可变事件、账本、审计和查询投影。
- 所有跨存储写入使用幂等键和可恢复事件。
- 查询投影延迟不能阻塞 Controller 的正确性。
- 不允许 Web、Agent 或后台任务绕过命令入口直接修改控制状态。

具体事务、Outbox 和重放协议在技术概要设计中定义。

### 16.3 并发与写入所有权

| 写入内容 | 唯一写入者 | 规则 |
|---|---|---|
| Delivery/Task 目标、暂停、取消和人工决策 | OMAC API | Web 与 CLI 只能提交命令 |
| Delivery/Task 当前状态和 Conditions | OMAC Controller | 用户和 Agent 只读 |
| AgentRun 创建、停止和调度关联 | OMAC Controller | Agent 不得创建其他运行 |
| AgentRun 心跳、结果和证据 | 对应 Agent 身份经 OMAC API | 校验 run_id、版本和幂等键 |
| GateResult | 质量门服务或受控 Controller | 必须引用证据和规则版本 |
| 用量账本 | 用量接收服务 | 使用供应商请求 ID 幂等写入 |

---

## 17. Storage 策略

### 17.1 不设置强制 ArtifactStore

OMAC 的核心业务是管理交付状态、责任和质量，不是保存所有工作文件。

默认策略：

- 代码和 Diff 使用 Git。
- 需求、合同、决策和结构化证据使用 PostgreSQL。
- Agent 工作目录使用临时卷或 Sandbox 工作区。
- 构建制品使用用户现有制品仓库。
- 实时日志使用标准日志。
- 大型截图、视频和文件只保存外部引用。

### 17.2 可选对象存储

只有以下场景需要对象存储 Adapter：

- 用户要求保存截图或测试视频。
- 需要保留大型模型输出或数据文件。
- 需要跨 AgentRun 共享非 Git 文件。
- 企业要求长期归档原始证据。

Compact 模式默认不安装对象存储。External Cluster 可以接入企业现有对象存储。

### 17.3 避免旧平台适配遗留

Multica 时代的附件、评审文件和日志产物不再自动成为 V2 领域对象。

每类信息必须先回答：

- 它是否参与状态判断？
- 是否需要长期审计？
- 是否已经有外部权威来源？
- 是否只需要引用而不需要复制？

不能回答上述问题的信息不进入核心存储设计。

---

## 18. 产品部署模式

### 18.1 Compact 模式

适用：

- 单台 Linux 主机。
- 本地工作站或实验服务器。
- 两到数台机器组成的小集群。
- 个人、开源团队和小型研发团队。

交付内容：

- 在线安装器下载并校验固定版本的 K3s 和 OMAC 安装内容。
- 离线安装包包含 K3s 二进制、OMAC 镜像和安装清单。
- 默认部署 OMAC Web、API、Controller 和内置数据库。
- 默认使用 Kubernetes Job 和临时工作区运行 Agent。
- 使用本地持久卷保存数据库和必要配置。
- 提供备份、恢复、卸载、诊断和升级命令。

Compact 优先降低安装和运维成本，不承诺单节点高可用。需要高可用时，用户可以扩展为多节点 K3s，或迁移到 External Cluster。

### 18.2 External Cluster 模式

适用：

- 企业已有 Kubernetes。
- 需要高可用、统一监控、集中身份和网络策略。
- 需要 GPU、弹性节点池或更高并发。
- 需要接入企业数据库、Secret、Ingress、证书和对象存储。

交付行为：

- 不安装 Kubernetes。
- 通过 Helm 或等价声明式安装方式部署。
- 支持企业现有 StorageClass、Ingress、Secret、数据库和可观测系统。
- 默认受 Namespace、ResourceQuota、NetworkPolicy 和 RBAC 约束。
- 支持将 AgentRun 调度到指定节点池或隔离域。

### 18.3 一套产品原则

Compact 和 External Cluster 必须保持：

- 相同 Delivery 领域模型。
- 相同 API 和 Web 行为。
- 相同 CRD 或等价集群资源。
- 相同 Runtime Adapter 接口。
- 相同业务验收用例。

部署差异只允许出现在基础设施配置，不允许渗透为业务逻辑分支。

---

## 19. Web 控制系统

Web 是 OMAC 的主要产品面，必须以 Delivery 为中心，而不是以 Pod 或 Session 为中心。

### 19.1 Delivery 首页

- 当前阶段和目标环境。
- 需求摘要、范围和成功指标。
- 关键路径与总体进度。
- 当前运行中的 AgentRun。
- 质量门和生产验收状态。
- Token、金额和预算余量。
- 阻塞、风险和待处理 Decision。

### 19.2 需求与设计中心

- RequirementRevision 历史。
- 未决问题和已接受假设。
- 架构与验收基线。
- 用户确认和变更影响。

### 19.3 任务图与 Agent Team

- Task 依赖图、就绪节点和关键路径。
- 每个 Task 的合同、能力要求和状态。
- AgentProfile、Runtime 和当前负载。
- AgentRun 时间线、工作区和结果。
- 返工链路和失败原因。

### 19.4 质量与证据中心

- 代码评审。
- 测试结果。
- 安全扫描。
- 构建和制品。
- 部署与运行验证。
- GateResult 及引用证据。

### 19.5 发布与生产验收

- 发布候选版本。
- 目标环境和授权状态。
- 部署进度。
- 冒烟、健康和关键链路验证。
- 回滚状态。
- 独立 Acceptor 结论。

### 19.6 决策中心

- 需求冲突。
- 预算超限。
- 返工超过上限。
- 权限或外部依赖阻塞。
- 安全与数据风险。
- 生产发布授权。
- 接受风险、调整范围或终止交付。

### 19.7 Operator 视图

节点、Pod、CPU、内存、GPU、队列和基础设施事件属于 Operator 视图，不作为普通用户首页主信息。

---

## 20. 自主等级

OMAC 通过项目策略控制自主边界：

| 等级 | 能力 |
|---|---|
| L0 计划 | 只生成需求、设计、验收和任务图，不执行代码变更 |
| L1 开发 | 可修改代码、运行测试和创建 PR，合并由人工批准 |
| L2 预发布 | 可完成集成并部署到预发布环境，生产发布由人工批准 |
| L3 受控生产 | 通过质量门后请求一次生产授权，获批后自动部署和验收 |
| L4 策略生产 | 在预设风险策略和预算内自动部署生产，异常自动回滚或请求决策 |

默认建议：

- Compact 示例使用 L1 或 L2。
- 企业首次接入使用 L2。
- 稳定项目逐步提升到 L3。
- L4 必须由项目负责人显式启用，并设置严格的风险、预算和回滚策略。

---

## 21. 外部依赖与降级策略

| 外部依赖 | 定位 | 异常处理 |
|---|---|---|
| 模型供应商 | 核心但不可靠 | 有界重试、限流感知、模型降级、预算保护、任务隔离 |
| Git 平台 | 代码事实源 | 幂等操作、状态重新读取、延迟重试、人工接管 |
| CI/CD | 构建和发布能力 | 保存外部运行引用、超时进入阻塞、允许替换适配器 |
| 部署目标 | 生产结果载体 | 发布授权、健康检查、自动回滚、失败进入 Decision |
| Agent Runtime | 可替换执行者 | 心跳、超时、强制终止、切换 Profile、创建新 AgentRun |
| 通知系统 | 非核心 | 失败不阻塞 Delivery，保留 Web 待办 |
| Kubernetes/K3s | 产品运行底座 | Compact 提供备份恢复，External 依赖企业集群 SLA |

外部系统只能提供能力和原始事实，不能成为 OMAC Delivery 或 Task 状态的唯一所有者。

---

## 22. 安全与治理

### 22.1 Agent 最小权限

- 每个 AgentRun 使用短期、单用途运行身份。
- 权限由 Task 合同和 AgentProfile 共同决定。
- Secret 通过运行时注入，不写入任务正文、日志或数据库明文。
- Agent 不能直接修改 Delivery、Task 和其他 AgentRun。
- 不同 AgentRun 默认不共享可写工作空间。
- 高风险工具和生产操作需要显式策略或人工授权。

### 22.2 安全质量门

根据项目策略，至少支持以下 Evidence：

- Secret 检测。
- 依赖漏洞。
- 静态代码安全分析。
- 许可证检查。
- 基础设施配置检查。
- 容器镜像扫描。
- 高风险工具和权限审计。

高风险发现不能由执行 Agent 自行忽略，必须形成失败 GateResult 或 Decision。

### 22.3 资源和成本治理

- Delivery、Task 和 AgentRun 可以配置 Token 和金额预算。
- AgentRun 必须设置 CPU、内存和可选 GPU 请求与上限。
- 超预算后停止创建新运行并进入 NeedsDecision。
- 失败重试和返工必须计入预算。
- 不允许通过无限增加副本掩盖模型限流或任务结构问题。

### 22.4 审计

以下操作必须形成审计事件：

- 需求和验收基线确认。
- Delivery 启动、暂停、恢复、取消和终止。
- AgentProfile、权限和 Runtime 策略变化。
- Task 图变化和合同变化。
- 每次 AgentRun 创建、停止和结果提交。
- GateResult 和 Evidence 引用。
- Token 或预算调整。
- 风险接受。
- 生产授权、部署和回滚。
- 最终验收结论。

---

## 23. 主要风险与治理策略

| 风险 | 影响 | 治理策略 |
|---|---|---|
| 只完成代码、不完成生产交付 | 产品退化为并行 Coding Agent | Delivery 完成定义绑定部署与生产验收 |
| 通用 Agent 平台范围膨胀 | 无法形成差异，交付延期 | 首版只建设软件交付控制面 |
| CRD 与数据库成为双事实源 | 状态冲突、恢复错误 | 单一控制写入者、幂等事件、查询投影与控制状态分离 |
| Agent 自报完成 | 质量不可相信 | Evidence + GateResult + 独立评审与验收 |
| Agent 无限返工 | 成本失控 | 最大轮次、硬预算、范围和风险决策 |
| 同一 Agent 自产自验 | 结论偏置 | 能力和职责隔离策略 |
| Kubernetes 重建导致重复执行 | 重复提交和重复结算 | AgentRun 幂等、唯一终态、外部请求幂等键 |
| Runtime 或模型供应商锁定 | 成本和能力受制于单一厂商 | Runtime Adapter、模型策略和开放协议 |
| Compact 安装过重 | 开箱即用失败 | 默认最小组件、Job 执行、可选 Sandbox 和外部存储 |
| Agent 执行不受信任代码 | 主机和凭证风险 | 隔离工作区、最小权限、NetworkPolicy 和高风险授权 |
| 自动生产发布风险 | 数据或服务事故 | 自主等级、发布授权、健康门和回滚 |
| 仓库不适合 Agent 执行 | 成功率低、探索成本高 | Agent-ready 项目检查、环境引导和可复制验证命令 |
| Clean Slate 降低升级意愿 | V1 用户需要重新部署 | 明确产品分界，提供示例和迁移指南，不建设兼容代码 |

---

## 24. 建议实施里程碑

~~~mermaid
gantt
    title OMAC V2 业务方案实施里程碑
    dateFormat  YYYY-MM-DD
    axisFormat  %m-%d

    section 领域内核
    Delivery领域模型与状态机       :m1, 2026-07-20, 21d
    Evidence、Gate与Decision闭环   :m2, after m1, 21d

    section 云原生运行
    Controller与Job Runtime        :m3, 2026-08-03, 35d
    AgentProfile与Runtime Adapter  :m4, after m3, 28d

    section 产品闭环
    Web需求、任务与决策中心        :m5, 2026-08-17, 42d
    发布、部署与生产验收           :m6, after m2, 35d

    section 产品交付
    Compact K3s安装                :m7, after m3, 28d
    External Kubernetes安装        :m8, after m4, 28d
    安全、预算与故障演练           :m9, after m6, 28d
    端到端基准与公开案例           :m10, after m9, 21d
~~~

| 里程碑 | 交付内容 | 完成判据 |
|---|---|---|
| M1 | Delivery、Task、AgentRun、Evidence、GateResult、Decision 模型 | 领域测试证明主状态闭环 |
| M2 | 需求澄清、设计、开发、评审、测试和返工 | 一个仓库任务可独立收敛 |
| M3 | Kubernetes Controller 和基础 Job Runtime | 控制器重启后任务继续推进 |
| M4 | 至少两种 Agent Runtime Adapter | 同一 Delivery 可更换 Runtime 执行 |
| M5 | Delivery 中心 Web、任务图、运行、证据、预算和决策 | 用户无需外部 Issue 完成全流程 |
| M6 | 发布候选、部署、部署后验证、回滚和生产验收 | done 不再等于 merge |
| M7 | Compact K3s 安装、备份、恢复和诊断 | 支持环境 15 分钟内 Web 可用 |
| M8 | External Cluster 安装和企业依赖接入 | 与 Compact 使用同一业务验收 |
| M9 | 安全、预算、故障恢复和容量演练 | 核心风险场景可复现并恢复 |
| M10 | 公开端到端案例和竞争基准 | 可量化证明 Agent Team 的交付价值 |

---

## 25. 最小可交付闭环

首个可用版本必须完成以下闭环：

1. 用户在单台 Linux 主机安装 Compact 模式。
2. 用户在 Web 配置模型、Git 仓库、AgentProfile 和目标环境。
3. 用户提交一个初始软件需求。
4. 系统生成 RequirementRevision、验收基线和 Delivery。
5. 系统生成并校验 Task 依赖图。
6. 控制平面识别就绪 Task 并创建 AgentRun。
7. Kubernetes 将 AgentRun 调度到可用节点。
8. Agent 执行代码变更并提交结构化 Evidence。
9. 独立评审和测试 Task 运行；失败时创建新的修复 AgentRun。
10. 通过质量门后形成发布候选并部署到用户配置的目标环境。
11. 系统执行部署后冒烟和环境级最终验收。
12. 用户在 Web 查看 Delivery、Task、AgentRun、Evidence、预算、部署和验收结果。
13. 控制组件重启后，未完成 Delivery 可以继续推进。

首个工程里程碑可以先在受控环境验证部署闭环，但对外宣称具备生产交付能力前，必须完成至少一个真实生产环境的端到端案例。不得长期将“测试环境部署成功”等同于生产交付。

---

## 26. 业务验收标准

### 26.1 独立性

- 关闭 Multica 后，OMAC 仍能创建、调度、执行、评审和完成 Delivery。
- 外部 Issue 系统不可用不影响 OMAC 核心闭环。
- Runtime 替换不改变 Delivery 和 Task 数据模型。

### 26.2 用户闭环

- 用户无需进入 Kubernetes 管理界面完成日常业务操作。
- 用户无需进入外部 Issue 系统查看任务状态。
- 用户能在同一 Web 中完成需求确认、决策、预算调整、生产授权和最终验收。

### 26.3 执行闭环

- 任务依据依赖、资源、能力和预算自动推进。
- 节点或 Runtime 失败后能够创建新的 AgentRun 并恢复。
- 重复事件不会产生重复终态、重复账本、重复 PR 或重复部署。

### 26.4 质量闭环

- 每个需要评审的 Task 都有独立 GateResult。
- 每次返工产生新的 AgentRun，不覆盖历史。
- Agent 的文本结论不能绕过 Evidence 和 GateResult。
- Delivery 只有在合同、测试、安全、部署和生产验收条件满足后才能成功。

### 26.5 云原生一致性

- Compact 和 External Cluster 使用相同业务验收用例。
- 两种模式对 Web、API、Agent 和 Delivery 行为无差异。
- 增加节点后，可调度 AgentRun 并发能够提升，不依赖固定单机。

### 26.6 开源与可拥有性

- 用户可以在不依赖 OMAC 托管服务的情况下运行核心控制平面。
- 用户可以审查 Delivery 状态转换、质量门和预算控制逻辑。
- 用户可以实现自己的 Runtime Adapter。
- 用户数据、代码和运行事实不被强制写入厂商托管平台。

---

## 27. 竞争力验证方案

OMAC 不能只通过功能列表证明竞争力，必须建立公开端到端基准。

### 27.1 基准场景

1. 从零交付一个小型生产 Web 产品。
2. 在现有系统中实现跨前端和后端的业务功能。
3. 修复生产缺陷并完成回归、部署和验收。
4. 对多个模块或仓库进行兼容性迁移。

### 27.2 对照组

在相同需求、环境和 Token 预算下比较：

- 单一最佳 Coding Agent。
- 简单并行多个 Agent。
- OMAC Agent Team。

### 27.3 公开证据

- 原始需求和需求澄清记录。
- 架构与验收基线。
- Task 图和 AgentRun 历史。
- 代码、提交和 PR。
- 测试、安全和评审证据。
- 发布、部署和回滚记录。
- Token、金额和耗时。
- 返工和人工介入次数。
- 最终生产验收报告。

### 27.4 核心比较指标

- 生产验收通过率。
- 人类介入次数。
- Time to Production。
- 单次成功交付成本。
- 返工轮次。
- 集成冲突率。
- 逃逸缺陷率。
- 发布和回滚成功率。

只有当 OMAC 在这些指标上稳定优于单一 Agent 或无控制并行方式，才能证明 Agent Cluster 产生了真实产品价值。

---

## 28. 评审重点

本轮评审建议重点确认：

1. 是否确认使命是“从初始需求到生产验收”，而不是“运行多个 Agent”。
2. 是否确认 Delivery 是根对象，Workflow 下沉为内部任务组织方式。
3. 是否确认评审、测试、安全、部署和验收统一使用 Task、AgentRun、Evidence 和 GateResult。
4. 是否确认 OMAC 的市场差异是开源、自托管、异构 Runtime 和确定性交付控制的组合。
5. 是否确认 Kubernetes 调度机器，OMAC 调度软件交付责任。
6. 是否确认 kagent、AgentTeams、Agent Sandbox 和 OpenHands 是借鉴或集成对象，而不是强制产品内核。
7. 是否确认 Compact 首版优先使用 Job 形成闭环，Sandbox 和 Kueue 按真实需求引入。
8. 是否确认不强制建设 ArtifactStore。
9. 是否确认 done 必须包含部署后验证和最终生产验收。
10. 是否确认 V2 不兼容 V1，不建设任何过渡模型和兼容代码。
11. 是否确认首版只支持单组织。
12. 是否确认公开端到端基准是产品完成的一部分。

---

## 附录 A：行业参考资料

### A.1 软件交付 Agent 与 Agent Team

- [Factory Software Factory](https://factory.ai/product/software-factory)
- [Factory Missions](https://docs.factory.ai/features/missions/overview)
- [GitLab Duo Agent Platform](https://docs.gitlab.com/user/duo_agent_platform/)
- [GitLab Planner Agent](https://docs.gitlab.com/user/duo_agent_platform/agents/foundational_agents/planner/)
- [GitLab Software Development Flow](https://docs.gitlab.com/user/duo_agent_platform/flows/foundational_flows/software_development/)
- [Devin Advanced Capabilities / Managed Devins](https://docs.devin.ai/work-with-devin/advanced-capabilities)
- [OpenAI Symphony](https://github.com/openai/symphony)
- [OpenAI Symphony Specification](https://github.com/openai/symphony/blob/main/SPEC.md)
- [MetaGPT](https://github.com/FoundationAgents/MetaGPT)
- [OpenHands](https://www.openhands.dev/)
- [OpenHands Remote Agent Server](https://docs.openhands.dev/sdk/guides/agent-server/overview)
- [OpenHands Kubernetes Installation](https://docs.openhands.dev/enterprise/k8s-install)

### A.2 云原生 Agent 平台

- [AgentTeams](https://github.com/agentscope-ai/AgentTeams)
- [AgentTeams Kubernetes 原生协作设计](https://github.com/agentscope-ai/AgentTeams/blob/main/docs/zh-cn/k8s-native-agent-orch.md)
- [kagent](https://kagent.dev/)
- [kagent AgentHarness](https://kagent.dev/docs/kagent/concepts/agent-harness)
- [Kubernetes Agent Sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
- [Running Agents on Kubernetes with Agent Sandbox](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/)

### A.3 Kubernetes 与工作流基础设施

- [Kubernetes Custom Resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
- [Kubernetes Controllers](https://kubernetes.io/docs/concepts/architecture/controller/)
- [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)
- [Kubernetes Resource Management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
- [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)
- [Kubernetes Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [Kueue](https://kueue.sigs.k8s.io/)
- [K3s Architecture](https://docs.k3s.io/architecture)
- [K3s High Availability Embedded etcd](https://docs.k3s.io/datastore/ha-embedded)
- [Argo Workflows](https://argo-workflows.readthedocs.io/)
- [Tekton Pipelines](https://tekton.dev/docs/pipelines/)
- [Temporal](https://docs.temporal.io/)

### A.4 现有 OMAC 设计依据

- [OMAC README](../../../README.md)
- [OMAC Planner Agent 执行协议](../../../src/omac/guide/roles/planner.md)
- [OMAC Orchestrator Agent 执行协议](../../../src/omac/guide/roles/orchestrator.md)
- [OMAC Reviewer Agent 执行协议](../../../src/omac/guide/roles/reviewer.md)
- [OMAC Acceptor Agent 执行协议](../../../src/omac/guide/roles/acceptor.md)
- [OMAC 恢复协议](../../../src/omac/guide/recovery.md)

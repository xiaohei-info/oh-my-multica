---
name: software-design-lifecycle-tech-overview-functional-arch-diagramming
description: "Use when an architect in overview technical design must produce or review the 系统功能架构图 and needs the fully merged functional-architecture specialist method directly inside the new lifecycle family."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, technical-design, overview-design, functional-architecture]
    related_skills: [arch-lifecycle-tech-overview-methodology, arch-lifecycle-tech-overview-system-arch-diagramming]
---

# Technical Overview Functional Architecture Diagramming

## Overview

This skill now directly contains the merged specialist doctrine that previously lived in the legacy system-functional-architecture skill.

## Integrated Legacy Specialist Doctrine (Preserved and Merged)

# System Functional Architecture Diagramming

## Overview

This is the architect-private skill for drawing and reviewing **系统功能架构图** in the **概要设计** stage.

It strengthens a generic diagramming meta-spec with the architecture-lifecycle requirement that a functional architecture diagram must answer, at minimum:
- 系统建设目标是什么
- 要有哪些功能
- 这些功能如何分层
- 哪些功能是核心闭环，哪些是支撑能力
- 功能之间如何协作，但不过早坠入技术实现细节

Core principle:
**系统功能架构图 belongs to overview design, so it should explain capability structure and layering, not code structure, deployment topology, or runtime sequence detail.**

This skill exists because many so-called “功能架构图” fail in one of two ways:
- they are only a feature list with boxes, but no layering logic or system goal
- they collapse into technical architecture, process flow, or deployment views and lose their functional-design role

Use this skill to produce a diagram that is abstract enough for 概要设计, but concrete enough to guide later system architecture, technical architecture, and detailed design.

Canonical full-fidelity drawing spec:
- `references/universal-diagramming-spec-full.md`

Important preservation rule:
- the full drawing/detail content from the source publication is preserved there verbatim
- this umbrella skill is an architect usage layer on top of that source, not a replacement for it
- when any summary here conflicts in detail, completeness, wording, or emphasis with the full reference, treat the full reference as authoritative
- do **not** compress, rewrite away, or omit the drawing-specific details from the full reference when using this skill for real diagram production

## When to Use

Use when:
- the user asks for 系统功能架构图、功能分层图、概要设计中的功能架构图
- you are in the 概要设计 stage and must show what the system does before showing how it is implemented
- a solution/design doc needs one picture to explain major functional modules and their relationships
- the team is mixing business solution view, functional view, system view, and technical view and needs clean separation
- you need to review whether an existing architecture diagram is truly a functional-architecture artifact

Do not use when:
- the main question is end-to-end business processing steps → use business flow / process flow
- the main question is subsystem runtime collaboration, middleware, external dependencies, carrier placement → use system architecture / technical architecture
- the main question is deployment topology, machines, networks, zones → use physical/deployment architecture
- the main question is detailed state transitions, retries, branches, exception handling → use sequence/state/flow artifacts
- the main question is domain semantics and bounded contexts → pair with `ddd-domain-modeling-for-architecture`

## What A System Functional Architecture Diagram Must Answer

At 概要设计 stage, the functional architecture diagram is the direct answer to two design questions from the lifecycle document:
- **系统建设目标是什么样的、要有哪些功能？**
- **这些功能是如何分层的？**

Therefore, every qualified diagram must let a reviewer infer within ~30 seconds:
1. what system is being built for
2. what major function classes exist
3. how those functions are layered or grouped
4. what the central functional closed loop is
5. which functions are core vs governance/support/integration/data support

If the diagram cannot answer those five points quickly, it is not finished.

## Boundary With Neighboring Artifacts

Keep the functional architecture diagram separate from nearby artifacts.

### Functional architecture vs business solution architecture
- **Business solution architecture**: from user/business operation view, explaining how the business problem gets solved end-to-end
- **Functional architecture**: from system capability view, explaining what functional blocks exist and how they are layered

### Functional architecture vs system architecture
- **Functional architecture**: what functions/modules the system must have
- **System architecture**: how subsystems collaborate, what the boundaries are, how internal/external systems interact

### Functional architecture vs technical architecture
- **Functional architecture**: avoids framework, middleware, process, host, and code-package detail
- **Technical architecture**: names concrete stacks, components, middleware, runtime units, and implementation carriers

### Functional architecture vs process/flow/state diagrams
- **Functional architecture** is stable structure
- **Flow/state/sequence** artifacts are dynamic behavior

Important review rule:
**If the current “功能架构图” mainly shows time order, request sequence, or retry branches, it is actually a flow artifact, not a functional architecture artifact.**

## The Core Modeling Lens

Before drawing, classify all candidate content into four buckets:

1. **Core functional closed loop**
   - the minimum chain that directly realizes the system goal
   - usually the modules closest to user intent or core business outcome

2. **Domain/feature capabilities**
   - major functional blocks that express the main business or product capability set
   - these are the primary body of the diagram

3. **Support/governance capabilities**
   - configuration, rule management, audit, permissions, monitoring, operations support, reporting, governance controls
   - important, but should not visually overpower the core closed loop

4. **External or data-adjacent support**
   - upstream/downstream systems, shared data services, persistence-related capability placeholders
   - include only when they materially shape the functional boundary

This classification prevents two failure modes:
- support functions hijacking the whole picture
- every related thing being drawn as if it were a first-class core function

## Diagram Semantics: Containers, Nodes, Edges

Adopt the universal grammar, but interpret it specifically for overview-stage functional architecture.

### Containers
Containers express **functional layers** or **functional groups**, not random visual grouping.

Allowed first-level container examples:
- 接入与交互层
- 业务应用层
- 核心能力层
- 平台支撑层
- 治理与运营层
- 数据服务层
- 外部协同域

Allowed second-level container examples:
- 核心域能力
- 支撑域能力
- 风控治理组
- 配置策略组
- 运营分析组

Do not exceed 2 container levels.

### Nodes
Nodes represent one of:
- a major functional module
- a business capability block
- a cross-cutting functional service only if it matters at overview level

At this stage, nodes should usually be named as:
- **业务对象/领域 + 功能职责**
- or **技术类型 + 功能职责** when the audience is engineering-heavy

Good examples:
- 规则管理
- 策略编排
- 任务调度
- 风险校验
- 结果审计
- 用户接入
- 数据汇聚
- 指标分析

Bad examples:
- Kafka 3.7
- `module-a`
- `RuleEngineImpl`
- `doCheck()`
- MySQL连接池配置

### Edges
Edges show only relationships that matter to functional understanding:
- strong dependency
- core invoke / data handoff direction
- major control relation

Do not draw every conceivable relationship.

Default rule:
- keep only **main chain** and **meaningful cross-layer dependency** edges
- prefer omission over arrow spam

## Default Layout Choice

For overview-stage 系统功能架构图, the default layout is:
- **模式 A：分层拓扑（Layered）**

Because the dominant question is almost always:
- what functions exist
- how those functions are layered

### Layered layout default stack

A strong default stack is:
1. **接入/触发层**
2. **应用编排层**
3. **核心功能能力层**
4. **支撑与治理层**
5. **数据与外部协同层**

You do not always need all five. Collapse or rename layers to match the system, but preserve clear semantic separation.

### When to switch layouts

Use a different layout only when the dominant question changes:
- **Matrix**: capability-map style, many peer capability domains, classification matters more than call direction
- **Hub-and-Spoke**: one central platform/gateway/bus dominates all interactions
- **Pipeline**: the architecture is fundamentally a staged processing chain
- **Hybrid**: enterprise blueprint where separate regions answer different questions

Review warning:
If you chose non-layered layout for a normal overview functional diagram, be able to justify why layered view was insufficient.

## The Seven-Step Drawing Workflow

### Step 1: State the design target first

Before drawing, write one sentence:
- **本系统的建设目标是：____**

Then derive the diagram from that statement.

If no goal statement exists, the diagram will devolve into a feature list.

### Step 2: Enumerate candidate functions

List the major functions the system must own.

Then classify each into:
- core closed-loop capability
- domain capability
- support/governance capability
- external/data-adjacent support

Do not draw low-level technical pieces yet.

### Step 3: Decide the primary layering axis

Choose one dominant layering logic and keep it pure.

Preferred axes:
- user interaction → application orchestration → core capability → support/governance → data/external support
- channel/access → business operation → common services → governance/data support
- front-office → middle capability → back-office support

Do not mix different abstraction axes in the same sibling layer.

Bad layering example:
- 用户入口
- 订单服务
- Redis
- 审计规则
- Kubernetes

That mixes interaction, function, data tech, governance, and deployment.

### Step 4: Establish core closed loop visually

Place the central chain so the viewer can identify it immediately.

Typical methods:
- center the core nodes
- use slightly stronger border weight on main-chain nodes
- use one thicker edge for the main functional path
- place support/governance functions around or below the core instead of interleaving them into the main chain

A good functional architecture diagram should reveal:
- what must happen for the system to deliver its primary value
- what only supports or constrains that value delivery

### Step 5: Add support and governance functions deliberately

At overview stage, the lifecycle document explicitly expects design principles, complexity awareness, and later risk/constraint thinking.

So if support/governance functions materially affect the system shape, include them as functional blocks such as:
- 权限控制
- 配置中心
- 规则管理
- 审计留痕
- 监控告警
- 任务调度
- 运营分析

But do not let them dominate the topology unless governance is itself the system’s primary mission.

### Step 6: Add only the necessary relationships

Ask of each edge:
- does this help explain the main capability collaboration?
- would removing it make the functional logic materially less clear?

If no, omit it.

Recommended edge policy:
- main chain: solid arrow
- major support/control relation: solid or dashed arrow depending on strength
- future/weak/event relation: dashed arrow

### Step 7: Verify artifact type before finalizing

Final check:
- is this still a functional architecture diagram?
- or did it silently become a system architecture / flow / technical / deployment diagram?

If the picture contains too much of the following, it likely drifted:
- machine/node topology
- process/thread/job placement
- middleware product names
- exact request sequence branches
- schema/table/index detail

## Functional Layer Reference Model

Use this as a starting template, not a rigid doctrine.

### 1. Access / Interaction Layer
Purpose:
- accept user, operator, partner, or upstream triggers
- expose the system’s visible interaction surface

Typical nodes:
- 用户入口
- 管理后台
- 开放接口
- 消息接入
- 批处理触发

### 2. Application Orchestration Layer
Purpose:
- coordinate use cases
- translate interaction into business actions
- manage high-level process orchestration without owning deep domain rules

Typical nodes:
- 流程编排
- 任务协调
- 指令分发
- 场景服务

### 3. Core Functional Capability Layer
Purpose:
- hold the system’s primary business capabilities
- embody the main value-producing functional modules

Typical nodes:
- 订单处理
- 风险校验
- 策略执行
- 额度计算
- 对账处理
- 内容生成

This is usually the center of the diagram.

### 4. Support / Governance Layer
Purpose:
- provide cross-cutting control, management, compliance, observability, and maintainability capabilities

Typical nodes:
- 配置管理
- 规则管理
- 权限鉴权
- 审计留痕
- 监控告警
- 运维控制

### 5. Data / External Collaboration Layer
Purpose:
- represent data support or external coordination that materially shapes functional boundaries

Typical nodes:
- 数据汇聚
- 指标服务
- 外部系统适配
- 结果回传
- 主数据读取

Important:
At functional architecture stage, these nodes represent **functional responsibilities**, not specific storage engines or transport products.

## Naming and Granularity Rules

### Node naming rule
Choose names that are:
- capability-oriented
- responsibility-specific
- stable enough to survive moderate implementation change

Prefer:
- 动作 + 对象
- 领域 + 职责
- capability phrase of 2–6 Chinese words

Avoid:
- framework names
- internal class/package names
- vague labels like “处理中心”, “能力平台”, “管理模块” unless further specified

### Granularity rule
Inside the same container, nodes must live at the same abstraction level.

Bad same-layer mix:
- 风险校验
- 策略引擎
- MySQL索引优化
- 审计报表

Good same-layer mix:
- 风险校验
- 规则匹配
- 策略执行
- 结果归档

### Container naming rule
Container titles must be classification dimensions, not leftovers.

Good:
- 核心业务能力层
- 运营治理层
- 数据协同层

Bad:
- 其他
- 杂项
- 通用模块

## Risk-Aware Additions for Overview Design

Because the lifecycle document pushes architects to analyze complexity and risks early, a stronger functional architecture diagram should reveal where the main pressure points live.

Without turning the picture into a technical design, you may annotate or visually hint:
- high-concurrency core capability
- strong-control / compliance-sensitive function
- external dependency boundary
- stateful/high-consistency function
- operational governance choke point

Use subtle cues:
- border emphasis
- concise edge labels
- one small legend

Do not turn the functional diagram into a risk matrix. Just expose the places where later architecture decisions will concentrate.

## Runtime-capability inheritance rule for product functional diagrams

When the system is a **product built on top of an existing agent runtime**, the 系统功能架构图 should show not only product-facing functions but also the inherited platform-capability groups that materially shape the product's real ceiling.

Preferred handling:
- keep the diagram product-facing first
- but reserve one support / capability-assembly region to show inherited capability groups such as:
  - multi-host entry surfaces (CLI / Gateway / Cron / ACP / Batch) when they matter to the product's operation model
  - provider routing / fallback
  - tools / MCP / plugins
  - memory / skills / context assembly
  - session storage / profile isolation
  - multimodal capabilities such as image understanding, image generation, STT, and TTS when relevant to the product roadmap

Reason:
- otherwise the functional diagram under-describes what the product can actually become
- teams may then redesign capabilities the runtime already provides

Important boundary:
- do **not** let the functional diagram collapse into an implementation inventory
- show inherited platform capabilities as support/capability blocks, not as low-level file/module names

## Diagram packaging rule for overview documents

When the functional architecture diagram is part of a markdown/vault-based overview-design document set:
- do not leave the overview doc with prose plus a standalone `.html` link only
- produce an embed-friendly companion `.svg` and let the overview doc inline the `.svg`, with the `.html` kept as an optional richer artifact
- this keeps the overview package reviewable inside the document itself rather than forcing reviewers to context-switch into a browser artifact
- when embedding raw SVG into markdown/vault documents, do not rely only on CSS class rules for text color inheritance; add explicit `fill` colors to visible `<text>` elements so dark-theme renderers do not degrade into unreadable black text

## Live delivery addition: same-round overview completion

When the user asks to continue/optimize the 概要设计方案 rather than just “draw one diagram”, the stronger default is:
1. tighten or create the overview markdown/doc
2. add the 功能架构图
3. ensure the paired 系统架构图 is aligned to the same terminology and abstraction
4. cross-check that the doc explains the boundary between the two diagrams

A functional diagram delivered alone in that situation is usually incomplete, because overview design is being reviewed as a package rather than as an isolated figure.

## Naming discipline reminder from live iteration

At overview functional level, node names may still be product-facing, but they should remain capability-oriented rather than page-title-oriented.
Prefer:
- `身份与空间入口`
- `员工定义与配置`
- `团队模板与行业方案`
- `多智能体协作编排`
- `审计与经营分析`

Prefer not to anchor the diagram in one UI surface name when the capability is broader than that UI.

## Review Gates

When reviewing an existing 系统功能架构图, check these gates.

### Gate 1: Goal visibility
Can a reviewer infer the system construction goal from the title, layering, and core chain?

If no, the diagram is likely only enumerating functions.

### Gate 2: Layer purity
Does each layer represent one abstraction axis?

If no, the diagram is mixing functional, technical, and deployment concerns.

### Gate 3: Core-loop visibility
Can a reviewer point to the system’s primary functional closed loop within 10 seconds?

If no, support functions are probably overpowering the core.

### Gate 4: Functional vs technical contamination
Are there concrete technologies, hosts, middleware brands, runtime carriers, code packages, or schema details in the main nodes?

If yes, the diagram has drifted downward into technical architecture.

### Gate 5: Arrow discipline
Do arrows only show important collaboration/dependency?

If no, reduce edges until the main capability logic becomes legible.

### Gate 6: External dependency honesty
If external systems materially shape the functional boundary, are they shown clearly as external/supporting rather than pretending to be internal core functions?

### Gate 7: Governance sufficiency
If audit, permissions, rules, monitoring, operations, or compliance materially affect the solution, are they visible enough to guide later design rather than being forgotten entirely?

## Common Pitfalls

1. Drawing a feature catalog instead of a layered functional architecture.
2. Using the diagram to show implementation structure too early.
3. Mixing business flow order with functional classification in one artifact.
4. Making support/governance capabilities visually dominate the core functional path.
5. Letting every related module connect to every other module.
6. Naming nodes with framework, package, class, or product names.
7. Mixing abstraction levels inside one layer.
8. Hiding the system goal so the viewer sees boxes but not purpose.
9. Using “overall architecture” language to avoid deciding whether the artifact is functional, system, technical, or deployment view.

## Output Template

When generating a diagram spec, use this compact scaffold before tool-specific syntax. Then apply the full visual/tooling rules from `references/universal-diagramming-spec-full.md` without compressing them:

- 建设目标：
- 受众：
- 图类型：系统功能架构图（概要设计）
- 布局模式：
- 分层逻辑：
- 核心功能闭环：
- 一级容器：
- 二级分组（如有）：
- 关键节点清单：
- 必保留边：
- 省略原则：
- 图例语义：

## Full-Fidelity Reference Usage Rule

Before producing a real diagram spec, review:
- `references/universal-diagramming-spec-full.md`
- `references/layering-templates.md`

Usage split:
- `universal-diagramming-spec-full.md` = canonical full publication-grade drawing standard, preserved without compression
- `layering-templates.md` = reusable architect templates for common functional-layer shapes
- this `SKILL.md` = overview-design positioning, artifact boundary control, and architect review method

Do not let the presence of templates or overview-design guidance silently erase the original visual grammar, spacing rules, naming rules, layout library, tool mapping, or checklist detail preserved in the full reference.

## Tool Mapping Usage Note

Tool-specific drawing details are governed by the canonical full reference:
- `references/universal-diagramming-spec-full.md`

This umbrella skill adds architect-usage routing on top of that reference:
- 正式文档、可编辑源文件留存、功能分层清晰、需要长期维护的系统功能架构图场景，使用 Draw.io 一类可编辑独立图资产工具
- 草图评审、手绘风格、非正式沟通、快速迭代场景，使用 Excalidraw
- 网页嵌入、交互式功能图、动态高亮、高精度输出场景使用 SVG / HTML 路线；当运行环境提供 SVG / HTML 制图能力时，选择对应工具
- choose the tool that best fits the document/review context, but do not change the underlying visual grammar, spacing discipline, or naming rules
- when producing a 系统功能架构图 in 概要设计, keep the artifact focused on capability layering and functional boundary clarity rather than expanding into implementation/runtime detail just because the chosen tool makes that easy

## Verification Checklist

- [ ] The diagram answers “系统建设目标是什么、要有哪些功能、这些功能如何分层”
- [ ] The artifact is clearly a functional architecture diagram, not a flow/technical/deployment view
- [ ] A dominant layering axis is present and internally consistent
- [ ] The core functional closed loop is visually recognizable
- [ ] Support/governance capabilities are present when materially necessary, but not overpowering
- [ ] External dependencies are shown only when they shape functional boundaries
- [ ] Node names are capability-oriented and free of code/product/version leakage
- [ ] Containers are no deeper than 2 levels
- [ ] Arrows are sparse and meaningful
- [ ] The diagram is strong enough to guide later system architecture and technical design without prematurely becoming them
